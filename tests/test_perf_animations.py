"""Fixture and cut-scene AnimationClip export: synthetic tests.

These tests exercise the export code with synthetic clip typetrees and synthetic
hierarchies.  Synthetic fixtures test code only — no game semantics are inferred
from them.

The planted-violation tests are built on **removing or corrupting the decoded
data / the resolved hierarchy**, not on "not doing a step" — so they stay red
even if the exporter later gains a lazy-loading or fallback path: a control built
on "the data is not there" survives a fallback; one built on "do not call this"
dies under it.  Each red condition answers "**what input
makes it red**", and each is asserted red before the corresponding fidelity fix
is applied.
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import perf.animations as animations  # noqa: E402
from chara.mecanim.clip import ANIMATOR_TYPEID, TRANSFORM_TYPEID  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic clip typetree helpers
# ---------------------------------------------------------------------------
def _xyz(coeff, slot=0):
    """The three curve slots a Transform vector binding occupies.

    Unity allocates one slot per component, so a translation binding at base
    *slot* owns ``slot..slot+2``.  Handing the decoder only the first is a shape
    the game never ships (the real corpus raises zero "incomplete component
    set" anomalies over 1769 animations), and the exporter rightly declines to
    fabricate the missing two rather than zero-filling them into the export."""
    return [(slot, coeff), (slot + 1, (0.0, 0.0, 0.0, 0.0)),
            (slot + 2, (0.0, 0.0, 0.0, 0.0))]


def _streamed_payload(frames):
    """Pack frame (time, [(idx, (a, b, c, d))]) into the big-endian payload the
    decoder expects: ``>f i`` then per key ``>i 4f``.  Returns a list of uint32
    words (the ``data`` field the decoder reinterprets)."""
    raw = b""
    for time, keys in frames:
        raw += struct.pack(">f", time)
        raw += struct.pack(">i", len(keys))
        for idx, coeff in keys:
            raw += struct.pack(">i", idx)
            raw += struct.pack(">4f", *coeff)
    if len(raw) % 4:
        raw += b"\x00" * (4 - len(raw) % 4)
    return [int.from_bytes(raw[i:i + 4], "big") for i in range(0, len(raw), 4)]


def _flow_times(payload):
    """Recompute the streamed key times a decoder would see (for assertions)."""
    raw = b"".join(int(v).to_bytes(4, "big") for v in payload)
    off, times = 0, []
    while off + 8 <= len(raw):
        t, count = struct.unpack_from(">f i", raw, off)
        off += 8
        for _ in range(count):
            off += 20
        if abs(t) <= 3.0e38:                    # not the -FLT_MAX sentinel
            times.append(t)
    return times


def _synthetic_typetree(bindings, streamed_payload=(), curve_count=0,
                        const_data=()):
    """Build a clip typetree shaped exactly as the decoder expects.

    *bindings* is the ``m_ClipBindingConstant.genericBindings`` list.  Streamed
    curve indices come straight from the payload; constant curves occupy the
    region ``curve_count .. curve_count + len(const_data)`` (dense=0).  A second
    binding with a distinct attribute exercises the c6 non-transform path.
    """
    return {"m_ClipBindingConstant": {"genericBindings": list(bindings)},
            "m_MuscleClip": {"m_StopTime": 1.0, "m_Clip": {"data": {
                "m_StreamedClip": {"data": list(streamed_payload),
                                   "curveCount": int(curve_count)},
                "m_DenseClip": {"m_CurveCount": 0, "m_FrameCount": 0,
                                "m_BeginTime": 0.0, "m_SampleRate": 60.0,
                                "m_SampleArray": []},
                "m_ConstantClip": {"data": list(const_data)}}}}}


def _transform_typetree(path_hash, payload, *, curve_count=0):
    """A clip with a single translation binding at *path_hash*."""
    binding = {"typeID": TRANSFORM_TYPEID, "attribute": 1, "path": path_hash}
    return _synthetic_typetree([binding], streamed_payload=payload,
                               curve_count=curve_count)


def _mix_typetree(path_hash, payload, *, curve_count=0, animator_count=1):
    """A clip with a translation Transform binding and an animator (typeID 95)
    muscle binding, to exercise the c6 non-transform classification path."""
    bindings = [{"typeID": TRANSFORM_TYPEID, "attribute": 1, "path": path_hash}]
    for i in range(animator_count):
        bindings.append({"typeID": ANIMATOR_TYPEID, "attribute": 42 + i,
                         "path": 0})
    return _synthetic_typetree(bindings, streamed_payload=payload,
                               curve_count=curve_count)


class _FakeObject:
    """Minimal UnityPy-object stand-in: a Transform or GameObject as the
    hierarchy builder reads it."""
    def __init__(self, type_name, path_id, typetree):
        self.type = type(type("T", (), {"name": type_name})())
        self.path_id = path_id
        self._tree = typetree

    def read_typetree(self):
        return self._tree


def _synthetic_hierarchy(anchor_pid, nodes):
    """Build a GameObject/Transform object list.

    *nodes* is a list of ``(path_id, name, parent_pid)``.  The object owning the
    Animator is *anchor_pid*'s transform (an Animator is attached to its GO).
    """
    objs = []
    for pid, name, parent in nodes:
        gameobject_off = pid + 1000
        objs.append(_FakeObject("Transform", pid, {
            "m_GameObject": {"m_PathID": gameobject_off},
            "m_Father": {"m_PathID": parent or 0},
            "m_LocalPosition": {"x": 0, "y": 0, "z": 0},
            "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
            "m_LocalScale": {"x": 1, "y": 1, "z": 1},
        }))
        objs.append(_FakeObject("GameObject", gameobject_off, {"m_Name": name}))
    objs.append(_FakeObject("Animator", anchor_pid + 2000,
                            {"m_GameObject": {"m_PathID": anchor_pid + 1000}}))
    return objs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _crc(path):
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


def _joint_hierarchy():
    """Two-node hierarchy: anchor "root" (its animator path is ""), child
    "joint" whose animator-relative path is "joint".  Bind "joint" for any
    test that needs a resolvable path string."""
    return animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0), (2, "joint", 1)]))


def _kv_from_payload(payload, slot=0):
    """Reconstruct [(time, (a, b, c, d))] for one streamed curve slot."""
    raw = b"".join(int(v).to_bytes(4, "big") for v in payload)
    off, pts = 0, []
    while off + 8 <= len(raw):
        t, count = struct.unpack_from(">f i", raw, off)
        off += 8
        for _ in range(count):
            vals = struct.unpack_from(">i 4f", raw, off)
            off += 20
            if vals[0] == slot:
                pts.append((t, tuple(vals[1:])))
    return sorted(pts)


def approx(a, b, tol=1e-4):
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# 1. Path-string preservation (c5).
# ---------------------------------------------------------------------------
def test_path_hash_roundtrips_to_readable_path():
    """A binding hash must resolve back to the exact animator-relative string."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0), (2, "controller", 1)]))
    assert hierarchy.resolve(_crc("controller")) == "controller"
    assert hierarchy.node_index("controller") == 1


def test_path_normalised_is_a_planted_violation():
    """The animator-relative binding string is the exact anchor-relative path.

    With "root" as the animator anchor, its own path is "" and a child chain
    is "j", "j/k" — the binding hash must resolve to that exact string, never a
    GameObject-absolute "root/j/k" and never a normalised rewrite.  A normalising
    re-lexer would produce a different string and the equality goes red."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0), (2, "j", 1), (3, "k", 2)]))
    # The animator-relative path is "j/k"; the anchor name is not prepended to
    # it.  The root-name form is registered too -- deliberately, because a
    # package may carry several animators and a binding may be hashed from the
    # root-name path -- but each hash must resolve to its OWN literal string.
    # A normalising re-lexer would rewrite one into the other and go red here.
    assert hierarchy.resolve(_crc("j/k")) == "j/k"
    assert hierarchy.path_by_hash[_crc("j/k")] == "j/k",         "the animator-relative path must not retain the anchor name"
    if _crc("root/j/k") in hierarchy.path_by_hash:
        assert hierarchy.path_by_hash[_crc("root/j/k")] == "root/j/k",             "a root-anchored variant must resolve to its own literal string"


# ---------------------------------------------------------------------------
# 2. "Unchanged" channel must NOT be dropped (const still exported).
# ---------------------------------------------------------------------------
def test_const_channel_is_exported_not_dropped():
    """A constant (never-changing) channel must still produce a glTF channel —
    dropping it is a planted fidelity violation.  A const curve occupies the
    constant region (curve_count .. +1) and decodes to STEP.
    """
    hierarchy = _joint_hierarchy()
    clip = _transform_typetree(_crc("joint"), (), curve_count=0)
    # put one const value in the constant region -> a 1-component translation
    clip["m_MuscleClip"]["m_Clip"]["data"]["m_ConstantClip"]["data"] = [7.0, 0.0, 0.0]
    channels, anomalies = animations.decode_transform_channels(clip, hierarchy)
    assert channels, "a const transform channel was dropped"
    assert channels[0]["interpolation"] == "STEP"
    assert channels[0]["times"] == [0.0]
    assert channels[0]["values"] == [[7.0, 0.0, 0.0]]
    assert anomalies == []


# ---------------------------------------------------------------------------
# 3. No resampling (c4) — source keyframe times are verbatim.
# ---------------------------------------------------------------------------
def _three_key_kit():
    """Irregular source key times so a uniform re-sampler would genuinely
    diverge (always 0.0/0.5/1.0 grid already matches a uniform grid and a
    resampling test could not distinguish them)."""
    frames = [(0.0, _xyz((0.0, 0.0, 1.0, 5.0))),
              (0.37, _xyz((0.0, 0.0, 2.0, 7.0))),
              (0.61, _xyz((0.0, 0.0, 0.0, 9.0))),
              (1.0, _xyz((0.0, 0.0, 0.0, 11.0)))]
    return frames, _streamed_payload(frames)


def test_no_resampling_keyframe_times_verbatim():
    """Keyframe times are copied verbatim, never recomputed at a sample rate."""
    frames, payload = _three_key_kit()
    hierarchy = _joint_hierarchy()
    clip = _transform_typetree(_crc("joint"), payload, curve_count=0)
    channels, _ = animations.decode_transform_channels(clip, hierarchy)
    # float32 key times carry a small rounding error; compare with tolerance.
    expected = [0.0, 0.37, 0.61, 1.0]
    got = channels[0]["times"]
    assert len(got) == len(expected)
    assert all(approx(g, e, 1e-5) for g, e in zip(got, expected)), \
        "keyframes were resampled"


def test_resampling_is_a_planted_violation():
    """RED when keyframe times are re-mapped onto a uniform sample grid.

    The source times (0.0, 0.5, 1.0) must survive verbatim; a uniform sampler
    would emit (0.0, 0.5, 1.0) too here, so instead assert the exact source set
    (`_flow_times`) matches — a grid at a different resolution (e.g. step 0.1)
    would diverge."""
    frames, payload = _three_key_kit()
    hierarchy = _joint_hierarchy()
    clip = _transform_typetree(_crc("joint"), payload, curve_count=0)
    channels, _ = animations.decode_transform_channels(clip, hierarchy)
    assert channels[0]["times"] == _flow_times(payload), \
        "keyframe times must equal the source, not a uniform grid"


# ---------------------------------------------------------------------------
# 4. Cubic tangent recovery (c4 value/tangent fidelity).
# ---------------------------------------------------------------------------
def test_cubic_value_and_tangent_recovery():
    """For a cubic stream, value = d, out-tangent = c, in-tangent =
    3a·Δt² + 2b·Δt + c."""
    points = animations._channel_points("cubic", [
        (0.0, (0.0, 0.0, 1.0, 5.0)),
        (1.0, (0.0, 0.0, 2.0, 7.0))])
    assert points[0][1] == 5.0, "value must be the polynomial d"
    assert points[0][3] == 1.0, "out-tangent must be c"
    assert approx(points[0][2], 1.0), \
        "in-tangent must be 3a·dt² + 2b·dt + c (a=0,b=0,c=1,dt=1)"
    # second key has no successor -> its in-tangent is 0.0
    assert points[1][2] == 0.0


def test_value_fidelity_const_and_cubic():
    """The exported keyframe values match evaluating the decoded curve at the
    source times (c4).  For cubic, value at a key = d."""
    frames, payload = _three_key_kit()
    hierarchy = _joint_hierarchy()
    clip = _transform_typetree(_crc("joint"), payload, curve_count=0)
    channels, _ = animations.decode_transform_channels(clip, hierarchy)
    pts = _kv_from_payload(payload, slot=0)
    # values are the polynomial d at each key time (1-component translation)
    expected = [p[1][3] for p in pts]
    got = [channels[0]["values"][i][0] for i in range(len(expected))]
    for e, g in zip(expected, got):
        assert approx(g, e), f"value mismatch: exported {g}, source {e}"


# ---------------------------------------------------------------------------
# 5. c6 anomaly classification: non-Transform binding classified, not dropped.
# ---------------------------------------------------------------------------
def test_non_transform_binding_is_classified_not_skipped():
    """An animator/muscle curve (typeID 95) is reported as a classified
    anomaly, never silently skipped (no catch-all).  The transform channel still
    decodes."""
    hierarchy = _joint_hierarchy()
    # transform curve (slot 0) + animator muscle curve (slot 3, per the
    # curve-index layout: translation binding occupies 0-2, animator follows).
    payload = _streamed_payload([
        (0.0, _xyz((0, 0, 1, 5.0)) + [(3, (0, 0, 0, 0.5))])])
    clip = _mix_typetree(_crc("joint"), payload, curve_count=0, animator_count=1)
    curves, channels, _ = animations.decode_clip(clip, hierarchy)
    # An animator (typeID 95) curve has no glTF channel and never will, but
    # "no channel" must not mean "no data": it is carried in the verbatim table
    # with its binding tuple intact.  An earlier revision dropped these and
    # silently emptied 802 clips -- that is what this asserts against.
    animator = [c for c in curves if c["typeID"] == ANIMATOR_TYPEID]
    assert animator, "the animator curve was dropped instead of exported"
    assert animator[0]["times"] and animator[0]["values"],         "the animator curve was exported with no data"
    assert len(channels) == 1, "the transform channel must still decode"


def test_unknown_reason_is_a_catch_all_violation():
    """RED if any anomaly reason is the catch-all 'other'/'unknown'/None.

    The classification must be precise — a broad bucket is what the gate
    forbids.  On the real decoder every reason is a concrete label."""
    hierarchy = _joint_hierarchy()
    clip = _transform_typetree(_crc("joint"), _streamed_payload(
        [(0.0, [(0, (0, 0, 1, 5.0))])]), curve_count=0)
    # plant a hop that the real decoder resolves: an unresolved hash binding.
    clip["m_ClipBindingConstant"]["genericBindings"][0]["path"] = 0xFFFFFFFF
    _, anomalies = animations.decode_transform_channels(clip, hierarchy)
    assert all(a.get("reason") not in (None, "other", "unknown") for a in anomalies)
    assert anomalies, "an unresolved binding must be reported, not swallowed"


# ---------------------------------------------------------------------------
# 6. Node-index mapping (glTF channel targets a real node).
# ---------------------------------------------------------------------------
def test_channel_targets_existing_node():
    """A decoded channel must point at a node present in the hierarchy."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0), (2, "joint", 1)]))
    clip = _transform_typetree(_crc("joint"), _streamed_payload(
        [(0.0, _xyz((0, 0, 1, 3.0)))]), curve_count=0)
    channels, _ = animations.decode_transform_channels(clip, hierarchy)
    assert channels[0]["node"] == hierarchy.node_index("joint")
    assert channels[0]["node"] == 1


def test_unresolved_binding_reported_as_path_hash_unresolved():
    """A binding hash with no matching node is classified as 'path hash
    unresolved' — not silently dropped and not a catch-all."""
    hierarchy = animations.NodeHierarchy(_synthetic_hierarchy(1, [(1, "root", 0)]))
    clip = _transform_typetree(999999999, _streamed_payload(
        [(0.0, [(0, (0, 0, 1, 3.0))])]), curve_count=0)
    channels, anomalies = animations.decode_transform_channels(clip, hierarchy)
    assert channels == [], "unresolved hash must not produce a bogus channel"
    reasons = {a["reason"] for a in anomalies}
    # The class carries the evidence that placed it there — here, that no other
    # binding of the clip resolved either — so the reason string is more
    # specific than the bare label while still naming it.
    assert animations.UNRESOLVED_REASON in reasons
    details = {a.get("detail") for a in anomalies}
    assert animations.UNRESOLVED_DETAIL[animations.UNRESOLVED_CLASS_NONE] in details
    # The fourth class is named in the record, not signalled by an absent key:
    # absence cannot be told apart from a producer that never wrote the field.
    unresolved = [a for a in anomalies if a["reason"] == animations.UNRESOLVED_REASON]
    assert unresolved and all(
        a["classification"] == animations.UNRESOLVED_CLASS_NONE for a in unresolved)


# ---------------------------------------------------------------------------
# 7. Resolution against a rig that is not in the animation's own package.
#
# A performance clip may bind to a hierarchy that ships elsewhere -- the
# furniture model's own rig, or a character rig.  Resolution may only *add*
# results: it is consulted after the package's own anchored table has missed,
# it never rewrites the raw hash, and a hash it cannot match stays unresolved
# and classified rather than being pinned on some arbitrary node.
#
# Both planted violations here are built by taking data away or corrupting it
# (an empty rig set; a rig whose node name is off by one character), never by
# "skipping a step" -- so a later lazy-load or fallback in the exporter cannot
# quietly disarm them.
# ---------------------------------------------------------------------------
def _rig_doc(names, translation=(1.5, 2.5, 3.5)):
    """A synthetic glTF hierarchy: *names* chained root-first."""
    nodes = []
    for i, name in enumerate(names):
        node = {"name": name, "translation": list(translation),
                "rotation": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 1.0, 1.0]}
        if i + 1 < len(names):
            node["children"] = [i + 1]
        nodes.append(node)
    return {"nodes": nodes}


