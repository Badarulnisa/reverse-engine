"""
Proof of concept: parse ONE DFSA firm detail page using the DOM structure
we confirmed via Console diagnostics (not guessed).

Confirmed facts this relies on:
  - Whole page is server-rendered in one GET, no browser needed.
  - Firm Details fields: <div class="col"><p class="small grey">Label:</p><p>Value</p></div>
  - Individuals rows:    <a href="..."><div class="table-row">
                            <div class="col"><p>Name</p></div>
                            <div class="col"><p class="grey">RefNumber</p></div>
                            <div class="col"><p class="grey">Type</p></div>
                            <div class="col"><p class="grey">EffectiveDate</p></div>
                            <div class="col"><p class="grey">WithdrawnDate (often empty)</p></div>
                          </div></a>
  - Regulatory Actions: same table-row pattern (untested here since Julius
    Baer's is empty -- structure assumed identical to Individuals until we
    confirm against a firm that actually HAS regulatory actions, e.g. one
    of the ones mentioned earlier like Al Ramz Capital LLC).

Run: python poc_firm_detail.py
"""
import re
import requests
from bs4 import BeautifulSoup
import json

URL = "https://www.dfsa.ae/public-register/firms/julius-baer-middle-east-limited"


def parse_firm_fields(soup: BeautifulSoup) -> dict:
    """
    Every simple label/value pair on the Firm Details tab follows the same
    shape: div.col > p.small.grey (label, includes colon) + p (value).
    We don't need to guess which labels exist -- just walk every div.col
    in the firms tab-pane and pair them up generically.
    """
    fields = {}
    firms_pane = soup.find("div", id="firms")
    if not firms_pane:
        return fields

    for col in firms_pane.select("div.col"):
        label_p = col.find("p", class_="small grey")
        # class_ match with multiple classes needs a different approach in bs4
        if label_p is None:
            label_p = col.find("p", class_=lambda c: c and "small" in c and "grey" in c)
        if not label_p:
            continue
        label = label_p.get_text(strip=True).rstrip(":")
        # "Financial Service" / "Investments" are the header labels for the
        # repeating category rows (see parse_financial_services) -- they
        # have no same-row sibling value, so this generic walker would
        # otherwise record them as misleadingly empty. Skip them here;
        # they're populated properly by parse_financial_services instead.
        if label in ("Financial Service", "Investments"):
            continue
        value_p = label_p.find_next_sibling("p")
        value = value_p.get_text(strip=True) if value_p else ""
        fields[label] = value
    return fields


def parse_financial_services(soup: BeautifulSoup) -> list[dict]:
    """
    Confirmed via Console diagnostic against the live Julius Baer page:
    the Financial Service / Investments section is NOT the simple
    label-then-next-sibling pattern used by every other Firm Details
    field, and it's NOT the <a><div class="table-row"> pattern used by
    Individuals/Regulatory Actions either. It's a third, distinct shape:

        <div class="table-row spcl_row">      <!-- header row, no digit suffix -->
            <div class="col"><p class="small grey">Financial Service</p></div>
            <div class="col"></div>
            <div class="col"><p class="small grey">Investments</p></div>
            <div class="col"></div>
        </div>
        <div class="table-row spcl_row1">      <!-- one row per category -->
            <div class="col"><p>Advising on Financial Products</p></div>
            <div class="col"></div>
            <div class="col"><p class="word_break_style">Certificates, Debentures, ...</p></div>
            <div class="col"></div>
        </div>
        <div class="table-row spcl_row2">...</div>   <!-- spcl_row1/spcl_row2 alternate
                                                          for zebra-striping -- CONFIRMED
                                                          the digit is NOT a stable per-row
                                                          index, just odd/even styling, so
                                                          we match on the class PATTERN
                                                          (spcl_row followed by a digit),
                                                          never on a specific digit value. -->

    Returns a list of {"category": ..., "instruments": [...]} dicts, in
    document order. A firm with no financial services returns [].
    """
    firms_pane = soup.find("div", id="firms")
    if not firms_pane:
        return []

    entries = []
    # Match spcl_row1, spcl_row2, spcl_row3, etc. -- but NOT the bare
    # "spcl_row" header row (no trailing digit).
    for row in firms_pane.select('div.table-row[class*="spcl_row"]'):
        classes = row.get("class", [])
        if not any(re.fullmatch(r"spcl_row\d+", c) for c in classes):
            continue  # this is the header row, skip it
        cols = row.select("div.col")
        if len(cols) < 3:
            continue
        category_p = cols[0].find("p")
        instruments_p = cols[2].find("p")
        category = category_p.get_text(strip=True) if category_p else ""
        instruments_text = instruments_p.get_text(strip=True) if instruments_p else ""
        instruments = [x.strip() for x in instruments_text.split(",") if x.strip()]
        if category:
            entries.append({"category": category, "instruments": instruments})
    return entries


