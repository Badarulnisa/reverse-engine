"""
JAFZA Google Places address enrichment -- CLI runner.

Usage:
    export GOOGLE_MAPS_API_KEY=your_key_here
    python run_enrich.py --input path/to/source.xlsx --limit 8      # validation run
    python run_enrich.py --input path/to/source.xlsx                # full run (uses cache)

Never re-queries a company that's already in the cache from a prior
run -- safe to Ctrl+C and rerun at any time.
"""
from __future__ import annotations

import argparse
import logging
import sys

from cache_store import PlacesCache
from config import MissingApiKeyError, load_settings, redact
from matcher import MatchResult, confidence_label, pick_best
from places_client import PlacesApiError, PlacesClient
from xlsx_io import load_source, write_enriched, write_unresolved

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run_enrich")


def build_query(company_name: str, geography_hint: str) -> str:
    return f"{company_name}, {geography_hint}"


def resolve_one(
    row_company_name: str,
    row_email: str | None,
    row_phone: str | None,
    client: PlacesClient,
    cache: PlacesCache,
    geography_hint: str,
) -> tuple[MatchResult, bool]:
    """Returns (result, was_cached)."""
    cached = cache.get(row_company_name, geography_hint)
    if cached is not None:
        return cached, True

    query = build_query(row_company_name, geography_hint)
    try:
        candidates = client.search_text(query)
    except PlacesApiError as exc:
        result = MatchResult(
            status="api_error",
            confidence="none",
            error_message=str(exc)[:300],
        )
        # Do NOT cache API errors -- we want to retry those on next run,
        # not lock in a transient failure.
        return result, False

    best, scored = pick_best(row_company_name, row_email, row_phone, candidates)

    if best is None:
        top = scored[0] if scored else None
        result = MatchResult(
            status="unresolved",
            confidence="none",
            reasoning=(
                f"no candidate cleared the matching bar "
                f"({len(scored)} candidate(s) considered)"
            ),
            candidates_considered=len(scored),
            top_candidate_name=top.candidate.name if top else None,
            top_candidate_address=top.candidate.formatted_address if top else None,
            top_candidate_score=round(top.total_score, 2) if top else None,
            top_candidate_reasoning=top.reasoning if top else None,
        )
    else:
        c = best.candidate
        result = MatchResult(
            status="matched",
            confidence=confidence_label(best.total_score),
            place_id=c.place_id,
            name=c.name,
            formatted_address=c.formatted_address,
            latitude=c.latitude,
            longitude=c.longitude,
            maps_url=c.maps_url,
            website=c.website,
            phone=c.phone,
            business_types=c.business_types,
            reasoning=f"score={best.total_score:.2f} ({best.reasoning})",
            candidates_considered=len(scored),
        )

    cache.put(row_company_name, geography_hint, result)
    return result, False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to source XLSX")
    parser.add_argument("--output", default="output/jafza_google_enriched.xlsx")
    parser.add_argument("--unresolved-output", default="output/jafza_google_unresolved.xlsx")
    parser.add_argument("--company-col", type=int, default=None,
                         help="0-based column index override if auto-detection is wrong")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only process the first N companies (validation runs)")
    args = parser.parse_args()

    try:
        settings = load_settings()
    except MissingApiKeyError as exc:
        log.error(str(exc))
        sys.exit(1)

    log.info("Using GOOGLE_MAPS_API_KEY=%s", redact(settings.google_maps_api_key))

    headers, rows = load_source(args.input, company_col=args.company_col)
    log.info("Loaded %d source rows. Detected company column values look like: %r",
              len(rows), rows[0].company_name if rows else None)

    if args.limit:
        rows = rows[: args.limit]
        log.info("Validation mode: limiting to first %d rows", len(rows))

    cache = PlacesCache(settings.cache_path)
    client = PlacesClient(settings)

    results: dict[int, MatchResult] = {}
    stats = {
        "total": len(rows), "matched": 0, "high": 0, "medium": 0, "low": 0,
        "unresolved": 0, "api_failures": 0, "cache_hits": 0,
    }

    for i, row in enumerate(rows, start=1):
        result, was_cached = resolve_one(
            row.company_name, row.email, row.phone, client, cache, settings.geography_hint,
        )
        results[row.row_index] = result

        if was_cached:
            stats["cache_hits"] += 1
        if result.status == "matched":
            stats["matched"] += 1
            stats[result.confidence] += 1
        elif result.status == "unresolved":
            stats["unresolved"] += 1
        elif result.status == "api_error":
            stats["api_failures"] += 1

        tag = "CACHE" if was_cached else "LIVE "
        log.info("[%d/%d] %s %-45s -> %s/%s %s",
                  i, len(rows), tag, row.company_name[:45],
                  result.status, result.confidence,
                  (result.formatted_address or result.error_message or "")[:60])

    write_enriched(args.output, headers, rows, results)
    review_count = write_unresolved(args.unresolved_output, headers, rows, results)

    print("\n" + "=" * 60)
    print("ENRICHMENT SUMMARY")
    print("=" * 60)
    print(f"total companies processed : {stats['total']}")
    print(f"matched                   : {stats['matched']}")
    print(f"  high confidence         : {stats['high']}")
    print(f"  medium confidence       : {stats['medium']}")
    print(f"  low confidence          : {stats['low']}")
    print(f"unresolved                : {stats['unresolved']}")
    print(f"api failures              : {stats['api_failures']}")
    print(f"cache hits (no API spend) : {stats['cache_hits']}")
    print(f"cache size on disk        : {cache.count()} companies")
    print(f"enriched output           : {args.output}")
    print(f"review/unresolved output  : {args.unresolved_output} ({review_count} rows)")
    print("=" * 60)

    cache.close()


if __name__ == "__main__":
    main()