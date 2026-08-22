"""Humanoid AnimationClip decoding.

A humanoid clip stores no bone rotations.  Its sampled data is three curve
segments over one flat curve-index space:

* ``StreamedClip`` — fast-moving curves; each key stores cubic polynomial
  coefficients ``(a, b, c, d)`` evaluated as ``v(dt) = ((a·dt+b)·dt+c)·dt+d``
  with ``dt = t − keyTime``.  (Cubic, *not* linear: self-proved by
  extrapolating each key to its successor's time — median error 5.8e-10 over
  1182 key pairs.)  The first frame carries a ``-FLT_MAX`` time sentinel.
* ``DenseClip`` — evenly sampled curves, linear between samples.  It holds one
  frame *more* than ``m_StopTime`` implies.
* ``ConstantClip`` — curves that never change.

Curve-index layout (verified bit-for-bit against ``m_StartX``):
indices 7..9 root translation, 10..13 root rotation (== ``bodyRotation``),
14..41 four IK goals (unused for playback — muscles + body rotation already
equal the final pose), 42..136 the 95 muscles, 137+ translation DoF
(``137 + 3*slot + component``; see :data:`.traits.TDOF_BONES`).  TDoF
channels translate their host joints and therefore shift the body-orientation
frame — dropping them skews the reconstructed hips rotation by whole degrees
on pose-heavy clips.

Muscle values are *not* confined to [-1, 1]: official content reaches
-12.06..+9.31 (all overshoot on finger spread/thumb channels).  Do **not**
clamp before mapping — the engine allows overextension, and clamping breaks
every bone that overshoots.
"""
import collections
import math
import struct

MUSCLE_ATTR_BASE = 42            # attribute 42 == muscle 0
MUSCLE_ATTR_LAST = 136
TDOF_ATTR_BASE = 137             # attribute 137 + 3*slot + component
ROOT_T_BASE = 7                  # attributes 7..9
ROOT_Q_BASE = 10                 # attributes 10..13
ATTR_SIZE = {1: 3, 2: 4, 3: 3, 4: 3}   # Transform position/rotation/scale/euler
TRANSFORM_TYPEID = 4
ANIMATOR_TYPEID = 95
FLT_MAX = 3.4028234663852886e+38


def curve_index_map(bindings):
    """curve index -> (typeID, attribute, path hash, component).
    Prefix sums expand multi-component Transform bindings."""
    out, cur = {}, 0
    for b in bindings:
        n = ATTR_SIZE.get(b["attribute"], 1) if b["typeID"] == TRANSFORM_TYPEID else 1
        for k in range(n):
            out[cur + k] = (b["typeID"], b["attribute"], int(b["path"]), k)
        cur += n
    return out, cur


def _stream_frames(streamed):
    """StreamedClip: the uint32 array reinterpreted as a big-endian byte
    stream of frames ``(float time, int32 count, count × (int32 index,
    4 × float coeff))``."""
    payload = b"".join(int(v).to_bytes(4, "big") for v in streamed["data"])
    off, frames = 0, []
    while off + 8 <= len(payload):
        time, count = struct.unpack_from(">fi", payload, off)
        off += 8
        keys = []
        for _ in range(count):
            if off + 20 > len(payload):
                raise ValueError("truncated StreamedClip key")
            idx = struct.unpack_from(">i", payload, off)[0]
            coeff = struct.unpack_from(">4f", payload, off + 4)
            off += 20
            keys.append((idx, coeff))
        frames.append((time, keys))
    if off != len(payload):
        raise ValueError(f"trailing {len(payload) - off} bytes in StreamedClip")
    return frames


def decode(clip_tt):
    """Decode a clip typetree into ``{curveIndex: (kind, points)}`` where

    * kind ``"cubic"``:  points = [(time, (a, b, c, d))]
    * kind ``"linear"``: points = [(time, value)]
    * kind ``"const"``:  points = [(0.0, value)]
    """
    c = clip_tt["m_MuscleClip"]["m_Clip"]["data"]
    out = {}
    tmp = collections.defaultdict(list)
    for time, keys in _stream_frames(c["m_StreamedClip"]):
        if abs(time) > FLT_MAX / 2:          # leading -FLT_MAX sentinel frame
            continue
        for idx, coeff in keys:
            tmp[idx].append((time, tuple(coeff)))
    for idx, pts in tmp.items():
        out[idx] = ("cubic", sorted(pts))
    dense = c["m_DenseClip"]
    base = int(c["m_StreamedClip"]["curveCount"])
    n = int(dense["m_CurveCount"])
    for k in range(n):
        pts = []
        for f in range(int(dense["m_FrameCount"])):
            t = float(dense["m_BeginTime"]) + f / float(dense["m_SampleRate"])
            pts.append((t, dense["m_SampleArray"][f * n + k]))
        out[base + k] = ("linear", pts)
    cbase = base + n
    for k, v in enumerate(c["m_ConstantClip"]["data"]):
        out[cbase + k] = ("const", [(0.0, v)])
    return out


