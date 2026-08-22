import json

from core.assets.manifest import parse_manifest
from core.extract import extract_manifest


def test_manifest_accepts_slash_and_double_underscore_names():
    assert parse_manifest("mysekai/character/mdl_sd_101_001\n") == [
        "mysekai__character__mdl_sd_101_001"
    ]


def test_missing_bundle_is_reported_as_failed(tmp_path):
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("mysekai/character/mdl_sd_999_001\n", encoding="utf-8")
    report = extract_manifest(manifest, tmp_path / "bundles", tmp_path / "out")
    assert report["summary"]["failed"] == 1
    assert report["bundles"][0]["status"] == "failed"
    assert "mdl_sd_999_001" in report["bundles"][0]["error"]


def test_pack_manifest_rebuilt_from_disk(tmp_path):
    from core.extract import write_pack_manifest
    out = tmp_path / "out"
    out.mkdir()
    (out / "sd_112.glb").write_bytes(b"x")
    (out / "sd_112.rig.json").write_text("{}", encoding="utf-8")
    (out / "sd_101.glb").write_bytes(b"x")
    (out / "motion-library.glb").write_bytes(b"x")
    path = write_pack_manifest(out)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [u["unit"] for u in doc["units"]] == ["101", "112"]
    assert doc["units"][1] == {"unit": "112", "glb": "sd_112.glb", "rig": "sd_112.rig.json"}
    assert "rig" not in doc["units"][0]


def test_pack_manifest_skipped_when_no_characters(tmp_path):
    from core.extract import write_pack_manifest
    out = tmp_path / "out"
    out.mkdir()
    assert write_pack_manifest(out) is None
    assert not (out / "manifest.json").exists()


def test_discovery_selects_recognized_bundles(tmp_path):
    from core.extract import discover_bundles
    store = tmp_path / "decrypted"
    store.mkdir()
    for name in ("mysekai__character__mdl_sd_112_001", "mysekai__character_motion",
                 "mysekai__character_settings", "mysekai__character_alone_action",
                 "mysekai__shader", "mysekai__effect__emoticon__fx_emote_001",
                 "mysekai__site__something", "readme.txt"):
        (store / name).write_bytes(b"x")
    names, ignored = discover_bundles(store)
    assert names[0] == "mysekai__character__mdl_sd_112_001"   # 角色在前(动作库参考骨架)
    assert set(names) == {"mysekai__character__mdl_sd_112_001", "mysekai__character_motion",
                          "mysekai__character_settings", "mysekai__character_alone_action",
                          "mysekai__shader", "mysekai__effect__emoticon__fx_emote_001"}
    assert ignored == 2


def test_extract_without_manifest_discovers(tmp_path):
    store = tmp_path / "decrypted"
    store.mkdir()
    (store / "mysekai__character__mdl_sd_999_001").write_bytes(b"not a bundle")
    report = extract_manifest(None, store, tmp_path / "out")
    assert report["summary"]["requested"] == 1
    assert report["discovery"] == {"scanned": 1, "selected": 1, "ignored": 0}
