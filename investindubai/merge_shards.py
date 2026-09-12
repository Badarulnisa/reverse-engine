import glob
import json
import pandas as pd

# Merges every visitdubai_brute*.xlsx and visitdubai_brute*.json in the
# current folder into one combined master file. Safe to run anytime,
# including while shards are still running (reads current snapshot).

OUTPUT_MASTER_XLSX = "visitdubai_master_combined.xlsx"
OUTPUT_MASTER_JSON = "visitdubai_master_seen_combined.json"


def merge_xlsx():
    files = sorted(glob.glob("visitdubai_brute*.xlsx"))
    if not files:
        print("[!] No visitdubai_brute*.xlsx files found.")
        return
    frames = []
    for f in files:
        try:
            df = pd.read_excel(f)
            print(f"  {f}: {len(df)} records")
            frames.append(df)
        except Exception as e:
            print(f"  [!] Skipped {f} (read error, likely mid-write): {e}")
    if not frames:
        return
    combined = pd.concat(frames, ignore_index=True)
    before = len(combined)
    if "dulNumber" in combined.columns:
        combined = combined.drop_duplicates(subset=["dulNumber"])
    after = len(combined)
    combined.to_excel(OUTPUT_MASTER_XLSX, index=False)
    print(f"[+] Merged {before} rows -> {after} unique rows -> {OUTPUT_MASTER_XLSX}")


def merge_json():
    files = sorted(glob.glob("visitdubai_brute*.json"))
    if not files:
        print("[!] No visitdubai_brute*.json state files found.")
        return
    all_seen = set()
    for f in files:
        try:
            with open(f, "r") as fh:
                data = json.load(fh)
                ids = data.get("seen_identifiers", [])
                print(f"  {f}: {len(ids)} identifiers")
                all_seen.update(ids)
        except Exception as e:
            print(f"  [!] Skipped {f} (read error): {e}")
    with open(OUTPUT_MASTER_JSON, "w") as fh:
        json.dump({"seen_identifiers": sorted(all_seen)}, fh)
    print(f"[+] Merged {len(all_seen)} total unique evaluated identifiers -> {OUTPUT_MASTER_JSON}")


if __name__ == "__main__":
    print("[*] Merging output records (xlsx)...")
    merge_xlsx()
    print("\n[*] Merging state files (json)...")
    merge_json()
