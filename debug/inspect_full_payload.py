import json
from app.core.har_parser import parse_har_file
import requests

records = parse_har_file("dmcc.ae.har")

target_rec = None
for rec in records:
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        target_rec = rec
        break

if not target_rec:
    print("[!] No apexremote POST found.")
    exit(1)

url = target_rec.get("url")
raw_headers = target_rec.get("request_headers", {})
DROP_HEADERS = {"content-length", "host"}
headers = {
    k: v for k, v in raw_headers.items()
    if not k.startswith(":") and k.lower() not in DROP_HEADERS
}

req_body = target_rec.get("request_body")
ctx = req_body.get("ctx")

session = requests.Session()
session.headers.update(headers)

payload = [
    {
        "action": "DMCCPublicDirectoryPage_Ctrl",
        "method": "searchCustomerDirectory",
        "data": ["a", ""],
        "type": "rpc",
        "tid": 1,
        "ctx": ctx
    }
]

print(f"[+] POSTing to {url} with real ctx from HAR...")
response = session.post(url, json=payload, timeout=60)
print(f"[+] Status code: {response.status_code}")

try:
    parsed = response.json()
    print("[+] Parsed response (first 3000 chars):")
    print(json.dumps(parsed, indent=2)[:3000])
    if isinstance(parsed, list) and parsed:
        result = parsed[0].get("result")
        if isinstance(result, list):
            print(f"\n[+] result is a LIST with {len(result)} items.")
        elif isinstance(result, dict):
            print(f"\n[+] result is a DICT with keys: {list(result.keys())}")
except Exception as e:
    print(f"[!] Could not parse JSON: {e}")
    print(response.text[:2000])