"""Unity meshes as glTF meshes.

One Unity mesh becomes one glTF mesh with a primitive per submesh: submeshes are
the material slots of a renderer, so collapsing them would lose which part of the
geometry each material paints.  Vertex attributes are shared by every primitive of
the mesh — glTF addresses them per primitive but Unity stores one vertex stream —
so the accessors are written once and referenced.

Coordinate handedness and winding are handled by :mod:`core.gltf`.

A skinned mesh also carries per-vertex joint influences and a bind pose.  Those
are written here too, next to the geometry they belong to, because every domain
that reads Unity meshes meets the same bytes: the *skeleton* differs between a
character and a site prop, but ``JOINTS_0``/``WEIGHTS_0`` and the inverse bind
matrices are a property of the mesh asset and are decoded identically.
"""
import struct

from UnityPy.helpers.MeshHelper import MeshHandler

from .gltf import flip_winding, unity_to_gltf_pos

# glTF component types and buffer targets.
FLOAT, UNSIGNED_INT, UNSIGNED_SHORT = 5126, 5125, 5123
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963

TRIANGLES = 0

NOT_TRIANGLES = "mesh submesh is not a triangle list"
INDEX_RANGE = "mesh submesh index out of range"

# glTF fixes four joint influences per vertex; Unity may store fewer (a rigidly
# bound mesh stores one) or more (dropped, matching Unity's own import limit).
INFLUENCES = 4


def skin_accessors(glb, handler, tree, reflect=True):
    """Write one Unity mesh's skin buffers into *glb*, or ``None`` if it has none.

    Returns ``{"attributes", "inverseBindMatrices", "joints", "influences"}``:
    the ``JOINTS_0``/``WEIGHTS_0`` accessors, the inverse-bind-matrix accessor and
    how many joints the mesh's bind pose expects.  Deliberately domain-neutral —
    the *joint node indices* are not here, because one mesh can be driven by
    several renderers with different bone lists (the site packages do exactly
    that); only the caller knows which nodes a given renderer names.

    ``JOINTS_0`` values index the mesh's own bind-pose order, so they are the
    same for every renderer of the mesh and are written once.

    Weights: Unity omits ``m_BoneWeights`` entirely when every influence is 1.0
    (a rigidly bound mesh).  That is an authored state, not missing data, so the
    absent array reads as weight 1.0 rather than as zero — a zero would collapse
    the mesh onto the origin.
    """
    indices = getattr(handler, "m_BoneIndices", None)
    if not indices:
        return None
    bind_poses = tree.get("m_BindPose") or []
    if not bind_poses:
        return None
    weights = getattr(handler, "m_BoneWeights", None)
    count = handler.m_VertexCount
    joint_bytes, weight_bytes = bytearray(), bytearray()
    for vertex in range(count):
        joints = [int(j) for j in indices[vertex]][:INFLUENCES]
        if weights:
            values = [float(w) for w in weights[vertex]][:INFLUENCES]
        else:
            values = [1.0] * len(joints)
        joints += [0] * (INFLUENCES - len(joints))
        values += [0.0] * (INFLUENCES - len(values))
        total = sum(values) or 1.0
        joint_bytes += struct.pack("<4H", *joints)
        weight_bytes += struct.pack("<4f", *(w / total for w in values))

    # Inverse bind matrices come from the mesh's authored bind poses, which is
    # what the runtime skins with.  Unity stores them row-major and glTF wants
    # column-major; the X reflection of core.gltf conjugates each matrix by
    # diag(-1, 1, 1), which flips exactly the entries with one index in row/
    # column 0 (the pure-X row and column) and leaves the rest alone.
    #
    # *reflect* is that conjugation, and it is a parameter rather than a fact of
    # the mesh because a bind pose only means something against the space the
    # node transforms are written in.  A document that writes its nodes and
    # positions through ``unity_to_gltf_pos`` needs the conjugated matrix; a
    # document that writes both verbatim needs the authored one, and handing it
    # the conjugated matrix would mirror the skinned parts against geometry that
    # was not mirrored.  The joint indices and weights below carry no
    # handedness, so they are the same either way.
    matrices = bytearray()
    for pose in bind_poses:
        values = [0.0] * 16
        for row in range(4):
            for column in range(4):
                sign = (-1.0 if reflect and (row == 0) != (column == 0)
                        else 1.0)
                values[column * 4 + row] = pose[f"e{row}{column}"] * sign
        matrices += struct.pack("<16f", *values)

    return {"attributes": {
                "JOINTS_0": glb.acc(bytes(joint_bytes), UNSIGNED_SHORT, "VEC4",
                                    count, ARRAY_BUFFER),
                "WEIGHTS_0": glb.acc(bytes(weight_bytes), FLOAT, "VEC4",
                                     count, ARRAY_BUFFER)},
            "inverseBindMatrices": glb.acc(bytes(matrices), FLOAT, "MAT4",
                                           len(bind_poses)),
            "joints": len(bind_poses),
            "influences": max((len(t) for t in indices), default=0)}


def mesh_accessors(glb, mesh_object, tree, skin=True):
    """Write one Unity mesh's buffers into *glb* and return how to reference them.

    Vertex attributes are one stream in Unity and are shared by every submesh, so
    they are written once; the submeshes get one index accessor each.  Split out
    from :func:`add_mesh` because the same geometry is drawn with different
    materials in one package, and glTF binds the material into the primitive:
    composing a second mesh from these accessors costs a few bytes of JSON,
    whereas re-adding the mesh would copy the whole vertex buffer.

    Returns ``{"attributes", "submeshes", "vertices", "triangles", "name", "skin"}``;
    ``skin`` is ``None`` for a mesh that carries no bone data.

    *skin* is off for documents that never bind a skeleton (a single-mesh preview
    blob, a phenomenon's geometry).  The influences would be written into the
    binary and then referenced by nothing — a mesh with a bind pose costs ~24
    bytes a vertex, so the flag is what keeps those files the size they were.
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

    skin = skin_accessors(glb, handler, tree) if skin else None

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
            "submeshes": submeshes, "vertices": count, "triangles": triangles,
            "skin": skin}


def compose_mesh(glb, buffers, name=None, materials=None, skinned=False):
    """Add a glTF mesh built from already-written *buffers*, one primitive per
    submesh, optionally binding a material index per submesh.

    *skinned* adds the joint attributes to every primitive.  It is the caller's
    choice rather than a property of the buffers because one Unity mesh can be
    drawn by both a plain ``MeshRenderer`` and a ``SkinnedMeshRenderer``: a
    primitive carrying ``JOINTS_0`` on a node with no ``skin`` is invalid glTF,
    so the joints go only on the meshes a skin will actually drive.
    """
    attributes = buffers["attributes"]
    if skinned and buffers.get("skin"):
        attributes = dict(attributes, **buffers["skin"]["attributes"])
    primitives = []
    for index, accessor in enumerate(buffers["submeshes"]):
        primitive = {"attributes": attributes, "indices": accessor}
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

    No skin: this writes one mesh into a document that binds no skeleton, so the
    influences would be bytes nothing references.

    Returns ``(mesh index, vertex count, triangle count)``.
    """
    buffers = mesh_accessors(glb, mesh_object, tree, skin=False)
    index = compose_mesh(glb, buffers, name, materials)
    return index, buffers["vertices"], buffers["triangles"]
