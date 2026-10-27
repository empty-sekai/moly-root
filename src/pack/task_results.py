"""Extraction result roots in the existing pack store.

The result is committed after its artifacts. Every retained result protects
its artifacts from release GC even before a product selects them. Results
hold no input paths, local clocks, credentials or mutable release labels.
"""
import re

from core.content import canonical_bytes, content_hash, document_hash
from .paths import output_path
from .verify import strict_json


def read_result(root, key):
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("invalid task result key")
    path = output_path(root, f"tasks/{key}.json")
    if not path.exists():
        return None
    raw = path.read_bytes()
    result = strict_json(raw)
    if (result.get("schema") != "moly-result/1" or result.get("key") != key
            or document_hash(result["keyParts"]) != key or result.get("status") != "ok"
            or canonical_bytes(result) != raw):
        raise ValueError("invalid task result identity")
    seen = set()
    for artifact in result["artifacts"]:
        logical = artifact["path"]
        if (not isinstance(logical, str) or "\\" in logical or ":" in logical
                or any(part in ("", ".", "..") for part in logical.split("/"))):
            raise ValueError("invalid task artifact logical path")
        output_path(root, logical)
        if logical in seen:
            raise ValueError("duplicate task artifact path")
        seen.add(logical)
        digest = artifact["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid task artifact hash")
        if artifact["blob"] != f"{digest[:2]}/{digest}.bin":
            raise ValueError("invalid task artifact storage address")
        data = output_path(root, "blobs/" + artifact["blob"]).read_bytes()
        if type(artifact["size"]) is not int or len(data) != artifact["size"] or content_hash(data) != digest:
            raise ValueError(f"corrupt task artifact: {logical}")
    return result


def retained_results(root):
    """Return verified roots and blobs; corrupt roots abort collection."""
    folder = output_path(root, "tasks")
    roots, blobs = {}, {}
    for path in sorted(folder.glob("*.json")):
        result = read_result(root, path.stem)
        roots[f"tasks/{path.name}"] = "extraction result"
        for artifact in result["artifacts"]:
            blobs[artifact["blob"]] = artifact["size"]
    return roots, blobs
