"""Tests for the dialogue camera asset reader (``src.perf.camera``).

The reader's job is to pull real numbers out of a serialised camera asset, so
the tests exercise the shape of what it reads — seven curves with their keys,
and a ``CameraSetting.distance`` that must come from the asset.  The synthetic
fixtures below build *code*, not game semantics; they exist to make a reader
misbehave in a reproducible way, not to say what the game's camera is.

Two of the tests guard the two failure modes the work order names.  A curve is
expected key by key, so a reader that collapsed it to a bare count fails the
strict key-content assertion; and a ``distance`` that is absent is expected to
be reported as absent, so a reader that silently filled in a constructor
default fails.  The fake bundle is built the same way the other perf domains
build theirs: objects answer ``type.name``, ``path_id``, ``assets_file`` and
``read_typetree()``, and ``UnityPy.load`` is replaced, so no real bundle opens.

The fixtures are synthetic and must never be read back as if they described the
game's camera; they only exercise the reader's code.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from perf import camera


# -- synthetic packages -------------------------------------------------------


class _Object:
    def __init__(self, kind, path_id, tree, archive):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = archive

    def read_typetree(self):
        return self._tree


class _Bundle:
    """One fake serialized file: objects sharing one archive file."""

    def __init__(self):
        self.name = "CAB-camera"
        self.externals = []
        self.objects = []
        self._next = 100

    def add(self, kind, tree, path_id=None):
        if path_id is None:
            path_id = self._next
            self._next += 1
        self.objects.append(_Object(kind, path_id, tree, self))
        return path_id


def _key(time, value, slope=0.0):
    return {"time": time, "value": value, "inSlope": slope, "outSlope": slope,
            "weightedMode": 0, "inWeight": 0.0, "outWeight": 0.0}


def _curve(keys, pre=2, post=2, order=4):
    return {"m_Curve": keys, "m_PreInfinity": pre, "m_PostInfinity": post,
            "m_RotationOrder": order}


def _camera_bundle(setting_with_distance=True, distance=8.0,
                   param_curve_fields=None, param_has_keys=True):
    """One camera-shaped bundle: one CameraParam, one CameraSetting.

    *setting_with_distance* controls whether the setting carries ``distance``
    at all — that is the field the reader must read, so omitting it is the
    fixture for the "must not be silently defaulted" guard.  *param_curve_fields*
    lets a test drop a curve field to trip the completeness check, and
    *param_has_keys* lets a test hand a field an empty or missing ``m_Curve``.
    """
    bundle = _Bundle()
    setting_script = bundle.add("MonoScript", {"m_ClassName": "CameraSetting"})
    param_script = bundle.add("MonoScript", {"m_ClassName": "CameraParam"})

    setting_tree = {
        "m_Name": "FieldCameraSetting",
        "m_Script": {"m_FileID": 0, "m_PathID": setting_script},
        "offset": {"x": 0.0, "y": 0.5, "z": 0.0},
        "minDistance": 1.7, "maxDistance": 8.0, "fov": 35.0,
    }
    if setting_with_distance:
        setting_tree["distance"] = distance
    setting_pid = bundle.add("MonoBehaviour", setting_tree)

    fields = param_curve_fields if param_curve_fields is not None else list(camera.CURVE_FIELDS)
    curves = {}
    for field in fields:
        keys = [_key(0.0, 1.0), _key(1.0, 2.0)] if param_has_keys else []
        curves[field] = _curve(keys)
    param_tree = {"m_Name": "CameraParam",
                  "m_Script": {"m_FileID": 0, "m_PathID": param_script}, **curves}
    param_pid = bundle.add("MonoBehaviour", param_tree)

    bundle.add("AssetBundle", {
        "m_Container": [
            ("assets/x/cameraparam.asset",
             {"asset": {"m_FileID": 0, "m_PathID": param_pid}}),
            ("assets/x/fieldcamerasetting.asset",
             {"asset": {"m_FileID": 0, "m_PathID": setting_pid}}),
        ]})
    return bundle


def _read(tmp_path, monkeypatch, bundle, name="mysekai__camera__synth"):
    out = tmp_path / "out" / "camera"
    bundle_path = tmp_path / "bundles" / name
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(packages_module.UnityPy, "load", lambda path: bundle)
    report = camera.read_camera_assets([str(bundle_path)], str(out))
    return report, report["packages"][0], out


def _load_doc(out, name="mysekai__camera__synth"):
    return json.loads((out / f"{name}.json").read_text(encoding="utf-8"))


# -- the classes are found, and every instance is exported ---------------------


def test_both_classes_are_found_and_every_instance_exported(tmp_path, monkeypatch):
    """Both classes survive: one CameraParam and one CameraSetting are found,
    and both land in the product document rather than the first one winning.
    Red when either class is dropped, or when a second instance is skipped."""
    report, package, out = _read(
        tmp_path, monkeypatch, _camera_bundle())
    assert package["cameraParam"] == 1
    assert package["cameraSetting"] == 1
    doc = _load_doc(out)
    assert len(doc["cameraParams"]) == 1
    assert len(doc["cameraSettings"]) == 1
    assert doc["cameraSettings"][0]["container"].endswith(
        "fieldcamerasetting.asset")
    assert doc["cameraParams"][0]["container"].endswith("cameraparam.asset")


def test_a_second_instance_is_exported_not_overwritten(tmp_path, monkeypatch):
    """Red when the reader keeps only the first of several instances: a package
    with two CameraSettings must report two, each with its own distance."""
    bundle = _camera_bundle()
    # add a second CameraSetting by cloning the first at a new path id
    second_pid = _add_second_setting(bundle)
    _, package, _ = _read(tmp_path, monkeypatch, bundle)
    assert package["cameraSetting"] == 2
    assert sorted(package["distance"]) == [3.7, 8.0]


def _add_second_setting(bundle):
    tree = {
        "m_Name": "FieldCameraSetting2",
        "m_Script": {"m_FileID": 0, "m_PathID": _setting_script_id(bundle)},
        "offset": {"x": 0.0, "y": 1.0, "z": 0.0},
        "minDistance": 1.7, "maxDistance": 5.0, "fov": 50.0,
        "distance": 3.7,
    }
    return bundle.add("MonoBehaviour", tree)


def _setting_script_id(bundle):
    for obj in bundle.objects:
        if obj.type.name == "MonoScript" and obj.read_typetree().get("m_ClassName") == "CameraSetting":
            return obj.path_id
    raise AssertionError("no CameraSetting script")


# -- the seven curves are exported key by key, not as a count ------------------


def test_curves_are_exported_key_by_key_not_as_a_count(tmp_path, monkeypatch):
    """Red when a curve is collapsed to a bare key count: the reader must keep
    every key's full contents, so a reader that emitted ``keys: N`` fails the
    value-bearing assertion below."""
    _, package, out = _read(tmp_path, monkeypatch, _camera_bundle())
    doc = _load_doc(out)
    curves = doc["cameraParams"][0]["curves"]
    assert set(curves) == set(camera.CURVE_FIELDS)
    for field, curve in curves.items():
        assert isinstance(curve["keys"], list)
        for key in curve["keys"]:
            assert set(key) == set(camera.KEYFRAME_KEYS)
    # a specific value must survive, not merely some key-typed object
    assert curves["_distance"]["keys"][0]["value"] == 1.0
    assert curves["_distance"]["keys"][1]["time"] == 1.0
    assert package["keyframes"]["_distance"] == 2


# -- distance must be read from the asset, never silently defaulted -------------


def test_distance_is_read_when_present(tmp_path, monkeypatch):
    """The real reason this reader exists: when the asset stores a distance,
    that value is read and reported, not a constructor default.  Red when the
    value is dropped or replaced."""
    _, package, out = _read(tmp_path, monkeypatch, _camera_bundle(distance=8.0))
    doc = _load_doc(out)
    assert package["distance"] == [8.0]
    assert doc["cameraSettings"][0]["distancePresent"] is True
    assert doc["cameraSettings"][0]["distance"] == 8.0


def test_distance_absent_is_reported_not_defaulted(tmp_path, monkeypatch):
    """Red when a missing ``distance`` is silently filled with a default: the
    reader must say the field is absent, because an absent field would falsify
    the whole "the asset must be read" premise.  A default value here hides a
    real problem and must never be produced."""
    _, package, out = _read(
        tmp_path, monkeypatch, _camera_bundle(setting_with_distance=False))
    doc = _load_doc(out)
    assert package["distanceMissing"] == 1
    assert package["distance"] == []
    assert doc["cameraSettings"][0]["distancePresent"] is False
    assert doc["cameraSettings"][0]["distance"] is None


# -- a curve with no keys is empty, not dropped --------------------------------


def test_empty_curve_is_reported_not_dropped(tmp_path, monkeypatch):
    """An ``AnimationCurve`` with no keys is an empty curve and must survive as
    such: the completeness check must still see the field, and the keyframe
    count for it is zero without losing the field or the instance."""
    _, package, out = _read(tmp_path, monkeypatch, _camera_bundle())
    doc = _load_doc(out)
    field = camera.CURVE_FIELDS[0]
    assert set(doc["cameraParams"][0]["curves"]) == set(camera.CURVE_FIELDS)
    assert package["keyframes"].get(field, 0) >= 0
    assert all(isinstance(curve["keys"], list)
               for curve in doc["cameraParams"][0]["curves"].values())


def test_missing_path_is_reported_never_empty(tmp_path):
    """Red when a missing package path is read silently: UnityPy yields no
    objects for a path that does not exist, so the reader must say the package
    is missing rather than claim it holds zero camera assets."""
    missing = str(tmp_path / "bundles" / "mysekai__camera__gone")
    report = camera.read_camera_assets([missing], str(tmp_path / "out"))
    assert report["missing"] == ["mysekai__camera__gone"]
    assert report["packages"] == [{"package": "mysekai__camera__gone",
                                   "missing": True}]
    assert list((Path(tmp_path / "out")).glob("*.json")) == []


# -- timeline-owned Cinemachine components ------------------------------------


def _cinemachine_bundle():
    """One synthetic instance of each timeline-owned Cinemachine class."""
    bundle = _Bundle()
    scripts = {
        name: bundle.add("MonoScript", {"m_ClassName": name})
        for name in (
            "CinemachineVirtualCamera", "CinemachinePipeline",
            "CinemachineTransposer", "CinemachineComposer")
    }
    bundle.add("MonoBehaviour", {
        "m_Name": "ShotCamera",
        "m_Script": {"m_FileID": 0, "m_PathID": scripts["CinemachineVirtualCamera"]},
        "m_LegacyBlendHint": 2,
        "m_Lens": {
            "Dutch": 3.5, "FarClipPlane": 500.0, "FieldOfView": 42.0,
            "FocusDistance": 7.0, "GateFit": 1,
            "LensShift": {"x": 0.1, "y": -0.2}, "ModeOverride": 0,
            "NearClipPlane": 0.03, "OrthographicSize": 5.0,
            "m_SensorSize": {"x": 36.0, "y": 24.0},
        },
        "m_Priority": 20, "m_StandbyUpdate": 1,
        "m_ComponentOwner": {"m_FileID": 0, "m_PathID": 700},
        "m_Follow": {"m_FileID": 0, "m_PathID": 701},
        "m_LookAt": {"m_FileID": 0, "m_PathID": 702},
        "m_ExcludedPropertiesInInspector": ["m_Priority"],
        "m_LockStageInInspector": 1,
        "m_StreamingVersion": 13,
    })
    bundle.add("MonoBehaviour", {
        "m_Name": "FollowRig",
        "m_Script": {"m_FileID": 0, "m_PathID": scripts["CinemachineTransposer"]},
        "m_AngularDamping": 0.5, "m_AngularDampingMode": 1,
        "m_BindingMode": 2, "m_FollowOffset": {"x": 1.0, "y": 2.0, "z": -3.0},
        "m_PitchDamping": 0.6, "m_RollDamping": 0.7,
        "m_XDamping": 0.8, "m_YDamping": 0.9, "m_YawDamping": 1.0,
        "m_ZDamping": 1.1,
    })
    bundle.add("MonoBehaviour", {
        "m_Name": "ComposerRig",
        "m_Script": {"m_FileID": 0, "m_PathID": scripts["CinemachineComposer"]},
        "m_BiasX": 0.1, "m_BiasY": -0.1, "m_CenterOnActivate": 1,
        "m_DeadZoneHeight": 0.2, "m_DeadZoneWidth": 0.3,
        "m_HorizontalDamping": 0.4, "m_LookaheadIgnoreY": 1,
        "m_LookaheadSmoothing": 0.5, "m_LookaheadTime": 0.6,
        "m_ScreenX": 0.7, "m_ScreenY": 0.8,
        "m_SoftZoneHeight": 0.9, "m_SoftZoneWidth": 1.0,
        "m_TrackedObjectOffset": {"x": 1.1, "y": 1.2, "z": 1.3},
        "m_VerticalDamping": 1.4,
    })
    bundle.add("MonoBehaviour", {
        "m_Name": "PipelineRig",
        "m_Script": {"m_FileID": 0, "m_PathID": scripts["CinemachinePipeline"]},
    })
    return bundle


def test_cinemachine_components_export_values_pointers_and_editor_fields(
        tmp_path, monkeypatch):
    """Every requested Cinemachine class keeps values in its own record."""
    _, package, out = _read(tmp_path, monkeypatch, _cinemachine_bundle())
    doc = _load_doc(out)

    assert len(doc["cinemachineVirtualCameras"]) == 1
    assert len(doc["cinemachinePipelines"]) == 1
    assert len(doc["cinemachineTransposers"]) == 1
    assert len(doc["cinemachineComposers"]) == 1

    virtual = doc["cinemachineVirtualCameras"][0]
    assert set(virtual["fields"]) == {
        "m_LegacyBlendHint", "m_Lens", "m_Name", "m_Priority",
        "m_StandbyUpdate"}
    assert virtual["fields"]["m_LegacyBlendHint"] == 2
    assert virtual["fields"]["m_Lens"]["FieldOfView"] == 42.0
    assert virtual["fields"]["m_Lens"]["m_SensorSize"] == {
        "x": 36.0, "y": 24.0}
    assert virtual["fields"]["m_Priority"] == 20
    assert virtual["pointers"]["m_Follow"]["m_PathID"] == 701
    assert set(virtual["editorOnly"]) == {
        "m_ExcludedPropertiesInInspector", "m_LockStageInInspector",
        "m_StreamingVersion"}
    assert "m_Follow" not in virtual["fields"]
    assert "m_StreamingVersion" not in virtual["fields"]

    transposer = doc["cinemachineTransposers"][0]
    assert set(transposer["fields"]) == {
        "m_AngularDamping", "m_AngularDampingMode", "m_BindingMode",
        "m_FollowOffset", "m_Name", "m_PitchDamping", "m_RollDamping",
        "m_XDamping", "m_YDamping", "m_YawDamping", "m_ZDamping"}
    assert transposer["fields"]["m_FollowOffset"] == {
        "x": 1.0, "y": 2.0, "z": -3.0}
    assert transposer["fields"]["m_ZDamping"] == 1.1
    composer = doc["cinemachineComposers"][0]
    assert set(composer["fields"]) == {
        "m_BiasX", "m_BiasY", "m_CenterOnActivate", "m_DeadZoneHeight",
        "m_DeadZoneWidth", "m_HorizontalDamping", "m_LookaheadIgnoreY",
        "m_LookaheadSmoothing", "m_LookaheadTime", "m_Name", "m_ScreenX",
        "m_ScreenY", "m_SoftZoneHeight", "m_SoftZoneWidth",
        "m_TrackedObjectOffset", "m_VerticalDamping"}
    assert composer["fields"]["m_ScreenX"] == 0.7
    assert composer["fields"]["m_TrackedObjectOffset"]["z"] == 1.3
    assert composer["fields"]["m_VerticalDamping"] == 1.4
    assert doc["cinemachinePipelines"][0]["fields"] == {
        "m_Name": "PipelineRig"}

    assert package["cinemachine"] == {
        "CinemachineComposer": {"total": 1, "nonEmptyNames": 1},
        "CinemachinePipeline": {"total": 1, "nonEmptyNames": 1},
        "CinemachineTransposer": {"total": 1, "nonEmptyNames": 1},
        "CinemachineVirtualCamera": {"total": 1, "nonEmptyNames": 1},
    }
    assert package["editorOnly"]["m_StreamingVersion"]["reason"] == (
        "is a serialized Cinemachine/editor version marker rather than runtime camera behavior")
    assert package["editorOnly"]["m_StreamingVersion"]["instances"] == 1
