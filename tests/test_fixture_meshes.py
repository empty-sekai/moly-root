"""Furniture mesh export: what the extractor must report, and what makes it fail.

The fixtures here are synthetic.  A real furniture package is a bundle of scene
objects and is not in this repository, so every check runs against a small corpus
built in-process — which also lets several tests plant a *wrong* corpus on purpose
and assert how the extractor must not act: it must not collapse several prefab
variants to the first one, must not flip or convert a node transform, must not
flatten an attach-point node out of the exported hierarchy, and must export
geometry that hangs on ``SkinnedMeshRenderer`` as well as on ``MeshRenderer``.
A criterion that cannot go red is not a criterion.

Mesh vertex data is read through :class:`UnityPy.helpers.MeshHelper.MeshHandler`,
which needs a real serialized unity mesh; the tests substitute a fake handler that
feeds canned vertex channels, because the corpus here is built in memory.  The
scene-graph walk, the material/texture lookup, the attach-point weld and the
per-variant accounting are all real code exercised against the fake objects.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from core.assets.packages import PackageStore
from fixtures import meshes as meshes_module
from fixtures.meshes import extract_from_store

SCRIPT_ID = 9001

MESH_ID = 7001
MATERIAL_ID = 6001
TEXTURE_ID = 5001


class _FakeMeshHandler:
    """Feeds canned vertex channels instead of decoding a real unity mesh."""

    def __init__(self, src):
        self.src = src

    def process(self):
        self.m_VertexCount = getattr(self.src, "_vertexCount", 0)
        self.m_Vertices = getattr(self.src, "_vertices", [])
        self.m_Normals = getattr(self.src, "_normals", [])
        self.m_Tangents = getattr(self.src, "_tangents", [])
        self.m_UV0 = getattr(self.src, "_uv0", [])
        self.m_UV1 = getattr(self.src, "_uv1", None)
        self.m_UV2 = getattr(self.src, "_uv2", None)
        self.m_UV3 = getattr(self.src, "_uv3", None)
        self.m_Colors = getattr(self.src, "_colors", None)
        self.m_IndexBuffer = getattr(self.src, "_indices", [])


class _AssetFile:
    def __init__(self, name, externals=()):
        self.name = name
        self.externals = [SimpleNamespace(name=e) for e in externals]


class _Object:
    def __init__(self, kind, path_id, tree, asset_file, read=None):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = asset_file
        self._read = read

    def read_typetree(self):
        return self._tree

    def read(self):
        if self._read is not None:
            return self._read
        return SimpleNamespace()


class _Package:
    """A fixture package under construction: meshes, materials, node tree."""

    def __init__(self, name):
        self.name = name
        self.archive = _AssetFile(f"CAB-{name}")
        self.archive.name = name
        self.objects = []
        self._next = 1000

    def _id(self):
        self._next += 1
        return self._next

    def add(self, kind, tree, path_id=None, read=None):
        path_id = self._id() if path_id is None else path_id
        self.objects.append(_Object(kind, path_id, tree, self.archive, read))
        return path_id

    def _transform(self, goid, position=(0.0, 0.0, 0.0), rotation=None,
                   scale=None, children=(), parent=None):
        rotation = rotation or (0.0, 0.0, 0.0, 1.0)
        scale = scale or (1.0, 1.0, 1.0)
        return self.add("Transform", {
            "m_GameObject": {"m_FileID": 0, "m_PathID": goid},
            "m_Father": {"m_FileID": 0, "m_PathID": parent
                         if parent is not None else 0},
            "m_LocalPosition": {"x": position[0], "y": position[1],
                                "z": position[2]},
            "m_LocalRotation": {"x": rotation[0], "y": rotation[1],
                                "z": rotation[2], "w": rotation[3]},
            "m_LocalScale": {"x": scale[0], "y": scale[1], "z": scale[2]},
            "m_Children": [{"m_FileID": 0, "m_PathID": c} for c in children]})

    def node(self, name, position=(0.0, 0.0, 0.0), rotation=None, scale=None):
        """One game object with its transform; returns (goid, tpid)."""
        goid = self._id()
        tpid = self._id()
        self.add("GameObject", {"m_Name": name, "m_IsActive": 1,
                                "m_Component": [{"component": {"m_FileID": 0,
                                                               "m_PathID": tpid}}]},
                 path_id=goid)
        self._transform(goid, position, rotation, scale)
        return goid, tpid

    def renderer(self, goid, mesh_id, material_ids, kind="MeshRenderer"):
        """Attach a MeshRenderer (or SkinnedMeshRenderer) and a MeshFilter."""
        filter_tree = {"m_Mesh": {"m_FileID": 0, "m_PathID": mesh_id}}
        if kind == "MeshRenderer":
            filter_id = self.add("MeshFilter", filter_tree)
            renderer_id = self.add("MeshRenderer", {
                "m_GameObject": {"m_FileID": 0, "m_PathID": goid},
                "m_Materials": [{"m_FileID": 0, "m_PathID": m}
                                for m in material_ids]})
            comps = [{"component": {"m_FileID": 0, "m_PathID": filter_id}},
                     {"component": {"m_FileID": 0, "m_PathID": renderer_id}}]
        else:
            renderer_id = self.add("SkinnedMeshRenderer", {
                "m_GameObject": {"m_FileID": 0, "m_PathID": goid},
                "m_Mesh": {"m_FileID": 0, "m_PathID": mesh_id},
                "m_Materials": [{"m_FileID": 0, "m_PathID": m}
                                for m in material_ids]})
            comps = [{"component": {"m_FileID": 0, "m_PathID": renderer_id}}]
        # merge the renderer components into the game object's m_Component
        obj = next(o for o in self.objects if o.path_id == goid)
        obj._tree["m_Component"] = comps
        return renderer_id

    def mesh(self, vertices, indices, submeshes=None, colors=None, tri=True,
             normals=None, tangents=None, path_id=MESH_ID):
        tree = {"m_Name": "mesh", "m_SubMeshes": submeshes or
                [{"topology": 0, "indexCount": len(indices)}],
                "m_IsReadable": True}
        read = SimpleNamespace(_vertexCount=len(vertices), _vertices=vertices,
                               _indices=indices, _colors=colors,
                               _normals=normals, _tangents=tangents)
        return self.add("Mesh", tree, path_id=path_id, read=read)

    def material(self, path_id=MATERIAL_ID, name="mat"):
        tree = {"m_Name": name, "m_SavedProperties": {
            "m_TexEnvs": [["_MainTex", {"m_Texture": {"m_FileID": 0,
                                                      "m_PathID": TEXTURE_ID}}]],
            "m_Floats": {"_Cull": 2.0, "_AlphaClip": 0.5},
            "m_Colors": []}}
        return self.add("Material", tree, path_id=path_id)

    def texture(self, path_id=TEXTURE_ID, name="tex"):
        from PIL import Image
        image = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
        read = SimpleNamespace(m_Name=name, image=image)
        return self.add("Texture2D", {"m_Name": name, "m_Width": 2,
                                      "m_Height": 2, "m_TextureFormat": 50},
                        path_id=path_id, read=read)

    def finish(self):
        return SimpleNamespace(objects=self.objects)


def _wire(meshes_module_ref, packages_ref, packages, monkeypatch):
    monkeypatch.setattr(meshes_module_ref, "MeshHandler", _FakeMeshHandler)
    monkeypatch.setattr(packages_ref.UnityPy, "load",
                        lambda path: packages[os.path.basename(str(path))])


def _run(tmp_path, monkeypatch, packages, bundle_root=None, source_names=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_names = list(source_names or packages)
    for name in packages:
        (tmp_path / name).touch()
    _wire(meshes_module, packages_module, packages, monkeypatch)
    source_paths = [str(tmp_path / name) for name in source_names]
    store = PackageStore(source_paths, root=str(bundle_root) if bundle_root else None)
    out = tmp_path / "out"
    extract_from_store(store, str(out))
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    return index


def test_mesh_vertices_are_exported(tmp_path, monkeypatch):
    """Red when a mesh is dropped: the exporter must read a MeshFilter/MeshRenderer
    pair and write the vertices, not an empty glTF."""
    pkg = _Package("mysekai__fixture__mdl_box")
    goid, tpid = pkg.node("furniture")
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                       [0, 1, 2])
    mat = pkg.material()
    pkg.renderer(goid, mesh_id, [mat])
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    meta = index["packages"][pkg.name]
    assert meta["status"] == "exported"
    assert meta["meshCount"] == 1
    assert meta["vertexCount"] == 3


def test_material_extras_record_shader_object_name(tmp_path, monkeypatch):
    """Every exported material must carry the source Shader object name."""
    pkg = _Package("mysekai__fixture__mdl_shader")
    goid, _ = pkg.node("furniture")
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    mat = pkg.material(name="shader-mat")
    shader_id = pkg.add("Shader", {"m_ParsedForm": {"m_Name": "Synthetic/Fixture"}})
    pkg.objects[-2]._tree["m_Shader"] = {"m_FileID": 0, "m_PathID": shader_id}
    pkg.renderer(goid, mesh_id, [mat])
    _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    gltf = _glb(tmp_path / "out" / f"{pkg.name}.glb")
    material = gltf["materials"][0]
    assert "shader" in material["extras"]
    assert material["extras"]["shader"] == "Synthetic/Fixture"


def test_unreferenced_materials_are_exported_with_shader_evidence(tmp_path,
                                                                  monkeypatch):
    """Material census entries remain visible even when no renderer uses them."""
    pkg = _Package("mysekai__fixture__mdl_shader_orphan")
    goid, _ = pkg.node("furniture")
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    used = pkg.material(path_id=6001, name="used-mat")
    orphan = pkg.material(path_id=6002, name="orphan-mat")
    used_shader = pkg.add(
        "Shader", {"m_ParsedForm": {"m_Name": "Synthetic/Used"}},
        path_id=7001)
    orphan_shader = pkg.add(
        "Shader", {"m_ParsedForm": {"m_Name": "Synthetic/Orphan"}},
        path_id=7002)
    for material_id, shader_id in ((used, used_shader), (orphan, orphan_shader)):
        material_obj = next(obj for obj in pkg.objects
                            if obj.path_id == material_id)
        material_obj._tree["m_Shader"] = {
            "m_FileID": 0, "m_PathID": shader_id,
        }
    pkg.renderer(goid, mesh_id, [used])
    _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    materials = _glb(tmp_path / "out" / f"{pkg.name}.glb")["materials"]
    by_name = {item["extras"]["sourceMaterial"]: item for item in materials}
    assert by_name["orphan-mat"]["extras"]["shader"] == "Synthetic/Orphan"
    assert by_name["used-mat"]["extras"]["shader"] == "Synthetic/Used"


def test_material_extras_record_shader_passes_and_light_modes(tmp_path,
                                                              monkeypatch):
    """Pass names and serialized LIGHTMODE tags are retained when present."""
    pkg = _Package("mysekai__fixture__mdl_shader_passes")
    goid, _ = pkg.node("furniture")
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    mat = pkg.material(name="shader-pass-mat")
    shader_id = pkg.add("Shader", {"m_ParsedForm": {
        "m_Name": "Synthetic/Passes",
        "m_SubShaders": [{"m_Passes": [
            {"m_Name": "Forward",
             "m_State": {"m_Tags": {"tags": [["LIGHTMODE", "ForwardBase"]]}}},
            {"m_Name": "ShadowCaster",
             "m_State": {"m_Tags": {"tags": []}}},
        ]}],
    }})
    pkg.objects[-2]._tree["m_Shader"] = {
        "m_FileID": 0, "m_PathID": shader_id,
    }
    pkg.renderer(goid, mesh_id, [mat])
    _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    material = _glb(tmp_path / "out" / f"{pkg.name}.glb")["materials"][0]
    assert material["extras"]["shaderPasses"] == [
        {"name": "Forward", "lightMode": "ForwardBase"},
        {"name": "ShadowCaster", "lightMode": None},
    ]
    assert material["extras"]["lightModes"] == ["ForwardBase", None]


def test_cross_package_shader_pointer_resolves_from_bundle_root(tmp_path,
                                                                monkeypatch):
    """A Shader in a dependency package is resolved through the store root."""
    pkg = _Package("mysekai__fixture__mdl_shader_dep_client")
    dep = _Package("mysekai__fixture__shader_dependency")
    goid, _ = pkg.node("furniture")
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    mat = pkg.material(name="dependency-mat")
    shader_id = dep.add(
        "Shader", {"m_ParsedForm": {"m_Name": "Synthetic/Dependency"}},
        path_id=7001)
    mat_obj = next(obj for obj in pkg.objects if obj.path_id == mat)
    mat_obj._tree["m_Shader"] = {"m_FileID": 1, "m_PathID": shader_id}
    pkg.archive.externals = [SimpleNamespace(name=dep.name)]
    pkg.renderer(goid, mesh_id, [mat])

    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir()
    (bundle_root / dep.name).touch()
    _run(tmp_path, monkeypatch,
         {pkg.name: pkg.finish(), dep.name: dep.finish()},
         bundle_root=bundle_root, source_names=[pkg.name])
    material = _glb(tmp_path / "out" / f"{pkg.name}.glb")["materials"][0]
    assert material["extras"]["shader"] == "Synthetic/Dependency"


def test_empty_bundle_root_names_each_unresolved_dependency_shader(tmp_path,
                                                                    monkeypatch):
    """Removing dependency data produces named unresolved records for each slot."""
    pkg = _Package("mysekai__fixture__mdl_shader_negative")
    dep = _Package("mysekai__fixture__shader_negative_dependency")
    goid, _ = pkg.node("furniture")
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    materials = [pkg.material(path_id=6001, name="dependency-mat-a"),
                 pkg.material(path_id=6002, name="dependency-mat-b")]
    shader_id = dep.add(
        "Shader", {"m_ParsedForm": {"m_Name": "Synthetic/Dependency"}},
        path_id=7001)
    for mat in materials:
        mat_obj = next(obj for obj in pkg.objects if obj.path_id == mat)
        mat_obj._tree["m_Shader"] = {"m_FileID": 1, "m_PathID": shader_id}
    pkg.archive.externals = [SimpleNamespace(name=dep.name)]
    pkg.renderer(goid, mesh_id, materials)
    packages = {pkg.name: pkg.finish(), dep.name: dep.finish()}

    available_root = tmp_path / "available"
    available_root.mkdir()
    (available_root / dep.name).touch()
    _run(tmp_path / "available-run", monkeypatch, packages,
         bundle_root=available_root, source_names=[pkg.name])
    resolved = _glb((tmp_path / "available-run" / "out" /
                     f"{pkg.name}.glb"))["materials"]
    available_unresolved_count = sum(
        isinstance(item["extras"].get("shader"), dict)
        and item["extras"]["shader"].get("status") == "unresolved"
        for item in resolved
    )
    assert all(isinstance(item["extras"]["shader"], str) for item in resolved)

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _run(tmp_path / "empty-run", monkeypatch, packages,
         bundle_root=empty_root, source_names=[pkg.name])
    unresolved = _glb((tmp_path / "empty-run" / "out" /
                       f"{pkg.name}.glb"))["materials"]
    records = [item["extras"]["shader"] for item in unresolved]
    empty_unresolved_count = sum(
        record.get("status") == "unresolved" for record in records
    )
    assert empty_unresolved_count == 2
    assert empty_unresolved_count > available_unresolved_count
    assert {item["extras"]["sourceMaterial"] for item in unresolved} == {
        "dependency-mat-a", "dependency-mat-b"}
    assert all(dep.name in record["reason"] for record in records)


def test_skinned_meshes_are_not_exported_empty(tmp_path, monkeypatch):
    """Red when geometry on a SkinnedMeshRenderer is silently ignored: a package
    whose only renderer is a SkinnedMeshRenderer (no MeshFilter/MeshRenderer) must
    still export its vertices, or the 111 skinned furniture packages would all be
    empty files with a green run."""
    pkg = _Package("mysekai__fixture__mdl_gate")
    goid, tpid = pkg.node("gate")
    mesh_id = pkg.mesh([(0, 0, 0), (2, 0, 0), (0, 2, 0)],
                       [0, 1, 2])
    mat = pkg.material()
    pkg.renderer(goid, mesh_id, [mat], kind="SkinnedMeshRenderer")
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    meta = index["packages"][pkg.name]
    assert meta["status"] == "exported"
    assert meta["vertexCount"] == 3


def test_null_material_slots_are_not_anomalies(tmp_path, monkeypatch):
    """Red when a null material slot is counted as missing: a renderer's
    ``m_Materials`` may carry trailing nulls (path id 0) for unused submesh
    slots; those are authored empty slots, not unresolved references, and must
    not be flagged."""
    pkg = _Package("mysekai__fixture__mdl_nullmat")
    goid, tpid = pkg.node("furniture")
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    mat = pkg.material()
    pkg.renderer(goid, mesh_id, [mat, 0, 0])
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    meta = index["packages"][pkg.name]
    assert meta["anomalies"] == []


def test_a_material_that_does_not_resolve_is_an_anomaly(tmp_path, monkeypatch):
    """Red when a dangling material pointer is silently swallowed: a renderer that
    names a material object absent from the package must be reported as
    ``material-unresolved``, on a package that still exports its geometry."""
    pkg = _Package("mysekai__fixture__mdl_dangling")
    goid, tpid = pkg.node("furniture")
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    pkg.renderer(goid, mesh_id, [424242])          # no such material
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    meta = index["packages"][pkg.name]
    assert meta["status"] == "exported"
    assert any(anomaly["type"] == "material-unresolved"
               for anomaly in meta["anomalies"])


def test_every_variant_is_exported_not_just_the_first(tmp_path, monkeypatch):
    """Red when a package is collapsed to its first prefab: several root transform
    trees (the same furniture at different sizes) must each become a scene variant,
    so the prefab count for a package can exceed one."""
    pkg = _Package("mysekai__fixture__mdl_doll")
    for name in ("doll_small", "doll_medium", "doll_large"):
        goid, tpid = pkg.node(name)
        mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
        mat = pkg.material()
        pkg.renderer(goid, mesh_id, [mat])
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    meta = index["packages"][pkg.name]
    assert len(meta["variants"]) == 3
    assert index["summary"]["prefabs"] == 3
    assert index["summary"]["prefabs"] > index["summary"]["bundles"]


def test_transforms_are_exported_verbatim_no_flip(tmp_path, monkeypatch):
    """Red when a node transform is converted: the exported node translation and
    rotation must equal the typetree value exactly — no axis reflection, no unit
    change, no float truncation — or a placement at an attach point lands wrong."""
    pkg = _Package("mysekai__fixture__mdl_verbatim")
    goid, tpid = pkg.node("furniture", position=(0.0, 0.5, -1.25),
                          rotation=(0.0, 1.0, 0.0, 0.0))
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    mat = pkg.material()
    pkg.renderer(goid, mesh_id, [mat])
    out = tmp_path / "out"
    _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    gltf = _glb(out / f"{pkg.name}.glb")
    root = gltf["nodes"][gltf["scenes"][0]["nodes"][0]]
    assert root["translation"] == [0.0, 0.5, -1.25]
    assert root["rotation"] == [0.0, 1.0, 0.0, 0.0]


def test_attach_points_stay_findable_in_the_exported_hierarchy(tmp_path,
                                                               monkeypatch):
    """Red when an attach-point node is flattened out of the hierarchy: the
    ``loc_startNNN`` game objects the attach extractor recorded must appear, by
    name, in the exported node tree, because a character is placed at them."""
    pkg = _Package("mysekai__fixture__mdl_chair")
    root_go, root_tp = pkg.node("chair")
    start_go, start_tp = pkg.node("loc_start013", position=(0.0, 0.0, -0.46))
    end_go, end_tp = pkg.node("loc_end013", rotation=(0.0, 1.0, 0.0, 0.0))
    mesh_id = pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    mat = pkg.material()
    pkg.renderer(root_go, mesh_id, [mat])
    index = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    names = set(index["packages"][pkg.name]["nodeNames"])
    assert "loc_start013" in names
    assert "loc_end013" in names


def _glb(path):
    import struct
    data = path.read_bytes()
    _magic, _version, _length = struct.unpack("<4sII", data[:12])
    chunk_len, chunk_type = struct.unpack("<I4s", data[12:20])
    assert chunk_type == b"JSON"
    return json.loads(data[20:20 + chunk_len].decode("utf-8"))
