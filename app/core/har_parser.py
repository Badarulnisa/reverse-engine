"""
Parses HAR (HTTP Archive) files exported from browser DevTools into a
normalized list of request/response records. This is the entry point
for the whole pipeline: everything downstream (system detection,
extraction, reporting) consumes the output of parse_har_file().
"""

import json
from typing import Any, Dict, List
from urllib.parse import urlparse


def load_har(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_entries(har: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull the raw entry list out of a HAR file's log.entries."""
    return har.get("log", {}).get("entries", [])


def _try_json(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def parse_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one raw HAR entry into a normalized request/response record."""
    request = entry.get("request", {})
    response = entry.get("response", {})
    url = request.get("url", "")
    parsed_url = urlparse(url)

    content = response.get("content", {})
    body_json = _try_json(content.get("text"))

    post_data = request.get("postData", {})
    req_body_json = _try_json(post_data.get("text"))

    return {
        "host": parsed_url.netloc,
        "path": parsed_url.path or "/",
        "method": request.get("method"),
        "url": url,
        "status": response.get("status"),
        "mime_type": content.get("mimeType"),
        "request_headers": {h["name"]: h["value"] for h in request.get("headers", [])},
        "response_headers": {h["name"]: h["value"] for h in response.get("headers", [])},
        "query_params": {q["name"]: q["value"] for q in request.get("queryString", [])},
        "request_body": req_body_json,
        "response_body": body_json,
        "started_at": entry.get("startedDateTime"),
        "time_ms": entry.get("time"),
    }


def parse_har_file(path: str) -> List[Dict[str, Any]]:
    """Load a .har file from disk and return normalized call records."""
    har = load_har(path)
    entries = extract_entries(har)
    return [parse_entry(e) for e in entries]
