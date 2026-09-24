"""
os_level_dul_discovery.py

The most "human" automation tier we have left. Browser-level automation
(Playwright/Patchright) still leaves some CDP-protocol traces even when
patched. This script removes that entirely for the INTERACTION side:

  - Chrome is launched as a completely normal process (subprocess, same
    as double-clicking the icon) - not "attached" as an automation
    target.
  - All clicking and typing goes through pyautogui, which sends real
    OS-level input events (Windows SendInput) - indistinguishable from
    you physically using the mouse/keyboard, because it IS that.
  - A separate, PASSIVE CDP connection (via patchright/playwright's
    connect_over_cdp) is used ONLY to read the network response after
    the fact - it never calls page.click(), page.fill(), or anything
    that dispatches synthetic input through the automation protocol.
    This still uses Chrome's --remote-debugging-port flag, which is a
    detectable signal in principle, but far less commonly checked than
    active CDP command traffic (Runtime.enable, Input.dispatchKeyEvent,
    etc.), since here it's just listening, not driving.

HARD REQUIREMENTS / TRADEOFFS (be realistic about these):
  - Your screen must be visible and this window focused/on top the
    entire time it runs. It cannot run headless or in the background.
  - You cannot use the mouse/keyboard for anything else while it runs -
    pyautogui is moving your REAL cursor.
  - Coordinates are calibrated once per screen/window layout (below) -
    if you move or resize the Chrome window afterward, recalibrate.
  - IP rotation here is NOT via Tor - Tor exits are already blacklisted
    on this exact site (confirmed earlier). Use a VPN app's server
    switch instead; this script will pause periodically and prompt you
    to switch servers manually, since VPN CLI control is app-specific
    and not something I can wire in generically.

Setup:
    pip install pyautogui patchright pyperclip
    patchright install chromium
    (patchright is used ONLY for the passive CDP read - the actual
    clicking/typing never goes through it)

First run will walk you through a one-time coordinate calibration.

Usage:
    python os_level_dul_discovery.py --candidates candidates.txt
"""

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime

import pyautogui
import pyperclip

if sys.platform == "win32":
    try:
        import ctypes
        # SetProcessDPIAware() (the older API) doesn't handle per-monitor
        # scaling correctly on modern Windows setups - this is the
        # correct modern call for that case. If this fails, falls back
        # to the older API as better-than-nothing.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception as e:
        print(f"[!] Could not set DPI awareness ({e}) - if clicks land in "
              "the wrong place, check Windows display scaling (Settings > "
              "System > Display > Scale) and try setting it to 100% for "
              "this session.")

try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from visitdubai_bruteforce import parse_visitdubai_dul  # noqa: E402

SEARCH_URL = "https://www.investindubai.gov.ae/en/dubai-business-directory-search"
REMOTE_DEBUG_PORT = 9333
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_DIR = os.path.abspath("chrome_os_profile")

CALIBRATION_FILE = "os_calibration.json"
HITS_FILE = "os_discovery_hits.jsonl"
SKIPPED_FILE = "os_discovery_skipped.txt"
EMPTY_FILE = "os_discovery_empty.txt"
STATE_FILE = "os_discovery_state.json"
MASTER_JSON = "os_discovery_master.json"
MASTER_XLSX = "os_discovery_master.xlsx"

# Default reminder interval - overridable via --vpn-switch-every


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
    if not records:
        return
    import pandas as pd
    df = pd.DataFrame(records)

    tmp_json = MASTER_JSON + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    os.replace(tmp_json, MASTER_JSON)

    tmp_xlsx = MASTER_XLSX.rsplit(".", 1)[0] + ".tmp.xlsx"
    df.to_excel(tmp_xlsx, index=False)
    os.replace(tmp_xlsx, MASTER_XLSX)


