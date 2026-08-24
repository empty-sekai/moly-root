import json

from core.assets.manifest import parse_manifest
from core.assets.router import route
from core.extract import extract_manifest


def test_route_fixture_interface():
    # Two real fixture bundles, and the domain is the honest *fixture-interface*
    # (not *fixture*: only attach points + grid are read, not the whole family).
    for name in ("mysekai__fixture__mdl_bir1103_fixture_balloon1",
                 "mysekai__fixture__mdl_bir1103_fixture_cake1"):
        target = route(name)
        assert target is not None
        assert target.domain == "fixture-interface"
        assert target.extractor == "fixtures"


def test_route_cutscene_timeline():
    for name in ("mysekai__cut_scene__expansion_homesite_01",
                 "mysekai__cut_scene__expansion_homesite_02"):
        target = route(name)
        assert target is not None
        assert target.domain == "cutscene-timeline"
        assert target.extractor == "perf"


def test_route_fixture_timeline():
    for name in ("mysekai__fixture_timeline__mdl_chr0003_fixture_arcade1",
                 "mysekai__fixture_timeline__mdl_chr0005_fixture_booth1"):
        target = route(name)
        assert target is not None
        assert target.domain == "fixture-timeline"
        assert target.extractor == "perf"


def test_route_fixture_prefix_does_not_eat_neighbour():
    # A fixture-timeline bundle must not be claimed by the fixture-interface
    # domain.  That is exactly the regression a shorter prefix
    # (``mysekai__fixture``, one underscore short) would cause: it matches both
    # families and silently routes the timeline family into the interface job.
    target = route("mysekai__fixture_timeline__mdl_chr0003_fixture_arcade1")
    assert target is not None
    assert target.domain == "fixture-timeline"
    assert target.domain != "fixture-interface"


def test_route_new_domains_do_not_collide_with_existing():
    alone = route("mysekai__character_alone_action")
    assert alone is not None
    assert alone.domain == "performance"
    assert alone.extractor == "alone-actions"
    existing = {"character", "motion", "facial", "performance", "emoticon",
                "talk", "phenomena", "site", "sound"}
    for name in ("mysekai__fixture__mdl_bir1103_fixture_balloon1",
                 "mysekai__cut_scene__expansion_homesite_01",
                 "mysekai__fixture_timeline__mdl_chr0003_fixture_arcade1"):
        assert route(name).domain not in existing


def test_new_domain_packages_not_reported_unsupported(tmp_path):
    # Adding the three routes must only shrink `unsupported` by exactly the
    # packages those families own; a package that stays outside them must still
    # be reported unsupported, not silently swallowed into one of the new jobs.
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("\n".join([
        "mysekai__fixture__mdl_bir1103_fixture_balloon1",
        "mysekai__cut_scene__expansion_homesite_01",
        "mysekai__fixture_timeline__mdl_chr0003_fixture_arcade1",
        "mysekai__system__unrelated"])
        + "\n", encoding="utf-8")
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    report = extract_manifest(manifest, bundles, tmp_path / "out")
    unsupported = [e["bundle"] for e in report["bundles"]
                   if e["status"] == "unsupported"]
    assert unsupported == ["mysekai__system__unrelated"]


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
                 "mysekai__site__field__grasslands",
                 "mysekai__system__unrelated", "readme.txt"):
        (store / name).write_bytes(b"x")
    names, ignored = discover_bundles(store)
    assert names[0] == "mysekai__character__mdl_sd_112_001"   # 角色在前(动作库参考骨架)
    assert set(names) == {"mysekai__character__mdl_sd_112_001", "mysekai__character_motion",
                          "mysekai__character_settings", "mysekai__character_alone_action",
                          "mysekai__shader", "mysekai__effect__emoticon__fx_emote_001",
                          "mysekai__site__field__grasslands"}
    assert ignored == 2


def test_extract_without_manifest_discovers(tmp_path):
    store = tmp_path / "decrypted"
    store.mkdir()
    (store / "mysekai__character__mdl_sd_999_001").write_bytes(b"not a bundle")
    report = extract_manifest(None, store, tmp_path / "out")
    assert report["summary"]["requested"] == 1
    assert report["discovery"] == {"scanned": 1, "selected": 1, "ignored": 0}
