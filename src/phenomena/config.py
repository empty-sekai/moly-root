"""Environment configuration and post-processing profile of one phenomenon.

Two authored assets decide how a phenomenon looks apart from its particles.

The **configuration** asset holds five parameter groups plus three scalars.  It
is flattened here — group by group, field by field — because a consumer needs the
whole set: the light group drives the scene light and the character shading, the
cloud group drives the moving cloud shadow, the wind group drives vertex
animation on foliage, and the character and fixture groups are two *independent*
outline setups that happen to have the same field names.  Values are copied
exactly as stored, with the leading underscores of the source field names
dropped; nothing is rescaled, rounded, or given a default.

The **profile** asset is a list of post-processing volume components.  Each
component parameter carries an *override state* next to its value: with the
override off, the profile does not set that parameter at all and the inherited
value stays live, so dropping the flag would turn "leave this alone" into "force
this value".  Both are preserved.

A field the asset carries but this module does not model, and a field this module
expects but the asset does not carry, are both reported rather than passed over —
the two cases mean opposite things and only one of them is a gap in the export.
"""

# Every MonoBehaviour carries these; they say nothing about the phenomenon.
BOILERPLATE = ("m_GameObject", "m_Enabled", "m_Script", "m_Name")

_COLOR = "color"
_SCALAR = "scalar"
_VECTOR2 = "vector2"
_TEXTURE = "texture"
_HOME_ANGLE = "homeAngle"

# Scalars that sit directly on the configuration asset.
TOP_FIELDS = (
    ("description", "description", _SCALAR),
    ("rendererType", "rendererType", _SCALAR),
    ("_gridColorKey", "gridColorKey", _SCALAR),
    ("_emissionType", "emissionType", _SCALAR),
)

# Scene light and character shading: the phenomenon's own light colours, the sun
# angle, the indoor sun-angle override, and the two drop-shadow colours.
LIGHT_FIELDS = (
    ("characterDirectionalLightColor", "characterDirectionalLightColor", _COLOR),
    ("characterShadeSkinColor", "characterShadeSkinColor", _COLOR),
    ("characterBodyShadeColor", "characterBodyShadeColor", _COLOR),
    ("phenomenaDirectionalLightColor", "phenomenaDirectionalLightColor", _COLOR),
    ("phenomenaShadeColor", "phenomenaShadeColor", _COLOR),
    ("angleXZ", "angleXZ", _SCALAR),
    ("angleY", "angleY", _SCALAR),
    ("_homeSiteLightAngleData", "homeSiteLightAngle", _HOME_ANGLE),
    ("dropShadowColor1", "dropShadowColor1", _COLOR),
    ("dropShadowColor2", "dropShadowColor2", _COLOR),
    ("dropShadowEdgeSmoothness", "dropShadowEdgeSmoothness", _SCALAR),
)

# Scrolling cloud shadow projected over the ground.
CLOUD_FIELDS = (
    ("cloudShadowTexture", "cloudShadowTexture", _TEXTURE),
    ("cloudShadowOpacity", "cloudShadowOpacity", _SCALAR),
    ("cloudShadowTextureSize", "cloudShadowTextureSize", _SCALAR),
    ("cloudScrollVelocity", "cloudScrollVelocity", _VECTOR2),
    ("cloudScrollSpeed", "cloudScrollSpeed", _SCALAR),
)

# Character outline.  Characters and fixtures have separate outline setups.
CHARACTER_FIELDS = (
    ("outlineWidth", "outlineWidth", _SCALAR),
    ("outlineDepthOffset", "outlineDepthOffset", _SCALAR),
    ("outlineWidthMaxRate", "outlineWidthMaxRate", _SCALAR),
    ("outlineWidthMinRate", "outlineWidthMinRate", _SCALAR),
    ("outlineColor", "outlineColor", _COLOR),
)

FIXTURE_FIELDS = (
    ("_outlineWidth", "outlineWidth", _SCALAR),
    ("_outlineDepthOffset", "outlineDepthOffset", _SCALAR),
    ("_outlineWidthMaxRate", "outlineWidthMaxRate", _SCALAR),
    ("_outlineWidthMinRate", "outlineWidthMinRate", _SCALAR),
    ("_outlineColor", "outlineColor", _COLOR),
)

# Wind, which drives both a scrolling noise lookup and per-vertex animation.
WIND_FIELDS = (
    ("windSpeed", "windSpeed", _SCALAR),
    ("windColor", "windColor", _COLOR),
    ("vertexWaveAnimationAmount", "vertexWaveAnimationAmount", _SCALAR),
    ("vertexWaveExponent", "vertexWaveExponent", _SCALAR),
    ("vertexRandomAnimationAmount", "vertexRandomAnimationAmount", _SCALAR),
    ("vertexRandomAnimationSpeed", "vertexRandomAnimationSpeed", _SCALAR),
    ("windWaveDistortionAmount", "windWaveDistortionAmount", _SCALAR),
    ("windWaveDistortionFrequency", "windWaveDistortionFrequency", _SCALAR),
    ("windNoiseTexture", "windNoiseTexture", _TEXTURE),
)

