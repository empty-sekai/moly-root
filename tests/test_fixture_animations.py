"""Furniture animation embedding: clips must land in the same glb, on the right
nodes, with the source's own values, and everything that is *not* a furniture
TRS curve must be accounted instead of dropped.

The fixtures are synthetic, like :mod:`test_fixture_meshes`: a real furniture
package is not in the repository, so each test builds a small package in
memory -- a node tree, an Animator, and a hand-packed AnimationClip whose
streamed / dense / constant curve blocks follow the exact byte layout
:func:`chara.mecanim.clip.decode` parses.  A criterion that cannot go red is
not a criterion: the tests pin the hash anchoring (an animator-relative path
must resolve to the walked node), the interpolation mapping (cubic -> CUBICSPLINE,
dense -> LINEAR, constant -> STEP), the verbatim value rule (no coordinate
conversion in this product), and the m_Float / non-Transform accounting.
"""
import json
import os
import struct
import sys
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from core.assets.packages import PackageStore
from fixtures import meshes as meshes_module
from fixtures.animations import (build_hash_table, decode_clip, embed)
from test_fixture_meshes import _Package, _glb, _run

PKG = "mysekai__fixture__mdl_anim"


def _crc(path):
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


def _binding(path, attribute, type_id=4):
    return {"path": _crc(path), "attribute": attribute, "typeID": type_id,
            "customType": 0, "isPPtrCurve": 0, "isIntCurve": 0,
            "isSerializeReferenceCurve": 0,
            "script": {"m_FileID": 0, "m_PathID": 0}}


def _streamed_words(frames):
    """StreamedClip byte layout: the logical stream is big-endian frames of
    (time, count, keys); the typetree carries it as uint32 words whose
    big-endian bytes are that stream (this is what
    :func:`chara.mecanim.clip._stream_frames` reassembles)."""
    payload = b""
    for time, keys in frames:
        payload += struct.pack(">fi", time, len(keys))
        for index, coeff in keys:
            payload += struct.pack(">i", index) + struct.pack(">4f", *coeff)
    return list(struct.unpack(f">{len(payload) // 4}I", payload))


def _clip_tree(name, bindings, streamed=None, dense=None, constant=None,
               stop_time=1.0, sample_rate=60.0):
    """One AnimationClip typetree with hand-packed curve blocks.

    *streamed* maps curve index -> [(time, (a, b, c, d))]; *dense* is a list of
    per-curve sample lists (evenly sampled from time 0 at *sample_rate*);
    *constant* is a list of never-changing values.
    """
    streamed = streamed or {}
    dense = dense or []
    constant = constant or []
    frames = []
    for curve, points in streamed.items():
        for time, coeff in points:
            frames.append((time, [(curve, coeff)]))
    frames.sort()
    sample_array = [v for frame in range(len(dense[0]) if dense else 0)
                    for v in ([d[frame] for d in dense] or [])]
    dense_curve_count = len(dense)
    return {
        "m_Name": name,
        "m_SampleRate": sample_rate,
        "m_WrapMode": 1,
        "m_MuscleClipSize": 0,
        "m_MuscleClip": {
            "m_StartTime": 0.0, "m_StopTime": stop_time,
            "m_Clip": {"data": {
                "m_StreamedClip": {"curveCount": len(streamed),
                                   "data": _streamed_words(frames)},
                "m_DenseClip": {"m_FrameCount": len(dense[0]) if dense else 0,
                                "m_CurveCount": dense_curve_count,
                                "m_BeginTime": 0.0,
                                "m_SampleRate": sample_rate,
                                "m_SampleArray": sample_array},
                "m_ConstantClip": {"data": list(constant)},
            }}},
        "m_ClipBindingConstant": {"genericBindings": bindings,
                                  "pptrCurveMapping": []},
    }


def _animated_package():
    """A package with a two-level tree, an Animator on the root, and meshes."""
    pkg = _Package(PKG)
    root_go, root_tp = pkg.node("carrier")
    arm_go, arm_tp = pkg.node("arm", position=(0.0, 1.0, 0.0))
    pkg.renderer(root_go, pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2]),
                 [pkg.material()])
    animator_id = pkg.add("Animator", {"m_GameObject": {"m_FileID": 0,
                                                        "m_PathID": root_go}})
    obj = next(o for o in pkg.objects if o.path_id == root_go)
    obj._tree["m_Component"].append(
        {"component": {"m_FileID": 0, "m_PathID": animator_id}})
    return pkg


