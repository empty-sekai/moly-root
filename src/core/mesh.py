"""Unity meshes as glTF meshes.

One Unity mesh becomes one glTF mesh with a primitive per submesh: submeshes are
the material slots of a renderer, so collapsing them would lose which part of the
geometry each material paints.  Vertex attributes are shared by every primitive of
the mesh — glTF addresses them per primitive but Unity stores one vertex stream —
so the accessors are written once and referenced.

Coordinate handedness and winding are handled by :mod:`core.gltf`.

Texture coordinates come in two ways.  The default writes the first two sets as
the mesh helper hands them back; :data:`AUTHORED_UV_SETS` writes *every* set the
mesh's vertex stream carries, decoded here from the mesh's own channel table and
stream layout (:func:`authored_uv_sets`).  The second is what a consumer porting a
source shader needs: the room wall meshes carry four sets and the wall shader reads
the fourth, so a writer that stops at two drops data the source draws with.

A skinned mesh also carries per-vertex joint influences and a bind pose.  Those
are written here too, next to the geometry they belong to, because every domain
that reads Unity meshes meets the same bytes: the *skeleton* differs between a
character and a site prop, but ``JOINTS_0``/``WEIGHTS_0`` and the inverse bind
matrices are a property of the mesh asset and are decoded identically.
"""
import struct

from UnityPy.helpers.MeshHelper import MeshHandler
from UnityPy.helpers.ResourceReader import get_resource_data

from .gltf import flip_winding, unity_to_gltf_pos

# glTF component types and buffer targets.
FLOAT, UNSIGNED_INT, UNSIGNED_SHORT = 5126, 5125, 5123
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963

TRIANGLES = 0

NOT_TRIANGLES = "mesh submesh is not a triangle list"
INDEX_RANGE = "mesh submesh index out of range"

# Which texture coordinate sets :func:`mesh_accessors` writes: ``None`` is the first
# two, through the mesh helper; this value is every set the vertex stream carries.
AUTHORED_UV_SETS = "authored"

# The engine's vertex attribute order from Unity 2018 on (``VertexAttribute`` in its
# graphics enums, "keep in sync with ShaderChannel"): position, normal, tangent,
# colour, the eight texture coordinate sets, blend weight, blend indices.  A
# channel's index in ``m_VertexData.m_Channels`` is that value.
TEXCOORD_CHANNEL_0 = 4
TEXCOORD_SETS = 8

# The engine's ``VertexAttributeFormat`` from Unity 2019 on ("match VertexFormat on
# C++ side"): the value stored in a channel's ``format`` byte, as the struct code of
# one component, its size, and how the component becomes the value a shader reads.
# Normalised formats read as unsigned c / (2^n - 1) and signed max(c / (2^(n-1) - 1),
# -1); integer formats have no float reading a texture coordinate accessor can carry.
_FLOAT, _UNORM, _SNORM, _INTEGER = "float", "unorm", "snorm", "integer"
VERTEX_ATTRIBUTE_FORMATS = {
    0: ("f", 4, _FLOAT),      # Float32
    1: ("e", 2, _FLOAT),      # Float16
    2: ("B", 1, _UNORM),      # UNorm8
    3: ("b", 1, _SNORM),      # SNorm8
    4: ("H", 2, _UNORM),      # UNorm16
    5: ("h", 2, _SNORM),      # SNorm16
    6: ("B", 1, _INTEGER),    # UInt8
    7: ("b", 1, _INTEGER),    # SInt8
    8: ("H", 2, _INTEGER),    # UInt16
    9: ("h", 2, _INTEGER),    # SInt16
    10: ("I", 4, _INTEGER),   # UInt32
    11: ("i", 4, _INTEGER),   # SInt32
}
FIRST_ATTRIBUTE_FORMAT_VERSION = 2019

# Each vertex stream starts on a 16-byte boundary after the one before it.
VERTEX_STREAM_ALIGNMENT = 16

UV_VERSION = "raw uv decode reads the 2019+ vertex attribute format table"
UV_COMPRESSED = "raw uv decode does not read a compressed mesh"
UV_LAYOUT = "vertex channel table and vertex bytes disagree"
UV_FORMAT = "texture coordinate channel format has no float reading"
UV_DIMENSION = "texture coordinate channel is not two-component"
UV_SETS_NOT_CONSECUTIVE = ("texture coordinate sets are not consecutive from 0, "
                           "so TEXCOORD_n cannot keep the authored set index")

