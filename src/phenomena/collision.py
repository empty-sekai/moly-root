"""Collision surfaces of weather effects, in the form the particle collision query reads.

Rain drops, meteors and night snow do not stop in the air: their emitters carry a
collision module in world mode whose layer mask selects the site's ground layer
alone, and the thing they hit is a ``MeshCollider`` that the site effect prefab
carries on that layer.  Nothing else in the environment packages is on that layer,
and the site packages carry no collider on it either, so for those emitters the
effect rows are the whole candidate set.  Soap bubbles collide with every layer.
No soap-bubble package carries a collider; what they hit is the site's own
colliders -- the site prefab of each scene package carries them on layers 0, 4 and
9 -- and the fixtures placed at run time, which are not in any package.  The site
prefab's colliders are recorded here too, one row each (``kind`` ``scene``), with
the same controls and the chain from the site prefab root; the site effect is
instantiated under the object carrying the site view, which is that root, so the
effect rows and the site rows share one frame.  Fixtures are not recorded.  A
collider in a ``common`` variant prefab is recorded like any other, but that
prefab is a template the site environment loader never instantiates: the one such
collider, in the rainbow's ground template, is copied into each of the rainbow's
site-unique prefabs, and nothing in its row marks it as a template.

What the query needs from the collider is not what the viewer needs from the same
mesh.  The render copy of the mesh (``meshes`` on the collider record, a glTF
binary) is reflected into the viewer's right-handed frame with its winding
reversed.  The physics engine is handed the authored mesh: the vertex positions as
stored, and the triangle list in index-buffer order, in the authored left-handed
frame.  The particle query does not ask for hits on both sides of a triangle mesh,
so which way a triangle winds decides whether a sweep can hit it at all; and it
takes the nearest hit, so the triangle set has to be exactly the authored one.
This module therefore carries that mesh as it was authored, keyed by its source
object, next to every collider that uses it, with the controls that decide whether
the shape exists and is hit:

* the collider's own switches (enabled, trigger, convex, cooking options, layer
  masks) and the layer of the object it sits on, which the query mask tests;
* the chain of local transforms from the prefab root down to the collider node,
  with each node's active flag: the engine places a static collider at its node's
  world pose and scales the mesh by the node's accumulated scale, and a collider
  under an inactive node is not in the scene.  The prefab root's own placement is
  decided where the effect is installed, not here;
* the source identity of the collider component and of the mesh, and the SHA-256
  of the package bytes each was read from.

What is deliberately not done: the engine cooks the mesh before querying it
(cooking options that include cleaning remove degenerate and duplicate triangles
and weld coincident vertices, and every mode builds its own acceleration
structure).  Cooking is the engine's, so the exported triangles are its *input*.
The facts a consumer needs to judge what cleaning could change -- exact duplicate
positions, triangles that repeat an index, triangles of zero area -- are counted
on the authored arrays and reported beside them, not applied.  A mesh can also
store a copy cooked when the game was built; its byte counts are carried per mesh
(``bakedPhysicsBytes``).  None of these meshes stores one, so the engine cooks each
of them itself when the collider is created, from the arrays carried here.  A convex collider
is queried against a hull the engine builds, which the authored mesh is not; no
such collider exists in these packages, and one would be reported, not approximated.
A mesh with more than one submesh is cooked from the engine's whole triangle list,
which it builds submesh by submesh in submesh order, each submesh's corners from its
first index byte with its base vertex added; that is the list carried here.  A mesh
whose vertex data is streamed from the package's resource file is read from that
file, as the engine reads it when it loads the mesh.
"""
import hashlib
import struct

from UnityPy.helpers.MeshHelper import MeshHandler

from core.colliders import COLLIDER_COMPONENTS, decode_collider
from core.gltf import SOURCE_SPACE

SCHEMA_VERSION = 1
UNITS = "source-unity-unit"

# Serialized cooking option bits, as the engine's public enumeration names them.
COOKING_OPTIONS = ((1 << 1, "CookForFasterSimulation"), (1 << 2, "EnableMeshCleaning"),
                   (1 << 3, "WeldColocatedVertices"), (1 << 4, "UseFastMidphase"))
OBSOLETE_COOKING_BIT = 1 << 0

