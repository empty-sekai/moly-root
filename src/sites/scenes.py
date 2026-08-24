"""Extracting one site package: every root, every asset, every object accounted for.

The same walk serves all eleven package classes, because they are all the same
shape underneath — Unity objects, some hanging off prefab node trees and some
standing alone — and because a class-specific path would be the place a new kind of
object goes missing.  So this module opens a package, walks each prefab root into a
glTF scene, then sweeps every object the walk did not touch, and finishes by
**counting**: each object in the package ends up either exported, deliberately
skipped with the reason, or listed as unsupported, and the three add up to the
package's object count.  That sum is the guarantee behind "nothing was left
unqueried"; a package where it fails is a bug in this module, not a quiet omission.

Three decisions worth naming:

* **A component this module has no structured reader for is still exported** — its
  serialized fields are written out verbatim, with pointers rendered as the object
  they name.  Interpretation is separate from extraction; refusing to interpret is
  not a reason to drop authored data.
* **Collision surfaces leave the visible scene** and are written one file each, with
  the role their name states (walkable ground, camera blocker, wall blocker,
  footstep surface).  A surface that is *also* drawn stays in the scene as well and
  is flagged, because one shipped surface really is both.
* **Inactive nodes and disabled renderers are kept and listed.**  They ship hidden;
  a pack that dropped them would not be the authored scene, and a consumer that
  drew them would show things the game never shows, so they are exported with
  ``extras.active``/``extras.enabled`` on the node and listed by path.
"""
from core.assets.packages import pairs
from core.jsonio import write_json
from core.particles import TRAIL_MATERIAL_SLOT, decode_renderer, decode_system
from . import census
from .clips import NO_CURVES, clip_document, path_hashes
from .geometry import (Builder, Graph, Textures, collider_document,
                       component_ids, material_document)
from .navmesh import NO_HEIGHT_MESH, height_mesh_blob, navmesh_document

# Render mode that draws each particle as a copy of a mesh.
MESH_RENDER_MODE = "Mesh"

# Object types that carry no data of their own for a consumer, with why.
SKIPPED_TYPES = {
    "AssetBundle": ("the package's own manifest object; its asset list and its "
                    "dependency list are reported under `inventory`"),
    "MonoScript": ("a script's type identity; it is reported by class name "
                   "wherever a behaviour uses it"),
    "Shader": ("an in-package shader variant; shaders are not translated by this "
               "repository, only named, and every site material's shader is in "
               "`mysekai/shader` anyway"),
    "AnimatorController": ("an animator state machine; its clips are exported and "
                           "listed under `animations.controllers`, and the state "
                           "graph itself is not modelled here"),
}

# Components with a structured reader below.
GEOMETRY_COMPONENTS = ("MeshFilter", "MeshRenderer", "SkinnedMeshRenderer")
COLLIDER_COMPONENTS = ("MeshCollider", "BoxCollider", "SphereCollider",
                       "CapsuleCollider")

# Boilerplate every serialized component carries; dropped from a verbatim dump
# because it says nothing about the asset.
BOILERPLATE = ("m_ObjectHideFlags", "m_CorrespondingSourceObject",
               "m_PrefabInstance", "m_PrefabAsset", "m_GameObject", "m_Script",
               "m_EditorHideFlags", "m_EditorClassIdentifier")

# The named slots the site prefabs use, and what each is for.  Read off the
# packages, not assumed: only the first three are present in all eight scene
# packages, so a slot missing here is normal and a slot *not* listed here is
# reported as unknown rather than guessed at.
SLOT_ROLES = {
    "navmesh_target": ("collision surfaces the runtime navigation bake reads; "
                       "present in all eight scene packages"),
    "env": ("environment volume anchor; empty in every outdoor package and holding "
            "the room volume indoors"),
    "collider": "collider slot; empty in every shipped package",
    "base": "static ground and fixed props",
    "decoration": "scatter dressing",
    "decoration (1)": ("a second scatter dressing node; Unity's duplicate-name "
                       "suffix. Two packages ship both, and how they divide the "
                       "dressing is not established"),
    "sound_mesh": "surface an ambience emitter is shaped by",
    "mdl_site_floor_large_sound": "indoor floor surface an ambience emitter is shaped by",
    "MysekaiHousingCompetitionCamera": "camera the housing competition shot is taken from",
    "effect": "effect anchor",
    "effect_root": "effect anchor",
    "light": "area light",
}

