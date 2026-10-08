"""Animation scope is a source relationship, never a clip-name heuristic."""
import sys
import zlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from phenomena import animations
from phenomena.effects import decode_effect
from phenomena.environments import _Phenomenon


class Record:
    bundle = "package"
    archive = "archive"
    externals = []

    def __init__(self, objects):
        self.kinds = {pid: kind for pid, (kind, tree) in objects.items()}
        self.trees = {pid: tree for pid, (kind, tree) in objects.items()}

    def tree(self, pid):
        return self.trees[pid]


def pointer(pid):
    return {"m_FileID": 0, "m_PathID": pid}


def scene():
    def go(name, transform, components=()):
        return ("GameObject", {"m_Name": name, "m_IsActive": True,
            "m_Component": [{"component": pointer(pid)} for pid in (transform, *components)]})
    def transform(owner, parent):
        return ("Transform", {"m_GameObject": pointer(owner), "m_Father": pointer(parent),
            "m_LocalPosition": {"x": 0., "y": 0., "z": 0.},
            "m_LocalRotation": {"x": 0., "y": 0., "z": 0., "w": 1.},
            "m_LocalScale": {"x": 1., "y": 1., "z": 1.}})
    return {1: go("same-name", 2), 2: transform(1, 0),
            3: go("moving", 4), 4: transform(3, 2),
            11: go("same-name", 12), 12: transform(11, 0),
            13: go("other", 14), 14: transform(13, 12)}


def clip():
    return {"m_Name": "name-is-not-a-binding", "m_Legacy": False, "m_SampleRate": 60.,
        "m_WrapMode": 0, "m_MuscleClip": {"m_StartTime": 0., "m_StopTime": 10., "m_LoopTime": True},
        "m_ClipBindingConstant": {"genericBindings": [{"typeID": 4, "attribute": 4,
            "path": zlib.crc32(b"moving"), "isPPtrCurve": 0, "isIntCurve": 0,
            "isSerializeReferenceCurve": 0, "customType": 4, "script": pointer(0)}]}}


def setup(monkeypatch, objects, contents):
    record = Record(objects)
    store = SimpleNamespace(follow=lambda owner, p: (owner, p["m_PathID"])
        if p and p.get("m_FileID", 0) == 0 and p.get("m_PathID") in owner.kinds else None)
    package = SimpleNamespace(name="package", files=[record],
        contents=[(asset, record, pid) for asset, pid in contents])
    monkeypatch.setattr(animations, "decode", lambda tree: {
        0: ("const", [(0., 0.)]), 1: ("cubic", [(0., [0., 0., -36., 0.]),
                                                   (10., [0., 0., 0., -360.])]),
        2: ("const", [(0., 0.)])})
    return store, package, record


def test_model_binding_uses_exact_container_owner_without_inventing_animator(monkeypatch):
    objects = scene(); objects[50] = ("AnimationClip", clip())
    store, package, _ = setup(monkeypatch, objects,
        [("correct.fbx", 1), ("wrong.fbx", 11), ("correct.fbx", 50)])
    result = animations.decode_package(store, package, "common")
    assert not result["animators"] and not result["controllers"]
    assert result["unsupported"] == []
    value = result["clips"][0]
    assert value["accounting"] == {"bindingSlots": 3, "decodedSlots": 3, "unresolved": []}
    assert all(curve["targets"][0]["scopeKind"] == "modelAsset" for curve in value["curves"])
    assert all(curve["targets"][0]["target"]["pathId"] == "4" for curve in value["curves"])
    assert value["curves"][1]["points"][0][1] == [0., 0., -36., 0.]


def test_matching_display_name_does_not_resolve_a_missing_container_edge(monkeypatch):
    objects = scene(); objects[50] = ("AnimationClip", clip())
    store, package, _ = setup(monkeypatch, objects,
        [("right-name.fbx", 1), ("unrelated.anim", 50)])
    result = animations.decode_package(store, package, "common")
    assert not result["modelScopes"]
    assert len(result["clips"][0]["accounting"]["unresolved"]) == 3


def test_animator_scope_and_empty_director_preserve_real_clock_and_owner(monkeypatch):
    objects = scene(); objects[1][1]["m_Component"].extend(
        [{"component": pointer(30)}, {"component": pointer(40)}])
    objects[30] = ("Animator", {"m_GameObject": pointer(1), "m_Enabled": 1,
        "m_UpdateMode": 0, "m_CullingMode": 0, "m_Controller": pointer(31)})
    objects[31] = ("AnimatorController", {"m_AnimationClips": [pointer(50)]})
    objects[40] = ("PlayableDirector", {"m_GameObject": pointer(1), "m_Enabled": 1,
        "m_DirectorUpdateMode": 1, "m_InitialTime": 0., "m_PlayableAsset": pointer(0),
        "m_SceneBindings": [{"key": pointer(0), "value": pointer(0)}]})
    objects[50] = ("AnimationClip", clip())
    monkeypatch.setattr(animations, "_controller", lambda *args: {"status": "decoded"})
    store, package, record = setup(monkeypatch, objects, [("fx.prefab", 1), ("clip.anim", 50)])
    result = animations.decode_package(store, package, "global")
    assert result["unsupported"] == []
    assert result["clips"][0]["curves"][1]["targets"][0]["scopeKind"] == "animator"
    director = result["directors"][0]
    assert director["playableAsset"]["target"] is None
    assert director["playableAsset"]["pointer"]["pathId"] == "0"
    assert director["fields"]["m_DirectorUpdateMode"] == 1
    assert director["sceneBindings"][0]["key"]["target"] is None
    refs = {int(row["source"]["pathId"]): {"source": row["source"], "document": "x/animations.json"}
            for row in [*result["animators"], *result["directors"]]}
    effect, gaps = decode_effect(1, record.kinds, record.trees, lambda *args: None,
                                resolve_animation=refs.get)
    assert not gaps
    assert [row["component"] for row in effect["animationComponents"]] == ["Animator", "PlayableDirector"]


