from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("https://dmcc.ae/public-register", timeout=30000)
    input("Press enter to close...")
    browser.close()