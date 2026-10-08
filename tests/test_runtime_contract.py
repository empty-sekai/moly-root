import json

import pytest

from core.runtime_contract import publish_runtime_aliases, validate_camera


def camera():
    return {"fieldCameras": [{"class": "FieldCamera", "setting": {"file": "jp", "pathId": "1"},
                              "cameraParam": {"file": "jp", "pathId": "2"},
                              "view": {"orthographic": False, "nearClipPlane": .1, "farClipPlane": 100.}}],
            "cameraSettings": [{"asset": {"file": "jp", "pathId": "1"}}],
            "cameraParams": [{"asset": {"file": "jp", "pathId": "2"}}]}


def test_aliases_preserve_exact_same_region_source_bytes(tmp_path):
    documents = {"camera/mysekai__camera.json": camera(),
                 "fixture-interface/attach-points.json": {"packages": {"jp-source": {}}}}
    for name, value in documents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    report = publish_runtime_aliases(tmp_path)
    for row in report["aliases"]:
        assert (tmp_path / row["source"]).read_bytes() == (tmp_path / row["runtimePath"]).read_bytes()


def test_camera_contract_refuses_unresolved_source_and_invalid_planes():
    value = camera()
    value["cameraSettings"] = []
    with pytest.raises(ValueError, match="unresolved"):
        validate_camera(value)
    value = camera()
    value["fieldCameras"][0]["view"]["farClipPlane"] = .01
    with pytest.raises(ValueError, match="projection"):
        validate_camera(value)
