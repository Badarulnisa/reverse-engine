"""
Shared environment & proxy configuration for Playwright scripts.
Includes proxy rotation, dynamic User-Agent injection, and retry logic.
"""

import os
import time
import random
import logging
from dataclasses import dataclass, field
from typing import Optional, List

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip().strip('"').strip("'")

USE_PROXY = os.environ.get("USE_PROXY", "false").lower() == "true"
_ENV_FILE = ".env.proxy" if USE_PROXY else ".env.local"
_load_dotenv(_ENV_FILE)

@dataclass
class ProxyConfig:
    server_list: List[str] = field(default_factory=lambda: [s.strip() for s in os.environ.get("PROXY_SERVERS", os.environ.get("PROXY_SERVER", "")).split(",") if s.strip()])
    username: Optional[str] = field(default_factory=lambda: os.environ.get("PROXY_USERNAME"))
    password: Optional[str] = field(default_factory=lambda: os.environ.get("PROXY_PASSWORD"))
    bypass: Optional[str] = field(default_factory=lambda: os.environ.get("PROXY_BYPASS"))

    @property
    def enabled(self) -> bool:
        return len(self.server_list) > 0

    def get_random_proxy_dict(self) -> Optional[dict]:
        if not self.enabled:
            return None
        server = random.choice(self.server_list)
        cfg = {"server": server}
        if self.username:
            cfg["username"] = self.username
        if self.password:
            cfg["password"] = self.password
        if self.bypass:
            cfg["bypass"] = self.bypass
        return cfg

@dataclass
class EnvironmentConfig:
    viewport_width: int = int(os.environ.get("VIEWPORT_WIDTH", 1366))
    viewport_height: int = int(os.environ.get("VIEWPORT_HEIGHT", 768))
    locale: str = os.environ.get("LOCALE", "en-US")
    timezone_id: str = os.environ.get("TIMEZONE_ID", "America/New_York")
    headless: bool = os.environ.get("HEADLESS", "true").lower() != "false"

    user_agents: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    ])

    @property
    def viewport(self) -> dict:
        return {"width": self.viewport_width, "height": self.viewport_height}

    def get_random_ua(self) -> str:
        return random.choice(self.user_agents)

# Trimmed: removed --disable-blink-features=AutomationControlled and other
# fingerprint-patching flags. On DMCC's site these made reCAPTCHA MORE
# suspicious rather than less. Plain Chromium with no flags passed cleanly.
CHROMIUM_LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
]

def build_browser(playwright, env_cfg: EnvironmentConfig, proxy_cfg: ProxyConfig):
    launch_kwargs = {
        "headless": env_cfg.headless,
        "args": CHROMIUM_LAUNCH_ARGS,
    }
    active_proxy = proxy_cfg.get_random_proxy_dict()
    if active_proxy:
        launch_kwargs["proxy"] = active_proxy
        logger.info(f"Launching Chromium with proxy: {active_proxy['server']}")

    return playwright.chromium.launch(**launch_kwargs)

def build_context(browser, env_cfg: EnvironmentConfig, **extra_context_kwargs):
    return browser.new_context(
        viewport=env_cfg.viewport,
        locale=env_cfg.locale,
        timezone_id=env_cfg.timezone_id,
        user_agent=env_cfg.get_random_ua(),
        device_scale_factor=1,
        is_mobile=False,
        **extra_context_kwargs,
    )

def goto_with_retry(page, url: str, max_attempts: int = 4, base_delay: float = 1.0, max_delay: float = 20.0, wait_until: str = "domcontentloaded", timeout_ms: int = 30_000):
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1))) + random.uniform(0, 2.0)
            logger.warning(f"Navigation to {url} failed. Retrying in {delay:.1f}s...")
            time.sleep(delay)
    raise RuntimeError(f"Failed to load {url} after {max_attempts} attempts") from last_exc