"""
rebuild_checkpoint.py

Recovery tool. If checkpoint.json ever becomes corrupted (e.g. from two
scraper processes running concurrently and racing to write it), this
rebuilds a clean checkpoint.json purely from firms.jsonl -- the file
that's actually been reliable throughout (append-only, one process
writes to it per line, never rewritten in place).

Every successfully collected firm's "url" field becomes a "done" entry
in the rebuilt checkpoint, so a resumed run will correctly skip
everything already in firms.jsonl and only fetch what's missing.

This does NOT mark permanently-failed firms (from errors.jsonl) as done
-- those should be retried on the next run, which is the correct
behavior for "record failure, continue, and let it be retried later"
rather than skipping them forever.

Usage:
    python rebuild_checkpoint.py
"""
from __future__ import annotations

import json
from pathlib import Path

FIRMS_PATH = "firms.jsonl"
CHECKPOINT_PATH = "checkpoint.json"


def main():
    firms_path = Path(FIRMS_PATH)
    if not firms_path.exists():
        print(f"{FIRMS_PATH} not found -- nothing to rebuild from.")
        return

    checkpoint = {}
    bad_lines = 0

    with firms_path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            url = record.get("url")
            if url:
                checkpoint[url] = "done"

    Path(CHECKPOINT_PATH).write_text(json.dumps(checkpoint, indent=0), encoding="utf-8")

    print(f"Rebuilt {CHECKPOINT_PATH} from {FIRMS_PATH}.")
    print(f"  {len(checkpoint)} firm(s) marked done.")
    if bad_lines:
        print(f"  WARNING: {bad_lines} malformed line(s) in {FIRMS_PATH} were skipped -- worth a manual look.")


if __name__ == "__main__":
    main()