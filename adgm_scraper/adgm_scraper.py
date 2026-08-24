"""
AdgmScraper -- Aura (Salesforce Experience Cloud) client for ADGM's public
register: https://newreg.adgm.com/s/public-registrar

Built to match run_adgm.py's imports exactly:
    AdgmScraper, AuraApiError, StaleContextError,
    ENTITY_STATUS_VALUES, CATEGORY_VALUES, OFFSET_CEILING_ROWS
"""

from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass, field

import requests

try:
    from monitor import log_event
except ImportError:
    def log_event(*a, **k): pass

log = logging.getLogger("adgm_scraper")

BASE_URL = "https://newreg.adgm.com/s/sfsites/aura"

FWUID = "OUcwT3JDYUZld21JQ2ZOckR1VnppUWtVMjdnTGFERUU2S3FfSVdrcU92bkExNC4xOTIuODM4ODYwOA"
APP = "siteforce:communityApp"
LOADED = {"APPLICATION@markup://siteforce:communityApp": "1692_mtviYwT4OQy30JbfgmF_yA"}
PAGE_URI = "/s/search-results"

# Salesforce SOQL enforces a hard OFFSET ceiling around row 2000 -- confirmed
# live by an earlier run stalling at 2010 companies regardless of the true
# requestcount. Any single query/bucket must stay under this.
OFFSET_CEILING_ROWS = 2000

# Confirmed real picklist values -- pulled directly from the live rendered
# dropdown DOM via a shadow-DOM-aware query (not guessed), 2026-08-12.
ENTITY_STATUS_VALUES = [
    "Active", "Inactive", "Struck-Off", "Deregistered", "Registered",
    "Enters Administration", "In administration", "Registration Inactive",
    "In Liquidation", "In receivership", "Deregistered by Registrar",
    "Dissolved", "Continued outside ADGM", "Removed", "Issued", "Expired",
    "Cancelled", "Suspended by Registrar", "Withdrawn by Registrar", "Rejected",
]
CATEGORY_VALUES = [
    "Non-Financial (Category B)", "Financial (Category A)", "Retail (Category C)",
]

# Fixed pageId per detail-page tab -- constant across every entity.
DETAIL_PAGE_IDS: dict[str, str] = {
    "General_Details": "a0z5q000000kSixAAE",
    "Business_Activities": "a0z5q000000kSiyAAE",
    "Trade_Names": "a0z5q000000kSizAAE",
    "Addresses": "a0z5q000000kSj0AAE",
    "Shares_Details": "a0z5q000000kSj1AAE",
    "Shareholder": "a0z5q000000kSj2AAE",
    "Partners": "a0z5q000000kSj3AAE",
    "Non_Cell_Members": "a0z5q000000kSj4AAE",
    "Cell_Members": "a0z5q000000kSj5AAE",
    "Members": "a0z5q000000kSj6AAE",
    "Cells": "a0z5q000000kSj7AAE",
    "Director": "a0z5q000000kSj8AAE",
    "Secretary": "a0z5q000000kSj9AAE",
    "Beneficial_Owners": "a0z5q000000kSjAAAU",
    "Authorised_Signatories": "a0z5q000000kSjBAAU",
    "Data_Protection": "a0z5q000000kSjDAAU",
    "Filings": "a0z5q000000kSjEAAU",
    "Parent_Company_Members": "a0z5q000002LINvAAO",
    "Parent_Company_Shareholder": "a0z5q000002LINqAAO",
}

DEFAULT_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
}

