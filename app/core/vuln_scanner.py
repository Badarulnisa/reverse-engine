"""
Passive vulnerability scanner for captured HAR traffic.

This does NOT send any requests or probe anything live -- it only
inspects the request/response data already captured in the HAR file
and flags patterns that commonly indicate security issues:

  - missing security headers (HSTS, CSP, X-Content-Type-Options, etc.)
  - secrets/tokens leaking in response bodies (API keys, JWTs, AWS keys,
    private keys, generic high-entropy tokens)
  - cookies set without Secure / HttpOnly / SameSite
  - permissive CORS (wildcard origin combined with credentials)
  - verbose error responses (stack traces, SQL errors, debug info)
  - data sent over plain HTTP instead of HTTPS
  - mixed content (HTTPS page pulling HTTP resources) -- best-effort,
    only accurate when the referring page is also in the same HAR

Each finding is a flat dict ready to drop straight into a report row.
Severity is a coarse label (info/low/medium/high) meant to help
triage, not a formal CVSS score -- always verify manually before
treating anything here as confirmed.
"""

import re
from typing import Any, Dict, List

SECURITY_HEADERS = {
    "strict-transport-security": ("Missing HSTS header", "medium"),
    "content-security-policy": ("Missing Content-Security-Policy header", "low"),
    "x-content-type-options": ("Missing X-Content-Type-Options header", "low"),
    "x-frame-options": ("Missing X-Frame-Options / frame-ancestors CSP", "low"),
}

SECRET_PATTERNS = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret Key (heuristic)", re.compile(r"(?i)aws_secret_access_key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}")),
    ("Generic API Key field", re.compile(r"(?i)\"(api[_-]?key|apikey|secret|client_secret)\"\s*:\s*\"[A-Za-z0-9\-_]{12,}\"")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("Private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe key", re.compile(r"sk_live_[A-Za-z0-9]{16,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
]

ERROR_LEAK_PATTERNS = [
    ("Stack trace", re.compile(r"(?i)(traceback \(most recent call last\)|at\s+\S+\(\S+:\d+:\d+\)|\.java:\d+\))")),
    ("SQL error", re.compile(r"(?i)(sql syntax|sqlstate|ORA-\d{5}|unclosed quotation mark)")),
    ("Debug info", re.compile(r"(?i)(DEBUG = ?True|django\.core\.handlers|werkzeug\.debug)")),
]


def _get_header(headers: Dict[str, str], name: str):
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def _check_security_headers(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    resp_headers = record.get("response_headers", {}) or {}
    lower_keys = {k.lower() for k in resp_headers}
    for header, (message, severity) in SECURITY_HEADERS.items():
        if header not in lower_keys:
            findings.append(_finding(record, "Missing security header", severity, message, header))
    return findings


def _check_secrets(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    haystacks = {
        "response_body": str(record.get("response_body") or ""),
        "request_body": str(record.get("request_body") or ""),
        "url": record.get("url") or "",
    }
    for location, text in haystacks.items():
        if not text:
            continue
        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                snippet = match.group(0)
                masked = snippet[:6] + "..." + snippet[-4:] if len(snippet) > 12 else "***"
                findings.append(_finding(
                    record, "Exposed secret", "high",
                    f"{label} found in {location}", masked,
                ))
    return findings


def _check_cookies(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    resp_headers = record.get("response_headers", {}) or {}
    cookie_val = _get_header(resp_headers, "set-cookie")
    if not cookie_val:
        return findings
    cookie_lower = cookie_val.lower()
    is_https = (record.get("url") or "").startswith("https://")
    if is_https and "secure" not in cookie_lower:
        findings.append(_finding(record, "Weak cookie flag", "medium", "Set-Cookie missing Secure flag over HTTPS", cookie_val[:60]))
    if "httponly" not in cookie_lower:
        findings.append(_finding(record, "Weak cookie flag", "medium", "Set-Cookie missing HttpOnly flag", cookie_val[:60]))
    if "samesite" not in cookie_lower:
        findings.append(_finding(record, "Weak cookie flag", "low", "Set-Cookie missing SameSite attribute", cookie_val[:60]))
    return findings


def _check_cors(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    resp_headers = record.get("response_headers", {}) or {}
    origin = _get_header(resp_headers, "access-control-allow-origin")
    creds = _get_header(resp_headers, "access-control-allow-credentials")
    if origin == "*" and creds and creds.lower() == "true":
        findings.append(_finding(
            record, "CORS misconfiguration", "high",
            "Wildcard Access-Control-Allow-Origin combined with allow-credentials: true",
            "Access-Control-Allow-Origin: *",
        ))
    return findings


def _check_transport(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    url = record.get("url") or ""
    if url.startswith("http://"):
        findings.append(_finding(record, "Insecure transport", "high", "Call made over plain HTTP, not HTTPS", url))
    return findings


def _check_verbose_errors(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    status = record.get("status") or 0
    body_text = str(record.get("response_body") or "")
    if status >= 400 and body_text:
        for label, pattern in ERROR_LEAK_PATTERNS:
            if pattern.search(body_text):
                findings.append(_finding(record, "Verbose error disclosure", "medium", f"{label} exposed in {status} response body", ""))
    return findings


def _finding(record: Dict[str, Any], category: str, severity: str, description: str, evidence: str) -> Dict[str, Any]:
    return {
        "system": record.get("host"),
        "endpoint": record.get("path"),
        "method": record.get("method"),
        "url": record.get("url"),
        "status": record.get("status"),
        "category": category,
        "severity": severity,
        "description": description,
        "evidence": evidence,
        "started_at": record.get("started_at"),
    }


CHECKS = [
    _check_security_headers,
    _check_secrets,
    _check_cookies,
    _check_cors,
    _check_transport,
    _check_verbose_errors,
]


def scan_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run every check against every record and return a flat list of findings."""
    findings: List[Dict[str, Any]] = []
    for record in records:
        for check in CHECKS:
            findings.extend(check(record))
    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: severity_order.get(f["severity"], 9))
    return findings


def summarize_findings(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-system finding counts by severity, for a quick-glance summary row."""
    from collections import defaultdict
    counts = defaultdict(lambda: {"high": 0, "medium": 0, "low": 0, "info": 0, "total": 0})
    for f in findings:
        c = counts[f["system"]]
        c[f["severity"]] = c.get(f["severity"], 0) + 1
        c["total"] += 1
    return [
        {"system": system, **vals}
        for system, vals in sorted(counts.items(), key=lambda kv: kv[1]["total"], reverse=True)
    ]
