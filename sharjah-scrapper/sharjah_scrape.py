#!/usr/bin/env python3
"""
sharjah_directory_scraper.py  (parallel-prefix edition)

Same architecture as before (plain GET, ?company=<prefix>&page=<n>, 20
cards/page, no auth). The only change from the original is concurrency:
prefixes are independent (their own page-cursor and end condition), so we
run several of them at once in separate threads, each with its own
requests.Session (own connection), while a single lock protects checkpoint
writes and the shared records dict.

Usage:
    python sharjah_directory_scraper.py --probe
    python sharjah_directory_scraper.py --workers 3            # NEW
    python sharjah_directory_scraper.py --letters ABC --workers 2
    python sharjah_directory_scraper.py --export-only
"""

import argparse
import datetime
import json
import os
import re
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from html import unescape

import requests
from requests.adapters import HTTPAdapter

BASE = "https://sharjah.gov.ae"
DIRECTORY_URL = f"{BASE}/en/knowledge-center/business-directory/"
CHECKPOINT_PATH = "sharjah_checkpoint.json"
OUTPUT_XLSX = "sharjah_companies.xlsx"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
CARDS_PER_PAGE = 20

REQUEST_TIMEOUT = 45          # was 30 -- server is legitimately slow sometimes
RETRY_LIMIT = 3
RETRY_BACKOFF_SECONDS = 5
POLITE_DELAY_SECONDS = 0.8    # per-thread delay; unchanged, since concurrency does the speedup

DEFAULT_PREFIXES = list(string.ascii_uppercase) + list(string.digits)

CARD_SPLIT_RE = re.compile(r'<strong>Activity:</strong>')
NAME_RE = re.compile(r'<span class="text-decoration-none text-dark fw-bold">\s*([^<]*?)\s*</span>')
ADDRESS_RE = re.compile(r'<i class="bx bx-map[^"]*"[^>]*></i>\s*<span>\s*([^<]*?)\s*</span>')
POBOX_RE = re.compile(r'<i class="bx bx-box[^"]*"[^>]*></i>\s*<span>\s*:?\s*([^<]*?)\s*</span>')
PHONE_RE = re.compile(r'href="tel:([^"]*)"')
FAX_RE = re.compile(r'href="fax:([^"]*)"')
EMAIL_RE = re.compile(r'href="mailto:([^"]*)"')
ACTIVITY_TEXT_RE = re.compile(r'^\s*([^\n<]*)')


def parse_cards(html: str) -> list:
    chunks = CARD_SPLIT_RE.split(html)[1:]
    cards = []
    for chunk in chunks:
        activity_m = ACTIVITY_TEXT_RE.match(chunk)
        activity = unescape(activity_m.group(1)).strip() if activity_m else ""
        name_m = NAME_RE.search(chunk)
        name = unescape(name_m.group(1)).strip() if name_m else ""
        if not name:
            continue
        address_m = ADDRESS_RE.search(chunk)
        pobox_m = POBOX_RE.search(chunk)
        phone_m = PHONE_RE.search(chunk)
        fax_m = FAX_RE.search(chunk)
        email_m = EMAIL_RE.search(chunk)
        cards.append({
            "Company Name": name,
            "Activity": activity,
            "Address": unescape(address_m.group(1)).strip() if address_m else "",
            "PO Box": unescape(pobox_m.group(1)).strip() if pobox_m else "",
            "Phone": phone_m.group(1).strip() if phone_m else "",
            "Fax": fax_m.group(1).strip() if fax_m else "",
            "Email": unescape(email_m.group(1)).strip() if email_m else "",
        })
    return cards


@dataclass
class ScrapeState:
    prefixes_done: set = field(default_factory=set)
    prefix_last_page: dict = field(default_factory=dict)
    records: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path):
        if not os.path.isfile(path):
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls(
            prefixes_done=set(raw.get("prefixes_done", [])),
            prefix_last_page=raw.get("prefix_last_page", {}),
            records=raw.get("records", {}),
        )

    def save(self, path):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "prefixes_done": sorted(self.prefixes_done),
                "prefix_last_page": self.prefix_last_page,
                "records": self.records,
            }, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def record_key(card):
    return f"{card['Company Name']}|{card['Phone']}|{card['PO Box']}"


def build_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.5"})
    # Bigger pool so N worker threads each get their own persistent connection
    # instead of contending over the default pool of 10.
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