# glTF fixes four joint influences per vertex; Unity may store fewer (a rigidly
# bound mesh stores one) or more (dropped, matching Unity's own import limit).
INFLUENCES = 4

# glTF's COLOR_0 accessor is FLOAT and requires values in [0,1].  UnityPy's
# ``MeshHandler.m_Colors`` does not uniformly satisfy that: it is populated by one
# of three different internal paths, and only two of them already scale into
# [0,1] (a legacy per-mesh color array divided by 255 on the way in, and Unity's
# runtime mesh compression, whose float-color and packed-color branches both
# normalize before handing the values back) -- those two always come back as
# Python ``float``. The remaining path -- reading the ordinary per-vertex stream,
# which is what every mesh actually observed here uses -- copies the channel's
# raw, format-dependent value verbatim and comes back as Python ``int``: a
# vertex-format value of 0 or 1 (Float / Float16) means that raw value already
# is the [0,1] color; a value of 2 (the byte-packed color format, called
# ``kVertexFormatUNorm8`` from Unity 2019 and ``kVertexFormatColor`` before that)
# means it is an unsigned byte 0..255 that still needs dividing by 255.
# Distinguishing float already-scaled data from int raw data by Python type is
# exact, because ``struct`` only ever returns float for the Float/Float16 codes
# and int for every byte/short code.
_COLOR_CHANNEL_FLOAT_FORMATS = (0, 1)   # Float, Float16: raw value already [0,1]
_COLOR_CHANNEL_UNORM8_FORMAT = 2        # byte-packed color: raw value is 0..255


def _normalized_colors(handler):
    """``handler.m_Colors`` rescaled into glTF's [0,1], or ``None`` without any.

    Only the raw-int case (the ordinary per-vertex-stream path) needs the
    channel's declared vertex format looked up; the channel index matches the
    one UnityPy itself uses to populate ``m_Colors`` (``assign_channel_vertex_data``
    in its mesh helper). Every color channel format observed across this
    project's site, fixture, character and avatar-part meshes was the
    byte-packed one, so anything else is refused rather than guessed at -- a
    wrong guess would silently write an out-of-range or corrupted color instead
    of failing where it can be noticed.
    """
    colors = getattr(handler, "m_Colors", None)
    if not colors:
        return None
    if isinstance(colors[0][0], float):
        return colors  # already scaled by UnityPy (legacy field or compressed mesh)
    channel_index = 3 if handler.version[0] >= 2018 else 2
    channels = handler.src.m_VertexData.m_Channels
    channel = channels[channel_index] if channel_index < len(channels) else None
    channel_format = channel.format if channel and channel.dimension > 0 else None
    if channel_format in _COLOR_CHANNEL_FLOAT_FORMATS:
        return colors
    if channel_format != _COLOR_CHANNEL_UNORM8_FORMAT:
        raise ValueError(
            f"unhandled vertex color channel format {channel_format!r}; only "
            "float and byte-packed (UNorm8/Color) formats are implemented")
    return [tuple(v / 255.0 for v in c) for c in colors]


def vertex_bytes(mesh):
    """The mesh's vertex buffer as stored: in the object, or in the resource file
    the object points at (``m_StreamData``)."""
    stream = getattr(mesh, "m_StreamData", None)
    if stream is not None and stream.path and stream.size:
        return bytes(get_resource_data(stream.path, mesh.object_reader.assets_file,
                                       stream.offset, stream.size))
    return bytes(mesh.m_VertexData.m_DataSize or b"")


def _align(value):
    return (value + VERTEX_STREAM_ALIGNMENT - 1) & ~(VERTEX_STREAM_ALIGNMENT - 1)


