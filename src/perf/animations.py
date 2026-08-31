"""Export fixture and cut-scene AnimationClips as glTF animations.

The demo's hard blocker: furniture-performance animation data is almost entirely
not in hand.  The character motion library (``chara.motion_library``) covers
``mov_*`` humanoid clips, but a fixture's performance clips live **inside the
fixture package itself** and bind to a transform hierarchy by a ``crc32(path)``
hash — Unity stores an animation binding's target path as this hash, never as a
readable string (95/95 of a reference skeleton's ``m_TOS`` pairs reproduce it
exactly).

Two views of the same clip are written, and they are not interchangeable:

* the **verbatim curve table** (``animation.extras.curves``) — *every* decoded
  curve slot of the clip, at its source times, with its source values, in the
  source's own units and coordinate frame, keyed by the binding tuple
  ``(typeID, attribute, path, component)`` exactly as the typetree stores it
  (``path`` stays the raw ``crc32`` integer).  Nothing is resampled, converted,
  grouped, or dropped — a curve that never changes value is a ``const`` curve
  with one key and it is written like any other.  This is the fidelity record
  and it is what c4/c5 check.
* the **playable glTF view** (``animation.channels`` / ``samplers``) — only the
  subset that glTF can express: Transform bindings whose path hash resolves to
  a node of this package's tree, converted to glTF's right-handed frame.  Every
  curve that has no glTF counterpart is still in the verbatim table.

The clip families this lane must cover split cleanly along that line.  A
``fixture_*`` clip animates the fixture's own transform tree (typeID 4) and
lands in both views.  An ``act_*`` clip is a **humanoid character action**: its
data is entirely Animator curves (typeID 95 — root T/Q, the 95 muscles, and the
translate-DoF channels), which have no transform target at all and therefore no
glTF channel.  Dropping them, as an earlier revision did, silently emptied 802
of the 1403 ``fixture_timeline`` clips; they are real, decodable animation data
and are now written verbatim.

Path resolution is anchored, not guessed.  A binding path is relative to the
GameObject that carries the Animator, and a package may carry **several**
Animators (an egg fixture has one on the model root and one on the item child).
Every animator transform, and every hierarchy root, is used as an anchor when
building the hash table, in that order of preference, plus the full root-name
path as a last variant — so ``crc32("root/Hips/Spine/UpArm_R")`` resolves even
when the shallowest animator is not the tree root.

Anything that cannot be *represented* — a binding whose path hash matches no
node, a Euler-rotation curve that glTF has no channel for, a curve slot with no
binding record — is classified in ``anomalies`` and its data is still exported.
There is no catch-all "other" bucket.

The two views are also reconciled by count, not by trust.  ``channeled`` counts
curve *slots* and ``gltfChanneled`` counts grouped glTF *channels*, so their
difference is not a drop figure and cannot be read as one.  Every slot is
therefore classified into exactly one ``curveAccounting`` class — written as a
glTF channel, or excluded for a stated reason (a non-Transform ``typeID``, an
unresolved path, a Euler attribute, an incomplete component set) — and the
classes must sum to ``channeled`` with residual 0.  That is what makes a
resolution defect distinguishable from an Animator muscle curve glTF has no way
to express; without it both are just "not in the playable view".

Interpolation is carried verbatim: ``const`` curves become a one-keyframe
``STEP`` channel, ``linear`` curves a ``LINEAR`` channel, and Unity's
``cubic`` curve a ``CUBICSPLINE`` channel whose in/out tangents are recovered
from the curve's ``(a, b, c, d)`` polynomial coefficients (``d`` is the value at
the keyframe, ``c`` the out-tangent, and the in-tangent is the neighbour
``3a·Δt² + 2b·Δt + c``).
"""
import collections
import glob
import json
import os
import struct
import zlib

import UnityPy

from core.gltf import GLB, unity_to_gltf_pos, unity_to_gltf_quat
from chara.mecanim.clip import (ANIMATOR_TYPEID, TRANSFORM_TYPEID, ATTR_SIZE,
                                curve_index_map, decode)

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

# Artifact format revision.  Bumped when the on-disk shape changes so a resumed
# run re-exports stale packages instead of trusting them.  Bumping it is not
# optional when the *resolution* logic changes either: ``package_is_current``
# skips a package whose recorded version matches, so a fix that is not
# accompanied by a bump is silently eaten on the next run.
FORMAT_VERSION = 6

# Unity Transform attribute -> glTF channel property and glTF component width.
# Attribute 4 is *Euler* rotation: three components in the source, and glTF has
# no Euler rotation channel, so it never becomes a playable channel.
ATTR_NAME = {1: "translation", 2: "rotation", 3: "scale"}
ATTR_WIDTH = {1: 3, 2: 4, 3: 3}

# Verbatim value packing: how many floats one keyframe of each curve kind holds.
KIND_CODE = {"cubic": 0, "linear": 1, "const": 2}
VALUE_STRIDE = {"cubic": 4, "linear": 1, "const": 1}

SEMANTICS = "unity-crc32-path-bindings"


def _crc(path):
    """Unity's binding-path hash: ``crc32`` of the UTF-8 path string."""
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


def _vec(value, keys):
    return [float(value.get(k, 0.0)) for k in keys]


# ---------------------------------------------------------------------------
#  Rigs that live outside the animation's own package
# ---------------------------------------------------------------------------
#
#  A performance clip does not have to bind to a node of the package it ships
#  in.  Two whole classes of binding target a hierarchy that is somewhere else
#  entirely, and both were measured, not assumed:
#
#  * the **fixture model's own rig** (paths under a lowercase ``root/``).  The
#    ``mysekai__fixture_timeline__*`` packages carry clips for a model that
#    ships in the sibling ``mysekai__fixture__*`` package, so the bones are
#    simply not in the package being exported.
#  * the **character rig** (paths under a capital ``Root/``).  An ``act_*``
#    clip is a character action performed *at* a fixture; its transform curves
#    drive the character's bones (``Root/Hips/Spine/Arm_L``, the ``*_twist``
#    bones, hair and accessory offsets), never the furniture.
#
#  Both are read from glTF hierarchies already on disk and are used **only
#  after** the package's own anchored table has missed, so a foreign rig can
#  add resolutions and can never change one.  A resolved binding keeps its raw
#  hash verbatim; what resolution adds is the path string, the owning package,
#  and an imported node to hang the channel on.

FOREIGN_KIND_FIXTURE = "fixture-model"
FOREIGN_KIND_CHARACTER = "character-rig"

