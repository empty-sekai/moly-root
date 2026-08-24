"""Minimal glTF 2.0 binary (.glb) writer and Unity -> glTF conversions.

Coordinate convention (matches Unity's own glTF exporters): Unity is
left-handed Y-up, glTF right-handed Y-up; conversion reflects the X axis.

    positions   (x, y, z)    -> (-x, y, z)
    quaternions (x, y, z, w) -> (x, -y, -z, w)   (conjugation by diag(-1,1,1))
    triangle winding flipped (det < 0), inverse bind matrices re-accumulated
    in glTF space, UV V flipped (v -> 1 - v: Unity's origin is bottom-left,
    glTF's top-left).
"""
import json
import struct


def unity_to_gltf_pos(v):
    """(x, y, z) -> (-x, y, z)."""
    return (-v[0], v[1], v[2])


def unity_to_gltf_quat(q):
    """(x, y, z, w) -> (x, -y, -z, w): conjugation by S = diag(-1, 1, 1);
    det(S) < 0, so the rotation angle changes sign around the kept axes."""
    return (q[0], -q[1], -q[2], q[3])


def flip_winding(indices):
    """Reverse triangle winding (swap the 2nd and 3rd corner of each
    triangle).  Required because det(S) = -1: without it every face normal
    points inward and backface culling hides the model."""
    out = list(indices)
    for i in range(0, len(out) - 2, 3):
        out[i + 1], out[i + 2] = out[i + 2], out[i + 1]
    return out


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
        self.g["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(self.g, separators=(",", ":"), allow_nan=False).encode("utf-8")
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
