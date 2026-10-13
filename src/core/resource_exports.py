"""Tables and prefabs the player build loads from its Resources, as data.

Three exports share the player-data reader in :mod:`core.player_resources`:

* :func:`export_effect_resources` -- the effect manager's type table.  The effect
  manager builds its pools from one ScriptableObject it loads from Resources; each
  row names an effect type, the bundle and prefab to instantiate, and how many
  copies to pool.  The table is written as it is serialized, with the source
  object named, each type's declared name attached when the caller supplies the
  build's declarations, and -- when a bundle directory is given -- whether each
  row's bundle is on disk, whether it holds the named prefab, and which effect
  behaviour that prefab's root carries.
* :func:`export_resource_prefab` -- one prefab loaded by name, walked by the site
  package reader over a view of exactly the objects it reaches (see
  :func:`core.player_resources.prefab_view`), so its particle rows, materials,
  textures and component fields have the site packages' shape.
* :func:`export_resource_programs` -- the compiled programs of every shader the
  materials of one Resources prefab use, through the shared program library
  (:class:`shaders.programs.ProgramLibrary`), next to that prefab's export.
* :func:`export_drop_item_shade` -- the drop-item shading settings of the MySekai
  graphics configuration (a ScriptableObject the graphics configuration loader
  loads from Resources), with the global shader property each value is written
  to.
* :func:`export_stencil_shadow` -- from the same configuration, the character
  stencil-shadow colour and the per-attribute material rendering settings (render
  queue and the pass state the material builder writes into a material).
* :func:`export_npc_dither` and :func:`export_site_object_dither` -- from the
  same configuration, the camera-dither distances of the NPC view and of the
  site objects and tree fixtures.
"""
import hashlib
from pathlib import Path

from core.assets.packages import PackageStore
from core.jsonio import write_json
from core.player_resources import open_player_data, prefab_view, resource_targets

EFFECT_RESOURCE_DATA = "Mysekai/Effects/EffectResourceData"
EFFECT_RESOURCE_CLASS = "EffectResourceData"

EFFECT_SEMANTICS = {
    "source": ("the ScriptableObject the effect manager loads with Resources.Load at "
               "the load name above (the manager's EFFECT_RESOURCE_DATA_PATH "
               "constant); its serialized list is exported verbatim, in order"),
    "rows": ("for each row the manager loads the prefab `prefabName` from the bundle "
             "`assetBundleName` (from Resources by `prefabName` when the bundle name "
             "is empty), instantiates one template, clones it `poolSize` times into "
             "that type's pool under one `EffectRoot` object, and destroys the "
             "template. A type registered twice stops the whole table at that row "
             "with an error; `duplicates` lists any"),
    "typeNames": ("the effect type's declared constant name, read from the build's "
                  "declarations when supplied; the serialized value is the number"),
    "poolKey": ("not in this table. Every Emit overload defaults its pool key to "
                "\"DefaultEffect\" (the MysekaiConstants.DEFAULT_EFFECT_POOL_KEY "
                "constant); the key only groups the pools an emit drew from so that "
                "Stop(poolKey) can stop them together"),
    "bundles": ("`bundle` is checked against the supplied bundle directory: whether "
                "the package file is there, whether its container holds "
                "`<prefabName>.prefab`, and the MonoBehaviour classes on that "
                "prefab's root. Without a directory these stay null"),
}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _scriptable(store, package, load_name, class_name):
    """``(rows, record, path id)`` of the one MonoBehaviour *load_name* names."""
    rows = resource_targets(store, package, load_name)
    targets = [row for row in rows if row["target"] is not None
               and row["target"][0].kinds[row["target"][1]] == "MonoBehaviour"
               and row["target"][0].script_of(row["target"][1]) == class_name]
    if len(targets) != 1:
        raise ValueError(f"{load_name}: expected one {class_name} resource, found "
                         f"{len(targets)} of {len(rows)} entries")
    record, path_id = targets[0]["target"]
    return rows, record, path_id


