"""Comparison guards for isolated site coordinate re-export."""
import importlib.util
import copy
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "reexport_site_coordinates", Path(__file__).resolve().parents[1]
    / "tools" / "reexport_site_coordinates.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_geometry_comparison_ignores_only_extras():
    old = {"translation": [1, 2, 3], "extras": {"active": True},
           "children": [{"mesh": 0, "extras": {"legacy": True}}]}
    new = {"translation": [1, 2, 3], "extras": {"coordinateContract": tool.CONTRACT},
           "children": [{"mesh": 0, "extras": {"canonical": True}}]}
    assert tool.without_extras(old) == tool.without_extras(new)
    new["translation"][0] = -1
    assert tool.without_extras(old) != tool.without_extras(new)


def test_collision_identity_does_not_use_content_hash_filename():
    old = {"root": "home", "node": "navmesh_target/ground", "component": "MeshCollider",
           "mesh": "ground", "meshPointer": {"fileId": 0, "pathId": 123},
           "file": "collision/old.glb", "sha256": "old"}
    new = dict(old, file="collision/new.glb", sha256="new", coordinateContract=tool.CONTRACT)
    assert tool.collision_identity(old) == tool.collision_identity(new)
    new["node"] = "navmesh_target/wall"
    assert tool.collision_identity(old) != tool.collision_identity(new)


def test_source_animation_append_keeps_all_existing_geometry(monkeypatch):
    old = {"nodes": [{"mesh": 0}], "meshes": [{"primitives": []}],
           "accessors": [{"bufferView": 0}],
           "bufferViews": [{"byteOffset": 0, "byteLength": 4}]}
    new = copy.deepcopy(old)
    new["accessors"].append({"bufferView": 1})
    new["bufferViews"].append({"byteOffset": 4, "byteLength": 4})
    new["animations"] = [{"extras": {"sourceClip": {"file": "source", "pathId": "7"}}}]
    monkeypatch.setattr(tool, "glb_parts", lambda path: (old, b"mesh") if path == "old" else (new, b"meshclip"))
    monkeypatch.setattr(tool, "sha", lambda path: path)
    monkeypatch.setattr(tool, "validate_glb", lambda path: {"errors": []})
    report = tool.compare_glb("old", "new")
    assert report["sourceAnimationAdditionsOnly"]
    new["nodes"][0]["mesh"] = 1
    assert not tool.compare_glb("old", "new")["sourceAnimationAdditionsOnly"]
