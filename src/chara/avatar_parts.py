"""Player-appearance packages: ``virtual_live/avatar/{skin,decoration,penlight}``.

Three families under one prefix, in one extractor:

* ``skin/`` — costume textures only.  No mesh, material or shader ships in
  these packages: the game assembles the costume material at runtime, so what
  is here is the images and the container paths that name them.
* ``decoration/`` — one accessory prefab: a static ``MeshFilter``/
  ``MeshRenderer`` tree (no skinning, no clips), materials on the engine's
  Standard / URP Lit shaders, and the part texture.
* ``penlight/`` — a penlight prop: the same static shape, with one material
  on the game's ``Sekai/VirtualLive/Penlight-Color`` shader.

fbx-imported packages carry each mesh twice — an fbx node and a prefab node
reference one mesh asset, each through its own material — so geometry is
written once per mesh asset and composed once per (mesh, material set) pair,
which is exactly the split :mod:`core.mesh` makes between accessors and
composition.

Per package the extractor writes ``<category>/<package>/``: a ``package.json``
index (containers, dependencies, per-object records, every pointer that did
not resolve), decoded PNGs under ``tex/``, and — when the package holds a
hierarchy — one ``<package>.glb`` whose materials carry the serialized shader
inputs in ``extras``, the convention the character domain established.  One
index document, ``avatar-parts.json``, spans every package and is recomputed
from what was written, never from what was attempted.
"""
import argparse
import json
import os

import UnityPy

# The decrypted packages carry no Unity version in their headers (see the
# root docs); without this every load raises, which reads as "all packages
# broken" when it is only the loader's default missing.  Same precedent as
# chara.motion_index, the other module here with its own CLI.
UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

from core.gltf import GLB, unity_to_gltf_pos, unity_to_gltf_quat
from core.jsonio import dumps
from core.mesh import compose_mesh, mesh_accessors
from .emoticons import _material, _pairs, _shader_name

#: Bundle file names are the flattened logical names (``mysekai__fixture__…``
#: convention): ``virtual_live__avatar__<category>__<package>``.
CATEGORY_PREFIX = "virtual_live__avatar__"
CATEGORIES = ("skin", "decoration", "penlight")
INDEX_NAME = "avatar-parts.json"


def _flat_name(bundle):
    return os.path.basename(str(bundle))


def split_flat(flat):
    """``virtual_live__avatar__skin__costume_x`` → ``(category, package)``.

    ``(None, None)`` when the name is not an avatar-part package, so a
    misrouted bundle fails loudly here instead of silently landing nowhere.
    """
    if not flat.startswith(CATEGORY_PREFIX):
        return None, None
    rest = flat[len(CATEGORY_PREFIX):]
    for category in CATEGORIES:
        prefix = category + "__"
        if rest.startswith(prefix):
            return category, rest[len(prefix):]
    return None, None


def _containers(env, objects):
    """Container path per in-package path_id, plus the sorted path list."""
    paths, by_object = [], {}
    container = getattr(env, "container", None) or {}
    for path, obj in container.items():
        paths.append(str(path))
        if obj is not None and getattr(obj, "path_id", None) is not None:
            by_object[obj.path_id] = str(path)
    return sorted(paths), by_object


def _texture_file(package, name, disambiguator=None):
    """PNG file name for a texture, qualified by its package name.

    *disambiguator*, when given, is appended so that a second Texture2D
    object sharing the same ``m_Name`` inside one package (probed:
    penlight_0011 carries two objects both named ``light`` -- one imported
    from a ``.png`` source, one from a ``.tga`` source) gets its own file
    instead of silently overwriting the first one written.
    """
    if not disambiguator:
        return f"{package}__{name}.png"
    return f"{package}__{name}__{disambiguator}.png"


def _local_trs(tree):
    position = tree.get("m_LocalPosition", {}) or {}
    rotation = tree.get("m_LocalRotation", {}) or {}
    scale = tree.get("m_LocalScale", {}) or {}
    return ([float(position.get(k, 0.0)) for k in "xyz"],
            [float(rotation.get(k, 0.0)) for k in "xyzw"],
            [float(scale.get(k, 1.0)) for k in "xyz"])


