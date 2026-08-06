from typing import Dict, List, Optional
from pydantic import BaseModel


class PaginationProfile(BaseModel):
    strategy: str  # "OFFSET", "PAGE", "CURSOR", or "NONE"
    param_name: Optional[str] = None
    step_size: int = 1
    initial_value: int = 0
    cursor_json_path: Optional[str] = None


def detect_pagination(calls: List[Dict]) -> PaginationProfile:
    """Analyzes a group of calls to the same endpoint to infer pagination logic."""
    if len(calls) < 1:
        return PaginationProfile(strategy="NONE")

    # Common pagination keys to check in query params or JSON body
    numeric_keys = ["offset", "page", "p", "skip", "startIndex", "start"]
    cursor_keys = ["cursor", "next_token", "pageToken", "after"]

    # Check query parameters across captured calls
    first_params = calls[0].get("query_params", {})

    for key in numeric_keys:
        if key in first_params:
            try:
                val1 = int(first_params[key])
                if len(calls) > 1:
                    second_params = calls[1].get("query_params", {})
                    val2 = int(second_params.get(key, val1))
                    step = max(abs(val2 - val1), 1)
                else:
                    step = 20 if key in ["offset", "skip"] else 1

                strategy_type = (
                    "OFFSET" if key in ["offset", "skip"] else "PAGE"
                )
                return PaginationProfile(
                    strategy=strategy_type,
                    param_name=key,
                    step_size=step,
                    initial_value=val1,
                )
            except ValueError:
                continue

    for key in cursor_keys:
        if key in first_params:
            return PaginationProfile(
                strategy="CURSOR",
                param_name=key,
                cursor_json_path=f"data.{key}",
            )

    return PaginationProfile(strategy="NONE")