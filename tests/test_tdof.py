"""Translation-DoF: sampler exposure, translation law, and hips sensitivity.

The clip curve space carries TDoF channels at attribute ``137 + 3*slot``.
Their host-joint displacement law (measured against engine playback):
``local_t = rest_t - humanScale * (v.x, v.z, v.y)``.  Dropping TDoF skews the
body-orientation frame and with it the reconstructed hips rotation.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chara.mecanim import clip as mclip
from chara.mecanim import traits


def test_tdof_attr_layout():
    # slot/component round-trip of the attribute encoding
    for slot in (0, 14, 18, 20):
        for comp in range(3):
            attr = mclip.TDOF_ATTR_BASE + 3 * slot + comp
            s, c = divmod(attr - mclip.TDOF_ATTR_BASE, 3)
            assert (s, c) == (slot, comp)
    assert traits.TDOF_BONES[14] == "LeftUpperArm"
    assert traits.TDOF_BONES[18] == "RightUpperArm"
    assert len(traits.TDOF_BONES) == 21


class _StubRig:
    """Minimal rig contract for tdof_translations."""
    params = {"scale": 0.5}
    bind_t = {"LeftArm": (1.0, 2.0, 3.0), "RightArm": (-1.0, 2.0, -3.0)}

    tdof_translations = None  # replaced below


def test_tdof_translation_law():
    from chara.mecanim.rig import Rig
    rig = _StubRig()
    out = Rig.tdof_translations(rig, {14: (0.1, 0.2, 0.4), 18: (-0.2, 0.0, 0.6)})
    # law: rest - scale * (x, z, y)
    assert out["LeftArm"] == (1.0 - 0.5 * 0.1, 2.0 - 0.5 * 0.4, 3.0 - 0.5 * 0.2)
    assert out["RightArm"] == (-1.0 + 0.5 * 0.2, 2.0 - 0.5 * 0.6, -3.0 - 0.0)
    # unknown slots and slots without a host bone are skipped, not fabricated
    assert Rig.tdof_translations(rig, {2: (1, 1, 1), 99: (1, 1, 1)}) == {}


def test_globals_translation_override_moves_child():
    from chara.mecanim.rig import HumanSkeleton

    class R:
        human_bone_index = None

        @staticmethod
        def q0(_):
            return (0.0, 0.0, 0.0, 1.0)

    nodes = [
        {"name": "Hips", "parent": -1,
         "pose": {"t": (0.0, 0.0, 0.0), "q": (0.0, 0.0, 0.0, 1.0)}},
        {"name": "LeftArm", "parent": 0,
         "pose": {"t": (1.0, 0.0, 0.0), "q": (0.0, 0.0, 0.0, 1.0)}},
        {"name": "RightArm", "parent": 0,
         "pose": {"t": (-1.0, 0.0, 0.0), "q": (0.0, 0.0, 0.0, 1.0)}},
        {"name": "LeftUpLeg", "parent": 0,
         "pose": {"t": (0.5, -1.0, 0.0), "q": (0.0, 0.0, 0.0, 1.0)}},
        {"name": "RightUpLeg", "parent": 0,
         "pose": {"t": (-0.5, -1.0, 0.0), "q": (0.0, 0.0, 0.0, 1.0)}},
    ]
    hs = HumanSkeleton(R(), nodes)
    base = hs.globals({})
    moved = hs.globals({}, {"LeftArm": (1.0, 0.0, 0.25)})
    assert base[1] == (1.0, 0.0, 0.0)
    assert moved[1] == (1.0, 0.0, 0.25)
    # override must not leak into other joints
    assert moved[2] == base[2] and moved[0] == base[0]
    # and the body frame built on those joints must actually change
    from chara.mecanim.retarget import _frame_q
    q0 = _frame_q(base, hs.frame_idx)
    q1 = _frame_q(moved, hs.frame_idx)
    d = sum(a * b for a, b in zip(q0, q1))
    assert abs(abs(d) - 1.0) > 1e-6, "frame ignored the translation override"
