"""
browser_dul_discovery.py

Continues DUL discovery now that the free GetDulDetails endpoint is
dead. Drives a REAL (non-headless, channel="chrome") browser to the
search page with the DUL pre-filled via URL params, which triggers the
page's own search + hCaptcha flow automatically. Instead of scraping
the rendered DOM (unreliable, changes with app updates), this
intercepts the actual dul/Search network response directly - we know
its exact JSON shape from a real capture:

    Not found: {"license":null,"status":null,"message":null}
    Found:     {"license":[{...full record...}],"status":null,"message":null}

If hCaptcha shows a visible challenge instead of passing invisibly, no
network response for dul/Search will arrive within the timeout - that
candidate is logged to the skip file and we move on immediately, per
your team's decision not to solve challenges manually or via a
paid service.

Requires: playwright, real Google Chrome installed (channel="chrome").
    pip install playwright
    playwright install chrome   (only if channel="chrome" fails to launch)

Usage:
    python browser_dul_discovery.py --candidates candidates.txt
    python browser_dul_discovery.py --candidates candidates.txt --headed   (recommended for the first run, to watch it work)
    python browser_dul_discovery.py --candidates candidates.txt --tor
"""

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime

import pandas as pd
from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from visitdubai_bruteforce import parse_visitdubai_dul  # noqa: E402

SEARCH_URL = "https://www.investindubai.gov.ae/en/dubai-business-directory-search"
API_MATCH = "**/api/dul/Search*"

PROFILE_DIR = "chrome_profile"  # persists across runs - real cookies/history
                                  # accumulate here over time, which should
                                  # look more like a genuine returning
                                  # visitor to hCaptcha's risk engine than a
                                  # fresh throwaway profile every run.
HITS_FILE = "browser_discovery_hits.jsonl"          # raw safety log, one line per hit
SKIPPED_FILE = "browser_discovery_skipped.txt"      # visible-challenge, retry later
CONFIRMED_EMPTY_FILE = "browser_discovery_empty.txt"  # genuinely no such DUL
STATE_FILE = "browser_discovery_state.json"
MASTER_JSON = "browser_discovery_master.json"       # flattened, same shape as shard output
MASTER_XLSX = "browser_discovery_master.xlsx"       # flattened, same shape as shard output


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"done": []}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def append_line(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def save_master_files(records):
    """Flatten every hit into the same column shape as the shard/
    recovery outputs and write both a JSON and an xlsx master file,
    atomically (temp file + rename) so a crash mid-write can't corrupt
    the existing file."""
    if not records:
        return
    df = pd.DataFrame(records)

    tmp_json = MASTER_JSON + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    os.replace(tmp_json, MASTER_JSON)

    tmp_xlsx = MASTER_XLSX.rsplit(".", 1)[0] + ".tmp.xlsx"
    df.to_excel(tmp_xlsx, index=False)
    os.replace(tmp_xlsx, MASTER_XLSX)


async def check_one_dul(page, dul, timeout_ms=12000, is_first=False):
    """Types the DUL into the real search box and clicks Search, staying
    on the same page/session throughout the run instead of a fresh
    navigation per candidate - a full page reload for every single
    search is a very repetitive, easily-fingerprinted pattern; typing
    into a persistent page looks closer to real usage. Waits for the
    real dul/Search network response and reads it directly.

    Returns ('hit', record_dict) | ('empty', None) | ('challenged', None)
    | ('error', message)
    """
    if is_first:
        try:
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1.5, 3.0))
        except Exception as e:
            return "error", f"initial navigation failed: {e}"

    response_holder = {}

    def on_response(response):
        if "/api/dul/Search" in response.url and response.request.method == "POST":
            response_holder["response"] = response

    page.on("response", on_response)

    try:
        # A previous search's results modal may still be open, covering
        # the search input (confirmed via a real run: "subtree intercepts
        # pointer events" errors after a successful hit). Dismiss it
        # first - Escape closes most modals; a close-button click is a
        # fallback if Escape alone isn't enough for this one.
        await page.keyboard.press("Escape")
        await asyncio.sleep(random.uniform(0.3, 0.6))
        try:
            close_btn = page.locator(
                'button[aria-label*="close" i], [class*="close"]:visible'
            ).first
            if await close_btn.is_visible(timeout=1000):
                await close_btn.click(timeout=2000)
                await asyncio.sleep(random.uniform(0.3, 0.6))
        except Exception:
            pass  # no modal/close button present - fine, nothing to close

        search_input = page.locator('#dul-search-input')
        await search_input.click(timeout=8000)
        # Clear whatever's currently in the box (select-all + type over it)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await asyncio.sleep(random.uniform(0.15, 0.35))
        # Type character by character with small random delays - closer
        # to real typing than instantly filling the whole value at once.
        for ch in dul:
            await page.keyboard.type(ch)
            await asyncio.sleep(random.uniform(0.05, 0.18))
        await asyncio.sleep(random.uniform(0.3, 0.7))
        # Press Enter to submit rather than hunting for a specific
        # button - simpler, and real users commonly search this way too.
        await page.keyboard.press("Enter")
    except Exception as e:
        page.remove_listener("response", on_response)
        return "error", f"could not interact with search UI: {e}"

    waited = 0
    interval = 300
    while "response" not in response_holder and waited < timeout_ms:
        await asyncio.sleep(interval / 1000)
        waited += interval

    page.remove_listener("response", on_response)

    if "response" not in response_holder:
        return "challenged", None

    resp = response_holder["response"]
    try:
        data = await resp.json()
    except Exception as e:
        return "error", f"could not parse response JSON: {e}"

    license_data = data.get("license")
    if license_data is None:
        return "empty", None
    if isinstance(license_data, list) and len(license_data) > 0:
        return "hit", license_data[0]
    return "empty", None


