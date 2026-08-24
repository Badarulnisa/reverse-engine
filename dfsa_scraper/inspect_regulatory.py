html = open("al_ramz_page.html", encoding="utf-8").read()
idx = html.find("Regulatory Actions")
if idx == -1:
    print('STRING "Regulatory Actions" NOT FOUND IN PAGE AT ALL')
    # also check a couple of plausible variants in case of casing/wording differences
    for variant in ["regulatory actions", "Regulatory Action", "regulatory-actions", "id=\"regulatory\""]:
        i2 = html.lower().find(variant.lower())
        print(f'  variant {variant!r} found at index: {i2}')
else:
    print(html[max(0, idx - 200): idx + 3000])