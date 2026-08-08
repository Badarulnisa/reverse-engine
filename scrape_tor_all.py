
import os
import requests
import json
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from app.core.har_parser import parse_har_file

print("[*] Parsing HAR file for session headers and endpoint template...")
har_path = "dmcc.ae.har"
records = parse_har_file(har_path)

target_rec = None
for rec in records:
    if "apexremote" in rec.get("url", "") and rec.get("method") == "POST":
        target_rec = rec
        break

if not target_rec:
    print("[!] Error: Could not find the Salesforce apexremote POST call in the HAR file.")
    exit(1)

url = target_rec.get("url")
headers = target_rec.get("headers", {})
headers.pop("Content-Length", None)
headers.pop("Host", None)

print(f"[+] Target Endpoint: {url}")
print("[*] Configuring Tor proxy at socks5h://127.0.0.1:26000...")

session = requests.Session()
session.headers.update(headers)
session.proxies = {
    "http": "socks5h://127.0.0.1:26000",
    "https": "socks5h://127.0.0.1:26000"
}

all_extracted_rows = []
page = 1
page_size = 100
max_pages = 300

print("[*] Starting full pagination extraction through Tor...")

for page in range(1, max_pages + 1):
    payload = [
        {
            "action": "DMCCPublicDirectoryPage_Ctrl",
            "method": "searchCustomerDirectory",
            "data": ["", page, page_size],
            "type": "rpc",
            "tid": page
        }
    ]

    try:
        response = session.post(url, json=payload, timeout=60)
        if response.status_code != 200:
            print(f"[!] HTTP Error {response.status_code} on page {page}")
            break
            
        res_json = response.json()
        result_data = []
        if isinstance(res_json, list) and len(res_json) > 0:
            result_data = res_json[0].get("result", [])
        elif isinstance(res_json, dict):
            result_data = res_json.get("result", [])

        if not result_data:
            print(f"[*] Reached end of data at page {page}.")
            break

        for row in result_data:
            clean_row = {
                "Company Name (English)": row.get("customerName"),
                "Company Name (Arabic)": row.get("customerArabicName"),
                "License Number": row.get("licNo"),
                "License Issue Date": row.get("licIssueDate"),
                "License Expiry Date": row.get("licExpDate"),
                "License Address (English)": str(row.get("licAddress", "")).replace("<br>", "\n"),
                "License Address (Arabic)": str(row.get("licArabicAddress", "")).replace("<br>", "\n"),
                "License Manager": row.get("licManagerName"),
                "License Manager (Arabic)": row.get("licManagerArabicName"),
                "License Activity": str(row.get("licenseActivities", "")).replace("<br>", "\n"),
                "License Activity (Arabic)": str(row.get("licenseArabicActivities", "")).replace("<br>", "\n"),
                "License Status": row.get("licenseStatus"),
                "Registration Status": row.get("customerRegistrationStatus"),
                "Registration Number": row.get("customerRegistrationNumber"),
                "Registration Date": row.get("customerRegistrationDate")
            }
            all_extracted_rows.append(clean_row)

        print(f"[+] Page {page} fetched via Tor. Total records: {len(all_extracted_rows)}")

        if len(result_data) < page_size:
            print("[*] Final page reached.")
            break

    except Exception as e:
        print(f"[!] Exception on page {page}: {e}")
        break

if all_extracted_rows:
    df = pd.DataFrame(all_extracted_rows)
    os.makedirs("output_schemas", exist_ok=True)
    output_path = "output_schemas/all_26k_companies_tor.xlsx"
    df.to_excel(output_path, index=False)

    wb = load_workbook(output_path)
    ws = wb.active

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9")
    )

    for col in ws.columns:
        col_letter = col[0].column_letter
        max_length = 0
        for cell in col:
            if cell.row == 1:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            else:
                cell.font = Font(name="Calibri", size=10)
                cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
                cell.border = thin_border
            
            val = str(cell.value or "")
            for line in val.split("\n"):
                if len(line) > max_length:
                    max_length = len(line)
                
        adjusted_width = max(max_length + 4, 22)
        if adjusted_width > 55:
            adjusted_width = 55
        ws.column_dimensions[col_letter].width = adjusted_width

    ws.row_dimensions[1].height = 30
    for row in range(2, ws.max_row + 1):
        ws.row_dimensions[row].height = 75

    wb.save(output_path)
    print(f"[+] SUCCESS! Extracted {len(df)} records through Tor into {output_path}")
else:
    print("[!] No records extracted.")

