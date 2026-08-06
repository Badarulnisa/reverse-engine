# Integrating into your reverse-engine project

Drop these files into your existing tree at these exact paths (they match your layout):

- app/core/har_parser.py
- app/core/system_detector.py
- app/core/report_builder.py
- app/collectors/extractor.py
- app/main.py

Then add to requirements.txt:

    pydantic>=2.6
    pydantic-settings>=2.2
    openpyxl>=3.1

Install:

    pip install -r requirements.txt

Run against a HAR file (export one from Chrome DevTools: Network tab -> right-click -> "Save all as HAR"):

    python -m app.main path\to\capture.har -o report.xlsx

sample_report.xlsx in this folder was generated from a synthetic 2-system HAR
(a shop API and an internal auth API) so you can see the output shape before
running it on real data: one Overview sheet with per-system call counts,
plus one sheet per system+endpoint with every field flattened into columns.

## How it decides what's a "system"
Right now system = hostname (api.shop.example.com vs auth.internal.example.com).
That's the fast, reliable signal HAR gives you for free. If you also want it to
split systems by things like auth scheme (Bearer vs API-key vs cookie) or by
URL path prefix (e.g. /v1/ vs /v2/ vs /internal/), that's a small addition to
system_detector.py — happy to add it once you've run this on a real capture
and see how the hosts actually break down.

## Known gaps to be upfront about
- Binary/non-JSON response bodies (images, protobuf) are skipped, not extracted.
- Pagination isn't stitched together yet — each page is separate rows, not merged.
- Auth token values ARE captured in request_headers if present in the HAR; consider
  scrubbing those before sharing the report with anyone else.

## Vulnerability scanning (new)

app/core/vuln_scanner.py adds a passive security pass over the same captured
HAR data -- it does not send any requests anywhere, it only inspects what's
already in the file. Checks include:

- missing security headers (HSTS, CSP, X-Content-Type-Options, X-Frame-Options)
- secrets/tokens in response or request bodies (AWS keys, Stripe keys, JWTs,
  Slack tokens, Google API keys, private key blocks, generic api_key fields)
- cookies missing Secure / HttpOnly / SameSite
- CORS wildcard origin combined with allow-credentials: true
- plain HTTP instead of HTTPS
- verbose error bodies (stack traces, SQL errors, debug flags) on 4xx/5xx

Report now includes two more sheets: "Security Summary" (counts by severity
per system) and "Security Findings" (every finding, sorted high->low, with
the exact call it came from). Secrets are masked in the evidence column
(first 6 / last 4 chars only) so the report itself isn't a new leak vector.

sample_report_with_vulns.xlsx shows this against a HAR with planted issues
(exposed key, permissive CORS, plaintext HTTP call).

Severity labels are a coarse triage aid, not a CVSS score -- verify manually
before treating any finding as confirmed, and note the false-negative risk:
this only catches known patterns, it's not exhaustive.

## Bot-defense detection + request snippets (new)

app/core/bot_defense_scanner.py flags CAPTCHA challenges (reCAPTCHA, hCaptcha,
Turnstile, FunCaptcha/Arkose), known bot-management vendors (Cloudflare,
Akamai, PerimeterX, DataDome, FingerprintJS), and requests that depend on
short-lived values (csrf/nonce/timestamp/signature params) that will break
on naive replay. Like vuln_scanner, it's passive-only -- it reads what's
already in the HAR, it doesn't solve or bypass anything.

app/core/curl_generator.py turns each distinct endpoint into a ready-to-run
cURL command and Python requests snippet. Sensitive headers (Authorization,
Cookie, API keys) are redacted to placeholders by default -- fill them in
yourself before running, don't paste live tokens into a report you might
share or commit.

Three more report sheets: "Automation Status" (per-endpoint: likely
automatable / fragile / blocked), "Bot Defense Findings" (every signal,
worst-first), and "Request Snippets" (curl + requests code per endpoint).

sample_report_full.xlsx shows the complete report -- vuln scan, bot-defense
scan, and snippets together -- against a HAR with a CAPTCHA challenge page,
a Cloudflare bot-management cookie, and a CSRF-token-dependent request.

Bug fix in this pass: har_parser.py now also keeps the *raw* response text
(response_text_raw) alongside the parsed JSON. Non-JSON responses (HTML
CAPTCHA pages, plaintext error pages) were silently invisible to the
pattern-based scanners before this -- worth knowing if you already ran the
tool against real captures, since HTML-based challenge pages and error
pages may have been under-reported.
