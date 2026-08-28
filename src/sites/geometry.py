"""Site geometry: prefab node trees, their meshes, materials and collision surfaces.

A site scene package holds several prefab **roots**, not one.  The site prefab is
the scene the game places, but the same package also carries the model assets it
was built from, the per-level dressing sets, and — indoors — a standalone sky.  So
one package becomes **one glTF binary with a scene per root**: mesh and material
data are written once and shared between the scenes, the site prefab is the default
scene, and nothing in the package is dropped for being a sibling of it.

Two rules here are load-bearing and are not conveniences:

* **The geometry keeps the package's own origin.**  Every site prefab root ships at
  local position zero, and the world offset of a site is applied by the game at
  runtime from its master row.  Three of the nine sites are three placements of the
  *same* package, so baking that offset into the geometry would collapse them into
  one and lose the second and third floors for good.  The offset therefore appears
  only in the placement table, never in a mesh.

* **Collision surfaces are exported separately, one file per surface.**  A site's
  walkable ground, its camera blocker, its wall blocker and its footstep-material
  surface are distinct surfaces with distinct meanings; merged into one file a
  consumer can no longer tell which is which.  They are invisible geometry, so they
  are also kept out of the visible scene — with one measured exception, a sea
  surface that is both drawn and collided with, which appears in both places and is
  flagged where it appears.

Materials cannot be translated: every site material points at a shader in a package
this domain does not own, so what is exported is the shader's family name, its
subshader tags and the whole authored property block.  The glTF material beside it
is a **preview approximation** built from those properties (see `preview_material`),
and the property block, not the approximation, is the record.
"""
import hashlib
import struct

from core.assets.packages import pairs
from core.gltf import GLB, unity_to_gltf_pos, unity_to_gltf_quat
from core.mesh import add_mesh, compose_mesh, mesh_accessors

TRANSFORMS = ("Transform", "RectTransform")

# Unity's cull modes; 0 is "draw both sides".
CULL_OFF = 0

# Property names of the alpha-clip pair the site shaders declare.  Read from the
# material, not assumed: `Mysekai/Site/Tree` and its neighbours expose exactly
# these two, so a cutout is authored data rather than a guess about foliage.
ALPHA_CLIP_SWITCH = "_UseAlphaClip"
ALPHA_CLIP_THRESHOLD = "_AlphaClip"

# Texture slots a preview material takes its base colour picture from, in order.
BASE_COLOR_SLOTS = ("_MainTex", "_BaseMap", "_BaseColorMap", "_MainTexture")
# Colour properties a preview material takes its base colour factor from.
BASE_COLOR_PROPERTIES = ("_Color", "_BaseColor", "_MainColor")

MESH_NOT_IN_PACKAGE = ("mesh pointer names geometry no supplied package holds: "
                       "either the engine's own built-in resources or a package "
                       "that was not supplied")
BUILT_IN_RESOURCES = "unity default resources"
MESH_NULL = ("mesh pointer is null on disk: the component ships without geometry, "
             "which is an authored state and not a lookup failure")
MESH_UNREADABLE = "mesh could not be decoded"


def _vec(node, keys):
    return [round(float((node or {}).get(key, 0.0)), 6) for key in keys]


def component_ids(tree):
    """Component path ids of one game object, in either serialized form."""
    for entry in (tree or {}).get("m_Component") or []:
        if isinstance(entry, dict):
            pointer = entry.get("component", entry)
            yield (pointer or {}).get("m_PathID", 0)
        else:
            yield (entry or {}).get("m_PathID", 0)


