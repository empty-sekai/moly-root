"""Delivery is exact, reversible and rejects conflicts before touching a checkout."""
from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import apply_moly_asset_v2 as installer
import build_moly_asset_v2_bundle as builder


HEAD = "1" * 40


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def delivery(tmp_path):
    baseline, updated = tmp_path / "baseline", tmp_path / "updated"
    old = {
        "Cargo.toml": b"[workspace]\r\n",
        "crates/moly-assets/src/packs.rs": b"old reader\r\n",
        "crates/moly-app/src/asset_source.rs": b"old source without newline",
        "crates/moly-game/src/lib.rs": b"stable moly:// ABI\r\n",
        "web/obsolete.mjs": b"obsolete\n",
        "web/keep.mjs": b"unrelated dirty source\r\n",
    }
    for relative, data in old.items():
        write(baseline / relative, data)
    shutil.copytree(baseline, updated)
    write(updated / "crates/moly-assets/src/packs.rs", b"new reader\r\n")
    write(updated / "crates/moly-app/src/asset_source.rs", b"new source without newline")
    write(updated / "web/nested/new.mjs", b"new module\n")
    write(updated / "web/empty.mjs", b"")
    (updated / "web/obsolete.mjs").unlink()
    receipt = tmp_path / "baseline-hashes.json"
    receipt.write_text(json.dumps({path: installer.sha(data) for path, data in old.items()}, sort_keys=True), encoding="utf-8")
    bundle, patch = tmp_path / "delivery.json", tmp_path / "delivery.patch"
    report = builder.generate(baseline, updated, bundle, patch, HEAD, receipt)
    target = tmp_path / "target"
    shutil.copytree(baseline, target)
    return {"baseline": baseline, "updated": updated, "target": target, "bundle": bundle, "patch": patch,
            "receipt": receipt, "report": report, "tmp": tmp_path}


def regenerate(delivery, **kwargs):
    values = dict(baseline=delivery["baseline"], updated=delivery["updated"], output=delivery["bundle"],
                  patch=delivery["patch"], base_head=HEAD, baseline_hashes=delivery["receipt"])
    values.update(kwargs)
    return builder.generate(**values)


def mutate_bundle(delivery, change):
    bundle = json.loads(delivery["bundle"].read_bytes())
    change(bundle)
    delivery["bundle"].write_text(json.dumps(bundle), encoding="utf-8")


def test_preflight_exact_apply_idempotence_reverse_and_unchanged_files(delivery):
    target, bundle = delivery["target"], delivery["bundle"]
    write(target / "unrelated-local.txt", b"preserve local additions")
    write(target / "web/keep.mjs", b"independent edit after baseline capture\r\n")
    before = tree(target)
    untouched = target / "crates/moly-game/src/lib.rs"
    before_stat = untouched.stat()
    result = installer.apply_bundle(target, bundle)
    assert result["would_change"] == delivery["report"]["changed_files"]
    assert tree(target) == before
    result = installer.apply_bundle(target, bundle, apply=True)
    expected = tree(delivery["updated"]) | {"unrelated-local.txt": before["unrelated-local.txt"], "web/keep.mjs": before["web/keep.mjs"]}
    assert tree(target) == expected
    assert result["changed"] == delivery["report"]["changed_files"]
    assert untouched.stat().st_mtime_ns == before_stat.st_mtime_ns
    repeated = installer.apply_bundle(target, bundle, apply=True)
    assert repeated["changed"] == []
    assert repeated["already_current"] == result["changed"]
    installer.apply_bundle(target, bundle, apply=True, reverse=True)
    assert tree(target) == before
    assert installer.apply_bundle(target, bundle, apply=True, reverse=True)["changed"] == []


def test_wrong_baseline_fails_before_any_write(delivery):
    # Sort order puts the conflict after files that otherwise could be changed.
    write(delivery["target"] / "web/obsolete.mjs", b"concurrent user edit")
    before = tree(delivery["target"])
    with pytest.raises(ValueError, match="preimage conflict; no files changed"):
        installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert tree(delivery["target"]) == before


def test_partial_install_resumes_without_touching_already_applied_files(delivery):
    first = json.loads(delivery["bundle"].read_bytes())["files"][0]
    path = delivery["target"] / first["path"]
    write(path, base64.b64decode(first["after"]))
    timestamp = path.stat().st_mtime_ns
    result = installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert result["already_current"] == [first["path"]]
    assert path.stat().st_mtime_ns == timestamp
    assert tree(delivery["target"]) == tree(delivery["updated"])


