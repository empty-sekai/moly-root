"""Overhead-item (emoticon) effect packages: hierarchy, sprites, and animation.

An item is a tiny scene: a node tree whose leaves draw sprites, three generic
clips (start / loop / end) driving those nodes, and a view component that names
the sound cue and the body anchor.  Two view kinds exist.  A *sprite* item has
an animator and clips; the runtime plays ``start``, repeats ``loop``, sets the
animator flag named by ``loopEndFlag`` to leave the loop, plays ``end``, then
disposes the object one second later.  A *particle* item has no animator and no
clips: its visuals are particle emitters, exported as emitter parameters (shape,
emission, lifetime, size and colour over lifetime, and the material they draw
with) rather than as baked frames, plus the anchor and sound cue from its view.

Two encoding details decide whether an export is usable.  Clip binding paths are
relative to the **animator's own node**, not to the package root, so nodes carry
both a root-relative ``path`` and the ``animationPath`` that channels match on.
And texture names are only unique within a package — variants of the same item
reuse them — so each PNG is written under a package-qualified file name.

Items are shared across characters: a character asset pack references them by
name, it does not embed them.
"""
import json
import os
import zlib

import UnityPy

from .mecanim.clip import ATTR_SIZE, TRANSFORM_TYPEID, curve_index_map, decode, evaluate
from core.particles import decode_renderer, decode_system

ATTR_NAME = {1: "position", 2: "rotation", 3: "scale", 4: "eulerAngles"}
PHASE_BY_SUFFIX = {"start": "start", "loop": "loop", "end": "end"}
LOOP_END_FLAG = "LOOP_END_FLAG"
DISPOSE_DELAY_SECONDS = 1.0
BLEND_SHAPE_ATTR = 0                                   # not used by these packages

# View component enums, spelled out so a consumer never has to guess an integer.
LABEL_TYPE = {0: "PlaySE", 1: "StopSE", 2: "FadePlaySE", 3: "FadeSEVolume", 4: "FadeStopSE"}
ROOT_TYPE = {0: "Face", 1: "Spine", 2: "Hips"}
VIEW_KIND = {"AvatarEmoticonSpriteView": "sprite", "AvatarEmoticonParticleView": "particle"}

MODELLED_TYPES = {
    "AnimationClip", "Animator", "AnimatorController", "AssetBundle", "GameObject",
    "Material", "MonoBehaviour", "MonoScript", "ParticleSystem", "ParticleSystemRenderer",
    "Sprite", "SpriteRenderer", "Texture2D", "Transform", "RectTransform",
}


def _phase_of(name):
    tail = name.rsplit("_", 1)[-1].lower()
    return PHASE_BY_SUFFIX.get(tail)


def _texture_file(item, texture_name):
    """PNG file name for a texture, qualified by its package.

    Texture names repeat across item variants, so a flat name would let one
    package overwrite another's image.
    """
    return f"{item}__{texture_name}.png"


def _hash(path):
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


def _vec(value, keys):
    return [round(float(value.get(k, 0.0)), 6) for k in keys]


