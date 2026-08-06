import json
import re
from typing import Any, Dict

def _safe_filename(host: str, path: str, suffix: str = ".py") -> str:
    base = f"collector_{host}_{path}".strip("/")
    base = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_")
    return f"{base[:90]}{suffix}"

def generate_curlcffi_collector(record: Dict[str, Any]) -> str:
    """Generates a high-performance script bypassing TLS/JA3 fingerprinting."""
    host = record.get("host", "api")
    path = record.get("path", "/")
    method = (record.get("method") or "GET").upper()
    url = record.get("url", f"https://{host}{path}")
    headers = record.get("request_headers", {}) or {}
    params = record.get("query_params", {}) or {}

    return f'''"""
Generated curl_cffi Collector (Chrome TLS/JA3 Impersonation)
Target: {method} {url}
"""
import json
from pathlib import Path
from curl_cffi import requests

URL = "{url}"
METHOD = "{method}"
DEFAULT_HEADERS = {json.dumps(headers, indent=4)}
DEFAULT_PARAMS = {json.dumps(params, indent=4)}

def load_session() -> tuple[dict, dict]:
    path = Path("session.json")
    if path.exists():
        print(f"[*] Loading live session overrides from session.json...")
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("headers", DEFAULT_HEADERS), data.get("params", DEFAULT_PARAMS)
    return DEFAULT_HEADERS, DEFAULT_PARAMS

def run():
    headers, params = load_session()
    print("[*] Sending request via curl_cffi (impersonating Chrome 120)...")
    
    # impersonate="chrome120" matches real browser TLS/HTTP2 fingerprints
    response = requests.request(
        method=METHOD,
        url=URL,
        headers=headers,
        params=params,
        impersonate="chrome120",
        timeout=15
    )

    print(f"[*] Response Status: {{response.status_code}}")
    if response.status_code == 200:
        Path("extracted_data.json").write_text(response.text, encoding="utf-8")
        print("[+] Success! Saved response to extracted_data.json")
    elif response.status_code in (403, 429):
        print("[!] Blocked by bot defense.")
        print("[!] Tip: Save fresh cookies into 'session.json' or use the Playwright collector.")
    else:
        print(f"[!] Request failed: {{response.text[:200]}}")

if __name__ == "__main__":
    run()
'''

def generate_playwright_collector(record: Dict[str, Any]) -> str:
    """Generates a Playwright script to manually solve CAPTCHAs and intercept data."""
    host = record.get("host", "api")
    path = record.get("path", "/")
    url = record.get("url", f"https://{host}{path}")

    return f'''"""
Generated Playwright Collector (Real Browser Engine)
Target: {url}
"""
import json
import asyncio
from playwright.async_api import async_playwright

TARGET_URL = "{url}"

async def run():
    async with async_playwright() as p:
        print("[*] Launching Chromium (Headful mode for CAPTCHA solving)...")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        data_intercepted = []

        # Intercept backend API JSON directly from the network stream
        async def handle_response(response):
            if "{path}" in response.url and response.status == 200:
                try:
                    json_body = await response.json()
                    data_intercepted.append(json_body)
                    print(f"[+] Intercepted API data from: {{response.url}}")
                except Exception:
                    pass

        page.on("response", handle_response)
        print(f"[*] Navigating to: {{TARGET_URL}}")
        await page.goto(TARGET_URL, wait_until="networkidle")

        print("[*] If a Cloudflare/Turnstile check appears, solve it in the browser...")
        await asyncio.sleep(15)  # Wait for user interactions and data load

        if data_intercepted:
            with open("intercepted_data.json", "w", encoding="utf-8") as f:
                json.dump(data_intercepted, f, indent=2)
            print("[+] Saved intercepted payloads to intercepted_data.json")
        else:
            print("[!] No matching JSON response captured.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
'''