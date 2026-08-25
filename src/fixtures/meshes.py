"""Furniture geometry: one glTF binary (.glb) per package, every prefab variant.

A furniture package holds one or more *prefab variants* — the model-import tree
and the runtime prefab tree are two parallel root transform trees, and several
packages carry extra variant trees (a doll package ships the same furniture at
small / medium / large, distinguished from one another by their root name and
container path, and the attach points of ``fixture-attach`` live only in the tree
that owns the ``FixtureView``).  This module reads the geometry everyone of those
trees points at — the meshes, the materials and the textures — and writes one
``.glb`` whose ``scenes`` array holds one scene per variant, so nothing is
flattened or merged.

Two things are kept deliberately against the coordinate-conversion convention
that the rest of this repository follows (see :mod:`core.gltf`):

* Every node transform is exported **verbatim** from the Unity typetree —
  ``m_LocalPosition`` / ``m_LocalRotation`` / ``m_LocalScale`` become the glTF
  ``translation`` / ``rotation`` / ``scale`` with no unit conversion, no axis
  reflection and no node merging.  The ``loc_startNNN`` / ``loc_endNNN`` attach
  points (see :mod:`fixtures.attach`) are GameObjects in that same hierarchy and
  were recorded verbatim; putting a character there needs the geometry and the
  level to agree, so both are left in the authored (Unity, left-handed) space.
  A package that needs a different convention is reported, never converted.
* Vertex data follows the same rule: positions, normals, tangents and UVs are
  written verbatim (no ``unity_to_gltf_pos`` reflection, no winding reordering,
  no UV ``1 - v`` flip), and colors are written as the authored UNorm8 bytes
  rather than as floats — writing a color as ``<4f`` would let a 0..255 byte read
  as a 0..1 float plus a 255x glow (the fixture shaders ship byte colors).

The extractor reads meshes from **both** a ``MeshFilter``/``MeshRenderer`` pair
and a ``SkinnedMeshRenderer``.  ``fixture-mesh-topology`` reports 111 packages
whose geometry hangs entirely on ``SkinnedMeshRenderer``; an extractor that only
watches ``MeshRenderer`` would silently export empty files for all of them and
still report a green run.  Skins are not exported (the contract's skeleton is for
characters, and the dispatch here asks for geometry, materials and node
transforms), but the bone transforms stay in the hierarchy as ordinary nodes.
"""
import io
import json
import os
import struct
from pathlib import Path

import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

from core.assets.packages import PackageStore
from core.gltf import GLB
from core.jsonio import write_json
from core.mesh import (FLOAT, UNSIGNED_INT, ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER,
                       TRIANGLES, NOT_TRIANGLES, INDEX_RANGE, compose_mesh)

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

FIXTURE_PREFIX = "mysekai__fixture__"
TRANSFORMS = ("Transform", "RectTransform")
UNSIGNED_BYTE = 5121

MISSING_BUNDLE = "bundle file not found; it was not opened"
NO_RENDERER = "a transform carries no MeshRenderer or SkinnedMeshRenderer"
UNREADABLE_MESH = "mesh vertex data could not be read"
NOT_TRIANGLE_LIST = "a submesh is not a triangle list"
TEXTURE_DECODE_FAILED = "texture could not be decoded to PNG"
MATERIAL_UNRESOLVED = "a renderer names a material that does not resolve here"

UNSIGNED_BYTE = 5121


def _tree(record, path_id):
    """A game object's typetree, or ``None`` when the id names nothing here."""
    if path_id not in record.kinds:
        return None
    return record.tree(path_id)


def _components(record, goid):
    """The component ids and kinds attached to a game object, in authored order."""
    tree = _tree(record, goid)
    if not tree:
        return []
    out = []
    for entry in tree.get("m_Component") or []:
        pointer = (entry.get("component") or entry) if isinstance(entry, dict) else entry
        cid = (pointer or {}).get("m_PathID", 0)
        out.append((cid, record.kinds.get(cid)))
    return out


