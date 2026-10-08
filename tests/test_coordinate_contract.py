"""The spatial boundary is one X reflection, independently of UV convention."""
import math
import os
import struct
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.gltf import (COORDINATE_SPACE, GLB, coordinate_contract, flip_winding,
                       reflect_position, reflect_quaternion, reflect_tangent)
from core.fixture_collision_document import read_fixture_collision
from core.mesh import skin_accessors
from fixtures.animations import _write_channels
from fixtures import meshes
from fixtures.house_views import _transform_chain
from fixtures.attach import _view_identity
from sites.source_nodes import attach_component
from sites.navmesh import navmesh_document
from sites.geometry import collider_document
from chara.cloth import extract_bone_cloth
from test_fixture_meshes import _FakeMeshHandler, _Package, _glb, _run


def _values(glb, accessor):
    entry = glb.g["accessors"][accessor]
    view = glb.g["bufferViews"][entry["bufferView"]]
    widths = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
    code = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}[entry["componentType"]]
    width = widths[entry["type"]]
    data = glb.bin[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
    flat = struct.unpack(f"<{entry['count'] * width}{code}", data)
    return [tuple(flat[i:i + width]) for i in range(0, len(flat), width)]


def _rotate(q, p):
    x, y, z, w = q
    px, py, pz = p
    # q * p * q^-1, with the unit quaternion used in this test.
    uv = (y*pz-z*py, z*px-x*pz, x*py-y*px)
    uuv = (y*uv[2]-z*uv[1], z*uv[0]-x*uv[2], x*uv[1]-y*uv[0])
    return tuple(p[i] + 2*(w*uv[i]+uuv[i]) for i in range(3))


def test_reflection_is_an_involution_and_quaternion_conjugation():
    p = (1.125, -2.5, 3.75)
    q = (0.2, 0.3, -0.4, math.sqrt(0.71))
    assert reflect_position(reflect_position(p)) == p
    assert reflect_quaternion(reflect_quaternion(q)) == q
    assert _rotate(reflect_quaternion(q), reflect_position(p)) == pytest.approx(
        reflect_position(_rotate(q, p)))
    assert flip_winding(flip_winding([0, 1, 2, 2, 3, 0])) == [0, 1, 2, 2, 3, 0]
    assert reflect_tangent(reflect_tangent((1, 2, 3, -1))) == (1, 2, 3, -1)
    contract = coordinate_contract()
    assert contract["space"] == COORDINATE_SPACE
    assert contract["units"] == "source-unity-unit"
    assert contract["uvOrigin"] == "unity-bottom-left"


def test_common_glb_boundary_marks_every_scene_root_without_inventing_uv():
    glb = GLB()
    glb.g["nodes"] = [{"name": "site"}, {"name": "actor", "extras": {"other": 7}}]
    glb.g["scenes"] = [{"nodes": [0]}, {"nodes": [1]}]
    glb.blob()
    for node in glb.g["nodes"]:
        assert node["extras"]["coordinateContract"] == COORDINATE_SPACE
        assert node["extras"]["coordinateUnits"] == "source-unity-unit"
        assert "uvConvention" not in node["extras"]
    assert glb.g["nodes"][1]["extras"]["other"] == 7


def test_fixture_mesh_uses_reflected_basis_and_keeps_authored_uv(monkeypatch):
    pkg = _Package("mysekai__fixture__mdl_spatial_test")
    pid = pkg.mesh([(1, 0, 0), (2, 0, 0), (1, 1, 0)], [0, 1, 2],
                   normals=[(1, 0, 0)] * 3, tangents=[(1, 0, 0, -1)] * 3)
    obj = next(obj for obj in pkg.objects if obj.path_id == pid)
    obj._read._uv0 = [(0.1, 0.2), (0.3, 0.4), (0.5, 0.6)]
    monkeypatch.setattr(meshes, "MeshHandler", _FakeMeshHandler)
    glb = GLB()
    result = meshes._mesh_channels(glb, obj, obj._tree, skin=False)
    attributes = result["attributes"]
    assert _values(glb, attributes["POSITION"]) == [(-1, 0, 0), (-2, 0, 0), (-1, 1, 0)]
    assert _values(glb, attributes["NORMAL"]) == [(-1, 0, 0)] * 3
    assert _values(glb, attributes["TANGENT"]) == [(-1, 0, 0, 1)] * 3
    assert _values(glb, attributes["TEXCOORD_0"])[0] == pytest.approx((0.1, 0.2))
    assert _values(glb, result["submeshes"][0]) == [(0,), (2,), (1,)]
    assert glb.g["accessors"][attributes["POSITION"]]["min"] == [-2, 0, 0]


def test_inverse_bind_matrix_is_conjugated_not_reflected_only_once():
    # A full asymmetric affine matrix catches missing left/right S factors.
    values = [[1., 2., 3., 4.], [5., 6., 7., 8.],
              [9., 10., 11., 12.], [0., 0., 0., 1.]]
    tree = {"m_BindPose": [{f"e{r}{c}": values[r][c] for r in range(4) for c in range(4)}]}
    handler = SimpleNamespace(m_BoneIndices=[(0,)], m_BoneWeights=None, m_VertexCount=1)
    glb = GLB()
    result = skin_accessors(glb, handler, tree, reflect=True)
    actual = _values(glb, result["inverseBindMatrices"])[0]
    expected = tuple(values[r][c] * (-1 if (r == 0) != (c == 0) else 1)
                     for c in range(4) for r in range(4))
    assert actual == expected


@pytest.mark.parametrize("property,width,sign", [
    ("translation", 3, (-1, 1, 1)),
    ("rotation", 4, (1, -1, -1, 1)),
    ("scale", 3, (1, 1, 1)),
])
def test_animation_hermite_values_and_derivatives_share_basis(property, width, sign):
    rows = [[float(i + offset) for i in range(width)] for offset in (1, 4, 7)]
    channel = {"pathHash": 1, "path": "root/joint", "variant": 0,
               "source": "animator-anchor-relative", "node": 0,
               "property": property, "kind": "cubic", "count": 1,
               "times": [0.], "inTangents": [rows[0]],
               "values": [rows[1]], "outTangents": [rows[2]]}
    glb = GLB()
    animation = _write_channels(glb, "curve", [channel])
    actual = _values(glb, animation["samplers"][0]["output"])
    assert actual == [tuple(value * sign[i] for i, value in enumerate(row)) for row in rows]
    assert animation["extras"]["space"] == COORDINATE_SPACE


def test_fixture_contract_is_present_in_index_asset_and_each_scene_root(tmp_path, monkeypatch):
    pkg = _Package("mysekai__fixture__mdl_contract")
    view_go = None
    for name in ("variant_a", "variant_b"):
        go, _ = pkg.node(name, position=(1., 2., 3.))
        if name == "variant_b":
            view_go = go
        mesh = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
        pkg.renderer(go, mesh, [pkg.material()])
    script = pkg.add("MonoScript", {"m_ClassName": "FixtureView", "m_Name": "FixtureView"})
    pkg.add("MonoBehaviour", {"m_GameObject": {"m_FileID": 0, "m_PathID": view_go},
                              "m_Script": {"m_FileID": 0, "m_PathID": script}})
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    gltf = _glb(tmp_path / "out" / f"{pkg.name}.glb")
    assert index["version"] == 4
    assert index["coordinateContract"] == COORDINATE_SPACE
    assert index["packages"][pkg.name]["coordinateContract"] == COORDINATE_SPACE
    assert gltf["asset"]["extras"]["coordinateContract"] == COORDINATE_SPACE
    collision = read_fixture_collision(gltf)
    assert collision["coordinateContract"] == COORDINATE_SPACE
    reference = gltf["extras"]["fixtureCollisionRef"]
    for scene in gltf["scenes"]:
        for i in scene["nodes"]:
            assert gltf["nodes"][i]["extras"]["coordinateContract"] == COORDINATE_SPACE
            assert gltf["nodes"][i]["extras"]["fixtureCollisionRef"] == reference
            assert "fixtureCollision" not in gltf["nodes"][i]["extras"]
    marked = [node for node in gltf["nodes"] if node.get("extras", {}).get("fixtureViewRoot")]
    assert len(marked) == 1
    assert marked[0]["name"] == "variant_b"
    assert marked[0]["extras"]["gameObjectId"] == view_go


def test_house_view_chain_marks_canonical_and_retains_source_trs():
    class Store:
        def __init__(self):
            self.rows = {}
        def follow(self, _owner, pointer):
            return self.rows.get(pointer["m_PathID"])
    ptr = lambda pid: {"m_FileID": 0, "m_PathID": pid}
    store = Store()
    record = SimpleNamespace(archive="CAB", kinds={10: "Transform", 11: "GameObject"},
                             tree=lambda pid: {
                                 10: {"m_GameObject": ptr(11), "m_Father": ptr(0),
                                      "m_LocalPosition": {"x": 1., "y": 2., "z": 3.},
                                      "m_LocalRotation": {"x": .1, "y": .2, "z": .3, "w": .9},
                                      "m_LocalScale": {"x": 1., "y": 1., "z": 1.}},
                                 11: {"m_Name": "door"},
                             }[pid])
    store.rows[10] = (record, 10)
    store.rows[11] = (record, 11)
    transforms = {}
    selected = _transform_chain(store, record, ptr(10), transforms)
    assert selected["pathId"] == "10"
    row = next(iter(transforms.values()))
    assert row["coordinateContract"] == COORDINATE_SPACE
    assert row["localPosition"]["x"] == -1.
    assert row["sourceTransform"]["localPosition"]["x"] == 1.


def test_nav_obstacle_center_is_canonical_and_source_evidence_is_tagged():
    extras = {}
    tree = {"m_Enabled": 1, "m_Shape": 0, "m_Center": {"x": 1., "y": 2., "z": 3.},
            "m_Extents": {"x": .3, "y": .4, "z": .5}, "m_Carve": 1,
            "m_CarveOnlyStationary": 0, "m_MoveThreshold": .1, "m_TimeToStationary": .2}
    attach_component(extras, 4, "NavMeshObstacle", "", tree)
    row = extras["navMeshObstacles"][0]
    assert row["coordinateContract"] == COORDINATE_SPACE
    assert row["center"] == {"x": -1., "y": 2., "z": 3.}
    assert row["extents"] == {"x": .3, "y": .4, "z": .5}
    assert row["sourceGeometry"]["coordinateSpace"] == "unity-lh-y-up-authored"


def test_nested_fixture_view_is_refused_before_runtime_root_normalization():
    record = SimpleNamespace(
        kinds={1: "MonoBehaviour"},
        script_of=lambda _: "FixtureView",
        tree=lambda _: {"m_GameObject": {"m_PathID": 22}},
    )
    transforms = {10: {"m_GameObject": {"m_PathID": 11}},
                  20: {"m_GameObject": {"m_PathID": 22}, "m_Father": {"m_PathID": 10}}}
    with pytest.raises(ValueError, match="nested below its prefab root"):
        meshes._validate_fixture_view_roots(record, transforms, [10])


def test_fixture_view_identity_preserves_scale_and_declares_placement_root():
    ptr = lambda pid: {"m_FileID": 0, "m_PathID": pid}
    rows = {11: {"m_Component": [{"component": ptr(10)}]},
            10: {"m_Father": ptr(0), "m_LocalScale": {"x": 2., "y": 3., "z": 4.}}}
    record = SimpleNamespace(archive="CAB", kinds={10: "Transform", 11: "GameObject"},
                             tree=rows.__getitem__)
    store = SimpleNamespace(follow=lambda owner, pointer:
                            (record, pointer["m_PathID"]) if pointer["m_PathID"] else None)
    actual = _view_identity(store, record, 9, {"m_GameObject": ptr(11), "_collider": ptr(0)})
    assert actual["coordinateContract"] == COORDINATE_SPACE
    assert actual["localScale"] == [2., 3., 4.]
    assert actual["sourceLocalScale"] == {"coordinateSpace": "unity-lh-y-up-authored",
                                          "value": [2., 3., 4.]}
    assert actual["placementRoot"] is True
    rows[10]["m_Father"] = ptr(12)
    assert _view_identity(store, record, 9, {"m_GameObject": ptr(11), "_collider": ptr(0)})["placementRoot"] is False


def test_native_navmesh_sidecar_preserves_raw_fields_and_tags_canonical_glb_separately():
    tree = {"m_Position": {"x": 2., "y": 3., "z": 4.},
            "m_Rotation": {"x": .1, "y": .2, "z": .3, "w": .9},
            "m_SourceBounds": {"m_Center": {"x": 5., "y": 6., "z": 7.}},
            "m_NavMeshTiles": [{"m_MeshData": [1, 2, 3]}],
            "m_HeightMeshes": [{"m_Bounds": {"m_Center": {"x": 8., "y": 9., "z": 10.}}}]}
    doc, blob = navmesh_document(tree)
    assert doc["position"] == [2., 3., 4.]
    assert doc["rotation"] == [.1, .2, .3, .9]
    assert doc["fieldCoordinateSpace"]["position"] == "unity-lh-y-up-authored"
    assert doc["tiles"]["coordinateSpace"] == "unity-lh-y-up-authored"
    assert blob == bytes([1, 2, 3])
    height = doc["heightMeshes"][0]
    assert height["bounds"]["center"] == [8., 9., 10.]
    assert height["fieldCoordinateSpace"] == {"bounds": "unity-lh-y-up-authored",
                                               "file": COORDINATE_SPACE}
    assert "coordinateContract" not in doc


def test_site_collider_raw_dimensions_are_explicit_not_blindly_relabelled():
    tree = {"m_Center": {"x": 2., "y": 3., "z": 4.},
            "m_Size": {"x": 5., "y": 6., "z": 7.}}
    record = SimpleNamespace(tree=lambda _: tree, kinds={})
    doc = collider_document(None, record, 1, "BoxCollider", "root/box")
    assert doc["center"] == [2., 3., 4.]
    assert doc["size"] == [5., 6., 7.]
    assert doc["fieldCoordinateSpace"]["center"] == "unity-lh-y-up-authored"
    assert "coordinateContract" not in doc


def test_site_collider_coordinate_metadata_keeps_serialized_physics_controls():
    tree = {"m_GameObject": {"m_PathID": 2}, "m_Enabled": False,
            "m_IsTrigger": True, "m_IncludeLayers": {"m_Bits": 4},
            "m_ExcludeLayers": {"m_Bits": 8}, "m_LayerOverridePriority": 3,
            "m_ProvidesContacts": True,
            "m_Material": {"m_FileID": 1, "m_PathID": 123},
            "m_Center": {"x": 2., "y": 3., "z": 4.},
            "m_Size": {"x": 5., "y": 6., "z": 7.}}
    record = SimpleNamespace(tree=lambda pid: tree if pid == 1 else {"m_Layer": 9},
                             kinds={1: "BoxCollider", 2: "GameObject"})
    doc = collider_document(None, record, 1, "BoxCollider", "root/box")
    assert doc["schemaVersion"] == 2
    assert doc["enabled"] is False and doc["isTrigger"] is True
    assert (doc["layer"], doc["includeLayers"], doc["excludeLayers"]) == (9, 4, 8)
    assert doc["layerOverridePriority"] == 3 and doc["providesContacts"] is True
    assert doc["materialPointer"] == {"fileId": 1, "pathId": "123"}
    assert doc["center"] == [2., 3., 4.]
    assert doc["fieldCoordinateSpace"]["center"] == "unity-lh-y-up-authored"


def test_cloth_does_not_claim_raw_params_share_converted_geometry_basis():
    doc = extract_bone_cloth([], to_gltf=True)
    assert doc["coordinateUnits"] == "source-unity-unit"
    assert doc["fieldCoordinateSpace"]["components[].vertices"] == COORDINATE_SPACE
    assert doc["fieldCoordinateSpace"]["components[].clothParams"] == "unity-lh-y-up-authored"
    assert "coordinateContract" not in doc
    raw = extract_bone_cloth([], to_gltf=False)
    assert set(raw["fieldCoordinateSpace"].values()) == {"unity-lh-y-up-authored"}