def enum_constants(declarations, namespace, name):
    """``{value: constant name}`` of one enum in the build's declaration dump."""
    head = f"public enum {name} "
    current, inside, values = None, False, {}
    found = 0
    with open(declarations, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("// Namespace: "):
                current = line[len("// Namespace: "):].strip()
                continue
            if not inside:
                if line.startswith(head) and current == namespace:
                    inside, found = True, found + 1
                continue
            stripped = line.strip()
            if stripped == "}":
                inside = False
                continue
            prefix = f"public const {name} "
            if stripped.startswith(prefix) and stripped.endswith(";"):
                constant, value = stripped[len(prefix):-1].split(" = ")
                values[int(value)] = constant
    if found != 1:
        raise ValueError(f"enum {namespace}.{name}: {found} declarations found")
    return values


def _bundle_check(bundle_root, bundle, prefab):
    name = bundle.replace("/", "__")
    path = Path(bundle_root) / name
    if not path.is_file():
        return {"package": name, "onDisk": False, "prefab": None, "behaviours": None}
    store = PackageStore([str(path)])
    package = store.package(name)
    matches = [(record, path_id) for asset, record, path_id in package.contents
               if asset == f"{prefab}.prefab"]
    if len(matches) != 1:
        return {"package": name, "onDisk": True, "bytes": path.stat().st_size,
                "sha256": _sha(path.read_bytes()), "prefab": None,
                "reason": f"{len(matches)} containers named {prefab}.prefab",
                "behaviours": None}
    record, game_object = matches[0]
    tree = record.tree(game_object)
    classes = []
    for entry in tree.get("m_Component") or []:
        pointer = entry.get("component", entry)
        target = store.follow(record, pointer)
        if target is not None and target[0].kinds[target[1]] == "MonoBehaviour":
            classes.append(target[0].script_of(target[1]))
    return {"package": name, "onDisk": True, "bytes": path.stat().st_size,
            "sha256": _sha(path.read_bytes()),
            "prefab": {"asset": f"{prefab}.prefab", "root": str(tree.get("m_Name", "")),
                       "pathId": str(game_object)},
            "behaviours": classes}


def export_effect_resources(player_data, types, out_path, declarations=None,
                            bundle_root=None):
    """Write the effect manager's type table; returns a summary."""
    from core.unity import configure_fallback_unity_version
    store, package, version = open_player_data(player_data, types)
    if bundle_root:
        # Bundles do not state their Unity version; they are read at the
        # configured fallback, which must be this build's own version.
        assumed = configure_fallback_unity_version(None)
        if assumed != version:
            raise ValueError(f"bundles would be read as Unity {assumed}, but the "
                             f"player data is {version}")
    rows, record, path_id = _scriptable(store, package, EFFECT_RESOURCE_DATA,
                                        EFFECT_RESOURCE_CLASS)
    obj = record.objects[path_id]
    tree = record.tree(path_id)           # the generated node must consume every byte
    names = (enum_constants(declarations, "Sekai.Mysekai", "EffectType")
             if declarations else None)
    resources, seen, duplicates = [], set(), []
    for index, row in enumerate(tree["resources"]):
        value = int(row["effectType"])
        if value in seen:
            duplicates.append({"index": index, "effectType": value})
        seen.add(value)
        entry = {"index": index, "effectType": value,
                 "effectTypeName": None if names is None else names.get(value),
                 "assetBundleName": row["assetBundleName"],
                 "prefabName": row["prefabName"], "poolSize": row["poolSize"],
                 "loadsFrom": "bundle" if row["assetBundleName"] else "resources"}
        if bundle_root and row["assetBundleName"]:
            entry["bundle"] = _bundle_check(bundle_root, row["assetBundleName"],
                                            row["prefabName"])
        else:
            entry["bundle"] = None
        resources.append(entry)
    undeclared = (None if names is None else
                  sorted(value for value in seen if value not in names))
    unlisted = (None if names is None else
                {str(value): name for value, name in sorted(names.items())
                 if value not in seen})
    raw = obj.get_raw_data()
    document = {
        "version": 1, "semantics": EFFECT_SEMANTICS,
        "source": {"loadName": EFFECT_RESOURCE_DATA,
                   "containerEntries": [row["loadName"] for row in rows],
                   "serializedFile": record.archive, "pathId": str(path_id),
                   "class": EFFECT_RESOURCE_CLASS, "name": tree.get("m_Name"),
                   "byteSize": int(obj.byte_size), "rawSha256": _sha(raw),
                   "unityVersion": version,
                   "decode": "typetree generated from the build's assembly stubs; "
                             "every serialized byte consumed"},
        "resources": resources,
        "duplicates": duplicates,
        "typesWithoutDeclaredName": undeclared,
        "declaredTypesNotInTable": unlisted,
    }
    write_json(out_path, document)
    return {"path": str(out_path), "rows": len(resources),
            "duplicates": len(duplicates),
            "bundlesChecked": sum(1 for row in resources if row["bundle"]),
            "bundlesOnDisk": sum(1 for row in resources
                                 if row["bundle"] and row["bundle"]["onDisk"]),
            "prefabsFound": sum(1 for row in resources
                                if row["bundle"] and row["bundle"].get("prefab"))}


PREFAB_SEMANTICS = {
    "view": ("the prefab is read out of the player's shared serialized files as a "
             "view: the objects reachable from its root through the pointers its "
             "objects hold, whole prefabs at a time, not following pointers out of "
             "the kinds listed under source.view.stopKinds. `objects` accounts for "
             "the view, not for the player file"),
    "roots": ("every prefab the view reaches is a root with a glTF scene of its own; "
              "the loaded prefab is the primary root and the default scene. A prefab "
              "reached through a serialized field (an effect prefab a behaviour "
              "instantiates, say) is another root"),
    "components": ("UI and behaviour components are exported with their serialized "
                   "fields verbatim, pointers named where they resolve; MonoBehaviour "
                   "fields are read with a typetree generated from the build's "
                   "assembly stubs, and every read consumed the object's bytes exactly"),
    "particles": "particle rows have the site package reader's shape",
    "nodeTable": ("every game object of every root with the path ids of the object and "
                  "of each of its components, so a serialized pointer (a behaviour's "
                  "field naming a Transform or a component by path id) can be joined "
                  "to a node path; paths are root-relative like `components[].node`"),
}


def node_table(view):
    """``[{root, node, gameObject, active, components}]`` for a view package."""
    from sites.geometry import Graph
    rows = []
    for record in view.files:
        graph = Graph(record)
        for root in graph.roots:
            paths = graph.node_paths(root)
            for transform in graph.subtree(root):
                game_object = graph.owner[transform]
                components = []
                for kind, path_id in graph.components(transform):
                    entry = {"type": kind, "pathId": str(path_id)}
                    if kind == "MonoBehaviour":
                        entry["class"] = record.script_of(path_id) or None
                    components.append(entry)
                rows.append({"root": graph.name(root), "node": paths.get(game_object, ""),
                             "archive": record.archive, "gameObject": str(game_object),
                             "active": bool(graph.game_object(transform).get("m_IsActive", 1)),
                             "components": components})
    return rows


def export_resource_prefab(player_data, types, load_name, out_dir, builtin_resources=()):
    """Extract one Resources prefab by its load name; returns the document.

    A pointer of the prefab's objects into a serialized file the player data
    as given does not hold fails the export and names that file: a container
    build needs its built-in resource files (*builtin_resources*) for the
    objects that point into them, and without them the geometry those objects
    carry would silently be missing.
    """
    from sites.scenes import PackageExtract
    store, package, version = open_player_data(player_data, types, builtin_resources)
    rows = resource_targets(store, package, load_name)
    targets = [row for row in rows if row["target"] is not None
               and row["target"][0].kinds[row["target"][1]] == "GameObject"]
    if len(targets) != 1:
        raise ValueError(f"{load_name}: expected one prefab entry, found "
                         f"{len(targets)} of {len(rows)} entries")
    record, game_object = targets[0]["target"]
    stem = str(record.tree(game_object).get("m_Name", ""))
    if not stem:
        raise ValueError(f"{load_name}: prefab root has no name")
    view, report = prefab_view(store, [(record, game_object)], f"resources:{load_name}")
    if report["unreadable"]:
        names = ", ".join(f"{row['class'] or row['type']} ({row['bytes']} B)"
                          for row in report["unreadable"])
        raise ValueError(f"{load_name}: objects whose fields could not be read: {names}")
    if report.get("unfollowedArchives"):
        names = ", ".join(f"{name} ({count} pointers)" for name, count in report["unfollowedArchives"].items())
        raise ValueError(f"{load_name}: pointers into serialized files the player data as given "
                         f"does not hold: {names}; a container build takes its built-in resource "
                         f"files with --builtin-resources")
    store.adopt(view)
    out = Path(out_dir) / stem
    document = PackageExtract(store, view.name, out, stem,
                              classification=("resource-prefab", stem)).run()
    primary = [root for root in document["roots"] if root["primary"]]
    if len(primary) != 1 or primary[0]["name"] != stem:
        raise ValueError(f"{load_name}: the loaded prefab is not the one primary root")
    document["inventory"]["dependencySource"] = (
        "player data: the view's objects live in the player's own serialized files, "
        "so there is no package dependency list")
    document["semantics"] = PREFAB_SEMANTICS
    document["nodeTable"] = node_table(view)
    document["source"] = {
        "loadName": load_name,
        "containerEntries": [row["loadName"] for row in rows],
        "serializedFile": record.archive, "pathId": str(game_object),
        "unityVersion": version,
        "view": {key: value for key, value in report.items()},
    }
    write_json(out / f"{stem}.json", document)
    document["file"] = str(out / f"{stem}.json")
    return document


PROGRAMS_SEMANTICS = {
    "layout": ("the program library's layout: <sha>.shader.json is the source shader "
               "document, <sha>.variants.json the runtime catalogue of its passes and "
               "variants, programs/ the compiled source programs and compiled/ their "
               "offline conversions; the same layout the fixture particle and site "
               "exports write under their shaders/"),
    "shaders": ("one row per shader the prefab's materials name, in the order first "
                "named: its name (the parsed form's), where the player data holds it "
                "(serialized file and path id), its library reference and the materials "
                "that use it; a shader the player data does not hold (a built-in "
                "resource) is listed with state not-in-player-data and no reference"),
}


def export_resource_programs(player_data, types, load_name, out_dir, shader_compiler=None):
    """Write the programs of the shaders one Resources prefab's materials use.

    The prefab is found as :func:`export_resource_prefab` finds it; its view's
    Material objects are read and each ``m_Shader`` pointer is followed in the
    player data (a shader is a stop kind of the view, so it is resolved from the
    material's full serialized file).  Every shader found goes through the
    program library into ``<out_dir>/<prefab>/shaders/``, and
    ``shaders/index.json`` names each one.  Returns the index document.
    """
    from shaders.bridge import Compiler
    from shaders.objects import shader_name
    from shaders.programs import ProgramLibrary
    store, package, version = open_player_data(player_data, types)
    rows = resource_targets(store, package, load_name)
    targets = [row for row in rows if row["target"] is not None
               and row["target"][0].kinds[row["target"][1]] == "GameObject"]
    if len(targets) != 1:
        raise ValueError(f"{load_name}: expected one prefab entry, found "
                         f"{len(targets)} of {len(rows)} entries")
    record, game_object = targets[0]["target"]
    stem = str(record.tree(game_object).get("m_Name", ""))
    if not stem:
        raise ValueError(f"{load_name}: prefab root has no name")
    view, report = prefab_view(store, [(record, game_object)], f"resources:{load_name}")
    if report["unreadable"]:
        names = ", ".join(f"{row['class'] or row['type']} ({row['bytes']} B)"
                          for row in report["unreadable"])
        raise ValueError(f"{load_name}: objects whose fields could not be read: {names}")
    directory = Path(out_dir) / stem / "shaders"
    compiler = Compiler(shader_compiler) if shader_compiler is not None else None
    library = ProgramLibrary(store, directory, compiler=compiler)
    shaders, order = {}, []
    for file in view.files:
        full = getattr(file, "full", file)
        for path_id, kind in file.kinds.items():
            if kind != "Material":
                continue
            material = full.tree(path_id)
            pointer = material.get("m_Shader") or {}
            use = {"material": str(material.get("m_Name", "")),
                   "serializedFile": full.archive, "pathId": str(path_id)}
            target = store.follow(full, pointer)
            if target is None:
                key = ("absent", pointer.get("m_FileID"), pointer.get("m_PathID"),
                       str(full.externals[pointer["m_FileID"] - 1]) if pointer.get("m_FileID") else "")
                row = shaders.get(key)
                if row is None:
                    row = shaders[key] = {"name": None, "state": "not-in-player-data",
                                          "pointer": {"fileId": pointer.get("m_FileID"),
                                                      "pathId": str(pointer.get("m_PathID")),
                                                      "external": key[3]},
                                          "materials": []}
                    order.append(key)
                row["materials"].append(use)
                continue
            source, shader_id = target
            key = ("shader", source.archive, shader_id)
            row = shaders.get(key)
            if row is None:
                reference = library.reference(source, shader_id)
                row = shaders[key] = {"name": shader_name(source.tree(shader_id)), "state": "exported",
                                      "serializedFile": source.archive, "pathId": str(shader_id),
                                      "reference": reference, "materials": []}
                order.append(key)
            row["materials"].append(use)
    document = {"loadName": load_name, "prefab": stem, "unityVersion": version,
                "compilerSupplied": compiler is not None,
                "compilerSha256": compiler.executable_sha256 if compiler is not None else None,
                "semantics": PROGRAMS_SEMANTICS,
                "shaders": [shaders[key] for key in order]}
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "index.json", document)
    document["file"] = str(directory / "index.json")
    return document


