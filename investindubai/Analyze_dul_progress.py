import json
import os
from collections import defaultdict
import pandas as pd

STATE_FILE = "visitdubai_brute.json"
OUTPUT_FILE = "visitdubai_brute.xlsx"

# How many missing IDs to actually print per prefix before truncating (keeps output readable)
MAX_GAPS_SHOWN = 30


def load_seen_identifiers(state_file):
    if not os.path.exists(state_file):
        print(f"[!] State file '{state_file}' not found. No progress has been saved yet.")
        return set()
    with open(state_file, "r") as f:
        data = json.load(f)
    return set(data.get("seen_identifiers", []))


def load_hit_identifiers(output_file):
    if not os.path.exists(output_file):
        print(f"[!] Output file '{output_file}' not found. No successful records saved yet.")
        return set()
    df = pd.read_excel(output_file)
    if "Target ID" not in df.columns:
        print(f"[!] '{output_file}' has no 'Target ID' column — cannot cross-reference hits.")
        return set()
    return set(df["Target ID"].astype(str))


def split_prefix_num(identifier):
    prefix = "".join(filter(str.isalpha, identifier))
    num_part = "".join(filter(str.isdigit, identifier))
    return prefix, num_part


def format_ranges(nums):
    """Collapse a sorted list of ints into compact range strings, e.g. [1,2,3,7,9,10] -> ['1-3','7','9-10']"""
    if not nums:
        return []
    ranges = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append((start, prev))
        start = prev = n
    ranges.append((start, prev))
    return [f"{a}-{b}" if a != b else f"{a}" for a, b in ranges]


def main():
    seen = load_seen_identifiers(STATE_FILE)
    hits = load_hit_identifiers(OUTPUT_FILE)

    if not seen:
        return

    print(f"[*] Total evaluated identifiers: {len(seen)}")
    print(f"[*] Total confirmed real businesses (hits): {len(hits)}")

    evaluated_by_prefix = defaultdict(int)
    max_num_evaluated = defaultdict(int)
    evaluated_nums = defaultdict(set)

    hits_by_prefix = defaultdict(int)
    max_num_hit = defaultdict(int)
    hit_nums = defaultdict(set)

    for ident in seen:
        prefix, num_part = split_prefix_num(ident)
        evaluated_by_prefix[prefix] += 1
        if num_part:
            n = int(num_part)
            max_num_evaluated[prefix] = max(max_num_evaluated[prefix], n)
            evaluated_nums[prefix].add(n)

    for ident in hits:
        prefix, num_part = split_prefix_num(ident)
        hits_by_prefix[prefix] += 1
        if num_part:
            n = int(num_part)
            max_num_hit[prefix] = max(max_num_hit[prefix], n)
            hit_nums[prefix].add(n)

    print("\n--- Breakdown by Prefix (evaluated vs. real hits) ---")
    header = f"{'Prefix':<8}{'Evaluated':<11}{'Hits':<7}{'Hit Rate':<10}{'Highest Hit ID'}"
    print(header)
    print("-" * len(header))
    for prefix in sorted(evaluated_by_prefix.keys()):
        evaluated = evaluated_by_prefix[prefix]
        hit_count = hits_by_prefix.get(prefix, 0)
        hit_rate = (hit_count / evaluated * 100) if evaluated else 0.0
        highest_hit = f"{prefix}{max_num_hit[prefix]:04d}" if hit_count else "-"
        print(f"{prefix:<8}{evaluated:<11}{hit_count:<7}{hit_rate:<9.1f}%{highest_hit}")

    # --- Gap analysis: numeric coverage within each prefix's evaluated range ---
    print("\n--- Numeric Range Coverage & Gaps (per prefix) ---")
    for prefix in sorted(evaluated_nums.keys()):
        nums = evaluated_nums[prefix]
        if not nums:
            continue
        lo, hi = min(nums), max(nums)
        full_range = set(range(lo, hi + 1))
        missing = sorted(full_range - nums)
        coverage_pct = (len(nums) / len(full_range) * 100) if full_range else 0.0

        print(f"\nPrefix {prefix}: range {prefix}{lo:04d} - {prefix}{hi:04d} "
              f"({len(nums)}/{len(full_range)} evaluated, {coverage_pct:.1f}% dense)")

        if not missing:
            print("  [+] No gaps — fully contiguous evaluation within this range.")
        else:
            gap_ranges = format_ranges(missing)
            shown = gap_ranges[:MAX_GAPS_SHOWN]
            print(f"  [!] {len(missing)} un-evaluated ID(s) within range:")
            print("      " + ", ".join(f"{prefix}{r}" if "-" not in r
                                        else f"{prefix}{r.split('-')[0]}-{prefix}{r.split('-')[1]}"
                                        for r in shown))
            if len(gap_ranges) > MAX_GAPS_SHOWN:
                print(f"      ... and {len(gap_ranges) - MAX_GAPS_SHOWN} more gap range(s) truncated")

        # Hits vs non-hits within evaluated set for this prefix
        hnums = hit_nums.get(prefix, set())
        dead_nums = nums - hnums
        print(f"  Hits within evaluated: {len(hnums)} | Dead/failed within evaluated: {len(dead_nums)}")


if __name__ == "__main__":
    main()