def calibrate():
    """One-time setup: have the user hover over the real search box and
    the DUL-number dropdown option, confirming from the terminal, so we
    capture real screen coordinates pyautogui will click/type into.
    Re-run this (delete CALIBRATION_FILE) if the window moves, resizes,
    or the page layout changes.

    IMPORTANT: close every other Chrome window/process first. If a
    Chrome window using this profile is already running, our
    --window-position/--window-size launch flags get silently ignored
    (Chrome just opens a new tab in the existing window instead), so
    the window ends up somewhere unpredictable and every calibrated
    coordinate will be wrong."""
    print("\n=== CALIBRATION ===")
    print("Make sure Chrome is open on the search page, positioned where")
    print("it will stay for the whole run.")
    input("\nIMPORTANT: if a cookie-consent banner is showing at the bottom "
          "of the page, dismiss/accept it FIRST (it shifts the page layout "
          "and will throw off every coordinate below). Once it's gone, "
          "press Enter here...")

    def capture_point(prompt):
        while True:
            input(f"\n{prompt}\nThen press Enter here...")
            pos = pyautogui.position()
            print(f"  Captured: {pos}")
            confirm = input("  Correct? (Enter to accept, 'r' to retry): ").strip().lower()
            if confirm != "r":
                return pos

    dropdown_pos = capture_point(
        "Hover your mouse over the SEARCH TYPE DROPDOWN (currently showing "
        "'Business name' or 'DUL number')."
    )
    dul_option_pos = capture_point(
        "Click the dropdown open yourself so 'DUL number' is visible as an "
        "option, then hover over the 'DUL number' OPTION ITSELF (don't "
        "click it yet)."
    )
    search_pos = capture_point(
        "Hover your mouse over the SEARCH BOX itself."
    )

    calibration = {
        "search_box": list(search_pos),
        "type_dropdown": list(dropdown_pos),
        "dul_number_option": list(dul_option_pos),
    }
    with open(CALIBRATION_FILE, "w") as f:
        json.dump(calibration, f)
    print(f"[+] Saved to {CALIBRATION_FILE}\n")
    return calibration


def load_calibration():
    if os.path.exists(CALIBRATION_FILE):
        with open(CALIBRATION_FILE) as f:
            return json.load(f)
    return calibrate()


