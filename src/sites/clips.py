"""Animation clips of the site packages, with their targets named.

Doors open, water ripples, a cannon fires: the clips that drive those are ordinary
transform animation, and Unity stores them compiled rather than as editor curves —
the ``m_RotationCurves``/``m_PositionCurves`` lists are all empty and the real data
is one packed clip of streamed, dense and constant curve segments.  This repository
already decodes exactly that format, so the curves are decoded, not skipped.

What a compiled clip does *not* carry is the name of the object each curve drives:
a binding names its target by a hash of the node path.  Those hashes are CRC-32 of
the path string, which is checked rather than assumed — the packages' own node paths
are hashed and matched against the bindings, and a binding whose hash matches no
node in the package keeps the hash and says so instead of being dropped.

Curve values are written exactly as the segments store them.  A streamed segment's
keys are cubic coefficients, so they are labelled ``cubic`` and carry all four; a
dense segment is linear samples and a constant segment is one value.  Resampling
them to a fixed frame rate here would bake in a choice the consumer should make.
"""
import zlib

from chara.mecanim.clip import curve_index_map, decode

# Unity's binding type ids and the attributes a transform binding can name.
TRANSFORM_TYPEID = 4
TRANSFORM_ATTRIBUTES = {1: ("translation", 3), 2: ("rotation", 4),
                        3: ("scale", 3), 4: ("eulerAngles", 3)}

UNDECODABLE = "compiled animation clip could not be decoded"
NO_CURVES = ("clip carries no curve data: its compiled segments are all empty, "
             "which is an authored state (an idle pose) and not a decode failure")


def path_hashes(graph):
    """``{CRC-32 of a node path: the path}`` for every node under every root.

    A binding's path is relative to the object the animator sits on, and an
    animator can sit on any node, so every node's path is hashed relative to
    every root: a collision between two of them would need two identical paths,
    which cannot happen inside one root.
    """
    hashes = {}
    for root in graph.roots:
        for game_object, path in graph.node_paths(root).items():
            hashes[zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF] = path
    return hashes


def _binding(entry, hashes):
    attribute, components = TRANSFORM_ATTRIBUTES.get(
        entry.get("attribute"), (None, 1))
    digest = int(entry.get("path", 0)) & 0xFFFFFFFF
    return {"typeId": entry.get("typeID"),
            "attribute": attribute if entry.get("typeID") == TRANSFORM_TYPEID
            else entry.get("attribute"),
            "components": components,
            "pathHash": digest,
            "node": hashes.get(digest),
            "resolved": digest in hashes}


def clip_document(record, path_id, hashes):
    """One clip: its settings, its bindings and its decoded curves.

    Returns ``(document, reason)``; *reason* is ``None`` when the clip decoded.
    """
    tree = record.tree(path_id)
    document = {"name": str(tree.get("m_Name", "")),
                "legacy": bool(tree.get("m_Legacy", 0)),
                "sampleRate": round(float(tree.get("m_SampleRate", 0.0)), 6),
                "wrapMode": tree.get("m_WrapMode"),
                "stopTime": round(float((tree.get("m_MuscleClip") or {})
                                        .get("m_StopTime", 0.0)), 6),
                "events": len(tree.get("m_Events") or []),
                "bindings": [], "curves": []}
    bindings = (tree.get("m_ClipBindingConstant") or {}).get("genericBindings") or []
    document["bindings"] = [_binding(entry, hashes) for entry in bindings]
    document["unresolvedBindings"] = sum(1 for entry in document["bindings"]
                                         if not entry["resolved"])
    try:
        curves = decode(tree)
        index_map, _ = curve_index_map(bindings)
    except Exception as exc:              # unreadable or unfamiliar clip layout
        return document, f"{UNDECODABLE}: {type(exc).__name__}: {exc}"
    for index in sorted(curves):
        kind, points = curves[index]
        target = index_map.get(index)
        entry = {"curve": index, "kind": kind}
        if target is not None:
            type_id, attribute, digest, component = target
            name, _ = TRANSFORM_ATTRIBUTES.get(attribute, (None, 1))
            entry.update(typeId=type_id, node=hashes.get(digest & 0xFFFFFFFF),
                         pathHash=digest & 0xFFFFFFFF,
                         attribute=name if type_id == TRANSFORM_TYPEID else attribute,
                         component=component)
        entry["keys"] = [[round(float(time), 6),
                          ([round(float(v), 6) for v in value]
                           if isinstance(value, (list, tuple))
                           else round(float(value), 6))]
                         for time, value in points]
        document["curves"].append(entry)
    if not document["curves"]:
        return document, NO_CURVES
    return document, None
