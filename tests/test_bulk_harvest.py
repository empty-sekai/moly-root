"""Runtime bindings must remain explicit and source-qualified."""
from types import SimpleNamespace

import pytest

from core.gltf import GLB
from sites import bulk_harvest as bulk
from test_fixture_animations import _binding, _clip_tree


def _identity(pid):
    return {"file": "CAB-synthetic", "pathId": str(pid)}


def _tables():
    names = ["CharacterAnimator", "Fixture1", "camera"]
    tracks = [{"class": "AnimationTrack", "name": name, "asset": _identity(i)}
              for i, name in enumerate(names, 1)]
    source = {"timelines": [{"asset": _identity(9), "tracks": tracks}]}
    targets = {"keyedClips": [{"track": t["asset"], "clipIndex": 0,
        "target": {"sourceClip": _identity(20+i), "clipName": t["name"]+"_clip",
                   "targetPackage": "external" if i == 1 else bulk.PACKAGE}}
        for i, t in enumerate(tracks)]}
    director = {"timeline": _identity(9), "sceneBindings": [
        {"key": t["asset"], "value": None} for t in tracks]}
    return source, targets, director


def test_bulk_streams_keep_the_declared_clip_package():
    source, targets, director = _tables()
    selected = bulk.selected_tracks(source, targets, director, bulk.PACKAGE)
    assert selected["Fixture1"][0]["sourcePackage"] == "external"
    assert selected["Fixture1"][0]["sourceClip"] == _identity(21)
    director["sceneBindings"][1]["value"] = _identity(80)
    with pytest.raises(ValueError, match="scene binding"):
        bulk.selected_tracks(source, targets, director, bulk.PACKAGE)


def test_bulk_streams_refuse_unmodelled_or_missing_bindings():
    source, targets, director = _tables()
    source["timelines"][0]["tracks"][2]["name"] = "second-camera"
    with pytest.raises(ValueError, match="unsupported bulk"):
        bulk.selected_tracks(source, targets, director, bulk.PACKAGE)
    source, targets, director = _tables()
    targets["keyedClips"][1]["target"].pop("sourceClip")
    with pytest.raises(ValueError, match="unresolved clip"):
        bulk.selected_tracks(source, targets, director, bulk.PACKAGE)


def test_camera_proxy_is_separate_and_has_no_source_object_identity():
    glb = GLB()
    glb.g["nodes"] = [{"name": "source-root"}]
    glb.g["scenes"][0]["nodes"] = [0]
    tree = _clip_tree("camera", [_binding("", 1)], constant=[2.0, 3.0, 4.0])
    tree["m_MuscleClip"]["m_StopTime"] = 1.5
    records = SimpleNamespace(lookup=lambda _: (SimpleNamespace(tree=lambda _: tree), 1))
    clips = [{"name": "camera", "sourceClip": _identity(1)}]
    bulk.append_camera(glb, records, clips)
    assert glb.g["scenes"][0]["nodes"] == [0, 1]
    assert glb.g["nodes"][1]["name"] == bulk.CAMERA_NODE
    extras = glb.g["nodes"][1]["extras"]
    assert "sourceObject" not in extras
    assert extras["exportedProxy"]["cameraForwardCorrection"] == "runtime-y-pi"
    assert clips[0]["sourceStopTime"] == 1.5
    assert glb.g["animations"][0]["channels"][0]["target"]["node"] == 1
    with pytest.raises(ValueError, match="already exists"):
        bulk.append_camera(glb, records, clips)


def test_camera_rejects_a_non_root_curve():
    glb = GLB()
    tree = _clip_tree("camera", [_binding("unbound", 1)], constant=[0.0, 0.0, 0.0])
    records = SimpleNamespace(lookup=lambda _: (SimpleNamespace(tree=lambda _: tree), 1))
    with pytest.raises(ValueError, match="unsupported bindings"):
        bulk.append_camera(glb, records, [{"name": "camera", "sourceClip": _identity(1)}])