def minimize_console_window():
    """The terminal stays on top after every input() prompt, which was
    covering the browser at the exact coordinates we click - explains a
    lot of today's 'coordinates look right but nothing works' failures.
    Minimize it right before automation starts so the browser is
    actually on top and reachable. Returns True if it actually minimized."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            time.sleep(0.3)
            # 2 = SW_SHOWMINIMIZED state check via GetWindowPlacement is
            # more reliable but heavier; IsIconic is the simple direct
            # check for "is this window currently minimized".
            return bool(ctypes.windll.user32.IsIconic(hwnd))
    except Exception:
        pass
    return False


def check_no_chrome_running():
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
            capture_output=True, text=True, timeout=5,
        )
        if "chrome.exe" in result.stdout:
            print("\n[!] WARNING: Chrome is already running. If it's using this "
                  f"script's profile ({PROFILE_DIR}), our window-size/position "
                  "flags will be silently ignored and every calibrated click "
                  "will land in the wrong place.")
            print("    Close ALL Chrome windows (check Task Manager for "
                  "lingering chrome.exe processes too), then re-run this script.")
            resp = input("    Continue anyway? (y/N): ").strip().lower()
            if resp != "y":
                sys.exit(0)
    except Exception:
        pass  # tasklist not available or failed - not fatal, just skip the check


def launch_chrome():
    """Launches Chrome as a completely ordinary process - same as
    double-clicking the icon. --remote-debugging-port is present so we
    can passively read network traffic, but nothing here attaches an
    automation controller to the window itself."""
    args = [
        CHROME_PATH,
        f"--remote-debugging-port={REMOTE_DEBUG_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--window-position=0,0",
        "--window-size=1400,900",
        "--new-window",
        SEARCH_URL,
    ]
    print("[*] Launching Chrome normally (not automation-attached)...")
    proc = subprocess.Popen(args)

    # Poll the actual CDP endpoint instead of guessing a fixed delay -
    # a fresh profile cold-starts at an unpredictable speed, and a fixed
    # sleep either wastes time or (worse) isn't long enough, letting the
    # script try to connect before Chrome is truly ready.
    import urllib.request
    print("[*] Waiting for Chrome to actually be ready...")
    for attempt in range(60):  # up to ~30s
        if proc.poll() is not None:
            raise RuntimeError(
                f"Chrome process exited immediately (return code {proc.returncode}) "
                "- it crashed on startup rather than opening. Try deleting the "
                f"{PROFILE_DIR} folder (a corrupted profile can cause this) and "
                "run again."
            )
        try:
            urllib.request.urlopen(f"http://localhost:{REMOTE_DEBUG_PORT}/json/version", timeout=1)
            print(f"[*] Chrome ready after {attempt * 0.5:.1f}s.")
            return proc
        except Exception:
            time.sleep(0.5)

    raise RuntimeError(
        "Chrome's remote debugging port never became responsive after 30s. "
        "Check that Chrome actually opened a window and isn't stuck on a "
        "crash-recovery dialog."
    )


def human_type(text):
    """Real OS-level keystrokes via pyautogui, with per-character jitter -
    not synthetic CDP input events."""
    for ch in text:
        pyautogui.typewrite(ch, interval=0)
        time.sleep(random.uniform(0.05, 0.18))


def human_click(pos):
    """Moves the real cursor in a slightly curved multi-step path rather
    than teleporting, then clicks - closer to real mouse movement."""
    x, y = pos
    screen_w, screen_h = pyautogui.size()
    # Safety clamp: refuse an obviously-wrong coordinate (e.g. a scaling
    # math bug sending a click far outside the window, potentially onto
    # something like a window's own close button) rather than blindly
    # clicking it and risking closing Chrome or clicking who-knows-what.
    if not (0 <= x <= screen_w and 0 <= y <= screen_h):
        raise RuntimeError(
            f"Computed click position {pos} is outside the actual screen "
            f"bounds ({screen_w}x{screen_h}) - refusing to click. This "
            "means the coordinate math is still wrong, not a page issue."
        )
    start = pyautogui.position()
    steps = random.randint(8, 15)
    for i in range(1, steps + 1):
        t = i / steps
        xi = start[0] + (x - start[0]) * t + random.randint(-3, 3)
        yi = start[1] + (y - start[1]) * t + random.randint(-3, 3)
        pyautogui.moveTo(xi, yi, duration=0)
        time.sleep(random.uniform(0.01, 0.03))
    pyautogui.moveTo(x, y, duration=0)
    pyautogui.click()


async def get_element_screen_position(page, selector, x_bias=0.5):
    """Asks the real DOM for an element's exact position, then converts
    to absolute screen coordinates - removes manual calibration/DPI
    mismatch entirely, since this is computed fresh every run instead of
    eyeballed once. Uses the already-open passive CDP connection - a
    one-time bounding-box read, not per-search driven input.

    x_bias controls where along the element's width to target: 0.5 is
    dead-center (the default), 0.0 is the far left edge, 1.0 the far
    right. Useful for the search box specifically - when an hCaptcha
    challenge overlay appears, it covers the center/right portion of
    the bar but not the far-left edge near the search icon (confirmed
    from a real run), so clicking there instead avoids accidentally
    hitting the overlay's own UI (its language badge landed exactly at
    the bar's horizontal center in one observed case)."""
    box = await page.locator(selector).bounding_box()
    if box is None:
        raise RuntimeError(f"Could not find element for selector: {selector}")

    window_info = await page.evaluate("""
        () => ({
            screenX: window.screenX,
            screenY: window.screenY,
            outerHeight: window.outerHeight,
            innerHeight: window.innerHeight,
            outerWidth: window.outerWidth,
            innerWidth: window.innerWidth,
            dpr: window.devicePixelRatio,
        })
    """)
    chrome_height = window_info["outerHeight"] - window_info["innerHeight"]
    chrome_width = (window_info["outerWidth"] - window_info["innerWidth"]) / 2
    dpr = window_info["dpr"] or 1.0

    # window.screenX/Y, outerHeight/innerHeight, and bounding_box() are
    # all reported in CSS pixels by Chrome. But since the Python process
    # is DPI-aware (needed so pyautogui's click coordinates match real
    # screen pixels), pyautogui operates in PHYSICAL pixels. On a scaled
    # display (125%/150% - the default on most laptops) these two
    # spaces don't match, and the gap grows the further down/right the
    # element is - which matches the "crosshair consistently lands
    # above the real box, worse for further-down elements" pattern
    # across every debug screenshot so far. Multiplying by dpr converts
    # CSS pixels to physical pixels before handing off to pyautogui.
    css_x = window_info["screenX"] + chrome_width + box["x"] + box["width"] * x_bias
    css_y = window_info["screenY"] + chrome_height + box["y"] + box["height"] / 2
    screen_x = css_x * dpr
    screen_y = css_y * dpr
    print(f"    [debug] devicePixelRatio={dpr}, css=({css_x:.0f},{css_y:.0f}), "
          f"physical=({screen_x:.0f},{screen_y:.0f})")
    return (int(screen_x), int(screen_y))