def authored_uv_sets(mesh, version):
    """Every texture coordinate set of one mesh, ``{set index: [(u, v), ...]}``.

    Decoded from the vertex bytes with the mesh's own channel table: each channel
    names its stream, its byte offset in that stream's vertex, its format and its
    component count.  A stream's stride is the sum of its channels' sizes and must
    equal the end of its last channel (the channels are packed); streams follow each
    other on 16-byte boundaries; the bytes must end where the last stream ends, up
    to that boundary.  Anything this cannot account for is refused by name rather
    than read at a guessed offset.  Values are the authored ones (Unity's
    bottom-left origin); the V flip belongs to the caller.
    """
    if version[0] < FIRST_ATTRIBUTE_FORMAT_VERSION:
        raise ValueError(f"{UV_VERSION} (mesh version {version[0]})")
    if getattr(mesh, "m_MeshCompression", 0):
        raise ValueError(UV_COMPRESSED)
    vertex_data = mesh.m_VertexData
    channels = list(vertex_data.m_Channels or [])
    count = int(vertex_data.m_VertexCount)
    data = vertex_bytes(mesh)

    sizes, ends = {}, {}
    for index, channel in enumerate(channels):
        if not channel.dimension:
            continue
        if channel.dimension & ~0xF or channel.format not in VERTEX_ATTRIBUTE_FORMATS:
            raise ValueError(f"{UV_LAYOUT}: channel {index} dimension "
                             f"{channel.dimension} format {channel.format}")
        size = VERTEX_ATTRIBUTE_FORMATS[channel.format][1] * channel.dimension
        sizes[channel.stream] = sizes.get(channel.stream, 0) + size
        ends[channel.stream] = max(ends.get(channel.stream, 0), channel.offset + size)
    if sizes != ends:
        raise ValueError(f"{UV_LAYOUT}: channel sizes {sizes} against channel ends {ends}")
    starts, end = {}, 0
    for stream in range(max(sizes, default=-1) + 1):
        starts[stream] = _align(end)
        end = starts[stream] + count * sizes.get(stream, 0)
    if not end <= len(data) <= _align(end):
        raise ValueError(f"{UV_LAYOUT}: streams end at {end}, bytes are {len(data)}")

    sets = {}
    for uv_set in range(TEXCOORD_SETS):
        index = TEXCOORD_CHANNEL_0 + uv_set
        if index >= len(channels) or not channels[index].dimension:
            continue
        channel = channels[index]
        code, size, reading = VERTEX_ATTRIBUTE_FORMATS[channel.format]
        if reading == _INTEGER:
            raise ValueError(f"{UV_FORMAT}: uv{uv_set} format {channel.format}")
        if channel.dimension != 2:
            raise ValueError(f"{UV_DIMENSION}: uv{uv_set} dimension {channel.dimension}")
        base, stride = starts[channel.stream] + channel.offset, sizes[channel.stream]
        layout = struct.Struct("<2" + code)
        values = [layout.unpack_from(data, base + vertex * stride)
                  for vertex in range(count)]
        if reading == _UNORM:
            scale = float((1 << (8 * size)) - 1)
            values = [(u / scale, v / scale) for u, v in values]
        elif reading == _SNORM:
            scale = float((1 << (8 * size - 1)) - 1)
            values = [(max(u / scale, -1.0), max(v / scale, -1.0)) for u, v in values]
        sets[uv_set] = [(float(u), float(v)) for u, v in values]
    return sets


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


def mesh_accessors(glb, mesh_object, tree, skin=True, uv_sets=None):
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

    *uv_sets* ``None`` writes ``TEXCOORD_0``/``TEXCOORD_1`` from the mesh helper;
    :data:`AUTHORED_UV_SETS` writes ``TEXCOORD_n`` for every set ``n`` the vertex
    stream carries, from :func:`authored_uv_sets`.  Either way V is flipped
    (``1 - v``), one convention for every set of the document.  glTF requires the
    set indices to run from 0 without a gap; a mesh whose authored sets do not is
    refused, because renumbering would hand a shader that reads set 3 some other
    set.
    """
    mesh = mesh_object.read()
    authored = (authored_uv_sets(mesh, mesh.object_reader.version)
                if uv_sets == AUTHORED_UV_SETS else None)
    if authored is not None and sorted(authored) != list(range(len(authored))):
        raise ValueError(f"{UV_SETS_NOT_CONSECUTIVE}: {sorted(authored)}")
    handler = MeshHandler(mesh)
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
    texcoords = ((("TEXCOORD_0", handler.m_UV0),
                  ("TEXCOORD_1", getattr(handler, "m_UV1", None)))
                 if authored is None else
                 tuple((f"TEXCOORD_{index}", authored[index])
                       for index in sorted(authored)))
    for slot, values in texcoords:
        if values:
            attributes[slot] = glb.acc(
                b"".join(struct.pack("<2f", v[0], 1.0 - v[1]) for v in values),
                FLOAT, "VEC2", count, ARRAY_BUFFER)
    colors = _normalized_colors(handler)
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
