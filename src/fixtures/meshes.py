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
still report a green run.  A skinned renderer's joint influences, bind pose and
bone list are exported too, through the same ``core.mesh`` reader every other
domain uses: without them the package is drawn in its bind pose, and the bind
pose is not what the prefab is authored at.  Measured over those 111 packages,
22 carry at least one bone whose authored pose differs from the bind pose, and
in those the difference reaches 0.34 authored units.

The AnimationClips each package ships are exported as glTF ``animations`` over
these same node trees (:mod:`fixtures.animations`) -- position / rotation /
scale curves become channels + samplers targeting the walked nodes, in this
glb's authored frame, so a furniture performance can play without a sidecar.
"""
import io
import json
import math
import os
import struct
from pathlib import Path

import UnityPy
from core.unity import configure_fallback_unity_version
from UnityPy.helpers.MeshHelper import MeshHandler

from core.assets.packages import PackageStore
from core.gltf import GLB
from core.jsonio import write_json
from core.renderer import shadow_casting_mode_or_gap
from core.shader_passes import declared_passes, texture_defaults
from core.mesh import (FLOAT, UNSIGNED_INT, ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER,
                       TRIANGLES, NOT_TRIANGLES, INDEX_RANGE, compose_mesh,
                       skin_accessors)
from fixtures.animations import embed
from sites.geometry import Graph
from sites.source_nodes import identity as source_identity

configure_fallback_unity_version()

FIXTURE_PREFIX = "mysekai__fixture__"
TRANSFORMS = ("Transform", "RectTransform")
UNSIGNED_BYTE = 5121

MISSING_BUNDLE = "bundle file not found; it was not opened"
NO_RENDERER = "a transform carries no MeshRenderer or SkinnedMeshRenderer"
UNREADABLE_MESH = "mesh vertex data could not be read"
NOT_TRIANGLE_LIST = "a submesh is not a triangle list"
TEXTURE_DECODE_FAILED = "texture could not be decoded to PNG"
MATERIAL_UNRESOLVED = "a renderer names a material that does not resolve here"

NO_SKIN_DATA = ("the renderer is skinned but its mesh carries no bone weights or "
                "no bind pose, so it is exported as plain geometry")
BONES_DISAGREE = ("the renderer's bone list and the mesh's bind pose disagree on "
                  "how many joints there are, so no skin is written rather than "
                  "a mismatched one")
BONES_OUTSIDE_ROOT = ("the renderer names bones outside the prefab variant it "
                      "hangs under, so their glTF nodes are not in this scene "
                      "and no skin is written")

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


def _mesh_channels(glb, mesh_obj, tree, skin=True):
    """Write one mesh's vertex buffers into *glb*, verbatim, for composing.

    Returns the ``buffers`` dict that :func:`core.mesh.compose_mesh` consumes:
    ``{"attributes", "submeshes", "name", "vertices", "triangles", "skin"}``.
    Unlike ``core.mesh.mesh_accessors`` this does not reflect the X axis,
    reorder winding or flip UVs — the surrounding node transforms are exported
    verbatim, so the geometry is kept in the same (authored) space.  Colors are
    written as the UNorm8 bytes they are authored in.

    ``skin`` comes from ``core.mesh.skin_accessors`` with the reflection off, for
    the same reason: an inverse bind matrix is read against the space the node
    transforms are written in, and here that space is the authored one.  It is
    ``None`` for a mesh with no bone weights or no bind pose.
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
            "submeshes": submeshes, "vertices": count, "triangles": triangles,
            "skin": skin_accessors(glb, handler, tree, reflect=False)
                    if skin else None}


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
    from core.gltf import unity_sampler
    settings = tex.m_TextureSettings
    sampler = unity_sampler(settings.m_FilterMode, settings.m_WrapU,
                            settings.m_WrapV, tex.m_MipCount)
    if sampler not in glb.g["samplers"]:
        glb.g["samplers"].append(sampler)
    sampler_index = glb.g["samplers"].index(sampler)
    glb.g["images"].append({"bufferView": view, "mimeType": "image/png",
                            "name": tex.m_Name})
    glb.g["textures"].append({"sampler": sampler_index, "source": len(glb.g["images"]) - 1,
                            "name": tex.m_Name,
                            "extras": {"mipCount": tex.m_MipCount,
                                       "anisotropy": settings.m_Aniso,
                                       "mipBias": settings.m_MipBias}})
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
    shader = {"status": "resolved", "name": shader_name,
              "textureDefaults": texture_defaults(parsed)}
    passes = declared_passes(parsed)
    if passes:
        shader["shaderPasses"] = passes
        shader["lightModes"] = [item["lightMode"] for item in passes]
    return shader


