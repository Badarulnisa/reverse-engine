import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Optional, Union

from playwright.sync_api import (
    sync_playwright,
    Page,
    FrameLocator,
    TimeoutError as PlaywrightTimeoutError,
)
from env_config import ProxyConfig, EnvironmentConfig, build_browser, build_context, goto_with_retry

class RateLimitDetected(Exception):
    pass

@dataclass
class RegistryConfig:
    base_url: str
    iframe_selector: str = "iframe[src*='salesforce-sites.com']"
    search_input_selector: str = "input[type='search']"
    submit_selector: Optional[str] = None
    results_container_selector: str = "#results"
    result_row_selector: str = "#results .result-row"
    no_results_selector: Optional[str] = "#results .no-results"
    row_field_selectors: dict = field(default_factory=lambda: {
        "name": ".entity-name",
        "id": ".entity-id",
        "status": ".entity-status",
    })
    results_timeout_ms: int = 20000
    min_delay_s: float = 1.5
    max_delay_s: float = 4.0
    next_page_selector: Optional[str] = None
    max_pages: int = 50
    storage_state_path: str = "storage_state.json"
    checkpoint_path: str = "checkpoint.json"
    interactive_fallback: bool = True
    max_captcha_attempts: int = 3

    block_signals: list = field(default_factory=lambda: [
        "#challenge-running",
        "text=/verify you are human/i",
        "text=/checking your browser/i",
    ])

Scope = Union[Page, FrameLocator]

def get_dmcc_frame(page: Page, cfg: "RegistryConfig") -> FrameLocator:
    return page.frame_locator(cfg.iframe_selector)

def human_typing(scope: Scope, selector: str, text: str, page: Page):
    print(f"    [debug] Typing {text!r} into {selector!r}...")
    box = scope.locator(selector)
    box.click()
    box.fill("")
    for char in text:
        page.keyboard.type(char, delay=random.uniform(50, 150))
    time.sleep(random.uniform(0.5, 1.2))
    print(f"    [debug] Finished typing into {selector!r}.")

def check_for_block(scope: Scope, cfg: RegistryConfig) -> None:
    for signal in cfg.block_signals:
        try:
            if scope.locator(signal).count() > 0:
                raise RateLimitDetected(f"Block signal matched: {signal}")
        except RateLimitDetected:
            raise
        except Exception:
            continue

def captcha_needs_solving(frame: FrameLocator) -> bool:
    recaptcha_iframe = frame.locator("iframe[src*='recaptcha']").first
    if recaptcha_iframe.count() == 0:
        return False
    try:
        checkbox_frame = frame.frame_locator("iframe[src*='recaptcha']").first
        checked = checkbox_frame.locator("#recaptcha-anchor[aria-checked='true']").count() > 0
        return not checked
    except Exception:
        return True

def handle_manual_captcha(context, cfg: RegistryConfig, env_cfg: EnvironmentConfig) -> bool:
    print("\n[!] WARNING: reCAPTCHA needs manual solving!")
    if env_cfg.headless:
        print("[!] Script is running in HEADLESS mode. Cannot manually solve.")
        return False

    print("[>>>] Please solve the CAPTCHA in the browser window.")
    input("[>>>] Press ENTER here in the terminal once you have solved it to continue...")
    context.storage_state(path=cfg.storage_state_path)
    print(f"[i] Session saved to {cfg.storage_state_path}.")
    time.sleep(2)
    return True

def save_checkpoint(path: str, completed_terms: list, results: list) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"completed_terms": completed_terms, "results": results}, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)

def run_search(page: Page, cfg: RegistryConfig, term: str, context, env_cfg: EnvironmentConfig) -> dict:
    outcome = {"term": term, "status": "ok", "rows": [], "error": None}

    frame = get_dmcc_frame(page, cfg)
    check_for_block(frame, cfg)

    frame.locator(cfg.search_input_selector).first.wait_for(state="visible", timeout=25000)
    human_typing(frame, cfg.search_input_selector, term, page)

    if captcha_needs_solving(frame):
        print(f"    -> reCAPTCHA not yet solved for term {term!r}.")
        solved = handle_manual_captcha(context, cfg, env_cfg)
        if not solved:
            outcome["status"] = "blocked"
            outcome["error"] = "Headless block hit; cannot manually solve."
            return outcome

    if cfg.submit_selector:
        frame.locator(cfg.submit_selector).click()
    else:
        frame.locator(cfg.search_input_selector).press("Enter")

    time.sleep(random.uniform(1.0, 2.5))
    check_for_block(frame, cfg)

    frame.locator(cfg.results_container_selector).first.wait_for(
        state="visible", timeout=cfg.results_timeout_ms
    )

    # ============================================================
    # ONE-TIME DEBUG BLOCK: dump real structure of result cards.
    # Remove this block once we've identified the correct selector.
    # ============================================================
    print("\n[ROW-DEBUG] ==== Inspecting possible result containers ====")
    debug_candidates = [
        ".slds-card",
        "[class*='card']",
        "[class*='result']",
        "[class*='Result']",
        "table tr",
        ".slds-table tr",
    ]
    for sel in debug_candidates:
        loc = frame.locator(sel)
        n = loc.count()
        print(f"[ROW-DEBUG] selector={sel!r} count={n}")
        if n > 0:
            for i in range(min(3, n)):
                try:
                    html = loc.nth(i).evaluate("el => el.outerHTML")
                    print(f"[ROW-DEBUG]   [{i}] {html[:400]}")
                except Exception as e:
                    print(f"[ROW-DEBUG]   [{i}] error: {e}")
    print("[ROW-DEBUG] ==== End inspection ====\n")

    outcome["status"] = "debug_stop"
    outcome["error"] = "Stopped after debug dump (remove debug block to resume normal scraping)"
    return outcome
    # ============================================================
    # END DEBUG BLOCK
    # ============================================================