#  How a path string got into a hash table.  A binding path is stored relative
#  to the GameObject that carries the Animator, so the *rig-root-relative*
#  variant (top prefab node stripped) is the one Unity's own hashes are built
#  from and it must be the one that carries the corpus: ``crc32("Root/Hips")``
#  = 3794787658 is what a clip holds, never ``crc32("mdl_sd_101_001/Root/Hips")``.
#  The full-node-path variant is kept beside it because a hierarchy that has no
#  such wrapper node makes the two identical, and a deeper anchor can only add
#  resolutions.  Which variant answered a lookup is recorded per hash so the
#  two can never be reported as one number: if root-relative indexing regresses,
#  its count goes to 0 instead of the total merely shifting between buckets.
#  There is deliberately **no** leaf-name or path-suffix fallback here -- a
#  binding resolves on the whole path string or it is classified unresolved.
VARIANT_ROOT_RELATIVE = "rig-root-relative"
VARIANT_FULL_PATH = "full-node-path"
VARIANT_ANCHOR_RELATIVE = "animator-anchor-relative"


def _read_gltf_json(path):
    """The JSON chunk of a .glb, without touching its (large) binary chunk."""
    with open(path, "rb") as fh:
        if fh.read(4) != b"glTF":
            raise ValueError("not a glb: " + path)
        fh.read(8)
        length, kind = struct.unpack("<II", fh.read(8))
        chunk = fh.read(length)
    if kind != 0x4E4F534A:
        raise ValueError("first chunk is not JSON: " + path)
    return json.loads(chunk.decode("utf-8"))


class ForeignRig:
    """One hierarchy from another package, addressable by ``crc32(path)``.

    The node TRS is taken verbatim from the source glTF, so it is already in
    glTF's right-handed frame — imported nodes are marked ``space="gltf"`` and
    the scene writer must not convert them a second time.
    """

    def __init__(self, kind, package, doc):
        self.kind = kind
        self.package = package
        nodes = doc.get("nodes", [])
        parent = {}
        for i, node in enumerate(nodes):
            for c in node.get("children", []):
                parent[c] = i
        full = [None] * len(nodes)

        def full_path(i):
            if full[i] is None:
                name = nodes[i].get("name", "")
                full[i] = (name if i not in parent
                           else full_path(parent[i]) + "/" + name)
            return full[i]

        self.nodes = nodes
        self.full = [full_path(i) for i in range(len(nodes))]
        self.index_by_path = {}
        self.variant_by_path = {}
        roots = [i for i in range(len(nodes)) if i not in parent]
        for r in roots:                       # root-relative, one root at a time
            prefix = self.full[r]
            for i, fp in enumerate(self.full):
                if fp == prefix:
                    self._add("", i, VARIANT_ROOT_RELATIVE)
                elif fp.startswith(prefix + "/"):
                    self._add(fp[len(prefix) + 1:], i, VARIANT_ROOT_RELATIVE)
        for i, fp in enumerate(self.full):    # full root-name variant
            self._add(fp, i, VARIANT_FULL_PATH)

    def _add(self, path, index, variant):
        """Register *path* once, remembering which variant produced it."""
        if path not in self.index_by_path:
            self.index_by_path[path] = index
            self.variant_by_path[path] = variant

    def record(self, path):
        """The node record for *path*, in the shape ``NodeHierarchy`` holds."""
        i = self.index_by_path[path]
        node = self.nodes[i]
        return {
            "name": node.get("name", ""),
            "position": [float(x) for x in node.get("translation", [0.0] * 3)],
            "rotation": [float(x) for x in node.get("rotation", [0, 0, 0, 1])],
            "scale": [float(x) for x in node.get("scale", [1.0] * 3)],
            "space": "gltf",
        }


def _model_stem(package):
    """``mysekai__fixture_timeline__mdl_x`` and ``mysekai__fixture__mdl_x``
    share the stem ``mdl_x`` — that is how a clip package is matched to the
    model package whose rig it drives."""
    return package.rsplit("__", 1)[-1]


class ForeignRigs:
    """The searchable union of every foreign rig, ordered and deterministic."""

    def __init__(self, rigs=()):
        self.rigs = list(rigs)
        self.by_hash = {}
        self.ambiguous = {}
        for rig in self.rigs:
            for path in rig.index_by_path:
                digest = _crc(path)
                bucket = self.by_hash.setdefault(digest, [])
                if any(p != path for _, p in bucket):
                    self.ambiguous.setdefault(digest, set()).update(
                        {p for _, p in bucket} | {path})
                bucket.append((rig, path))

    def __bool__(self):
        return bool(self.rigs)

    @property
    def paths(self):
        return sum(len(r.index_by_path) for r in self.rigs)

    def lookup(self, path_hash, prefer=None):
        """``{"rig", "path", "kind", "package"}`` for *path_hash*, or ``None``.

        When several rigs carry the same path, the model package matching
        *prefer*'s stem wins, then the fixture-model kind, then the package
        name — so the choice never depends on filesystem order.
        """
        bucket = self.by_hash.get(path_hash)
        if not bucket:
            return None
        stem = _model_stem(prefer) if prefer else None
        best = min(bucket, key=lambda rp: (
            0 if stem and _model_stem(rp[0].package) == stem else 1,
            0 if rp[0].kind == FOREIGN_KIND_FIXTURE else 1,
            rp[0].package, rp[1]))
        return {"rig": best[0], "path": best[1],
                "kind": best[0].kind, "package": best[0].package,
                "variant": best[0].variant_by_path[best[1]]}

    # -- loaders ------------------------------------------------------------
    @classmethod
    def load(cls, fixture_models_dir=None, character_rig_paths=()):
        rigs = []
        if fixture_models_dir:
            for path in sorted(glob.glob(os.path.join(fixture_models_dir,
                                                      "*.glb"))):
                name = os.path.basename(path)[:-4]
                try:
                    rigs.append(ForeignRig(FOREIGN_KIND_FIXTURE, name,
                                           _read_gltf_json(path)))
                except Exception:
                    continue
        for path in sorted(character_rig_paths):
            name = os.path.basename(path)[:-4]
            try:
                rigs.append(ForeignRig(FOREIGN_KIND_CHARACTER, name,
                                       _read_gltf_json(path)))
            except Exception:
                continue
        return cls(rigs)


