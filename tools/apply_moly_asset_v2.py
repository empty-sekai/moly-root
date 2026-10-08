"""Hash-guarded cross-repository delivery; defaults to a read-only preflight.

Use --target Moly checkout --apply to install, or add --reverse to restore the
captured preimages. Stop other editors/build generators during application.
Every file is preflighted before the first write, each replacement is atomic,
ordinary failures are rolled back, and interrupted partial installs are safely
resumable. This does not claim filesystem-wide atomicity across process crashes.
No git checkout, reset, index edit, branch creation, or commit is performed.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


EXCLUDED = {".git", "node_modules", "target", "pkg", "qa", "screenshots", "__pycache__", ".cache", "dist"}
RESERVED_NAMES = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$"} | {
    prefix + suffix for prefix in ("com", "lpt") for suffix in "123456789¹²³"
}


def sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def strict_json(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate JSON field: " + key)
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=pairs)


def validate_relative(relative, *, asset_boundary=True):
    """Reject aliases on both POSIX and Windows, regardless of the current host."""
    if not isinstance(relative, str) or not relative:
        raise ValueError("invalid consumer upgrade path: " + str(relative))
    parts = relative.split("/")
    if any(
        part in {"", ".", ".."} or part.endswith((".", " "))
        or part.split(".", 1)[0].casefold() in RESERVED_NAMES
        or any(ord(char) < 32 or char in '<>:"\\|?*' for char in part)
        or part.casefold() in EXCLUDED
        for part in parts
    ):
        raise ValueError("invalid consumer upgrade path: " + relative)
    if asset_boundary and not (
        relative.startswith(("crates/moly-assets/", "web/"))
        or relative == "crates/moly-app/src/asset_source.rs"
    ):
        raise ValueError("consumer edit outside the asset boundary: " + relative)
    return relative


def is_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def checked_path(root, relative):
    validate_relative(relative)
    current = root
    for part in relative.split("/"):
        current = current / part
        if is_link(current):
            raise ValueError("consumer upgrade path traverses a link: " + relative)
    if not current.resolve().is_relative_to(root):
        raise ValueError("consumer path escapes target: " + relative)
    if current.exists() and not current.is_file():
        raise ValueError("consumer path is not a file: " + relative)
    return current


def read_optional(path):
    return path.read_bytes() if path.exists() else None


def atomic_replace(path, data, mode=0o644):
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".moly-v2-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def decode_image(row, side):
    if side not in row or side + "_sha256" not in row:
        raise ValueError("missing upgrade bundle payload: " + side)
    encoded, digest = row[side], row[side + "_sha256"]
    if encoded is None:
        if digest is not None:
            raise ValueError("absent upgrade payload has a hash")
        return None
    if not isinstance(encoded, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid upgrade bundle payload")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("invalid upgrade bundle payload encoding") from error
    if sha(data) != digest:
        raise ValueError("damaged upgrade bundle payload: " + row.get("path", "unknown"))
    return data


def apply_bundle(target, bundle_path=None, *, apply=False, reverse=False):
    target = Path(target).resolve(strict=True)
    if not target.is_dir():
        raise ValueError("consumer target must be a directory")
    if bundle_path is None:
        raise ValueError("an explicit consumer upgrade bundle is required")
    bundle_path = Path(bundle_path)
    bundle_bytes = bundle_path.read_bytes()
    bundle = strict_json(bundle_bytes)
    if (not isinstance(bundle, dict) or bundle.get("schema") != "moly-consumer-upgrade/1"
            or not isinstance(bundle.get("files"), list) or not bundle["files"]):
        raise ValueError("unsupported or empty consumer upgrade bundle")
    source_side, target_side = ("after", "before") if reverse else ("before", "after")
    plan, seen, unchanged = [], set(), []
    for row in bundle["files"]:
        if not isinstance(row, dict):
            raise ValueError("invalid consumer upgrade row")
        relative = validate_relative(row.get("path"))
        alias = relative.casefold()
        if alias in seen:
            raise ValueError("duplicate consumer upgrade path: " + relative)
        seen.add(alias)
        path = checked_path(target, relative)
        before, after = decode_image(row, source_side), decode_image(row, target_side)
        if before == after:
            raise ValueError("consumer upgrade row has no change: " + relative)
        actual = read_optional(path)
        if sha(actual) == sha(after):
            unchanged.append(relative)
            continue
        if sha(actual) != sha(before):
            raise ValueError("preimage conflict; no files changed: " + relative)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        plan.append((relative, path, before, after, mode))
    for relative in seen:
        parts = relative.split("/")
        if any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
            raise ValueError("overlapping consumer upgrade paths: " + relative)
    written, created_directories = [], set()
    if apply:
        try:
            for relative, path, before, after, mode in plan:
                checked_path(target, relative)
                if sha(read_optional(path)) != sha(before):
                    raise ValueError("file changed after preflight: " + relative)
                parent = path.parent
                while not parent.exists():
                    created_directories.add(parent)
                    parent = parent.parent
                atomic_replace(path, after, mode)
                written.append((relative, path, before, after, mode))
        except BaseException as error:
            rollback_errors = []
            for relative, path, before, after, mode in reversed(written):
                try:
                    checked_path(target, relative)
                    if sha(read_optional(path)) != sha(after):
                        raise ValueError("concurrent edit detected")
                    atomic_replace(path, before, mode)
                except BaseException as rollback:
                    rollback_errors.append(f"{relative}: {rollback}")
            for directory in sorted(created_directories, key=lambda path: len(path.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass  # Preserve directories containing a concurrent edit.
            if rollback_errors:
                raise RuntimeError(f"upgrade failed ({error}); rollback needs review: " + "; ".join(rollback_errors)) from error
            raise
    return {"schema": "moly-consumer-upgrade-result/1", "target": str(target), "bundle_sha256": sha(bundle_bytes),
            "source_base": bundle.get("source_base"), "mode": "reverse" if reverse else "forward", "applied": apply,
            "changed" if apply else "would_change": [row[0] for row in plan], "already_current": unchanged}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--bundle", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="read-only preflight (default)")
    action.add_argument("--apply", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(apply_bundle(args.target, args.bundle, apply=args.apply, reverse=args.reverse), indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
