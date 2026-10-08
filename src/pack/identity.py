"""Deterministic identities inside the existing pack CAS (not a second store).

Canonical encoding is UTF-8, sorted object keys, compact separators, finite JSON
numbers, and one final LF. Array order is semantic; entries are sorted by path.
This is an explicit Python JSON contract, not a claim of RFC 8785 compliance.
The cross-language semantic projection only permits nonnegative safe integers,
strings, booleans and null (with arrays/objects); it never contains floats.
"""
from __future__ import annotations
import json
from .hashing import sha256_bytes

MANIFEST_V2 = "moly-asset-manifest/2"
CATALOG_V2 = "moly-asset-packs/2"
CONTENT_V1 = "moly-asset-content/1"
CHANNEL_V1 = "moly-asset-channel/1"
MAX_SAFE_INTEGER = (1 << 53) - 1


def canonical_bytes(document):
    return (json.dumps(document, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def content_projection(manifest):
    projection = {"schema": CONTENT_V1, "transforms": manifest["transforms"],
            "entries": [{key: entry[key] for key in ("path", "bytes", "content_sha256", "xf")}
                        for entry in sorted(manifest["entries"], key=lambda item: item["path"])]}
    def check(value):
        if value is None or type(value) in (str, bool):
            return
        if type(value) is int and 0 <= value <= MAX_SAFE_INTEGER:
            return
        if isinstance(value, list):
            for item in value:
                check(item)
            return
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            for item in value.values():
                check(item)
            return
        raise ValueError("v2 content facts require interoperable JSON values (nonnegative safe integers, no floats)")
    check(projection)
    return projection


def content_id(manifest):
    return sha256_bytes(canonical_bytes(content_projection(manifest)))


def manifest_v2(manifest, encoders):
    """Release metadata and deployment paths have no ownership in a package."""
    result = {"schema": MANIFEST_V2,
              **{key: manifest[key] for key in ("download_bytes", "resident_bytes",
                  "content_bytes", "logical_bytes", "transforms")},
              "encoders": encoders,
              "entries": sorted(manifest["entries"], key=lambda entry: entry["path"])}
    result["content_id"] = content_id(result)
    return result
