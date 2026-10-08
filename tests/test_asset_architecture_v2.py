"""Executable invariants for immutable package content and multi-region releases."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from core.atomic import write_bytes
from pack.build import build
from pack.codecs import gzip_compress
from pack.gc import catalog_garbage, collect_garbage, retire_catalog
from pack import groups
from pack.groups import build_groups, runtime_paths
from pack.identity import canonical_bytes, content_id
from pack.release import activate
from pack.verify import strict_json, verify_catalog, verify_catalog_data, verify_manifest


def glb():
    raw = b'{"asset":{"version":"2.0"},"scenes":[{"nodes":[]}],"scene":0}'
    raw += b" " * (-len(raw) % 4)
    return struct.pack("<III", 0x46546C67, 2, len(raw) + 20) + struct.pack("<I4s", len(raw), b"JSON") + raw


@pytest.fixture
def assets(tmp_path):
    root = tmp_path / "input"
    files = {
        "manifest.json": b'{"units":[{"glb":"sd_104.glb","rig":"sd_104.rig.json"}]}',
        "characters.json": b'{"characters":[]}',
        "sd_104.glb": glb(), "sd_104.rig.json": b'{"textures":[]}',
        "fixture-models/mysekai__test/model.glb": glb(),
        "fixture-thumbnails/mysekai__test/thumb.png": b"thumbnail fixture bytes",
        "custom-fixture-models/mysekai__test/model.glb": glb(),
        "site/scenes/site01/site01.glb": glb(),
        "site/scenes/site01/site01.json": b'{"nodes":[]}',
        "ui/action-icon/Talk.png": b"representative UI bytes",
        "phenomena/audio/voice.ogg": b"representative audio bytes",
        "phenomena/001_sunny/config.json": b'{"sun":1}',
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def packages(catalog):
    return {row["id"]: (row["manifest"], row.get("content_id")) for row in catalog["packages"]}


def immutable_bytes(output):
    return {p.relative_to(output).as_posix(): p.read_bytes()
            for directory in ("packages", "blobs") for p in (output / directory).rglob("*") if p.is_file()}


def release_id(catalog):
    return hashlib.sha256(canonical_bytes(catalog)).hexdigest()


def test_same_content_shared_by_regions_versions_and_provenance(assets, tmp_path):
    out = tmp_path / "store"
    cn = build_groups(assets, out, "6.0.0", region="cn", provenance={"master": {"commit": "a" * 40}, "time": "yesterday"})
    immutable = immutable_bytes(out)
    jp = build_groups(assets, out, "6.8.0", region="jp", provenance={"master": {"commit": "b" * 40}, "time": "tomorrow"})
    tw = build_groups(assets, out, "9", region="tw", provenance={"machine": "other host"})
    assert packages(cn) == packages(jp) == packages(tw)
    assert immutable_bytes(out) == immutable
    assert len({release_id(cn), release_id(jp), release_id(tw)}) == 3
    for package in cn["packages"]:
        doc = strict_json((out / package["manifest"]).read_bytes())
        assert not {"version", "generated_utc", "region", "provenance", "blob_prefix"} & doc.keys()
    errors, checked = verify_catalog(out / "asset-packs.json")
    assert not errors
    assert len(checked["channel_roots"]) == 3
    assert not checked["orphan_blobs"] and not checked["orphan_manifests"]


@pytest.mark.parametrize("seed", [1, 21, 99])
def test_fresh_warm_moved_shuffled_builds_are_byte_identical(assets, tmp_path, seed):
    first, second = tmp_path / "first", tmp_path / "second"
    original = build_groups(assets, first, "build", region="cn", provenance={"a": 1, "b": 2})
    moved = tmp_path / "different-machine-path"
    shutil.copytree(assets, moved)
    names = list(runtime_paths(moved)); random.Random(seed).shuffle(names)
    rebuilt = build_groups(moved, second, "build", region="cn", provenance={"b": 2, "a": 1}, paths=names)
    assert original == rebuilt
    assert (first / "asset-packs.json").read_bytes() == (second / "asset-packs.json").read_bytes()
    assert immutable_bytes(first) == immutable_bytes(second)
    build_groups(moved, second, "build", region="cn", provenance={"b": 2, "a": 1}, paths=reversed(names))
    assert immutable_bytes(first) == immutable_bytes(second)


def test_legacy_source_metadata_requires_a_separate_portable_copy(assets, tmp_path, capsys):
    private = {
        "version": 1, "kind": "moly-source", "coordinateContract": "moly-rh-y-up-reflect-x-v1",
        "source": {"region": "cn", "appVersion": "1.2.3", "assetHash": "synthetic",
                   "localDebug": {"root": "C:/fixture-cache/bundles"}},
        "workspace": "local-job-name", "extraction": "/private-cache/extracted",
        "master": {"commit": "a" * 40, "repository": "example/master-data",
                   "checkout": "\\\\fixture-host\\private-share\\master"},
        "sharedUi": {"files": [{"path": "ui/action-icon/Talk.png", "sha256": "b" * 64}]},
    }
    source_file = assets / "source.json"
    original = json.dumps(private).encode()
    source_file.write_bytes(original)
    with pytest.raises(ValueError, match="sanitize-source"):
        runtime_paths(assets)
    out = tmp_path / "store"
    with pytest.raises(ValueError, match="sanitize-source"):
        build_groups(assets, out, "private", region="cn")
    assert not (out / "asset-packs.json").exists()
    clean = tmp_path / "clean-root"
    shutil.copytree(assets, clean)
    assert groups.main(["--sanitize-source", str(source_file), "--source-copy", str(clean / "source.json")]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["sourceSha256"] == hashlib.sha256(original).hexdigest()
    assert receipt["publicSha256"] == hashlib.sha256((clean / "source.json").read_bytes()).hexdigest()
    assert source_file.read_bytes() == original
    assert "source.json" in runtime_paths(clean)
    with pytest.raises(ValueError, match="differ from the original"):
        groups.write_public_source_copy(source_file, source_file)
    catalog = build_groups(clean, out, "public", region="cn")
    public = catalog["provenance"]["runtime_source"]
    assert public["source"] == {"region": "cn", "appVersion": "1.2.3", "assetHash": "synthetic"}
    assert public["master"] == {"commit": "a" * 40, "repository": "example/master-data"}
    assert public["sharedUi"]["files"][0]["path"] == "ui/action-icon/Talk.png"
    assert "source.json" in {name for row in catalog["packages"] for name in row["paths"]}
    assert not verify_catalog(out / "asset-packs.json")[0]
    for marker in (b"private-cache", b"fixture-cache", b"private-share", b"local-job-name"):
        assert marker not in canonical_bytes(catalog)
        assert marker not in (clean / "source.json").read_bytes()
    assert source_file.read_bytes() == original


def test_runtime_membership_allows_missing_source_and_rejects_private_identity(assets):
    assert "source.json" not in runtime_paths(assets)
    source_file = assets / "source.json"
    original = canonical_bytes({"source": {"appVersion": "C:/fixture-cache/private"}})
    source_file.write_bytes(original)
    with pytest.raises(ValueError, match="sanitize-source"):
        runtime_paths(assets)
    assert source_file.read_bytes() == original


@pytest.mark.parametrize("codec,encoding", [
    ("identity", "identity"), ("gzip", "identity"),
    ("brotli", "identity"), ("identity", "br"),
])
def test_legacy_migration_checks_stored_source_without_rewriting_blobs(tmp_path, monkeypatch, codec, encoding):
    from pack.migrate import migrate_catalog
    root, store = tmp_path / "legacy-root", tmp_path / "legacy-store"
    root.mkdir()
    source_file = root / "source.json"
    source_file.write_bytes(canonical_bytes({
        "source": {"region": "cn", "appVersion": "1.2.3"},
        "sharedUi": {"files": [{"path": "ui/action-icon/Talk.png", "sha256": "b" * 64}]},
        "extraction": "/private-cache/extracted",
    }))
    original_source = source_file.read_bytes()
    categories = tmp_path / "legacy-codecs.toml"
    categories.write_text(f'[default]\ncodec = "{codec}"\nhttp_encoding = "{encoding}"\n')
    monkeypatch.setattr(groups, "CATEGORIES", categories)
    # Explicit no-region membership recreates an existing v1 store, bypassing
    # today's runtime-root checks just as a pre-existing published blob does.
    build_groups(root, store, "old", paths=["source.json"])
    before = {p.relative_to(store): p.read_bytes() for p in store.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match=r"sanitize-source.*--src CLEAN.*immutable blobs"):
        migrate_catalog(store, region="cn")
    assert {p.relative_to(store): p.read_bytes() for p in store.rglob("*") if p.is_file()} == before
    assert source_file.read_bytes() == original_source

    clean, clean_store = tmp_path / "clean-root", tmp_path / "clean-store"
    groups.write_public_source_copy(source_file, clean / "source.json")
    build_groups(clean, clean_store, "old-clean")
    immutable = immutable_bytes(clean_store)
    migrated = migrate_catalog(clean_store, region="cn")
    assert migrated["schema"] == "moly-asset-packs/2"
    assert not verify_catalog(clean_store / "asset-packs.json")[0]
    for name, data in immutable.items():
        assert (clean_store / name).read_bytes() == data
    assert source_file.read_bytes() == original_source


def test_explicit_and_migrated_provenance_use_the_public_allowlist(assets, tmp_path):
    from pack.migrate import migrate_catalog
    private = {"source": {"region": "cn", "assetVersion": "test", "root": "/private-cache/source"},
               "master": {"commit": "a" * 40, "checkout": "C:/fixture-cache/master"},
               "command": "extract --out /private-cache/output"}
    expected = {"source": {"region": "cn", "assetVersion": "test"}, "master": {"commit": "a" * 40}}
    catalog = build_groups(assets, tmp_path / "v2", "new", region="cn", provenance=private)
    assert catalog["provenance"] == expected
    legacy = tmp_path / "v1"
    build_groups(assets, legacy, "old")
    migrated = migrate_catalog(legacy, region="cn", provenance=private)
    assert migrated["provenance"]["source"] == expected
    assert "private-cache" not in json.dumps(migrated)
    assert not verify_catalog(legacy / "asset-packs.json")[0]


@pytest.mark.parametrize("location", ["/private-cache/master", "C:/fixture-cache/master",
                                      "C:\\fixture-cache\\master", "\\\\fixture-host\\share", "file:///private-cache/master"])
def test_allowlisted_identity_fields_cannot_smuggle_machine_paths(assets, tmp_path, location):
    with pytest.raises(ValueError, match="machine-specific"):
        build_groups(assets, tmp_path / "store", "bad", region="cn",
                     provenance={"master": {"repository": location}})
    assert not (tmp_path / "store/asset-packs.json").exists()


def test_change_only_invalidates_own_package_and_release(assets, tmp_path):
    out = tmp_path / "store"
    before = build_groups(assets, out, "a", region="cn")
    (assets / "ui/action-icon/Talk.png").write_bytes(b"actual changed UI content")
    after = build_groups(assets, out, "b", region="cn")
    changed = [name for name in packages(before) if packages(before)[name] != packages(after)[name]]
    assert changed == ["common/ui"]
    assert release_id(before) != release_id(after)
    assert not verify_catalog(out / "asset-packs.json")[0]


def test_encoding_changes_representation_not_content_identity(assets, tmp_path):
    source = tmp_path / "one"; source.mkdir()
    (source / "x.json").write_bytes(b'{"value":"some content"}')
    identity = tmp_path / "identity.toml"
    identity.write_text('[default]\ncodec="identity"\nhttp_encoding="identity"\n')
    gzip = tmp_path / "gzip.toml"
    gzip.write_text('[default]\ncodec="gzip"\nhttp_encoding="identity"\n')
    a = build(source, tmp_path / "a", "version1", categories_path=identity, format_version=2)
    b = build(source, tmp_path / "b", "version2", categories_path=gzip, format_version=2)
    assert a["content_id"] == b["content_id"]
    assert a["manifest_sha256"] != b["manifest_sha256"]
    assert gzip_compress(b"abc")[:10] == bytes.fromhex("1f8b08000000000002ff")
    assert gzip_compress(b"abc") == gzip_compress(b"abc")


def test_encoder_profile_prevents_history_dependent_output(assets, tmp_path, monkeypatch):
    from pack import codecs
    out = tmp_path / "store"
    a = build(assets, out, "a", format_version=2)
    doc = strict_json(Path(a["manifest"]).read_bytes())
    # A semantically valid legacy receipt cannot seed a new deterministic encoder.
    for recipe in doc["encoders"].values():
        recipe.clear(); recipe.update(implementation="legacy-v1", version="unrecorded")
    Path(a["manifest"]).write_bytes(canonical_bytes(doc))
    count = 0; encode = codecs.encode_blob
    def spy(*args):
        nonlocal count
        count += 1
        return encode(*args)
    monkeypatch.setattr(codecs, "encode_blob", spy)
    build(assets, out, "b", format_version=2)
    assert count


@pytest.mark.parametrize("damage", ["missing_blob", "corrupt_blob", "missing_manifest", "corrupt_manifest", "content_id", "wrong_address", "noncanonical"])
def test_damage_rejected_before_gc_or_publication(assets, tmp_path, damage):
    out = tmp_path / "store"
    catalog = build_groups(assets, out, "v1", region="cn")
    package = catalog["packages"][0]; path = out / package["manifest"]
    doc = strict_json(path.read_bytes()); blob = out / "blobs" / doc["entries"][0]["blob"]
    if damage == "missing_blob": blob.unlink()
    elif damage == "corrupt_blob": blob.write_bytes(b"BAD")
    elif damage == "missing_manifest": path.unlink()
    elif damage == "corrupt_manifest": path.write_bytes(b"{")
    elif damage == "content_id":
        doc["content_id"] = "0" * 64; path.write_bytes(canonical_bytes(doc))
    elif damage == "wrong_address":
        package["manifest"] = "packages/" + "f" * 64 + ".json"
        (out / package["manifest"]).write_bytes(canonical_bytes(doc))
        (out / "asset-packs.json").write_bytes(canonical_bytes(catalog))
    else: path.write_bytes(json.dumps(doc, indent=2).encode())
    errors, _ = verify_catalog(out / "asset-packs.json")
    assert errors
    with pytest.raises(ValueError, match="garbage"):
        collect_garbage(out, apply=True)


def test_active_channels_survive_history_selection_and_gc(assets, tmp_path):
    out = tmp_path / "store"
    cn = build_groups(assets, out, "cn1", region="cn")
    (assets / "ui/action-icon/Talk.png").write_bytes(b"jp-only changed bytes")
    jp = build_groups(assets, out, "jp1", region="jp")
    errors, info = verify_catalog(out / "asset-packs.json", retained_catalogs=[])
    assert not errors and len(info["channel_roots"]) == 2
    assert not info["orphan_manifests"] and not info["orphan_blobs"]
    for catalog in [cn, jp]:
        with pytest.raises(ValueError, match="active"):
            retire_catalog(out, release_id(catalog))
    (assets / "ui/action-icon/Talk.png").write_bytes(b"cn-newer")
    build_groups(assets, out, "cn2", region="cn")
    garbage = retire_catalog(out, release_id(cn))
    assert garbage["delete_manifests"]
    collect_garbage(out, apply=True)
    assert not verify_catalog(out / "asset-packs.json")[0]
    activate(out, release_id(jp))
    assert strict_json((out / "asset-packs.json").read_bytes())["version"] == "jp1"


def test_corrupt_or_wrong_region_channel_fails_closed(assets, tmp_path):
    out = tmp_path / "store"
    build_groups(assets, out, "cn", region="cn")
    build_groups(assets, out, "jp", region="jp")
    pointer = out / "channels/cn/stable.json"
    doc = strict_json(pointer.read_bytes()); doc["catalog"] = strict_json((out / "channels/jp/stable.json").read_bytes())["catalog"]
    pointer.write_bytes(canonical_bytes(doc))
    assert verify_catalog(out / "asset-packs.json")[0]
    with pytest.raises(ValueError): collect_garbage(out, apply=True)


def test_membership_covers_runtime_assets_not_build_receipts(assets):
    receipt = assets / "fixture-models/export-report.json"; receipt.write_text('{"time":"irrelevant"}')
    paths = runtime_paths(assets)
    assert "fixture-thumbnails/mysekai__test/thumb.png" in paths
    assert "custom-fixture-models/mysekai__test/model.glb" in paths
    assert "fixture-models/export-report.json" not in paths


def test_strict_document_and_release_ownership(assets, tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        strict_json(b'{"version":1,"version":2}')
    with pytest.raises(ValueError, match="finite"):
        strict_json(b'{"value":NaN}')
    out = tmp_path / "store"
    doc = build_groups(assets, out, "v1", region="tw")
    for damage in ["cycle", "ownership", "region", "content"]:
        altered = copy.deepcopy(doc)
        one, two = altered["packages"][:2]
        if damage == "cycle": one["dependencies"] = [one["id"]]
        elif damage == "ownership": two["paths"].append(one["paths"][0])
        elif damage == "region": altered["region"] = "../cn"
        else: one["content_id"] = "0" * 64
        assert verify_catalog_data(altered, out)[0]


def test_v2_schema_lite_matches_full_validation(assets, tmp_path, monkeypatch):
    monkeypatch.setenv("PACK_FORCE_SCHEMA_LITE", "1")
    out = tmp_path / "store"
    catalog = build_groups(assets, out, "v", region="cn")
    assert not verify_catalog(out / "asset-packs.json")[0]
    package = catalog["packages"][0]
    document = strict_json((out / package["manifest"]).read_bytes())
    document["version"] = "poison"
    assert verify_manifest(document, out / "blobs", check_orphans=False)[0]


def test_lossless_legacy_migration_preserves_original_and_blob_bytes(assets, tmp_path):
    from pack.migrate import migrate_catalog
    out = tmp_path / "store"
    legacy = build_groups(assets, out, "old")
    before = immutable_bytes(out)
    migrated = migrate_catalog(out, region="cn", version="new", provenance={"asset_version": "source"})
    assert migrated["schema"] == "moly-asset-packs/2"
    assert not verify_catalog_data(legacy, out)[0]
    assert not verify_catalog(out / "asset-packs.json")[0]
    for name, data in before.items(): assert (out / name).read_bytes() == data
    assert {p.relative_to(out).as_posix() for p in (out / "blobs").rglob("*") if p.is_file()} == {name for name in before if name.startswith("blobs/")}
    fresh = build_groups(assets, tmp_path / "fresh", "irrelevant", region="jp")
    # Migration preserves the legacy graph; v2 release labels may rename audio
    # families, but labels do not own the immutable content identity.
    assert sorted(p["content_id"] for p in fresh["packages"]) == sorted(p["content_id"] for p in migrated["packages"])


def test_verified_publisher_reuses_shared_global_objects(assets, tmp_path):
    from pack.publish import publish_catalog
    cn_root, jp_root, store = tmp_path / "cn", tmp_path / "jp", tmp_path / "global"
    cn = build_groups(assets, cn_root, "1", region="cn")
    jp = build_groups(assets, jp_root, "2", region="jp")
    first = publish_catalog(cn_root, store)
    second = publish_catalog(jp_root, store)
    assert first["copied_bytes"] > 0 and first["reused_bytes"] == 0
    assert second["copied_bytes"] == 0 and second["copied_objects"] == 0
    assert second["reused_bytes"] == first["copied_bytes"]
    assert not verify_catalog(store / "asset-packs.json")[0]
    assert len(list((store / "channels").glob("*/*.json"))) == 2
    selected = publish_catalog(cn_root, store, catalog=release_id(cn))
    assert selected["copied_objects"] == 0
    assert not collect_garbage(store)["delete"]


def test_publication_failure_never_changes_visible_release(assets, tmp_path, monkeypatch):
    from pack.publish import publish_catalog
    import pack.publish as publisher
    source, store = tmp_path / "source-store", tmp_path / "destination"
    first = build_groups(assets, source, "1", region="cn")
    publish_catalog(source, store)
    visible = (store / "asset-packs.json").read_bytes()
    (assets / "ui/action-icon/Talk.png").write_bytes(b"changed content for failed import")
    build_groups(assets, source, "2", region="cn")
    original_write = publisher.write_bytes
    def fail(path, data, **kwargs):
        if "blobs" in Path(path).parts: raise OSError("injected import failure")
        original_write(path, data, **kwargs)
    monkeypatch.setattr(publisher, "write_bytes", fail)
    with pytest.raises(OSError, match="injected"):
        publish_catalog(source, store)
    assert (store / "asset-packs.json").read_bytes() == visible
    assert not verify_catalog(store / "asset-packs.json")[0]


def test_v2_cli_region_selection_is_explicit(assets, tmp_path):
    from pack.groups import main
    with pytest.raises(SystemExit): main(["--src", str(assets), "--out", str(tmp_path / "out"), "--version", "1"])
    main(["--src", str(assets), "--out", str(tmp_path / "out"), "--version", "1", "--region", "tw"])
    assert strict_json((tmp_path / "out/asset-packs.json").read_bytes())["region"] == "tw"


def test_v2_audio_banks_and_bounded_shards_preserve_logical_abi(assets, tmp_path, monkeypatch):
    import pack.groups as grouping
    for index in range(16):
        file = assets / f"ui/more/icon{index}.png"; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(str(index).encode())
    for bank in ["voice-a", "voice-b"]:
        file = assets / f"phenomena/audio/{bank}/clip.wav"; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(bank.encode())
    monkeypatch.setattr(grouping, "MAX_PACKAGE_ENTRIES", 2)
    out = tmp_path / "store"
    catalog = build_groups(assets, out, "1", region="cn")
    ids = {row["id"] for row in catalog["packages"]}
    assert {"audio/voice-a", "audio/voice-b", "common/audio"} <= ids
    assert any(name.startswith("common/ui/shard-") for name in ids)
    assert all(len(row["paths"]) <= 2 for row in catalog["packages"])
    assert {p for row in catalog["packages"] for p in row["paths"]} == set(runtime_paths(assets))
    assert not verify_catalog(out / "asset-packs.json")[0]
    other = build_groups(assets, tmp_path / "other", "2", region="jp", paths=reversed(list(runtime_paths(assets))))
    assert packages(catalog) == packages(other)


def test_source_changes_during_build_cannot_publish_a_mixed_release(assets, tmp_path, monkeypatch):
    import pack.groups as grouping
    out = tmp_path / "store"
    build_groups(assets, out, "1", region="cn")
    visible = (out / "asset-packs.json").read_bytes()
    original = grouping.build; changed = False
    def mutate(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            (assets / "characters.json").write_bytes(b'{"characters":["changed while building"]}')
        return result
    monkeypatch.setattr(grouping, "build", mutate)
    with pytest.raises(RuntimeError, match="source changed"):
        build_groups(assets, out, "2", region="cn")
    assert (out / "asset-packs.json").read_bytes() == visible
    assert not verify_catalog(out / "asset-packs.json")[0]


def test_concurrent_independent_builds_produce_identical_immutable_bytes(assets, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    one, two = tmp_path / "one", tmp_path / "two"
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(build_groups, assets, one, "1", region="cn")
        b = executor.submit(build_groups, assets, two, "2", region="jp")
        assert packages(a.result()) == packages(b.result())
    assert immutable_bytes(one) == immutable_bytes(two)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_strict_json_rejects_all_nonfinite_encodings(number):
    with pytest.raises(ValueError, match="non-finite"):
        strict_json(('{"value":' + number + '}').encode())


@pytest.mark.parametrize("damage", ["duplicate_key", "noncanonical", "wrong_address"])
def test_standalone_manifest_verifier_checks_serialization_and_address(assets, tmp_path, damage):
    from pack.verify import verify
    out = tmp_path / "store"
    catalog = build_groups(assets, out, "v1", region="cn")
    file = out / catalog["packages"][0]["manifest"]
    if damage == "duplicate_key":
        raw = file.read_bytes()
        file.write_bytes(raw.replace(b'{', b'{"schema":"duplicate",', 1))
    elif damage == "noncanonical":
        file.write_bytes(json.dumps(strict_json(file.read_bytes()), indent=2).encode())
    else:
        other = file.parent / ("f" * 64 + ".json")
        other.write_bytes(file.read_bytes())
        file = other
    errors, _ = verify(file, out / "blobs", check_orphans=False)
    assert errors


def test_standalone_v2_manifest_cli_uses_store_root(tmp_path):
    from pack.verify import main
    source = tmp_path / "input"
    source.mkdir()
    (source / "one.bin").write_bytes(b"one immutable blob")
    result = build(source, tmp_path / "store", "v", format_version=2, manifest_name=None)
    assert main(["--manifest", result["manifest"]]) == 0


@pytest.mark.parametrize("damage", ["noncanonical_catalog", "unaddressed_history", "missing_history_manifest"])
def test_gc_validates_serialization_and_each_history_root(assets, tmp_path, damage):
    out = tmp_path / "store"
    old = build_groups(assets, out, "v1", region="cn")
    (assets / "ui/action-icon/Talk.png").write_bytes(b"new release UI")
    build_groups(assets, out, "v2", region="cn")
    if damage == "noncanonical_catalog":
        selected = out / "asset-packs.json"
        selected.write_bytes(json.dumps(strict_json(selected.read_bytes()), indent=2).encode())
    elif damage == "unaddressed_history":
        (out / "catalogs/unaddressed.json").write_bytes(canonical_bytes(old))
    else:
        previous_ui = next(p for p in old["packages"] if p["id"] == "common/ui")
        (out / previous_ui["manifest"]).unlink()
    assert verify_catalog(out / "asset-packs.json")[0]
    with pytest.raises(ValueError, match="garbage"):
        collect_garbage(out, apply=True)


def test_retained_catalog_paths_are_relative_to_explicit_store(assets, tmp_path):
    out = tmp_path / "store"
    old = build_groups(assets, out, "v1", region="cn")
    build_groups(assets, out, "v2", region="cn")
    retained = f"catalogs/{release_id(old)}.json"
    errors, info = verify_catalog(out / "asset-packs.json", retained_catalogs=[retained])
    assert not errors
    assert retained in info["roots"]


@pytest.mark.parametrize("component", ["blobs", "packages", "catalogs", "channels"])
def test_linked_store_components_are_rejected_before_gc(assets, tmp_path, component):
    import os
    out = tmp_path / "store"
    build_groups(assets, out, "v1", region="cn")
    original = out / component
    target = out / (component + "-actual")
    original.rename(target)
    try:
        original.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlinks are unavailable")
        import _winapi
        _winapi.CreateJunction(str(target), str(original))
    errors, _ = verify_catalog(out / "asset-packs.json")
    assert errors
    with pytest.raises(ValueError, match="garbage"):
        collect_garbage(out, apply=True)


@pytest.mark.parametrize("parameter", [0.1, 1.0, -1, 1 << 53])
def test_content_projection_rejects_noninteroperable_transform_numbers(assets, tmp_path, parameter):
    out = tmp_path / "store"
    result = build(assets, out, "v", format_version=2)
    doc = strict_json(Path(result["manifest"]).read_bytes())
    doc["entries"][0]["xf"] = "draco"
    doc["transforms"] = {"draco": {"tool": "test", "tool_version": "1", "params": {"parameter": parameter}}}
    with pytest.raises(ValueError, match="interoperable"):
        content_id(doc)
    assert any("interoperable" in error for error in verify_manifest(doc, out / "blobs")[0])


@pytest.mark.parametrize("damage", ["missing_parameter", "boolean_width", "out_of_range", "integer_switch", "extra_field"])
def test_v2_transform_receipt_validation_is_precise(assets, tmp_path, damage):
    from pack.build import load_transform_recipe
    out = tmp_path / "store"
    result = build(assets, out, "v", format_version=2)
    doc = strict_json(Path(result["manifest"]).read_bytes())
    doc["entries"][0]["xf"] = "meshopt"
    recipe = load_transform_recipe("meshopt")
    doc["transforms"] = {"meshopt": recipe}
    if damage == "missing_parameter": recipe["params"].pop("weld")
    elif damage == "boolean_width": recipe["params"]["quantize_position"] = True
    elif damage == "out_of_range": recipe["params"]["quantize_position"] = 17
    elif damage == "integer_switch": recipe["params"]["weld"] = 1
    else: recipe["machine"] = "should not be here"
    doc["content_id"] = content_id(doc)
    assert any("transform" in error for error in verify_manifest(doc, out / "blobs")[0])


@pytest.mark.parametrize("codec,http_encoding", [("gzip", "identity"), ("brotli", "identity"), ("identity", "br")])
def test_verifier_bounds_expansion_by_declared_size(tmp_path, codec, http_encoding):
    from pack.codecs import encode_blob, derive_blob_path
    from pack.verify import verify_blob
    content = b"highly compressible incorrect declared size" * 10000
    encoded = encode_blob(content, codec, http_encoding)
    digest = hashlib.sha256(encoded).hexdigest()
    entry = {"blob": derive_blob_path(digest, codec, http_encoding), "blob_bytes": len(encoded),
             "blob_sha256": digest, "bytes": 1, "content_sha256": hashlib.sha256(content).hexdigest(),
             "codec": codec, "http_encoding": http_encoding}
    path = tmp_path / "blobs" / entry["blob"]
    path.parent.mkdir(parents=True)
    path.write_bytes(encoded)
    assert any("expansion exceeds" in error for error in verify_blob(entry, tmp_path / "blobs"))


def test_runtime_budgets_are_independently_enforced_by_verifier(assets, tmp_path, monkeypatch):
    import pack.verify as verifier
    out = tmp_path / "store"
    catalog = build_groups(assets, out, "v", region="cn")
    document = strict_json((out / catalog["packages"][0]["manifest"]).read_bytes())
    with monkeypatch.context() as patch:
        patch.setattr(verifier, "MAX_RUNTIME_BLOB_BYTES", 1)
        assert any("runtime byte budget" in error for error in verify_manifest(document, out / "blobs")[0])
    with monkeypatch.context() as patch:
        patch.setattr(verifier, "MAX_RUNTIME_DOCUMENT_BYTES", 1)
        assert any("runtime document budget" in error for error in verify_manifest(document, out / "blobs")[0])
        assert any("runtime document budget" in error for error in verify_catalog_data(catalog, out)[0])


def test_migration_refuses_packages_outside_runtime_budget(assets, tmp_path, monkeypatch):
    import pack.verify as verifier
    from pack.migrate import migrate_catalog
    out = tmp_path / "store"
    build_groups(assets, out, "legacy")
    visible = (out / "asset-packs.json").read_bytes()
    monkeypatch.setattr(verifier, "MAX_RUNTIME_ASSET_BYTES", 1)
    with pytest.raises(ValueError, match="runtime byte budget"):
        migrate_catalog(out, region="cn")
    assert (out / "asset-packs.json").read_bytes() == visible


@pytest.mark.parametrize("use_lite", [False, True])
def test_v2_integer_contract_matches_full_and_fallback_schemas(assets, tmp_path, monkeypatch, use_lite):
    if use_lite:
        monkeypatch.setenv("PACK_FORCE_SCHEMA_LITE", "1")
    out = tmp_path / "store"
    catalog = build_groups(assets, out, "v", region="cn")
    document = strict_json((out / catalog["packages"][0]["manifest"]).read_bytes())
    document["download_bytes"] = float(document["download_bytes"])
    assert verify_manifest(document, out / "blobs")[0]
    catalog["packages"][0]["download_bytes"] = float(catalog["packages"][0]["download_bytes"])
    assert verify_catalog_data(catalog, out)[0]


def test_encoder_profile_rejects_numeric_lookalikes(assets, tmp_path, monkeypatch):
    # Release stores are identity-only; the gzip reader path is built explicitly.
    monkeypatch.setattr(groups, "CATEGORIES", Path(groups.__file__).with_name("categories.toml"))
    out = tmp_path / "store"
    catalog = build_groups(assets, out, "v", region="cn")
    package = next(p for p in catalog["packages"] if p["id"] == "common/tables")
    document = strict_json((out / package["manifest"]).read_bytes())
    document["encoders"]["gzip"]["level"] = 9.0
    assert "invalid deterministic gzip profile" in verify_manifest(document, out / "blobs")[0]


@pytest.mark.parametrize("size", [0, 1024 * 1024])
@pytest.mark.parametrize("codec,http_encoding", [("gzip", "identity"), ("brotli", "identity"), ("identity", "br")])
def test_verified_codec_roundtrip_accepts_empty_and_high_ratio_content(tmp_path, size, codec, http_encoding):
    from pack.codecs import encode_blob, derive_blob_path
    from pack.verify import verify_blob
    content = b"0" * size
    encoded = encode_blob(content, codec, http_encoding)
    digest = hashlib.sha256(encoded).hexdigest()
    entry = {"blob": derive_blob_path(digest, codec, http_encoding), "blob_bytes": len(encoded),
             "blob_sha256": digest, "bytes": size, "content_sha256": hashlib.sha256(content).hexdigest(),
             "codec": codec, "http_encoding": http_encoding}
    path = tmp_path / "blobs" / entry["blob"]
    path.parent.mkdir(parents=True)
    path.write_bytes(encoded)
    if size:
        assert size > 256 * len(encoded)
    assert not verify_blob(entry, tmp_path / "blobs")


def test_runtime_package_roundtrip_accepts_high_ratio_content(assets, tmp_path, monkeypatch):
    # Release stores are identity-only; the gzip reader path is built explicitly.
    monkeypatch.setattr(groups, "CATEGORIES", Path(groups.__file__).with_name("categories.toml"))
    import gzip
    data = canonical_bytes({"values": [0] * (512 * 1024)})
    (assets / "characters.json").write_bytes(data)
    out = tmp_path / "store"
    catalog = build_groups(assets, out, "v", region="cn")
    package = next(p for p in catalog["packages"] if p["id"] == "common/tables")
    document = strict_json((out / package["manifest"]).read_bytes())
    entry = next(e for e in document["entries"] if e["path"] == "characters.json")
    assert entry["bytes"] > 256 * entry["blob_bytes"]
    assert gzip.decompress((out / "blobs" / entry["blob"]).read_bytes()) == data
    assert not verify_catalog(out / "asset-packs.json")[0]


def test_node_brotli_verifier_fallback_is_bounded(tmp_path, monkeypatch):
    from pack.codecs import brotli_compress
    import pack.verify as verifier
    if shutil.which("node") is None:
        pytest.skip("Node.js fallback is not installed")
    data = b"0" * (1024 * 1024)
    encoded = brotli_compress(data)
    monkeypatch.setitem(sys.modules, "brotli", None)
    assert verifier._brotli_decompress(encoded, len(data)) == data
    with pytest.raises((ValueError, RuntimeError)):
        verifier._brotli_decompress(encoded, 1)


def test_actor_export_plans_progress_receipts_and_backups_never_enter_runtime(assets, tmp_path):
    evidence = ["actor-animations/export-plan.json", "actor-animations/export-progress.json",
                "actor-animations/generated/unit-01/motion/export-source.json",
                "actor-animations/_previous/unit-01/motion-123456/actor.glb",
                "actor-animations/generated/unit-01/motion.staging/actor.glb"]
    for name in evidence:
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"machine path / timing / incomplete evidence")
    runtime = assets / "actor-animations/generated/unit-01/motion/actor.glb"
    runtime.write_bytes(glb())
    paths = runtime_paths(assets)
    assert runtime.relative_to(assets).as_posix() in paths
    assert not set(evidence) & paths.keys()
    before = build_groups(assets, tmp_path / "first", "v", region="cn")
    for name in evidence:
        (assets / name).write_bytes(b"different machine, completion order and timestamp")
    after = build_groups(assets, tmp_path / "second", "v2", region="jp")
    assert packages(before) == packages(after)


def test_verifier_shares_successful_blob_checks_only_within_one_call(assets, tmp_path, monkeypatch):
    import pack.verify as verifier
    out = tmp_path / "store"
    build_groups(assets, out, "cn", region="cn")
    build_groups(assets, out, "jp", region="jp")
    original = verifier._decode_content
    decoded = 0
    def observed(*args, **kwargs):
        nonlocal decoded
        decoded += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(verifier, "_decode_content", observed)
    errors, info = verify_catalog(out / "asset-packs.json")
    assert not errors
    assert decoded == len(info["referenced_blobs"])
    victim = out / "blobs" / info["referenced_blobs"][0]
    victim.write_bytes(b"corrupted after prior verification")
    assert verify_catalog(out / "asset-packs.json")[0]


def test_compiled_schema_cache_reuses_configuration_not_instance_results(monkeypatch):
    import pack.verify as verifier
    verifier._compiled_schema_validator.cache_clear()
    schema = {"type": "integer", "minimum": 0}
    first, backend = verifier._load_schema_validator(schema)
    second, _ = verifier._load_schema_validator({"minimum": 0, "type": "integer"})
    assert first is second
    assert first(3) == [] and first(-1)
    schema["minimum"] = 2
    changed, _ = verifier._load_schema_validator(schema)
    assert changed is not first and changed(1)
    monkeypatch.setenv("PACK_FORCE_SCHEMA_LITE", "1")
    fallback, fallback_backend = verifier._load_schema_validator(schema)
    assert fallback_backend.startswith("schema_lite")
    assert fallback(1)
    if backend.startswith("jsonschema"):
        assert fallback is not changed
