"""Validator rejects relabelled raw assets and makes partial scope explicit."""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.gltf import GLB

spec = importlib.util.spec_from_file_location(
    "coordinate_validator", Path(__file__).resolve().parents[1] / "tools/validate_coordinate_contract.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def _extras():
    return {"coordinateContract": validator.CONTRACT, "coordinateUnits": validator.UNITS}


def _glb(path, translation=-2.0, marked=True):
    glb = GLB()
    extras = _extras() if marked else {}
    extras["sourceTransform"] = {"coordinateSpace": validator.SOURCE,
        "translation": {"x": 2., "y": 0., "z": 1.},
        "rotation": {"x": 0., "y": .6, "z": 0., "w": .8},
        "scale": {"x": 1., "y": 1., "z": 1.}}
    glb.g["nodes"] = [{"translation": [translation, 0., 1.],
                        "rotation": [0., -.6, 0., .8], "scale": [1., 1., 1.],
                        "extras": extras}]
    glb.g["scenes"] = [{"nodes": [0]}]
    glb.save(path)


def test_numeric_source_evidence_rejects_blind_relabelling(tmp_path):
    good, raw = tmp_path / "good.glb", tmp_path / "raw.glb"
    _glb(good)
    _glb(raw, translation=2.0)
    assert validator.validate_glb(good)["errors"] == []
    errors = validator.validate_glb(raw)["errors"]
    assert any("translation does not match source conversion" in value for value in errors)


def test_legacy_root_is_rejected_even_when_asset_header_is_tagged(tmp_path):
    path = tmp_path / "legacy.glb"
    _glb(path)
    document = validator.read_glb(path)
    document["nodes"][0]["extras"].pop("coordinateContract")
    document["nodes"][0]["extras"].pop("coordinateUnits")
    # Hand-pack a legacy/partial document: the canonical GLB writer would
    # install root metadata, so going through it cannot simulate this defect.
    import struct
    raw = json.dumps(document).encode()
    raw += b" " * (-len(raw) % 4)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 20 + len(raw))
                     + struct.pack("<I4s", len(raw), b"JSON") + raw)
    assert any("root[0]" in value for value in validator.validate_glb(path)["errors"])


def test_attach_rotation_and_scale_are_checked_as_well_as_position(tmp_path):
    path = tmp_path / "attach.json"
    document = {"version": 2, **_extras(), "packages": {"p": {"entries": [{"id": "1", "start": {
        "transform": {"coordinateSpace": validator.CONTRACT, "position": [-1., 2., 3.],
                      "rotation": [0., .6, 0., .8], "scale": [2., 1., 1.]},
        "sourceTransform": {"coordinateSpace": validator.SOURCE, "position": [1., 2., 3.],
                            "rotation": [0., .6, 0., .8], "scale": [1., 1., 1.]}}}]}}}
    path.write_text(json.dumps(document), encoding="utf-8")
    errors = validator.validate_attach(path)["errors"]
    assert any("rotation does not match" in value for value in errors)
    assert any("scale does not match" in value for value in errors)


def test_obstacle_centers_are_reflected_but_extents_are_not(tmp_path):
    path = tmp_path / "obstacles.json"
    center, extents = {"x": 2., "y": 3., "z": 4.}, {"x": .2, "y": .3, "z": .4}
    row = {"schemaVersion": 2, **_extras(), "center": {**center, "x": -2.}, "extents": extents,
           "sourceGeometry": {"coordinateSpace": validator.SOURCE, "center": center, "extents": extents}}
    path.write_text(json.dumps({"navMeshObstacles": [row]}), encoding="utf-8")
    assert validator.validate_json_subcontract(path, "navMeshObstacles")["errors"] == []
    row["extents"] = {**extents, "x": -.2}
    path.write_text(json.dumps({"navMeshObstacles": [row]}), encoding="utf-8")
    assert validator.validate_json_subcontract(path, "navMeshObstacles")["errors"]


def _snapshot(root):
    fixture = root / "fixture-models"
    attach = root / "fixture-attach"
    fixture.mkdir()
    attach.mkdir()
    _glb(fixture / "fixture.glb")
    (fixture / "index.json").write_text(json.dumps({"version": 3, **_extras(), "packages": {
        "fixture": {"status": "exported", "coordinateContract": validator.CONTRACT,
                    "glb": "fixture.glb"}}}), encoding="utf-8")
    (attach / "attach-points.json").write_text(json.dumps({"version": 2, **_extras(),
                                                        "packages": {}}), encoding="utf-8")


def test_snapshot_checks_non_fixture_domains_and_never_rewrites(tmp_path):
    _snapshot(tmp_path)
    actor = tmp_path / "actor.glb"
    _glb(actor, translation=2.)
    before = actor.read_bytes()
    report = validator.validate_snapshot(tmp_path)
    assert report["status"] == "invalid"
    assert report["checkedGlbs"] == 2
    assert any("actor.glb:node[0]: translation" in error for error in report["errors"])
    assert actor.read_bytes() == before
    _glb(actor)
    report = validator.validate_snapshot(tmp_path)
    assert report["status"] == "ready"
    assert [row["path"] for row in report["inventory"]] == ["actor.glb", "fixture-models/fixture.glb"]
    assert all(len(row["sha256"]) == 64 for row in report["inventory"])


def test_snapshot_requires_both_contract_documents_and_ignores_backups(tmp_path):
    _snapshot(tmp_path)
    backup = tmp_path / "_previous-legacy"
    backup.mkdir()
    (backup / "invalid.glb").write_bytes(b"legacy")
    assert validator.validate_snapshot(tmp_path)["status"] == "ready"
    (tmp_path / "fixture-attach/attach-points.json").unlink()
    report = validator.validate_snapshot(tmp_path)
    assert report["status"] == "invalid"
    assert any("attach-points.json" in error for error in report["errors"])


def test_attach_view_requires_exact_source_scale_and_root_placement(tmp_path):
    path = tmp_path / "attach.json"
    view = {"transform": {"file": "CAB", "pathId": "1"}, "component": {"pathId": "2"},
            "coordinateContract": validator.CONTRACT, "placementRoot": True,
            "localScale": [1., 2., 3.], "sourceLocalScale": {
                "coordinateSpace": validator.SOURCE, "value": [1., 2., 3.]}}
    document = {"version": 2, **_extras(), "packages": {"p": {"views": [view], "entries": []}}}
    path.write_text(json.dumps(document), encoding="utf-8")
    assert validator.validate_attach(path)["errors"] == []
    view["placementRoot"] = False
    view["localScale"] = [2., 2., 3.]
    path.write_text(json.dumps(document), encoding="utf-8")
    assert len(validator.validate_attach(path)["errors"]) == 2
