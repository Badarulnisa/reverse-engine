"""
probe_facets.py — STRICTLY READ-ONLY diagnostic.

1. Fetches the live firms register bootstrap page and extracts real
   <select> options for legal_status, type, financial_service, endorsement.
   Does NOT invent/guess values.
2. Calls get_total() once per discovered value for legal_status and type
   (financial_service/endorsement extracted too, printed, but not queried
   unless found -- kept scoped to what the handoff asked for: legal_status
   and type totals).
3. Never fetches a listing page, never fetches a detail page, never writes
   to checkpoint.json / firms.jsonl / errors.jsonl / production code.
"""
from __future__ import annotations
import time
from bs4 import BeautifulSoup
from dfsa_common import DfsaSession, BASE
from dfsa_registers import FIRMS

FACET_FIELDS = ["legal_status", "type", "financial_service", "endorsement"]


def extract_select_options(html: str) -> dict[str, list[tuple[str, str]]]:
    """
    Returns {field_name: [(value, label), ...]} for each <select> whose
    name/id matches one of FACET_FIELDS. Only real options found in the
    live markup -- nothing invented.
    """
    soup = BeautifulSoup(html, "lxml")
    result: dict[str, list[tuple[str, str]]] = {}
    for field in FACET_FIELDS:
        select = soup.find("select", attrs={"name": field}) or soup.find("select", attrs={"id": field})
        if not select:
            result[field] = []
            continue
        opts = []
        for opt in select.find_all("option"):
            value = opt.get("value", "").strip()
            label = opt.get_text(strip=True)
            opts.append((value, label))
        result[field] = opts
    return result


def main():
    register_path = FIRMS["path"]
    base_filters = FIRMS["default_filters"]

    print("=" * 70)
    print("FACET DISCOVERY + PER-FACET getTotal() PROBE — READ-ONLY")
    print("=" * 70)

    session = DfsaSession()
    token = session.bootstrap(register_path)
    print(f"csrf_token: {token}\n")

    # Step 1: fetch the bootstrap page HTML again explicitly for select
    # extraction (bootstrap() already fetched it internally but didn't
    # expose the parsed HTML back to us -- re-fetch via the same _get()
    # path, still read-only, still just a GET of the listing page).
    resp = session._get(f"{BASE}{register_path}")
    html = resp.text

    facets = extract_select_options(html)

    print("DISCOVERED FILTER OPTIONS (from live <select> markup):")
    for field in FACET_FIELDS:
        opts = facets[field]
        print(f"\n  {field}: {len(opts)} option(s) found")
        for value, label in opts:
            print(f"    value={value!r:30s} label={label!r}")
    print()

    if not facets["legal_status"] and not facets["type"]:
        print("WARNING: no <select> options found for legal_status or type via "
              "name/id lookup. The page may render these via JS-populated "
              "markup not present in the raw GET, or use different "
              "name/id attributes than assumed here. Cannot proceed with "
              "per-facet getTotal() calls without real values -- stopping "
              "rather than guessing.")
        return

    live_total_unfiltered = session.get_total(register_path, base_filters)
    print(f"LIVE getTotal() (unfiltered, baseline): {live_total_unfiltered}\n")

    results = {}  # field -> list of (value, label, total)

    for field in ("legal_status", "type"):
        opts = facets[field]
        if not opts:
            print(f"No options discovered for '{field}' -- skipping per-facet totals for this field.\n")
            continue
        results[field] = []
        print(f"--- Per-value getTotal() for facet: {field} ---")
        for value, label in opts:
            if not value:
                # blank/"all" option -- same as baseline, skip re-querying
                continue
            filters = dict(base_filters)
            filters[field] = value
            total = session.get_total(register_path, filters)
            results[field].append((value, label, total))
            print(f"  {field}={value!r:25s} ({label!r:35s}) -> getTotal() = {total}")
            time.sleep(0.3)
        print()

    # Table + sums
    print("=" * 70)
    print("SUMMARY TABLE")
    print("=" * 70)
    print(f"{'facet':15s} {'value':25s} {'label':35s} {'getTotal()':>10s}")
    print("-" * 90)
    for field, rows in results.items():
        for value, label, total in rows:
            print(f"{field:15s} {value:25s} {label:35s} {total:>10d}")

    print()
    for field, rows in results.items():
        s = sum(t for _, _, t in rows)
        print(f"SUM of getTotal() across all discovered '{field}' values: {s}")
        print(f"  (unfiltered baseline total: {live_total_unfiltered})")
        print(f"  NOTE: this sum assumes mutual exclusivity, which has NOT been "
              f"verified. A firm could plausibly match zero, one, or more than "
              f"one {field} value depending on what the field represents -- "
              f"do not treat this sum as a confirmed unique-firm count without "
              f"further evidence (e.g. cross-checking overlap directly).")
        print()

    print("No files were modified. checkpoint.json, firms.jsonl, errors.jsonl, "
          "and all production scraper code are untouched.")


if __name__ == "__main__":
    main()