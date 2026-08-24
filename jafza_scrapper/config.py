"""
Configuration for the JAFZA Google enrichment pipeline.

The API key is read from the environment only. It is never written to
disk, logged, or embedded in any output file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class MissingApiKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    google_maps_api_key: str
    requests_per_second: float = 5.0      # Places API default quota is generous; stay well under it
    max_retries: int = 3
    timeout_seconds: int = 10
    cache_path: str = "cache/places_cache.sqlite3"
    geography_hint: str = "Jebel Ali Free Zone, Dubai, UAE"
    country_bias: str = "AE"


def load_settings() -> Settings:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise MissingApiKeyError(
            "GOOGLE_MAPS_API_KEY is not set. Export it in your shell "
            "(never hardcode it in source):\n"
            "  export GOOGLE_MAPS_API_KEY=your_key_here"
        )
    return Settings(google_maps_api_key=key)


def redact(key: str) -> str:
    """For logging only -- never log the full key."""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"