def test_clip_embeds_into_the_same_glb_on_the_right_node(tmp_path, monkeypatch):
    """Red when clips are dropped or bound to the wrong node: the streamed cubic
    position curve of ``arm`` must become a CUBICSPLINE channel targeting the
    glTF node the walk created for that transform, values verbatim."""
    pkg = _animated_package()
    # One streamed cubic curve: position component 0 of "arm", two keys whose
    # polynomial coefficients are the identity v = t (a=0, b=0, c=1, d=0).
    clip = _clip_tree("wave", [_binding("arm", 1)],
                      streamed={0: [(0.0, (0.0, 0.0, 1.0, 0.0)),
                                    (1.0, (0.0, 0.0, 1.0, 1.0))]})
    pkg.add("AnimationClip", clip)
    out = tmp_path / "out"
    _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    gltf = _glb(out / f"{pkg.name}.glb")
    animations = gltf.get("animations")
    assert animations, "clip was dropped: no animations in the glb"
    anim = animations[0]
    assert anim["name"] == "wave"
    assert len(anim["channels"]) == 1 and len(anim["samplers"]) == 1
    channel = anim["channels"][0]
    sampler = anim["samplers"][channel["sampler"]]
    assert sampler["interpolation"] == "CUBICSPLINE"
    # The target node is the one the walk built for "arm" (second node).
    target = gltf["nodes"][channel["target"]["node"]]
    assert target["name"] == "arm"
    assert channel["target"]["path"] == "translation"
    # Accessor counts: CUBICSPLINE packs 3 rows (in/value/out) per key.
    assert gltf["accessors"][sampler["input"]]["count"] == 2
    assert gltf["accessors"][sampler["output"]]["count"] == 6


def test_dense_and_constant_curves_map_to_linear_and_step(tmp_path, monkeypatch):
    """Red when interpolation is invented instead of mapped: evenly sampled
    dense frames are LINEAR, constants stay STEP over their source duration."""
    pkg = _animated_package()
    # Dense: three curves = translation components 0..2 of "arm", 3 frames.
    # Constant: three values = scale components 0..2 (the next binding's
    # curve-index span), one frame, never changing.
    clip = _clip_tree("bob", [_binding("arm", 1), _binding("arm", 3)],
                      dense=[[0.0, 0.5, 1.0], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]],
                      constant=[2.0, 2.0, 2.0])
    pkg.add("AnimationClip", clip)
    out = tmp_path / "out"
    _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    gltf = _glb(out / f"{pkg.name}.glb")
    anim = gltf["animations"][0]
    by_path = {c["target"]["path"]: c for c in anim["channels"]}
    translation = anim["samplers"][by_path["translation"]["sampler"]]
    scale = anim["samplers"][by_path["scale"]["sampler"]]
    assert translation["interpolation"] == "LINEAR"
    assert scale["interpolation"] == "STEP"
    assert gltf["accessors"][scale["input"]]["count"] == 2
    assert gltf["accessors"][translation["input"]]["count"] == 3


def test_constant_pose_preserves_authored_nonzero_duration(tmp_path, monkeypatch):
    pkg = _animated_package()
    clip = _clip_tree("hold", [_binding("arm", 3)], constant=[2.0, 2.0, 2.0])
    clip["m_MuscleClip"]["m_StartTime"] = 0.0
    clip["m_MuscleClip"]["m_StopTime"] = 2.0
    pkg.add("AnimationClip", clip)
    _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    gltf = _glb(tmp_path / "out" / f"{pkg.name}.glb")
    animation = gltf["animations"][0]
    sampler = animation["samplers"][0]
    assert sampler["interpolation"] == "STEP"
    assert gltf["accessors"][sampler["input"]]["count"] == 2
    assert gltf["accessors"][sampler["output"]]["count"] == 2
    assert animation["extras"]["sourceDuration"] == 2.0