GRAPHICS_CONFIG = "Graphics/Data/MysekaiGraphicsConfig"
GRAPHICS_CONFIG_CLASS = "MysekaiGraphicsConfig"
DROP_ITEM_SHADE_FIELD = "_dropItemShadeSettings"
# ``(serialized field, property, global shader property)`` for each drop-item
# shading value.  The environment shader view writes each value as a global
# float on the id of the named property: the ids are the static fields of the
# drop-item shader-property class, each created from exactly this name by
# ``Shader.PropertyToID`` in that class's static constructor (read in the build's
# machine code, the name to field-offset pairing included; the edge pair's names
# drop the word "Normal" that their properties carry).
DROP_ITEM_SHADE_GLOBALS = (
    ("<PhenomenaLightIntensity>k__BackingField", "PhenomenaLightIntensity",
     "_MysekaiDropItemPhenomenaLightIntensity"),
    ("<NormalShadingIntensity>k__BackingField", "NormalShadingIntensity",
     "_MysekaiDropItemNormalShadingIntensity"),
    ("<NormalShadingEdgeThreshold>k__BackingField", "NormalShadingEdgeThreshold",
     "_MysekaiDropItemShadingEdgeThreshold"),
    ("<NormalShadingEdgeSmoothness>k__BackingField", "NormalShadingEdgeSmoothness",
     "_MysekaiDropItemShadingEdgeSmoothness"),
)
# The helper that writes them replaces a value whose magnitude is below this
# threshold with 0 before setting it (its default argument, passed explicitly
# by the environment shader view).
GLOBAL_ZERO_THRESHOLD = 0.001