# Collision surfaces, by the suffix of the node or mesh name.
SURFACE_ROLES = (
    ("_nav_ground", "walkableGround"),
    ("_nav_cam", "cameraBlocker"),
    ("_nav_wall", "wallBlocker"),
    ("_footse", "footstepSurface"),
    ("_nav", "walkableGround"),
)

# What fills an empty timeline socket.  The site shell ships a director with no
# asset in it; that is not an unsupported timeline, it is a socket the runtime
# assigns per weather, and naming the field that assigns it is the whole record.
EMPTY_SOCKET = ("an empty timeline socket, not a missing asset: the director ships "
                "with no playable asset, and the runtime assigns one per phenomenon "
                "from `EnvironmentLoadData.PlayableAsset` through "
                "`SiteEnvironmentViewController._playableDirector`")
BOUND_SOCKET = "a bound timeline: the director ships with its playable asset in it"

UNREADABLE = "object typetree could not be read"
MESH_UNREACHED = ("mesh not reached from any prefab root; exported as a scene of its "
                  "own so the package's geometry is complete")


def surface_role(*names):
    """The role a collision surface's name states, or ``None`` when it states none."""
    for name in names:
        for suffix, role in SURFACE_ROLES:
            if str(name or "").endswith(suffix):
                return role
    return None


def _pointer(value):
    return (isinstance(value, dict) and len(value) == 2
            and "m_FileID" in value and "m_PathID" in value)


def _plain(value, store, record, depth=0):
    """A typetree as JSON-safe data, with pointers named where they resolve."""
    if depth > 12:
        return "<nesting deeper than this dump goes>"
    if _pointer(value):
        if not value.get("m_PathID", 0):
            return None
        entry = {"fileId": value.get("m_FileID", 0), "pathId": value.get("m_PathID")}
        target = store.follow(record, value)
        if target is not None:
            owner, path_id = target
            entry["type"] = owner.kinds.get(path_id)
            try:
                entry["name"] = str(owner.tree(path_id).get("m_Name", "")) or None
            except Exception:             # unreadable target: the pointer still stands
                entry["name"] = None
            if entry["type"] == "MonoBehaviour":
                entry["class"] = owner.script_of(path_id) or None
        else:
            entry["archive"] = store.archive_of(record, value)
        return entry
    if isinstance(value, dict):
        return {str(key): _plain(item, store, record, depth + 1)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item, store, record, depth + 1) for item in value]
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value)}
    return value


def verbatim(store, record, path_id):
    """One component's serialized fields, boilerplate dropped."""
    tree = record.tree(path_id)
    return {key: _plain(value, store, record)
            for key, value in tree.items() if key not in BOILERPLATE}


