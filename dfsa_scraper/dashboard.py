"""
dashboard.py

The optional monitoring layer. This is the ONLY file in the project that
imports FastAPI/WebSocket. dfsa_common.py, the parsers, and
run_scraper_with_dashboard.py know nothing about this file -- they only
ever see a plain `on_event(...)` callback, which is what keeps the scraper
independently runnable (requirement 5).

Serves:
  GET  /                    the dashboard UI (dashboard.html)
  GET  /ws                  WebSocket: full event backlog on connect, then
                             every new event pushed live
  POST /run/julius-baer     starts the single-firm test run in a
                             background thread

Run with:
    uvicorn dashboard:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from events import EventBus
import run_scraper_with_dashboard as runner
import run_register

app = FastAPI()
bus = EventBus()

# --- Bridging the scraper's synchronous world to FastAPI's async world ---
#
# The scraper runs in a plain background thread (requests is blocking, not
# async). bus.emit() therefore also happens on that thread. We can't call
# `await websocket.send_json()` directly from a non-async thread, so the
# subscriber below just hands the event to an asyncio.Queue via
# call_soon_threadsafe, and a single asyncio task (_broadcast_loop) drains
# that queue and does the actual sending. This is the standard pattern for
# getting events from a worker thread into an asyncio event loop.
_loop: asyncio.AbstractEventLoop | None = None
_queue: "asyncio.Queue | None" = None
_clients: set[WebSocket] = set()


def _on_event(event: dict) -> None:
    if _loop is not None and _queue is not None:
        _loop.call_soon_threadsafe(_queue.put_nowait, event)


bus.subscribe(_on_event)


@app.on_event("startup")
async def startup() -> None:
    global _loop, _queue
    _loop = asyncio.get_running_loop()
    _queue = asyncio.Queue()
    asyncio.create_task(_broadcast_loop())


async def _broadcast_loop() -> None:
    assert _queue is not None
    while True:
        event = await _queue.get()
        dead = []
        for ws in _clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _clients.add(websocket)
    # Replay full history first, so a client that connects mid-run (or
    # after a page refresh) sees everything that already happened, not
    # just events emitted after it connected.
    #
    # CONFIRMED (2026-08): a client can disconnect WHILE this replay is
    # still in progress (e.g. a page refresh that reconnects before the
    # previous connection has fully torn down), which raises
    # WebSocketDisconnect from inside send_json here -- previously
    # uncaught, since only the receive_text() loop below was wrapped.
    # This crashed that one connection attempt with an unhandled
    # exception in the server log and left the dashboard showing
    # "disconnected", even though the scraper itself (a separate
    # background thread, untouched by this) kept running fine. Catching
    # it here just means "this particular reconnect attempt didn't pan
    # out, clean up and move on" -- the browser will simply retry.
    try:
        for event in bus.events:
            await websocket.send_json(event)
    except WebSocketDisconnect:
        _clients.discard(websocket)
        return
    try:
        while True:
            # We don't expect the client to send anything; this just keeps
            # the connection open and lets us detect disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        _clients.discard(websocket)


@app.post("/run/julius-baer")
async def run_julius_baer() -> dict:
    """
    Starts the single-firm test run (requirement 6) in a background
    thread so the blocking `requests` calls inside it don't stall the
    async event loop / WebSocket broadcasting.
    """
    thread = threading.Thread(target=runner.run_julius_baer_test, args=(bus,), daemon=True)
    thread.start()
    return {"status": "started"}


@app.post("/run/firms")
async def run_firms(max_firms: int | None = None) -> dict:
    """
    Starts the production register walk in a background thread.
    max_firms=None (the default, when the client omits the query param)
    means no limit -- the full register. Pass ?max_firms=N to cap it for
    a small test run instead.
    """
    limit = None if max_firms is not None and max_firms <= 0 else max_firms
    thread = threading.Thread(target=run_register.run_firms_register, args=(bus, limit), daemon=True)
    thread.start()
    return {"status": "started", "max_firms": limit if limit is not None else "ALL"}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    html_path = Path(__file__).parent / "dashboard.html"
    return html_path.read_text(encoding="utf-8")