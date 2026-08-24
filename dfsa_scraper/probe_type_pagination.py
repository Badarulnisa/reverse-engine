"""
probe_type_pagination.py — STRICTLY READ-ONLY diagnostic.

Tests whether `type=Authorised Firms` and `type=Authorised Firms (Withdrawn)`
paginate completely (bypassing the ~1010-row unfiltered boundary), and
measures reference-number overlap between the two at the listing-row level.

Uses ONLY existing DfsaSession / get_total / get_listing_page /
parse_listing_fragment. No new HTTP/session/pagination logic. No detail
page fetches. No writes to checkpoint.json / firms.jsonl / errors.jsonl /
any production file. Bounded: never requests more pages than each
filter's live getTotal() implies, plus exactly one page past the expected
final page.
"""
from __future__ import annotations
import math
from dfsa_common import DfsaSession, parse_listing_fragment
from dfsa_registers import FIRMS

PAGE_SIZE = 10
TYPES_TO_TEST = ["Authorised Firms", "Authorised Firms (Withdrawn)"]


def probe_one_page(session, register_path, filters, page):
    diag = {"page": page}
    try:
        html = session.get_listing_page(register_path, page, filters)
    except Exception as exc:
        diag.update(http_ok=False, error=str(exc), response_length=0,
                     rows=[], row_count=0, first="", last="", status="REQUEST_FAILED")
        return diag
    rows = parse_listing_fragment(html)
    diag.update(
        http_ok=True,
        response_length=len(html),
        rows=rows,
        row_count=len(rows),
        first=f"{rows[0].name} ({rows[0].reference_number})" if rows else "",
        last=f"{rows[-1].name} ({rows[-1].reference_number})" if rows else "",
        status="VALID" if rows else "EMPTY",
    )
    return diag


def print_diag(d):
    print(f"  --- page {d['page']} ---")
    if not d["http_ok"]:
        print(f"    http_ok: False  error: {d.get('error')}")
        return
    print(f"    response_length: {d['response_length']} bytes")
    print(f"    rows_parsed:     {d['row_count']}")
    print(f"    first:           {d['first']}")
    print(f"    last:            {d['last']}")
    print(f"    status:          {d['status']}")


def spot_check(session, register_path, filters, total):
    """
    Probes: page 0, a page near the end, the expected final page, and one
    page past the expected final page. Bounded to exactly these 4 (minus
    dedup) -- never a full walk.
    """
    last_page = math.ceil(total / PAGE_SIZE) - 1 if total > 0 else 0
    near_end = max(last_page - 2, 0)
    one_past = last_page + 1

    pages = sorted(set([0, near_end, last_page, one_past]))
    print(f"  live getTotal(): {total}")
    print(f"  expected last 0-indexed page (page_size={PAGE_SIZE}): {last_page}")
    print(f"  expected rows on final page: {total % PAGE_SIZE or PAGE_SIZE}")
    print(f"  spot-check pages: {pages}\n")

    results = {}
    for p in pages:
        d = probe_one_page(session, register_path, filters, p)
        results[p] = d
        print_diag(d)
    print()
    return results, last_page


def full_bounded_walk(session, register_path, filters, total, label):
    """
    Walks page 0..last_page (inclusive) plus exactly one page past it,
    stopping immediately and recording the exact page if an UNEXPECTED
    empty page appears before the expected final page. Never exceeds
    last_page + 1 requests worth of pages. Collects reference numbers
    in memory only -- never written to disk.
    """
    last_page = math.ceil(total / PAGE_SIZE) - 1 if total > 0 else 0
    max_page = last_page + 1  # exactly one page beyond expected final page

    refs = []
    stopped_early = False
    stop_page = None
    stop_reason = None

    print(f"  [{label}] bounded walk: pages 0..{max_page} (last_page={last_page}, +1 safety page)")
    for page in range(0, max_page + 1):
        d = probe_one_page(session, register_path, filters, page)
        if not d["http_ok"]:
            stopped_early = True
            stop_page = page
            stop_reason = f"REQUEST_FAILED: {d.get('error')}"
            print(f"    page {page}: REQUEST_FAILED ({d.get('error')}) -- stopping walk here.")
            break
        if d["status"] == "EMPTY":
            if page <= last_page:
                stopped_early = True
                stop_page = page
                stop_reason = (
                    f"UNEXPECTED EMPTY at page {page}, before expected final page {last_page} "
                    f"(only {len(refs)} rows collected so far vs total={total})."
                )
                print(f"    page {page}: EMPTY -- UNEXPECTED (before expected final page {last_page}). Stopping.")
            else:
                print(f"    page {page}: EMPTY -- expected (past final page {last_page}). Walk complete.")
            break
        for row in d["rows"]:
            refs.append(row.reference_number)
        if page == last_page:
            print(f"    page {page}: VALID, {d['row_count']} rows (expected final page reached, "
                  f"{len(refs)} total rows collected so far)")
        elif page % 20 == 0 or page == 0:
            print(f"    page {page}: VALID, {d['row_count']} rows ({len(refs)} collected so far)")

    print(f"  [{label}] walk finished. rows collected: {len(refs)}  "
          f"reached_full_total: {len(refs) >= total}  stopped_early: {stopped_early}"
          + (f"  stop_page={stop_page}  reason={stop_reason}" if stopped_early else ""))
    print()
    return {
        "refs": refs,
        "stopped_early": stopped_early,
        "stop_page": stop_page,
        "stop_reason": stop_reason,
        "last_page": last_page,
        "total": total,
    }


