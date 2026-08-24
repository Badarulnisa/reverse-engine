html = open("al_ramz_page.html", encoding="utf-8").read()

print(f"Total page length: {len(html)}")
print()

# Find every occurrence of "Regulatory Actions", show a short snippet of each
import re
positions = [m.start() for m in re.finditer(re.escape("Regulatory Actions"), html)]
print(f"'Regulatory Actions' appears {len(positions)} time(s), at indices: {positions}")
print()

# Look specifically for the tab pane with id="regulatory" (used by the
# Individuals-table-style parser) -- this is the actual section we need.
idx = html.find('id="regulatory"')
if idx == -1:
    print('No id="regulatory" found on the page at all.')
else:
    print('--- content around id="regulatory" ---')
    print(html[max(0, idx - 100): idx + 3000])