def evaluate(entry, t):
    """Evaluate one decoded curve at time ``t``."""
    kind, pts = entry
    if kind == "const":
        return pts[0][1]
    if not pts:
        return 0.0
    if t <= pts[0][0]:
        return pts[0][1][3] if kind == "cubic" else pts[0][1]
    if kind == "cubic":
        lo, hi = 0, len(pts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if pts[mid][0] <= t:
                lo = mid
            else:
                hi = mid - 1
        t0, (a, b, c, d) = pts[lo]
        dt = t - t0
        return ((a * dt + b) * dt + c) * dt + d
    if t >= pts[-1][0]:
        return pts[-1][1]
    for k in range(len(pts) - 1):
        t0, v0 = pts[k]
        t1, v1 = pts[k + 1]
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def sample_frames(clip_tt):
    """Decode a clip and evaluate it frame by frame at its own sample rate.

    Returns ``(rate, frames)`` where each frame is
    ``(t, muscles, body_q, body_p, transform_rotations, tdof)``:

    * muscles — {muscle index: value}
    * body_q — normalized body rotation (xyzw)
    * body_p — body position (humanoid-normalized space)
    * transform_rotations — {path hash: quaternion} for plain Transform
      rotation curves the clip also drives (e.g. auxiliary twist bones)
    * tdof — {TDoF slot: (x, y, z)} translation-DoF values in
      humanoid-normalized units (see :data:`.traits.TDOF_BONES`)
    """
    curves = decode(clip_tt)
    cmap, _ = curve_index_map(clip_tt["m_ClipBindingConstant"]["genericBindings"])
    anim, xform = {}, {}
    for ci, e in curves.items():
        info = cmap.get(ci)
        if not info:
            continue
        tid, attr, path, k = info
        if tid == ANIMATOR_TYPEID:
            anim[attr] = e
        elif tid == TRANSFORM_TYPEID and attr == 2:    # attribute 2 == rotation
            xform[(path, k)] = e
    rate = float(clip_tt["m_SampleRate"]) or 60.0
    stop = float(clip_tt["m_MuscleClip"]["m_StopTime"])
    n = max(2, int(round(stop * rate)) + 1)
    frames = []
    for f in range(n):
        t = f / rate
        mus = {m: evaluate(anim[MUSCLE_ATTR_BASE + m], t)
               for m in range(95) if MUSCLE_ATTR_BASE + m in anim}
        bq = [evaluate(anim[ROOT_Q_BASE + i], t) if ROOT_Q_BASE + i in anim
              else (1.0 if i == 3 else 0.0) for i in range(4)]
        nn = math.sqrt(sum(c * c for c in bq)) or 1.0
        bq = tuple(c / nn for c in bq)
        bp = tuple(evaluate(anim[ROOT_T_BASE + i], t) if ROOT_T_BASE + i in anim else 0.0
                   for i in range(3))
        tw = {}
        for (path, k), e in xform.items():
            tw.setdefault(path, [0.0] * 4)[k] = evaluate(e, t)
        td = {}
        for attr, e in anim.items():
            if attr >= TDOF_ATTR_BASE:
                slot, comp = divmod(attr - TDOF_ATTR_BASE, 3)
                td.setdefault(slot, [0.0, 0.0, 0.0])[comp] = evaluate(e, t)
        frames.append((t, mus, bq, bp, tw, {k: tuple(v) for k, v in td.items()}))
    return rate, frames


def clip_settings(clip_tt):
    """Return serialized Mecanim motion-X settings without discarding fields."""
    m = clip_tt.get("m_MuscleClip", {})
    names = (
        "m_StartX", "m_StopX", "m_StartTime", "m_StopTime",
        "m_OrientationOffsetY", "m_Level", "m_CycleOffset", "m_LoopTime",
        "m_LoopBlend", "m_LoopBlendOrientation", "m_LoopBlendPositionY",
        "m_LoopBlendPositionXZ", "m_StartAtOrigin", "m_KeepOriginalOrientation",
        "m_KeepOriginalPositionY", "m_KeepOriginalPositionXZ", "m_HeightFromFeet",
        "m_Mirror",
    )
    return {k: m[k] for k in names if k in m}