def _shader_keywords(tree):
    """A material's enabled shader-keyword state, serialized arrays and parsed.

    Unity 2021.2 no longer serializes a single ``m_ShaderKeywords`` field: the
    enabled keyword set is split across ``m_ValidKeywords`` (keywords the
    material has enabled that its shader declares) and ``m_InvalidKeywords``
    (enabled but not declared by the shader -- tooling leftovers ride here).
    Measured over this game's fixture materials: neither key is ever absent
    and the legacy ``m_ShaderKeywords`` string does not exist, so the arrays
    *are* the serialized state and are exported verbatim -- an empty list is
    exported as ``[]`` because enabling no keyword is a state too, not a gap
    (a shadow-pass material legitimately enables nothing valid).

    ``shaderKeywords`` is the parsed form: the concatenation, valid first --
    what Unity's ``Material.shaderKeywords`` property hands back.  The name is
    deliberately not ``keywords``: the site and character domains export a
    ``keywords`` field that holds only the valid half, and one name meaning
    two things would let a consumer read the union as the valid set.
    """
    valid = [str(word) for word in tree.get("m_ValidKeywords") or []]
    invalid = [str(word) for word in tree.get("m_InvalidKeywords") or []]
    return {"validKeywords": valid, "invalidKeywords": invalid,
            "shaderKeywords": valid + invalid}


