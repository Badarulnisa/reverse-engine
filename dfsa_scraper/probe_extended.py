"""
probe_extended.py — STRICTLY READ-ONLY. Extends prob_register.py's probe.

Does NOT touch checkpoint.json / firms.jsonl / errors.jsonl. Does NOT
fetch detail pages. Does NOT run the full scraper.

Tests: 99, 100, 101, 102, 103, 105, 110, 115, 120, 121, 122, 123
Plus: re-tests page 101 with a completely fresh, independent
DfsaSession.bootstrap() call (separate session object).
"""
from __future__ import annotations
import math
from dfsa_common import DfsaSession, parse_listing_fragment
from dfsa_registers import FIRMS

PAGE_SIZE = 10
PAGES = [99, 100, 101, 102, 103, 105, 110, 115, 120, 121, 122, 123]


def probe_page(session, register_path, filters, page):
    diag = {"page": page}
    try:
        html = session.get_listing_page(register_path, page, filters)
    except Exception as exc:
        diag.update(http_ok=False, error=str(exc), response_length=0,
                     rows=0, first="", last="", status="REQUEST_FAILED")
        return diag
    rows = parse_listing_fragment(html)
    diag.update(
        http_ok=True,
        response_length=len(html),
        rows=len(rows),
        first=f"{rows[0].name} ({rows[0].reference_number})" if rows else "",
        last=f"{rows[-1].name} ({rows[-1].reference_number})" if rows else "",
        status="VALID" if rows else "EMPTY",
        raw_len_stripped=len(html.strip()) if html else 0,
    )
    return diag


def print_diag(d):
    print(f"--- page {d['page']} ---")
    if not d["http_ok"]:
        print(f"  http_ok: False  error: {d.get('error')}")
        print()
        return
    print(f"  response_length: {d['response_length']} bytes  (stripped: {d.get('raw_len_stripped')})")
    print(f"  rows_parsed:     {d['rows']}")
    print(f"  first:           {d['first']}")
    print(f"  last:            {d['last']}")
    print(f"  status:          {d['status']}")
    print()


def main():
    register_path = FIRMS["path"]
    filters = FIRMS["default_filters"]

    print("=" * 70)
    print("EXTENDED READ-ONLY PROBE — no production files touched")
    print("=" * 70)

    session = DfsaSession()
    token = session.bootstrap(register_path)
    print(f"csrf_token: {token}\n")

    live_total = session.get_total(register_path, filters)
    print(f"LIVE getTotal(): {live_total}")
    last_page_idx = math.ceil(live_total / PAGE_SIZE) - 1
    remainder = live_total % PAGE_SIZE
    print(f"Expected last page index (0-indexed, page_size={PAGE_SIZE}): {last_page_idx}")
    print(f"Expected rows on final page: {remainder if remainder else PAGE_SIZE}")
    print()

    results = {}
    for p in PAGES:
        d = probe_page(session, register_path, filters, p)
        results[p] = d
        print_diag(d)

    # Independent fresh session re-test of page 101
    print("=" * 70)
    print("INDEPENDENT FRESH SESSION RE-TEST OF PAGE 101")
    print("=" * 70)
    session2 = DfsaSession()
    token2 = session2.bootstrap(register_path)
    print(f"csrf_token (session 2): {token2}")
    print(f"same token as session 1? {token2 == token}")
    d101b = probe_page(session2, register_path, filters, 101)
    print_diag(d101b)

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    valid_pages = [p for p, d in results.items() if d["http_ok"] and d["status"] == "VALID"]
    empty_pages = [p for p, d in results.items() if d["http_ok"] and d["status"] == "EMPTY"]
    print(f"VALID pages in probed set: {valid_pages}")
    print(f"EMPTY pages in probed set: {empty_pages}")
    print(f"Any data resumes after page 101? {'YES' if any(p > 101 for p in valid_pages) else 'NO'}")
    print(f"Page 101 empty on fresh independent session too? {d101b['status'] == 'EMPTY'}")


if __name__ == "__main__":
    main()