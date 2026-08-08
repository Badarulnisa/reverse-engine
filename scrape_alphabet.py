import requests
import json
import string
import time
from app.core.har_parser import parse_har_file

print("[*] Extracting fresh session context from HAR...")
records = parse_har_file("dmcc.ae.har")
saved_payload = None
url = None
headers = {}

# Find the most recent search request to get the freshest ctx
for rec in reversed(records):
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        body = rec.get("request_body")
        if body:
            bodies = body if isinstance(body, list) else [body]
            for b in bodies:
                if b.get("method") == "searchCustomerDirectory":
                    saved_payload = b
                    url = rec.get("url")
                    headers = rec.get("headers", {})
                    break
        if saved_payload:
            break

if not saved_payload:
    print("[!] Failed to find a valid request. Did you export a fresh HAR?")
    exit(1)

headers.pop("Content-Length", None)
headers.pop("Host", None)
ctx = saved_payload.get("ctx", {})

session = requests.Session()
session.headers.update(headers)

all_results = []
alphabet = list(string.ascii_lowercase)

print("[*] Starting alphabetical prefix extraction...")

for letter in alphabet:
    payload = [{
        "action": "DMCCPublicDirectoryPage_Ctrl",
        "method": "searchCustomerDirectory",
        "data": [letter, ""],  # Leave license number blank
        "type": "rpc",
        "tid": 1,
        "ctx": ctx
    }]
    
    try:
        response = session.post(url, json=payload, timeout=15)
        res_json = response.json()
        
        result_data = []
        if isinstance(res_json, list) and len(res_json) > 0:
            first_record = res_json[0]
            
            # Catch Salesforce Visualforce/Apex exceptions
            if first_record.get("type") == "exception":
                print(f"[!] Salesforce Exception on '{letter}': {first_record.get('message')}")
                # If it's a CSRF/Session issue, break the loop
                if "CSRF" in first_record.get('message', '') or "expired" in first_record.get('message', '').lower():
                    print("[!] Session expired. Stopping.")
                    break
                continue
                
            result_data = first_record.get("result")
            # Handle cases where result is None
            if result_data is None:
                result_data = []
                
        print(f"[+] Prefix '{letter}' -> {len(result_data)} records found.")
        all_results.extend(result_data)
        
    except Exception as e:
        print(f"[!] Network/JSON Error on prefix '{letter}': {e}")
        break
    
    time.sleep(0.5)

output_file = "dmcc_dump.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(all_results, f, indent=4)
    
print(f"\n[*] Finished! Saved {len(all_results)} total records to {output_file}.")