def _material_properties(tree, glb, record, tex_cache):
    """Every authored property of a material: floats, colors, texture slots.

    The three named properties this exporter used to keep -- cull mode, alpha
    clip, the fixture shader usage flag -- are three of about 324 that a fixture
    material carries (measured over a sample: 277 distinct float properties, 18
    colors, 29 texture slots).  A consumer handed only those three cannot drive
    the program the material names, however correctly it routes to it: the
    gradient and parallax groups of ``Mysekai/Fixture/Basic`` read
    ``_GradientMap`` / ``_GradientColor1`` / ``_ParallaxMap``, and the water
    surfaces read ``_FlowMap`` / ``_FoamTex`` / ``_WaterColor`` -- none of which
    were leaving this extractor at all.

    The shape follows the emoticon extractor's, so the two products describe a
    material the same way rather than each inventing a layout:

    * ``floats`` / ``colors`` keyed by the authored property name;
    * ``textures`` mapping a slot to the exported image index, or to ``None``
      when the slot names an image this package does not hold -- a slot whose
      image is elsewhere is still reported, because "the shader samples this
      slot" is a fact even when the bytes are not here;
    * ``textureScaleOffset`` for every slot, image or not, since the vertex
      stage reads the scale/offset pair through ``<name>_ST`` whether or not the
      image resolved.
    """
    props = tree.get("m_SavedProperties") or {}
    floats, colors, textures, scale_offset = {}, {}, {}, {}
    non_finite = {}
    for entry in props.get("m_Floats") or []:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            continue
        name, value = entry
        if not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            floats[str(name)] = number
        else:
            non_finite[str(name)] = repr(number)
    for entry in props.get("m_Colors") or []:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            continue
        name, value = entry
        if not isinstance(value, dict):
            continue
        channels, odd = [], {}
        for channel in "rgba":
            number = float(value.get(channel, 0.0))
            if math.isfinite(number):
                channels.append(number)
            else:
                # JSON has no infinity, and substituting a finite number would
                # invent a limit the author did not set -- `_CameraFadeParams`
                # uses an infinite channel to mean "no far fade".  So the
                # numeric list keeps a placeholder and the real value is stated
                # beside it, rather than the property being dropped or bent.
                channels.append(0.0)
                odd[channel] = repr(number)
        colors[str(name)] = channels
        if odd:
            non_finite[str(name)] = odd
    for entry in props.get("m_TexEnvs") or []:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            continue
        name, value = entry
        value = value or {}
        scale = value.get("m_Scale") or {}
        offset = value.get("m_Offset") or {}
        scale_offset[str(name)] = [
            float(scale.get("x", 1.0)),
            float(scale.get("y", 1.0)),
            float(offset.get("x", 0.0)),
            float(offset.get("y", 0.0))]
        path_id = (value.get("m_Texture") or {}).get("m_PathID", 0)
        if not path_id:
            continue
        # The slot is reported either way; None means the image is not in this
        # package, which is a different fact from the slot being unused.
        try:
            textures[str(name)] = _texture_index(glb, record, path_id, tex_cache)
        except ValueError:
            textures[str(name)] = None
    out = {"floats": floats, "colors": colors, "textures": textures,
           "textureScaleOffset": scale_offset}
    if non_finite:
        # Named, not dropped: a reader that needs the true value finds it here,
        # and a reader that only wants numbers is not handed an unusable one.
        out["nonFiniteProperties"] = non_finite
    return out


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
    extras.update(_shader_keywords(tt))
    extras.update(_material_properties(tt, glb, record, tex_cache))
    if shader["status"] == "resolved":
        extras["shaderTextureDefaults"] = shader["textureDefaults"]
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
    """The mesh pointer, material pointers and bones a renderer draws.

    Returns ``(mesh pointer, material pointers, bones, reason)``; ``bones`` is
    the ``(bone transform ids, root bone id)`` pair of a ``SkinnedMeshRenderer``
    and ``None`` for a plain one, and ``reason`` says why there is no mesh.
    """
    tree = _tree(record, component_id)
    if not tree:
        return None, [], None, NO_RENDERER
    goid = (tree.get("m_GameObject") or {}).get("m_PathID")
    if kind == "MeshRenderer":
        for cid, ckind in _components(record, goid):
            if ckind == "MeshFilter":
                mesh = (_tree(record, cid) or {}).get("m_Mesh") or {}
                return mesh, _materials(tree), None, None
        return None, [], None, NO_RENDERER
    if kind == "SkinnedMeshRenderer":
        bones = ([(bone or {}).get("m_PathID", 0)
                  for bone in tree.get("m_Bones") or []],
                 (tree.get("m_RootBone") or {}).get("m_PathID", 0))
        return (tree.get("m_Mesh") or {}), _materials(tree), bones, None
    return None, [], None, NO_RENDERER


def _materials(tree):
    """The material pointers a renderer's ``m_Materials`` names."""
    return list(tree.get("m_Materials") or [])


