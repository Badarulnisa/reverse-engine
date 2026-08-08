
from app.core.har_parser import parse_har_file
import json

records = parse_har_file("dmcc.ae.har")
count = 0
for rec in records:
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        body = rec.get("request_body")
        if body:
            # Handle if body is a list or dict
            bodies = body if isinstance(body, list) else [body]
            for b in bodies:
                if b.get("method") == "searchCustomerDirectory":
                    count += 1
                    print(f"--- Request {count} ---")
                    print(json.dumps(b, indent=2))
                    if count >= 5:  # Show first 5 examples
                        exit(0)

