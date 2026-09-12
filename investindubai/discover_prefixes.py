import asyncio
import json
import re
import time
from collections import Counter

from curl_cffi import requests as cffi_requests
from playwright.async_api import async_playwright


SEARCH_URL = "https://api.visitdubai.com/api/dul/Search"
OUTPUT_FILE = "discovered_prefixes.json"

# Broad net of generic terms likely to appear across many different license
# holders/free zones - business words, common name fragments, single/double
# letters, and a few Arabic transliterations. The goal isn't to find every
# business, just to sample enough real DUL numbers to see which prefixes
# (issuing authorities/free zones) actually exist.
SEARCH_TERMS = [
    # Generic business words
    "Gen", "Tra", "Ser", "Tec", "Con", "Grp", "LLC", "FZE", "Ent", "Com",
    "Dub", "Global", "Prime", "Star", "Mega", "Best", "New", "United",
    "Al ", "The", "Pro", "Elite", "Royal", "Gulf", "Emirates", "Arabian",
    "National", "International", "Middle East", "World", "Smart", "Digital",
    "Solutions", "Group", "Holding", "Investment", "Trading", "Logistics",
    "Consulting", "Marketing", "Media", "Design", "Tech", "Food", "Retail",
    "Real Estate", "Construction", "Engineering", "Industries", "Manufacturing",
    # Common Arabic-origin name fragments (transliterated)
    "Bin", "Abu", "Ibn", "Sheikh", "Ahmed", "Mohammed", "Ali", "Hassan",
    "Khalifa", "Rashid", "Saeed", "Salem", "Nasser", "Fahad", "Majid",
    # Free zone / authority name fragments (may directly surface prefix clusters)
    "Free Zone", "Trakhees", "JAFZA", "DMCC", "DIFC", "DAFZA", "Dubai South",
    "Silicon Oasis", "Internet City", "Media City", "Healthcare City",
    "Maritime City", "Airport Free", "Gold and Diamond",
    # Single high-frequency letters (broad net, catches whatever slips through)
    "A L", "M A", "S A", "A M", "T H",
]

# Headers matching the confirmed-working session (Burp-captured real browser
# call had no cookies on the search/detail endpoints themselves - only the
# initial page load needed sensor cookies).
BASE_HEADERS = {
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
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
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


def extract_prefix(dul_number):
    match = re.match(r"^([A-Za-z]+)(\d+)$", dul_number or "")
    return match.group(1).upper() if match else None


def run_discovery(cookies):
    session = cffi_requests.Session(impersonate="chrome136")
    session.headers.update(BASE_HEADERS)
    if cookies:
        session.cookies.update(cookies)

    prefix_counter = Counter()
    prefix_examples = {}
    all_dul_numbers = set()

    for term in SEARCH_TERMS:
        if len(term.strip()) < 3:
            continue
        try:
            params = {"name": term, "page": 0, "size": 20}
            resp = session.post(SEARCH_URL, params=params, json={}, timeout=15)
            if resp.status_code != 200:
                body_preview = resp.text[:150].replace("\n", " ")
                print(f"[!] '{term}' -> HTTP {resp.status_code} | body: {body_preview}")
                time.sleep(1.0)
                continue

            data = resp.json()
            items = data.get("content") or data.get("result") or data.get("data") or data.get("items") or []
            found_this_term = 0
            for item in items:
                dul = item.get("dulNumber") or item.get("dul")
                if not dul:
                    continue
                all_dul_numbers.add(dul)
                prefix = extract_prefix(dul)
                if prefix:
                    prefix_counter[prefix] += 1
                    prefix_examples.setdefault(prefix, dul)
                    found_this_term += 1

            print(f"[*] '{term}': {found_this_term} DUL numbers, "
                  f"{len(prefix_counter)} distinct prefixes so far")

        except Exception as e:
            print(f"[!] '{term}' -> exception: {e}")

        time.sleep(1.2)

    return prefix_counter, prefix_examples, all_dul_numbers


def main():
    cookies = asyncio.run(fetch_browser_cookies())
    print(f"\n[*] Running discovery across {len(SEARCH_TERMS)} search terms...\n")
    prefix_counter, prefix_examples, all_dul_numbers = run_discovery(cookies)

    print("\n" + "=" * 60)
    print("DISCOVERED PREFIXES (sorted by frequency)")
    print("=" * 60)
    for prefix, count in prefix_counter.most_common():
        print(f"  {prefix:<6} seen {count:>3}x   example: {prefix_examples[prefix]}")

    result = {
        "discovered_prefixes": sorted(prefix_counter.keys()),
        "prefix_counts": dict(prefix_counter),
        "prefix_examples": prefix_examples,
        "sample_dul_numbers": sorted(all_dul_numbers),
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n[+] {len(prefix_counter)} distinct prefixes found across "
          f"{len(all_dul_numbers)} sampled DUL numbers.")
    print(f"[+] Saved to {OUTPUT_FILE}")
    print("\nUse this list to replace the AA-ZZ brute prefix set with only "
          "verified-real prefixes, so shards stop wasting cycles on dead "
          "letter-pairs.")


if __name__ == "__main__":
    main()