def _graph(record):
    """The transform graph: ``(transforms, gameobjects, roots)``.

    ``transforms`` maps a transform id to its typetree, ``gameobjects`` maps a
    game object id to its typetree, and ``roots`` are the transform ids that have
    no parent transform in this file (the prefab-variant roots).
    """
    transforms, gameobjects = {}, {}
    for pid, kind in record.kinds.items():
        if kind in TRANSFORMS:
            transforms[pid] = record.tree(pid)
        elif kind == "GameObject":
            gameobjects[pid] = record.tree(pid)
    roots = [pid for pid, tt in transforms.items()
             if not ((tt.get("m_Father") or {}).get("m_PathID", 0) in transforms)]
    return transforms, gameobjects, roots


def _local_transform(tree):
    """A transform's local (translation, rotation, scale) read verbatim."""
    lp = tree.get("m_LocalPosition") or {}
    lr = tree.get("m_LocalRotation") or {}
    ls = tree.get("m_LocalScale") or {}
    return ([lp.get("x", 0.0), lp.get("y", 0.0), lp.get("z", 0.0)],
            [lr.get("x", 0.0), lr.get("y", 0.0), lr.get("z", 0.0), lr.get("w", 1.0)],
            [ls.get("x", 1.0), ls.get("y", 1.0), ls.get("z", 1.0)])


def _mesh_channels(glb, mesh_obj, tree):
    """Write one mesh's vertex buffers into *glb*, verbatim, for composing.

    Returns the ``buffers`` dict that :func:`core.mesh.compose_mesh` consumes:
    ``{"attributes", "submeshes", "name", "vertices", "triangles"}``.  Unlike
    ``core.mesh.mesh_accessors`` this does not reflect the X axis, reorder
    winding or flip UVs — the surrounding node transforms are exported verbatim,
    so the geometry is kept in the same (authored) space.  Colors are written as
    the UNorm8 bytes they are authored in.
    """
    handler = MeshHandler(mesh_obj.read())
    handler.process()
    count = handler.m_VertexCount
    positions = [tuple(float(c) for c in v[:3]) for v in (handler.m_Vertices or [])]
    bounds = ([min(p[axis] for p in positions) for axis in range(3)],
              [max(p[axis] for p in positions) for axis in range(3)])
    attributes = {"POSITION": glb.acc(
        b"".join(struct.pack("<3f", *p) for p in positions),
        FLOAT, "VEC3", count, ARRAY_BUFFER, bounds)}
    normals = getattr(handler, "m_Normals", None)
    if normals:
        attributes["NORMAL"] = glb.acc(
            b"".join(struct.pack("<3f", *tuple(float(c) for c in v[:3]))
                     for v in normals),
            FLOAT, "VEC3", count, ARRAY_BUFFER)
    tangents = getattr(handler, "m_Tangents", None)
    if tangents:
        attributes["TANGENT"] = glb.acc(
            b"".join(struct.pack("<4f", *tuple(float(c) for c in v[:4]))
                     for v in tangents),
            FLOAT, "VEC4", count, ARRAY_BUFFER)
    for slot, values in (("TEXCOORD_0", handler.m_UV0),
                         ("TEXCOORD_1", getattr(handler, "m_UV1", None)),
                         ("TEXCOORD_2", getattr(handler, "m_UV2", None)),
                         ("TEXCOORD_3", getattr(handler, "m_UV3", None))):
        if values:
            attributes[slot] = glb.acc(
                b"".join(struct.pack("<2f", *tuple(float(c) for c in v[:2]))
                         for v in values),
                FLOAT, "VEC2", count, ARRAY_BUFFER)
    colors = getattr(handler, "m_Colors", None)
    if colors:
        accessor = glb.acc(
            b"".join(struct.pack("<4B", *tuple(int(round(float(c))) & 0xFF
                                              for c in v[:4])) for v in colors),
            UNSIGNED_BYTE, "VEC4", count, ARRAY_BUFFER)
        glb.g["accessors"][accessor]["normalized"] = True
        attributes["COLOR_0"] = accessor

    indices = list(handler.m_IndexBuffer or [])
    submeshes, triangles, base = [], 0, 0
    for submesh in tree.get("m_SubMeshes") or []:
        if submesh.get("topology", TRIANGLES) != TRIANGLES:
            raise ValueError(NOT_TRIANGLE_LIST)
        length = int(submesh.get("indexCount", 0))
        part = indices[base:base + length]
        base += length
        if part and max(part) >= count:
            raise ValueError(INDEX_RANGE)
        submeshes.append(glb.acc(
            b"".join(struct.pack("<I", int(v)) for v in part),
            UNSIGNED_INT, "SCALAR", length, ELEMENT_ARRAY_BUFFER))
        triangles += length // 3
    return {"name": str(tree.get("m_Name") or "mesh"), "attributes": attributes,
            "submeshes": submeshes, "vertices": count, "triangles": triangles}


