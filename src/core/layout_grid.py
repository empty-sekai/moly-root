"""The layout editor's placement grid, from the player data's Resources.

The grid manager loads one ScriptableObject, the grid settings, by its load
name; the settings hold the line sizes, the layout offsets and a library of
materials, one per grid part (the solid line, the dotted line, the tile face and
the floating, stripe and light-pole parts).  The grid colours come from a second
ScriptableObject, the colour profile table: each row is a profile keyed by the
string a phenomenon's environment configuration names (``gridColorKey``).

This export writes both as data, with each material's shader, its serialized
properties and the render state of the shader's pass, so a consumer can draw the
grid with the source's values and shader behaviour:

* ``settings`` -- the grid settings' serialized fields, under their names;
* ``materials`` -- per library slot, the material (``null`` for an empty slot):
  its name, shader, custom render queue, keywords and properties;
* ``shaders`` -- per shader used by a material: its passes' render state (a
  state bound to a material property keeps the property name next to the
  shader's fixed value) and its compiled GL program, written as a file;
* ``colorProfiles`` -- the profile table's rows in order, each profile's colour
  fields under their serialized names.

Display names of the profile rows are authored text in another language and are
left out; the keys are what the runtime looks profiles up by.
"""
import hashlib
from pathlib import Path

from core.jsonio import write_json
from core.player_resources import open_player_data, resource_targets

SETTINGS = "Data/MysekaiGridSettings"
SETTINGS_CLASS = "MysekaiGridSettings"
PROFILES = "Data/GridColorProfileTable"
PROFILES_CLASS = "GridColorProfileTable"
LIBRARY_FIELD = "<MaterialLibrary>k__BackingField"
PROFILE_FIELDS = (
    "GridLine", "DottedGridLine", "EmptyTile", "PlacedFixtureTile",
    "MovingFixtureTileSafe", "MovingFixtureTileAlert", "HidedFixtureTile",
    "FloatingHighlightGridSafe", "FloatingHighlightGridAlert",
    "LightPoleBodyMaterialSafe", "LightPoleBodyMaterialAlert",
    "LightPoleLineMaterialSafe", "LightPoleLineMaterialAlert",
    "MotionAreaTileMaterial", "MotionDisableAreaTileMaterial",
    "MotionConflictAreaTileMaterial",
)
# Pass state fields read from the shader's parsed form.
STATE_FIELDS = ("zTest", "zWrite", "culling", "offsetFactor", "offsetUnits",
                "alphaToMask", "zClip")
BLEND_FIELDS = ("srcBlend", "destBlend", "srcBlendAlpha", "destBlendAlpha",
                "blendOp", "blendOpAlpha", "colMask")
GLSL_PLATFORM = 9

SEMANTICS = {
    "settings": ("the grid settings' serialized fields, under their serialized names "
                 "with the auto-property wrapper removed; 32-bit floats widened"),
    "materials": ("per material-library slot of the settings: the material, or null "
                  "for an empty slot. `floats`, `colors` and `textures` are the "
                  "material's saved properties as serialized; `renderQueue` is its "
                  "custom render queue (-1: the shader's)"),
    "shaders": ("per shader a material uses: every pass's render state. A state "
                "entry is {value, property}: `value` is the shader's fixed value and "
                "`property` names the material float that overrides it (null when "
                "the state is fixed). `programs` are the pass's GL programs, one per "
                "keyword variant (vertex and fragment in one text), written under "
                "programs/ by content hash"),
    "colorProfiles": ("the colour profile table's rows in order: `key` is what a "
                      "phenomenon configuration's gridColorKey names; each profile "
                      "field holds `color` (and `color2` for two-colour fields) as "
                      "serialized RGBA floats"),
}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        raise ValueError("unexpected byte payload in a grid object")
    return value


def _scriptable(store, package, load_name, class_name):
    rows = resource_targets(store, package, load_name)
    hits = [row["target"] for row in rows if row["target"] is not None
            and row["target"][0].kinds[row["target"][1]] == "MonoBehaviour"
            and row["target"][0].script_of(row["target"][1]) == class_name]
    if len(hits) != 1:
        raise ValueError(f"{load_name}: expected one {class_name}, found {len(hits)}")
    record, path_id = hits[0]
    return record, path_id, record.tree(path_id)


def _is_pointer(value):
    return isinstance(value, dict) and set(value) == {"m_FileID", "m_PathID"}


def _field_name(name):
    if name.startswith("<") and name.endswith(">k__BackingField"):
        return name[1:-len(">k__BackingField")]
    return name


def _pairs(entries):
    out = {}
    for entry in entries or []:
        key, value = entry[0], entry[1]
        if key in out:
            raise ValueError(f"saved property {key} is serialized twice")
        out[str(key)] = value
    return out


def _state(entry):
    if not isinstance(entry, dict) or "val" not in entry:
        raise ValueError(f"unexpected pass state entry {entry!r}")
    name = str(entry.get("name") or "")
    return {"value": float(entry["val"]),
            "property": None if name in ("", "<noninit>") else name}


