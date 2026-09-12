import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
import random
import threading
import time
from curl_cffi import requests as cffi_requests
from playwright.async_api import async_playwright
import pandas as pd
from bs4 import BeautifulSoup


# --- Invest in Dubai Name-Search Enumerator ---
class InvestInDubaiSearchEngine:
    SEARCH_STATE_FILE = "investindubai_search_state.json"
    SEARCH_URL = "https://api.visitdubai.com/api/dul/Search"

    def __init__(self):
        self.session = cffi_requests.Session(impersonate="chrome136")
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Referer": "https://www.investindubai.gov.ae/",
            "Origin": "https://www.investindubai.gov.ae",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="136", "Not(A:Brand";v="24", "Google Chrome";v="136"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        })
        self.seen_terms = self.load_search_state()
        self.discovered_dul_numbers = set()

    def load_search_state(self):
        if os.path.exists(self.SEARCH_STATE_FILE):
            try:
                with open(self.SEARCH_STATE_FILE, "r") as f:
                    data = json.load(f)
                    return set(data.get("seen_terms", []))
            except Exception as e:
                print(f"[!] Error loading search state file: {e}")
        return set()

    def save_search_state(self):
        data = {
            "seen_terms": list(self.seen_terms),
            "discovered_dul_numbers": list(self.discovered_dul_numbers),
        }
        with open(self.SEARCH_STATE_FILE, "w") as f:
            json.dump(data, f, indent=4)

    def validate_query_length(self, query: str) -> bool:
        if len(query.strip()) < 3:
            print(f"[!] Skipped query '{query}': fails 3-character minimum length requirement.")
            return False
        return True

    def check_for_captcha_or_block(self, response) -> bool:
        if response.status_code in (403, 429):
            print(f"[!] WAF interception triggered: HTTP {response.status_code}.")
            return True

        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type:
            soup = BeautifulSoup(response.text, "html.parser")
            if soup.find(string=lambda t: t and ("hCaptcha" in t or "Access Denied" in t or "Robot" in t)):
                print("[!] hCaptcha / bot challenge detected in HTML response payload.")
                return True
        return False

    def handle_captcha_solver(self):
        raise NotImplementedError(
            "hCaptcha solving is not implemented. Search-based enumeration cannot "
            "proceed until a real token source (solver API or browser automation) "
            "is wired into handle_captcha_solver()."
        )

    def execute_search(self, query_term: str, page: int = 0, size: int = 15, captcha_token: str = None):
        if not self.validate_query_length(query_term):
            return None

        params = {"name": query_term, "page": page, "size": size}
        body = {"h-captcha": captcha_token} if captcha_token else {}

        try:
            response = self.session.post(self.SEARCH_URL, params=params, json=body, timeout=15)

            if self.check_for_captcha_or_block(response):
                try:
                    self.handle_captcha_solver()
                except NotImplementedError as e:
                    print(f"[!] Captcha solver unavailable, skipping term '{query_term}': {e}")
                return None

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    print("[!] Response did not return valid JSON structure.")
                    return None
            else:
                print(f"[!] Search query failed with status code: {response.status_code}")
                return None

        except Exception as e:
            print(f"[!] Network request exception encountered: {e}")
            return None

    def extract_dul_numbers(self, results_json) -> set:
        found = set()
        if not results_json:
            return found
        items = results_json.get("data") or results_json.get("results") or []
        for item in items:
            dul_no = item.get("dulNumber") or item.get("dul")
            if dul_no:
                found.add(dul_no)
        return found

    def run_search_enumeration(self, keyword_list, captcha_token_source=None):
        print(f"[*] Starting search enumeration across {len(keyword_list)} terms...")
        for term in keyword_list:
            if term in self.seen_terms:
                continue

            print(f"[*] Querying search term: '{term}'")
            token = captcha_token_source() if captcha_token_source else None

            try:
                results = self.execute_search(term, captcha_token=token)
            except Exception as e:
                print(f"[!] Unexpected error on term '{term}': {e}")
                results = None

            if results:
                new_ids = self.extract_dul_numbers(results)
                self.discovered_dul_numbers.update(new_ids)
                print(f"    -> {len(new_ids)} DUL numbers discovered.")

            self.seen_terms.add(term)
            self.save_search_state()
            time.sleep(1.5)

        return self.discovered_dul_numbers


