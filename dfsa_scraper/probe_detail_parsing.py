"""
probe_detail_parsing.py -- STRICTLY READ-ONLY diagnostic.

The individuals/regulatory-actions parser (poc_firm_detail.parse_table_rows)
was only ever confirmed against ONE firm (Julius Baer Middle East Limited)
via manual Console inspection. This probe fetches a small, deliberately
varied set of real firm detail pages -- large banks (should have many
individuals), a law firm, and a couple from firms.jsonl that show 0
individuals in your current output -- and reports, for each:
  - whether a <div id="individuals"> / <div id="regulatory"> pane was
    found at all in the raw HTML
  - how many rows parse_table_rows extracts from each
  - a short raw-HTML snippet around the Individuals section so we can see
    the ACTUAL markup if it's empty or if the pane isn't found, rather
    than guessing

Only fetches detail pages -- no listing/pagination calls, no writes to
firms.jsonl / checkpoint.json / errors.jsonl / any production file.
"""
import re
import requests
from bs4 import BeautifulSoup
from poc_firm_detail import parse_firm_fields, parse_financial_services, parse_table_rows

# A deliberately varied set: large, well-known DIFC firms almost certain
# to have real individuals + likely regulatory history, plus the smaller
# firms already visually confirmed via screenshot to legitimately show 0
# individuals (Volaw Trust) -- included as a control/sanity check, not
# because we expect a bug there.
TEST_FIRMS = [
    ("Julius Baer (Middle East) Limited", "https://www.dfsa.ae/public-register/firms/julius-baer-middle-east-limited"),
    ("Goldman Sachs International", "https://www.dfsa.ae/public-register/firms/goldman-sachs-international"),
    ("JPMorgan Chase Bank, N.A.", "https://www.dfsa.ae/public-register/firms/jpmorgan-chase-bank-na"),
    ("UBS AG", "https://www.dfsa.ae/public-register/firms/ubs-ag"),
    ("Volaw Trust & Corporate Services Limited (control -- confirmed 0 individuals via screenshot)",
     "https://www.dfsa.ae/public-register/firms/volaw-trust-corporate-services-limited"),
]


def find_pane_raw(html: str, pane_id: str) -> str:
    """Returns a short snippet of raw HTML around div id="{pane_id}" if present, else empty."""
    soup = BeautifulSoup(html, "lxml")
    pane = soup.find("div", id=pane_id)
    if not pane:
        return ""
    raw = str(pane)
    return raw[:600] + ("... [truncated]" if len(raw) > 600 else "")


def main():
    print("=" * 78)
    print("DETAIL-PAGE PARSER DIAGNOSTIC — READ-ONLY, DETAIL PAGES ONLY")
    print("=" * 78)
    print("Checking whether parse_table_rows finds real data beyond the single "
          "Julius Baer example it was originally confirmed against.\n")

    for name, url in TEST_FIRMS:
        print(f"--- {name} ---")
        print(f"    {url}")
        try:
            resp = requests.get(url, timeout=20)
        except Exception as exc:
            print(f"    FETCH FAILED: {exc}\n")
            continue

        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code} -- not fetched cleanly\n")
            continue

        html = resp.text
        soup = BeautifulSoup(html, "lxml")

        individuals_pane_found = soup.find("div", id="individuals") is not None
        regulatory_pane_found = soup.find("div", id="regulatory") is not None

        individuals = parse_table_rows(
            soup, "individuals",
            ["name", "reference_number", "type_of_individual", "effective_date", "date_withdrawn"],
        )
        regulatory_actions = parse_table_rows(soup, "regulatory", ["title", "category", "date_of_use"])

        print(f"    HTTP {resp.status_code}, {len(html)} bytes")
        print(f"    individuals pane found in HTML: {individuals_pane_found}  -> parsed rows: {len(individuals)}")
        print(f"    regulatory pane found in HTML:  {regulatory_pane_found}  -> parsed rows: {len(regulatory_actions)}")

        if individuals_pane_found and not individuals:
            print("    >>> Pane exists but 0 rows parsed -- worth inspecting raw markup below.")
        if not individuals_pane_found:
            print("    >>> NO 'individuals' pane found at all in this page's HTML -- parser cannot "
                  "possibly find data here regardless of markup details. Raw snippet below.")

        # Only dump raw snippets for the interesting/broken cases, not
        # every firm, to keep output readable.
        if not individuals_pane_found or (individuals_pane_found and not individuals):
            print("    --- raw individuals-area snippet (first tabs/panes found near 'individual') ---")
            # Look for anything with "individual" in id/class anywhere on the page,
            # not just the exact id="individuals" the parser expects.
            candidates = soup.find_all(attrs={"id": re.compile("individual", re.I)})
            candidates += soup.find_all(attrs={"class": re.compile("individual", re.I)})
            if candidates:
                for c in candidates[:3]:
                    snippet = str(c)[:300]
                    print(f"      found element: <{c.name} id={c.get('id')!r} class={c.get('class')!r}> "
                          f"{snippet[:200]}...")
            else:
                print("      No element with 'individual' anywhere in id/class found on this page at all.")
        print()

    print("=" * 78)
    print("No firms.jsonl / checkpoint.json / errors.jsonl were touched. Detail pages only, "
          "no listing/pagination calls made.")


if __name__ == "__main__":
    main()