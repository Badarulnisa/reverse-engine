import json

with open('visitdubai_brute_shard0.json', encoding='utf-8') as f:
    data = json.load(f)

print(type(data))
if isinstance(data, dict):
    print('Keys:', list(data.keys()))
    for k, v in data.items():
        length = len(v) if hasattr(v, '__len__') else 'n/a'
        print(f'  {k}: type={type(v)}, len={length}')
elif isinstance(data, list):
    print('List length:', len(data))
    print('First item:', data[0] if data else None)