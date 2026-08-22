"""MagicaCloth BoneCloth rig extraction (data plane only, no simulation).

Reads every MagicaBoneCloth component of a character bundle into a plain
dict: chain bone names, parent/root/selection/depth tables, raw BezierParam
cloth parameters, colliders (sphere/capsule), team/target data and bind-pose
transforms — everything a clean-room bone-cloth solver needs.

Field semantics (verified against the runtime's own data classes):

* selection: 0 invalid / 1 move / 2 fixed / 3 extend; fixed set == chain tops.
* data version contract: MagicaBoneCloth=7, ClothData=5, MeshData=2,
  SelectionData=2.
* BezierParam: ``startValue`` when no end value; else linear or quadratic
  bezier over clamped [0,1] with control = end + (start-end)(curve*0.5+0.5).
* capsule ``length`` is the runtime segment HALF-length (endpoints =
  bone-world of ``center +- axis*length``); ``startRadius`` applies at the
  -axis end (t=0), ``endRadius`` at +axis (t=1), surface radius lerps.
* the serialized per-vertex TRS lists are the bake-time pose snapshot;
  struct distance constraint lengths equal world distances in that pose
  (live prefab poses may drift after baking — shipped as ``bakeDrift``).

Structural verifications from the reference extraction are kept and returned
alongside the rig; every entry carries ``ok`` plus enough detail to act on.

Output coordinates follow the package's glTF convention when ``to_gltf`` is
set (see :mod:`.gltf`): positions (-x, y, z), quaternions (x, -y, -z, w),
and each capsule gains an ``axisDirection`` local vector (the reflected unit
axis) so downstream code never re-derives handedness.  Scalar data (lengths,
depths, radii, BezierParam) is reflection-invariant and kept verbatim.
Rotation-space constraint tables (composite/line/clamp rotation) hold
Unity-local axes and are *omitted* rather than shipped in a mixed
convention; their entry counts are kept in ``constraintCounts``.
"""
import math
from collections import Counter

from core.gltf import unity_to_gltf_pos, unity_to_gltf_quat

SELECTION_NAMES = {0: "invalid", 1: "move", 2: "fixed", 3: "extend"}
EXPECTED_VERSIONS = {"MagicaBoneCloth": 7, "ClothData": 5, "MeshData": 2, "SelectionData": 2}
AXIS_NAMES = {0: "x", 1: "y", 2: "z"}
_AXIS_VEC = {0: (1.0, 0.0, 0.0), 1: (0.0, 1.0, 0.0), 2: (0.0, 0.0, 1.0)}

# Scalar (coordinate-free) constraint tables shipped verbatim.
_SCALAR_CONSTRAINTS = ("structDistanceDataList", "bendDistanceDataList",
                      "rootDistanceDataList")
# Present in the data but holding Unity-local axes; counted, not shipped.
_OMITTED_CONSTRAINTS = ("clampDistance2DataList", "clampDistance2RootInfoList",
                        "clampRotationDataList", "clampRotationRootInfoList",
                        "restoreRotationDataList",
                        "compositeRotationDataList", "compositeRotationRootInfoList",
                        "lineRotationDataList", "lineRotationRootInfoList",
                        "nearDistanceDataList", "penetrationDataList",
                        "adjustRotationDataList", "twistDataList",
                        "volumeDataList", "triangleBendDataList")


def _v3(d):
    return [float(d["x"]), float(d["y"]), float(d["z"])]


def _q4(d):
    return [float(d["x"]), float(d["y"]), float(d["z"]), float(d["w"])]


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw*bx + ax*bw + ay*bz - az*by,
            aw*by - ax*bz + ay*bw + az*bx,
            aw*bz + ax*by - ay*bx + az*bw,
            aw*bw - ax*bx - ay*by - az*bz]


def _qrot(q, v):
    x, y, z, w = q
    cx = y*v[2] - z*v[1] + w*v[0]
    cy = z*v[0] - x*v[2] + w*v[1]
    cz = x*v[1] - y*v[0] + w*v[2]
    return [v[0] + 2.0*(y*cz - z*cy), v[1] + 2.0*(z*cx - x*cz), v[2] + 2.0*(x*cy - y*cx)]


