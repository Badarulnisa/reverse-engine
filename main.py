"""
Unified CLI Entry Point for Network Reverse-Engineering & API Extraction Pipeline.

Usage:
    # 1. Analyze HAR traffic capture:
    python main.py analyze path/to/capture.har -o output_schemas/har_report.xlsx

    # 2. Run live recursive collector with optional proxy:
    python main.py collect --html page.html --proxy "socks5h://127.0.0.1:9050" -o output_schemas/records.xlsx

    # 3. Run full pipeline (HAR analysis + live extraction):
    python main.py full path/to/capture.har --html page.html --proxy "socks5h://127.0.0.1:9050"
"""

import argparse
import os
import sys
import time
from pathlib import Path

# --- HAR Analysis Imports ---
from app.collectors.extractor import extract_records
from app.core.bot_defense_scanner import automatable_endpoints, scan_records as scan_bot_defense
from app.core.curl_generator import generate_snippets
from app.core.har_parser import parse_har_file
from app.core.report_builder import build_report
from app.core.system_detector import group_by_system, summarize_systems
from app.core.vuln_scanner import scan_records as scan_vulns, summarize_findings

# --- Collector Pipeline Imports ---
from generated_collectors.api_client import RPCClient
from generated_collectors.data_cleaner import DataProcessor
from generated_collectors.fanout_engine import FanoutEngine
from generated_collectors.token_manager import extract_visualforce_credentials, get_jwt_expiration

# Configure target endpoint URL
ENDPOINT_URL = "https://api.example.com/v1/endpoint"


def run_har_analysis(har_path: str, output_path: str) -> str:
    print("\n" + "=" * 60)
    print(" STAGE 1: HAR FILE NETWORK REVERSE-ENGINEERING")
    print("=" * 60)

    print(f"[1/6] Reading HAR file: {har_path}")
    records = parse_har_file(har_path)
    print(f"      -> {len(records)} network calls found")

    print("[2/6] Detecting systems (grouping by host)")
    grouped = group_by_system(records)
    for host, recs in grouped.items():
        print(f"      - {host}: {len(recs)} calls")

    print("[3/6] Extracting structured data per system/endpoint")
    system_endpoint_data = {
        host: extract_records(recs) for host, recs in grouped.items()
    }
    summaries = summarize_systems(grouped)

    print("[4/6] Scanning for security findings")
    findings = scan_vulns(records)
    finding_summary = summarize_findings(findings)
    high = sum(1 for f in findings if f["severity"] == "high")
    print(f"      -> {len(findings)} findings ({high} high severity)")

    print("[5/6] Scanning for bot-defense signals")
    bot_findings = scan_bot_defense(records)
    automation = automatable_endpoints(records, bot_findings)
    blocked = sum(1 for a in automation if a["automation_status"] == "blocked")
    print(f"      -> {len(bot_findings)} bot-defense signals, {blocked} endpoint(s) likely blocked")

    print("[6/6] Generating cURL snippets and building audit report")
    snippets = generate_snippets(records)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
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
    print(f"[+] HAR Audit Complete. Report saved to: {output_path}\n")
    return output_path


def interactive_token_refresh(html_file: str):
    """Callback function triggered mid-loop when JWT token approaches expiration."""
    print("\n" + "=" * 50)
    print(" ACTION REQUIRED: SESSION TOKENS EXPIRING SOON")
    print("=" * 50)
    print("1. Refresh the session page in your browser.")
    print("2. Save the updated page source.")
    print(f"3. Overwrite '{html_file}' in this directory.")
    print("=" * 50)

    while True:
        input(f"Press ENTER when the updated {html_file} is saved... ")
        try:
            creds = extract_visualforce_credentials(html_file)
            exp = get_jwt_expiration(creds["authorization"])
            time_left = exp - int(time.time())

            if time_left > 60:
                print(f"[+] Success! New token loaded (Valid for {time_left // 60} minutes).")
                return creds["csrf"], creds["authorization"]
            else:
                print("[!] Tokens in file are still near expiration. Refresh page again.")
        except Exception as e:
            print(f"[!] Parsing error: {e}. Ensure file is completely saved.")


