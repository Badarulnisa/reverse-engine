from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional
from datetime import datetime

@dataclass
class RegistryRecord:
    business_name: str
    license_number: str
    issue_date: Optional[str]
    expiry_date: Optional[str]
    address: str
    manager: str
    activity: str
    status: str

def _get(d: dict, *keys, default=""):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur

def _normalize_date(raw: Any) -> Optional[str]:
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        try:
            ts = raw / 1000 if raw > 10_000_000_000 else raw
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return None
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw

def parse_registry_record(raw: dict) -> RegistryRecord:
    return RegistryRecord(
        business_name=_get(raw, "companyName") or _get(raw, "business_name") or _get(raw, "name"),
        license_number=_get(raw, "licenseNumber") or _get(raw, "license_no"),
        issue_date=_normalize_date(_get(raw, "issueDate") or _get(raw, "license_issue_date")),
        expiry_date=_normalize_date(_get(raw, "expiryDate") or _get(raw, "license_expiry_date")),
        address=_get(raw, "address", "fullAddress") or _get(raw, "address"),
        manager=_get(raw, "manager", "name") or _get(raw, "managerName"),
        activity=_get(raw, "activity", "description") or _get(raw, "businessActivity"),
        status=_get(raw, "licenseStatus") or _get(raw, "status"),
    )

def parse_registry_response(payload: dict, records_key: str = "results") -> list[RegistryRecord]:
    if isinstance(payload, list):
        entries = payload
    else:
        entries = payload.get(records_key, payload.get("data", []))
    if isinstance(entries, dict):
        entries = [entries]
    return [parse_registry_record(e) for e in entries if isinstance(e, dict)]

