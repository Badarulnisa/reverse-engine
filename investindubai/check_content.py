import json

with open('visitdubai_brute_shard0.json', encoding='utf-8') as f:
    data = json.load(f)

ids = data['seen_identifiers']
print('Total seen:', len(ids))
print('Sample entries:', ids[:5])
print('Type of entries:', type(ids[0]) if ids else None)