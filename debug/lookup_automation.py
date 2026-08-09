"""
Structured data lookup utility built on Playwright's sync API.

Config (proxy + environment/viewport) now lives in env_config.py and is
shared with scrape_registry_generic.py. This file only has the retry
helper and the lookup routine itself.
"""

import time
import random
import logging

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from env_config import ProxyConfig, EnvironmentConfig, build_browser, build_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry / backoff helper
# ---------------------------------------------------------------------------
def goto_with_retry(
    page,
    url: str,
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    wait_until: str = "load",
    timeout_ms: int = 30_000,
):
    """Navigate with exponential backoff + jitter.

    Retries on timeouts and transient network/proxy errors (connection reset,
    proxy auth hiccups, DNS blips). Does not retry on programming errors.
    """
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # jitter
            logger.warning(
                "Navigation attempt %d/%d to %s failed (%s); retrying in %.1fs",
                attempt, max_attempts, url, exc.__class__.__name__, delay,
            )
            time.sleep(delay)
    raise RuntimeError(f"Failed to load {url} after {max_attempts} attempts") from last_exc


# ---------------------------------------------------------------------------
# Lookup routine
# ---------------------------------------------------------------------------
def run_lookup(query: str, url: str = "https://example.com/lookup") -> str:
    env_cfg = EnvironmentConfig()
    proxy_cfg = ProxyConfig()

    with sync_playwright() as p:
        browser = build_browser(p, env_cfg, proxy_cfg)
        context = build_context(browser, env_cfg)
        page = context.new_page()

        try:
            goto_with_retry(page, url)
            page.fill("#query", query)
            page.click("#search-button")
            page.wait_for_selector("#results")
            return page.inner_text("#results")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    result = run_lookup("example query")
    print(result)