DROP_SHADE_SEMANTICS = {
    "source": ("the ScriptableObject the graphics configuration loader loads with "
               "Resources.Load at the load name above; `settings` is its serialized "
               "field `source.field`, verbatim, under its serialized names"),
    "values": ("32-bit floats as serialized, written here widened to double"),
    "globals": ("one row per value: the property it backs, and the global shader "
                "property the environment shader view writes it to as a float. The "
                "writer replaces a value whose magnitude is below `zeroThreshold` "
                "with 0; `written` is the value after that rule"),
}


def export_drop_item_shade(player_data, types, out_path):
    """Write the drop-item shading settings of the graphics configuration."""
    store, package, version = open_player_data(player_data, types)
    rows, record, path_id = _scriptable(store, package, GRAPHICS_CONFIG,
                                        GRAPHICS_CONFIG_CLASS)
    obj = record.objects[path_id]
    tree = record.tree(path_id)           # the generated node must consume every byte
    settings = tree.get(DROP_ITEM_SHADE_FIELD)
    if not isinstance(settings, dict):
        raise ValueError(f"{GRAPHICS_CONFIG}: no serialized {DROP_ITEM_SHADE_FIELD}")
    known = [field for field, _, _ in DROP_ITEM_SHADE_GLOBALS]
    if sorted(settings) != sorted(known):
        raise ValueError(f"{DROP_ITEM_SHADE_FIELD}: serialized fields {sorted(settings)} "
                         f"are not the known set {sorted(known)}")
    globals_ = []
    for field, prop, name in DROP_ITEM_SHADE_GLOBALS:
        value = float(settings[field])
        globals_.append({"field": field, "property": prop, "shaderGlobal": name,
                         "value": value,
                         "written": 0.0 if abs(value) < GLOBAL_ZERO_THRESHOLD else value})
    document = {
        "version": 1, "semantics": DROP_SHADE_SEMANTICS,
        "source": {"loadName": GRAPHICS_CONFIG,
                   "containerEntries": [row["loadName"] for row in rows],
                   "serializedFile": record.archive, "pathId": str(path_id),
                   "class": GRAPHICS_CONFIG_CLASS, "name": tree.get("m_Name"),
                   "field": DROP_ITEM_SHADE_FIELD,
                   "byteSize": int(obj.byte_size), "rawSha256": _sha(obj.get_raw_data()),
                   "unityVersion": version,
                   "decode": "typetree generated from the build's assembly stubs; "
                             "every serialized byte consumed"},
        "settings": {field: float(value) for field, value in settings.items()},
        "zeroThreshold": GLOBAL_ZERO_THRESHOLD,
        "globals": globals_,
    }
    write_json(out_path, document)
    return {"path": str(out_path), "values": len(globals_),
            "byteSize": int(obj.byte_size)}


