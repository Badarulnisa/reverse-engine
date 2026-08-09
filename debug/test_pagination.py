import json
from app.core.har_parser import parse_har_file
import requests

records = parse_har_file("dmcc.ae.har")

target_rec = None
for rec in records:
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        target_rec = rec
        break

url = target_rec.get("url")
raw_headers = target_rec.get("request_headers", {})
DROP_HEADERS = {"content-length", "host"}
headers = {
    k: v for k, v in raw_headers.items()
    if not k.startswith(":") and k.lower() not in DROP_HEADERS
}
ctx = target_rec.get("request_body", {}).get("ctx")

session = requests.Session()
session.headers.update(headers)

def try_call(second_param, label):
    payload = [{
        "action": "DMCCPublicDirectoryPage_Ctrl",
        "method": "searchCustomerDirectory",
        "data": ["a", second_param],
        "type": "rpc",
        "tid": 1,
        "ctx": ctx
    }]
    r = session.post(url, json=payload, timeout=60)
    try:
        parsed = r.json()
        result = parsed[0].get("result")
        if isinstance(result, list):
            first_name = result[0].get("customerName") if result else None
            print(f"[{label}] status={parsed[0].get('statusCode')} count={len(result)} first={first_name!r}")
        else:
            print(f"[{label}] status={parsed[0].get('statusCode')} result type={type(result)}: {str(result)[:200]}")
    except Exception as e:
        print(f"[{label}] ERROR: {e} -- raw: {r.text[:300]}")

try_call("", "empty string (baseline)")
try_call("1", "page=1 as string")
try_call("2", "page=2 as string")
try_call(1, "page=1 as int")
try_call(2, "page=2 as int")
try_call("100", "offset=100 as string")
try_call(100, "offset=100 as int")