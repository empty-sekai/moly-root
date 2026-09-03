"""Player avatar (audience rig) skeleton + motion export.

The player avatar reuses the virtual-live concert audience rig -- the model,
skeleton and base mesh in ``audience.prefab``/``audienceAvatar`` under
``virtual_live/avatar/model/default`` -- and plays clips from two separate
motion bundles on top of it: ``virtual_live/avatar/motion`` (the shared base
movement/gesture set) and ``mysekai/player/motion/unique`` (site-interaction
and home clips specific to this mode). Every AnimationClip in either motion
bundle binds to a transform by CRC32 hash of its path *from the animator
root* (see ``perf.animations.SEMANTICS``), never by a readable string, so the
hash table can only be built by walking the real scene hierarchy -- which
lives only in the model bundle.

``UnityPy.load(*paths)`` merges any number of bundle files into one
``Environment``, and ``perf.animations.NodeHierarchy`` builds its transform
forest and CRC32 table purely from the ``Transform``/``RectTransform``/
``GameObject``/``Animator`` objects handed to it, independent of which single
file they came from. Loading the model bundle together with both motion
bundles therefore gives ``NodeHierarchy`` the real audience skeleton once, and
clips in either motion bundle resolve against it directly, with no
foreign-rig fallback constructed. Not every clip binds fully against this one
skeleton: a subset of ``virtual_live/avatar/motion``'s clips carry transform
bindings whose CRC32 hash matches no path in the audience hierarchy at all
(``bindingCoverage`` in the exported record reports this exactly, per clip
via ``curveAccounting``); every clip in ``mysekai/player/motion/unique``
binds fully.

This module reuses (does not reimplement) the decode/export internals of
``perf.animations``: ``NodeHierarchy``, ``decode_clip``, ``_add_animation``,
``curve_accounting``, ``CLASS_GLTF``. New logic here is the multi-bundle
load and, per clip: ``container`` and ``sourcePackage`` (read directly off
each Unity object, not looked up), ``family`` (the container path's folder
segment, where the source package has one), ``guessedFamily`` (guessed from
the clip name prefix alone, for packages that ship no folder to read), and
``phaseBase``/``phase`` (the ``_S``/``_L``/``_E``/``_O`` suffix split used by
``examples/viewer/segments.js``). A single-package export has no notion of
any of these, since its caller already knows its own package's layout.
"""
import collections
import io
import os
import re

import UnityPy

from core.gltf import GLB
from core.jsonio import write_json
from core.mesh import compose_mesh, mesh_accessors
from perf.animations import (
    CLASS_GLTF,
    CLASS_NO_BINDING,
    CLASS_NO_NODE,
    CLASS_UNRESOLVED,
    FORMAT_VERSION,
    SEMANTICS,
    NodeHierarchy,
    _add_animation,
    curve_accounting,
    decode_clip,
)
from .emoticons import _material, _pairs, _shader_name

# examples/viewer/segments.js:3-5 (accepted demo, not a true-source citation):
# "The public clip convention uses _S, _L, _E, and _O suffixes for Start,
# Loop, End, and OneShot." Same regex as that file's ``splitName``.
_PHASE_RE = re.compile(r"^(.*)_(S|L|E|O)$")
_PHASE_PROVENANCE = ("examples/viewer/segments.js:3-5 (demo convention, "
                     "not confirmed against a true source)")


def _phase_of(clip_name):
    m = _PHASE_RE.match(clip_name)
    if not m:
        return clip_name, None
    return m.group(1), m.group(2)


def group_phases(names):
    """Group clip names into S/L/E/O families, same grouping as
    ``examples/viewer/segments.js``'s ``groupClips`` (``_PHASE_PROVENANCE``
    applies: this grouping is a demo convention, not a true-source fact).
    """
    fams = {}
    for n in names:
        base, phase = _phase_of(n)
        fam = fams.setdefault(base, {"base": base, "segs": {}, "plain": None})
        if phase:
            fam["segs"][phase] = n
        else:
            fam["plain"] = n
    return list(fams.values())