def _walk(glb, record, store, tpid, parent, ctx, prefix="", fence_scope=False):
    """Export one transform and its children, returning the glTF node index.

    *prefix* is the node's full path from its variant root -- the same path
    space the animation bindings hash, so the walk records every node under
    ``ctx["table"]["paths"]`` while it builds the tree and this module can
    resolve a clip's ``crc32(path)`` binding without a second pass.
    """
    tt = ctx["transforms"][tpid]
    goid = (tt.get("m_GameObject") or {}).get("m_PathID")
    name = str((ctx["gameobjects"].get(goid) or {}).get("m_Name", ""))
    full = f"{prefix}/{name}" if prefix else name
    translation, rotation, scale = _local_transform(tt)
    node = {"name": name, "translation": translation,
            "rotation": rotation, "scale": scale,
            "extras": {"sourcePathId": tpid, "gameObjectId": goid}}
    # Reuse the scene importer's exact object/component identity contract.
    # Legacy numeric extras remain for existing consumers; runtime animation
    # binding uses the lossless serialized-file + signed string identities.
    node["extras"].update(source_identity(record, ctx["source_graph"], tpid))
    if goid in ctx["roadCells"]:
        node["extras"]["roadCellType"] = ctx["roadCells"][goid]
    fence_scope = fence_scope or goid in ctx["fenceRoots"]
    if fence_scope:
        node["extras"]["fenceActive"] = bool(ctx["gameobjects"][goid]["m_IsActive"])
        if goid in ctx["fenceRoots"]:
            node["extras"]["fenceView"] = True
        if goid in ctx["fenceParts"]:
            node["extras"]["fencePart"] = ctx["fenceParts"][goid]
        renderers = [(_tree(record, cid) or {}) for cid, kind in _components(record, goid)
                     if kind in ("MeshRenderer", "SkinnedMeshRenderer")]
        if renderers:
            node["extras"]["fenceRendererEnabled"] = all(bool(r["m_Enabled"]) for r in renderers)
    index = len(glb.g["nodes"])
    glb.g["nodes"].append(node)
    ctx["nodeIndex"][tpid] = index
    ctx["table"]["paths"][full] = index
    ctx["report"]["nodeNames"].add(name)
    ctx["variantGoids"].add(goid)
    if goid in ctx["fixtureView"]:
        ctx["fvRoots"].add(ctx["root"])

    for cid, kind in _components(record, goid):
        if kind == "Animator":
            # An animation binding path is relative to the transform carrying
            # the Animator; this node becomes one of the variant's anchors.
            ctx["table"]["animators"].append(full)
            continue
        if kind not in ("MeshRenderer", "SkinnedMeshRenderer"):
            continue
        skinned = kind == "SkinnedMeshRenderer"
        # This belongs to the renderer instance, not the shared mesh/material.
        shadow_mode, shadow_gap = shadow_casting_mode_or_gap(_tree(record, cid))
        if shadow_gap is not None:
            ctx["report"]["anomalies"].append(
                {"type": "no-shadow-casting-mode", "node": name, "kind": kind,
                 "reason": shadow_gap})
        node["extras"]["shadowCastingMode"] = shadow_mode
        mesh_pointer, material_pointers, bones, reason = (
            _renderer_mesh_and_materials(record, cid, kind))
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
        mesh_file = mesh_record.archive or mesh_record.bundle
        # Materials are resolved before the cache is consulted because a glTF
        # mesh carries its materials: compose_mesh binds one material per
        # submesh into the primitives, so "which mesh" and "drawn with which
        # materials" name one glTF mesh together.  A Unity mesh that two
        # renderers draw with different materials therefore needs two glTF
        # meshes over the same geometry, and a cache key that omits the
        # materials hands every later renderer the first one's binding -- the
        # later renderer's own material pointers are then never read at all.
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
        # ``skinned`` joins the key because glTF binds the joint attributes into
        # the primitive: one Unity mesh drawn by both a plain and a skinned
        # renderer needs two glTF meshes, and a primitive carrying ``JOINTS_0``
        # on a node with no ``skin`` is invalid.  The mesh names its serialized
        # file as well, because a path id is unique only within its file -- the
        # material cache keys the same way -- and the resolved materials join
        # for the reason above.
        cache_key = (mesh_file, mesh_id, skinned,
                     tuple(sorted(materials.items())))
        if cache_key in ctx["mesh_cache"]:
            node["mesh"] = ctx["mesh_cache"][cache_key]
            if skinned:
                _plan_skin(ctx, node, index, name, bones,
                           ctx["skin_cache"].get((mesh_file, mesh_id)))
            continue
        # One set of vertex accessors per (mesh, skinned): a second material
        # combination composes a new mesh from the same accessors, which costs
        # a few bytes of JSON, instead of writing the whole vertex buffer into
        # the binary a second time.
        channel_key = (mesh_file, mesh_id, skinned)
        buffers = ctx["channel_cache"].get(channel_key)
        if buffers is None:
            mesh_obj = mesh_record.objects.get(mesh_id)
            if mesh_obj is None:
                ctx["report"]["anomalies"].append(
                    {"type": "mesh-unresolved", "node": name})
                continue
            try:
                mesh_tt = mesh_obj.read_typetree()
                buffers = _mesh_channels(glb, mesh_obj, mesh_tt, skin=skinned)
            except Exception:
                ctx["report"]["anomalies"].append(
                    {"type": "mesh-unreadable", "node": name, "kind": kind})
                continue
            ctx["channel_cache"][channel_key] = buffers
        mesh_index = compose_mesh(glb, buffers, None, materials,
                                  skinned and bool(buffers.get("skin")))
        ctx["mesh_cache"][cache_key] = mesh_index
        ctx["skin_cache"][(mesh_file, mesh_id)] = buffers.get("skin")
        node["mesh"] = mesh_index
        if skinned:
            _plan_skin(ctx, node, index, name, bones, buffers.get("skin"))
        ctx["report"]["vertexCount"] += buffers["vertices"]
        ctx["report"]["meshCount"] += 1
        if not buffers["vertices"]:
            ctx["report"]["zeroVertexMeshes"] += 1
        ctx["report"]["hasGeometry"] = True

    children = []
    for child in tt.get("m_Children") or []:
        child_id = (child or {}).get("m_PathID", 0)
        if child_id in ctx["transforms"]:
            children.append(_walk(glb, record, store, child_id, index, ctx,
                                  prefix=full, fence_scope=fence_scope))
    if children:
        node["children"] = children
    if parent is not None:
        glb.g["nodes"][parent].setdefault("children", []).append(index)
    return index


