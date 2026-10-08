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
node in the package keeps the hash and says so instead of being dropped.  The
same one-way rule covers the *property* name: a non-transform binding
addresses a serialized property by a CRC-32 of its name, which is resolved
through the table of observed property names and left as the hash when the
table does not know it.

Curve values are written exactly as the segments store them.  A streamed segment's
keys are cubic coefficients, so they are labelled ``cubic`` and carry all four; a
dense segment is linear samples and a constant segment is one value.  Resampling
them to a fixed frame rate here would bake in a choice the consumer should make.

A clip's *events* are exported as bodies rather than as a count: an event is a
call at a time (the cannon fires one ``PlayEffect`` partway through its clip),
and the count alone says an event exists without saying when it happens or what
it calls, which is the one thing a consumer needs.
"""
import zlib

from chara.mecanim.clip import curve_index_map, decode

# Unity's binding type ids and the attributes a transform binding can name.
TRANSFORM_TYPEID = 4
TRANSFORM_ATTRIBUTES = {1: ("translation", 3), 2: ("rotation", 4),
                        3: ("scale", 3), 4: ("eulerAngles", 3)}

# The class each observed binding type id names.  Unity's native class ids are
# fixed, and every id here is confirmed by the field its bindings address:
# 224 bindings carry ``m_AnchoredPosition`` (RectTransform), 225 carries
# ``m_Alpha`` (CanvasGroup), 1 carries ``m_IsActive`` (GameObject), 114 carries
# ``m_Color`` (a UI Graphic behaviour), 198 carries ``EmissionModule.enabled``
# (ParticleSystem) and 23 carries ``m_Enabled`` (MeshRenderer); the last two
# are the class ids of the objects those bindings' paths lead to.
BINDING_TYPES = {1: "GameObject", 4: "Transform", 23: "MeshRenderer",
                 114: "MonoBehaviour", 198: "ParticleSystem",
                 224: "RectTransform", 225: "CanvasGroup"}

# Serialized property names a non-transform binding can address, keyed by the
# CRC-32 the binding carries in place of the name.  Every name is a real
# serialized field of the class its type id names (the RectTransform set is the
# typetree's own key list; ``m_Color`` is Graphic's; ``m_Alpha`` is
# CanvasGroup's; ``m_IsActive`` is GameObject's; ``EmissionModule.enabled`` is
# the ParticleSystem typetree's emission switch; ``m_Enabled`` is a Renderer's),
# and the ten hashes observed in the shipped clips all resolve through it.  A
# hash the table does not know stays a hash, visibly, rather than being guessed
# at.
ATTRIBUTE_NAMES = {zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF: name for name in (
    "m_AnchoredPosition.x", "m_AnchoredPosition.y", "m_AnchoredPosition.z",
    "m_SizeDelta.x", "m_SizeDelta.y",
    "m_AnchorMin.x", "m_AnchorMin.y", "m_AnchorMax.x", "m_AnchorMax.y",
    "m_Pivot.x", "m_Pivot.y",
    "m_LocalPosition.x", "m_LocalPosition.y", "m_LocalPosition.z",
    "m_LocalScale.x", "m_LocalScale.y", "m_LocalScale.z",
    "m_LocalRotation.x", "m_LocalRotation.y", "m_LocalRotation.z",
    "m_LocalRotation.w",
    "m_Color.r", "m_Color.g", "m_Color.b", "m_Color.a",
    "m_Alpha", "m_IsActive",
    "EmissionModule.enabled", "m_Enabled",
)}

UNDECODABLE = "compiled animation clip could not be decoded"
NO_CURVES = ("clip carries no curve data: its compiled segments are all empty, "
             "which is an authored state (an idle pose) and not a decode failure")


def _events(tree, record=None):
    """The clip's animation events, bodies and all.

    An event is a call the clip makes at a time: the cannon's clip fires one
    ``PlayEffect`` partway through, and a bare count cannot tell a consumer when
    to fire it or what to fire.  Unity gives every event all four parameter slots
    whether the call uses them or not, so they are all carried rather than
    guessed at from the function name.  ``frame`` is the time on the clip's own
    sample rate, which is what an authoring tool shows.
    """
    rate = float(tree.get("m_SampleRate", 0.0)) or 0.0
    out = []
    for event in tree.get("m_Events") or []:
        time = float(event.get("time", 0.0))
        reference = event.get("objectReferenceParameter") or {}
        entry = {"time": time,
                 "frame": time * rate if rate else None,
                 "functionName": str(event.get("functionName", "")),
                 "stringParameter": str(event.get("data", "")),
                 "floatParameter": float(event.get("floatParameter", 0.0)),
                 "intParameter": int(event.get("intParameter", 0)),
                 "messageOptions": int(event.get("messageOptions", 0)),
                 "objectParameter": None}
        if reference.get("m_PathID", 0):
            entry["objectParameter"] = {
                "pathId": reference.get("m_PathID"),
                "fileId": reference.get("m_FileID"),
                "name": _reference_name(record, reference)}
        out.append(entry)
    return out


def _reference_name(record, reference):
    """The name of an event's object parameter, when the package holds it."""
    if record is None:
        return None
    try:
        tree = record.tree(reference.get("m_PathID"))
    except Exception:                     # a pointer into another package
        return None
    return str(tree.get("m_Name")) if tree else None


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


def _attribute_name(type_id, attribute):
    """The property name a binding's attribute names, when it is known.

    A transform binding's attribute is one of Unity's four transform
    properties, and the table names those by number.  Every other binding
    addresses a serialized property **by a CRC-32 of its name**, which is
    one-way: the name is recovered by lookup, and an unknown hash is kept as
    the hash rather than guessed at.
    """
    if type_id == TRANSFORM_TYPEID:
        return TRANSFORM_ATTRIBUTES.get(attribute, (None, 1))[0]
    return ATTRIBUTE_NAMES.get(attribute)


def _binding(entry, hashes):
    type_id = entry.get("typeID")
    raw = entry.get("attribute")
    attribute, components = TRANSFORM_ATTRIBUTES.get(raw, (None, 1))
    digest = int(entry.get("path", 0)) & 0xFFFFFFFF
    named = _attribute_name(type_id, raw)
    return {"typeId": type_id,
            "typeName": BINDING_TYPES.get(type_id),
            "attribute": named if named is not None else raw,
            "attributeName": named is not None,
            "attributeHash": None if type_id == TRANSFORM_TYPEID else raw,
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
                "sampleRate": float(tree.get("m_SampleRate", 0.0)),
                "wrapMode": tree.get("m_WrapMode"),
                "stopTime": float((tree.get("m_MuscleClip") or {})
                                        .get("m_StopTime", 0.0)),
                "events": _events(tree, record),
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
            named = _attribute_name(type_id, attribute)
            entry.update(typeId=type_id, typeName=BINDING_TYPES.get(type_id),
                         node=hashes.get(digest & 0xFFFFFFFF),
                         pathHash=digest & 0xFFFFFFFF,
                         attribute=named if named is not None else attribute,
                         attributeName=named is not None,
                         attributeHash=None if type_id == TRANSFORM_TYPEID else attribute,
                         component=component)
        entry["keys"] = [[float(time),
                          ([float(v) for v in value]
                           if isinstance(value, (list, tuple))
                           else float(value))]
                         for time, value in points]
        document["curves"].append(entry)
    if not document["curves"]:
        return document, NO_CURVES
    return document, None
