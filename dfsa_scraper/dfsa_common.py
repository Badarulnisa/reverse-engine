"""
Shared HTTP session, pagination walker, and row/detail parsing helpers
for the DFSA public register (https://www.dfsa.ae/public-register/).

Confirmed from HAR captures across firms / individuals / funds / prohibited-individuals:

  GET /public-register/{register}/getTotal?page=0&<filters>&isAjax=true
      -> {"success": true, "total": "<N>"}   (csrf_token NOT required here)

  GET /public-register/{register}?page=N&<filters>&isAjax=true&csrf_token=<tok>
      -> HTML fragment of <a class="table-row"> rows (csrf_token IS present
         on every real listing call in every capture; unconfirmed whether it's
         validated server-side, so we always bootstrap and send it anyway)

Row shape (consistent across firms/individuals/funds; prohibited-individuals
differs -- see dfsa_prohibited.py):

    <a href="{detail_url}" class="table-row">
        <div class="col"><p><span>Name:</span>{name}</p></div>
        <div class="col"><p class="grey"><span>Reference number:</span>{ref}</p></div>
        <div class="col"><p class="grey"><span>{Firm Type|Individual Type|Fund Type}:</span>{type}</p></div>
    </a>

Pagination: 10 rows/page, 0-indexed. Empty response body == end of register.
Don't trust total // 10 as the page count -- always walk until an empty page,
since short/uneven final pages are observed (e.g. individuals: page 3 had 1 row,
page 4 was empty, without a clean multiple-of-10 boundary).

CONFIRMED separately (Console diagnostics against a live firm page): detail
pages under /public-register/firms/{slug} are fully server-rendered in ONE
plain GET -- Firm Details, Individuals, and Regulatory Actions tabs are all
already present in that single response. The "tabs" are pure CSS/JS
show-hide; there is no separate AJAX call for them, confirmed by an empty
Fetch/XHR filter in DevTools Network while switching tabs. get_detail_page()
below is just a plain GET for this reason -- no browser automation needed
anywhere in this module.

EVENT EMISSION (dashboard support): DfsaSession optionally accepts an
`on_event` callback -- see events.EventBus.emit for its signature. When not
provided, it defaults to a no-op, so this module has ZERO dashboard-related
dependencies and remains fully runnable headless (e.g. from run_scraper.py
or script_testing.py exactly as before). The callback is only ever given
plain keyword arguments; this file never imports events.py or anything
FastAPI/WebSocket related.
"""
from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("dfsa_scraper")

BASE = "https://www.dfsa.ae"
USER_AGENT = "Mozilla/5.0 (compatible; dfsa-register-research/1.0)"

# Polite pacing -- this is a small public register (1224 firms / 4063
# individuals / ~280 funds), no need to hammer it.
REQUEST_DELAY_SECONDS = 0.5
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0


def _noop_on_event(event_type: str, **kwargs) -> None:
    """Default `on_event` -- does nothing. Keeps this module dashboard-free."""
    pass


