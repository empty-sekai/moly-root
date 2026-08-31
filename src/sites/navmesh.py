"""Baked navigation meshes: transported whole, and their height mesh exported.

Five of the nine sites ship a **baked** navigation mesh; the rest are built at
runtime from the collision surfaces under a site's ``navmesh_target`` node.  So the
honest record has two halves, and neither may be presented as the other: a site
with a baked mesh gets its bake, and a site without one is marked as building its
own rather than being quietly given an empty one.

A baked mesh is a Detour tile set.  Re-solving it would mean reimplementing Unity's
navigation baker, and guessing at its tile format would mean inventing data, so the
tiles are carried across **as bytes** — one blob plus an index of offsets, lengths
and the per-tile hash — and marked unparsed.  The bake settings beside them are the
part a consumer can act on: they say what agent the surface was baked for, and one
of them, the agent height, matches the player-height constant exactly, five times
over.

What *is* geometry is the **height mesh**: a vertex and index buffer of the walkable
surface, present in one of the five bakes.  That is exported as a glTF binary, so a
consumer can draw or sample the walkable surface without a navigation runtime.

Like the scene geometry, a baked mesh is site-local: every one of them ships at
position zero with an identity rotation, so a consumer must offset it by the site's
own world position exactly as it offsets the scene.
"""
import struct

from core.gltf import GLB, flip_winding, unity_to_gltf_pos
from core.mesh import ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER, FLOAT, UNSIGNED_INT

TILES_UNPARSED = ("navigation tiles are carried across as bytes: the tile format is "
                  "Unity's own baked Detour data, and this repository neither parses "
                  "it nor guesses at it")
RUNTIME_BAKED = ("this package ships no baked navigation mesh; the site's walkable "
                 "surface is built at runtime from the collision surfaces under "
                 "`navmesh_target`")
NO_HEIGHT_MESH = "this bake carries no height mesh, so it has no walkable geometry"


def _hash_bytes(node):
    """Unity serialises a 16-byte hash as ``bytes[0]``…``bytes[15]``."""
    if not isinstance(node, dict):
        return ""
    values = []
    for index in range(16):
        value = node.get(f"bytes[{index}]")
        if value is None:
            break
        values.append(int(value) & 0xFF)
    return bytes(values).hex()


def _vector(node):
    return [float((node or {}).get(axis, 0.0)) for axis in "xyz"]


def height_mesh_blob(mesh):
    """One height mesh as a glTF binary: ``(blob, vertices, triangles)``.

    The buffer is triangles of the walkable surface.  The bounding-volume nodes
    beside it are a query index, not geometry, and are reported by count rather
    than exported: a consumer that wants to sample the surface can build its own
    index over the triangles, and Unity's node layout is not documented here.
    """
    vertices = mesh.get("m_Vertices") or []
    indices = [int(value) for value in mesh.get("m_Indices") or []]
    if not vertices or not indices:
        return None, 0, 0
    glb = GLB()
    positions = [unity_to_gltf_pos(_vector(vertex)) for vertex in vertices]
    bounds = ([min(p[axis] for p in positions) for axis in range(3)],
              [max(p[axis] for p in positions) for axis in range(3)])
    position = glb.acc(b"".join(struct.pack("<3f", *p) for p in positions),
                       FLOAT, "VEC3", len(positions), ARRAY_BUFFER, bounds)
    inside = [value for value in indices if 0 <= value < len(positions)]
    wound = flip_winding(inside[:len(inside) - len(inside) % 3])
    index = glb.acc(b"".join(struct.pack("<I", value) for value in wound),
                    UNSIGNED_INT, "SCALAR", len(wound), ELEMENT_ARRAY_BUFFER)
    glb.g["meshes"].append({"name": "heightmesh",
                            "primitives": [{"attributes": {"POSITION": position},
                                            "indices": index}]})
    glb.g["nodes"].append({"name": "heightmesh", "mesh": 0})
    glb.g["scenes"][0]["nodes"] = [0]
    glb.g["scenes"][0]["name"] = "heightmesh"
    return glb.blob(), len(positions), len(wound) // 3


def navmesh_document(tree):
    """One ``NavMeshData`` asset: settings, placement, tiles, height meshes.

    ``tiles`` is the index into the blob a caller writes; the blob itself is
    returned beside the document because the document has to name its own file.
    """
    settings = dict(tree.get("m_NavMeshBuildSettings") or {})
    tiles, blob = [], bytearray()
    for entry in tree.get("m_NavMeshTiles") or []:
        data = bytes(int(value) & 0xFF for value in entry.get("m_MeshData") or [])
        tiles.append({"offset": len(blob), "bytes": len(data),
                      "hash": _hash_bytes(entry.get("m_Hash"))})
        blob += data
    bounds = tree.get("m_SourceBounds") or {}
    document = {
        "name": str(tree.get("m_Name", "")),
        "agentTypeID": tree.get("m_AgentTypeID"),
        "position": _vector(tree.get("m_Position")),
        "rotation": [float((tree.get("m_Rotation") or {}).get(axis, 0.0))
                     for axis in "xyzw"],
        "siteLocal": (_vector(tree.get("m_Position")) == [0.0, 0.0, 0.0]
                      and float((tree.get("m_Rotation") or {}).get("w", 1.0)) == 1.0),
        "sourceBounds": {"center": _vector(bounds.get("m_Center")),
                         "extent": _vector(bounds.get("m_Extent"))},
        "buildSettings": {key: (float(value)
                                if isinstance(value, float) else value)
                          for key, value in settings.items()},
        "tiles": {"count": len(tiles), "bytes": len(blob), "parsed": False,
                  "reason": TILES_UNPARSED, "index": tiles, "file": None},
        "heightMeshes": [], "offMeshLinks": len(tree.get("m_OffMeshLinks") or []),
        "heightmaps": len(tree.get("m_Heightmaps") or []),
    }
    for mesh in tree.get("m_HeightMeshes") or []:
        document["heightMeshes"].append({
            "vertices": len(mesh.get("m_Vertices") or []),
            "indices": len(mesh.get("m_Indices") or []),
            "bvhNodes": len(mesh.get("m_Nodes") or []),
            "bounds": {"center": _vector((mesh.get("m_Bounds") or {}).get("m_Center")),
                       "extent": _vector((mesh.get("m_Bounds") or {}).get("m_Extent"))},
            "file": None})
    return document, bytes(blob)
