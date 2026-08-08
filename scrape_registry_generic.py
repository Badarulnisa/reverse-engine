"""
Registry directory scraper (Playwright, sync API)

Iterates a list of search terms against a public business-registry / directory
search portal, submits each query, waits for results to render, and extracts
row text into a list of dicts. Includes randomized pacing and per-query
error handling (no-results vs timeout vs unexpected failure).

Before using this against a real site:
  - Check that site's Terms of Service and robots.txt for automated access.
  - Many registries expose a public bulk-data export or an API — prefer that
    over UI scraping when available; it's faster and more reliable for you too.
  - Keep concurrency at 1 and the delays below (or larger) to stay easy on
    the target server.

Fill in the CSS/XPath selectors in RegistryConfig for your target site —
they're placeholders below since every portal's markup differs.
"""

import csv
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import (
    sync_playwright,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)


class RateLimitDetected(Exception):
    """Raised when the page shows signs of CAPTCHA / block / rate-limiting."""


@dataclass
class RegistryConfig:
    """Site-specific configuration. Update these for your target portal."""

    base_url: str
    search_input_selector: str = "input[type='search']"
    submit_selector: Optional[str] = None  # None -> press Enter instead
    results_container_selector: str = "#results"
    result_row_selector: str = "#results .result-row"
    no_results_selector: Optional[str] = "#results .no-results"
    # Fields to pull from each row, mapped to a sub-selector relative to the row
    row_field_selectors: dict = field(default_factory=lambda: {
        "name": ".entity-name",
        "id": ".entity-id",
        "status": ".entity-status",
    })
    results_timeout_ms: int = 15000
    min_delay_s: float = 1.5
    max_delay_s: float = 4.0

    # Pagination (optional). Leave next_page_selector as None if the portal
    # doesn't paginate. max_pages guards against accidental infinite loops.
    next_page_selector: Optional[str] = None
    max_pages: int = 50

    # Session persistence: cookies/localStorage saved here between runs so
    # you don't have to re-establish a session (e.g. re-accept a terms
    # dialog) every time the script starts.
    storage_state_path: str = "storage_state.json"

    # Error artifacts: on unexpected failure, dump a screenshot + HTML here
    debug_dir: str = "debug_artifacts"

    # Text/selector signals that indicate a CAPTCHA or block page. Checked
    # after every search. Extend this list for your target site.
    block_signals: list = field(default_factory=lambda: [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "#challenge-running",       # Cloudflare
        "text=/rate limit/i",
        "text=/unusual traffic/i",
        "text=/access denied/i",
        "text=/verify you are human/i",
    ])
    checkpoint_path: str = "checkpoint.json"


def human_pause(cfg: RegistryConfig) -> None:
    """Randomized delay between actions."""
    time.sleep(random.uniform(cfg.min_delay_s, cfg.max_delay_s))


def check_for_block(page: Page, cfg: RegistryConfig) -> None:
    """Raise RateLimitDetected if the page shows a CAPTCHA/block signal."""
    for signal in cfg.block_signals:
        try:
            if page.locator(signal).count() > 0:
                raise RateLimitDetected(f"Block signal matched: {signal!r}")
        except RateLimitDetected:
            raise
        except Exception:
            # A malformed/unsupported selector shouldn't crash the run
            continue