def _hierarchy(objects, trees, material_resolver=None):
    """Node tree of one package, parents first.

    Returns ``(nodes, animator_path, path_names, go_paths)`` where
    ``animator_path`` is the root-relative path of the animator's node (``None``
    when the package has no animator), ``path_names`` maps a binding hash to its
    animator-relative path, and ``go_paths`` maps a game-object id to its node
    path so components can name the node they sit on.
    """
    transforms, gameobjects, renderers, sprites, animator_go = {}, {}, {}, {}, None
    for obj in objects:
        kind = obj.type.name
        if kind in ("Transform", "RectTransform"):
            transforms[obj.path_id] = trees[obj.path_id]
        elif kind == "GameObject":
            gameobjects[obj.path_id] = trees[obj.path_id]
        elif kind == "SpriteRenderer":
            renderers[obj.path_id] = trees[obj.path_id]
        elif kind == "Sprite":
            sprites[obj.path_id] = str(trees[obj.path_id].get("m_Name", ""))
        elif kind == "Animator" and animator_go is None:
            animator_go = trees[obj.path_id].get("m_GameObject", {}).get("m_PathID", 0)

    owner, components = {}, {}
    for go_id, tree in gameobjects.items():
        for entry in tree.get("m_Component", []):
            ref = entry.get("component", entry) if isinstance(entry, dict) else {}
            comp = (ref or {}).get("m_PathID", 0)
            if comp in transforms:
                owner[comp] = go_id
            components.setdefault(go_id, []).append(comp)

    children = {}
    roots = []
    for pid, tree in transforms.items():
        father = tree.get("m_Father", {}).get("m_PathID", 0)
        if father in transforms:
            children.setdefault(father, []).append(pid)
        else:
            roots.append(pid)

    def name_of(pid):
        return str(gameobjects.get(owner.get(pid), {}).get("m_Name", ""))

    draw = {}                                   # gameobject id -> sprite renderer tree
    for tree in renderers.values():
        draw[tree.get("m_GameObject", {}).get("m_PathID", 0)] = tree

    nodes, paths = [], {}
    root_ids = set(roots)
    queue = [(pid, "") for pid in sorted(roots)]
    while queue:
        pid, parent_path = queue.pop(0)
        name = name_of(pid)
        if pid in root_ids:
            path = ""                           # the package root anchors the tree
        elif parent_path == "":
            path = name
        else:
            path = f"{parent_path}/{name}"
        paths[pid] = path
        tree = transforms[pid]
        go = gameobjects.get(owner.get(pid), {})
        node = {
            "name": name,
            "path": path,
            "parent": None if pid in root_ids else parent_path,
            "active": bool(go.get("m_IsActive", 1)),
            "position": _vec(tree.get("m_LocalPosition", {}), "xyz"),
            "rotation": _vec(tree.get("m_LocalRotation", {}), "xyzw"),
            "scale": _vec(tree.get("m_LocalScale", {}), "xyz"),
        }
        renderer = draw.get(owner.get(pid))
        if renderer is not None:
            node["sprite"] = sprites.get(renderer.get("m_Sprite", {}).get("m_PathID", 0))
            node["sortingOrder"] = renderer.get("m_SortingOrder")
            node["color"] = _vec(renderer.get("m_Color", {}), "rgba")
            node["flipX"] = bool(renderer.get("m_FlipX", 0))
            node["flipY"] = bool(renderer.get("m_FlipY", 0))
            node["rendererEnabled"] = bool(renderer.get("m_Enabled", 1))
            if material_resolver is not None:
                node["material"] = material_resolver(renderer)
        nodes.append(node)
        for child in sorted(children.get(pid, [])):
            queue.append((child, path))

    animator_pid = next((pid for pid in transforms if owner.get(pid) == animator_go), None)
    animator_path = paths.get(animator_pid) if animator_pid is not None else None
    anchor = animator_path if animator_path is not None else ""
    prefix = "" if anchor == "" else anchor + "/"
    path_names = {}
    for node in nodes:
        path = node["path"]
        if path == anchor:
            relative = ""
        elif path.startswith(prefix):
            relative = path[len(prefix):]
        else:
            node["animationPath"] = None
            continue
        node["animationPath"] = relative
        path_names[_hash(relative)] = relative
    go_paths = {owner[pid]: paths[pid] for pid in paths if pid in owner}
    return nodes, animator_path, path_names, go_paths


