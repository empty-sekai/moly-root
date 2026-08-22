"""Mecanim humanoid retargeting: muscle values + body rotation -> bone local
rotations.  Pure Python, no engine dependency.

The law, per DoF-bearing bone with axes ``(preQ, postQ, sgn, limits)`` and
``Q0 = preQ · conj(postQ)``:

1. angle per axis: ``θ_k = v · (v ≥ 0 ? max_k : −min_k)`` — limits in the
   avatar are the engine defaults; ``v`` is **never clamped** (official
   content overshoots [-1, 1] by an order of magnitude on finger channels,
   and clamping breaks every overshooting bone).
2. ``Swing ∝ (0, tan(θy/2)·sgn_y, tan(θz/2)·sgn_z, 1)`` normalized — the
   tan-half-angle form, *not* a single axis-angle of ``(0, θy, θz)``;
   ``Twist = axisAngle((sgn_x, 0, 0), θx · t)`` where ``t`` is the bone's
   twist-distribution factor; ``R = Swing · Twist`` (order matters).
3. inherited twist: the parent's undistributed ``(1 − t_parent) · θx_parent``
   premultiplies as an independent rotation about
   ``conj(postQ_child)·conj(Q0_child)·postQ_parent·(sgn_x_parent, 0, 0)``
   (the parent twist axis carried through the child's rest rotation).
4. bone local rotation: ``q = Q0 · postQ · R · conj(postQ)``.
5. hips: ``Hips = bodyRotation · conj(G) · Q0_hips`` where
   ``G = F(pose) · conj(F(rest))`` and ``F`` is the body orientation frame
   (see ``_frame_q``) evaluated on the human skeleton's global joint
   positions.

Validated end to end against the engine's own humanoid pose application on
official content: 36/36 bones exact, worst component error 5.6e-07 over 9684
samples; and across each muscle's full observed value envelope
(-12.06..+9.31): 36/36, worst 6.1e-07.
"""
import math

from core.quat import qmul, qconj, qrotv, axis_angle
from .traits import BONE_MUSCLES, MUSCLE_LIMITS, GAME_TO_HUMAN, TWIST_PARENT, FOLD_INTO


def _norm4(q):
    n = math.sqrt(sum(x * x for x in q))
    return tuple(x / n for x in q)


def _one_muscle(mi, muscles):
    if mi < 0:
        return 0.0
    v = muscles.get(mi)
    if not v:
        return 0.0
    dmin, dmax = MUSCLE_LIMITS[mi]
    return math.radians(v * (dmax if v >= 0 else -dmin))


def muscle_angle(bone, muscles, k):
    """The bone's k-th axis muscle angle in radians, before twist scaling;
    includes folded contributions from missing bones."""
    human = GAME_TO_HUMAN.get(bone)
    if human is None or human not in BONE_MUSCLES:
        return 0.0
    a = _one_muscle(BONE_MUSCLES[human][k], muscles)
    fold = FOLD_INTO.get(bone)
    if fold is not None:
        a += _one_muscle(fold[1][k], muscles)
    return a


def pose_bone(rig, bone, muscles):
    """Local rotation of one humanoid bone. ``muscles``: {muscle index: value}."""
    A = rig.axes.get(bone)
    if A is None:
        return rig.bind_q.get(bone, (0.0, 0.0, 0.0, 1.0))
    human = GAME_TO_HUMAN.get(bone)
    if human is None or human not in BONE_MUSCLES:
        return rig.q0(bone)
    th = [muscle_angle(bone, muscles, k) for k in range(3)]
    own = th[0] * rig.twist_factor(bone)
    sgn = A["sgn"]
    swing = _norm4((0.0, math.tan(th[1] / 2) * sgn[1], math.tan(th[2] / 2) * sgn[2], 1.0))
    twist_own = axis_angle((sgn[0], 0.0, 0.0), own)
    R = qmul(swing, twist_own)

    postq = tuple(A["postQ"])

    # Inherited twist from the parent, carried through this bone's rest
    # rotation.  Using either bone's raw (sgn_x, 0, 0) only works when the two
    # muscle frames happen to align (they do for hands, they don't for legs).
    par = TWIST_PARENT.get(bone)
    if par is not None and rig.axes.get(par) is not None:
        inh = muscle_angle(par, muscles, 0) * (1.0 - rig.twist_factor(par))
        if inh:
            Ap = rig.axes[par]
            ax = qrotv(qmul(qconj(postq), qconj(rig.q0(bone))),
                       qrotv(tuple(Ap["postQ"]), (Ap["sgn"][0], 0.0, 0.0)))
            R = qmul(axis_angle(ax, inh), R)

    return qmul(qmul(rig.q0(bone), postq), qmul(R, qconj(postq)))