# Full jsonSearchString template -- confirmed the Apex controller NPEs if
# any of these field-set blocks are missing, so every call sends the
# complete structure and only mutates the handful of fields that matter.
SEARCH_TEMPLATE: dict = {
    "advancedSearch": [{"fieldSetName": "SearchFieldsAdvGen", "headers": [
        {"dataType": "BOOLEAN", "fieldAPIName": "Is_continued__c", "isRequired": False,
         "label": "Is continued?", "options": [], "value": "", "allowRender": True},
        {"dataType": "PICKLIST", "fieldAPIName": "Entity_Status__c", "isRequired": False,
         "label": "Entity Status", "options": [], "value": "", "allowRender": True},
        {"dataType": "DATE", "fieldAPIName": "Incorporation_Date__c", "isRequired": False,
         "label": "Incorporation Date", "options": [], "value": "", "allowRender": False},
        {"dataType": "PICKLIST", "fieldAPIName": "Category__c", "isRequired": False,
         "label": "Category", "options": [], "value": "", "allowRender": True},
    ], "isParent": True, "objectName": "Account"}],
    "advancedSearch_auditors": [{"fieldSetName": "SearchFieldsAuditor", "headers": [],
                                  "isParent": False, "objectName": "Account",
                                  "relationshipName": "Subject_Account__r"}],
    "advancedSearch_companies": [{"fieldSetName": "SearchFieldsAdvComp", "headers": [],
                                   "isParent": True, "objectName": "Account"}],
    "advancedSearch_foliostrataplanstratalot": [{"fieldSetName": "SearchFieldsFolioStrataPlanLotAdvanced",
                                                   "headers": [], "isParent": False, "objectName": "Property__c"}],
    "advancedSearch_foundation": [{"fieldSetName": "SearchFieldsAdvFoun", "headers": [],
                                    "isParent": True, "objectName": "Account"}],
    "advancedSearch_general": [{"fieldSetName": "SearchFieldsAdvGen", "headers": [
        {"dataType": "BOOLEAN", "fieldAPIName": "Is_continued__c", "isRequired": False,
         "label": "Is continued?", "options": [], "value": ""},
        {"dataType": "PICKLIST", "fieldAPIName": "Entity_Status__c", "isRequired": False,
         "label": "Entity Status", "options": [], "value": ""},
        {"dataType": "DATE", "fieldAPIName": "Incorporation_Date__c", "isRequired": False,
         "label": "Incorporation Date", "options": [], "value": ""},
        {"dataType": "PICKLIST", "fieldAPIName": "Category__c", "isRequired": False,
         "label": "Category", "options": [], "value": ""},
    ], "isParent": True, "objectName": "Account"}],
    "advancedSearch_InsolvencyPractitioner": [{"fieldSetName": "SearchFieldsInsolvencyPractitioner",
                                                 "headers": [], "isParent": False, "objectName": "Account",
                                                 "relationshipName": "Subject_Account__r"}],
    "advancedSearch_partnership": [{"fieldSetName": "SearchFieldsAdvPart", "headers": [],
                                     "isParent": True, "objectName": "Account"}],
    "advancedsearch_RegisteredBuilding": [{"fieldSetName": "SearchFieldsLeaseAdvancedBuilding",
                                            "headers": [], "isParent": False, "objectName": "Linked_Unit__c"}],
    "advancedsearch_RegisteredLand": [{"fieldSetName": "SearchFieldsLeaseAdvancedLand",
                                        "headers": [], "isParent": False, "objectName": "Linked_Unit__c"}],
    "advancedsearch_RegisteredLease": [{"fieldSetName": "SearchFieldsLeaseAdvanced",
                                         "headers": [], "isParent": False, "objectName": "Linked_Unit__c"}],
    "advancedsearch_RegisteredUnit": [{"fieldSetName": "SearchFieldsLeaseAdvancedUnit",
                                        "headers": [], "isParent": False, "objectName": "Linked_Unit__c"}],
    "advancedSearch_reservedname": [{"fieldSetName": "ReservedName", "headers": [],
                                      "isParent": True, "objectName": "Trade_Name__c"}],
    "advancedSearch_role": [
        {"fieldSetName": "RoleSearchFields", "headers": [], "isParent": True, "objectName": "Role__c"},
        {"fieldSetName": "RoleFieldSet", "headers": [], "isParent": False, "objectName": "Account",
         "relationshipName": "Subject_Account__r"},
    ],
    "advancedSearch_temporarypermit": [{"fieldSetName": "TempPermitSearchFields", "headers": [],
                                         "isParent": True, "objectName": "Account"}],
    "buttonConfig": {
        "buttonPlacement": "RIGHT",
        "buttons": [
            {"actionType": "Create_BookMark", "label": "Add to Watchlist",
             "renderCheckField": "Show_Request_Option__c", "renderCheckValue": "Add BookMark",
             "styleClass": "requestBtn"},
            {"actionType": "Remove_BookMark", "label": "Remove from Watchlist",
             "renderCheckField": "Show_Request_Option__c", "renderCheckValue": "Remove BookMark",
             "styleClass": "cancelBtn"},
        ],
        "canSelectMultiple": False,
        "rowLevel": True,
    },
    "defaultOrderBy": "ASC",
    "generalSearch": [{
        "fieldSetName": "SearchFields",
        "headers": [{"dataType": "STRING", "fieldAPIName": "Name", "isRequired": False,
                     "label": "Account Name", "options": [], "value": ""}],
        "isParent": True, "objectName": "Account",
    }],
    "generalSearch_Folio": [{"fieldSetName": "SearchFieldsFolioGeneral", "headers": [],
                              "isParent": True, "objectName": "Property__c"}],
    "generalsearch_RegisteredLease": [{"fieldSetName": "SearchFieldsLeaseGeneral", "headers": [],
                                        "isParent": True, "objectName": "Linked_Unit__c"}],
    "generalSearch_StrataLot": [{"fieldSetName": "SearchFieldsStrataLotGeneral", "headers": [],
                                  "isParent": True, "objectName": "Property__c"}],
    "generalSearch_StrataPlan": [{"fieldSetName": "SearchFieldsStrataPlanGeneral", "headers": [],
                                   "isParent": True, "objectName": "Property__c"}],
    "orderByFields": "Name",
    "resultFieldSet": "RequestAccessSearchResult",
    "showAdvancedSearch": False,
    "showRegisteredEntities": True,
    "pageNumber": 1,
    "pageSize": "10",
}


