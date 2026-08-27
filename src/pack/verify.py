"""Independent manifest/blob-store verifier.

This module does not import build.py, codecs.py, categories.py, or
hashing.py. Every calculation here -- hashing, brotli/gzip decoding, the
blob-path derivation formula, the four totals -- is written again from
scratch. Reusing the packer's own functions would only prove the packer
agrees with itself; this exists to catch the case where it does not agree
with the manifest schema, with the bytes actually on disk, or with its own
stated totals.

The only cross-module dependency is ``pack.schema_lite`` (used only when the
third-party ``jsonschema`` package is unavailable, and only as a fallback for
JSON Schema evaluation, which is generic tooling, not packer arithmetic).

Checks performed, each independent of the others so one failure does not
mask the rest:
  1. the manifest validates against manifest.schema.json
  2. every entry's `blob` path equals blob_sha256[0:2]/blob_sha256+suffix
  3. every blob file exists, with byte length == blob_bytes and
     sha256 == blob_sha256
  4. decoding each blob (codec + http_encoding) reproduces `bytes` and
     `content_sha256`
  5. `path` is globally unique and contains no leading slash, no '..'
     segment, and no backslash
  6. download_bytes / resident_bytes / content_bytes each equal the sum of
     the relevant per-entry quantity over DISTINCT blobs (one blob counted
     once no matter how many paths reference it); logical_bytes equals the
     sum of `bytes` over ALL entries (the one total that is per-entry, not
     per-blob) -- and if two entries share a `blob` value, they must agree
     on every blob-level field (blob_bytes, blob_sha256, bytes, codec,
     http_encoding), since disagreement there would mean the same blob
     decodes to two different things depending on which path asked
  7. http_encoding == 'br' implies codec == 'identity'
  8. the blobs directory contains no file that is not referenced by any
     entry
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _blob_suffix(codec: str, http_encoding: str) -> str:
    if http_encoding == "br":
        return ".br"
    if codec == "brotli":
        return ".brz"
    if codec == "gzip":
        return ".gzz"
    return ".bin"


def _derive_blob_path(blob_sha256_hex: str, codec: str, http_encoding: str) -> str:
    return f"{blob_sha256_hex[0:2]}/{blob_sha256_hex}{_blob_suffix(codec, http_encoding)}"


def _brotli_decompress(data: bytes) -> bytes:
    try:
        import brotli
    except ImportError:
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            raise RuntimeError(
                "verify: no Python 'brotli' module and no 'node' on PATH; cannot decode a brotli blob")
        helper = Path(__file__).with_name("_node_codec.mjs")
        proc = subprocess.run([node, str(helper), "brotli-decompress"],
                               input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"verify: node brotli decode failed: {proc.stderr.decode('utf-8', 'replace')}")
        return proc.stdout
    return brotli.decompress(data)


def _decode_content(blob_bytes: bytes, codec: str, http_encoding: str) -> bytes:
    content = _brotli_decompress(blob_bytes) if http_encoding == "br" else blob_bytes
    if codec == "brotli":
        content = _brotli_decompress(content)
    elif codec == "gzip":
        content = gzip.decompress(content)
    return content


PATH_FORBIDDEN_LEADING_SLASH = "leading '/'"
PATH_FORBIDDEN_BACKSLASH = "backslash"
PATH_FORBIDDEN_DOTDOT = "'..' path segment"


def _path_problems(path: str) -> list[str]:
    problems = []
    if path.startswith("/"):
        problems.append(PATH_FORBIDDEN_LEADING_SLASH)
    if "\\" in path:
        problems.append(PATH_FORBIDDEN_BACKSLASH)
    segments = path.split("/")
    if any(seg in (".", "..") for seg in segments):
        problems.append(PATH_FORBIDDEN_DOTDOT)
    return problems


def _load_schema_validator(schema: dict):
    """Returns a callable(instance) -> list[str] of violation messages."""
    force_lite = os.environ.get("PACK_FORCE_SCHEMA_LITE") == "1"
    if not force_lite:
        try:
            import jsonschema
            from jsonschema.validators import validator_for
        except ImportError:
            jsonschema = None
    else:
        jsonschema = None

    if jsonschema is not None:
        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)

        def run(instance):
            errors = sorted(validator.iter_errors(instance),
                             key=lambda e: "/".join(str(p) for p in e.path))
            return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors]

        return run, "jsonschema package"

    from . import schema_lite

    def run(instance):
        return schema_lite.validate(instance, schema)

    return run, "schema_lite fallback (jsonschema package not installed, or PACK_FORCE_SCHEMA_LITE=1)"


def verify(manifest_path, blobs_dir, schema_path) -> tuple[list[str], dict]:
    """Returns (errors, info). errors is empty iff *out* is fully valid."""
    manifest_path = Path(manifest_path)
    blobs_dir = Path(blobs_dir)
    schema_path = Path(schema_path)
    errors: list[str] = []

    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    run_schema, backend_name = _load_schema_validator(schema)
    schema_errors = run_schema(manifest)
    errors += [f"schema: {msg}" for msg in schema_errors]

    entries = manifest.get("entries", [])
    if not isinstance(entries, list):
        # Schema validation already reported this; nothing else here is checkable.
        return errors, {"schema_backend": backend_name, "entries": 0}

    seen_paths: dict[str, int] = {}
    referenced_blobs: set[str] = set()
    # blob path -> {"blob_bytes", "blob_sha256", "bytes", "codec", "http_encoding"}
    # of the first entry seen referencing that blob. Every later entry
    # referencing the same blob must agree on all five fields -- disagreement
    # would mean the same bytes on disk decode to two different things
    # depending on which path asked, which is not possible and means the
    # manifest itself is internally inconsistent.
    blob_info: dict[str, dict] = {}
    logical_bytes_sum = 0

    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errors.append(f"entries[{i}]: not an object")
            continue
        path = e.get("path")
        blob = e.get("blob")
        blob_bytes = e.get("blob_bytes")
        blob_sha256 = e.get("blob_sha256")
        content_bytes = e.get("bytes")
        content_sha256 = e.get("content_sha256")
        codec = e.get("codec")
        http_encoding = e.get("http_encoding")

        # (5) path uniqueness and shape
        if isinstance(path, str):
            if path in seen_paths:
                errors.append(f"entries[{i}]: path {path!r} duplicates entries[{seen_paths[path]}]")
            else:
                seen_paths[path] = i
            for problem in _path_problems(path):
                errors.append(f"entries[{i}]: path {path!r} contains {problem}")

        # (7) br implies identity codec
        if http_encoding == "br" and codec != "identity":
            errors.append(
                f"entries[{i}]: http_encoding='br' but codec={codec!r} (must be 'identity')")

        # (2) blob path derivation
        if isinstance(blob_sha256, str) and isinstance(codec, str) and isinstance(http_encoding, str):
            expected_blob = _derive_blob_path(blob_sha256, codec, http_encoding)
            if blob != expected_blob:
                errors.append(
                    f"entries[{i}]: blob={blob!r} but derives to {expected_blob!r} "
                    f"from blob_sha256[0:2]/blob_sha256+suffix")

        # (3) blob file existence, size, hash
        blob_file = None
        if isinstance(blob, str):
            referenced_blobs.add(blob)
            blob_file = blobs_dir / blob
            if not blob_file.is_file():
                errors.append(f"entries[{i}]: blob file does not exist: {blob_file}")
                blob_file = None
            else:
                actual_size = blob_file.stat().st_size
                if isinstance(blob_bytes, int) and actual_size != blob_bytes:
                    errors.append(
                        f"entries[{i}]: blob file size {actual_size} != blob_bytes {blob_bytes} ({blob_file})")
                actual_sha = _sha256_file(blob_file)
                if isinstance(blob_sha256, str) and actual_sha != blob_sha256:
                    errors.append(
                        f"entries[{i}]: blob file sha256 {actual_sha} != blob_sha256 {blob_sha256} ({blob_file})")

        # (4) decode -> bytes, content_sha256
        if blob_file is not None and isinstance(codec, str) and isinstance(http_encoding, str):
            try:
                raw = blob_file.read_bytes()
                content = _decode_content(raw, codec, http_encoding)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"entries[{i}]: failed to decode blob ({codec=}, {http_encoding=}): {exc}")
            else:
                if isinstance(content_bytes, int) and len(content) != content_bytes:
                    errors.append(
                        f"entries[{i}]: decoded length {len(content)} != bytes {content_bytes}")
                actual_content_sha = _sha256_bytes(content)
                if isinstance(content_sha256, str) and actual_content_sha != content_sha256:
                    errors.append(
                        f"entries[{i}]: decoded content sha256 {actual_content_sha} != "
                        f"content_sha256 {content_sha256}")

        # (6) per-blob fields, collected once per distinct blob + agreement
        # check across every entry that references it; and the one per-entry
        # total (logical_bytes).
        if isinstance(content_bytes, int):
            logical_bytes_sum += content_bytes

        if isinstance(blob, str):
            this_info = {
                "blob_bytes": blob_bytes,
                "blob_sha256": blob_sha256,
                "bytes": content_bytes,
                "codec": codec,
                "http_encoding": http_encoding,
            }
            prior = blob_info.get(blob)
            if prior is None:
                blob_info[blob] = this_info
            else:
                for field_name in ("blob_bytes", "blob_sha256", "bytes", "codec", "http_encoding"):
                    if prior[field_name] != this_info[field_name]:
                        errors.append(
                            f"entries[{i}]: blob {blob!r} disagrees with an earlier entry on "
                            f"{field_name} ({this_info[field_name]!r} vs {prior[field_name]!r})")

    download_computed = sum(
        info["blob_bytes"] for info in blob_info.values() if isinstance(info["blob_bytes"], int))
    resident_computed = sum(
        (info["bytes"] if info["http_encoding"] == "br" else info["blob_bytes"])
        for info in blob_info.values()
        if isinstance(info["blob_bytes"], int) and (info["http_encoding"] != "br" or isinstance(info["bytes"], int))
    )
    content_computed = sum(
        info["bytes"] for info in blob_info.values() if isinstance(info["bytes"], int))

    for field, computed in (
        ("download_bytes", download_computed),
        ("resident_bytes", resident_computed),
        ("content_bytes", content_computed),
        ("logical_bytes", logical_bytes_sum),
    ):
        stated = manifest.get(field)
        if stated != computed:
            errors.append(f"totals: manifest {field}={stated!r} but recomputed sum is {computed!r}")

    # (8) no blob on disk that nothing references
    if blobs_dir.is_dir():
        on_disk = set()
        for p in blobs_dir.rglob("*"):
            if p.is_file():
                on_disk.add(p.relative_to(blobs_dir).as_posix())
        orphans = sorted(on_disk - referenced_blobs)
        for orphan in orphans:
            errors.append(f"orphan blob (not referenced by any entry): {orphan}")
    else:
        errors.append(f"blobs directory does not exist: {blobs_dir}")

    info = {
        "schema_backend": backend_name,
        "entries": len(entries),
        "unique_blobs_referenced": len(referenced_blobs),
    }
    return errors, info


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pack.verify", description="independently verify a packed manifest + blob store")
    ap.add_argument("--out", default=None, help="a pack.build output directory (manifest.json + blobs/)")
    ap.add_argument("--manifest", default=None, help="explicit manifest.json path (overrides --out)")
    ap.add_argument("--blobs", default=None, help="explicit blobs directory (overrides --out)")
    ap.add_argument("--schema", required=True, help="path to manifest.schema.json")
    args = ap.parse_args(argv)

    if args.manifest:
        manifest_path = Path(args.manifest)
    elif args.out:
        manifest_path = Path(args.out) / "manifest.json"
    else:
        ap.error("need --out, or --manifest (optionally with --blobs)")
        return 2

    if args.blobs:
        blobs_dir = Path(args.blobs)
    elif args.out:
        blobs_dir = Path(args.out) / "blobs"
    else:
        # blob_prefix from the manifest itself, relative to the manifest's directory
        with open(manifest_path, encoding="utf-8") as fh:
            prefix = json.load(fh).get("blob_prefix", "blobs/")
        blobs_dir = manifest_path.parent / prefix

    errors, info = verify(manifest_path, blobs_dir, args.schema)

    print(f"schema validator: {info.get('schema_backend', '?')}")
    if errors:
        for e in errors:
            print(f"FAIL {e}")
        print(f"{len(errors)} error(s) -- {manifest_path}")
        return 1
    print(f"OK  {info['entries']} entries, {info['unique_blobs_referenced']} unique blobs -- {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
