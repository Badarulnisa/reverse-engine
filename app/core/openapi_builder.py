import json
from typing import Any, Dict, List


def _infer_schema(obj: Any) -> Dict[str, Any]:
    """Recursively infers JSON Schema types from response/request payloads."""
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
        items_schema = _infer_schema(obj[0]) if obj else {"type": "string"}
        return {"type": "array", "items": items_schema}
    if isinstance(obj, dict):
        properties = {k: _infer_schema(v) for k, v in obj.items()}
        return {"type": "object", "properties": properties}
    return {"type": "string"}


def build_openapi_spec(
    records: List[Dict[str, Any]], title: str = "Extracted API"
) -> Dict[str, Any]:
    """Generates an OpenAPI 3.0 spec dictionary from normalized HAR records."""
    paths: Dict[str, Any] = {}

    for r in records:
        path = r.get("path", "/")
        method = (r.get("method") or "get").lower()

        if path not in paths:
            paths[path] = {}

        parameters = []
        for k, v in (r.get("query_params") or {}).items():
            parameters.append(
                {
                    "name": k,
                    "in": "query",
                    "required": False,
                    "schema": _infer_schema(v),
                }
            )

        operation: Dict[str, Any] = {
            "summary": f"{method.upper()} {path}",
            "parameters": parameters,
            "responses": {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": _infer_schema(r.get("response_body"))
                        }
                    },
                }
            },
        }

        if r.get("request_body") and isinstance(r["request_body"], dict):
            operation["requestBody"] = {
                "content": {
                    "application/json": {
                        "schema": _infer_schema(r["request_body"])
                    }
                }
            }

        paths[path][method] = operation

    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1.0.0"},
        "paths": paths,
    }