NETWORK_DOWN = threading.Event()   # shared "pause everyone" flag
STOP = threading.Event()           # shared "shut down now" flag, set on Ctrl+C


def is_network_error(e):
    """DNS/connect-level failures mean YOUR machine can't reach the internet
    right now -- not the server rejecting you. If every worker hits this at
    once (as opposed to one slow/flaky request), it's local network/DNS."""
    s = str(e)
    return "NameResolutionError" in s or "getaddrinfo failed" in s or \
           "ConnectTimeoutError" in s or isinstance(e, requests.exceptions.ConnectionError)


def wait_for_network(log_prefix=""):
    """Poll a fast, reliable host until DNS/connectivity is back, instead of
    every thread independently retrying 3x and giving up. Only one thread
    needs to detect recovery; the rest just wait on the Event."""
    if NETWORK_DOWN.is_set():
        return  # someone else is already polling
    NETWORK_DOWN.set()
    print(f"  [!] {log_prefix}network appears down -- pausing ALL workers until it's back...", flush=True)
    probe = requests.Session()
    while not STOP.is_set():
        try:
            probe.get("https://www.google.com/generate_204", timeout=5)
            break
        except requests.RequestException:
            time.sleep(5)
    NETWORK_DOWN.clear()
    print(f"  [+] network is back -- resuming all workers.", flush=True)


def fetch_with_retry(session, url, params, log_prefix=""):
    last_exc = None
    for attempt in range(1, RETRY_LIMIT + 1):
        if STOP.is_set():
            raise KeyboardInterrupt()
        while NETWORK_DOWN.is_set() and not STOP.is_set():
            time.sleep(1)   # someone else's outage-wait is in progress; ride it out
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if is_network_error(e):
                wait_for_network(log_prefix)
                continue  # don't burn a retry slot on a local outage
            if attempt < RETRY_LIMIT:
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(f"  [!] {log_prefix}{e} -- retry {attempt}/{RETRY_LIMIT} in {wait}s", flush=True)
                time.sleep(wait)
    raise last_exc


def fetch_page(session, prefix, page, valid_till):
    params = {"company": prefix, "validTill": valid_till}
    if page > 1:
        params["page"] = page
    resp = fetch_with_retry(session, DIRECTORY_URL, params, log_prefix=f"'{prefix}' ")
    return resp.text


def sweep_one_prefix(state, lock, prefix, valid_till, probe=False):
    """Runs in its own thread with its own session. Thread-safe writes to
    shared `state` are protected by `lock`; each thread's own page-walk
    needs no locking since it only touches state.records / prefix_last_page
    keys that no other thread writes to (per-prefix keys) plus the shared
    records dict (locked)."""
    session = build_session()
    start_page = state.prefix_last_page.get(prefix, 0) + 1
    page = start_page
    new_records = 0
    print(f"  [+] [{prefix}] starting from page {page}...", flush=True)

    while True:
        if STOP.is_set():
            print(f"  [!] [{prefix}] stopping (interrupted) -- checkpoint reflects last completed page.", flush=True)
            return prefix, new_records, False

        time.sleep(POLITE_DELAY_SECONDS)
        t0 = time.monotonic()
        try:
            html = fetch_page(session, prefix, page, valid_till)
        except KeyboardInterrupt:
            return prefix, new_records, False
        except Exception as e:
            with lock:
                state.prefix_last_page[prefix] = page - 1
                state.save(CHECKPOINT_PATH)
            print(f"  [!] [{prefix}] page {page} failed after retries: {e} -- will resume here next run.", flush=True)
            return prefix, new_records, False
        dt = time.monotonic() - t0

        cards = parse_cards(html)
        if not cards:
            with lock:
                state.prefixes_done.add(prefix)
                state.prefix_last_page[prefix] = page
                state.save(CHECKPOINT_PATH)
            print(f"  [+] [{prefix}] no cards on page {page} -- done ({new_records} new).", flush=True)
            return prefix, new_records, True

        new_here = 0
        with lock:
            for card in cards:
                key = record_key(card)
                if key not in state.records:
                    state.records[key] = card
                    new_here += 1
            new_records += new_here
            state.prefix_last_page[prefix] = page
            total = len(state.records)

        print(f"  [{prefix}] page {page:>5} | {dt:4.1f}s | {len(cards):>2} cards ({new_here} new) | {total:>6} total unique", flush=True)

        if probe:
            print(f"  [probe] [{prefix}] page 1 sample: {cards[0]}", flush=True)
            return prefix, new_records, True

        if page % 20 == 0:
            with lock:
                state.save(CHECKPOINT_PATH)

        if len(cards) < CARDS_PER_PAGE:
            with lock:
                state.prefixes_done.add(prefix)
                state.save(CHECKPOINT_PATH)
            print(f"  [+] [{prefix}] short page ({len(cards)} cards) at {page} -- done ({new_records} new).", flush=True)
            return prefix, new_records, True

        page += 1