def _texture_index(glb, record, path_id, cache):
    """Add a texture to *glb* and return its glTF ``textures`` index."""
    key = (record.archive, path_id)
    if key in cache:
        return cache[key]
    obj = record.objects.get(path_id)
    if obj is None:
        raise ValueError(TEXTURE_DECODE_FAILED)
    tex = obj.read()
    try:
        image = tex.image
        buffer = io.BytesIO()
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        png = buffer.getvalue()
    except Exception:
        raise ValueError(TEXTURE_DECODE_FAILED)
    view = glb.view(png)
    glb.g["images"].append({"bufferView": view, "mimeType": "image/png",
                            "name": tex.m_Name})
    glb.g["textures"].append({"sampler": 0, "source": len(glb.g["images"]) - 1,
                              "name": tex.m_Name})
    index = len(glb.g["textures"]) - 1
    cache[key] = index
    return index


def _texenvs(tt):
    sp = tt.get("m_SavedProperties") or {}
    return sp.get("m_TexEnvs") or []


def _texenv(texenvs, key):
    """A texture path id named *key* in a material's ``m_TexEnvs``, or ``None``."""
    for entry in texenvs:
        if (isinstance(entry, (list, tuple)) and len(entry) == 2
                and entry[0] == key):
            value = entry[1] or {}
            return (value.get("m_Texture") or {}).get("m_PathID")
        if isinstance(entry, dict) and entry.get("first") == key:
            return (entry.get("second") or {}).get("m_Texture", {}).get("m_PathID")
    return None


def _float_prop(tt, key):
    sp = tt.get("m_SavedProperties") or {}
    floats = sp.get("m_Floats") or {}
    if isinstance(floats, dict):
        return floats.get(key)
    for entry in floats:
        if isinstance(entry, (list, tuple)) and len(entry) == 2 and entry[0] == key:
            return entry[1]
    return None


