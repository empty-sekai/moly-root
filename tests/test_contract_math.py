import math
from types import SimpleNamespace

import pytest

from core.gltf import unity_to_gltf_pos, unity_to_gltf_quat
from chara.mecanim.clip import evaluate
from chara.mecanim.retarget import pose_bone
from chara.mecanim import bodyxform
from core.quat import axis_angle, qdist, qmul, qrotv, qnorm


def atlas_cell(index, texture=(2048, 2048), cell=(512, 256)):
    if index < 2:
        index = 1
    i = index - 1
    return (i % 4, i >> 2)


def test_reflection_is_involutive_for_position_and_quaternion():
    p = (1.25, -2.0, 3.5)
    q = (0.1, 0.2, -0.3, 0.9)
    assert unity_to_gltf_pos(unity_to_gltf_pos(p)) == pytest.approx(p)
    assert unity_to_gltf_quat(unity_to_gltf_quat(q)) == pytest.approx(q)


def test_atlas_index_clamps_below_two_and_uses_four_columns():
    assert atlas_cell(0) == (0, 0)
    assert atlas_cell(1) == (0, 0)
    assert atlas_cell(2) == (1, 0)
    assert atlas_cell(9) == (0, 2)


def test_streamed_curve_is_cubic_and_constant_is_constant():
    cubic = ("cubic", [(1.0, (2.0, 3.0, 4.0, 5.0))])
    assert evaluate(cubic, 1.5) == pytest.approx(2 * .5**3 + 3 * .5**2 + 4 * .5 + 5)
    assert evaluate(("const", [(0.0, 7.25)]), 99.0) == pytest.approx(7.25)


def test_swing_twist_are_composed_in_order_without_clamping():
    rig = SimpleNamespace(
        axes={"LeftArm": {"preQ": (0, 0, 0, 1), "postQ": (0, 0, 0, 1), "sgn": (1, 1, 1), "min": (-90, -90, -90), "max": (90, 90, 90)}},
        bind_q={}, params={"armTwist": 0.5},
        q0=lambda bone: (0, 0, 0, 1),
        twist_factor=lambda bone: 0.5,
    )
    muscles = {39: 0.5, 40: 0.25, 41: 0.5}
    got = pose_bone(rig, "LeftArm", muscles)
    th = [math.radians(45.0), math.radians(25.0), math.radians(50.0)]
    swing = qnorm((0.0, math.tan(th[1] / 2), math.tan(th[2] / 2), 1.0))
    twist = axis_angle((1.0, 0.0, 0.0), th[0] * 0.5)
    expected = qmul(swing, twist)
    assert qdist(got, expected) < 1e-9


def test_hips_translation_uses_scale_body_position_and_com_offset(monkeypatch):
    rig = SimpleNamespace(
        human_bone_index=[0] + [-1] * 24,
        human_bone_mass=[1.0] + [0.0] * 24,
        rootx_q=(0, 0, 0, 1),
        params={"scale": 2.0},
        human=SimpleNamespace(nodes=[{"name": "Hips"}], q=[(0, 0, 0, 1)], frame_idx=(0, 0, 0, 0),
                              globals=lambda locs, trans=None: [(1.0, 2.0, 3.0)]),
    )
    monkeypatch.setattr(bodyxform, "_frame_q", lambda pos, idx: (0, 0, 0, 1))
    monkeypatch.setattr(bodyxform, "com", lambda rig, pos, symmetric_lower_arm=False: (0.5, 1.0, 1.5))
    t, q = bodyxform.pose_root(rig, {}, (0, 0, 0, 1), (0.25, 0.5, 0.75))
    assert t == pytest.approx((1.0, 2.0, 3.0))
    assert qdist(q, (0, 0, 0, 1)) < 1e-12
