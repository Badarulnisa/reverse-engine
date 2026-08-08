
import requests
from app.core.har_parser import parse_har_file

records = parse_har_file("dmcc.ae.har")
target_rec = next(r for r in records if "apexremote" in r.get("url", "") and r.get("method") == "POST")

url = target_rec.get("url")
headers = target_rec.get("headers", {})
headers.pop("Content-Length", None)
headers.pop("Host", None)

session = requests.Session()
session.headers.update(headers)

payload = [
    {
        "action": "DMCCPublicDirectoryPage_Ctrl",
        "method": "searchCustomerDirectory",
        "data": ["", 1, 100],
        "type": "rpc",
        "tid": 1
    }
]

response = session.post(url, json=payload, timeout=30)
print("Status Code:", response.status_code)
print("Raw Response Text:", response.text[:1000])