# Family guessed from the clip name alone (NOT read from a container path) --
# only usable as a cross-check on packages shipping their clips flat, never
# as a substitute for the container-derived family where one is available.
_GUESS_RULES = (
    ("harvest", ("site_",)),
    ("conversation", ("hou_",)),
    ("home", ("myroom_", "fixture_", "house_open", "other_house")),
    ("c_000", ("c_000_",)),
)


def guessed_family_from_name(clip_name):
    for family, needles in _GUESS_RULES:
        if any(needle in clip_name for needle in needles):
            return family
    return "other"


def _container_family(container, source_package):
    """Domain family read from the container path folder, never guessed.

    For ``mysekai/player/motion/unique`` the folder right after ``unique/``
    is the family (``harvest``, ``conversation``, ``home``, ``common``,
    verified 2026-08-29 to enumerate exactly those four for this package).
    ``virtual_live/avatar/motion`` ships its clips flat -- no folder to read,
    so the family is reported as ``"(flat)"`` rather than inferred from the
    name.
    """
    if not container:
        return None
    parts = container.split("/")
    if "unique" in parts:
        idx = parts.index("unique")
        if idx + 1 < len(parts) - 1:
            return parts[idx + 1]
    return "(flat)"


def _clip_frame_info(tree):
    """Best-effort (sampleRate, durationSeconds, frameCount) from a clip typetree.

    ``m_SampleRate`` and ``m_MuscleClip.m_StopTime``/``m_StartTime`` are plain
    typetree fields (verified against the reference package: idle clip reads
    m_SampleRate=60.0, m_StopTime=0.8333333730697632, matching its recorded
    frameCount=51 via round(0.8333333730697632*60)+1 == 51).  A clip lacking
    ``m_MuscleClip`` (none observed in this corpus, but not guaranteed) yields
    ``None`` for duration/frameCount rather than a guessed number.
    """
    sample_rate = tree.get("m_SampleRate")
    muscle = tree.get("m_MuscleClip") or {}
    stop_time = muscle.get("m_StopTime")
    start_time = muscle.get("m_StartTime", 0.0)
    duration = frame_count = None
    if stop_time is not None and sample_rate:
        duration = stop_time - start_time
        frame_count = round(duration * sample_rate) + 1
    return sample_rate, duration, frame_count


# --- player body mesh (SkinnedMeshRenderer) --------------------------------
#
# The model bundle carries the body mesh twice: once under the instantiated
# ``audience.prefab`` and once under the inert ``fbx/audience.fbx`` import
# scaffold -- the same duplicate-tree pattern ``chara.characters`` documents
# for NPC bundles. ``characters.read_scene`` tells the two trees apart by
# which root GameObject carries script components (``has_scripts()``); this
# bundle carries zero MonoBehaviour objects (probed 2026-09-03: a type count
# of ``list(env.objects)`` for ``virtual_live__avatar__model__default``), so
# the same "pick the instantiated tree, not the import scaffold" principle is
# applied through ``env.container`` instead, mirroring
# ``avatar_parts._containers``: the instantiated root is the one registered
# under a path ending ``.prefab``.


def _game_object_names(objects):
    return {o.path_id: str(o.read_typetree().get("m_Name", "") or "")
            for o in objects if o.type.name == "GameObject"}


def _transform_trees(objects):
    # Same type filter as NodeHierarchy._read_objects's Transform branch, so
    # the full-path strings computed below line up with hierarchy.node_index.
    return {o.path_id: o.read_typetree()
            for o in objects if o.type.name in ("Transform", "RectTransform")}


def _walk_transforms(transform_trees, game_object_names):
    """Per Transform path_id: its "/"-joined GameObject-name path from the
    tree's own root (the same string ``perf.animations.NodeHierarchy``
    computes as a node's ``fullPath``), and that root's own Transform
    path_id.

    Verified 2026-09-02/03 against the audience rig: stripping
    ``hierarchy.anchors[0]`` from a path computed here and calling
    ``hierarchy.node_index()`` on the result resolves every one of a
    renderer's ``m_Bones`` entries and its own transform node to the exact
    indices ``NodeHierarchy`` assigned.
    """
    paths, roots = {}, {}

    def walk(path_id):
        if path_id in paths:
            return paths[path_id], roots[path_id]
        tree = transform_trees.get(path_id)
        if tree is None:
            paths[path_id] = paths.get(path_id, "")
            roots[path_id] = path_id
            return paths[path_id], roots[path_id]
        name = game_object_names.get(
            (tree.get("m_GameObject") or {}).get("m_PathID", 0), "")
        father = (tree.get("m_Father") or {}).get("m_PathID", 0)
        if father and father in transform_trees:
            prefix, root = walk(father)
            path = f"{prefix}/{name}" if prefix else name
        else:
            path, root = name, path_id
        paths[path_id], roots[path_id] = path, root
        return path, root

    for path_id in transform_trees:
        walk(path_id)
    return paths, roots