class _Scene:
    """Typetree maps over one character's own objects."""

    def __init__(self, objects):
        self.scripts, self.go, self.tr, self.mb = {}, {}, {}, {}
        for o in objects:
            tn = o.type.name
            if tn == "MonoScript":
                self.scripts[o.path_id] = o.read_typetree()
            elif tn == "GameObject":
                self.go[o.path_id] = o.read_typetree()
            elif tn == "Transform":
                self.tr[o.path_id] = o.read_typetree()
            elif tn == "MonoBehaviour":
                self.mb[o.path_id] = o.read_typetree()
        self._world = {}
        self.external_refs = []

    def ptr_pid(self, p, context=""):
        if not p or p.get("m_PathID", 0) == 0:
            return None
        if p.get("m_FileID", 0) != 0:
            self.external_refs.append((context, int(p["m_FileID"]), int(p["m_PathID"])))
            return None
        return p["m_PathID"]

    def cls_of(self, mb_tree):
        pid = (mb_tree.get("m_Script") or {}).get("m_PathID")
        return self.scripts.get(pid, {}).get("m_ClassName", "?")

    def bone_name(self, tr_pid):
        t = self.tr.get(tr_pid)
        if t is None:
            return None
        g = self.go.get((t.get("m_GameObject") or {}).get("m_PathID"))
        return None if g is None else str(g["m_Name"])

    def tr_father(self, tr_pid):
        t = self.tr.get(tr_pid)
        if t is None:
            return None
        f = (t.get("m_Father") or {}).get("m_PathID", 0)
        return f if f else None

    def world_trs(self, tr_pid):
        """Bind-pose world (pos, quat, lossy scale).  No shear (scales ~1)."""
        if tr_pid in self._world:
            return self._world[tr_pid]
        t = self.tr[tr_pid]
        lp, lr, ls = _v3(t["m_LocalPosition"]), _q4(t["m_LocalRotation"]), _v3(t["m_LocalScale"])
        f = self.tr_father(tr_pid)
        if f is None:
            out = (lp, lr, ls)
        else:
            pp, pr, ps = self.world_trs(f)
            sp = [lp[i] * ps[i] for i in range(3)]
            wp = [pp[i] + x for i, x in enumerate(_qrot(pr, sp))]
            out = (wp, _qmul(pr, lr), [ps[i] * ls[i] for i in range(3)])
        self._world[tr_pid] = out
        return out


def _classify(name):
    n = name.lower()
    if "hair" in n:
        return "hair"
    if "skirt" in n:
        return "skirt"
    return "other"