class NodeHierarchy:
    """The transform forest of one package, with an anchored path-hash table.

    ``nodes`` is an ordered list of ``Transform`` records covering **every**
    transform in the package — not only the first animator's subtree.  Each
    carries its full path (root name included), its animator-relative ``path``
    when it sits under the primary animator, its local TRS, its parent index
    (``-1`` for a root) and a stable ``name``.

    ``path_by_hash`` maps ``crc32(relative_path)`` back to that relative path
    string.  It is populated anchor by anchor, in preference order — primary
    animator, the other animators (shallowest first), each hierarchy root, then
    the full root-name path — and an already-registered hash is never
    overwritten, so the most specific anchor wins and a later variant can only
    add resolutions, never change one.
    """

    def __init__(self, objects, anchor=None, foreign=None, prefer=None):
        self.objects = list(objects)
        self.anchor = anchor
        self.foreign = foreign
        self.prefer = prefer
        self.nodes = []
        self.path_by_hash = {}
        self.animator_path = None
        self.anchors = []
        self.read_failures = []
        self._index_by_path = {}
        self._node_by_hash = {}
        self._variant_by_hash = {}
        self.native_hits = {}
        self.foreign_hits = {}
        self.foreign_roots = []
        self._imported = {}
        self._build()
        self.nativeNodes = len(self.nodes)

    # -- construction -------------------------------------------------------
    def _read_objects(self):
        """Read only the object types the tree needs.

        Filtering on ``obj.type.name`` *before* ``read_typetree`` matters: a
        fixture package carries meshes, textures and dozens of clips whose
        typetrees are large and would otherwise all be parsed to build a
        transform tree.
        """
        transforms, gameobjects, owner, animator_gos = {}, {}, {}, set()
        for obj in self.objects:
            name = obj.type.name
            if name not in ("Transform", "RectTransform", "GameObject", "Animator"):
                continue
            try:
                tree = obj.read_typetree()
            except Exception as exc:
                self.read_failures.append({
                    "pathId": getattr(obj, "path_id", None), "type": name,
                    "reason": "typetree read failed",
                    "detail": type(exc).__name__})
                continue
            if name in ("Transform", "RectTransform"):
                transforms[obj.path_id] = tree
                owner[obj.path_id] = tree.get("m_GameObject", {}).get("m_PathID")
            elif name == "GameObject":
                gameobjects[obj.path_id] = tree
            else:
                animator_gos.add(tree.get("m_GameObject", {}).get("m_PathID"))
        return transforms, gameobjects, owner, animator_gos

    def _build(self):
        transforms, gameobjects, owner, animator_gos = self._read_objects()
        if not transforms:
            return

        def name_of(pid):
            return (gameobjects.get(owner.get(pid), {}) or {}).get("m_Name", "")

        children, roots = {}, []
        for tid, tr in transforms.items():
            parent = tr.get("m_Father", {}).get("m_PathID", 0)
            if parent in transforms:
                children.setdefault(parent, []).append(tid)
            else:
                roots.append(tid)
        for pid in children:
            children[pid].sort(key=name_of)
        roots.sort(key=name_of)

        # BFS the whole forest so the scene holds every transform.  Parent
        # indices are assigned as we descend, so a child always follows its
        # parent in the list.
        queue, seen, index_by_pid = list(roots), set(), {}
        while queue:
            pid = queue.pop(0)
            if pid in seen:
                continue
            seen.add(pid)
            tr = transforms[pid]
            parent_pid = tr.get("m_Father", {}).get("m_PathID", 0)
            parent_index = index_by_pid.get(parent_pid, -1)
            if parent_index >= 0:
                parent_full = self.nodes[parent_index]["fullPath"]
                full = parent_full + "/" + name_of(pid)
            else:
                full = name_of(pid)
            index = len(self.nodes)
            self.nodes.append({
                "name": name_of(pid), "fullPath": full, "path": full,
                "position": _vec(tr.get("m_LocalPosition", {}), "xyz"),
                "rotation": _vec(tr.get("m_LocalRotation", {}), "xyzw"),
                "scale": _vec(tr.get("m_LocalScale", {}), "xyz"),
                "parent": parent_index,
            })
            index_by_pid[pid] = index
            queue.extend(children.get(pid, []))

        # Anchor candidates: every animator-owning transform, shallowest first
        # (a nested item animator must not outrank the model-root one), then
        # every hierarchy root.
        animator_indexes = [index_by_pid[pid] for pid in transforms
                            if owner.get(pid) in animator_gos and pid in index_by_pid]
        animator_indexes.sort(key=lambda i: (self.nodes[i]["fullPath"].count("/"),
                                             self.nodes[i]["fullPath"]))
        if self.anchor is not None and self.anchor in index_by_pid:
            forced = index_by_pid[self.anchor]
            animator_indexes = ([forced] +
                                [i for i in animator_indexes if i != forced])
        root_indexes = [index_by_pid[pid] for pid in roots if pid in index_by_pid]
        candidates = animator_indexes + [i for i in root_indexes
                                         if i not in animator_indexes]
        self.anchors = [self.nodes[i]["fullPath"] for i in candidates]

        primary = candidates[0] if candidates else None
        if primary is not None:
            prefix = self.nodes[primary]["fullPath"]
            for node in self.nodes:
                if node["fullPath"] == prefix:
                    node["path"] = ""
                elif node["fullPath"].startswith(prefix + "/"):
                    node["path"] = node["fullPath"][len(prefix) + 1:]
            self.animator_path = ""
        for i, node in enumerate(self.nodes):
            self._index_by_path.setdefault(node["path"], i)

        # Hash table, anchor by anchor.  First registration wins.
        for a in candidates:
            self._register_anchor(self.nodes[a]["fullPath"])
        for i, node in enumerate(self.nodes):          # full root-name variant
            self._register(node["fullPath"], i, VARIANT_FULL_PATH)

    def _register_anchor(self, prefix):
        for i, node in enumerate(self.nodes):
            full = node["fullPath"]
            if full == prefix:
                self._register("", i, VARIANT_ANCHOR_RELATIVE)
            elif full.startswith(prefix + "/"):
                self._register(full[len(prefix) + 1:], i,
                               VARIANT_ANCHOR_RELATIVE)

    def _register(self, path, index, variant):
        digest = _crc(path)
        if digest not in self.path_by_hash:
            self.path_by_hash[digest] = path
            self._node_by_hash[digest] = index
            self._variant_by_hash[digest] = variant

    # -- lookup -------------------------------------------------------------
    def node_index(self, path):
        return self._index_by_path.get(path)

    # -- foreign rigs -------------------------------------------------------
    def _foreign_hit(self, path_hash):
        if self.foreign is None:
            return None
        return self.foreign.lookup(path_hash, self.prefer)

    def _import_foreign(self, rig, path):
        """Import *path* and its ancestor chain from *rig*; return its index.

        Only the chain that a binding actually names is imported, so a rig
        contributes exactly the nodes the clips drive plus the parents needed
        to keep their local transforms meaningful.
        """
        key = (id(rig), path)
        if key in self._imported:
            return self._imported[key]
        parent_index = -1
        if "/" in path:
            parent_path = path.rsplit("/", 1)[0]
            if parent_path in rig.index_by_path:
                parent_index = self._import_foreign(rig, parent_path)
        record = rig.record(path)
        record.update({"fullPath": path, "path": path, "parent": parent_index,
                       "foreign": {"kind": rig.kind, "package": rig.package,
                                   "path": path}})
        index = len(self.nodes)
        self.nodes.append(record)
        self._imported[key] = index
        if parent_index < 0:
            self.foreign_roots.append(index)
        return index

    def resolve(self, path_hash):
        path = self.path_by_hash.get(path_hash)
        if path is not None:
            return path
        hit = self._foreign_hit(path_hash)
        return hit["path"] if hit else None

    def node_for_hash(self, path_hash):
        index = self._node_by_hash.get(path_hash)
        if index is not None:
            self.native_hits[path_hash] = self._variant_by_hash[path_hash]
            return index
        hit = self._foreign_hit(path_hash)
        if hit is None:
            return None
        index = self._import_foreign(hit["rig"], hit["path"])
        self.foreign_hits[path_hash] = {"kind": hit["kind"],
                                        "package": hit["package"],
                                        "path": hit["path"], "node": index,
                                        "variant": hit["variant"]}
        return index

    def foreign_counts(self):
        counts = collections.Counter(h["kind"] for h in self.foreign_hits.values())
        variants = collections.Counter(h["variant"]
                                       for h in self.foreign_hits.values())
        return {"total": len(self.foreign_hits), "byKind": dict(counts),
                "byVariant": dict(variants),
                "packages": sorted({h["package"]
                                    for h in self.foreign_hits.values()}),
                "importedNodes": len(self.nodes) - self.nativeNodes}

    def resolution_sources(self):
        """Which index variant answered each distinct hash that resolved.

        Native and foreign are kept apart, and inside each the variant is kept
        apart, because they are different claims: a corpus carried by
        ``rig-root-relative`` says the exporter reproduces Unity's own
        animator-relative hashing, while the same total carried by
        ``full-node-path`` would mean the clips name whole prefab paths.  A
        single "resolved" number cannot tell those apart, and the regression
        this lane was sent after is exactly a shift between them.
        """
        native = collections.Counter(self.native_hits.values())
        foreign = collections.Counter(h["variant"]
                                      for h in self.foreign_hits.values())
        return {"native": dict(native), "foreign": dict(foreign),
                "nativeTotal": len(self.native_hits),
                "foreignTotal": len(self.foreign_hits)}

    def write_scene(self, glb):
        """Append the node tree (TRS) to *glb* and return the root node indices.

        Imported foreign nodes are appended in the same array — a glTF channel
        needs a node to target — but they are **not** returned as roots of the
        package's own scene, so the default scene still holds exactly the
        package's own transforms.  Their TRS is already glTF-handed and is
        copied through untouched.
        """
        children = [[] for _ in self.nodes]
        for i, node in enumerate(self.nodes):
            if node["parent"] >= 0:
                children[node["parent"]].append(i)
        for i, node in enumerate(self.nodes):
            if node.get("space") == "gltf":
                entry = {"name": node["name"],
                         "translation": list(node["position"]),
                         "rotation": list(node["rotation"]),
                         "scale": list(node["scale"]),
                         "extras": {"foreign": node["foreign"]}}
            else:
                entry = {
                    "name": node["name"],
                    "translation": list(unity_to_gltf_pos(node["position"])),
                    "rotation": list(unity_to_gltf_quat(node["rotation"])),
                    "scale": list(node["scale"]),
                }
            if children[i]:
                entry["children"] = children[i]
            glb.g["nodes"].append(entry)
        return [i for i, node in enumerate(self.nodes)
                if node["parent"] < 0 and "foreign" not in node]