def _plan_skin(ctx, node, index, name, bones, skin):
    """Record what a skinned renderer needs before its joints can be nodes.

    The bone list is transform path ids, and the walk that turns those into glTF
    node indices has not finished when the renderer is read — a bone is very
    often a sibling the walk reaches later.  So the binding is planned here and
    closed by :func:`_bind_skins` once the variant's whole tree has nodes.  A
    renderer whose mesh has no skin data, or whose bone list and bind pose
    disagree on how many joints there are, is reported and left as plain
    geometry rather than bound to a skeleton that does not match it.
    """
    bone_ids, root_bone = bones or ([], 0)
    if skin is None:
        ctx["report"]["anomalies"].append(
            {"type": "skin-omitted", "node": name, "bones": len(bone_ids),
             "reason": NO_SKIN_DATA})
        return
    if len(bone_ids) != skin["joints"]:
        ctx["report"]["anomalies"].append(
            {"type": "skin-omitted", "node": name, "bones": len(bone_ids),
             "bindPoses": skin["joints"], "reason": BONES_DISAGREE})
        return
    ctx["pendingSkins"].append(
        {"node": index, "name": name, "bones": bone_ids, "rootBone": root_bone,
         "joints": skin["joints"], "influences": skin["influences"],
         "inverseBindMatrices": skin["inverseBindMatrices"]})


def _bind_skins(glb, ctx):
    """Turn each planned skin's bone list into glTF node indices.

    Runs after one variant's walk, when every transform of that variant has a
    node.  A bone naming a transform outside the variant cannot be a joint here,
    and that is reported rather than guessed at: the alternative is a skin whose
    joints silently point at the wrong nodes.  Returns how many skins were
    written for this variant.
    """
    written = 0
    for plan in ctx["pendingSkins"]:
        joints = [ctx["nodeIndex"].get(bone) for bone in plan["bones"]]
        missing = sum(1 for joint in joints if joint is None)
        if missing:
            ctx["report"]["anomalies"].append(
                {"type": "skin-omitted", "node": plan["name"],
                 "bones": len(plan["bones"]), "unresolvedBones": missing,
                 "reason": BONES_OUTSIDE_ROOT})
            continue
        entry = {"joints": joints,
                 "inverseBindMatrices": plan["inverseBindMatrices"]}
        skeleton = ctx["nodeIndex"].get(plan["rootBone"])
        if skeleton is not None:
            entry["skeleton"] = skeleton
        glb.g["skins"].append(entry)
        glb.g["nodes"][plan["node"]]["skin"] = len(glb.g["skins"]) - 1
        ctx["report"]["skinCount"] += 1
        ctx["report"]["jointCount"] += len(joints)
        written += 1
    ctx["pendingSkins"] = []
    return written