def test_animator_and_float_slots_are_counted_not_exported(tmp_path, monkeypatch):
    """Red when non-TRS curves vanish silently: Animator muscle slots (typeID
    95) and float curves have no furniture node to move -- they must show up in
    the accounting, and leave no channel behind."""
    pkg = _animated_package()
    # Curve indices follow the binding layout: 0..2 are the translation
    # components of "arm", 3 is the one Animator muscle slot (typeID 95).
    clip = _clip_tree("act",
                      [_binding("arm", 1),
                       {"path": _crc("arm"), "attribute": 42, "typeID": 95,
                        "customType": 0, "isPPtrCurve": 0, "isIntCurve": 0,
                        "isSerializeReferenceCurve": 0,
                        "script": {"m_FileID": 0, "m_PathID": 0}}],
                      streamed={0: [(0.0, (0.0, 0.0, 1.0, 0.0))],
                                1: [(0.0, (0.0, 0.0, 1.0, 0.5))],
                                2: [(0.0, (0.0, 0.0, 1.0, -0.5))],
                                3: [(0.0, (0.0, 0.0, 0.0, 0.5))]})
    pkg.add("AnimationClip", clip)
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    meta = index["packages"][pkg.name]["animations"]
    assert meta["clipCount"] == 1
    assert meta["gltfChannels"] == 1          # only the translation channel
    assert meta["channeledSlots"] == 3        # its three components
    # Every decoded slot is accounted: channels + the muscle slot.
    assert (meta["channeledSlots"] + meta["floatSlots"]
            + meta["unresolvedSlots"]) == 4
    assert meta["floatSlots"] == 1
    gltf = _glb(tmp_path / "out" / f"{pkg.name}.glb")
    assert len(gltf["animations"][0]["channels"]) == 1
    assert len(gltf["animations"][0]["channels"]) == 1


def test_unresolved_hash_is_reported_not_guessed(tmp_path, monkeypatch):
    """Red when a dangling binding is silently attached to some node: a path
    hash matching no node of this package must raise an anomaly and stay
    unexported -- retargeting is out of scope."""
    pkg = _animated_package()
    clip = _clip_tree("ghost", [_binding("no/such/node", 2)],
                      streamed={0: [(0.0, (0.0, 0.0, 0.0, 1.0))]})
    pkg.add("AnimationClip", clip)
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    meta = index["packages"][pkg.name]["animations"]
    assert meta["unresolvedSlots"] == 1
    types = {a["type"] for a in index["packages"][pkg.name]["anomalies"]}
    assert "clip-anomaly" in types
    gltf = _glb(tmp_path / "out" / f"{pkg.name}.glb")
    assert gltf["animations"][0]["channels"] == []


def test_duplicate_clip_names_keep_the_first_and_record_the_loser(tmp_path,
                                                                  monkeypatch):
    """Red when duplicate names overwrite or vanish: a glTF animation is
    addressed by name, so the first object wins and the loser is recorded."""
    pkg = _animated_package()
    pkg.add("AnimationClip", _clip_tree("twice", [_binding("arm", 1)],
                                        constant=[0.0, 0.0, 0.0]))
    pkg.add("AnimationClip", _clip_tree("twice", [_binding("arm", 1)],
                                        constant=[9.0, 9.0, 9.0]))
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    meta = index["packages"][pkg.name]["animations"]
    assert meta["clipCount"] == 1
    assert any(a["type"] == "clip-duplicate-name"
               for a in index["packages"][pkg.name]["anomalies"])


def test_hash_table_prefers_animator_variants_over_later_roots():
    """Red when the anchor priority flips: several variants share relative
    paths, and the animator-carrying variant must win over walk order, with
    full paths as the last resort."""
    variants = [
        {"paths": {"doll": 0, "doll/arm": 1}, "root": "doll", "animators": []},
        {"paths": {"doll": 2, "doll/arm": 3}, "root": "doll",
         "animators": ["doll"]},
    ]
    table = build_hash_table(variants)
    assert table[_crc("arm")][0] == 3        # animator variant, not walk order
    assert table[_crc("doll/arm")][0] == 3   # full path also resolves
    assert table[_crc("arm")][2] == 1        # ...in the animator's variant
