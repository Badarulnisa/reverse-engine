"""
Thin client for the Google Places API (New) Text Search endpoint.

Endpoint used: POST https://places.googleapis.com/v1/places:searchText
Docs: https://developers.google.com/maps/documentation/places/web-service/text-search

This is a paid, quota-metered endpoint (Places API SKU). Each call to
`search_text` is one billable request regardless of how many candidates
come back. We do NOT call Place Details separately -- the fields we
need (name, address, location, website, phone, types) are requested
directly via the fieldMask on the Text Search call, since that keeps
this to one billable call per company instead of two.

We never log the API key. We never guess at response fields not
documented by Google -- if a field is absent we leave it as None.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from config import Settings
from matcher import PlaceCandidate

log = logging.getLogger("places_client")

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Only request the fields we actually use -- keeps the response small
# and is standard practice for the field-masked Places API (New).
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.websiteUri",
    "places.internationalPhoneNumber",
    "places.nationalPhoneNumber",
    "places.types",
    "places.googleMapsUri",
])


class PlacesApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class PlacesClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._session = requests.Session()
        self._min_interval = 1.0 / max(settings.requests_per_second, 0.1)
        self._last_call_ts = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_ts
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()

    def search_text(self, query: str) -> list[PlaceCandidate]:
        """One billable Text Search call. Returns up to Google's default
        page of candidates (no pagination -- we only need enough
        candidates to score, not an exhaustive list)."""
        self._throttle()

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._settings.google_maps_api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        body = {
            "textQuery": query,
            "regionCode": self._settings.country_bias,
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, self._settings.max_retries + 1):
            try:
                resp = self._session.post(
                    SEARCH_URL,
                    headers=headers,
                    json=body,
                    timeout=self._settings.timeout_seconds,
                )
            except requests.RequestException as exc:
                last_error = exc
                log.warning("request error (attempt %d/%d): %s",
                            attempt, self._settings.max_retries, exc)
                time.sleep(min(2 ** attempt, 8))
                continue

            if resp.status_code == 200:
                return self._parse(resp.json())

            if resp.status_code in (429, 500, 502, 503, 504):
                log.warning("retryable HTTP %d (attempt %d/%d)",
                            resp.status_code, attempt, self._settings.max_retries)
                time.sleep(min(2 ** attempt, 8))
                last_error = PlacesApiError(resp.text[:300], resp.status_code)
                continue

            # Non-retryable (400, 401, 403, ...) -- fail fast, don't burn retries
            raise PlacesApiError(
                f"Places API returned {resp.status_code}: {resp.text[:300]}",
                resp.status_code,
            )

        raise PlacesApiError(f"Exhausted retries: {last_error}")

    @staticmethod
    def _parse(payload: dict) -> list[PlaceCandidate]:
        out: list[PlaceCandidate] = []
        for p in payload.get("places", []):
            loc = p.get("location") or {}
            phone = p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber")
            out.append(PlaceCandidate(
                place_id=p.get("id", ""),
                name=(p.get("displayName") or {}).get("text", ""),
                formatted_address=p.get("formattedAddress"),
                latitude=loc.get("latitude"),
                longitude=loc.get("longitude"),
                website=p.get("websiteUri"),
                phone=phone,
                business_types=", ".join(p.get("types", [])) or None,
                maps_url=p.get("googleMapsUri"),
            ))
        return out