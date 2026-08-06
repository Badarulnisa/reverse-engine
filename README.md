# reverse-engine

Pipeline that reverse-engineers API calls from HAR captures. Given a `.har`
file exported from browser DevTools, it:

1. Parses every network call (`app/core/har_parser.py`)
2. Auto-detects the distinct backend systems involved, by host (`app/core/system_detector.py`)
3. Extracts and flattens the JSON data each system returns, endpoint by endpoint (`app/collectors/extractor.py`)
4. Passively scans the same traffic for common security issues -- exposed
   secrets, missing security headers, weak cookies, CORS misconfig, plaintext
   HTTP, verbose error leaks (`app/core/vuln_scanner.py`)
5. Builds a single Excel report with an Overview, a Security Summary, full
   Security Findings, and one sheet per system/endpoint with every field the
   API returned (`app/core/report_builder.py`)

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

## Usage

Export a HAR file from your browser (DevTools -> Network tab -> right-click -> "Save all as HAR"), then:

```bash
python -m app.main path\to\capture.har -o report.xlsx
```

## Project layout

```
app/
  core/
    config.py           # app settings (pydantic)
    har_parser.py        # HAR -> normalized call records
    system_detector.py   # groups calls by host into "systems"
    vuln_scanner.py       # passive security checks
    report_builder.py     # builds the final .xlsx
  collectors/
    extractor.py          # flattens JSON response bodies into rows
  api/                     # (reserved for a future HTTP API layer)
  db/                      # (reserved for persistence)
  queue/                   # (reserved for async/background processing)
  main.py                  # CLI entry point
tests/
```

## Notes

- HAR files and generated reports are gitignored by default -- they contain
  real captured request/response data (tokens, cookies, PII) and should not
  be committed. Sample reports (`sample_report*.xlsx`) are the exception.
- Vulnerability scanning is passive only: it inspects data already in the
  HAR file and never sends requests of its own. Severity labels are a rough
  triage aid, not a formal CVSS score.
