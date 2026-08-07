"""
Infers path templates from concrete URL paths captured in HAR entries.

/users/12345/orders/9f8a7b6c-...  ->  /users/{userId}/orders/{orderId}

This lets pagination_stitcher and openapi_builder group calls that hit the
"same" logical endpoint with different resource IDs, instead of treating
every literal URL as a distinct path.
"""

import re
from typing import Any, Dict, List, Tuple

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_MONGO_OID_RE = re.compile(r"^[0-9a-fA-F]{24}$")
_NUMERIC_RE = re.compile(r"^\d+$")
# long mixed alnum tokens (session ids, short hashes) - digits+letters, len >= 8
_MIXED_TOKEN_RE = re.compile(r"^(?=.*[0-9])(?=.*[a-zA-Z])[0-9a-zA-Z_-]{8,}$")


def classify_segment(segment: str) -> bool:
    """Return True if this path segment looks like a dynamic resource ID."""
    if not segment:
        return False
    if _NUMERIC_RE.match(segment):
        return True
    if _UUID_RE.match(segment):
        return True
    if _MONGO_OID_RE.match(segment):
        return True
    if _MIXED_TOKEN_RE.match(segment):
        return True
    return False


def _param_name_for(prev_static_segment: str, index: int) -> str:
    """Derive a param name from the preceding static segment, e.g. 'users' -> 'userId'."""
    if not prev_static_segment:
        return f"param{index}"
    word = prev_static_segment.rstrip("s")  # naive singularize
    word = re.sub(r"[^a-zA-Z0-9]", "", word)
    if not word:
        return f"param{index}"
    return f"{word[0].lower()}{word[1:]}Id" if len(word) > 1 else f"{word}Id"


def infer_path_template(path: str) -> Tuple[str, Dict[int, str]]:
    """
    Turn one concrete path into a templated path + map of segment index -> param name.

    Example: /users/12345/orders/9f8a7b6c-1111-2222-3333-444455556666
          -> ("/users/{userId}/orders/{orderId}", {1: "userId", 3: "orderId"})
    """
    segments = [s for s in path.split("/")]
    template_parts: List[str] = []
    path_params: Dict[int, str] = {}
    last_static = ""

    for i, seg in enumerate(segments):
        if seg == "":
            template_parts.append(seg)
            continue
        if classify_segment(seg):
            param_name = _param_name_for(last_static, i)
            # avoid collisions if two dynamic segments would derive the same name
            base_name = param_name
            suffix = 1
            while param_name in path_params.values():
                suffix += 1
                param_name = f"{base_name}{suffix}"
            template_parts.append(f"{{{param_name}}}")
            path_params[i] = param_name
        else:
            template_parts.append(seg)
            last_static = seg

    return "/".join(template_parts) or "/", path_params


def group_by_template(records: List[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """
    Group HAR records by (method, path_template). Mutates each record in place,
    attaching 'path_template' and 'path_params' (dict[str, str] of param_name -> observed value).
    """
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    for r in records:
        path = r.get("path", "/")
        method = (r.get("method") or "GET").upper()
        template, param_positions = infer_path_template(path)

        segments = path.split("/")
        observed_values = {
            name: segments[idx] for idx, name in param_positions.items() if idx < len(segments)
        }

        r["path_template"] = template
        r["path_params"] = observed_values

        key = (method, template)
        groups.setdefault(key, []).append(r)

    return groups