GROUPS = (
    ("lightSettings", "light", LIGHT_FIELDS),
    ("cloudSettings", "cloud", CLOUD_FIELDS),
    ("globalCharacterSettings", "character", CHARACTER_FIELDS),
    ("globalFixtureSettings", "fixture", FIXTURE_FIELDS),
    ("globalWindSettingsData", "wind", WIND_FIELDS),
)

UNMODELLED_FIELD = "configuration field not modelled"
ABSENT_FIELD = "configuration field absent from the asset"
UNMODELLED_PARAMETER = "parameter value shape not modelled"
NOT_A_PARAMETER = "component field is not a volume parameter"

OVERRIDE_STATE = "m_OverrideState"
PARAMETER_VALUE = "m_Value"


def _color(node):
    return [node.get(component, 0.0) for component in "rgba"]


def _vector(node):
    return [node.get(axis) for axis in "xyzw" if axis in node]


def _home_angle(node):
    """The indoor sun-angle override: a flag plus the two angles it substitutes."""
    return {"active": bool(node.get("_isActive", 0)),
            "angleXZ": node.get("_angleXZ"),
            "angleY": node.get("_angleY")}


def _value(kind, raw, texture_ref):
    if kind == _COLOR:
        return _color(raw or {})
    if kind == _VECTOR2:
        return _vector(raw or {})
    if kind == _HOME_ANGLE:
        return _home_angle(raw or {})
    if kind == _TEXTURE:
        return texture_ref(raw) if texture_ref else None
    return raw


def flatten_config(tree, texture_ref=None):
    """Flatten one configuration asset.

    *texture_ref* turns a texture pointer into the reference a consumer follows;
    without it texture fields are present but unresolved.  Returns
    ``(document, unsupported)``.
    """
    document, unsupported = {}, []
    seen = set(BOILERPLATE)
    for source, name, kind in TOP_FIELDS:
        seen.add(source)
        if source not in tree:
            unsupported.append({"group": None, "field": source, "reason": ABSENT_FIELD})
            continue
        document[name] = _value(kind, tree[source], texture_ref)
    for group, out_name, fields in GROUPS:
        seen.add(group)
        if group not in tree:
            unsupported.append({"group": None, "field": group, "reason": ABSENT_FIELD})
            continue
        source_group = tree[group] or {}
        values = {}
        for source, name, kind in fields:
            if source not in source_group:
                unsupported.append({"group": group, "field": source,
                                    "reason": ABSENT_FIELD})
                continue
            values[name] = _value(kind, source_group[source], texture_ref)
        known = {source for source, _, _ in fields}
        for field in source_group:
            if field not in known:
                unsupported.append({"group": group, "field": field,
                                    "reason": UNMODELLED_FIELD})
        document[out_name] = values
    for field in tree:
        if field not in seen:
            unsupported.append({"group": None, "field": field,
                                "reason": UNMODELLED_FIELD})
    return document, unsupported


def _parameter(raw, texture_ref):
    """One volume parameter: its override state, its value, and any extra keys.

    Returns ``(entry, decoded)``; ``decoded`` is false when the value shape is
    not one this module reads, in which case the raw value is kept as it is.
    """
    entry = {"overrideState": bool(raw.get(OVERRIDE_STATE, 0))}
    value = raw.get(PARAMETER_VALUE)
    decoded = True
    if isinstance(value, dict):
        keys = set(value)
        if {"r", "g", "b", "a"} <= keys:
            value = _color(value)
        elif keys and keys <= {"x", "y", "z", "w"}:
            value = _vector(value)
        elif "m_PathID" in keys:
            value = texture_ref(value) if texture_ref else None
        else:
            decoded = False
    elif isinstance(value, (list, tuple)):
        decoded = False
    entry["value"] = value
    for key, extra in raw.items():
        if key not in (OVERRIDE_STATE, PARAMETER_VALUE):
            entry[key] = extra
    return entry, decoded


def volume_component(name, class_name, tree, texture_ref=None):
    """One post-processing volume component of a profile."""
    document = {"name": name, "class": class_name,
                "active": bool(tree.get("active", 0)), "parameters": {}}
    unsupported = []
    for field, raw in tree.items():
        if field in BOILERPLATE or field == "active":
            continue
        if not (isinstance(raw, dict) and OVERRIDE_STATE in raw):
            unsupported.append({"component": name, "field": field,
                                "reason": NOT_A_PARAMETER})
            document.setdefault("fields", {})[field] = raw
            continue
        entry, decoded = _parameter(raw, texture_ref)
        if not decoded:
            unsupported.append({"component": name, "parameter": field,
                                "reason": UNMODELLED_PARAMETER})
        document["parameters"][field] = entry
    return document, unsupported


def volume_profile(components, texture_ref=None):
    """Flatten a profile from its ``(name, class name, typetree)`` components.

    Component order is the profile's own order, which is the order the volume
    stack applies them in.  Returns ``(document, unsupported)``.
    """
    document = {"components": []}
    unsupported = []
    for name, class_name, tree in components:
        component, gaps = volume_component(name, class_name, tree, texture_ref)
        document["components"].append(component)
        unsupported.extend(gaps)
    return document, unsupported
