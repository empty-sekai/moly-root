"""Humanoid rig contract, read straight from a Unity Avatar object.

An Avatar's ``m_Human`` block carries everything retargeting needs:

* ``m_Skeleton`` / ``m_SkeletonPose`` — the *human* skeleton (a subset of the
  full avatar skeleton) and its rest pose.  The body-orientation frame must be
  computed on **this** skeleton's global joint positions, not the full one
  (using the full skeleton introduces a ~2e-4 systematic bias).
* per-node axes — ``PreQ / PostQ / Sgn / Limit / Length`` for each bone that
  owns degrees of freedom.
* twist distribution parameters (``armTwist`` etc.) and the humanoid scale.

Cross-avatar caveats (measured across all shipped members):

* axes-array order follows each avatar's own node order — identify DoF hosts
  by bone *name* (or ``m_HumanBoneIndex``), never by axes index;
* some avatars insert an extra unnamed root and host the hips DoF on a node
  named ``Root`` instead of ``Hips``.
"""
from core.quat import qmul, qconj, qrotv
from .traits import TWIST_PARAM, GAME_TO_HUMAN


def _unwrap(x):
    return x["data"] if isinstance(x, dict) and set(x) == {"data"} else x


def _v3(v):
    return [v["x"], v["y"], v["z"]]


def _v4(v):
    return [v["x"], v["y"], v["z"], v["w"]]


def _xform(x):
    return {"t": _v3(x["t"]), "q": _v4(x["q"]), "s": _v3(x["s"])}


def _axes(a):
    a = _unwrap(a)
    return {"preQ": _v4(a["m_PreQ"]), "postQ": _v4(a["m_PostQ"]),
            "sgn": _v3(a["m_Sgn"]),
            "min": _v3(a["m_Limit"]["m_Min"]), "max": _v3(a["m_Limit"]["m_Max"]),
            "length": a["m_Length"], "type": a["m_Type"]}


def _skeleton(s, pose_x, tos):
    s = _unwrap(s)
    nodes = _unwrap(s["m_Node"])
    ids = _unwrap(s["m_ID"])
    ax = _unwrap(s["m_AxesArray"])
    out = []
    for i, n in enumerate(nodes):
        n = _unwrap(n)
        h = int(ids[i])
        path = tos.get(h, "")
        out.append({"i": i, "hash": h, "path": path,
                    "name": path.split("/")[-1] if path else "",
                    "parent": n["m_ParentId"], "axesId": n["m_AxesId"],
                    "pose": _xform(_unwrap(pose_x[i])) if i < len(pose_x) else None})
    return out, [_axes(a) for a in ax]


def rig_doc(avatar_tt):
    """Build the rig contract dict from an Avatar's typetree."""
    A = avatar_tt["m_Avatar"]
    H = _unwrap(A["m_Human"])
    tos = {int(e[0]): e[1] for e in avatar_tt["m_TOS"]
           if isinstance(e, (list, tuple)) and len(e) == 2}
    fn, fa = _skeleton(A["m_AvatarSkeleton"],
                       _unwrap(_unwrap(A["m_AvatarSkeletonPose"])["m_X"]), tos)
    hn, ha = _skeleton(H["m_Skeleton"],
                       _unwrap(_unwrap(H["m_SkeletonPose"])["m_X"]), tos)
    return {"full": {"nodes": fn, "axes": fa},
            "human": {"nodes": hn, "axes": ha,
                      "rootX": _xform(_unwrap(H["m_RootX"])),
                      "humanBoneIndex": [int(x) for x in _unwrap(H["m_HumanBoneIndex"])],
                      "humanBoneMass": [float(x) for x in _unwrap(H["m_HumanBoneMass"])],
                      "hasTDoF": bool(H["m_HasTDoF"]),
                      "scale": H["m_Scale"], "armTwist": H["m_ArmTwist"],
                      "foreArmTwist": H["m_ForeArmTwist"],
                      "upperLegTwist": H["m_UpperLegTwist"],
                      "legTwist": H["m_LegTwist"], "armStretch": H["m_ArmStretch"],
                      "legStretch": H["m_LegStretch"],
                      "feetSpacing": H["m_FeetSpacing"]}}