def _shader_value(store, record, material_tree):
    """Read a material's Shader name and declared pass tags."""
    pointer = material_tree.get("m_Shader") or {}
    path_id = pointer.get("m_PathID", 0)
    if not path_id:
        return {"status": "unresolved", "reason": "material has no Shader pointer"}
    target = _resolve(store, record, pointer)
    if target is None:
        archive = None
        try:
            archive = store.archive_of(record, pointer)
        except (AttributeError, IndexError):
            archive = None
        target_name = str(archive) if archive else "same asset file"
        return {"status": "unresolved",
                "reason": f"Shader pointer {target_name}:{path_id} did not resolve"}
    shader_record, shader_id = target
    shader_obj = shader_record.objects.get(shader_id)
    if shader_obj is None:
        return {"status": "unresolved",
                "reason": f"Shader object {shader_id} is absent"}
    shader_tree = shader_obj.read_typetree() or {}
    parsed = shader_tree.get("m_ParsedForm") or {}
    shader_name = parsed.get("m_Name")
    if not isinstance(shader_name, str) or not shader_name:
        return {"status": "unresolved",
                "reason": f"Shader object {shader_id} has no parsed name"}
    shader = {"status": "resolved", "name": shader_name}
    passes = []
    light_modes = []
    for subshader in parsed.get("m_SubShaders") or []:
        for shader_pass in subshader.get("m_Passes") or []:
            state = shader_pass.get("m_State") or {}
            tags = {}
            for entry in (state.get("m_Tags") or {}).get("tags") or []:
                if isinstance(entry, (list, tuple)) and len(entry) == 2:
                    tags[str(entry[0])] = entry[1]
                elif isinstance(entry, dict):
                    tags[str(entry.get("first"))] = entry.get("second")
            pass_name = shader_pass.get("m_Name") or shader_pass.get("m_PassName")
            light_mode = tags.get("LIGHTMODE")
            item = {"name": pass_name if pass_name else None,
                    "lightMode": light_mode}
            passes.append(item)
            light_modes.append(light_mode)
    if passes:
        shader["shaderPasses"] = passes
        shader["lightModes"] = light_modes
    return shader


def _material_index(glb, record, path_id, cache, tex_cache, store):
    """Add a material to *glb* and return its glTF ``materials`` index.

    The cache key is the serialized file's own archive name plus the path id,
    not the identity of the record object.  A path id is unique only within its
    file, so the file has to be part of the key; naming the file by its archive
    keys on the file itself rather than on which instance of it we happen to be
    holding, which is what ``id(record)`` keys on.

    This is not why an export can hold more materials than its own bundle does.
    A glb is self-contained, so a material a renderer reaches in a *dependency*
    package is written into this package's glb too: ``road_bg1`` declares
    ``road_brick1`` as a dependency and uses four of its materials alongside its
    own four.  Counting materials over all the exported glbs therefore exceeds
    counting Material objects per bundle -- 1862 against 1805 -- and those are
    two different quantities, not a discrepancy.
    """
    key = (record.archive or record.bundle, path_id)
    if key in cache:
        return cache[key]
    obj = record.objects.get(path_id)
    if obj is None:
        raise ValueError(MATERIAL_UNRESOLVED)
    tt = obj.read_typetree()
    name = str(tt.get("m_Name") or "")
    texenvs = _texenvs(tt)
    material = {"name": name, "doubleSided": False,
                "pbrMetallicRoughness": {"metallicFactor": 0.0,
                                         "roughnessFactor": 1.0}}
    tex_path = _texenv(texenvs, "_MainTex")
    if tex_path:
        try:
            material["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": _texture_index(glb, record, tex_path, tex_cache)}
        except ValueError:
            pass
    shader = _shader_value(store, record, tt)
    extras = {"sourceMaterial": name,
              "cullMode": _float_prop(tt, "_Cull"),
              "alphaClip": _float_prop(tt, "_AlphaClip"),
              "fixtureShaderUsage": _float_prop(tt, "_FixtureShaderUsage"),
              "shader": shader["name"] if shader["status"] == "resolved" else shader}
    if shader["status"] == "resolved":
        if "shaderPasses" in shader:
            extras["shaderPasses"] = shader["shaderPasses"]
        if "lightModes" in shader:
            extras["lightModes"] = shader["lightModes"]
    material["extras"] = extras
    glb.g["materials"].append(material)
    index = len(glb.g["materials"]) - 1
    cache[key] = index
    return index


def _resolve(store, record, pointer):
    """Follow *pointer* to a ``(record, path id)`` in the store, or ``None``."""
    pointer = pointer or {}
    if not pointer.get("m_PathID"):
        return None
    return store.follow(record, pointer)


