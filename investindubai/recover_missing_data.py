"""
recover_missing_data.py

Targeted re-fetch for DULs that are already confirmed valid (present in
seen_identifiers across the shard state files, or in a missing-DULs
text file from merge_all_sources.py) but whose full company record
never made it into any output file.

Rewritten to directly reuse AdvancedScraperEngine from
visitdubai_bruteforce.py instead of a hand-rolled fetch loop. The
engine already has the proven-working retry/backoff, adaptive
throttling, 403/429 block detection, and confirmed-not-found handling -
duplicating a simplified version of that logic in this script was the
source of most of today's failures, since the two kept drifting apart.
This way there is exactly one place that logic lives.

Usage:
    python recover_missing_data.py --shards visitdubai_brute_shard0.json visitdubai_brute_shard1.json visitdubai_brute_shard2.json visitdubai_brute_shard3.json --workers 3
    python recover_missing_data.py --missing-file visitdubai_MISSING_DULS.txt --workers 3
    python recover_missing_data.py --shards ... --tor
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from visitdubai_bruteforce import (  # noqa: E402
    AdvancedScraperEngine,
    parse_visitdubai_dul,
    fetch_browser_cookies,
)
import asyncio  # noqa: E402

OUTPUT_XLSX = "visitdubai_recovered.xlsx"
OUTPUT_STATE = "visitdubai_recovered_state.json"


def load_all_seen(shard_files):
    all_ids = set()
    for path in shard_files:
        if not os.path.exists(path):
            print(f"[!] Skipping missing file: {path}")
            continue
        with open(path, "r") as f:
            data = json.load(f)
        ids = data.get("seen_identifiers", [])
        print(f"  {path}: {len(ids)} identifiers")
        all_ids.update(ids)
    return sorted(all_ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", nargs="+", help="shard state JSON files")
    ap.add_argument("--missing-file", default=None,
                     help="plain text file of DULs (one per line) to fetch - "
                          "use instead of --shards for a targeted re-check")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--delay-min", type=float, default=1.5)
    ap.add_argument("--delay-max", type=float, default=3.0)
    ap.add_argument("--tor", action="store_true",
                     help="route through Tor - only use if your regular "
                          "connection is genuinely blocked, since curl_cffi "
                          "impersonation alone has been the reliable path")
    args = ap.parse_args()

    if not args.shards and not args.missing_file:
        ap.error("either --shards or --missing-file is required")

    if args.missing_file:
        print(f"[*] Loading DULs from {args.missing_file}...")
        with open(args.missing_file, "r") as f:
            all_ids = sorted(set(line.strip() for line in f if line.strip()))
        print(f"[*] {len(all_ids)} DULs loaded.")
    else:
        print("[*] Loading confirmed DUL identifiers from shard state files...")
        all_ids = load_all_seen(args.shards)
        print(f"[*] Total unique identifiers across all shards: {len(all_ids)}")

    if not all_ids:
        print("[*] Nothing to do.")
        return

    config = {
        "target_name": "VisitDubai-Recovery-Pass",
        "base_url": "https://api.visitdubai.com/api/dul/GetDulDetails?dul={id}",
        "output_file": OUTPUT_XLSX,
        "state_file": OUTPUT_STATE,
        "max_workers": args.workers,
        "delay_range": (args.delay_min, args.delay_max),
        "checkpoint_interval": 50,
        "use_tor": args.tor,
    }

    print("[*] Initializing recovery engine (reusing the same proven "
          "scraper engine the shards use)...")
    if args.tor:
        print("[*] Tor mode enabled.")
    fresh_cookies = asyncio.run(fetch_browser_cookies(use_tor=args.tor))

    engine = AdvancedScraperEngine(config, initial_cookies=fresh_cookies)
    before = len(engine.scraped_records)
    print(f"[*] Resumed with {before} records already recovered "
          f"(from a prior run of this script, if any).")

    engine.run(iter(all_ids), parse_visitdubai_dul)

    after = len(engine.scraped_records)
    print(f"\n[+] Recovery pass complete. {after - before} new records "
          f"recovered this run. {after} total in {OUTPUT_XLSX}.")


if __name__ == "__main__":
    main()
