import time
from curl_cffi import requests
from stem import Signal
from stem.control import Controller

# --- Configuration ---
TOR_SOCKS_PROXY = "socks5h://127.0.0.1:9050"
TOR_CONTROL_PORT = 9051
# Replace this with the EXACT PLAINTEXT password you passed to --hash-password
TOR_PASSWORD = "YOUR_PLAINTEXT_PASSWORD_HERE"


def create_tor_session() -> requests.Session:
    """Creates a new curl_cffi Session that routes through Tor SOCKS5 and spoofs Chrome."""
    return requests.Session(
        impersonate="chrome124",
        proxies={"http": TOR_SOCKS_PROXY, "https": TOR_SOCKS_PROXY},
        timeout=15,
    )


def rotate_tor_circuit(password: str):
    """Sends the NEWNYM signal to Tor's Control Port to request a new circuit (IP)."""
    print("\n[*] Contacting Tor Control Port to request a new IP...")
    try:
        with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
            controller.authenticate(password=password)
            controller.signal(Signal.NEWNYM)
            print("[+] NEWNYM signal accepted.")
            print("[*] Waiting 6 seconds for Tor to establish new circuit...")
            time.sleep(6)  # Brief pause to allow circuit construction
    except Exception as e:
        print(f"[!] Tor Control Port Error: {e}")
        raise


def fetch_current_ip(session: requests.Session) -> str:
    """Fetches public IP address as seen by httpbin.org."""
    response = session.get("https://httpbin.org/ip")
    response.raise_for_status()
    return response.json().get("origin", "Unknown")


def main():
    print("=" * 60)
    print(" TOR IP ROTATION TEST (stem + curl_cffi)")
    print("=" * 60)

    # 1. First Request - Initial IP
    print("\n[1/3] Fetching initial IP...")
    session1 = create_tor_session()
    try:
        ip1 = fetch_current_ip(session1)
        print(f"      -> Initial Tor IP: {ip1}")
    except Exception as e:
        print(f"[!] Failed to connect: {e}")
        print("[!] Verify that 'tor.exe -f torrc' is currently running in your other terminal.")
        return

    # 2. Trigger Circuit Rotation
    print("\n[2/3] Triggering Tor circuit rotation...")
    try:
        rotate_tor_circuit(TOR_PASSWORD)
    except Exception:
        print("[!] Rotation failed. Check your password in TOR_PASSWORD.")
        return

    # 3. Second Request - New Session & New IP
    print("\n[3/3] Fetching post-rotation IP...")
    # NOTE: We create a fresh session object so sockets are not reused
    session2 = create_tor_session()
    try:
        ip2 = fetch_current_ip(session2)
        print(f"      -> New Tor IP:     {ip2}")

        print("\n" + "=" * 60)
        if ip1 != ip2:
            print(f"[SUCCESS] Circuit rotated successfully! ({ip1} -> {ip2})")
        else:
            print("[NOTE] IP remained unchanged. Tor enforces a ~10s rate limit on NEWNYM,")
            print("       or the random selection picked the same exit node.")
        print("=" * 60)

    except Exception as e:
        print(f"[!] Failed post-rotation check: {e}")


if __name__ == "__main__":
    main()