class Graph:
    """The transform graph of one serialized file, read once."""

    def __init__(self, record):
        self.record = record
        self.owner = {}                  # transform id -> game object id
        self.transform_of = {}           # game object id -> transform id
        self.father = {}
        self.children = {}
        for path_id, kind in record.kinds.items():
            if kind not in TRANSFORMS:
                continue
            tree = record.tree(path_id)
            game_object = (tree.get("m_GameObject") or {}).get("m_PathID", 0)
            self.owner[path_id] = game_object
            self.transform_of[game_object] = path_id
            self.father[path_id] = (tree.get("m_Father") or {}).get("m_PathID", 0)
        for path_id, father in self.father.items():
            self.children.setdefault(father, []).append(path_id)
        for kids in self.children.values():
            kids.sort()

    @property
    def roots(self):
        """Root transforms, ordered by name then path id so runs are repeatable."""
        return sorted((t for t in self.owner if not self.father.get(t)),
                      key=lambda t: (self.name(t), t))

    def name(self, transform):
        return str(self.record.tree(self.owner[transform]).get("m_Name", ""))

    def game_object(self, transform):
        return self.record.tree(self.owner[transform])

    def components(self, transform):
        """``[(kind, path id)]`` of the game object a transform belongs to."""
        out = []
        for path_id in component_ids(self.game_object(transform)):
            out.append((self.record.kinds.get(path_id), path_id))
        return out

    def subtree(self, transform):
        """*transform* and every descendant, parents first."""
        out, queue = [], [transform]
        while queue:
            current = queue.pop(0)
            out.append(current)
            queue.extend(self.children.get(current, []))
        return out

    def node_paths(self, root):
        """``{game object id: path}`` under *root*; the root's own path is ``""``."""
        paths = {}
        queue = [(root, "")]
        while queue:
            transform, path = queue.pop(0)
            paths[self.owner[transform]] = path
            for child in self.children.get(transform, []):
                name = self.name(child)
                queue.append((child, name if not path else f"{path}/{name}"))
        return paths


class Textures:
    """Images of one package, written once and referenced by a relative path."""

    def __init__(self, directory, prefix):
        self.directory = directory
        self.prefix = prefix              # path of the directory, relative to the glb
        self.written = {}
        self.failed = []

    def _array(self, record, path_id, tree, name, stem):
        """Every layer of a texture array, in layer order.

        An array holds several pictures of one size and format and is sampled with
        a layer coordinate, so it is written one file per layer and kept apart from
        the single-image bindings: a consumer must not mistake one for the other.
        """
        reference = {"name": name, "kind": "Texture2DArray",
                     "width": tree.get("m_Width"), "height": tree.get("m_Height"),
                     "layers": tree.get("m_Depth"), "file": None, "files": []}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            layers = list(record.objects[path_id].read().images)
            for index, image in enumerate(layers):
                image.save(self.directory / f"{stem}.{index}.png")
                reference["files"].append(f"{self.prefix}/{stem}.{index}.png")
            if len(layers) != reference["layers"]:
                self.failed.append({"image": name,
                                    "reason": f"{len(layers)} layers decoded but the "
                                              f"asset declares {reference['layers']}"})
        except Exception as exc:          # unreadable texture format
            self.failed.append({"image": name,
                                "reason": f"{type(exc).__name__}: {exc}"})
            reference["files"] = []
        return reference

    def reference(self, target):
        """Write the image a pointer resolves to and return how to find it."""
        if target is None:
            return None
        record, path_id = target
        key = (record.archive, path_id)
        if key in self.written:
            return dict(self.written[key])
        tree = record.tree(path_id)
        name = str(tree.get("m_Name", "") or "image")
        kind = record.kinds.get(path_id)
        stem = f"{name}-{abs(path_id) % 0xFFFFFFFF:08x}"
        if kind == "Texture2DArray":
            self.written[key] = self._array(record, path_id, tree, name, stem)
            return dict(self.written[key])
        if kind not in ("Texture2D", "Sprite"):
            self.failed.append({"image": name,
                                "reason": f"{kind} is not a single image"})
            self.written[key] = {"name": name, "kind": kind, "file": None}
            return dict(self.written[key])
        reference = {"name": name, "kind": kind, "file": f"{self.prefix}/{stem}.png",
                     "width": tree.get("m_Width"), "height": tree.get("m_Height")}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            record.objects[path_id].read().image.save(self.directory / f"{stem}.png")
        except Exception as exc:          # unreadable texture format
            self.failed.append({"image": name, "reason": f"{type(exc).__name__}: {exc}"})
            reference = {"name": name, "kind": kind, "file": None}
        self.written[key] = reference
        return dict(reference)


def shader_reference(store, record, tree):
    """The shader a material points at: its family name and its subshader tags.

    The shader is named and its tags are recorded, but shader code is not
    translated here.  The preview material therefore makes its transparency
    decision from the material's authored render queue instead.
    """
    target = store.follow(record, tree.get("m_Shader") or {})
    if target is None:
        pointer = tree.get("m_Shader") or {}
        return {"name": None, "external": True,
                "archive": store.archive_of(record, pointer),
                "fileId": pointer.get("m_FileID", 0)}
    shader_record, path_id = target
    parsed = shader_record.tree(path_id).get("m_ParsedForm") or {}
    tags = {}
    for subshader in (parsed.get("m_SubShaders") or [])[:1]:
        for key, value in pairs((subshader.get("m_Tags") or {}).get("tags")):
            tags[str(key)] = str(value)
    return {"name": str(parsed.get("m_Name")) if parsed.get("m_Name") else None,
            "package": shader_record.bundle, "tags": tags,
            "fallback": parsed.get("m_FallbackName") or None}