def _renderer_mesh_and_materials(record, component_id, kind):
    """The mesh pointer and material pointers a renderer draws, or why none."""
    tree = _tree(record, component_id)
    if not tree:
        return None, [], NO_RENDERER
    goid = (tree.get("m_GameObject") or {}).get("m_PathID")
    if kind == "MeshRenderer":
        for cid, ckind in _components(record, goid):
            if ckind == "MeshFilter":
                mesh = (_tree(record, cid) or {}).get("m_Mesh") or {}
                return mesh, _materials(tree), None
        return None, [], NO_RENDERER
    if kind == "SkinnedMeshRenderer":
        return (tree.get("m_Mesh") or {}), _materials(tree), None
    return None, [], NO_RENDERER


def _materials(tree):
    """The material pointers a renderer's ``m_Materials`` names."""
    return list(tree.get("m_Materials") or [])


def _walk(glb, record, store, tpid, parent, ctx):
    """Export one transform and its children, returning the glTF node index."""
    tt = ctx["transforms"][tpid]
    goid = (tt.get("m_GameObject") or {}).get("m_PathID")
    name = str((ctx["gameobjects"].get(goid) or {}).get("m_Name", ""))
    translation, rotation, scale = _local_transform(tt)
    node = {"name": name, "translation": translation,
            "rotation": rotation, "scale": scale,
            "extras": {"sourcePathId": tpid, "gameObjectId": goid}}
    index = len(glb.g["nodes"])
    glb.g["nodes"].append(node)
    ctx["report"]["nodeNames"].add(name)
    ctx["variantGoids"].add(goid)
    if goid in ctx["fixtureView"]:
        ctx["fvRoots"].add(ctx["root"])

    for cid, kind in _components(record, goid):
        if kind not in ("MeshRenderer", "SkinnedMeshRenderer"):
            continue
        mesh_pointer, material_pointers, reason = _renderer_mesh_and_materials(
            record, cid, kind)
        if not (mesh_pointer or {}).get("m_PathID"):
            ctx["report"]["anomalies"].append(
                {"type": "no-mesh", "node": name, "kind": kind})
            continue
        target = _resolve(store, record, mesh_pointer)
        if target is None:
            ctx["report"]["anomalies"].append(
                {"type": "mesh-unresolved", "node": name, "kind": kind})
            continue
        mesh_record, mesh_id = target
        if mesh_id in ctx["mesh_cache"]:
            node["mesh"] = ctx["mesh_cache"][mesh_id]
            continue
        mesh_obj = mesh_record.objects.get(mesh_id)
        if mesh_obj is None:
            ctx["report"]["anomalies"].append(
                {"type": "mesh-unresolved", "node": name})
            continue
        try:
            mesh_tt = mesh_obj.read_typetree()
            buffers = _mesh_channels(glb, mesh_obj, mesh_tt)
        except Exception:
            ctx["report"]["anomalies"].append(
                {"type": "mesh-unreadable", "node": name, "kind": kind})
            continue
        materials = {}
        for sub_index, mat_pointer in enumerate(material_pointers):
            if not (mat_pointer or {}).get("m_PathID"):
                continue
            mat_target = _resolve(store, record, mat_pointer)
            if mat_target is None:
                ctx["report"]["anomalies"].append(
                    {"type": "material-unresolved", "node": name})
                continue
            mat_record, mat_id = mat_target
            try:
                materials[sub_index] = _material_index(
                    glb, mat_record, mat_id, ctx["material_cache"],
                    ctx["tex_cache"], store)
            except ValueError:
                ctx["report"]["anomalies"].append(
                    {"type": "material-unresolved", "node": name})
        mesh_index = compose_mesh(glb, buffers, None, materials)
        ctx["mesh_cache"][mesh_id] = mesh_index
        node["mesh"] = mesh_index
        ctx["report"]["vertexCount"] += buffers["vertices"]
        ctx["report"]["meshCount"] += 1
        if not buffers["vertices"]:
            ctx["report"]["zeroVertexMeshes"] += 1
        ctx["report"]["hasGeometry"] = True

    children = []
    for child in tt.get("m_Children") or []:
        child_id = (child or {}).get("m_PathID", 0)
        if child_id in ctx["transforms"]:
            children.append(_walk(glb, record, store, child_id, index, ctx))
    if children:
        node["children"] = children
    if parent is not None:
        glb.g["nodes"][parent].setdefault("children", []).append(index)
    return index