# Bones whose joints define the humanoid body orientation frame.
BODY_FRAME_BONES = ("LeftUpLeg", "RightUpLeg", "LeftArm", "RightArm")


class HumanSkeleton:
    """The human skeleton with forward kinematics, hips pinned at its
    zero-muscle rotation. Used to evaluate the body orientation frame."""

    def __init__(self, rig, nodes):
        self.nodes = nodes
        self.idx = {n["name"]: i for i, n in enumerate(nodes)}
        self.parent = [n["parent"] for n in nodes]
        self.t = [tuple(n["pose"]["t"]) for n in nodes]
        self.q = [tuple(n["pose"]["q"]) for n in nodes]
        self._rig = rig
        self._hips_index = (rig.human_bone_index[0]
                            if rig.human_bone_index else self.idx.get("Hips", -1))
        from .retarget import _frame_q  # cycle-free at call time
        self.frame_idx = tuple(self.idx[b] for b in BODY_FRAME_BONES)
        self.rest_frame = _frame_q(self.globals({}), self.frame_idx)

    def globals(self, locs, trans=None):
        """Global joint positions; ``locs`` maps bone name -> local rotation,
        ``trans`` (optional) bone name -> local translation override (TDoF).
        Hips stays at its zero-muscle rotation."""
        pos = [None] * len(self.nodes)
        rot = [None] * len(self.nodes)
        for i, n in enumerate(self.nodes):
            nm = n["name"]
            q = locs.get(nm) or (self._rig.q0("Hips") if i == self._hips_index else self.q[i])
            t = trans.get(nm, self.t[i]) if trans else self.t[i]
            p = self.parent[i]
            if p >= 0 and pos[p] is not None:
                v = qrotv(rot[p], t)
                pos[i] = tuple(pos[p][k] + v[k] for k in range(3))
                rot[i] = qmul(rot[p], q)
            else:
                pos[i] = t
                rot[i] = q
        return pos


# Unity HumanBoneIndex uses this fixed 25-slot order.
HUMAN_BONE_NAMES = ("Hips", "LeftUpperLeg", "RightUpperLeg",
                    "LeftLowerLeg", "RightLowerLeg", "LeftFoot", "RightFoot",
                    "Spine", "Chest", "UpperChest", "Neck", "Head",
                    "LeftShoulder", "RightShoulder", "LeftUpperArm",
                    "RightUpperArm", "LeftLowerArm", "RightLowerArm",
                    "LeftHand", "RightHand", "LeftToes", "RightToes",
                    "LeftEye", "RightEye", "Jaw")


