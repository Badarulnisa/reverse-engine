"""
Auto-detects the different backend "systems" hit during a browser session
by grouping HAR calls by host. Each distinct host (api.stripe.com,
api.internal.example.com, etc.) is treated as its own system so the
report can separate them cleanly instead of dumping everything into
one undifferentiated table.
"""

from collections import defaultdict
from typing import Any, Dict, List


def group_by_system(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group parsed HAR records by host. Each host = one detected system."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        host = r.get("host") or "unknown-host"
        grouped[host].append(r)
    return dict(grouped)


def summarize_systems(grouped: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Per-system summary: call volume, methods, endpoints, status codes seen."""
    summaries = []
    for host, records in grouped.items():
        methods = sorted({r.get("method") for r in records if r.get("method")})
        endpoints = sorted({r.get("path") for r in records if r.get("path")})
        statuses = sorted({r.get("status") for r in records if r.get("status") is not None})
        with_json = [r for r in records if r.get("response_body") is not None]
        summaries.append({
            "system": host,
            "total_calls": len(records),
            "calls_with_json_data": len(with_json),
            "methods": ", ".join(methods),
            "unique_endpoints": len(endpoints),
            "status_codes": ", ".join(str(s) for s in statuses),
        })
    summaries.sort(key=lambda s: s["total_calls"], reverse=True)
    return summaries
