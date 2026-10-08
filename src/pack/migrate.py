"""Lossless v1 catalog migration within its existing CAS store.

Original manifests/catalogs remain immutable history. No blob is re-encoded;
unknown legacy encoder versions are explicitly recorded, never fabricated.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from core.atomic import PUBLIC_FILE_MODE, exclusive_lock, write_bytes
from core.jp import public_release_provenance
from .hashing import sha256_bytes
from .groups import validate_public_source_document
from .identity import CATALOG_V2, canonical_bytes, manifest_v2
from .limits import MAX_RUNTIME_DOCUMENT_BYTES
from .paths import output_path
from .release import commit_catalog, validate_selector
from .verify import _decode_content, strict_json, verify_catalog_data


def migrate_catalog(output, *, region, version=None, provenance=None, catalog=None, channel="stable"):
    output = Path(output).resolve()
    validate_selector(region, channel)
    if provenance is not None and not isinstance(provenance, dict):
        raise ValueError("provenance must be a JSON object")
    provenance = public_release_provenance(provenance or {})
    with exclusive_lock(output / ".publish.lock"):
        source = output_path(output, catalog or "asset-packs.json")
        raw = source.read_bytes()
        original = strict_json(raw)
        if original.get("schema") != "moly-asset-packs/1":
            raise ValueError("migration requires a v1 catalog")
        if source.parent.name == "catalogs" and source.stem != sha256_bytes(raw):
            raise ValueError("legacy catalog content address mismatch")
        errors, _ = verify_catalog_data(original, output)
        if errors:
            raise ValueError("cannot migrate an invalid legacy release: " + "; ".join(errors))
        manifests = []
        for package in original["packages"]:
            old = strict_json(output_path(output, package["manifest"]).read_bytes())
            for entry in old["entries"]:
                if entry["path"] == "source.json":
                    if entry["bytes"] > MAX_RUNTIME_DOCUMENT_BYTES:
                        raise ValueError("source.json exceeds the runtime document budget")
                    blob = output_path(output, "blobs/" + entry["blob"]).read_bytes()
                    content = _decode_content(blob, entry["codec"], entry["http_encoding"], entry["bytes"])
                    validate_public_source_document(strict_json(content))
            manifests.append((package, old))
        legacy_name = f"catalogs/{sha256_bytes(raw)}.json"
        write_bytes(output_path(output, legacy_name), raw, mode=PUBLIC_FILE_MODE)
        packages = []
        for package, old in manifests:
            used = {"brotli" if entry["http_encoding"] == "br" else entry["codec"] for entry in old["entries"]} - {"identity"}
            encoders = {codec: {"implementation": "legacy-v1", "version": "unrecorded"} for codec in sorted(used)}
            document = manifest_v2(old, encoders)
            data = canonical_bytes(document)
            name = f"packages/{sha256_bytes(data)}.json"
            write_bytes(output_path(output, name), data, mode=PUBLIC_FILE_MODE)
            packages.append({key: package[key] for key in ("id", "kind", "dependencies", "paths", "download_bytes", "content_bytes")}
                            | {"manifest": name, "content_id": document["content_id"]})
        release = {"schema": CATALOG_V2, "region": region, "version": version or original["version"],
                   "provenance": {"legacy_catalog": legacy_name, "migration": "lossless-v1-to-v2",
                                  "source": provenance or {}}, "packages": packages}
        commit_catalog(release, output, channel=channel)
        return release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--version")
    parser.add_argument("--catalog", help="legacy catalog path relative to the store")
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--provenance", help="JSON provenance input")
    args = parser.parse_args(argv)
    document = migrate_catalog(args.out, region=args.region, version=args.version, catalog=args.catalog,
                               channel=args.channel, provenance=strict_json(Path(args.provenance).read_bytes()) if args.provenance else None)
    print(canonical_bytes({"catalog": f"catalogs/{sha256_bytes(canonical_bytes(document))}.json",
                           "packages": len(document["packages"]), "reencoded_blobs": 0}).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
