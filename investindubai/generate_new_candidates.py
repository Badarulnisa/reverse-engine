"""
generate_new_candidates.py

Looks at every shard's seen_identifiers (2-letter prefix + 4-digit
number, e.g. "AK1234") and, for each prefix that was ever touched,
finds the highest number reached. Generates new candidates starting
just past that point, up to a chosen count per prefix - so this picks
up exactly where the old brute force left off, with zero overlap.

Also includes prefixes that were NEVER touched by any shard at all
(the full AA-ZZ space is 676 prefixes; your 4 shards likely only
reached partway through their assigned slice each), starting those
fresh from 1000 (per the original script's dead-zone skip of 1-999).

Usage:
    python generate_new_candidates.py --shards visitdubai_brute_shard0.json visitdubai_brute_shard1.json visitdubai_brute_shard2.json visitdubai_brute_shard3.json --per-prefix 200 --output candidates.txt
"""

import argparse
import json
import os
import re
import string

DUL_RE = re.compile(r"^([A-Z]{2})(\d{4})$")


def load_all_seen(shard_files):
    all_ids = set()
    for path in shard_files:
        if not os.path.exists(path):
            print(f"[!] Skipping missing file: {path}")
            continue
        with open(path) as f:
            data = json.load(f)
        ids = data.get("seen_identifiers", [])
        print(f"  {path}: {len(ids)} identifiers")
        all_ids.update(ids)
    return all_ids


def highest_per_prefix(all_ids):
    highest = {}
    for ident in all_ids:
        m = DUL_RE.match(ident)
        if not m:
            continue
        prefix, num = m.group(1), int(m.group(2))
        if prefix not in highest or num > highest[prefix]:
            highest[prefix] = num
    return highest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", nargs="+", required=True)
    ap.add_argument("--per-prefix", type=int, default=200,
                     help="how many new candidates to generate per prefix")
    ap.add_argument("--output", default="candidates.txt")
    ap.add_argument("--include-untouched-prefixes", action="store_true",
                     help="also generate candidates for prefixes no shard ever reached at all")
    args = ap.parse_args()

    print("[*] Loading seen identifiers from all shards...")
    all_ids = load_all_seen(args.shards)
    print(f"[*] Total unique identifiers across all shards: {len(all_ids)}")

    highest = highest_per_prefix(all_ids)
    print(f"[*] {len(highest)} distinct prefixes were touched by at least one shard.")

    all_prefixes = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    untouched = [p for p in all_prefixes if p not in highest]
    print(f"[*] {len(untouched)} prefixes were never touched by any shard.")

    candidates = []

    for prefix, top in sorted(highest.items()):
        start = top + 1
        end = min(9999, start + args.per_prefix - 1)
        if start > 9999:
            continue  # this prefix was already fully exhausted
        for n in range(start, end + 1):
            candidates.append(f"{prefix}{n:04d}")

    if args.include_untouched_prefixes:
        for prefix in untouched:
            for n in range(1000, 1000 + args.per_prefix):
                candidates.append(f"{prefix}{n:04d}")

    with open(args.output, "w") as f:
        f.write("\n".join(candidates))

    print(f"\n[+] Wrote {len(candidates)} new candidates to {args.output}")
    print(f"    (continuing past each prefix's highest previously-seen number"
          f"{', plus untouched prefixes' if args.include_untouched_prefixes else ''})")


if __name__ == "__main__":
    main()