class PackageExtract:
    """One package's artifacts and one package's document."""

    def __init__(self, store, name, out, prefix):
        self.store = store
        self.name = name
        self.kind, self.key = census.classify(name)
        self.out = out                       # directory artifacts are written to
        self.prefix = prefix                 # that directory, relative to the index
        self.package = store.package(name)
        self.stem = self.key.rsplit("__", 1)[-1]
        self.textures = Textures(out / "textures", "textures")
        self.builder = Builder(store, self.textures)
        self.disposition = {}                # (archive, path id) -> (state, reason)
        self.roots = []
        self.slots = []
        self.collision = []
        self.materials = []
        self.components = {}
        self.particles = []
        self.navmeshes = []
        self.clips = []
        self.environments = {}
        self.inactive = []
        self.disabled = []
        self.unsupported = []
        self.omitted = []
        self.directors = []
        self.extra_scenes = []
        self._container = {}
        self._collision_written = {}

    # -- accounting -------------------------------------------------------
    def _mark(self, record, path_id, state, reason=""):
        """Record what became of one object *of this package*.

        A walk reaches objects in the packages this one depends on — a room
        module's meshes live in the kit — and those belong to their own package's
        accounting, not to this one's, or the totals would not add up.
        """
        if record not in self.package.files:
            return
        self.disposition[(record.archive, path_id)] = (state, reason)

    def _state(self, record, path_id):
        return self.disposition.get((record.archive, path_id), (None, ""))[0]

    # -- containers -------------------------------------------------------
    def _read_containers(self):
        for record in self.package.files:
            for path_id, kind in list(record.kinds.items()):
                if kind != "AssetBundle":
                    continue
                for asset_path, info in record.tree(path_id).get("m_Container") or []:
                    target = self.store.follow(record, (info or {}).get("asset") or {})
                    if target is None:
                        continue
                    self._container.setdefault(
                        (target[0].archive, target[1]), []).append(str(asset_path))

    def asset_paths(self, record, path_id):
        return sorted(set(self._container.get((record.archive, path_id), [])))

    # -- node walk --------------------------------------------------------
    def _component(self, record, graph, transform, path, node_paths, root=None):
        """Read every component of one node; returns the glTF mesh index or None."""
        components = graph.components(transform)
        kinds = {kind for kind, _ in components}
        mesh_index = None
        for kind, path_id in components:
            if kind is None:
                self.unsupported.append({"node": path, "component": None,
                                         "reason": "component object not in this package"})
                continue
            if kind in ("Transform", "RectTransform"):
                self._mark(record, path_id, "exported", "node tree")
                continue
            if kind in ("MeshFilter", "MeshRenderer", "SkinnedMeshRenderer"):
                self._mark(record, path_id, "exported", "geometry")
                continue
            if kind in COLLIDER_COMPONENTS:
                self._collider(record, path_id, kind, path,
                               visible="MeshRenderer" in kinds, root=root)
                continue
            if kind == "ParticleSystem":
                self._particle_system(record, path_id, path, node_paths, graph)
                continue
            if kind == "ParticleSystemRenderer":
                self._particle_renderer(record, path_id, path)
                continue
            if kind == "NavMeshData":
                self._navmesh(record, path_id, path)
                continue
            if kind == "PlayableDirector":
                self._director(record, path_id, path)
                continue
            self._other_component(record, path_id, kind, path)
        return mesh_index

    def _geometry_of(self, record, graph, transform, path):
        """``(glTF mesh index, entry)`` for the geometry on one node."""
        components = dict((kind, path_id) for kind, path_id in graph.components(transform)
                          if kind in GEOMETRY_COMPONENTS)
        renderer_kind = ("MeshRenderer" if "MeshRenderer" in components
                         else "SkinnedMeshRenderer" if "SkinnedMeshRenderer" in components
                         else None)
        if renderer_kind is None:
            return None, None
        renderer = record.tree(components[renderer_kind])
        pointer = (renderer.get("m_Mesh") if renderer_kind == "SkinnedMeshRenderer"
                   else (record.tree(components["MeshFilter"]).get("m_Mesh")
                         if "MeshFilter" in components else None))
        slots = {}
        for index, material in enumerate(renderer.get("m_Materials") or []):
            gltf_material, document = self.builder.material(record, material)
            if gltf_material is not None:
                slots[index] = gltf_material
            elif (material or {}).get("m_PathID", 0):
                self.unsupported.append(
                    {"node": path, "component": renderer_kind, "slot": index,
                     "reason": "material pointer names a material no supplied "
                               "package holds",
                     "archive": self.store.archive_of(record, material)})
        mesh_index, reason = self.builder.mesh(record, pointer, slots)
        vertices, triangles = self.builder.mesh_size(record, pointer)
        if mesh_index is None:
            self.unsupported.append({"node": path, "component": renderer_kind,
                                     "reason": reason})
            return None, None
        if not renderer.get("m_Enabled", 1):
            self.disabled.append(path)
        entry = {"skinned": renderer_kind == "SkinnedMeshRenderer",
                 "enabled": bool(renderer.get("m_Enabled", 1)),
                 "vertices": vertices, "triangles": triangles}
        if entry["skinned"]:
            self.omitted.append(
                {"node": path, "component": renderer_kind,
                 "bones": len(renderer.get("m_Bones") or []),
                 "reason": "a skinned mesh is exported as its bind-pose geometry; "
                           "the skin weights and the skeleton that drive it are the "
                           "character domain's contract and are not re-implemented "
                           "here"})
        return mesh_index, entry

    def root(self, record, graph, transform, primary=False):
        """Walk one prefab root into a glTF scene; returns its entry."""
        node_paths = graph.node_paths(transform)
        indices = {}
        entry = {"name": graph.name(transform), "primary": primary, "nodes": 0,
                 "meshes": 0, "renderers": 0, "vertices": 0, "triangles": 0,
                 "assets": self.asset_paths(record, graph.owner[transform])}
        for current in graph.subtree(transform):
            game_object = graph.owner[current]
            path = node_paths.get(game_object, "")
            tree = graph.game_object(current)
            self._mark(record, game_object, "exported", "node")
            mesh_index, geometry = self._geometry_of(record, graph, current, path)
            index = self.builder.node(graph, current, mesh_index)
            node = self.builder.glb.g["nodes"][index]
            active = bool(tree.get("m_IsActive", 1))
            if not active:
                self.inactive.append(path)
                node["extras"] = dict(node.get("extras") or {}, active=False)
            if geometry is not None:
                entry["meshes"] += 1
                entry["renderers"] += 1
                entry["vertices"] += geometry["vertices"]
                entry["triangles"] += geometry["triangles"]
                if geometry["skinned"]:
                    node["extras"] = dict(node.get("extras") or {}, skinned=True)
                if not geometry["enabled"]:
                    node["extras"] = dict(node.get("extras") or {}, enabled=False)
            indices[current] = index
            parent = graph.father.get(current)
            if parent in indices:
                self.builder.glb.g["nodes"][parent := indices[parent]] \
                    .setdefault("children", []).append(index)
            entry["nodes"] += 1
            self._component(record, graph, current, path, node_paths,
                            root=entry["name"])
        scene = self.builder.scene(entry["name"], indices[transform])
        entry["scene"] = scene
        if primary:
            self.slots = self._slot_table(record, graph, transform, node_paths)
        return entry

    def _slot_table(self, record, graph, transform, node_paths):
        """The direct children of a site prefab root, with their stated role."""
        table = []
        for child in graph.children.get(transform, []):
            name = graph.name(child)
            subtree = graph.subtree(child)
            renderers = sum(1 for node in subtree
                            for kind, _ in graph.components(node)
                            if kind in ("MeshRenderer", "SkinnedMeshRenderer"))
            colliders = sum(1 for node in subtree
                            for kind, _ in graph.components(node)
                            if kind in COLLIDER_COMPONENTS)
            table.append({
                "name": name,
                "role": SLOT_ROLES.get(name),
                "known": name in SLOT_ROLES,
                "nodes": len(subtree), "renderers": renderers, "colliders": colliders,
                "active": bool(graph.game_object(child).get("m_IsActive", 1)),
                "path": node_paths.get(graph.owner[child], "")})
        return table

    # -- component readers ------------------------------------------------
    def _collider(self, record, path_id, kind, path, visible=False, root=None):
        entry = collider_document(self.store, record, path_id, kind, path)
        entry["root"] = root
        entry["visible"] = visible
        entry["role"] = surface_role(entry.get("mesh"), path.rsplit("/", 1)[-1])
        if kind == "MeshCollider" and entry.get("mesh"):
            target = self.store.follow(record, record.tree(path_id).get("m_Mesh") or {})
            entry.update(self._collision_file(*target))
        self.collision.append(entry)
        self._mark(record, path_id, "exported", "collision")

    def _collision_file(self, record, path_id):
        """Write one collision surface as its own glTF binary, once per mesh."""
        from .geometry import digest, single_mesh_blob
        key = (record.archive, path_id)
        if key in self._collision_written:
            return dict(self._collision_written[key])
        try:
            blob, vertices, triangles = single_mesh_blob(self.store, record, path_id)
        except Exception as exc:          # unreadable or non-triangle mesh
            reference = {"file": None, "reason": f"{type(exc).__name__}: {exc}"}
            self._collision_written[key] = reference
            return dict(reference)
        name = str(record.tree(path_id).get("m_Name", "") or "surface")
        stem = f"{name}-{digest(blob)[:8]}"
        directory = self.out / "collision"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{stem}.glb").write_bytes(blob)
        reference = {"file": f"collision/{stem}.glb", "sha256": digest(blob),
                     "bytes": len(blob), "vertices": vertices,
                     "triangles": triangles}
        self._collision_written[key] = reference
        self._mark(record, path_id, "exported", "collision surface")
        return dict(reference)

    def _particle_system(self, record, path_id, path, node_paths, graph):
        def resolve(pointer):
            pointer = pointer or {}
            if pointer.get("m_FileID", 0):
                return None
            tree = record.trees.get(pointer.get("m_PathID", 0))
            if tree is None:
                return None
            return node_paths.get((tree.get("m_GameObject") or {}).get("m_PathID", 0))
        try:
            system, gaps = decode_system(record.tree(path_id), resolve)
        except Exception as exc:          # unreadable emitter
            self.unsupported.append({"node": path, "component": "ParticleSystem",
                                     "reason": f"{type(exc).__name__}: {exc}"})
            self._mark(record, path_id, "unsupported", UNREADABLE)
            return
        entry = next((e for e in self.particles if e["node"] == path), None)
        if entry is None:
            entry = {"node": path}
            self.particles.append(entry)
        entry["system"] = system
        for gap in gaps:
            self.unsupported.append(dict(gap, node=path))
        self._mark(record, path_id, "exported", "emitter")

    def _particle_renderer(self, record, path_id, path):
        tree = record.tree(path_id)
        slots = len(tree.get("m_Materials") or [])
        material = self.builder.material(record, (tree.get("m_Materials") or [None])[0]
                                         if slots else None)[1]
        trail = (self.builder.material(record, tree["m_Materials"][TRAIL_MATERIAL_SLOT])[1]
                 if slots > TRAIL_MATERIAL_SLOT else None)
        entry = next((e for e in self.particles if e["node"] == path), None)
        if entry is None:
            entry = {"node": path}
            self.particles.append(entry)
        entry["renderer"] = decode_renderer(tree, material, trail)
        if entry["renderer"]["renderMode"] == MESH_RENDER_MODE:
            index, reason = self.builder.mesh(record, tree.get("m_Mesh") or {})
            entry["mesh"] = None if index is None else \
                self.builder.glb.g["meshes"][index]["name"]
            if index is None:
                self.unsupported.append({"node": path,
                                         "component": "ParticleSystemRenderer",
                                         "reason": reason})
        self._mark(record, path_id, "exported", "emitter renderer")

    def _navmesh(self, record, path_id, node=None):
        tree = record.tree(path_id)
        document, blob = navmesh_document(tree)
        document["node"] = node
        document["asset"] = self.asset_paths(record, path_id)
        directory = self.out / "navmesh"
        if blob:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "navmesh.bin").write_bytes(blob)
            document["tiles"]["file"] = "navmesh/navmesh.bin"
        for index, mesh in enumerate(tree.get("m_HeightMeshes") or []):
            blob, vertices, triangles = height_mesh_blob(mesh)
            if blob is None:
                document["heightMeshes"][index]["reason"] = NO_HEIGHT_MESH
                continue
            directory.mkdir(parents=True, exist_ok=True)
            name = f"heightmesh-{index}.glb"
            (directory / name).write_bytes(blob)
            document["heightMeshes"][index].update(
                file=f"navmesh/{name}", bytes=len(blob),
                exportedVertices=vertices, exportedTriangles=triangles)
        self.navmeshes.append(document)
        self._mark(record, path_id, "exported", "navigation mesh")

    def _director(self, record, path_id, node):
        """One `PlayableDirector`: whether its socket ships filled, and by what."""
        tree = record.tree(path_id)
        pointer = tree.get("m_PlayableAsset") or {}
        target = self.store.follow(record, pointer)
        bound = bool(pointer.get("m_PathID", 0))
        self.directors.append({
            "node": node, "bound": bound,
            "playableAsset": (str(target[0].tree(target[1]).get("m_Name", ""))
                              if target is not None else None),
            "sceneBindings": len((tree.get("m_SceneBindings") or [])),
            "exposedReferences": len(((tree.get("m_ExposedReferences") or {})
                                      .get("m_References") or [])),
            "wrapMode": tree.get("m_WrapMode"),
            "initialState": tree.get("m_InitialState"),
            "initialTime": round(float(tree.get("m_InitialTime", 0.0)), 6),
            "state": BOUND_SOCKET if bound else EMPTY_SOCKET})
        self._mark(record, path_id, "exported", "timeline socket")

    def _other_component(self, record, path_id, kind, node):
        """Every other component: its class, its node, and its fields verbatim."""
        try:
            fields = verbatim(self.store, record, path_id)
        except Exception as exc:          # unreadable component
            self.unsupported.append({"node": node, "component": kind,
                                     "reason": f"{UNREADABLE}: {type(exc).__name__}: {exc}"})
            self._mark(record, path_id, "unsupported", UNREADABLE)
            return
        name = kind
        if kind == "MonoBehaviour":
            name = record.script_of(path_id) or "<script not in package>"
        entry = self.components.setdefault(name, {"type": kind, "count": 0,
                                                  "instances": []})
        entry["count"] += 1
        entry["instances"].append({"node": node, "fields": fields})
        self._mark(record, path_id, "exported", "component fields")

    # -- loose assets -----------------------------------------------------
    def _loose(self, record, path_id, kind):
        if kind in SKIPPED_TYPES:
            self._mark(record, path_id, "skipped", SKIPPED_TYPES[kind])
            return
        if kind == "Mesh":
            if (record.archive, path_id) in self.builder.mesh_keys:
                self._mark(record, path_id, "exported", "geometry")
                return
            index, reason = self.builder.mesh(record, {"m_FileID": 0,
                                                       "m_PathID": path_id})
            if index is None:
                self.unsupported.append({"asset": str(record.tree(path_id)
                                                      .get("m_Name", "")),
                                         "reason": reason})
                self._mark(record, path_id, "unsupported", reason)
                return
            node = self.builder.glb.g["nodes"]
            node.append({"name": self.builder.glb.g["meshes"][index]["name"],
                         "mesh": index})
            scene = self.builder.scene(node[-1]["name"], len(node) - 1)
            self.extra_scenes.append({"name": node[-1]["name"], "scene": scene,
                                      "reason": MESH_UNREACHED})
            self._mark(record, path_id, "exported", MESH_UNREACHED)
            return
        if kind == "Material":
            key = (record.archive, path_id)
            if key not in self.builder._materials:
                self.materials.append(material_document(self.store, record, path_id,
                                                        self.textures))
            self._mark(record, path_id, "exported", "material")
            return
        if kind in ("Texture2D", "Sprite"):
            reference = self.textures.reference((record, path_id))
            self._mark(record, path_id,
                       "exported" if reference and reference.get("file") else "unsupported",
                       "image")
            return
        if kind == "AnimationClip":
            document, reason = clip_document(record, path_id, self.hashes)
            document["asset"] = self.asset_paths(record, path_id)
            if reason is not None:
                document["note"] = reason
            self.clips.append(document)
            failed = reason is not None and reason != NO_CURVES
            self._mark(record, path_id, "unsupported" if failed else "exported",
                       reason or "animation curves")
            if failed:
                self.unsupported.append({"asset": document["name"], "reason": reason})
            return
        if kind == "NavMeshData":
            self._navmesh(record, path_id)
            return
        if kind == "GameObject":
            self._mark(record, path_id, "unsupported",
                       "game object not reached from any root transform")
            self.unsupported.append(
                {"asset": str(record.tree(path_id).get("m_Name", "")),
                 "reason": "game object with no transform, so it is in no node tree"})
            return
        node = None
        for asset in self.asset_paths(record, path_id):
            node = asset
            break
        self._other_component(record, path_id, kind, node)

    # -- the run ----------------------------------------------------------
    def run(self):
        self._read_containers()
        primary = census.scene_name(self.name) or self.key
        self.hashes = {}
        for record in self.package.files:
            graph = Graph(record)
            self.hashes.update(path_hashes(graph))
            roots = graph.roots
            for transform in roots:
                is_primary = graph.name(transform) == primary and not any(
                    entry["primary"] for entry in self.roots)
                self.roots.append(self.root(record, graph, transform, is_primary))
        if self.roots and not any(entry["primary"] for entry in self.roots):
            self.roots[0]["primary"] = True
        for record in self.package.files:
            for path_id, kind in sorted(record.kinds.items()):
                if self._state(record, path_id) is not None:
                    continue
                self._loose(record, path_id, kind)
        return self.finish()

    def finish(self):
        default = next((entry["scene"] for entry in self.roots if entry["primary"]), 0)
        blob = self.builder.finish(default)
        document = {
            "package": self.name, "kind": self.kind, "key": self.key,
            "inventory": census.inventory(self.package),
            "geometry": None, "roots": self.roots, "extraScenes": self.extra_scenes,
            "slots": self.slots, "collision": self.collision,
            "navmesh": self.navmeshes,
            "materials": self.builder.materials + self.materials,
            "textures": sorted([reference["file"] for reference
                                in self.textures.written.values()
                                if reference.get("file")]
                               + [path for reference in self.textures.written.values()
                                  for path in reference.get("files") or []]),
            "components": {name: entry for name, entry
                           in sorted(self.components.items())},
            "particles": self.particles,
            "animations": {"clips": self.clips},
            "timelineSockets": self.directors,
            "environments": self.environments,
            "inactiveNodes": sorted(self.inactive),
            "disabledRenderers": sorted(self.disabled),
            "omitted": self.omitted,
            "unsupported": self.unsupported + [dict(gap, kind="image")
                                               for gap in self.textures.failed],
        }
        if self.builder.glb.g["meshes"]:
            from .geometry import digest
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / f"{self.stem}.glb").write_bytes(blob)
            document["geometry"] = {
                "file": f"{self.stem}.glb",
                "sha256": digest(blob), "bytes": len(blob),
                "scenes": [scene.get("name") for scene in self.builder.glb.g["scenes"]],
                "defaultScene": self.builder.glb.g["scene"],
                "meshes": len(self.builder.glb.g["meshes"]),
                "uniqueMeshes": len(self.builder._buffers),
                "vertices": self.builder.vertices,
                "triangles": self.builder.triangles}
        states = {}
        for state, reason in self.disposition.values():
            states[state] = states.get(state, 0) + 1
        document["objects"] = {
            "total": document["inventory"]["objects"],
            "exported": states.get("exported", 0),
            "skipped": states.get("skipped", 0),
            "unsupported": states.get("unsupported", 0),
            "accountedFor": sum(states.values()) == document["inventory"]["objects"]}
        document["skipped"] = sorted({reason for state, reason
                                      in self.disposition.values()
                                      if state == "skipped"})
        document["environments"] = self._environment_presets()
        write_json(self.out / f"{self.stem}.json", document)
        document["file"] = f"{self.prefix}/{self.stem}.json"
        return document

    def _environment_presets(self):
        """Per-phenomenon presets, which ship inside the site package itself.

        There is no per-phenomenon environment package to look in: the game builds
        such a name but the manifest ships none, and the presets live here, under
        `env/<phenomenon>/`.  They are grouped by that directory so a consumer can
        pick the set for the weather it is drawing.
        """
        presets = {}
        for name, entry in self.components.items():
            for instance in entry["instances"]:
                asset = str(instance.get("node") or "")
                if "/env/" not in asset:
                    continue
                phenomenon = asset.split("/env/", 1)[1].split("/", 1)[0]
                presets.setdefault(phenomenon, []).append(
                    {"class": name, "asset": asset})
        return {name: presets[name] for name in sorted(presets)}
