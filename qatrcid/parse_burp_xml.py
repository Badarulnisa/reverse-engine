#!/usr/bin/env python3
"""
parse_burp_xml.py

Parses a Burp Suite "Save selected items" / "Save all items" XML export
(the classic <items><item>...</item></items> format with base64-encoded
request/response) and prints a summary of every distinct endpoint hit:
method, path, query params, content-type, response status, and response
size — plus optionally dumps full decoded request/response bodies for
specific items you want to inspect closely.

Usage:
    python parse_burp_xml.py cid.xml                    # summary of all items
    python parse_burp_xml.py cid.xml --full 3            # full decode of item #3
    python parse_burp_xml.py cid.xml --filter search     # only URLs containing "search"
    python parse_burp_xml.py cid.xml --json out.json     # dump structured summary to JSON
"""

import argparse
import base64
import json
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs


def decode_b64_field(el) -> str:
    """Burp XML wraps request/response in <base64="true"> attribute when
    the content is base64-encoded (almost always the case for raw HTTP)."""
    if el is None or el.text is None:
        return ""
    if el.get("base64") == "true":
        try:
            return base64.b64decode(el.text).decode("utf-8", errors="replace")
        except Exception:
            return "(base64 decode failed)"
    return el.text


def split_http_message(raw: str) -> tuple[dict, str]:
    """Very small HTTP/1.1 message splitter: returns (headers_dict, body)."""
    if "\r\n\r\n" in raw:
        head, body = raw.split("\r\n\r\n", 1)
    elif "\n\n" in raw:
        head, body = raw.split("\n\n", 1)
    else:
        head, body = raw, ""
    headers = {}
    for line in head.splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers, body


def parse_items(xml_path: str):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    items = root.findall(".//item")
    parsed = []

    for idx, item in enumerate(items):
        url_el = item.find("url")
        method_el = item.find("method")
        status_el = item.find("status")
        mimetype_el = item.find("mimetype")
        request_el = item.find("request")
        response_el = item.find("response")

        url = url_el.text if url_el is not None else ""
        method = method_el.text if method_el is not None else ""
        status = status_el.text if status_el is not None else ""
        mimetype = mimetype_el.text if mimetype_el is not None else ""

        raw_request = decode_b64_field(request_el)
        raw_response = decode_b64_field(response_el)

        req_headers, req_body = split_http_message(raw_request) if raw_request else ({}, "")
        resp_headers, resp_body = split_http_message(raw_response) if raw_response else ({}, "")

        parsed_url = urlparse(url) if url else None
        query_params = parse_qs(parsed_url.query) if parsed_url else {}

        parsed.append({
            "index": idx,
            "method": method,
            "url": url,
            "path": parsed_url.path if parsed_url else "",
            "query_params": query_params,
            "status": status,
            "mimetype": mimetype,
            "request_content_type": req_headers.get("content-type", ""),
            "request_body_preview": req_body[:300],
            "response_body_size": len(resp_body),
            "response_body_preview": resp_body[:300],
            "_raw_request": raw_request,
            "_raw_response": raw_response,
        })

    return parsed


def print_summary(parsed: list[dict], filter_str: str | None = None):
    print(f"{'#':<4} {'METHOD':<7} {'STATUS':<7} {'TYPE':<20} {'SIZE':<8} URL")
    print("-" * 110)
    for entry in parsed:
        if filter_str and filter_str.lower() not in entry["url"].lower():
            continue
        print(
            f"{entry['index']:<4} {entry['method']:<7} {entry['status']:<7} "
            f"{entry['mimetype'][:18]:<20} {entry['response_body_size']:<8} {entry['url'][:90]}"
        )
        if entry["query_params"]:
            print(f"     params: {entry['query_params']}")
        if entry["request_body_preview"].strip():
            print(f"     body  : {entry['request_body_preview'][:150]!r}")


def print_full(parsed: list[dict], index: int):
    entry = next((e for e in parsed if e["index"] == index), None)
    if not entry:
        print(f"No item at index {index}")
        return
    print(f"=== ITEM {index}: {entry['method']} {entry['url']} ===\n")
    print("--- RAW REQUEST ---")
    print(entry["_raw_request"])
    print("\n--- RAW RESPONSE (first 3000 chars) ---")
    print(entry["_raw_response"][:3000])


def main():
    ap = argparse.ArgumentParser(description="Summarize a Burp Suite XML export")
    ap.add_argument("xml_path", help="Path to the Burp XML export (e.g. cid.xml)")
    ap.add_argument("--full", type=int, metavar="INDEX", help="Print full raw request/response for item INDEX")
    ap.add_argument("--filter", type=str, metavar="TEXT", help="Only show items whose URL contains TEXT")
    ap.add_argument("--json", type=str, metavar="PATH", help="Write structured summary (no raw bodies) to a JSON file")
    args = ap.parse_args()

    parsed = parse_items(args.xml_path)
    print(f"Loaded {len(parsed)} captured item(s) from {args.xml_path}\n")

    if args.full is not None:
        print_full(parsed, args.full)
        return

    print_summary(parsed, filter_str=args.filter)

    if args.json:
        slim = [{k: v for k, v in e.items() if not k.startswith("_raw")} for e in parsed]
        with open(args.json, "w") as f:
            json.dump(slim, f, indent=2, default=str)
        print(f"\nStructured summary written to {args.json}")


if __name__ == "__main__":
    main()
