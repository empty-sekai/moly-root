import json
import re
from pathlib import Path

import pytest

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


def test_the_constant_package_is_a_domain_of_its_own(tmp_path):
    # The talk scripts resolve against tables this package carries, so it is
    # read on every run that has it.  A package that is read must not be
    # reported as having no extractor: `unsupported` means nobody looks at it,
    # and reading it while saying that is the label promising less than the
    # coverage.  It is also not folded into the talk domain, so a failure to
    # read the tables is visible as its own entry.
    from core.assets.router import TALK_CONSTANTS_PACKAGE

    target = route(TALK_CONSTANTS_PACKAGE)
    assert target is not None
    assert target.domain == "talk-constants"
    assert route("mysekai__talk__scenario__talk").domain == "talk"

    manifest = tmp_path / "manifest.txt"
    manifest.write_text(TALK_CONSTANTS_PACKAGE + "\nmysekai__talk__scenario__x\n",
                        encoding="utf-8")
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    report = extract_manifest(manifest, bundles, tmp_path / "out")
    by_name = {e["bundle"]: e for e in report["bundles"]}
    assert by_name[TALK_CONSTANTS_PACKAGE]["status"] != "unsupported"
    # A neighbour in the same path that nobody reads stays visible.
    assert by_name["mysekai__talk__scenario__x"]["status"] == "unsupported"


def test_the_constant_package_path_reaches_the_furniture_talk_reader(tmp_path, monkeypatch):
    # The tables are an input to the reader, not something it finds for itself:
    # if the path stops being handed over, every token stays a source token and
    # only the document's own constants criterion would notice.
    from core.assets.router import TALK_CONSTANTS_PACKAGE
    from chara import talks as chara_talks
    import perf.fixture_talks as perf_fixture_talks

    seen = {}

    def fake_talks(master_source, talk_bundle, out_path, master_cache=None,
                   lib_bundle=None):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("{}", encoding="utf-8")
        return {"talks": 0, "json": out_path}

    def fake_fixture_talks(master_source, talk_bundle, out_path,
                           master_cache=None, lib_bundle=None):
        seen["lib"] = lib_bundle
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("{}", encoding="utf-8")
        return {"talks": 0}

    def fake_read_constants(lib_bundle):
        return {"Motions": {"a": "mov_a"}}, {}, "defines.lua"

    monkeypatch.setattr(chara_talks, "extract_talks", fake_talks)
    monkeypatch.setattr(perf_fixture_talks, "extract_fixture_talks",
                        fake_fixture_talks)
    monkeypatch.setattr(perf_fixture_talks, "read_constants", fake_read_constants)

    bundles = tmp_path / "bundles"
    bundles.mkdir()
    for name in ("mysekai__talk__scenario__talk", TALK_CONSTANTS_PACKAGE):
        (bundles / name).write_bytes(b"x")
    manifest = tmp_path / "manifest.txt"
    # The constant package listed *after* the talk package: the tables are read
    # before the loop, so the order in the manifest must not decide the outcome.
    manifest.write_text("mysekai__talk__scenario__talk\n"
                        + TALK_CONSTANTS_PACKAGE + "\n", encoding="utf-8")
    report = extract_manifest(manifest, bundles, tmp_path / "out",
                              master=str(tmp_path / "master"))

    assert seen.get("lib") == str(bundles / TALK_CONSTANTS_PACKAGE)
    entry = {e["bundle"]: e for e in report["bundles"]}[TALK_CONSTANTS_PACKAGE]
    assert entry["status"] == "succeeded"
    assert entry["counts"]["entries"] == 1


def test_the_furniture_talk_reader_is_told_when_there_are_no_constants(tmp_path, monkeypatch):
    # A run without the constant package is not an error, but the reader must be
    # told so its document can say the tokens are unresolved rather than
    # describing them as resolved.
    from chara import talks as chara_talks
    import perf.fixture_talks as perf_fixture_talks

    seen = {}

    def fake_talks(master_source, talk_bundle, out_path, master_cache=None,
                   lib_bundle=None):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("{}", encoding="utf-8")
        return {"talks": 0, "json": out_path}

    def fake_fixture_talks(master_source, talk_bundle, out_path,
                           master_cache=None, lib_bundle=None):
        seen["lib"] = lib_bundle
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("{}", encoding="utf-8")
        return {"talks": 0}

    monkeypatch.setattr(chara_talks, "extract_talks", fake_talks)
    monkeypatch.setattr(perf_fixture_talks, "extract_fixture_talks",
                        fake_fixture_talks)

    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "mysekai__talk__scenario__talk").write_bytes(b"x")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("mysekai__talk__scenario__talk\n", encoding="utf-8")
    extract_manifest(manifest, bundles, tmp_path / "out",
                     master=str(tmp_path / "master"))

    assert "lib" in seen
    assert seen["lib"] is None


