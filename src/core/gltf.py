"""Minimal glTF 2.0 binary (.glb) writer and Unity -> glTF conversions.

Coordinate convention (matches Unity's own glTF exporters): Unity is
left-handed Y-up, glTF right-handed Y-up; conversion reflects the X axis.

    positions   (x, y, z)    -> (-x, y, z)
    quaternions (x, y, z, w) -> (x, -y, -z, w)   (conjugation by diag(-1,1,1))
    triangle winding flipped (det < 0), inverse bind matrices conjugated
    into glTF space. UV handling is separately declared by each asset writer:
    sites/characters flip V, while furniture retains Unity bottom-left UVs for
    its source shaders. A spatial coordinate contract does not imply a UV flip.
"""
import json
import struct


# Canonical runtime space shared by exported site, character and fixture
# assets.  Unity source assets are left-handed Y-up; reflecting X makes the
# basis right-handed while preserving Y-up and +Z-forward.  Physical units are
# deliberately not claimed here: the source contract only establishes the
# authored Unity unit.
COORDINATE_SPACE = "moly-rh-y-up-reflect-x-v1"
SOURCE_SPACE = "unity-lh-y-up-authored"
UV_ORIGIN = "unity-bottom-left"


def coordinate_contract():
    """Return the explicit space contract embedded in every canonical asset."""
    return {
        "space": COORDINATE_SPACE,
        "sourceSpace": SOURCE_SPACE,
        "units": "source-unity-unit",
        "up": "+Y",
        "forward": "+Z",
        "reflection": "S=diag(-1,1,1)",
        "position": "S*p",
        "rotation": "S*R*S; quaternion (x,-y,-z,w)",
        "normal": "S*n",
        "tangent": "S*t.xyz; -t.w when UV is unchanged",
        "triangleWinding": "swap second and third index per triangle",
        "uvOrigin": UV_ORIGIN,
        "uvTransform": "none; UV origin is explicit and separate from spatial basis",
        "skinInverseBind": "S*IBM*S",
    }


def reflect_position(v):
    """Reflect one Unity position/vector through the canonical X boundary."""
    return (-v[0], v[1], v[2])


def reflect_quaternion(q):
    """Conjugate a quaternion by S=diag(-1,1,1)."""
    return (q[0], -q[1], -q[2], q[3])


def reflect_tangent(v):
    """Reflect tangent xyz and its handedness for unchanged authored UVs."""
    return (-v[0], v[1], v[2], -v[3])


def unity_to_gltf_pos(v):
    """(x, y, z) -> (-x, y, z)."""
    return reflect_position(v)


def unity_to_gltf_quat(q):
    """(x, y, z, w) -> (x, -y, -z, w): conjugation by S = diag(-1, 1, 1);
    det(S) < 0, so the rotation angle changes sign around the kept axes."""
    return reflect_quaternion(q)


def flip_winding(indices):
    """Reverse triangle winding (swap the 2nd and 3rd corner of each
    triangle).  Required because det(S) = -1: without it every face normal
    points inward and backface culling hides the model."""
    out = list(indices)
    for i in range(0, len(out) - 2, 3):
        out[i + 1], out[i + 2] = out[i + 2], out[i + 1]
    return out


def unity_sampler(filter_mode, wrap_u, wrap_v, mip_count):
    """Represent the source texture's filtering and address modes in glTF."""
    wraps = {0: 10497, 1: 33071, 2: 33648}
    magnification = {0: 9728, 1: 9729, 2: 9729}
    mip_filters = {0: 9984, 1: 9985, 2: 9987}
    return {"magFilter": magnification[filter_mode],
            "minFilter": mip_filters[filter_mode] if mip_count > 1 else magnification[filter_mode],
            "wrapS": wraps[wrap_u], "wrapT": wraps[wrap_v]}


class GLB:
    def __init__(self, generator="moly-root"):
        self.bin = bytearray()
        self.g = {"asset": {"version": "2.0", "generator": generator},
                  "buffers": [], "bufferViews": [], "accessors": [], "meshes": [],
                  "nodes": [], "scenes": [{"nodes": []}], "scene": 0, "skins": [],
                  "materials": [], "textures": [], "images": [],
                  "samplers": [{"magFilter": 9729, "minFilter": 9987,
                                "wrapS": 33071, "wrapT": 33071}]}

    def _pad(self, n=4):
        while len(self.bin) % n:
            self.bin.append(0)

    def view(self, data, target=None):
        self._pad()
        off = len(self.bin)
        self.bin += data
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        self.g["bufferViews"].append(v)
        return len(self.g["bufferViews"]) - 1

    def acc(self, data, ctype, atype, count, target=None, minmax=None):
        vi = self.view(data, target)
        a = {"bufferView": vi, "componentType": ctype, "count": count, "type": atype}
        if minmax:
            a["min"], a["max"] = minmax
        self.g["accessors"].append(a)
        return len(self.g["accessors"]) - 1

    def blob(self):
        """The whole file as bytes, so a caller can hash it before writing."""
        self._pad()
        # Asset writers may append their own extras after construction. Install
        # the common spatial declaration here so those additions cannot erase it.
        # UV convention is intentionally exporter-specific and not inferred from
        # this spatial marker (fixtures preserve V, sites/characters flip V).
        self.g["asset"].setdefault("extras", {}).setdefault("coordinateContract", COORDINATE_SPACE)
        self.g["asset"]["extras"].setdefault("coordinateUnits", "source-unity-unit")
        for scene in self.g.get("scenes", []):
            for root in scene.get("nodes", []):
                extras = self.g["nodes"][root].setdefault("extras", {})
                extras.setdefault("coordinateContract", COORDINATE_SPACE)
                extras.setdefault("coordinateUnits", "source-unity-unit")
        self.g["buffers"] = [{"byteLength": len(self.bin)}]
        # Object key order is not glTF semantics; node/accessor/animation array
        # order is. Canonical objects prevent traversal-built extras/materials
        # from changing an otherwise identical immutable artifact.
        js = json.dumps(self.g, sort_keys=True, separators=(",", ":"),
                        allow_nan=False).encode("utf-8")
        while len(js) % 4:
            js += b" "
        binary = bytes(self.bin)
        total = 12 + 8 + len(js) + 8 + len(binary)
        return (b"glTF" + struct.pack("<II", 2, total)
                + struct.pack("<I", len(js)) + b"JSON" + js
                + struct.pack("<I", len(binary)) + b"BIN\x00" + binary)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.blob())
