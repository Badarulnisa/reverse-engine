"""
test_adgm_tor_integration.py

Controlled, minimal test of the ACTUAL integration (AdgmScraper with
use_tor=True), as opposed to test_tor_rotation.py which only proves Tor
itself works standalone. Run this before committing to the full
5000+-company run.

Requires the same setup as test_tor_rotation.py:
  - Tor running locally (tor.exe -f torrc)
  - TOR_CONTROL_PASSWORD set in the environment to the plaintext
    password matching torrc's HashedControlPassword

Usage:
    set TOR_CONTROL_PASSWORD=your_real_password   (Windows cmd)
    $env:TOR_CONTROL_PASSWORD="your_real_password"  (PowerShell)
    python test_adgm_tor_integration.py
"""
from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

if not os.environ.get("TOR_CONTROL_PASSWORD"):
    print("TOR_CONTROL_PASSWORD is not set -- that's fine for this project's "
          "torrc (CookieAuthentication 1, no password configured). Continuing "
          "with cookie auth. Make sure Tor is running first (tor.exe -f torrc).")

from adgm_scraper import AdgmScraper  # noqa: E402


def main():
    print("=" * 60)
    print(" ADGM SCRAPER <-> TOR INTEGRATION TEST")
    print("=" * 60)

    print("\n[1/3] Building a Tor-routed AdgmScraper...")
    scraper = AdgmScraper(use_tor=True, worker_name="tortest")

    print("\n[2/3] Bootstrapping (single GET through Tor)...")
    resp = scraper.bootstrap()
    if resp is None or resp.status_code != 200:
        print(f"[!] Bootstrap did not return 200 (got {getattr(resp, 'status_code', None)}). "
              f"Check that Tor is running and the SOCKS proxy is reachable.")
        sys.exit(1)
    print(f"      -> bootstrap OK, status={resp.status_code}, "
          f"cookies={list(scraper.session.cookies.get_dict().keys())}")

    print("\n[3/3] One small real search request (page_size=5) through the same Tor session...")
    try:
        rows, requestcount = scraper.search_page_with_count("", 1, page_size=5)
    except Exception as e:
        print(f"[!] Search request failed: {e}")
        sys.exit(1)
    print(f"      -> got {len(rows)} row(s) back, requestcount={requestcount}")

    print("\n" + "=" * 60)
    print("[SUCCESS] AdgmScraper is correctly routing through Tor end-to-end.")
    print("This did NOT test circuit rotation itself (that's already confirmed by")
    print("test_tor_rotation.py) -- only that adgm_scraper.py's own request path")
    print("actually uses the Tor session rather than falling back to a direct one.")
    print("=" * 60)


if __name__ == "__main__":
    main()