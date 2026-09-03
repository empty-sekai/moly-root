"""Compose dressed player-avatar glbs from already-extracted artifacts.

Everything this module reads was produced by :mod:`chara.avatar_parts` (the
part packages under ``<parts_root>/{skin,decoration,penlight}``) and
:mod:`chara.player_avatar` (the nude base rig ``motion/<name>.glb``); no
bundle is parsed here.  The merge is **append-only** toward the base glb:

* every base node index is unchanged, so the 172 animations' channel targets
  and the skin's 16 joints stay valid (the base binary chunk is carried over
  verbatim as the output's prefix);
* parts are attached by adding one child pointer to a mount node, never by
  renumbering.

Mount points are the rig's own named attachment bones (read from the base
glb's node array): ``Accessory_face`` under ``Head`` for decoration
accessories -- coordinate fullset overlays included -- and
``Penlight_L``/``Penlight_R`` under the forearms for penlights.
Which copy of a duplicated node name to use is fixed by the skin itself:
the bound skeleton (``skins[0].skeleton``) is the tree every animation
channel targets (all 5320 channels resolve to its 20 nodes), so a mount
name must resolve to exactly one node among that skeleton's descendants --
the same-named duplicates in the inert tree are never candidates.

The rig also carries an ``Accessory_body`` bone under ``Hips``, and nothing
mounts there.  It is an art bone: the client's string table holds every
bone name the code can look up by name, and that name is absent from it
while ``Accessory_face``, ``Head``, ``Hips`` and both penlight bones are
present; every by-name bone lookup in the avatar code has been enumerated
and none targets it.  Geometry alone suggested fullset overlays belonged
there, which is why this is written down: the bone exists, it looks like
the obvious home for a torso overlay, and it is still not the one the
game uses.

Which *package* mounts to which *bone* is only partly a data fact:

* the *slot* of a decoration package is data (``avatarAccessories`` lists
  ``accessory_*``; ``avatarCoordinates`` references ``fullset_*`` in both
  its costume and accessory slots; the eight avatar master tables carry no
  bone/attach column at all);
* the *bone* for a slot is client code: the avatar setup finds the
  attachment root by the literal name ``Accessory_face`` and the combine
  step offsets vertices by that bone's world position while weighting them
  entirely to ``Head``.  An accessory's bounding box landing on the head
  when parented there is a consequence, not the evidence.

The hand choice (L vs R) is **not** a game fact and is not invented here:
the source hardcodes both hands in three separate places with three
different conventions -- the live-stage path instantiates one penlight per
hand rather than choosing, the handheld animation dispatches on a
direction baked into the command, and the ornament builder writes both --
and no master table carries a hand column.  ``--penlight-hand`` defaults
to ``L`` as a local convention and every record says so.

The skin (costume texture) half *is* a data fact end to end: the body
material's serialized texture slots (``_ColorTex``/``_MainTex``, extracted
by ``player_avatar`` as texture *names*) currently hold textures named
``skin_tex``/``costume_tex``, and every costume package ships textures with
exactly those names, so the swap is by name equality.  A costume texture
whose name matches no body slot (``skin``, in every package) is reported as
present-but-unbound, never forced onto a slot.
"""
import argparse
import json
import os
import struct

INDEX_NAME = "avatar-parts.json"
BODY_RELPATH = os.path.join("motion", "mysekai__player_avatar.glb")
#: master tables live outside this repository, so there is no baked default:
#: a checked-in path would be one machine's layout, and this tree is the one
#: that ships. Callers pass ``--master-dir``; ``MOLY_MASTER_DIR`` is the
#: fallback, matching how the other out-of-tree inputs are located.
MASTER_DIR_ENV = "MOLY_MASTER_DIR"
#: mount bone per part kind (see module docstring for the evidence split).
MOUNT_FOR_ACCESSORY = "Accessory_face"
MOUNT_FOR_PENLIGHT = {"L": "Penlight_L", "R": "Penlight_R"}


def read_glb(path):
    """One glb -> (json dict, binary chunk bytes). Asserts, never swallows."""
    assert os.path.exists(path), f"glb does not exist: {path}"
    with open(path, "rb") as f:
        data = f.read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67, f"not a glb: {path}"
    assert version == 2, f"unsupported glTF version {version}: {path}"
    assert length == len(data), f"truncated glb: {path} ({len(data)} of {length})"
    offset, js, bin_parts = 12, None, []
    while offset < length:
        clen, ctype = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + clen]
        if ctype == 0x4E4F534A:
            assert js is None, f"two JSON chunks in {path}"
            js = json.loads(chunk.decode("utf-8"))
        elif ctype == 0x004E4942:
            bin_parts.append(chunk)
        else:
            raise AssertionError(f"unknown chunk type {ctype:#x} in {path}")
        offset += 8 + clen
    assert js is not None, f"no JSON chunk in {path}"
    return js, b"".join(bin_parts)