STENCIL_SHADOW_FIELD = "_stencilShadowColor"
MATERIAL_RENDERING_FIELD = "materialRenderingSettings"
# The environment shader view writes the colour as a global colour on the id of
# this property, created from exactly this name in the character shader-property
# class's static constructor.
STENCIL_SHADOW_GLOBAL = "_MysekaiCharacterStencilShadowColor"

STENCIL_SHADOW_SEMANTICS = {
    "source": ("the ScriptableObject the graphics configuration loader loads with "
               "Resources.Load at the load name above; values are its serialized "
               "fields, verbatim; display names are left out"),
    "stencilShadowColor": ("`rgba` is the serialized colour; the environment shader "
                           "view writes it as the global colour `shaderGlobal`, which "
                           "the avatar programs' stencil-shadow pass outputs"),
    "materialRenderingSettings": (
        "one row per serialized entry, in order. `shaderAttribute` is the attribute "
        "enum value the material builder looks the row up by; `renderQueue` is the "
        "queue it gives the material (plus the material's own render priority "
        "property when it has one). For each pass row the builder sets, on the "
        "material, the colour mask, the depth write and depth test when their "
        "`isActive` flag is set, source and destination blend, the stencil id "
        "(the stencil id table of the build maps `stencilIdReference` to the value), "
        "comparison, pass, fail and depth-fail operations; a shader whose pass "
        "state is not bound to those properties ignores them. Enum fields are the "
        "engine's numeric values (CompareFunction, StencilOp, BlendMode)"),
}