async def passive_response_listener(response_holder):
    """Connects to the already-running Chrome over CDP in READ-ONLY mode -
    only registers a response listener, never sends input/navigation
    commands through this connection."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{REMOTE_DEBUG_PORT}")
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            response_holder["page"] = page
            response_holder["ready"].set()

            def on_response(response):
                if "/api/dul/Search" in response.url and response.request.method == "POST":
                    # Read the body IMMEDIATELY, inside this callback, not
                    # later in the main loop after waiting on an event.
                    # This SPA updates the URL via client-side routing on
                    # every search, and by the time the main loop got
                    # around to calling resp.json() later, that routing
                    # had already invalidated the response's body buffer
                    # in some cases ("Response body is not available for
                    # a response that was navigated away from") - losing
                    # genuine hits to a parse error even though the data
                    # had actually arrived. Scheduling the read as a task
                    # right when the event fires reads it as close to
                    # instantly as possible, before that can happen.
                    async def read_body():
                        try:
                            data = await response.json()
                            response_holder["data"] = data
                        except Exception as e:
                            response_holder["parse_error"] = str(e)
                        response_holder["event"].set()

                    asyncio.create_task(read_body())

            page.on("response", on_response)

            while not response_holder.get("stop"):
                await asyncio.sleep(0.5)

            await browser.close()
    except Exception as e:
        response_holder["connect_error"] = str(e)
        response_holder["ready"].set()  # unblock the waiter so the error surfaces


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--vpn-switch-every", type=int, default=200,
                     help="print a (non-blocking) reminder to switch VPN "
                          "server every N candidates - default 200, since "
                          "no generic way exists to switch a browser-"
                          "extension VPN's server programmatically")
    args = ap.parse_args()

    if args.recalibrate and os.path.exists(CALIBRATION_FILE):
        os.remove(CALIBRATION_FILE)

    with open(args.candidates) as f:
        candidates = [line.strip() for line in f if line.strip()]

    state = load_state()
    done = set(state["done"])
    remaining = [c for c in candidates if c not in done]
    print(f"[*] {len(remaining)} candidates remaining ({len(done)} already processed).")
    if not remaining:
        return

    check_no_chrome_running()
    launch_chrome()

    async def run_async():
        response_holder = {
            "stop": False,
            "event": asyncio.Event(),
            "ready": asyncio.Event(),
        }
        listener_task = asyncio.create_task(passive_response_listener(response_holder))

        print("[*] Waiting for Chrome's remote debugging connection...")
        try:
            await asyncio.wait_for(response_holder["ready"].wait(), timeout=30)
        except asyncio.TimeoutError:
            print("[!] Timed out waiting for Chrome to be ready for the CDP "
                  "connection after 30s. Is Chrome actually open on the search "
                  "page? Try closing everything and running again.")
            response_holder["stop"] = True
            return

        if "connect_error" in response_holder:
            print(f"[!] Could not connect to Chrome's remote debugging port: "
                  f"{response_holder['connect_error']}")
            print("    Common causes: Chrome didn't finish starting up yet "
                  "(try again), another process is using the debug port, or "
                  "a firewall/antivirus is blocking localhost connections.")
            return

        page = response_holder["page"]

        print("[*] Reading exact element positions from the real page "
              "(no manual calibration needed)...")

        # Switch to "DUL number" mode if not already selected - persists
        # for the rest of the session once set.
        try:
            current_mode = await page.locator('#dul-search-input').get_attribute("placeholder")
        except Exception:
            current_mode = None

        if current_mode != "Enter Dubai Unified Licence number":
            print("[*] Search is not in 'DUL number' mode yet.")
            try:
                dropdown_pos = await get_element_screen_position(
                    page, 'div:has-text("Business name") >> nth=0'
                )
                human_click(dropdown_pos)
                await asyncio.sleep(random.uniform(0.5, 0.9))
                option_pos = await get_element_screen_position(page, 'text="DUL number"')
                human_click(option_pos)
                await asyncio.sleep(random.uniform(0.6, 1.0))
            except Exception as e:
                print(f"[!] Couldn't auto-switch mode ({e}).")
                input("    Please click the dropdown and select 'DUL number' "
                      "yourself now, then press Enter here to continue...")

        search_box = await get_element_screen_position(page, "#dul-search-input", x_bias=0.08)
        print(f"    Search box (one-time sanity check): {search_box}")
        print("    (Every actual search re-locates this fresh, so a small "
              "shift afterward - e.g. a banner appearing/disappearing - "
              "won't cause stale clicks.)")

        # Verify visually before touching anything - draws a red crosshair
        # exactly where the script thinks the search box is, on top of a
        # real screenshot, and shows it to you before any clicking starts.
        # If the marker isn't on the search box, something is still off
        # (scaling, wrong monitor, etc.) and you can stop before wasting
        # a run on wrong clicks.
        try:
            from PIL import ImageDraw
            shot = pyautogui.screenshot()
            draw = ImageDraw.Draw(shot)
            x, y = search_box
            r = 15
            draw.line((x - r, y, x + r, y), fill="red", width=4)
            draw.line((x, y - r, x, y + r), fill="red", width=4)
            draw.ellipse((x - r, y - r, x + r, y + r), outline="red", width=4)
            debug_path = os.path.abspath("os_click_target_debug.png")
            shot.save(debug_path)
            print(f"\n[*] Saved {debug_path} - a screenshot with a red "
                  "crosshair marking exactly where the script will click.")
            print("    Open that file NOW and check the crosshair is "
                  "actually on the search box before continuing.")
        except Exception as e:
            print(f"[!] Could not save debug screenshot: {e}")

        try:
            input("\nIf the crosshair looks correct, press Enter to continue. "
                  "If not, Ctrl+C now and tell me what you see...")
        except EOFError:
            print("\n[!] Could not read confirmation input (EOF) - "
                  "continuing anyway since a crosshair was already saved for you to check.")

        print("[*] Minimizing this terminal so it's not covering the browser...")
        minimized_ok = minimize_console_window()
        if not minimized_ok:
            print("[!] Could not confirm the terminal minimized - if clicks "
                  "still land in the wrong place, manually minimize this "
                  "window yourself right now (a few seconds' grace period follows).")
        await asyncio.sleep(1.5)

        print("[!] Starting - controlling your real cursor now.")

        hits = empty = challenged = errors = 0
        flattened_records = []
        if os.path.exists(MASTER_JSON):
            with open(MASTER_JSON, encoding="utf-8") as f:
                flattened_records = json.load(f)

        try:
            for i, dul in enumerate(remaining):
                response_holder["data"] = None
                response_holder["parse_error"] = None
                response_holder["event"].clear()

                # Fresh lookup every single search, not a cached position -
                # any transient layout shift (a restore-session banner, a
                # popup, anything) can otherwise leave us clicking a stale
                # coordinate. This asks the live DOM right before each click.
                try:
                    search_box = await get_element_screen_position(page, "#dul-search-input", x_bias=0.08)
                except Exception as e:
                    print(f"\n[!] {dul}: could not locate search box this round ({e}) - skipping.")
                    errors += 1
                    continue

                human_click(search_box)

                # Verify the click actually landed on the real input before
                # doing anything else. If a challenge overlay is covering
                # it, the click can land on the overlay instead, leaving
                # focus on the page body - Ctrl+A there selects the WHOLE
                # PAGE instead of the search box's text (confirmed via a
                # real screenshot). Checking this first avoids that
                # entirely: if focus isn't where we expect, treat this as
                # a skip rather than blindly typing into the wrong place.
                try:
                    focus_ok = await page.evaluate(
                        "() => document.activeElement && document.activeElement.id === 'dul-search-input'"
                    )
                except Exception:
                    focus_ok = False

                if not focus_ok:
                    append_line(SKIPPED_FILE, dul)
                    challenged += 1
                    done.add(dul)
                    state["done"] = sorted(done)
                    save_state(state)
                    print(f"\r[Discovery] hits={hits} empty={empty} challenged={challenged} "
                          f"errors={errors} | last={dul} (click missed - likely a challenge overlay)",
                          end="", flush=True)
                    time.sleep(random.uniform(2.0, 4.0))
                    continue

                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("backspace")
                time.sleep(random.uniform(0.2, 0.4))
                human_type(dul)
                time.sleep(random.uniform(0.3, 0.6))
                pyautogui.press("enter")

                # Scroll down a little via real OS input (mouse wheel),
                # not a synthetic CDP scroll command - matches the same
                # "everything through real input" principle as the rest.
                # Purely cosmetic (so results are visible if you're
                # watching), doesn't affect data collection.
                time.sleep(random.uniform(0.5, 1.0))
                pyautogui.scroll(-250)

                try:
                    await asyncio.wait_for(response_holder["event"].wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass

                resp_data = response_holder.get("data")
                parse_error = response_holder.get("parse_error")
                if resp_data is None and parse_error is None:
                    append_line(SKIPPED_FILE, dul)
                    challenged += 1
                elif parse_error is not None:
                    print(f"\n[!] {dul}: response parse error: {parse_error}")
                    errors += 1
                    print(f"\r[Discovery] hits={hits} empty={empty} "
                          f"challenged={challenged} errors={errors} | last={dul}",
                          end="", flush=True)
                    continue
                else:
                    data = resp_data
                    license_data = data.get("license")

                    if license_data is None:
                        append_line(EMPTY_FILE, dul)
                        empty += 1
                    elif isinstance(license_data, list) and license_data:
                        record = license_data[0]
                        append_line(HITS_FILE, json.dumps({
                            "dul": dul, "record": record, "ts": datetime.now().isoformat()
                        }))
                        flattened = parse_visitdubai_dul(record, identifier=dul)
                        if flattened:
                            flattened_records.append(flattened)
                            if len(flattened_records) % 5 == 0:
                                save_master_files(flattened_records)
                        hits += 1
                    else:
                        append_line(EMPTY_FILE, dul)
                        empty += 1

                done.add(dul)
                state["done"] = sorted(done)
                save_state(state)

                print(f"\r[Discovery] hits={hits} empty={empty} challenged={challenged} "
                      f"errors={errors} | last={dul}", end="", flush=True)

                if (i + 1) % args.vpn_switch_every == 0:
                    # Non-blocking reminder - doesn't stall an unattended
                    # run waiting for you to press Enter. Switch whenever
                    # you notice it; the script keeps going regardless,
                    # since there's no generic way to control a browser-
                    # extension VPN's server choice programmatically.
                    print(f"\n[!] {args.vpn_switch_every} candidates done - "
                          "consider switching your VPN server for a fresh IP "
                          "(not required, just a reminder - continuing now).")

                time.sleep(random.uniform(7.0, 12.0))
        finally:
            # Guarantees the master files reflect everything collected so
            # far even on Ctrl+C or a crash - previously this only ran
            # after the loop finished normally, so an interrupt could
            # leave up to 4 recent hits out of the xlsx/json summary
            # (though never out of the raw hits.jsonl append-log, which
            # is safe regardless).
            save_master_files(flattened_records)
            response_holder["stop"] = True

        print(f"\n[+] Done. hits={hits} empty={empty} challenged(skipped)={challenged} errors={errors}")
        print(f"[+] Master output: {MASTER_JSON} / {MASTER_XLSX}")
        print(f"[+] Skipped (retry later): {SKIPPED_FILE}")

    asyncio.run(run_async())


if __name__ == "__main__":
    main()
