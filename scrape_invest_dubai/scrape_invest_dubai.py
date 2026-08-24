"""
Scraper for app.invest.dubai.ae/search-license (Dubai DET unified license search).

ARCHITECTURE NOTE (read this first):
Unlike the DMCC scraper, we do NOT scrape rendered DOM table rows. This site is a
Nuxt.js SPA that calls a clean JSON API: POST /api/license-search/search
The search request body includes a "token" field that is an hCaptcha proof-of-work
token ("hsw" type), minted client-side by hCaptcha's own JS when a real search is
triggered in the browser. We cannot forge this token ourselves in a raw HTTP client
(that's why a pure `requests` approach, like scrape_tor_all.py uses for DMCC, won't
work here).

Instead: we drive a real Playwright browser through the actual search UI (so the
page's own JS mints a valid token for us), and we intercept the resulting network
RESPONSE via page.on("response") to pull out the clean JSON -- we never touch the
token directly at all.

TODO BEFORE RUNNING:
The three CSS selectors below (SEARCH_INPUT_SELECTOR, SEARCH_SUBMIT_SELECTOR,
NEXT_PAGE_SELECTOR) are placeholders. To fill them in:
  1. Set HEADLESS=false in .env.local
  2. Run this script once -- it will pause after page load
  3. Right-click the search box in the opened browser -> Inspect
  4. Copy the actual selector (id, data-testid, or a stable class) and paste below
  5. Repeat for the search button and the "next page" control
"""

import itertools
import json
import logging
import os
import string
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import sync_playwright, Page, Response

from env_config import ProxyConfig, EnvironmentConfig, build_browser, build_context, goto_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("InvestDubaiScraper")

# ============================================================
# TODO: replace these placeholder selectors -- see docstring above
# ============================================================
SEARCH_INPUT_SELECTOR = "input[type='text']"        # PLACEHOLDER -- verify
SEARCH_SUBMIT_SELECTOR = "button[type='submit']"     # PLACEHOLDER -- verify
NEXT_PAGE_SELECTOR = "button:has-text('Next')"        # PLACEHOLDER -- verify
# ============================================================

SEARCH_API_PATH = "/api/license-search/search"


@dataclass
class InvestDubaiConfig:
    base_url: str = "https://app.invest.dubai.ae/search-license"
    storage_state_path: str = "invest_dubai_storage_state.json"
    checkpoint_path: str = "invest_dubai_checkpoint.json"
    output_path: str = "output_schemas/invest_dubai_licenses.jsonl"
    min_delay_s: float = 2.0
    max_delay_s: float = 4.5
    max_pages_per_term: int = 50          # safety cap; API pageSize is 10/page
    response_wait_timeout_ms: int = 20000

    # --- Term generation ---
    min_term_length: int = 3              # site rejects searches under 3 chars
    cap_threshold: int = 500              # if a term returns >= this many total
                                           # results, treat it as "capped" and
                                           # split into 4-letter children


def generate_base_terms(min_length: int) -> list:
    """
    Generates all lowercase letter combinations of exactly min_length chars.
    For min_length=3 this is 26^3 = 17,576 terms (aaa, aab, ... zzz).

    Why start at the minimum rather than length-1 like DMCC did: the site
    rejects searches under 3 characters, so there's no "a" -> split into
    "aa","ab",.. path available here. We must start at the floor length.
    """
    letters = string.ascii_lowercase
    return ["".join(combo) for combo in itertools.product(letters, repeat=min_length)]


def expand_term(term: str) -> list:
    """
    Splits a capped term into 26 children one character longer, e.g.
    "abc" -> "abca", "abcb", ..., "abcz".
    Used when a base term returns too many results to fetch exhaustively.
    """
    letters = string.ascii_lowercase
    return [term + ch for ch in letters]


