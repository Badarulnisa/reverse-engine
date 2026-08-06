"""
Generates ready-to-run cURL commands (and Python requests snippets) for
each distinct endpoint seen in the capture. This is what turns the
report from documentation into something you can actually paste into a
terminal to hit the API again.

Sensitive header values (Authorization, Cookie, API keys) are replaced
with placeholders by default -- the report is meant to be shared/stored,
and baking live tokens into it defeats the point of the vuln scanner
flagging leaked secrets elsewhere in the same file.
"""

import json
import shlex
from typing import Any, Dict, List

SENSITIVE_HEADER_NAMES = {
    "authorization", "cookie", "x-api-key", "api-key", "x-auth-token",
    "x-csrf-token", "proxy-authorization",
}


def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    redacted = {}
    for k, v in headers.items():
        if k.lower() in SENSITIVE_HEADER_NAMES:
            redacted[k] = f"<{k.upper().replace('-', '_')}>"
        else:
            redacted[k] = v
    return redacted


def _one_example_per_endpoint(records: List[Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
    """Keep the first captured call for each (host, method, path) combination."""
    examples: Dict[tuple, Dict[str, Any]] = {}
    for r in records:
        key = (r.get("host"), r.get("method"), r.get("path"))
        if key not in examples:
            examples[key] = r
    return examples


def build_curl_command(record: Dict[str, Any], redact: bool = True) -> str:
    method = record.get("method") or "GET"
    url = record.get("url") or ""
    headers = record.get("request_headers", {}) or {}
    if redact:
        headers = _redact_headers(headers)

    parts = ["curl", "-X", method, shlex.quote(url)]
    for k, v in headers.items():
        parts.extend(["-H", shlex.quote(f"{k}: {v}")])

    body = record.get("request_body")
    if body is not None:
        body_str = json.dumps(body)
        parts.extend(["-d", shlex.quote(body_str)])

    return " ".join(parts)


def build_requests_snippet(record: Dict[str, Any], redact: bool = True) -> str:
    method = (record.get("method") or "GET").lower()
    url = record.get("url") or ""
    headers = record.get("request_headers", {}) or {}
    if redact:
        headers = _redact_headers(headers)
    body = record.get("request_body")

    lines = ["import requests", "", f"headers = {json.dumps(headers, indent=4)}"]
    if body is not None:
        lines.append(f"payload = {json.dumps(body, indent=4)}")
        lines.append(f"response = requests.{method}({json.dumps(url)}, headers=headers, json=payload)")
    else:
        lines.append(f"response = requests.{method}({json.dumps(url)}, headers=headers)")
    lines.append("print(response.status_code, response.text)")
    return "\n".join(lines)


def generate_snippets(records: List[Dict[str, Any]], redact: bool = True) -> List[Dict[str, Any]]:
    """
    One row per distinct (host, method, path) endpoint, with a ready-to-run
    cURL command and Python requests snippet attached.
    """
    examples = _one_example_per_endpoint(records)
    rows = []
    for (host, method, path), record in examples.items():
        rows.append({
            "system": host,
            "method": method,
            "endpoint": path,
            "status_seen": record.get("status"),
            "curl_command": build_curl_command(record, redact=redact),
            "python_requests": build_requests_snippet(record, redact=redact),
            "secrets_redacted": redact,
        })
    rows.sort(key=lambda r: (r["system"] or "", r["endpoint"] or ""))
    return rows