class AuraApiError(Exception):
    """Raised for any non-SUCCESS Aura action state or a transport failure."""


class StaleContextError(AuraApiError):
    """Raised when Aura rejects the request because fwuid/app/loaded no
    longer match the deployed org. Re-capture a HAR and update the
    constants above."""


@dataclass
class AdgmScraper:
    session: requests.Session = field(default_factory=requests.Session)
    fwuid: str = FWUID
    app: str = APP
    loaded: dict[str, str] = field(default_factory=lambda: dict(LOADED))
    page_uri: str = PAGE_URI
    timeout: int = 30
    max_retries: int = 3
    retry_backoff: float = 2.0
    request_delay: float = 1.0
    worker_name: str = "main"
    use_tor: bool = False  # opt-in only -- False preserves existing behavior exactly

    def __post_init__(self):
        if self.use_tor:
            # Reuses the user's own tested Tor mechanism (tor_rotation.py,
            # built from test_tor_rotation.py) instead of the default
            # requests.Session. build_tor_session() now returns a plain
            # requests.Session routed through Tor's SOCKS5 proxy (see
            # that function's docstring: curl_cffi's compiled extension
            # failed to load on this environment, so this fell back from
            # curl_cffi to plain requests -- same requests.Session type
            # this class already uses by default, so nothing else here
            # needs to change).
            from tor_rotation import build_tor_session
            self.session = build_tor_session()
            log.info("[%s] using Tor-routed session (requests + SOCKS5)", self.worker_name)
        self.session.headers.update(DEFAULT_HEADERS)
        self._next_action_id = 100
        self._bootstrapped = False

    def bootstrap(self, page_path: str = "/s/search-results"):
        if self._bootstrapped:
            return
        url = f"https://newreg.adgm.com{page_path}"
        resp = self.session.get(url, timeout=self.timeout, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        log.info("[%s] bootstrap GET %s -> %s, cookies: %s",
                 self.worker_name, url, resp.status_code, list(self.session.cookies.get_dict().keys()))
        self._bootstrapped = True
        return resp

    def _aura_context(self) -> str:
        return json.dumps({"mode": "PROD", "fwuid": self.fwuid, "app": self.app,
                            "loaded": self.loaded, "dn": [], "globals": {}, "uad": True},
                           separators=(",", ":"))

    def _next_id(self) -> str:
        self._next_action_id += 1
        return f"{self._next_action_id};a"

    def _post(self, actions: list[dict], page_uri: str | None = None) -> dict:
        if not self._bootstrapped:
            self.bootstrap()

        message = json.dumps({"actions": actions}, separators=(",", ":"))
        payload = {
            "message": message,
            "aura.context": self._aura_context(),
            "aura.pageURI": page_uri or self.page_uri,
            "aura.token": "null",
        }
        log_event("request", f"[{self.worker_name}] {actions[0].get('params', {}).get('method', actions[0].get('descriptor', ''))}")

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(BASE_URL, data=payload, timeout=self.timeout)
            except Exception as e:
                # Kept as a broad `Exception` catch (rather than narrowing
                # back to `requests.RequestException`) mainly for safety
                # margin around the Tor/SOCKS path -- PySocks can raise
                # its own connection error types in some failure modes.
                # Harmless when use_tor=False too, since a plain
                # requests.Session only ever raises RequestException here.
                last_exc = e
                log.warning("request error (attempt %d/%d): %s", attempt, self.max_retries, e)
                log_event("error", str(e))
                if self.use_tor and attempt < self.max_retries:
                    from tor_rotation import rotate_tor_circuit
                    rotate_tor_circuit(reason=f"connection error: {e}", worker_name=self.worker_name)
                time.sleep(self.retry_backoff * attempt)
                continue

            if resp.status_code != 200:
                last_exc = AuraApiError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                log.warning("bad status (attempt %d/%d): %s", attempt, self.max_retries, last_exc)
                log_event("error", str(last_exc))
                # 403/429/503 are the status codes actually consistent
                # with rate-limiting or blocking (as opposed to e.g. a
                # 500 from a malformed payload, which a new IP won't
                # fix) -- only rotate for those.
                if self.use_tor and resp.status_code in (403, 429, 503) and attempt < self.max_retries:
                    from tor_rotation import rotate_tor_circuit
                    rotate_tor_circuit(reason=f"HTTP {resp.status_code}", worker_name=self.worker_name)
                time.sleep(self.retry_backoff * attempt)
                continue

            try:
                data = resp.json()
            except ValueError as e:
                snippet = resp.text[:300]
                if "clientOutOfSync" in resp.text or "Something has gone wrong" in resp.text:
                    raise StaleContextError(f"stale fwuid/context: {snippet}") from e
                last_exc = AuraApiError(f"non-JSON response: {snippet}")
                time.sleep(self.retry_backoff * attempt)
                continue

            for ev in data.get("events", []):
                if ev.get("descriptor", "").endswith("clientOutOfSync"):
                    raise StaleContextError(f"clientOutOfSync event: {ev}")

            log_event("response", f"[{self.worker_name}] OK")
            return data

        raise AuraApiError(f"exhausted retries: {last_exc}")

    @staticmethod
    def _unwrap_action(data: dict) -> dict:
        actions = data.get("actions", [])
        if not actions:
            raise AuraApiError(f"no actions in response: {data}")
        action = actions[0]
        if action.get("state") != "SUCCESS":
            raise AuraApiError(f"action state={action.get('state')}: {action}")
        return action.get("returnValue", {})

    # -- search -------------------------------------------------------------

    def _search_apex_params(self, name_term: str, page_number: int, page_size,
                             entity_status: str = "", category: str = "") -> dict:
        search_string = copy.deepcopy(SEARCH_TEMPLATE)
        search_string["generalSearch"][0]["headers"][0]["value"] = name_term
        search_string["pageNumber"] = page_number
        search_string["pageSize"] = page_size
        show_advanced = bool(entity_status or category)
        search_string["showAdvancedSearch"] = show_advanced

        for block_key in ("advancedSearch", "advancedSearch_general"):
            for header in search_string[block_key][0]["headers"]:
                if header["fieldAPIName"] == "Entity_Status__c":
                    header["value"] = entity_status
                elif header["fieldAPIName"] == "Category__c":
                    header["value"] = category

        registerationDate = json.dumps([
            {"fieldAPIName": "Incorporation_Date__c", "value": "", "dataType": "DATE"},
            {"fieldAPIName": "DateOperator", "value": "=", "dataType": "PICKLIST"},
            {"fieldAPIName": "Incorporation_Date__c", "value": "", "dataType": "DATE"},
        ])

        return {
            "jsonSearchString": json.dumps(search_string, separators=(",", ":")),
            "isAdvancedSearch": True,
            "searchMetadataProcessName": "Public Registry Search",
            "registerType": "",
            "registerationDate": registerationDate,
        }

    def search_page_with_count(self, name_term: str, page_number: int, page_size=10,
                                entity_status: str = "", category: str = ""):
        """Returns (rows, requestcount)."""
        action = {
            "id": self._next_id(),
            "descriptor": "aura://ApexActionController/ACTION$execute",
            "callingDescriptor": "UNKNOWN",
            "params": {
                "namespace": "", "classname": "RASearchUtil", "method": "getSearchResponseForPR",
                "params": self._search_apex_params(name_term, page_number, page_size, entity_status, category),
                "cacheable": False, "isContinuation": False,
            },
        }
        data = self._post([action], page_uri="/s/search-results")
        result = self._unwrap_action(data)
        rv = result.get("returnValue", {}).get("data", {})
        rows = rv.get("data", []) or []
        requestcount = rv.get("requestcount")
        return rows, requestcount

    def probe_requestcount(self, entity_status: str = "", category: str = "") -> int | None:
        try:
            _, rc = self.search_page_with_count("", 1, page_size=10,
                                                 entity_status=entity_status, category=category)
            return rc
        except AuraApiError as e:
            log.warning("probe status=%r category=%r failed: %s", entity_status, category, e)
            return None

    # -- detail pages ---------------------------------------------------------

    def get_detail_page(self, record_id: str, page_id: str) -> dict:
        action_related = {
            "id": self._next_id(),
            "descriptor": "apex://RAPRPageFlowController/ACTION$getRelatedRecordsWithAdditionalDetails",
            "callingDescriptor": "markup://c:rAPageFlowPublicRegistrar",
            "params": {"input": {"pageId": page_id, "parentRecordId": record_id}},
        }
        action_page = {
            "id": self._next_id(),
            "descriptor": "apex://RAPRPageFlowController/ACTION$getPageAndRelatedDetails",
            "callingDescriptor": "markup://c:rAPageFlowPublicRegistrar",
            "params": {"pageId": page_id, "recordId": record_id},
        }
        data = self._post([action_related, action_page], page_uri=f"/s/public-registrar?entityid={record_id}")
        actions = data.get("actions", [])

        result: dict = {}
        for action in actions:
            rv = action.get("returnValue")
            if not (isinstance(rv, dict) and "data" in rv):
                continue
            d = rv["data"]
            record_buckets = d.get("mpObjectTompRecordToActiveInActiveRecords", []) or []
            pages = d.get("pages") or {}
            if pages:
                # attach buckets only to the FIRST page label to avoid the
                # duplicate-attribution bug of attaching to every label
                first_label = next(iter(pages))
                entry = result.setdefault(first_label, {"layout": None, "records": []})
                entry["layout"] = pages[first_label]
                entry["records"].extend(record_buckets)
            elif record_buckets:
                result.setdefault("_unlabeled", {"layout": None, "records": []})["records"].extend(record_buckets)

        if not result:
            log.warning("empty response for record_id=%s page_id=%s", record_id, page_id)
        return result

    def get_all_detail_pages(self, record_id: str) -> dict:
        all_pages: dict = {}
        for label, page_id in DETAIL_PAGE_IDS.items():
            try:
                pages = self.get_detail_page(record_id, page_id)
            except StaleContextError:
                raise
            except AuraApiError as e:
                log.error("detail fetch failed record_id=%s tab=%s: %s", record_id, label, e)
                continue

            entry = all_pages.setdefault(label, {"layout": None, "records": []})
            for v in pages.values():
                if v.get("layout"):
                    entry["layout"] = v["layout"]
                entry["records"].extend(v.get("records", []))

            time.sleep(self.request_delay)
        return all_pages