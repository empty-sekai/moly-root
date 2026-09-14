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
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_SCHEMA_PATH = Path(__file__).with_name("manifest.schema.json")


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


def verify_blob(entry, blobs_dir) -> list[str]:
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
        root = Path(blobs_dir).resolve()
        path = (root / blob).resolve()
        if not path.is_relative_to(root):
            return ["blob path escapes the blob store"]
        raw = path.read_bytes()
        if len(raw) != entry["blob_bytes"]:
            errors.append("blob file size does not match blob_bytes")
        if _sha256_bytes(raw) != entry["blob_sha256"]:
            errors.append("blob file sha256 does not match blob_sha256")
        content = _decode_content(raw, codec, encoding)
        if len(content) != entry["bytes"]:
            errors.append("decoded length does not match bytes")
        if _sha256_bytes(content) != entry["content_sha256"]:
            errors.append("decoded content sha256 does not match content_sha256")
    except Exception as exc:
        errors.append(f"cannot verify blob: {exc}")
    return errors


def verify(manifest_path, blobs_dir, schema_path=None, *, check_orphans=True) -> tuple[list[str], dict]:
    """Returns (errors, info). errors is empty iff *out* is fully valid."""
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    return verify_manifest(manifest, blobs_dir, schema_path, check_orphans=check_orphans)


def verify_manifest(manifest, blobs_dir, schema_path=None, *, check_orphans=True) -> tuple[list[str], dict]:
    blobs_dir = Path(blobs_dir)
    schema_path = Path(schema_path) if schema_path else DEFAULT_SCHEMA_PATH
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
            errors.extend(f"entries[{i}]: {message}" for message in verify_blob(e, blobs_dir))
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
                    or not all(key in recipe for key in ("tool", "tool_version", "params"))):
                errors.append(f"transforms: invalid recipe for {name!r}")
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


def verify_catalog_data(catalog, root, schema_path=None) -> tuple[list[str], dict]:
    """Check package identities, ownership, dependencies and every referenced blob."""
    root = Path(root).resolve()
    errors, referenced, sizes = [], set(), {}
    info = {"entries": 0, "packages": 0, "unique_blobs_referenced": 0,
            "referenced_blobs": [], "blob_sizes": {}}
    if (not isinstance(catalog, dict) or catalog.get("schema") != "moly-asset-packs/1"
            or not isinstance(catalog.get("version"), str) or not catalog["version"]
            or not isinstance(catalog.get("packages"), list) or not catalog["packages"]):
        return ["invalid or empty asset catalog"], info
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
        path = (root / name).resolve()
        if not path.is_relative_to(root) or name in manifests:
            errors.append(f"{label}: escaping or duplicate manifest path")
            continue
        manifests.add(name)
        try:
            raw = path.read_bytes()
            document = json.loads(raw)
            if re.fullmatch(r"packages/[0-9a-f]{64}\.json", name) \
                    and path.stem != _sha256_bytes(raw):
                errors.append(f"{label}: manifest content address mismatch")
            failures, checked = verify_manifest(document, root / "blobs", schema_path, check_orphans=False)
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: cannot read manifest: {exc}")
            continue
        errors.extend(f"{label}: {message}" for message in failures)
        if failures:
            continue
        if document["version"] != catalog["version"]:
            errors.append(f"{label}: version differs from catalog")
        if document["blob_prefix"] != "blobs/":
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
                referenced_blobs=sorted(referenced), blob_sizes=sizes)
    return errors, info


def verify_catalog(catalog_path, schema_path=None, *, retained_catalogs=None, root=None):
    """Verify a release and its retained generations; report unreferenced files.

    Extra blobs are GC candidates, not release-integrity failures. Failed builds
    can legitimately leave them behind. All retained catalogs are roots, not
    just one package's manifest or the active generation.
    """
    catalog_path = Path(catalog_path).resolve()
    raw = catalog_path.read_bytes()
    historical = (catalog_path.parent.name == "catalogs"
                  and re.fullmatch(r"[0-9a-f]{64}\.json", catalog_path.name))
    if historical and catalog_path.stem != _sha256_bytes(raw):
        raise ValueError("historical catalog content address mismatch")
    if root is not None:
        root = Path(root).resolve()
    else:
        root = catalog_path.parent
        if historical:
            root = root.parent
    catalog = json.loads(raw)
    errors, info = verify_catalog_data(catalog, root, schema_path)
    active = set(info["referenced_blobs"])
    retained = set()
    archives = list((root / "catalogs").glob("*.json")) if retained_catalogs is None else list(retained_catalogs)
    for archive in archives:
        archive = Path(archive)
        try:
            raw = archive.read_bytes()
            if re.fullmatch(r"[0-9a-f]{64}", archive.stem) and archive.stem != _sha256_bytes(raw):
                errors.append(f"retained catalog {archive.name}: content address mismatch")
            failures, prior = verify_catalog_data(json.loads(raw), root, schema_path)
            errors.extend(f"retained catalog {archive.name}: {message}" for message in failures)
            retained.update(prior["referenced_blobs"])
            info["blob_sizes"].update(prior["blob_sizes"])
        except (OSError, ValueError) as exc:
            errors.append(f"retained catalog {archive.name}: {exc}")
    on_disk = {path.relative_to(root / "blobs").as_posix()
               for path in (root / "blobs").rglob("*") if path.is_file()}
    info.update(retained_catalogs=len(archives), retained_blobs=sorted(retained - active),
                orphan_blobs=sorted(on_disk - active - retained),
                referenced_blobs=sorted(active | retained))
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
