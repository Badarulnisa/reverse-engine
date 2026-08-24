"""
Parser for a single individual detail page:
  https://www.dfsa.ae/public-register/individuals/{slug}

Confirmed from one live example (Mr Ahmad Alanani, I004080):

  - Name, DFSA Reference Number
  - Firm Name: single link to a firm -- appears to be the *current* /
    most-recently-relevant one, NOT necessarily the individual's full
    history (the firm-side Individuals table can show the same person
    across multiple separate tenures/firms; this single field is a
    simplification of that on the individual's own page)
  - Individual type, Functions (comma-separated), Effective Date,
    Withdrawal Date, Comments
  - A secondary "Firms" table: Name / Reference number / Type of Firm /
    Date Withdrawn -- this is the fuller affiliation history, mirroring
    the firm page's Individuals table from the other direction
  - Regulatory Actions table: same empty-header-always-renders pattern as
    the firm page, same caveat that a populated example was never captured
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

from dfsa_firm_detail import RegulatoryAction, _clean, _field_value_after_label  # reuse


@dataclass
class FirmAffiliation:
    name: str
    reference_number: str
    type_of_firm: str
    date_withdrawn: str
    detail_url: str


@dataclass
class IndividualDetail:
    url: str
    name: str
    reference_number: Optional[str]
    current_firm_name: Optional[str]
    current_firm_url: Optional[str]
    individual_type: Optional[str]
    functions: list[str]
    effective_date: Optional[str]
    withdrawal_date: Optional[str]
    comments: Optional[str]
    firm_affiliations: list[FirmAffiliation]
    regulatory_actions: list[RegulatoryAction]


def parse_individual_detail(html: str, url: str) -> IndividualDetail:
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1") or soup.find("h2")
    name = _clean(h1.get_text(" ", strip=True)) if h1 else ""

    reference_number = _field_value_after_label(soup, "DFSA Reference Number")
    individual_type = _field_value_after_label(soup, "Individual type")
    functions_raw = _field_value_after_label(soup, "Functions")
    functions = [f.strip() for f in (functions_raw or "").split(",") if f.strip()]
    effective_date = _field_value_after_label(soup, "Effective Date")
    withdrawal_date = _field_value_after_label(soup, "Withdrawal Date")
    comments = _field_value_after_label(soup, "Comments")

    # Current firm: a link under the "Firm Name" label
    current_firm_name = None
    current_firm_url = None
    firm_name_node = soup.find(string=re.compile(r"^\s*Firm Name\s*$", re.I))
    if firm_name_node:
        container = firm_name_node.parent.find_next_sibling()
        if container:
            link = container.find("a")
            if link:
                current_firm_name = _clean(link.get_text(" ", strip=True))
                current_firm_url = link.get("href", "").strip()

    # Firm affiliation history table (Name / Reference number / Type of Firm / Date Withdrawn)
    firm_affiliations: list[FirmAffiliation] = []
    for a in soup.select("a[href*='/public-register/firms/']"):
        href = a.get("href", "").strip()
        text_blob = a.get_text(" ", strip=True)
        ref_match = re.search(r"(F\d{6})", text_blob)
        ref = ref_match.group(1) if ref_match else ""
        name_part = text_blob[: ref_match.start()] if ref_match else text_blob
        rest = text_blob[ref_match.end():] if ref_match else ""
        date_match = re.search(r"\d{2}-[A-Za-z]{3}-\d{4}", rest)
        date_withdrawn = date_match.group(0) if date_match else ""
        type_part = rest.replace(date_withdrawn, "").strip() if date_match else rest.strip()

        firm_affiliations.append(
            FirmAffiliation(
                name=_clean(name_part) or "",
                reference_number=ref,
                type_of_firm=_clean(type_part) or "",
                date_withdrawn=date_withdrawn,
                detail_url=href,
            )
        )

    # Regulatory Actions -- confirmed shape from the firm-page example
    # (Al Ramz Capital LLC): row is an <a> to a document (PDF), not a table
    # row. Same parsing approach as dfsa_firm_detail.parse_firm_detail;
    # kept inline here rather than shared since neither module has enough
    # confirmed examples yet to be sure the individual-page markup is
    # identical to the firm-page markup (plausible, unconfirmed).
    regulatory_actions: list[RegulatoryAction] = []
    ra_header = soup.find(string=re.compile(r"^\s*Regulatory Actions\s*$", re.I))
    if ra_header:
        section = ra_header.find_parent()
        anchors = []
        if section:
            for sib in section.find_next_siblings():
                if not hasattr(sib, "select"):
                    continue
                anchors.extend(
                    sib.find_all(
                        "a",
                        href=re.compile(r"(application/files/|amazonaws\.com/files/)", re.I),
                    )
                )
        known_categories = ["Decision Notice", "Settlement Notice", "Administrative Fine"]
        for a in anchors:
            href = a.get("href", "").strip()
            text_blob = _clean(a.get_text(" ", strip=True)) or ""
            date_match = re.search(r"\d{2}-\d{2}-\d{4}|\d{2}-[A-Za-z]{3}-\d{4}", text_blob)
            date_of_use = date_match.group(0) if date_match else ""
            before_date = text_blob[: date_match.start()].strip() if date_match else text_blob
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

    return IndividualDetail(
        url=url,
        name=name or "",
        reference_number=reference_number,
        current_firm_name=current_firm_name,
        current_firm_url=current_firm_url,
        individual_type=individual_type,
        functions=functions,
        effective_date=effective_date,
        withdrawal_date=withdrawal_date,
        comments=comments,
        firm_affiliations=firm_affiliations,
        regulatory_actions=regulatory_actions,
    )