def save_checkpoint(path: str, completed_terms: list, pending_queue: list) -> None:
    """
    Persists BOTH completed terms and the current pending queue.
    The queue must be saved too, not just `completed` -- otherwise any
    dynamically-generated child terms (from a capped-term split) that
    haven't been processed yet would be silently lost if the script
    crashes or is stopped mid-run, since they don't exist anywhere in
    the original base term list.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(
            {"completed_terms": completed_terms, "pending_queue": pending_queue},
            f, indent=2, ensure_ascii=False
        )
    os.replace(tmp_path, path)


def load_checkpoint(path: str) -> tuple:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("completed_terms", [])), data.get("pending_queue", None)
    return set(), None


def append_records(output_path: str, licenses: list) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as f:
        for lic in licenses:
            f.write(json.dumps(lic, ensure_ascii=False) + "\n")


class SearchResponseCapture:
    """
    Attached via page.on("response"). Captures the JSON body of any
    /api/license-search/search response as it flies by, so we never
    have to touch the hCaptcha token ourselves.
    """
    def __init__(self):
        self.latest_payload: Optional[dict] = None

    def handle(self, response: Response) -> None:
        if SEARCH_API_PATH not in response.url:
            return
        try:
            if response.status != 200:
                logger.warning("Search API returned status %d for %s", response.status, response.url)
                return
            body = response.json()
            self.latest_payload = body
        except Exception as e:
            logger.warning("Failed to parse search API response as JSON: %s", e)


def wait_for_response(capture: SearchResponseCapture, timeout_ms: int) -> Optional[dict]:
    """Poll capture.latest_payload until it's set (page.on fires async)."""
    deadline = time.time() + (timeout_ms / 1000)
    capture.latest_payload = None
    while time.time() < deadline:
        if capture.latest_payload is not None:
            payload = capture.latest_payload
            capture.latest_payload = None
            return payload
        time.sleep(0.1)
    return None


def search_term(page: Page, capture: SearchResponseCapture, cfg: InvestDubaiConfig, term: str) -> tuple:
    """
    Runs one search term through the real UI, paginating through all result
    pages by clicking the actual "next page" control (so each page mints its
    own valid hCaptcha token naturally).

    Returns (licenses: list, hit_cap: bool). hit_cap is True if we stopped
    because we ran into max_pages_per_term while still receiving full pages
    -- meaning there's very likely more data we didn't fetch, and the term
    should be split into 4-letter children (see expand_term()).
    """
    all_licenses = []
    hit_cap = False

    box = page.locator(SEARCH_INPUT_SELECTOR).first
    box.click()
    box.fill("")
    box.type(term, delay=80)
    time.sleep(0.5)

    if page.locator(SEARCH_SUBMIT_SELECTOR).count() > 0:
        page.locator(SEARCH_SUBMIT_SELECTOR).first.click()
    else:
        box.press("Enter")

    PAGE_SIZE = 10  # matches pageSize in the observed API payload

    payload = wait_for_response(capture, cfg.response_wait_timeout_ms)
    if payload is None:
        logger.warning("No search response captured for term=%r (first page).", term)
        return all_licenses, False

    if payload.get("code") != 0:
        logger.warning("API returned non-success code for term=%r: %s", term, payload.get("desc"))
        return all_licenses, False

    licenses = payload.get("data", {}).get("licenses", [])
    all_licenses.extend(licenses)
    logger.info("term=%r page=1: +%d licenses", term, len(licenses))

    page_num = 1
    while len(licenses) == PAGE_SIZE and page_num < cfg.max_pages_per_term:
        next_btn = page.locator(NEXT_PAGE_SELECTOR).first
        if next_btn.count() == 0 or not next_btn.is_enabled():
            break

        next_btn.click()
        payload = wait_for_response(capture, cfg.response_wait_timeout_ms)
        if payload is None or payload.get("code") != 0:
            logger.warning("term=%r page=%d: no valid response, stopping pagination.", term, page_num + 1)
            break

        licenses = payload.get("data", {}).get("licenses", [])
        if not licenses:
            break

        all_licenses.extend(licenses)
        page_num += 1
        logger.info("term=%r page=%d: +%d licenses (running total: %d)", term, page_num, len(licenses), len(all_licenses))

        time.sleep(cfg.min_delay_s)

    # If we stopped only because we hit max_pages_per_term while the last
    # page was still full (PAGE_SIZE items), there's likely more data beyond
    # what we fetched -- flag for splitting into 4-letter children.
    if page_num >= cfg.max_pages_per_term and len(licenses) == PAGE_SIZE:
        hit_cap = True
    # Also flag purely on total volume, independent of hitting the page cap --
    # matches cfg.cap_threshold as a belt-and-suspenders check.
    if len(all_licenses) >= cfg.cap_threshold:
        hit_cap = True

    return all_licenses, hit_cap