def _channels(clip_tree, path_names):
    """Transform channels of one generic clip, resampled at the clip's own rate."""
    bindings = clip_tree["m_ClipBindingConstant"]["genericBindings"]
    curves = decode(clip_tree)
    index, _ = curve_index_map(bindings)
    grouped = {}
    for slot, entry in curves.items():
        info = index.get(slot)
        if not info or info[0] != TRANSFORM_TYPEID:
            continue
        _, attr, path, component = info
        grouped.setdefault((path, attr), {})[component] = entry
    rate = float(clip_tree.get("m_SampleRate") or 60.0)
    stop = float(clip_tree["m_MuscleClip"]["m_StopTime"])
    frames = max(2, int(round(stop * rate)) + 1)
    out, unsupported, unresolved = [], [], []
    for (path, attr), parts in sorted(grouped.items()):
        width = ATTR_SIZE.get(attr)
        if width is None:
            unsupported.append({"pathHash": path, "attribute": attr,
                                "reason": "unmodelled transform attribute"})
            continue
        values = []
        for frame in range(frames):
            t = frame / rate
            values.append([round(evaluate(parts[c], t), 6) if c in parts else 0.0
                           for c in range(width)])
        name = path_names.get(path)
        if name is None and path not in unresolved:
            unresolved.append(path)
        out.append({"pathHash": path, "path": name,
                    "property": ATTR_NAME.get(attr, f"attribute{attr}"),
                    "values": values})
    for path in unresolved:
        unsupported.append({"pathHash": path, "reason": "path hash unresolved"})
    non_transform = sorted({b["attribute"] for b in bindings
                            if b["typeID"] != TRANSFORM_TYPEID})
    for attr in non_transform:
        unsupported.append({"attribute": attr, "reason": "non-transform binding"})
    return {"rate": rate, "duration": stop, "frames": frames,
            "channels": out, "unsupported": unsupported}


def _pairs(entries):
    """Unity serialises property maps as (name, value) pairs; accept either form."""
    for entry in entries or []:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            yield entry[0], entry[1]
        elif isinstance(entry, dict):
            yield entry.get("first"), entry.get("second")


def _material(tree, texture_files, shader=None):
    """Material name, shader, queue, texture bindings, and scalar properties."""
    props = tree.get("m_SavedProperties", {}) or {}
    textures = {}
    for name, value in _pairs(props.get("m_TexEnvs")):
        pointer = (value or {}).get("m_Texture", {}) or {}
        path_id = pointer.get("m_PathID", 0)
        if not path_id:
            continue
        textures[name] = texture_files.get(path_id)   # None when the image is elsewhere
    return {
        "name": tree.get("m_Name"),
        "shader": shader,
        "renderQueue": tree.get("m_CustomRenderQueue", -1),
        "textures": textures,
        "floats": {n: round(float(v), 6) for n, v in _pairs(props.get("m_Floats"))
                   if isinstance(v, (int, float))},
        "colors": {n: [round(v.get(c, 0.0), 6) for c in "rgba"]
                   for n, v in _pairs(props.get("m_Colors")) if isinstance(v, dict)},
    }


def _shader_name(tree, trees, external_shaders=None, source_name=None):
    """Resolve a shader pointer to its parsed shader name when available."""
    pointer = tree.get("m_Shader", {}) or {}
    path_id, file_id = pointer.get("m_PathID", 0), pointer.get("m_FileID", 0)
    if not path_id:
        return None
    shader_tree = None
    if file_id:
        resolved = None
        if external_shaders:
            resolved = external_shaders.get((source_name, file_id, path_id))
            if resolved is None:
                resolved = external_shaders.get((file_id, path_id))
            if resolved is None:
                matches = [value for key, value in external_shaders.items()
                           if isinstance(key, tuple) and len(key) == 3
                           and key[1:] == (file_id, path_id)]
                if len(matches) == 1:
                    resolved = matches[0]
        if isinstance(resolved, dict) and "shader" in resolved:
            return resolved["shader"]
        shader_tree = resolved
    else:
        shader_tree = trees.get(path_id)
    if not isinstance(shader_tree, dict):
        return None
    parsed = shader_tree.get("m_ParsedForm", {}) or {}
    name = parsed.get("m_Name")
    return str(name) if name else None


