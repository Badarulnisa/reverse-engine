import json
from app.core.har_parser import parse_har_file

records = parse_har_file("dmcc.ae.har")

target_rec = None
for rec in records:
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        target_rec = rec
        break

if not target_rec:
    print("[!] No apexremote POST found.")
    exit(1)

req_body = target_rec.get("request_body")
print("[+] Parsed request_body from HAR:")
print(json.dumps(req_body, indent=2))