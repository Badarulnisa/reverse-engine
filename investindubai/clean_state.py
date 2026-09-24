"""
clean_state.py

One-off fix for state pollution from the old browser_dul_discovery.py
bug, which marked challenged (visible-captcha) DULs as "done" even
though they were never actually verified. This removes any "done"
entry that has no real outcome on record - i.e. it's not a confirmed
hit (browser_discovery_hits.jsonl) and not a confirmed empty
(browser_discovery_empty.txt) - so those DULs become eligible for
re-checking again.

Usage:
    python clean_state.py
"""

import json
import os

STATE_FILE = "browser_discovery_state.json"
HITS_FILE = "browser_discovery_hits.jsonl"
EMPTY_FILE = "browser_discovery_empty.txt"


def load_hit_duls():
    duls = set()
    if os.path.exists(HITS_FILE):
        with open(HITS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    duls.add(json.loads(line)["dul"])
                except Exception:
                    continue
    return duls


def load_empty_duls():
    if os.path.exists(EMPTY_FILE):
        with open(EMPTY_FILE) as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def main():
    if not os.path.exists(STATE_FILE):
        print(f"[!] {STATE_FILE} not found.")
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    done = set(state.get("done", []))
    verified = load_hit_duls() | load_empty_duls()

    genuinely_done = done & verified
    falsely_done = done - verified

    print(f"[*] {len(done)} total marked done.")
    print(f"[*] {len(genuinely_done)} have a real verified outcome (hit or confirmed empty) - kept.")
    print(f"[*] {len(falsely_done)} have NO real outcome (likely old-bug challenged entries) - removing.")

    if falsely_done:
        print(f"    Examples: {sorted(falsely_done)[:10]}")

    state["done"] = sorted(genuinely_done)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)

    print(f"[+] Cleaned. {len(falsely_done)} DULs are now eligible to be re-checked.")
    print("    Run your normal --candidates pass again (or add them to a "
          "small candidates file) to re-verify them.")


if __name__ == "__main__":
    main()
