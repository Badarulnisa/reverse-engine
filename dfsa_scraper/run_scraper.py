"""
CLI entry point.

Usage:
    python3 run_scrape.py firms [--out firms.jsonl] [--max-pages N] [--skip-detail]
    python3 run_scrape.py individuals [--out individuals.jsonl] [--max-pages N] [--skip-detail]
    python3 run_scrape.py funds [--out funds.jsonl] [--max-pages N] [--skip-detail]

Writes one JSON object per line (listing row merged with parsed detail page,
when --skip-detail is not passed). Streams to disk incrementally so a
crash/interrupt partway through doesn't lose earlier progress -- safe to
resume by de-duping reference_number against the existing output file
(not done automatically here; add a --resume flag if you need it for a
long run against the 4063-row individuals register).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys

from dfsa_common import DfsaSession, walk_register
from dfsa_registers import FIRMS, INDIVIDUALS, FUNDS
from dfsa_firm_detail import parse_firm_detail
from dfsa_individual_detail import parse_individual_detail

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("run_scrape")

REGISTER_MAP = {
    "firms": (FIRMS, parse_firm_detail),
    "individuals": (INDIVIDUALS, parse_individual_detail),
    "funds": (FUNDS, None),  # no detail parser written yet -- listing only
}


def _asdict(obj):
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("register", choices=REGISTER_MAP.keys())
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--skip-detail", action="store_true")
    ap.add_argument("--keywords", default="", help="optional keyword filter")
    args = ap.parse_args()

    config, detail_parser = REGISTER_MAP[args.register]
    out_path = args.out or f"{args.register}.jsonl"

    filters = dict(config["default_filters"])
    if args.keywords:
        filters["keywords"] = args.keywords

    session = DfsaSession()

    count = 0
    with open(out_path, "w", encoding="utf-8") as out_f:
        for row in walk_register(session, config["path"], filters, max_pages=args.max_pages):
            record = {
                "detail_url": row.detail_url,
                "name": row.name,
                "reference_number": row.reference_number,
                "type_label": row.type_label,
            }

            if not args.skip_detail and detail_parser is not None and row.detail_url:
                try:
                    html = session.get_detail_page(row.detail_url)
                    detail = detail_parser(html, row.detail_url)
                    record["detail"] = _asdict(detail)
                except Exception as exc:
                    logger.error("Failed to fetch/parse detail for %s: %s", row.detail_url, exc)
                    record["detail_error"] = str(exc)

            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            count += 1
            if count % 50 == 0:
                logger.info("...%d rows written to %s", count, out_path)

    logger.info("Done. %d rows written to %s", count, out_path)


if __name__ == "__main__":
    main()