def run_live_collection(html_file: str, output_path: str, proxy_url: str = None):
    print("\n" + "=" * 60)
    print(" STAGE 2: LIVE RECURSIVE DATA EXTRACTION")
    print("=" * 60)

    if not Path(html_file).exists():
        print(f"[!] Error: File '{html_file}' not found.")
        sys.exit(1)

    print(f"[*] Extracting session tokens from {html_file}...")
    try:
        creds = extract_visualforce_credentials(html_file)
    except Exception as e:
        print(f"[ERROR] Token extraction failed: {e}")
        sys.exit(1)

    exp = get_jwt_expiration(creds["authorization"])
    time_left = exp - int(time.time())
    print(f"[*] Session extracted. JWT valid for {time_left // 60} minutes.")

    if time_left < 60:
        print("[!] JWT is expired or expiring in <60s. Refresh page.html before proceeding.")
        sys.exit(1)

    # Initialize client with optional proxy routing and retry handler
    client = RPCClient(
        endpoint_url=ENDPOINT_URL,
        csrf_token=creds["csrf"],
        jwt_token=creds["authorization"],
        token_refresh_func=lambda: interactive_token_refresh(html_file),
        proxy_url=proxy_url,
    )
    engine = FanoutEngine(api_client=client, limit=100, max_depth=6)

    print("[*] Launching Fanout Engine...")
    raw_records = engine.run()

    if not raw_records:
        print("[!] Extraction yielded 0 records. Process terminated.")
        return

    print("\n[*] Post-Processing: Cleaning & deduplicating records...")
    processor = DataProcessor()
    clean_records = processor.deduplicate(raw_records, primary_key="licNo")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    processor.export_to_excel(clean_records, output_path)
    print(f"[+] Extraction Complete! {len(clean_records)} unique records exported to {output_path}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Master CLI for Reverse-Engineering HAR traffic and API Fan-out Collection."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Mode 1: HAR Analysis
    parser_analyze = subparsers.add_parser("analyze", help="Audit a HAR network capture file.")
    parser_analyze.add_argument("har_file", help="Path to exported .har file")
    parser_analyze.add_argument(
        "-o", "--output", default="output_schemas/har_report.xlsx", help="Output report path"
    )

    # Mode 2: Live Collection
    parser_collect = subparsers.add_parser("collect", help="Execute recursive data scraper.")
    parser_collect.add_argument(
        "--html", default="page.html", help="Path to page source (default: page.html)"
    )
    parser_collect.add_argument(
        "-o", "--output", default="output_schemas/records.xlsx", help="Output dataset path"
    )
    parser_collect.add_argument(
        "--proxy",
        default=None,
        help="Proxy URL (e.g., http://user:pass@host:port or socks5h://127.0.0.1:9050)",
    )

    # Mode 3: Full Pipeline
    parser_full = subparsers.add_parser("full", help="Run HAR audit followed by live collection.")
    parser_full.add_argument("har_file", help="Path to exported .har file")
    parser_full.add_argument("--html", default="page.html", help="Path to page source")
    parser_full.add_argument(
        "--har-output", default="output_schemas/har_report.xlsx", help="Path for HAR audit report"
    )
    parser_full.add_argument(
        "--collect-output", default="output_schemas/records.xlsx", help="Path for dataset output"
    )
    parser_full.add_argument(
        "--proxy",
        default=None,
        help="Proxy URL (e.g., socks5h://127.0.0.1:9050)",
    )

    args = parser.parse_args()

    if args.command == "analyze":
        if not Path(args.har_file).exists():
            print(f"File not found: {args.har_file}", file=sys.stderr)
            sys.exit(1)
        run_har_analysis(args.har_file, args.output)

    elif args.command == "collect":
        run_live_collection(args.html, args.output, args.proxy)

    elif args.command == "full":
        if not Path(args.har_file).exists():
            print(f"File not found: {args.har_file}", file=sys.stderr)
            sys.exit(1)
        run_har_analysis(args.har_file, args.har_output)
        run_live_collection(args.html, args.collect_output, args.proxy)


if __name__ == "__main__":
    main()