async def run(candidates, use_tor, headless):
    state = load_state()
    done = set(state["done"])
    remaining = [c for c in candidates if c not in done]
    print(f"[*] {len(remaining)} candidates remaining ({len(done)} already processed).")

    async with async_playwright() as p:
        launch_kwargs = dict(
            headless=headless,
            channel="chrome",
            user_data_dir=os.path.abspath(PROFILE_DIR),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        if use_tor:
            launch_kwargs["proxy"] = {"server": "socks5://127.0.0.1:9050"}
        try:
            # launch_persistent_context (not launch + new_context) so the
            # profile - cookies, local storage, hCaptcha's own tracking
            # data - persists on disk across runs in PROFILE_DIR, instead
            # of starting completely fresh every time. A real returning
            # visitor's session history is likely a meaningful trust
            # signal we were throwing away before.
            context = await p.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as e:
            print(f"[!] Could not launch real Chrome ({e}). Run "
                  "'playwright install chrome' and try again.")
            return

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.navigator.chrome = { runtime: {} };
        """)

        page = context.pages[0] if context.pages else await context.new_page()

        hits = empty = challenged = errors = 0
        flattened_records = []
        if os.path.exists(MASTER_JSON):
            try:
                with open(MASTER_JSON, encoding="utf-8") as f:
                    flattened_records = json.load(f)
                print(f"[*] Resumed {len(flattened_records)} previously flattened records from {MASTER_JSON}.")
            except Exception:
                pass

        for i, dul in enumerate(remaining):
            try:
                result, data = await check_one_dul(page, dul, is_first=(i == 0))
            except Exception as e:
                result, data = "error", str(e)

            if result == "hit":
                append_line(HITS_FILE, json.dumps({
                    "dul": dul, "record": data, "ts": datetime.now().isoformat()
                }))
                flattened = parse_visitdubai_dul(data, identifier=dul)
                if flattened:
                    flattened_records.append(flattened)
                    if len(flattened_records) % 10 == 0:
                        save_master_files(flattened_records)
                hits += 1
            elif result == "empty":
                append_line(CONFIRMED_EMPTY_FILE, dul)
                empty += 1
            elif result == "challenged":
                append_line(SKIPPED_FILE, dul)
                challenged += 1
                # Deliberately NOT marked done - a challenge means we
                # couldn't verify this DUL at all, not a real outcome.
                # It must stay eligible for a future --retry-skipped pass
                # (a different session/IP/time may pass invisibly where
                # this attempt didn't).
                print(f"\r[Discovery] hits={hits} empty={empty} challenged={challenged} "
                      f"errors={errors} | last={dul}", end="", flush=True)
                continue
            else:
                print(f"\n[!] {dul}: {data}")
                errors += 1
                continue  # don't mark as done - worth retrying

            done.add(dul)
            state["done"] = sorted(done)
            save_state(state)

            print(f"\r[Discovery] hits={hits} empty={empty} challenged={challenged} "
                  f"errors={errors} | last={dul}", end="", flush=True)

            # Occasional longer pause mimics a real user reading a result
            # or getting briefly distracted, rather than a metronomic
            # search-after-search pattern.
            if random.random() < 0.15:
                await asyncio.sleep(random.uniform(15, 35))
            else:
                await asyncio.sleep(random.uniform(5.0, 9.0))

        await context.close()

    save_master_files(flattened_records)  # final save, guarantees nothing since the last checkpoint is lost

    print(f"\n[+] Done. hits={hits} empty={empty} challenged(skipped)={challenged} errors={errors}")
    print(f"[+] Raw hits log: {HITS_FILE}")
    print(f"[+] Flattened master (same shape as shard output): {MASTER_JSON} / {MASTER_XLSX}")
    print(f"[+] Skipped (retry later) DULs: {SKIPPED_FILE}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", help="text file, one DUL candidate per line")
    ap.add_argument("--retry-skipped", action="store_true",
                     help="use browser_discovery_skipped.txt as the candidate list "
                          "instead of --candidates - re-checks everything that got "
                          "a visible challenge last time, since a fresh session/IP "
                          "may pass invisibly where the last attempt didn't")
    ap.add_argument("--tor", action="store_true")
    ap.add_argument("--headed", action="store_true",
                     help="show the browser window (recommended for the first run)")
    args = ap.parse_args()

    if not args.candidates and not args.retry_skipped:
        ap.error("either --candidates or --retry-skipped is required")

    if args.retry_skipped:
        if not os.path.exists(SKIPPED_FILE):
            print(f"[*] {SKIPPED_FILE} not found - nothing to retry.")
            return
        with open(SKIPPED_FILE) as f:
            candidates = sorted(set(line.strip() for line in f if line.strip()))
        print(f"[*] Retrying {len(candidates)} previously-skipped (challenged) DULs.")
        # Clear the skip file - we're about to re-attempt everything in it.
        # Whatever gets challenged again this pass will be re-appended
        # fresh, so nothing is lost, and the file doesn't just grow
        # forever with permanent duplicates across retry rounds.
        open(SKIPPED_FILE, "w").close()
    else:
        with open(args.candidates) as f:
            candidates = [line.strip() for line in f if line.strip()]

    asyncio.run(run(candidates, use_tor=args.tor, headless=not args.headed))


if __name__ == "__main__":
    main()