def parse_regulatory_actions(soup: BeautifulSoup) -> list[dict]:
    """
    CONFIRMED (live capture, Al Ramz Capital LLC -- 2 populated rows,
    2026-08): the <div id="regulatory"> tab pane holds rows shaped as

        <a href="...pdf" class="table-row" target="_blank">
            <div class="col"><p>Title</p></div>
            <div class="col"><p class="grey">Category</p></div>
            <div class="col"><p class="grey">Date of Use</p></div>
        </a>

    i.e. the <a> tag ITSELF carries class="table-row", with div.col
    children directly inside it -- NOT a nested <div class="table-row">
    inside the anchor (that was the original, unconfirmed guess), and
    NOT identified by matching PDF/S3 href patterns (that was this
    function's first revision, also wrong -- it happened to work
    incidentally for some rows but wasn't matching the actual DOM
    shape). This mirrors the exact same a.table-row > div.col pattern
    dfsa_common.parse_listing_fragment() already uses successfully for
    register listing rows.
    """
    pane = soup.find("div", id="regulatory")
    if not pane:
        return []

    rows = []
    for a in pane.select("a.table-row"):
        cols = a.select("div.col")
        title_p = cols[0].find("p") if len(cols) > 0 else None
        category_p = cols[1].find("p") if len(cols) > 1 else None
        date_p = cols[2].find("p") if len(cols) > 2 else None
        rows.append(
            {
                "title": title_p.get_text(strip=True) if title_p else "",
                "category": category_p.get_text(strip=True) if category_p else "",
                "date_of_use": date_p.get_text(strip=True) if date_p else "",
                "document_url": a.get("href", "").strip(),
            }
        )
    return rows


def parse_table_rows(soup: BeautifulSoup, pane_id: str, col_names: list[str]) -> list[dict]:
    """
    Generic parser for the table-row pattern used by both Individuals and
    Regulatory Actions tabs -- positional div.col children under each
    <a><div class="table-row">.
    """
    pane = soup.find("div", id=pane_id)
    if not pane:
        return []

    rows = []
    for a in pane.select("a"):
        row_div = a.find("div", class_="table-row")
        if not row_div:
            continue
        cols = row_div.select("div.col")
        record = {"detail_url": a.get("href", "").strip()}
        for i, col_name in enumerate(col_names):
            if i < len(cols):
                p = cols[i].find("p")
                record[col_name] = p.get_text(strip=True) if p else ""
            else:
                record[col_name] = ""
        rows.append(record)
    return rows


def main():
    resp = requests.get(URL, timeout=20)
    print(f"HTTP {resp.status_code}, {len(resp.text)} bytes\n")

    soup = BeautifulSoup(resp.text, "html.parser")

    firm_details = parse_firm_fields(soup)
    financial_services = parse_financial_services(soup)
    individuals = parse_table_rows(
        soup, "individuals",
        ["name", "reference_number", "type_of_individual", "effective_date", "date_withdrawn"],
    )
    regulatory_actions = parse_table_rows(
        soup, "regulatory",
        ["title", "category", "date_of_use"],
    )

    record = {
        "url": URL,
        "firm_details": firm_details,
        "financial_services": financial_services,
        "individuals": individuals,
        "individuals_count": len(individuals),
        "regulatory_actions": regulatory_actions,
        "regulatory_actions_count": len(regulatory_actions),
    }

    print(json.dumps(record, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()