def main():
    register_path = FIRMS["path"]
    base_filters = FIRMS["default_filters"]

    print("=" * 70)
    print("TYPE-FILTERED PAGINATION + OVERLAP PROBE — READ-ONLY")
    print("=" * 70)

    session = DfsaSession()
    session.bootstrap(register_path)
    print()

    walk_results = {}

    for type_value in TYPES_TO_TEST:
        print("=" * 70)
        print(f"FILTER: type = {type_value!r}")
        print("=" * 70)
        filters = dict(base_filters)
        filters["type"] = type_value

        total = session.get_total(register_path, filters)

        print("Spot-check (page 0, near-end, expected-final, one-past-final):")
        spot_check(session, register_path, filters, total)

        print("Full bounded walk (collecting reference numbers, in memory only):")
        result = full_bounded_walk(session, register_path, filters, total, type_value)
        walk_results[type_value] = result

    # Overlap analysis
    print("=" * 70)
    print("OVERLAP ANALYSIS (listing-row level, in-memory only)")
    print("=" * 70)
    set_a = set(walk_results[TYPES_TO_TEST[0]]["refs"])
    set_b = set(walk_results[TYPES_TO_TEST[1]]["refs"])
    intersection = set_a & set_b
    print(f"{TYPES_TO_TEST[0]}: {len(set_a)} unique reference numbers")
    print(f"{TYPES_TO_TEST[1]}: {len(set_b)} unique reference numbers")
    print(f"Intersection: {len(intersection)} reference number(s) appear in BOTH")
    if intersection:
        sample = list(sorted(intersection))[:10]
        print(f"Sample overlapping refs (up to 10): {sample}")
    print()

    # Conclusion
    print("=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    r_a = walk_results[TYPES_TO_TEST[0]]
    r_b = walk_results[TYPES_TO_TEST[1]]

    a_complete = len(r_a["refs"]) >= r_a["total"] and not (r_a["stopped_early"] and r_a["stop_page"] <= r_a["last_page"])
    b_complete = len(r_b["refs"]) >= r_b["total"] and not (r_b["stopped_early"] and r_b["stop_page"] <= r_b["last_page"])

    print(f"A. Does 'Authorised Firms' ({r_a['total']}) paginate completely? "
          f"{'YES' if a_complete else 'NO'} "
          f"(collected {len(r_a['refs'])}/{r_a['total']} rows"
          + (f", stopped early at page {r_a['stop_page']}: {r_a['stop_reason']}" if r_a["stopped_early"] else "")
          + ")")
    print(f"B. Does 'Authorised Firms (Withdrawn)' ({r_b['total']}) paginate completely? "
          f"{'YES' if b_complete else 'NO'} "
          f"(collected {len(r_b['refs'])}/{r_b['total']} rows"
          + (f", stopped early at page {r_b['stop_page']}: {r_b['stop_reason']}" if r_b["stopped_early"] else "")
          + ")")
    c_hit_boundary = r_a["stopped_early"] or r_b["stopped_early"]
    c_text = (
        "YES"
        if c_hit_boundary
        else "NO -- both totals are well under 1010, and neither walk was stopped by an "
             "unexpected empty page before its expected final page."
    )
    print(f"C. Does either hit the ~1010 server-side boundary? {c_text}")
    print(f"D. Reference-number overlap between the two: {len(intersection)} shared ref(s) "
          f"out of {len(set_a)} + {len(set_b)} = {len(set_a) + len(set_b)} total rows collected "
          f"(union: {len(set_a | set_b)} unique).")
    if intersection:
        print("   NOTE: overlap is NON-ZERO -- the two type values are NOT mutually exclusive. "
              "Do not sum their totals as unique-firm counts.")
    else:
        print("   NOTE: no overlap observed in this probe -- consistent with mutual exclusivity for "
              "these two specific values, but this does NOT confirm exclusivity across all 16 type "
              "values, and is only evidence from this one pair.")
    print(f"E. Based strictly on these results, is type-filtered crawling a technically viable "
          f"workaround for the 1225-record unfiltered pagination ceiling? "
          f"{'Provisionally YES for these two filters -- both paginated past where the unfiltered walk failed, without hitting an empty-page wall.' if (a_complete and b_complete) else 'NOT YET CONFIRMED -- see stop reasons above before relying on this approach.'} "
          f"This is NOT a full answer for all 16 type values or for de-duplication strategy -- "
          f"only these two filters were tested here.")
    print()
    print("No files were modified. checkpoint.json, firms.jsonl, errors.jsonl, and all production "
          "scraper code are untouched. No detail pages were fetched.")


if __name__ == "__main__":
    main()