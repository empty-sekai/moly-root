"""Import a verified release into a shared store without snapshot payload copies.

This is transport/publication for src/pack's existing CAS, not another CAS.
The full graph is independently verified before any mutable selector moves.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re
from core.atomic import PUBLIC_FILE_MODE, exclusive_lock, write_bytes
from .hashing import sha256_bytes, sha256_file
from .identity import CATALOG_V2, canonical_bytes
from .paths import output_path, separate_output
from .release import commit_catalog, validate_selector
from .verify import strict_json, verify_catalog_data


def publish_catalog(source, output, *, catalog=None, channel="stable"):
    source, output = separate_output(source, output)
    name = f"catalogs/{catalog}.json" if isinstance(catalog, str) and re.fullmatch(r"[0-9a-f]{64}", catalog) else (catalog or "asset-packs.json")
    # Hold both stores against cooperating builders and GC. Locks fail rather
    # than wait, so inverse imports cannot deadlock. Files are copied, not linked.
    with exclusive_lock(source / ".publish.lock"), exclusive_lock(output / ".publish.lock"):
        selected = output_path(source, name)
        raw = selected.read_bytes()
        release = strict_json(raw)
        if release.get("schema") != CATALOG_V2:
            raise ValueError("publication requires v2; migrate the v1 catalog first")
        validate_selector(release.get("region"), channel)
        if name.startswith("catalogs/") and selected.stem != sha256_bytes(raw):
            raise ValueError("catalog content address mismatch")
        if raw != canonical_bytes(release):
            raise ValueError("v2 catalog bytes must be canonical")
        failures, checked = verify_catalog_data(release, source)
        if failures:
            raise ValueError("cannot publish an invalid release: " + "; ".join(failures))
        copied_bytes = reused_bytes = 0
        copied_objects = reused_objects = 0
        objects = [(item, item.rsplit("/", 1)[-1][:-5]) for item in checked["referenced_manifests"]]
        for blob in checked["referenced_blobs"]:
            objects.append(("blobs/" + blob, blob.rsplit("/", 1)[-1].split(".", 1)[0]))
        for relative, expected_hash in sorted(objects):
            path = output_path(source, relative)
            data = path.read_bytes()
            if sha256_bytes(data) != expected_hash:
                raise ValueError("source changed after verification: " + relative)
            destination = output_path(output, relative)
            if destination.exists():
                if not destination.is_file() or destination.stat().st_size != len(data) or sha256_file(destination) != expected_hash:
                    raise ValueError("destination has a damaged immutable object: " + relative)
                reused_objects += 1; reused_bytes += len(data)
            else:
                write_bytes(destination, data, mode=PUBLIC_FILE_MODE)
                copied_objects += 1; copied_bytes += len(data)
        archive = commit_catalog(release, output, channel=channel)
        return {"schema": "moly-asset-publication/1", "catalog": archive,
                "region": release["region"], "version": release["version"],
                "copied_objects": copied_objects, "copied_bytes": copied_bytes,
                "reused_objects": reused_objects, "reused_bytes": reused_bytes,
                "packages": checked["packages"], "entries": checked["entries"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--catalog", help="full catalog SHA-256 or path relative to --src")
    parser.add_argument("--channel", default="stable")
    args = parser.parse_args(argv)
    print(canonical_bytes(publish_catalog(args.src, args.out, catalog=args.catalog, channel=args.channel)).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