def _relative_to_anchor(full_path, anchor):
    """Strip *anchor* the way ``NodeHierarchy._build`` strips the primary
    anchor's ``fullPath`` from every node's ``fullPath`` to get its ``path``
    -- the string ``node_index`` looks up."""
    if not anchor:
        return full_path
    if full_path == anchor:
        return ""
    prefix = anchor + "/"
    if full_path.startswith(prefix):
        return full_path[len(prefix):]
    return full_path


def _container_of(env, objects):
    """Container path per registered path_id (mirrors
    ``avatar_parts._containers``, reimplemented locally since that module is
    out of scope for this change)."""
    by_pid = {}
    container = getattr(env, "container", None) or {}
    for path, obj in container.items():
        if obj is not None and getattr(obj, "path_id", None) is not None:
            by_pid[obj.path_id] = str(path)
    return by_pid


def _select_body_renderer(objects, container_of, transform_of_go, roots,
                          transform_trees):
    """The one SkinnedMeshRenderer to export, or ``None`` if this bundle
    carries none (the caller must not fabricate a skeleton attachment then).

    A single renderer needs no disambiguation. More than one (the audience
    rig's prefab/fbx duplicate-tree pattern): keep only renderers whose
    ultimate root GameObject is registered under a ``.prefab`` container path
    -- the instantiated tree, never the import scaffold -- and fail loudly
    rather than guess if that does not land on exactly one.
    """
    renderers = [o for o in objects if o.type.name == "SkinnedMeshRenderer"]
    if not renderers:
        return None
    if len(renderers) == 1:
        return renderers[0]
    candidates = []
    for o in renderers:
        tree = o.read_typetree()
        go_pid = (tree.get("m_GameObject") or {}).get("m_PathID", 0)
        transform_pid = transform_of_go.get(go_pid)
        root_pid = roots.get(transform_pid) if transform_pid is not None else None
        root_go = ((transform_trees.get(root_pid) or {}).get("m_GameObject") or {}
                  ).get("m_PathID", 0)
        path = container_of.get(root_go)
        if path and path.endswith(".prefab"):
            candidates.append(o)
    if len(candidates) != 1:
        raise ValueError(
            "expected exactly one prefab-rooted SkinnedMeshRenderer among "
            f"{len(renderers)} found, got {len(candidates)} via container path")
    return candidates[0]


def _same_file(pointer):
    """Resolve a same-file PPtr's path_id, raising on a cross-file one.

    Every pointer this module resolves (mesh, material, texture slots,
    shader) was confirmed 2026-09-02/03 to carry ``m_FileID == 0`` in the
    player-avatar model bundle; a cross-file pointer raises rather than
    silently returning ``None``, so a bundle that does carry one is not
    silently mishandled.
    """
    path_id = int((pointer or {}).get("m_PathID", 0) or 0)
    if not path_id:
        return None
    file_id = int((pointer or {}).get("m_FileID", 0) or 0)
    if file_id:
        raise ValueError(f"cross-file pointer (m_FileID={file_id}) is not "
                         "supported for the player body mesh")
    return path_id


