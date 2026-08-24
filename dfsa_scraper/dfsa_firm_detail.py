"""
Parser for a single firm detail page:
  https://www.dfsa.ae/public-register/firms/{slug}

Confirmed server-rendered HTML (no AJAX for the detail page itself) from
three live examples (Julius Baer, Al Masah Capital Management, Aramid
Capital) plus print-rendered screenshots of Julius Baer:

  - Name / optional Trading Name
  - Legal Status, DFSA Reference Number
  - Address (single free-text block)
  - Telephone Number / Fax Number (either can be blank)
  - Date of Licence  OR  Date of Withdrawal (mutually informative about
    whether the firm is currently active) -- Al Masah shows "(currently in
    voluntary liquidation)" appended to the Name plus a Date of Withdrawal
    and no Date of Licence in that snippet. Treat status as *inferred*
    from which date field is present + any parenthetical suffix on Name,
    not as a clean separate field the page gives you.
  - Endorsements: list, optional
  - Financial Service: repeating (category, [instrument list]) pairs --
    confirmed on Aramid that some categories (e.g. "Managing a Collective
    Investment Fund") have NO instrument sub-line at all. CONFIRMED on
    Al Ramz Capital LLC that this whole section can be entirely EMPTY
    (both "Financial Service" and "Investments" column headers render with
    zero rows underneath) -- do not assume every firm has at least one.
  - Restrictions: free text, optional
  - Individuals table: Name / Reference number / Type of Individual /
    Effective Date / Date Withdrawn -- same person can have multiple rows
    (different tenures / withdrawn-then-reappointed). CONFIRMED this table
    can also be entirely empty (Al Ramz Capital LLC: header renders, zero
    rows) -- treat as normal, not an error condition.
  - Regulatory Actions table: Title / Category / Date of Use -- confirmed
    header always renders even when there are zero rows (Julius Baer). A
    POPULATED example is now confirmed (Al Ramz Capital LLC, 2 rows): each
    row is an <a> (matching the site's table-row pattern used on listing
    pages) whose href points to the actual notice document -- NOT a
    /public-register/... detail page but a PDF, hosted either at
    dfsa.ae/application/files/... or on an AWS S3 bucket
    (s<id>-web-server-storage-s3-<region>.amazonaws.com/files/...). Title
    text repeats the firm's name, Category seen so far: "Decision Notice".
    Capture document_url separately from title/category/date.

  Notable field differences seen on Non-DIFC firms (Al Ramz Capital LLC,
  Legal Status "Non-DIFC Company"): NO "Date of Licence" field at all --
  instead "Date of Recognition", plus an "Exchange Membership" field
  (e.g. "NASDAQ Dubai") that DIFC-company firms (Julius Baer, Aramid, Al
  Masah) did not show. Parser treats all of these as optional/best-effort
  rather than assuming DIFC-company field set is universal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

# Vocabulary of Financial Service *category* headers seen so far. Used to
# distinguish a category line from its instrument sub-line when walking
# the flat sequence of text nodes under the "Financial Service" heading.
# EXTEND THIS as new categories are encountered -- it is not guaranteed
# exhaustive, only what's been observed across 3 firms.
KNOWN_FINANCIAL_SERVICE_CATEGORIES = {
    "Accepting Deposits",
    "Advising on Financial Products",
    "Arranging Credit & Advising on Credit",
    "Arranging Custody",
    "Arranging Deals in Investments",
    "Dealing in Investments as Agent",
    "Dealing in Investments as Principal",
    "Insurance Intermediation",
    "Insurance Management",
    "Managing a Collective Investment Fund",
    "Managing Assets",
    "Operating a Credit Facility",
    "Providing Custody",
    "Providing Trust Services",
    "Providing Fund Administration",
}


@dataclass
class RegulatoryAction:
    title: str
    category: str
    date_of_use: str
    document_url: str = ""


@dataclass
class IndividualLink:
    name: str
    reference_number: str
    type_of_individual: str
    effective_date: str
    date_withdrawn: str
    detail_url: str


@dataclass
class FinancialServiceEntry:
    category: str
    instruments: list[str] = field(default_factory=list)


@dataclass
class FirmDetail:
    url: str
    name: str
    trading_name: Optional[str]
    legal_status: Optional[str]
    reference_number: Optional[str]
    address: Optional[str]
    telephone: Optional[str]
    fax: Optional[str]
    date_of_licence: Optional[str]
    date_of_withdrawal: Optional[str]
    date_of_recognition: Optional[str]  # Non-DIFC firms (e.g. Al Ramz) use this instead of Date of Licence
    exchange_membership: Optional[str]  # seen on Non-DIFC firms, e.g. "NASDAQ Dubai"
    is_active_guess: Optional[bool]  # inferred, see module docstring
    endorsements: list[str]
    financial_services: list[FinancialServiceEntry]
    restrictions: Optional[str]
    individuals: list[IndividualLink]
    regulatory_actions: list[RegulatoryAction]


def _clean(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _field_value_after_label(soup: BeautifulSoup, label: str) -> Optional[str]:
    """
    The detail page renders each field as a label element followed by a
    value element (confirmed pattern: a small caption-style node with the
    field name, then the value in the next sibling). This is brittle
    without the actual DOM (only the rendered/markdown view was captured,
    not raw HTML for the detail page) -- confirm exact tag/class names
    against a saved HTML source before relying on this in production, and
    adjust the selector below. As written, it looks for a text node exactly
    matching `label` and returns the next non-empty sibling's text.
    """
    node = soup.find(string=re.compile(rf"^\s*{re.escape(label)}\s*:?\s*$", re.I))
    if not node:
        return None
    el = node.parent
    nxt = el.find_next_sibling()
    if nxt:
        return _clean(nxt.get_text(" ", strip=True))
    # fallback: next element in document order
    nxt2 = el.find_next(string=True)
    return _clean(nxt2) if nxt2 else None


def parse_firm_detail(html: str, url: str) -> FirmDetail:
    soup = BeautifulSoup(html, "lxml")

    # --- Name / Trading Name -------------------------------------------------
    h1 = soup.find("h1") or soup.find("h2")
    raw_name = _clean(h1.get_text(" ", strip=True)) if h1 else None

    trading_name = None
    tn_node = soup.find(string=re.compile(r"Trading Name\s*:", re.I))
    if tn_node:
        # "Trading Name: X" is often rendered as one bolded line
        full = _clean(tn_node.parent.get_text(" ", strip=True))
        if full:
            trading_name = re.sub(r"^Trading Name\s*:\s*", "", full, flags=re.I).strip() or None

    name = raw_name

    # --- Simple label/value fields -------------------------------------------
    legal_status = _field_value_after_label(soup, "Legal Status")
    reference_number = _field_value_after_label(soup, "DFSA Reference Number")
    address = _field_value_after_label(soup, "Address")
    telephone = _field_value_after_label(soup, "Telephone Number")
    fax = _field_value_after_label(soup, "Fax Number")
    date_of_licence = _field_value_after_label(soup, "Date of Licence")
    date_of_withdrawal = _field_value_after_label(soup, "Date of Withdrawal")
    date_of_recognition = _field_value_after_label(soup, "Date of Recognition")
    exchange_membership = _field_value_after_label(soup, "Exchange Membership")
    restrictions = _field_value_after_label(soup, "Restrictions")

    # --- Status inference ------------------------------------------------------
    # Confirmed signal #1: parenthetical suffix on the Name, e.g.
    #   "Al Masah Capital Management Limited (currently in voluntary liquidation)"
    # Confirmed signal #2: Date of Licence present vs Date of Withdrawal present.
    is_active_guess: Optional[bool] = None
    if name and re.search(r"\(currently in voluntary liquidation\)", name, re.I):
        is_active_guess = False
    elif date_of_withdrawal and not date_of_licence:
        is_active_guess = False
    elif date_of_licence or date_of_recognition:
        is_active_guess = True

    # --- Endorsements (list) ---------------------------------------------------
    endorsements: list[str] = []
    end_node = soup.find(string=re.compile(r"^\s*Endorsements\s*$", re.I))
    if end_node:
        container = end_node.parent.find_next_sibling()
        if container:
            text = container.get_text("\n", strip=True)
            endorsements = [_clean(x) for x in text.split("\n") if _clean(x)]

    # --- Financial Service (category -> instrument list, repeating) -----------
    financial_services: list[FinancialServiceEntry] = []
    fs_header = soup.find(string=re.compile(r"^\s*Financial Service\s*$", re.I))
    if fs_header:
        # Walk forward collecting lines of text until we hit "Restrictions"
        # or the Individuals table, whichever the page structure uses as
        # the section terminator. Category lines are matched against the
        # known-category vocabulary; anything else immediately following a
        # category is treated as its comma-separated instrument list.
        block = fs_header.parent.find_parent()
        lines: list[str] = []
        if block:
            for sib in block.find_next_siblings():
                sib_text = _clean(sib.get_text(" ", strip=True)) or ""
                if not sib_text:
                    continue
                if sib_text.lower() in ("restrictions", "individuals", "name"):
                    break
                lines.append(sib_text)

        current: Optional[FinancialServiceEntry] = None
        for line in lines:
            if line in KNOWN_FINANCIAL_SERVICE_CATEGORIES:
                current = FinancialServiceEntry(category=line, instruments=[])
                financial_services.append(current)
            else:
                if current is not None:
                    current.instruments = [x.strip() for x in line.split(",") if x.strip()]
                else:
                    # Unrecognised category not in our vocabulary -- keep it
                    # as a bare category with no parsed instruments rather
                    # than silently dropping data. Extend
                    # KNOWN_FINANCIAL_SERVICE_CATEGORIES when this fires.
                    current = FinancialServiceEntry(category=line, instruments=[])
                    financial_services.append(current)

    # --- Individuals table -------------------------------------------------
    individuals: list[IndividualLink] = []
    for a in soup.select("a[href*='/public-register/individuals/']"):
        href = a.get("href", "").strip()
        text_blob = a.get_text(" ", strip=True)
        # Row text is concatenated (name + ref + type + dates) with no
        # separators in the rendered/markdown captures seen so far, e.g.
        #   "Mr Regis BurgerI001507Authorised Individuals05-Nov-2025"
        # Split heuristically: ref number is I\d+, dates are DD-Mon-YYYY.
        ref_match = re.search(r"(I\d{6})", text_blob)
        ref = ref_match.group(1) if ref_match else ""
        dates = re.findall(r"\d{2}-[A-Za-z]{3}-\d{4}", text_blob)
        effective_date = dates[0] if len(dates) >= 1 else ""
        date_withdrawn = dates[1] if len(dates) >= 2 else ""

        name_part = text_blob
        if ref_match:
            name_part = text_blob[: ref_match.start()]
        type_part = ""
        if ref:
            after_ref = text_blob[ref_match.end():]
            # strip trailing dates from the type label
            type_part = after_ref
            for d in dates:
                type_part = type_part.replace(d, "")
            type_part = type_part.strip()

        individuals.append(
            IndividualLink(
                name=_clean(name_part) or "",
                reference_number=ref,
                type_of_individual=_clean(type_part) or "",
                effective_date=effective_date,
                date_withdrawn=date_withdrawn,
                detail_url=href,
            )
        )

    # --- Regulatory Actions table -----------------------------------------
    # CONFIRMED shape (Al Ramz Capital LLC, 2 populated rows): each row is
    # an <a> whose href is the actual notice document (PDF), hosted either
    # under dfsa.ae/application/files/... or an AWS S3 bucket
    # (s<id>-web-server-storage-s3-<region>.amazonaws.com/files/...). The
    # anchor's text contains Title + Category + Date of Use concatenated,
    # same "no separator" pattern seen in the Individuals table. Empty
    # state (Julius Baer, Aramid): header renders, zero <a> rows.
    regulatory_actions: list[RegulatoryAction] = []
    ra_header = soup.find(string=re.compile(r"^\s*Regulatory Actions\s*$", re.I))
    if ra_header:
        section = ra_header.find_parent()
        anchors = []
        if section:
            for sib in section.find_next_siblings():
                if not hasattr(sib, "select"):
                    continue
                # Row links point at a document, not a /public-register/ page --
                # match on file-hosting hosts/paths rather than a fixed class,
                # since we don't have the raw HTML to confirm the exact class name.
                anchors.extend(
                    sib.find_all(
                        "a",
                        href=re.compile(r"(application/files/|amazonaws\.com/files/)", re.I),
                    )
                )
        for a in anchors:
            href = a.get("href", "").strip()
            text_blob = _clean(a.get_text(" ", strip=True)) or ""
            date_match = re.search(r"\d{2}-\d{2}-\d{4}|\d{2}-[A-Za-z]{3}-\d{4}", text_blob)
            date_of_use = date_match.group(0) if date_match else ""
            before_date = text_blob[: date_match.start()].strip() if date_match else text_blob
            # Category values seen so far are short, known labels; title is
            # everything before it. Without more populated examples this
            # split is a best guess -- "Decision Notice" is the only
            # confirmed Category value, so match on known categories first
            # and fall back to a naive last-two-words split.
            known_categories = ["Decision Notice", "Settlement Notice", "Administrative Fine"]
            title, category = before_date, ""
            for cat in known_categories:
                if before_date.endswith(cat):
                    title = before_date[: -len(cat)].strip()
                    category = cat
                    break
            regulatory_actions.append(
                RegulatoryAction(
                    title=title, category=category, date_of_use=date_of_use, document_url=href
                )
            )

    return FirmDetail(
        url=url,
        name=name or "",
        trading_name=trading_name,
        legal_status=legal_status,
        reference_number=reference_number,
        address=address,
        telephone=telephone,
        fax=fax,
        date_of_licence=date_of_licence,
        date_of_withdrawal=date_of_withdrawal,
        date_of_recognition=date_of_recognition,
        exchange_membership=exchange_membership,
        is_active_guess=is_active_guess,
        endorsements=endorsements,
        financial_services=financial_services,
        restrictions=restrictions,
        individuals=individuals,
        regulatory_actions=regulatory_actions,
    )