import requests
r = requests.get("https://www.dfsa.ae/public-register/firms/julius-baer-middle-east-limited")
print(r.status_code)
print("Bryan Dale Stirewalt" in r.text)
print("Reference Number" in r.text or "reference number" in r.text.lower())