def valid_keywords(tree):
    """A material's enabled shader keyword set, as declared strings.

    Shared with the character domain (:mod:`chara.characters`) so the one
    reading of ``m_ValidKeywords`` serves both; legally empty (a material can
    enable no keywords), so an empty list here is not itself a defect.
    """
    return [str(word) for word in tree.get("m_ValidKeywords") or []]


def material_document(store, record, path_id, textures):
    """One material: its shader, its texture bindings and its property block."""
    tree = record.tree(path_id)
    properties = tree.get("m_SavedProperties") or {}
    floats = {str(name): round(float(value), 6)
              for name, value in pairs(properties.get("m_Floats"))
              if isinstance(value, (int, float))}
    colors = {str(name): [round(float((value or {}).get(channel, 0.0)), 6)
                          for channel in "rgba"]
              for name, value in pairs(properties.get("m_Colors"))
              if isinstance(value, dict)}
    slots, arrays, scale_offset = {}, {}, {}
    for name, value in pairs(properties.get("m_TexEnvs")):
        entry = value or {}
        scale, offset = entry.get("m_Scale") or {}, entry.get("m_Offset") or {}
        scale_offset[str(name)] = [round(float(scale.get("x", 1.0)), 6),
                                   round(float(scale.get("y", 1.0)), 6),
                                   round(float(offset.get("x", 0.0)), 6),
                                   round(float(offset.get("y", 0.0)), 6)]
        pointer = entry.get("m_Texture") or {}
        if not pointer.get("m_PathID", 0):
            slots[str(name)] = None
            continue
        reference = textures.reference(store.follow(record, pointer))
        if reference is not None and "files" in reference:
            arrays[str(name)] = reference
            continue
        slots[str(name)] = reference["file"] if reference else None
    return {"name": str(tree.get("m_Name", "")),
            "shader": shader_reference(store, record, tree),
            "renderQueue": tree.get("m_CustomRenderQueue", -1),
            "keywords": valid_keywords(tree),
            "textures": slots, "textureArrays": arrays,
            "textureScaleOffset": scale_offset,
            "floats": floats, "colors": colors}


TEXTURE_TRANSFORM = "KHR_texture_transform"


def effective_queue(document):
    """The render queue the material is actually drawn in.

    A material may override the queue its shader asks for.  When it does not,
    the field holds Unity's "no override" marker and the shader's own queue is
    the one in force -- so reading only the override would call every material
    that never overrode anything opaque.  The shader is not translated here, so
    for that case only the queue's name is available, and only the transparent
    one changes the outcome.
    """
    queue = float(document.get("renderQueue", -1))
    if queue >= 0:
        return queue
    tags = document["shader"].get("tags") or {}
    return 3000.0 if str(tags.get("QUEUE", "")).startswith("Transparent") else -1.0


def preview_material(document, texture=None):
    """A glTF material approximating one Unity material, for viewers.

    Every value used here is authored material data.  Shader code is not
    translated: the preview reads the bound base-colour slot and factor, the
    alpha-clip pair, the material render queue, and cull mode.  A queue above
    Unity's last opaque queue (2500) maps to glTF ``BLEND``.  glTF has no
    additive blend mode, so an additive material keeps that fact in
    ``extras.blendMode`` and its authored factors in ``extras.blendFactors``.
    """

    floats = document["floats"]
    colors = document["colors"]
    factor = [1.0, 1.0, 1.0, 1.0]
    for name in BASE_COLOR_PROPERTIES:
        if name in colors:
            factor = colors[name]
            break
    material = {"name": document["name"],
                "pbrMetallicRoughness": {"baseColorFactor": factor,
                                         "metallicFactor": 0.0,
                                         "roughnessFactor": 1.0}}
    material["extras"] = {
        "blendMode": ("additive" if floats.get("_BlendSrc") == 5
                       and floats.get("_BlendDst") == 1 else "standard_alpha"
                       if floats.get("_BlendSrc") == 5
                       and floats.get("_BlendDst") == 10 else None),
        "blendFactors": ({"src": floats["_BlendSrc"], "dst": floats["_BlendDst"]}
                         if floats.get("_BlendSrc") in (1, 5)
                         and floats.get("_BlendDst") in (1, 10) else None)}
    if not any(value is not None for value in material["extras"].values()):
        material.pop("extras")
    if texture is not None:
        index, slot = texture
        binding = {"index": index}
        scale = document["textureScaleOffset"].get(slot)
        if scale and scale != [1.0, 1.0, 0.0, 0.0]:
            # The slot is sampled through its scale/offset pair, so a tiled ground
            # drawn without it is visibly wrong; glTF says the same thing with this
            # extension rather than by rewriting the mesh's coordinates.
            binding["extensions"] = {TEXTURE_TRANSFORM: {
                "scale": [scale[0], scale[1]], "offset": [scale[2], scale[3]]}}
        material["pbrMetallicRoughness"]["baseColorTexture"] = binding
    if floats.get(ALPHA_CLIP_SWITCH):
        material["alphaMode"] = "MASK"
        material["alphaCutoff"] = float(floats.get(ALPHA_CLIP_THRESHOLD, 0.5))
    elif effective_queue(document) > 2500:
        material["alphaMode"] = "BLEND"
    if float(floats.get("_Cull", 2.0)) == CULL_OFF:
        material["doubleSided"] = True
    return material


