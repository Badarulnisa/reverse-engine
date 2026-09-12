import asyncio
import json
import string
import time

from curl_cffi import requests as cffi_requests
from playwright.async_api import async_playwright

DETAIL_URL = "https://api.visitdubai.com/api/dul/GetDulDetails?dul={id}"
OUTPUT_FILE = "verified_prefixes.json"

# Sample points spread across the 4-digit space per prefix. If a prefix has
# ANY real allocations, at least one of these should hit (based on the
# pattern seen in FI: dense blocks scattered from ~1000 to ~4000+, sparse
# elsewhere). Adjust/add more points for more thoroughness at the cost of
# more requests (12 prefixes * N points each).
PROBE_POINTS = [1, 100, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 9500]

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


def is_permanent_not_found(resp):
    if resp.status_code != 500:
        return False
    try:
        data = resp.json()
    except Exception:
        return False
    return data.get("result") is False and data.get("error") == "An error occurred."


def probe_prefix(session, prefix):
    """Return True as soon as one probe point hits a real record."""
    for num in PROBE_POINTS:
        dul_id = f"{prefix}{num:04d}"
        try:
            resp = session.get(DETAIL_URL.format(id=dul_id), timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("dulNumber"):
                    print(f"  [+] {prefix}: HIT at {dul_id}")
                    return True, dul_id
            elif not is_permanent_not_found(resp):
                # Unexpected status - could be a rate limit / block, worth knowing
                print(f"  [?] {prefix}: unexpected HTTP {resp.status_code} at {dul_id}")
        except Exception as e:
            print(f"  [!] {prefix}: exception at {dul_id} -> {e}")
        time.sleep(0.6)
    return False, None


def main():
    cookies = asyncio.run(fetch_browser_cookies())
    if not cookies:
        print("[!] FATAL: no cookies harvested, aborting.")
        return

    session = cffi_requests.Session(impersonate="chrome136")
    session.headers.update(BASE_HEADERS)
    session.cookies.update(cookies)

    all_prefixes = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    verified = {}

    print(f"\n[*] Probing {len(all_prefixes)} prefixes x {len(PROBE_POINTS)} sample points each "
          f"({len(all_prefixes) * len(PROBE_POINTS)} total requests, ~0.6s apart)...\n")

    for i, prefix in enumerate(all_prefixes):
        hit, example = probe_prefix(session, prefix)
        if hit:
            verified[prefix] = example
        if (i + 1) % 26 == 0:
            print(f"[*] Progress: {i+1}/{len(all_prefixes)} prefixes checked, "
                  f"{len(verified)} verified so far")

    print("\n" + "=" * 60)
    print(f"VERIFIED REAL PREFIXES: {len(verified)} / {len(all_prefixes)}")
    print("=" * 60)
    for prefix, example in sorted(verified.items()):
        print(f"  {prefix}   example: {example}")

    with open(OUTPUT_FILE, "w") as f:
        json.dump({"verified_prefixes": sorted(verified.keys()), "examples": verified}, f, indent=2)

    print(f"\n[+] Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
