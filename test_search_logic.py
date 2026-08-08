
import requests
import json
from app.core.har_parser import parse_har_file

print("[*] Parsing HAR file...")
records = parse_har_file("dmcc.ae.har")
target_rec = next((r for r in records if "apexremote" in r.get("url", "") and r.get("method") == "POST"), None)
saved_payload = None

if target_rec:
    body = target_rec.get("request_body")
    if body:
        bodies = body if isinstance(body, list) else [body]
        for b in bodies:
            if b.get("method") == "searchCustomerDirectory":
                saved_payload = b
                break

url = target_rec.get("url")
headers = target_rec.get("headers", {})
headers.pop("Content-Length", None)
headers.pop("Host", None)
ctx = saved_payload.get("ctx", {})

session = requests.Session()
session.headers.update(headers)

# Let us test known data, a single prefix, and an empty wildcard search
test_queries = [
    ["HYPERPAY", ""],
    ["a", ""],
    ["", ""]
]

for q in test_queries:
    payload = [{
        "action": "DMCCPublicDirectoryPage_Ctrl",
        "method": "searchCustomerDirectory",
        "data": q,
        "type": "rpc",
        "tid": 1,
        "ctx": ctx
    }]
    
    response = session.post(url, json=payload, timeout=30)
    res_json = response.json()
    
    result_data = []
    if isinstance(res_json, list) and len(res_json) > 0:
        result_data = res_json[0].get("result", [])
        
    print(f"[*] Query {q} returned {len(result_data)} records.")
    if result_data:
        print(f"    -> Sample: {result_data[0].get('customerName')}")

