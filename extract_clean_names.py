
import os
import pandas as pd
from app.core.har_parser import parse_har_file
from app.collectors.extractor import find_data_rows

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
                if "customerName" in row or "customerRegistrationNumber" in row:
                    clean_row = {
                        "Company Name (English)": row.get("customerName"),
                        "Company Name (Arabic)": row.get("customerArabicName"),
                        "Registration Number": row.get("customerRegistrationNumber"),
                        "Status": row.get("customerRegistrationStatus"),
                        "Registration Date": row.get("customerRegistrationDate"),
                        "License Number": row.get("licNo"),
                        "Expiry Date": row.get("licExpDate"),
                        "Address": str(row.get("licAddress", "")).replace("<br>", ", ")
                    }
                    all_rows.append(clean_row)

if all_rows:
    df = pd.DataFrame(all_rows)
    os.makedirs("output_schemas", exist_ok=True)
    output_path = "output_schemas/companies_extracted.xlsx"
    df.to_excel(output_path, index=False)
    print(f"[+] Success! Extracted {len(df)} clean company records into {output_path}")
else:
    print("[!] No company records found.")

