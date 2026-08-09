from playwright.sync_api import sync_playwright
import time

URL = "https://dmcc.ae/public-register"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(8)

    frame = page.frame_locator("iframe[src*='salesforce-sites.com']")
    frame.locator("#customerName").fill("A")

    print("Please solve the CAPTCHA now, then press ENTER here.")
    input()

    frame.locator("button:has-text('Search')").click()
    time.sleep(4)

    print("Inspecting result card structure...")
    # DMCC results appeared as pink-header cards in your screenshot, not classic <table> rows.
    # Let's find the actual repeating container.
    candidates = [
        ".slds-card", "[class*='card']", "[class*='result']",
        "div[class*='Result']", "table tr", ".slds-table tr"
    ]
    for sel in candidates:
        count = frame.locator(sel).count()
        print(f"  selector {sel!r}: {count} matches")

    input("\nPress ENTER to close...")
    browser.close()