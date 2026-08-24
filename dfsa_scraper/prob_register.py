"""
probe_register.py

STRICTLY READ-ONLY diagnostic probe. Reuses the existing DfsaSession /
walk_register machinery from dfsa_common.py and dfsa_registers.py --
does NOT reimplement bootstrap, csrf handling, or pagination.

This script:
  - calls get_total() once, fresh
  - reads checkpoint.json (if present) ONLY to estimate where the last
    run stopped, purely to decide which pages are interesting to probe
  - fetches a small, bounded set of listing pages around the ~1010-row
    boundary and prints a diagnostic row per page

It NEVER writes to, appends to, or deletes:
  - firms.jsonl
  - checkpoint.json
  - errors.jsonl
  - any other production/output file

No production run is started. No detail pages are fetched (this probes
the LISTING/pagination layer only, since that's where the anomaly is).

Usage (from your existing project/venv, in the same directory as
dfsa_common.py / dfsa_registers.py / checkpoint.json):

    python3 probe_register.py

Optional: override how many pages past the anomaly to check, or force a
different suspected stop page:

    python3 probe_register.py --after 5
    python3 probe_register.py --stop-page 101
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dfsa_common import DfsaSession, parse_listing_fragment
from dfsa_registers import FIRMS

PAGE_SIZE = 10  # confirmed in dfsa_common.py docstring/comments
CHECKPOINT_PATH = "checkpoint.json"


def estimate_stop_page_from_checkpoint(path: str) -> int | None:
    """
    READ-ONLY. Uses the existing checkpoint.json purely to guess which
    page the previous walk likely stopped on (checkpoint entry count //
    page size). This is an estimate for probe targeting only -- it is
    never written back to.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        checkpoint = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    n = len(checkpoint)
    if n == 0:
        return None
    return n // PAGE_SIZE


def row_summary(rows) -> tuple[str, str]:
    if not rows:
        return "", ""
    first = f"{rows[0].name} ({rows[0].reference_number})"
    last = f"{rows[-1].name} ({rows[-1].reference_number})"
    return first, last


def count_internal_duplicates(rows) -> int:
    seen = set()
    dupes = 0
    for r in rows:
        key = r.detail_url or r.reference_number
        if key in seen:
            dupes += 1
        else:
            seen.add(key)
    return dupes


def probe_page(session: DfsaSession, register_path: str, filters: dict, page: int) -> dict:
    """
    Fetches exactly one listing page via the EXISTING session/pagination
    code (session.get_listing_page -> same _get() retry/backoff logic
    used by the real scraper) and returns a diagnostic dict. Does not
    touch detail pages, does not write anything to disk.
    """
    diag = {"page": page, "params": {**filters, "page": page, "isAjax": "true"}}
    try:
        html = session.get_listing_page(register_path, page, filters)
        diag["http_ok"] = True
    except Exception as exc:  # noqa: BLE001 -- diagnostic tool, want to see everything
        diag["http_ok"] = False
        diag["error"] = str(exc)
        diag["response_length"] = 0
        diag["rows"] = []
        diag["row_count"] = 0
        diag["first"] = ""
        diag["last"] = ""
        diag["internal_duplicates"] = 0
        diag["status"] = "REQUEST_FAILED"
        return diag

    diag["response_length"] = len(html)
    rows = parse_listing_fragment(html)
    diag["rows"] = rows
    diag["row_count"] = len(rows)
    diag["first"], diag["last"] = row_summary(rows)
    diag["internal_duplicates"] = count_internal_duplicates(rows)
    diag["status"] = "EMPTY" if not rows else "VALID"
    return diag


