"""Embedded furniture animations: AnimationClip -> glTF ``animations``.

The geometry pass (:mod:`fixtures.meshes`) used to write furniture packages
whose scenes and nodes carried the full prefab trees but whose ``animations``
array did not exist at all: every AnimationClip a package ships was dropped
between reading and writing.  The clips are real and on disk --
``mysekai__fixture__mdl_clb1102_fixture_egg1`` alone decodes to 51 of them
(the idle / nod / tilthead family) -- and the furniture-performance timeline
consumes them by name, so the loss surfaced downstream as
``change_fixture_timeline`` refusing to run for want of clip data.  This module
closes that at the extractor: the clips ride inside the same ``.glb`` as the
geometry they animate (no sidecar), which is where a glTF consumer expects
them.

Bindings and path hashes
    A clip's ``m_ClipBindingConstant.genericBindings`` name its targets by
    ``(typeID, attribute, crc32(path))`` -- Unity stores the target path only
    as this hash, never as a readable string, so the hash table is rebuilt
    here from the node trees the walk produced.  A binding path is relative
    to the GameObject carrying the Animator, and a package may hold several
    animators (an egg fixture has one on the model root and one on the item
    child), so registration is *anchored*: animator-carrying variants come
    first, within a variant the animator anchors shallowest-first then the
    variant root, every node is registered relative to the current anchor,
    and whole full-node paths come last.  First registration wins and a later
    one can never change it -- a doll package's three size variants share the
    same relative paths, and without a fixed priority the animation would
    bind to whichever variant happened to be walked first.

Space and interpolation
    Values are written **verbatim** in the authored Unity frame, exactly like
    the node transforms of this glb (see the module docstring of
    :mod:`fixtures.meshes` for why this product deliberately does not convert
    to glTF's right-handed frame).  Interpolation is mapped from the source
    curve kind -- the three storage forms of :func:`chara.mecanim.clip.decode`:
    ``StreamedClip`` keys are cubic polynomial coefficients and become a
    ``CUBICSPLINE`` sampler (tangents recovered from the ``(a, b, c, d)``
    coefficients: the value is ``d``, the out-tangent ``c``, the in-tangent
    the neighbouring segment's ``3a*dt^2 + 2b*dt + c``); ``DenseClip`` frames
    are evenly sampled and become ``LINEAR``; ``ConstantClip`` values become
    ``STEP`` with their single key.  Rotation stays the authored quaternion
    (attribute 2 carries the four components directly); Euler rotation
    (attribute 4) has no glTF channel and is counted, not converted.

What is *not* exported, on purpose
    Non-Transform bindings -- the ``m_Float`` family: physics curves, material
    and blendshape properties, Animator muscle slots -- have no TRS target on
    the node tree.  They are accounted per slot in the package's index record
    and never silently dropped into an "other" bucket: each lands in a class
    named after its own evidence (the binding's typeID), so a new binding kind
    shows up as its own line.  Animation retargeting is out of scope: a
    binding whose hash resolves to no node here is reported, not guessed.
"""
import collections
import struct
import zlib

from chara.mecanim.clip import TRANSFORM_TYPEID, curve_index_map, decode, hermite_keyframes
from core.assets.identity import identity, json_pointers

# Unity Transform generic-binding attribute -> glTF channel property, and the
# component width each carries.  Attribute 4 is Euler rotation: three source
# components, no glTF counterpart, counted separately.
ATTR_PROPERTY = {1: "translation", 2: "rotation", 3: "scale"}
ATTR_WIDTH = {1: 3, 2: 4, 3: 3}

# Source curve kind -> glTF sampler interpolation.  The mapping is one-to-one
# with the three storage forms the decoder distinguishes.
INTERPOLATION = {"cubic": "CUBICSPLINE", "linear": "LINEAR", "const": "STEP"}

BINDING_SEMANTICS = "unity-crc32-path-bindings"
SPACE = "authored-unity"

# Curve-slot accounting classes.  Every decoded slot lands in exactly one, and
# the classes must sum to the slot count -- the same contract the perf lane's
# animation export uses, so the two products read the same way.
CLASS_GLTF = "gltf-channel"
CLASS_NO_BINDING = "no-binding-record"
CLASS_UNRESOLVED = "transform-path-unresolved"
CLASS_EULER = "transform-euler-rotation-no-gltf-channel"
CLASS_UNMODELLED = "unmodelled-transform-attribute"
CLASS_MIXED_KIND = "transform-group-mixed-kind"
CLASS_KEY_DISAGREE = "transform-group-key-counts-disagree"
CLASS_EMPTY = "transform-group-empty"


def _crc(path):
    """Unity's binding-path hash: crc32 of the UTF-8 path string."""
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


