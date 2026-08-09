import os
import json
import requests
from app.core.har_parser import parse_har_file

print("[*] Parsing HAR file...")
records = parse_har_file("dmcc.ae.har")

target_rec = None
for rec in records:
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        target_rec = rec
        break

if not target_rec:
    print("[!] No apexremote POST found in HAR.")
    exit(1)

url = target_rec.get("url")
raw_headers = target_rec.get("request_headers", {})
DROP_HEADERS = {"content-length", "host"}
headers = {
    k: v for k, v in raw_headers.items()
    if not k.startswith(":") and k.lower() not in DROP_HEADERS
}

session = requests.Session()
session.headers.update(headers)

payload = [
    {
        "action": "DMCCPublicDirectoryPage_Ctrl",
        "method": "searchCustomerDirectory",
        "data": ["a", 1, 100],
        "type": "rpc",
        "tid": 1
    }
]

print(f"[+] POSTing to {url}")
response = session.post(url, json=payload, timeout=60)
print(f"[+] Status code: {response.status_code}")
print(f"[+] Raw response body:\n{response.text[:2000]}")