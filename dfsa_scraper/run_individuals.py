"""
run_individuals.py

Collector for the DFSA individuals register (dfsa_registers.INDIVIDUALS,
"confirmed_total": 4063). Mirrors run_register.py's structure exactly --
same checkpoint/output/errors pattern, same event names/shapes, same
outage-retry behavior -- but reuses walk_register() UNFILTERED rather
than walk_register_by_type().

IMPORTANT -- WHAT IS AND ISN'T CONFIRMED HERE, UNLIKE run_register.py:

  run_register.py's type-partitioned strategy for FIRMS exists because a
  live diagnostic probe CONFIRMED the unfiltered firms listing hits a
  hard ~1010-row server-side pagination ceiling and is not even a
  superset of the type-filtered data (see dfsa_registers.FIRMS's
  docstring for the full evidence trail).

  No equivalent probe has been run against the individuals register.
  It is NOT confirmed whether the same ~1010-row ceiling applies here,
  whether it applies at a different row count, or whether it's absent
  entirely. This script therefore:
    1. Walks UNFILTERED first (simplest correct approach if there's no
       ceiling -- don't add type-partitioning complexity speculatively
       for a limit that may not exist).
    2. Self-checks its own result the same way walk_register() already
       does internally (raw_rows_seen vs getTotal()), and ALSO checks it
       explicitly here at the summary level, loudly, in case the
       ~1010-ish shape reappears.
    3. If discovered is suspiciously short of confirmed_total (4063),
       DOES NOT silently accept it as "the real number" -- flags it as
       an anomaly requiring the same kind of type-partitioned-walk
       treatment run_register.py needed for firms, using
       INDIVIDUALS["default_filters"]'s facet fields (key_individual_
       function / authorised_individual_function / audit_principal_
       function) as the candidate partition keys, via
       dfsa_common.discover_select_options() to enumerate real values
       -- exactly the same diagnostic process that discovered
       CONFIRMED_FIRM_TYPES for firms. That extension is NOT built here
       because it should only be built in response to a REAL confirmed
       ceiling, not guessed at blind.

  The detail-page parser (dfsa_individual_detail.parse_individual_detail)
  is itself only confirmed against ONE live example (Mr Ahmad Alanani,
  I004080) -- see that module's docstring. Field coverage across the
  full 4063 should be treated as provisional until more of the run
  succeeds and any KeyError/empty-field patterns are reviewed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from bs4 import BeautifulSoup
import requests

from dfsa_common import DfsaSession, walk_register
from dfsa_registers import INDIVIDUALS
from dfsa_individual_detail import parse_individual_detail
from events import EventBus

logger = logging.getLogger("dfsa_scraper")

DEFAULT_OUTPUT_PATH = "individuals.jsonl"
DEFAULT_ERRORS_PATH = "individuals_errors.jsonl"
DEFAULT_CHECKPOINT_PATH = "individuals_checkpoint.json"


def load_checkpoint(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_checkpoint(path: str, checkpoint: dict) -> None:
    Path(path).write_text(json.dumps(checkpoint, indent=0), encoding="utf-8")


def _load_existing_records_index(output_path: str) -> dict:
    """Same purpose/shape as run_register.py's version -- {url: record}
    lookup from a prior run's output, so checkpoint-skipped individuals
    can still be reported as COMPLETE with real data instead of stalling
    a dashboard at PROCESSING (see run_register.py / FIRM_SKIPPED_FROM_
    CHECKPOINT for the original bug this pattern fixes)."""
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


def process_one_individual(session: DfsaSession, bus: EventBus, name: str, detail_url: str) -> dict:
    session.company = name

    bus.emit("DETAIL_FETCH_STARTED", company=name, url=detail_url)
    try:
        html = session.get_detail_page(detail_url)
    except Exception as exc:
        bus.emit(
            "FIRM_FAILED",  # reusing the same event-type vocabulary the dashboard already understands
            company=name, url=detail_url, stage="FETCH", error=str(exc), exception=exc,
            message="Detail page fetch failed after all retries.",
        )
        return {"success": False, "company": name, "stage": "FETCH"}

    bus.emit("DETAIL_FETCH_SUCCESS", company=name, url=detail_url, counts={"bytes": len(html)})

    bus.emit("PARSING_STARTED", company=name, url=detail_url)
    try:
        soup = BeautifulSoup(html, "html.parser")
        detail = parse_individual_detail(html, detail_url)
    except Exception as exc:
        bus.emit(
            "PARSER_ERROR", company=name, url=detail_url, stage="PARSE",
            error=str(exc), exception=exc,
            message="The page was retrieved successfully, but parsing raised an exception.",
        )
        bus.emit("FIRM_FAILED", company=name, stage="PARSE", error=str(exc))
        return {"success": False, "company": name, "stage": "PARSE"}

    bus.emit(
        "FIRM_FIELDS_PARSED",
        company=name,
        counts={
            "functions": len(detail.functions),
            "firm_affiliations": len(detail.firm_affiliations),
            "regulatory_actions": len(detail.regulatory_actions),
        },
    )

    bus.emit("VALIDATION_STARTED", company=name)
    if not detail.reference_number:
        bus.emit("VALIDATION_WARNING", company=name, message="Missing required field: DFSA Reference Number")

    record = asdict(detail)

    bus.emit(
        "FIRM_COMPLETED",
        company=name, url=detail_url,
        counts={
            "functions": len(detail.functions),
            "firm_affiliations": len(detail.firm_affiliations),
            "regulatory_actions": len(detail.regulatory_actions),
        },
        extra={"record": record},
    )
    return {"success": True, "company": name, "record": record}


def run_individuals_register(
    bus: EventBus,
    max_individuals: int | None = None,
    output_path: str = DEFAULT_OUTPUT_PATH,
    errors_path: str = DEFAULT_ERRORS_PATH,
    checkpoint_path: str = DEFAULT_CHECKPOINT_PATH,
) -> dict:
    checkpoint = load_checkpoint(checkpoint_path)
    existing_records = _load_existing_records_index(output_path)
    bus.emit(
        "RUN_STARTED",
        message=(
            f"Individuals register run started (max_individuals={max_individuals if max_individuals is not None else 'ALL'}, "
            f"resuming with {len(checkpoint)} already-completed individual(s) in checkpoint, "
            f"{len(existing_records)} existing record(s) indexed from {output_path}). "
            f"NOTE: unfiltered walk -- pagination-ceiling behavior for this register is UNCONFIRMED, "
            f"unlike firms; the summary below will flag it if discovered falls suspiciously short "
            f"of INDIVIDUALS['confirmed_total']."
        ),
    )

    session = DfsaSession(on_event=bus.emit)
    summary = {
        "discovered": 0,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped_from_checkpoint": 0,
        "failed_individuals": [],
    }

    out_f = open(output_path, "a", encoding="utf-8")
    err_f = open(errors_path, "a", encoding="utf-8")

    try:
        for row in walk_register(session, INDIVIDUALS["path"], INDIVIDUALS["default_filters"]):
            summary["discovered"] += 1

            if row.detail_url in checkpoint:
                summary["skipped_from_checkpoint"] += 1
                record = existing_records.get(row.detail_url)
                bus.emit(
                    "FIRM_SKIPPED_FROM_CHECKPOINT",
                    company=row.name, url=row.detail_url, stage="COMPLETE",
                    counts={
                        "firm_affiliations": len(record["firm_affiliations"]) if record else None,
                        "regulatory_actions": len(record["regulatory_actions"]) if record else None,
                    },
                    message=(
                        "Already completed in a previous run (found in checkpoint.json)."
                        if record
                        else "Marked done in checkpoint.json, but no matching record was found "
                        f"in {output_path}. This individual's data may be missing -- worth investigating."
                    ),
                    extra={"record": record} if record else None,
                )
                continue

            result = process_one_individual(session, bus, row.name, row.detail_url)
            summary["processed"] += 1

            if result["success"]:
                summary["succeeded"] += 1
                record = result["record"]
                try:
                    out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out_f.flush()
                    bus.emit(
                        "RECORD_SAVED", company=result["company"],
                        counts={"saved_total": summary["succeeded"]},
                        message=f"Appended to {output_path}.",
                    )
                except Exception as exc:
                    bus.emit(
                        "RECORD_SAVE_FAILED", company=result["company"], error=str(exc), exception=exc,
                        message=f"Individual was parsed successfully but could not be written to {output_path}.",
                    )
            else:
                summary["failed"] += 1
                failure_record = {
                    "individual": result["company"], "url": row.detail_url,
                    "stage": result.get("stage"), "reference_number": row.reference_number,
                }
                summary["failed_individuals"].append(failure_record)
                err_f.write(json.dumps(failure_record, ensure_ascii=False) + "\n")
                err_f.flush()

            checkpoint[row.detail_url] = "done"
            save_checkpoint(checkpoint_path, checkpoint)

            if max_individuals is not None and summary["processed"] >= max_individuals:
                bus.emit("RUN_FINISHED", message=f"Reached max_individuals={max_individuals}, stopping.")
                return summary
    except (requests.exceptions.RequestException, RuntimeError) as exc:
        summary["stopped_due_to_outage"] = True
        summary["outage_error"] = str(exc)
        bus.emit(
            "RUN_FINISHED",
            message=(
                f"Run stopped: connection to DFSA failed repeatedly ({exc}). "
                f"{summary['processed']} individual(s) were processed and checkpointed before this "
                f"happened -- safe to simply re-run once the connection is stable."
            ),
            error=str(exc),
        )
        return summary
    finally:
        out_f.close()
        err_f.close()

    # --- Self-check against the confirmed total (see module docstring) ---
    confirmed_total = INDIVIDUALS.get("confirmed_total")
    undercount_ratio = None
    if confirmed_total:
        undercount_ratio = summary["discovered"] / confirmed_total
        if undercount_ratio < 0.97:
            logger.warning(
                "run_individuals_register: discovered=%d is only %.1f%% of confirmed_total=%d. "
                "This is the SAME SHAPE of anomaly that affected the unfiltered firms listing "
                "(996/1224 discovered, later found to be a hard pagination ceiling masking 1056 "
                "additional firms). DO NOT assume the extra individuals don't exist -- treat this "
                "the same way: probe with discover_select_options() against the individuals "
                "listing's facet <select> fields (key_individual_function, authorised_individual_"
                "function, audit_principal_function) and consider a type-partitioned walk, exactly "
                "as run_register.py had to do for firms.",
                summary["discovered"], undercount_ratio * 100, confirmed_total,
            )
    summary["confirmed_total"] = confirmed_total
    summary["undercount_ratio"] = undercount_ratio
    summary["possible_pagination_ceiling"] = bool(undercount_ratio and undercount_ratio < 0.97)

    bus.emit(
        "RUN_FINISHED",
        message=(
            f"Individuals register walk complete. {summary['processed']} processed "
            f"({summary['succeeded']} succeeded, {summary['failed']} failed), "
            f"{summary['skipped_from_checkpoint']} already done from checkpoint. "
            f"discovered={summary['discovered']} vs confirmed_total={confirmed_total}"
            + (" -- POSSIBLE PAGINATION CEILING, see log warning above."
               if summary["possible_pagination_ceiling"] else "")
        ),
    )
    return summary


if __name__ == "__main__":
    bus = EventBus()
    bus.subscribe(lambda e: print(f"[{e['type']}] {e.get('company') or ''} {e.get('message') or ''}"))
    summary = run_individuals_register(bus, max_individuals=None)
    print("\n--- SUMMARY ---")
    for k, v in summary.items():
        if k != "failed_individuals":
            print(f"{k}: {v}")
    if summary["failed_individuals"]:
        print(f"\nFailed individuals ({len(summary['failed_individuals'])}) -- see individuals_errors.jsonl:")
        for f in summary["failed_individuals"]:
            print(f"  - {f['individual']} ({f['reference_number']}) failed at {f['stage']}")
    if summary.get("possible_pagination_ceiling"):
        print(
            "\n*** discovered count is suspiciously below confirmed_total -- this may be the same "
            "kind of hidden pagination ceiling that affected the firms register. Do not trust this "
            "as the full individuals dataset until you've probed the facet fields the way firms' "
            "CONFIRMED_FIRM_TYPES was derived. ***"
        )