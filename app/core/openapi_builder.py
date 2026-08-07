"""
Generates an OpenAPI 3.0 spec from normalized HAR records.

Improvements over v1:
  - groups records by templated path (via path_templater) instead of literal
    URL, so /users/1 and /users/2 merge into one /users/{userId} operation
  - merges schemas across ALL samples for an endpoint instead of only using
    the first record seen, so accumulated JSON shape reflects the union of
    what was actually observed
  - array item schemas merge across multiple items instead of only obj[0]
  - adds "in": "path" parameters for templated path segments
  - attaches an "x-pagination" extension block per operation when a
    PaginationProfile is supplied for that endpoint
"""

from typing import Any, Dict, List, Optional

from .path_templater import group_by_template

MAX_ARRAY_ITEMS_SAMPLED = 5


def _infer_schema(obj: Any, _depth: int = 0) -> Dict[str, Any]:
    """Infers JSON Schema for a single value. Arrays sample multiple items and merge."""
    if _depth > 12:  # guard against pathological nesting
        return {"type": "string"}
    if obj is None:
        return {"type": "string", "nullable": True}
    if isinstance(obj, bool):
        return {"type": "boolean"}
    if isinstance(obj, int):
        return {"type": "integer"}
    if isinstance(obj, float):
        return {"type": "number"}
    if isinstance(obj, str):
        return {"type": "string"}
    if isinstance(obj, list):
        if not obj:
            return {"type": "array", "items": {"type": "string"}}
        sample = obj[:MAX_ARRAY_ITEMS_SAMPLED]
        merged_items = _infer_schema(sample[0], _depth + 1)
        for item in sample[1:]:
            merged_items = _merge_schemas(merged_items, _infer_schema(item, _depth + 1))
        return {"type": "array", "items": merged_items}
    if isinstance(obj, dict):
        properties = {k: _infer_schema(v, _depth + 1) for k, v in obj.items()}
        return {"type": "object", "properties": properties}
    return {"type": "string"}


def _merge_schemas(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Union two JSON Schema fragments produced by _infer_schema."""
    if a == b:
        return a

    a_type = a.get("type")
    b_type = b.get("type")

    # nullable handling: one side null, other side typed -> typed schema, marked nullable
    if a_type == "string" and a.get("nullable") and b_type != "string":
        merged = dict(b)
        merged["nullable"] = True
        return merged
    if b_type == "string" and b.get("nullable") and a_type != "string":
        merged = dict(a)
        merged["nullable"] = True
        return merged

    if a_type != b_type:
        # genuinely conflicting types observed across samples - record both via oneOf
        options = []
        for schema in (a, b):
            if schema.get("oneOf"):
                options.extend(schema["oneOf"])
            else:
                options.append(schema)
        # dedupe by repr
        seen = set()
        deduped = []
        for opt in options:
            key = str(opt)
            if key not in seen:
                seen.add(key)
                deduped.append(opt)
        return {"oneOf": deduped}

    if a_type == "object":
        merged_props: Dict[str, Any] = dict(a.get("properties", {}))
        for k, v in b.get("properties", {}).items():
            if k in merged_props:
                merged_props[k] = _merge_schemas(merged_props[k], v)
            else:
                merged_props[k] = v
        return {"type": "object", "properties": merged_props}

    if a_type == "array":
        merged_items = _merge_schemas(a.get("items", {}), b.get("items", {}))
        return {"type": "array", "items": merged_items}

    # same primitive type, nothing to merge further
    return a


def _build_parameters(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    parameters: List[Dict[str, Any]] = []

    for param_name in (record.get("path_params") or {}).keys():
        parameters.append(
            {
                "name": param_name,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        )

    for k, v in (record.get("query_params") or {}).items():
        parameters.append(
            {
                "name": k,
                "in": "query",
                "required": False,
                "schema": _infer_schema(v),
            }
        )

    return parameters


def build_openapi_spec(
    records: List[Dict[str, Any]],
    pagination_by_endpoint: Optional[Dict[Any, Any]] = None,
    title: str = "Extracted API",
) -> Dict[str, Any]:
    """
    Generates an OpenAPI 3.0 spec dictionary from normalized HAR records.

    pagination_by_endpoint: optional dict mapping (method, path_template) -> PaginationProfile
    (as produced by pagination_stitcher.detect_pagination per group). When present,
    matching operations get an "x-pagination" extension block.
    """
    paths: Dict[str, Any] = {}
    groups = group_by_template(records)

    for (method, template), group_records in groups.items():
        method_lower = method.lower()

        if template not in paths:
            paths[template] = {}

        # merge response schemas across every sample for this endpoint
        response_schema: Optional[Dict[str, Any]] = None
        request_body_schema: Optional[Dict[str, Any]] = None
        status_codes = set()

        # parameters: union query params seen across samples + path params from template
        all_query_param_names: Dict[str, Dict[str, Any]] = {}

        for r in group_records:
            if r.get("status") is not None:
                status_codes.add(r["status"])

            resp_body = r.get("response_body")
            if resp_body is not None:
                schema = _infer_schema(resp_body)
                response_schema = schema if response_schema is None else _merge_schemas(response_schema, schema)

            req_body = r.get("request_body")
            if isinstance(req_body, dict):
                schema = _infer_schema(req_body)
                request_body_schema = (
                    schema if request_body_schema is None else _merge_schemas(request_body_schema, schema)
                )

            for k, v in (r.get("query_params") or {}).items():
                s = _infer_schema(v)
                if k not in all_query_param_names:
                    all_query_param_names[k] = s
                else:
                    all_query_param_names[k] = _merge_schemas(all_query_param_names[k], s)

        path_param_names = set()
        for r in group_records:
            path_param_names.update((r.get("path_params") or {}).keys())

        parameters: List[Dict[str, Any]] = [
            {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
            for name in sorted(path_param_names)
        ] + [
            {"name": name, "in": "query", "required": False, "schema": schema}
            for name, schema in all_query_param_names.items()
        ]

        operation: Dict[str, Any] = {
            "summary": f"{method} {template}",
            "parameters": parameters,
            "responses": {
                str(status or 200): {
                    "description": "Response",
                    "content": {
                        "application/json": {"schema": response_schema or {"type": "object"}}
                    },
                }
                for status in (sorted(status_codes) or [200])
            },
        }

        if request_body_schema is not None:
            operation["requestBody"] = {
                "content": {"application/json": {"schema": request_body_schema}}
            }

        if pagination_by_endpoint:
            profile = pagination_by_endpoint.get((method, template))
            if profile is not None and getattr(profile, "strategy", "NONE") != "NONE":
                operation["x-pagination"] = (
                    profile.model_dump() if hasattr(profile, "model_dump") else dict(profile)
                )

        paths[template][method_lower] = operation

    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1.0.0"},
        "paths": paths,
    }