# Vertex channel 0 is the position; format 0 is 32-bit float.
POSITION_CHANNEL, FLOAT32_FORMAT = 0, 0
TRIANGLES = 0
INDEX_WIDTH = {0: 2, 1: 4}          # serialized index format: 16-bit, 32-bit

SEMANTICS = {
    "frame": ("positions, transforms and triangle corners are in the authored "
              f"frame ({SOURCE_SPACE}, {UNITS}); nothing here is reflected into the "
              "viewer's frame, so a consumer that queries in the authored frame "
              "uses them as they stand"),
    "triangles": ("corner indices in the order the mesh's index buffer stores them, "
                  "base vertex applied; the winding is the authored one"),
    "geometrySha256": ("SHA-256 of the positions as little-endian 32-bit floats "
                       "(x, y, z per vertex, in vertex order) followed by the "
                       "triangle corners as little-endian 32-bit unsigned integers"),
    "chain": ("local transforms from the prefab root (path \"\") down to the "
              "collider node, parents first; `activeInHierarchy` is true only when "
              "every node on the chain is active. The root's own placement is set "
              "where the effect is installed"),
    "queryShape": ("`triangleMesh` for a non-convex mesh collider: the query runs "
                   "against the authored triangles after the engine's cooking, which "
                   "is not applied here (see `cooking`); `box` for a box collider: "
                   "the query runs against the box of `center` and `size` on the "
                   "collider node's pose"),
    "kind": ("the kind of the effect a row belongs to (`sky`, `camera`, `site`), or "
             "`scene` for a collider of a site scene package's site prefab "
             "(`phenomenon` and `effect` null, `variant` `scene`, `package` the scene "
             "package). A scene row's chain starts at the site prefab root, the object "
             "the site effects are instantiated under (`sites[<site>].siteView`), so "
             "scene rows and site effect rows share one frame"),
    "cleaningFacts": ("counted on the authored arrays, not applied: what mesh "
                      "cleaning could remove or weld if the cooking options ask for it. "
                      "`coincidentTriangles` counts triangles whose three corner "
                      "positions equal an earlier triangle's, which welding coincident "
                      "vertices would turn into duplicates"),
    "bakedPhysicsBytes": ("byte lengths of the pre-cooked physics copies the mesh "
                          "stores (`triangleMesh`, `convexMesh`); null when the serialized "
                          "mesh has no such field. 0 means the package holds no cooked "
                          "copy, and the engine cooks the carried arrays itself"),
    "instanceIds": ("the collider and body ids a query reports are runtime object "
                    "ids assigned when the effect is instantiated; they are not in "
                    "the packages"),
    "preloadFirst": ("the position of the collider component's first entry in its "
                     "package's asset bundle preload table (entries naming the "
                     "package's own file). Loading the bundle reads that table front "
                     "to back and numbers each object the first time it is named, and "
                     "a prefab clone wakes its colliders, each adding itself to the "
                     "physics scene, in ascending number of the originals; null when "
                     "the file holds no asset bundle object or the table does not "
                     "name the component"),
    "activationOrder": ("scene rows only: the collider's position in the order "
                        "activating the site prefab root adds its colliders to the "
                        "physics scene, each node's children (in the transform's child "
                        "order) before the node's own components (in the game object's "
                        "component order). The game hides a site right after it "
                        "instantiates it and shows it when the player enters, so the "
                        "colliders the player meets were added in this order"),
}

NOT_TRIANGLES = "mesh collider submesh is not a triangle list"
POSITION_FORMAT = ("mesh collider positions are not a plain 32-bit float stream; "
                   "only that encoding is carried")
COMPRESSED = "mesh collider mesh is compressed; only uncompressed meshes are carried"
STREAMED = ("mesh collider vertex data is streamed from a resource that did not "
            "yield the declared vertex count")
INDEX_RANGE = "mesh collider triangle corner is outside the vertex range"
INDEX_FORMAT = "mesh collider index format is not 16-bit or 32-bit"
CONVEX = ("convex mesh collider: the query runs against a hull the engine cooks from "
          "the mesh, which the authored mesh is not")
NO_MESH = "mesh collider names no mesh"
MESH_UNRESOLVED = "mesh collider mesh is not in any supplied package"
NOT_A_MESH = "mesh collider pointer does not name a mesh"
PRIMITIVE = ("primitive collider: its shape is carried on the collider record; "
             "no mesh geometry applies")