class Builder:
    """One glTF binary with a scene per prefab root, meshes and images shared."""

    def __init__(self, store, textures, generator="moly-root"):
        self.store = store
        self.textures = textures
        self.glb = GLB(generator)
        # A fresh document carries one nameless placeholder scene; every scene here
        # is a named prefab root, so the placeholder is dropped rather than kept and
        # then worked around when the default scene is chosen.
        self.glb.g["scenes"] = []
        self._images = {}                 # relative image path -> glTF texture index
        self._buffers = {}                # (archive, path id) -> written accessors
        self._meshes = {}                 # (mesh key, material key) -> glTF mesh
        self._materials = {}              # (archive, path id) -> glTF material index
        self.materials = []               # index-order material documents
        self.mesh_keys = set()            # every mesh pointer that became geometry
        self.vertices = 0
        self.triangles = 0

    # -- lookups ----------------------------------------------------------
    def material(self, record, pointer):
        """``(glTF material index, document)`` for a material pointer."""
        target = self.store.follow(record, pointer or {})
        if target is None:
            return None, None
        owner, path_id = target
        key = (owner.archive, path_id)
        if key not in self._materials:
            document = material_document(self.store, owner, path_id, self.textures)
            self.glb.g["materials"].append(
                preview_material(document, self.texture(document)))
            self._materials[key] = len(self.glb.g["materials"]) - 1
            self.materials.append(document)
        return self._materials[key], self.materials[self._materials[key]]

    def texture(self, document):
        """``(glTF texture index, slot name)`` of a material's base colour picture.

        The picture is referenced by a relative path rather than embedded: the pack
        writes each image once beside the binary, and embedding would write every
        one of them a second time.
        """
        for slot in BASE_COLOR_SLOTS:
            path = document["textures"].get(slot)
            if not path:
                continue
            if path not in self._images:
                self.glb.g["images"].append({"uri": path})
                self.glb.g["textures"].append(
                    {"sampler": 0, "source": len(self.glb.g["images"]) - 1})
                self._images[path] = len(self.glb.g["textures"]) - 1
            return self._images[path], slot
        return None

    def mesh(self, record, pointer, materials=None):
        """``(glTF mesh index, reason)``; the reason is why there is no mesh."""
        pointer = pointer or {}
        if not pointer.get("m_PathID", 0):
            return None, MESH_NULL
        target = self.store.follow(record, pointer)
        if target is None:
            archive = self.store.archive_of(record, pointer)
            if str(archive) == BUILT_IN_RESOURCES:
                return None, ("mesh pointer names the engine's own built-in "
                              "resources (a primitive quad, plane or cube), which "
                              "no asset package ships")
            return None, f"{MESH_NOT_IN_PACKAGE} (archive: {archive})"
        owner, path_id = target
        mesh_key = (owner.archive, path_id)
        self.mesh_keys.add(mesh_key)
        combination = (mesh_key, tuple(sorted((materials or {}).items())))
        if combination in self._meshes:
            return self._meshes[combination], None
        tree = owner.tree(path_id)
        if mesh_key not in self._buffers:
            try:
                self._buffers[mesh_key] = mesh_accessors(
                    self.glb, owner.objects[path_id], tree)
            except Exception as exc:      # unreadable or non-triangle mesh
                self._buffers[mesh_key] = None
                return None, f"{MESH_UNREADABLE}: {type(exc).__name__}: {exc}"
            self.vertices += self._buffers[mesh_key]["vertices"]
            self.triangles += self._buffers[mesh_key]["triangles"]
        buffers = self._buffers[mesh_key]
        if buffers is None:
            return None, MESH_UNREADABLE
        index = compose_mesh(self.glb, buffers, buffers["name"], materials)
        self._meshes[combination] = index
        return index, None

    def mesh_size(self, record, pointer):
        """``(vertices, triangles)`` of an already-added mesh pointer."""
        target = self.store.follow(record, pointer or {})
        if target is None:
            return 0, 0
        buffers = self._buffers.get((target[0].archive, target[1]))
        return (buffers["vertices"], buffers["triangles"]) if buffers else (0, 0)

    # -- writing ----------------------------------------------------------
    def node(self, graph, transform, mesh=None):
        tree = graph.record.tree(transform)
        node = {"name": graph.name(transform),
                "translation": list(unity_to_gltf_pos(
                    _vec(tree.get("m_LocalPosition"), "xyz"))),
                "rotation": list(unity_to_gltf_quat(
                    _vec(tree.get("m_LocalRotation"), "xyzw"))),
                "scale": _vec(tree.get("m_LocalScale"), "xyz")}
        if mesh is not None:
            node["mesh"] = mesh
        self.glb.g["nodes"].append(node)
        return len(self.glb.g["nodes"]) - 1

    def scene(self, name, root_index):
        self.glb.g["scenes"].append({"name": name, "nodes": [root_index]})
        return len(self.glb.g["scenes"]) - 1

    def finish(self, default_scene=0):
        """Drop the placeholder scene and select the default one."""
        if self.glb.g["scenes"] and not self.glb.g["scenes"][0].get("name") \
                and not self.glb.g["scenes"][0]["nodes"]:
            self.glb.g["scenes"].pop(0)
        self.glb.g["scene"] = default_scene if self.glb.g["scenes"] else 0
        if not self.glb.g["scenes"]:
            self.glb.g["scenes"] = [{"nodes": []}]
        if any(TEXTURE_TRANSFORM in ((material.get("pbrMetallicRoughness") or {})
                                     .get("baseColorTexture") or {})
               .get("extensions", {})
               for material in self.glb.g["materials"]):
            self.glb.g["extensionsUsed"] = [TEXTURE_TRANSFORM]
        return self.glb.blob()


