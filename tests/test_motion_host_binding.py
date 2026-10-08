"""A humanoid slot must animate its Avatar host, not a same-named scene bone."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from chara import motion_library as motion
from core.gltf import GLB


@pytest.mark.parametrize("hips_host", ["Hips", "Root"])
def test_hips_rotation_and_translation_target_the_same_actual_host(monkeypatch, hips_host):
    rig = SimpleNamespace(host_node=lambda bone: hips_host if bone == "Hips" else bone)
    glb = GLB()
    authored_hips = [0.02028527, -0.02028527, 0.70681578, 0.70681578]
    glb.g["nodes"] = [{"name": "Root"}, {"name": "Hips", "rotation": authored_hips.copy()}]
    monkeypatch.setattr(motion, "pose_root", lambda *args, **kwargs: ((0, .25, 0), (0, 0, 0, 1)))
    sample = {"name": "source-owned", "frames": [(0., {}, (0, 0, 0, 1), (0, 0, 0), {}, {}),
                                                 (1., {}, (0, 0, 0, 1), (0, 0, 0), {}, {})]}
    motion._add_animation(glb, rig, sample, {"Root": 0, "Hips": 1}, {})
    channels = glb.g["animations"][0]["channels"]
    expected_node = 0 if hips_host == "Root" else 1
    assert {(c["target"]["node"], c["target"]["path"]) for c in channels} == {
        (expected_node, "rotation"), (expected_node, "translation")}
    assert glb.g["nodes"][1]["rotation"] == authored_hips
    for sampler in glb.g["animations"][0]["samplers"]:
        assert glb.g["accessors"][sampler["input"]]["count"] == 2
        assert glb.g["accessors"][sampler["output"]]["count"] == 2


def test_semantic_slot_does_not_require_a_literal_scene_node(monkeypatch):
    rig = SimpleNamespace(host_node=lambda bone: "Root" if bone == "Hips" else bone)
    glb = GLB()
    monkeypatch.setattr(motion, "pose_root", lambda *args, **kwargs: ((0, 0, 0), (0, 0, 0, 1)))
    sample = {"name": "source-owned", "frames": [(0., {}, (0, 0, 0, 1), (0, 0, 0), {}, {})]}
    motion._add_animation(glb, rig, sample, {"Root": 0}, {})
    assert all(c["target"]["node"] == 0 for c in glb.g["animations"][0]["channels"])


def test_translation_dof_and_rotation_share_an_aliased_host(monkeypatch):
    hosts = {"Hips": "Root", "Spine": "Thorax"}
    rig = SimpleNamespace(host_node=lambda bone: hosts.get(bone, bone),
                          tdof_translations=lambda _: {"Spine": (0.1, 0.2, 0.3)})
    monkeypatch.setattr(motion, "pose_root", lambda *args, **kwargs: ((0, 0, 0), (0, 0, 0, 1)))
    monkeypatch.setattr(motion, "pose_bone", lambda *args: (0, 0, 0, 1))
    glb = GLB()
    sample = {"name": "source-owned", "frames": [(0., {}, (0, 0, 0, 1), (0, 0, 0), {}, {1: (1, 2, 3)})]}
    motion._add_animation(glb, rig, sample, {"Root": 0, "Thorax": 1}, {})
    assert {(c["target"]["node"], c["target"]["path"]) for c in glb.g["animations"][0]["channels"]} == {
        (0, "rotation"), (0, "translation"), (1, "rotation"), (1, "translation")}


def test_pose_binding_revision_is_an_export_receipt_input():
    from chara import fixture_actor_libraries as libraries
    assert motion.POSE_BINDING_REVISION >= 2
    assert libraries.POSE_BINDING_REVISION == motion.POSE_BINDING_REVISION
