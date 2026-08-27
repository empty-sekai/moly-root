"""Upgrade diff between two manifests: which blobs to delete, which are
shared, and which are new.

This module exists for one reason: without it, an upgrade has no way to know
which of the old version's blobs stopped being referenced by the new
version, and the only safe-looking default -- keep everything -- means every
upgrade leaves the previous version's full blob set on disk next to the new
one. Two versions coexisting is a doubling, not a rounding error, and it
compounds on every release this step is skipped for.
"""
from __future__ import annotations

import argparse
import json
import sys


def _blob_bytes_index(manifest: dict) -> dict[str, int]:
    """blob path -> blob_bytes, deduped across entries that share a blob.

    A manifest can have many entries pointing at the same blob (that is the
    whole point of content addressing); this collapses to one row per blob
    and checks that every entry referencing a given blob agrees on its size,
    since two different sizes for the same blob path would mean the manifest
    itself is internally inconsistent.
    """
    index: dict[str, int] = {}
    for e in manifest["entries"]:
        blob = e["blob"]
        blob_bytes = e["blob_bytes"]
        if blob in index and index[blob] != blob_bytes:
            raise ValueError(
                f"manifest is internally inconsistent: blob {blob!r} has blob_bytes "
                f"{index[blob]} from one entry and {blob_bytes} from another")
        index[blob] = blob_bytes
    return index


def diff_manifests(old: dict, new: dict) -> dict:
    """The upgrade diff from *old* to *new*.

    delete   = blobs only the old manifest references -- must be removed
               after the new version is live, or disk use doubles.
    shared   = blobs both manifests reference -- kept, nothing to transfer.
    download = blobs only the new manifest references -- what the upgrade
               actually has to fetch.
    """
    old_blobs = _blob_bytes_index(old)
    new_blobs = _blob_bytes_index(new)
    old_set, new_set = set(old_blobs), set(new_blobs)

    delete = sorted(old_set - new_set)
    shared = sorted(old_set & new_set)
    download = sorted(new_set - old_set)

    return {
        "old_version": old.get("version"),
        "new_version": new.get("version"),
        "delete": delete,
        "delete_bytes": sum(old_blobs[b] for b in delete),
        "shared": shared,
        "shared_bytes": sum(old_blobs[b] for b in shared),
        "download": download,
        "download_bytes": sum(new_blobs[b] for b in download),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pack.gc", description="upgrade diff between two manifests")
    ap.add_argument("--old", required=True, help="old manifest.json")
    ap.add_argument("--new", required=True, help="new manifest.json")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv)

    with open(args.old, encoding="utf-8") as fh:
        old = json.load(fh)
    with open(args.new, encoding="utf-8") as fh:
        new = json.load(fh)

    result = diff_manifests(old, new)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"old={result['old_version']}  new={result['new_version']}")
        print(f"  delete    {len(result['delete']):5d} blobs  {result['delete_bytes']:12d} bytes  (must be removed)")
        print(f"  shared    {len(result['shared']):5d} blobs  {result['shared_bytes']:12d} bytes  (kept)")
        print(f"  download  {len(result['download']):5d} blobs  {result['download_bytes']:12d} bytes  (new transfer)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
