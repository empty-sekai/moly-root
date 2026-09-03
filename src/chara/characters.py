"""Character bundle -> skinned glTF 2.0 (.glb) + rig sidecar JSON.

One character ships as an asset bundle holding a humanoid Avatar (full
skeleton + human contract), two skinned meshes (face, body), three materials
(body / eye / mouth on the game's character shader), five textures (main x3,
body mask, eyebrow mask) and bone-cloth physics components.  Animation clips
live in a shared motion bundle as humanoid muscle clips.

This module turns all of that into:

* ``<name>.glb`` — skeleton nodes, skinned meshes (submesh -> material
  strictly in SkinnedMeshRenderer order), all five textures as PNG, three
  glTF materials named ``body`` / ``eye`` / ``mouth`` whose extras carry the
  character-shader inputs, and baked animations (humanoid bone rotations +
  auxiliary twist-bone rotations + the hips translation reconstructed by
  :mod:`.mecanim.bodyxform`).
* ``<name>.rig.json`` — everything the renderer needs beyond glTF: facial
  atlas constants and per-character default expression rows, the bone-cloth
  rig (:mod:`.cloth`), and the materials' full serialized uniform values.

Coordinates follow :mod:`.gltf`: positions (-x, y, z), quaternions
(x, -y, -z, w), triangle winding flipped, inverse bind matrices
re-accumulated in glTF space, UVs V-flipped (v -> 1 - v).
"""
import io
import json
import math
import os
import struct

import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler

from core.quat import trs_matrix, mat_mul, mat_inv
from core.gltf import GLB, unity_to_gltf_pos, unity_to_gltf_quat, flip_winding
from core.mesh import _normalized_colors
from sites.geometry import shader_reference, valid_keywords
from .mecanim import Rig, rig_doc, pose_bone, pose_root, sample_frames
from .mecanim.traits import GAME_TO_HUMAN
from . import cloth as cloth_mod

# The engine's facial-expression atlases use a fixed 4-column grid of
# fixed-size cells; expressions are selected by offsetting the material's
# main-texture UV by whole cells.  Indices are 1-based and clamped to >= 1.
ATLAS_COLUMNS = 4
DEFAULT_ATLAS_CELL = (512, 256)


def _unwrap(x):
    return x["data"] if isinstance(x, dict) and set(x) == {"data"} else x


class CharacterAssets:
    """One character's bundles: the main bundle plus optional auxiliary
    bundles that satisfy cross-bundle references."""

    def __init__(self, bundle, aux=()):
        self.bundle = bundle
        if aux:
            self.env = UnityPy.load(bundle, *aux)
            own_ids = {o.path_id for o in UnityPy.load(bundle).objects}
            self.objects = [o for o in self.env.objects if o.path_id in own_ids]
        else:
            self.env = UnityPy.load(bundle)
            self.objects = list(self.env.objects)

    def deref(self, owner, ptr):
        """Resolve a PPtr dict relative to ``owner``'s serialized file."""
        pid = int(ptr.get("m_PathID", 0) or 0)
        if pid == 0:
            return None
        fid = int(ptr.get("m_FileID", 0) or 0)
        af = owner.assets_file
        if fid == 0:
            return af.objects[pid]
        ext = af.externals[fid - 1]
        tail = str(getattr(ext, "path", "")).split("/")[-1].lower()
        target = self.env.cabs.get(tail)
        if target is None or not hasattr(target, "objects"):
            raise LookupError(f"unresolved external file {tail!r}: pass the "
                              f"bundle containing it as an auxiliary bundle")
        return target.objects[pid]

    def one(self, type_name):
        objs = [o for o in self.objects if o.type.name == type_name]
        if len(objs) != 1:
            raise ValueError(f"expected exactly one {type_name}, found {len(objs)}")
        return objs[0]


