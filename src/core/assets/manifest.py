"""Bundle manifest parsing and logical-name normalization."""
import json
from pathlib import Path


def normalize_name(value):
    value = str(value).strip().replace("/", "__")
    while "____" in value:
        value = value.replace("____", "__")
    return value


def parse_manifest(source):
    path = Path(source)
    text = path.read_text(encoding="utf-8") if path.exists() else str(source)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [line.strip() for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    if isinstance(data, dict):
        data = data.get("bundles", data.get("manifest", []))
    if not isinstance(data, list):
        raise ValueError("manifest must be a JSON array, object with bundles, or text lines")
    result = []
    for item in data:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict) and "name" in item:
            name = item["name"]
        else:
            raise ValueError("manifest entries must be strings or objects with name")
        normalized = normalize_name(name)
        if normalized and normalized not in result:
            result.append(normalized)
    return result
