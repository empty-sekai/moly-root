"""Humanoid body transform: bodyPosition / bodyRotation -> hips local TRS.

A humanoid clip does not store the hips transform.  It stores the *body*
transform — the pose's mass center and body orientation frame in normalized
humanoid space — and the engine reconstructs the hips local TRS from it plus
the muscle-driven pose:

    pose    = human-skeleton FK with muscle rotations (hips pinned at rest)
    COM     = mass-weighted average of the per-bone mass centers (25 bones)
    O.q     = normalize(normalize(F_geom(pose)) * conj(rootX.q))
    hips.t  = scale * bodyPosition + R(bodyRotation) R(conj(O.q)) (pos[hips] - COM)
    hips.q  = bodyRotation * conj(O.q) * rest_q(hips)

``F_geom`` is the body orientation frame (:func:`.retarget._frame_q`)
evaluated on the posed human-skeleton joint positions.  ``rootX`` is the
avatar's stored rest body transform: its rotation equals ``F_geom(rest)`` and
its translation equals ``COM(rest)`` to float precision — rootX *is* the
rest-pose body frame, so ``O`` measures the pose's body frame relative to it.

The mass-center table mirrors the engine's own computation, including its
left-lower-arm asymmetry: the left lower arm's center is the midpoint of the
left *upper arm* and left hand joints, while the right side uses the right
lower arm joint as expected.  The asymmetry is baked into shipped avatars
(their stored ``rootX.t`` matches the asymmetric form to ~8e-8 m but the
symmetric form only to ~8e-4 m), so it must be reproduced; the
``symmetric_lower_arm`` flag exists only to prove the criterion can fail.

Validated frame by frame against the engine's own humanoid pose application
(4 official clips x 269 frames): worst hips translation error 1.93e-7 m and
worst rotation component error 4.7e-7 — the float32 noise floor of the
reference itself.  The symmetric variant degrades translation to a constant
~8.2e-4 m.

Caveat: avatars with ``hasTDoF`` carry translation-DoF channels that would
shift the FK joint positions before COM; on official content those channels
are absent from clips (the pose input has no TDoF values), which this module
assumes.
"""
from core.quat import qmul, qconj, qrotv
from .retarget import pose_bone, _frame_q, _norm4


def _mid(a, b):
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5)


def _avg(*pts):
    n = float(len(pts))
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n,
            sum(p[2] for p in pts) / n)


def bone_mass_center(idx, pos, b, symmetric_lower_arm=False):
    """Mass center of humanoid bone ``b`` (0..24) from posed joint positions.

    ``idx`` maps humanoid bone slots to human-skeleton node indices (-1 =
    missing bone); ``pos`` is the FK joint position list.  Transcribed from
    the engine's per-bone jump table; entries not listed use the bone's own
    joint.
    """
    P = lambda k: pos[idx[k]]
    if b == 0:                      # Hips: mean of both upper legs + spine
        return _avg(P(1), P(2), P(7))
    if b == 1:
        return _mid(P(1), P(3))
    if b == 2:
        return _mid(P(2), P(4))
    if b == 3:
        return _mid(P(3), P(5))
    if b == 4:
        return _mid(P(4), P(6))
    if b == 7:                      # Spine
        if idx[8] >= 0:
            return _mid(P(7), P(8))
        a, c, d = P(7), P(14), P(15)
        return tuple(0.1 * a[k] + 0.45 * (c[k] + d[k]) for k in range(3))
    if b == 8:                      # Chest
        if idx[9] >= 0:
            return _mid(P(8), P(9))
        if idx[10] >= 0 and idx[12] >= 0 and idx[13] >= 0:
            return _avg(P(8), P(10), P(12), P(13))
        return _avg(P(8), P(14), P(15))
    if b == 9:                      # UpperChest
        if idx[10] >= 0 and idx[12] >= 0 and idx[13] >= 0:
            return _avg(P(9), P(10), P(12), P(13))
        return _avg(P(9), P(14), P(15))
    if b == 10:                     # Neck
        return _mid(P(10), P(11))
    if b == 12:
        return _mid(P(12), P(14))
    if b == 13:
        return _mid(P(13), P(15))
    if b == 14:
        return _mid(P(14), P(16))
    if b == 15:
        return _mid(P(15), P(17))
    if b == 16:                     # LeftLowerArm: engine reads the UPPER arm joint
        return _mid(P(16) if symmetric_lower_arm else P(14), P(18))
    if b == 17:                     # RightLowerArm: regular
        return _mid(P(17), P(19))
    return P(b)                     # feet, head, hands, toes, eyes, jaw


def com(rig, pos, symmetric_lower_arm=False):
    """Mass-weighted center of mass over the 25 humanoid bones (missing
    bones are skipped and the weight sum renormalized, as the engine does)."""
    idx, mass = rig.human_bone_index, rig.human_bone_mass
    acc = [0.0, 0.0, 0.0]
    msum = 0.0
    for b in range(25):
        if idx[b] < 0:
            continue
        c = bone_mass_center(idx, pos, b, symmetric_lower_arm)
        m = mass[b]
        msum += m
        for k in range(3):
            acc[k] += m * c[k]
    return (acc[0] / msum, acc[1] / msum, acc[2] / msum)


def pose_root(rig, muscles, body_q, body_p, locs=None, symmetric_lower_arm=False,
              trans=None):
    """Hips local (translation, rotation) in scene space, with the skeleton
    root at identity.

    ``muscles``: {muscle index: value}; ``body_q``/``body_p``: the clip's
    body rotation (normalized xyzw) and body position.  ``locs`` may carry
    precomputed local rotations ({bone name: xyzw}) to share the per-frame
    :func:`pose_bone` work with the caller; missing bones are filled in.
    ``trans`` maps bone name -> local translation (translation DoF); it moves
    the frame joints and the mass center, so both the reconstructed hips
    rotation and translation depend on it.
    """
    if rig.human_bone_index is None or rig.rootx_q is None:
        raise ValueError("rig contract lacks humanBoneIndex/humanBoneMass/rootX "
                         "(re-dump it from the Avatar with this version)")
    hs = rig.human
    idx = rig.human_bone_index
    hips = idx[0]
    hips_name = hs.nodes[hips]["name"]
    rest_q = hs.q[hips]
    full = dict(locs) if locs else {}
    for b in range(1, 25):
        n = idx[b]
        if n < 0:
            continue
        nm = hs.nodes[n]["name"]
        if nm not in full:
            full[nm] = pose_bone(rig, nm, muscles)
    full[hips_name] = rest_q
    pos = hs.globals(full, trans)
    c = com(rig, pos, symmetric_lower_arm)
    oq = _norm4(qmul(_frame_q(pos, hs.frame_idx), qconj(rig.rootx_q)))
    rel = qrotv(qconj(oq), tuple(pos[hips][k] - c[k] for k in range(3)))
    rot = qrotv(tuple(body_q), rel)
    s = float(rig.params["scale"])
    t = tuple(s * body_p[k] + rot[k] for k in range(3))
    q = qmul(qmul(tuple(body_q), qconj(oq)), tuple(rest_q))
    return t, q