def read_scene(assets):
    """The instantiable prefab transform tree.

    A character bundle carries two parallel object trees sharing meshes and
    materials: the mesh-import hierarchy and the runtime prefab (the one
    whose root GameObject carries script components, and which hosts the
    cloth physics and anchor nodes).  The prefab tree is the instantiated
    truth: its transforms equal the meshes' bind pose (verified to ~4e-7),
    while the Avatar's default pose deviates at finger and hair bones — so
    glTF nodes are built from the prefab tree.

    Returns ``(nodes, index_by_transform_pid)`` where each node is
    ``{"name", "parent", "t", "q", "s"}`` in Unity space, children in
    ``m_Children`` order, node 0 the prefab root.
    """
    gos = {o.path_id: o.read_typetree() for o in assets.objects
           if o.type.name == "GameObject"}
    trs = {o.path_id: o.read_typetree() for o in assets.objects
           if o.type.name == "Transform"}
    mbs = {o.path_id for o in assets.objects if o.type.name == "MonoBehaviour"}

    def go_of(tr_pid):
        return gos.get(trs[tr_pid].get("m_GameObject", {}).get("m_PathID"))

    def has_scripts(tr_pid):
        go = go_of(tr_pid)
        return any(c.get("component", {}).get("m_PathID") in mbs
                   for c in (go or {}).get("m_Component", []))

    roots = [pid for pid, t in trs.items()
             if not t.get("m_Father", {}).get("m_PathID", 0)]
    prefab_roots = [r for r in roots if has_scripts(r)]
    if len(prefab_roots) != 1:
        raise ValueError(f"expected exactly one scripted prefab root, found "
                         f"{len(prefab_roots)} of {len(roots)} roots")

    nodes, index = [], {}

    def walk(tr_pid, parent, path):
        t = trs[tr_pid]
        go = go_of(tr_pid)
        i = len(nodes)
        index[tr_pid] = i
        nm = str((go or {}).get("m_Name", "")) or f"node{i}"
        p = nm if parent < 0 else (f"{path}/{nm}" if path else nm)
        lp, lq, ls = t["m_LocalPosition"], t["m_LocalRotation"], t["m_LocalScale"]
        nodes.append({"name": nm, "parent": parent,
                      "path": "" if parent < 0 else p,
                      "t": [lp["x"], lp["y"], lp["z"]],
                      "q": [lq["x"], lq["y"], lq["z"], lq["w"]],
                      "s": [ls["x"], ls["y"], ls["z"]]})
        for c in t.get("m_Children", []):
            cp = c.get("m_PathID", 0)
            if cp in trs:
                walk(cp, i, "" if parent < 0 else p)

    walk(prefab_roots[0], -1, "")
    return nodes, index


# Attachment points the prefab's own view component names, by the field that
# names them.  An overhead item is parented to one of these with a zero local
# position, so a consumer that wants to place one needs the node name.
ANCHOR_FIELDS = ("_headRoot", "_headTopRoot", "_spineRoot", "_hipsRoot",
                 "_lightingHeadCenter")


def read_anchors(assets, index, nodes):
    """{field name: node name} for the view component's attachment points.

    Fields present but unset are reported as ``None`` so a consumer can tell
    "this rig has no such point" from "this exporter did not look".
    """
    scripts = {o.path_id: str(o.read_typetree().get("m_ClassName", ""))
               for o in assets.objects if o.type.name == "MonoScript"}
    for obj in assets.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        tree = obj.read_typetree()
        if not any(f in tree for f in ANCHOR_FIELDS):
            continue
        out = {}
        for field in ANCHOR_FIELDS:
            if field not in tree:
                continue
            pid = (tree.get(field) or {}).get("m_PathID", 0)
            node = index.get(pid)
            out[field] = nodes[node]["name"] if node is not None else None
        return out
    return {}


def _texenv_map(mat_tt):
    props = mat_tt["m_SavedProperties"]
    return (dict(props.get("m_Floats") or []),
            {k: v for k, v in (props.get("m_Colors") or [])},
            {k: v for k, v in (props.get("m_TexEnvs") or [])})


class _ShaderRecord:
    """Adapts one resolved UnityPy object to the ``record`` half of the
    ``(store, record)`` contract :func:`sites.geometry.shader_reference`
    expects: a ``.tree(path_id)`` reader and a ``.bundle`` label."""

    def __init__(self, obj):
        self._obj = obj
        self.bundle = str(getattr(obj.assets_file, "name", "") or "")

    def tree(self, path_id):
        return self._obj.read_typetree()


class _ShaderStore:
    """Adapts :meth:`CharacterAssets.deref` to the ``store`` half of the same
    contract, so the one shader-tag reader in this repo (``sites.geometry``)
    also resolves character materials' shaders — this domain has no
    :class:`core.assets.packages.PackageStore` of its own, only ``deref``'s
    pointer-relative-to-owner-object resolution, which this wraps rather than
    reimplementing."""

    def __init__(self, assets):
        self._assets = assets

    def follow(self, owner, pointer):
        try:
            target = self._assets.deref(owner, pointer or {})
        except (LookupError, KeyError):
            return None
        if target is None:
            return None
        return _ShaderRecord(target), target.path_id

    def archive_of(self, owner, pointer):
        pointer = pointer or {}
        index = pointer.get("m_FileID", 0) - 1
        af = owner.assets_file
        if index < 0:
            return str(getattr(af, "name", "") or "")
        externals = af.externals
        if 0 <= index < len(externals):
            return str(getattr(externals[index], "path", "") or externals[index])
        return None


