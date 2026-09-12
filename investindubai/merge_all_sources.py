"""
Merge all 4 shard xlsx files + the recovered.xlsx into one deduplicated
master file for delivery. Safe to run while shards are still active -
retries briefly if it catches a file mid-checkpoint-write (momentarily
0 bytes, since _save_output_unlocked overwrites the whole file on each
save rather than writing atomically).

Usage:
    python merge_all_sources.py
    python merge_all_sources.py --shards visitdubai_brute_shard0.xlsx visitdubai_brute_shard1.xlsx visitdubai_brute_shard2.xlsx visitdubai_brute_shard3.xlsx --recovered visitdubai_recovered.xlsx
"""

import argparse
import json
import os
import time

import pandas as pd


def read_xlsx_with_retry(path, retries=5, wait=2.0):
    if not os.path.exists(path):
        print(f"[!] {path} not found - skipping.")
        return None
    for attempt in range(retries):
        size = os.path.getsize(path)
        if size == 0:
            print(f"[!] {path} is 0 bytes (likely mid-checkpoint-write) - "
                  f"waiting {wait}s and retrying ({attempt + 1}/{retries})...")
            time.sleep(wait)
            continue
        try:
            df = pd.read_excel(path)
            print(f"  {path}: {len(df)} rows")
            return df
        except Exception as e:
            print(f"[!] {path} failed to read ({e}) - "
                  f"waiting {wait}s and retrying ({attempt + 1}/{retries})...")
            time.sleep(wait)
    print(f"[!] Giving up on {path} after {retries} attempts - skipping it "
          "for this merge (re-run later to pick it up).")
    return None


def export_formatted_xlsx(df, path):
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="DUL Records")
        workbook = writer.book
        worksheet = writer.sheets["DUL Records"]

        header_format = workbook.add_format({
            "bold": True, "bg_color": "#1F2937", "font_color": "#FFFFFF",
            "border": 1, "valign": "vcenter", "align": "center",
        })
        cell_format = workbook.add_format({"border": 1, "valign": "vcenter"})

        for col_idx, col_name in enumerate(df.columns):
            worksheet.write(0, col_idx, col_name, header_format)
        for col_idx, col_name in enumerate(df.columns):
            max_len = df[col_name].fillna("").astype(str).map(len).max()
            max_len = max_len if pd.notna(max_len) else 10
            width = min(max(len(str(col_name)), int(max_len)) + 3, 45)
            worksheet.set_column(col_idx, col_idx, width, cell_format)

        worksheet.freeze_panes(1, 0)
        worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", nargs="+", default=[
        "visitdubai_brute_shard0.xlsx",
        "visitdubai_brute_shard1.xlsx",
        "visitdubai_brute_shard2.xlsx",
        "visitdubai_brute_shard3.xlsx",
    ])
    ap.add_argument("--recovered", default="visitdubai_recovered.xlsx")
    ap.add_argument("--output", default="visitdubai_MASTER_FINAL.xlsx")
    ap.add_argument("--shard-jsons", nargs="+", default=[
        "visitdubai_brute_shard0.json",
        "visitdubai_brute_shard1.json",
        "visitdubai_brute_shard2.json",
        "visitdubai_brute_shard3.json",
    ])
    ap.add_argument("--missing-output", default="visitdubai_MISSING_DULS.txt",
                     help="where to write the list of DULs seen but not yet "
                          "fetched anywhere, so nothing is silently dropped")
    args = ap.parse_args()

    all_dfs = []
    print("[*] Reading shard files...")
    for path in args.shards:
        df = read_xlsx_with_retry(path)
        if df is not None:
            all_dfs.append(df)

    print("[*] Reading recovered file...")
    df = read_xlsx_with_retry(args.recovered)
    if df is not None:
        all_dfs.append(df)

    if not all_dfs:
        print("[!] Nothing to merge - no readable files found.")
        return

    combined = pd.concat(all_dfs, ignore_index=True)
    before = len(combined)
    if "dulNumber" in combined.columns:
        combined = combined.drop_duplicates(subset=["dulNumber"])
    else:
        combined = combined.drop_duplicates()
    after = len(combined)

    export_formatted_xlsx(combined, args.output)
    print(f"\n[+] Merged {before} rows -> {after} unique rows")
    print(f"[+] Wrote {args.output}")

    # Cross-check against every DUL the shards have ever confirmed
    # (seen_identifiers = hits + misses combined) so we know exactly what,
    # if anything, is still missing full data - rather than silently
    # trusting that everything made it in.
    print("\n[*] Cross-checking against shard state files for any gaps...")
    all_seen = set()
    for path in args.shard_jsons:
        if not os.path.exists(path):
            print(f"[!] {path} not found - skipping in gap check.")
            continue
        try:
            with open(path, "r") as f:
                data = json.load(f)
            ids = set(data.get("seen_identifiers", []))
            all_seen.update(ids)
        except Exception as e:
            print(f"[!] Could not read {path} for gap check: {e}")

    if not all_seen:
        print("[!] No shard state files readable - skipping gap check.")
        return

    have = set(combined["dulNumber"].astype(str)) if "dulNumber" in combined.columns else set()
    missing = sorted(all_seen - have)

    print(f"[*] Shards have confirmed {len(all_seen)} DULs total.")
    print(f"[*] Master file has full data for {len(have)} DULs.")

    if missing:
        # Not all "missing" are actual gaps - many will be confirmed
        # not-found DULs (the API returned "no such DUL"), which correctly
        # have no record. There's no way to tell those apart from this
        # file alone, so this list is "candidates to re-check", not
        # confirmed lost data.
        with open(args.missing_output, "w") as f:
            f.write("\n".join(missing))
        print(f"[!] {len(missing)} DULs are seen by shards but have no row "
              f"in the master file - written to {args.missing_output}.\n"
              "    Note: most of these are likely confirmed not-found DULs "
              "(normal, not a real gap) rather than actually lost data - "
              "there's no way to tell the difference without re-querying "
              "them. Re-run recover_missing_data.py against this file's "
              "contents if you want to confirm/backfill any real gaps.")
    else:
        print("[+] No gap - every DUL the shards have seen is accounted for "
              "in the master file (as either a real record or a confirmed "
              "not-found).")


if __name__ == "__main__":
    main()
