import asyncio
import sys
sys.path.insert(0, ".")

from scan_prefixes import fetch_browser_cookies, scan_prefix

PREFIX_TO_RECHECK = "GD"  # change this to re-check any other already-abandoned prefix

if __name__ == "__main__":
    cookies = asyncio.run(fetch_browser_cookies())
    if not cookies:
        print("[!] No cookies harvested, aborting.")
        sys.exit(1)

    print(f"[*] Re-checking {PREFIX_TO_RECHECK} with the raised threshold...")
    is_real, first_hit, hits = scan_prefix(PREFIX_TO_RECHECK, cookies)
    if is_real:
        print(f"[+] {PREFIX_TO_RECHECK} IS real after all - first hit at {first_hit} "
              f"({len(hits)} hits found). The old 1500 threshold was a false negative.")
    else:
        print(f"[-] {PREFIX_TO_RECHECK} confirmed genuinely dead even with the raised "
              f"threshold - the earlier abandonment was correct.")