def _canonical_material(name, floats):
    """Map a material to its canonical role the way the runtime does: face
    materials are found by name substring, the remaining ones are body (and,
    on some characters, a body accessory)."""
    if "_eye" in name:
        return "eye"
    if "_mouth" in name:
        return "mouth"
    if "_accessory" in name:
        return "accessory"
    if floats.get("_CharacterShaderUsage") == 1.0:
        return "body"
    raise ValueError(f"cannot classify material {name!r}")


def read_materials(assets, renderers):
    """The materials actually referenced by the renderers, keyed by canonical
    role (body / eye / mouth, plus accessory on some characters).  Bundles
    may also carry orphan materials no renderer uses; those are ignored and
    reported separately."""
    out = {}
    for r in renderers:
        for mo in r["materialObjs"]:
            tt = mo.read_typetree()
            floats, colors, texenvs = _texenv_map(tt)
            canon = _canonical_material(tt["m_Name"], floats)
            if canon in out:
                if out[canon]["pathId"] != mo.path_id:
                    raise ValueError(f"two different {canon!r} materials referenced")
                continue
            textures = {}
            for prop, te in texenvs.items():
                tex_obj = assets.deref(mo, te.get("m_Texture") or {})
                if tex_obj is not None:
                    textures[prop] = tex_obj
            out[canon] = {"name": tt["m_Name"], "pathId": mo.path_id,
                          "shader": shader_reference(_ShaderStore(assets), mo, tt),
                          "keywords": valid_keywords(tt),
                          "floats": floats,
                          "colors": {k: [v["r"], v["g"], v["b"], v["a"]]
                                     for k, v in colors.items()},
                          "textures": textures,
                          "renderQueue": int(tt.get("m_CustomRenderQueue", -1))}
    if not {"body", "eye", "mouth"} <= set(out):
        raise ValueError(f"expected body/eye/mouth materials, got {sorted(out)}")
    return out


def read_renderers(assets, tr_index):
    """Skinned meshes of the prefab tree with the renderer's material list
    (in SkinnedMeshRenderer order — the submesh -> material mapping) and the
    renderer's bone list as prefab node indices (the skin joint order)."""
    gos = {o.path_id: o.read_typetree() for o in assets.objects
           if o.type.name == "GameObject"}
    tr_of_go = {}
    for o in assets.objects:
        if o.type.name != "Transform":
            continue
        tt = o.read_typetree()
        tr_of_go[tt.get("m_GameObject", {}).get("m_PathID")] = o.path_id
    out = []
    for o in assets.objects:
        if o.type.name != "SkinnedMeshRenderer":
            continue
        tt = o.read_typetree()
        go_pid = tt["m_GameObject"]["m_PathID"]
        node = tr_index.get(tr_of_go.get(go_pid))
        if node is None:
            continue                     # renderer of the mesh-import tree
        mesh_obj = assets.deref(o, tt["m_Mesh"])
        if mesh_obj is None:
            raise ValueError("renderer without a resolvable mesh")
        mats = []
        mat_objs = []
        for p in tt["m_Materials"]:
            mo = assets.deref(o, p)
            if mo is None:
                raise ValueError("renderer with an unresolvable material")
            mat_objs.append(mo)
            mats.append(mo.read_typetree()["m_Name"])
        bones = []
        for p in tt["m_Bones"]:
            bi = tr_index.get(p.get("m_PathID", 0))
            if bi is None:
                raise KeyError("renderer bone outside the prefab tree")
            bones.append(bi)
        go = gos.get(go_pid)
        out.append({"mesh": mesh_obj, "materialNames": mats, "materialObjs": mat_objs,
                    "bones": bones, "node": node,
                    "rendererName": go["m_Name"] if go else ""})
    return out


def decode_textures(materials):
    """PNG-encode every texture referenced by the materials.  Returns
    {texture name: png bytes} plus {material role: {property: texture name}}."""
    pngs, refs = {}, {}
    for role, m in materials.items():
        refs[role] = {}
        for prop, tex_obj in m["textures"].items():
            t = tex_obj.read()
            refs[role][prop] = t.m_Name
            if t.m_Name not in pngs:
                buf = io.BytesIO()
                t.image.convert("RGBA").save(buf, format="PNG", optimize=True)
                pngs[t.m_Name] = buf.getvalue()
    return pngs, refs


def _facial_lookup(tables, unit_id):
    """Resolve a character's default eye/mouth rows from the shared tables
    (tables as returned by :func:`facial_tables`, detected by signature)."""
    def find(field):
        for rows in (tables or {}).values():
            if rows and isinstance(rows[0], dict) and field in rows[0]:
                return rows
        return None
    defaults = find("CharacterUnitId")
    eyes = find("OpenEyeIndex")
    lips = find("OpenLipSyncIndex")
    row = next((r for r in defaults or [] if int(r.get("CharacterUnitId", -1)) == unit_id), None)
    if row is None:
        return None, None
    eye = next((dict(r) for r in eyes or [] if r.get("PatternName") == row.get("EyePatternName")), None)
    lip = next((dict(r) for r in lips or [] if r.get("Name") == row.get("MouthPatternName")), None)
    return eye, lip


