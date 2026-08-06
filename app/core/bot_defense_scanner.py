"""
Detects bot-defense and anti-automation signals in captured traffic:
CAPTCHA/challenge widgets, known bot-management vendors, and requests
that depend on short-lived tokens (nonces/CSRF/timestamps) which would
break on naive replay. This tells you where an automated pipeline
would hit friction -- it does not attempt to solve or bypass anything.
"""

import re
from typing import Any, Dict, List

CAPTCHA_PATTERNS = [
    ("reCAPTCHA", re.compile(r"(?i)(recaptcha/api\.js|g-recaptcha|grecaptcha)")),
    ("hCaptcha", re.compile(r"(?i)(hcaptcha\.com|h-captcha)")),
    ("Cloudflare Turnstile", re.compile(r"(?i)(challenges\.cloudflare\.com/turnstile|cf-turnstile)")),
    ("FunCaptcha/Arkose", re.compile(r"(?i)(arkoselabs|funcaptcha)")),
    ("Generic CAPTCHA JSON", re.compile(r"(?i)\"captcha[_-]?(token|required|challenge)\"")),
]

BOT_DEFENSE_VENDORS = [
    ("Cloudflare Bot Management", re.compile(r"(?i)(cf-mitigated|__cf_bm|cf-ray)")),
    ("Akamai Bot Manager", re.compile(r"(?i)(_abck|akamai-bot|sensor_data)")),
    ("PerimeterX / HUMAN", re.compile(r"(?i)(_px[0-9]?=|perimeterx|px-captcha)")),
    ("DataDome", re.compile(r"(?i)(datadome|dd_cookie)")),
    ("FingerprintJS", re.compile(r"(?i)(fingerprintjs|fpjs\.io|/fp/)")),
]

CHALLENGE_STATUS_HEADERS = [
    ("Cloudflare challenge", re.compile(r"(?i)cf-mitigated")),
]

FRAGILE_PARAM_NAMES = re.compile(
    r"(?i)^(csrf|xsrf|nonce|_token|timestamp|ts|expires|signature|sig)$"
)


def _get_header(headers: Dict[str, str], name: str):
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def _check_captcha(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    body_text = record.get("response_text_raw") or str(record.get("response_body") or "")
    if not body_text:
        return findings
    for label, pattern in CAPTCHA_PATTERNS:
        if pattern.search(body_text):
            findings.append(_finding(record, "CAPTCHA detected", "blocker", f"{label} present in response body"))
    return findings


def _check_bot_defense(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    resp_headers = record.get("response_headers", {}) or {}
    header_blob = " ".join(f"{k}: {v}" for k, v in resp_headers.items())
    body_text = record.get("response_text_raw") or str(record.get("response_body") or "")
    combined = header_blob + " " + body_text
    for label, pattern in BOT_DEFENSE_VENDORS:
        if pattern.search(combined):
            findings.append(_finding(record, "Bot-defense signal", "high", f"{label} detected (headers/cookies or body)"))
    status = record.get("status") or 0
    if status == 403 and any(pattern.search(header_blob) for _, pattern in CHALLENGE_STATUS_HEADERS):
        findings.append(_finding(record, "Bot-defense signal", "blocker", "403 response with Cloudflare challenge marker"))
    return findings


def _check_fragile_params(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings = []
    query_params = record.get("query_params", {}) or {}
    req_body = record.get("request_body")
    names = set(query_params.keys())
    if isinstance(req_body, dict):
        names |= set(req_body.keys())
    fragile = [n for n in names if FRAGILE_PARAM_NAMES.match(n)]
    if fragile:
        findings.append(_finding(
            record, "Replay-fragile request", "medium",
            f"Depends on short-lived value(s): {', '.join(sorted(fragile))} -- captured request will likely fail if replayed verbatim later",
        ))
    return findings


def _finding(record: Dict[str, Any], category: str, severity: str, description: str) -> Dict[str, Any]:
    return {
        "system": record.get("host"),
        "endpoint": record.get("path"),
        "method": record.get("method"),
        "url": record.get("url"),
        "status": record.get("status"),
        "category": category,
        "severity": severity,
        "description": description,
        "started_at": record.get("started_at"),
    }


CHECKS = [_check_captcha, _check_bot_defense, _check_fragile_params]


def scan_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run every bot-defense check against every record."""
    findings: List[Dict[str, Any]] = []
    for record in records:
        for check in CHECKS:
            findings.extend(check(record))
    order = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return findings


def automatable_endpoints(records: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Per system+endpoint, report whether it looks safe to script against
    (no CAPTCHA/bot-defense signal seen) or is likely to need special
    handling. This is a heuristic, not a guarantee.
    """
    blocked_keys = {
        (f["system"], f["endpoint"]) for f in findings if f["category"] in ("CAPTCHA detected", "Bot-defense signal")
    }
    fragile_keys = {
        (f["system"], f["endpoint"]) for f in findings if f["category"] == "Replay-fragile request"
    }
    seen = {}
    for r in records:
        key = (r.get("host"), r.get("path"))
        if key not in seen:
            status = "blocked" if key in blocked_keys else ("fragile" if key in fragile_keys else "likely automatable")
            seen[key] = {
                "system": r.get("host"),
                "endpoint": r.get("path"),
                "method": r.get("method"),
                "automation_status": status,
            }
    return list(seen.values())
