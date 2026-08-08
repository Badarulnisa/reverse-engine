
from app.core.har_parser import parse_har_file

records = parse_har_file("dmcc.ae.har")
for rec in records:
    if "dmccsf.my.salesforce-sites.com" in rec.get("url", ""):
        body = rec.get("response_body")
        if body and "HYPERPAY" in str(body):
            print("[+] Found target record in URL:", rec.get("url"))
            print("[+] Raw body snippet:")
            import json
            print(json.dumps(body, indent=2)[:1000]) # Print first 1000 chars of JSON
            break