def run_sweep(state, prefixes, workers=1, probe=False):
    lock = threading.Lock()
    valid_till = datetime.date.today().isoformat()
    todo = [p for p in prefixes if p not in state.prefixes_done]
    skipped = [p for p in prefixes if p in state.prefixes_done]
    for p in skipped:
        print(f"  [=] '{p}' already fully swept, skipping.")

    if probe:
        sweep_one_prefix(state, lock, "A", valid_till, probe=True)
        return

    if not todo:
        print("  [+] Nothing to do -- all requested prefixes already complete.")
        return

    print(f"  [+] Sweeping {len(todo)} prefixes with {workers} worker(s): {todo}")
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = {ex.submit(sweep_one_prefix, state, lock, p, valid_till): p for p in todo}
        for fut in as_completed(futs):
            prefix, new_records, ok = fut.result()
    except KeyboardInterrupt:
        # Signal every in-flight thread to bail out at its next checkpoint-safe
        # point (checked in sweep_one_prefix's loop and fetch_with_retry), then
        # tell the executor to drop every prefix that hasn't started yet
        # instead of pulling it off the queue. cancel_futures=True (py3.9+)
        # is what actually stops "L", "M", "N"... from starting after Ctrl+C --
        # without it, exiting a `with ThreadPoolExecutor(...)` block still
        # blocks on every already-submitted (queued-but-not-started) task.
        print("\n  [!] Interrupted -- signaling all workers to stop, cancelling unstarted prefixes...", flush=True)
        STOP.set()
        ex.shutdown(wait=True, cancel_futures=True)
        with lock:
            state.save(CHECKPOINT_PATH)
        print("  [!] Stopped. Checkpoint saved -- rerun to resume from last completed page per prefix.", flush=True)
        raise
    else:
        ex.shutdown(wait=True)

    with lock:
        state.save(CHECKPOINT_PATH)
    print(f"  [+] Sweep complete: {len(state.records)} unique records across "
          f"{len(state.prefixes_done)}/{len(prefixes)} prefixes.")


def export_to_excel(state, path=OUTPUT_XLSX):
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Font
    if not state.records:
        print("  [!] No records to export yet.")
        return
    columns = ["Company Name", "Activity", "Address", "PO Box", "Phone", "Fax", "Email"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sharjah Business Directory"
    ws.append(columns)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for rec in state.records.values():
        ws.append([rec.get(c, "") for c in columns])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, col in enumerate(columns, 1):
        letter = get_column_letter(i)
        m = max([len(col)] + [len(str(rec.get(col, ""))) for rec in state.records.values()])
        ws.column_dimensions[letter].width = min(m + 2, 60)
    wb.save(path)
    print(f"  [+] Exported {len(state.records)} records to {path}")


def main():
    global CHECKPOINT_PATH
    ap = argparse.ArgumentParser(description="Sharjah Business Directory scraper")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--letters", type=str, default="")
    ap.add_argument("--workers", type=int, default=1, help="parallel prefixes (try 3-4)")
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--checkpoint", default=CHECKPOINT_PATH)
    ap.add_argument("--output", default=OUTPUT_XLSX)
    args = ap.parse_args()

    CHECKPOINT_PATH = args.checkpoint

    state = ScrapeState.load(args.checkpoint)
    print(f"Loaded checkpoint: {len(state.records)} records, {len(state.prefixes_done)} prefixes fully done")

    if args.export_only:
        export_to_excel(state, args.output)
        return

    prefixes = list(args.letters.upper()) if args.letters else DEFAULT_PREFIXES

    if args.probe:
        run_sweep(state, prefixes, probe=True)
        return

    try:
        run_sweep(state, prefixes, workers=args.workers)
    except KeyboardInterrupt:
        return
    export_to_excel(state, args.output)


if __name__ == "__main__":
    main()
