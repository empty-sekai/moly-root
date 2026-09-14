"""Blob repair, canonical paths and failure-safe grouped publication."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core import atomic
from pack.build import build
from pack.groups import build_groups
from pack.gc import catalog_garbage
from pack.verify import verify, verify_catalog, verify_catalog_data

build_module = importlib.import_module("pack.build")


def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.bin").write_bytes(b"GOOD")
    return root


@pytest.mark.parametrize("damage,remove_manifest", [(b"BAD!", False), (b"X", False), (b"BAD!", True)])
def test_rebuilding_repairs_corrupt_existing_blobs(tmp_path, damage, remove_manifest):
    src, out = source(tmp_path), tmp_path / "output"
    build(src, out, "v1")
    manifest = out / "manifest.json"
    blob = out / "blobs" / json.loads(manifest.read_bytes())["entries"][0]["blob"]
    blob.write_bytes(damage)
    if remove_manifest:
        manifest.unlink()
    build(src, out, "v2")
    assert blob.read_bytes() == b"GOOD"
    assert verify(manifest, out / "blobs")[0] == []


def test_unchanged_rebuild_reuses_only_verified_blobs(tmp_path, monkeypatch):
    src, out = source(tmp_path), tmp_path / "output"
    build(src, out, "v1")

    def unexpected(*_):
        raise AssertionError("a verified unchanged blob should not need compression")

    monkeypatch.setattr(build_module.codecs, "encode_blob", unexpected)
    build(src, out, "v2")
    assert verify(out / "manifest.json", out / "blobs")[0] == []


def test_independent_final_validation_detects_a_bad_disk_write(tmp_path, monkeypatch):
    src, out = source(tmp_path), tmp_path / "output"
    write = build_module.write_bytes

    def corrupt(path, data, **kwargs):
        return write(path, b"BAD!" if "blobs" in Path(path).parts else data, **kwargs)

    monkeypatch.setattr(build_module, "write_bytes", corrupt)
    with pytest.raises(RuntimeError, match="invalid manifest"):
        build(src, out, "v1")
    assert not (out / "manifest.json").exists()


def test_parent_segments_and_aliases_have_one_canonical_logical_path(tmp_path):
    src, out = source(tmp_path), tmp_path / "output"
    (src / "models").mkdir()
    build(src, out, "v1", paths=["models/../a.bin", "a.bin"])
    document = json.loads((out / "manifest.json").read_bytes())
    assert [entry["path"] for entry in document["entries"]] == ["a.bin"]
    assert verify(out / "manifest.json", out / "blobs")[0] == []


def test_escaping_input_is_rejected_before_output_creation(tmp_path):
    src, out = source(tmp_path), tmp_path / "output"
    (tmp_path / "outside.bin").write_bytes(b"outside")
    with pytest.raises(ValueError, match="outside"):
        build(src, out, "v1", paths=["../outside.bin"])
    assert not out.exists()


@pytest.mark.parametrize("position", ["same", "child", "parent"])
def test_overlapping_source_and_output_are_rejected_before_writing(tmp_path, position):
    src = source(tmp_path)
    out = {"same": src, "child": src / "output", "parent": tmp_path}[position]
    before = set(tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="overlap"):
        build(src, out, "v1")
    assert set(tmp_path.rglob("*")) == before


def test_overlay_cannot_contain_the_output(tmp_path):
    src = source(tmp_path)
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    with pytest.raises(ValueError, match="overlap"):
        build(src, overlay / "output", "v1", xf_overlay=overlay, xf_name="meshopt")
    assert not (overlay / "output").exists()


def test_link_alias_cannot_hide_an_output_overlap(tmp_path):
    src = source(tmp_path)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(src, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(src), str(alias))
        else:
            pytest.skip("directory symlinks are unavailable on this host")
    with pytest.raises(ValueError, match="overlap"):
        build(src, alias / "output", "v1")
    assert not (src / "output").exists()


def grouped_source(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "manifest.json").write_text('{"units":[]}', encoding="utf-8")
    (src / "phenomena" / "001_sunny").mkdir(parents=True)
    (src / "ui").mkdir()
    update_grouped_source(src, "v1")
    return src


def update_grouped_source(src, version):
    (src / "characters.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (src / "ui" / "layout.json").write_text(json.dumps({"ui": version}), encoding="utf-8")
    (src / "phenomena" / "001_sunny" / "config.json").write_text(json.dumps({"weather": version}), encoding="utf-8")


def test_grouped_releases_keep_immutable_manifests_and_all_retained_blobs(tmp_path):
    src, out = grouped_source(tmp_path), tmp_path / "output"
    old = build_groups(src, out, "v1")
    original = {package["manifest"]: (out / package["manifest"]).read_bytes() for package in old["packages"]}
    update_grouped_source(src, "v2")
    new = build_groups(src, out, "v2")
    assert {p["manifest"] for p in old["packages"]}.isdisjoint(p["manifest"] for p in new["packages"])
    for path, data in original.items():
        assert (out / path).read_bytes() == data
    errors, info = verify_catalog(out / "asset-packs.json")
    assert not errors
    assert info["retained_catalogs"] == 2
    assert info["retained_blobs"]
    assert catalog_garbage(out)["delete"] == []
    orphan = out / "blobs" / "unused.bin"
    orphan.write_bytes(b"not in any release")
    assert catalog_garbage(out)["delete"] == ["unused.bin"]


@pytest.mark.parametrize("point", ["read", "encode", "blob", "package", "archive", "publish", "after_publish"])
def test_failed_publication_exposes_one_complete_generation(tmp_path, monkeypatch, point):
    src, out = grouped_source(tmp_path), tmp_path / "output"
    old = build_groups(src, out, "v1")
    old_packages = {p["manifest"]: (out / p["manifest"]).read_bytes() for p in old["packages"]}
    update_grouped_source(src, "v2")
    real_read = Path.read_bytes
    real_encode = build_module.codecs.encode_blob
    real_replace = atomic.os.replace

    def read(path):
        if point == "read" and path == src / "phenomena" / "001_sunny" / "config.json":
            raise OSError("injected source read failure")
        return real_read(path)

    def encode(data, codec, encoding):
        if point == "encode" and b'"weather": "v2"' in data:
            raise OSError("injected compression failure")
        return real_encode(data, codec, encoding)

    def replace(source, destination):
        path = Path(destination)
        fail = ((point == "blob" and "blobs" in path.parts)
                or (point == "package" and path.parent.name == "packages")
                or (point == "archive" and path.parent.name == "catalogs")
                or (point in {"publish", "after_publish"} and path == out / "asset-packs.json"))
        if fail:
            if point == "after_publish":
                real_replace(source, destination)
            raise OSError("injected publication failure")
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", read)
        patch.setattr(build_module.codecs, "encode_blob", encode)
        patch.setattr(atomic.os, "replace", replace)
        with pytest.raises(OSError, match="injected"):
            build_groups(src, out, "v2")
    live = json.loads((out / "asset-packs.json").read_bytes())
    assert live["version"] == ("v2" if point == "after_publish" else "v1")
    for package in live["packages"]:
        assert json.loads((out / package["manifest"]).read_bytes())["version"] == live["version"]
    for path, original in old_packages.items():
        assert (out / path).read_bytes() == original
    assert verify_catalog(out / "asset-packs.json")[0] == []


@pytest.mark.parametrize("damage", ["duplicate_id", "missing_dependency", "cycle", "ownership", "paths", "version"])
def test_catalog_verifier_rejects_inconsistent_release_metadata(tmp_path, damage):
    src, out = grouped_source(tmp_path), tmp_path / "output"
    catalog = copy.deepcopy(build_groups(src, out, "v1"))
    first, second = catalog["packages"][:2]
    if damage == "duplicate_id":
        second["id"] = first["id"]
    elif damage == "missing_dependency":
        first["dependencies"] = ["absent/package"]
    elif damage == "cycle":
        first["dependencies"] = [second["id"]]
        second["dependencies"] = [first["id"]]
    elif damage == "ownership":
        second["paths"].append(first["paths"][0])
    elif damage == "paths":
        first["paths"] = []
    else:
        catalog["version"] = "wrong"
    assert verify_catalog_data(catalog, out)[0]


def test_unchanged_external_build_has_stable_asset_membership(tmp_path):
    src, out = source(tmp_path), tmp_path / "output"
    build(src, out, "v1")
    first = json.loads((out / "manifest.json").read_bytes())["entries"]
    build(src, out, "v2")
    assert json.loads((out / "manifest.json").read_bytes())["entries"] == first


def test_grouped_glb_parent_uris_use_the_same_canonical_path_contract(tmp_path):
    src = grouped_source(tmp_path)
    (src / "models").mkdir()
    (src / "image.png").write_bytes(b"synthetic image")
    document = json.dumps({"asset": {"version": "2.0"}, "images": [{"uri": "../image.png"}]}).encode()
    document += b" " * (-len(document) % 4)
    glb = b"glTF" + (2).to_bytes(4, "little") + (20 + len(document)).to_bytes(4, "little")
    glb += len(document).to_bytes(4, "little") + b"JSON" + document
    (src / "models" / "sd_001.glb").write_bytes(glb)
    (src / "manifest.json").write_text(json.dumps({"units": [{"glb": "models/sd_001.glb"}]}), encoding="utf-8")
    out = tmp_path / "output"
    catalog = build_groups(src, out, "v1")
    paths = [path for package in catalog["packages"] for path in package["paths"]]
    assert "image.png" in paths
    assert all(".." not in path.split("/") for path in paths)
    assert verify_catalog(out / "asset-packs.json")[0] == []


@pytest.mark.parametrize("directory", ["release", "catalogs"])
@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("explicit_root", [False, True])
def test_active_and_historical_catalog_roots_do_not_depend_on_output_name(tmp_path, directory, historical, explicit_root):
    src, out = grouped_source(tmp_path), tmp_path / directory
    build_groups(src, out, "v1")
    path = next((out / "catalogs").glob("*.json")) if historical else out / "asset-packs.json"
    errors, info = verify_catalog(path, root=out if explicit_root else None)
    assert errors == []
    assert info["packages"] == 3
    assert info["orphan_blobs"] == []


@pytest.mark.parametrize("directory", ["release", "catalogs"])
def test_catalog_cli_and_gc_respect_the_output_root(tmp_path, directory, capsys):
    from pack.verify import main as verify_main
    from pack.gc import main as gc_main
    src, out = grouped_source(tmp_path), tmp_path / directory
    build_groups(src, out, "v1")
    assert verify_main(["--out", str(out)]) == 0
    assert gc_main(["--out", str(out), "--json"]) == 0
    assert catalog_garbage(out)["delete"] == []
    assert "OK 3 packages" in capsys.readouterr().out


def test_explicit_root_takes_priority_even_for_a_content_named_catalog(tmp_path):
    src, out = grouped_source(tmp_path), tmp_path / "catalogs"
    build_groups(src, out, "v1")
    data = (out / "asset-packs.json").read_bytes()
    path = out / (hashlib.sha256(data).hexdigest() + ".json")
    path.write_bytes(data)
    assert verify_catalog(path, root=out)[0] == []


@pytest.mark.parametrize("explicit_root", [False, True])
def test_history_root_inference_requires_a_valid_content_address(tmp_path, explicit_root):
    src, out = grouped_source(tmp_path), tmp_path / "release"
    build_groups(src, out, "v1")
    path = out / "catalogs" / ("0" * 64 + ".json")
    path.write_bytes((out / "asset-packs.json").read_bytes())
    with pytest.raises(ValueError, match="content address"):
        verify_catalog(path, root=out if explicit_root else None)