def _variants(glb, record, store, transforms, gameobjects, roots, ctx,
              container):
    """Export every root transform tree as one scene variant.

    Each variant also leaves behind a path table (``ctx["tables"]``) -- every
    node's full path to its glTF node index, plus the nodes carrying an
    Animator -- which :mod:`fixtures.animations` resolves clip bindings
    against.
    """
    root_nodes, variants = [], []
    for root in roots:
        root_go = gameobjects.get((transforms[root].get("m_GameObject")
                                   or {}).get("m_PathID"))
        root_name = str((root_go or {}).get("m_Name", ""))
        ctx["root"] = root
        ctx["variantGoids"] = set()
        ctx["nodeIndex"] = {}
        ctx["pendingSkins"] = []
        ctx["table"] = {"paths": {}, "root": root_name, "animators": []}
        before = ctx["report"]["nodeNames"].copy()
        node_index = _walk(glb, record, store, root, None, ctx)
        skins = _bind_skins(glb, ctx)
        ctx["tables"].append(ctx["table"])
        container_paths = [path for path, goid in container
                           if goid in ctx["variantGoids"]]
        variants.append({
            "rootNode": node_index,
            "rootName": root_name,
            "containerPaths": container_paths,
            "nodeCount": len(set(ctx["report"]["nodeNames"]) - before),
            "hasFixtureView": root in ctx["fvRoots"],
            "skins": skins,
        })
        root_nodes.append(node_index)
    return root_nodes, variants


def _road_cells(record):
    """RoadView cell roles resolved through RoadCell._renderer references."""
    cells = {}
    for cid, kind in record.kinds.items():
        if kind != "MonoBehaviour" or record.script_of(cid) != "RoadView":
            continue
        for entry in record.tree(cid)["_roadViewDataList"]:
            pointer = entry["_cell"]
            if pointer["m_FileID"] != 0:
                raise ValueError("RoadCell reference outside serialized file")
            cell = record.tree(pointer["m_PathID"])
            renderer = cell["_renderer"]
            if renderer["m_FileID"] != 0:
                raise ValueError("Road renderer reference outside serialized file")
            goid = record.tree(renderer["m_PathID"])["m_GameObject"]["m_PathID"]
            cells[goid] = entry["_cellType"]
    return cells