def test_builder_is_byte_deterministic_after_moved_snapshots_and_reversed_enumeration(delivery, monkeypatch):
    before = delivery["bundle"].read_bytes(), delivery["patch"].read_bytes()
    moved = delivery["tmp"] / "other machine"
    moved.mkdir()
    baseline, updated = moved / "baseline", moved / "updated"
    shutil.copytree(delivery["baseline"], baseline)
    shutil.copytree(delivery["updated"], updated)
    original_walk = os.walk

    def reverse_walk(*args, **kwargs):
        for path, dirs, files in original_walk(*args, **kwargs):
            dirs.reverse()
            files.reverse()
            yield path, dirs, files

    monkeypatch.setattr(builder.os, "walk", reverse_walk)
    report = regenerate(delivery, baseline=baseline, updated=updated)
    assert (delivery["bundle"].read_bytes(), delivery["patch"].read_bytes()) == before
    assert report == delivery["report"]


@pytest.mark.parametrize("change", ["modified", "deleted", "extra"])
def test_builder_requires_exact_captured_baseline(delivery, change):
    path = delivery["baseline"] / "web/keep.mjs"
    if change == "deleted":
        path.unlink()
    else:
        write(path if change == "modified" else delivery["baseline"] / "web/extra.mjs", b"not captured")
    before = delivery["bundle"].read_bytes(), delivery["patch"].read_bytes()
    with pytest.raises(ValueError, match="baseline hash receipt"):
        regenerate(delivery)
    assert (delivery["bundle"].read_bytes(), delivery["patch"].read_bytes()) == before


@pytest.mark.parametrize("relative", ["Cargo.toml", "crates/moly-game/src/lib.rs", "crates/moly-app/src/asset_source.rs.bak"])
def test_builder_refuses_unrelated_consumer_changes(delivery, relative):
    write(delivery["updated"] / relative, b"not in integration scope")
    with pytest.raises(ValueError, match="outside the asset boundary"):
        regenerate(delivery)


@pytest.mark.parametrize("destination", ["baseline", "updated", "receipt", "same"])
def test_builder_never_writes_input_snapshots_or_receipt(delivery, destination):
    output = delivery[destination] / "bundle.json" if destination in {"baseline", "updated"} else delivery["receipt"]
    if destination == "same":
        output = delivery["patch"]
    with pytest.raises(ValueError, match="(input snapshot|baseline hash receipt|outputs must differ)"):
        regenerate(delivery, output=output)


@pytest.mark.parametrize("relative", [
    "../escape", "/web/absolute.mjs", "web/../escape", "web//file", "web/./file",
    "web\\file", "web/file:stream", "web/file.", "web/file ", "web/NUL.mjs", "web/con", "web/COM¹",
    "web/file\nname", "web/file?", "web/.git/config", "web/node_modules/generated.js",
    "crates/moly-game/src/lib.rs", "crates/moly-app/src/asset_source.rs.bak",
])
def test_installer_rejects_noncanonical_or_out_of_scope_paths_before_writing(delivery, relative):
    mutate_bundle(delivery, lambda bundle: bundle["files"][-1].update(path=relative))
    before = tree(delivery["target"])
    with pytest.raises(ValueError, match="(path|boundary)"):
        installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert tree(delivery["target"]) == before


@pytest.mark.parametrize("case_alias", [False, True])
def test_installer_rejects_duplicate_file_aliases(delivery, case_alias):
    # Use a web path so capitalization remains within the explicit boundary.
    def web_change(bundle):
        row = copy.deepcopy(next(row for row in bundle["files"] if row["path"] == "web/empty.mjs"))
        if case_alias:
            row["path"] = "web/EMPTY.mjs"
        bundle["files"].append(row)

    mutate_bundle(delivery, web_change)
    before = tree(delivery["target"])
    with pytest.raises(ValueError, match="duplicate consumer upgrade path"):
        installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert tree(delivery["target"]) == before


def test_installer_rejects_parent_child_file_conflict(delivery):
    def change(bundle):
        bundle["files"].append({"path": "web/nested", "before": None, "before_sha256": None,
                                "after": base64.b64encode(b"file").decode(), "after_sha256": installer.sha(b"file")})

    mutate_bundle(delivery, change)
    with pytest.raises(ValueError, match="overlapping"):
        installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert tree(delivery["target"]) == tree(delivery["baseline"])