def _resolve_material(renderer_tree, trees, texture_files, externals,
                      external_materials=None, source_name=None,
                      external_shaders=None):
    """Resolve a renderer material, preserving null and unresolved states."""
    materials = renderer_tree.get("m_Materials") or []
    if not materials:
        return None
    pointer = materials[0] or {}
    path_id, file_id = pointer.get("m_PathID", 0), pointer.get("m_FileID", 0)
    if not path_id:
        return None
    if file_id:
        resolved = None
        if external_materials:
            resolved = external_materials.get((source_name, file_id, path_id))
            if resolved is None:
                resolved = external_materials.get((file_id, path_id))
            if resolved is None:
                matches = [value for key, value in external_materials.items()
                           if isinstance(key, tuple) and len(key) == 3
                           and key[1:] == (file_id, path_id)]
                if len(matches) == 1:
                    resolved = matches[0]
        if resolved is not None:
            return resolved.get("material", resolved) if isinstance(resolved, dict) else resolved
        return {"external": True, "fileId": file_id,
                "archive": externals[file_id - 1] if 0 < file_id <= len(externals) else None}
    material_tree = trees.get(path_id)
    if material_tree is None:
        return None
    return _material(material_tree, texture_files,
                     shader=_shader_name(material_tree, trees, external_shaders,
                                         source_name))


def _view(tree, class_name):
    """View component fields, with enum integers spelled out."""
    label = tree.get("_labelType")
    root = tree.get("_avatarEmoticonRootType")
    view = {
        "class": class_name,
        "kind": VIEW_KIND.get(class_name),
        "soundLabelType": LABEL_TYPE.get(label, label),
        "soundInput": tree.get("_input"),
    }
    if root is not None:
        view["anchor"] = ROOT_TYPE.get(root, root)
    if tree.get("_keepPosition") is not None:
        view["keepPosition"] = bool(tree.get("_keepPosition"))
    return view


SEMANTICS = {
    "sharedAcrossCharacters": True,
    "referencedBy": ("performance data step op `emoticon`, by the item name used here "
                     "as a key"),
    "viewKinds": ("sprite = animator plus start/loop/end clips; particle = emitters "
                  "described under `particles`, to be simulated rather than replayed"),
    "particles": ("one entry per node that emits: `system` holds emitter parameters, "
                  "`renderer` holds draw settings and the material; a module that is "
                  "on but not modelled is listed under `unsupported`"),
    "particleValues": ("every animatable particle value is a mode-tagged range: "
                       "`constant` uses `value`; `twoConstants` picks uniformly in "
                       "[`min`, `max`]; `curve` is `keys` evaluated on normalised "
                       "lifetime times `multiplier`; `twoCurves` picks between "
                       "`minKeys` and `maxKeys`. Colours use the same shape with "
                       "`colorKeys` / `alphaKeys` on independent 0..1 time axes"),
    "particleAngles": "angular particle values are radians per second",
    "phases": ("start plays once, loop repeats while shown, then the runtime sets the "
               "animator flag named by `loopEndFlag` and end plays once"),
    "disposeDelaySeconds": DISPOSE_DELAY_SECONDS,
    "nodePaths": ("`path` is relative to the package root; `animationPath` is relative "
                  "to the animator's node and is what clip channels match on "
                  "(`animationPath: null` means the node sits above the animator)"),
    "rotationOrder": "rotation quaternions are [x, y, z, w]",
    "channelValues": ("resampled at the clip's own rate; index a frame by "
                      "round(t * rate)"),
    "anchor": ("particle views name the body anchor they attach to (Face / Spine / "
               "Hips); sprite views are placed by the caller and carry no anchor "
               "field"),
    "keepPosition": ("particle views only: when true the effect stays where it was "
                     "spawned instead of following the anchor"),
    "soundInput": ("argument string for the sound call named by `soundLabelType`; for "
                   "PlaySE it is a comma-separated cue list to pick from at random"),
    "textureFiles": ("file names are package-qualified because texture names repeat "
                     "across item variants"),
}