def _atlas_doc(tex_w, tex_h, cell):
    cw, ch = cell
    return {"texture": [tex_w, tex_h], "cell": [cw, ch],
            "columns": ATLAS_COLUMNS, "rows": tex_h // ch,
            "indexBase": 1, "clampMinIndex": 1,
            "unityOffsetPerCell": [cw / tex_w, -ch / tex_h],
            "gltfOffsetPerCell": [cw / tex_w, ch / tex_h]}


def facial_tables(settings_bundle):
    """Dump the facial ScriptableObject tables of a settings bundle:
    {asset name: [row dicts]} for every MonoBehaviour whose payload is a
    single list of records."""
    env = UnityPy.load(settings_bundle)
    out = {}
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        tt = o.read_typetree()
        name = tt.get("m_Name")
        if not name:
            continue
        for k, v in tt.items():
            if k.startswith("m_"):
                continue
            v = _unwrap(v)
            if isinstance(v, list) and v and isinstance(_unwrap(v[0]), dict):
                out[name] = [_unwrap(x) for x in v]
    return out


def sample_clips(motion_bundle, names):
    """Decode and frame-sample the named muscle clips of a motion bundle.
    Character-independent, so run once and share across characters."""
    want = set(names)
    env = UnityPy.load(motion_bundle)
    out = {}
    for o in env.objects:
        if o.type.name != "AnimationClip" or not want:
            continue
        tt = o.read_typetree()
        if tt["m_Name"] not in want:
            continue
        want.discard(tt["m_Name"])
        rate, frames = sample_frames(tt)
        out[tt["m_Name"]] = {"rate": rate, "frames": frames}
    if want:
        raise LookupError(f"clips not found in motion bundle: {sorted(want)}")
    return out


def _bake_animations(glb, rig, sampled, node_by_name, node_by_hash):
    """Bake sampled clips: humanoid bone rotations, auxiliary transform
    (twist bone) rotations, and the reconstructed hips translation."""
    glb.g["animations"] = []
    report = {}
    bones = sorted(b for b in GAME_TO_HUMAN if b in node_by_name)
    for name in sorted(sampled):
        frames = sampled[name]["frames"]
        times = [f[0] for f in frames]
        rot = {b: [] for b in bones}
        hips_t = []
        twist = {}
        tdof_t = {}
        for (_, mus, bq, bp, tw, td) in frames:
            locs = {b: pose_bone(rig, b, mus) for b in bones if b != "Hips"}
            trans = rig.tdof_translations(td) if td else None
            ht, hq = pose_root(rig, mus, bq, bp, locs=locs, trans=trans)
            for b in bones:
                rot[b].append(hq if b == "Hips" else locs[b])
            hips_t.append(ht)
            if trans:
                for b, t3 in trans.items():
                    tdof_t.setdefault(b, []).append(t3)
            for h, q in tw.items():
                ni = node_by_hash.get(h)
                if ni is None:
                    continue
                n4 = math.sqrt(sum(c * c for c in q)) or 1.0
                twist.setdefault(ni, []).append(tuple(c / n4 for c in q))
        a_t = glb.acc(b"".join(struct.pack("<f", t) for t in times), 5126,
                      "SCALAR", len(times), None, ([times[0]], [times[-1]]))
        samplers, channels = [], []

        def chan(node, path, data, dim):
            acc = glb.acc(b"".join(struct.pack(f"<{dim}f", *v) for v in data),
                          5126, "VEC4" if dim == 4 else "VEC3", len(data))
            samplers.append({"input": a_t, "output": acc, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1,
                             "target": {"node": node, "path": path}})

        for b in bones:
            # target the humanoid slot's host node (sd_115/sd_130 hang the
            # hips DoF on Root; writing to the literal Hips node would clobber
            # its authored ~90-degree rest rotation and lay the model down)
            host = rig.host_node(b)
            chan(node_by_name.get(host, node_by_name[b]), "rotation",
                 [unity_to_gltf_quat(q) for q in rot[b]], 4)
        for ni in sorted(twist):
            if len(twist[ni]) != len(times):
                raise ValueError(f"{name}: twist track on node {ni} has "
                                 f"{len(twist[ni])} frames, expected {len(times)}")
            chan(ni, "rotation", [unity_to_gltf_quat(q) for q in twist[ni]], 4)
        hips_host = rig.host_node("Hips")
        chan(node_by_name.get(hips_host, node_by_name["Hips"]), "translation",
             [unity_to_gltf_pos(t) for t in hips_t], 3)
        # translation-DoF hosts: the engine animates these joints' local
        # translations; without the channel the arms cross too deep and the
        # reconstructed hips above is inconsistent with the skeleton.
        for b in sorted(tdof_t):
            if len(tdof_t[b]) != len(times):
                raise ValueError(f"{name}: TDoF track on {b} has "
                                 f"{len(tdof_t[b])} frames, expected {len(times)}")
            host = rig.host_node(b)
            chan(node_by_name.get(host, node_by_name[b]), "translation",
                 [unity_to_gltf_pos(t) for t in tdof_t[b]], 3)
        glb.g["animations"].append({"name": name, "samplers": samplers,
                                    "channels": channels})
        report[name] = {"frames": len(times), "humanoidBones": len(bones),
                        "twistTracks": len(twist), "tdofTracks": len(tdof_t)}
    return report


