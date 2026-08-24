"""
run_register.py

The production collector. Walks the real DFSA firms register via
walk_register_by_type() -- CONFIRMED (2026-08 diagnostic probes) that the
unfiltered listing hits a hard server-side pagination ceiling around page
100 (~1010 rows), independent of session/CSRF freshness, and that it does
not even expose every firm that type-filtered queries can reach (996
unique refs unfiltered vs. 2052 unique refs across all 16 confirmed
`type` values). walk_register_by_type() walks each of the 16 confirmed
type values in turn (see dfsa_registers.CONFIRMED_FIRM_TYPES) and
deduplicates by reference_number (falling back to detail_url for any
row with a missing reference number), since type values are confirmed
NOT fully mutually exclusive (10 overlapping firm instances observed
across 5 type-value pairs). Every discovered firm is pushed through
Fetch -> Parse -> Validate -> Complete, streaming completed records to
firms.jsonl and failures to errors.jsonl as it goes.

CHECKPOINTING: progress is tracked in checkpoint.json -- a JSON object
mapping detail_url -> "done" for every firm that has been through the
pipeline (success OR permanent failure) in a previous run. On start, the
register walk still runs (we always re-page through listings -- cheap,
~123 pages for firms), but any row whose detail_url is already in the
checkpoint is skipped without a detail-page fetch. This means an
interrupted run (crash, Ctrl+C, network drop, machine restart) can be
resumed by simply re-running the same command -- already-completed firms
are not re-fetched, and firms.jsonl is never truncated or overwritten
(append-only), so no valid data already on disk is lost or duplicated.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from bs4 import BeautifulSoup
import requests

from dfsa_common import DfsaSession, walk_register_by_type
from dfsa_registers import FIRMS, CONFIRMED_FIRM_TYPES
from events import EventBus
from poc_firm_detail import parse_firm_fields, parse_financial_services, parse_table_rows, parse_regulatory_actions

logger = logging.getLogger("dfsa_scraper")

DEFAULT_OUTPUT_PATH = "firms.jsonl"
DEFAULT_ERRORS_PATH = "errors.jsonl"
DEFAULT_CHECKPOINT_PATH = "checkpoint.json"


def load_checkpoint(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupted checkpoint must never crash the run or silently
        # discard already-collected data -- worst case we re-fetch
        # firms we'd already done, which is safe (append-only output),
        # just slower.
        return {}


def save_checkpoint(path: str, checkpoint: dict) -> None:
    Path(path).write_text(json.dumps(checkpoint, indent=0), encoding="utf-8")


def process_one_firm(session: DfsaSession, bus: EventBus, name: str, detail_url: str) -> dict:
    """
    Runs a single already-discovered firm through
    Fetch -> Parse -> Validate -> Complete. Mirrors
    run_scraper_with_dashboard.run_julius_baer_test's stage structure
    exactly, generalized to any firm.
    """
    session.company = name  # so REQUEST_* events attach to the right firm

    bus.emit("DETAIL_FETCH_STARTED", company=name, url=detail_url)
    try:
        html = session.get_detail_page(detail_url)
    except Exception as exc:
        bus.emit(
            "FIRM_FAILED",
            company=name,
            url=detail_url,
            stage="FETCH",
            error=str(exc),
            exception=exc,
            message="Detail page fetch failed after all retries.",
        )
        return {"success": False, "company": name, "stage": "FETCH"}

    bus.emit("DETAIL_FETCH_SUCCESS", company=name, url=detail_url, counts={"bytes": len(html)})

    bus.emit("PARSING_STARTED", company=name, url=detail_url)
    try:
        soup = BeautifulSoup(html, "html.parser")
        firm_fields = parse_firm_fields(soup)
        financial_services = parse_financial_services(soup)
        individuals = parse_table_rows(
            soup, "individuals",
            ["name", "reference_number", "type_of_individual", "effective_date", "date_withdrawn"],
        )
        regulatory_actions = parse_regulatory_actions(soup)
    except Exception as exc:
        bus.emit(
            "PARSER_ERROR",
            company=name,
            url=detail_url,
            stage="PARSE",
            error=str(exc),
            exception=exc,
            message="The page was retrieved successfully, but parsing raised an exception.",
        )
        bus.emit("FIRM_FAILED", company=name, stage="PARSE", error=str(exc))
        return {"success": False, "company": name, "stage": "PARSE"}

    bus.emit(
        "FIRM_FIELDS_PARSED",
        company=name,
        counts={"fields_found": len(firm_fields), "financial_service_categories": len(financial_services)},
    )
    if not financial_services:
        bus.emit(
            "PARSER_WARNING",
            company=name,
            message="No Financial Service / Investments entries found. May be genuine (unlicensed for services) or worth a spot-check.",
        )

    bus.emit("INDIVIDUALS_PARSED", company=name, counts={"rows_found": len(individuals)})
    bus.emit("REGULATORY_ACTIONS_PARSED", company=name, counts={"rows_found": len(regulatory_actions)})

    bus.emit("VALIDATION_STARTED", company=name)
    required_fields = ["Legal Status", "DFSA Reference Number"]
    missing = [f for f in required_fields if not firm_fields.get(f)]
    if missing:
        bus.emit("VALIDATION_WARNING", company=name, message=f"Missing required field(s): {', '.join(missing)}")

    record = {
        "url": detail_url,
        "firm_details": firm_fields,
        "financial_services": financial_services,
        "individuals": individuals,
        "regulatory_actions": regulatory_actions,
    }

    bus.emit(
        "FIRM_COMPLETED",
        company=name,
        url=detail_url,
        counts={
            "firm_fields": len(firm_fields),
            "financial_service_categories": len(financial_services),
            "individuals": len(individuals),
            "regulatory_actions": len(regulatory_actions),
        },
        extra={"record": record},
    )
    return {"success": True, "company": name, "record": record}


def _load_existing_records_index(output_path: str) -> dict:
    """
    Reads output_path (firms.jsonl) once at startup and returns a
    {record["url"]: record} lookup. Used so that firms already marked
    "done" in checkpoint.json -- and therefore skipped by
    run_firms_register() below -- can still be reported to the dashboard
    (and to callers) as fully-populated COMPLETE rows instead of stalling
    at PROCESSING forever with no further events.

    Tolerates a missing file (first-ever run, nothing to index yet) and
    tolerates duplicate lines for the same url (keeps the *last* one seen,
    since later appends reflect the most recent successful parse).
    Malformed JSON lines are skipped rather than crashing the whole run --
    a corrupt line in an otherwise-good jsonl file is not a reason to
    lose the checkpoint-resume/report capability for everything else.
    """
    index: dict = {}
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = record.get("url")
                if url:
                    index[url] = record
    except FileNotFoundError:
        pass
    return index


def run_firms_register(
    bus: EventBus,
    max_firms: int | None = None,
    output_path: str = DEFAULT_OUTPUT_PATH,
    errors_path: str = DEFAULT_ERRORS_PATH,
    checkpoint_path: str = DEFAULT_CHECKPOINT_PATH,
) -> dict:
    """
    Walks the real firms register (paginated, 10 rows/page) and processes
    every firm end-to-end, unless max_firms limits it. Skips any firm
    already marked done in the checkpoint (resume support). Appends
    completed records to output_path, appends failures to errors_path,
    and updates the checkpoint after every firm (success or failure) so
    an interrupted run can be resumed by re-running this function.

    Returns a summary dict: discovered, processed, succeeded, failed,
    skipped_from_checkpoint, individuals_total, regulatory_actions_total,
    failed_firms (list of {company, url, stage, error}).
    """
    checkpoint = load_checkpoint(checkpoint_path)
    existing_records = _load_existing_records_index(output_path)
    bus.emit(
        "RUN_STARTED",
        message=(
            f"Firms register run started (max_firms={max_firms if max_firms is not None else 'ALL'}, "
            f"resuming with {len(checkpoint)} already-completed firm(s) in checkpoint, "
            f"{len(existing_records)} existing record(s) indexed from {output_path})."
        ),
    )

    session = DfsaSession(on_event=bus.emit)
    summary = {
        "discovered": 0,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped_from_checkpoint": 0,
        "individuals_total": 0,
        "regulatory_actions_total": 0,
        "failed_firms": [],
    }

    out_f = open(output_path, "a", encoding="utf-8")
    err_f = open(errors_path, "a", encoding="utf-8")

    # CONFIRMED (2026-08 diagnostic probes): the unfiltered walk hits a
    # hard server-side pagination ceiling (~1010 rows) and is not even a
    # superset of what's reachable -- walking every confirmed `type`
    # value is the confirmed working strategy. walk_register_by_type()
    # deduplicates by reference_number in-process (the diagnostic
    # probes' authoritative uniqueness criterion -- type values are
    # confirmed NOT mutually exclusive, 10 cross-type overlap instances
    # observed) and preserves discovered_type provenance on each row.
    # The checkpoint lookup below (keyed by detail_url, unchanged) is a
    # second, independent line of defense against re-processing. See
    # dfsa_registers.FIRMS's "confirmed_total" comment for the full
    # evidence trail.
    type_diagnostics: dict = {}
    summary["type_walk_diagnostics"] = type_diagnostics  # same object -- filled in-place
    # by walk_register_by_type once its generator is fully exhausted; if
    # max_firms triggers an early return below, this will be empty/absent
    # since the type walk was cut short (expected and noted at that
    # return point).
    try:
        for row in walk_register_by_type(
            session, FIRMS["path"], FIRMS["default_filters"], CONFIRMED_FIRM_TYPES,
            diagnostics=type_diagnostics,
        ):
            summary["discovered"] += 1

            if row.detail_url in checkpoint:
                summary["skipped_from_checkpoint"] += 1
                record = existing_records.get(row.detail_url)
                bus.emit(
                    "FIRM_SKIPPED_FROM_CHECKPOINT",
                    company=row.name,
                    url=row.detail_url,
                    stage="COMPLETE",
                    counts={
                        "individuals": len(record["individuals"]) if record else None,
                        "regulatory_actions": len(record["regulatory_actions"]) if record else None,
                    },
                    message=(
                        "Already completed in a previous run (found in checkpoint.json)."
                        if record
                        else "Marked done in checkpoint.json, but no matching record was found "
                        f"in {output_path}. This firm's data may be missing -- worth investigating."
                    ),
                    extra={"record": record} if record else None,
                )
                continue

            result = process_one_firm(session, bus, row.name, row.detail_url)
            summary["processed"] += 1

            if result["success"]:
                summary["succeeded"] += 1
                record = result["record"]
                summary["individuals_total"] += len(record["individuals"])
                summary["regulatory_actions_total"] += len(record["regulatory_actions"])
                try:
                    out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out_f.flush()
                    bus.emit(
                        "RECORD_SAVED",
                        company=result["company"],
                        counts={"saved_total": summary["succeeded"]},
                        message=f"Appended to {output_path}.",
                    )
                except Exception as exc:
                    bus.emit(
                        "RECORD_SAVE_FAILED",
                        company=result["company"],
                        error=str(exc),
                        exception=exc,
                        message=f"Firm was parsed successfully but could not be written to {output_path}.",
                    )
            else:
                summary["failed"] += 1
                failure_record = {
                    "company": result["company"],
                    "url": row.detail_url,
                    "stage": result.get("stage"),
                    "reference_number": row.reference_number,
                }
                summary["failed_firms"].append(failure_record)
                err_f.write(json.dumps(failure_record, ensure_ascii=False) + "\n")
                err_f.flush()

            # Checkpoint after every firm, success or failure -- a firm
            # that failed permanently (already retried MAX_RETRIES times
            # inside DfsaSession) is marked done so we don't hammer it
            # again on every resume; it's captured in errors.jsonl for
            # manual review instead.
            checkpoint[row.detail_url] = "done"
            save_checkpoint(checkpoint_path, checkpoint)

            if max_firms is not None and summary["processed"] >= max_firms:
                bus.emit(
                    "RUN_FINISHED",
                    message=(
                        f"Reached max_firms={max_firms}, stopping. NOTE: the type-partitioned "
                        f"walk was cut short by max_firms -- type_walk_diagnostics is partial/"
                        f"incomplete (it's only fully populated once every type has been walked "
                        f"to completion)."
                    ),
                )
                return summary
    except (requests.exceptions.RequestException, RuntimeError) as exc:
        # Reached only after walk_register_by_type's own outage-retry
        # budget (TYPE_RETRY_MAX_ATTEMPTS, see dfsa_common.py) has been
        # fully exhausted -- i.e. a SUSTAINED outage well beyond a normal
        # blip. Previously this propagated uncaught and silently killed
        # the background thread (confirmed real occurrence: a
        # NameResolutionError took down the whole run with no dashboard
        # feedback, requiring the user to notice and manually restart).
        # Every firm processed before this point is already safely
        # checkpointed (checkpoint.json is saved after each firm, above),
        # so this is a clean, resumable stopping point, not data loss --
        # re-running this function will pick up exactly where it left off.
        summary["stopped_due_to_outage"] = True
        summary["outage_error"] = str(exc)
        bus.emit(
            "RUN_FINISHED",
            message=(
                f"Run stopped: connection to DFSA failed repeatedly and exhausted the outage-retry "
                f"budget ({exc}). {summary['processed']} firm(s) were processed and checkpointed "
                f"before this happened -- safe to simply re-run once the connection is stable; "
                f"already-completed firms will be skipped via checkpoint.json."
            ),
            error=str(exc),
        )
        return summary
    finally:
        out_f.close()
        err_f.close()

    if type_diagnostics.get("any_anomaly"):
        logger.warning(
            "walk_register_by_type reported anomalies -- see summary['type_walk_diagnostics'] "
            "for details (a type may have hit its max-pages cap or shown a getTotal() mismatch "
            "not explained by within-type or cross-type duplicate suppression)."
        )

    bus.emit(
        "RUN_FINISHED",
        message=(
            f"Register walk complete. {summary['processed']} processed "
            f"({summary['succeeded']} succeeded, {summary['failed']} failed), "
            f"{summary['skipped_from_checkpoint']} already done from checkpoint. "
            f"Type-partitioned discovery: {type_diagnostics.get('types_walked', '?')} type(s) walked, "
            f"{type_diagnostics.get('global_unique_references', '?')} globally unique reference(s) found, "
            f"{type_diagnostics.get('total_duplicate_cross_type', '?')} cross-type duplicate(s) suppressed."
            + (" ANOMALIES DETECTED in type walk -- see type_walk_diagnostics." if type_diagnostics.get("any_anomaly") else "")
        ),
    )
    return summary


if __name__ == "__main__":
    # Headless mode -- proves this still runs with no dashboard attached.
    # Full production run: no max_firms limit.
    bus = EventBus()
    bus.subscribe(lambda e: print(f"[{e['type']}] {e.get('company') or ''} {e.get('message') or ''}"))
    summary = run_firms_register(bus, max_firms=None)
    print("\n--- SUMMARY ---")
    for k, v in summary.items():
        if k not in ("failed_firms", "type_walk_diagnostics"):
            print(f"{k}: {v}")
    if summary["failed_firms"]:
        print(f"\nFailed firms ({len(summary['failed_firms'])}) -- see errors.jsonl for details:")
        for f in summary["failed_firms"]:
            print(f"  - {f['company']} ({f['reference_number']}) failed at {f['stage']}")

    # --- Type-partitioned discovery diagnostics (point H: never hide anomalies) ---
    diag = summary.get("type_walk_diagnostics") or {}
    if diag:
        print("\n--- TYPE-PARTITIONED DISCOVERY DIAGNOSTICS ---")
        print(f"types_walked: {diag.get('types_walked')}")
        print(f"global_unique_references: {diag.get('global_unique_references')}")
        print(f"total_duplicate_within_type: {diag.get('total_duplicate_within_type')}")
        print(f"total_duplicate_cross_type: {diag.get('total_duplicate_cross_type')}")
        print(f"any_anomaly: {diag.get('any_anomaly')}")
        print(f"\n{'type':45s} {'getTotal':>9s} {'raw_rows':>9s} {'unique':>7s} {'dup_in':>7s} "
              f"{'dup_cross':>9s} {'empty_pg_term':>14s} {'hit_cap':>8s} {'mismatch':>9s}")
        for type_value, stats in diag.get("per_type", {}).items():
            flags = []
            if stats.get("hit_max_pages_cap"):
                flags.append("HIT_CAP")
            if stats.get("getTotal_mismatch"):
                flags.append("MISMATCH")
            if not stats.get("terminated_via_empty_page"):
                flags.append("NOT_EMPTY_PAGE_TERM")
            print(f"{type_value:45s} {stats.get('getTotal'):>9} {stats.get('raw_rows'):>9} "
                  f"{stats.get('unique_yielded'):>7} {stats.get('duplicate_within_type'):>7} "
                  f"{stats.get('duplicate_cross_type'):>9} {str(stats.get('terminated_via_empty_page')):>14s} "
                  f"{str(stats.get('hit_max_pages_cap')):>8s} {str(stats.get('getTotal_mismatch')):>9s}"
                  + (f"   <-- {', '.join(flags)}" if flags else ""))
        if diag.get("any_anomaly"):
            print("\n*** ANOMALIES DETECTED -- review the flagged row(s) above before trusting this "
                  "run's completeness. ***")
    else:
        print("\n--- TYPE-PARTITIONED DISCOVERY DIAGNOSTICS ---")
        print("(empty -- run was cut short by max_firms before the type walk completed, or no "
              "rows were discovered)")