def write_document(out_dir, items):
    """Write ``emoticons.json``, merging *items* into an already-present document.

    Every package is its own extraction job, so this accumulates: a job that
    knows only its own item must add to the index rather than replace it, and the
    summary is recomputed from every item present.
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "emoticons.json")
    merged = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            merged = json.load(handle).get("items", {})
    merged.update(items)
    merged = {k: merged[k] for k in sorted(merged)}
    unsupported = [{"item": name, **entry}
                   for name, item in merged.items()
                   for entry in item.get("unsupported", [])]
    document = {
        "version": 2,
        "semantics": SEMANTICS,
        "items": merged,
        "summary": {
            "items": len(merged),
            "textures": sum(len(item.get("textures", [])) for item in merged.values()),
            "unsupported": unsupported,
        },
    }
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    return path




def _texture_names(objects, trees):
    """Names used by Texture2D objects in one serialized file."""
    return {obj.path_id: str(trees[obj.path_id].get("m_Name", "") or "texture")
            for obj in objects if obj.type.name == "Texture2D"}


def _asset_records(objects, trees, texture_names):
    """Group objects by serialized file for cross-package PPtr lookup."""
    records = {}
    for obj in objects:
        asset_file = getattr(obj, "assets_file", None)
        if asset_file is None:
            continue
        key = id(asset_file)
        record = records.setdefault(key, {"file": asset_file, "trees": {},
                                          "textureNames": {}, "materialTrees": {},
                                          "shaderTrees": {}})
        record["trees"][obj.path_id] = trees[obj.path_id]
        if obj.type.name == "Texture2D":
            if obj.path_id in texture_names:
                record["textureNames"][obj.path_id] = texture_names[obj.path_id]
        elif obj.type.name == "Material":
            record["materialTrees"][obj.path_id] = trees[obj.path_id]
        elif obj.type.name == "Shader":
            record["shaderTrees"][obj.path_id] = trees[obj.path_id]
    return list(records.values())


def _archive_name(asset_file):
    return str(getattr(asset_file, "name", "")).rsplit("/", 1)[-1]


def _bundle_name(bundle):
    return os.path.basename(str(bundle)).split("__")[-1]


def _is_emoticon_bundle(bundle):
    """Whether a path names an item-producing effect/emoticon bundle."""
    logical = str(bundle).replace("\\", "/")
    basename = logical.rsplit("/", 1)[-1]
    return ("__effect__emoticon__" in basename or
            "/effect/emoticon/" in logical)


def _external_shader_index(loaded):
    """Index parsed shader names reachable through loaded external archives."""
    archives = {}
    for data in loaded:
        for record in data["assets"]:
            archive = _archive_name(record["file"])
            if archive:
                archives[archive] = (data["name"], record)
    index = {}
    for data in loaded:
        for source in data["assets"]:
            for file_id, external in enumerate(getattr(source["file"], "externals", []), 1):
                target = archives.get(str(getattr(external, "name", "")))
                if target is None:
                    continue
                target_name, record = target
                for path_id, tree in record["shaderTrees"].items():
                    parsed = tree.get("m_ParsedForm", {}) or {}
                    index[(data["name"], file_id, path_id)] = {
                        "shader": parsed.get("m_Name") or None,
                        "source": target_name,
                    }
    return index


def _external_material_index(loaded, external_shaders=None):
    """Index materials in the passed bundles by source file ID and path ID."""
    archives = {}
    for data in loaded:
        for record in data["assets"]:
            archive = _archive_name(record["file"])
            if archive:
                archives[archive] = (data["name"], record)
    index = {}
    for data in loaded:
        for source in data["assets"]:
            for file_id, external in enumerate(getattr(source["file"], "externals", []), 1):
                target = archives.get(str(getattr(external, "name", "")))
                if target is None:
                    continue
                target_name, record = target
                texture_files = {pid: _texture_file(target_name, tex)
                                 for pid, tex in record["textureNames"].items()}
                for path_id, tree in record["materialTrees"].items():
                    index[(data["name"], file_id, path_id)] = {
                        "material": _material(
                            tree, texture_files,
                            shader=_shader_name(tree, record["trees"],
                                                external_shaders, target_name))}
    return index


def extract_emoticons(bundles, out_dir, lookup_bundles=None):
    """Extract overhead-item packages into ``out_dir``.

    Effect/emoticon bundles produce ``items``.  Non-emoticon bundles supplied
    in *bundles*, or explicitly through *lookup_bundles*, are lookup-only
    sources for cross-package material and shader pointers.  Omitted
    dependencies remain explicit ``external`` values.
    """
    os.makedirs(out_dir, exist_ok=True)
    if lookup_bundles is None:
        targets = [bundle for bundle in bundles if _is_emoticon_bundle(bundle)]
        lookup = [bundle for bundle in bundles if not _is_emoticon_bundle(bundle)]
    else:
        targets = list(bundles)
        lookup = list(lookup_bundles)
    target_keys = {str(bundle) for bundle in targets}
    all_bundles = list(targets)
    all_bundles.extend(bundle for bundle in lookup if str(bundle) not in target_keys)
    loaded = []
    target_data = []
    for bundle in sorted(all_bundles, key=str):
        name = _bundle_name(bundle)
        env = UnityPy.load(str(bundle))
        objects = list(env.objects)
        trees = {obj.path_id: obj.read_typetree() for obj in objects}
        texture_names = _texture_names(objects, trees)
        data = {"name": name, "env": env, "objects": objects, "trees": trees,
                "textureNames": texture_names,
                "assets": _asset_records(objects, trees, texture_names)}
        loaded.append(data)
        if str(bundle) in target_keys:
            target_data.append(data)
    external_shaders = _external_shader_index(loaded)
    external_materials = _external_material_index(loaded, external_shaders)
    items, textures, unsupported = {}, 0, 0
    for data in target_data:
        name, objects, trees = data["name"], data["objects"], data["trees"]
        texture_names = data["textureNames"]
        texture_files = {pid: _texture_file(name, tex) for pid, tex in texture_names.items()}
        externals = [str(getattr(e, "name", ""))
                     for obj in objects[:1]
                     for e in getattr(obj.assets_file, "externals", [])]
        resolve_renderer = lambda renderer: _resolve_material(
            renderer, trees, texture_files, externals,
            external_materials=external_materials, source_name=name,
            external_shaders=external_shaders)
        dependencies = sorted({str(dep) for obj in objects if obj.type.name == "AssetBundle"
                               for dep in (trees[obj.path_id].get("m_Dependencies") or [])})
        scripts = {obj.path_id: str(trees[obj.path_id].get("m_ClassName", ""))
                   for obj in objects if obj.type.name == "MonoScript"}
        nodes, animator_path, path_names, go_paths = _hierarchy(
            objects, trees, material_resolver=resolve_renderer)
        item = {"viewKind": None, "view": None, "animator": None,
                "dependencies": dependencies, "nodes": nodes,
                "sprites": {}, "textures": [], "clips": {}, "particles": [],
                "unsupported": []}
        if animator_path is not None:
            item["animator"] = {"node": animator_path, "loopEndFlag": LOOP_END_FLAG}
        texture_names = data["textureNames"]
        for obj in objects:
            kind = obj.type.name
            tree = trees[obj.path_id]
            if kind not in MODELLED_TYPES:
                item["unsupported"].append({"type": kind,
                                            "reason": "object type not modelled"})
                continue
            if kind == "Texture2D":
                texture_name = str(tree.get("m_Name", "") or name)
                file_name = _texture_file(name, texture_name)
                try:
                    obj.read().image.save(os.path.join(out_dir, file_name))
                except Exception as exc:                 # unreadable texture format
                    item["unsupported"].append({"type": kind, "texture": texture_name,
                                                "reason": str(exc)})
                    continue
                texture_names[obj.path_id] = texture_name
                item["textures"].append({"name": texture_name, "file": file_name,
                                         "width": tree.get("m_Width"),
                                         "height": tree.get("m_Height")})
            elif kind == "MonoBehaviour":
                class_name = scripts.get(tree.get("m_Script", {}).get("m_PathID", 0), "")
                if class_name in VIEW_KIND:
                    item["view"] = _view(tree, class_name)
                    item["viewKind"] = VIEW_KIND[class_name]
            elif kind == "AnimationClip":
                clip_name = str(tree.get("m_Name", ""))
                phase = _phase_of(clip_name)
                entry = {"name": clip_name, **_channels(tree, path_names)}
                item["unsupported"].extend(entry.pop("unsupported"))
                item["clips"][phase or clip_name] = entry
        # Sprites are keyed by name and resolved to the texture they sample, so a
        # node's `sprite` field is enough to find the image to draw.
        for obj in objects:
            if obj.type.name != "Sprite":
                continue
            tree = trees[obj.path_id]
            sprite_name = str(tree.get("m_Name", ""))
            rect = tree.get("m_Rect", {})
            pivot = tree.get("m_Pivot", {})
            texture_id = tree.get("m_RD", {}).get("texture", {}).get("m_PathID", 0)
            texture_name = texture_names.get(texture_id)
            if sprite_name in item["sprites"]:
                item["unsupported"].append({"sprite": sprite_name,
                                            "reason": "duplicate sprite name in package"})
                continue
            item["sprites"][sprite_name] = {
                "texture": texture_name,
                "file": _texture_file(name, texture_name) if texture_name else None,
                "rect": [rect.get("x"), rect.get("y"), rect.get("width"), rect.get("height")],
                "pivot": [pivot.get("x"), pivot.get("y")],
                "pixelsToUnits": tree.get("m_PixelsToUnits"),
            }
        # Particle emitters, keyed by the node they sit on.  A system and its
        # renderer are separate components of the same node, so they are merged.
        texture_files = {pid: _texture_file(name, tex) for pid, tex in texture_names.items()}
        emitters = {}
        for obj in objects:
            kind = obj.type.name
            if kind not in ("ParticleSystem", "ParticleSystemRenderer"):
                continue
            tree = trees[obj.path_id]
            node_path = go_paths.get(tree.get("m_GameObject", {}).get("m_PathID", 0))
            slot = emitters.setdefault(node_path, {"node": node_path})
            if kind == "ParticleSystem":
                system, unmodelled = decode_system(tree)
                slot["system"] = system
                for module in unmodelled:
                    item["unsupported"].append({"node": node_path, "module": module,
                                                "reason": "particle module not modelled"})
            else:
                slot["renderer"] = decode_renderer(tree, resolve_renderer(tree))
        item["particles"] = [emitters[k] for k in sorted(emitters, key=lambda x: (x is None, x))]

        if not item["sprites"] and not item["textures"] and not item["particles"]:
            item["unsupported"].append({"reason": "package holds no sprite or texture"})
        drawn = {node.get("sprite") for node in nodes if node.get("sprite")}
        for sprite_name in sorted(drawn - set(item["sprites"])):
            item["unsupported"].append({"sprite": sprite_name,
                                        "reason": "node draws a sprite not in this package"})
        items[name] = item
        textures += len(item["textures"])
        unsupported += len(item["unsupported"])
    path = write_document(out_dir, items)
    return {"path": path, "items": len(items), "textures": textures,
            "unsupported": unsupported}
