"""
Analyzes groups of calls to the same logical endpoint (same method + path_template)
to infer the pagination strategy in use.

Improvements over v1:
  - checks request_body top-level keys, not just query_params
  - uses all available samples (not just first 2) to verify step consistency
  - emits a confidence score instead of a binary detected/not-detected
  - cursor detection inspects response_body to find where the cursor value
    actually appears, instead of assuming a fixed "data.<key>" path
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel

NUMERIC_KEYS = ["offset", "page", "p", "skip", "startIndex", "start"]
CURSOR_KEYS = ["cursor", "next_token", "pageToken", "after", "next_cursor"]


class PaginationProfile(BaseModel):
    strategy: str  # "OFFSET", "PAGE", "CURSOR", or "NONE"
    param_name: Optional[str] = None
    param_source: Optional[str] = None  # "query" or "body"
    step_size: int = 1
    initial_value: int = 0
    cursor_json_path: Optional[str] = None
    confidence: float = 0.0
    sample_size: int = 0


def _get_param_sources(call: Dict[str, Any]) -> Dict[str, Any]:
    """Merge query params and top-level request body keys into one lookup,
    tagging where each came from."""
    merged: Dict[str, Any] = {}
    for k, v in (call.get("query_params") or {}).items():
        merged[k] = ("query", v)
    body = call.get("request_body")
    if isinstance(body, dict):
        for k, v in body.items():
            if k not in merged:  # query params take precedence if both present
                merged[k] = ("body", v)
    return merged


def _numeric_sequence_confidence(values: List[int]) -> float:
    """
    Given an ordered list of observed numeric values for a candidate pagination
    param, return (is_consistent, step, confidence).
    Confidence rises with sample size and consistency of the step between samples.
    """
    if len(values) < 2:
        return 0.0
    steps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    if any(s <= 0 for s in steps):
        return 0.0
    # consistent if all steps are equal (allow off-by-nothing; exact match required)
    consistent = len(set(steps)) == 1
    if not consistent:
        return 0.0
    # more samples agreeing on the same step = higher confidence, caps at 0.95 for <5 samples
    sample_bonus = min(len(values) / 5.0, 1.0)
    return 0.6 + 0.35 * sample_bonus


def _find_cursor_json_path(response_body: Any, cursor_value: Any, _prefix: str = "") -> Optional[str]:
    """Search a response body (dict/list) for the given cursor_value, return dotted path to it."""
    if response_body is None or cursor_value is None:
        return None
    if isinstance(response_body, dict):
        for k, v in response_body.items():
            path = f"{_prefix}.{k}" if _prefix else k
            if v == cursor_value:
                return path
            found = _find_cursor_json_path(v, cursor_value, path)
            if found:
                return found
    elif isinstance(response_body, list):
        for i, item in enumerate(response_body):
            path = f"{_prefix}[{i}]"
            found = _find_cursor_json_path(item, cursor_value, path)
            if found:
                return found
    return None


def detect_pagination(calls: List[Dict[str, Any]]) -> PaginationProfile:
    """Analyzes a group of calls to the same endpoint to infer pagination logic."""
    if not calls:
        return PaginationProfile(strategy="NONE", sample_size=0)

    param_maps = [_get_param_sources(c) for c in calls]
    candidate_keys = set()
    for pm in param_maps:
        candidate_keys.update(pm.keys())

    # --- numeric offset/page detection ---
    best: Optional[PaginationProfile] = None
    for key in NUMERIC_KEYS:
        if key not in candidate_keys:
            continue
        values: List[int] = []
        source = None
        ok = True
        for pm in param_maps:
            if key not in pm:
                ok = False
                break
            src, raw_val = pm[key]
            try:
                values.append(int(raw_val))
                source = src
            except (ValueError, TypeError):
                ok = False
                break
        if not ok or len(values) < 1:
            continue

        if len(values) == 1:
            # only one sample ever hit this param - low-confidence guess
            step_guess = 20 if key in ("offset", "skip") else 1
            candidate = PaginationProfile(
                strategy="OFFSET" if key in ("offset", "skip") else "PAGE",
                param_name=key,
                param_source=source,
                step_size=step_guess,
                initial_value=values[0],
                confidence=0.25,
                sample_size=1,
            )
        else:
            confidence = _numeric_sequence_confidence(values)
            if confidence == 0.0:
                continue
            step = values[1] - values[0]
            candidate = PaginationProfile(
                strategy="OFFSET" if key in ("offset", "skip") else "PAGE",
                param_name=key,
                param_source=source,
                step_size=step,
                initial_value=values[0],
                confidence=confidence,
                sample_size=len(values),
            )

        if best is None or candidate.confidence > best.confidence:
            best = candidate

    if best is not None and best.confidence >= 0.5:
        return best

    # --- cursor/token detection ---
    for key in CURSOR_KEYS:
        if key not in candidate_keys:
            continue

        # find the source (query/body) from whichever call actually used the param
        src = next((pm[key][0] for pm in param_maps if key in pm), None)
        if src is None:
            continue

        json_path = None
        # look for the cursor value used in call N inside the response of call N-1
        for i in range(1, len(calls)):
            pm = param_maps[i]
            if key in pm:
                _, val_used = pm[key]
                prev_resp = calls[i - 1].get("response_body")
                found = _find_cursor_json_path(prev_resp, val_used)
                if found:
                    json_path = found
                    break

        confidence = 0.85 if json_path else 0.4
        return PaginationProfile(
            strategy="CURSOR",
            param_name=key,
            param_source=src,
            cursor_json_path=json_path,
            confidence=confidence,
            sample_size=len(calls),
        )

    if best is not None:
        # low-confidence numeric guess, nothing better found
        return best

    return PaginationProfile(strategy="NONE", sample_size=len(calls))