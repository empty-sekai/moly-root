"""Produce reviewable diffs and exact pre/post-image delivery inside this repo."""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
from pathlib import Path
import re

from apply_moly_asset_v2 import EXCLUDED, atomic_replace, is_link, sha, strict_json, validate_relative


def inputs(root):
    result, aliases = {}, set()

    def fail_walk(error):
        raise error

    for directory, names, files in os.walk(root, onerror=fail_walk):
        names[:] = sorted(name for name in names if name.casefold() not in EXCLUDED)
        for name in names + sorted(files):
            path = Path(directory) / name
            if is_link(path):
                raise ValueError("snapshot contains a link: " + str(path))
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            validate_relative(relative, asset_boundary=False)
            if relative.casefold() in aliases:
                raise ValueError("snapshot contains case-aliased paths: " + relative)
            aliases.add(relative.casefold())
            result[relative] = path.read_bytes()
    return result


def diff_chunk(relative, before, after):
    # The bundle preserves exact binary bytes and line endings; the patch is a
    # human-readable, LF-normalized review view of the same change.
    def label(prefix):
        name = prefix + "/" + relative
        return json.dumps(name) if any(char.isspace() or ord(char) > 126 for char in name) else name

    old_label = label("a") if before is not None else "/dev/null"
    new_label = label("b") if after is not None else "/dev/null"
    chunks = [f"diff --git {label('a')} {label('b')}\n"]
    if before is None:
        chunks.append("new file mode 100644\n")
    if after is None:
        chunks.append("deleted file mode 100644\n")
    try:
        old_lines = (before or b"").decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True)
        new_lines = (after or b"").decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True)
    except UnicodeDecodeError:
        chunks.append(f"Binary files {old_label} and {new_label} differ\n")
        return "".join(chunks)
    for line in difflib.unified_diff(old_lines, new_lines, fromfile=old_label, tofile=new_label):
        chunks.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(chunks)


def generate(baseline, updated, output, patch, base_head, baseline_hashes):
    baseline, updated = Path(baseline).resolve(strict=True), Path(updated).resolve(strict=True)
    if not baseline.is_dir() or not updated.is_dir() or baseline.is_relative_to(updated) or updated.is_relative_to(baseline):
        raise ValueError("baseline and updated snapshot must be separate directories")
    if not isinstance(base_head, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_head):
        raise ValueError("base-head must be a full lowercase Git commit hash")
    destinations = [Path(name).resolve() for name in (output, patch)]
    if destinations[0] == destinations[1]:
        raise ValueError("bundle and patch outputs must differ")
    for destination in destinations:
        if destination.is_relative_to(baseline) or destination.is_relative_to(updated):
            raise ValueError("delivery output cannot modify an input snapshot")
        if destination == Path(baseline_hashes).resolve():
            raise ValueError("delivery output cannot overwrite the baseline hash receipt")
    old, new = inputs(baseline), inputs(updated)
    baseline_bytes = Path(baseline_hashes).read_bytes()
    expected = strict_json(baseline_bytes)
    actual = {relative: sha(data) for relative, data in old.items()}
    if expected != actual:
        raise ValueError("captured baseline does not match baseline hash receipt")
    files, chunks = [], []
    for relative in sorted(old.keys() | new.keys()):
        before, after = old.get(relative), new.get(relative)
        if before == after:
            continue
        validate_relative(relative)
        files.append({"path": relative, "before_sha256": sha(before), "after_sha256": sha(after),
                      "before": base64.b64encode(before).decode() if before is not None else None,
                      "after": base64.b64encode(after).decode() if after is not None else None})
        chunks.append(diff_chunk(relative, before, after))
    if not files:
        raise ValueError("no consumer changes to deliver")
    bundle = {"schema": "moly-consumer-upgrade/1", "source_base": base_head,
              "baseline_manifest_sha256": sha(baseline_bytes),
              "baseline_policy": "Captured dirty working-tree input; exact per-file preimages, not HEAD, are authoritative. Unrelated files are untouched.",
              "files": files}
    bundle_bytes = (json.dumps(bundle, sort_keys=True, separators=(",", ":")) + "\n").encode()
    patch_bytes = "".join(chunks).encode()
    for destination, data in zip(destinations, (bundle_bytes, patch_bytes)):
        atomic_replace(destination, data)
    return {"changed_files": [row["path"] for row in files], "bundle_sha256": sha(bundle_bytes),
            "patch_sha256": sha(patch_bytes), "moly_game_changes": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--updated", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--base-head", required=True)
    parser.add_argument("--baseline-hashes", required=True, help="captured SHA-256 receipt for every baseline source file")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(generate(args.baseline, args.updated, args.output, args.patch, args.base_head, args.baseline_hashes), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