def test_an_explicit_unity_version_is_the_one_the_run_reads_with(tmp_path, monkeypatch):
    # The parameter used to be accepted and ignored, so a caller that passed a
    # version read with whatever an extractor module had assigned at import.
    # Red when the argument stops reaching the reader configuration.
    import UnityPy.config
    from core.extract import DEFAULT_UNITY_VERSION

    monkeypatch.setattr(UnityPy.config, "FALLBACK_UNITY_VERSION",
                        "9.9.9f9", raising=False)
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("mysekai__system__unrelated\n", encoding="utf-8")
    extract_manifest(manifest, bundles, tmp_path / "out",
                     unity_version="1.2.3f4")
    assert UnityPy.config.FALLBACK_UNITY_VERSION == "1.2.3f4"
    assert DEFAULT_UNITY_VERSION != "1.2.3f4"


def test_a_run_with_nothing_configured_still_has_a_version(tmp_path, monkeypatch):
    # The violation is planted by taking the configuration away, which is the
    # real starting state: UnityPy ships this unset and raises on a header-less
    # bundle.  The jobs that run before the per-bundle loop load bundles before
    # any module that assigns it has been imported, so leaving it to import
    # order is what made those domains fail.
    import UnityPy.config
    from core.extract import DEFAULT_UNITY_VERSION

    monkeypatch.setattr(UnityPy.config, "FALLBACK_UNITY_VERSION",
                        None, raising=False)
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("mysekai__system__unrelated\n", encoding="utf-8")
    extract_manifest(manifest, bundles, tmp_path / "out")
    assert UnityPy.config.FALLBACK_UNITY_VERSION == DEFAULT_UNITY_VERSION


def test_a_configured_version_is_not_overwritten_by_the_default(tmp_path, monkeypatch):
    # Passing no version must not reset what the caller already chose; the
    # command line configures it before calling.
    import UnityPy.config

    monkeypatch.setattr(UnityPy.config, "FALLBACK_UNITY_VERSION",
                        "5.5.5f5", raising=False)
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("mysekai__system__unrelated\n", encoding="utf-8")
    extract_manifest(manifest, bundles, tmp_path / "out")
    assert UnityPy.config.FALLBACK_UNITY_VERSION == "5.5.5f5"


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


def test_route_camera_is_an_exact_name_not_a_prefix():
    # The dialogue-camera assets are one package, so the domain is claimed by
    # that exact name.  Red when the check is written as a prefix: every
    # ``mysekai__camera_*`` neighbour would then be swallowed into the camera
    # job instead of staying visible as an unsupported domain.
    target = route("mysekai__camera")
    assert target is not None
    assert target.domain == "camera"
    assert target.extractor == "perf"
    for neighbour in ("mysekai__camera_xxx", "mysekai__camera__setting",
                      "mysekai__cameras", "mysekai__camera_motion"):
        assert route(neighbour) is None, neighbour


def test_camera_domain_leaves_every_existing_domain_alone():
    # The new domain name collides with none of the existing ones, and no
    # existing package changed hands.  Red if ``camera`` reuses a name already
    # taken, or if any of these packages starts answering with another domain.
    existing = {"character", "motion", "facial", "performance", "emoticon",
                "talk", "phenomena", "site", "sound", "fixture-interface",
                "cutscene-timeline", "fixture-timeline"}
    assert route("mysekai__camera").domain not in existing
    unchanged = {
        "mysekai__character_alone_action": "performance",
        "mysekai__talk__scenario__talk": "talk",
        "mysekai__fixture__mdl_bir1103_fixture_balloon1": "fixture-interface",
        "mysekai__cut_scene__expansion_homesite_01": "cutscene-timeline",
        "mysekai__fixture_timeline__mdl_chr0003_fixture_arcade1": "fixture-timeline",
    }
    for name, domain in unchanged.items():
        assert route(name).domain == domain, name


