"""Static geometry of a phenomenon: model assets and the meshes particles draw.

Two kinds of geometry sit in these packages next to the emitters.

A **model asset** is a small node tree — a sky dome, a cloud ring, a rainbow fan, a
milky-way plane — with a mesh on some of its nodes.  It is exported as one glTF
binary per asset, node transforms and all, so the shape a consumer draws is the
authored shape rather than an approximation of it.

A **mesh-mode emitter** draws its particles as copies of one mesh instead of as
camera-facing quads, so without that mesh the emitter cannot be drawn at all.  Those
meshes are exported the same way, as a single-mesh file.

The same geometry is reached from several phenomena — packages share meshes across
phenomena and a mesh can also be repeated inside one package — so files are written
once and named after their content: identical geometry always lands in the same file,
a second reference to it adds a pointer rather than a copy, and two different meshes
that happen to share a name stay separate.  Nothing that varies per phenomenon is
recorded in a shared file's entry, so a shared file means the same thing to every
phenomenon that points at it.

Some emitters point at the engine's own built-in primitives, which no package ships;
those stay visible as unresolved pointers instead of being replaced by a guess.
"""
import hashlib
from pathlib import Path

from core.gltf import GLB, unity_to_gltf_pos, unity_to_gltf_quat
from core.mesh import add_mesh  # noqa: F401  (re-exported: callers import it here)

NOT_IN_PACKAGE = ("mesh is not in any supplied package: the pointer names the "
                  "engine's own built-in resources, which no package ships")


def _vec(node, keys):
    return [float(node.get(key, 0.0)) for key in keys]


def _components(node_tree):
    for entry in node_tree.get("m_Component") or []:
        if isinstance(entry, dict):
            pointer = entry.get("component", entry)
            yield (pointer or {}).get("m_PathID", 0)


def single_mesh(mesh_object, tree, name=None):
    """One mesh on one node: ``(glb, document)``."""
    glb = GLB()
    mesh_name = str(name or tree.get("m_Name") or "mesh")
    _, vertices, triangles = add_mesh(glb, mesh_object, tree, mesh_name)
    glb.g["nodes"].append({"name": mesh_name, "mesh": 0})
    glb.g["scenes"][0]["nodes"] = [0]
    return glb, {"name": mesh_name, "nodes": 1, "vertices": vertices,
                 "triangles": triangles, "meshes": [{"node": mesh_name,
                                                     "mesh": mesh_name,
                                                     "vertices": vertices,
                                                     "triangles": triangles}]}


def model(root_id, kinds, trees, follow, material_name=None):
    """One model asset: its node tree with the meshes sitting on it.

    *follow* resolves a mesh pointer to ``(record, path id)`` or ``None``;
    *material_name* names the material of a renderer, which is a property of the
    asset itself and so is recorded by name only.  Returns
    ``(glb, document, unsupported)``.
    """
    glb = GLB()
    unsupported = []
    owner, transform_of = {}, {}
    for path_id, kind in kinds.items():
        if kind not in ("Transform", "RectTransform"):
            continue
        game_object = (trees[path_id].get("m_GameObject") or {}).get("m_PathID", 0)
        owner[path_id] = game_object
        transform_of[game_object] = path_id
    children = {}
    for path_id in owner:
        father = (trees[path_id].get("m_Father") or {}).get("m_PathID", 0)
        children.setdefault(father, []).append(path_id)

    document = {"name": str(trees.get(root_id, {}).get("m_Name", "")), "nodes": 0,
                "vertices": 0, "triangles": 0, "meshes": [], "materials": []}
    root_transform = transform_of.get(root_id)
    if root_transform is None:
        return glb, document, unsupported

    queue = [(root_transform, None)]
    while queue:
        transform, parent = queue.pop(0)
        game_object = owner[transform]
        local = trees[transform]
        node_tree = trees.get(game_object, {})
        node = {"name": str(node_tree.get("m_Name", "")),
                "translation": list(unity_to_gltf_pos(
                    _vec(local.get("m_LocalPosition") or {}, "xyz"))),
                "rotation": list(unity_to_gltf_quat(
                    _vec(local.get("m_LocalRotation") or {}, "xyzw"))),
                "scale": _vec(local.get("m_LocalScale") or {}, "xyz")}
        glb.g["nodes"].append(node)
        index = len(glb.g["nodes"]) - 1
        if parent is not None:
            glb.g["nodes"][parent].setdefault("children", []).append(index)

        components = list(_components(node_tree))
        filters = [c for c in components if kinds.get(c) == "MeshFilter"]
        renderers = [c for c in components if kinds.get(c) == "MeshRenderer"]
        if filters and renderers:
            pointer = trees[filters[0]].get("m_Mesh") or {}
            target = follow(pointer)
            if target is None:
                unsupported.append({"node": node["name"], "reason": NOT_IN_PACKAGE,
                                    "pointer": {"fileId": pointer.get("m_FileID"),
                                                "pathId": pointer.get("m_PathID")}})
            else:
                record, mesh_id = target
                mesh_tree = record.tree(mesh_id)
                mesh_name = str(mesh_tree.get("m_Name") or "mesh")
                node["mesh"], vertices, triangles = add_mesh(
                    glb, record.objects[mesh_id], mesh_tree, mesh_name)
                document["vertices"] += vertices
                document["triangles"] += triangles
                document["meshes"].append({"node": node["name"], "mesh": mesh_name,
                                           "vertices": vertices,
                                           "triangles": triangles})
            if material_name is not None:
                document["materials"].append(
                    {"node": node["name"],
                     "material": material_name(trees[renderers[0]])})
        for child in sorted(children.get(transform, [])):
            queue.append((child, index))
    glb.g["scenes"][0]["nodes"] = [0]
    document["nodes"] = len(glb.g["nodes"])
    return glb, document, unsupported


class Store:
    """Geometry files of one run, written once per distinct content."""

    def __init__(self, directory, prefix):
        self.directory = Path(directory)
        self.prefix = prefix                   # path of the directory in the index
        self.written = {}                      # content digest -> reference
        self.by_key = {}                       # (archive, path id) -> reference
        self.entries = []                      # index-level list, in write order

    def _write(self, glb, name, document):
        blob = glb.blob()
        digest = hashlib.sha256(blob).hexdigest()
        if digest not in self.written:
            file_name = f"{name}-{digest[:8]}.glb"
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / file_name).write_bytes(blob)
            self.written[digest] = dict(document, file=f"{self.prefix}/{file_name}",
                                        sha256=digest, bytes=len(blob))
            self.entries.append(self.written[digest])
        return dict(self.written[digest])

    def mesh(self, record, path_id):
        """Reference to one mesh, exported as a single-mesh file."""
        key = (record.archive, path_id)
        if key not in self.by_key:
            tree = record.tree(path_id)
            glb, document = single_mesh(record.objects[path_id], tree)
            self.by_key[key] = self._write(glb, document["name"], document)
        return dict(self.by_key[key])

    def model(self, record, path_id, follow, material_name=None, asset=None):
        """Reference to one model asset, exported as a node tree."""
        key = (record.archive, path_id)
        if key in self.by_key:
            return dict(self.by_key[key]), []
        glb, document, unsupported = model(path_id, record.kinds, record.trees,
                                          follow, material_name)
        stem = str(asset or document["name"] or "model").rsplit(".", 1)[0]
        self.by_key[key] = self._write(glb, stem, document)
        return dict(self.by_key[key]), unsupported
