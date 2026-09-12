import time
import random
import threading
import json
import os
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class AdvancedScraperEngine:
    def __init__(self, config):
        self.target_name = config.get("target_name", "generic_target")
        self.base_url = config.get("base_url")
        self.output_file = config.get("output_file", "scraped_data.xlsx")
        self.state_file = config.get("state_file", "scraper_state.json")
        self.max_workers = config.get("max_workers", 5)
        
        # Feature 3: AutoThrottle configuration
        self.base_delay_min, self.base_delay_max = config.get("delay_range", (0.3, 0.8))
        self.current_delay_min = self.base_delay_min
        self.current_delay_max = self.base_delay_max
        
        # Feature 4: Proxy Rotation configuration
        self.proxies = config.get("proxies", [])
        self.proxy_index = 0
        
        self.checkpoint_interval = config.get("checkpoint_interval", 20)
        self.lock = threading.Lock()
        
        # Feature 5: Pause & Resume persistence structures
        self.seen_identifiers = set()
        self.scraped_records = []
        self.stats = {
            "requests_sent": 0,
            "success": 0,
            "failed": 0,
            "duplicates_skipped": 0,
            "start_time": None
        }
        
        self.session = self._init_session()
        self._load_state()

    def _init_session(self):
        session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9"
        })
        return session

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    self.seen_identifiers = set(data.get("seen_identifiers", []))
                    print(f"[*] Resumed state: Loaded {len(self.seen_identifiers)} previously processed identifiers.")
            except Exception:
                pass

    def _save_state(self):
        with self.lock:
            try:
                with open(self.state_file, "w") as f:
                    json.dump({"seen_identifiers": list(self.seen_identifiers)}, f)
            except Exception:
                pass

    def _get_next_proxy(self):
        if not self.proxies:
            return None
        with self.lock:
            proxy = self.proxies[self.proxy_index % len(self.proxies)]
            self.proxy_index += 1
        return {"http": proxy, "https": proxy}

    def _adjust_throttle(self, is_success, status_code=200):
        with self.lock:
            if is_success and status_code == 200:
                self.current_delay_min = max(self.base_delay_min, self.current_delay_min * 0.95)
                self.current_delay_max = max(self.base_delay_max, self.current_delay_max * 0.95)
            else:
                backoff_multiplier = 2.0 if status_code == 429 else 1.4
                self.current_delay_min = min(5.0, self.current_delay_min * backoff_multiplier)
                self.current_delay_max = min(10.0, self.current_delay_max * backoff_multiplier)

    def fetch(self, identifier, parser_func):
        with self.lock:
            if identifier in self.seen_identifiers:
                self.stats["duplicates_skipped"] += 1
                return None

        url = self.base_url.format(id=identifier)
        proxy = self._get_next_proxy()

        try:
            with self.lock:
                self.stats["requests_sent"] += 1

            with self.lock:
                sleep_time = random.uniform(self.current_delay_min, self.current_delay_max)
            time.sleep(sleep_time)

            response = self.session.get(url, proxies=proxy, timeout=10)
            
            if response.status_code == 200:
                json_data = response.json()
                parsed_data = parser_func(json_data, identifier) if "identifier" in parser_func.__code__.co_varnames else parser_func(json_data)
                
                if parsed_data:
                    self._adjust_throttle(is_success=True, status_code=200)
                    with self.lock:
                        if identifier in self.seen_identifiers:
                            self.stats["duplicates_skipped"] += 1
                            return None
                        
                        self.seen_identifiers.add(identifier)
                        self.scraped_records.append(parsed_data)
                        self.stats["success"] += 1
                        
                        if len(self.scraped_records) % self.checkpoint_interval == 0:
                            self._save_output_unlocked(is_checkpoint=True)
                            self._save_state()
                    return parsed_data
                else:
                    self._adjust_throttle(is_success=False, status_code=200)
                    with self.lock:
                        self.stats["failed"] += 1
                    return None
            else:
                self._adjust_throttle(is_success=False, status_code=response.status_code)
                with self.lock:
                    self.stats["failed"] += 1
                return None
        except Exception as e:
            self._adjust_throttle(is_success=False, status_code=500)
            with self.lock:
                self.stats["failed"] += 1
            return None

    def render_dashboard(self, current_id):
        with self.lock:
            elapsed = datetime.now() - self.stats["start_time"]
            print(
                f"\r[Engine] Target: {self.target_name} | "
                f"Reqs: {self.stats['requests_sent']} | "
                f"Success: {self.stats['success']} | "
                f"Failed: {self.stats['failed']} | "
                f"Dupes: {self.stats['duplicates_skipped']} | "
                f"Delay: {self.current_delay_min:.2f}s | "
                f"Time: {str(elapsed).split('.')[0]} | "
                f"Current: {current_id:<10}", 
                end="", flush=True
            )

    def _save_output_unlocked(self, is_checkpoint=False):
        if self.scraped_records:
            df = pd.DataFrame(self.scraped_records)
            df.to_excel(self.output_file, index=False)
            if not is_checkpoint:
                print(f"\n[+] Exported {len(self.scraped_records)} unique records to {self.output_file}")

    def save_output(self):
        with self.lock:
            self._save_output_unlocked(is_checkpoint=False)
            self._save_state()

    def run(self, identifier_list, parser_func):
        print(f"[*] Initializing scraping run for: {self.target_name}")
        self.stats["start_time"] = datetime.now()

        pending_identifiers = [ident for ident in identifier_list if ident not in self.seen_identifiers]
        print(f"[*] Resuming: {len(identifier_list) - len(pending_identifiers)} skipped, {len(pending_identifiers)} pending.")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.fetch, ident, parser_func): ident for ident in pending_identifiers}
            
            for future in as_completed(futures):
                ident = futures[future]
                self.render_dashboard(ident)
                try:
                    future.result()
                except Exception as thread_err:
                    with self.lock:
                        self.stats["failed"] += 1

        print(f"\n[*] Run completed in {datetime.now() - self.stats['start_time']}")
        self.save_output()

