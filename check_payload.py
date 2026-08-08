
from app.core.har_parser import parse_har_file
import json

records = parse_har_file("dmcc.ae.har")
for rec in records:
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        print(json.dumps(rec.get("request_body"), indent=2))
        break