def save_error_artifacts(page: Page, cfg: RegistryConfig, term: str, label: str) -> str:
    """
    Save a full-page screenshot + HTML dump to a timestamped debug folder.
    Returns the folder path. Never raises — a failure here shouldn't mask
    the original error.
    """
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        safe_term = "".join(c if c.isalnum() else "_" for c in str(term))
        folder = os.path.join(cfg.debug_dir, f"{ts}_{safe_term}_{label}")
        os.makedirs(folder, exist_ok=True)

        try:
            page.screenshot(path=os.path.join(folder, "screenshot.png"), full_page=True)
        except Exception as e:
            print(f"    [debug] screenshot failed: {e}")

        try:
            with open(os.path.join(folder, "page.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception as e:
            print(f"    [debug] html dump failed: {e}")

        print(f"    [debug] artifacts saved to {folder}")
        return folder
    except Exception as e:
        print(f"    [debug] failed to save error artifacts: {e}")
        return ""


def load_checkpoint(path: str) -> dict:
    """Returns {'completed_terms': [...], 'results': [...]}"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed_terms": [], "results": []}


def save_checkpoint(path: str, completed_terms: list, results: list) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"completed_terms": completed_terms, "results": results}, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)  # atomic on POSIX/Windows


def get_storage_state_arg(path: str) -> Optional[str]:
    """Returns the path to pass as new_context(storage_state=...) if a
    saved session exists, else None (fresh context)."""
    return path if os.path.exists(path) else None


def save_storage_state(context, path: str) -> None:
    """Persist cookies/localStorage so the next run can reuse the session."""
    try:
        context.storage_state(path=path)
        print(f"[i] Session state saved to {path}")
    except Exception as e:
        print(f"[i] Failed to save session state: {e}")


def run_search(page: Page, cfg: RegistryConfig, term: str) -> dict:
    """
    Submit a single search term and extract results.

    Returns a dict: {"term": term, "status": "ok"/"no_results"/"timeout"/"error",
                      "rows": [...], "error": Optional[str]}
    """
    outcome = {"term": term, "status": "ok", "rows": [], "error": None}

    try:
        search_box = page.locator(cfg.search_input_selector)
        search_box.wait_for(state="visible", timeout=cfg.results_timeout_ms)
        search_box.click()
        search_box.fill("")  # clear
        search_box.type(term, delay=random.uniform(30, 90))  # human-ish typing

        human_pause(cfg)

        if cfg.submit_selector:
            page.locator(cfg.submit_selector).click()
        else:
            search_box.press("Enter")

        # Check for CAPTCHA/block pages before assuming a normal timeout/no-results
        check_for_block(page, cfg)

        # Wait for either the results container to update or a no-results marker
        try:
            page.wait_for_selector(
                cfg.results_container_selector,
                timeout=cfg.results_timeout_ms,
                state="visible",
            )
        except PlaywrightTimeoutError:
            outcome["status"] = "timeout"
            outcome["error"] = "Results container did not appear in time"
            return outcome

        # Check explicit no-results marker first, if the site provides one
        if cfg.no_results_selector and page.locator(cfg.no_results_selector).count() > 0:
            outcome["status"] = "no_results"
            return outcome

        rows = page.locator(cfg.result_row_selector)
        try:
            rows.first.wait_for(state="visible", timeout=cfg.results_timeout_ms)
        except PlaywrightTimeoutError:
            outcome["status"] = "no_results"
            return outcome

        # --- Pagination loop: keep parsing + clicking "next" until there is
        # no next page, no new rows appear, or max_pages is hit. ---
        pages_seen = 0
        while True:
            pages_seen += 1
            rows = page.locator(cfg.result_row_selector)
            count = rows.count()
            for i in range(count):
                row = rows.nth(i)
                record = {}
                for field_name, sub_selector in cfg.row_field_selectors.items():
                    try:
                        record[field_name] = row.locator(sub_selector).inner_text().strip()
                    except Exception:
                        record[field_name] = None
                outcome["rows"].append(record)

            check_for_block(page, cfg)  # re-check after parsing each page

            if not cfg.next_page_selector:
                break  # site has no pagination configured
            if pages_seen >= cfg.max_pages:
                print(f"    [pagination] hit max_pages={cfg.max_pages}, stopping")
                break

            next_btn = page.locator(cfg.next_page_selector)
            if next_btn.count() == 0:
                break  # no next-page control present -> last page
            try:
                if not next_btn.first.is_enabled():
                    break  # disabled "next" button -> last page
            except Exception:
                break

            first_row_before = rows.first.inner_text() if count > 0 else None

            human_pause(cfg)
            next_btn.first.click()

            try:
                # Wait for the row set to actually change, not just re-render
                if first_row_before is not None:
                    page.wait_for_function(
                        """([sel, prevText]) => {
                            const el = document.querySelector(sel);
                            return el && el.innerText.trim() !== prevText.trim();
                        }""",
                        arg=[cfg.result_row_selector, first_row_before],
                        timeout=cfg.results_timeout_ms,
                    )
                else:
                    page.wait_for_selector(cfg.result_row_selector, timeout=cfg.results_timeout_ms)
            except PlaywrightTimeoutError:
                # Next page didn't load new content in time; stop paginating
                # for this term rather than failing the whole search.
                print("    [pagination] next page did not load new rows in time; stopping")
                break

    except RateLimitDetected:
        raise  # propagate; caller decides whether to pause/abort
    except PlaywrightTimeoutError as e:
        outcome["status"] = "timeout"
        outcome["error"] = str(e)
        save_error_artifacts(page, cfg, term, "timeout")
    except Exception as e:
        outcome["status"] = "error"
        outcome["error"] = str(e)
        save_error_artifacts(page, cfg, term, "error")

    return outcome


def scrape_terms(
    cfg: RegistryConfig,
    terms: list,
    headless: bool = True,
    resume: bool = True,
    max_block_retries: int = 3,
    block_backoff_s: float = 60.0,
) -> list:
    """
    Run the full workflow across a list of search terms.

    Resumable: progress is written to cfg.checkpoint_path after every term.
    If resume=True and a checkpoint exists, already-completed terms are
    skipped, so a killed/crashed run can just be restarted.

    On CAPTCHA/rate-limit detection, backs off and retries the SAME term
    up to max_block_retries times (with growing delay) before giving up
    and recording it as a 'blocked' outcome — the run then continues to
    the next term rather than crashing entirely.
    """
    checkpoint = load_checkpoint(cfg.checkpoint_path) if resume else {"completed_terms": [], "results": []}
    completed = set(checkpoint["completed_terms"])
    results = checkpoint["results"]

    remaining = [t for t in terms if t not in completed]
    if resume and completed:
        print(f"[i] Resuming: {len(completed)} terms already done, {len(remaining)} remaining")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)

        storage_state = get_storage_state_arg(cfg.storage_state_path)
        if storage_state:
            print(f"[i] Reusing saved session from {storage_state}")
        context = browser.new_context(storage_state=storage_state)

        page = context.new_page()
        try:
            page.goto(cfg.base_url, wait_until="domcontentloaded")
        except Exception as e:
            save_error_artifacts(page, cfg, "initial_load", "error")
            browser.close()
            raise

        for term in remaining:
            print(f"[+] Searching: {term!r}")

            outcome = None
            for attempt in range(1, max_block_retries + 1):
                try:
                    outcome = run_search(page, cfg, term)
                    break
                except RateLimitDetected as e:
                    wait_s = block_backoff_s * attempt
                    print(f"    -> possible block/CAPTCHA ({e}); "
                          f"backing off {wait_s:.0f}s (attempt {attempt}/{max_block_retries})")
                    time.sleep(wait_s)
            else:
                outcome = {"term": term, "status": "blocked", "rows": [],
                           "error": "Exceeded max retries after repeated block/CAPTCHA detection"}
                print("    -> giving up on this term after repeated blocks; "
                      "consider stopping the run and checking manually")

            results.append(outcome)
            completed.add(term)

            status = outcome["status"]
            if status == "ok":
                print(f"    -> {len(outcome['rows'])} rows")
            elif status == "no_results":
                print("    -> no results")
            elif status == "timeout":
                print(f"    -> timed out: {outcome['error']}")
            elif status == "blocked":
                pass  # already printed above
            else:
                print(f"    -> error: {outcome['error']}")

            save_checkpoint(cfg.checkpoint_path, sorted(completed), results)
            human_pause(cfg)

        save_storage_state(context, cfg.storage_state_path)
        browser.close()

    return results


def save_results(results: list, json_path: str = "results.json", csv_path: str = "results.csv") -> None:
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    all_fields = set()
    for r in results:
        for row in r["rows"]:
            all_fields.update(row.keys())
    all_fields = ["search_term"] + sorted(all_fields)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields)
        writer.writeheader()
        for r in results:
            for row in r["rows"]:
                writer.writerow({"search_term": r["term"], **row})


if __name__ == "__main__":
    import string

    config = RegistryConfig(
        base_url="https://example-registry.gov/search",  # <-- set real URL
    )

    search_terms = list(string.ascii_lowercase)  # a..z

    all_results = scrape_terms(config, search_terms, headless=True)
    save_results(all_results)

    ok = sum(1 for r in all_results if r["status"] == "ok")
    no_res = sum(1 for r in all_results if r["status"] == "no_results")
    timeouts = sum(1 for r in all_results if r["status"] == "timeout")
    blocked = sum(1 for r in all_results if r["status"] == "blocked")
    errors = sum(1 for r in all_results if r["status"] == "error")
    print(f"\nDone. ok={ok} no_results={no_res} timeouts={timeouts} blocked={blocked} errors={errors}")
    if blocked:
        print("Some terms were blocked repeatedly — re-run the script (resume=True, the "
              "default) to retry only those once you've confirmed it's safe to continue.")