def _variants(glb, record, store, transforms, gameobjects, roots, ctx,
              container):
    """Export every root transform tree as one scene variant."""
    root_nodes, variants = [], []
    for root in roots:
        root_go = gameobjects.get((transforms[root].get("m_GameObject")
                                   or {}).get("m_PathID"))
        root_name = str((root_go or {}).get("m_Name", ""))
        ctx["root"] = root
        ctx["variantGoids"] = set()
        before = ctx["report"]["nodeNames"].copy()
        node_index = _walk(glb, record, store, root, None, ctx)
        container_paths = [path for path, goid in container
                           if goid in ctx["variantGoids"]]
        variants.append({
            "rootNode": node_index,
            "rootName": root_name,
            "containerPaths": container_paths,
            "nodeCount": len(set(ctx["report"]["nodeNames"]) - before),
            "hasFixtureView": root in ctx["fvRoots"],
        })
        root_nodes.append(node_index)
    return root_nodes, variants


def _fixture_view_game_objects(record):
    """Game object ids whose behaviour is a ``FixtureView``, by class name."""
    out = []
    for cid, kind in record.kinds.items():
        if kind != "MonoBehaviour":
            continue
        if record.script_of(cid) != "FixtureView":
            continue
        tree = record.tree(cid)
        out.append((tree.get("m_GameObject") or {}).get("m_PathID"))
    return out


def _container(record):
    """The bundle's container: ``[(asset path, asset path id), ...]``."""
    for cid, kind in record.kinds.items():
        if kind != "AssetBundle":
            continue
        tree = record.tree(cid)
        out = []
        for asset_path, info in tree.get("m_Container") or []:
            target = (info or {}).get("asset") or {}
            out.append((str(asset_path), target.get("m_PathID")))
        return out
    return []


def _export_package(store, name, out_dir):
    """One furniture package to ``<out_dir>/<name>.glb`` plus its record."""
    package = store.package(name)
    if package is None:
        return {"name": name, "status": "no-mesh", "reason": MISSING_BUNDLE,
                "variants": [], "meshCount": 0, "vertexCount": 0,
                "nodeNames": [], "anomalies": []}
    # Load what this package declares it depends on, before any pointer is
    # followed.  A cross-file pointer resolves through the archive table, and
    # that table only holds archives of packages already loaded -- so without
    # this every external pointer comes back unresolved and the run reads as
    # "the dependency is not on disk" when in fact nobody opened it.  Measured
    # on one package: its two material shader pointers resolve to
    # ``Mysekai/Fixture/Basic`` and ``Mysekai/Fixture/ShadowMesh`` once
    # ``mysekai/shader`` is loaded, and to nothing at all before.
    for dependency in package.dependencies:
        store.package(str(dependency).replace("/", "__"))
    glb = GLB(generator="moly-root fixture extractor")
    report = {"name": name, "nodeNames": set(), "vertexCount": 0,
              "meshCount": 0, "zeroVertexMeshes": 0, "hasGeometry": False,
              "anomalies": []}
    ctx = {"fixtureView": set(), "fvRoots": set(), "root": None,
           "variantGoids": set(), "mesh_cache": {}, "material_cache": {},
           "tex_cache": {}, "report": report}
    variants, root_nodes, container = [], [], []
    for record in package.files:
        if not record.kinds:
            continue
        transforms, gameobjects, roots = _graph(record)
        ctx["transforms"] = transforms
        ctx["gameobjects"] = gameobjects
        ctx["fixtureView"] |= set(_fixture_view_game_objects(record))
        if not container:
            container = _container(record)
        local_roots, local_variants = _variants(glb, record, store,
                                                transforms, gameobjects,
                                                roots, ctx, container)
        root_nodes += local_roots
        variants += local_variants

    # Materials can be present in the package without a renderer slot.  Keep
    # those source objects in the exported material table as well.
    for record in package.files:
        for material_id, kind in record.kinds.items():
            if kind != "Material":
                continue
            try:
                _material_index(glb, record, material_id,
                                ctx["material_cache"], ctx["tex_cache"], store)
            except ValueError:
                report["anomalies"].append(
                    {"type": "material-unresolved", "pathId": material_id})

    # One scene per variant; the default scene is the one carrying the FixtureView.
    if variants:
        glb.g["scenes"] = [{"nodes": [variant["rootNode"]]}
                           for variant in variants]
        default = next((i for i, variant in enumerate(variants)
                        if variant["hasFixtureView"]), 0)
    else:
        glb.g["scenes"] = [{"nodes": []}]
        default = 0
    glb.g["scene"] = default
    path = out_dir / f"{name}.glb"
    glb.save(path)
    document = {
        "name": name,
        "status": "exported" if report["hasGeometry"] else "no-mesh",
        "glb": path.name,
        "roots": len(root_nodes),
        "variants": variants,
        "meshCount": report["meshCount"],
        "vertexCount": report["vertexCount"],
        "zeroVertexMeshes": report["zeroVertexMeshes"],
        "hasFixtureView": bool(ctx["fixtureView"]),
        "nodeNames": sorted(report["nodeNames"]),
        "anomalies": report["anomalies"],
    }
    return document