def _foreign_rigs(names=("Root", "Hips", "Spine"), kind=None, package="rig_pkg"):
    kind = kind or animations.FOREIGN_KIND_FIXTURE
    return animations.ForeignRigs([animations.ForeignRig(kind, package,
                                                         _rig_doc(names))])


def _clip_on(path, payload_value=3.0):
    return _transform_typetree(_crc(path), _streamed_payload(
        [(0.0, _xyz((0, 0, 1, payload_value)))]), curve_count=0)


def test_foreign_rig_resolves_a_hash_the_package_cannot():
    """A binding whose target is not in this package resolves against the rig
    that owns it, and becomes a playable channel on an imported node."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=_foreign_rigs())
    clip = _clip_on("Root/Hips/Spine")
    curves, channels, anomalies = animations.decode_clip(clip, hierarchy)
    assert hierarchy.resolve(_crc("Root/Hips/Spine")) == "Root/Hips/Spine"
    assert len(channels) == 1, "resolved binding must yield a playable channel"
    assert channels[0]["node"] >= hierarchy.nativeNodes, "must be an imported node"
    assert curves[0]["pathSource"] == animations.FOREIGN_KIND_FIXTURE
    assert not [a for a in anomalies
                if str(a["reason"]).startswith("path hash unresolved")]
    # The whole ancestor chain comes with it, so the local transform means
    # something: Root -> Hips -> Spine.
    counts = hierarchy.foreign_counts()
    assert counts["total"] == 1 and counts["importedNodes"] == 3
    assert counts["packages"] == ["rig_pkg"]


def test_own_hierarchy_outranks_the_foreign_rig():
    """A hash the package itself can answer must never be taken from a rig."""
    rigs = _foreign_rigs(names=("joint",))
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0), (2, "joint", 1)]), foreign=rigs)
    clip = _clip_on("joint")
    curves, channels, _ = animations.decode_clip(clip, hierarchy)
    assert channels[0]["node"] == hierarchy.node_index("joint") == 1
    assert channels[0]["node"] < hierarchy.nativeNodes
    assert curves[0]["pathSource"] == "this package"
    assert hierarchy.foreign_counts()["total"] == 0
    assert hierarchy.foreign_counts()["importedNodes"] == 0


def test_foreign_resolution_never_rewrites_the_raw_hash():
    """Resolution adds a path string and a node; the stored binding target
    stays the source's own crc32 integer."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=_foreign_rigs())
    digest = _crc("Root/Hips/Spine")
    curves, channels, _ = animations.decode_clip(_clip_on("Root/Hips/Spine"),
                                                 hierarchy)
    assert curves[0]["path"] == digest
    assert _crc(curves[0]["pathString"]) == digest
    assert channels[0]["pathHash"] == digest