def _shader(store, record, pointer, programs):
    from shaders import blob, objects
    target = store.follow(record, pointer)
    if target is None:
        raise ValueError("a grid material names a shader outside the player data")
    srecord, spath = target
    tree = srecord.tree(spath)
    parsed = objects.parsed_form(tree)
    passes = []
    for sub_index, sub in enumerate(parsed.get("m_SubShaders") or []):
        for pass_index, entry in enumerate(sub.get("m_Passes") or []):
            state = entry.get("m_State") or {}
            blend = state.get("rtBlend0") or {}
            passes.append({
                "subshader": sub_index, "pass": pass_index,
                "blend": {field: _state(blend[field]) for field in BLEND_FIELDS},
                "separateBlend": bool(state.get("rtSeparateBlend")),
                **{field: _state(state[field]) for field in STATE_FIELDS},
                "programs": [],
            })
    segments = dict(objects.platform_segments(tree))
    if GLSL_PLATFORM not in segments:
        raise ValueError(f"shader {objects.shader_name(tree)} has no GL program")
    parsed_blob = blob.parse_segments(segments[GLSL_PLATFORM], GLSL_PLATFORM)
    for item in objects.subprograms(tree):
        if item["platform"] != GLSL_PLATFORM:
            continue
        record_row = parsed_blob["records"][item["record"]]
        code = bytes(record_row["code"])
        sha = _sha(code)
        programs[sha] = code
        row = next(p for p in passes
                   if (p["subshader"], p["pass"]) == (item["subshader"], item["pass"]))
        row["programs"].append({"file": f"programs/{sha}.glsl", "sha256": sha,
                                "bytes": len(code),
                                "keywords": list(record_row.get("keywords") or [])})
    return objects.shader_name(tree), {
        "serializedFile": srecord.archive, "pathId": str(spath),
        "properties": [str(p.get("m_Name")) for p in
                       ((parsed.get("m_PropInfo") or {}).get("m_Props") or [])],
        "passes": passes,
    }


def _material(store, record, pointer, shaders, programs):
    if int(pointer.get("m_PathID") or 0) == 0:
        return None
    target = store.follow(record, pointer)
    if target is None:
        raise ValueError(f"grid material pointer {pointer} does not resolve")
    mrecord, mpath = target
    tree = mrecord.tree(mpath)
    name, document = _shader(store, mrecord, tree["m_Shader"], programs)
    shaders.setdefault(name, document)
    saved = tree.get("m_SavedProperties") or {}
    textures = {}
    for key, env in _pairs(saved.get("m_TexEnvs")).items():
        texture = env.get("m_Texture") or {}
        if int(texture.get("m_PathID") or 0) != 0:
            raise ValueError(f"grid material {tree.get('m_Name')} binds a texture to "
                             f"{key}; textures are not exported by this domain")
        textures[key] = {"scale": _plain(env.get("m_Scale")),
                         "offset": _plain(env.get("m_Offset")), "texture": None}
    return {"name": str(tree.get("m_Name")), "shader": name,
            "serializedFile": mrecord.archive, "pathId": str(mpath),
            "renderQueue": int(tree.get("m_CustomRenderQueue", -1)),
            "keywords": sorted(str(k) for k in (tree.get("m_ValidKeywords") or [])),
            "floats": {k: float(v) for k, v in _pairs(saved.get("m_Floats")).items()},
            "colors": {k: _plain(v) for k, v in _pairs(saved.get("m_Colors")).items()},
            "textures": textures}


def export_layout_grid(player_data, types, out_dir):
    """Write ``grid.json`` (and its programs) under *out_dir*; returns a summary."""
    out_dir = Path(out_dir)
    store, package, version = open_player_data(player_data, types)
    record, path_id, tree = _scriptable(store, package, SETTINGS, SETTINGS_CLASS)
    library = tree.get(LIBRARY_FIELD)
    if not isinstance(library, dict) or not library:
        raise ValueError(f"{SETTINGS}: no serialized material library")
    settings = {}
    for key, value in tree.items():
        if key in ("m_GameObject", "m_Enabled", "m_Script", "m_Name", LIBRARY_FIELD):
            continue
        settings[_field_name(key)] = _plain(value)
    shaders, programs, materials = {}, {}, {}
    for slot, pointer in library.items():
        if not _is_pointer(pointer):
            continue
        materials[slot] = _material(store, record, pointer, shaders, programs)
    precord, ppath, ptree = _scriptable(store, package, PROFILES, PROFILES_CLASS)
    profiles = []
    for element in ptree.get("_elements") or []:
        target = store.follow(precord, element.get("_profile") or {})
        if target is None:
            raise ValueError(f"colour profile {element.get('_key')} does not resolve")
        profile = target[0].tree(target[1])
        missing = [field for field in PROFILE_FIELDS if field not in profile]
        if missing:
            raise ValueError(f"colour profile {element.get('_key')} lacks {missing}")
        profiles.append({"key": str(element.get("_key")),
                         "pathId": str(target[1]),
                         "fields": {field: _plain(profile[field]) for field in PROFILE_FIELDS}})
    for sha, code in programs.items():
        path = out_dir / "programs" / f"{sha}.glsl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(code)
    document = {
        "version": 1, "semantics": SEMANTICS,
        "source": {"unityVersion": version,
                   "settings": {"loadName": SETTINGS, "serializedFile": record.archive,
                                "pathId": str(path_id)},
                   "profiles": {"loadName": PROFILES, "serializedFile": precord.archive,
                                "pathId": str(ppath)},
                   "decode": ("typetree generated from the build's assembly stubs; "
                              "every serialized byte consumed")},
        "settings": settings,
        "materials": materials,
        "shaders": shaders,
        "colorProfiles": profiles,
    }
    write_json(out_dir / "grid.json", document)
    return {"path": str(out_dir / "grid.json"), "materials": len(materials),
            "emptySlots": sum(1 for m in materials.values() if m is None),
            "shaders": len(shaders), "programs": len(programs),
            "profiles": len(profiles)}