@pytest.mark.parametrize("damage", ["hash", "base64", "missing-data", "missing-hash", "null-hash", "no-op", "wrong-row-type"])
def test_installer_validates_entire_bundle_before_writing(delivery, damage):
    def change(bundle):
        row = bundle["files"][-1]
        if damage == "hash":
            row["before_sha256"] = "0" * 64
        elif damage == "base64":
            row["before"] = "@@@"
        elif damage == "missing-data":
            del row["after"]
        elif damage == "missing-hash":
            del row["after_sha256"]
        elif damage == "null-hash":
            row["after_sha256"] = "0" * 64
        elif damage == "no-op":
            row["after"], row["after_sha256"] = row["before"], row["before_sha256"]
        else:
            bundle["files"][-1] = "not a row"

    mutate_bundle(delivery, change)
    with pytest.raises(ValueError):
        installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert tree(delivery["target"]) == tree(delivery["baseline"])


def test_duplicate_json_fields_are_rejected(delivery):
    raw = delivery["bundle"].read_text(encoding="utf-8")
    delivery["bundle"].write_text('{"schema":"wrong",' + raw[1:], encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON field"):
        installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)


def make_directory_link(link, destination):
    try:
        link.symlink_to(destination, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # NTFS junction creation does not need Windows Developer Mode.
        command = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(destination).replace("'", "''") + "' | Out-Null"
        subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, capture_output=True)


def test_installer_rejects_directory_symlink_or_windows_junction(delivery):
    outside = delivery["tmp"] / "outside"
    outside.mkdir()
    link = delivery["target"] / "web/nested"
    make_directory_link(link, outside)
    try:
        with pytest.raises(ValueError, match="traverses a link"):
            installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
        assert not list(outside.iterdir())
        assert (delivery["target"] / "crates/moly-assets/src/packs.rs").read_bytes() == b"old reader\r\n"
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()


def test_builder_refuses_snapshot_symlink_or_windows_junction(delivery):
    link = delivery["updated"] / "web/link"
    make_directory_link(link, delivery["baseline"] / "web")
    try:
        with pytest.raises(ValueError, match="snapshot contains a link"):
            regenerate(delivery)
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()


def test_write_failure_rolls_back_changes_and_new_directories(delivery, monkeypatch):
    original = installer.atomic_replace

    def fail_last(path, data, mode=0o644):
        if path.name == "obsolete.mjs" and data is None:
            raise OSError("injected write failure")
        return original(path, data, mode)

    monkeypatch.setattr(installer, "atomic_replace", fail_last)
    with pytest.raises(OSError, match="injected write failure"):
        installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert tree(delivery["target"]) == tree(delivery["baseline"])
    assert not (delivery["target"] / "web/nested").exists()


def test_concurrent_edit_after_preflight_is_preserved_and_prior_writes_rolled_back(delivery, monkeypatch):
    original = installer.atomic_replace
    other = delivery["target"] / "web/obsolete.mjs"
    called = False

    def concurrent_write(path, data, mode=0o644):
        nonlocal called
        result = original(path, data, mode)
        if not called:
            called = True
            other.write_bytes(b"concurrent user edit")
        return result

    monkeypatch.setattr(installer, "atomic_replace", concurrent_write)
    with pytest.raises(ValueError, match="file changed after preflight"):
        installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert tree(delivery["target"]) == tree(delivery["baseline"]) | {"web/obsolete.mjs": b"concurrent user edit"}


def test_patch_review_handles_missing_newline_and_binary_payload(delivery):
    patch = delivery["patch"].read_text(encoding="utf-8")
    assert "\\ No newline at end of file\n+new source without newline\n" in patch
    write(delivery["updated"] / "web/binary.dat", b"\x00\xff\x80")
    regenerate(delivery)
    assert "Binary files /dev/null and b/web/binary.dat differ" in delivery["patch"].read_text(encoding="utf-8")
    installer.apply_bundle(delivery["target"], delivery["bundle"], apply=True)
    assert (delivery["target"] / "web/binary.dat").read_bytes() == b"\x00\xff\x80"


def test_cli_defaults_to_read_only_and_conflicts_exit_nonzero(delivery):
    command = [sys.executable, str(Path(installer.__file__)), "--target", str(delivery["target"]), "--bundle", str(delivery["bundle"])]
    check = subprocess.run(command, check=True, capture_output=True, text=True)
    assert json.loads(check.stdout)["applied"] is False
    assert tree(delivery["target"]) == tree(delivery["baseline"])
    write(delivery["target"] / "web/obsolete.mjs", b"wrong baseline")
    failed = subprocess.run(command + ["--apply"], capture_output=True, text=True)
    assert failed.returncode == 1
    assert "preimage conflict; no files changed" in failed.stderr
