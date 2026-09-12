from curl_cffi import requests as cffi_requests

session = cffi_requests.Session(impersonate="chrome136")
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Referer": "https://www.investindubai.gov.ae/",
    "Origin": "https://www.investindubai.gov.ae",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
})

# Known-good ID from your earlier keyword-search scraper's confirmed hits
r = session.get("https://api.visitdubai.com/api/dul/GetDulDetails?dul=FI1438", timeout=10)
print("Status:", r.status_code)
print("Body:", r.text[:400])
