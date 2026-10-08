"""Source-authored environment timeline graphs and their typed payloads.

Every edge retains its owning object and original PPtr. Array order, including
same-label siblings and same-start clips, is source data. The custom environment
mixers evaluate director time directly; exported clip remapping fields must not
be mistaken for a generic Timeline mixer implementation.
"""
from core.assets.identity import object_identity, source_reference
from core.particles import min_max_gradient

TIMELINE_SCRIPT = "TimelineAsset"

# What a track drives.  Both enumerations have the same shape: 0 is "nothing".
COLOR_TARGETS = {0: "none", 1: "skyAdditiveColor", 2: "lightAdditiveColor"}
VALUE_TARGETS = {0: "none", 1: "skyAdditiveIntensity", 2: "lightAdditiveIntensity"}

TRACK_TARGETS = {"SiteEnvironmentColorTrack": ("_controlTarget", COLOR_TARGETS),
                 "SiteEnvironmentValueTrack": ("_controlTarget", VALUE_TARGETS)}

UNMODELLED_CLIP = "timeline clip class not modelled"
UNMODELLED_TRACK = "timeline track class not modelled"

MODELLED_TRACKS = ("SiteEnvironmentColorTrack", "SiteEnvironmentValueTrack",
                   "ValueNoiseTrack", "MarkerTrack")


def _keys(curve):
    """Keyframes of one animation curve, as authored.

    Infinite slopes mean a stepped key; they are reported as ``None`` rather than
    as a number no JSON reader can represent.
    """
    out = []
    for key in (curve or {}).get("m_Curve") or []:
        entry = {"time": float(key.get("time", 0.0)),
                 "value": float(key.get("value", 0.0))}
        for name in ("inSlope", "outSlope"):
            slope = float(key.get(name, 0.0))
            entry[name] = (None if slope in (float("inf"), float("-inf"))
                           else slope)
        entry["weightedMode"] = key.get("weightedMode")
        entry["inWeight"] = float(key.get("inWeight", 0.0))
        entry["outWeight"] = float(key.get("outWeight", 0.0))
        out.append(entry)
    return out


def _curve(curve):
    return {"keys": _keys(curve),
            "preInfinity": (curve or {}).get("m_PreInfinity"),
            "postInfinity": (curve or {}).get("m_PostInfinity")}


def clip_asset(class_name, tree):
    """The authored payload of one clip, by clip class.  Returns ``(body, gap)``."""
    if class_name == "SiteEnvironmentColorClip":
        gradient = min_max_gradient({"minMaxState": 1,
                                     "maxGradient": tree.get("_gradient") or {}})["gradient"]
        gradient["mode"] = (tree.get("_gradient") or {}).get("m_Mode")
        gradient["colorSpace"] = (tree.get("_gradient") or {}).get("m_ColorSpace")
        return {"gradient": gradient}, None
    if class_name == "SiteEnvironmentValueClip":
        return {"scale": float(tree.get("_scale", 0.0)),
                "curve": _curve(tree.get("_curve"))}, None
    if class_name == "ValueNoiseClip":
        return {"intensity": float(tree.get("_noiseIntensity", 0.0)),
                "frequency": float(tree.get("_noiseFrequency", 0.0)),
                "intensityCurve": _curve(tree.get("_noiseIntensityCurve"))}, None
    return None, UNMODELLED_CLIP


def _clip(tree, class_name, body):
    return {"start": float(tree.get("m_Start", 0.0)),
            "duration": float(tree.get("m_Duration", 0.0)),
            "clipIn": float(tree.get("m_ClipIn", 0.0)),
            "timeScale": float(tree.get("m_TimeScale", 1.0)),
            "label": str(tree.get("m_DisplayName", "")),
            "class": class_name,
            "blendInDuration": float(tree.get("m_BlendInDuration", 0.0)),
            "blendOutDuration": float(tree.get("m_BlendOutDuration", 0.0)),
            "easeInDuration": float(tree.get("m_EaseInDuration", 0.0)),
            "easeOutDuration": float(tree.get("m_EaseOutDuration", 0.0)),
            "preExtrapolation": tree.get("m_PreExtrapolationMode"),
            "postExtrapolation": tree.get("m_PostExtrapolationMode"),
            "asset": body}


def source_identity(record, path_id):
    """Compatibility name for the shared, signed-64-bit source identity export."""
    return object_identity(record, path_id)


def _identity_key(record, path_id):
    return record.bundle, record.archive, path_id


def _script_class(record, path_id, follow):
    target = follow(record, record.tree(path_id).get("m_Script") or {})
    return str(target[0].tree(target[1]).get("m_ClassName", "")) if target else ""