NPC_DITHER_FIELDS = (
    "_npcDitherStartDistanceOffset", "_npcDitherDistanceFallOff",
    "_npcDitherStartDistanceOffsetInPhotoMode", "_npcDitherDistanceFallOffInPhotoMode",
)

NPC_DITHER_SEMANTICS = {
    "source": ("the ScriptableObject the graphics configuration loader loads with "
               "Resources.Load at the load name above; `values` are four of its "
               "serialized fields, verbatim, under their serialized names"),
    "values": ("32-bit floats as serialized, written here widened to double; `bits` "
               "holds each float's IEEE-754 word as eight hex digits"),
    "use": ("the NPC view's camera dither alpha reads them: the dither starts at the "
            "camera's near clip plane plus the start offset and is complete after the "
            "fall-off length, in the character's screen-space depth; the photo-mode "
            "pair replaces the first pair while the photo mode runs"),
}


SITE_OBJECT_DITHER_FIELDS = (
    "_siteObjectDitherMinDistance", "_siteObjectDitherDistanceLength",
    "_siteObjectFarDitherAlpha",
    "_treeFixtureViewDitherMinDistance", "_treeFixtureViewDitherDistanceLength",
    "_treeFixtureViewFarDitherAlpha",
)

SITE_OBJECT_DITHER_SEMANTICS = {
    "source": ("the ScriptableObject the graphics configuration loader loads with "
               "Resources.Load at the load name above; `values` are six of its "
               "serialized fields, verbatim, under their serialized names"),
    "values": ("32-bit floats as serialized, written here widened to double; `bits` "
               "holds each float's IEEE-754 word as eight hex digits"),
    "use": ("the site objects' camera dither reads the `_siteObject` three: an object "
            "whose position is nearer the camera than the player avatar, in view-space "
            "depth, is dithered with alpha = far dither alpha * clamp((its depth - "
            "(the camera's near clip plane + min distance)) / distance length, 0, 1); "
            "the tree fixtures' camera dither reads the `_treeFixtureView` three in the "
            "same formula"),
}


