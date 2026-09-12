import asyncio
import json
import string
import time

from curl_cffi import requests as cffi_requests
from playwright.async_api import async_playwright

DETAIL_URL = "https://api.visitdubai.com/api/dul/GetDulDetails?dul={id}"
OUTPUT_FILE = "verified_prefixes.json"
BONUS_HITS_FILE = "discovery_bonus_hits.json"

# Abandon a prefix after this many CONSECUTIVE misses with zero hits found
# so far in that prefix. Must be well above the widest known dead zone
# (FI's dead zone was ~700-999, so 2500 gives large safety margin) to avoid
# ever producing a false negative on real data.
MISS_THRESHOLD = 3000  # raised from 1500: verified data shows real prefixes starting
                        # as late as AR1285/BH1245/BR1247 - 1500 left too thin a margin
                        # for those cases. 3000 gives solid headroom; costs little overall
                        # since only the ~7% genuinely dead prefixes ever hit this ceiling.
DNS_RECOVERY_MAX_ATTEMPTS = 6  # ~1+2+4+8+16+32 = 63s of backoff before giving up


def wait_for_dns_recovery(last_failed_id):
    """Actively checks whether DNS resolution has actually recovered instead
    of blindly sleeping and hoping. Uses escalating backoff. Returns True if
    recovered, False if it never came back within the attempt budget."""
    import socket

    for attempt in range(1, DNS_RECOVERY_MAX_ATTEMPTS + 1):
        wait_time = 2 ** attempt
        print(f"[!!!] Connection errors at {last_failed_id} (attempt {attempt}/"
              f"{DNS_RECOVERY_MAX_ATTEMPTS}) - waiting {wait_time}s before checking "
              f"DNS again...")
        time.sleep(wait_time)
        try:
            socket.gethostbyname("api.visitdubai.com")
            print(f"[+] DNS resolved successfully - resuming.")
            return True
        except socket.gaierror:
            print(f"[!] DNS still not resolving (attempt {attempt}/{DNS_RECOVERY_MAX_ATTEMPTS})")
            continue
    return False

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Referer": "https://www.investindubai.gov.ae/",
    "Origin": "https://www.investindubai.gov.ae",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}


async def fetch_browser_cookies():
    print("[*] Launching headless browser to initialize Akamai session & sensor cookies...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-setuid-sandbox",
        ])
        context = await browser.new_context(
            user_agent=BASE_HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
            locale="en-US", timezone_id="Asia/Dubai",
        )
        page = await context.new_page()
        try:
            await page.goto("https://www.investindubai.gov.ae/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)
            await page.mouse.move(350, 250)
            await page.mouse.down()
            await page.mouse.up()
            await page.wait_for_timeout(4000)
            cookies = await context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}
            print(f"[*] Harvested {len(cookie_dict)} cookies.")
            return cookie_dict
        except Exception as e:
            print(f"[!] Cookie harvest error: {e}")
            return {}
        finally:
            await browser.close()


