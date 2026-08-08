
import os
import pandas as pd
from app.core.har_parser import parse_har_file
from app.collectors.extractor import find_data_rows, flatten

har_path = "dmcc.ae.har"
print(f"Reading HAR file: {har_path}")
records = parse_har_file(har_path)

all_rows = []
for rec in records:
    if "dmccsf.my.salesforce-sites.com" in rec.get("url", ""):
        body = rec.get("response_body")
        if body:
            rows = find_data_rows(body)
            for row in rows:
                flat = flatten(row)
                flat["_url"] = rec.get("url")
                flat["_status"] = rec.get("status")
                all_rows.append(flat)

if all_rows:
    df = pd.DataFrame(all_rows)
    os.makedirs("output_schemas", exist_ok=True)
    output_path = "output_schemas/extracted_data.xlsx"
    df.to_excel(output_path, index=False)
    print(f"[+] Success! Extracted {len(df)} rows into {output_path}")
else:
    print("[!] No records found for that host.")

