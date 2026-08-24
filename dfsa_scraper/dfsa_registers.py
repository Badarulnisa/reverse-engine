"""
Filter parameter shapes confirmed per register from HAR captures.
Empty string = "no filter" (confirmed default state in the unfiltered
listing captures for firms and funds).
"""

FIRMS = {
    "path": "/public-register/firms",
    "default_filters": {
        "type": "",
        "financial_service": "",
        "keywords": "",
        "legal_status": "",
        "endorsement": "",
    },
    # CONFIRMED VIA DIAGNOSTIC PROBES (2026-08): DO NOT use the unfiltered
    # listing/getTotal for firms as the collection target. Confirmed facts:
    #   - Unfiltered getTotal() ~1225, but the unfiltered LISTING endpoint
    #     hits a hard server-side pagination ceiling around page 100/1010
    #     rows -- reproducible with a completely fresh session/csrf token,
    #     not a stale-session artifact.
    #   - The unfiltered walk is NOT a superset of the type-filtered data:
    #     it collected only 996 unique reference numbers, while walking
    #     ALL 16 confirmed `type` values (below) and deduplicating by
    #     reference number reaches 2052 unique firms -- 1056 of which are
    #     NOT reachable through the unfiltered listing at all.
    #   - Every individual type value stays far under the ~1010 ceiling
    #     (largest: "Authorised Firms", 971 records / 101 pages), so
    #     type-by-type walking (see dfsa_common.walk_register_by_type) is
    #     the confirmed working collection strategy.
    #   - Type values are NOT fully mutually exclusive (10 overlapping
    #     ref instances confirmed across 5 pairs, e.g. "Authorised Firms"
    #     and "Recognised Members" share 4 firms) -- always dedupe by
    #     reference_number/detail_url across the whole run, never assume
    #     type values partition the register cleanly.
    #   - "confirmed_total" below is therefore a KNOWN UNDERCOUNT, kept
    #     only as a legacy/reference figure. Use CONFIRMED_FIRM_TYPES +
    #     walk_register_by_type for real collection; the true target is
    #     "however many unique firms the type-partitioned walk finds",
    #     not this number.
    "confirmed_total": 1224,
}

# CONFIRMED live (2026-08) via dfsa_common.discover_select_options() against
# the firms register's <select name="type"> markup. Do not hand-edit this
# list from assumption -- if the site adds/removes a type value, re-run
# discovery (discover_select_options(html, "type")) rather than guessing.
# 3 of these 16 currently return 0 results (getTotal()=0, confirmed empty
# on a genuine first page) -- kept in the list for completeness/future-
# proofing rather than silently dropped.
CONFIRMED_FIRM_TYPES = [
    "Ancillary Service Providers",
    "Ancillary Service Providers (Withdrawn)",
    "Authorised Firms",
    "Authorised Firms (Withdrawn)",
    "Authorised Market Institutions",
    "Authorised Market Institutions (Withdrawn)",
    "DNFBP",
    "DNFBP (Withdrawn)",
    "External Fund Manager",
    "External Fund Manager (Withdrawn)",
    "Recognised Bodies",
    "Recognised Bodies (Revoked)",
    "Recognised Members",
    "Recognised Members (Revoked)",
    "Registered Auditors",
    "Registered Auditors (Withdrawn)",
]

INDIVIDUALS = {
    "path": "/public-register/individuals",
    "default_filters": {
        "key_individual_function": "",
        "authorised_individual_function": "",
        "audit_principal_function": "",
        "keywords": "",
    },
    "confirmed_total": 4063,
}

FUNDS = {
    "path": "/public-register/funds",
    "default_filters": {
        # fundType and type are BOTH multi-value, comma-joined when set,
        # e.g. "Sub Fund,External Fund" -- confirmed from HAR, don't
        # conflate the two params, they're independently faceted.
        "fundType": "",
        "type": "",
        "jurisdiction": "",
        "status": "",
        "keywords": "",
    },
    # CONFIRMED via live fetch of the unfiltered page (server-rendered,
    # not the AJAX getTotal endpoint): "294 funds found" at time of check.
    # This supersedes the two earlier conflicting numbers (280 from one
    # filtered HAR capture, 292 seen once in stale UI text) -- always
    # re-verify via getTotal rather than hardcoding any of these.
    # CONFIRMED facet values (live fetch of unfiltered funds page):
    #   fundType: "Registered Fund", "Sub Fund", "External Fund"
    #   type: "Public Fund", "Qualified Investor Fund", "Exempt Fund",
    #         "External Fund", "CIF Exempt"
    #   jurisdiction: "DIFC", "Cayman Islands"
    #   status: "Active", "Withdrawn", "Winding up"
    "confirmed_total": 294,
}

# CONFIRMED (live fetch, not previously known): fund detail URLs are NOT
# uniform. Parent/standalone funds use:
#   /public-register/funds/{slug}
# but Sub Funds use a DIFFERENT path segment:
#   /public-register/funds/sub_funds/{slug}
# e.g. "Mashreq Al Islami Equity Fund (MAIEF)" (Fund Type: Sub Fund) ->
#   /public-register/funds/sub_funds/mashreq-al-islami-equity-fund-maief
# A detail-page fetcher for funds must branch on Fund Type (or just use
# the href verbatim from the listing row, which is safest) rather than
# constructing /public-register/funds/{slug} from the name/ref for every row.

# CONFIRMED (live fetch): there is a FIFTH register not previously
# captured -- "Passported Funds" at /public-register/passport-funds.
# Not yet HAR-captured or schema-confirmed at all. Flagging for whenever
# you want to extend beyond firms/individuals/funds/prohibited-individuals.
PASSPORT_FUNDS = {
    "path": "/public-register/passport-funds",
    "default_filters": {},  # UNCONFIRMED -- no capture yet
    "confirmed_total": None,
}

# Prohibited individuals has a DIFFERENT row shape (href points straight to
# a PDF on S3, not to a /public-register/... detail page) -- do not reuse
# parse_listing_fragment's detail_url assumption blindly; it will still
# extract name/date/status fine since those come from the same
# <div class="col"><p><span>...</span>...</p></div> structure, but there is
# no secondary detail page to fetch for this register.
PROHIBITED_INDIVIDUALS = {
    "path": "/public-register/prohibited-individuals",
    "default_filters": {
        "status": "",  # confirmed values seen: "Ongoing", "Past"
        "keywords": "",
    },
    "confirmed_total_ongoing": 25,
    "has_detail_pages": False,
}