def _list(store):
    """The package names the store holds, in the order they will be extracted."""
    return sorted(name for name in store.paths if name.startswith(FIXTURE_PREFIX))


def extract_from_store(store, out_dir):
    """Extract every furniture package's geometry into ``<out_dir>``.

    *store* is a ``PackageStore`` of the bundle files to read.  Each package
    becomes one ``<name>.glb`` (one scene per prefab variant); a package whose
    file is not on disk, or which carries no renderable mesh, is reported with
    its reason rather than written as an empty file, because the loader answers a
    missing path with zero objects and silence.  Returns a summary dict.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = _list(store)
    packages = {}
    exported = no_mesh = failed = 0
    prefabs = 0
    zero_mesh_packages = 0
    mesh_packages = 0
    anomalies_by_type = {}

    for name in names:
        try:
            document = _export_package(store, name, out)
        except Exception as exc:
            failed += 1
            packages[name] = {"name": name, "status": "failed",
                              "reason": f"{type(exc).__name__}: {exc}",
                              "variants": [], "meshCount": 0,
                              "vertexCount": 0, "nodeNames": [],
                              "anomalies": []}
            continue
        packages[name] = document
        prefabs += len(document["variants"])
        if document["status"] == "exported":
            exported += 1
            mesh_packages += 1
            if document["vertexCount"] == 0:
                zero_mesh_packages += 1
        elif document["status"] == "no-mesh":
            no_mesh += 1
        for anomaly in document["anomalies"]:
            anomalies_by_type[anomaly["type"]] = anomalies_by_type.get(anomaly["type"], 0) + 1

    resolution = [name for name in names
                  if packages[name]["status"] == "no-mesh"]
    failures = [name for name in names
                if packages[name]["status"] == "failed"]

    summary = {
        "bundles": len(names),
        "exported": exported,
        "noMesh": no_mesh,
        "failed": failed,
        "prefabs": prefabs,
        "meshPackages": mesh_packages,
        "zeroVertexPackages": zero_mesh_packages,
        "nonZeroVertexPackages": mesh_packages - zero_mesh_packages,
        "anomalies": anomalies_by_type,
        "noMeshNames": resolution,
        "failedNames": failures,
    }
    document = {"version": 1, "summary": summary, "packages": packages}
    index_path = write_json(out / "index.json", document)
    return dict(summary, path=str(index_path))


def extract_meshes(store, out_dir):
    """Alias kept for the fixture interface's naming pattern."""
    return extract_from_store(store, out_dir)