def extract_bone_cloth(objects, to_gltf=True, mutate=None):
    """Extract every MagicaBoneCloth of ``objects`` (one character's own
    UnityPy objects).  Returns ``{rig..., "checks": [...]}``.

    ``mutate(scene)`` is a test hook: it may corrupt the in-memory typetrees
    to prove the structural checks can fail.
    """
    S = _Scene(objects)
    if mutate is not None:
        mutate(S)
    checks = []

    def check(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    mb_classes = Counter(S.cls_of(d) for d in S.mb.values())

    colliders = {}
    for pid, d in sorted(S.mb.items()):
        c = S.cls_of(d)
        if c not in ("MagicaSphereCollider", "MagicaCapsuleCollider",
                     "MagicaPlaneCollider"):
            continue
        gtree = S.go.get((d.get("m_GameObject") or {}).get("m_PathID"), {})
        tr_pid = None
        for comp in gtree.get("m_Component", []):
            cp = comp.get("component", {})
            if cp.get("m_PathID") in S.tr:
                tr_pid = cp["m_PathID"]
                break
        wp, wr, ws = S.world_trs(tr_pid)
        item = {
            "pathId": pid,
            "kind": {"MagicaSphereCollider": "sphere",
                     "MagicaCapsuleCollider": "capsule",
                     "MagicaPlaneCollider": "plane"}[c],
            "bone": S.bone_name(tr_pid),
            "transformPathId": tr_pid,
            "enabled": bool(d.get("m_Enabled", 1)),
            "isGlobal": bool(d.get("isGlobal", 0)),
            "center": _v3(d["center"]),
            "boneWorld": {"position": wp, "rotation": wr, "scale": ws},
        }
        if item["kind"] == "sphere":
            item["radius"] = float(d["radius"])
        elif item["kind"] == "capsule":
            item.update(axis=int(d["axis"]), axisName=AXIS_NAMES.get(int(d["axis"])),
                        length=float(d["length"]), startRadius=float(d["startRadius"]),
                        endRadius=float(d["endRadius"]))
        # plane: infinite plane through boneWorld * center, normal = the
        # transform's up axis (local +Y); no further fields are serialized.
        colliders[pid] = item

    components = []
    inactive = []
    referenced = set()
    for pid, d in sorted(S.mb.items()):
        if S.cls_of(d) != "MagicaBoneCloth":
            continue
        comp_go = S.go.get((d.get("m_GameObject") or {}).get("m_PathID"), {})
        cn = str(comp_go.get("m_Name", ""))

        cloth_pid = S.ptr_pid(d.get("clothData"), f"{cn}.clothData")
        mesh_pid = S.ptr_pid(d.get("meshData"), f"{cn}.meshData")
        if cloth_pid is None or mesh_pid is None:
            # shipped prefabs carry leftover components with null data
            # (superseded by a combined mesh component); the runtime cannot
            # initialize them either, so they are skipped and recorded
            inactive.append(cn)
            continue
        cloth = S.mb[cloth_pid]
        mesh = S.mb[mesh_pid]
        sel_pid = S.ptr_pid(d.get("clothSelection"), f"{cn}.clothSelection")
        sel = S.mb.get(sel_pid)          # may be external (editor-only data)

        use_tr = [S.ptr_pid(p) for p in d.get("useTransformList") or []]
        bones = [S.bone_name(t) for t in use_tr]
        use_vertex = [int(x) for x in cloth.get("useVertexList") or []]
        n = len(use_vertex)
        depth = [float(x) for x in cloth.get("vertexDepthList") or []]
        parent = [int(x) for x in cloth.get("parentList") or []]
        root = [int(x) for x in cloth.get("rootList") or []]
        selection = [int(x) for x in cloth.get("selectionData") or []]
        flag_level = [int(x) for x in cloth.get("vertexFlagLevelList") or []]
        sel_list = (sel.get("selectionList") or []) if sel else []
        authored = ([int(x) for x in (sel_list[0].get("selectData") or [])]
                    if sel_list else None)

        pos_l = [_v3(x) for x in d.get("useTransformPositionList") or []]
        rot_l = [_q4(x) for x in d.get("useTransformRotationList") or []]
        scl_l = [_v3(x) for x in d.get("useTransformScaleList") or []]
        world = [S.world_trs(t) for t in use_tr]

        team = d.get("teamData") or {}
        col_pids = [p for p in (S.ptr_pid(x) for x in team.get("colliderList") or []) if p]
        referenced.update(col_pids)
        target = d.get("clothTarget") or {}
        target_roots = [S.ptr_pid(p) for p in target.get("rootList") or []]

        counts = {}
        scalars = {}
        for k in _SCALAR_CONSTRAINTS + _OMITTED_CONSTRAINTS:
            val = cloth.get(k) or []
            counts[k] = len(val)
            if k in _SCALAR_CONSTRAINTS and val:
                scalars[k] = val

        comp = {
            "pathId": pid,
            "component": cn,
            "class": _classify(cn),
            "enabled": bool(d.get("m_Enabled", 1)),
            "versions": {
                "component": int(d.get("dataVersion", -1)),
                "clothData": int(cloth.get("dataVersion", -1)),
                "meshData": int(mesh.get("dataVersion", -1)),
                "selectionData": int(sel.get("dataVersion", -1)) if sel else None,
                "componentRecorded": {
                    "clothDataVersion": int(d.get("clothDataVersion", -1)),
                    "meshDataVersion": int(d.get("meshDataVersion", -1)),
                    "clothSelectionVersion": int(d.get("clothSelectionVersion", -1)),
                },
            },
            "team": {
                "updateMode": int(d.get("updateMode", 0)),
                "cullingMode": int(d.get("cullingMode", 0)),
                "skinningMode": int(d.get("skinningMode", 0)),
                "userBlendWeight": float(d.get("userBlendWeight", 1)),
                "mergeAvatarCollider": bool(team.get("mergeAvatarCollider", 0)),
                "colliderPathIds": col_pids,
                "colliderBones": [colliders[p]["bone"] for p in col_pids],
            },
            "target": {
                "connection": int(target.get("connection", -1)),
                "sameSurfaceAngle": float(target.get("sameSurfaceAngle", 0)),
                "rootBones": [S.bone_name(t) for t in target_roots],
                "rootTransformPathIds": target_roots,
            },
            "mesh": {
                "vertexCount": int(mesh.get("vertexCount", 0)),
                "boneCount": int(mesh.get("boneCount", 0)),
                "lineCount": int(mesh.get("lineCount", 0)),
                "lineList": [int(x) for x in mesh.get("lineList") or []],
            },
            "maxLevel": int(cloth.get("maxLevel", 0)),
            "algorithms": {
                "clampRotationAlgorithm": int(cloth.get("clampRotationAlgorithm", -1)),
                "restoreRotationAlgorithm": int(cloth.get("restoreRotationAlgorithm", -1)),
            },
            "vertices": {
                "bones": bones[:n],
                "terminalBone": bones[n] if len(bones) > n else None,
                "transformPathIds": use_tr,
                "useVertex": use_vertex,
                "depth": depth,
                "parent": parent,
                "root": root,
                "selection": selection,
                "selectionNames": [SELECTION_NAMES.get(s, str(s)) for s in selection],
                "flagLevel": flag_level,
                "localPosition": [list(p) for p in pos_l],
                "localRotation": [list(q) for q in rot_l],
                "localScale": [list(s) for s in scl_l],
                "worldPosition": [list(w[0]) for w in world[:n]],
                "worldRotation": [list(w[1]) for w in world[:n]],
            },
            "clothParams": d.get("clothParams") or {},
            "constraintCounts": counts,
            "constraints": scalars,
        }
        components.append(comp)

        # ---- structural verifications (all in Unity space) ----
        v = comp["versions"]
        ok = (v["component"] == EXPECTED_VERSIONS["MagicaBoneCloth"]
              and v["clothData"] == EXPECTED_VERSIONS["ClothData"]
              and v["meshData"] == EXPECTED_VERSIONS["MeshData"]
              and v["selectionData"] in (None, EXPECTED_VERSIONS["SelectionData"])
              and v["componentRecorded"]["clothDataVersion"] == EXPECTED_VERSIONS["ClothData"]
              and v["componentRecorded"]["meshDataVersion"] == EXPECTED_VERSIONS["MeshData"]
              and v["componentRecorded"]["clothSelectionVersion"] == EXPECTED_VERSIONS["SelectionData"])
        check(f"{cn}: version contract 7/5/2/2", ok, v)

        ok = (len(depth) == n and len(parent) == n and len(root) == n
              and len(selection) == n and len(flag_level) == n
              and len(use_tr) in (n, n + 1)
              and len(pos_l) == len(rot_l) == len(scl_l) == len(use_tr)
              and all(b for b in bones))
        check(f"{cn}: array lengths coherent (N={n})", ok,
              {"N": n, "transforms": len(use_tr)})

        if authored is not None:
            used_expect = [i for i, s0 in enumerate(authored) if s0 != 0]
            check(f"{cn}: useVertexList == non-invalid authored vertices",
                  used_expect == use_vertex, {"useVertexList": use_vertex})
            check(f"{cn}: selectionData == authored[useVertexList]",
                  [authored[i] for i in use_vertex] == selection,
                  {"selection": selection})
        else:
            check(f"{cn}: SKIPPED(authored selection external)", True, None)

        bad = []
        for vtx in range(n):
            p = parent[vtx]
            if p >= 0 and S.tr_father(use_tr[vtx]) != use_tr[p]:
                bad.append((vtx, p))
        check(f"{cn}: parentList edges are Transform-tree parent edges", not bad, bad[:8])

        bad = []
        for vtx in range(n):
            if parent[vtx] < 0:
                if root[vtx] != -1:
                    bad.append((vtx, "top but root!=-1"))
            else:
                cur, hops = vtx, 0
                while parent[cur] >= 0 and hops <= n:
                    cur = parent[cur]
                    hops += 1
                if root[vtx] != cur:
                    bad.append((vtx, root[vtx], cur))
        check(f"{cn}: rootList = chain top (-1 at the top)", not bad, bad[:8])

        fixed_set = {v2 for v2 in range(n) if selection[v2] == 2}
        top_set = {v2 for v2 in range(n) if parent[v2] < 0}
        check(f"{cn}: fixed set == chain-top set", fixed_set == top_set,
              {"fixed": sorted(fixed_set), "tops": sorted(top_set)})

        bad = []
        for vtx in range(n):
            if parent[vtx] < 0:
                if depth[vtx] != 0.0:
                    bad.append((vtx, "root depth != 0"))
            elif not depth[vtx] > depth[parent[vtx]]:
                bad.append((vtx, "not increasing"))
        if depth and max(depth) != 1.0:
            bad.append(("max", max(depth)))
        check(f"{cn}: depth 0 at root, increasing, max==1", not bad, bad[:8])

        lv = [f & 0xFFFF for f in flag_level]
        ml = comp["maxLevel"]
        ok = all(abs(depth[v2] - ((lv[v2] - 1) / (ml - 1) if ml > 1 else 0.0)) < 1e-6
                 for v2 in range(n))
        check(f"{cn}: depth == (level-1)/(maxLevel-1)", ok, {"maxLevel": ml})

        # The serialized per-vertex TRS lists are the *bake-time* pose of the
        # cloth data.  On most components they equal the live prefab pose,
        # but shipped data contains post-bake edits (chains moved by up to
        # ~7 cm, leaf rotations changed) — so the live-vs-snapshot delta is
        # measured and shipped as ``bakeDrift`` (with a coarse sanity bound),
        # while the hard self-consistency gate below checks the constraint
        # lengths against the snapshot pose they were baked in.
        has_cloth_child = {t: False for t in use_tr}
        for t in use_tr:
            f = S.tr_father(t)
            if f in has_cloth_child:
                has_cloth_child[f] = True
        max_dp = max_ds = max_dr_int = max_dr_leaf = 0.0
        for i, t in enumerate(use_tr):
            tt = S.tr[t]
            tp, trq, tsc = _v3(tt["m_LocalPosition"]), _q4(tt["m_LocalRotation"]), _v3(tt["m_LocalScale"])
            max_dp = max(max_dp, max(abs(a - b) for a, b in zip(tp, pos_l[i])))
            qd = min(max(abs(a - b) for a, b in zip(trq, rot_l[i])),
                     max(abs(a + b) for a, b in zip(trq, rot_l[i])))
            if has_cloth_child[t]:
                max_dr_int = max(max_dr_int, qd)
            else:
                max_dr_leaf = max(max_dr_leaf, qd)
            max_ds = max(max_ds, max(abs(a - b) for a, b in zip(tsc, scl_l[i])))
        comp["bakeDrift"] = {"maxPosDelta": max_dp, "maxRotDeltaInternal": max_dr_int,
                             "maxRotDeltaLeaf": max_dr_leaf, "maxSclDelta": max_ds}
        check(f"{cn}: bake-pose snapshot within sanity bounds of live pose",
              max_dp < 0.5 and max_ds < 1e-5, comp["bakeDrift"])

        # snapshot-pose worlds: serialized local TRS for cloth bones chained
        # onto the live world of whatever sits above the chain roots
        idx_of = {t: i for i, t in enumerate(use_tr)}
        snap_world = {}

        def _swm(t):
            if t in snap_world:
                return snap_world[t]
            i = idx_of[t]
            f = S.tr_father(t)
            if f in idx_of:
                pp, pr, ps = _swm(f)
            elif f is not None:
                pp, pr, ps = S.world_trs(f)
            else:
                pp, pr, ps = [0.0] * 3, [0.0, 0.0, 0.0, 1.0], [1.0] * 3
            sp = [pos_l[i][k] * ps[k] for k in range(3)]
            out = ([pp[k] + x for k, x in enumerate(_qrot(pr, sp))],
                   _qmul(pr, rot_l[i]), [ps[k] * scl_l[i][k] for k in range(3)])
            snap_world[t] = out
            return out

        snap = [_swm(t) for t in use_tr]
        sd = cloth.get("structDistanceDataList") or []
        max_rel = 0.0
        for e in sd:
            a, b = int(e["vertexIndex"]), int(e["targetVertexIndex"])
            L = float(e["length"])
            if L > 1e-9:
                max_rel = max(max_rel, abs(math.dist(snap[a][0], snap[b][0]) - L) / L)
        check(f"{cn}: structDistance lengths == bake-pose world distances ({len(sd)} edges)",
              max_rel < 1e-3, {"maxRelErr": max_rel})

        # clothTarget.rootList holds the authored chain-root transforms.  On
        # most components it equals the fixed set, but shipped data also
        # contains fixed non-root particles (collider anchor bones swept into
        # the chain walk and pinned by selection), so the exact invariant is:
        # every target root is a fixed chain top, and every moving vertex's
        # chain top is a target root.
        top_tr = {use_tr[v2] for v2 in fixed_set}
        moving_tops = {use_tr[root[v2]] for v2 in range(n)
                       if selection[v2] == 1 and root[v2] >= 0}
        check(f"{cn}: clothTarget roots within fixed tops and covering all "
              f"moving chains",
              set(target_roots) <= top_tr and moving_tops <= set(target_roots),
              {"targetRoots": comp["target"]["rootBones"],
               "fixedTops": [bones[v2] for v2 in sorted(fixed_set)]})

    if to_gltf:
        for comp in components:
            V = comp["vertices"]
            V["localPosition"] = [list(unity_to_gltf_pos(p)) for p in V["localPosition"]]
            V["localRotation"] = [list(unity_to_gltf_quat(q)) for q in V["localRotation"]]
            V["worldPosition"] = [list(unity_to_gltf_pos(p)) for p in V["worldPosition"]]
            V["worldRotation"] = [list(unity_to_gltf_quat(q)) for q in V["worldRotation"]]
        for c in colliders.values():
            c["center"] = list(unity_to_gltf_pos(c["center"]))
            bw = c["boneWorld"]
            bw["position"] = list(unity_to_gltf_pos(bw["position"]))
            bw["rotation"] = list(unity_to_gltf_quat(bw["rotation"]))
            if c["kind"] == "capsule":
                c["axisDirection"] = list(unity_to_gltf_pos(_AXIS_VEC[c["axis"]]))
            elif c["kind"] == "plane":
                c["normalDirection"] = list(unity_to_gltf_pos((0.0, 1.0, 0.0)))

    stats = {
        "monoBehaviourClassHistogram": dict(mb_classes),
        "boneClothComponents": len(components),
        "inactiveComponents": inactive,
        "chains": sum(sum(1 for p in c["vertices"]["parent"] if p < 0) for c in components),
        "vertices": sum(len(c["vertices"]["bones"]) for c in components),
        "movingVertices": sum(sum(1 for s in c["vertices"]["selection"] if s == 1)
                              for c in components),
        "collidersInBundle": len(colliders),
        "collidersReferenced": len(referenced),
        "byClass": dict(Counter(c["class"] for c in components)),
    }
    return {
        "coordinateSystem": ("gltf-right-handed-y-up-meters (x reflected from unity)"
                             if to_gltf else "unity-left-handed-y-up-meters"),
        "expectedVersions": EXPECTED_VERSIONS,
        "selectionEnum": {"invalid": 0, "move": 1, "fixed": 2, "extend": 3},
        "capsuleSemantics": {
            "axisDirection": "local unit axis in output space; endpoints = "
                             "boneWorld * (center +- axisDirection*length)",
            "length": "runtime segment HALF-length",
            "startRadius": "radius at -axis end (t=0)",
            "endRadius": "radius at +axis end (t=1)",
            "surfaceRadius": "lerp(startRadius, endRadius, t) + particleRadius(depth)",
        },
        "planeSemantics": {
            "normalDirection": "local unit normal in output space (the "
                               "transform's up axis); infinite plane through "
                               "boneWorld * center, particles pushed to the "
                               "normal side",
        },
        "omittedConstraintLists": list(_OMITTED_CONSTRAINTS),
        "stats": stats,
        "components": components,
        "colliders": [colliders[p] for p in sorted(colliders)],
        "checks": checks,
    }