def test_imported_rig_node_keeps_its_frame_and_stays_out_of_the_scene():
    """An imported node carries its source frame and its provenance, and does
    not become a root of the package's own scene."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=_foreign_rigs())
    animations.decode_clip(_clip_on("Root/Hips/Spine"), hierarchy)
    glb = animations.GLB()
    roots = hierarchy.write_scene(glb)
    glb.g["scenes"][0]["nodes"] = roots
    assert roots == [0], "only the package's own root belongs to its scene"
    imported = glb.g["nodes"][hierarchy.nativeNodes:]
    assert len(imported) == 3
    assert all(n["extras"]["foreign"]["package"] == "rig_pkg" for n in imported)
    # Copied through, not run through the Unity->glTF conversion a second time.
    assert imported[0]["translation"] == [1.5, 2.5, 3.5]


def test_rig_name_off_by_one_character_is_a_planted_violation():
    """PLANTED VIOLATION (corrupted data): the rig is present but one node name
    differs by a single character, so its hash no longer matches the binding.
    The exporter must report the hash unresolved -- not accept the near miss,
    and not silently pin the channel on some other node."""
    good = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=_foreign_rigs())
    _, good_channels, _ = animations.decode_clip(_clip_on("Root/Hips/Spine"),
                                                 good)
    assert good_channels, "control: the intact rig does resolve this binding"

    broken = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]),
        foreign=_foreign_rigs(names=("Root", "Hips", "Spinf")))
    curves, channels, anomalies = animations.decode_clip(
        _clip_on("Root/Hips/Spine"), broken)
    assert channels == [], "a near-miss hash must not produce a channel"
    assert curves[0]["node"] is None and curves[0]["pathString"] is None
    assert broken.foreign_counts()["importedNodes"] == 0, (
        "nothing may be imported for a hash that did not match")
    assert [a for a in anomalies
            if str(a["reason"]).startswith("path hash unresolved")]


def test_empty_rig_set_resolves_nothing_planted_violation():
    """PLANTED VIOLATION (data removed): with no rigs on hand -- the state the
    c10 positive control creates by pointing at an empty directory -- the same
    binding must go back to unresolved, and the resolved count must be 0."""
    empty = animations.ForeignRigs([])
    assert not empty and empty.paths == 0
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=empty)
    curves, channels, anomalies = animations.decode_clip(
        _clip_on("Root/Hips/Spine"), hierarchy)
    assert channels == []
    assert curves[0]["pathSource"] is None
    assert hierarchy.foreign_counts()["total"] == 0
    assert [a for a in anomalies
            if str(a["reason"]).startswith("path hash unresolved")]


def test_unresolved_hash_is_classified_by_its_clip_siblings():
    """The residual class is read off the clip's other bindings, so it states
    evidence rather than guessing: a clip that resolved bones on a character rig
    reports its leftover hash against that class."""
    rigs = _foreign_rigs(names=("Root", "Hips", "Spine"),
                         kind=animations.FOREIGN_KIND_CHARACTER,
                         package="chara_rig")
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=rigs)
    clip = _synthetic_typetree(
        [{"typeID": TRANSFORM_TYPEID, "attribute": 1,
          "path": _crc("Root/Hips/Spine")},
         {"typeID": TRANSFORM_TYPEID, "attribute": 1, "path": 4242424242}],
        streamed_payload=_streamed_payload(
            [(0.0, _xyz((0, 0, 1, 3.0)) + _xyz((0, 0, 1, 4.0), slot=3))]),
        curve_count=0)
    _, _, anomalies = animations.decode_clip(clip, hierarchy)
    residual = [a for a in anomalies
                if str(a["reason"]).startswith("path hash unresolved")]
    assert len(residual) == 1 and residual[0]["pathHash"] == 4242424242
    assert residual[0]["classification"] == animations.FOREIGN_KIND_CHARACTER
    assert residual[0]["detail"] == \
        animations.UNRESOLVED_DETAIL[animations.FOREIGN_KIND_CHARACTER]
    assert residual[0]["resolvedSiblingSources"] == \
        [animations.FOREIGN_KIND_CHARACTER]


# ---------------------------------------------------------------------------
# 8. Where each resolution came from (c18), and where each curve slot went (c17).
#
# Both numbers used to be unreadable, and each hid a different failure:
#
# * A single "resolved" count cannot tell an animator-relative hash (what Unity
#   actually stores: crc32("Root/Hips")) from a whole-prefab-path hash
#   (crc32("mdl_sd_101_001/Root/Hips")).  The rig-root-relative variant is the
#   one that must carry a character rig, because that rig ships wrapped in a
#   prefab node the binding path does not contain.
# * ``channeled`` counts curve *slots* while ``gltfChanneled`` counts grouped
#   *channels*, so their difference is not a drop count.  Only an exhaustive
#   per-slot census says what the playable view left out, and why.
#
# The planted violations take data away -- index entries, curve records, an
# emitted channel -- so no amount of extra fallback in the exporter can disarm
# them.
# ---------------------------------------------------------------------------
def _wrapped_rig(package="chara_rig"):
    """A character rig as it really ships: the skeleton under a prefab node.

    Root-relative paths are "", "Root", "Root/Hips"; full node paths are
    "mdl_sd_101_001", "mdl_sd_101_001/Root", "mdl_sd_101_001/Root/Hips".  A clip
    binds the former -- that is the whole point of the distinction.
    """
    return animations.ForeignRigs([animations.ForeignRig(
        animations.FOREIGN_KIND_CHARACTER, package,
        _rig_doc(("mdl_sd_101_001", "Root", "Hips")))])


def test_wrapped_rig_resolves_by_rig_root_relative_path():
    """crc32("Root/Hips") resolves against a rig whose top node is the prefab
    wrapper, and the hit is reported as rig-root-relative -- not as the
    full-node-path variant, which names a different string entirely."""
    rigs = _wrapped_rig()
    assert rigs.rigs[0].variant_by_path["Root/Hips"] == \
        animations.VARIANT_ROOT_RELATIVE
    assert rigs.rigs[0].variant_by_path["mdl_sd_101_001/Root/Hips"] == \
        animations.VARIANT_FULL_PATH
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=rigs)
    _, channels, _ = animations.decode_clip(_clip_on("Root/Hips"), hierarchy)
    assert len(channels) == 1
    sources = hierarchy.resolution_sources()
    assert sources["foreign"] == {animations.VARIANT_ROOT_RELATIVE: 1}
    assert sources["native"] == {}
    assert hierarchy.foreign_counts()["byVariant"] == \
        {animations.VARIANT_ROOT_RELATIVE: 1}


def test_dropping_the_root_relative_index_is_a_planted_violation():
    """PLANTED VIOLATION (data removed): strip the rig-root-relative entries
    out of the built index, leaving only the whole-prefab-path variant -- the
    state the exporter would be in if it indexed foreign rigs by full node path.
    crc32("Root/Hips") must then go unresolved and the rig-root-relative count
    must be 0, which is what c18 turns red on."""
    control = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=_wrapped_rig())
    _, control_channels, _ = animations.decode_clip(_clip_on("Root/Hips"),
                                                    control)
    assert control_channels, "control: the intact index does resolve Root/Hips"

    rig = _wrapped_rig().rigs[0]
    for path, variant in list(rig.variant_by_path.items()):
        if variant == animations.VARIANT_ROOT_RELATIVE:
            del rig.index_by_path[path]
            del rig.variant_by_path[path]
    broken = animations.ForeignRigs([rig])          # rebuild the hash table
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=broken)
    curves, channels, anomalies = animations.decode_clip(
        _clip_on("Root/Hips"), hierarchy)
    assert channels == [], "a whole-prefab-path index must not resolve Root/Hips"
    assert curves[0]["node"] is None and curves[0]["pathString"] is None
    sources = hierarchy.resolution_sources()
    assert sources["foreign"].get(animations.VARIANT_ROOT_RELATIVE, 0) == 0
    assert [a for a in anomalies
            if str(a["reason"]).startswith("path hash unresolved")]


def _mixed_clip():
    """One clip carrying all three fates at once: a Transform binding that
    resolves, a Transform binding that resolves nowhere, and an Animator
    (typeID 95) muscle binding glTF has no channel for."""
    bindings = [{"typeID": TRANSFORM_TYPEID, "attribute": 1,
                 "path": _crc("Root/Hips")},
                {"typeID": TRANSFORM_TYPEID, "attribute": 1,
                 "path": 4242424242},
                {"typeID": ANIMATOR_TYPEID, "attribute": 42, "path": 0}]
    payload = _streamed_payload([(0.0, _xyz((0, 0, 1, 3.0))
                                 + _xyz((0, 0, 1, 4.0), slot=3)
                                 + _xyz((0, 0, 1, 5.0), slot=6)[:1])])
    return _synthetic_typetree(bindings, streamed_payload=payload,
                               curve_count=0)


def test_every_curve_slot_lands_in_exactly_one_class():
    """The census is exhaustive and residual-free, and it separates the three
    fates by evidence: the Animator slot is named after its own typeID, the
    unresolved Transform slots after their failure, and neither is folded into
    the playable class."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=_wrapped_rig())
    curves, channels, _ = animations.decode_clip(_mixed_clip(), hierarchy)
    counts = animations.curve_accounting(curves, channels)
    assert sum(counts.values()) == len(curves), "residual must be 0"
    assert counts[animations.CLASS_GLTF] == 3, "one xyz channel = three slots"
    assert counts[animations.CLASS_UNRESOLVED] == 3
    assert counts["typeid-%d-is-not-a-transform-binding" % ANIMATOR_TYPEID] == 1
    assert not {"other", "unknown", "unclassified", ""} & set(counts)