def _export_float_fields(player_data, types, out_path, fields, semantics):
    """Write *fields* of the graphics configuration, each a serialized float."""
    import struct
    store, package, version = open_player_data(player_data, types)
    rows, record, path_id = _scriptable(store, package, GRAPHICS_CONFIG,
                                        GRAPHICS_CONFIG_CLASS)
    obj = record.objects[path_id]
    tree = record.tree(path_id)           # the generated node must consume every byte
    values, bits = {}, {}
    for field in fields:
        if field not in tree:
            raise ValueError(f"{GRAPHICS_CONFIG}: no serialized {field}")
        value = tree[field]
        if not isinstance(value, float):
            raise ValueError(f"{GRAPHICS_CONFIG}: {field} is not a float ({value!r})")
        values[field] = float(value)
        bits[field] = struct.pack(">f", value).hex()
    document = {
        "version": 1, "semantics": semantics,
        "source": {"loadName": GRAPHICS_CONFIG,
                   "containerEntries": [row["loadName"] for row in rows],
                   "serializedFile": record.archive, "pathId": str(path_id),
                   "class": GRAPHICS_CONFIG_CLASS, "fields": list(fields),
                   "byteSize": int(obj.byte_size), "rawSha256": _sha(obj.get_raw_data()),
                   "unityVersion": version,
                   "decode": "typetree generated from the build's assembly stubs; "
                             "every serialized byte consumed"},
        "values": values,
        "bits": bits,
    }
    write_json(out_path, document)
    return {"path": str(out_path), "values": len(values),
            "byteSize": int(obj.byte_size)}