def test_talk_package_feeds_both_talk_extractors(tmp_path, monkeypatch):
    # One package, two families: the single-character talks that happen away
    # from a fixture, and the beside-the-fixture ones.  Both extractors run over
    # it and write different artifacts.  Red when only one of them is called,
    # and red when the two are pointed at one path so the second overwrites the
    # first.
    import chara.talks as chara_talks
    import perf.fixture_talks as perf_fixture_talks

    written = {}

    def fake_talks(master_source, talk_bundle, out_path, master_cache=None,
                   lib_bundle=None):
        written["chara"] = out_path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text('{"talks": 1412}', encoding="utf-8")
        return {"talks": 1412, "json": out_path}

    def fake_fixture_talks(master_source, talk_bundle, out_path, master_cache=None,
                           lib_bundle=None):
        written["perf"] = out_path
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text('{"talks": 4768}', encoding="utf-8")
        return {"talks": 4768}

    monkeypatch.setattr(chara_talks, "extract_talks", fake_talks)
    monkeypatch.setattr(perf_fixture_talks, "extract_fixture_talks", fake_fixture_talks)

    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "mysekai__talk__scenario__talk").write_bytes(b"x")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("mysekai__talk__scenario__talk\n", encoding="utf-8")
    out = tmp_path / "out"
    report = extract_manifest(manifest, bundles, out, master=str(tmp_path / "master"))

    assert set(written) == {"chara", "perf"}
    assert Path(written["chara"]) != Path(written["perf"])
    assert json.loads(Path(written["chara"]).read_text(encoding="utf-8")) == {"talks": 1412}
    assert json.loads(Path(written["perf"]).read_text(encoding="utf-8")) == {"talks": 4768}
    fixture_entry = [entry for entry in report["derived"]
                     if entry["artifact"] == "fixture-talks/talks.json"]
    assert len(fixture_entry) == 1
    assert fixture_entry[0]["status"] == "succeeded"
    assert fixture_entry[0]["counts"]["talks"] == 4768


def test_animation_export_is_a_derived_pass_after_the_timeline_jobs(tmp_path, monkeypatch):
    # The animation export is driven by the clip-target documents the timeline
    # jobs write, not by any package name.  Red when the pass runs before them:
    # it would be handed an empty target set.  Red as well if it is ever given a
    # route, which would promise that some bundle name selects it.
    import core.extract as extract_module
    import perf.animations as animations

    def fake_timeline_job(paths, out_dir, bundle_root=None):
        targets = Path(out_dir) / "clip-targets"
        targets.mkdir(parents=True, exist_ok=True)
        (targets / "package.json").write_text(json.dumps({
            "package": "mysekai__fixture_timeline__mdl_chr0003_fixture_arcade1",
            "clips": [{"targetPackage": "mysekai__character_motion",
                       "clipName": "mov_cw_normal_idle001_L"}]}), encoding="utf-8")
        return {"perBundle": {}}

    seen = {}

    def fake_export_targets(by_target, decrypted_dir, out_dir, limit=None,
                            progress=None, foreign=None):
        seen["byTarget"] = {name: sorted(clips) for name, clips in by_target.items()}
        return {"results": {}, "exported": 0, "skipped": 0, "failed": [],
                "remaining": 0, "total": len(by_target)}

    monkeypatch.setattr(extract_module, "_run_timeline_job", fake_timeline_job)
    monkeypatch.setattr(animations, "export_targets", fake_export_targets)

    bundles = tmp_path / "bundles"
    bundles.mkdir()
    package = "mysekai__fixture_timeline__mdl_chr0003_fixture_arcade1"
    (bundles / package).write_bytes(b"x")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text(package + "\n", encoding="utf-8")
    report = extract_manifest(manifest, bundles, tmp_path / "out")

    assert "byTarget" in seen, "the export pass never saw the timeline output"
    assert seen["byTarget"] == {"mysekai__character_motion": ["mov_cw_normal_idle001_L"]}
    entry = [e for e in report["derived"] if e["artifact"] == "perf-animations/"]
    assert len(entry) == 1
    assert entry[0]["counts"]["targets"] == 1
    for name in ("mysekai__animation", "mysekai__animations",
                 "mysekai__perf_animations", "mysekai__character_motion",
                 package):
        target = route(name)
        assert target is None or target.domain != "animations", name