def test_unaccounted_curve_slots_are_a_planted_violation():
    """PLANTED VIOLATION (data removed): drop the unresolved Transform slots
    from the records handed to the census -- the shape a silent discard has.
    The classified total must then stop matching the decoded slot count, which
    is the residual c17 turns red on."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=_wrapped_rig())
    curves, channels, _ = animations.decode_clip(_mixed_clip(), hierarchy)
    assert sum(animations.curve_accounting(curves, channels).values()) == \
        len(curves), "control: the intact record set has residual 0"

    kept = [c for c in curves if not (c["typeID"] == TRANSFORM_TYPEID
                                      and c["pathString"] is None)]
    assert len(kept) < len(curves), "the plant must actually remove something"
    counts = animations.curve_accounting(kept, channels)
    assert sum(counts.values()) != len(curves)
    assert len(curves) - sum(counts.values()) == 3, "residual is the drop count"
    assert animations.CLASS_UNRESOLVED not in counts


def test_removing_an_emitted_channel_moves_slots_out_of_the_playable_class():
    """PLANTED VIOLATION (data removed): take the written channel away and the
    slots that fed it must stop counting as playable.  This is what keeps c17's
    artifact-side census honest -- it reads the channels the .glb actually
    holds, so a slot cannot be reported playable when no channel carries it."""
    hierarchy = animations.NodeHierarchy(
        _synthetic_hierarchy(1, [(1, "root", 0)]), foreign=_wrapped_rig())
    curves, channels, _ = animations.decode_clip(_mixed_clip(), hierarchy)
    assert animations.curve_accounting(curves, channels)[
        animations.CLASS_GLTF] == 3

    counts = animations.curve_accounting(curves, [])
    assert animations.CLASS_GLTF not in counts
    assert counts[animations.CLASS_UNGROUPED] == 3
    assert sum(counts.values()) == len(curves), "still exhaustive"
