"""Unity meshes as glTF meshes.

One Unity mesh becomes one glTF mesh with a primitive per submesh: submeshes are
the material slots of a renderer, so collapsing them would lose which part of the
geometry each material paints.  Vertex attributes are shared by every primitive of
the mesh — glTF addresses them per primitive but Unity stores one vertex stream —
so the accessors are written once and referenced.

Coordinate handedness and winding are handled by :mod:`core.gltf`.
"""
import struct

from UnityPy.helpers.MeshHelper import MeshHandler

from .gltf import flip_winding, unity_to_gltf_pos

# glTF component types and buffer targets.
FLOAT, UNSIGNED_INT = 5126, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963

TRIANGLES = 0

NOT_TRIANGLES = "mesh submesh is not a triangle list"
INDEX_RANGE = "mesh submesh index out of range"


def mesh_accessors(glb, mesh_object, tree):
    """Write one Unity mesh's buffers into *glb* and return how to reference them.

    Vertex attributes are one stream in Unity and are shared by every submesh, so
    they are written once; the submeshes get one index accessor each.  Split out
    from :func:`add_mesh` because the same geometry is drawn with different
    materials in one package, and glTF binds the material into the primitive:
    composing a second mesh from these accessors costs a few bytes of JSON,
    whereas re-adding the mesh would copy the whole vertex buffer.

    Returns ``{"attributes", "submeshes", "vertices", "triangles", "name"}``.
    """
    handler = MeshHandler(mesh_object.read())
    handler.process()
    count = handler.m_VertexCount
    positions = [unity_to_gltf_pos(v) for v in handler.m_Vertices]
    bounds = ([min(p[axis] for p in positions) for axis in range(3)],
              [max(p[axis] for p in positions) for axis in range(3)])
    attributes = {"POSITION": glb.acc(
        b"".join(struct.pack("<3f", *p) for p in positions),
        FLOAT, "VEC3", count, ARRAY_BUFFER, bounds)}
    if handler.m_Normals:
        attributes["NORMAL"] = glb.acc(
            b"".join(struct.pack("<3f", *unity_to_gltf_pos(v))
                     for v in handler.m_Normals),
            FLOAT, "VEC3", count, ARRAY_BUFFER)
    for slot, values in (("TEXCOORD_0", handler.m_UV0),
                         ("TEXCOORD_1", getattr(handler, "m_UV1", None))):
        if values:
            attributes[slot] = glb.acc(
                b"".join(struct.pack("<2f", v[0], 1.0 - v[1]) for v in values),
                FLOAT, "VEC2", count, ARRAY_BUFFER)
    colors = getattr(handler, "m_Colors", None)
    if colors:
        attributes["COLOR_0"] = glb.acc(
            b"".join(struct.pack("<4f", *c[:4]) for c in colors),
            FLOAT, "VEC4", count, ARRAY_BUFFER)

    indices = list(handler.m_IndexBuffer)
    submeshes, triangles, base = [], 0, 0
    for submesh in tree.get("m_SubMeshes") or []:
        if submesh.get("topology", TRIANGLES) != TRIANGLES:
            raise ValueError(NOT_TRIANGLES)
        length = int(submesh.get("indexCount", 0))
        part = flip_winding(indices[base:base + length])
        base += length
        if part and max(part) >= count:
            raise ValueError(INDEX_RANGE)
        submeshes.append(glb.acc(
            b"".join(struct.pack("<I", int(v)) for v in part),
            UNSIGNED_INT, "SCALAR", length, ELEMENT_ARRAY_BUFFER))
        triangles += length // 3
    return {"name": str(tree.get("m_Name") or "mesh"), "attributes": attributes,
            "submeshes": submeshes, "vertices": count, "triangles": triangles}


def compose_mesh(glb, buffers, name=None, materials=None):
    """Add a glTF mesh built from already-written *buffers*, one primitive per
    submesh, optionally binding a material index per submesh."""
    primitives = []
    for index, accessor in enumerate(buffers["submeshes"]):
        primitive = {"attributes": buffers["attributes"], "indices": accessor}
        material = (materials or {}).get(index)
        if material is not None:
            primitive["material"] = material
        primitives.append(primitive)
    glb.g["meshes"].append({"name": str(name or buffers["name"]),
                            "primitives": primitives})
    return len(glb.g["meshes"]) - 1


def add_mesh(glb, mesh_object, tree, name=None, materials=None):
    """Add one Unity mesh to *glb* as a glTF mesh, one primitive per submesh.

    *materials* optionally maps a submesh index to a glTF material index, so a
    consumer's viewer draws the authored material slots rather than plain grey.

    Returns ``(mesh index, vertex count, triangle count)``.
    """
    buffers = mesh_accessors(glb, mesh_object, tree)
    index = compose_mesh(glb, buffers, name, materials)
    return index, buffers["vertices"], buffers["triangles"]