def extract_character(bundle, out_dir, name, sampled=None, aux=(),
                      unit_id=None, facial=None, atlas_cell=DEFAULT_ATLAS_CELL,
                      with_cloth=True):
    """Extract one character bundle into ``<out_dir>/<name>.glb`` and
    ``<out_dir>/<name>.rig.json``.  Returns a report dict of counts and
    verification numbers (all verifications also raise on hard violations).
    """
    assets = CharacterAssets(bundle, aux)
    av_tt = assets.one("Avatar").read_typetree()
    rig = Rig(rig_doc(av_tt))
    nodes, tr_index = read_scene(assets)
    n_nodes = len(nodes)

    glb = GLB(generator="moly-root character extractor")

    # ---- skeleton nodes (glTF node index == prefab tree walk order) ----
    for i, nd in enumerate(nodes):
        g = {"name": nd["name"],
             "translation": list(unity_to_gltf_pos(nd["t"])),
             "rotation": list(unity_to_gltf_quat(nd["q"])),
             "scale": list(nd["s"])}
        kids = [j for j, m in enumerate(nodes) if m["parent"] == i]
        if kids:
            g["children"] = kids
        glb.g["nodes"].append(g)
    roots = [i for i, nd in enumerate(nodes) if nd["parent"] < 0]
    # Node names are not guaranteed unique (shipped data contains misnamed
    # sibling chains); indices and paths are the reliable identity.  Only the
    # humanoid bone names used for animation binding must be unique.
    node_by_name = {}
    dup_names = set()
    for i, nd in enumerate(nodes):
        if nd["name"] in node_by_name:
            dup_names.add(nd["name"])
        else:
            node_by_name[nd["name"]] = i
    bad = dup_names & set(GAME_TO_HUMAN)
    if bad:
        raise ValueError(f"duplicate humanoid bone names in prefab tree: {sorted(bad)}")
    node_by_path = {nd["path"]: i for i, nd in enumerate(nodes) if nd["path"]}
    if len(node_by_path) != n_nodes - len(roots):
        raise ValueError("duplicate node paths in prefab tree")
    # clip curves address transforms by the avatar's path hash; resolve
    # hash -> path -> prefab node (exact even under duplicate leaf names)
    node_by_hash = {}
    for h, path in ((int(e[0]), e[1]) for e in av_tt["m_TOS"]
                    if isinstance(e, (list, tuple)) and len(e) == 2):
        ni = node_by_path.get(path)
        if ni is not None:
            node_by_hash[h] = ni

    # glTF-space world matrices for inverse bind matrices
    world = [None] * n_nodes
    def wm(i):
        if world[i] is None:
            nd = nodes[i]
            local = trs_matrix(unity_to_gltf_pos(nd["t"]),
                               unity_to_gltf_quat(nd["q"]), nd["s"])
            p = nd["parent"]
            world[i] = local if p < 0 else mat_mul(wm(p), local)
        return world[i]
    for i in range(n_nodes):
        wm(i)

    # ---- renderers, materials, textures ----
    renderers = read_renderers(assets, tr_index)
    if len(renderers) != 2:
        raise ValueError(f"expected 2 skinned meshes in the prefab tree, "
                         f"found {len(renderers)}")
    materials = read_materials(assets, renderers)
    pngs, tex_refs = decode_textures(materials)
    if len(pngs) != 5:
        raise ValueError(f"expected 5 textures, decoded {len(pngs)}: {sorted(pngs)}")
    tex_index = {}
    for tex_name in sorted(pngs):
        vi = glb.view(pngs[tex_name])
        glb.g["images"].append({"bufferView": vi, "mimeType": "image/png",
                                "name": tex_name})
        glb.g["textures"].append({"sampler": 0, "source": len(glb.g["images"]) - 1,
                                  "name": tex_name})
        tex_index[tex_name] = len(glb.g["textures"]) - 1

    mat_gltf_index = {}
    name_to_canon = {}
    mat_roles = ["body", "eye", "mouth"] + (["accessory"] if "accessory" in materials else [])
    for canon in mat_roles:
        m = materials[canon]
        f = m["floats"]
        refs = tex_refs[canon]
        main = refs.get("_MainTex")
        mask = refs.get("_BodyMaskTex")
        brow = refs.get("_EyebrowTex")
        gm = {"name": canon, "doubleSided": False,
              "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0,
                                       "baseColorTexture": {"index": tex_index[main]}},
              "extras": {
                  "sourceMaterial": m["name"],
                  "usage": "body" if f.get("_CharacterShaderUsage") == 1.0 else "face",
                  "characterShaderUsage": f.get("_CharacterShaderUsage"),
                  "shading": {"override": f.get("_OverrideShadingParameter"),
                              "intensity": f.get("_LocalBodyShadingIntensity"),
                              "edgeThreshold": f.get("_LocalBodyShadingEdgeThreshold"),
                              "edgeSmoothness": f.get("_LocalBodyShadingEdgeSmoothness")},
                  "eyebrowClip": f.get("_EyebrowClip"),
                  "eyebrowAlpha": f.get("_EyebrowAlpha"),
                  "eyebrowStencil": f.get("_EyebrowStencil"),
                  "bodyMaskTexture": tex_index.get(mask),
                  "eyebrowTexture": tex_index.get(brow),
              }}
        glb.g["materials"].append(gm)
        mat_gltf_index[canon] = len(glb.g["materials"]) - 1
        name_to_canon[m["name"]] = canon

    # ---- meshes ----
    mesh_report = []
    ibm_authored_diff = 0.0
    face_material_order = None
    for r in renderers:
        mesh_obj = r["mesh"]
        m = mesh_obj.read()
        tt = mesh_obj.read_typetree()
        mh = MeshHandler(m)
        mh.process()
        nv = mh.m_VertexCount
        canon_order = [name_to_canon[mn] for mn in r["materialNames"]]
        is_face = "eye" in canon_order
        if is_face:
            if canon_order != ["eye", "mouth"]:
                raise ValueError(f"{m.m_Name}: face material order {canon_order} "
                                 f"(renderer order must be [eye, mouth])")
            face_material_order = list(r["materialNames"])
        elif not (canon_order and canon_order[0] == "body"
                  and set(canon_order) <= {"body", "accessory"}):
            raise ValueError(f"{m.m_Name}: unexpected body material order {canon_order}")

        pos = [unity_to_gltf_pos(v) for v in mh.m_Vertices]
        mn3 = [min(p[k] for p in pos) for k in range(3)]
        mx3 = [max(p[k] for p in pos) for k in range(3)]
        attrs = {"POSITION": glb.acc(b"".join(struct.pack("<3f", *p) for p in pos),
                                     5126, "VEC3", nv, 34962, (mn3, mx3))}
        if mh.m_Normals:
            attrs["NORMAL"] = glb.acc(
                b"".join(struct.pack("<3f", *unity_to_gltf_pos(v)) for v in mh.m_Normals),
                5126, "VEC3", nv, 34962)
        for slot, arr in (("TEXCOORD_0", mh.m_UV0), ("TEXCOORD_1", getattr(mh, "m_UV1", None)),
                          ("TEXCOORD_2", getattr(mh, "m_UV2", None))):
            if arr:
                attrs[slot] = glb.acc(
                    b"".join(struct.pack("<2f", v[0], 1.0 - v[1]) for v in arr),
                    5126, "VEC2", nv, 34962)
        colors = _normalized_colors(mh)
        if colors:
            attrs["COLOR_0"] = glb.acc(
                b"".join(struct.pack("<4f", *c[:4]) for c in colors),
                5126, "VEC4", nv, 34962)

        # skin: joints in renderer bone order == mesh bind-pose order; verify
        # the parallel bone-name-hash list agrees through the avatar's table
        joints = r["bones"]
        if len(joints) != len(tt["m_BindPose"]):
            raise ValueError(f"{m.m_Name}: {len(joints)} bones vs "
                             f"{len(tt['m_BindPose'])} bind poses")
        for j, h in zip(joints, tt["m_BoneNameHashes"]):
            hn = node_by_hash.get(int(h))
            if hn is not None and hn != j:
                raise ValueError(f"{m.m_Name}: renderer bone order disagrees "
                                 f"with mesh bone-name hashes at node {j}")
        jb, wb = bytearray(), bytearray()
        bi_arr, bw_arr = mh.m_BoneIndices, mh.m_BoneWeights
        for i in range(nv):
            bi = list(bi_arr[i])[:4]
            bw = list(bw_arr[i])[:4] if bw_arr else [1.0]
            bi += [0] * (4 - len(bi))
            bw += [0.0] * (4 - len(bw))
            sw = sum(bw) or 1.0
            jb += struct.pack("<4H", *(int(x) for x in bi))
            wb += struct.pack("<4f", *(x / sw for x in bw))
        attrs["JOINTS_0"] = glb.acc(bytes(jb), 5123, "VEC4", nv, 34962)
        attrs["WEIGHTS_0"] = glb.acc(bytes(wb), 5126, "VEC4", nv, 34962)

        # Inverse bind matrices come from the mesh's authored bind poses
        # (S * B * S, row-major -> column-major): the runtime always skins
        # with them.  On most characters they equal the inverse of the
        # prefab-rest world transforms to ~4e-7, but some ship bones moved
        # after skinning (repositioned accessories), where the authored
        # matrices are the only correct choice; the recomputed comparison is
        # reported as a per-character deviation measure.
        ibm = bytearray()
        for j, bp in zip(joints, tt["m_BindPose"]):
            vals = [0.0] * 16
            inv = mat_inv(world[j])
            for rr in range(4):
                for cc in range(4):
                    a = bp[f"e{rr}{cc}"] * (-1.0 if (rr == 0) != (cc == 0) else 1.0)
                    vals[cc * 4 + rr] = a
                    ibm_authored_diff = max(ibm_authored_diff, abs(a - inv[cc * 4 + rr]))
            ibm += struct.pack("<16f", *vals)
        a_ibm = glb.acc(bytes(ibm), 5126, "MAT4", len(joints))
        glb.g["skins"].append({"joints": joints, "inverseBindMatrices": a_ibm,
                               "skeleton": roots[0]})

        idxbuf = list(mh.m_IndexBuffer)
        if sum(sm["indexCount"] for sm in tt["m_SubMeshes"]) != len(idxbuf):
            raise ValueError(f"{m.m_Name}: submesh index counts do not sum to "
                             f"the index buffer length")
        if len(canon_order) != len(tt["m_SubMeshes"]):
            raise ValueError(f"{m.m_Name}: {len(canon_order)} renderer materials "
                             f"vs {len(tt['m_SubMeshes'])} submeshes")
        prims = []
        base = 0
        for si, sm in enumerate(tt["m_SubMeshes"]):
            if sm.get("topology", 0) != 0:
                raise ValueError(f"{m.m_Name}: submesh {si} is not triangles")
            cnt = int(sm["indexCount"])
            sub = flip_winding(idxbuf[base:base + cnt])
            base += cnt
            if sub and max(sub) >= nv:
                raise ValueError(f"{m.m_Name}: submesh {si} index out of range")
            a_i = glb.acc(b"".join(struct.pack("<I", int(v)) for v in sub),
                          5125, "SCALAR", cnt, 34963)
            prims.append({"attributes": attrs, "indices": a_i,
                          "material": mat_gltf_index[canon_order[si]]})
        glb.g["meshes"].append({"name": m.m_Name, "primitives": prims})
        rn = glb.g["nodes"][r["node"]]
        rn["mesh"] = len(glb.g["meshes"]) - 1
        rn["skin"] = len(glb.g["skins"]) - 1
        mesh_report.append({"mesh": m.m_Name, "vertices": nv,
                            "primitives": len(prims),
                            "role": "face" if is_face else "body",
                            "materials": canon_order,
                            "uv1": "TEXCOORD_1" in attrs, "uv2": "TEXCOORD_2" in attrs,
                            "colors": "COLOR_0" in attrs})
    roles = sorted(mr["role"] for mr in mesh_report)
    if roles != ["body", "face"]:
        raise ValueError(f"character must have exactly one face and one body "
                         f"mesh, got roles {roles}")

    glb.g["scenes"][0]["nodes"] = roots
    glb.g["asset"]["extras"] = {"uv0": "v flipped (v -> 1 - v)",
                                "coordinates": "unity x-axis reflected"}

    anim_report = {}
    if sampled:
        anim_report = _bake_animations(glb, rig, sampled, node_by_name, node_by_hash)

    os.makedirs(out_dir, exist_ok=True)
    glb_path = os.path.join(out_dir, f"{name}.glb")
    glb.save(glb_path)

    # The same PNG bytes already embedded in the .glb (above) are also
    # written to disk beside it, one file per decoded texture name, so the
    # sidecar's top-level ``textures[]`` (relative paths, the shape
    # moly-render's loader expects) names files that actually exist. This
    # duplicates the bytes on disk deliberately -- the .glb stays a complete,
    # self-contained artifact for the already-verified glTF-loader viewer
    # (which never reads the sidecar for images), while the sidecar gets a
    # real path per texture for consumers that go by material texture slots.
    tex_paths = {}
    for tex_name in sorted(pngs):
        tex_file = f"{tex_name}.png"
        with open(os.path.join(out_dir, tex_file), "wb") as fh:
            fh.write(pngs[tex_name])
        tex_paths[tex_name] = tex_file

    # ---- rig sidecar ----
    cloth_rig = None
    cloth_summary = None
    if with_cloth:
        cloth_rig = cloth_mod.extract_bone_cloth(assets.objects, to_gltf=True)
        checks = cloth_rig.pop("checks")
        failed = [c for c in checks if not c["ok"]]
        cloth_summary = {"total": len(checks), "failed": len(failed),
                         "failures": failed}
        cloth_rig["checks"] = {"total": len(checks), "failed": len(failed)}
        # bind cloth to glb nodes by index (names may collide in shipped data)
        for comp in cloth_rig["components"]:
            V = comp["vertices"]
            pids = V.pop("transformPathIds")
            try:
                idxs = [tr_index[p] for p in pids]
            except KeyError:
                raise ValueError(f"cloth component {comp['component']}: bone "
                                 f"transform outside the prefab tree") from None
            nb = len(V["bones"])
            V["nodeIndices"] = idxs[:nb]
            V["terminalNodeIndex"] = idxs[nb] if len(idxs) > nb else None
            comp["target"]["rootNodeIndices"] = [
                tr_index[p] for p in comp["target"].pop("rootTransformPathIds")]
        for c in cloth_rig["colliders"]:
            c["node"] = tr_index[c.pop("transformPathId")]

    default_eye = default_mouth = None
    if facial is not None and unit_id is not None:
        default_eye, default_mouth = _facial_lookup(facial, unit_id)
    eye_tex = materials["eye"]["textures"]["_MainTex"].read()
    mouth_tex = materials["mouth"]["textures"]["_MainTex"].read()
    sidecar = {
        "name": name,
        "unitId": unit_id,
        "defaultEye": default_eye,
        "defaultMouth": default_mouth,
        "eyeAtlas": _atlas_doc(eye_tex.m_Width, eye_tex.m_Height, atlas_cell),
        "mouthAtlas": _atlas_doc(mouth_tex.m_Width, mouth_tex.m_Height, atlas_cell),
        "cloth": cloth_rig,
        "anchors": read_anchors(assets, tr_index, nodes),
        "textures": [tex_paths[n] for n in sorted(pngs)],
        # materials[i].name's contract is "the glTF material's own name",
        # matching the site domain's convention (there it holds the real
        # Unity material name because the glTF material does too). For
        # characters the glTF material name is the canon role string
        # (see the `gm = {"name": canon, ...}` build above), so name is
        # canon here as well -- the contract is uniform, only its content
        # differs by domain. The real Unity material name is not lost; it
        # lives in unityName. role duplicates canon for callers that want
        # it without relying on name's domain-specific content.
        "materials": [{"role": canon, "name": canon, "unityName": m["name"],
                       "shader": m["shader"],
                       "renderQueue": m["renderQueue"],
                       "keywords": m["keywords"],
                       "textures": {prop: tex_paths[tex_name]
                                    for prop, tex_name in tex_refs[canon].items()},
                       "floats": m["floats"],
                       "colors": m["colors"]}
                      for canon, m in materials.items()],
    }
    rig_path = os.path.join(out_dir, f"{name}.rig.json")
    with open(rig_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(sidecar, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")

    # orphan assets: materials/textures in the bundle that no renderer or
    # material references (shipped leftovers on some characters)
    used_mat = {m["pathId"] for m in materials.values()}
    used_tex = {t.path_id for m in materials.values() for t in m["textures"].values()}
    orphan_materials = sorted(o.read_typetree()["m_Name"] for o in assets.objects
                              if o.type.name == "Material" and o.path_id not in used_mat)
    orphan_textures = sorted(o.read_typetree()["m_Name"] for o in assets.objects
                             if o.type.name == "Texture2D" and o.path_id not in used_tex)

    return {"name": name, "glb": glb_path, "rig": rig_path,
            "bones": n_nodes, "meshes": mesh_report,
            "faceMaterialOrder": face_material_order,
            "materialRoles": mat_roles,
            "duplicateNodeNames": sorted(dup_names),
            "orphanMaterials": orphan_materials,
            "orphanTextures": orphan_textures,
            "textures": sorted(pngs),
            "animations": anim_report,
            "ibmAuthoredMaxDiff": ibm_authored_diff,
            "cloth": (cloth_rig or {}).get("stats"),
            "clothChecks": cloth_summary,
            "glbBytes": os.path.getsize(glb_path)}
