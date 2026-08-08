import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Tuple

def normalize_path(path: str) -> str:
    """Normalizes dynamic path segments (UUIDs, integer IDs) into generalized placeholders."""
    # Replace UUID patterns
    path = re.sub(
        r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", 
        "/{uuid}", 
        path
    )
    # Replace integer ID segments
    path = re.sub(r"/\d+", "/{id}", path)
    return path

def group_by_template(items: List[Any]) -> Dict[Tuple[str, str], List[Any]]:
    """
    Groups a list of network records or items by their (method, template) tuple key.
    """
    grouped: Dict[Tuple[str, str], List[Any]] = {}
    
    for item in items:
        # Extract path and HTTP method depending on item type
        if isinstance(item, str):
            path = urlparse(item).path
            method = "GET"
        elif isinstance(item, dict):
            url = item.get("url", item.get("path", ""))
            path = urlparse(url).path
            method = item.get("method", "GET")
        else:
            url = getattr(item, "url", getattr(item, "path", ""))
            path = urlparse(url).path
            method = getattr(item, "method", "GET")
        
        template = normalize_path(path)
        key = (method, template)
        
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(item)
        
    return grouped

