from playwright.sync_api import sync_playwright
import time

URL = "https://dmcc.ae/public-register"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(6)

    sf_frame = None
    for f in page.frames:
        if "salesforce-sites.com" in f.url:
            sf_frame = f
            break

    if sf_frame is None:
        print("Salesforce iframe not found!")
    else:
        print(f"Found Salesforce frame: {sf_frame.url}")
        try:
            sf_frame.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"wait_for_load_state error: {e}")

        time.sleep(3)

        inputs = sf_frame.locator("input")
        n = inputs.count()
        print(f"\nFound {n} input(s) inside Salesforce iframe:")
        for i in range(n):
            el = inputs.nth(i)
            try:
                print(f"  [{i}] id={el.get_attribute('id')!r} name={el.get_attribute('name')!r} "
                      f"type={el.get_attribute('type')!r} placeholder={el.get_attribute('placeholder')!r} "
                      f"aria-label={el.get_attribute('aria-label')!r}")
            except Exception as e:
                print(f"  [{i}] error: {e}")

        buttons = sf_frame.locator("button")
        n_btn = buttons.count()
        print(f"\nFound {n_btn} button(s) inside Salesforce iframe:")
        for i in range(n_btn):
            el = buttons.nth(i)
            try:
                print(f"  [{i}] text={el.inner_text().strip()!r} id={el.get_attribute('id')!r}")
            except Exception as e:
                print(f"  [{i}] error: {e}")

    input("\nPress ENTER to close...")
    browser.close()