def build_hash_table(variants):
    """crc32 path hash -> ``(node index, path, variant, source)``.

    *variants* is the ordered list the geometry walk produced, each
    ``{"paths": {fullPath: nodeIndex}, "root": rootFullPath,
    "animators": [fullPath, ...]}``.  See the module docstring for the
    registration order and why it is fixed, not incidental.
    """
    table = {}

    def register(digest, entry):
        if digest not in table:
            table[digest] = entry

    order = ([i for i, v in enumerate(variants) if v["animators"]] +
             [i for i, v in enumerate(variants) if not v["animators"]])
    for vi in order:
        variant = variants[vi]
        anchors = sorted(variant["animators"],
                         key=lambda p: (p.count("/"), p)) + [variant["root"]]
        for anchor in anchors:
            for full, node in variant["paths"].items():
                if full == anchor or not full.startswith(anchor + "/"):
                    continue
                rel = full[len(anchor) + 1:]
                register(_crc(rel), (node, rel, vi, "animator-anchor-relative"))
    for vi in order:
        variant = variants[vi]
        for full, node in variant["paths"].items():
            register(_crc(full), (node, full, vi, "full-node-path"))
    return table


def _component_points(kind, pts):
    """Per-keyframe ``(time, value, inTangent, outTangent)`` of one curve.

    Mirrors the perf lane's playable view: ``const`` emits its single value at
    time 0 with zero tangents, ``linear`` its samples with zero tangents, and
    ``cubic`` recovers the hermite tangents from the stored polynomial
    coefficients ``(a, b, c, d)``.
    """
    return hermite_keyframes(kind, pts)


def _accessor(glb, rows, atype):
    """Write *rows* (list of equal-width float tuples) as one accessor."""
    width = len(rows[0])
    data = bytearray()
    for row in rows:
        data += struct.pack(f"<{width}f", *row)
    return glb.acc(bytes(data), 5126, atype, len(rows))


def decode_clip(tt, table):
    """Decode one clip into ``(channels, accounting, anomalies)``.

    *channels* is the playable glTF subset: one record per ``(path hash,
    attribute)`` group whose components all decoded, resolve to a node, and
    agree on kind and key count.  *accounting* covers **every** decoded slot
    exhaustively; *anomalies* names the groups that could not become a channel
    and the hashes that resolved nowhere.
    """
    bindings = (tt.get("m_ClipBindingConstant") or {}).get("genericBindings") or []
    decoded = decode(tt)
    index, _ = curve_index_map(bindings)

    accounting = collections.Counter()
    groups = collections.defaultdict(dict)
    unresolved = []
    anomalies = []
    for slot in sorted(decoded):
        kind, pts = decoded[slot]
        info = index.get(slot)
        if info is None:
            accounting[CLASS_NO_BINDING] += 1
            continue
        tid, attr, path_hash, comp = info
        if tid != TRANSFORM_TYPEID:
            # The m_Float family and every other non-TRS target: counted under
            # a class named after the binding's own typeID, never merged.
            accounting[f"typeid-{tid}-is-not-a-transform-binding"] += 1
            continue
        if attr == 4:
            accounting[CLASS_EULER] += 1
            continue
        if attr not in ATTR_WIDTH:
            accounting[CLASS_UNMODELLED] += 1
            continue
        hit = table.get(path_hash)
        if hit is None:
            accounting[CLASS_UNRESOLVED] += 1
            if path_hash not in unresolved:
                unresolved.append(path_hash)
            continue
        groups[(path_hash, attr)][comp] = (kind, pts, hit)

    channels = []
    for (path_hash, attr), parts in sorted(groups.items()):
        width = ATTR_WIDTH[attr]
        kinds = {kind for kind, _, _ in parts.values()}
        if len(kinds) != 1:
            accounting[CLASS_MIXED_KIND] += len(parts)
            anomalies.append({"pathHash": path_hash, "attribute": attr,
                              "kinds": sorted(kinds),
                              "reason": "mixed-kind components"})
            continue
        counts = {len(pts) for _, pts, _ in parts.values()}
        if len(counts) != 1:
            accounting[CLASS_KEY_DISAGREE] += len(parts)
            anomalies.append({"pathHash": path_hash, "attribute": attr,
                              "keyCounts": sorted(counts),
                              "reason": "component key counts disagree"})
            continue
        kind = kinds.pop()
        count = counts.pop()
        if not count:
            accounting[CLASS_EMPTY] += len(parts)
            anomalies.append({"pathHash": path_hash, "attribute": attr,
                              "reason": "curve group decodes to no keyframes"})
            continue
        comp_points = {k: _component_points(kind, pts)
                       for k, (_, pts, _) in parts.items()}
        node, path, variant, source = parts[min(parts)][2]
        channels.append({
            "pathHash": path_hash, "path": path, "node": node,
            "variant": variant, "source": source,
            "components": len(parts),
            "property": ATTR_PROPERTY[attr], "kind": kind,
            "count": count,
            "times": [pt[0] for pt in comp_points[min(comp_points)]],
            "values": [[comp_points[k][i][1] if k in comp_points else 0.0
                        for k in range(width)] for i in range(count)],
            "inTangents": [[comp_points[k][i][2] if k in comp_points else 0.0
                            for k in range(width)] for i in range(count)],
            "outTangents": [[comp_points[k][i][3] if k in comp_points else 0.0
                             for k in range(width)] for i in range(count)],
        })
    for path_hash in unresolved:
        anomalies.append({"pathHash": path_hash,
                          "reason": "binding path hash unresolved in this "
                                    "package's node trees"})
    accounting[CLASS_GLTF] = sum(ch["components"] for ch in channels)
    return channels, dict(accounting), anomalies, len(decoded)


