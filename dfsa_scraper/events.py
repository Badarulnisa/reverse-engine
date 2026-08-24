"""
events.py

Tiny, dependency-free event bus used to decouple the scraper (which knows
WHEN something interesting happens) from anything that wants to observe it
(a dashboard, a log file, a test, or nothing at all).

The scraper modules only ever call `bus.emit(...)`. They never import
FastAPI, websockets, or anything dashboard-related. This is what keeps
dfsa_common.py runnable headless with zero extra dependencies -- requirement
5 ("preserve headless compatibility").

Each event is a plain dict so it's trivially JSON-serializable for the
WebSocket layer. The FULL structured payload is always kept (requirement 1:
"keep the raw evidence") -- the dashboard decides what to summarize vs. show
in an expandable "Raw Event" panel; the bus itself never throws information
away.
"""
from __future__ import annotations

import time
import traceback as _traceback
import uuid
from typing import Callable, Optional

# Short, human-readable explanation shown next to each event type in the
# dashboard (requirement 2: "make the dashboard educational"). Kept here,
# not duplicated in the frontend, so it can never drift out of sync with
# what the backend actually emits.
EVENT_EXPLANATIONS = {
    "RUN_STARTED": "A scraper run has begun.",
    "RUN_FINISHED": "The scraper run has finished (successfully or not).",
    "FIRM_DISCOVERED": "A firm was found (normally via a listing page row) and queued for detail collection.",
    "REQUEST_STARTED": "An HTTP request is being sent to the DFSA site.",
    "REQUEST_SUCCESS": "The HTTP request completed with a 200 OK response.",
    "REQUEST_RETRY": "The request failed or returned a non-200 status and is being retried.",
    "REQUEST_FAILED": "The request failed after all retries were exhausted. This item will not be processed further unless retried manually.",
    "DETAIL_FETCH_STARTED": "Fetching the firm's detail page. Firm Details, Individuals, and Regulatory Actions all arrive in this one server-rendered response -- no separate AJAX call is needed (confirmed earlier via DevTools).",
    "DETAIL_FETCH_SUCCESS": "The firm detail page was successfully retrieved. The parser can now process the HTML response.",
    "PARSING_STARTED": "Parsing the retrieved HTML into structured fields.",
    "FIRM_FIELDS_PARSED": "The Firm Details section (Legal Status, Reference Number, etc.) was parsed.",
    "INDIVIDUALS_PARSED": "The Individuals table was parsed into structured rows. Duplicate names are preserved deliberately -- they represent separate tenures, not parsing errors.",
    "REGULATORY_ACTIONS_PARSED": "The Regulatory Actions table was parsed. An empty result is a normal, valid outcome for most firms.",
    "PARSER_WARNING": "Parsing succeeded but something looked unexpected. Worth a manual look, not necessarily a bug.",
    "PARSER_ERROR": "The page was retrieved successfully, but the expected HTML structure was not found. This may indicate a parser bug or a website structure change.",
    "VALIDATION_STARTED": "Checking the parsed record for missing required fields or malformed values.",
    "VALIDATION_WARNING": "The record passed but has a data-quality issue worth flagging.",
    "FIRM_COMPLETED": "This firm fully passed through Discovery, Fetch, Parse, and Validate.",
    "FIRM_SKIPPED_FROM_CHECKPOINT": "This firm was already completed in a previous run, per checkpoint.json. Its stored record was loaded from disk rather than re-fetched.",
    "FIRM_FAILED": "This firm did not complete the pipeline. Check the stage and error fields for why.",
    "RECORD_SAVED": "The completed record was appended to the output file on disk.",
    "RECORD_SAVE_FAILED": "The firm was parsed successfully, but writing its record to disk failed. Check the error field -- the data was not lost from memory but was not persisted either.",
}

PIPELINE_STAGES = ["DISCOVERY", "FETCH", "PARSE", "VALIDATE", "COMPLETE"]

# Maps each event type to the pipeline stage it belongs to, so the frontend
# can light up the DISCOVERY -> FETCH -> PARSE -> VALIDATE -> COMPLETE strip
# without re-deriving that mapping itself.
EVENT_STAGE = {
    "FIRM_DISCOVERED": "DISCOVERY",
    "REQUEST_STARTED": "FETCH",
    "REQUEST_RETRY": "FETCH",
    "REQUEST_FAILED": "FETCH",
    "DETAIL_FETCH_STARTED": "FETCH",
    "REQUEST_SUCCESS": "FETCH",
    "DETAIL_FETCH_SUCCESS": "FETCH",
    "PARSING_STARTED": "PARSE",
    "FIRM_FIELDS_PARSED": "PARSE",
    "INDIVIDUALS_PARSED": "PARSE",
    "REGULATORY_ACTIONS_PARSED": "PARSE",
    "PARSER_WARNING": "PARSE",
    "PARSER_ERROR": "PARSE",
    "VALIDATION_STARTED": "VALIDATE",
    "VALIDATION_WARNING": "VALIDATE",
    "FIRM_COMPLETED": "COMPLETE",
    "FIRM_FAILED": "COMPLETE",
    "FIRM_SKIPPED_FROM_CHECKPOINT": "COMPLETE",
}


class EventBus:
    """
    Minimal pub/sub. `emit()` builds a full structured event dict (never a
    pre-summarized string) and hands it to every subscriber. Subscribers are
    plain callables -- the dashboard registers one that forwards to
    WebSocket clients; a headless run can register one that just prints.
    With zero subscribers, emit() still records history but does no I/O,
    which is what lets the scraper call it unconditionally with no
    dashboard attached.
    """

    def __init__(self):
        self._subscribers: list[Callable[[dict], None]] = []
        self.events: list[dict] = []  # full history, replayed to late-connecting clients

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        self._subscribers.append(callback)

    def emit(
        self,
        event_type: str,
        *,
        company: Optional[str] = None,
        url: Optional[str] = None,
        stage: Optional[str] = None,
        http_status: Optional[int] = None,
        response_ms: Optional[float] = None,
        retry: Optional[int] = None,
        error: Optional[str] = None,
        exception: Optional[BaseException] = None,
        counts: Optional[dict] = None,
        message: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        event = {
            "id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "type": event_type,
            "stage": stage or EVENT_STAGE.get(event_type),
            "explanation": EVENT_EXPLANATIONS.get(event_type, ""),
            "company": company,
            "url": url,
            "http_status": http_status,
            "response_ms": response_ms,
            "retry": retry,
            "error": error,
            "traceback": (
                "".join(_traceback.format_exception(type(exception), exception, exception.__traceback__))
                if exception is not None
                else None
            ),
            "counts": counts,
            "message": message,
            "extra": extra,
        }
        self.events.append(event)
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception:
                # A broken subscriber (e.g. a dropped websocket) must never
                # break the scraper itself.
                pass
        return event