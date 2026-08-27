"""Streaming SHA-256.

This is deliberately five lines and is not shared with verify.py: verify.py
is required to check the packer's work independently, so it carries its own
copy of the same five lines rather than importing this module. If that ever
looks like duplication worth removing, it isn't -- removing it would make
verify.py trust build.py's arithmetic instead of checking it.
"""
from __future__ import annotations

import hashlib

CHUNK_SIZE = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