def _write_channels(glb, name, channels):
    """One glTF animation from *channels*; returns the glTF animation entry."""
    anim = {"name": name, "samplers": [], "channels": []}
    resolved = {}
    for ch in channels:
        resolved[str(ch["pathHash"])] = ch["path"]
        interp = INTERPOLATION[ch["kind"]]
        width = len(ch["values"][0])
        atype = "VEC3" if width == 3 else "VEC4"
        if interp == "CUBICSPLINE":
            rows = []
            for i in range(ch["count"]):
                rows.append(ch["inTangents"][i])
                rows.append(ch["values"][i])
                rows.append(ch["outTangents"][i])
        else:
            rows = ch["values"]
        times_acc = _accessor(glb, [[t] for t in ch["times"]], "SCALAR")
        values_acc = _accessor(glb, rows, atype)
        anim["samplers"].append({"input": times_acc, "output": values_acc,
                                 "interpolation": interp})
        anim["channels"].append({
            "sampler": len(anim["samplers"]) - 1,
            "target": {"node": ch["node"], "path": ch["property"]},
            "extras": {"pathHash": ch["pathHash"], "variant": ch["variant"],
                       "source": ch["source"]}})
    if resolved:
        anim["extras"] = {"space": SPACE, "binding": BINDING_SEMANTICS,
                          "resolvedPaths": resolved}
    else:
        anim["extras"] = {"space": SPACE, "binding": BINDING_SEMANTICS}
    return anim


def embed(glb, package, tables, report):
    """Append every AnimationClip of *package* as a glTF animation.

    *tables* is the per-variant list :func:`build_hash_table` consumes, in the
    order the geometry walk produced it.  A clip that cannot be decoded is
    reported and skipped rather than allowed to fail the package: the geometry
    must not be lost to one malformed curve block.  Returns the package's
    animation summary for the index document.
    """
    table = build_hash_table(tables)
    summary = {"clipCount": 0, "gltfChannels": 0, "channeledSlots": 0,
               "floatSlots": 0, "unresolvedSlots": 0, "anomalies": []}
    clips = []
    seen = set()
    for record in package.files:
        for pid, kind in (record.kinds or {}).items():
            if kind != "AnimationClip":
                continue
            obj = record.objects.get(pid)
            if obj is None:
                summary["anomalies"].append(
                    {"type": "clip-unreadable", "pathId": pid})
                continue
            try:
                tt = obj.read_typetree()
            except Exception as exc:
                summary["anomalies"].append(
                    {"type": "clip-unreadable", "pathId": pid,
                     "detail": f"{type(exc).__name__}: {exc}"})
                continue
            name = str(tt.get("m_Name") or "")
            if not name:
                summary["anomalies"].append(
                    {"type": "clip-unnamed", "pathId": pid})
                continue
            if name in seen:
                # A glTF animation is addressed by name, so only the first
                # object under a name can be written; the loser is recorded.
                summary["anomalies"].append(
                    {"type": "clip-duplicate-name", "clip": name,
                     "pathId": pid, "reason": "duplicate clip name in package,"
                                              " first object kept"})
                continue
            seen.add(name)
            try:
                channels, accounting, anomalies, slots = decode_clip(tt, table)
            except Exception as exc:
                summary["anomalies"].append(
                    {"type": "clip-decode-failed", "clip": name,
                     "detail": f"{type(exc).__name__}: {exc}"})
                continue
            if not channels:
                summary["anomalies"].append(
                    {"type": "clip-no-channel", "clip": name,
                     "slots": slots})
            index = len(glb.g.get("animations", []))
            anim = _write_channels(glb, name, channels)
            anim.setdefault("extras", {}).update(
                sourceClip=identity(record, pid),
                sourceEvents=json_pointers(tt.get("m_Events", [])))
            glb.g.setdefault("animations", []).append(anim)
            summary["clipCount"] += 1
            summary["gltfChannels"] += len(channels)
            summary["channeledSlots"] += accounting.get(CLASS_GLTF, 0)
            summary["floatSlots"] += sum(
                v for k, v in accounting.items()
                if k.endswith("-is-not-a-transform-binding"))
            summary["unresolvedSlots"] += accounting.get(CLASS_UNRESOLVED, 0)
            for anomaly in anomalies:
                anomaly["clip"] = name
                summary["anomalies"].append(
                    {"type": "clip-anomaly", "clip": name, "detail": anomaly})
            clips.append({"name": name, "animation": index,
                          "bindings": len((tt.get("m_ClipBindingConstant")
                                           or {}).get("genericBindings") or []),
                          "slots": slots, "channels": len(channels),
                          "keys": sum(ch["count"] for ch in channels)})
    report["animations"] = clips
    return summary