def _channel_interp(kind):
    return {"cubic": "CUBICSPLINE", "linear": "LINEAR", "const": "STEP"}[kind]


def _channel_points(kind, pts):
    """Per-keyframe (time, value, in_tangent, out_tangent) for one component.

    ``pts`` mirrors the shape ``decode`` produces, keyed by its ``kind``.  For
    ``const`` the single value is emitted at time 0 with zero tangents; for
    ``linear`` value only; for ``cubic`` the tangents are recovered from the
    polynomial coefficients ``(a, b, c, d)`` (value = d, out = c, in = neighbour
    ``3a·Δt² + 2b·Δt + c``).
    """
    out = []
    if kind == "const":
        out.append((0.0, pts[0][1], 0.0, 0.0))
    elif kind == "linear":
        for t, v in pts:
            out.append((t, v, 0.0, 0.0))
    else:  # cubic
        n = len(pts)
        for i, (t, (a, b, c, d)) in enumerate(pts):
            if i + 1 < n:
                dt = pts[i + 1][0] - t
                in_t = 3.0 * a * dt * dt + 2.0 * b * dt + c
            else:
                in_t = 0.0
            out.append((t, d, in_t, c))
    return out


def _verbatim_values(kind, pts):
    """The source values of one curve, flat, in source order and source units.

    ``cubic`` keeps all four polynomial coefficients per key — that is what the
    typetree holds, and reducing it to the keyframe value would throw away the
    segment shape.  ``linear`` and ``const`` keep their single value.
    """
    if kind == "cubic":
        return [float(x) for _, coeff in pts for x in coeff]
    return [float(v) for _, v in pts]


#  Classes for a hash that resolves in no hierarchy on disk.  The class is read
#  off the clip's *other* transform bindings, so it is evidence and not a guess.
#
#  The fourth class is **named** rather than left as an absent key.  It is the
#  one that means "no sibling told us anything", so it is exactly the class a
#  consumer most needs to see; written as a missing key it becomes
#  indistinguishable from a record some other producer wrote without the field
#  at all, and an aggregation by class then carries an unlabelled residual that
#  reads as "nothing to classify here".
UNRESOLVED_REASON = "path hash unresolved"
UNRESOLVED_CLASS_NONE = "nothing resolves"
UNRESOLVED_DETAIL = {
    FOREIGN_KIND_CHARACTER: "clip's other bindings are character-rig bones",
    FOREIGN_KIND_FIXTURE: "clip's other bindings are fixture-model nodes",
    "this package": "clip's other bindings are this package's nodes",
    UNRESOLVED_CLASS_NONE: "no binding of this clip resolves anywhere",
}


def _unresolved_class(resolved_kinds):
    for kind in (FOREIGN_KIND_CHARACTER, FOREIGN_KIND_FIXTURE, "this package"):
        if kind in resolved_kinds:
            return kind
    return UNRESOLVED_CLASS_NONE


