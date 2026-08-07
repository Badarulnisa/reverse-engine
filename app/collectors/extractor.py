"""
Extracts structured data out of JSON response bodies captured in HAR
calls, flattening arbitrarily nested objects/arrays into flat rows
suitable for a spreadsheet. Every key encountered is preserved (union
of columns is computed later in report_builder) so nothing gets
silently dropped.
"""

from typing import Any, Dict, List

from path_templater import group_by_template


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


_LIST_KEYS = ("data", "result", "results", "items", "records", "rows")


def find_data_rows(body: Any) -> List[Dict[str, Any]]:
    """
    Given a JSON response body, find the list of 'records' it represents.
    Handles the common shapes seen in real APIs:
      - a bare list of objects
      - {"data": [...]} / {"result": [...]} / {"results": [...]} / {"items": [...]} / {"records": [...]} / {"rows": [...]}
      - a LIST of envelopes, each itself containing one of the above
        (e.g. batched Salesforce Apex/Aura RPC responses: [{"statusCode":200,"result":[...]}, ...])
        -- envelope fields (statusCode, tid, action, method, etc.) are kept and
        attached to every row produced from that envelope's nested list, so you
        don't lose which call a row came from.
      - a single object (treated as one row)
    """
    if body is None:
        return []

    if isinstance(body, dict):
        for key in _LIST_KEYS:
            inner = body.get(key)
            if isinstance(inner, list):
                return [b if isinstance(b, dict) else {"value": b} for b in inner]
        return [body]

    if isinstance(body, list):
        rows: List[Dict[str, Any]] = []
        for item in body:
            if not isinstance(item, dict):
                rows.append({"value": item})
                continue

            nested_list = None
            nested_key = None
            for key in _LIST_KEYS:
                inner = item.get(key)
                if isinstance(inner, list):
                    nested_list = inner
                    nested_key = key
                    break

            if nested_list is None:
                # plain object in the list, no further nesting to unwrap
                rows.append(item)
                continue

            envelope_meta = {k: v for k, v in item.items() if k != nested_key}
            for nested_item in nested_list:
                if isinstance(nested_item, dict):
                    merged = dict(envelope_meta)
                    merged.update(nested_item)
                    rows.append(merged)
                else:
                    rows.append({**envelope_meta, "value": nested_item})
        return rows

    return [{"value": body}]


def extract_records(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    For all HAR records belonging to one system (host), group flattened
    data rows by templated endpoint (e.g. /users/{userId} instead of the
    literal /users/1, /users/2, ...) so each logical endpoint becomes one
    table instead of one table per distinct resource ID.

    Call-level metadata (method/status/timestamp) is attached to each row
    so it stays traceable back to the originating request. Path params
    observed on that specific call (e.g. userId=1) are also attached so
    you can still tell which resource a row came from.
    """
    per_endpoint: Dict[str, List[Dict[str, Any]]] = {}

    # group_by_template mutates each record in place, attaching
    # 'path_template' and 'path_params' -- reuse the same grouping logic
    # the OpenAPI builder and pagination stitcher use, so all three stay
    # in agreement about what counts as "the same endpoint".
    grouped = group_by_template(records)

    for (_method, template), group_records in grouped.items():
        for r in group_records:
            rows = find_data_rows(r.get("response_body"))
            if not rows:
                continue
            for row in rows:
                flat = flatten(row)
                flat["_method"] = r.get("method")
                flat["_status"] = r.get("status")
                flat["_started_at"] = r.get("started_at")
                for param_name, observed_value in (r.get("path_params") or {}).items():
                    flat[f"_path_param.{param_name}"] = observed_value
                per_endpoint.setdefault(template, []).append(flat)

    return per_endpoint