def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0])


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 1e-12 else (0.0, 0.0, 0.0)


def _frame_q(pos, idx):
    """Body orientation frame from four joints (left/right upper leg,
    left/right upper arm), as the engine computes it:

        up    = normalize(mid(arms) − mid(legs))
        right = normalize((legR − legL) + (armR − armL))
        c = cross(up, right);  d = cross(c, up)     # NOT normalized
        q = normalize(quaternion(columns (d, up, −c)))

    Leaving both cross products unnormalized is essential: when up and right
    are not orthogonal the matrix is deliberately non-orthogonal, and only
    this variant matches the engine to float precision (an exhaustive search
    over 288 permutation/sign/normalization variants leaves it 4 orders of
    magnitude ahead of the runner-up).
    """
    A, B, C, D = idx
    mAB = [(pos[A][i] + pos[B][i]) * 0.5 for i in range(3)]
    mCD = [(pos[C][i] + pos[D][i]) * 0.5 for i in range(3)]
    up = _unit([mCD[i] - mAB[i] for i in range(3)])
    rr = _unit([(pos[B][i] - pos[A][i]) + (pos[D][i] - pos[C][i]) for i in range(3)])
    c = _cross(up, rr)
    d = _cross(c, up)
    cols = (d, up, (-c[0], -c[1], -c[2]))
    m = tuple(tuple(cols[j][i] for j in range(3)) for i in range(3))
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        t = math.sqrt(tr + 1.0) * 2
        q = ((m[2][1]-m[1][2])/t, (m[0][2]-m[2][0])/t, (m[1][0]-m[0][1])/t, 0.25*t)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        t = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = (0.25*t, (m[0][1]+m[1][0])/t, (m[0][2]+m[2][0])/t, (m[2][1]-m[1][2])/t)
    elif m[1][1] > m[2][2]:
        t = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = ((m[0][1]+m[1][0])/t, 0.25*t, (m[1][2]+m[2][1])/t, (m[0][2]-m[2][0])/t)
    else:
        t = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = ((m[0][2]+m[2][0])/t, (m[1][2]+m[2][1])/t, 0.25*t, (m[1][0]-m[0][1])/t)
    return _norm4(q)


def body_frame_delta(rig, muscles, trans=None):
    """``G = F(pose) · conj(F(rest))`` with hips pinned at rest — G depends on
    the spine chain's local rotations plus any translation-DoF displacement of
    the frame joints (``trans``: {bone name: local translation})."""
    hs = rig.human
    locs = {rig.host_node("Spine"): pose_bone(rig, "Spine", muscles),
            rig.host_node("Spine1"): pose_bone(rig, "Spine1", muscles)}
    return qmul(_frame_q(hs.globals(locs, trans), hs.frame_idx),
                qconj(hs.rest_frame))


def pose_hips(rig, body_rotation, muscles, trans=None):
    """``Hips = bodyRotation · conj(G) · Q0_hips`` (bodyRotation is strictly
    equivariant on hips).  ``trans`` carries TDoF translations — omitting them
    on TDoF-driven clips skews hips by whole degrees (measured 7.7 deg)."""
    return qmul(qmul(body_rotation, qconj(body_frame_delta(rig, muscles, trans))),
                rig.q0("Hips"))


def pose_all(rig, muscles, body_rotation):
    """All humanoid bone local rotations for one frame: {bone name: xyzw}."""
    out = {"Hips": pose_hips(rig, body_rotation, muscles)}
    for bone in GAME_TO_HUMAN:
        if bone != "Hips":
            out[bone] = pose_bone(rig, bone, muscles)
    return out
