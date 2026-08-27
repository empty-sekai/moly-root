"""Content-addressed asset packer.

Walks a source tree, classifies each file by extension (see
``pack.categories``), encodes it into the exact bytes that will be served at
its blob URL, and writes one immutable blob per distinct
(content, codec, http_encoding) combination plus a manifest describing every
logical path.

Two files with identical content that classify to the same codec and
http_encoding land on the same blob for free: the blob's name is the hash of
the bytes actually written, so there is never a second identity to invent for
the same bytes. A build-local cache keyed on (content_sha256, codec,
http_encoding) also means each distinct combination is only compressed once,
not once per path.

An optional ``--xf-overlay`` directory substitutes its own bytes for the
matching ``--src`` file at the same relative path, and records ``--xf-name``
as that entry's ``xf``. This is how a content transform computed out of band
(e.g. re-encoding a .glb's geometry into EXT_meshopt_compression) gets packed
without teaching this module anything about glTF: the overlay is just a
different source of bytes for a subset of paths, so it flows through the
existing hash/codec/dedup machinery unchanged. Every other path is untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import codecs
from .categories import VALID_XF, load_categories
from .hashing import sha256_bytes

SCHEMA_ID = "moly-asset-manifest/1"
DEFAULT_BLOB_PREFIX = "blobs/"


def iter_files(root: Path):
    """Every regular file under *root*, in a stable (sorted) order."""
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def build(src, out, version, *, categories_path=None, blob_prefix=DEFAULT_BLOB_PREFIX,
          progress=None, xf_overlay=None, xf_name=None,
          xf_overlay_expect_files=None, xf_overlay_expect_bytes=None) -> dict:
    """Pack *src* into *out*/blobs/ + *out*/manifest.json. Returns a report dict."""
    src = Path(src)
    out = Path(out)
    assert src.is_dir(), (
        f"--src is not a directory: {src!r} "
        f"(an MSYS-style path such as /c/data resolves to nothing on Windows "
        f"without raising, so pass a drive-qualified path instead)")

    if (xf_overlay is None) != (xf_name is None):
        raise ValueError("--xf-overlay and --xf-name must be given together (both or neither)")

    # rel path (posix, relative to xf_overlay) -> absolute Path of the
    # overlay file whose bytes replace the --src file at that path.
    overlay_index: dict[str, Path] = {}
    overlay_bytes_total = 0
    if xf_overlay is not None:
        if xf_name not in (VALID_XF - {None}):
            raise ValueError(f"--xf-name {xf_name!r} is not a known xf (want one of {sorted(VALID_XF - {None})})")
        xf_overlay = Path(xf_overlay)
        assert xf_overlay.is_dir(), (
            f"--xf-overlay is not a directory: {xf_overlay!r} "
            f"(an MSYS-style path such as /c/data resolves to nothing on Windows "
            f"without raising, so pass a drive-qualified path instead)")
        for p in iter_files(xf_overlay):
            rel = p.relative_to(xf_overlay).as_posix()
            overlay_index[rel] = p
            overlay_bytes_total += p.stat().st_size

        # Generic invariant, not specific to any one overlay: a file in the
        # overlay that has no counterpart under --src is not "replacing"
        # anything -- it would ship content at a path the source tree never
        # produced, silently. Caught here rather than left to surface later
        # as a manifest entry nobody can explain.
        missing = sorted(rel for rel in overlay_index if not (src / rel).is_file())
        if missing:
            raise RuntimeError(
                f"--xf-overlay has {len(missing)} file(s) with no counterpart under --src "
                f"(first 5: {missing[:5]})")

        # Optional, caller-supplied expectations -- generic mechanism (no
        # magic numbers live in this module), used to catch "the overlay
        # directory isn't what I think it is" before spending build time.
        if xf_overlay_expect_files is not None and len(overlay_index) != xf_overlay_expect_files:
            raise RuntimeError(
                f"--xf-overlay-expect-files={xf_overlay_expect_files} but --xf-overlay "
                f"({xf_overlay}) has {len(overlay_index)} files")
        if xf_overlay_expect_bytes is not None and overlay_bytes_total != xf_overlay_expect_bytes:
            raise RuntimeError(
                f"--xf-overlay-expect-bytes={xf_overlay_expect_bytes} but --xf-overlay "
                f"({xf_overlay}) files total {overlay_bytes_total} bytes")

    table = load_categories(categories_path)
    blobs_dir = out / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    # (content_sha256, codec, http_encoding) -> (blob_rel_path, blob_bytes_len, blob_sha256, content_len)
    # content_len (the decoded length) is cached here too because the four
    # manifest totals below need it per DISTINCT blob, not per entry -- and
    # every entry sharing a blob decodes to the same content by construction,
    # so the value cached at first sight of the blob is correct for all of
    # them.
    blob_cache: dict[tuple, tuple] = {}
    files_seen = 0
    overlay_applied = 0

    for path in iter_files(src):
        rel = path.relative_to(src).as_posix()
        overlay_path = overlay_index.get(rel)
        if overlay_path is not None:
            content = overlay_path.read_bytes()
            overlay_applied += 1
        else:
            content = path.read_bytes()
        content_sha = sha256_bytes(content)
        rule = table.classify(path.suffix)
        entry_xf = xf_name if overlay_path is not None else rule.xf
        cache_key = (content_sha, rule.codec, rule.http_encoding)

        cached = blob_cache.get(cache_key)
        if cached is None:
            encoded = codecs.encode_blob(content, rule.codec, rule.http_encoding)
            # Round-trip guard: an encoder that produced bytes which do not
            # decode back to the source content would silently corrupt every
            # path that shares this blob. Caught here, at write time, rather
            # than later by verify.py on a blob already on disk.
            back = codecs.decode_blob(encoded, rule.codec, rule.http_encoding)
            if back != content:
                raise RuntimeError(
                    f"encode/decode round trip failed for {rel} "
                    f"(codec={rule.codec}, http_encoding={rule.http_encoding})")
            blob_sha = sha256_bytes(encoded)
            blob_rel = codecs.derive_blob_path(blob_sha, rule.codec, rule.http_encoding)
            dest = blobs_dir / blob_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(encoded)
            cached = (blob_rel, len(encoded), blob_sha, len(content))
            blob_cache[cache_key] = cached
        blob_rel, blob_len, blob_sha, _content_len = cached

        entries.append({
            "path": rel,
            "blob": blob_rel,
            "blob_bytes": blob_len,
            "blob_sha256": blob_sha,
            "bytes": len(content),
            "content_sha256": content_sha,
            "codec": rule.codec,
            "http_encoding": rule.http_encoding,
            "xf": entry_xf,
        })
        files_seen += 1
        if progress and files_seen % progress == 0:
            print(f"  ... {files_seen} files", file=sys.stderr)

    if not entries:
        raise RuntimeError(
            f"no files found under {src} -- an empty result here almost always means "
            f"the wrong path was handed in, not an empty asset tree")

    if overlay_index and overlay_applied != len(overlay_index):
        # Every overlay path was already proven to exist under --src above;
        # iter_files(src) walking the same tree should therefore visit every
        # one of them exactly once. Anything else means the two walks
        # disagree (e.g. a path component that is a symlink on one side),
        # which would silently ship some overlay bytes as if unmodified.
        raise RuntimeError(
            f"overlay applied to {overlay_applied} entries but overlay_index has "
            f"{len(overlay_index)} paths -- src walk and overlay walk disagree")

    entries.sort(key=lambda e: e["path"])

    # Four totals, per the manifest schema's contract verbatim (schema.json's
    # per-field description is the source of truth here, not this comment):
    #   download_bytes / resident_bytes / content_bytes each sum over DISTINCT
    #   blobs -- a blob referenced by N paths is transferred and stored once,
    #   not N times, so summing per entry would double-count every shared
    #   blob by exactly the amount content addressing was supposed to save.
    #   logical_bytes is the one exception: it sums bytes over ALL entries
    #   (the asset tree's own, pre-dedup size), so that
    #   logical_bytes - content_bytes reads directly as what deduplication
    #   saved without recomputing anything.
    download_bytes = sum(blob_len for _rel, blob_len, _sha, _clen in blob_cache.values())
    resident_bytes = sum(
        (content_len if http_encoding == "br" else blob_len)
        for (_content_sha, _codec, http_encoding), (_rel, blob_len, _sha, content_len)
        in blob_cache.items()
    )
    content_bytes = sum(content_len for _rel, _blob_len, _sha, content_len in blob_cache.values())
    logical_bytes = sum(e["bytes"] for e in entries)

    manifest = {
        "schema": SCHEMA_ID,
        "version": version,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "blob_prefix": blob_prefix,
        "download_bytes": download_bytes,
        "resident_bytes": resident_bytes,
        "content_bytes": content_bytes,
        "logical_bytes": logical_bytes,
        "entries": entries,
    }

    manifest_path = out / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n")

    # Dedup savings, in gen.mjs's own terms: logical_bytes minus content_bytes
    # (both already computed above) is exactly the decoded bytes that did not
    # have to be stored a second time.
    dedup_saved_bytes = logical_bytes - content_bytes

    return {
        "src": str(src),
        "out": str(out),
        "version": version,
        "files": files_seen,
        "entries": len(entries),
        "unique_blobs": len(blob_cache),
        "download_bytes": download_bytes,
        "resident_bytes": resident_bytes,
        "content_bytes": content_bytes,
        "logical_bytes": logical_bytes,
        "dedup_saved_bytes": dedup_saved_bytes,
        "xf_overlay": str(xf_overlay) if xf_overlay is not None else None,
        "xf_name": xf_name,
        "xf_overlay_files": len(overlay_index),
        "xf_overlay_bytes": overlay_bytes_total,
        "xf_applied_entries": overlay_applied,
        "brotli_backend": codecs.brotli_backend(),
        "gzip_backend": codecs.gzip_backend(),
        "manifest": str(manifest_path),
        "blobs_dir": str(blobs_dir),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pack.build", description="pack a source tree into a content-addressed blob store + manifest")
    ap.add_argument("--src", required=True, help="asset root to pack")
    ap.add_argument("--out", required=True, help="output directory (gets blobs/ and manifest.json)")
    ap.add_argument("--version", required=True, help="opaque build identity written into the manifest")
    ap.add_argument("--categories", default=None, help="alternate categories.toml (default: the bundled table)")
    ap.add_argument("--blob-prefix", default=DEFAULT_BLOB_PREFIX)
    ap.add_argument("--progress", type=int, default=0, help="print progress every N files (0 = silent)")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--xf-overlay", default=None,
                     help="directory whose files replace --src content at matching relative paths "
                          "(requires --xf-name); every overlay path must exist under --src")
    ap.add_argument("--xf-name", default=None,
                     help="xf value recorded for entries sourced from --xf-overlay "
                          "(one of: meshopt, draco, ktx2)")
    ap.add_argument("--xf-overlay-expect-files", type=int, default=None,
                     help="fail before packing unless --xf-overlay contains exactly this many files")
    ap.add_argument("--xf-overlay-expect-bytes", type=int, default=None,
                     help="fail before packing unless --xf-overlay files total exactly this many bytes")
    args = ap.parse_args(argv)

    t0 = time.time()
    report = build(args.src, args.out, args.version,
                    categories_path=args.categories, blob_prefix=args.blob_prefix,
                    progress=args.progress or None,
                    xf_overlay=args.xf_overlay, xf_name=args.xf_name,
                    xf_overlay_expect_files=args.xf_overlay_expect_files,
                    xf_overlay_expect_bytes=args.xf_overlay_expect_bytes)
    report["wall_seconds"] = round(time.time() - t0, 3)

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"packed {report['files']} files -> {report['entries']} entries, "
              f"{report['unique_blobs']} unique blobs ({report['wall_seconds']}s)")
        print(f"  download_bytes {report['download_bytes']:>12d}")
        print(f"  resident_bytes {report['resident_bytes']:>12d}")
        print(f"  content_bytes  {report['content_bytes']:>12d}")
        print(f"  logical_bytes  {report['logical_bytes']:>12d}")
        print(f"  dedup saved    {report['dedup_saved_bytes']:>12d} bytes "
              f"({report['entries']} entries -> {report['unique_blobs']} blobs on disk)")
        if report["xf_overlay"] is not None:
            print(f"  xf overlay: {report['xf_overlay']} (xf={report['xf_name']}), "
                  f"{report['xf_overlay_files']} files / {report['xf_overlay_bytes']} bytes, "
                  f"{report['xf_applied_entries']} entries transformed")
        print(f"  brotli backend: {report['brotli_backend']}")
        print(f"  gzip backend:   {report['gzip_backend']}")
        print(f"  manifest: {report['manifest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
