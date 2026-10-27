"""Tagged source picking geometry and the failure/publication boundaries."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.assets.packages import Package, PackageFile
from sites import edit_pick


def _pointer(path_id, file_id=0):
    return {"m_FileID": file_id, "m_PathID": path_id}


def _transform(owner, parent, position):
    return {"m_GameObject": _pointer(owner), "m_Father": _pointer(parent),
            "m_LocalPosition": dict(zip("xyz", position)),
            "m_LocalRotation": dict(zip("xyzw", (0, 0, 0, 1))),
            "m_LocalScale": dict(zip("xyz", (1, 2, -1)))}


@pytest.fixture
def scene(monkeypatch):
    record = PackageFile("mysekai__site__field__home", "CAB-test", [])
    record.kinds = {1: "GameObject", 2: "Transform", 3: "GameObject", 4: "Transform",
                    5: "MeshCollider", 6: "Mesh", 7: "GameObject", 8: "MeshCollider"}
    record._trees = {
        1: {"m_Name": "root", "m_IsActive": 0},
        2: _transform(1, 0, (7, 8, 9)),
        3: {"m_Name": "arbitrary-name", "m_Tag": 20001, "m_Layer": 9, "m_IsActive": 1},
        4: _transform(3, 2, (-1, 2, 3)),
        5: {"m_GameObject": _pointer(3), "m_Mesh": _pointer(6),
            "m_Enabled": False, "m_IsTrigger": True, "m_Convex": False},
        # A similarly named untagged collider must not participate.
        7: {"m_Name": "nav_ground", "m_Tag": 0},
        8: {"m_GameObject": _pointer(7)},
    }
    mesh = SimpleNamespace(m_SubMeshes=[SimpleNamespace(
        topology=0, firstByte=0, indexCount=3, baseVertex=0)])
    handler = SimpleNamespace(process=lambda: None, m_Vertices=[(1, 0, 4), (2, 0, 5), (3, 0, 6)],
                              m_VertexCount=3, m_IndexBuffer=(2, 1, 0), m_Use16BitIndices=True)
    record.objects[6] = SimpleNamespace(read=lambda: mesh)
    monkeypatch.setattr(edit_pick, "MeshHandler", lambda _: handler)
    return SimpleNamespace(record=record, mesh=mesh, handler=handler,
                           package=Package(record.bundle, [record], [], []))


def test_tag_index_comes_from_the_matching_client():
    assert edit_pick._tag_index({"tags": ["Other", "RaycastTargetGroup"]}) == 20001
    assert edit_pick._tag_index({"tags": ["RaycastTargetGroup", "Other"]}) == 20000


@pytest.mark.parametrize("tags", [None, [], ["Other"], ["RaycastTargetGroup"] * 2])
def test_missing_or_ambiguous_tag_fails(tags):
    with pytest.raises(ValueError):
        edit_pick._tag_index({"tags": tags})


def test_selection_and_authored_coordinates_are_lossless(scene):
    rows = edit_pick._colliders(scene.package, 20001)
    assert len(rows) == 1
    row = rows[0]
    assert row["positions"] == [[1.0, 0.0, 4.0], [2.0, 0.0, 5.0], [3.0, 0.0, 6.0]]
    assert row["triangles"] == [[2, 1, 0]]
    assert [node["position"] for node in row["chain"]] == [[7, 8, 9], [-1, 2, 3]]
    assert [node["active"] for node in row["chain"]] == [False, True]
    assert row["chain"][1]["scale"] == [1, 2, -1]
    assert not row["enabled"] and row["isTrigger"] and not row["convex"]
    assert row["layer"] == 9
    assert row["source"] == {"file": "CAB-test", "componentId": "5",
                             "gameObjectId": "3", "meshId": "6"}


def test_submesh_range_and_base_vertex_are_applied(scene):
    scene.handler.m_Vertices = [(1, 2, 3)] * 6
    scene.handler.m_VertexCount = 6
    scene.handler.m_IndexBuffer = (99, 98, 97, 2, 1, 0)
    scene.handler.m_Use16BitIndices = False
    scene.mesh.m_SubMeshes[0].firstByte = 12
    scene.mesh.m_SubMeshes[0].baseVertex = 3
    assert edit_pick._mesh_geometry(scene.mesh)[1] == [[5, 4, 3]]


@pytest.mark.parametrize("field,value", [("topology", 2), ("firstByte", 1),
                                         ("indexCount", 4), ("indexCount", 6),
                                         ("baseVertex", 3)])
def test_invalid_or_unhandled_geometry_fails(scene, field, value):
    setattr(scene.mesh.m_SubMeshes[0], field, value)
    with pytest.raises(ValueError):
        edit_pick._mesh_geometry(scene.mesh)


@pytest.mark.parametrize("field,pointer", [("m_Mesh", _pointer(99)),
                                           ("m_Mesh", _pointer(6, 1)),
                                           ("m_GameObject", _pointer(3, 1))])
def test_unresolved_or_external_pointer_is_not_silently_dropped(scene, field, pointer):
    scene.record._trees[5][field] = pointer
    with pytest.raises(ValueError):
        edit_pick._colliders(scene.package, 20001)


@pytest.mark.parametrize("pointer", [_pointer(2, 1), _pointer(4), _pointer(99)])
def test_external_cyclic_or_missing_parent_fails(scene, pointer):
    scene.record._trees[2]["m_Father"] = pointer
    with pytest.raises(ValueError):
        edit_pick._colliders(scene.package, 20001)


def test_tagged_other_collider_is_not_silently_dropped(scene):
    scene.record.kinds[5] = "BoxCollider"
    with pytest.raises(ValueError, match="unsupported"):
        edit_pick._colliders(scene.package, 20001)


def test_missing_parent_metadata_is_not_a_root(scene):
    del scene.record._trees[4]["m_Father"]
    with pytest.raises(ValueError, match="parent pointer"):
        edit_pick._colliders(scene.package, 20001)


def test_missing_tag_is_not_untagged(scene):
    del scene.record._trees[7]["m_Tag"]
    with pytest.raises(ValueError, match="GameObject tag"):
        edit_pick._colliders(scene.package, 20001)


def test_absent_surface_does_not_publish_an_empty_success(scene):
    with pytest.raises(ValueError, match="no RaycastTargetGroup"):
        edit_pick._colliders(scene.package, 20009)


def _export_inputs(scene, tmp_path, monkeypatch):
    bundle = tmp_path / scene.record.bundle
    bundle.write_bytes(b"synthetic source")
    monkeypatch.setattr(edit_pick, "_tag_manager", lambda _: (20001, {
        "file": "globalgamemanagers", "fileSha256": "a" * 64, "objectId": "42"}))
    monkeypatch.setattr(edit_pick, "PackageStore", lambda _: SimpleNamespace(
        package=lambda _: scene.package,
        source_receipt=lambda _: {"sha256": "b" * 64}))
    return bundle


def test_success_is_deterministic_and_breaks_only_output_hardlink(scene, tmp_path, monkeypatch):
    bundle = _export_inputs(scene, tmp_path, monkeypatch)
    out, alias = tmp_path / "home.json", tmp_path / "retained.json"
    out.write_bytes(b"retained source")
    os.link(out, alias)
    result = edit_pick.extract_site_edit_pick(bundle, [tmp_path / "client.apk"], out)
    first = out.read_bytes()
    assert result == {"output": str(out), "site": "home", "colliders": 1,
                      "vertices": 3, "triangles": 1}
    assert alias.read_bytes() == b"retained source"
    document = json.loads(first)
    assert document["coordinateContract"] == "unity-lh-y-up-authored"
    assert document["bundleSha256"] == "b" * 64
    assert document["tagManagerSource"]["objectId"] == "42"
    edit_pick.extract_site_edit_pick(bundle, [tmp_path / "client.apk"], out)
    assert out.read_bytes() == first


def test_failed_export_preserves_existing_output(scene, tmp_path, monkeypatch):
    bundle = _export_inputs(scene, tmp_path, monkeypatch)
    out = tmp_path / "home.json"
    out.write_bytes(b"previous export")
    scene.record._trees[5]["m_Mesh"] = _pointer(99)
    with pytest.raises(ValueError):
        edit_pick.extract_site_edit_pick(bundle, [tmp_path / "client.apk"], out)
    assert out.read_bytes() == b"previous export"


def test_input_cannot_be_replaced(scene, tmp_path, monkeypatch):
    bundle = _export_inputs(scene, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="input"):
        edit_pick.extract_site_edit_pick(bundle, [], bundle)


def test_grouped_cli_dispatch(monkeypatch, capsys):
    from cli.main import main
    calls = []
    monkeypatch.setattr(edit_pick, "extract_site_edit_pick", lambda *args: calls.append(args) or {})
    assert main(["export", "site-edit-pick", "--bundle", "scene", "--apk", "base.apk",
                 "--apk", "pack.apk", "--out", "home.json"]) == 0
    assert calls == [("scene", ["base.apk", "pack.apk"], "home.json")]
    assert capsys.readouterr().out.strip() == "{}"