POINTER_GAPS = (NO_MESH, MESH_UNRESOLVED, NOT_A_MESH)


def cooking(options):
    """The serialized cooking option bits with their names, unknown bits kept."""
    if options is None:
        return None
    known = [name for bit, name in COOKING_OPTIONS if options & bit]
    rest = options & ~sum(bit for bit, _ in COOKING_OPTIONS) & ~OBSOLETE_COOKING_BIT
    return {"value": options, "flags": known,
            "obsoleteBit": bool(options & OBSOLETE_COOKING_BIT), "unknownBits": rest}


def _index_values(tree, handler_count):
    """Triangle corners of every submesh, straight from the serialized index buffer.

    Submesh by submesh in submesh order, each submesh's corners from its first
    index byte with its base vertex added: the list the engine builds for cooking.
    """
    submeshes = tree.get("m_SubMeshes") or []
    width = INDEX_WIDTH.get(tree.get("m_IndexFormat"))
    if width is None:
        raise ValueError(INDEX_FORMAT)
    raw = bytes(tree.get("m_IndexBuffer") or b"")
    code = "<H" if width == 2 else "<I"
    corners = []
    for submesh in submeshes:
        if submesh.get("topology", TRIANGLES) != TRIANGLES:
            raise ValueError(NOT_TRIANGLES)
        first = int(submesh.get("firstByte", 0))
        count = int(submesh.get("indexCount", 0))
        base = int(submesh.get("baseVertex", 0))
        if count % 3 or first + count * width > len(raw):
            raise ValueError(INDEX_RANGE)
        corners.extend(struct.unpack_from(code, raw, first + i * width)[0] + base
                       for i in range(count))
    if any(not 0 <= corner < handler_count for corner in corners):
        raise ValueError(INDEX_RANGE)
    return [corners[i:i + 3] for i in range(0, len(corners), 3)]


def _positions(mesh_object, tree):
    vertex_data = tree.get("m_VertexData") or {}
    channels = vertex_data.get("m_Channels") or []
    channel = channels[POSITION_CHANNEL] if channels else {}
    if channel.get("format") != FLOAT32_FORMAT or channel.get("dimension") != 3:
        raise ValueError(POSITION_FORMAT)
    if ((tree.get("m_CompressedMesh") or {}).get("m_Vertices") or {}).get("m_NumItems"):
        raise ValueError(COMPRESSED)
    streamed = ((tree.get("m_StreamData") or {}).get("size") or 0) > 0
    handler = MeshHandler(mesh_object.read())
    handler.process()
    positions = [[float(v[0]), float(v[1]), float(v[2])] for v in handler.m_Vertices]
    if len(positions) != int(vertex_data.get("m_VertexCount", -1)):
        raise ValueError(STREAMED if streamed else POSITION_FORMAT)
    return positions


def _cleaning_facts(positions, triangles):
    seen, duplicates = set(), 0
    for position in positions:
        key = tuple(position)
        duplicates += key in seen
        seen.add(key)
    repeated = sum(1 for a, b, c in triangles if a == b or b == c or a == c)
    zero_area = 0
    for a, b, c in triangles:
        pa, pb, pc = positions[a], positions[b], positions[c]
        u = [pb[i] - pa[i] for i in range(3)]
        v = [pc[i] - pa[i] for i in range(3)]
        cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                 u[0] * v[1] - u[1] * v[0])
        zero_area += cross == (0.0, 0.0, 0.0)
    referenced = {corner for triangle in triangles for corner in triangle}
    shapes, coincident = set(), 0
    for triangle in triangles:
        key = frozenset(tuple(positions[corner]) for corner in triangle)
        coincident += key in shapes
        shapes.add(key)
    return {"duplicatePositions": duplicates, "repeatedIndexTriangles": repeated,
            "zeroAreaTriangles": zero_area, "coincidentTriangles": coincident,
            "unreferencedVertices": len(positions) - len(referenced),
            "arithmetic": "exact float equality; areas in double precision"}


def _baked_bytes(tree):
    return {name: (len(bytes(tree.get(key) or b"")) if key in tree else None)
            for name, key in (("triangleMesh", "m_BakedTriangleCollisionMesh"),
                              ("convexMesh", "m_BakedConvexCollisionMesh"))}