def check_id(session, dul_id, max_retries=2):
    """Returns (status, data) where status is 'hit', 'miss', or 'error'."""
    for attempt in range(max_retries):
        try:
            resp = session.get(DETAIL_URL.format(id=dul_id), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("dulNumber"):
                    return "hit", data
                return "miss", None
            if resp.status_code == 500:
                try:
                    body = resp.json()
                    if body.get("result") is False and body.get("error") == "An error occurred.":
                        return "miss", None
                except Exception:
                    pass
            return "error", None
        except Exception as e:
            if attempt == max_retries - 1:
                return "error", str(e)
            time.sleep(1.0)
    return "error", None


def scan_prefix(prefix, cookies, miss_threshold=MISS_THRESHOLD, stop_after_hits=5):
    """True sequential scan with exact consecutive-miss tracking.
    Stops early once stop_after_hits real hits are confirmed - discovery's
    job is just to verify a prefix is real, not fully harvest it (that's
    the production brute-forcer's job on the verified prefix list).
    Returns (is_real, first_hit_id, all_hits_found)."""
    session = cffi_requests.Session(impersonate="chrome136")
    session.headers.update(BASE_HEADERS)
    session.cookies.update(cookies)

    consecutive_misses = 0
    consecutive_errors = 0
    hits = []

    for num in range(1, 10000):
        dul_id = f"{prefix}{num:04d}"
        status, data = check_id(session, dul_id)

        if status == "hit":
            hits.append(dul_id)
            consecutive_misses = 0
            consecutive_errors = 0
            if len(hits) == 1:
                print(f"  [+] {prefix}: first hit at {dul_id}")
            if len(hits) >= stop_after_hits:
                print(f"  [+] {prefix}: confirmed real ({len(hits)} hits), stopping early")
                return True, hits[0], hits
        elif status == "miss":
            consecutive_misses += 1
            consecutive_errors = 0
        else:  # error - likely network/DNS issue, not a real miss
            consecutive_errors += 1
            if consecutive_errors >= 5:
                if not wait_for_dns_recovery(dul_id):
                    print(f"\n[!!!] ABORTING: network/DNS did not recover after "
                          f"{DNS_RECOVERY_MAX_ATTEMPTS} attempts. Check your internet "
                          f"connection, then restart this script - progress up to "
                          f"'{prefix}' is safe in {OUTPUT_FILE} from the last checkpoint.")
                    raise SystemExit(1)
                consecutive_errors = 0
            continue  # don't count errors toward the miss threshold

        if not hits and consecutive_misses >= miss_threshold:
            print(f"  [-] {prefix}: abandoned after {consecutive_misses} consecutive "
                  f"misses with zero hits (checked up to {dul_id})")
            return False, None, []

        time.sleep(0.35)

    return (len(hits) > 0), (hits[0] if hits else None), hits


def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    cookies = asyncio.run(fetch_browser_cookies())
    if not cookies:
        print("[!] FATAL: no cookies harvested, aborting.")
        return

    # --- Mandatory self-check against known-real FI before trusting anything ---
    print("[*] Self-check: scanning known-good prefix FI (should find hits well before "
          f"the {MISS_THRESHOLD} miss-threshold, since its dead zone is only ~700-999)...")
    fi_real, fi_first, fi_hits = scan_prefix("FI", cookies)
    if not fi_real:
        print("\n[!!!] SELF-CHECK FAILED: FI is confirmed real from prior runs but this "
              "scan found nothing. STOP - do not trust results, check network/session first.")
        return
    print(f"[+] Self-check passed: FI confirmed, {len(fi_hits)} hits found "
          f"(first: {fi_first})\n")

    all_prefixes = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    all_prefixes.remove("FI")

    verified = {"FI": fi_first}
    bonus_hits = {"FI": fi_hits}
    lock = threading.Lock()
    completed_count = [0]

    MAX_WORKERS = 15  # concurrent prefixes - each internally sequential, so this
                      # is pure parallelism across prefixes, no correctness risk

    print(f"[*] Scanning remaining {len(all_prefixes)} prefixes with {MAX_WORKERS} "
          f"concurrent workers (abandon threshold: {MISS_THRESHOLD} consecutive misses, "
          f"stop-early after 5 hits)...\n")

    def save_checkpoint():
        with open(OUTPUT_FILE, "w") as f:
            json.dump({"verified_prefixes": sorted(verified.keys()), "examples": verified}, f, indent=2)
        with open(BONUS_HITS_FILE, "w") as f:
            json.dump(bonus_hits, f, indent=2)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_prefix, p, cookies): p for p in all_prefixes}
        for future in as_completed(futures):
            prefix = futures[future]
            try:
                is_real, first_hit, hits = future.result()
            except SystemExit:
                # DNS recovery gave up inside a worker thread - stop everything
                print("[!!!] A worker hit unrecoverable DNS failure - shutting down.")
                executor.shutdown(wait=False, cancel_futures=True)
                save_checkpoint()
                raise SystemExit(1)
            except Exception as e:
                print(f"  [!] {prefix}: worker crashed -> {e}")
                is_real, first_hit, hits = False, None, []

            with lock:
                if is_real:
                    verified[prefix] = first_hit
                    bonus_hits[prefix] = hits
                completed_count[0] += 1
                if completed_count[0] % 10 == 0:
                    print(f"[*] Progress: {completed_count[0]}/{len(all_prefixes)} "
                          f"prefixes checked, {len(verified)} verified real so far")
                if completed_count[0] % 20 == 0:
                    save_checkpoint()

    print("\n" + "=" * 60)
    print(f"VERIFIED REAL PREFIXES: {len(verified)} / {len(all_prefixes) + 1}")
    print("=" * 60)
    for prefix, example in sorted(verified.items()):
        print(f"  {prefix}   example: {example}   ({len(bonus_hits.get(prefix, []))} hits found during scan)")

    save_checkpoint()

    total_bonus = sum(len(v) for v in bonus_hits.values())
    print(f"\n[+] Saved {len(verified)} verified prefixes to {OUTPUT_FILE}")
    print(f"[+] Saved {total_bonus} bonus hit IDs (found during scanning) to {BONUS_HITS_FILE}")


if __name__ == "__main__":
    main()
