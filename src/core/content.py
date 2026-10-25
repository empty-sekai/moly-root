"""Canonical bytes and hashes for content-addressed contract documents.

This is the existing pack v2 encoding. Domain writers keep their authored
nonfinite-number representation; this encoder accepts only finite JSON.
"""
import hashlib
import json


def canonical_bytes(document):
    return (json.dumps(document, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def content_hash(data):
    return hashlib.sha256(data).hexdigest()


def document_hash(document):
    return content_hash(canonical_bytes(document))
