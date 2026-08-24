"""
Lightweight local monitoring dashboard for adgm_scraper.py.

Runs a tiny HTTP server on localhost:8765 in a background thread. The
scraper calls log_event(...) on every request/response; events are kept
in an in-memory ring buffer and served as JSON to a simple auto-refreshing
HTML page. No external dependencies beyond the standard library.

Usage from the scraper:
    from monitor import log_event, start_monitor
    start_monitor()             # call once, early
    log_event("request", ...)   # call around each HTTP call
    log_event("response", ...)
    log_event("error", ...)

Open http://localhost:8765 in a browser while the scraper runs.
"""

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_events = deque(maxlen=500)
_lock = threading.Lock()
_stats = {"requests": 0, "errors": 0, "companies_found": 0, "started_at": None}

DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ADGM Scraper Monitor</title>
<style>
body { font-family: -apple-system, Segoe UI, sans-serif; background: #0f1117; color: #e6e6e6; margin: 0; padding: 20px; }
h1 { font-size: 18px; color: #7dd3fc; }
.stats { display: flex; gap: 20px; margin-bottom: 16px; }
.stat { background: #1a1d27; padding: 10px 16px; border-radius: 8px; }
.stat .n { font-size: 22px; font-weight: bold; color: #7dd3fc; }
.stat .l { font-size: 11px; color: #999; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; padding: 6px; border-bottom: 1px solid #333; color: #999; position: sticky; top: 0; background: #0f1117; }
td { padding: 6px; border-bottom: 1px solid #1f2230; vertical-align: top; }
.type-request { color: #93c5fd; }
.type-response { color: #86efac; }
.type-error { color: #fca5a5; }
.mono { font-family: monospace; word-break: break-all; max-width: 500px; }
</style></head>
<body>
<h1>ADGM Scraper — Live Monitor</h1>
<div class="stats">
  <div class="stat"><div class="n" id="s-req">0</div><div class="l">requests</div></div>
  <div class="stat"><div class="n" id="s-err">0</div><div class="l">errors</div></div>
  <div class="stat"><div class="n" id="s-found">0</div><div class="l">companies found</div></div>
  <div class="stat"><div class="n" id="s-uptime">0s</div><div class="l">uptime</div></div>
</div>
<table>
<thead><tr><th>Time</th><th>Type</th><th>Detail</th></tr></thead>
<tbody id="rows"></tbody>
</table>
<script>
async function refresh() {
  const res = await fetch('/api/events');
  const data = await res.json();
  document.getElementById('s-req').textContent = data.stats.requests;
  document.getElementById('s-err').textContent = data.stats.errors;
  document.getElementById('s-found').textContent = data.stats.companies_found;
  document.getElementById('s-uptime').textContent = data.stats.uptime + 's';
  const rows = document.getElementById('rows');
  rows.innerHTML = data.events.slice().reverse().map(e =>
    `<tr><td>${e.time}</td><td class="type-${e.type}">${e.type}</td><td class="mono">${e.detail}</td></tr>`
  ).join('');
}
setInterval(refresh, 1500);
refresh();
</script>
</body></html>
"""


def start_monitor(port: int = 8765):
    _stats["started_at"] = time.time()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # silence default request logging

        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
            elif self.path == "/api/events":
                with _lock:
                    events_copy = list(_events)
                    stats_copy = dict(_stats)
                stats_copy["uptime"] = int(time.time() - stats_copy["started_at"])
                del stats_copy["started_at"]
                body = json.dumps({"events": events_copy, "stats": stats_copy}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    server = ThreadingHTTPServer(("localhost", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[monitor] Dashboard running at http://localhost:{port}")
    return server


def log_event(event_type: str, detail: str):
    """event_type: 'request' | 'response' | 'error'"""
    with _lock:
        _events.append({
            "time": time.strftime("%H:%M:%S"),
            "type": event_type,
            "detail": detail[:300],
        })
        if event_type == "request":
            _stats["requests"] += 1
        elif event_type == "error":
            _stats["errors"] += 1


def log_companies_found(count: int):
    with _lock:
        _stats["companies_found"] = count