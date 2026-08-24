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
# Optional. This project's actual torrc (tor-expert-bundle .../tor/torrc)
# uses `CookieAuthentication 1`, NOT `HashedControlPassword` -- there is
# no password to configure. stem's Controller.authenticate() auto-detects
# this: with no password argument, it reads Tor's own auth cookie file
# (any local process that can read it is trusted) instead of needing a
# password at all. TOR_CONTROL_PASSWORD is kept only as an optional
# override, in case torrc is ever reconfigured to use
# HashedControlPassword instead of cookie auth.
TOR_CONTROL_PASSWORD = os.environ.get("TOR_CONTROL_PASSWORD")  # optional

# Tor enforces roughly a 10s minimum interval between NEWNYM signals and
# will just no-op / rate-limit requests sent faster than that. This lock
# + timestamp make that safe across multiple concurrent worker threads,
# so two workers hitting a failure at the same moment don't both fire
# NEWNYM back-to-back for no benefit (or hammer the control port).
_rotation_lock = threading.Lock()
_last_rotation_ts = 0.0
_MIN_ROTATION_INTERVAL_SECONDS = 12.0  # a little above Tor's own ~10s floor


def build_tor_session(impersonate: str = "chrome124") -> requests.Session:
    """
    FALLBACK IMPLEMENTATION (2026-08): the originally-planned curl_cffi
    session (Chrome TLS/browser fingerprint spoofing over Tor) could not
    load on this machine -- curl_cffi's compiled _wrapper extension
    failed with "DLL load failed" under this venv's Python 3.14 /
    MinGW-built interpreter, even after reinstalling to the matching
    mingw wheel. Rather than keep debugging the Windows/MinGW/curl_cffi
    toolchain under a hard deadline, this uses plain `requests` +
    PySocks instead -- pure Python, no compiled extension, so no DLL
    risk. It routes through the exact same Tor SOCKS5 proxy.

    Trade-off: no TLS/browser fingerprint spoofing. Nothing in this
    project's actual failure evidence (ConnectionResetError, stalled
    detail_done_ids under 3 workers / 0.5s delay) pointed to
    fingerprint-based blocking specifically -- it looked like plain
    rate-limiting, which IP rotation alone addresses regardless of
    fingerprint. If ADGM turns out to also be fingerprinting TLS/HTTP
    clients, this would need revisiting (e.g. retrying curl_cffi on a
    standard CPython build/venv instead of this MinGW one, or a
    different impersonation library).

    The `impersonate` parameter is accepted but unused, kept only so
    call sites don't need to change if curl_cffi is restored later.
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
    """Sends NEWNYM to request a new Tor circuit (new exit IP), same
    mechanism as test_tor_rotation.py's rotate_tor_circuit(). Serialized
    across threads and rate-limited to avoid hammering the control port
    when several workers fail around the same time.

    Returns True if a rotation was actually sent, False if this call was
    skipped because another rotation just happened (still safe to
    proceed -- the caller's next request will use whatever circuit is
    currently active, which is already fresh).
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
                # No password argument when TOR_CONTROL_PASSWORD is unset --
                # stem then auto-detects the auth method Tor is actually
                # using. This project's torrc uses CookieAuthentication,
                # not a password, so this is the normal path here.
                if TOR_CONTROL_PASSWORD:
                    controller.authenticate(password=TOR_CONTROL_PASSWORD)
                else:
                    controller.authenticate()
                controller.signal(Signal.NEWNYM)
        except Exception as e:
            log.error("[%s] Tor control port rotation failed: %s", worker_name, e)
            raise
        _last_rotation_ts = time.time()

    # Give Tor a moment to actually build the new circuit before the
    # caller's next request goes out over it (same 6s pause test_tor_
    # rotation.py used, confirmed to work in the user's own testing).
    time.sleep(6)
    log.info("[%s] new Tor circuit should now be active", worker_name)
    return True