def chunk_bytes(js, binary):
    """Serialise (json, binary) back to glb bytes (spec padding)."""
    js_bytes = json.dumps(js, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
    js_bytes += b" " * ((4 - len(js_bytes) % 4) % 4)
    bin_bytes = binary + b"\x00" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(js_bytes) + 8 + len(bin_bytes)
    return (struct.pack("<III", 0x46546C67, 2, total)
            + struct.pack("<II", len(js_bytes), 0x4E4F534A) + js_bytes
            + struct.pack("<II", len(bin_bytes), 0x004E4942) + bin_bytes)


def _descendants(js, root):
    seen, stack = set(), [root]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(js["nodes"][node].get("children", []))
    return seen


def resolve_mount(js, bone_name):
    """The mount node for *bone_name*: unique among the bound skeleton's
    descendants, else a loud failure (the exported glb carries every node
    name twice -- bound tree plus inert tree; only the bound one mounts)."""
    skeleton = js["skins"][0]["skeleton"]
    scope = _descendants(js, skeleton)
    hits = [i for i, n in enumerate(js["nodes"])
            if n.get("name") == bone_name and i in scope]
    assert len(hits) == 1, (f"mount {bone_name!r} resolved to {hits} nodes "
                            f"among skeleton {skeleton}'s descendants")
    return hits[0]


class BinBuilder:
    """Output binary = frozen base bytes + aligned appends."""

    def __init__(self, base):
        self.base = base
        self.chunks, self.cursor = [], 0

    def offset(self):
        return len(self.base) + self.cursor

    def add(self, payload):
        pad = (-self.cursor) % 4
        if pad:
            self.chunks.append(b"\x00" * pad)
            self.cursor += pad
        offset = self.offset()
        self.chunks.append(payload)
        self.cursor += len(payload)
        return offset

    def binary(self):
        return self.base + b"".join(self.chunks)


class SubtreeCopier:
    """Append one part glb's static subtree into the base document.

    Accessor/texture/material/mesh indices inside the copied block shift by
    the base lengths; copied bufferViews move to freshly appended, 4-byte
    aligned ranges of the output binary.  Meshes/materials/textures the
    subtree does not reference are not copied.
    """

    def __init__(self, js, part_js, part_binary, out_binary):
        self.js = js
        self.part_js, self.part_binary = part_js, part_binary
        self.out = out_binary
        self.n_bv = len(js["bufferViews"])
        self.n_acc = len(js["accessors"])
        self.n_img = len(js["images"])
        self.n_tex = len(js["textures"])
        self.n_mat = len(js["materials"])
        self.view_map, self.acc_map, self.img_map = {}, {}, {}
        self.tex_map, self.mat_map, self.mesh_map = {}, {}, {}
        self.sampler_map = {}

    def _buffer_view(self, index):
        if index in self.view_map:
            return self.view_map[index]
        source = self.part_js["bufferViews"][index]
        rel, length = source.get("byteOffset", 0), source["byteLength"]
        payload = self.part_binary[rel:rel + length]
        assert len(payload) == length, "part bufferView out of range"
        view = {k: v for k, v in source.items() if k != "byteOffset"}
        view["byteOffset"] = self.out.add(payload)
        new_index = len(self.js["bufferViews"])
        self.js["bufferViews"].append(view)
        self.view_map[index] = new_index
        return new_index

    def _accessor(self, index):
        if index in self.acc_map:
            return self.acc_map[index]
        acc = dict(self.part_js["accessors"][index])
        assert "sparse" not in acc, "sparse accessors not supported"
        acc["bufferView"] = self._buffer_view(acc["bufferView"])
        new_index = len(self.js["accessors"])
        self.js["accessors"].append(acc)
        self.acc_map[index] = new_index
        return new_index

    def _sampler(self, index):
        if index not in self.sampler_map:
            sampler = self.part_js.get("samplers", [])[index]
            for base_index, base in enumerate(self.js.get("samplers", [])):
                if base == sampler:
                    self.sampler_map[index] = base_index
                    break
            else:
                self.js.setdefault("samplers", []).append(sampler)
                self.sampler_map[index] = len(self.js["samplers"]) - 1
        return self.sampler_map[index]

    def _image(self, index):
        if index in self.img_map:
            return self.img_map[index]
        image = dict(self.part_js["images"][index])
        image["bufferView"] = self._buffer_view(image["bufferView"])
        new_index = len(self.js["images"])
        self.js["images"].append(image)
        self.img_map[index] = new_index
        return new_index

    def _texture(self, index):
        if index in self.tex_map:
            return self.tex_map[index]
        tex = dict(self.part_js["textures"][index])
        tex["source"] = self._image(tex["source"])
        tex["sampler"] = self._sampler(tex.get("sampler", 0))
        new_index = len(self.js["textures"])
        self.js["textures"].append(tex)
        self.tex_map[index] = new_index
        return new_index

    def _material(self, index):
        if index in self.mat_map:
            return self.mat_map[index]
        mat = json.loads(json.dumps(self.part_js["materials"][index]))
        textures = (mat.get("extras") or {}).get("textures") or {}
        for slot, tex in textures.items():
            if tex is None:
                continue
            assert isinstance(tex, int), (
                f"material {mat.get('name')!r} slot {slot}: extras.textures "
                f"must carry glTF texture indices, not file names, got {tex!r}")
            textures[slot] = self._texture(tex)
        base_color = (mat.get("pbrMetallicRoughness") or {}).get(
            "baseColorTexture")
        if base_color:
            base_color["index"] = self._texture(base_color["index"])
        new_index = len(self.js["materials"])
        self.js["materials"].append(mat)
        self.mat_map[index] = new_index
        return new_index

    def _mesh(self, index):
        if index in self.mesh_map:
            return self.mesh_map[index]
        mesh = json.loads(json.dumps(self.part_js["meshes"][index]))
        for primitive in mesh["primitives"]:
            assert "targets" not in primitive and "extensions" not in \
                primitive, "morph targets / extensions not supported"
            for slot, accessor in primitive["attributes"].items():
                primitive["attributes"][slot] = self._accessor(accessor)
            if "indices" in primitive:
                primitive["indices"] = self._accessor(primitive["indices"])
            if "material" in primitive:
                primitive["material"] = self._material(primitive["material"])
        new_index = len(self.js["meshes"])
        self.js["meshes"].append(mesh)
        self.mesh_map[index] = new_index
        return new_index

    def copy_subtree(self, root, extras=None):
        """Append *root*'s whole node subtree; returns the new root index.

        Copied nodes keep their serialized name and TRS verbatim; *extras*
        (a dict) is merged into the subtree root's ``extras`` for
        provenance.
        """
        mapping = {}

        def walk(index):
            if index in mapping:
                return mapping[index]
            source = self.part_js["nodes"][index]
            node = {k: json.loads(json.dumps(v)) for k, v in source.items()
                    if k not in ("mesh", "children", "extras")}
            assert "skin" not in source, (
                "part subtree carries a skin; the composer mounts static "
                "subtrees only")
            if "mesh" in source:
                node["mesh"] = self._mesh(source["mesh"])
            if index == root and extras:
                node["extras"] = {**source.get("extras", {}), **extras}
            new_index = len(self.js["nodes"])
            self.js["nodes"].append(node)
            mapping[index] = new_index
            if "children" in source:
                node["children"] = [walk(c) for c in source["children"]]
            return new_index

        return walk(root)


def _split_part_glb(path, wanted_root_name):
    """(part glb json, part binary, subtree root index) for a part glb.

    Every part glb carries its package twice (fbx import scaffold plus
    instantiated prefab tree -- the pattern ``avatar_parts`` documents);
    *wanted_root_name* is the instantiated tree's root node name, probed
    read per package from the container path ending ``.prefab``.
    """
    js, binary = read_glb(path)
    hits = [i for i, n in enumerate(js["nodes"])
            if n.get("name") == wanted_root_name]
    assert len(hits) == 1, (f"{path}: node {wanted_root_name!r} found "
                            f"{len(hits)} times, expected exactly 1")
    return js, binary, hits[0]


def _master_dir(master_dir):
    resolved = master_dir or os.environ.get(MASTER_DIR_ENV)
    assert resolved, (
        "master tables directory not given: pass --master-dir or set "
        f"{MASTER_DIR_ENV}. There is no default on purpose — guessing a path "
        "here would silently compose against whichever tables happened to be "
        "on disk, and a wrong master row is not visible in the output glb."
    )
    return resolved


def _master_rows(master_dir, table):
    path = os.path.join(_master_dir(master_dir), f"{table}.json")
    assert os.path.exists(path), f"master table missing: {path}"
    with open(path, encoding="utf-8") as f:
        return {r["assetbundleName"]: r for r in json.load(f)}


def _swap_costume(js, parts_root, costume, out, costume_row):
    """Bind a costume package's textures onto the body material by name.

    Slot names come from the body material's own ``extras.textures``
    (serialized slot -> texture name, the ``player_avatar`` convention).
    Every slot must find a same-named PNG in the costume package -- a
    costume missing one fails here instead of half-dressing silently.
    Costume textures matching no slot are reported, never bound.
    """
    costume_dir = os.path.join(parts_root, "skin", costume, "tex")
    assert os.path.isdir(costume_dir), costume_dir
    material = js["materials"][0]
    previous = dict(material["extras"]["textures"])
    assert all(isinstance(v, str) for v in previous.values()), (
        "body glb extras.textures must map slot -> texture name; got "
        f"{previous!r}")
    bound, tail = {}, []
    for slot, texture_name in previous.items():
        png = os.path.join(costume_dir, f"{costume}__{texture_name}.png")
        assert os.path.exists(png), (f"costume {costume} has no texture for "
                                     f"slot {slot} (expected {png})")
        with open(png, "rb") as f:
            payload = f.read()
        view_index = len(js["bufferViews"])
        js["bufferViews"].append({"byteOffset": out.add(payload),
                                  "byteLength": len(payload)})
        js["images"].append({"name": texture_name, "mimeType": "image/png",
                             "bufferView": view_index})
        js["textures"].append({"sampler": 0, "source": len(js["images"]) - 1,
                               "name": texture_name})
        bound[slot] = len(js["textures"]) - 1
        tail.append({"slot": slot, "texture": texture_name,
                     "textureIndex": bound[slot]})
        if slot == "_MainTex":
            material.setdefault("pbrMetallicRoughness", {})[
                "baseColorTexture"] = {"index": bound[slot]}
    known_names = set(previous.values())
    unbound = sorted(
        name for name in sorted(os.listdir(costume_dir))
        if not any(name == f"{costume}__{n}.png" for n in known_names))
    material["extras"]["textures"] = {slot: bound[slot] for slot in previous}
    return {"kind": "costumeTextureSwap", "package": costume,
            "category": "skin", "masterRow": costume_row,
            "basis": ("body material slot names (extras.textures, from the "
                      "serialized _ColorTex/_MainTex bindings) matched "
                      "against the costume package's texture names"),
            "bound": tail, "unboundFiles": unbound,
            "previousTextures": previous}


def compose(parts_root, out_dir, costume=None, accessory=None, penlight=None,
            penlight_hand="L", name="mysekai__player_avatar_dressed",
            master_dir=None):
    """Compose one dressed player glb into ``<out_dir>/<name>.glb``.

    Returns the record also written as ``<name>.compose.json``: per-part
    provenance (package, master row, mount bone and why) plus the
    before/after counts, recomputed by reading the output file back.
    """
    body_path = os.path.join(parts_root, BODY_RELPATH)
    for path in (parts_root, body_path, os.path.join(parts_root, INDEX_NAME)):
        assert os.path.exists(path), f"missing input: {path}"
    assert penlight_hand in MOUNT_FOR_PENLIGHT, penlight_hand
    js, base_binary = read_glb(body_path)
    before = counts(js)
    out = BinBuilder(base_binary)
    attachments = []

    if costume:
        row = _master_rows(master_dir, "avatarCostumes").get(costume)
        attachments.append(_swap_costume(js, parts_root, costume, out, row))

    if accessory:
        directory = os.path.join(parts_root, "decoration", accessory)
        assert os.path.isdir(directory), directory
        part_js, part_binary, root = _split_part_glb(
            os.path.join(directory, f"{accessory}.glb"), accessory)
        copier = SubtreeCopier(js, part_js, part_binary, out)
        mount = resolve_mount(js, MOUNT_FOR_ACCESSORY)
        subtree = copier.copy_subtree(root, extras={
            "sourcePackage": f"virtual_live__avatar__decoration__{accessory}",
            "category": "decoration", "package": accessory,
            "mountBone": MOUNT_FOR_ACCESSORY,
            "mountBasis": ("rig attachment bone; the prefab is authored at "
                           "identity offset relative to it; slot from "
                           "avatarAccessories (data), bone from rig+geometry"
                           "+client literal, not from any table column")})
        js["nodes"][mount].setdefault("children", []).append(subtree)
        attachments.append({
            "kind": "accessoryMount", "package": accessory,
            "category": "decoration",
            "masterRow": _master_rows(master_dir,
                                      "avatarAccessories").get(accessory),
            "mountBone": MOUNT_FOR_ACCESSORY, "mountNode": mount,
            "subtreeRoot": subtree,
            "nodesAdded": _subtree_size(part_js, root)})

    if penlight:
        directory = os.path.join(parts_root, "penlight", penlight)
        assert os.path.isdir(directory), directory
        bone = MOUNT_FOR_PENLIGHT[penlight_hand]
        part_js, part_binary, root = _split_part_glb(
            os.path.join(directory, f"{penlight}.glb"), penlight)
        copier = SubtreeCopier(js, part_js, part_binary, out)
        mount = resolve_mount(js, bone)
        subtree = copier.copy_subtree(root, extras={
            "sourcePackage": f"virtual_live__avatar__penlight__{penlight}",
            "category": "penlight", "package": penlight,
            "mountBone": bone,
            "mountBasis": ("rig attachment bone; the L/R hand choice is "
                           "client policy, defaulted to "
                           f"{penlight_hand} and recorded")})
        js["nodes"][mount].setdefault("children", []).append(subtree)
        attachments.append({
            "kind": "penlightMount", "package": penlight,
            "category": "penlight",
            "masterRow": _master_rows(master_dir,
                                      "penlights").get(penlight),
            "mountBone": bone, "mountNode": mount,
            "subtreeRoot": subtree,
            "nodesAdded": _subtree_size(part_js, root),
            "handPolicy": penlight_hand})

    os.makedirs(out_dir, exist_ok=True)
    glb_path = os.path.join(out_dir, f"{name}.glb")
    with open(glb_path, "wb") as f:
        f.write(chunk_bytes(js, out.binary()))
    after = counts_from_file(glb_path)
    record = {
        "version": 1, "body": body_path, "glb": glb_path,
        "attachments": attachments,
        "counts": {"before": before, "after": after,
                   "deltas": {k: after[k] - before[k] for k in before}},
        "invariants": {
            "joints": ("skins[0].joints must not move: parts carry no "
                       "SkinnedMeshRenderer and no skin of their own, so "
                       "mounting adds a child pointer to an existing joint's "
                       "node and no bone"),
            "animations": ("append-only merge: base node indices are "
                           "unchanged, so every channel target stays valid "
                           "and channels are carried over byte-identically"),
            "vertices": ("expected delta = sum of the mounted packages' "
                         "prefab-tree mesh vertex counts (fbx-scaffold "
                         "duplicates are not mounted)"),
        },
    }
    with open(os.path.join(out_dir, f"{name}.compose.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    return record


def counts(js):
    """Recomputable file-level counts (vertices read off POSITION accessors)."""
    total = 0
    for mesh in js["meshes"]:
        for primitive in mesh["primitives"]:
            position = primitive["attributes"].get("POSITION")
            assert position is not None, "mesh primitive without POSITION"
            total += js["accessors"][position]["count"]
    return {
        "nodes": len(js["nodes"]),
        "meshes": len(js["meshes"]),
        "skins": len(js.get("skins", [])),
        "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0,
        "animations": len(js.get("animations", [])),
        "channels": sum(len(a["channels"]) for a in js.get("animations", [])),
        "vertices": total,
        "materials": len(js["materials"]),
        "textures": len(js["textures"]),
        "images": len(js["images"]),
        "accessors": len(js["accessors"]),
        "bufferViews": len(js["bufferViews"]),
    }


def counts_from_file(path):
    js, _ = read_glb(path)
    return counts(js)


def _subtree_size(part_js, root):
    seen, stack = set(), [root]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(part_js["nodes"][node].get("children", []))
    return len(seen)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="compose a dressed player avatar glb from extracted parts")
    parser.add_argument("--parts-root", required=True,
                        help="directory holding {skin,decoration,penlight}, "
                             "motion/<body glb> and avatar-parts.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--costume", default=None,
                        help="skin package name (texture swap)")
    parser.add_argument("--accessory", default=None,
                        help="decoration package name (mounted at "
                             "Accessory_face)")
    parser.add_argument("--penlight", default=None,
                        help="penlight package name (mounted at Penlight_L/R)")
    parser.add_argument("--penlight-hand", default="L", choices=("L", "R"))
    parser.add_argument("--name", default="mysekai__player_avatar_dressed")
    parser.add_argument("--master-dir", default=None,
                        help=f"master tables directory (or set {MASTER_DIR_ENV})")
    arguments = parser.parse_args(argv)
    record = compose(arguments.parts_root, arguments.out,
                     costume=arguments.costume, accessory=arguments.accessory,
                     penlight=arguments.penlight,
                     penlight_hand=arguments.penlight_hand,
                     name=arguments.name, master_dir=arguments.master_dir)
    print(json.dumps({"glb": record["glb"], "counts": record["counts"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
