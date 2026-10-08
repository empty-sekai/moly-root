"""Shared collision storage changes neither geometry nor instance identity."""
import copy
import json
import os
import sys
import struct

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.fixture_collision_document import (
    METADATA_NODE_NAME, compact_fixture_collision, read_fixture_collision,
)


def legacy_document():
    collision = {
        "schemaVersion": 1, "coordinateContract": "moly-rh-y-up-reflect-x-v1",
        "units": "source-unity-unit", "geometry": [{
            "geometryId": "CAB:9223372036854775807",
            "positions": [[-0.15388910472393036, 0.28566959500312805, -0.0]],
            "triangles": [[0, 0, 0]],
        }], "gaps": [],
    }
    return {
        "asset": {"version": "2.0"}, "scene": 1,
        "scenes": [{"nodes": [0]}, {"nodes": [1]}],
        "nodes": [
            {"name": "variant-a", "translation": [1.0000001192092896, 0.0, -0.0],
             "extras": {"active": False, "sourcePathId": 9223372036854775807,
                        "sourceObject": {"file": "CAB", "transformId": "9223372036854775807"},
                        "sourceCollision": {"activeSelf": False}, "fixtureCollision": collision}},
            {"name": "variant-b", "mesh": 0, "extras": {"active": True,
                        "fixtureViewRoot": True, "fixtureCollision": collision}},
        ],
        "extras": {"fixtureCollision": collision, "other": "kept"},
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "animations": [{"channels": [{"sampler": 0, "target": {"node": 1, "path": "translation"}}]}],
    }


def test_legacy_and_reference_storage_preserve_every_source_value_and_index():
    gltf = legacy_document()
    old = copy.deepcopy(gltf)
    table = json.dumps(read_fixture_collision(gltf), sort_keys=True)
    assert compact_fixture_collision(gltf)
    reference = {"schemaVersion": 1, "node": 2}
    assert gltf["extras"]["fixtureCollisionRef"] == reference
    assert gltf["nodes"][2] == {"name": METADATA_NODE_NAME,
                                 "extras": {"fixtureCollision": old["extras"]["fixtureCollision"]}}
    assert json.dumps(read_fixture_collision(gltf), sort_keys=True) == table
    for index in (0, 1):
        expected = old["nodes"][index]
        del expected["extras"]["fixtureCollision"]
        expected["extras"]["fixtureCollisionRef"] = reference
        assert json.dumps(gltf["nodes"][index], sort_keys=True) == json.dumps(expected, sort_keys=True)
    for key in ("asset", "scene", "scenes", "meshes", "animations"):
        assert gltf[key] == old[key]
    assert not compact_fixture_collision(gltf)
    assert len(gltf["nodes"]) == 3


@pytest.mark.parametrize("damage", ["index", "schema", "scene", "child", "mesh", "root", "inline", "skeleton", "joint", "animation"])
def test_broken_or_instanced_references_fail_instead_of_losing_collision(damage):
    gltf = legacy_document()
    compact_fixture_collision(gltf)
    if damage == "index":
        gltf["extras"]["fixtureCollisionRef"]["node"] = 200
    elif damage == "schema":
        gltf["extras"]["fixtureCollisionRef"]["schemaVersion"] = 2
    elif damage == "scene":
        gltf["scenes"][0]["nodes"].append(2)
    elif damage == "child":
        gltf["nodes"][0]["children"] = [2]
    elif damage == "mesh":
        gltf["nodes"][2]["mesh"] = 0
    elif damage == "root":
        gltf["nodes"][0]["extras"]["fixtureCollisionRef"]["node"] = 1
    elif damage == "inline":
        gltf["extras"]["fixtureCollision"] = {"geometry": []}
    elif damage == "skeleton":
        gltf["skins"] = [{"skeleton": 2, "joints": [0]}]
    elif damage == "joint":
        gltf["skins"] = [{"joints": [2]}]
    else:
        gltf["animations"][0]["channels"][0]["target"]["node"] = 2
    with pytest.raises(ValueError):
        read_fixture_collision(gltf)


def test_conflicting_legacy_copy_is_never_silently_discarded():
    gltf = legacy_document()
    gltf["nodes"][1]["extras"]["fixtureCollision"] = {"geometry": []}
    with pytest.raises(ValueError, match="differs"):
        compact_fixture_collision(gltf)


def test_glb_overlay_preserves_binary_chunks_and_does_not_write_source(tmp_path):
    from tools.deduplicate_fixture_collision import encoded, rewrite

    payload = encoded(legacy_document())
    payload += b" " * (-len(payload) % 4)
    tail = struct.pack("<I4s", 4, b"BIN\x00") + b"\x00\xff\x01\x80"
    before = b"glTF" + struct.pack("<III", 2, 20 + len(payload) + len(tail), len(payload)) + b"JSON" + payload + tail
    source, target = tmp_path / "original.glb", tmp_path / "overlay.glb"
    source.write_bytes(before)
    receipt = rewrite(source, target)
    after = target.read_bytes()
    assert source.read_bytes() == before
    assert after[20 + struct.unpack_from("<I", after, 12)[0]:] == tail
    assert receipt["sourceNodes"] == 2 and receipt["changed"]
    assert len(json.loads(after[20:20 + struct.unpack_from("<I", after, 12)[0]])["nodes"]) == 3
    with pytest.raises(FileExistsError):
        rewrite(source, target)


def test_v4_index_rejects_inline_but_v3_still_accepts_legacy_storage(tmp_path):
    from core.coordinate_contract import validate_index
    from core.gltf import GLB

    glb = GLB()
    glb.g.update(legacy_document())
    glb.g["extras"]["fixtureCollision"]["coordinateContract"] = "moly-rh-y-up-reflect-x-v1"
    path = tmp_path / "model.glb"
    glb.save(path)
    index = {"version": 3, "coordinateContract": "moly-rh-y-up-reflect-x-v1",
             "coordinateUnits": "source-unity-unit", "uvConvention": "unity-bottom-left",
             "packages": {"model": {
        "status": "exported", "coordinateContract": "moly-rh-y-up-reflect-x-v1", "glb": path.name,
    }}}
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert not validate_index(index_path)["errors"]
    index["version"] = 4
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert any("requires detached" in error for error in validate_index(index_path)["errors"])
    compact_fixture_collision(glb.g)
    glb.save(path)
    assert not validate_index(index_path)["errors"]
