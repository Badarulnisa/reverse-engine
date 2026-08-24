"""
Standalone inspection helper -- opens the search-license page in a VISIBLE
browser and pauses so you can right-click -> Inspect on the elements you need
selectors for (search input, search button, next-page control).

Requires HEADLESS=false in .env.local (see instructions in the chat).

Usage:
    python inspect_page.py
"""

import time
from playwright.sync_api import sync_playwright

from env_config import ProxyConfig, EnvironmentConfig, build_browser, build_context, goto_with_retry

BASE_URL = "https://app.invest.dubai.ae/search-license"


def main():
    env_cfg = EnvironmentConfig()
    proxy_cfg = ProxyConfig()

    if env_cfg.headless:
        print("[!] WARNING: HEADLESS is currently True.")
        print("[!] Set HEADLESS=false in .env.local (project root) or you won't see a window.")
        print("[!] Continuing anyway in 3s...")
        time.sleep(3)

    with sync_playwright() as p:
        browser = build_browser(p, env_cfg, proxy_cfg)
        context = build_context(browser, env_cfg)
        page = context.new_page()

        print(f"[*] Navigating to {BASE_URL} ...")
        goto_with_retry(page, BASE_URL)
        time.sleep(2)

        print("\n" + "=" * 60)
        print("Browser window is open. In that window:")
        print("  1. Right-click the search input box -> Inspect")
        print("     -> copy the 'id' attribute, or a data-* attribute if present")
        print("  2. Right-click the search button -> Inspect (if there's a")
        print("     separate button; some forms just submit on Enter)")
        print("  3. Type a search (min 3 chars, e.g. 'abu') and hit search")
        print("  4. Right-click the pagination 'Next' control -> Inspect")
        print("=" * 60)
        print("\nThis terminal will stay paused. Press ENTER here when done.")
        input()

        # Optional: dump a quick auto-guess list of likely input/button
        # candidates to the terminal, in case DevTools inspection is awkward.
        print("\n[debug] Auto-scanning common candidate selectors on the page...")
        candidates = [
            "input[type='text']",
            "input[type='search']",
            "input",
            "button[type='submit']",
            "button",
            "[class*='pagination']",
            "[class*='next']",
            "[aria-label*='next' i]",
        ]
        for sel in candidates:
            try:
                count = page.locator(sel).count()
                if count > 0:
                    print(f"  selector={sel!r}  count={count}")
                    for i in range(min(count, 3)):
                        try:
                            outer = page.locator(sel).nth(i).evaluate("el => el.outerHTML")
                            print(f"    [{i}] {outer[:300]}")
                        except Exception:
                            pass
            except Exception:
                pass

        print("\n[*] Done. Closing browser.")
        browser.close()


if __name__ == "__main__":
    main()