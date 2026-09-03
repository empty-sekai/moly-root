"""The shared avatar-parts index must always describe every package on disk.

Two properties, only meaningful as a pair:

* A run that extracts a single package (the usual repair run, and the exact
  shape of the real incident where one ``--bundle`` CLI invocation overwrote
  a 634-package index down to 1) must leave the index covering every package
  already on disk.  ``extract_avatar_parts`` satisfies this by rebuilding the
  index from every ``package.json`` it finds under the output directory, so
  the index tracks disk and not "what this run attempted".
* When a package really is gone from disk, it must drop out of the index.
  Without this half, an index that is only ever grown (append, never remove)
  would pass the first property while drifting away from disk forever.

Neither check needs a real bundle: the unit under test here is what happens
*after* per-package extraction, so the per-package step is stubbed to write
records in exactly the shape ``extract_package`` writes, and the bundles
passed to ``extract_avatar_parts`` are paths that are never opened.  That the
stub is called only for the bundles actually passed is itself asserted -- the
index rebuild must not re-parse anything.
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chara.avatar_parts as avatar_parts_module
from chara.avatar_parts import extract_avatar_parts


def _bundle_name(category, package):
    # The flattened logical name split_flat parses; the file itself never
    # needs to exist because extract_package is stubbed below.
    return f"virtual_live__avatar__{category}__{package}"


def _write_package_record(out_dir, category, package, textures=2, materials=1,
                          meshes=0, renderers=0, unsupported=0, glb=None):
    """What extract_package leaves behind: one directory, one package.json."""
    record = {"package": package, "category": category,
              "sourcePackage": _bundle_name(category, package),
              "containers": [], "dependencies": [],
              "textures": [{"name": f"t{i}"} for i in range(textures)],
              "materials": [{"name": f"m{i}"} for i in range(materials)],
              "meshes": [{"name": f"mesh{i}"} for i in range(meshes)],
              "renderers": [{"node": f"n{i}"} for i in range(renderers)],
              "unsupported": [{"type": "Texture2D", "name": "x", "reason": "r"}
                              for _ in range(unsupported)],
              "glb": glb}
    directory = os.path.join(out_dir, category, package)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as f:
        json.dump(record, f)
    return record


def _packages_on_disk(out_dir):
    """Every <category>/<package>/ pair that carries a package.json."""
    found = set()
    for category in os.listdir(out_dir):
        category_dir = os.path.join(out_dir, category)
        if not os.path.isdir(category_dir):
            continue
        for package in os.listdir(category_dir):
            if os.path.isfile(os.path.join(category_dir, package, "package.json")):
                found.add(f"{category}/{package}")
    return found


def _covered_by_index(doc):
    return {f"{category}/{package}"
            for category, packages in doc["categories"].items()
            for package in packages}


def _read_index(out_dir):
    with open(os.path.join(out_dir, avatar_parts_module.INDEX_NAME),
              encoding="utf-8") as f:
        return json.load(f)


def _stub_extract_package(monkeypatch, texture_count=2):
    """Replace extract_package with the record-writer above; return its calls."""
    calls = []

    def fake_extract_package(bundle, out_root, category, package):
        calls.append(f"{category}/{package}")
        _write_package_record(out_root, category, package,
                              textures=texture_count)
        return {"package": package, "category": category}

    monkeypatch.setattr(avatar_parts_module, "extract_package",
                        fake_extract_package)
    return calls


def _seed_full_run(tmp_path, monkeypatch, packages):
    """A first run over every package, as the full pipeline would do."""
    _stub_extract_package(monkeypatch)
    out_dir = str(tmp_path / "avatar-parts")
    extract_avatar_parts([_bundle_name(*pair) for pair in packages], out_dir)
    return out_dir


def test_index_shape_is_unchanged(tmp_path, monkeypatch):
    # Downstream reads {"version":1,"categories":...,"summary":...} with the
    # five summary counters; the fix must not touch that shape.
    out_dir = _seed_full_run(
        tmp_path, monkeypatch,
        [("skin", "costume_a"), ("penlight", "penlight_0011")])
    doc = _read_index(out_dir)
    assert set(doc) == {"version", "categories", "summary"}
    assert doc["version"] == 1
    assert set(doc["categories"]) == {"skin", "penlight"}
    assert set(doc["summary"]) == {"packages", "textures", "meshes",
                                   "materials", "unsupported"}


def test_single_package_run_keeps_the_index_over_every_package_on_disk(
        tmp_path, monkeypatch):
    packages = [("skin", "costume_a"), ("skin", "costume_b"),
                ("decoration", "deco_a"),
                ("penlight", "penlight_0011"), ("penlight", "penlight_002")]
    out_dir = _seed_full_run(tmp_path, monkeypatch, packages)
    expected = _packages_on_disk(out_dir)          # N from disk, not hardcoded
    assert len(expected) == len(packages)
    assert _read_index(out_dir)["summary"]["packages"] == len(expected)

    # The repair run: one bundle, re-extracted with different content (the
    # incident's shape -- penlight_0011's texture fix changed what got written).
    second_calls = _stub_extract_package(monkeypatch, texture_count=7)
    result = extract_avatar_parts(
        [_bundle_name("penlight", "penlight_0011")], out_dir)

    assert second_calls == ["penlight/penlight_0011"], (
        "the index rebuild must not re-extract packages other than the ones "
        "the run was handed")
    doc = _read_index(out_dir)
    on_disk = _packages_on_disk(out_dir)
    assert doc["summary"]["packages"] == len(on_disk) == len(expected)
    assert _covered_by_index(doc) == on_disk, (
        "the index must cover exactly the disk set")
    # Rebuilt from the fresh package.json, not carried over from the old index.
    entry = doc["categories"]["penlight"]["penlight_0011"]
    assert entry["textures"] == 7
    assert result["packages"] == len(on_disk)
    assert result["skippedPackages"] == []


def test_a_package_removed_from_disk_drops_out_of_the_index(
        tmp_path, monkeypatch):
    packages = [("skin", "costume_a"), ("skin", "costume_b"),
                ("penlight", "penlight_0011")]
    out_dir = _seed_full_run(tmp_path, monkeypatch, packages)
    assert _read_index(out_dir)["summary"]["packages"] == len(packages)

    # The package really is gone -- directory and all.
    shutil.rmtree(os.path.join(out_dir, "skin", "costume_b"))
    _stub_extract_package(monkeypatch)
    extract_avatar_parts([_bundle_name("penlight", "penlight_0011")], out_dir)

    doc = _read_index(out_dir)
    on_disk = _packages_on_disk(out_dir)
    assert doc["summary"]["packages"] == len(on_disk) == len(packages) - 1
    assert _covered_by_index(doc) == on_disk
    assert "skin/costume_b" not in _covered_by_index(doc)
    assert set(doc["categories"]["skin"]) == {"costume_a"}, (
        "the removed package must leave its category's index section too")


def test_directory_without_package_json_is_skipped_and_reported(
        tmp_path, monkeypatch):
    packages = [("skin", "costume_a"), ("penlight", "penlight_0011")]
    out_dir = _seed_full_run(tmp_path, monkeypatch, packages)

    # An interrupted run's leftover: directory exists, index data does not.
    os.makedirs(os.path.join(out_dir, "skin", "costume_broken", "tex"))
    _stub_extract_package(monkeypatch)
    result = extract_avatar_parts(
        [_bundle_name("penlight", "penlight_0011")], out_dir)

    doc = _read_index(out_dir)
    on_disk = _packages_on_disk(out_dir)           # the readable set
    assert doc["summary"]["packages"] == len(on_disk) == len(packages)
    assert "skin/costume_broken" not in _covered_by_index(doc)
    assert result["skippedPackages"] == ["skin/costume_broken"], (
        "a dropped package must be visible in the return value, not silently "
        "treated as absent")


def test_corrupt_package_json_is_skipped_and_reported(tmp_path, monkeypatch):
    packages = [("skin", "costume_a"), ("penlight", "penlight_0011")]
    out_dir = _seed_full_run(tmp_path, monkeypatch, packages)

    # A truncated write: the file exists but carries no readable record.
    with open(os.path.join(out_dir, "skin", "costume_a", "package.json"),
              "w", encoding="utf-8") as f:
        f.write('{"package": "costume_a", "categ')
    _stub_extract_package(monkeypatch)
    result = extract_avatar_parts(
        [_bundle_name("penlight", "penlight_0011")], out_dir)

    doc = _read_index(out_dir)
    # costume_a was the only readable skin package, so the whole category
    # section disappears rather than surviving as an empty shell.
    assert doc["categories"].get("skin") is None
    assert doc["summary"]["packages"] == 1
    assert result["skippedPackages"] == ["skin/costume_a"]


def test_rebuild_only_run_reports_disk_without_extracting(tmp_path, monkeypatch):
    # Zero bundles handed in: the call must still leave the index describing
    # every package on disk (the old code would have clobbered it down to 0).
    packages = [("skin", "costume_a"), ("penlight", "penlight_0011")]
    out_dir = _seed_full_run(tmp_path, monkeypatch, packages)
    calls = _stub_extract_package(monkeypatch)

    result = extract_avatar_parts([], out_dir)

    assert calls == []
    assert result["packages"] == _read_index(out_dir)["summary"]["packages"] == 2