def test_ambiguous_container_roots_are_not_resolved_by_order(monkeypatch):
    objects = scene(); objects[50] = ("AnimationClip", clip())
    store, package, _ = setup(monkeypatch, objects,
        [("model.fbx", 1), ("model.fbx", 11), ("model.fbx", 50)])
    result = animations.decode_package(store, package, "common")
    assert result["unsupported"][0]["reason"] == "model animation container has ambiguous GameObject roots"
    assert not result["modelScopes"]


def test_formal_phenomenon_document_links_effect_components_to_animation_artifact(monkeypatch, tmp_path):
    objects = scene(); objects[1][1]["m_Component"].append({"component": pointer(30)})
    objects[30] = ("Animator", {"m_GameObject": pointer(1), "m_Enabled": 1,
        "m_UpdateMode": 0, "m_CullingMode": 0, "m_Controller": pointer(31)})
    objects[31] = ("AnimatorController", {"m_AnimationClips": [pointer(50)]})
    objects[50] = ("AnimationClip", clip())
    monkeypatch.setattr(animations, "_controller", lambda *args: {"status": "decoded"})
    store, package, record = setup(monkeypatch, objects,
        [("fx_env_sky_x.prefab", 1), ("clip.anim", 50)])
    store.package = lambda name: package
    record.script_of = lambda pid: None
    phenomenon = _Phenomenon("x", tmp_path, store, None)
    phenomenon.variant("global", "package")
    entry = phenomenon.finish()
    document = json.loads((tmp_path / entry["animations"]["file"]).read_text())
    effect = json.loads((tmp_path / entry["fx"]["file"]).read_text())["effects"]["fx_env_sky_x"]
    assert document["summary"]["clips"] == 1 and document["summary"]["curveSlots"] == 3
    assert effect["animationSchemaVersion"] == 1
    assert effect["animationComponents"][0]["document"] == entry["animations"]["file"]
    assert effect["animationComponents"][0]["source"] == document["packages"][0]["animators"][0]["source"]
    assert not [gap for gap in phenomenon.unsupported if gap.get("component") == "Animator"]


def test_additive_pose_and_loop_inputs_preserve_authored_empty_false_zero_and_missing(monkeypatch):
    source = clip()
    source["m_MuscleClip"].update({
        "m_ValueArrayDelta": [{"m_Start": -0.0, "m_Stop": -360.0}],
        "m_ValueArrayReferencePose": [], "m_LoopBlend": False,
        "m_LoopBlendOrientation": False, "m_LoopBlendPositionY": True,
        "m_CycleOffset": 0.0, "m_StartAtOrigin": True,
        "m_KeepOriginalPositionY": False, "m_HeightFromFeet": False,
        "m_StartX": {"t": {"x": 0.0}, "q": {"y": 0.5, "w": 0.5}},
    })
    objects = scene(); objects[50] = ("AnimationClip", source)
    store, package, _ = setup(monkeypatch, objects, [("model.fbx", 1), ("model.fbx", 50)])
    value = animations.decode_package(store, package, "common")["clips"][0]["muscleClip"]
    assert value["m_ValueArrayReferencePose"] == []
    assert value["m_ValueArrayDelta"] == source["m_MuscleClip"]["m_ValueArrayDelta"]
    assert math.copysign(1, value["m_ValueArrayDelta"][0]["m_Start"]) == -1
    assert value["m_LoopBlend"] is False and value["m_LoopBlendOrientation"] is False
    assert value["m_LoopBlendPositionY"] is True and value["m_StartAtOrigin"] is True
    assert value["m_KeepOriginalPositionY"] is False and value["m_HeightFromFeet"] is False
    assert value["m_CycleOffset"] == 0.0
    assert value["m_StartX"] == source["m_MuscleClip"]["m_StartX"]
    assert value["m_LoopBlendPositionXZ"] is None and value["m_StopX"] is None
    assert value["m_Mirror"] is None


def test_nonempty_additive_reference_pose_is_not_replaced_by_first_curve_key(monkeypatch):
    source = clip()
    source["m_MuscleClip"].update({"m_ValueArrayReferencePose": [12.5, -17.0, 3.0],
        "m_ValueArrayDelta": [], "m_CycleOffset": 0.375,
        "m_DeltaPose": {"m_RootX": {"q": {"x": 0., "y": .5, "z": 0., "w": .5}}}})
    objects = scene(); objects[50] = ("AnimationClip", source)
    store, package, _ = setup(monkeypatch, objects, [("model.fbx", 1), ("model.fbx", 50)])
    value = animations.decode_package(store, package, "common")["clips"][0]["muscleClip"]
    assert value["m_ValueArrayReferencePose"] == [12.5, -17.0, 3.0]
    assert value["m_ValueArrayDelta"] == []
    assert value["m_CycleOffset"] == 0.375
    assert value["m_DeltaPose"] == source["m_MuscleClip"]["m_DeltaPose"]


def test_missing_additive_pose_inputs_are_null_not_empty_or_defaulted(monkeypatch):
    objects = scene(); objects[50] = ("AnimationClip", clip())
    store, package, _ = setup(monkeypatch, objects, [("model.fbx", 1), ("model.fbx", 50)])
    value = animations.decode_package(store, package, "common")["clips"][0]["muscleClip"]
    assert value["m_LoopTime"] is True
    assert all(item is None for key, item in value.items() if key != "m_LoopTime")
