"""
Unified CLI Entry Point for Network Reverse-Engineering & API Extraction Pipeline.

Usage:
    # 1. Analyze HAR traffic capture:
    python main.py analyze path/to/capture.har -o output_schemas/har_report.xlsx

    # 2. Run live recursive collector with custom endpoint and proxy:
    python main.py collect --endpoint "https://api.example.com/v1" --html page.html --proxy "socks5h://127.0.0.1:9050" -o output_schemas/records.xlsx

    # 3. Run full pipeline:
    python main.py full path/to/capture.har --endpoint "https://api.example.com/v1" --html page.html
"""

import argparse
import logging
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

# Setup structured logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("PipelineCLI")


def run_har_analysis(har_path: str, output_path: str) -> bool:
    logger.info("=" * 50)
    logger.info("STAGE 1: HAR FILE NETWORK REVERSE-ENGINEERING")
    logger.info("=" * 50)

    try:
        logger.info("Reading HAR file: %s", har_path)
        records = parse_har_file(har_path)
        logger.info("-> %d network calls found", len(records))

        logger.info("Detecting systems (grouping by host)")
        grouped = group_by_system(records)
        for host, recs in grouped.items():
            logger.info("  - %s: %d calls", host, len(recs))

        logger.info("Extracting structured data per system/endpoint")
        system_endpoint_data = {
            host: extract_records(recs) for host, recs in grouped.items()
        }
        summaries = summarize_systems(grouped)

        logger.info("Scanning for security findings")
        findings = scan_vulns(records)
        finding_summary = summarize_findings(findings)
        high = sum(1 for f in findings if f["severity"] == "high")
        logger.info("-> %d findings (%d high severity)", len(findings), high)

        logger.info("Scanning for bot-defense signals")
        bot_findings = scan_bot_defense(records)
        automation = automatable_endpoints(records, bot_findings)
        blocked = sum(1 for a in automation if a["automation_status"] == "blocked")
        logger.info("-> %d bot-defense signals, %d endpoint(s) likely blocked", len(bot_findings), blocked)

        logger.info("Generating cURL snippets and building audit report")
        snippets = generate_snippets(records)

        output_dir = os.path.dirname(os.path.abspath(output_path))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

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
        logger.info("[+] HAR Audit Complete. Report saved to: %s", output_path)
        return True
    except Exception:
        logger.exception("[!] HAR analysis failed with an exception.")
        return False


def interactive_token_refresh(html_file: str, max_retries: int = 5):
    """Callback function triggered mid-loop with safety escape hatch and retry cap."""
    logger.warning("=" * 50)
    logger.warning("ACTION REQUIRED: SESSION TOKENS EXPIRING SOON")
    logger.warning("=" * 50)
    logger.warning("1. Refresh the session page in your browser.")
    logger.warning("2. Save the updated page source as '%s'.", html_file)
    logger.warning("Type 'q' or 'quit' at the prompt to abort.")
    logger.warning("=" * 50)

    attempts = 0
    while attempts < max_retries:
        user_input = input(f"Press ENTER when updated (attempt {attempts+1}/{max_retries}), or 'q' to quit: ").strip()
        if user_input.lower() in ("q", "quit"):
            logger.error("[!] Token refresh aborted by user.")
            return None, None

        try:
            creds = extract_visualforce_credentials(html_file)
            exp = get_jwt_expiration(creds["authorization"])
            if exp is None:
                logger.error("[!] Could not parse expiration claim from JWT token.")
                attempts += 1
                continue

            time_left = exp - int(time.time())
            if time_left > 60:
                logger.info("[+] Success! New token loaded (Valid for %d minutes).", time_left // 60)
                return creds["csrf"], creds["authorization"]
            else:
                logger.warning("[!] Tokens in file are still near expiration (%ds left). Refresh page again.", time_left)
        except Exception as e:
            logger.error("[!] Parsing error: %s. Ensure file is completely saved.", e)
        
        attempts += 1

    logger.error("[!] Max token refresh retries reached. Aborting.")
    return None, None


def run_live_collection(endpoint_url: str, html_file: str, output_path: str, proxy_url: str = None) -> bool:
    logger.info("=" * 50)
    logger.info("STAGE 2: LIVE RECURSIVE DATA EXTRACTION")
    logger.info("=" * 50)

    if not Path(html_file).exists():
        logger.error("[!] Error: File '%s' not found.", html_file)
        return False

    logger.info("Extracting session tokens from %s...", html_file)
    try:
        creds = extract_visualforce_credentials(html_file)
    except Exception:
        logger.exception("[ERROR] Token extraction failed.")
        return False

    exp = get_jwt_expiration(creds["authorization"])
    if exp is None:
        logger.error("[ERROR] Failed to extract expiration timestamp from JWT.")
        return False

    time_left = exp - int(time.time())
    logger.info("Session extracted. JWT valid for %d minutes.", time_left // 60)

    if time_left < 60:
        logger.error("[!] JWT is expired or expiring in <60s. Refresh page.html before proceeding.")
        return False

    def token_refresh_wrapper():
        csrf, auth = interactive_token_refresh(html_file)
        if not auth:
            raise RuntimeError("Token refresh failed or aborted.")
        return csrf, auth

    client = RPCClient(
        endpoint_url=endpoint_url,
        csrf_token=creds["csrf"],
        jwt_token=creds["authorization"],
        token_refresh_func=token_refresh_wrapper,
        proxy_url=proxy_url,
    )
    engine = FanoutEngine(api_client=client, limit=100, max_depth=6)

    logger.info("Launching Fanout Engine...")
    try:
        raw_records = engine.run()
    except Exception:
        logger.exception("[!] Fanout engine execution failed.")
        return False

    if not raw_records:
        logger.warning("[!] Extraction yielded 0 records. Process terminated.")
        return False

    logger.info("Post-Processing: Cleaning & deduplicating records...")
    processor = DataProcessor()
    clean_records = processor.deduplicate(raw_records, primary_key="licNo")

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    processor.export_to_excel(clean_records, output_path)
    logger.info("[+] Extraction Complete! %d unique records exported to %s", len(clean_records), output_path)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Master CLI for Reverse-Engineering HAR traffic and API Fan-out Collection."
    )
    
    default_endpoint = os.environ.get("TARGET_ENDPOINT", "https://api.example.com/v1/endpoint")
    parser.add_argument(
        "--endpoint",
        default=default_endpoint,
        help="Target API endpoint URL (or set TARGET_ENDPOINT environment variable)"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_analyze = subparsers.add_parser("analyze", help="Audit a HAR network capture file.")
    parser_analyze.add_argument("har_file", help="Path to exported .har file")
    parser_analyze.add_argument(
        "-o", "--output", default="output_schemas/har_report.xlsx", help="Output report path"
    )

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
        help="Proxy URL (e.g., socks5h://127.0.0.1:9050)",
    )

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

    success = True
    if args.command == "analyze":
        if not Path(args.har_file).exists():
            logger.error("File not found: %s", args.har_file)
            sys.exit(1)
        success = run_har_analysis(args.har_file, args.output)

    elif args.command == "collect":
        success = run_live_collection(args.endpoint, args.html, args.output, args.proxy)

    elif args.command == "full":
        if not Path(args.har_file).exists():
            logger.error("File not found: %s", args.har_file)
            sys.exit(1)
        success = run_har_analysis(args.har_file, args.har_output)
        if success:
            success = run_live_collection(args.endpoint, args.html, args.collect_output, args.proxy)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