class Rig:
    """One character's rig contract (a dict from :func:`rig_doc`, or a path
    to a JSON dump of the same shape)."""

    def __init__(self, rig_json):
        if isinstance(rig_json, dict):
            d = rig_json
        else:
            import json
            with open(rig_json, encoding="utf-8") as f:
                d = json.load(f)
        self.doc = d
        self.nodes = d["full"]["nodes"]
        self.name_of = {n["i"]: n["name"] for n in self.nodes}
        self.bind_q = {n["name"]: tuple(n["pose"]["q"]) for n in self.nodes if n["name"]}
        self.bind_t = {n["name"]: tuple(n["pose"]["t"]) for n in self.nodes if n["name"]}
        self.parent = {n["name"]: self.name_of.get(n["parent"], "")
                       for n in self.nodes if n["name"]}
        hax = d["human"]["axes"]
        hnodes = d["human"]["nodes"]
        self.axes = {n["name"]: (hax[n["axesId"]] if n["axesId"] >= 0 else None)
                     for n in hnodes}
        self.params = {k: d["human"][k] for k in
                       ("armTwist", "foreArmTwist", "upperLegTwist", "legTwist",
                        "armStretch", "legStretch", "feetSpacing", "scale")}
        # Body-transform contract (used by mecanim.bodyxform); absent in older
        # rig dumps, in which case hips translation cannot be reconstructed.
        h = d["human"]
        self.human_bone_index = ([int(x) for x in h["humanBoneIndex"]]
                                 if h.get("humanBoneIndex") else None)
        self.human_bone_mass = ([float(x) for x in h["humanBoneMass"]]
                                if h.get("humanBoneMass") else None)
        # Unity's HumanBoneIndex is the authority when an Avatar inserts an
        # unnamed human-skeleton root.  In sd_115/sd_130 the Hips slot points
        # at the node named Root, so resolving by the literal name Hips would
        # silently lose its axes and use the wrong zero pose.
        self.human_node_name = {}
        if self.human_bone_index:
            for slot, ni in enumerate(self.human_bone_index):
                if 0 <= ni < len(hnodes):
                    self.human_node_name[slot] = hnodes[ni]["name"]
        self.semantic_name = {}
        for game_name, human_name in GAME_TO_HUMAN.items():
            try:
                slot = next(i for i, name in enumerate(HUMAN_BONE_NAMES)
                            if name == human_name)
            except StopIteration:
                continue
            actual = self.human_node_name.get(slot)
            if actual:
                self.semantic_name[game_name] = actual
                if self.axes.get(actual) is not None:
                    self.axes[game_name] = self.axes[actual]
        rx = h.get("rootX")
        self.rootx_q = tuple(rx["q"]) if rx else None
        self.rootx_t = tuple(rx["t"]) if rx else None
        self.has_tdof = bool(h.get("hasTDoF", False))
        self.path_of = {}
        for n in self.nodes:
            if n["path"]:
                self.path_of[n["path"]] = n["name"]
        self.human = HumanSkeleton(self, d["human"]["nodes"])

    def _actual_bone(self, bone):
        return self.semantic_name.get(bone, bone)

    def tdof_translations(self, tdof):
        """Local translations displaced by clip translation-DoF values.

        ``tdof``: {TDoF slot: (x, y, z)} straight from the sampler.  Returns
        {bone name: local translation} for the hosts this rig has.  Law
        (measured against Animator playback, every TDoF slot the shipped
        library uses, two avatars, worst error 3.4e-5):

            local_t = rest_t - humanScale * (v.x, v.z, v.y)
        """
        from .traits import TDOF_BONES, HUMAN_TO_GAME
        h = self.params["scale"]
        out = {}
        for slot, v in tdof.items():
            if not (0 <= slot < len(TDOF_BONES)):
                continue
            game = HUMAN_TO_GAME.get(TDOF_BONES[slot])
            if game is None or game not in self.bind_t:
                continue
            r = self.bind_t[game]
            out[game] = (r[0] - h * v[0], r[1] - h * v[2], r[2] - h * v[1])
        return out

    def host_node(self, bone):
        """Node that actually hosts this humanoid slot.

        Animation must target this node: avatars that hang the hips DoF on
        ``Root`` keep the ``Hips`` node's authored rest rotation (a ~90-degree
        offset) and the engine writes the animated pose to ``Root``.  Baking
        onto the literal bone name would overwrite that rest rotation and lay
        the character on its side.
        """
        return self._actual_bone(bone)

    def q0(self, bone):
        """Zero-muscle local rotation: ``PreQ · conj(PostQ)``.

        Holds for every DoF-bearing humanoid bone (verified against the
        engine's own zero pose to 2.9e-07); bones without axes fall back to
        their bind rotation.
        """
        actual = self._actual_bone(bone)
        A = self.axes.get(bone) or self.axes.get(actual)
        if A:
            return qmul(tuple(A["preQ"]), qconj(tuple(A["postQ"])))
        return self.bind_q.get(actual, self.bind_q.get(bone, (0.0, 0.0, 0.0, 1.0)))

    def twist_factor(self, bone):
        p = TWIST_PARAM.get(bone)
        return 1.0 if p is None else float(self.params[p])
