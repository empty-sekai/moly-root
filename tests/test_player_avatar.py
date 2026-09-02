"""Checks for the player body mesh (SkinnedMeshRenderer) export.

One check stands on its own (pure string logic, no bundle needed). The rest need
real player-avatar packages, which are never part of this repository: point
``MOLY_PLAYER_MODEL_BUNDLES`` at the model bundle plus its motion bundles
(separated by the platform path separator, same order ``export_player_avatar``
takes) and ``MOLY_PLAYER_NEGATIVE_BUNDLE`` at a bundle that carries no
``SkinnedMeshRenderer`` (a motion-only or prop bundle) and they run; without
either, the checks that need it skip.

    MOLY_PLAYER_MODEL_BUNDLES=/path/model;/path/motion;/path/motion_unique \
    MOLY_PLAYER_NEGATIVE_BUNDLE=/path/some_prop_bundle \
    pytest tests/test_player_avatar.py

Both checks parse the produced ``.glb`` back out of its own bytes (a small
local binary-glb-JSON-chunk reader below) rather than trusting the in-memory
record ``export_player_avatar`` returns, so a bug that corrupts the file on
the way to disk cannot pass by only ever being checked against itself. The
source vertex count for the positive check is likewise read independently,
straight off the source Mesh object via ``UnityPy.helpers.MeshHelper``, not
from anything ``read_player_mesh`` computed.
"""
import json
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from chara.player_avatar import _relative_to_anchor, export_player_avatar

MODEL_VARIABLE = "MOLY_PLAYER_MODEL_BUNDLES"
NEGATIVE_VARIABLE = "MOLY_PLAYER_NEGATIVE_BUNDLE"

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A  # "JSON" little-endian


def _read_glb_json(path):
    """The glTF-JSON chunk of a ``.glb`` file, parsed straight from its bytes."""
    with open(path, "rb") as f:
        data = f.read()
    magic, _version, _total = struct.unpack_from("<4sII", data, 0)
    assert magic == GLB_MAGIC, f"{path} does not start with the glTF magic"
    offset = 12
    length, chunk_type = struct.unpack_from("<II", data, offset)
    assert chunk_type == CHUNK_JSON, f"{path}'s first chunk is not JSON"
    return json.loads(data[offset + 8:offset + 8 + length])


def test_relative_to_anchor_strips_only_the_anchor_prefix():
    assert _relative_to_anchor("audience/audience", "audience") == "audience"
    assert _relative_to_anchor("audience", "audience") == ""
    assert _relative_to_anchor("audience/Root/Hips", "audience") == "Root/Hips"
    # A sibling that merely starts with the same characters is not "under" the
    # anchor and must not be truncated as if it were.
    assert (_relative_to_anchor("audienceRoom/Hips", "audience")
            == "audienceRoom/Hips")
    assert _relative_to_anchor("foo/bar", "") == "foo/bar"


@pytest.fixture(scope="module")
def model_bundle_paths():
    configured = os.environ.get(MODEL_VARIABLE)
    if not configured:
        pytest.skip(f"{MODEL_VARIABLE} is not configured")
    paths = [p for p in configured.split(os.pathsep) if p]
    present = [p for p in paths if os.path.isfile(p)]
    if not present:
        pytest.skip(f"{MODEL_VARIABLE} points at no readable file")
    return present


@pytest.fixture(scope="module")
def negative_bundle_path():
    configured = os.environ.get(NEGATIVE_VARIABLE)
    if not configured:
        pytest.skip(f"{NEGATIVE_VARIABLE} is not configured")
    if not os.path.isfile(configured):
        pytest.skip(f"{NEGATIVE_VARIABLE} points at no readable file")
    return configured


@pytest.fixture(scope="module")
def source_vertex_count(model_bundle_paths):
    """The body Mesh's vertex count, read directly off the source bundle --
    independent of anything ``read_player_mesh`` computed, so the positive
    check below is not comparing the exported value against itself."""
    unitypy = pytest.importorskip("UnityPy")
    from UnityPy.helpers.MeshHelper import MeshHandler

    unitypy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"
    env = unitypy.load(*model_bundle_paths)
    meshes = [o for o in env.objects if o.type.name == "Mesh"]
    assert meshes, "the configured model bundle holds no Mesh object"
    assert len(meshes) == 1, (
        f"expected exactly one Mesh in the model bundle, found {len(meshes)}; "
        "the vertex-count reconciliation below assumes a single body mesh")
    handler = MeshHandler(meshes[0].read())
    handler.process()
    return handler.m_VertexCount


def test_exported_body_mesh_matches_the_source_vertex_count(
        tmp_path, model_bundle_paths, source_vertex_count):
    record = export_player_avatar(model_bundle_paths, str(tmp_path),
                                   name="player_avatar_test")
    assert record["playerMesh"] is not None, (
        "the configured model bundle carries a SkinnedMeshRenderer but "
        "export_player_avatar reported none")
    assert record["playerMesh"]["vertexCount"] == source_vertex_count

    doc = _read_glb_json(record["glb"])
    assert len(doc.get("meshes", [])) >= 1
    assert len(doc.get("skins", [])) >= 1
    mesh = doc["meshes"][0]
    primitive = mesh["primitives"][0]
    accessor = doc["accessors"][primitive["attributes"]["POSITION"]]
    assert accessor["count"] == source_vertex_count, (
        "the glb's own POSITION accessor count does not match the source "
        "Mesh object's vertex count")
    assert "JOINTS_0" in primitive["attributes"]
    assert "WEIGHTS_0" in primitive["attributes"]

    skin = doc["skins"][0]
    assert len(skin["joints"]) == record["playerMesh"]["joints"]
    mesh_nodes = [n for n in doc["nodes"] if n.get("mesh") == 0]
    assert len(mesh_nodes) == 1, (
        f"expected exactly one node bound to mesh 0, found {len(mesh_nodes)}")
    assert mesh_nodes[0].get("skin") == 0, (
        "the node carrying the body mesh does not also carry its skin")


def test_bundle_without_a_skinned_mesh_renderer_exports_no_skin(
        tmp_path, negative_bundle_path):
    record = export_player_avatar([negative_bundle_path], str(tmp_path),
                                   name="player_avatar_negative_test")
    assert record["playerMesh"] is None

    doc = _read_glb_json(record["glb"])
    assert doc.get("meshes", []) == []
    assert doc.get("skins", []) == []
    assert all("mesh" not in n and "skin" not in n for n in doc["nodes"]), (
        "a node carries a mesh/skin binding despite no SkinnedMeshRenderer "
        "in the source bundle")