#  Curve-slot accounting: where every decoded curve slot went.
#
#  ``channeled`` counts *curve slots*; ``gltfChanneled`` counts *grouped glTF
#  channels*, and one rotation channel consumes four slots — so the two numbers
#  are not in the same unit and their difference is not a drop count.  Before
#  this accounting existed that difference was simply unreadable: 56743 of
#  ``mysekai__character_motion``'s 58959 slots were outside the playable view,
#  every one of them present in the verbatim table, and nothing said which were
#  Animator muscle curves that glTF cannot express and which were transform
#  bindings whose path failed to resolve.  Both look like "not exported" from
#  outside, and only one of them is a defect.
#
#  So every slot is now classified into exactly one class and the classes must
#  sum to ``channeled``.  Each class states the evidence that put a slot there —
#  the binding's own ``typeID``, its attribute number, whether its path string
#  was recovered, whether a node was found — and none of them is a catch-all:
#  a slot with an unexpected ``typeID`` gets a class named after that number
#  rather than joining an "other" bucket, so a new binding kind shows up as its
#  own line instead of hiding inside one that already exists.
CLASS_GLTF = "gltf-channel"
CLASS_NO_BINDING = "no-binding-record"
CLASS_UNRESOLVED = "transform-path-unresolved"
CLASS_NO_NODE = "transform-resolved-but-no-node"
CLASS_EULER = "transform-euler-rotation-no-gltf-channel"
CLASS_UNMODELLED = "transform-attribute-not-modelled"
CLASS_UNGROUPED = "transform-group-not-channelable"
CLASS_EMPTY_CHANNEL = "transform-channel-has-no-keys"


def curve_class(typeid, attribute, resolved_path, node, key, emitted, empty):
    """The one accounting class of a curve slot, from the slot's own evidence.

    *key* is the slot's ``(pathHash, glTF property)`` pair; *emitted* and
    *empty* are the key sets of the channels that were written and of the ones
    that grouped but held no keyframe.  Nothing here guesses: a slot that is
    not a Transform binding is named after its ``typeID``, and a Transform
    binding is separated by whether its path resolved, whether a node was
    found, and whether its attribute has a glTF counterpart at all.
    """
    if typeid is None:
        return CLASS_NO_BINDING
    if typeid != TRANSFORM_TYPEID:
        return f"typeid-{typeid}-is-not-a-transform-binding"
    if node is None:
        return CLASS_UNRESOLVED if resolved_path is None else CLASS_NO_NODE
    if attribute == 4:
        return CLASS_EULER
    if attribute not in ATTR_WIDTH:
        return CLASS_UNMODELLED
    if key in emitted:
        return CLASS_GLTF
    if key in empty:
        return CLASS_EMPTY_CHANNEL
    return CLASS_UNGROUPED


def curve_accounting(curves, channels):
    """``{class: slots}`` over *curves*, exhaustive and residual-free.

    The caller asserts ``sum(counts.values()) == len(curves)``; it holds by
    construction here, which is exactly why the gate re-derives the same
    counts from the written ``.glb`` instead of trusting this return value.
    """
    emitted = {(c["pathHash"], c["property"]) for c in channels if c["times"]}
    empty = {(c["pathHash"], c["property"]) for c in channels if not c["times"]}
    counts = collections.Counter()
    for curve in curves:
        attribute = curve["attribute"]
        key = (curve["path"], ATTR_NAME.get(attribute))
        counts[curve_class(curve["typeID"], attribute, curve["pathString"],
                           curve["node"], key, emitted, empty)] += 1
    return dict(counts)


def decode_clip(clip_tt, hierarchy):
    """Decode one clip into ``(curves, channels, anomalies)``.

    *curves* is the verbatim table — one record per decoded curve slot, no
    exceptions.  *channels* is the playable glTF subset.  *anomalies* records
    every curve that could not become a playable channel, classified by reason;
    the curve's data is in *curves* either way.
    """
    bindings = clip_tt["m_ClipBindingConstant"]["genericBindings"]
    decoded = decode(clip_tt)
    index, _ = curve_index_map(bindings)

    curves, anomalies = [], []
    grouped = {}
    unresolved_seen, unbound = set(), 0
    unresolved_order, resolved_kinds = [], set()
    for slot in sorted(decoded):
        kind, pts = decoded[slot]
        info = index.get(slot)
        if info is None:
            typeid = attribute = component = None
            path_hash = None
            unbound += 1
        else:
            typeid, attribute, path_hash, component = info
        path_string, node, path_source = None, None, None
        if typeid == TRANSFORM_TYPEID:
            path_string = hierarchy.resolve(path_hash)
            if path_string is None:
                if path_hash not in unresolved_seen:
                    unresolved_seen.add(path_hash)
                    unresolved_order.append(path_hash)
            else:
                node = hierarchy.node_for_hash(path_hash)
                if node is None:
                    anomalies.append({"pathHash": path_hash, "path": path_string,
                                      "reason": "resolved path has no node"})
                else:
                    hit = hierarchy.foreign_hits.get(path_hash)
                    path_source = hit["kind"] if hit else "this package"
                    resolved_kinds.add(path_source)
            if node is not None and attribute in ATTR_WIDTH:
                grouped.setdefault((path_hash, attribute), {})[component] = (kind, pts)
        curves.append({
            "slot": slot, "typeID": typeid, "attribute": attribute,
            "path": path_hash, "pathString": path_string, "component": component,
            "pathSource": path_source,
            "node": node, "kind": kind,
            "times": [float(t) for t, _ in pts],
            "values": _verbatim_values(kind, pts),
            "stride": VALUE_STRIDE[kind],
        })
    if unbound:
        anomalies.append({"count": unbound,
                          "reason": "curve slot has no binding record"})

    # A hash that resolves nowhere keeps its raw value and is classified by the
    # only evidence there is: what the *other* transform bindings of the same
    # clip resolved to.  A clip whose siblings land on a character rig is
    # naming a bone of a rig variant not on disk; one whose siblings land on a
    # fixture model is naming a node of that model's rig.  The four classes are
    # exhaustive by construction and none of them is a catch-all: each states
    # the evidence that put the hash there, and each turns red the moment that
    # evidence changes.  Every record carries ``classification``, the fourth
    # class included, so grouping the output by that field leaves no residual.
    for path_hash in unresolved_order:
        classification = _unresolved_class(resolved_kinds)
        anomalies.append({"pathHash": path_hash,
                          "reason": UNRESOLVED_REASON,
                          "resolvedSiblingSources": sorted(resolved_kinds),
                          "classification": classification,
                          "detail": UNRESOLVED_DETAIL[classification]})

    # Transform bindings that exist but cannot become a glTF channel.
    for slot, info in sorted(index.items()):
        if slot not in decoded or info[0] != TRANSFORM_TYPEID:
            continue
        _, attribute, path_hash, component = info
        if attribute in ATTR_WIDTH or component:
            continue
        if attribute == 4:
            anomalies.append({"pathHash": path_hash, "attribute": attribute,
                              "reason": "euler rotation has no glTF channel"})
        else:
            anomalies.append({"pathHash": path_hash, "attribute": attribute,
                              "reason": "unmodelled transform attribute"})

    channels = []
    for (path_hash, attribute), parts in sorted(grouped.items()):
        width = ATTR_WIDTH[attribute]
        path = hierarchy.resolve(path_hash)
        node = hierarchy.node_for_hash(path_hash)
        kinds = {parts[k][0] for k in parts}
        if len(kinds) != 1:
            anomalies.append({"pathHash": path_hash, "path": path,
                              "attribute": attribute, "kinds": sorted(kinds),
                              "reason": "mixed-kind components"})
            continue
        if sorted(parts) != list(range(ATTR_SIZE.get(attribute, width))):
            anomalies.append({"pathHash": path_hash, "path": path,
                              "attribute": attribute,
                              "reason": "incomplete component set"})
            continue
        kind = kinds.pop()
        comp_points = {k: _channel_points(kind, parts[k][1]) for k in parts}
        times = [pt[0] for pt in comp_points[min(comp_points)]]
        values, in_tan, out_tan = [], [], []
        for i in range(len(times)):
            values.append([comp_points[k][i][1] if k in comp_points else 0.0
                           for k in range(width)])
            in_tan.append([comp_points[k][i][2] if k in comp_points else 0.0
                           for k in range(width)])
            out_tan.append([comp_points[k][i][3] if k in comp_points else 0.0
                            for k in range(width)])
        channels.append({
            "pathHash": path_hash, "path": path, "node": node,
            "property": ATTR_NAME[attribute],
            "interpolation": _channel_interp(kind),
            "kind": kind, "times": times, "values": values,
            "inTangents": in_tan, "outTangents": out_tan,
        })
    if not curves:
        # A clip that decoded nothing is either genuinely empty in the source or
        # a decoder defect, and those two must never share a bucket.  The test is
        # the source payload itself: no binding records *and* no curve data of
        # any of the three storage forms.  If any payload is present, nothing
        # decoding it is a defect and says so; the emptiness class cannot absorb
        # it, so it can never become a catch-all.
        muscle = clip_tt.get("m_MuscleClip") or {}
        # The three curve stores sit under ``m_Clip.data`` -- the same accessor
        # ``chara.mecanim.clip.decode`` uses.  Reading them one level too high
        # returns 0 for every clip and would turn this test into a rubber stamp.
        clip = (muscle.get("m_Clip") or {}).get("data") or {}
        dense = clip.get("m_DenseClip") or {}
        payload = {
            "genericBindings": len(bindings),
            "denseCurveCount": int(dense.get("m_CurveCount") or 0),
            "denseFrameCount": int(dense.get("m_FrameCount") or 0),
            "constantValues": len((clip.get("m_ConstantClip") or {}).get("data") or []),
            "streamedWords": len((clip.get("m_StreamedClip") or {}).get("data") or []),
            "streamedCurveCount": int((clip.get("m_StreamedClip") or {})
                                      .get("curveCount") or 0),
        }
        if any(payload.values()):
            anomalies.append({"reason": "curve payload present but nothing decoded",
                              "payload": payload})
        else:
            anomalies.append({"reason": "clip is empty in the source",
                              "payload": payload,
                              "stopTime": muscle.get("m_StopTime")})
    return curves, channels, anomalies


