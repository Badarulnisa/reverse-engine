"""
tor_rotation.py

Wraps the Tor rotation mechanism the user already built and tested in
test_tor_rotation.py (originally curl_cffi + stem/NEWNYM; this module
now uses plain requests + PySocks instead -- see build_tor_session()'s
docstring for why). Rotation itself (stem + NEWNYM to the Tor control
port) is unchanged from the original design.

Requires:
  - Tor running locally with a control port (the user's own torrc),
    e.g.  tor.exe -f torrc
  - This project's torrc uses CookieAuthentication (not a password) --
    TOR_CONTROL_PASSWORD below is optional, only needed if torrc is
    ever switched to HashedControlPassword instead.
  - pip install requests[socks] stem

Configuration is read from environment variables:

    TOR_SOCKS_PROXY      default "socks5h://127.0.0.1:9050"
    TOR_CONTROL_PORT     default 9051
    TOR_CONTROL_PASSWORD optional -- unset uses Tor's cookie auth file
                          instead (this project's actual torrc setup).
"""
from __future__ import annotations

import logging
import os
import threading
import time

import requests
from stem import Signal
from stem.control import Controller

log = logging.getLogger("tor_rotation")

TOR_SOCKS_PROXY = os.environ.get("TOR_SOCKS_PROXY", "socks5h://127.0.0.1:9050")
TOR_CONTROL_PORT = int(os.environ.get("TOR_CONTROL_PORT", "9051"))
TOR_CONTROL_PASSWORD = os.environ.get("TOR_CONTROL_PASSWORD")  # optional

# Tor enforces roughly a 10s minimum interval between NEWNYM signals and
# will just no-op / rate-limit requests sent faster than that.
_rotation_lock = threading.Lock()
_last_rotation_ts = 0.0
_MIN_ROTATION_INTERVAL_SECONDS = 12.0  # a little above Tor's own ~10s floor

# --- NEW: how long to actually wait for a *new* circuit to be built and
# healthy before letting a caller send a real request over it. 6s was too
# short -- the "Have tried resolving... giving up" / guard-overload log
# lines you saw were circuits that hadn't actually finished forming yet.
_POST_ROTATION_SETTLE_SECONDS = 12.0

# --- NEW: request-level tuning
DEFAULT_REQUEST_TIMEOUT = 30  # was implicitly 10s at call sites -- too tight for Tor
MAX_FETCH_RETRIES = 5
PRECHECK_URL = "https://check.torproject.org/api/ip"
PRECHECK_TIMEOUT = 15


def build_tor_session(impersonate: str = "chrome124") -> requests.Session:
    """
    FALLBACK IMPLEMENTATION (2026-08): the originally-planned curl_cffi
    session (Chrome TLS/browser fingerprint spoofing over Tor) could not
    load on this machine -- curl_cffi's compiled _wrapper extension
    failed with "DLL load failed" under this venv's Python 3.14 /
    MinGW-built interpreter, even after reinstalling to the matching
    mingw wheel. This uses plain `requests` + PySocks instead -- pure
    Python, no compiled extension, so no DLL risk. It routes through the
    exact same Tor SOCKS5 proxy.

    Trade-off: no TLS/browser fingerprint spoofing. The `impersonate`
    parameter is accepted but unused, kept only so call sites don't
    need to change if curl_cffi is restored later.
    """
    if impersonate:
        log.info(
            "build_tor_session: using plain requests (curl_cffi fallback active) -- "
            "TLS fingerprint spoofing ('%s') is NOT applied.", impersonate,
        )
    session = requests.Session()
    session.proxies = {"http": TOR_SOCKS_PROXY, "https": TOR_SOCKS_PROXY}
    return session


def rotate_tor_circuit(reason: str, worker_name: str = "main") -> bool:
    """Sends NEWNYM to request a new Tor circuit (new exit IP). Serialized
    across threads and rate-limited to avoid hammering the control port
    when several workers fail around the same time.

    Returns True if a rotation was actually sent and we waited for it to
    settle. Returns False if this call was skipped because another
    rotation just happened -- in that case, the caller's active circuit
    is already recent, so it's still safe to proceed without waiting.
    """
    global _last_rotation_ts
    with _rotation_lock:
        now = time.time()
        elapsed = now - _last_rotation_ts
        if elapsed < _MIN_ROTATION_INTERVAL_SECONDS:
            log.info(
                "[%s] skipping Tor rotation (%.1fs since last rotation, "
                "under the %.0fs floor) -- reason was: %s",
                worker_name, elapsed, _MIN_ROTATION_INTERVAL_SECONDS, reason,
            )
            return False

        log.warning("[%s] rotating Tor circuit -- reason: %s", worker_name, reason)
        try:
            with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
                if TOR_CONTROL_PASSWORD:
                    controller.authenticate(password=TOR_CONTROL_PASSWORD)
                else:
                    controller.authenticate()
                controller.signal(Signal.NEWNYM)
        except Exception as e:
            log.error("[%s] Tor control port rotation failed: %s", worker_name, e)
            raise
        _last_rotation_ts = time.time()

    # NEW: settle time bumped from 6s -> 12s. Tor needs a moment to
    # actually finish building the new circuit; firing a request too
    # soon is exactly what produced the "3 different places, giving up"
    # and guard-overload lines in your logs.
    time.sleep(_POST_ROTATION_SETTLE_SECONDS)
    log.info("[%s] new Tor circuit should now be active", worker_name)
    return True


def _verify_circuit_alive(session: requests.Session, worker_name: str = "main") -> bool:
    """Cheap precheck against check.torproject.org before spending a full
    timeout window on the real target. Catches dead/half-built circuits
    early instead of discovering them via a 30s hang on the real request.
    """
    try:
        r = session.get(PRECHECK_URL, timeout=PRECHECK_TIMEOUT)
        return r.status_code == 200
    except requests.exceptions.RequestException as e:
        log.info("[%s] circuit precheck failed: %s", worker_name, e)
        return False


def fetch_with_retry(
    session: requests.Session,
    url: str,
    worker_name: str = "main",
    method: str = "GET",
    max_retries: int = MAX_FETCH_RETRIES,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    **kwargs,
) -> requests.Response:
    """Drop-in replacement for session.get()/request() that:
      - prechecks the circuit is alive before hitting the real target
      - retries + rotates on timeouts/connection errors (dead circuit)
      - retries + rotates + backs off on 403/429 (real rate-limit/block
        from the target, not a Tor problem)
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            if not _verify_circuit_alive(session, worker_name):
                raise requests.exceptions.ConnectionError("circuit dead on precheck")

            response = session.request(method, url, timeout=timeout, **kwargs)

            if response.status_code in (403, 429):
                log.warning(
                    "[%s] attempt %d: target returned %d -> rotating + backoff",
                    worker_name, attempt, response.status_code,
                )
                rotate_tor_circuit(f"HTTP {response.status_code} from target", worker_name)
                time.sleep(2 ** attempt)
                continue

            response.raise_for_status()
            return response

        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            last_exc = e
            log.warning(
                "[%s] attempt %d: circuit failed (%s) -> rotating",
                worker_name, attempt, e,
            )
            rotate_tor_circuit(f"connection/timeout failure: {e}", worker_name)
            continue

        except requests.exceptions.HTTPError as e:
            # Non-403/429 HTTP error -- not a Tor/rate-limit issue, don't retry blindly
            raise

    raise RuntimeError(
        f"[{worker_name}] failed after {max_retries} attempts: {url}"
    ) from last_exc