def test_ui_is_skipped_with_a_reason_when_no_player_data(tmp_path):
    # The dialogue UI is built into the player data, which no bundle name can
    # name, so without that path the report still carries the domain, marked
    # skipped and saying why.  Red when the entry is dropped from the report,
    # and red when it is there without a reason: either way a reader cannot tell
    # "this domain does not exist" from "nobody supplied its input".
    store = tmp_path / "decrypted"
    store.mkdir()
    out = tmp_path / "out"
    report = extract_manifest(None, store, out)
    ui = [entry for entry in report.get("playerData", [])
          if entry.get("domain") == "ui"]
    assert len(ui) == 1, report.get("playerData")
    assert ui[0]["status"] == "skipped"
    assert ui[0]["error"].strip()
    assert not (out / "ui" / "talk.json").exists()


def test_ui_runs_when_the_player_data_path_is_supplied(tmp_path):
    # The other side of the same rule: given a path the domain is executed
    # rather than skipped, and a path that does not resolve is reported as a
    # failure naming it -- not as "skipped", which would claim nobody asked.
    store = tmp_path / "decrypted"
    store.mkdir()
    absent = tmp_path / "data.unity3d"
    report = extract_manifest(None, store, tmp_path / "out", player_data=absent)
    ui = [entry for entry in report["playerData"] if entry.get("domain") == "ui"]
    assert len(ui) == 1
    assert ui[0]["status"] == "failed"
    assert "data.unity3d" in ui[0]["error"]


# ---------------------------------------------------------------------------
# Read/write path alignment.
#
# The extraction side and the demo side each have their own green tests and can
# still fail to meet: the pipeline writes ``fixture-interface/attach-points.json``
# and the demo fetches ``fixture-attach/attach-points.json``, and nothing in
# either suite notices, because neither side names the other's path.  The checks
# below close that gap by reading the *consumer's* declared paths out of
# ``examples/stage/data.js`` and asking the pipeline whether it writes them.
#
# ``data.js`` is only ever read here, never written: it belongs to the viewer
# lane.  The pipeline's paths are the authoritative side -- they are what the
# report registers and what the extraction tests pin -- so a mismatch is a
# statement about the consumer, not a licence to move an artifact.
# ---------------------------------------------------------------------------

DEMO_DATA_JS = Path(__file__).resolve().parents[1] / "examples" / "stage" / "data.js"


def _demo_asset_specs():
    """The ``{id, label, path, kind}`` table ``examples/stage/data.js`` declares."""
    text = DEMO_DATA_JS.read_text(encoding="utf-8")
    start = text.index("ASSET_SPECS")
    body = text[text.index("[", start):text.index("];", start)]
    specs = []
    for entry in re.finditer(r"\{[^{}]*\}", body):
        fields = dict(re.findall(r"(\w+):\s*'([^']*)'", entry.group(0)))
        if "path" not in fields:
            continue
        specs.append({"id": fields.get("id", ""), "path": fields["path"],
                      "kind": fields.get("kind", "file")})
    return specs


def _writes(declared_path, kind, written):
    """Does the pipeline write *declared_path*, as declared?

    *written* is the pipeline's declared output set, directory entries carrying a
    trailing slash.  A declared file is satisfied by an exact match or by sitting
    inside a directory the pipeline fills with per-package documents; a declared
    directory is satisfied by an exact match or by the pipeline writing something
    beneath it.  A declared directory is *not* satisfied by an ancestor: reading
    ``camera/out/`` when the job writes its documents straight into ``camera/``
    finds nothing, which is exactly the failure this is here to catch.
    """
    target = declared_path.rstrip("/")
    for entry in written:
        if entry.rstrip("/") == target:
            return True
        if entry.startswith(target + "/"):
            return True
        if kind == "file" and entry.endswith("/") and target.startswith(entry):
            return True
    return False


def test_demo_declares_a_readable_asset_table():
    # The guard against a vacuous alignment check: if the parse above silently
    # returned nothing, "every declared path is written" would be trivially
    # true.  Red when ``data.js`` stops declaring its reads in a table this can
    # read -- the point at which the alignment check stops meaning anything and
    # must be rewritten rather than believed.
    specs = _demo_asset_specs()
    assert len(specs) >= 10, specs
    ids = {spec["id"] for spec in specs}
    assert {"manifest", "attach", "areas", "fixture-models"} <= ids, sorted(ids)
    assert all(spec["path"] for spec in specs)