def _vec(value, keys):
    return [float(value.get(key, 0.0)) for key in keys] if isinstance(value, dict) else None


def geometry_record(mesh_object, tree):
    """The authored collision mesh of one Mesh object, or raise ValueError."""
    positions = _positions(mesh_object, tree)
    triangles = _index_values(tree, len(positions))
    packed = (b"".join(struct.pack("<3f", *p) for p in positions)
              + b"".join(struct.pack("<3I", *t) for t in triangles))
    box = tree.get("m_LocalAABB") or {}
    return {
        "name": str(tree.get("m_Name") or "mesh"),
        "frame": SOURCE_SPACE, "units": UNITS,
        "vertexCount": len(positions), "triangleCount": len(triangles),
        "indexFormat": tree.get("m_IndexFormat"),
        "streamed": ((tree.get("m_StreamData") or {}).get("size") or 0) > 0,
        "subMeshes": [{key: submesh.get(key) for key in
                       ("firstByte", "indexCount", "topology", "baseVertex",
                        "firstVertex", "vertexCount")}
                      for submesh in tree.get("m_SubMeshes") or []],
        "authoredBounds": ({"center": _vec(box.get("m_Center"), "xyz"),
                            "extents": _vec(box.get("m_Extent"), "xyz")}
                           if "m_Center" in box else None),
        "vertexBounds": {"min": [min(p[i] for p in positions) for i in range(3)],
                         "max": [max(p[i] for p in positions) for i in range(3)]},
        "cleaningFacts": _cleaning_facts(positions, triangles),
        "bakedPhysicsBytes": _baked_bytes(tree),
        "geometrySha256": hashlib.sha256(packed).hexdigest(),
        "positions": positions, "triangles": triangles,
    }


def _component_ids(tree):
    """Component path ids of one game object, in either serialized form."""
    for entry in (tree or {}).get("m_Component") or []:
        pointer = entry.get("component", entry) if isinstance(entry, dict) else entry
        yield (pointer or {}).get("m_PathID", 0)


def activation_order(root_game_object, kinds, trees):
    """Each collider component under a root, numbered in the order activating the
    root adds it: children first (transform child order), then the node's own
    components (component order)."""
    order = {}
    transform_kinds = ("Transform", "RectTransform")
    stack = [(root_game_object, False)]
    seen = set()
    while stack:
        game_object, expanded = stack.pop()
        components = _component_ids(trees.get(game_object, {}))
        if expanded:
            for component in components:
                if kinds.get(component) in COLLIDER_COMPONENTS:
                    order.setdefault(component, len(order))
            continue
        if game_object in seen:
            continue
        seen.add(game_object)
        stack.append((game_object, True))
        transform = next((c for c in components if kinds.get(c) in transform_kinds), None)
        children = (trees[transform].get("m_Children") or []) if transform is not None else []
        for child in reversed(children):
            child_id = (child or {}).get("m_PathID", 0)
            if kinds.get(child_id) in transform_kinds:
                owner = (trees[child_id].get("m_GameObject") or {}).get("m_PathID", 0)
                stack.append((owner, False))
    return order


def chain(nodes, node_path):
    """Local transforms from the prefab root down to *node_path*, parents first."""
    by_path = {node["path"]: node for node in nodes}
    parts = node_path.split("/") if node_path else []
    paths = [""] + ["/".join(parts[:i + 1]) for i in range(len(parts))]
    if any(path not in by_path for path in paths):
        return None
    return [{"path": path, "active": by_path[path]["active"],
             "layer": by_path[path].get("layer"),
             "position": by_path[path]["position"],
             "rotation": by_path[path]["rotation"],
             "scale": by_path[path]["scale"]} for path in paths]


