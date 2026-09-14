"""Verified HTTP downloads with resumable, source-bound partial files."""
from __future__ import annotations

import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
import urllib.request

from .atomic import exclusive_lock, write_json

CHUNK_SIZE = 1024 * 1024


class InvalidDownload(OSError):
    """The response or partial bytes cannot be used for another attempt."""


def digest_file(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected(value):
    if value is None:
        return None
    algorithm, hexadecimal = value.split(":", 1) if ":" in value else ("sha256", value)
    digest = hashlib.new(algorithm)
    hexadecimal = hexadecimal.strip().lower()
    if not digest.digest_size or not re.fullmatch(r"[0-9a-f]{%d}" % (digest.digest_size * 2), hexadecimal):
        raise ValueError(f"invalid {algorithm} digest")
    return algorithm, hexadecimal


def _strong_etag(value):
    return value if value and re.fullmatch(r'"[^"\r\n]*"', value) else None


def _length(value):
    if value is None:
        return None
    value = value.strip()
    if not re.fullmatch(r"[0-9]+", value):
        raise InvalidDownload(f"invalid Content-Length: {value!r}")
    try:
        return int(value)
    except ValueError as exc:
        raise InvalidDownload("Content-Length is too large to parse") from exc


def _header_values(headers, name):
    """Retain every field occurrence instead of collapsing repeated headers."""
    if hasattr(headers, "get_all"):
        return headers.get_all(name, [])
    value = headers.get(name)
    return [] if value is None else [value]


def _response_length(response):
    """Accept one decimal length or a single, actually decoded chunked body.

    Repeated Content-Length fields and comma lists are deliberately rejected,
    even when equal. urllib chooses its own framing before returning a response;
    accepting a normalization it did not perform could otherwise change which
    bytes our caller treats as the resource.
    """
    lengths = _header_values(response.headers, "Content-Length")
    encodings = _header_values(response.headers, "Transfer-Encoding")
    if encodings:
        if lengths:
            raise InvalidDownload("Transfer-Encoding and Content-Length cannot be combined")
        if len(encodings) != 1 or encodings[0].strip().lower() != "chunked":
            raise InvalidDownload("unsupported Transfer-Encoding; only a single chunked coding is supported")
        if not getattr(response, "chunked", False):
            raise InvalidDownload("HTTP client did not activate chunked transfer decoding")
        return None
    if len(lengths) > 1:
        raise InvalidDownload("repeated Content-Length fields are not supported")
    return _length(lengths[0]) if lengths else None


def _discard(part, metadata):
    part.unlink(missing_ok=True)
    metadata.unlink(missing_ok=True)


def _checkpoint(part, metadata, state):
    if state is not None and part.is_file():
        state = dict(state, offset=part.stat().st_size, prefix_sha256=digest_file(part))
        write_json(metadata, state)


def _resume(part, metadata, identity, wanted):
    try:
        state = json.loads(metadata.read_text("utf-8"))
        offset = part.stat().st_size
        valid = (isinstance(state, dict) and state.get("identity") == identity
                 and state.get("offset") == offset and offset > 0
                 and state.get("prefix_sha256") == digest_file(part)
                 and (_strong_etag(state.get("etag")) or wanted))
        if valid:
            return state
    except (OSError, ValueError, TypeError):
        pass
    _discard(part, metadata)
    return None


def download_file(url, destination, *, expected_hash=None, identity=None,
                  headers=None, timeout=180.0, retries=4):
    """Return a SHA-256/length receipt only after a complete verified response.

    A saved prefix must match its recorded URL, caller identity and checksum.
    Resume additionally requires a strong ETag (sent as If-Range) or a supplied
    final digest. A failed digest/range restarts cleanly; a transport interruption
    preserves the prefix only when its representation can be checked next time.
    Manifest fileSize is deliberately not a wire-size contract.
    """
    if retries < 1:
        raise ValueError("retries must be at least 1")
    wanted = _expected(expected_hash)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    metadata = part.with_name(part.name + ".json")
    lock = destination.with_name(destination.name + ".lock")
    # JSON round-tripping gives tuples and other JSON-compatible input the
    # same representation in memory and on disk.
    identity = json.loads(json.dumps({"url": url, "object": identity, "expected": wanted}, sort_keys=True))
    last = None
    with exclusive_lock(lock):
        for attempt in range(1, retries + 1):
            state = _resume(part, metadata, identity, wanted)
            offset = state["offset"] if state else 0
            request_headers = {**(headers or {}), "Accept-Encoding": "identity"}
            if offset:
                request_headers["Range"] = f"bytes={offset}-"
                if state.get("etag"):
                    request_headers["If-Range"] = state["etag"]
            request = urllib.request.Request(url, headers=request_headers)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    response_headers = response.headers
                    if response_headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise InvalidDownload("server ignored Accept-Encoding: identity")
                    length = _response_length(response)
                    etag = _strong_etag(response_headers.get("ETag"))
                    final_url = response.geturl()
                    if response.status == 206:
                        match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)",
                                             response_headers.get("Content-Range", ""))
                        if not offset or match is None:
                            raise InvalidDownload("unsolicited or invalid Content-Range")
                        start, end, total = map(int, match.groups())
                        if start != offset or not start <= end < total:
                            raise InvalidDownload("Content-Range does not match the requested offset")
                        if state.get("total") is not None and total != state["total"]:
                            raise InvalidDownload("resumed object length changed")
                        if (state.get("etag") and etag != state["etag"]) or final_url != state["final_url"]:
                            raise InvalidDownload("resumed object identity changed")
                        span = end - start + 1
                        if length is not None and length != span:
                            raise InvalidDownload("Content-Length disagrees with Content-Range")
                        length = span
                    elif response.status == 200:
                        offset = 0
                        total = length
                    else:
                        raise InvalidDownload(f"unexpected HTTP status: {response.status}")
                    state = {"identity": identity, "etag": etag, "total": total,
                             "final_url": final_url}
                    received = 0
                    with part.open("ab" if offset else "wb") as stream:
                        # Invalidate any older checkpoint before changing its
                        # bytes. An unclean process exit forces a fresh fetch.
                        metadata.unlink(missing_ok=True)
                        while chunk := response.read(CHUNK_SIZE):
                            received += len(chunk)
                            if length is not None and received > length:
                                raise InvalidDownload("response body exceeds its declared length")
                            stream.write(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if length is not None and received != length:
                        raise OSError(f"incomplete HTTP body: expected {length}, received {received}")
                    if total is not None and part.stat().st_size != total:
                        raise OSError("partial response has not completed the object")
                digest = digest_file(part)
                if wanted:
                    actual = digest if wanted[0] == "sha256" else digest_file(part, wanted[0])
                    if actual != wanted[1]:
                        raise InvalidDownload(f"hash mismatch: expected {wanted[1]}, got {actual}")
                size = part.stat().st_size
                os.replace(part, destination)
                metadata.unlink(missing_ok=True)
                return {"sha256": digest, "bytes": size}
            except HTTPError as exc:
                last = exc
                if exc.code == 416:
                    # A digest plus the server's complete size can prove that
                    # an interrupted attempt already received the whole object.
                    total = re.fullmatch(r"bytes \*/([0-9]+)", exc.headers.get("Content-Range", ""))
                    if offset and wanted and total and int(total[1]) == offset \
                            and digest_file(part, wanted[0]) == wanted[1]:
                        receipt = {"sha256": digest_file(part), "bytes": offset}
                        os.replace(part, destination)
                        metadata.unlink(missing_ok=True)
                        exc.close()
                        return receipt
                    _discard(part, metadata)
                exc.close()
            except InvalidDownload as exc:
                last = exc
                _discard(part, metadata)
            except (OSError, HTTPException) as exc:
                last = exc
                if state and (state.get("etag") or wanted):
                    _checkpoint(part, metadata, state)
                else:
                    _discard(part, metadata)
            if attempt < retries:
                time.sleep(attempt)
    raise RuntimeError(f"download failed after {retries} attempt(s): {url}: {last}") from last
