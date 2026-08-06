"""
CLI entry point for the reverse-engineering pipeline.

Usage:
    python -m app.main path/to/capture.har -o report.xlsx
"""

import argparse
import sys
from pathlib import Path

from app.collectors.extractor import extract_records
from app.core.bot_defense_scanner import automatable_endpoints
from app.core.bot_defense_scanner import scan_records as scan_bot_defense
from app.core.curl_generator import generate_snippets
from app.core.har_parser import parse_har_file
from app.core.report_builder import build_report
from app.core.system_detector import group_by_system, summarize_systems
from app.core.vuln_scanner import scan_records as scan_vulns
from app.core.vuln_scanner import summarize_findings


def run(har_path: str, output_path: str) -> str:
    print(f"[1/6] Reading HAR file: {har_path}")
    records = parse_har_file(har_path)
    print(f"      -> {len(records)} network calls found")

    print("[2/6] Detecting systems (grouping by host)")
    grouped = group_by_system(records)
    for host, recs in grouped.items():
        print(f"      - {host}: {len(recs)} calls")

    print("[3/6] Extracting structured data per system/endpoint")
    system_endpoint_data = {host: extract_records(recs) for host, recs in grouped.items()}

    summaries = summarize_systems(grouped)

    print("[4/6] Scanning for common security issues (passive, no live requests sent)")
    findings = scan_vulns(records)
    finding_summary = summarize_findings(findings)
    high = sum(1 for f in findings if f["severity"] == "high")
    print(f"      -> {len(findings)} findings ({high} high severity)")

    print("[5/6] Scanning for CAPTCHA / bot-defense signals")
    bot_findings = scan_bot_defense(records)
    automation = automatable_endpoints(records, bot_findings)
    blocked = sum(1 for a in automation if a["automation_status"] == "blocked")
    print(f"      -> {len(bot_findings)} bot-defense signals, {blocked} endpoint(s) likely blocked")

    print("[6/6] Generating cURL / requests snippets and building report")
    snippets = generate_snippets(records)
    build_report(
        summaries,
        system_endpoint_data,
        output_path,
        findings=findings,
        finding_summary=finding_summary,
        bot_defense_findings=bot_findings,
        automation_status=automation,
        snippets=snippets,
    )
    print("Done.")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Reverse-engineer API calls from a HAR file into a complete Excel report."
    )
    parser.add_argument("har_file", help="Path to the .har file exported from browser DevTools")
    parser.add_argument("-o", "--output", default="report.xlsx", help="Output .xlsx path (default: report.xlsx)")
    args = parser.parse_args()

    if not Path(args.har_file).exists():
        print(f"File not found: {args.har_file}", file=sys.stderr)
        sys.exit(1)

    run(args.har_file, args.output)


if __name__ == "__main__":
    main()