# --- Playwright Session Initializer with Robust Akamai Sensor Simulation ---
async def fetch_browser_cookies(use_tor=False, headless=True):
    print("[*] Launching headless browser to initialize Akamai session & sensor cookies...")
    async with async_playwright() as p:
        launch_kwargs = dict(
            headless=headless,
            channel="chrome",  # use the real installed Chrome binary (genuine
                                # TLS/JA3 fingerprint) instead of Playwright's
                                # bundled Chromium, which Akamai's Bot Manager
                                # was flatly rejecting at the network layer
                                # before any page JS even ran (confirmed via
                                # debug screenshot: instant "Access Denied"
                                # from edgesuite.net with only 2 bare edge
                                # cookies ever set, regardless of IP/Tor).
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--start-maximized",
                "--lang=en-US",
            ],
        )
        if use_tor:
            launch_kwargs["proxy"] = {"server": "socks5://127.0.0.1:9050"}
        try:
            browser = await p.chromium.launch(**launch_kwargs)
        except Exception as e:
            print(f"[!] Could not launch real Chrome ({e}) - falling back to "
                  "Playwright's bundled Chromium. Run 'playwright install chrome' "
                  "to fix this properly, since bundled Chromium is what Akamai "
                  "was blocking.")
            launch_kwargs.pop("channel", None)
            browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Dubai",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.navigator.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters)
            );
        """)

        try:
            await page.goto(
                "https://www.investindubai.gov.ae/", wait_until="networkidle", timeout=60000
            )
        except Exception:
            # networkidle can time out on sites with persistent background
            # connections (analytics, sensors) - fall back to a fixed wait
            # instead of failing the whole harvest.
            pass

        if os.environ.get("DEBUG_COOKIE_HARVEST"):
            try:
                await page.screenshot(path="debug_harvest_screenshot.png", full_page=True)
                html = await page.content()
                with open("debug_harvest_page.html", "w", encoding="utf-8") as f:
                    f.write(html)
                print("[DEBUG] Saved debug_harvest_screenshot.png and "
                      "debug_harvest_page.html - inspect these to see what "
                      "Akamai actually served this session.")
            except Exception as e:
                print(f"[DEBUG] Could not save debug artifacts: {e}")

        try:
            # Simulate realistic human activity across a longer window so
            # Akamai's sensor script (which profiles mouse/scroll/timing
            # behavior before issuing full session cookies) has enough
            # signal to consider this a real browser, not just enough time
            # elapsed.
            for _ in range(4):
                x, y = random.randint(100, 1700), random.randint(100, 900)
                await page.mouse.move(x, y, steps=15)
                await page.wait_for_timeout(random.randint(400, 900))

            await page.mouse.wheel(0, random.randint(200, 600))
            await page.wait_for_timeout(1500)
            await page.mouse.wheel(0, random.randint(-200, -100))
            await page.wait_for_timeout(1500)

            await page.mouse.move(350, 250, steps=10)
            await page.mouse.down()
            await page.wait_for_timeout(150)
            await page.mouse.up()
            await page.wait_for_timeout(4000)

            cookies = await context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            # A healthy Akamai session usually yields several sensor/session
            # cookies (_abck, bm_sz, ak_bmsc, etc). If still thin, give it
            # one more extended round before accepting what we have.
            attempt = 0
            while len(cookie_dict) < 4 and attempt < 3:
                attempt += 1
                print(f"[!] Only {len(cookie_dict)} cookies so far - extending "
                      f"activity simulation (round {attempt})...")
                for _ in range(3):
                    x, y = random.randint(100, 1700), random.randint(100, 900)
                    await page.mouse.move(x, y, steps=20)
                    await page.wait_for_timeout(random.randint(600, 1200))
                await page.mouse.wheel(0, random.randint(300, 700))
                await page.wait_for_timeout(3000)
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(6000)
                cookies = await context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}

            print(f"[*] Successfully harvested {len(cookie_dict)} session & sensor cookies.")
            if len(cookie_dict) == 0:
                print("[!] WARNING: 0 cookies harvested - site may not have loaded correctly.")
            return cookie_dict
        except Exception as e:
            print(f"[!] Error during browser cookie pre-fetch: {e}")
            return {}
        finally:
            await browser.close()


class AdvancedScraperEngine:

    def __init__(self, config, initial_cookies=None):
        self.target_name = config.get("target_name", "generic_target")
        self.base_url = config.get("base_url")
        self.output_file = config.get("output_file", "visitdubai.xlsx")
        self.state_file = config.get("state_file", "visitdubai.json")
        self.max_workers = config.get("max_workers", 1)

        self.base_delay_min, self.base_delay_max = config.get(
            "delay_range", (2.0, 4.0)
        )
        self.current_delay_min = self.base_delay_min
        self.current_delay_max = self.base_delay_max

        self.checkpoint_interval = config.get("checkpoint_interval", 5)

        self.lock = threading.RLock()

        self.seen_identifiers = set()
        self.scraped_records = []
        self.stats = {
            "requests_sent": 0,
            "success": 0,
            "failed": 0,
            "empty_payload": 0,
            "duplicates_skipped": 0,
            "captcha_or_block_hits": 0,
            "start_time": None,
        }

        self._initial_cookies = initial_cookies
        self.use_tor = config.get("use_tor", False)

        self._thread_local = threading.local()
        self._cookies_lock = threading.Lock()
        self._current_cookies = dict(initial_cookies) if initial_cookies else {}
        self._consecutive_block_count = 0

        self._load_state()

    def _init_session(self, cookies=None):
        if self.use_tor:
            from tor_rotation import build_tor_session
            session = build_tor_session()
        else:
            session = cffi_requests.Session(impersonate="chrome136")
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Referer": "https://www.investindubai.gov.ae/",
            "Origin": "https://www.investindubai.gov.ae",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="136", "Not(A:Brand";v="24", "Google Chrome";v="136"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        })
        if cookies:
            session.cookies.update(cookies)
        return session

    def _get_thread_session(self):
        session = getattr(self._thread_local, "session", None)
        if session is None:
            with self._cookies_lock:
                cookies = dict(self._current_cookies)
            session = self._init_session(cookies)
            self._thread_local.session = session
        return session

    def _rotate_tor_circuit(self):
        if not self.use_tor:
            return False
        try:
            from tor_rotation import rotate_tor_circuit
            return rotate_tor_circuit(
                reason="3 consecutive blocks",
                worker_name=self.target_name,
            )
        except Exception as e:
            print(f"[!] Tor circuit rotation failed: {e}")
            return False

    def _refresh_cookies_if_needed(self):
        with self.lock:
            self._consecutive_block_count += 1
            should_refresh = self._consecutive_block_count >= 3
        if not should_refresh:
            return False

        if self.use_tor:
            self._rotate_tor_circuit()

        print("[!] Repeated blocking detected - re-harvesting Akamai session cookies...")
        try:
            fresh_cookies = asyncio.run(fetch_browser_cookies(use_tor=self.use_tor))
        except Exception as e:
            print(f"[!] Cookie refresh failed: {e}")
            return False

        if not fresh_cookies:
            return False

        with self._cookies_lock:
            self._current_cookies = fresh_cookies
        with self.lock:
            self._consecutive_block_count = 0

        self._thread_local.session = self._init_session(fresh_cookies)
        return True

    def _note_request_ok(self):
        with self.lock:
            self._consecutive_block_count = 0

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self.seen_identifiers = set(data.get("seen_identifiers", []))
                    print(
                        "[*] Resumed state: Loaded"
                        f" {len(self.seen_identifiers)} previously processed"
                        " identifiers."
                    )
            except Exception:
                pass

        # Reload previously scraped records so a restart doesn't wipe out
        # everything collected before the last checkpoint/exit. Without
        # this, seen_identifiers still causes those DULs to be skipped as
        # "already done" on resume, but their actual data (which only ever
        # lived in memory in self.scraped_records) is silently lost.
        if os.path.exists(self.output_file):
            try:
                df = pd.read_excel(self.output_file)
                self.scraped_records = df.to_dict("records")
                print(
                    "[*] Resumed output: Loaded"
                    f" {len(self.scraped_records)} previously scraped records"
                    f" from {self.output_file}."
                )
            except Exception as e:
                print(f"[!] Could not reload existing output file ({e}) - starting scraped_records empty.")

    def _save_state(self):
        with self.lock:
            try:
                with open(self.state_file, "w") as f:
                    json.dump({"seen_identifiers": list(self.seen_identifiers)}, f)
            except Exception:
                pass

    def _adjust_throttle(self, is_success, status_code=200):
        with self.lock:
            if is_success and status_code == 200:
                self.current_delay_min = max(
                    self.base_delay_min, self.current_delay_min * 0.95
                )
                self.current_delay_max = max(
                    self.base_delay_max, self.current_delay_max * 0.95
                )
            else:
                backoff_multiplier = 2.5 if status_code in [429, 403] else 1.3
                self.current_delay_min = min(
                    8.0, self.current_delay_min * backoff_multiplier
                )
                self.current_delay_max = min(
                    15.0, self.current_delay_max * backoff_multiplier
                )

    def _is_permanent_not_found(self, response) -> bool:
        if response.status_code != 500:
            return False
        try:
            data = response.json()
        except Exception:
            return False
        return data.get("result") is False and data.get("error") == "An error occurred."

    def fetch(self, identifier, parser_func, max_retries=3):
        with self.lock:
            if identifier in self.seen_identifiers:
                self.stats["duplicates_skipped"] += 1
                return None

        url = self.base_url.format(id=identifier)
        session = self._get_thread_session()

        for attempt in range(max_retries + 1):
            try:
                with self.lock:
                    self.stats["requests_sent"] += 1
                    sleep_time = random.uniform(
                        self.current_delay_min, self.current_delay_max
                    )
                time.sleep(sleep_time)

                response = session.get(url, timeout=(10, 15))

                if response.status_code != 200:
                    body_preview = response.text[:200].replace("\n", " ")
                    print(
                        f"\n[DEBUG] {identifier} -> HTTP {response.status_code} "
                        f"| attempt {attempt} | body: {body_preview}"
                    )

                if response.status_code in (403, 429):
                    with self.lock:
                        self.stats["captcha_or_block_hits"] += 1
                    self._refresh_cookies_if_needed()
                else:
                    self._note_request_ok()

                if response.status_code == 200:
                    try:
                        json_data = response.json()
                    except Exception:
                        self._adjust_throttle(is_success=False, status_code=200)
                        if attempt == max_retries:
                            with self.lock:
                                self.stats["failed"] += 1
                                self.seen_identifiers.add(identifier)
                                if len(self.seen_identifiers) % self.checkpoint_interval == 0:
                                    self._save_state()
                            return None
                        time.sleep(2 ** attempt)
                        continue

                    parsed_data = (
                        parser_func(json_data, identifier)
                        if "identifier" in parser_func.__code__.co_varnames
                        else parser_func(json_data)
                    )

                    with self.lock:
                        self.seen_identifiers.add(identifier)
                        if len(self.seen_identifiers) % self.checkpoint_interval == 0:
                            self._save_state()

                    if parsed_data:
                        self._adjust_throttle(is_success=True, status_code=200)
                        with self.lock:
                            self.scraped_records.append(parsed_data)
                            self.stats["success"] += 1

                            if len(self.scraped_records) % self.checkpoint_interval == 0:
                                self._save_output_unlocked(is_checkpoint=True)
                        return parsed_data
                    else:
                        self._adjust_throttle(is_success=True, status_code=200)
                        with self.lock:
                            self.stats["empty_payload"] += 1
                        return None
                else:
                    if self._is_permanent_not_found(response):
                        print(f"[DEBUG] {identifier} confirmed permanent-not-found (500, no such DUL)")
                        # A confirmed not-found is an expected, valid outcome in
                        # a brute-force keyspace (most DULs simply don't exist)
                        # - it is NOT a sign of being throttled or blocked, so
                        # don't back off the delay for it. Previously this
                        # called _adjust_throttle(is_success=False, ...), which
                        # meant a shard landing on a dense run of misses (which
                        # is normal and has nothing to do with rate limiting)
                        # would spiral into an ever-growing artificial delay.
                        with self.lock:
                            self.stats["failed"] += 1
                            self.seen_identifiers.add(identifier)
                            if len(self.seen_identifiers) % self.checkpoint_interval == 0:
                                self._save_state()
                        return None

                    if attempt == max_retries:
                        self._adjust_throttle(is_success=False, status_code=response.status_code)
                        with self.lock:
                            self.stats["failed"] += 1
                            self.seen_identifiers.add(identifier)
                            if len(self.seen_identifiers) % self.checkpoint_interval == 0:
                                self._save_state()
                        return None

                    self._adjust_throttle(is_success=False, status_code=response.status_code)
                    time.sleep((2 ** attempt) + random.uniform(1.0, 3.0))
                    continue

            except Exception as e:
                print(f"\n[DEBUG-EXC] {identifier} -> {type(e).__name__}: {e} | attempt {attempt}")
                if attempt == max_retries:
                    self._adjust_throttle(is_success=False, status_code=500)
                    with self.lock:
                        self.stats["failed"] += 1
                        self.seen_identifiers.add(identifier)
                        if len(self.seen_identifiers) % self.checkpoint_interval == 0:
                            self._save_state()
                    return None
                time.sleep((2 ** attempt) + random.uniform(1.0, 3.0))

        return None

    def render_dashboard(self, current_id):
        with self.lock:
            elapsed = datetime.now() - self.stats["start_time"]
            print(
                f"\r[Engine] Target: {self.target_name} | "
                f"Reqs: {self.stats['requests_sent']} | "
                f"Success: {self.stats['success']} | "
                f"Failed: {self.stats['failed']} | "
                f"Empty: {self.stats['empty_payload']} | "
                f"Dupes: {self.stats['duplicates_skipped']} | "
                f"Delay: {self.current_delay_min:.2f}s | "
                f"Time: {str(elapsed).split('.')[0]} | "
                f"Current: {current_id:<10}",
                end="",
                flush=True,
            )

    def _save_output_unlocked(self, is_checkpoint=False):
        if self.scraped_records:
            df = pd.DataFrame(self.scraped_records)
            # Atomic write: write to a temp file first, then rename over
            # the real target. A rename is effectively instantaneous at
            # the filesystem level, so anything reading self.output_file
            # concurrently (e.g. the merge script) never sees a partial/
            # corrupt xlsx mid-write - it either sees the old complete
            # file or the new complete file, never a torn one.
            tmp_path = self.output_file.rsplit(".", 1)[0] + ".tmp.xlsx"
            df.to_excel(tmp_path, index=False)
            os.replace(tmp_path, self.output_file)
            if not is_checkpoint:
                print(
                    "\n[+] Exported"
                    f" {len(self.scraped_records)} unique records to {self.output_file}"
                )

    def save_output(self):
        with self.lock:
            self._save_output_unlocked(is_checkpoint=False)
            self._save_state()

    def run(self, identifier_generator, parser_func):
        print(f"[*] Initializing streaming enumeration engine for: {self.target_name}")
        self.stats["start_time"] = datetime.now()

        batch_size = 500
        try:
            while True:
                batch = []
                for ident in identifier_generator:
                    if ident not in self.seen_identifiers:
                        batch.append(ident)
                        if len(batch) >= batch_size:
                            break

                if not batch:
                    break

                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {
                        executor.submit(self.fetch, ident, parser_func): ident
                        for ident in batch
                    }

                    try:
                        for future in as_completed(futures):
                            ident = futures[future]
                            self.render_dashboard(ident)
                            try:
                                future.result()
                            except Exception:
                                with self.lock:
                                    self.stats["failed"] += 1
                    except KeyboardInterrupt:
                        print("\n[!] Ctrl+C received - cancelling pending requests "
                              "and saving progress (this is fast, please wait)...")
                        # cancel_futures=True (Python 3.9+) drops anything not yet
                        # started instead of waiting for the full batch to drain.
                        executor.shutdown(wait=False, cancel_futures=True)
                        raise

        except KeyboardInterrupt:
            print("[*] Shutdown requested - saving current progress...")

        print(f"\n[*] Run completed in {datetime.now() - self.stats['start_time']}")
        self.save_output()


# --- Comprehensive VisitDubai DUL Parser Engine ---
def parse_visitdubai_dul(data, identifier=None):
    if not data or "dulNumber" not in data:
        return None

    issue_authority = data.get("issueAuthority") or {}
    category = data.get("category") or {}
    activity_type = data.get("activityType") or {}
    status = data.get("status") or {}
    trade_name = data.get("tradeName") or {}
    tn_issue_authority = trade_name.get("issueAuthority") or {}
    activity_group = trade_name.get("activityGroup") or {}
    address = data.get("address") or {}
    area = address.get("area") or {}
    main_area = area.get("mainArea") or {}
    unit_type = address.get("unitType") or {}
    country = data.get("country") or {}
    emirate = address.get("emirate") or {}
    company = data.get("company") or {}
    legal_type = company.get("legalType") or {}

    activities = data.get("operatingActivities") or []
    activity_names_en = "|".join(
        (a.get("activity") or {}).get("englishName") or "" for a in activities
    )
    activity_names_ar = "|".join(
        (a.get("activity") or {}).get("arabicName") or "" for a in activities
    )
    activity_statuses = "|".join(
        (a.get("status") or {}).get("englishName") or "" for a in activities
    )

    record = {
        "Target ID": identifier,
        "dulNumber": data.get("dulNumber"),
        "unifiedLicenceNumber": data.get("unifiedLicenceNumber"),
        "dulStatus": data.get("dulStatus"),
        "issueAuthorityId": issue_authority.get("id"),
        "issueAuthorityEn": issue_authority.get("englishName"),
        "issueAuthorityAr": issue_authority.get("arabicName"),
        "issueAuthorityLicenceNumber": data.get("issueAuthorityLicenceNumber"),
        "issueDate": data.get("issueDate"),
        "expiryDate": data.get("expiryDate"),
        "categoryId": category.get("id"),
        "categoryEn": category.get("englishName"),
        "activityTypeEn": activity_type.get("englishName"),
        "statusId": status.get("id"),
        "statusEn": status.get("englishName"),
        "statusAr": status.get("arabicName"),
        "unifiedTradeNameNumber": trade_name.get("unifiedTradeNameNumber"),
        "tradeNameIssueAuthorityEn": tn_issue_authority.get("englishName"),
        "tradeNameEn": trade_name.get("englishName"),
        "tradeNameAr": trade_name.get("arabicName"),
        "tradeNameIssueDate": trade_name.get("issueDate"),
        "tradeNameExpiryDate": trade_name.get("expiryDate"),
        "activityGroupCode": activity_group.get("activityGroupCode"),
        "activityGroupEn": activity_group.get("englishName"),
        "addressAreaEn": area.get("englishName"),
        "addressAreaAr": area.get("arabicName"),
        "addressMainAreaEn": main_area.get("englishName"),
        "addressLine1": address.get("addressLine1"),
        "addressFloor": address.get("floor"),
        "addressUnitTypeEn": unit_type.get("englishName"),
        "addressUnitNumber": address.get("unitNumber"),
        "addressParcelID": address.get("parcelID"),
        "addressCountryEn": country.get("englishName"),
        "addressEmirateEn": emirate.get("englishName"),
        "addressState": address.get("state"),
        "isMainLicense": data.get("isMainLicense"),
        "phone": data.get("phone"),
        "legalTypeEn": legal_type.get("englishName"),
        "legalTypeAr": legal_type.get("arabicName"),
        "numOperatingActivities": len(activities),
        "activityNamesEn": activity_names_en,
        "activityNamesAr": activity_names_ar,
        "activityStatuses": activity_statuses,
    }
    return record


def generate_dul_candidates(prefixes=None):
    import string

    if prefixes is None:
        # Full two-letter alphabet coverage: AA-ZZ = 676 combinations.
        # Previously only 41 hand-picked prefixes were tried - most of the
        # ID space was never even attempted. Known dead/thin ranges (see
        # per_prefix_start below) are skipped to avoid wasting requests.
        prefixes = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]

    # Confirmed via discovery scan across 54 checked prefixes (92.6% real,
    # every single real hit example landing in the 1000-1300 range, zero
    # exceptions): the 1-999 range is a universal dead zone across every
    # prefix, not just FI. Skip it everywhere - saves ~999 wasted requests
    # per prefix with zero observed false-negative risk so far.
    UNIVERSAL_DEAD_ZONE_END = 1000

    for prefix in prefixes:
        for num in range(UNIVERSAL_DEAD_ZONE_END, 10000):
            yield f"{prefix}{num:04d}"


def get_shard_prefixes(shard_index, shard_count):
    """Split the full AA-ZZ prefix space into shard_count disjoint chunks,
    returning the chunk for shard_index (0-based). Run one process per
    shard, each with a different --shard N --shards TOTAL, to parallelize
    across multiple terminals/machines without any two processes ever
    touching the same prefix."""
    import string
    all_prefixes = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    return [p for i, p in enumerate(all_prefixes) if i % shard_count == shard_index]


if __name__ == "__main__":
    import sys

    shard_index = 0
    shard_count = 1
    workers = 4
    use_tor = False
    for i, arg in enumerate(sys.argv):
        if arg == "--shard" and i + 1 < len(sys.argv):
            shard_index = int(sys.argv[i + 1])
        if arg == "--shards" and i + 1 < len(sys.argv):
            shard_count = int(sys.argv[i + 1])
        if arg == "--workers" and i + 1 < len(sys.argv):
            workers = int(sys.argv[i + 1])
        if arg == "--tor":
            use_tor = True

    if shard_count > 1:
        my_prefixes = get_shard_prefixes(shard_index, shard_count)
        output_file = f"visitdubai_brute_shard{shard_index}.xlsx"
        state_file = f"visitdubai_brute_shard{shard_index}.json"
        print(f"[*] Running as shard {shard_index}/{shard_count} - "
              f"{len(my_prefixes)} prefixes assigned: {my_prefixes[:5]}...")
    else:
        my_prefixes = None  # full AA-ZZ range, single process
        output_file = "visitdubai_brute.xlsx"
        state_file = "visitdubai_brute.json"

    config = {
        "target_name": f"VisitDubai-DUL-Enumeration-Shard{shard_index}" if shard_count > 1 else "VisitDubai-DUL-Enumeration",
        "base_url": "https://api.visitdubai.com/api/dul/GetDulDetails?dul={id}",
        "output_file": output_file,
        "state_file": state_file,
        "max_workers": workers,  # per-process; run multiple shards for more total concurrency
        "delay_range": (0.8, 1.8),
        "checkpoint_interval": 50,
        "use_tor": use_tor,
    }

    if use_tor:
        print("[*] Tor mode enabled - routing all traffic through 127.0.0.1:9050 "
              "(SOCKS5) and rotating circuits on repeated blocks via control port 9051.")

    print("[*] Initializing stealth streaming DUL enumeration script...")
    fresh_cookies = asyncio.run(fetch_browser_cookies(use_tor=use_tor))

    if not fresh_cookies:
        print("[!] FATAL: No cookies harvested. Aborting run - every request would fail "
              "against Akamai without a valid session. Check network connectivity to the "
              "target site, or try running with headless=False locally to see what the "
              "browser actually renders.")
        raise SystemExit(1)

    scraper = AdvancedScraperEngine(config, initial_cookies=fresh_cookies)
    scraper.run(generate_dul_candidates(prefixes=my_prefixes), parse_visitdubai_dul)
