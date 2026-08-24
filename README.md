# public-registry-scraper

![Python](https://img.shields.io/badge/Language-Python-3776AB?style=flat-square&logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Browser-Playwright-2EAD33?style=flat-square&logo=playwright&logoColor=white)
![Tor](https://img.shields.io/badge/Network-Tor%20%2F%20SOCKS5-7D4698?style=flat-square&logo=torproject&logoColor=white)
![API](https://img.shields.io/badge/Data%20Source-Internal%20RPC%20Replay-00A1E0?style=flat-square)
![Category](https://img.shields.io/badge/Category-OSINT%20%7C%20Corporate%20Registry-darkred?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active-brightgreen?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

A full-coverage extraction pipeline for public company registries — solves each registry's own access barrier once (a captcha-gated search form, a hard offset ceiling, an undiscovered internal API), then replays or systematically walks the registry's own backend directly to pull every registered entity. What started as a single-target scraper (DMCC) grew into a small library of registry-specific pipelines, each shaped by whatever that registry's backend actually does under the hood — five so far: **DMCC, DIFC, ADGM, DFSA, and JAFZA/Invest Dubai**.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [The Journey](#the-journey)
  - [DMCC — the origin](#dmcc--the-origin)
  - [DIFC — the prefix-sweep problem](#difc--the-prefix-sweep-problem)
  - [ADGM — the offset ceiling](#adgm--the-offset-ceiling)
  - [DFSA — the server-rendered surprise](#dfsa--the-server-rendered-surprise)
  - [JAFZA / Invest Dubai — the captcha that couldn't be replayed](#jafza--invest-dubai--the-captcha-that-couldnt-be-replayed)
- [Live Monitoring](#live-monitoring)
- [Sample Output](#sample-output)
- [Features](#features)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [Links](#links)
- [License](#license)

---

## Overview

Public company registries rarely expose a bulk export or a documented API. Some hide their search form in a cross-origin iframe behind a captcha. Some cap every query at a fixed number of rows with broken pagination. Some enforce a hard offset ceiling that silently truncates anything past a few thousand results. Each of the five registries in this repo hit a different one of these walls, and each pipeline was shaped by reverse-engineering that specific registry's actual backend behavior — not a generic "scraper template" applied five times.

<p align="center">
  <img src="docs/screenshots/00-public-registry-search.png" alt="A public registry search form, identifying details redacted" width="700"/>
  <br/>
  <sub>The search form and result cards — branding and license identifiers redacted.</sub>
</p>

---

## How It Works

Every registry here needed a different core trick to reach full coverage — captcha replay, name-prefix sweeps, status/category partitioning, or straightforward server-rendered pagination. The shared shell across all five is: capture or discover the real backend call, replay it directly with `requests`, checkpoint every unit of work immediately, and export to a clean Excel workbook.

| Registry | Core obstacle | Core trick |
|---|---|---|
| DMCC | Captcha-gated iframe search, no bulk export | Solve once via browser, replay the captured Visualforce Remoting call |
| DIFC | Backend search cap on result count | Two-letter/numeric name-prefix sweep + license-number range sweep |
| ADGM | Hard ~2,000-row OFFSET ceiling | Partition by Entity Status → Category → name-prefix as a last resort |
| DFSA | No documented API, but fully server-rendered pages | Walk the AJAX listing endpoint directly; detail pages need one plain GET, no browser |
| JAFZA / Invest Dubai | hCaptcha proof-of-work token minted client-side | Let a real browser mint the token, intercept the resulting network call |

<p align="center">
  <img src="docs/screenshots/04-recursive-splitting-concept.png" alt="Recursive term-splitting strategy" width="650"/>
</p>

---

## The Journey

Each registry went through its own real debugging arc. Documented separately below since the dead ends and the fixes were genuinely different each time.

### DMCC — the origin

**1. Started with browser automation (Playwright)** — driving a real Chromium instance through the visible search form, only to discover the form isn't on the visible page at all: it's inside a cross-origin iframe, invisible to naive DOM selectors until specifically targeted with a frame-scoped locator.

**2. Hit reCAPTCHA on every automated attempt.** Retry loops that re-triggered the checkbox repeatedly escalated the session's risk score into the full image-challenge puzzle — which can't and shouldn't be automated around. The fix was behavioral, not technical: stop hammering it, solve once, reuse the trust that earns.

**3. Considered IP rotation via Tor** as a way to keep sessions looking fresh, using `stem` to trigger new circuits over the control port and `curl_cffi` to impersonate a real browser's TLS fingerprint.

<p align="center">
  <img src="docs/screenshots/03-tor-rotation-test.png" alt="Tor circuit rotation test output" width="650"/>
</p>

**4. Pivoted to HAR replay** — captured one legitimate, manually-solved session and replayed its authenticated Visualforce Remoting call directly with `requests`, bypassing the DOM and the captcha entirely for every subsequent query.

<p align="center">
  <img src="docs/screenshots/01-har-session-capture.png" alt="Parsing session headers and signed context out of a captured HAR file" width="650"/>
</p>

**5. Hit a checkpoint-poisoning bug** — early runs marked search terms as "complete" even when the request had failed outright (network error, expired session), silently corrupting future resumes into skipping everything. Fixed by only checkpointing a term after a verified, successful response.

<p align="center">
  <img src="docs/screenshots/02-checkpoint-bug-fix.png" alt="Diagnosing and fixing the checkpoint logic" width="650"/>
</p>

**6. Found the real pagination behavior was fake** — every page/offset parameter returned identical results; the endpoint simply hard-caps results per query.

**7. Landed on alphabetical query iteration with Tor rotation and captcha defense** as the reliable way to reach full registry coverage. Took two full days running sequentially; the resulting scraper is considered frozen and final.

<p align="center">
  <img src="docs/screenshots/06-terminal-recursive-run.png" alt="Recursive term-splitting run in progress, tracking unique running total" width="750"/>
  <br/>
  <sub>Query iteration in action against the Salesforce endpoint — a capped query fans out into deeper sub-queries.</sub>
</p>

Result: **41,000+ businesses** scraped.

### DIFC — the prefix-sweep problem

DIFC's business directory had a claimed 8,000+ entries and, like DMCC, a hidden per-query result cap with no working pagination parameter. The listing pass initially tried simple alphabetical queries — but the directory's naming distribution meant many single-letter prefixes still exceeded the cap on their own.

**1. Two-letter/numeric prefix sweep** — expanded the query space to 686 total prefix combinations, which cracked the cap for the vast majority of the directory.

**2. Residual gap discovered** — even at 686 prefixes, some companies with unusual naming still weren't surfacing in any prefix bucket.

**3. License-number range sweep as a backstop** — swept the full plausible range of license numbers (~1–14,500) directly, catching entries the name-based sweep missed entirely.

**4. Deduplication and enrichment** — merged both sweeps by license number and enriched each hit with the full detail record.

Result: **5,777+ enriched company records** exported to Excel.

### ADGM — the offset ceiling

ADGM's public registrar runs on Salesforce Aura/Experience Cloud, with no authentication required — `aura.token="null"` works for both the search (`RASearchUtil.getSearchResponseForPR`) and detail (`RAPRPageFlowController`, ~18–20 fixed tabs per company) endpoints.

**1. Confirmed the true total** — roughly 18,398–18,452 companies, verified against the registry's own reported count.

**2. Hit Salesforce's OFFSET ceiling** — any query, regardless of filter, silently stopped returning new rows past roughly 2,000 offset, even with results clearly remaining.

**3. Partitioned by Entity Status** — 20 confirmed real status values, each queried independently to reset the offset ceiling per partition.

**4. Partitions still exceeding 2,000 for some statuses** — added a second split by Category (3 values), and a name-prefix sweep as a last-resort third split for any partition still too large.

**5. Built dual-file architecture** — `adgm_scraper.py` as the pure API client module, `run_adgm.py` as the driver/CLI with `--probe`, `--probe-buckets`, and checkpoint/resume support, kept separate so the scraper stays independently testable.

**6. Hit a transient DNS resolution failure mid-run** — a genuine network drop, not a code bug. The checkpoint/retry logic handled it correctly, failing only the affected page for retry on the next run rather than corrupting the whole partition.

Result: near-complete coverage of ADGM's ~18,400 companies, run from `adgm_scraper/` with a live local monitor dashboard.

### DFSA — the server-rendered surprise

DFSA's public register was chosen over the alternative (SHAMS) specifically because it's confirmed, complete, and well-structured — 1,224 firms via faceted search, versus SHAMS's unclear community directory.

**1. Mapped the five sub-registers** — firms (1,224), individuals (4,063), funds (294, plus a `sub_funds/` path variant), passported-funds, and prohibited-individuals (25, ongoing).

**2. Reverse-engineered the AJAX listing contract** — a `getTotal` endpoint returns a JSON count with no CSRF token needed, while the actual paged listing needs a bootstrapped `csrf_token` and returns raw HTML row fragments, 10 per page, walked until an empty page rather than trusting `total // 10`.

**3. Expected detail pages to need browser automation for tabbed content — they didn't.** A plain `requests.get()` on a firm or individual detail page returned the entire tabbed content (Individuals, Regulatory Actions, etc.) in the initial HTML response. The tabs are pure CSS/JS show-hide, not separate AJAX calls, so no Playwright was needed for this stage at all.

**4. Corrected an early parsing assumption** — the initial parser assumed detail-page tables were a concatenated text blob needing regex splitting. The real DOM turned out to use the same clean `<div class="table-row">` positional structure as the listing pages, so positional indexing replaced the regex approach entirely.

**5. Built a real-time control center** — a FastAPI + WebSocket dashboard (`dashboard.py` / `dashboard.html`) that bridges the scraper's synchronous worker thread into the async event loop via a thread-safe queue, showing a live pipeline view (Discovery → Fetch → Parse → Validate → Complete), per-firm status, and a raw-record inspector — while keeping the dashboard entirely optional and decoupled from the scraper's core logic.

**6. Fixed a reconnect race condition** — a client could disconnect mid-replay of the event history (e.g. a page refresh reconnecting before the previous socket had torn down), which raised an uncaught `WebSocketDisconnect` inside the history-replay loop. The scraper itself, running in a separate thread, was unaffected — but the dashboard showed "disconnected" until the exception was caught and the dead connection cleaned up.

### JAFZA / Invest Dubai — the captcha that couldn't be replayed

The Invest Dubai search endpoint includes a `token` field in its request body — an hCaptcha proof-of-work token of type `hsw`, minted entirely client-side by hCaptcha's own JavaScript at the moment a real search fires in the browser.

**1. Attempted direct replay** — same approach as DMCC's captcha bypass — but the token is single-use and tied to a specific challenge state; it can't be forged or pre-generated outside the browser.

**2. Fell back to browser-driven interception** — let a real page perform the search (its own JS mints a valid token naturally), and intercept the resulting network call rather than trying to construct the request from scratch.

**3. Layered in geographic matching for JAFZA enrichment** — a separate `matcher.py` normalizes company names and scores them by core-token overlap (filtering common noise tokens) to reconcile JAFZA listings against Google Places results, pulled via the Places API using a key read from `GOOGLE_MAPS_API_KEY` — never hardcoded.

---

## Live Monitoring

Two of the five scrapers ship with a live dashboard for watching a run in progress.

**ADGM** — a lightweight single-file monitor (`monitor.py`): a background HTTP server on `localhost:8765` serving an auto-refreshing page backed by an in-memory ring buffer of the last 500 events, with request/error/companies-found counters and uptime.

**DFSA** — a fuller FastAPI + WebSocket control center (`dashboard.py` / `dashboard.html`): a five-stage pipeline view, ten live stat cards (discovered, fetched, parsed, completed, resumed-from-checkpoint, failed, requests, retries, saved, save-failures), a live activity feed with expandable raw event JSON, a dedicated error feed, and a company table that opens into a full per-firm detail view — including financial services, individuals, and regulatory actions.

<p align="center">
  <img src="docs/screenshots/07-adgm-monitor-mockup.png" alt="Illustrative mockup of the ADGM monitor dashboard, values are placeholders" width="650"/>
  <br/>
  <sub>ADGM monitor layout — illustrative mockup with placeholder values, not a live capture.</sub>
</p>

<p align="center">
  <img src="docs/screenshots/08-dfsa-control-center-mockup.png" alt="Illustrative mockup of the DFSA control center dashboard, company names redacted" width="650"/>
  <br/>
  <sub>DFSA control center layout — illustrative mockup, company names blurred.</sub>
</p>

Clicking into a company row on the DFSA dashboard opens a full detail view — firm fields, financial service categories, individuals, regulatory actions, and a raw-record JSON inspector for debugging.

<p align="center">
  <img src="docs/screenshots/09-dfsa-company-detail-mockup.png" alt="Illustrative mockup of the DFSA company detail view, values redacted" width="650"/>
  <br/>
  <sub>DFSA company detail view — illustrative mockup, values blurred.</sub>
</p>

---

## Sample Output

Every row carries the full registry schema for that source — English & local-language name, license/reference number, issue/expiry dates, address, activities, and status, plus (for DFSA) individuals and regulatory actions where applicable — deduplicated across every overlapping query or sweep used to reach it.

<p align="center">
  <img src="docs/screenshots/05-output-table-preview.png" alt="Preview of the exported Excel structure, values redacted" width="750"/>
  <br/>
  <sub>Column structure of the final export — sample values shown, real identifiers redacted.</sub>
</p>

---

## Features

| Feature | Detail |
|---|---|
| **Captcha-free bulk extraction** | Solve once manually (DMCC, JAFZA), or skip entirely where no auth is required (ADGM, DFSA) |
| **Registry-specific cap workarounds** | Prefix sweeps, status/category partitioning, or license-range sweeps depending on what each backend actually enforces |
| **Deduplication** | Collapses overlapping matches across queries, sweeps, or partitions by license/reference number |
| **Checkpointing** | Every unit of work (query, partition, firm) is saved immediately — network drops or session expiry never cost lost progress |
| **Resumable sessions** | Drop in a freshly captured HAR (DMCC) or just re-run (ADGM, DFSA) to resume exactly where the last run stopped |
| **Optional Tor routing** | Circuit rotation available for IP-sensitive runs |
| **Live monitoring** | Real-time dashboards for ADGM and DFSA runs |
| **Excel export** | Clean, formatted, auto-sized workbook per registry, ready to hand off |

---

## Project Structure

```
scrape_direct_all.py        DMCC pipeline: query iteration + checkpointing + Excel export
scrape_tor_all.py           Tor-routed variant of the DMCC pipeline (optional IP rotation)
scrape_registry_generic.py  Playwright-based DOM automation fallback (captcha-gated, manual-solve)
env_config.py               shared Playwright environment/proxy/retry configuration
app/core/har_parser.py      normalizes exported .har files into request/response records
difc_scraper.py             DIFC pipeline: prefix sweep + license-number range sweep

adgm_scraper/
  adgm_scraper.py           API client module (Salesforce Aura/Experience Cloud)
  run_adgm.py                driver/CLI: --probe, --probe-buckets, checkpoint/resume
  monitor.py                 lightweight live dashboard (localhost:8765)
  tor_rotation.py             Tor control-port circuit rotation
  test_adgm_tor_integration.py

dfsa_scraper/
  dfsa_common.py             shared session/CSRF/pagination logic across sub-registers
  dfsa_registers.py           register-listing walker
  dfsa_firm_detail.py         firm detail-page parser
  dfsa_individual_detail.py   individual detail-page parser
  dashboard.py                 FastAPI + WebSocket live control center
  dashboard.html                control center frontend
  events.py                    EventBus used to bridge scraper thread -> dashboard
  run_register.py / run_individuals.py / run_scraper.py
  run_scraper_with_dashboard.py

jafza_scrapper/
  config.py                   reads GOOGLE_MAPS_API_KEY from environment
  places_client.py             Google Places enrichment client
  matcher.py                    name-normalization + token-overlap matching for enrichment
  run_enrich.py

scrape_invest_dubai/
  scrape_invest_dubai.py      hCaptcha token interception via browser-driven search

checkpoint.json / *_checkpoint.json    in-progress run state (gitignored)
output/ / output_schemas/               generated Excel output (gitignored)
*.har                                    captured authenticated sessions (gitignored, expire)
```

---

## Installation

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

For JAFZA enrichment, also set a Google Maps API key:

```powershell
$env:GOOGLE_MAPS_API_KEY="your_key_here"
```

---

## Usage

**DMCC / DIFC — HAR-based captcha bypass:**

1. Open the target registry's search page in a real Chrome window
2. DevTools (F12) → Network tab → Preserve log
3. Fill in the search form, solve the captcha manually, submit
4. Right-click the Network panel → **Save all as HAR with content**
5. Save the file in the project root, then run:

```powershell
python scrape_direct_all.py       # DMCC
python difc_scraper.py            # DIFC
```

**ADGM — no auth needed, run directly:**

```powershell
cd adgm_scraper
python run_adgm.py --probe          # verify bucket totals before a full run
python run_adgm.py                  # full run, checkpointed
python monitor.py                   # optional, in a separate terminal — open localhost:8765
```

**DFSA — no auth needed, optional live dashboard:**

```powershell
cd dfsa_scraper
python run_scraper_with_dashboard.py
uvicorn dashboard:app --reload --port 8000   # optional, in a separate terminal — open localhost:8000
```

**JAFZA / Invest Dubai:**

```powershell
cd scrape_invest_dubai
python scrape_invest_dubai.py

cd ../jafza_scrapper
python run_enrich.py
```

Output for each: an Excel workbook in that scraper's own output path. Progress and resumability: each scraper's respective checkpoint file. If a session-bound scraper's session expires mid-run (repeated API error lines instead of new-row counts), recapture a fresh HAR and re-run the same command — nothing already fetched is lost.

---

## Configuration

| Setting | Controls | Applies to |
|---|---|---|
| `CAP` | The endpoint's per-query result cap that triggers query splitting | DMCC, DIFC |
| `search_terms` / prefix set | Starting alphabet/digit/prefix set before splitting kicks in | DMCC, DIFC |
| `depth < 4` (in `process_term`) | Maximum recursion depth for term-splitting, as a safety ceiling | DMCC |
| Entity Status / Category buckets | Partition values used to reset the offset ceiling | ADGM |
| `CHECKPOINT_PATH` / `*_checkpoint.json` | Where in-progress state is saved for resumability | All |
| `GOOGLE_MAPS_API_KEY` | Places enrichment lookups | JAFZA |
| `TOR_CONTROL_PASSWORD` | Optional — only needed if torrc is switched from cookie auth to `HashedControlPassword` | DMCC, ADGM |
| `time.sleep(...)` calls | Pacing between requests | All |

---

## Limitations

| Limitation | Detail |
|---|---|
| **Session-bound (DMCC, JAFZA)** | Requires a manually captured, authenticated HAR session or browser interception — cannot run fully unattended from a cold start |
| **Session lifetime** | Session tokens expire; long runs may need a mid-run HAR recapture |
| **No official API for any registry** | Depends on each target's current internal implementation; a site redesign could break any endpoint contract |
| **Recursion/partition depth ceilings** | Extremely dense query branches or partitions beyond the configured limits could theoretically still be capped |
| **hCaptcha token is single-use** | Invest Dubai's token can't be pre-generated or reused — every search needs a fresh browser-driven interception |

---

## Roadmap

- [ ] Automatic session refresh via a headless captcha-solve fallback for long unattended runs
- [ ] Configurable partition/recursion strategy (skip low-yield branches automatically, based on observed patterns)
- [ ] Cross-run diffing to track new/updated registrations over time
- [ ] Unified live dashboard covering all five registries, not just ADGM and DFSA
- [ ] Additional registries beyond the current five

---

## Links

- [Playwright for Python](https://playwright.dev/python/)
- [HAR (HTTP Archive) format](https://en.wikipedia.org/wiki/HAR_(file_format))
- [Tor Project](https://www.torproject.org/)
- [stem (Tor control library)](https://stem.torproject.org/)
- [FastAPI](https://fastapi.tiangolo.com/)

---

## License

MIT — see `LICENSE`.