def scrape_terms(cfg: RegistryConfig, terms: list, resume: bool = True) -> list:
    checkpoint = {"completed_terms": [], "results": []}
    if resume and os.path.exists(cfg.checkpoint_path):
        with open(cfg.checkpoint_path, "r", encoding="utf-8") as f:
            checkpoint = json.load(f)

    completed = set(checkpoint["completed_terms"])
    results = checkpoint["results"]
    remaining = [t for t in terms if t not in completed]

    env_cfg = EnvironmentConfig()
    proxy_cfg = ProxyConfig()

    browser = None
    context = None
    with sync_playwright() as p:
        try:
            browser = build_browser(p, env_cfg, proxy_cfg)
            storage_state = cfg.storage_state_path if os.path.exists(cfg.storage_state_path) else None
            context = build_context(browser, env_cfg, storage_state=storage_state)

            page = context.new_page()
            print("[debug] Navigating to base_url...")
            goto_with_retry(page, cfg.base_url)
            print("[debug] Navigation done. Waiting 8s for iframe...")
            time.sleep(8)

            for term in remaining:
                if page.is_closed():
                    print("[!] Browser page was closed unexpectedly. Recreating...")
                    page = context.new_page()
                    goto_with_retry(page, cfg.base_url)
                    time.sleep(8)

                print(f"[+] Searching: {term!r}")
                outcome = None

                for attempt in range(1, cfg.max_captcha_attempts + 1):
                    try:
                        outcome = run_search(page, cfg, term, context, env_cfg)
                        if outcome["status"] != "blocked":
                            break
                        print(f"    -> Blocked on attempt {attempt}/{cfg.max_captcha_attempts}.")
                    except RateLimitDetected as e:
                        print(f"    -> Hard block signal on attempt {attempt}/{cfg.max_captcha_attempts}: {e}")
                        if cfg.interactive_fallback:
                            solved = handle_manual_captcha(context, cfg, env_cfg)
                            if not solved:
                                outcome = {"term": term, "status": "blocked", "rows": [], "error": "Headless block hit; cannot manually solve."}
                                break
                        else:
                            outcome = {"term": term, "status": "blocked", "rows": [], "error": "Headless block hit."}
                            break
                    except Exception as e:
                        outcome = {"term": term, "status": "error", "rows": [], "error": str(e)}
                        break
                else:
                    outcome = {"term": term, "status": "blocked", "rows": [], "error": f"Still blocked after {cfg.max_captcha_attempts} attempts."}

                results.append(outcome)

                if outcome["status"] in ("ok", "no_results"):
                    completed.add(term)

                print(f"    -> {outcome['status']}: {outcome.get('error', '')}")

                save_checkpoint(cfg.checkpoint_path, sorted(completed), results)
                time.sleep(random.uniform(cfg.min_delay_s, cfg.max_delay_s))

                # Debug mode: only run one term, then stop the whole script.
                if outcome["status"] == "debug_stop":
                    print("\n[debug] Debug dump complete. Exiting early.")
                    return results

        finally:
            if context is not None:
                try:
                    context.storage_state(path=cfg.storage_state_path)
                except Exception:
                    pass
            if browser is not None:
                browser.close()

    return results

if __name__ == "__main__":
    config = RegistryConfig(
        base_url="https://dmcc.ae/public-register",
        iframe_selector="iframe[src*='salesforce-sites.com']",
        search_input_selector="#customerName",
        submit_selector="button:has-text('Search')",
        results_container_selector="table, .slds-table",
        result_row_selector="tbody tr, .slds-table tr",
        row_field_selectors={
            "name": "td:nth-child(1)",
            "id": "td:nth-child(2)",
            "status": "td:nth-child(3)",
        },
        results_timeout_ms=20000,
        interactive_fallback=True,
    )
    search_terms = ["A"]  # just one term for debugging

    scrape_terms(config, search_terms)