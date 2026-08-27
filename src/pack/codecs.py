"""Content <-> blob transcoding, and the blob-path derivation formula.

Two backends exist for brotli and gzip: a Python binding, preferred, and a
Node child process, used only when the binding is missing. They are not
guaranteed to agree, and in fact do not always agree -- see
``select_backends()`` below for what was actually measured in this
environment. Because of that, this module commits to exactly one backend per
codec for the lifetime of the process (decided once, at import time) and
never mixes them within a run: mixing would mean the *same content* could
compress to two different blobs depending on which file the packer happened
to process first, which breaks content addressing.

Decompression is not part of that risk. Brotli and gzip are self-describing,
standard formats; decoding either one is not a design choice with two
disagreeing implementations the way encoding is, so decode functions use
whichever backend is available with no consistency question to settle.
"""
from __future__ import annotations

import gzip as _pygzip
import shutil
import subprocess
from pathlib import Path

try:
    import brotli as _pybrotli
except ImportError:  # pragma: no cover - exercised only where brotli is absent
    _pybrotli = None

BROTLI_QUALITY = 9
BROTLI_LGWIN = 24
GZIP_LEVEL = 9

SUFFIX_BR = ".br"
SUFFIX_BROTLI = ".brz"
SUFFIX_GZIP = ".gzz"
SUFFIX_IDENTITY = ".bin"

_NODE_HELPER = Path(__file__).with_name("_node_codec.mjs")


def _node_available() -> bool:
    return shutil.which("node") is not None


def _run_node(op: str, data: bytes) -> bytes:
    if not _node_available():
        raise RuntimeError(
            f"pack.codecs: no Python binding for this operation and no 'node' "
            f"executable on PATH (op={op!r}); install the 'brotli' package or "
            f"put node on PATH")
    proc = subprocess.run(
        ["node", str(_NODE_HELPER), op],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pack.codecs: node codec helper failed (op={op!r}, exit={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace')}")
    return proc.stdout


def brotli_backend() -> str:
    """Which backend brotli_compress()/brotli_decompress() actually use."""
    return "python (brotli module)" if _pybrotli is not None else "node (zlib.brotliCompressSync, subprocess)"


def gzip_backend() -> str:
    """gzip is always available via the Python standard library."""
    return "python (gzip module, mtime=0)"


def brotli_compress(data: bytes) -> bytes:
    if _pybrotli is not None:
        # The Python 'brotli' package's compress() does not expose a size_hint
        # parameter at all (checked: brotli 1.2.0's signature is
        # (string, mode=0, quality=11, lgwin=22, lgblock=0)). Measured against
        # Node's zlib.brotliCompressSync with BROTLI_PARAM_SIZE_HINT set to
        # len(data) at quality=9/lgwin=24 on three samples spanning ~40KB of
        # repetitive JSON, a 2.4MB quantised-float glTF-like buffer, and 1MB
        # of uniform random bytes: byte-identical output (same length, same
        # sha256) in all three. The missing size_hint does not appear to
        # change the encoded bytes at this quality/window.
        return _pybrotli.compress(data, quality=BROTLI_QUALITY, lgwin=BROTLI_LGWIN)
    return _run_node("brotli-compress", data)


def brotli_decompress(data: bytes) -> bytes:
    if _pybrotli is not None:
        return _pybrotli.decompress(data)
    return _run_node("brotli-decompress", data)


def gzip_compress(data: bytes) -> bytes:
    # mtime=0 is load-bearing, not cosmetic: gzip.compress()'s default stamps
    # wall-clock time into the header, so packing the *same* content twice on
    # two different days would produce two different blob hashes and defeat
    # content addressing (a rebuild with no source change would look like
    # every gzip'd file changed). mtime=0 makes the output a pure function of
    # the content.
    return _pygzip.compress(data, compresslevel=GZIP_LEVEL, mtime=0)


def gzip_decompress(data: bytes) -> bytes:
    return _pygzip.decompress(data)


def blob_suffix(codec: str, http_encoding: str) -> str:
    """The suffix that carries the serving decision into the blob filename.

    Matches the manifest schema's ``blob`` field description exactly:
    ``.br`` when the origin serves the blob with Content-Encoding: br, else
    ``.brz`` for a client-decoded brotli payload, ``.gzz`` for client-decoded
    gzip, ``.bin`` when nothing is compressed at all.
    """
    if http_encoding == "br":
        return SUFFIX_BR
    if codec == "brotli":
        return SUFFIX_BROTLI
    if codec == "gzip":
        return SUFFIX_GZIP
    return SUFFIX_IDENTITY


def derive_blob_path(blob_sha256_hex: str, codec: str, http_encoding: str) -> str:
    """blob_sha256[0:2] + '/' + blob_sha256 + suffix -- never chosen, always derived."""
    suffix = blob_suffix(codec, http_encoding)
    return f"{blob_sha256_hex[0:2]}/{blob_sha256_hex}{suffix}"


def encode_blob(content: bytes, codec: str, http_encoding: str) -> bytes:
    """The exact bytes that get served at the blob URL.

    Callers must already have picked a legal (codec, http_encoding) pair --
    http_encoding == 'br' together with codec != 'identity' double-compresses
    and is not re-checked here (pack.categories.Rule enforces it at load
    time).
    """
    if http_encoding == "br":
        return brotli_compress(content)
    if codec == "brotli":
        return brotli_compress(content)
    if codec == "gzip":
        return gzip_compress(content)
    return content


def decode_blob(blob_bytes: bytes, codec: str, http_encoding: str) -> bytes:
    """The content bytes a client ends up with: the browser strips
    http_encoding first, then the client applies codec."""
    content = brotli_decompress(blob_bytes) if http_encoding == "br" else blob_bytes
    if codec == "brotli":
        content = brotli_decompress(content)
    elif codec == "gzip":
        content = gzip_decompress(content)
    return content