def _hierarchy(objects, trees, kind_of):
    """Static Transform hierarchy as glTF-ready node records.

    Returns ``(nodes, root_ids)`` where each node is ``{path_id, game_object,
    name, t, q, s, children, father, mesh, materials}``; ``mesh`` and
    ``materials`` carry raw path ids (0 = none) for the caller to resolve.
    """
    transforms = {o.path_id: trees[o.path_id] for o in objects
                  if o.type.name == "Transform"}
    game_of_transform, components = {}, {}
    for path_id, kind in kind_of.items():
        tree = trees[path_id]
        if kind in ("Transform", "MeshFilter", "MeshRenderer"):
            go = (tree.get("m_GameObject") or {}).get("m_PathID", 0)
            components.setdefault(go, []).append(path_id)
            if kind == "Transform":
                game_of_transform[path_id] = go
    nodes = {}
    for transform_id, tree in transforms.items():
        go = game_of_transform.get(transform_id, 0)
        go_tree = trees.get(go, {}) if go else {}
        mesh, materials = 0, []
        for component_id in components.get(go, []):
            component = trees[component_id]
            if kind_of[component_id] == "MeshFilter":
                mesh = (component.get("m_Mesh") or {}).get("m_PathID", 0) or 0
            elif kind_of[component_id] == "MeshRenderer":
                materials = [(m or {}).get("m_PathID", 0) or 0
                             for m in component.get("m_Materials") or []]
        position, rotation, scale = _local_trs(tree)
        nodes[transform_id] = {
            "path_id": transform_id, "game_object": go,
            "name": str(go_tree.get("m_Name", "") or ""),
            "t": position, "q": rotation, "s": scale,
            "children": [c.get("m_PathID", 0)
                         for c in tree.get("m_Children") or []],
            "father": (tree.get("m_Father") or {}).get("m_PathID", 0) or 0,
            "mesh": mesh, "materials": materials,
        }
    roots = [node_id for node_id, node in nodes.items()
             if not node["father"] or node["father"] not in nodes]
    return nodes, roots


