"""Offline regressions for source-pinned masters and all-actor extraction."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import jp_master
from chara import fixture_actor_libraries as actors, motion_library, sd_switch


def test_haruki_path_mapping_is_flat_and_source_qualified():
    tree = [
        {"path": "master/mysekaiFixtures.json", "sha": "a" * 40, "type": "blob"},
        {"path": "master/musicDifficulties.json", "sha": "b" * 40, "type": "blob"},
        {"path": "master/nested/escape.json", "sha": "c" * 40, "type": "blob"},
        {"path": "master/../escape.json", "sha": "d" * 40, "type": "blob"},
        {"path": "versions/current_version.json", "sha": "e" * 40, "type": "blob"},
    ]
    selected = jp_master.master_paths(tree, "master/")
    assert [row["path"] for row in selected] == ["mysekaiFixtures.json"]
    assert selected[0]["sourcePath"] == "master/mysekaiFixtures.json"
    all_rows = jp_master.master_paths(tree, "master/", all_tables=True)
    assert {row["path"] for row in all_rows} == {"mysekaiFixtures.json", "musicDifficulties.json"}
    assert jp_master.PROVIDERS["haruki"][0] == "Team-Haruki/haruki-sekai-master"


def test_provider_tree_requires_real_blob_identity():
    with pytest.raises(jp_master.JPError, match="blob identity"):
        jp_master.master_paths([{"path": "master/mysekaiFixtures.json", "type": "blob", "sha": ""}], "master/")


def test_unknown_provider_fails_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(jp_master, "_read", lambda *args, **kwargs: pytest.fail("network was called"))
    with pytest.raises(ValueError, match="provider"):
        jp_master.download_master_snapshot(tmp_path / "missing.json", tmp_path / "out", provider="untrusted")


def test_legacy_provider_keeps_flat_master_paths():
    rows = jp_master.master_paths([{"path": "mysekaiFixtures.json", "type": "blob", "sha": "a" * 40}], "")
    assert rows[0]["path"] == rows[0]["sourcePath"] == "mysekaiFixtures.json"


def _clip(path_id, name="same_name_S", type_id=95):
    tree = {"m_Name": name, "m_ClipBindingConstant": {"genericBindings": [{"typeID": type_id}]},
            "m_MuscleClip": {"m_StartTime": 0., "m_StopTime": 1., "m_LoopTime": False}}
    return SimpleNamespace(type=SimpleNamespace(name="AnimationClip"),
        assets_file=SimpleNamespace(name="CAB-real-file"), path_id=path_id,
        read_typetree=lambda: tree)


def test_exact_source_selection_preserves_duplicate_clip_names(monkeypatch):
    import UnityPy
    monkeypatch.setattr(UnityPy, "load", lambda _: SimpleNamespace(objects=[_clip(11), _clip(22)]))
    monkeypatch.setattr(motion_library, "sample_frames", lambda _: (60., [(0., "test sample")]))
    samples, errors = motion_library.discover_and_sample("source", source_clips={("CAB-real-file", "11"), ("CAB-real-file", "22")})
    assert not errors and len(samples) == 2
    assert {row["sourceMetadata"]["sourceClip"]["pathId"] for row in samples.values()} == {"11", "22"}
    assert all(row["sourceMetadata"]["sourceClipName"] == "same_name_S" for row in samples.values())
    legacy, errors = motion_library.discover_and_sample("source")
    assert not errors and len(legacy) == 1


def test_exact_source_selection_does_not_fall_back_to_same_name(monkeypatch):
    import UnityPy
    monkeypatch.setattr(UnityPy, "load", lambda _: SimpleNamespace(objects=[_clip(11)]))
    monkeypatch.setattr(motion_library, "sample_frames", lambda _: pytest.fail("wrong clip sampled"))
    samples, errors = motion_library.discover_and_sample("source", source_clips={("CAB-real-file", "22")})
    assert samples == {} and len(errors) == 1
    assert errors[0]["reason"] == "missing_clip" and errors[0]["pathId"] == "22"


def test_generic_clip_is_never_faked_as_a_humanoid_pose(monkeypatch):
    import UnityPy
    monkeypatch.setattr(UnityPy, "load", lambda _: SimpleNamespace(objects=[_clip(11, type_id=4)]))
    monkeypatch.setattr(motion_library, "sample_frames", lambda _: pytest.fail("generic sampled as humanoid"))
    samples, errors = motion_library.discover_and_sample("source", source_clips={("CAB-real-file", "11")})
    assert not samples and errors[0]["reason"] == "unsupported"


def test_actor_plan_keeps_no_talk_and_pre_action_actor_relations(tmp_path):
    values = {
        "mysekaiCharacterTalkNoTalkMysekaiFixtureActions": [{"gameCharacterUnitId": 5, "mysekaiCharacterTalkFixtureTimelineGroupId": 9}],
        "mysekaiGameCharacterUnitGroups": [{"id": 2, "gameCharacterUnitId1": 1, "gameCharacterUnitId2": 3}],
        "mysekaiCharacterTalks": [{"id": 77, "mysekaiGameCharacterUnitGroupId": 2}],
        "mysekaiCharacterTalkPreActions": [{"mysekaiCharacterTalkId": 77, "mysekaiCharacterTalkFixtureTimelineGroupId": 10}],
    }
    for name, value in values.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    assert set(actors._source_relations(tmp_path)) == {(5, 9), (1, 10), (3, 10)}


def test_prefab_height_variants_do_not_cross_fixture_identity():
    variants = actors._prefab_names("tl_ext0013_fixture_bike1_ex01")
    assert variants == {"tl_ext0013_fixture_bike1_ex01", "tl_ext0013_fixture_bike1-ground_ex01", "tl_ext0013_fixture_bike1-low_ex01"}
    with pytest.raises(ValueError, match="timeline"):
        actors._template("arbitrary_id")


def test_actor_verified_staging_is_recoverable_and_tamper_detected(tmp_path):
    files = {"actor.glb": b"source geometry", "actor.index.json": b"source metadata"}
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    receipt = {"identity": "release", "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    (tmp_path / "export-source.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert actors._receipt_valid(tmp_path, "release")
    assert not actors._receipt_valid(tmp_path, "other release")
    (tmp_path / "actor.glb").write_bytes(b"truncated")
    assert not actors._receipt_valid(tmp_path, "release")


def test_product_rotation_sampler_preserves_step_and_short_arc():
    identity = (0., 0., 0., 1.)
    negated = (0., 0., 0., -1.)
    assert sd_switch.sample_rotation([0., 1.], [identity, negated], "STEP", 0.5) == identity
    assert sd_switch.sample_rotation([0., 1.], [identity, negated], "LINEAR", 0.5) == identity
    zero = (0., 0., 0., 0.)
    assert sd_switch.sample_rotation([0., 1.], [zero, identity, zero, zero, identity, zero], "CUBICSPLINE", 0.25) == identity
    with pytest.raises(ValueError):
        sd_switch.sample_rotation([0., 1.], [identity], "LINEAR", 0.5)
    with pytest.raises(ValueError):
        sd_switch._normal((float("nan"), 0., 0., 1.))


def test_product_switch_mapping_stays_rotation_only_and_explicit():
    assert len(sd_switch.PAIRS) == 14
    assert len(set(sd_switch.PAIRS.values())) == 14
    assert sd_switch.REST_ONLY not in sd_switch.PAIRS.values()
    assert all("Hand" not in path and "Finger" not in path for path in sd_switch.PAIRS.values())
    assert sd_switch.DURATION == 1.0000001192092896


def test_cut_scene_unit_tracks_join_the_actor_plan(tmp_path, monkeypatch):
    """"A cut-scene package the master names adds its unit-named animation
    tracks' clips to that unit's job; a non-numeric track and a unit without
    a character model do not.  Red when the relation is dropped, or when it
    binds a clip to a track it does not name."""
    root, master, bundles = (tmp_path / n for n in ("assets", "master", "bundles"))
    for directory in (root, master, bundles):
        directory.mkdir()
    package = "mysekai__cut_scene__tl_cutscene_invitation_001_001"
    pointer = lambda n: {"file": "CAB-cut", "pathId": str(n)}
    motion = "mysekai__character_motion"
    documents = {
        root / "manifest.json": {"units": [{"unit": 101}]},
        master / "mysekaiCharacterTalkFixtureTimelines.json": [],
        master / "mysekaiCutScenes.json": [{"id": 1, "externalId": 1,
                                             "timelineAssetbundleName": "tl_cutscene_invitation_001_001"}],
        root / "cutscene-timeline" / "tracks" / f"{package}.json": {
            "timelines": [{"asset": pointer(1), "tracks": [
                {"class": "AnimationTrack", "name": "1", "asset": pointer(2)},
                {"class": "AnimationTrack", "name": "Gate", "asset": pointer(3)},
                {"class": "GroupTrack", "name": "Others", "asset": pointer(4), "children": [
                    {"class": "AnimationTrack", "name": "7", "asset": pointer(5)}]}]}],
            "prefabs": [{"container": "tl_cutscene_invitation_001_001.prefab",
                         "directors": [{"timeline": pointer(1)}]}]},
        root / "cutscene-timeline" / "clip-targets" / f"{package}.json": {"keyedClips": [
            {"track": pointer(2), "target": {"sourceClip": {"file": "CAB-motion", "pathId": "11"},
                                           "targetPackage": motion, "clipName": "mov_a_L"}},
            {"track": pointer(3), "target": {"sourceClip": {"file": "CAB-gate", "pathId": "12"},
                                           "targetPackage": "mysekai__fixture__gate", "clipName": "gate_S"}},
            {"track": pointer(5), "target": {"sourceClip": {"file": "CAB-motion", "pathId": "13"},
                                           "targetPackage": motion, "clipName": "mov_b_L"}}]},
    }
    for path, document in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
    for name in ("mysekai__character__mdl_sd_101_001", motion):
        (bundles / name).write_bytes(b"source bundle identity")
    monkeypatch.setattr(actors, "_gltf", lambda _: {"nodes": [{"name": "Root"}], "scenes": [{"nodes": [0]}]})
    monkeypatch.setattr(actors, "_source_relations", lambda _: [])
    plan = actors.build_plan(root, master, bundles)
    assert [(job["unit"], job["package"]) for job in plan["jobs"]] == [(1, motion)]
    assert plan["jobs"][0]["clips"] == [{"name": "mov_a_L", "file": "CAB-motion", "pathId": "11"}]
    assert plan["jobs"][0]["relation"] == "cut_scene"
    assert plan["jobs"][0]["library"] == "cut-scene__" + motion
    assert {"unit": 7, "package": package,
            "reason": "character model is not in this source release"} in plan["gaps"]
    (master / "mysekaiCutScenes.json").unlink()
    assert actors.build_plan(root, master, bundles)["jobs"] == []


def test_cut_scene_library_does_not_repeat_a_furniture_clip(tmp_path, monkeypatch):
    """"A clip a unit's furniture job already carries stays in that library;
    the cut-scene library of the unit gets only the rest, and the furniture
    job's identity does not change when the cut-scene relation is added.
    Red when one source identity gets two routes or the furniture job moves."""
    root, master, bundles = (tmp_path / n for n in ("assets", "master", "bundles"))
    for directory in (root, master, bundles):
        directory.mkdir()
    motion = "mysekai__character_motion"
    fixture = "mysekai__fixture_timeline__mdl_ext0002_fixture_coffeemaker1"
    logical = "tl_ext0002_fixture_coffeemaker1_01"
    cut = "mysekai__cut_scene__tl_cutscene_returngate_001_001"
    pointer = lambda n: {"file": "CAB-t", "pathId": str(n)}
    shared = {"sourceClip": {"file": "CAB-motion", "pathId": "11"}, "targetPackage": motion, "clipName": "idle_L"}
    own = {"sourceClip": {"file": "CAB-motion", "pathId": "12"}, "targetPackage": motion, "clipName": "walk_L"}
    documents = {
        root / "manifest.json": {"units": [{"unit": 101}]},
        master / "mysekaiCharacterTalkFixtureTimelines.json": [{"groupId": 1, "assetbundleName": logical}],
        root / "fixture-timeline" / "tracks" / f"{fixture}.json": {
            "timelines": [{"asset": pointer(1), "tracks": [
                {"class": "AnimationTrack", "name": "CharacterAnimator", "asset": pointer(2)}]}],
            "prefabs": [{"container": logical + ".prefab", "directors": [{"timeline": pointer(1)}]}]},
        root / "fixture-timeline" / "clip-targets" / f"{fixture}.json": {"keyedClips": [
            {"track": pointer(2), "target": shared}]},
        root / "cutscene-timeline" / "tracks" / f"{cut}.json": {
            "timelines": [{"asset": pointer(3), "tracks": [
                {"class": "AnimationTrack", "name": "1", "asset": pointer(4)}]}],
            "prefabs": [{"container": "x.prefab", "directors": [{"timeline": pointer(3)}]}]},
        root / "cutscene-timeline" / "clip-targets" / f"{cut}.json": {"keyedClips": [
            {"track": pointer(4), "target": shared}, {"track": pointer(4), "target": own}]},
    }
    for path, document in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
    for name in ("mysekai__character__mdl_sd_101_001", motion):
        (bundles / name).write_bytes(b"source bundle identity")
    monkeypatch.setattr(actors, "_gltf", lambda _: {"nodes": [{"name": "Root"}], "scenes": [{"nodes": [0]}]})
    monkeypatch.setattr(actors, "_source_relations", lambda _: [(1, 1)])
    alone = actors.build_plan(root, master, bundles)
    (master / "mysekaiCutScenes.json").write_text(json.dumps(
        [{"id": 1, "externalId": 1, "timelineAssetbundleName": "tl_cutscene_returngate_001_001"}]),
        encoding="utf-8")
    both = actors.build_plan(root, master, bundles)
    assert [job.get("library", job["package"]) for job in both["jobs"]] == [motion, "cut-scene__" + motion]
    assert both["jobs"][0] == alone["jobs"][0]
    assert both["jobs"][1]["clips"] == [{"name": "walk_L", "file": "CAB-motion", "pathId": "12"}]