def print_row(diag: dict, live_total: int, seen_so_far: int) -> None:
    print(f"--- page {diag['page']} ---")
    print(f"  params:              {diag['params']}")
    print(f"  http_ok:             {diag['http_ok']}")
    if not diag["http_ok"]:
        print(f"  error:               {diag.get('error')}")
        print(f"  status:              {diag['status']}")
        print()
        return
    print(f"  response_length:     {diag['response_length']} bytes")
    print(f"  rows_parsed:         {diag['row_count']}")
    print(f"  first_row:           {diag['first']}")
    print(f"  last_row:            {diag['last']}")
    print(f"  internal_duplicates: {diag['internal_duplicates']}")
    print(f"  status:              {diag['status']}")
    if diag["status"] == "EMPTY":
        if live_total and seen_so_far < live_total:
            print(f"  >>> EMPTY RESPONSE — seen < live_total ({seen_so_far} < {live_total})")
        else:
            print(f"  >>> EMPTY RESPONSE — seen >= live_total ({seen_so_far} >= {live_total}), "
                  f"consistent with genuine end of register")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--after", type=int, default=3, help="pages to probe after the suspected stop page (default 3)")
    ap.add_argument("--stop-page", type=int, default=None, help="override the suspected stop page (default: derived from checkpoint.json)")
    args = ap.parse_args()

    register_path = FIRMS["path"]
    filters = FIRMS["default_filters"]

    print("=" * 70)
    print("DFSA REGISTER PROBE — READ-ONLY, NO DATA FILES WILL BE MODIFIED")
    print("=" * 70)
    print(f"register_path: {register_path}")
    print(f"filters:       {filters}")
    print()

    session = DfsaSession()

    # Step 1: bootstrap (reuses existing csrf/session logic, same as a
    # real run would do -- no separate implementation here).
    print("Bootstrapping session (reusing DfsaSession.bootstrap)...")
    token = session.bootstrap(register_path)
    print(f"  csrf_token acquired: {bool(token)}  ({token if token else 'NONE -- proceeding without one'})")
    print()

    # Step 2: live getTotal, called exactly once.
    live_total = session.get_total(register_path, filters)
    print(f"LIVE getTotal() RESULT: {live_total}")
    print()

    # Step 3: estimate the previous stop page from checkpoint.json
    # (read-only -- never written to).
    estimated_stop_page = args.stop_page
    source = "explicit --stop-page override"
    if estimated_stop_page is None:
        estimated_stop_page = estimate_stop_page_from_checkpoint(CHECKPOINT_PATH)
        source = f"derived from {CHECKPOINT_PATH} entry count // {PAGE_SIZE}"
    if estimated_stop_page is None:
        estimated_stop_page = 100  # ~1000 rows / 10 per page, fallback if no checkpoint found
        source = "no checkpoint.json found, falling back to page 100 (~row 1000)"
    print(f"Suspected stop page: {estimated_stop_page}  ({source})")
    print()

    # Step 4: build the bounded set of pages to probe --
    #   pages 99-102 (the ~1000-row boundary), the suspected stop page
    #   itself, and N pages after it. De-duplicated, sorted, bounded --
    #   this script deliberately never walks the whole register.
    pages_to_check = set(range(99, 103))
    pages_to_check.add(estimated_stop_page)
    pages_to_check.update(range(estimated_stop_page + 1, estimated_stop_page + 1 + args.after))
    pages_to_check = sorted(p for p in pages_to_check if p >= 0)

    print(f"Pages to probe (bounded, {len(pages_to_check)} total): {pages_to_check}")
    print()

    results = []
    seen_so_far = estimated_stop_page * PAGE_SIZE  # rough running estimate, diagnostic only
    for page in pages_to_check:
        diag = probe_page(session, register_path, filters, page)
        results.append(diag)
        if diag["http_ok"]:
            seen_so_far += diag["row_count"]
        print_row(diag, live_total, seen_so_far)

    # Step 5: cross-page comparison to help distinguish genuine
    # end-of-register vs. transient/stale-session emptiness vs. skipped
    # pages vs. duplicate pages.
    print("=" * 70)
    print("CROSS-PAGE COMPARISON")
    print("=" * 70)
    prev = None
    first_anomalous_page = None
    last_valid_page = None
    rows_before_anomaly = 0
    rows_after_anomaly = 0
    running_rows = 0

    for diag in results:
        if diag["http_ok"] and diag["status"] == "VALID":
            last_valid_page = diag["page"]
            running_rows += diag["row_count"]
        if diag["status"] in ("EMPTY", "REQUEST_FAILED") and first_anomalous_page is None:
            first_anomalous_page = diag["page"]
            rows_before_anomaly = running_rows

        if prev is not None:
            if prev["status"] == "VALID" and diag["status"] == "VALID" and prev["last"] == diag["first"]:
                print(f"  NOTE: page {prev['page']} last row == page {diag['page']} first row "
                      f"-- possible duplicate/overlapping page or off-by-one pagination.")
            if prev["status"] == "EMPTY" and diag["status"] == "VALID":
                print(f"  NOTE: page {prev['page']} was EMPTY but page {diag['page']} is VALID "
                      f"-- strong evidence of a TRANSIENT empty response, not genuine end of register.")
        prev = diag

    for diag in results:
        if first_anomalous_page is not None and diag["page"] > first_anomalous_page and diag["http_ok"] and diag["status"] == "VALID":
            rows_after_anomaly += diag["row_count"]

    print()
    print("=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    print(f"LIVE REGISTER TOTAL: {live_total}")
    print(f"LAST SUCCESSFUL PAGE: {last_valid_page if last_valid_page is not None else 'none in probed range'}")
    print(f"FIRST EMPTY/ANOMALOUS PAGE: {first_anomalous_page if first_anomalous_page is not None else 'none found in probed range'}")
    print(f"ROWS BEFORE ANOMALY (within probed range): {rows_before_anomaly}")
    print(f"ROWS AFTER ANOMALY (within probed range): {rows_after_anomaly}")

    if first_anomalous_page is None:
        likely_cause = "No empty/failed page encountered in the probed range -- register may extend cleanly through here; the earlier stop needs a wider probe or occurred elsewhere."
        confidence = "LOW -- probed range did not reproduce the anomaly"
    elif rows_after_anomaly > 0:
        likely_cause = ("A TRANSIENT empty response, not genuine end-of-register: at least one page after "
                         "the first empty/anomalous page returned valid rows. Consistent with a stale "
                         "csrf/session token or a momentary server hiccup rather than the register actually ending.")
        confidence = "HIGH -- valid data observed after the anomaly"
    elif live_total and (rows_before_anomaly < live_total):
        likely_cause = ("Empty page encountered before live_total rows were seen, and no valid rows were "
                         "observed afterward in this bounded probe. Suggestive of a real stopping problem "
                         "(stale session/csrf, or the register total not matching enumerable rows), but "
                         "NOT confirmed as genuine end-of-register -- probe more pages after this point "
                         "before concluding the register is smaller than getTotal reports.")
        confidence = "MEDIUM -- inconclusive within this bounded probe, needs a wider follow-up probe"
    else:
        likely_cause = "Rows seen at/after the anomaly are consistent with live_total -- likely genuine end of register."
        confidence = "MEDIUM-HIGH"

    print(f"LIKELY CAUSE: {likely_cause}")
    print(f"CONFIDENCE: {confidence}")
    print()
    print("No files were modified. checkpoint.json, firms.jsonl, and errors.jsonl are untouched.")


if __name__ == "__main__":
    main()