def decode_transform_channels(clip_tt, hierarchy):
    """The playable glTF view alone — ``(channels, anomalies)``."""
    _, channels, anomalies = decode_clip(clip_tt, hierarchy)
    return channels, anomalies


def _write_verbatim(glb, curves):
    """Pack every curve's source times and values into two flat accessors.

    One accessor pair per clip keeps the glTF JSON small: 100k curve records
    across the corpus would otherwise mean 400k accessor objects.  Each curve
    is located by ``(timeOffset, keyCount, valueOffset, stride)`` into the pair.
    """
    times, values, table = [], [], []
    for c in curves:
        t_off, v_off = len(times), len(values)
        times.extend(c["times"])
        values.extend(c["values"])
        table.append([c["slot"],
                      -1 if c["typeID"] is None else c["typeID"],
                      -1 if c["attribute"] is None else c["attribute"],
                      -1 if c["path"] is None else int(c["path"]),
                      -1 if c["component"] is None else c["component"],
                      KIND_CODE[c["kind"]], t_off, len(c["times"]), v_off,
                      c["stride"], -1 if c["node"] is None else c["node"]])
    extras = {"curveTable": table,
              "curveTableColumns": ["slot", "typeID", "attribute", "path",
                                    "component", "kind", "timeOffset",
                                    "keyCount", "valueOffset", "stride",
                                    "node"],
              "kindCodes": KIND_CODE,
              "paths": {str(c["path"]): c["pathString"] for c in curves
                        if c["pathString"] is not None},
              "pathSources": {str(c["path"]): c["pathSource"] for c in curves
                              if c["pathSource"] is not None}}
    if times:
        extras["time"] = glb.acc(struct.pack(f"<{len(times)}f", *times),
                                 5126, "SCALAR", len(times))
    if values:
        extras["value"] = glb.acc(struct.pack(f"<{len(values)}f", *values),
                                  5126, "SCALAR", len(values))
    return extras


def _add_animation(glb, clip_name, curves, channels, anomalies):
    """Append one glTF ``animation``: playable channels plus the verbatim table."""
    anim = {"name": clip_name, "samplers": [], "channels": [],
            "extras": _write_verbatim(glb, curves)}
    # Groups that reached this point and hold no keyframe leave no channel
    # behind, so the artifact would have no way to tell their curve slots from
    # slots whose group never formed.  Their keys are written down instead.
    empty_keys = []
    for ch in channels:
        times = ch["times"]
        if not times:
            anomalies.append({"clip": clip_name, "path": ch["path"],
                              "reason": "empty channel"})
            empty_keys.append([ch["pathHash"], ch["property"]])
            continue
        interp = ch["interpolation"]
        width = len(ch["values"][0])
        atype = "VEC3" if width == 3 else "VEC4"
        if ch["property"] == "translation":
            convert = lambda v: list(unity_to_gltf_pos(v))
        elif ch["property"] == "rotation":
            convert = lambda v: list(unity_to_gltf_quat(v))
        else:
            convert = list
        if interp == "CUBICSPLINE":
            trip = []
            for i, _ in enumerate(times):
                trip += [convert(ch["inTangents"][i]), convert(ch["values"][i]),
                         convert(ch["outTangents"][i])]
            rows = trip
        else:
            rows = [convert(v) for v in ch["values"]]
        times_acc = _write_accessor(glb, times, "SCALAR")
        values_acc = _write_accessor(glb, rows, atype)
        anim["samplers"].append({"input": times_acc, "output": values_acc,
                                 "interpolation": interp})
        # The raw binding hash rides along on the channel so the accounting
        # gate can rebuild ``(pathHash, property)`` from the artifact alone.
        # It is carried, never substituted for ``target.node``.
        anim["channels"].append({"sampler": len(anim["samplers"]) - 1,
                                 "target": {"node": ch["node"],
                                            "path": ch["property"]},
                                 "extras": {"pathHash": ch["pathHash"]}})
    anim["extras"]["emptyChannels"] = empty_keys
    glb.g.setdefault("animations", []).append(anim)
    return anim


