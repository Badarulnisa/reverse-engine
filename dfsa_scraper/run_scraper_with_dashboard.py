"""
run_scraper_with_dashboard.py

The single-firm test harness (requirement 6). This is intentionally
separate from run_scraper.py -- it does NOT touch pagination or discovery
across the whole register. It drives exactly one already-known firm
(Julius Baer Middle East Limited) through
Discovery -> Fetch -> Parse -> Validate -> Complete, emitting an event at
every stage, so we can verify the dashboard reflects reality before
trusting it on hundreds of firms.

Uses the parsing logic from poc_firm_detail.py -- confirmed correct
against the live site via Console diagnostics and manual screenshot
comparison -- rather than the older dfsa_firm_detail.py, whose
label-guessing approach was never confirmed against real markup.

This module can run with NO dashboard attached at all (see __main__ below)
-- that's the proof that the dashboard is a genuinely optional layer
(requirement 5), not something the scraper depends on.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from dfsa_common import DfsaSession
from events import EventBus
from poc_firm_detail import parse_firm_fields, parse_financial_services, parse_table_rows

JULIUS_BAER_URL = "https://www.dfsa.ae/public-register/firms/julius-baer-middle-east-limited"
JULIUS_BAER_NAME = "Julius Baer (Middle East) Limited"


def run_julius_baer_test(bus: EventBus) -> dict:
    bus.emit("RUN_STARTED", message="Single-firm test run: Julius Baer (Middle East) Limited")

    # --- DISCOVERY ----------------------------------------------------
    # In the full pipeline this event comes from walk_register() finding a
    # row on a listing page (see the FIRM_DISCOVERED emit added there).
    # Here we already know the firm, so we emit it manually -- this keeps
    # the pipeline visualization consistent end-to-end even though this
    # test skips the real listing walk.
    bus.emit(
        "FIRM_DISCOVERED",
        company=JULIUS_BAER_NAME,
        url=JULIUS_BAER_URL,
        message="Firm known in advance for this single-firm test. In a full run this event comes from a listing page row.",
    )

    session = DfsaSession(on_event=bus.emit, company=JULIUS_BAER_NAME)

    # --- FETCH ----------------------------------------------------
    bus.emit("DETAIL_FETCH_STARTED", company=JULIUS_BAER_NAME, url=JULIUS_BAER_URL)
    try:
        html = session.get_detail_page(JULIUS_BAER_URL)
    except Exception as exc:
        bus.emit(
            "FIRM_FAILED",
            company=JULIUS_BAER_NAME,
            url=JULIUS_BAER_URL,
            stage="FETCH",
            error=str(exc),
            exception=exc,
            message="Detail page fetch failed after all retries. See REQUEST_RETRY/REQUEST_FAILED events above for the individual attempts.",
        )
        bus.emit("RUN_FINISHED", message="Run ended after a fetch failure.")
        return {"success": False, "stage": "FETCH"}

    bus.emit(
        "DETAIL_FETCH_SUCCESS",
        company=JULIUS_BAER_NAME,
        url=JULIUS_BAER_URL,
        counts={"bytes": len(html)},
    )

    # --- PARSE ----------------------------------------------------
    bus.emit("PARSING_STARTED", company=JULIUS_BAER_NAME, url=JULIUS_BAER_URL)
    try:
        soup = BeautifulSoup(html, "lxml")
        firm_fields = parse_firm_fields(soup)
        financial_services = parse_financial_services(soup)
        individuals = parse_table_rows(
            soup, "individuals",
            ["name", "reference_number", "type_of_individual", "effective_date", "date_withdrawn"],
        )
        regulatory_actions = parse_table_rows(soup, "regulatory", ["title", "category", "date_of_use"])
    except Exception as exc:
        bus.emit(
            "PARSER_ERROR",
            company=JULIUS_BAER_NAME,
            url=JULIUS_BAER_URL,
            stage="PARSE",
            error=str(exc),
            exception=exc,
            message="The page was retrieved successfully, but parsing raised an exception. This may indicate a parser bug or a website structure change.",
        )
        bus.emit("FIRM_FAILED", company=JULIUS_BAER_NAME, stage="PARSE", error=str(exc))
        bus.emit("RUN_FINISHED", message="Run ended after a parser failure.")
        return {"success": False, "stage": "PARSE"}

    bus.emit(
        "FIRM_FIELDS_PARSED",
        company=JULIUS_BAER_NAME,
        counts={"fields_found": len(firm_fields), "financial_service_categories": len(financial_services)},
    )

    # Financial Service / Investments parser gap (previously surfaced as a
    # PARSER_WARNING) is now fixed -- see parse_financial_services in
    # poc_firm_detail.py, written against the real DOM shape confirmed via
    # Console diagnostics (a third, distinct row pattern: div.table-row
    # with class spcl_row1/spcl_row2 alternating for zebra-striping, NOT
    # the label/value pattern or the <a> table-row pattern used elsewhere
    # on this page). Still worth a warning if a firm genuinely has none,
    # since that's a real data-quality signal, not a parser failure.
    if not financial_services:
        bus.emit(
            "PARSER_WARNING",
            company=JULIUS_BAER_NAME,
            message="No Financial Service / Investments entries found. This may be a genuinely unlicensed-for-services firm, or worth spot-checking against the live page.",
        )

    bus.emit(
        "INDIVIDUALS_PARSED",
        company=JULIUS_BAER_NAME,
        counts={"rows_found": len(individuals)},
    )
    bus.emit(
        "REGULATORY_ACTIONS_PARSED",
        company=JULIUS_BAER_NAME,
        counts={"rows_found": len(regulatory_actions)},
    )

    # --- VALIDATE ----------------------------------------------------
    bus.emit("VALIDATION_STARTED", company=JULIUS_BAER_NAME)
    required_fields = ["Legal Status", "DFSA Reference Number"]
    missing = [f for f in required_fields if not firm_fields.get(f)]
    if missing:
        bus.emit(
            "VALIDATION_WARNING",
            company=JULIUS_BAER_NAME,
            message=f"Missing required field(s): {', '.join(missing)}",
        )

    record = {
        "url": JULIUS_BAER_URL,
        "firm_details": firm_fields,
        "financial_services": financial_services,
        "individuals": individuals,
        "regulatory_actions": regulatory_actions,
    }

    # --- COMPLETE ----------------------------------------------------
    bus.emit(
        "FIRM_COMPLETED",
        company=JULIUS_BAER_NAME,
        url=JULIUS_BAER_URL,
        counts={
            "firm_fields": len(firm_fields),
            "financial_service_categories": len(financial_services),
            "individuals": len(individuals),
            "regulatory_actions": len(regulatory_actions),
        },
        extra={"record": record},
    )
    bus.emit("RUN_FINISHED", message="Julius Baer test run completed successfully.")

    return {"success": True, "record": record}


if __name__ == "__main__":
    # Headless mode -- proves the scraper runs with NO dashboard attached
    # at all (requirement 5). Just prints each event as a one-liner.
    bus = EventBus()
    bus.subscribe(lambda e: print(f"[{e['type']}] {e.get('company') or ''} {e.get('message') or ''}"))
    result = run_julius_baer_test(bus)
    print("\nDone. success =", result["success"])