class DfsaSession:
    """
    Wraps a requests.Session, handles the bootstrap step (load the listing
    page once to obtain a csrf_token cookie/value), and provides a retrying
    GET.

    `on_event` and `company` are optional. When `on_event` is provided
    (typically `EventBus.emit` from a dashboard runner), every HTTP attempt
    inside `_get()` emits REQUEST_STARTED, then exactly one of
    REQUEST_SUCCESS / REQUEST_RETRY / REQUEST_FAILED. `company` is just a
    label attached to those events so a dashboard can associate requests
    with the firm/individual currently being processed; pass a different
    value per firm if you reuse one DfsaSession across many (e.g. via
    `session.company = "..."` before each detail fetch).
    """

    def __init__(
        self,
        on_event: Optional[Callable[..., None]] = None,
        company: Optional[str] = None,
    ):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "X-Requested-With": "XMLHttpRequest",  # matches isAjax=true semantics
            }
        )
        self._csrf_token: Optional[str] = None
        self.on_event = on_event or _noop_on_event
        self.company = company

    def bootstrap(self, register_path: str) -> str:
        """
        Load the non-AJAX listing page (e.g. /public-register/firms) once
        and scrape the csrf_token out of it. The token has the shape
        "<unix_ts>:<hex>" based on captured examples, and is embedded
        somewhere in the page's inline JS / data attributes -- exact
        location must be confirmed against a fresh page load, since we
        only ever captured it already-attached to XHR query strings, never
        the bootstrap page itself.
        """
        url = f"{BASE}{register_path}"
        resp = self._get(url)
        token = self._extract_csrf_token(resp.text)
        if not token:
            logger.warning(
                "Could not find csrf_token on %s -- proceeding without one. "
                "getTotal calls don't need it; listing calls might reject "
                "without it depending on whether it's actually validated "
                "server-side (unconfirmed).",
                url,
            )
        self._csrf_token = token
        return token

    @staticmethod
    def _extract_csrf_token(html: str) -> Optional[str]:
        # Try a few plausible patterns; tighten this once you've inspected
        # a real bootstrap page's source for the exact spot it's embedded.
        patterns = [
            r'csrf_token["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',
            r'data-csrf-token=["\']([^"\']+)["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                return m.group(1)
        return None

    def _get(self, url: str, **kwargs) -> requests.Response:
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            self.on_event(
                "REQUEST_STARTED",
                company=self.company,
                url=url,
                retry=attempt - 1,
            )
            start = time.monotonic()
            try:
                resp = self.session.get(url, timeout=20, **kwargs)
                elapsed_ms = (time.monotonic() - start) * 1000
                if resp.status_code == 200:
                    self.on_event(
                        "REQUEST_SUCCESS",
                        company=self.company,
                        url=url,
                        http_status=resp.status_code,
                        response_ms=round(elapsed_ms, 1),
                        retry=attempt - 1,
                    )
                    return resp
                logger.warning(
                    "GET %s -> HTTP %s (attempt %d/%d)",
                    url, resp.status_code, attempt, MAX_RETRIES,
                )
                self.on_event(
                    "REQUEST_RETRY",
                    company=self.company,
                    url=url,
                    http_status=resp.status_code,
                    response_ms=round(elapsed_ms, 1),
                    retry=attempt,
                    error=f"HTTP {resp.status_code}",
                    message=f"Non-200 response, attempt {attempt}/{MAX_RETRIES}.",
                )
            except requests.RequestException as exc:
                elapsed_ms = (time.monotonic() - start) * 1000
                last_exc = exc
                logger.warning(
                    "GET %s failed (%s) (attempt %d/%d)",
                    url, exc, attempt, MAX_RETRIES,
                )
                self.on_event(
                    "REQUEST_RETRY",
                    company=self.company,
                    url=url,
                    response_ms=round(elapsed_ms, 1),
                    retry=attempt,
                    error=str(exc),
                    exception=exc,
                    message=f"Network/connection error, attempt {attempt}/{MAX_RETRIES}.",
                )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        # All retries exhausted.
        self.on_event(
            "REQUEST_FAILED",
            company=self.company,
            url=url,
            retry=MAX_RETRIES,
            error=str(last_exc) if last_exc else "Exhausted retries",
            exception=last_exc,
            message=f"Failed after {MAX_RETRIES} attempts.",
        )
        if last_exc:
            raise last_exc
        raise RuntimeError(f"GET {url} failed after {MAX_RETRIES} attempts")

    def get_total(self, register_path: str, filters: dict) -> int:
        params = dict(filters)
        params.update({"page": 0, "isAjax": "true"})
        url = f"{BASE}{register_path}/getTotal"
        resp = self._get(url, params=params)
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            data = resp.json()
        except ValueError:
            logger.error("getTotal non-JSON response from %s: %r", url, resp.text[:200])
            return 0
        if not data.get("success"):
            logger.warning("getTotal reported success=false: %s", data)
        return int(data.get("total", 0) or 0)

    def get_listing_page(self, register_path: str, page: int, filters: dict) -> str:
        params = dict(filters)
        params.update({"page": page, "isAjax": "true"})
        if self._csrf_token:
            params["csrf_token"] = self._csrf_token
        url = f"{BASE}{register_path}"
        resp = self._get(url, params=params)
        time.sleep(REQUEST_DELAY_SECONDS)
        return resp.text

    def get_detail_page(self, url: str) -> str:
        if url.startswith("/"):
            url = f"{BASE}{url}"
        resp = self._get(url)
        time.sleep(REQUEST_DELAY_SECONDS)
        return resp.text


@dataclass
class ListingRow:
    detail_url: str
    name: str
    reference_number: str
    type_label: str  # "Firm Type" / "Individual Type" / "Fund Type" text
    raw_fields: dict = field(default_factory=dict)
    # Optional -- only set by walk_register_by_type() (see below). None for
    # any row that came from a plain walk_register() call, so this is
    # additive and doesn't change behavior for existing callers
    # (run_scraper.py, dashboard's single-firm test, etc.) that never
    # touch this field.
    discovered_type: Optional[str] = None


def parse_listing_fragment(html: str) -> list[ListingRow]:
    """
    Parse a listing AJAX fragment (firms/individuals/funds -- NOT
    prohibited-individuals, which has a different row shape) into rows.
    Empty/whitespace-only html means "no more rows" -- caller should stop
    paginating.
    """
    if not html or not html.strip():
        return []

    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for a in soup.select("a.table-row"):
        detail_url = a.get("href", "").strip()
        cols = a.select("div.col p")
        fields = {}
        for p in cols:
            span = p.find("span")
            if not span:
                continue
            label = span.get_text(strip=True).rstrip(":")
            # text after the <span> is the value
            value = p.get_text(strip=True)
            # strip the label prefix (span text) from the front of value
            span_text = span.get_text(strip=True)
            if value.startswith(span_text):
                value = value[len(span_text):].strip()
            fields[label] = value

        name = fields.get("Name", "")
        ref = fields.get("Reference number", "")
        # the third column's label varies by register
        type_label = ""
        for key in ("Firm Type", "Individual Type", "Fund Type"):
            if key in fields:
                type_label = fields[key]
                break

        rows.append(
            ListingRow(
                detail_url=detail_url,
                name=name,
                reference_number=ref,
                type_label=type_label,
                raw_fields=fields,
            )
        )
    return rows


def walk_register(
    session: DfsaSession,
    register_path: str,
    filters: dict,
    max_pages: Optional[int] = None,
) -> Iterator[ListingRow]:
    """
    Bootstraps a csrf token, then walks pages 0..N until an empty page is
    returned. Does NOT trust getTotal // 10 as the stopping point -- only
    an empty response body ends the walk (see module docstring: short /
    uneven final pages are observed in practice).
    """
    session.bootstrap(register_path)
    total = session.get_total(register_path, filters)
    logger.info("%s: register reports total=%d", register_path, total)

    page = 0
    raw_rows_seen = 0  # count of raw listing rows yielded (NOT deduplicated --
    # duplicate reference numbers within a single type's raw listing pages
    # are confirmed to occur, e.g. "Recognised Members (Revoked)" returned
    # 100 raw rows for only 89 unique reference numbers. This variable
    # counts raw rows on purpose, for the empty-page/stale-session
    # heuristic below, which cares about "have we seen roughly enough
    # rows yet", not unique-record accounting.
    rebootstrapped_once = False
    while True:
        if max_pages is not None and page >= max_pages:
            logger.info("Hit max_pages=%d, stopping.", max_pages)
            break
        html = session.get_listing_page(register_path, page, filters)
        rows = parse_listing_fragment(html)
        if not rows:
            # An empty page normally means end-of-register. But if we
            # haven't reached the reported total yet, this may instead be
            # a stale session/csrf token after a long run (confirmed
            # occurrence: register reported 1225, walk stopped at 1010).
            # Re-bootstrap once and retry this exact page before
            # concluding the register is actually finished.
            if total and raw_rows_seen < total and not rebootstrapped_once:
                logger.warning(
                    "%s: empty page at page=%d but only %d/%d raw rows seen so far -- "
                    "re-bootstrapping session and retrying this page once "
                    "(possible stale token, not necessarily end of register).",
                    register_path, page, raw_rows_seen, total,
                )
                session.bootstrap(register_path)
                rebootstrapped_once = True
                html = session.get_listing_page(register_path, page, filters)
                rows = parse_listing_fragment(html)
                if rows:
                    logger.info("%s: retry after re-bootstrap succeeded, continuing.", register_path)
                    for row in rows:
                        raw_rows_seen += 1
                        session.on_event(
                            "FIRM_DISCOVERED",
                            company=row.name,
                            url=row.detail_url,
                            extra={"reference_number": row.reference_number, "type_label": row.type_label},
                        )
                        yield row
                    page += 1
                    continue
            logger.info("%s: empty page at page=%d, stopping walk.", register_path, page)
            break
        rebootstrapped_once = False  # reset once we see real data again
        for row in rows:
            raw_rows_seen += 1
            session.on_event(
                "FIRM_DISCOVERED",
                company=row.name,
                url=row.detail_url,
                extra={"reference_number": row.reference_number, "type_label": row.type_label},
            )
            yield row
        page += 1

    if total and raw_rows_seen != total:
        # CONFIRMED (2026-08 diagnostic probes): getTotal() appears to
        # represent a UNIQUE logical record count, while raw_rows_seen
        # counts every raw listing row including duplicates (e.g.
        # "Recognised Members (Revoked)": getTotal()=90, raw_rows_seen=
        # 100, 89 actual unique reference numbers -- 11 duplicate raw
        # rows). A mismatch here is therefore NOT automatically a
        # pagination failure -- it can simply mean the raw listing
        # contains duplicate reference instances. Callers that need a
        # true unique count should deduplicate by reference_number (see
        # walk_register_by_type), not rely on raw_rows_seen == total.
        logger.warning(
            "%s: raw_rows_seen=%d (raw listing rows, NOT deduplicated) vs getTotal()=%d "
            "(believed to be a unique-record count) -- mismatch may be caused by duplicate "
            "reference numbers within the raw listing (confirmed to happen), by "
            "skipped/duplicate pages, or by a stale/approximate total. Deduplicate by "
            "reference_number before treating either number as authoritative.",
            register_path, raw_rows_seen, total,
        )


def discover_select_options(html: str, field_name: str) -> list[tuple[str, str]]:
    """
    Extracts real <option value="...">label</option> pairs from a
    <select name="field_name"> (or id="field_name") on a register's
    bootstrap/listing page. Returns [] if no matching <select> is found.
    Blank/"all" options (value="") are excluded -- callers that want the
    unfiltered case already get it via the empty-string default filter.

    CONFIRMED (2026-08, live probe against the firms register): the
    `legal_status` and `financial_service` selects were NOT found by this
    lookup -- either they use different name/id attributes than the AJAX
    query param, or they're populated by JS not present in the raw GET.
    Only `type` (16 values) and `endorsement` (7 real values + 1 blank)
    were confirmed reachable this way. Don't assume this function finds
    every filter; it only finds what's actually present in server-rendered
    markup under the given field_name.
    """
    soup = BeautifulSoup(html, "html.parser")
    select = soup.find("select", attrs={"name": field_name}) or soup.find("select", attrs={"id": field_name})
    if not select:
        return []
    opts = []
    for opt in select.find_all("option"):
        value = opt.get("value", "").strip()
        label = opt.get_text(strip=True)
        if value:
            opts.append((value, label))
    return opts


# Outage-level resilience (distinct from _get()'s per-REQUEST retry above,
# which handles brief blips within a single call). This handles a
# SUSTAINED outage -- e.g. the local machine's own internet/DNS drops for
# tens of seconds or minutes -- where _get() has already exhausted its
# MAX_RETRIES and raised. Confirmed real-world case (2026-08): a
# NameResolutionError killed the whole background thread mid-run because
# nothing above walk_register() caught it. TYPE_RETRY_MAX_ATTEMPTS is high
# and TYPE_RETRY_BACKOFF_SECONDS grows (capped) specifically because a bad
# connection may need several minutes to come back, and giving up after 2-3
# tries would just reproduce the original crash-and-manual-restart problem.
TYPE_RETRY_MAX_ATTEMPTS = 20
TYPE_RETRY_BACKOFF_SECONDS = 10  # doubles each attempt, capped below
TYPE_RETRY_BACKOFF_CAP_SECONDS = 120

# Exceptions that indicate a connectivity-level failure (as opposed to a
# parsing bug or a programming error) -- these are the ones worth retrying
# at the outage level. requests.exceptions.RequestException covers
# ConnectionError, Timeout, etc.; the RuntimeError case is _get()'s own
# "failed after MAX_RETRIES attempts" (see dfsa_common._get above).
_OUTAGE_EXCEPTIONS = (requests.exceptions.RequestException, RuntimeError)


def walk_register_by_type(
    session: DfsaSession,
    register_path: str,
    base_filters: dict,
    type_values: list[str],
    max_pages_per_type: Optional[int] = None,
    diagnostics: Optional[dict] = None,
) -> Iterator[ListingRow]:
    """
    CONFIRMED VIA DIAGNOSTIC PROBES (2026-08): the unfiltered firms
    listing hits a hard server-side pagination ceiling around page 100
    (~1010 rows) and, independently of that ceiling, does not expose
    every record that type-filtered queries can reach (996 unique refs
    unfiltered vs. 2052 unique refs across all 16 type-filtered walks --
    the unfiltered walk is a strict subset, not a superset). Every
    individual type value stays far under the ceiling (max observed: 971
    records / 101 pages for "Authorised Firms"), so walking type-by-type
    is the confirmed working strategy for reaching the full register.

    Reuses walk_register() unchanged for each type value -- no new
    pagination/session/retry/termination logic here, just iteration +
    cross-type dedup + provenance tagging. walk_register()'s own
    empty-page termination condition is untouched; this function never
    uses getTotal() or a hardcoded page count as a stop condition.

    DEDUPLICATION: the diagnostic probes' authoritative uniqueness
    criterion was reference_number (that's what "2052 unique references"
    means), NOT detail_url. This function matches that: the primary
    dedup key is row.reference_number. If reference_number is empty/
    missing (malformed row), detail_url is used as a fallback key so
    malformed rows don't all collapse into a single record. A global
    `seen` set spans ALL 16 type walks -- once a reference_number has
    been yielded under one type, later sightings of the same reference
    under a different type are suppressed (not re-yielded, not
    re-fetched), and the FIRST-discovered row is treated as canonical.
    This is confirmed necessary: 10 cross-type overlap instances were
    observed (e.g. "Authorised Firms" and "Recognised Members" share 4
    firms) -- type values are NOT mutually exclusive.

    PROVENANCE: each yielded row's `discovered_type` field is set to the
    type value that first surfaced it, so downstream processing can
    always answer "which type filter discovered this firm" even though
    the same firm may also appear under other types (which are silently
    skipped once the canonical row has been yielded).

    DIAGNOSTICS: if a `diagnostics` dict is passed, it is filled in-place
    with per-type and global stats (see module-level docstring on the
    dict shape below) so the caller can detect anomalies (a type that
    hit max_pages, returned zero rows unexpectedly, or has a genuine
    getTotal()-vs-unique-refs mismatch) without re-deriving them.
    Anomalies are recorded, never hidden or silently smoothed over.
    """
    seen_refs: set[str] = set()
    per_type_stats: dict[str, dict] = {}

    for type_value in type_values:
        filters = dict(base_filters)
        filters["type"] = type_value
        logger.info("walk_register_by_type: starting type=%r", type_value)

        total = session.get_total(register_path, filters)
        raw_rows = 0
        unique_yielded_this_type = 0
        duplicate_within_type = 0
        duplicate_cross_type = 0
        hit_max_pages = False
        outage_retries_used = 0

        # OUTAGE-LEVEL RETRY (see TYPE_RETRY_* constants above). If the
        # connection to DFSA drops for longer than _get()'s own
        # MAX_RETRIES can absorb, walk_register() raises out of this
        # loop. We catch it here, wait (growing backoff, capped), and
        # restart THIS type's walk from page 0. This is safe/idempotent
        # specifically because of the dedup layer below: any row already
        # yielded before the crash is still in `seen_refs`, so re-walking
        # from page 0 just re-suppresses it as a (within-type) duplicate
        # instead of double-yielding or double-fetching it -- the walk
        # effectively "resumes" even though it technically restarts.
        attempt = 0
        while True:
            attempt += 1
            try:
                for row in walk_register(session, register_path, filters, max_pages=max_pages_per_type):
                    raw_rows += 1
                    dedup_key = row.reference_number or row.detail_url

                    if dedup_key in seen_refs:
                        # Distinguish "duplicate raw row within this same type's
                        # own listing pages" (confirmed to happen, e.g.
                        # Recognised Members (Revoked): 100 raw rows / 89 unique)
                        # from "this reference was already yielded under a
                        # DIFFERENT type" (the 10 confirmed cross-type overlap
                        # instances) -- both are suppressed the same way, but
                        # counted separately for the diagnostic report.
                        if dedup_key in per_type_stats.get(type_value, {}).get("_own_refs", set()):
                            duplicate_within_type += 1
                        else:
                            duplicate_cross_type += 1
                        continue

                    seen_refs.add(dedup_key)
                    per_type_stats.setdefault(type_value, {}).setdefault("_own_refs", set()).add(dedup_key)
                    unique_yielded_this_type += 1
                    row.discovered_type = type_value
                    yield row
                break  # walk_register's generator exhausted normally (empty page) -- done with this type
            except _OUTAGE_EXCEPTIONS as exc:
                if attempt >= TYPE_RETRY_MAX_ATTEMPTS:
                    logger.error(
                        "walk_register_by_type: type=%r failed %d times (connection outage) -- "
                        "giving up on this type after exhausting TYPE_RETRY_MAX_ATTEMPTS. "
                        "%d unique row(s) were collected from it before giving up. Error: %s",
                        type_value, attempt, unique_yielded_this_type, exc,
                    )
                    raise
                outage_retries_used += 1
                backoff = min(TYPE_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)), TYPE_RETRY_BACKOFF_CAP_SECONDS)
                logger.warning(
                    "walk_register_by_type: type=%r hit a connection outage (attempt %d/%d) -- "
                    "%d unique row(s) collected so far for this type. Waiting %ds before retrying "
                    "this type from page 0 (already-yielded rows will be safely re-suppressed as "
                    "duplicates, not re-yielded or re-fetched downstream). Error: %s",
                    type_value, attempt, TYPE_RETRY_MAX_ATTEMPTS, unique_yielded_this_type, backoff, exc,
                )
                time.sleep(backoff)
                continue

        if max_pages_per_type is not None:
            # walk_register logs "Hit max_pages=%d, stopping." itself;
            # we can only infer this happened when a page cap was
            # actually supplied by the caller (the default, None, means
            # walk_register's ONLY termination path is a genuine empty
            # page -- see walk_register's docstring/body, unchanged).
            hit_max_pages = raw_rows >= max_pages_per_type * PAGE_SIZE_HINT

        per_type_stats[type_value] = {
            "getTotal": total,
            "raw_rows": raw_rows,
            "unique_yielded": unique_yielded_this_type,
            "duplicate_within_type": duplicate_within_type,
            "duplicate_cross_type": duplicate_cross_type,
            "terminated_via_empty_page": max_pages_per_type is None,
            "hit_max_pages_cap": hit_max_pages,
            # CONFIRMED (2026-08 probes): getTotal() represents a unique
            # logical record count. Compare it against this type's own
            # unique-row count BEFORE cross-type suppression
            # (unique_yielded_this_type + duplicate_cross_type) -- not
            # against unique_yielded_this_type alone, since a row that
            # was suppressed only because another type already claimed
            # that reference_number is still a legitimate member of
            # THIS type's own listing, not evidence of a getTotal()
            # mismatch for this type.
            "getTotal_mismatch": bool(total) and total != (unique_yielded_this_type + duplicate_cross_type),
            "outage_retries_used": outage_retries_used,
        }

    global_unique = len(seen_refs)
    total_duplicate_cross_type = sum(s["duplicate_cross_type"] for s in per_type_stats.values())
    total_duplicate_within_type = sum(s["duplicate_within_type"] for s in per_type_stats.values())
    any_anomaly = any(
        s["hit_max_pages_cap"] or s["getTotal_mismatch"]
        for s in per_type_stats.values()
    )

    logger.info(
        "walk_register_by_type: done. %d type(s) walked, %d globally unique firms yielded, "
        "%d within-type duplicate row(s) suppressed, %d cross-type duplicate row(s) suppressed.%s",
        len(type_values), global_unique, total_duplicate_within_type, total_duplicate_cross_type,
        " ANOMALIES DETECTED -- see diagnostics." if any_anomaly else "",
    )

    if diagnostics is not None:
        # Strip the internal-only "_own_refs" sets before handing back --
        # they're bookkeeping, not part of the public diagnostic shape.
        clean_per_type = {
            t: {k: v for k, v in stats.items() if k != "_own_refs"}
            for t, stats in per_type_stats.items()
        }
        diagnostics.update({
            "types_walked": len(type_values),
            "global_unique_references": global_unique,
            "total_duplicate_within_type": total_duplicate_within_type,
            "total_duplicate_cross_type": total_duplicate_cross_type,
            "any_anomaly": any_anomaly,
            "per_type": clean_per_type,
        })


# Used only for the max_pages-cap heuristic in walk_register_by_type's
# diagnostics -- matches the confirmed page size (see module docstring:
# "Pagination: 10 rows/page"). Not used anywhere in pagination/stopping
# logic itself.
PAGE_SIZE_HINT = 10