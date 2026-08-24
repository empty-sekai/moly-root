"""The one phenomenon that is driven by a timeline instead of by constants.

Thunder is not a steady state: the sky flashes, the light flashes with it, and both
fade on authored curves.  That schedule lives in a *timeline* asset — a list of
tracks, each holding clips placed on a common time axis — and one phenomenon in
these packages has one.

What a consumer needs in order to replay it is: which value each track drives, when
each clip starts and how long it lasts, and the curve or gradient inside the clip.
All three are exported here.  A track says what it drives with a small enumeration,
so the target is a name rather than a guess from the track's editor label; the label
is kept alongside it because it is what a person reads.

Clip time is exported in two forms because the two are not interchangeable: ``start``
and ``duration`` are seconds on the timeline, whereas a clip's own curve runs on a
normalized 0..1 axis over its duration, scaled by ``timeScale`` and offset by
``clipIn``.  A clip kind this module does not model keeps its class name so the gap
is visible rather than looking like an empty track.
"""
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
        entry = {"time": round(float(key.get("time", 0.0)), 6),
                 "value": round(float(key.get("value", 0.0)), 6)}
        for name in ("inSlope", "outSlope"):
            slope = float(key.get(name, 0.0))
            entry[name] = (None if slope in (float("inf"), float("-inf"))
                           else round(slope, 6))
        entry["weightedMode"] = key.get("weightedMode")
        entry["inWeight"] = round(float(key.get("inWeight", 0.0)), 6)
        entry["outWeight"] = round(float(key.get("outWeight", 0.0)), 6)
        out.append(entry)
    return out


def _curve(curve):
    return {"keys": _keys(curve),
            "preInfinity": (curve or {}).get("m_PreInfinity"),
            "postInfinity": (curve or {}).get("m_PostInfinity")}


def clip_asset(class_name, tree):
    """The authored payload of one clip, by clip class.  Returns ``(body, gap)``."""
    if class_name == "SiteEnvironmentColorClip":
        return {"gradient": min_max_gradient({"minMaxState": 1,
                                              "maxGradient": tree.get("_gradient")
                                              or {}})["gradient"]}, None
    if class_name == "SiteEnvironmentValueClip":
        return {"scale": round(float(tree.get("_scale", 0.0)), 6),
                "curve": _curve(tree.get("_curve"))}, None
    if class_name == "ValueNoiseClip":
        return {"intensity": round(float(tree.get("_noiseIntensity", 0.0)), 6),
                "frequency": round(float(tree.get("_noiseFrequency", 0.0)), 6),
                "intensityCurve": _curve(tree.get("_noiseIntensityCurve"))}, None
    return None, UNMODELLED_CLIP


def _clip(tree, class_name, body):
    return {"start": round(float(tree.get("m_Start", 0.0)), 6),
            "duration": round(float(tree.get("m_Duration", 0.0)), 6),
            "clipIn": round(float(tree.get("m_ClipIn", 0.0)), 6),
            "timeScale": round(float(tree.get("m_TimeScale", 1.0)), 6),
            "label": str(tree.get("m_DisplayName", "")),
            "class": class_name,
            "blendInDuration": round(float(tree.get("m_BlendInDuration", 0.0)), 6),
            "blendOutDuration": round(float(tree.get("m_BlendOutDuration", 0.0)), 6),
            "easeInDuration": round(float(tree.get("m_EaseInDuration", 0.0)), 6),
            "easeOutDuration": round(float(tree.get("m_EaseOutDuration", 0.0)), 6),
            "preExtrapolation": tree.get("m_PreExtrapolationMode"),
            "postExtrapolation": tree.get("m_PostExtrapolationMode"),
            "asset": body}


def decode_timeline(path_id, record, follow):
    """One timeline asset: its tracks, their clips, and what each track drives.

    *follow* resolves a pointer to ``(record, path id)`` or ``None``.  Returns
    ``(document, unsupported)``.
    """
    tree = record.tree(path_id)
    document = {"name": str(tree.get("m_Name", "")),
                "duration": round(float(tree.get("m_FixedDuration", 0.0)), 6),
                "durationMode": tree.get("m_DurationMode"),
                "frameRate": (tree.get("m_EditorSettings") or {}).get("m_Framerate"),
                "tracks": []}
    unsupported = []
    pending = [(pointer, None) for pointer in tree.get("m_Tracks") or []]
    marker = tree.get("m_MarkerTrack") or {}
    if marker.get("m_PathID", 0):
        pending.append((marker, "markerTrack"))
    while pending:
        pointer, role = pending.pop(0)
        target = follow(pointer)
        if target is None:
            unsupported.append({"reason": "timeline track not in this package"})
            continue
        track_record, track_id = target
        track_tree = track_record.tree(track_id)
        class_name = track_record.script_of(track_id)
        track = {"name": str(track_tree.get("m_Name", "")), "class": class_name,
                 "role": role, "muted": bool(track_tree.get("m_Muted", 0)),
                 "locked": bool(track_tree.get("m_Locked", 0)),
                 "target": None, "clips": []}
        field, targets = TRACK_TARGETS.get(class_name, (None, None))
        if field is not None:
            raw = track_tree.get(field)
            track["target"] = targets.get(raw, raw)
            track["targetValue"] = raw
        if "_scale" in track_tree:
            track["scale"] = round(float(track_tree.get("_scale", 0.0)), 6)
        if class_name not in MODELLED_TRACKS:
            unsupported.append({"track": track["name"], "trackClass": class_name,
                                "reason": UNMODELLED_TRACK})
        for clip_tree in track_tree.get("m_Clips") or []:
            clip_target = follow((clip_tree or {}).get("m_Asset") or {})
            if clip_target is None:
                unsupported.append({"track": track["name"],
                                    "reason": "timeline clip asset not in this "
                                              "package"})
                continue
            clip_record, clip_id = clip_target
            clip_class = clip_record.script_of(clip_id)
            body, gap = clip_asset(clip_class, clip_record.tree(clip_id))
            if gap is not None:
                unsupported.append({"track": track["name"], "clipClass": clip_class,
                                    "reason": gap})
            track["clips"].append(_clip(clip_tree, clip_class, body))
        track["clips"].sort(key=lambda clip: (clip["start"], clip["label"]))
        document["tracks"].append(track)
        for child in track_tree.get("m_Children") or []:
            pending.append((child, "child of %s" % track["name"]))
    document["summary"] = {"tracks": len(document["tracks"]),
                           "clips": sum(len(t["clips"])
                                        for t in document["tracks"])}
    return document, unsupported