def run(cfg: InvestDubaiConfig, search_terms: list, resume: bool = True):
    completed, saved_queue = load_checkpoint(cfg.checkpoint_path) if resume else (set(), None)

    # Dynamic work queue -- capped terms get split and their children
    # appended here mid-run, same pattern as scrape_tor_all.py's
    # terms_needing_split, just done inline rather than as a second pass.
    if saved_queue is not None:
        logger.info("Resuming from saved queue (%d pending terms).", len(saved_queue))
        queue = [t for t in saved_queue if t not in completed]
    else:
        queue = [t for t in search_terms if t not in completed]

    if not queue:
        logger.info("All terms already completed per checkpoint. Nothing to do.")
        return

    env_cfg = EnvironmentConfig()
    proxy_cfg = ProxyConfig()

    with sync_playwright() as p:
        browser = build_browser(p, env_cfg, proxy_cfg)
        storage_state = cfg.storage_state_path if os.path.exists(cfg.storage_state_path) else None
        context = build_context(browser, env_cfg, storage_state=storage_state)

        page = context.new_page()
        capture = SearchResponseCapture()
        page.on("response", capture.handle)

        logger.info("Navigating to %s", cfg.base_url)
        goto_with_retry(page, cfg.base_url)
        time.sleep(3)

        failure_counts = {}
        MAX_FAILURES_PER_TERM = 3

        try:
            while queue:
                term = queue.pop(0)
                if term in completed:
                    continue

                logger.info("=== Searching term: %r (queue depth: %d) ===", term, len(queue))
                try:
                    licenses, hit_cap = search_term(page, capture, cfg, term)
                    if licenses:
                        append_records(cfg.output_path, licenses)

                    if hit_cap:
                        children = expand_term(term)
                        logger.warning(
                            "term=%r hit cap (%d results) -- splitting into %d children: %r..%r",
                            term, len(licenses), len(children), children[0], children[-1]
                        )
                        # Parent term is done being searched directly, but we
                        # don't mark it "completed" in the sense of final --
                        # its data came from the children instead. We record
                        # it as completed so we don't re-search the parent
                        # itself on resume, and queue the children.
                        queue.extend(c for c in children if c not in completed)
                    completed.add(term)
                    save_checkpoint(cfg.checkpoint_path, sorted(completed), queue)
                except Exception as e:
                    logger.exception("Term %r failed: %s", term, e)
                    failure_counts[term] = failure_counts.get(term, 0) + 1
                    if failure_counts[term] < MAX_FAILURES_PER_TERM:
                        # Retry later, not immediately -- push to back of queue
                        # so other terms make progress in the meantime.
                        queue.append(term)
                    else:
                        logger.error(
                            "term=%r failed %d times, giving up for this run "
                            "(NOT marked completed -- will retry on next full run).",
                            term, MAX_FAILURES_PER_TERM
                        )
                    save_checkpoint(cfg.checkpoint_path, sorted(completed), queue)

                time.sleep(cfg.min_delay_s)

        finally:
            try:
                context.storage_state(path=cfg.storage_state_path)
            except Exception:
                pass
            browser.close()

    logger.info("Run complete. Output appended to %s", cfg.output_path)


if __name__ == "__main__":
    config = InvestDubaiConfig()

    # --- STEP 1: confirm selectors work with a single known-good term first.
    # Comment this block out (and uncomment STEP 2) once "abu" search results
    # are captured correctly end-to-end.
    search_terms = ["abu"]

    # --- STEP 2: full sweep, once selectors + single-term run are verified.
    # 26^3 = 17,576 base terms; capped ones auto-split into 4-letter children.
    # This WILL take a long time (hours) given min_delay_s between requests --
    # that's intentional, to stay well under any rate-limiting radar.
    # search_terms = generate_base_terms(config.min_term_length)

    run(config, search_terms)