"""Independent manifest/blob-store verifier.

This module does not import build.py, codecs.py, categories.py, or
hashing.py. Every calculation here -- hashing, brotli/gzip decoding, the
blob-path derivation formula, the four totals -- is written again from
scratch. Reusing the packer's own functions would only prove the packer
agrees with itself; this exists to catch the case where it does not agree
with the manifest schema, with the bytes actually on disk, or with its own
stated totals.

Shared dependencies are declarative runtime byte limits and ``pack.schema_lite``
(the fallback when the third-party ``jsonschema`` package is unavailable).
Identity arithmetic and codec verification stay independent of the producer.

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
  9. the set of non-null `xf` values across entries equals the set of keys
     in `transforms`, in both directions -- schema.json cannot express this
     (it has no way to compare an array of sibling objects against a
     property's key set), so it is arithmetic performed here instead. A
     transform used by an entry but absent from `transforms` means the pack
     cannot be reproduced; one present in `transforms` but used by no entry
     is a stale record of a build that no longer happens.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from .limits import MAX_RUNTIME_DOCUMENT_BYTES, MAX_RUNTIME_BLOB_BYTES, MAX_RUNTIME_ASSET_BYTES

DEFAULT_SCHEMA_PATH = Path(__file__).with_name("manifest.schema.json")


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid_number(value):
        raise ValueError(f"non-finite JSON number: {value}")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            invalid_number(value)
        return number
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_number,
                      parse_float=finite_float)


def _canonical_bytes(document):
    return (json.dumps(document, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _store_path(root, relative):
    """Reads and GC must reject linked store components just like writers."""
    root = Path(root).resolve()
    path = root / relative
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("object path escapes the store")
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ValueError("object path traverses a linked store component")
    return resolved


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


def _brotli_decompress(data: bytes, maximum: int) -> bytes:
    try:
        import brotli
    except ImportError:
        brotli = None
    if brotli is not None:
        decoder = brotli.Decompressor()
        try:
            result = decoder.process(data, output_buffer_limit=maximum + 1)
        except TypeError:
            # Older bindings expose only an unbounded operation. Never fall
            # back to allocating unchecked decoded content in this process.
            pass
        else:
            if len(result) > maximum or not decoder.is_finished():
                raise ValueError("brotli expansion exceeds the declared decoded length or is truncated")
            return result
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        raise RuntimeError("bounded brotli verification requires a recent Python brotli binding or Node.js")
    helper = Path(__file__).with_name("_node_codec.mjs")
    proc = subprocess.run([node, str(helper), "brotli-decompress", str(maximum + 1)],
                           input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"verify: node brotli decode failed: {proc.stderr.decode('utf-8', 'replace')}")
    if len(proc.stdout) > maximum:
        raise ValueError("brotli expansion exceeds the declared decoded length")
    return proc.stdout


def _decode_content(blob_bytes: bytes, codec: str, http_encoding: str, maximum: int) -> bytes:
    content = _brotli_decompress(blob_bytes, maximum) if http_encoding == "br" else blob_bytes
    if codec == "brotli":
        content = _brotli_decompress(content, maximum)
    elif codec == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
            content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("expansion exceeds the declared decoded length")
    return content


PATH_FORBIDDEN_LEADING_SLASH = "leading '/'"
PATH_FORBIDDEN_BACKSLASH = "backslash"
PATH_FORBIDDEN_DOTDOT = "'..' path segment"


def _path_problems(path: str) -> list[str]:
    problems = []
    if not path or any(not part for part in path.split("/")):
        problems.append("empty path segment")
    if ":" in path:
        problems.append("drive or URL prefix")
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
    # Schema compilation is immutable configuration work. Cache it separately
    # from instance checks: every document still runs the full validator.
    return _compiled_schema_validator(_canonical_bytes(schema), os.environ.get("PACK_FORCE_SCHEMA_LITE") == "1")


@lru_cache(maxsize=32)
def _compiled_schema_validator(schema_bytes, force_lite):
    schema = json.loads(schema_bytes)
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


def verify_blob(entry, blobs_dir, *, _verified_blobs=None) -> list[str]:
    """Check actual bytes independently of the producer and its codec helpers."""
    errors = []
    try:
        blob, codec, encoding = entry["blob"], entry["codec"], entry["http_encoding"]
        if codec not in {"identity", "gzip", "brotli"} or encoding not in {"identity", "br"}:
            return ["unknown blob codec/encoding"]
        if encoding == "br" and codec != "identity":
            errors.append("http_encoding='br' requires codec='identity'")
        if not isinstance(blob, str) or _path_problems(blob):
            return ["invalid blob path"]
        if blob != _derive_blob_path(entry["blob_sha256"], codec, encoding):
            errors.append("blob path does not match blob_sha256 and codec")
        blobs_dir = Path(blobs_dir).absolute()
        path = _store_path(blobs_dir.parent, blobs_dir.name + "/" + blob)
        if type(entry["bytes"]) is not int or entry["bytes"] < 0:
            return ["invalid decoded byte length"]
        stat = path.stat()
        if stat.st_size != entry["blob_bytes"]:
            return ["blob file size does not match blob_bytes"]
        stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
        signature = (str(path), stamp, entry["blob_sha256"], entry["content_sha256"],
                     entry["bytes"], codec, encoding)
        if not errors and _verified_blobs is not None and signature in _verified_blobs:
            return []
        raw = path.read_bytes()
        if len(raw) != entry["blob_bytes"]:
            errors.append("blob file size does not match blob_bytes")
        if _sha256_bytes(raw) != entry["blob_sha256"]:
            errors.append("blob file sha256 does not match blob_sha256")
        if errors:
            return errors
        content = _decode_content(raw, codec, encoding, entry["bytes"])
        if len(content) != entry["bytes"]:
            errors.append("decoded length does not match bytes")
        if _sha256_bytes(content) != entry["content_sha256"]:
            errors.append("decoded content sha256 does not match content_sha256")
        after = path.stat()
        if stamp != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
            errors.append("blob changed during verification")
        if not errors and _verified_blobs is not None:
            _verified_blobs.add(signature)
    except Exception as exc:
        errors.append(f"cannot verify blob: {exc}")
    return errors


def verify(manifest_path, blobs_dir, schema_path=None, *, check_orphans=True) -> tuple[list[str], dict]:
    """Returns (errors, info). errors is empty iff *out* is fully valid."""
    try:
        raw = Path(manifest_path).read_bytes()
        manifest = strict_json(raw)
        errors, info = verify_manifest(manifest, blobs_dir, schema_path, check_orphans=check_orphans)
        if isinstance(manifest, dict) and manifest.get("schema") == "moly-asset-manifest/2":
            if raw != _canonical_bytes(manifest):
                errors.append("v2 manifest is not canonical")
        path = Path(manifest_path)
        if path.parent.name == "packages" and re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
            if path.stem != _sha256_bytes(raw):
                errors.append("manifest content address mismatch")
        return errors, info
    except (OSError, ValueError) as exc:
        return [f"cannot verify manifest: {exc}"], {"entries": 0}


def verify_manifest(manifest, blobs_dir, schema_path=None, *, check_orphans=True, _verified_blobs=None) -> tuple[list[str], dict]:
    blobs_dir = Path(blobs_dir)
    schema_path = Path(schema_path) if schema_path else (
        Path(__file__).with_name("manifest-v2.schema.json")
        if isinstance(manifest, dict) and manifest.get("schema") == "moly-asset-manifest/2"
        else DEFAULT_SCHEMA_PATH)
    errors: list[str] = []

    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    run_schema, backend_name = _load_schema_validator(schema)
    schema_errors = run_schema(manifest)
    errors += [f"schema: {msg}" for msg in schema_errors]
    if schema_errors:
        return errors, {"schema_backend": backend_name, "entries": 0}

    entries = manifest.get("entries", [])
    if not isinstance(entries, list):
        # Schema validation already reported this; nothing else here is checkable.
        return errors, {"schema_backend": backend_name, "entries": 0}

    if manifest.get("schema") == "moly-asset-manifest/2":
        size_values = [manifest[key] for key in ("download_bytes", "resident_bytes", "content_bytes", "logical_bytes")]
        size_values += [entry[key] for entry in entries for key in ("blob_bytes", "bytes")]
        if any(type(value) is not int or not 0 <= value <= (1 << 53) - 1 for value in size_values):
            return ["v2 byte sizes must be nonnegative safe JSON integers"], {"schema_backend": backend_name, "entries": 0}
        if len(_canonical_bytes(manifest)) > MAX_RUNTIME_DOCUMENT_BYTES:
            errors.append("v2 manifest exceeds the runtime document budget")
        for entry in entries:
            if entry["blob_bytes"] > MAX_RUNTIME_BLOB_BYTES or entry["bytes"] > MAX_RUNTIME_ASSET_BYTES:
                errors.append("v2 asset exceeds the runtime byte budget: " + entry["path"])
        if errors:
            return errors, {"schema_backend": backend_name, "entries": 0}
        projection = {"schema": "moly-asset-content/1", "transforms": manifest["transforms"],
                      "entries": [{field: item[field] for field in ("path", "bytes", "content_sha256", "xf")}
                                  for item in sorted(entries, key=lambda item: item["path"])]}
        def interoperable(value):
            if value is None or type(value) in (str, bool):
                return True
            if type(value) is int:
                return 0 <= value <= (1 << 53) - 1
            if isinstance(value, list):
                return all(interoperable(item) for item in value)
            if isinstance(value, dict):
                return all(isinstance(key, str) and interoperable(item) for key, item in value.items())
            return False
        if not interoperable(projection):
            errors.append("v2 content facts require interoperable JSON values (nonnegative safe integers, no floats)")
            return errors, {"schema_backend": backend_name, "entries": 0}
        encoded = _canonical_bytes(projection)
        if _sha256_bytes(encoded) != manifest["content_id"]:
            errors.append("content identity differs from decoded package facts")
        if entries != sorted(entries, key=lambda item: item["path"]):
            errors.append("v2 entries must be in canonical path order")
        used = {"brotli" if item["http_encoding"] == "br" else item["codec"] for item in entries} - {"identity"}
        if used != set(manifest["encoders"]):
            errors.append("encoder profiles differ from used representations")
        for codec, recipe in manifest["encoders"].items():
            if (not isinstance(recipe, dict) or not isinstance(recipe.get("implementation"), str)
                    or not isinstance(recipe.get("version"), str) or not recipe["version"]):
                errors.append(f"invalid encoder profile: {codec}")
                continue
            implementation = recipe["implementation"]
            if implementation == "legacy-v1":
                if recipe != {"implementation": "legacy-v1", "version": "unrecorded"}:
                    errors.append("invalid legacy encoder receipt")
            elif codec == "gzip":
                expected = {"implementation": "zlib-deflate/gzip-v1", "version": recipe["version"],
                            "level": 9, "window_bits": 15, "memory_level": 8,
                            "strategy": 0, "mtime": 0, "os": 255}
                if _canonical_bytes(recipe) != _canonical_bytes(expected):
                    errors.append("invalid deterministic gzip profile")
            elif codec == "brotli":
                if (implementation not in {"python-brotli", "node-brotli"} or _canonical_bytes(recipe) != _canonical_bytes({
                        "implementation": implementation, "version": recipe["version"],
                        "quality": 9, "lgwin": 24, "mode": 0})):
                    errors.append("invalid deterministic brotli profile")
            else:
                errors.append(f"unknown encoder profile: {codec}")

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
    xf_values_used: set[str] = set()
    checked_blobs = set()

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

        # Verify each distinct representation once, but never reuse a check
        # when another entry changes any part of that representation's contract.
        signature = (blob, blob_sha256, blob_bytes, content_bytes, content_sha256, codec, http_encoding)
        if signature not in checked_blobs:
            errors.extend(f"entries[{i}]: {message}" for message in verify_blob(e, blobs_dir, _verified_blobs=_verified_blobs))
            checked_blobs.add(signature)
        referenced_blobs.add(blob)

        # (6) per-blob fields, collected once per distinct blob + agreement
        # check across every entry that references it; and the one per-entry
        # total (logical_bytes).
        if isinstance(content_bytes, int):
            logical_bytes_sum += content_bytes

        xf = e.get("xf")
        if xf is not None:
            xf_values_used.add(xf)

        if isinstance(blob, str):
            this_info = {
                "blob_bytes": blob_bytes,
                "blob_sha256": blob_sha256,
                "bytes": content_bytes,
                "codec": codec,
                "http_encoding": http_encoding,
                "content_sha256": content_sha256,
            }
            prior = blob_info.get(blob)
            if prior is None:
                blob_info[blob] = this_info
            else:
                for field_name in ("blob_bytes", "blob_sha256", "bytes", "codec", "http_encoding", "content_sha256"):
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

    # (9) entries' non-null xf values, as a set, must equal transforms' key
    # set, in both directions. Schema-blind by construction (JSON Schema
    # cannot compare sibling values), so this is the only place either
    # direction of this equality is ever checked.
    transforms_obj = manifest.get("transforms")
    if isinstance(transforms_obj, dict):
        for name, recipe in transforms_obj.items():
            if (not isinstance(recipe, dict)
                    or not isinstance(recipe.get("tool"), str) or not recipe["tool"]
                    or not isinstance(recipe.get("tool_version"), str) or not recipe["tool_version"]
                    or not isinstance(recipe.get("params"), dict)):
                errors.append(f"transforms: invalid recipe for {name!r}")
                continue
            if manifest.get("schema") == "moly-asset-manifest/2":
                if set(recipe) != {"tool", "tool_version", "params"}:
                    errors.append(f"transforms: unknown recipe fields for {name!r}")
                if name == "meshopt":
                    params = recipe["params"]
                    widths = {"quantize_position", "quantize_normal", "quantize_texcoord"}
                    switches = {"weld", "simplify", "reorder"}
                    if (set(params) != widths | switches
                            or not all(type(params.get(key)) is int and 1 <= params[key] <= 16 for key in widths)
                            or not all(type(params.get(key)) is bool for key in switches)):
                        errors.append("transforms: invalid meshopt quantization recipe")
        transforms_keys = set(transforms_obj.keys())
        used_but_undescribed = sorted(xf_values_used - transforms_keys)
        described_but_unused = sorted(transforms_keys - xf_values_used)
        for xf in used_but_undescribed:
            errors.append(
                f"transforms: xf={xf!r} is used by at least one entry but transforms has no "
                f"{xf!r} recipe -- the pack cannot be reproduced")
        for xf in described_but_unused:
            errors.append(
                f"transforms: transforms has a {xf!r} recipe but no entry has xf={xf!r} -- "
                f"stale record of a build that no longer happens")
    # else: schema validation above already reported `transforms` missing or
    # not an object; nothing further to check here without one.

    # (8) no blob on disk that nothing references
    if check_orphans and blobs_dir.is_dir():
        on_disk = set()
        for p in blobs_dir.rglob("*"):
            if p.is_file():
                on_disk.add(p.relative_to(blobs_dir).as_posix())
        orphans = sorted(on_disk - referenced_blobs)
        for orphan in orphans:
            errors.append(f"orphan blob (not referenced by any entry): {orphan}")
    elif not blobs_dir.is_dir():
        errors.append(f"blobs directory does not exist: {blobs_dir}")

    info = {
        "schema_backend": backend_name,
        "entries": len(entries),
        "unique_blobs_referenced": len(referenced_blobs),
        "referenced_blobs": sorted(referenced_blobs),
    }
    return errors, info


def verify_catalog_data(catalog, root, schema_path=None, *, _verified_blobs=None) -> tuple[list[str], dict]:
    """Check package identities, ownership, dependencies and every referenced blob."""
    root = Path(root).resolve()
    if _verified_blobs is None:
        _verified_blobs = set()
    errors, referenced, sizes = [], set(), {}
    info = {"entries": 0, "packages": 0, "unique_blobs_referenced": 0,
            "referenced_blobs": [], "blob_sizes": {}}
    if (not isinstance(catalog, dict) or catalog.get("schema") not in {"moly-asset-packs/1", "moly-asset-packs/2"}
            or not isinstance(catalog.get("version"), str) or not catalog["version"]
            or not isinstance(catalog.get("packages"), list) or not catalog["packages"]):
        return ["invalid or empty asset catalog"], info
    v2 = catalog["schema"] == "moly-asset-packs/2"
    if v2:
        if len(_canonical_bytes(catalog)) > MAX_RUNTIME_DOCUMENT_BYTES:
            return ["v2 catalog exceeds the runtime document budget"], info
        validator, _ = _load_schema_validator(json.loads(Path(__file__).with_name("catalog-v2.schema.json").read_bytes()))
        failures = validator(catalog)
        if failures:
            return ["catalog schema: " + failure for failure in failures], info
        for package in catalog["packages"]:
            if any(type(package[field]) is not int or not 0 <= package[field] <= (1 << 53) - 1
                   for field in ("download_bytes", "content_bytes")):
                return ["v2 catalog byte sizes must be nonnegative safe JSON integers"], info
    owners, manifests, dependencies = {}, set(), {}
    for index, package in enumerate(catalog["packages"]):
        label = f"packages[{index}]"
        if not isinstance(package, dict) or not isinstance(package.get("id"), str):
            errors.append(f"{label}: missing package id")
            continue
        group = package["id"]
        label = f"package {group!r}"
        if _path_problems(group) or group in dependencies:
            errors.append(f"{label}: invalid or duplicate package id")
        deps = package.get("dependencies")
        if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
            errors.append(f"{label}: invalid dependencies")
            deps = []
        if len(set(deps)) != len(deps):
            errors.append(f"{label}: duplicate dependency")
        dependencies[group] = deps
        paths = package.get("paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            errors.append(f"{label}: invalid paths")
            paths = []
        for path in paths:
            if _path_problems(path):
                errors.append(f"{label}: invalid asset path {path!r}")
            if path in owners:
                errors.append(f"{label}: path {path!r} already owned by {owners[path]!r}")
            owners[path] = group
        name = package.get("manifest")
        if not isinstance(name, str) or _path_problems(name):
            errors.append(f"{label}: invalid manifest path")
            continue
        if name in manifests:
            errors.append(f"{label}: escaping or duplicate manifest path")
            continue
        manifests.add(name)
        try:
            path = _store_path(root, name)
            raw = path.read_bytes()
            document = strict_json(raw)
            if v2 and not re.fullmatch(r"packages/[0-9a-f]{64}\.json", name):
                errors.append(f"{label}: v2 requires a content-addressed package manifest")
            if v2 and raw != _canonical_bytes(document):
                errors.append(f"{label}: v2 manifest is not canonical")
            if re.fullmatch(r"packages/[0-9a-f]{64}\.json", name) \
                    and path.stem != _sha256_bytes(raw):
                errors.append(f"{label}: manifest content address mismatch")
            failures, checked = verify_manifest(document, root / "blobs", schema_path, check_orphans=False,
                                                _verified_blobs=_verified_blobs)
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: cannot read manifest: {exc}")
            continue
        errors.extend(f"{label}: {message}" for message in failures)
        if failures:
            continue
        if v2:
            if document["schema"] != "moly-asset-manifest/2" or document.get("content_id") != package["content_id"]:
                errors.append(f"{label}: incompatible schema or content identity")
        else:
            if document.get("schema") != "moly-asset-manifest/1" or document.get("version") != catalog["version"]:
                errors.append(f"{label}: version differs from catalog")
            if document.get("blob_prefix") != "blobs/":
                errors.append(f"{label}: blob_prefix must be relative to the catalog root: 'blobs/'")
        entries = document["entries"]
        if sorted(paths) != sorted(entry["path"] for entry in entries):
            errors.append(f"{label}: catalog and manifest paths differ")
        for field in ("download_bytes", "content_bytes"):
            if package.get(field) != document[field]:
                errors.append(f"{label}: catalog {field} differs from manifest")
        referenced.update(checked["referenced_blobs"])
        for entry in entries:
            if entry["blob"] in sizes and sizes[entry["blob"]] != entry["blob_bytes"]:
                errors.append(f"{label}: conflicting blob size")
            sizes[entry["blob"]] = entry["blob_bytes"]
        info["entries"] += len(entries)
    visited, active = set(), set()

    def visit(group):
        if group in active:
            errors.append(f"dependency cycle at {group!r}")
            return
        if group in visited:
            return
        active.add(group)
        for dependency in dependencies[group]:
            if dependency not in dependencies:
                errors.append(f"package {group!r}: missing dependency {dependency!r}")
            else:
                visit(dependency)
        active.remove(group)
        visited.add(group)

    for group in dependencies:
        visit(group)
    info.update(packages=len(catalog["packages"]), unique_blobs_referenced=len(referenced),
                referenced_blobs=sorted(referenced), blob_sizes=sizes, referenced_manifests=sorted(manifests))
    return errors, info


def verify_catalog(catalog_path, schema_path=None, *, retained_catalogs=None, root=None):
    """Verify default + all active channels + retained immutable history.

    Omitting retained_catalogs retains every archive (the legacy policy).
    Supplying an explicit history list NEVER discards active channel roots.
    Manifest files are objects, not roots. Failed-build objects are garbage.
    """
    catalog_path = Path(catalog_path).absolute()
    raw = catalog_path.read_bytes()
    historical = (catalog_path.parent.name == "catalogs"
                  and re.fullmatch(r"[0-9a-f]{64}\.json", catalog_path.name))
    if historical and catalog_path.stem != _sha256_bytes(raw):
        raise ValueError("historical catalog content address mismatch")
    root = Path(root).resolve() if root is not None else (catalog_path.parent.parent if historical else catalog_path.parent)
    root = root.resolve()
    if catalog_path.is_relative_to(root):
        _store_path(root, catalog_path.relative_to(root))
    catalog = strict_json(raw)
    # Verification-local cache: one physical representation is decoded once
    # across packages/releases. Every use rechecks filesystem identity/stamps;
    # nothing survives this call or bypasses changed-object verification.
    verified_blobs = set()
    errors, info = verify_catalog_data(catalog, root, schema_path, _verified_blobs=verified_blobs)
    if isinstance(catalog, dict) and catalog.get("schema") == "moly-asset-packs/2" and raw != _canonical_bytes(catalog):
        errors.append("v2 catalog is not canonical")
    active = set(info["referenced_blobs"])
    referenced = set(active)
    manifests = set(info.get("referenced_manifests", []))
    verified = {_sha256_bytes(raw): info}
    channel_roots = {}
    roots = {catalog_path.relative_to(root).as_posix(): "selected"} if catalog_path.is_relative_to(root) else {}

    def include(path, label):
        try:
            path = Path(path).absolute()
            _store_path(root, path.relative_to(root))
            raw = path.read_bytes()
            digest = _sha256_bytes(raw)
            if path.parent == root / "catalogs" and not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                raise ValueError("retained catalog must have a content-addressed filename")
            if re.fullmatch(r"[0-9a-f]{64}", path.stem) and path.stem != digest:
                raise ValueError("content address mismatch")
            doc = strict_json(raw)
            if isinstance(doc, dict) and doc.get("schema") == "moly-asset-packs/2" and raw != _canonical_bytes(doc):
                raise ValueError("v2 catalog is not canonical")
            if digest not in verified:
                failures, prior = verify_catalog_data(doc, root, schema_path, _verified_blobs=verified_blobs)
                errors.extend(f"{label}: {message}" for message in failures)
                verified[digest] = prior
            prior = verified[digest]
            referenced.update(prior["referenced_blobs"])
            manifests.update(prior.get("referenced_manifests", []))
            info["blob_sizes"].update(prior["blob_sizes"])
            roots[path.relative_to(root).as_posix()] = label
            return doc
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            return None

    channel_validator, _ = _load_schema_validator(json.loads(Path(__file__).with_name("channel.schema.json").read_bytes()))
    for selector in sorted((root / "channels").glob("*/*.json")):
        label = "channel " + selector.relative_to(root).as_posix()
        try:
            _store_path(root, selector.relative_to(root))
            pointer = strict_json(selector.read_bytes())
            failures = channel_validator(pointer)
            if failures:
                raise ValueError("; ".join(failures))
            if selector.parent.name != pointer["region"] or selector.stem != pointer["channel"]:
                raise ValueError("channel path and identity disagree")
            channel_roots[selector.relative_to(root).as_posix()] = pointer["catalog"]
            doc = include(root / pointer["catalog"], label)
            if doc is not None and (doc.get("schema") != "moly-asset-packs/2" or doc.get("region") != pointer["region"]):
                errors.append(f"{label}: selected release region/schema mismatch")
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
    archives = sorted((root / "catalogs").glob("*.json")) if retained_catalogs is None else [
        Path(p) if Path(p).is_absolute() else root / p for p in retained_catalogs]
    for archive in archives:
        include(archive, "retained catalog " + archive.name)
    # Even when verifying a historical selection, the default is an active root.
    default = root / "asset-packs.json"
    if default.is_file() and default.resolve() != catalog_path:
        include(default, "default")
    on_disk = {path.relative_to(root / "blobs").as_posix()
               for path in (root / "blobs").rglob("*") if path.is_file()}
    package_files = {path.relative_to(root).as_posix()
                     for path in (root / "packages").glob("*.json") if path.is_file()}
    info.update(retained_catalogs=len(archives), retained_blobs=sorted(referenced - active),
                orphan_blobs=sorted(on_disk - referenced), referenced_blobs=sorted(referenced),
                referenced_manifests=sorted(manifests), orphan_manifests=sorted(package_files - manifests),
                channel_roots=channel_roots, roots=roots, selected_catalog_sha256=_sha256_bytes(raw))
    return errors, info


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pack.verify", description="independently verify a packed manifest + blob store")
    ap.add_argument("--out", default=None, help="a pack.build output directory (manifest.json + blobs/)")
    ap.add_argument("--manifest", default=None, help="explicit manifest.json path (overrides --out)")
    ap.add_argument("--catalog", default=None, help="verify a grouped release and all retained catalogs")
    ap.add_argument("--blobs", default=None, help="explicit blobs directory (overrides --out)")
    ap.add_argument("--schema", default=None, help="alternate manifest schema (default: bundled)")
    args = ap.parse_args(argv)

    catalog = args.catalog
    if catalog is None and args.out and not args.manifest and (Path(args.out) / "asset-packs.json").is_file():
        catalog = Path(args.out) / "asset-packs.json"
    if catalog is not None:
        if args.manifest or args.blobs:
            ap.error("--catalog cannot be combined with --manifest or --blobs")
        try:
            errors, info = verify_catalog(catalog, args.schema, root=args.out)
        except (OSError, ValueError) as exc:
            print(f"FAIL {exc}")
            return 1
        for error in errors:
            print(f"FAIL {error}")
        if errors:
            return 1
        print(f"OK {info['packages']} packages, {info['entries']} entries, "
              f"{info['retained_catalogs']} retained catalogs; "
              f"{len(info['orphan_blobs'])} unreferenced blobs")
        return 0

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
        try:
            manifest = strict_json(manifest_path.read_bytes())
            prefix = manifest.get("blob_prefix", "blobs/")
            parent = manifest_path.parent
            if manifest.get("schema") == "moly-asset-manifest/2" and parent.name == "packages":
                parent = parent.parent
            blobs_dir = parent / prefix
        except (OSError, ValueError) as exc:
            print(f"FAIL cannot read manifest: {exc}")
            return 1

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