def _write_accessor(glb, rows, atype):
    """Write *rows* (list of VECd or a flat SCALAR list) as one accessor."""
    if atype == "SCALAR":
        width, rows = 1, [[v] for v in rows]
    else:
        width = len(rows[0])
    data = bytearray()
    for row in rows:
        data += struct.pack(f"<{width}f", *row)
    return glb.acc(bytes(data), 5126, atype, len(rows))


# ---------------------------------------------------------------------------
#  Package-level export
# ---------------------------------------------------------------------------

def export_package(package_path, out_dir, clip_names=None, name=None,
                   foreign=None):
    """Export every requested (or every present) AnimationClip of one package.

    Each clip becomes a glTF ``animation`` carrying both views; the package's
    transform forest is written once as the scene.  Returns the package report
    record, which is also what lands in ``<name>.index.json``.
    """
    env = UnityPy.load(package_path)
    pkg_name = name or os.path.basename(package_path)
    hierarchy = NodeHierarchy(list(env.objects), foreign=foreign,
                              prefer=pkg_name)
    glb = GLB(generator="moly-root perf animations")
    wanted = set(clip_names) if clip_names is not None else None
    clips, seen, duplicates = [], set(), []
    for obj in env.objects:
        if obj.type.name != "AnimationClip":
            continue
        try:
            tree = obj.read_typetree()
        except Exception as exc:
            hierarchy.read_failures.append({
                "pathId": getattr(obj, "path_id", None), "type": "AnimationClip",
                "reason": "typetree read failed", "detail": type(exc).__name__})
            continue
        clip_name = str(tree.get("m_Name", ""))
        if not clip_name or (wanted is not None and clip_name not in wanted):
            continue
        if clip_name in seen:
            # Two AnimationClip objects under one name, and in this corpus they
            # are not copies: 13 names in ``mysekai__character_motion`` carry a
            # different binding count per object.  A glTF animation is addressed
            # by name, so only the first can be written -- but the drop is
            # recorded with the losing object's path_id and binding count rather
            # than skipped in silence.
            duplicates.append({
                "clip": clip_name,
                "pathId": getattr(obj, "path_id", None),
                "bindings": len(((tree.get("m_ClipBindingConstant") or {})
                                 .get("genericBindings") or [])),
                "reason": "duplicate clip name in package, first object kept"})
            continue
        seen.add(clip_name)
        try:
            curves, channels, anomalies = decode_clip(tree, hierarchy)
            index = len(glb.g.get("animations", []))
            _add_animation(glb, clip_name, curves, channels, anomalies)
            accounting = curve_accounting(curves, channels)
            clips.append({"name": clip_name, "animation": index,
                          "curves": len(curves),
                          "keys": sum(len(c["times"]) for c in curves),
                          "channels": len(curves),
                          "gltfChannels": len(channels),
                          "curveAccounting": accounting,
                          "anomalies": anomalies})
        except Exception as exc:                     # malformed curve block
            clips.append({"name": clip_name, "animation": None, "curves": 0,
                          "keys": 0, "channels": 0, "gltfChannels": 0,
                          "curveAccounting": {},
                          "anomalies": [{"clip": clip_name,
                                         "reason": "clip decode failed",
                                         "detail": f"{type(exc).__name__}: {exc}"}]})
    # The scene is written *after* the clips: resolving a binding against a
    # foreign rig imports nodes, and the node array must hold them before it is
    # handed to the glTF document, or a channel would point past its end.
    roots = hierarchy.write_scene(glb)
    glb.g["scenes"][0]["nodes"] = roots
    if hierarchy.foreign_roots:
        glb.g["scenes"].append({"name": "foreign-rigs",
                                "nodes": list(hierarchy.foreign_roots)})
    foreign_counts = hierarchy.foreign_counts()
    sources = hierarchy.resolution_sources()
    accounting = collections.Counter()
    for c in clips:
        accounting.update(c.get("curveAccounting") or {})
    glb.g["asset"]["extras"] = {"binding": SEMANTICS,
                                "formatVersion": FORMAT_VERSION,
                                "animatorPath": hierarchy.animator_path,
                                "anchors": hierarchy.anchors,
                                "foreignResolved": foreign_counts,
                                "resolutionSources": sources}
    os.makedirs(out_dir, exist_ok=True)
    glb_path = os.path.join(out_dir, f"{name or 'perf'}.glb")
    index_path = os.path.join(out_dir, f"{name or 'perf'}.index.json")
    glb.save(glb_path)
    for c in clips:
        for a in c.get("anomalies", []):
            a.setdefault("clip", c["name"])
    missing_wanted = sorted(wanted - seen) if wanted is not None else []
    record = {
        "version": 1, "formatVersion": FORMAT_VERSION, "binding": SEMANTICS,
        "package": name or os.path.basename(package_path),
        "animatorPath": hierarchy.animator_path,
        "anchors": hierarchy.anchors,
        "nodes": len(hierarchy.nodes),
        "nodeCount": len(hierarchy.nodes),
        "nativeNodeCount": hierarchy.nativeNodes,
        "foreignResolved": foreign_counts,
        "resolutionSources": sources,
        # Where every decoded curve slot went.  ``residual`` is the part of
        # ``channeled`` that no class claimed; it must be 0, and the gate
        # re-derives the whole table from the .glb rather than reading this.
        "curveAccounting": {"classes": dict(sorted(accounting.items())),
                            "total": sum(accounting.values()),
                            "gltfChannelSlots": accounting.get(CLASS_GLTF, 0),
                            "residual": sum(c["channels"] for c in clips)
                                        - sum(accounting.values())},
        "foreignHashes": {str(h): v for h, v in
                          sorted(hierarchy.foreign_hits.items())},
        "readFailures": hierarchy.read_failures,
        "clips": {c["name"]: c["channels"] for c in clips},
        "clipRecords": clips,
        "anomalies": [a for c in clips for a in c["anomalies"]] +
                     list(hierarchy.read_failures) + duplicates,
        "duplicateNames": duplicates,
        "missingWanted": missing_wanted,
        "exported": sum(1 for c in clips if c["channels"] > 0),
        "gltfExported": sum(1 for c in clips if c["gltfChannels"] > 0),
        "channeled": sum(c["channels"] for c in clips),
        "gltfChanneled": sum(c["gltfChannels"] for c in clips),
        "counts": {"discovered": len(seen),
                   "exported": sum(1 for c in clips if c["channels"] > 0),
                   "gltfExported": sum(1 for c in clips if c["gltfChannels"] > 0),
                   "channeled": sum(c["channels"] for c in clips),
                   "duplicateNames": len(duplicates),
                   "anomaly": sum(len(c["anomalies"]) for c in clips) +
                              len(duplicates)},
        "glb": glb_path, "index": index_path,
        "glbBytes": os.path.getsize(glb_path),
    }
    from core.jsonio import write_json
    write_json(index_path, record)
    return record