def test_extract_registers_no_artifact_path_it_does_not_declare(tmp_path):
    # ``FIXED_ARTIFACT_PATHS`` is what the alignment check below compares
    # against, so it must not drift away from the code.  Red when a new derived
    # artifact is registered under a path the declaration does not carry --
    # which would otherwise let the alignment check pass while the consumer
    # still cannot find the new artifact.
    from core.extract import FIXED_ARTIFACT_PATHS
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    for name in ("mysekai__fixture__mdl_bir1103_fixture_balloon1",
                 "mysekai__camera", "mysekai__site__field__grasslands",
                 "mysekai__phenomena", "mysekai__talk__scenario__talk"):
        (bundles / name).write_bytes(b"x")
    report = extract_manifest(None, bundles, tmp_path / "out",
                              master=str(tmp_path / "master"))
    declared = {entry.rstrip("/") for entry in FIXED_ARTIFACT_PATHS}
    registered = [entry["artifact"] for entry in
                  report["derived"] + report.get("playerData", [])]
    assert registered, report
    missing = [name for name in registered if name.rstrip("/") not in declared]
    assert not missing, missing


def test_every_path_the_demo_reads_is_a_path_the_pipeline_writes():
    # Red while any declared read path is one the pipeline never writes, and it
    # prints them one by one.  This cannot be quietened by adding a fallback on
    # either side: the comparison is between two independent declarations, so a
    # loader that guesses a second location still leaves the declared path
    # unwritten.
    from core.extract import FIXED_ARTIFACT_PATHS
    specs = _demo_asset_specs()
    unwritten = [spec for spec in specs
                 if not _writes(spec["path"], spec["kind"], FIXED_ARTIFACT_PATHS)]
    detail = "\n".join(
        "  {}: reads {!r} ({}) -- the pipeline writes no such path".format(
            spec["id"], spec["path"], spec["kind"])
        for spec in unwritten)
    assert not unwritten, (
        "{} of {} paths declared by {} are never written by extract:\n{}".format(
            len(unwritten), len(specs), DEMO_DATA_JS.name, detail))


def test_fixture_geometry_is_skipped_with_a_reason_when_not_asked_for(tmp_path):
    # The geometry pass is opt-in because it is the heaviest artifact the
    # pipeline makes.  Off, its entry must still be in the report, marked
    # skipped and saying why.  Red when the entry is dropped: a reader could
    # then not tell "nobody asked for geometry" from "this pipeline has no
    # geometry", which is what the report said while the exporter was
    # unreachable dead code.
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "mysekai__fixture__mdl_bir1103_fixture_balloon1").write_bytes(b"x")
    out = tmp_path / "out"
    report = extract_manifest(None, bundles, out)
    entry = [e for e in report["derived"] if e["artifact"] == "fixture-models/"]
    assert len(entry) == 1, [e["artifact"] for e in report["derived"]]
    assert entry[0]["status"] == "skipped"
    assert entry[0]["error"].strip()
    assert not (out / "fixture-models").exists()


def test_fixture_geometry_runs_and_is_registered_when_asked_for(tmp_path,
                                                                monkeypatch):
    # The other side of the same rule, and the wiring check: asked for, the
    # geometry pass is actually called, it is handed ``fixture-models`` under the
    # output directory, and its outcome is registered as its own artifact rather
    # than folded into the attach job's entry.  Red when the exporter is not
    # reachable from the pipeline at all -- which is how it sat until now, with
    # only its own unit test able to call it.
    import fixtures.interface as interface

    called = {}

    def fake_geometry(store, out_dir):
        called["out"] = out_dir
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "index.json").write_text("{}", encoding="utf-8")
        return {"bundles": 3, "exported": 3, "prefabs": 6, "failed": 0}

    monkeypatch.setattr(interface, "extract_geometry", fake_geometry)

    bundles = tmp_path / "bundles"
    bundles.mkdir()
    for name in ("mysekai__fixture__mdl_bir1103_fixture_balloon1",
                 "mysekai__fixture__mdl_bir1103_fixture_cake1",
                 "mysekai__fixture__mdl_bir1103_fixture_chair1"):
        (bundles / name).write_bytes(b"x")
    out = tmp_path / "out"
    report = extract_manifest(None, bundles, out, fixture_meshes=True)

    assert Path(called.get("out", "")) == out / "fixture-models"
    entry = [e for e in report["derived"] if e["artifact"] == "fixture-models/"]
    assert len(entry) == 1, [e["artifact"] for e in report["derived"]]
    assert entry[0]["status"] == "succeeded", entry[0]
    assert entry[0]["counts"]["exported"] == 3
    assert entry[0]["error"] == ""
    attach = [e for e in report["derived"]
              if e["artifact"] == "fixture-interface/attach-points.json"]
    assert len(attach) == 1
    assert "fixture-models" not in json.dumps(attach[0])