def decode_timeline(path_id, record, follow):
    """Export the complete authored structural graph, without label identities."""
    tree = record.tree(path_id)
    root_pointers = tree.get("m_Tracks") or []
    marker_pointer = tree.get("m_MarkerTrack") or {}
    document = {"schemaVersion": 3, "source": source_identity(record, path_id),
                "name": str(tree.get("m_Name", "")),
                "duration": float(tree.get("m_FixedDuration", 0.0)),
                "durationMode": tree.get("m_DurationMode"),
                "frameRate": (tree.get("m_EditorSettings") or {}).get("m_Framerate"),
                "rootReferences": [source_reference(record, path_id, p, follow) for p in root_pointers],
                "markerReference": source_reference(record, path_id, marker_pointer, follow),
                "tracks": [], "unsupported": []}
    unsupported = document["unsupported"]
    pending = [(record, path_id, pointer, None, False, order)
               for order, pointer in enumerate(root_pointers)]
    if marker_pointer.get("m_PathID", 0):
        pending.append((record, path_id, marker_pointer, None, True, 0))
    seen = set()
    while pending:
        owner, owner_id, pointer, parent_id, marker_track, sibling_index = pending.pop(0)
        reference = source_reference(owner, owner_id, pointer, follow)
        target = follow(owner, pointer)
        if target is None:
            unsupported.append({"parentId": parent_id, "siblingIndex": sibling_index,
                                "reference": reference, "reason": "timeline track not in this package"})
            continue
        track_record, track_id = target
        identity = _identity_key(track_record, track_id)
        if identity in seen:
            unsupported.append({"reference": reference, "source": source_identity(track_record, track_id),
                                "reason": "duplicate or cyclic timeline track edge"})
            continue
        seen.add(identity)
        track_tree = track_record.tree(track_id)
        class_name = _script_class(track_record, track_id, follow)
        parent_pointer = track_tree.get("m_Parent") or {}
        child_pointers = track_tree.get("m_Children") or []
        track = {"id": len(document["tracks"]), "parentId": parent_id,
                 "source": source_identity(track_record, track_id),
                 "siblingIndex": sibling_index, "reference": reference,
                 "parentReference": source_reference(track_record, track_id, parent_pointer, follow),
                 "scriptReference": source_reference(track_record, track_id, track_tree.get("m_Script"), follow),
                 "childReferences": [source_reference(track_record, track_id, p, follow) for p in child_pointers],
                 "animationReferences": {},
                 "name": str(track_tree.get("m_Name", "")), "class": class_name,
                 "markerTrack": marker_track,
                 "muted": bool(track_tree.get("m_Muted", 0)),
                 "locked": bool(track_tree.get("m_Locked", 0)),
                 "target": None, "clips": [], "markers": []}
        expected = (document["source"] if parent_id is None else document["tracks"][parent_id]["source"])
        if track["parentReference"]["target"] != expected:
            unsupported.append({"track": track["name"], "trackId": track["id"],
                                "reference": track["parentReference"],
                                "reason": "timeline parent/child PPtrs disagree"})
        field, targets = TRACK_TARGETS.get(class_name, (None, None))
        if field is not None:
            raw = track_tree.get(field)
            track["target"] = targets.get(raw, raw)
            track["targetValue"] = raw
        if "_scale" in track_tree:
            track["scale"] = float(track_tree["_scale"])
        if class_name not in MODELLED_TRACKS:
            unsupported.append({"track": track["name"], "trackClass": class_name,
                                "reason": UNMODELLED_TRACK})
        for field in ("m_AnimClip", "m_Curves"):
            animation_pointer = track_tree.get(field) or {}
            animation_reference = source_reference(track_record, track_id, animation_pointer, follow)
            track["animationReferences"][field] = animation_reference
            if animation_pointer.get("m_PathID", 0):
                unsupported.append({"trackId": track["id"], "field": field,
                                    "reference": animation_reference,
                                    "reason": "timeline track animation asset not modelled"})
        for order, pointer in enumerate((track_tree.get("m_Markers") or {}).get("m_Objects") or []):
            marker_target = follow(track_record, pointer)
            body = {"sourceOrder": order, "reference": source_reference(track_record, track_id, pointer, follow)}
            if marker_target:
                marker_record, marker_id = marker_target
                body.update({"source": source_identity(*marker_target),
                             "class": _script_class(marker_record, marker_id, follow),
                             "time": marker_record.tree(marker_id).get("m_Time")})
            track["markers"].append(body)
            unsupported.append({"trackId": track["id"], "marker": body,
                                "reason": "timeline marker behavior not modelled"})
        for order, clip_tree in enumerate(track_tree.get("m_Clips") or []):
            clip_pointer = (clip_tree or {}).get("m_Asset") or {}
            clip_reference = source_reference(track_record, track_id, clip_pointer, follow)
            clip_target = follow(track_record, clip_pointer)
            if clip_target is None:
                unsupported.append({"track": track["name"], "sourceOrder": order,
                                    "reference": clip_reference,
                                    "reason": "timeline clip asset not in this package"})
                continue
            clip_record, clip_id = clip_target
            clip_class = _script_class(clip_record, clip_id, follow)
            clip_tree_asset = clip_record.tree(clip_id)
            body, gap = clip_asset(clip_class, clip_tree_asset)
            if gap is not None:
                unsupported.append({"track": track["name"], "clipClass": clip_class,
                                    "reference": clip_reference, "reason": gap})
            clip = _clip(clip_tree, clip_class, body)
            clip["source"] = source_identity(clip_record, clip_id)
            clip["reference"] = clip_reference
            clip["scriptReference"] = source_reference(clip_record, clip_id, clip_tree_asset.get("m_Script"), follow)
            clip["sourceOrder"] = order
            track["clips"].append(clip)
        # GetClips() orders by start time. Equal-start clips retain source order.
        track["clips"].sort(key=lambda clip: clip["start"])
        document["tracks"].append(track)
        for order, child in enumerate(child_pointers):
            pending.append((track_record, track_id, child, track["id"], False, order))
    document["summary"] = {"tracks": len(document["tracks"]),
                           "clips": sum(len(t["clips"]) for t in document["tracks"])}
    return document, unsupported