def single_mesh_blob(store, record, path_id, name=None):
    """One mesh as its own glTF binary: ``(blob, vertices, triangles)``."""
    glb = GLB()
    tree = record.tree(path_id)
    mesh_name = str(name or tree.get("m_Name") or "mesh")
    _, vertices, triangles = add_mesh(glb, record.objects[path_id], tree, mesh_name)
    glb.g["nodes"].append({"name": mesh_name, "mesh": 0})
    glb.g["scenes"][0]["nodes"] = [0]
    return glb.blob(), vertices, triangles


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def collider_document(store, record, path_id, kind, node_path):
    """One collider: which surface it is and what it is shaped by."""
    tree = record.tree(path_id)
    entry = {"node": node_path, "component": kind,
             "enabled": bool(tree.get("m_Enabled", 1))}
    if kind == "MeshCollider":
        pointer = tree.get("m_Mesh") or {}
        entry["convex"] = bool(tree.get("m_Convex", 0))
        target = store.follow(record, pointer)
        entry["mesh"] = (str(target[0].tree(target[1]).get("m_Name", ""))
                         if target is not None else None)
        entry["meshPointer"] = {"fileId": pointer.get("m_FileID", 0),
                                "pathId": pointer.get("m_PathID", 0)}
        if target is None:
            entry["reason"] = MESH_NULL if not pointer.get("m_PathID", 0) \
                else MESH_NOT_IN_PACKAGE
    elif kind == "BoxCollider":
        entry["center"] = _vec(tree.get("m_Center"), "xyz")
        entry["size"] = _vec(tree.get("m_Size"), "xyz")
    elif kind == "SphereCollider":
        entry["center"] = _vec(tree.get("m_Center"), "xyz")
        entry["radius"] = round(float(tree.get("m_Radius", 0.0)), 6)
    elif kind == "CapsuleCollider":
        entry["center"] = _vec(tree.get("m_Center"), "xyz")
        entry["radius"] = round(float(tree.get("m_Radius", 0.0)), 6)
        entry["height"] = round(float(tree.get("m_Height", 0.0)), 6)
        entry["direction"] = tree.get("m_Direction")
    return entry