def package_is_current(out_dir, name):
    """True when *name*'s artifacts are on disk at the current format version.

    This is the resume test: a batch run skips such a package outright, so the
    export can be driven to completion by repeated invocation instead of one
    long run that must survive to the end to leave anything behind.
    """
    index_path = os.path.join(out_dir, f"{name}.index.json")
    glb_path = os.path.join(out_dir, f"{name}.glb")
    if not (os.path.exists(index_path) and os.path.exists(glb_path)):
        return False
    try:
        with open(index_path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception:
        return False
    return doc.get("formatVersion") == FORMAT_VERSION


def load_package_record(out_dir, name):
    with open(os.path.join(out_dir, f"{name}.index.json"), encoding="utf-8") as fh:
        return json.load(fh)


def export_targets(by_target, decrypted_dir, out_dir, limit=None, progress=None,
                   foreign=None):
    """Export up to *limit* not-yet-current packages, one written file each.

    Returns ``{"results", "exported", "skipped", "failed", "remaining"}``.  Each
    package is written to disk the moment it finishes, so an interrupted run
    loses at most the package in flight, and the next call picks up exactly
    where this one stopped.
    """
    os.makedirs(out_dir, exist_ok=True)
    results, exported, skipped, failed = {}, 0, 0, []
    pending = []
    for name in sorted(by_target):
        if package_is_current(out_dir, name):
            skipped += 1
            results[name] = load_package_record(out_dir, name)
        else:
            pending.append(name)
    for name in pending:
        if limit is not None and exported >= limit:
            break
        path = os.path.join(decrypted_dir, name)
        if not os.path.exists(path):
            results[name] = {"package": name, "missingPackage": True,
                             "formatVersion": FORMAT_VERSION, "clips": {},
                             "anomalies": [{"package": name,
                                            "reason": "package absent from decrypted set"}]}
            from core.jsonio import write_json
            write_json(os.path.join(out_dir, f"{name}.index.json"), results[name])
            exported += 1
            continue
        try:
            results[name] = export_package(path, out_dir,
                                           clip_names=sorted(by_target[name]),
                                           name=name, foreign=foreign)
        except Exception as exc:
            failed.append({"package": name, "reason": "package export failed",
                           "detail": f"{type(exc).__name__}: {exc}"})
            continue
        exported += 1
        if progress:
            progress(name, exported, len(pending))
    remaining = sum(1 for n in by_target if not package_is_current(out_dir, n))
    return {"results": results, "exported": exported, "skipped": skipped,
            "failed": failed, "remaining": remaining,
            "total": len(by_target)}


# ---------------------------------------------------------------------------
# Target set + batch driver
# ---------------------------------------------------------------------------

# Clip family label -> the ``clip-targets`` package prefix segment it comes
# from.  ``mysekai__cut_scene__x`` yields ``cut_scene``; ``mysekai__timeline__y``
# yields the timeline family.  The label is taken verbatim from the package
# name, never guessed, so a new family shows up as its own bucket instead of
# being folded into an existing one.
def target_family(package):
    return package.split("__")[1] if "__" in package else "?"


def load_clip_targets(targets_dir):
    """Read every resolved clip-target document into the two views this lane uses.

    Returns ``(fam, by_target)``:

    * ``fam[label][clipName]`` -> set of ``targetPackage`` the clip is referenced
      from, i.e. the *demand* side, grouped by clip family.
    * ``by_target[targetPackage]`` -> set of ``clipName`` wanted from that
      package, i.e. the *supply* side, which is what the exporter is driven by.

    Only ``clips[]`` is read.  The ``keyedClips`` table these documents also
    carry is a different question (which clip a keyframe selects) and is not
    part of the export target set.
    """
    fam = collections.defaultdict(lambda: collections.defaultdict(set))
    by_target = collections.defaultdict(set)
    for path in sorted(glob.glob(os.path.join(targets_dir, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        label = target_family(doc.get("package") or "")
        for clip in doc.get("clips", []):
            if not clip:
                continue
            name, target = clip.get("clipName"), clip.get("targetPackage")
            if not name or not target:
                continue
            fam[label][name].add(target)
            by_target[target].add(name)
    return fam, by_target


def _repo_data_dir():
    here = os.path.dirname(os.path.abspath(__file__))          # src/perf
    return os.path.join(os.path.dirname(os.path.dirname(here)), "local-data")


def main(argv=None):
    """Export one bounded batch of not-yet-current packages, then exit.

    The batch is the unit of progress on purpose: every package is written the
    moment it decodes, ``--limit`` caps how many *new* packages one invocation
    touches, and a package already on disk at the current ``FORMAT_VERSION`` is
    skipped without being reopened.  Repeated invocation therefore converges,
    and an invocation killed halfway keeps everything it had already written.
    """
    import argparse
    data = _repo_data_dir()
    ap = argparse.ArgumentParser(description="export perf AnimationClips in batches")
    ap.add_argument("--limit", type=int, default=200,
                    help="max NEW packages this invocation exports (0 = no cap)")
    ap.add_argument("--targets", default=os.path.join(data, "clip-targets", "out-on"))
    ap.add_argument("--decrypted", required=True,
                    help="directory containing the already-decrypted packages")
    ap.add_argument("--out", default=os.path.join(data, "perf-animations", "out"))
    args = ap.parse_args(argv)

    fam, by_target = load_clip_targets(args.targets)
    limit = None if args.limit == 0 else args.limit

    def progress(name, done, total):
        print(f"  [{done}/{total}] {name}", flush=True)

    report = export_targets(by_target, args.decrypted, args.out,
                            limit=limit, progress=progress)
    clips_done, clips_seen = set(), set()
    for rec in report["results"].values():
        for name, channels in (rec.get("clips") or {}).items():
            clips_seen.add(name)
            if channels > 0:
                clips_done.add(name)
    wanted = {n for names in by_target.values() for n in names}
    print(f"packages: exported {report['exported']} / skipped {report['skipped']} "
          f"/ failed {len(report['failed'])} / remaining {report['remaining']} "
          f"/ total {report['total']}")
    print(f"clips: wanted {len(wanted)} seen {len(clips_seen)} "
          f"with-curves {len(clips_done)}")
    for f in report["failed"]:
        print("  FAILED", f["package"], f["detail"])
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