# --- Comprehensive VisitDubai DUL Parser Engine ---
def parse_visitdubai_dul(data, identifier=None):
    if not data or "dulNumber" not in data:
        return None
        
    issue_authority = data.get("issueAuthority") or {}
    category = data.get("category") or {}
    activity_type = data.get("activityType") or {}
    status = data.get("status") or {}
    trade_name = data.get("tradeName") or {}
    tn_issue_authority = trade_name.get("issueAuthority") or {}
    activity_group = trade_name.get("activityGroup") or {}
    address = data.get("address") or {}
    area = address.get("area") or {}
    main_area = area.get("mainArea") or {}
    unit_type = address.get("unitType") or {}
    country = address.get("country") or {}
    emirate = address.get("emirate") or {}
    company = data.get("company") or {}
    legal_type = company.get("legalType") or {}

    activities = data.get("operatingActivities") or []
    activity_names_en = "|".join((a.get("activity") or {}).get("englishName", "") for a in activities)
    activity_names_ar = "|".join((a.get("activity") or {}).get("arabicName", "") for a in activities)
    activity_statuses = "|".join((a.get("status") or {}).get("englishName", "") for a in activities)

    record = {
        "Target ID": identifier,
        "dulNumber": data.get("dulNumber"),
        "unifiedLicenceNumber": data.get("unifiedLicenceNumber"),
        "dulStatus": data.get("dulStatus"),
        "issueAuthorityId": issue_authority.get("id"),
        "issueAuthorityEn": issue_authority.get("englishName"),
        "issueAuthorityAr": issue_authority.get("arabicName"),
        "issueAuthorityLicenceNumber": data.get("issueAuthorityLicenceNumber"),
        "issueDate": data.get("issueDate"),
        "expiryDate": data.get("expiryDate"),
        "categoryId": category.get("id"),
        "categoryEn": category.get("englishName"),
        "activityTypeEn": activity_type.get("englishName"),
        "statusId": status.get("id"),
        "statusEn": status.get("englishName"),
        "statusAr": status.get("arabicName"),
        "unifiedTradeNameNumber": trade_name.get("unifiedTradeNameNumber"),
        "tradeNameIssueAuthorityEn": tn_issue_authority.get("englishName"),
        "tradeNameEn": trade_name.get("englishName"),
        "tradeNameAr": trade_name.get("arabicName"),
        "tradeNameIssueDate": trade_name.get("issueDate"),
        "tradeNameExpiryDate": trade_name.get("expiryDate"),
        "activityGroupCode": activity_group.get("activityGroupCode"),
        "activityGroupEn": activity_group.get("englishName"),
        "addressAreaEn": area.get("englishName"),
        "addressAreaAr": area.get("arabicName"),
        "addressMainAreaEn": main_area.get("englishName"),
        "addressLine1": address.get("addressLine1"),
        "addressFloor": address.get("floor"),
        "addressUnitTypeEn": unit_type.get("englishName"),
        "addressUnitNumber": address.get("unitNumber"),
        "addressParcelID": address.get("parcelID"),
        "addressCountryEn": country.get("englishName"),
        "addressEmirateEn": emirate.get("englishName"),
        "addressState": address.get("state"),
        "isMainLicense": data.get("isMainLicense"),
        "phone": data.get("phone"),
        "legalTypeEn": legal_type.get("englishName"),
        "legalTypeAr": legal_type.get("arabicName"),
        "numOperatingActivities": len(activities),
        "activityNamesEn": activity_names_en,
        "activityNamesAr": activity_names_ar,
        "activityStatuses": activity_statuses,
    }
    return record

if __name__ == "__main__":
    config = {
        "target_name": "VisitDubai-DUL",
        "base_url": "https://api.visitdubai.com/api/dul/GetDulDetails?dul={id}",
        "output_file": "visitdubai_master_extracted.xlsx",
        "state_file": "visitdubai_state.json",
        "max_workers": 3,
        "delay_range": (0.3, 0.8),
        "checkpoint_interval": 20,
        "proxies": []  # Add your cyclic proxies here if needed, e.g., ["http://proxy1:port"]
    }

    prefixes = ["FG", "FE", "EW"]
    test_identifiers = [f"{prefix}{i:04d}" for prefix in prefixes for i in range(1, 100)]

    scraper = AdvancedScraperEngine(config)
    scraper.run(test_identifiers, parse_visitdubai_dul)