class CollisionTable:
    """Every collider of one run, and each collider mesh once per source object."""

    def __init__(self, store):
        self.store = store
        self.geometry = {}          # geometry id -> record
        self.by_source = {}         # (archive, path id) -> (geometry id or None, gap)
        self.colliders = []
        self.sites = {}             # site -> its scene package, root and site view
        self._receipts = {}
        self._preload = {}          # archive -> {path id: first preload-table position}

    def _preload_first(self, record, path_id):
        """Where the object first appears in its file's asset bundle preload table."""
        if record.archive not in self._preload:
            first = {}
            bundles = [pid for pid, kind in record.kinds.items() if kind == "AssetBundle"]
            if len(bundles) == 1:
                for position, pointer in enumerate(record.tree(bundles[0]).get("m_PreloadTable") or []):
                    pointer = pointer or {}
                    if not pointer.get("m_FileID", 0):
                        first.setdefault(pointer.get("m_PathID", 0), position)
            self._preload[record.archive] = first
        return self._preload[record.archive].get(int(path_id))

    def _receipt(self, bundle):
        if bundle not in self._receipts:
            try:
                self._receipts[bundle] = self.store.source_receipt(bundle)
            except FileNotFoundError as exc:
                self._receipts[bundle] = {"bundle": bundle, "sha256": None,
                                          "reason": str(exc)}
        return self._receipts[bundle]

    def _geometry(self, record, pointer):
        if not (pointer or {}).get("m_PathID"):
            return None, NO_MESH
        target = self.store.follow(record, pointer)
        if target is None:
            return None, MESH_UNRESOLVED
        mesh_record, mesh_id = target
        key = (mesh_record.archive, mesh_id)
        if key in self.by_source:
            return self.by_source[key]
        if mesh_record.kinds.get(mesh_id) != "Mesh":
            self.by_source[key] = (None, NOT_A_MESH)
            return self.by_source[key]
        try:
            document = geometry_record(mesh_record.objects[mesh_id], mesh_record.tree(mesh_id))
        except ValueError as exc:
            self.by_source[key] = (None, str(exc))
            return self.by_source[key]
        identity = f"{document['name']}-{document['geometrySha256'][:8]}"
        if identity in self.geometry:
            identity = f"{identity}-{mesh_id}"
        document["source"] = {"bundle": mesh_record.bundle, "archive": mesh_record.archive,
                              "pathId": str(mesh_id),
                              "bundleSha256": self._receipt(mesh_record.bundle)["sha256"]}
        self.geometry[identity] = dict(document, id=identity)
        self.by_source[key] = (identity, None)
        return self.by_source[key]

    def add_effect(self, phenomenon, name, effect, record):
        """Record every collider of one decoded effect; returns the gaps found."""
        gaps = []
        for entry in effect.get("colliders") or []:
            if entry.get("component") not in COLLIDER_COMPONENTS:
                continue
            entry["collisionGeometry"] = self._row(
                {"phenomenon": phenomenon, "effect": name, "kind": effect.get("kind"),
                 "site": effect.get("site"), "variant": effect.get("variant")},
                entry, effect.get("nodes") or [], record, gaps)
        return gaps

    def add_site(self, site, package):
        """Record every collider of one site scene package's site prefab.

        The site prefab is the package root named after the site (the scene the
        game places); the site effects are instantiated under the object carrying
        the site view, reported per site.  A collider under another root of the
        package is recorded with a reason: nothing establishes where it stands.
        Returns the gaps found.
        """
        from .effects import hierarchy
        gaps = []
        summary = {"package": package.name, "root": None, "siteView": None,
                   "bundleSha256": self._receipt(package.name)["sha256"], "colliders": 0}
        for record in package.files:
            kinds, trees = record.kinds, record.trees
            roots = {}
            for path_id, kind in kinds.items():
                if kind in ("Transform", "RectTransform") \
                        and not (trees[path_id].get("m_Father") or {}).get("m_PathID"):
                    game_object = (trees[path_id].get("m_GameObject") or {}).get("m_PathID", 0)
                    roots[game_object] = str(trees.get(game_object, {}).get("m_Name", ""))
            for root_id, root_name in sorted(roots.items(), key=lambda item: (item[1], item[0])):
                nodes, node_of = hierarchy(root_id, kinds, trees)
                placed = root_name == site
                activation = activation_order(root_id, kinds, trees) if placed else {}
                if placed:
                    summary["root"] = root_name
                    for path_id, kind in kinds.items():
                        if kind == "MonoBehaviour" and record.script_of(path_id) == "MysekaiSiteView":
                            owner = (trees[path_id].get("m_GameObject") or {}).get("m_PathID", 0)
                            if owner in node_of:
                                summary["siteView"] = node_of[owner]
                paths = [node["path"] for node in nodes]
                for game_object, path in sorted(node_of.items(), key=lambda item: item[1]):
                    for component in _component_ids(trees.get(game_object, {})):
                        kind = kinds.get(component)
                        if kind not in COLLIDER_COMPONENTS:
                            continue
                        entry = decode_collider(trees[component], trees.get(game_object, {}), kind, path)
                        entry["componentPathId"] = str(component)
                        reason = None
                        if not placed:
                            reason = ("collider under a package root other than the site "
                                      "prefab: where it stands is not established")
                        elif paths.count(path) != 1:
                            reason = "collider node path is not unique in the site prefab"
                        self._row({"phenomenon": None, "effect": None, "kind": "scene",
                                   "site": site, "variant": "scene", "package": package.name,
                                   "root": root_name},
                                  entry, nodes, record, gaps, reason)
                        self.colliders[-1]["activationOrder"] = activation.get(component)
                        summary["colliders"] += 1
        if summary["root"] is None:
            gaps.append({"site": site, "package": package.name,
                         "reason": "no package root is named after the site"})
        self.sites[site] = summary
        return gaps

    def _row(self, identity, entry, nodes, record, gaps, reason=None):
        """One collider row; returns its geometry id."""
        kind = entry.get("component")
        geometry, gap = None, None
        if kind == "MeshCollider":
            pointer = record.tree(int(entry["componentPathId"])).get("m_Mesh") or {}
            geometry, gap = self._geometry(record, pointer)
            if entry.get("convex"):
                geometry, gap = None, CONVEX
        elif kind != "BoxCollider":
            gap = PRIMITIVE
        links = chain(nodes, entry["node"])
        row = dict(identity, node=entry["node"], component=kind)
        row.update({"queryShape": ("triangleMesh" if kind == "MeshCollider"
                                   and not entry.get("convex")
                                   else "box" if kind == "BoxCollider" else None),
                    "layer": entry.get("layer"), "enabled": entry.get("enabled"),
                    "isTrigger": entry.get("isTrigger"), "convex": entry.get("convex"),
                    "cooking": cooking(entry.get("cookingOptions")),
                    "includeLayers": entry.get("includeLayers"),
                    "excludeLayers": entry.get("excludeLayers"),
                    "activeInHierarchy": (all(link["active"] for link in links)
                                          if links is not None else None),
                    "chain": links, "geometry": geometry,
                    "preloadFirst": self._preload_first(record, entry["componentPathId"]),
                    "source": {"bundle": record.bundle, "archive": record.archive,
                               "pathId": entry["componentPathId"],
                               "bundleSha256": self._receipt(record.bundle)["sha256"]}})
        for field in ("center", "size", "radius", "height", "direction"):
            if field in entry:
                row[field] = entry[field]
        if links is None:
            gap = gap or "collider node chain does not reach the prefab root"
        gap = reason or gap
        if gap is not None and gap != PRIMITIVE:
            row["reason"] = gap
            # A pointer that does not reach a mesh is already reported by the
            # effect decode, which resolves the same pointer for the render copy;
            # a site package has no such decode, so its pointer gaps are reported.
            if gap not in POINTER_GAPS or identity.get("kind") == "scene":
                gaps.append({"node": entry["node"], "component": kind, "reason": gap,
                             **({"site": identity["site"]} if identity.get("kind") == "scene" else {})})
        self.colliders.append(row)
        return geometry

    def document(self):
        return {"schemaVersion": SCHEMA_VERSION, "frame": SOURCE_SPACE, "units": UNITS,
                "semantics": SEMANTICS,
                "summary": {"colliders": len(self.colliders),
                            "effectColliders": sum(row["effect"] is not None
                                                   for row in self.colliders),
                            "sceneColliders": sum(row["kind"] == "scene"
                                                  for row in self.colliders),
                            "meshColliders": sum(row["component"] == "MeshCollider"
                                                 for row in self.colliders),
                            "withGeometry": sum(row["geometry"] is not None
                                                for row in self.colliders),
                            "geometry": len(self.geometry),
                            "byLayer": {str(layer): sum(row["layer"] == layer
                                                        for row in self.colliders)
                                        for layer in sorted({row["layer"] for row in self.colliders},
                                                            key=str)}},
                "sites": {site: self.sites[site] for site in sorted(self.sites)},
                "geometry": {key: self.geometry[key] for key in sorted(self.geometry)},
                "colliders": self.colliders}
