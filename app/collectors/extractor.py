"""
Extracts structured data out of JSON response bodies captured in HAR
calls, flattening arbitrarily nested objects/arrays into flat rows
suitable for a spreadsheet. Every key encountered is preserved (union
of columns is computed later in report_builder) so nothing gets
silently dropped.
"""

from typing import Any, Dict, List


def flatten(obj: Any, parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
    """Flatten a nested dict/list into a single-level dict with dotted/indexed keys."""
    items: Dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj:
            items[parent_key] = "{}"
        for k, v in obj.items():
            new_key = f"{parent_key}.{k}" if parent_key else str(k)
            items.update(flatten(v, new_key, sep))
    elif isinstance(obj, list):
        if not obj:
            items[parent_key] = "[]"
        else:
            for i, v in enumerate(obj):
                new_key = f"{parent_key}[{i}]"
                items.update(flatten(v, new_key, sep))
    else:
        items[parent_key] = obj
    return items


def find_data_rows(body: Any) -> List[Dict[str, Any]]:
    """
    Given a JSON response body, find the list of 'records' it represents.
    Handles the common shapes seen in real APIs:
      - a bare list of objects
      - {"data": [...]} / {"results": [...]} / {"items": [...]} / {"records": [...]} / {"rows": [...]}
      - a single object (treated as one row)
    """
    if body is None:
        return []
    if isinstance(body, list):
        return [b if isinstance(b, dict) else {"value": b} for b in body]
    if isinstance(body, dict):
        for key in ("data", "results", "items", "records", "rows"):
            inner = body.get(key)
            if isinstance(inner, list):
                return [b if isinstance(b, dict) else {"value": b} for b in inner]
        return [body]
    return [{"value": body}]


def extract_records(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    For all HAR records belonging to one system (host), group flattened
    data rows by endpoint (path) so each endpoint becomes its own table.
    Call-level metadata (method/status/timestamp) is attached to each row
    so it stays traceable back to the originating request.
    """
    per_endpoint: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        path = r.get("path") or "/"
        rows = find_data_rows(r.get("response_body"))
        if not rows:
            continue
        for row in rows:
            flat = flatten(row)
            flat["_method"] = r.get("method")
            flat["_status"] = r.get("status")
            flat["_started_at"] = r.get("started_at")
            per_endpoint.setdefault(path, []).append(flat)
    return per_endpoint