def read_player_mesh(env, glb, hierarchy):
    """The player body's skinned mesh, material and textures -> *glb*.

    Returns ``None`` when this bundle carries no ``SkinnedMeshRenderer`` (a
    motion-only bundle) -- callers must not fabricate a skin for it.
    Otherwise ``{"nodePath", "meshIndex", "skinIndex", "joints",
    "vertexCount", "materialName", "textures"}``: ``nodePath`` is the
    hierarchy-relative path of the renderer's own node, which the caller
    resolves through ``hierarchy.node_index()`` -- the caller must not
    mutate ``glb.g["nodes"]`` until after ``hierarchy.write_scene`` has
    populated them.
    """
    objects = list(env.objects)
    game_object_names = _game_object_names(objects)
    transform_trees = _transform_trees(objects)
    paths, roots = _walk_transforms(transform_trees, game_object_names)
    transform_of_go = {}
    for pid, tree in transform_trees.items():
        go_pid = (tree.get("m_GameObject") or {}).get("m_PathID", 0)
        if go_pid:
            transform_of_go[go_pid] = pid
    container_of = _container_of(env, objects)

    renderer = _select_body_renderer(objects, container_of, transform_of_go,
                                     roots, transform_trees)
    if renderer is None:
        return None
    renderer_tree = renderer.read_typetree()
    go_pid = (renderer_tree.get("m_GameObject") or {}).get("m_PathID", 0)
    renderer_transform_pid = transform_of_go.get(go_pid)
    if renderer_transform_pid is None:
        raise ValueError("SkinnedMeshRenderer's GameObject has no Transform")
    node_full_path = paths.get(renderer_transform_pid)
    if node_full_path is None:
        raise ValueError("SkinnedMeshRenderer's Transform is missing from "
                         "this bundle's Transform set")
    anchor = hierarchy.anchors[0] if hierarchy.anchors else ""
    node_rel_path = _relative_to_anchor(node_full_path, anchor)

    assets_file = renderer.assets_file

    def deref(pointer):
        # Same convention as CharacterAssets.deref's m_FileID==0 branch:
        # direct indexing, not .get() -- a same-file pointer that does not
        # resolve is a corrupt bundle, not an absent reference.
        path_id = _same_file(pointer)
        if path_id is None:
            return None
        return assets_file.objects[path_id]

    mesh_obj = deref(renderer_tree.get("m_Mesh"))
    if mesh_obj is None:
        raise ValueError("SkinnedMeshRenderer without a resolvable mesh")
    mesh_tree = mesh_obj.read_typetree()

    material_ptrs = renderer_tree.get("m_Materials") or []
    if not material_ptrs:
        raise ValueError("SkinnedMeshRenderer without a material")
    material_obj = deref(material_ptrs[0])
    if material_obj is None:
        raise ValueError("SkinnedMeshRenderer with an unresolvable material")
    material_tree = material_obj.read_typetree()

    shader_trees = {o.path_id: o.read_typetree() for o in objects
                    if o.type.name == "Shader" and o.assets_file is assets_file}
    shader_name = _shader_name(material_tree, shader_trees)

    texture_files, texture_index = {}, {}
    for _slot, value in _pairs((material_tree.get("m_SavedProperties") or {})
                               .get("m_TexEnvs")):
        tex_obj = deref((value or {}).get("m_Texture"))
        if tex_obj is None:
            continue
        pid = tex_obj.path_id
        if pid in texture_files:
            continue
        tex_name = str(tex_obj.read_typetree().get("m_Name") or f"tex_{pid}")
        # Keyed by the texture's own bare name (not a package-qualified file
        # name), matching what _material() below writes into a material's
        # "textures" slot -- the same string is looked up against
        # texture_index for the baseColorTexture binding.
        texture_files[pid] = tex_name
        buf = io.BytesIO()
        tex_obj.read().image.convert("RGBA").save(buf, format="PNG",
                                                   optimize=True)
        vi = glb.view(buf.getvalue())
        glb.g["images"].append({"bufferView": vi, "mimeType": "image/png",
                                "name": tex_name})
        glb.g["textures"].append({"sampler": 0,
                                  "source": len(glb.g["images"]) - 1,
                                  "name": tex_name})
        texture_index[tex_name] = len(glb.g["textures"]) - 1

    entry = _material(material_tree, texture_files, shader=shader_name)
    main = entry["textures"].get("_MainTex")
    gltf_material = {"name": entry["name"] or "material",
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
        gltf_material["pbrMetallicRoughness"]["baseColorTexture"] = {
            "index": texture_index[main]}
    glb.g["materials"].append(gltf_material)
    material_index = len(glb.g["materials"]) - 1

    buffers = mesh_accessors(glb, mesh_obj, mesh_tree, skin=True)
    if buffers["skin"] is None:
        # The negative-path guard: a SkinnedMeshRenderer whose mesh carries
        # no bind pose would otherwise silently be composed as an unskinned
        # mesh instead.
        raise ValueError("SkinnedMeshRenderer's mesh carries no bind pose "
                         "(m_BindPose empty or no bone indices)")
    mesh_index = compose_mesh(glb, buffers, name=mesh_tree.get("m_Name"),
                              materials={0: material_index}, skinned=True)

    joints = []
    for pointer in renderer_tree.get("m_Bones") or []:
        bone_pid = (pointer or {}).get("m_PathID", 0)
        bone_path = paths.get(bone_pid)
        if bone_path is None:
            raise ValueError(f"bone transform {bone_pid} is missing from "
                             "this bundle's Transform set")
        rel = _relative_to_anchor(bone_path, anchor)
        idx = hierarchy.node_index(rel)
        if idx is None:
            raise ValueError(f"bone path {rel!r} does not resolve to an "
                             "exported hierarchy node")
        joints.append(idx)
    if len(set(joints)) != len(joints):
        raise ValueError("resolved joint node indices are not distinct")

    root_bone_pid = (renderer_tree.get("m_RootBone") or {}).get("m_PathID", 0)
    root_bone_path = paths.get(root_bone_pid)
    skeleton_index = None
    if root_bone_path is not None:
        skeleton_index = hierarchy.node_index(
            _relative_to_anchor(root_bone_path, anchor))
    if skeleton_index is None:
        skeleton_index = joints[0] if joints else None

    skin_index = len(glb.g["skins"])
    glb.g["skins"].append({
        "joints": joints,
        "inverseBindMatrices": buffers["skin"]["inverseBindMatrices"],
        "skeleton": skeleton_index})

    return {"nodePath": node_rel_path, "meshIndex": mesh_index,
            "skinIndex": skin_index, "joints": joints,
            "vertexCount": buffers["vertices"],
            "materialName": entry["name"], "textures": sorted(texture_index)}


def _scene_roots(hierarchy, root_indexes, body_mesh):
    """Scene roots of the instantiated tree, never the import scaffold.

    The model bundle carries the body transform tree twice (see the module
    comment above ``_select_body_renderer``): the instantiated ``.prefab``
    tree and the inert ``fbx`` import scaffold, 22 nodes each with identical
    node names, so ``NodeHierarchy``'s forest -- and ``write_scene``'s
    returned roots -- cover both, and the default scene would present the
    scaffold as a second, unreferenced-by-anything skeleton.  This applies
    the same "instantiated, not scaffold" rule to the scene graph through
    its outcome: the renderer ``_select_body_renderer`` picked (by the
    ``.prefab`` container rule) lives in the instantiated tree, so the root
    its parent chain climbs to is the tree the skin, its joints and every
    resolved animation channel land in -- ``hierarchy.node_index`` keeps the
    first node per relative path, and both trees share relative paths, so
    all of them resolve into one and the same tree (verified 2026-09-03
    against the rerun record: mesh node 3, all 16 joint indices, and all
    5320 glTF channels inside the kept tree).

    Pruning is scene-level only: the scaffold's nodes stay in the node array
    (skin joints and animation channels reference array positions; removing
    the block would renumber them) and become unreachable from the scene,
    which is inert in glTF.  With no body renderer (a motion-only bundle
    set) or a single root there is nothing to disambiguate, so every root is
    kept; a body node that resolves to nothing or to a non-root tree raises
    rather than guessing.
    """
    if body_mesh is None or len(root_indexes) <= 1:
        return list(root_indexes)
    node = hierarchy.node_index(body_mesh["nodePath"])
    if node is None:
        raise ValueError(
            f"player body node {body_mesh['nodePath']!r} not found in the "
            "exported hierarchy; cannot tell the instantiated tree from "
            "the fbx import scaffold")
    while hierarchy.nodes[node]["parent"] >= 0:
        node = hierarchy.nodes[node]["parent"]
    if node not in root_indexes:
        raise ValueError(f"the body mesh's tree root {node} is not among "
                         f"the exported scene roots {root_indexes}")
    return [node]


def export_player_avatar(bundle_paths, out_dir, name="mysekai__player_avatar"):
    """Export the player (audience) skeleton + every AnimationClip found across
    *bundle_paths*, merged into one UnityPy ``Environment``.

    *bundle_paths* should include the model bundle
    (``virtual_live/avatar/model/default``) plus every motion bundle whose
    clips are to be resolved against it; order does not matter to
    ``NodeHierarchy`` since it reads the whole combined object list.

    Returns the package record (also written as ``<name>.index.json``),
    field-shape isomorphic to ``perf.animations.export_package``'s record
    (same top-level keys), with these additions:

    - ``sourcePackages``: basenames of *bundle_paths*, for provenance (a
      single-package record only ever needed a bare ``package`` name).
    - Each ``clipRecords`` entry additionally carries ``container`` (the
      bundle-internal asset path, e.g. ``.../harvest/mov_u000_site_ax01_e.anim``),
      ``harvest`` (``"/harvest/" in container``), ``sampleRate``,
      ``durationSeconds`` and ``frameCount`` -- none of which
      ``export_package`` records, since a single-package caller already knows
      its own package's naming convention.
    - ``counts.harvest``: how many exported clips have a harvest container.
    - ``playerMesh``: ``{"meshes", "skins", "vertexCount", "joints",
      "materialName", "textures"}`` for the body's ``SkinnedMeshRenderer``
      (see :func:`read_player_mesh`), or ``None`` when *bundle_paths* carries
      none (e.g. a motion-only bundle set).
    """
    for p in bundle_paths:
        assert os.path.exists(p), f"bundle path does not exist: {p}"
    env = UnityPy.load(*bundle_paths)
    hierarchy = NodeHierarchy(list(env.objects), foreign=None, prefer=name)
    glb = GLB(generator="moly-root player avatar")
    body_mesh = read_player_mesh(env, glb, hierarchy)

    clips, seen, duplicates = [], {}, []
    for obj in env.objects:
        if obj.type.name != "AnimationClip":
            continue
        # Per-object attributes (each object knows its own source file and
        # bundle-internal path directly; no reverse map over env.container
        # is needed): obj.container is the container path, and
        # obj.assets_file.parent.name is the bundle basename that produced
        # this object -- verified 2026-08-29 to match, per object, the
        # bundle passed to UnityPy.load for it.
        container = obj.container
        source_package = obj.assets_file.parent.name if obj.assets_file else None
        try:
            tree = obj.read_typetree()
        except Exception as exc:
            hierarchy.read_failures.append({
                "pathId": getattr(obj, "path_id", None), "type": "AnimationClip",
                "container": container, "sourcePackage": source_package,
                "reason": "typetree read failed", "detail": type(exc).__name__})
            continue
        clip_name = str(tree.get("m_Name", ""))
        if not clip_name:
            continue
        if clip_name in seen:
            duplicates.append({
                "clip": clip_name, "pathId": getattr(obj, "path_id", None),
                "container": container, "sourcePackage": source_package,
                "firstSourcePackage": seen[clip_name],
                "bindings": len(((tree.get("m_ClipBindingConstant") or {})
                                 .get("genericBindings") or [])),
                "reason": "duplicate clip name across merged bundles, first object kept"})
            continue
        seen[clip_name] = source_package
        sample_rate, duration, frame_count = _clip_frame_info(tree)
        harvest = bool(container and "/harvest/" in container)
        family = _container_family(container, source_package)
        base, phase = _phase_of(clip_name)
        common = {
            "container": container, "sourcePackage": source_package,
            "family": family, "guessedFamily": guessed_family_from_name(clip_name),
            "harvest": harvest,
            "phaseBase": base, "phase": phase,
            "sampleRate": sample_rate, "durationSeconds": duration,
            "frameCount": frame_count,
        }
        try:
            curves, channels, anomalies = decode_clip(tree, hierarchy)
            index = len(glb.g.get("animations", []))
            _add_animation(glb, clip_name, curves, channels, anomalies)
            accounting = curve_accounting(curves, channels)
            clips.append({
                "name": clip_name, "animation": index, "curves": len(curves),
                "keys": sum(len(c["times"]) for c in curves),
                "channels": len(curves), "gltfChannels": len(channels),
                "curveAccounting": accounting, "anomalies": anomalies,
                **common,
            })
        except Exception as exc:                     # malformed curve block
            clips.append({
                "name": clip_name, "animation": None, "curves": 0, "keys": 0,
                "channels": 0, "gltfChannels": 0, "curveAccounting": {},
                "anomalies": [{"clip": clip_name, "reason": "clip decode failed",
                               "detail": f"{type(exc).__name__}: {exc}"}],
                **common,
            })

    roots = hierarchy.write_scene(glb)
    glb.g["scenes"][0]["nodes"] = _scene_roots(hierarchy, roots, body_mesh)
    if body_mesh is not None:
        body_node_index = hierarchy.node_index(body_mesh["nodePath"])
        if body_node_index is None:
            raise ValueError(
                f"player body node {body_mesh['nodePath']!r} not found in "
                "the exported hierarchy after write_scene")
        glb.g["nodes"][body_node_index]["mesh"] = body_mesh["meshIndex"]
        glb.g["nodes"][body_node_index]["skin"] = body_mesh["skinIndex"]
    if hierarchy.foreign_roots:
        glb.g["scenes"].append({"name": "foreign-rigs",
                                "nodes": list(hierarchy.foreign_roots)})
    foreign_counts = hierarchy.foreign_counts()
    sources = hierarchy.resolution_sources()
    accounting = collections.Counter()
    for c in clips:
        accounting.update(c.get("curveAccounting") or {})

    # Binding-coverage guard (denominator excludes non-Transform typeIDs and
    # CLASS_NO_BINDING slots, since those were never candidates for a
    # transform-path resolution in the first place; "resolved == denominator"
    # is deliberately not asserted alone -- see denominator > 0 below):
    non_transform = sum(v for k, v in accounting.items()
                        if k == CLASS_NO_BINDING or k.startswith("typeid-"))
    denominator = sum(accounting.values()) - non_transform
    unresolved = accounting.get(CLASS_UNRESOLVED, 0) + accounting.get(CLASS_NO_NODE, 0)
    resolved = denominator - unresolved
    binding_coverage = {
        "denominator": denominator, "resolved": resolved, "unresolved": unresolved,
        "nonTransformSlots": non_transform,
        "assertion": "denominator > 0 and unresolved == 0",
        "assertionHolds": bool(denominator > 0 and unresolved == 0),
    }

    glb.g["asset"]["extras"] = {"binding": SEMANTICS,
                                "formatVersion": FORMAT_VERSION,
                                "animatorPath": hierarchy.animator_path,
                                "anchors": hierarchy.anchors,
                                "foreignResolved": foreign_counts,
                                "resolutionSources": sources}
    os.makedirs(out_dir, exist_ok=True)
    glb_path = os.path.join(out_dir, f"{name}.glb")
    index_path = os.path.join(out_dir, f"{name}.index.json")
    glb.save(glb_path)
    for c in clips:
        for a in c.get("anomalies", []):
            a.setdefault("clip", c["name"])
    record = {
        "version": 1, "formatVersion": FORMAT_VERSION, "binding": SEMANTICS,
        "package": name,
        "sourcePackages": [os.path.basename(p) for p in bundle_paths],
        "animatorPath": hierarchy.animator_path,
        "anchors": hierarchy.anchors,
        "nodes": len(hierarchy.nodes),
        "nodeCount": len(hierarchy.nodes),
        "nativeNodeCount": hierarchy.nativeNodes,
        "foreignResolved": foreign_counts,
        "resolutionSources": sources,
        "curveAccounting": {"classes": dict(sorted(accounting.items())),
                            "total": sum(accounting.values()),
                            "gltfChannelSlots": accounting.get(CLASS_GLTF, 0),
                            "residual": sum(c["channels"] for c in clips)
                                        - sum(accounting.values())},
        "foreignHashes": {str(h): v for h, v in
                          sorted(hierarchy.foreign_hits.items())},
        "readFailures": hierarchy.read_failures,
        "bindingCoverage": binding_coverage,
        "playerMesh": ({"meshes": 1, "skins": 1,
                       "vertexCount": body_mesh["vertexCount"],
                       "joints": len(body_mesh["joints"]),
                       "materialName": body_mesh["materialName"],
                       "textures": body_mesh["textures"]}
                      if body_mesh is not None else None),
        "clips": {c["name"]: c["channels"] for c in clips},
        "clipRecords": clips,
        "anomalies": [a for c in clips for a in c["anomalies"]] +
                     list(hierarchy.read_failures) + duplicates,
        "duplicateNames": duplicates,
        "missingWanted": [],
        "exported": sum(1 for c in clips if c["channels"] > 0),
        "gltfExported": sum(1 for c in clips if c["gltfChannels"] > 0),
        "channeled": sum(c["channels"] for c in clips),
        "gltfChanneled": sum(c["gltfChannels"] for c in clips),
        "counts": {"discovered": len(seen),
                   "exported": sum(1 for c in clips if c["channels"] > 0),
                   "gltfExported": sum(1 for c in clips if c["gltfChannels"] > 0),
                   "channeled": sum(c["channels"] for c in clips),
                   "duplicateNames": len(duplicates),
                   "anomaly": sum(len(c["anomalies"]) for c in clips) +
                              len(duplicates),
                   "harvest": sum(1 for c in clips if c.get("harvest")),
                   "byContainerFamily": dict(sorted(collections.Counter(
                       c.get("family") for c in clips).items(),
                       key=lambda kv: (kv[0] is None, kv[0]))),
                   "byGuessedFamily": dict(sorted(collections.Counter(
                       c.get("guessedFamily") for c in clips).items()))},
        "glb": glb_path, "index": index_path,
        "glbBytes": os.path.getsize(glb_path),
    }
    write_json(index_path, record)
    return record


def write_motion_manifest(record, manifest_path):
    """Write the flat name/container/family/phase manifest.

    One row per exported clip (``clipRecords``), sorted by name, plus two
    family breakdowns and a phase-segment grouping.

    - ``family``: read from the clip's container path folder (``None``/
      ``"(flat)"`` where the package ships no folder to read).
    - ``guessedFamily``: guessed from the clip name prefix alone (see
      ``guessed_family_from_name``) -- a cross-check, not a substitute.
    - ``phaseFamilies``: clips grouped by the ``_S``/``_L``/_E``/``_O``
      suffix convention documented at ``examples/viewer/segments.js:3-5``
      (demo convention, not confirmed against a true source) -- each entry
      lists which of S/L/E/O/plain exist for one base clip name.
    """
    rows = []
    for c in record["clipRecords"]:
        rows.append({
            "name": c["name"],
            "container": c.get("container"),
            "sourcePackage": c.get("sourcePackage"),
            "family": c.get("family"),
            "guessedFamily": c.get("guessedFamily"),
            "phaseBase": c.get("phaseBase"),
            "phase": c.get("phase"),
            "harvest": c.get("harvest", False),
            "frameCount": c.get("frameCount"),
            "sampleRate": c.get("sampleRate"),
            "durationSeconds": c.get("durationSeconds"),
            "channels": c.get("channels"),
            "gltfChannels": c.get("gltfChannels"),
        })
    rows.sort(key=lambda r: r["name"])
    doc = {
        "count": len(rows),
        "harvestCount": sum(1 for r in rows if r["harvest"]),
        "familyCounts": {"method": "read from container path folder",
                         "counts": dict(sorted(collections.Counter(
                             r["family"] for r in rows).items(),
                             key=lambda kv: (kv[0] is None, kv[0])))},
        "guessedFamilyCounts": {"method": "guessed from clip name prefix",
                                "counts": dict(sorted(collections.Counter(
                                    r["guessedFamily"] for r in rows).items()))},
        "phaseFamilies": {
            "provenance": _PHASE_PROVENANCE,
            "groups": sorted(group_phases(r["name"] for r in rows),
                             key=lambda g: g["base"]),
        },
        "duplicateNames": record.get("duplicateNames", []),
        "clips": rows,
    }
    write_json(manifest_path, doc)
    return doc
