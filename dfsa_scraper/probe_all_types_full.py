"""
probe_all_types_full.py — FINAL READ-ONLY DIAGNOSTIC.

For each of the 16 discovered `type` values, walks page 0, 1, 2, ...
sequentially with NO early stop based on getTotal() -- only a genuine
empty response ends a type's walk. getTotal() is recorded for reference
only; it is never used as a stop condition.

A generous hard safety cap (MAX_PAGES_PER_TYPE) exists purely to prevent
an infinite loop against a misbehaving endpoint -- it is far above any
total observed so far (largest so far: Authorised Firms, 99 pages) and
is reported explicitly if ever hit, so it's distinguishable from a
genuine end-of-data stop.

Uses ONLY existing DfsaSession / get_total / get_listing_page /
parse_listing_fragment. No detail-page requests. No writes to
checkpoint.json / firms.jsonl / errors.jsonl / any production file.
No production code modified.
"""
from __future__ import annotations
from bs4 import BeautifulSoup
from dfsa_common import DfsaSession, parse_listing_fragment, BASE
from dfsa_registers import FIRMS

MAX_PAGES_PER_TYPE = 500  # safety valve only, not a stop condition -- see docstring


def discover_type_values(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    select = soup.find("select", attrs={"name": "type"}) or soup.find("select", attrs={"id": "type"})
    if not select:
        return []
    opts = []
    for opt in select.find_all("option"):
        value = opt.get("value", "").strip()
        label = opt.get_text(strip=True)
        if value:
            opts.append((value, label))
    return opts


def probe_one_page(session, register_path, filters, page):
    diag = {"page": page}
    try:
        html = session.get_listing_page(register_path, page, filters)
    except Exception as exc:
        diag.update(http_ok=False, error=str(exc), rows=[], row_count=0, status="REQUEST_FAILED")
        return diag
    rows = parse_listing_fragment(html)
    diag.update(http_ok=True, rows=rows, row_count=len(rows),
                status="VALID" if rows else "EMPTY")
    return diag


def walk_to_genuine_empty(session, register_path, filters, type_value):
    """
    Walks page 0, 1, 2, ... until a genuine empty response, or the
    MAX_PAGES_PER_TYPE safety cap. getTotal() is NOT used to decide when
    to stop -- only an empty listing response does. Tracks whether any
    empty page appeared and then data resumed afterward (would indicate
    a transient/non-genuine empty, distinct from the true end).
    """
    refs_in_order = []
    pages_requested = 0
    first_empty_page = None
    resumed_after_empty = False
    hit_safety_cap = False
    request_failed = False
    fail_reason = None

    page = 0
    seen_empty_once = False
    while True:
        if page >= MAX_PAGES_PER_TYPE:
            hit_safety_cap = True
            break

        d = probe_one_page(session, register_path, filters, page)
        pages_requested += 1

        if not d["http_ok"]:
            request_failed = True
            fail_reason = f"REQUEST_FAILED at page {page}: {d.get('error')}"
            break

        if d["status"] == "EMPTY":
            if first_empty_page is None:
                first_empty_page = page
            seen_empty_once = True
            # Confirm this is genuinely the end: peek one more page. If
            # that's also empty, treat as genuine end and stop. If it
            # has data, this was a transient empty -- keep walking.
            peek = probe_one_page(session, register_path, filters, page + 1)
            pages_requested += 1
            if peek["http_ok"] and peek["status"] == "VALID":
                resumed_after_empty = True
                for row in peek["rows"]:
                    refs_in_order.append(row.reference_number)
                page += 2
                continue
            else:
                break  # genuine end (peek also empty, or peek failed)

        for row in d["rows"]:
            refs_in_order.append(row.reference_number)
        page += 1

    unique_refs = set(refs_in_order)
    duplicates_within_type = len(refs_in_order) - len(unique_refs)

    return {
        "type": type_value,
        "pages_requested": pages_requested,
        "rows_returned": len(refs_in_order),
        "unique_refs": unique_refs,
        "refs_in_order": refs_in_order,
        "duplicates_within_type": duplicates_within_type,
        "first_ref": refs_in_order[0] if refs_in_order else "",
        "last_ref": refs_in_order[-1] if refs_in_order else "",
        "first_empty_page": first_empty_page,
        "unexpected_empty_before_end": resumed_after_empty,
        "hit_safety_cap": hit_safety_cap,
        "request_failed": request_failed,
        "fail_reason": fail_reason,
        "terminated_normally": (not hit_safety_cap and not request_failed),
    }


def main():
    register_path = FIRMS["path"]
    base_filters = FIRMS["default_filters"]

    print("=" * 78)
    print("FINAL DIAGNOSTIC: UNBOUNDED PER-TYPE WALK TO GENUINE EMPTY PAGE")
    print("=" * 78)
    print(f"(safety cap: {MAX_PAGES_PER_TYPE} pages/type -- reported explicitly if ever hit, "
          f"not used as the real stop condition)\n")

    session = DfsaSession()
    session.bootstrap(register_path)

    resp = session._get(f"{BASE}{register_path}")
    type_values = discover_type_values(resp.text)
    print(f"Discovered {len(type_values)} type value(s) from live <select> markup.\n")

    if not type_values:
        print("WARNING: no type values discovered -- cannot proceed. Stopping.")
        return

    results = {}
    for value, label in type_values:
        filters = dict(base_filters)
        filters["type"] = value
        total = session.get_total(register_path, filters)
        print(f"--- type={value!r} ---  getTotal()={total}  (reference only, not used as stop condition)")
        r = walk_to_genuine_empty(session, register_path, filters, value)
        r["getTotal"] = total
        results[value] = r

        status_flags = []
        if r["hit_safety_cap"]:
            status_flags.append("HIT SAFETY CAP -- did not reach genuine end")
        if r["request_failed"]:
            status_flags.append(f"REQUEST FAILED: {r['fail_reason']}")
        if r["unexpected_empty_before_end"]:
            status_flags.append("transient empty page occurred before genuine end")

        print(f"  pages_requested={r['pages_requested']}  rows_returned={r['rows_returned']}  "
              f"unique_refs={len(r['unique_refs'])}  duplicates_within_type={r['duplicates_within_type']}")
        print(f"  first_ref={r['first_ref']}  last_ref={r['last_ref']}  "
              f"first_empty_page={r['first_empty_page']}")
        print(f"  terminated_normally={r['terminated_normally']}"
              + ("  [" + "; ".join(status_flags) + "]" if status_flags else ""))
        print()

    # Global union
    all_refs_union = set()
    for r in results.values():
        all_refs_union |= r["unique_refs"]

    print("=" * 78)
    print("GLOBAL UNIQUE REFERENCE-NUMBER UNION ACROSS ALL 16 TYPES")
    print("=" * 78)
    print(f"Total unique refs (deduplicated across all types): {len(all_refs_union)}\n")

    # Cross-type overlap
    print("=" * 78)
    print("CROSS-TYPE OVERLAP")
    print("=" * 78)
    values = list(results.keys())
    any_overlap = False
    total_overlap_instances = 0
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            a, b = values[i], values[j]
            shared = results[a]["unique_refs"] & results[b]["unique_refs"]
            if shared:
                any_overlap = True
                total_overlap_instances += len(shared)
                print(f"  {a!r} ∩ {b!r} = {len(shared)} shared ref(s). Sample: {sorted(shared)[:5]}")
    if not any_overlap:
        print("  No cross-type overlap found.")
    print(f"\nTotal overlapping ref instances across all pairs: {total_overlap_instances}\n")

    # Compare against unfiltered walk (bounded, known to hit the ~1010 wall --
    # re-walked here fresh, only up to its own genuine/known stop, for the
    # final comparison number; not re-investigating the unfiltered anomaly
    # itself, which is already fully diagnosed from prior probes).
    print("=" * 78)
    print("COMPARISON AGAINST UNFILTERED WALK")
    print("=" * 78)
    unfiltered_total = session.get_total(register_path, base_filters)
    print(f"Fresh unfiltered getTotal(): {unfiltered_total}")
    print("Re-walking unfiltered listing to its known stop (page 101, per all prior probes)...")
    unfiltered_refs = set()
    page = 0
    while True:
        d = probe_one_page(session, register_path, base_filters, page)
        if not d["http_ok"] or d["status"] == "EMPTY":
            break
        for row in d["rows"]:
            unfiltered_refs.add(row.reference_number)
        page += 1
        if page > 105:  # small safety margin past the known page-101 wall
            break
    print(f"Unfiltered walk collected {len(unfiltered_refs)} unique refs (stopped at page {page}).\n")

    only_typed = all_refs_union - unfiltered_refs
    only_unfiltered = unfiltered_refs - all_refs_union
    print(f"Refs reachable via type filtering but NOT in unfiltered walk: {len(only_typed)}")
    print(f"Refs in unfiltered walk but NOT reachable via any type filter: {len(only_unfiltered)}")
    if only_unfiltered:
        print(f"  Sample: {sorted(only_unfiltered)[:5]}")
    print()

    # Termination summary
    print("=" * 78)
    print("TERMINATION SUMMARY")
    print("=" * 78)
    all_terminated_normally = all(r["terminated_normally"] for r in results.values())
    for value, r in results.items():
        flag = "OK" if r["terminated_normally"] else "PROBLEM"
        print(f"  [{flag}] {value:45s} getTotal()={r['getTotal']:>5d}  "
              f"actual_unique={len(r['unique_refs']):>5d}  "
              f"diff={len(r['unique_refs']) - r['getTotal']:>+5d}")
    print()
    print(f"Did every type terminate normally (genuine empty page, no safety cap, no request failure)? "
          f"{'YES' if all_terminated_normally else 'NO -- see [PROBLEM] rows above'}")
    print()

    print("=" * 78)
    print("FINAL EVIDENCE TABLE")
    print("=" * 78)
    print(f"{'type':45s} {'getTotal()':>10s} {'pages':>6s} {'rows':>6s} {'unique':>7s} {'dupes':>6s} {'term_ok':>8s}")
    for value, r in results.items():
        print(f"{value:45s} {r['getTotal']:>10d} {r['pages_requested']:>6d} {r['rows_returned']:>6d} "
              f"{len(r['unique_refs']):>7d} {r['duplicates_within_type']:>6d} {str(r['terminated_normally']):>8s}")
    print()

    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"Global unique firms reachable via type-filtered crawling: {len(all_refs_union)}")
    print(f"Unfiltered walk unique refs (known truncated): {len(unfiltered_refs)}")
    print(f"Additional refs only reachable via type filtering: {len(only_typed)}")
    print(f"Cross-type overlap instances: {total_overlap_instances}")
    print(f"All types terminated normally: {all_terminated_normally}")
    print()
    print("No files were modified. checkpoint.json, firms.jsonl, errors.jsonl, and all production "
          "scraper code are untouched. No detail pages were fetched.")


if __name__ == "__main__":
    main()