def export_npc_dither(player_data, types, out_path):
    """Write the NPC camera-dither distances of the graphics configuration."""
    return _export_float_fields(player_data, types, out_path, NPC_DITHER_FIELDS,
                                NPC_DITHER_SEMANTICS)


def export_site_object_dither(player_data, types, out_path):
    """Write the site-object and tree-fixture camera-dither distances of the
    graphics configuration."""
    return _export_float_fields(player_data, types, out_path, SITE_OBJECT_DITHER_FIELDS,
                                SITE_OBJECT_DITHER_SEMANTICS)


def export_stencil_shadow(player_data, types, out_path):
    """Write the stencil-shadow colour and the material rendering settings."""
    store, package, version = open_player_data(player_data, types)
    rows, record, path_id = _scriptable(store, package, GRAPHICS_CONFIG,
                                        GRAPHICS_CONFIG_CLASS)
    obj = record.objects[path_id]
    tree = record.tree(path_id)           # the generated node must consume every byte
    colour = tree.get(STENCIL_SHADOW_FIELD)
    if not isinstance(colour, dict) or sorted(colour) != ["a", "b", "g", "r"]:
        raise ValueError(f"{GRAPHICS_CONFIG}: no serialized colour {STENCIL_SHADOW_FIELD}")
    items = tree.get(MATERIAL_RENDERING_FIELD)
    if not isinstance(items, list) or not items:
        raise ValueError(f"{GRAPHICS_CONFIG}: no serialized {MATERIAL_RENDERING_FIELD}")
    settings = []
    for item in items:
        entry = item["settings"]
        passes = []
        for row in entry["passRenderSettingsArray"]:
            blend, stencil, depth = (row["blendSettings"], row["stencilSettings"],
                                     row["depthSettings"])
            passes.append({
                "colorMask": int(row["colorMask"]),
                "srcBlend": int(blend["srcBlend"]), "dstBlend": int(blend["dstBlend"]),
                "stencilIdReference": int(stencil["stencilIdReference"]),
                "stencilComp": int(stencil["stencilComp"]),
                "stencilPassOp": int(stencil["stencilPassOp"]),
                "stencilFailOp": int(stencil["stencilFailOp"]),
                "stencilZFailOp": int(stencil["stencilZFailOp"]),
                "isActiveZWrite": bool(depth["isActiveZWrite"]),
                "isActiveZTest": bool(depth["isActiveZTest"]),
                "zTest": int(depth["zTest"]), "zWrite": bool(depth["zWrite"]),
            })
        settings.append({"shaderAttribute": int(entry["shaderAttribute"]),
                         "renderQueue": int(entry["renderQueueSettings"]["renderQueue"]),
                         "passes": passes})
    attributes = [row["shaderAttribute"] for row in settings]
    if len(set(attributes)) != len(attributes):
        raise ValueError(f"{MATERIAL_RENDERING_FIELD}: an attribute is listed twice")
    document = {
        "version": 1, "semantics": STENCIL_SHADOW_SEMANTICS,
        "source": {"loadName": GRAPHICS_CONFIG,
                   "containerEntries": [row["loadName"] for row in rows],
                   "serializedFile": record.archive, "pathId": str(path_id),
                   "class": GRAPHICS_CONFIG_CLASS,
                   "fields": [STENCIL_SHADOW_FIELD, MATERIAL_RENDERING_FIELD],
                   "byteSize": int(obj.byte_size), "rawSha256": _sha(obj.get_raw_data()),
                   "unityVersion": version,
                   "decode": "typetree generated from the build's assembly stubs; "
                             "every serialized byte consumed"},
        "stencilShadowColor": {"field": STENCIL_SHADOW_FIELD,
                               "shaderGlobal": STENCIL_SHADOW_GLOBAL,
                               "rgba": [float(colour[key]) for key in ("r", "g", "b", "a")]},
        "materialRenderingSettings": settings,
    }
    write_json(out_path, document)
    return {"path": str(out_path), "attributes": len(settings),
            "byteSize": int(obj.byte_size)}