def _fence_views(record):
    """Fence part enums follow serialized GameObject references, never names."""
    roots, parts = set(), {}
    for cid, kind in record.kinds.items():
        if kind != "MonoBehaviour" or record.script_of(cid) != "FenceView":
            continue
        tree = record.tree(cid)
        roots.add(tree["m_GameObject"]["m_PathID"])
        for key, enum_key, role in [("_poleViewDataList", "_poleType", "pole"),
                                     ("_wingViewDataList", "_wingType", "wing")]:
            for row in tree[key]:
                view = row["_view"]
                if view["m_FileID"] != 0 or view["m_PathID"] not in record.kinds:
                    raise ValueError("FenceView part reference is outside its serialized file")
                parts[view["m_PathID"]] = {"kind": role, "type": row[enum_key]}
    return roots, parts


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
              "skinCount": 0, "jointCount": 0, "anomalies": [],
              "animations": []}
    ctx = {"fixtureView": set(), "fvRoots": set(), "root": None,
           "variantGoids": set(), "mesh_cache": {}, "material_cache": {},
           "tex_cache": {}, "skin_cache": {}, "channel_cache": {},
           "nodeIndex": {}, "pendingSkins": [], "tables": [], "table": None,
           "report": report}
    variants, root_nodes, container = [], [], []
    for record in package.files:
        if not record.kinds:
            continue
        transforms, gameobjects, roots = _graph(record)
        ctx["source_graph"] = Graph(record)
        ctx["transforms"] = transforms
        ctx["gameobjects"] = gameobjects
        ctx["fenceRoots"], ctx["fenceParts"] = _fence_views(record)
        ctx["roadCells"] = _road_cells(record)
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

    # The AnimationClips this package ships become glTF animations over the
    # node trees just written -- embedded, not a sidecar (see
    # :mod:`fixtures.animations`).  A clip failure must not lose the geometry,
    # so the embed reports per-clip problems instead of raising; they join the
    # package's anomaly list so the run summary aggregates them like any other.
    animations = embed(glb, package, ctx["tables"], report)
    report["anomalies"].extend(animations["anomalies"])
    animations["anomalies"] = []

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
    from .house_views import read as read_house_views
    house_views = read_house_views(store, package)
    if house_views["views"]:
        glb.g.setdefault("extras", {})["houseViews"] = house_views
        owners = {(view["gameObject"]["file"], view["gameObject"]["pathId"])
                  for view in house_views["views"]}
        house_scenes = []
        for scene_index, variant in enumerate(variants):
            pending = [variant["rootNode"]]
            while pending:
                node = glb.g["nodes"][pending.pop()]
                source = node.get("extras", {}).get("sourceObject", {})
                if (source.get("file"), source.get("gameObjectId")) in owners:
                    house_scenes.append(scene_index)
                    break
                pending.extend(node.get("children", []))
        if len(house_scenes) != 1:
            raise ValueError("HouseView must resolve to one exported prefab scene")
        glb.g["scene"] = house_scenes[0]
    path = out_dir / f"{name}.glb"
    try:
        glb.save(path)
    except Exception:
        # The writer opens the file before it serialises, so a serialisation
        # failure leaves a zero-byte .glb behind -- and a zero-byte file passes
        # every check that only lists names.  Measured once: a material carried
        # an infinite colour channel, the JSON writer refused it (correctly),
        # and four packages were left as empty files while the run's own counts
        # reported them failed.  The counts were honest; the files were the
        # trap.  So the partial file goes, and the failure propagates.
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
        raise
    document = {
        "name": name,
        "status": "exported" if report["hasGeometry"] else "no-mesh",
        "glb": path.name,
        "roots": len(root_nodes),
        "variants": variants,
        "meshCount": report["meshCount"],
        "vertexCount": report["vertexCount"],
        "zeroVertexMeshes": report["zeroVertexMeshes"],
        "skinCount": report["skinCount"],
        "jointCount": report["jointCount"],
        "animations": dict(animations, clips=report["animations"]),
        "hasFixtureView": bool(ctx["fixtureView"]),
        "houseViews": house_views,
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
    skins = joints = 0
    skinned_packages = 0
    animated_packages = 0
    animation_clips = 0
    animation_channels = 0
    animation_float_slots = 0
    animation_unresolved_slots = 0
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
        skins += document.get("skinCount", 0)
        joints += document.get("jointCount", 0)
        if document.get("skinCount", 0):
            skinned_packages += 1
        animations = document.get("animations") or {}
        if animations.get("clipCount"):
            animated_packages += 1
            animation_clips += animations["clipCount"]
            animation_channels += animations.get("gltfChannels", 0)
            animation_float_slots += animations.get("floatSlots", 0)
            animation_unresolved_slots += animations.get("unresolvedSlots", 0)
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
        "skins": skins,
        "skinJoints": joints,
        "skinnedPackages": skinned_packages,
        "animatedPackages": animated_packages,
        "animationClips": animation_clips,
        "animationChannels": animation_channels,
        "animationFloatSlots": animation_float_slots,
        "animationUnresolvedSlots": animation_unresolved_slots,
        "anomalies": anomalies_by_type,
        "noMeshNames": resolution,
        "failedNames": failures,
    }
    document = {"version": 2, "summary": summary, "packages": packages}
    index_path = write_json(out / "index.json", document)
    return dict(summary, path=str(index_path))


def extract_meshes(store, out_dir):
    """Alias kept for the fixture interface's naming pattern."""
    return extract_from_store(store, out_dir)
