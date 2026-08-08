
import requests
import time
import json
from app.core.har_parser import parse_har_file

print("[*] Parsing HAR file to extract valid session context...")
records = parse_har_file("dmcc.ae.har")
target_rec = None
for rec in records:
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        body = rec.get("request_body")
        if body:
            bodies = body if isinstance(body, list) else [body]
            for b in bodies:
                if b.get("method") == "searchCustomerDirectory":
                    target_rec = rec
                    saved_payload = b
                    break
        if target_rec:
            break

if not target_rec:
    print("[!] Could not find a valid apexremote request in HAR.")
    exit(1)

url = target_rec.get("url")
headers = target_rec.get("headers", {})
headers.pop("Content-Length", None)
headers.pop("Host", None)

# Extract the working ctx from the HAR request body
ctx = saved_payload.get("ctx", {})

session = requests.Session()
session.headers.update(headers)

# Test query with prefix "a"
payload = [
    {
        "action": "DMCCPublicDirectoryPage_Ctrl",
        "method": "searchCustomerDirectory",
        "data": ["a", str(int(time.time() * 1000))],
        "type": "rpc",
        "tid": 1,
        "ctx": ctx
    }
]

print("[*] Sending test request with valid context...")
response = session.post(url, json=payload, timeout=30)
print("Status Code:", response.status_code)

res_json = response.json()
result_data = []
if isinstance(res_json, list) and len(res_json) > 0:
    result_data = res_json[0].get("result", [])
elif isinstance(res_json, dict):
    result_data = res_json.get("result", [])

print(f"[+] Success! Retrieved {len(result_data)} records for prefix \"a\".")
if result_data:
    print("Sample company:", result_data[0].get("customerName"))