def extract_package(bundle, out_root, category, package):
    """One package → ``<out_root>/<category>/<package>/``. Returns its record."""
    directory = os.path.join(out_root, category, package)
    os.makedirs(directory, exist_ok=True)
    env = UnityPy.load(str(bundle))
    objects = list(env.objects)
    trees = {o.path_id: o.read_typetree() for o in objects}
    kind_of = {o.path_id: o.type.name for o in objects}
    container_paths, container_of = _containers(env, objects)
    dependencies = sorted({str(dep) for o in objects if o.type.name == "AssetBundle"
                           for dep in (trees[o.path_id].get("m_Dependencies") or [])})

    record = {"package": package, "category": category,
              "sourcePackage": _flat_name(bundle),
              "containers": container_paths, "dependencies": dependencies,
              "textures": [], "materials": [], "meshes": [], "renderers": [],
              "unsupported": [], "glb": None}

    # Shaders resolve in-package for these families (probed: every sampled
    # package carries its own shader objects); anything else is named as
    # unresolved rather than guessed.
    shaders = {}
    for o in objects:
        if o.type.name != "Shader":
            continue
        parsed = (trees[o.path_id].get("m_ParsedForm") or {}).get("m_Name")
        shaders[o.path_id] = str(parsed) if parsed else None

    texture_objects = [o for o in objects if o.type.name == "Texture2D"]
    # Counted up front so a same-named pair (probed: penlight_0011 carries two
    # Texture2D objects both named "light") is detected before either file is
    # written, instead of discovering the collision only when the second write
    # silently clobbers the first.
    name_counts = {}
    for o in texture_objects:
        name = str(trees[o.path_id].get("m_Name", "") or package)
        name_counts[name] = name_counts.get(name, 0) + 1

    texture_files = {}
    used_files = set()
    tex_dir = os.path.join(directory, "tex")
    os.makedirs(tex_dir, exist_ok=True)
    for o in texture_objects:
        tree = trees[o.path_id]
        name = str(tree.get("m_Name", "") or package)
        disambiguator = None
        if name_counts[name] > 1:
            # The container path is unique per object within a package and is
            # already carried in the record, so a name built from it is
            # recomputable from ``package.json`` in both directions -- the
            # source-file extension (``light.tga`` vs ``light.png``, the
            # observed case) is the natural qualifier.
            container = container_of.get(o.path_id) or ""
            ext = os.path.splitext(container)[1].lstrip(".").lower()
            disambiguator = ext or None
        file_name = _texture_file(package, name, disambiguator)
        if file_name in used_files:
            # Extension alone still collides (e.g. two same-named,
            # same-extension objects) -- path_id is unique per object in the
            # package and always breaks the tie, and is recorded below so the
            # name stays recomputable from package.json.
            file_name = _texture_file(package, name, str(o.path_id))
        used_files.add(file_name)
        try:
            o.read().image.save(os.path.join(tex_dir, file_name))
        except Exception as exc:  # unreadable texture format
            record["unsupported"].append({"type": "Texture2D", "name": name,
                                          "reason": str(exc)})
            continue
        texture_files[o.path_id] = file_name
        record["textures"].append({"name": name, "file": file_name,
                                   "width": tree.get("m_Width"),
                                   "height": tree.get("m_Height"),
                                   "container": container_of.get(o.path_id),
                                   "pathId": o.path_id})

    # Materials resolve against the package's own textures; a texture pointer
    # that leaves the package keeps its raw path id in the record, flagged.
    material_records = {}
    for o in objects:
        if o.type.name != "Material":
            continue
        tree = trees[o.path_id]
        shader_name = _shader_name(tree, trees)
        entry = _material(tree, texture_files, shader=shader_name)
        if shader_name is None:
            pointer = (tree.get("m_Shader") or {}).get("m_PathID", 0)
            entry["shaderUnresolved"] = bool(pointer)
        missing = [slot for slot, file_name in entry["textures"].items()
                   if file_name is None]
        for slot in missing:
            record["unsupported"].append({
                "type": "Material", "name": entry["name"],
                "reason": f"texture slot {slot} points outside this package"})
        material_records[o.path_id] = entry
        record["materials"].append(entry)

    if not any(o.type.name == "Transform" for o in objects):
        # Texture-only family (skin): no hierarchy, no glb, nothing to compose.
        _write_json(os.path.join(directory, "package.json"), record)
        return record

    glb = GLB(generator="moly-root avatar-parts")
    glb.g["samplers"].append({"magFilter": 9728, "minFilter": 9728,
                              "wrapS": 33071, "wrapT": 33071})
    texture_index = {}
    for entry in record["textures"]:
        path = os.path.join(directory, "tex", entry["file"])
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            vi = glb.view(f.read())
        glb.g["images"].append({"bufferView": vi, "mimeType": "image/png",
                                "name": entry["name"]})
        glb.g["textures"].append({"sampler": 0,
                                  "source": len(glb.g["images"]) - 1,
                                  "name": entry["name"]})
        texture_index[entry["name"]] = len(glb.g["textures"]) - 1

    # glTF material per Unity material: engine-viewer fields from the main
    # texture, everything serialized stays verbatim in extras.
    gltf_material = {}
    for path_id, entry in material_records.items():
        main = entry["textures"].get("_MainTex")
        material = {"name": entry["name"] or f"material_{path_id}",
                    "doubleSided": True,
                    "pbrMetallicRoughness": {"metallicFactor": 0.0,
                                             "roughnessFactor": 1.0},
                    "extras": {"shader": entry["shader"],
                               "renderQueue": entry["renderQueue"],
                               "floats": entry["floats"],
                               "colors": entry["colors"],
                               "textures": entry["textures"],
                               "textureScaleOffset": entry["textureScaleOffset"]}}
        if main and main in texture_index:
            material["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": texture_index[main]}
        glb.g["materials"].append(material)
        gltf_material[path_id] = len(glb.g["materials"]) - 1

    mesh_buffers, composed, mesh_records = {}, {}, {}
    for o in objects:
        if o.type.name != "Mesh":
            continue
        try:
            mesh_buffers[o.path_id] = mesh_accessors(glb, o, trees[o.path_id],
                                                     skin=False)
        except Exception as exc:
            record["unsupported"].append({"type": "Mesh",
                                          "name": trees[o.path_id].get("m_Name"),
                                          "reason": str(exc)})

    nodes, roots = _hierarchy(objects, trees, kind_of)
    for node in nodes.values():
        key = (node["mesh"], tuple(node["materials"]))
        if not node["mesh"] or node["mesh"] not in mesh_buffers:
            if node["mesh"]:
                record["unsupported"].append({
                    "type": "MeshRenderer", "name": node["name"],
                    "reason": "renderer's mesh is not in this package"})
            node["glbMesh"] = None
            node["glbMaterials"] = []
            continue
        if key not in composed:
            slots = {}
            for index, material_id in enumerate(node["materials"]):
                if material_id in gltf_material:
                    slots[index] = gltf_material[material_id]
                elif material_id:
                    record["unsupported"].append({
                        "type": "MeshRenderer", "name": node["name"],
                        "reason": f"material slot {index} is not in this package"})
            composed[key] = compose_mesh(glb, mesh_buffers[node["mesh"]],
                                         name=node["name"] or None,
                                         materials=slots)
        node["glbMesh"] = composed[key]
        node["glbMaterials"] = [gltf_material.get(m) for m in node["materials"]]
        buffers = mesh_buffers[node["mesh"]]
        mesh_records.setdefault(node["mesh"], {
            "name": buffers["name"], "vertices": buffers["vertices"],
            "triangles": buffers["triangles"],
            "submeshes": len(buffers["submeshes"])})
        record["renderers"].append({
            "node": node["name"], "mesh": node["mesh"],
            "materials": node["materials"]})

    glb_nodes = {}
    for node in nodes.values():
        glb_node = {"name": node["name"],
                    "translation": unity_to_gltf_pos(node["t"]),
                    "rotation": unity_to_gltf_quat(node["q"]),
                    "scale": node["s"]}
        if node["glbMesh"] is not None:
            glb_node["mesh"] = node["glbMesh"]
        glb_nodes[node["path_id"]] = len(glb.g["nodes"])
        glb.g["nodes"].append(glb_node)
    for node in nodes.values():
        index = glb_nodes[node["path_id"]]
        children = [glb_nodes[c] for c in node["children"] if c in glb_nodes]
        if children:
            glb.g["nodes"][index]["children"] = children
    glb.g["scenes"] = [{"nodes": [glb_nodes[r] for r in roots if r in glb_nodes]}]
    glb.g["scene"] = 0

    glb_name = f"{package}.glb"
    glb.save(os.path.join(directory, glb_name))
    record["glb"] = glb_name
    record["meshes"] = [mesh_records[k] for k in sorted(mesh_records)]
    _write_json(os.path.join(directory, "package.json"), record)
    return record


