import json
import base64

def extract_balanced_json(content, start_marker):
    """Brace-counts from the first '{' after start_marker to find the matching close."""
    start = content.index(start_marker) + len(start_marker)
    start = content.index('{', start)
    depth = 0
    for i in range(start, len(content)):
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
            if depth == 0:
                return content[start:i + 1]
    raise ValueError("Unbalanced braces — no matching close found.")

def extract_visualforce_credentials(file_path):
    """Parses a Visualforce.remoting.Manager.add(...) block to get auth context."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    json_str = extract_balanced_json(content, "RemotingProviderImpl(")
    data = json.loads(json_str)

    actions = data.get("actions", {})
    if not actions:
        raise ValueError("No 'actions' key found in parsed JSON.")

    first_ctrl = next(iter(actions.values()))
    ms_list = first_ctrl.get("ms", [])
    if not ms_list:
        raise ValueError("No 'ms' entries found under controller.")

    entry = ms_list[0]
    return {
        "csrf": entry.get("csrf"),
        "authorization": entry.get("authorization")
    }

def get_jwt_expiration(jwt_string):
    """Decodes a JWT's payload segment and returns the 'exp' claim as an integer."""
    parts = jwt_string.split('.')
    if len(parts) != 3:
        raise ValueError(f"Not a standard JWT (expected 3 segments, got {len(parts)}).")

    payload_b64 = parts[1]
    padding = '=' * (-len(payload_b64) % 4)
    payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
    payload = json.loads(payload_bytes)

    if "exp" not in payload:
        raise KeyError("No 'exp' claim found in JWT payload.")

    return int(payload["exp"])