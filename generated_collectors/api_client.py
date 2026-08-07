import os
import time
from dotenv import load_dotenv
from curl_cffi import requests
from curl_cffi.requests.errors import RequestsError
from stem import Signal
from stem.control import Controller

load_dotenv()

class RPCClient:
    """API client with built-in proxy support, Chrome TLS impersonation, and automatic Tor cookie IP rotation."""

    def __init__(
        self,
        endpoint_url: str,
        csrf_token: str,
        jwt_token: str,
        token_refresh_func=None,
        proxy_url: str = None,
        tor_control_port: int = 9051,
    ):
        self.endpoint_url = endpoint_url
        self.csrf_token = csrf_token
        self.jwt_token = jwt_token
        self.token_refresh_func = token_refresh_func
        self.proxy_url = proxy_url
        self.tor_control_port = tor_control_port
        
        self._create_session()

    def _create_session(self):
        """Creates or recreates the curl_cffi Session object."""
        self.session = requests.Session(impersonate="chrome124")
        if self.proxy_url:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
        self._update_headers()

    def _update_headers(self):
        """Synchronizes session headers with current tokens."""
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "X-CSRF-Token": self.csrf_token,
                "Authorization": f"Bearer {self.jwt_token}",
            }
        )

    def update_credentials(self, csrf_token: str, jwt_token: str):
        """Updates active session credentials when tokens are refreshed."""
        self.csrf_token = csrf_token
        self.jwt_token = jwt_token
        self._update_headers()

    def _rotate_tor_ip(self):
        """Requests a new Tor circuit via cookie authentication and recreates the session."""
        print("\n[*] Block detected. Contacting Tor Control Port for a new IP...")
        try:
            with Controller.from_port(port=self.tor_control_port) as controller:
                controller.authenticate()
                controller.signal(Signal.NEWNYM)
                print("[+] NEWNYM signal sent. Rebuilding circuit...")
                time.sleep(6)  
            
            self._create_session()
            print("[+] Session rebuilt with new Tor IP.")
        except Exception as e:
            print(f"[!] Warning: Failed to rotate Tor IP via Control Port: {e}")

    def make_request(self, payload: dict) -> dict:
        """Executes API request with IP rotation and exponential backoff."""
        max_retries = 4
        backoff_factor = 2

        for attempt in range(max_retries + 1):
            try:
                response = self.session.post(
                    self.endpoint_url, json=payload, timeout=15
                )
                
                if response.status_code in [403, 429]:
                    if attempt < max_retries:
                        print(f"[!] Server returned {response.status_code} (Likely blocked).")
                        self._rotate_tor_ip()
                        continue

                elif response.status_code in [500, 502, 503, 504]:
                    if attempt < max_retries:
                        sleep_time = backoff_factor ** attempt
                        print(f"[!] Server returned {response.status_code}. Retrying in {sleep_time}s...")
                        time.sleep(sleep_time)
                        continue
                
                response.raise_for_status()
                return response.json()

            except RequestsError as e:
                if attempt < max_retries:
                    sleep_time = backoff_factor ** attempt
                    print(f"[!] Network error: {e}. Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                else:
                    print(f"[!] Request failed after {max_retries} retries.")
                    raise