def _write_json(path, document):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(document))


def extract_avatar_parts(bundles, out_dir):
    """Extract avatar-part packages into ``out_dir``.

    Every bundle passed here is one package; the families never reference
    each other's contents (each package carries its own materials, shaders
    and textures, probed), so unlike the phenomena or site domains this is
    one job per package with no shared lookup pass.
    """
    os.makedirs(out_dir, exist_ok=True)
    documents = {}
    totals = {"textures": 0, "meshes": 0, "materials": 0, "unsupported": 0}
    for bundle in bundles:
        flat = _flat_name(bundle)
        category, package = split_flat(flat)
        if category is None:
            raise ValueError(f"not an avatar-part package: {flat}")
        record = extract_package(bundle, out_dir, category, package)
        documents[f"{category}/{package}"] = {
            "glb": record["glb"],
            "textures": len(record["textures"]),
            "materials": len(record["materials"]),
            "meshes": len(record["meshes"]),
            "renderers": len(record["renderers"]),
            "unsupported": len(record["unsupported"]),
        }
        totals["textures"] += len(record["textures"])
        totals["meshes"] += len(record["meshes"])
        totals["materials"] += len(record["materials"])
        totals["unsupported"] += len(record["unsupported"])

    by_category = {}
    for key, document in sorted(documents.items()):
        by_category.setdefault(key.split("/", 1)[0], {})[key.split("/", 1)[1]] = document
    index = {"version": 1, "categories": by_category,
             "summary": {"packages": len(documents), **totals}}
    path = os.path.join(out_dir, INDEX_NAME)
    _write_json(path, index)
    return {"path": path, "packages": len(documents), **totals}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="extract virtual_live avatar-part packages")
    parser.add_argument("bundles", nargs="+", help="decrypted bundle files")
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args(argv)
    print(json.dumps(extract_avatar_parts(arguments.bundles, arguments.out),
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
