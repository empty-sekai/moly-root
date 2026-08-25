"""Furniture and cut-scene particle export: what the extractor must report, and
what makes it fail.

The packages here are synthetic.  A real furniture package is a bundle of scene
objects and is not in this repository, so every check runs against a small corpus
built in-process -- which also lets several tests plant a *wrong* corpus on
purpose and assert how the extractor must not act: it must not merge two emitters
that merely share a path with one another, must not silently swallow a particle
system whose typetree will not read (its count is owed to the census), must not
turn an unresolved material pointer into null, and must not write a document for
a package whose emitters it counted as zero.  A criterion that cannot go red is
not a criterion.

The corpus mimics what the real packages hold: serialized objects grouped into a
file, the typetrees those objects answer with, and the game-object trees the
paths run along.  The store, the pointer follower, the census reconciliation and
the emitter decoder are the real code exercised against the fakes; only
``UnityPy.load`` is substituted, because the corpus is in memory.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from core.assets.packages import PackageStore
from fixtures import particles as particles_module
from fixtures.particles import extract_from_store

FIXTURE = "mysekai__fixture__mdl_synth"
FX = "mysekai__effect__fixture__fx_synth"
SITE = "mysekai__site__synth"

SHADER_ID = 8001
MATERIAL_ID = 6001
TEXTURE_ID = 5001
MESH_ID = 7001


class _AssetFile:
    def __init__(self, name, externals=()):
        self.name = name
        self.externals = [SimpleNamespace(name=e) for e in externals]


class _Object:
    def __init__(self, kind, path_id, tree, asset_file, fail_read=False):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = asset_file
        self._fail_read = fail_read
        self._image = None

    def read_typetree(self):
        if self._fail_read:
            raise RuntimeError("synthetic typetree failure")
        return self._tree

    def read(self):
        if self._image is not None:
            return SimpleNamespace(image=self._image)
        return SimpleNamespace()


class _Package:
    """A package under construction: particle systems, renderers, materials."""

    def __init__(self, name, archive_name=None):
        self.name = name
        self.archive = _AssetFile(archive_name or f"CAB-{name}")
        self.objects = []
        self.transform_of = {}
        self._next = 1000

    def _new(self):
        self._next += 1
        return self._next

    def add(self, kind, tree, fail_read=False, path_id=None):
        path_id = self._new() if path_id is None else path_id
        self.objects.append(_Object(kind, path_id, tree, self.archive, fail_read))
        return path_id

    def node(self, name, parent_goid=None):
        """One game object with its transform; returns (goid, tpid)."""
        goid = self._new()
        tpid = self._new()
        self.add("GameObject", {"m_Name": name, "m_IsActive": 1,
                                "m_Component": []}, path_id=goid)
        father = self.transform_of.get(parent_goid, 0) if parent_goid else 0
        self.add("Transform", {"m_GameObject": {"m_FileID": 0, "m_PathID": goid},
                               "m_Father": {"m_FileID": 0, "m_PathID": father},
                               "m_LocalPosition": {"x": 0.0, "y": 0.0, "z": 0.0},
                               "m_LocalRotation": {"x": 0.0, "y": 0.0,
                                                   "z": 0.0, "w": 1.0},
                               "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0},
                               "m_Children": []}, path_id=tpid)
        self.transform_of[goid] = tpid
        return goid, tpid

    def particle_system(self, goid, extra=None, fail_read=False, path_id=None):
        tree = {"m_GameObject": {"m_FileID": 0, "m_PathID": goid},
                "lengthInSec": 1.0, "looping": False, "prewarm": False,
                "playOnAwake": True, "simulationSpeed": 1.0,
                "moveWithTransform": 0, "randomSeed": 42, "autoRandomSeed": True,
                "scalingMode": 0, "emitterVelocityMode": 0, "ringBufferMode": 0,
                "ringBufferLoopRange": {"x": 0.0, "y": 1.0},
                "startDelay": {"minMaxState": 0, "scalar": 0.0},
                "InitialModule": {"maxNumParticles": 100,
                                  "startLifetime": {"minMaxState": 0,
                                                    "scalar": 2.0},
                                  "startSpeed": {"minMaxState": 0, "scalar": 1.0},
                                  "startSize": {"minMaxState": 0, "scalar": 1.0},
                                  "startRotation": {"minMaxState": 0,
                                                    "scalar": 0.0},
                                  "startColor": {"minMaxState": 0,
                                                 "maxColor": {"r": 1.0, "g": 1.0,
                                                              "b": 1.0, "a": 1.0}},
                                  "gravityModifier": {"minMaxState": 0,
                                                      "scalar": 0.0},
                                  "size3D": False, "rotation3D": False}}
        tree.update(extra or {})
        return self.add("ParticleSystem", tree, fail_read=fail_read,
                        path_id=path_id)

    def renderer(self, goid, materials, render_mode=None, mesh_pointer=None,
                 fail_read=False):
        tree = {"m_GameObject": {"m_FileID": 0, "m_PathID": goid},
                "m_Enabled": 1, "m_SortMode": 0, "m_SortingOrder": 0,
                "m_MinParticleSize": 0.0, "m_MaxParticleSize": 0.0,
                "m_LengthScale": 0.0, "m_VelocityScale": 0.0,
                "m_CameraVelocityScale": 0.0,
                "m_Pivot": {"x": 0.0, "y": 0.0, "z": 0.0},
                "m_RenderAlignment": 0, "m_UseCustomVertexStreams": False,
                "m_VertexStreams": [], "m_UseCustomTrailVertexStreams": False,
                "m_TrailVertexStreams": [],
                "m_Materials": list(materials)}
        if render_mode is not None:
            tree["m_RenderMode"] = render_mode
        if mesh_pointer is not None:
            tree["m_Mesh"] = mesh_pointer
        return self.add("ParticleSystemRenderer", tree, fail_read=fail_read)

    def material(self, name="mat", binding=(), shader_pid=None, path_id=None):
        shader = {"m_FileID": 0, "m_PathID": shader_pid} if shader_pid else {}
        tree = {"m_Name": name, "m_Shader": shader, "m_CustomRenderQueue": -1,
                "m_SavedProperties": {
                    "m_TexEnvs": [[key, {"m_Scale": {"x": 1.0, "y": 1.0},
                                          "m_Offset": {"x": 0.0, "y": 0.0},
                                          "m_Texture": {"m_FileID": 0,
                                                        "m_PathID": tex}}]
                                  for key, tex in binding],
                    "m_Floats": [["_Cull", 2.0]],
                    "m_Colors": [["_Color", {"r": 1.0, "g": 1.0,
                                             "b": 1.0, "a": 1.0}]]}}
        return self.add("Material", tree, path_id=path_id)

    def texture(self, name="tex", path_id=None):
        from PIL import Image
        image = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
        path_id = self._new() if path_id is None else path_id
        obj = _Object("Texture2D", path_id,
                      {"m_Name": name, "m_Width": 2, "m_Height": 2},
                      self.archive)
        obj._image = image
        self.objects.append(obj)
        return path_id

    def shader(self, name="Synthetic/Fixture"):
        return self.add("Shader", {"m_ParsedForm": {"m_Name": name}})

    def mesh(self, name="mesh", path_id=None):
        path_id = self._new() if path_id is None else path_id
        return self.add("Mesh", {"m_Name": name, "m_IsReadable": True},
                        path_id=path_id)

    def finish(self):
        return SimpleNamespace(objects=self.objects)


def _run(tmp_path, monkeypatch, packages, out="out"):
    for name in packages:
        (tmp_path / name).touch()
    monkeypatch.setattr(packages_module.UnityPy, "load",
                        lambda path: packages[os.path.basename(str(path))])
    store = PackageStore([str(tmp_path / name) for name in packages])
    out_path = tmp_path / out
    summary = extract_from_store(store, str(out_path))
    index = json.loads((out_path / "index.json").read_text(encoding="utf-8"))
    return index, out_path, summary


def _document(out_path, name):
    path = out_path / f"{name}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def test_emitter_and_renderer_pair_into_one_entry(tmp_path, monkeypatch):
    """Red when the renderer is not the same entry as the system: an emitter is
    the pair of a ParticleSystem and the ParticleSystemRenderer on the same
    node, and the node is the path a consumer lays a character down at."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    mat = pkg.material("main")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
    pkg.particle_system(goid)
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    assert len(document["emitters"]) == 1
    entry = document["emitters"][0]
    assert entry["node"] == "furniture"
    assert entry["system"]["maxParticles"] == 100
    assert entry["system"]["start"]["lifetime"]["value"] == 2.0
    assert entry["renderer"]["material"]["name"] == "main"
    meta = index["packages"][pkg.name]
    assert meta["emitters"] == 1
    assert meta["renderers"] == 1


def test_two_systems_share_the_node_renderer(tmp_path, monkeypatch):
    """Red when a node's several systems are fused into one emitter: the
    jacuzzi holds 103 systems on far fewer nodes, and each system keeps its own
    parameters while the node's single renderer is repeated."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    mat = pkg.material("main")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
    pkg.particle_system(goid, extra={"InitialModule": {
        "maxNumParticles": 100,
        "startLifetime": {"minMaxState": 0, "scalar": 2.0},
        "startSpeed": {"minMaxState": 0, "scalar": 1.0},
        "startSize": {"minMaxState": 0, "scalar": 1.0},
        "startRotation": {"minMaxState": 0, "scalar": 0.0},
        "startColor": {"minMaxState": 0,
                       "maxColor": {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}},
        "gravityModifier": {"minMaxState": 0, "scalar": 0.0},
        "size3D": False, "rotation3D": False}})
    pkg.particle_system(goid, extra={"InitialModule": {
        "maxNumParticles": 50,
        "startLifetime": {"minMaxState": 0, "scalar": 4.0}}})
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    assert len(document["emitters"]) == 2
    assert document["emitters"][0]["system"]["maxParticles"] == 100
    assert document["emitters"][1]["system"]["maxParticles"] == 50
    assert document["emitters"][0]["renderer"] is document["emitters"][1]["renderer"] \
        or document["emitters"][0]["renderer"] == document["emitters"][1]["renderer"]
    assert index["packages"][pkg.name]["renderers"] == 1
    assert index["packages"][pkg.name]["emitters"] == 2


def test_unmodelled_module_is_named_in_unsupported(tmp_path, monkeypatch):
    """Red when an enabled module the decoder does not model is dropped: a
    running custom-speed module must be named, with the emitter's node, not
    folded into a bucket or silently absent."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    mat = pkg.material("main")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
    pkg.particle_system(goid, extra={"ColorBySpeedModule": {"enabled": True}})
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    names = [(entry["module"], entry["node"]) for entry in document["unsupported"]]
    assert ("ColorBySpeedModule", "furniture") in names
    assert any(entry["module"] for entry in document["unsupported"])


def test_orphan_renderer_without_system_is_reported(tmp_path, monkeypatch):
    """Red when a renderer whose node has no system is silently swallowed: the
    renderer's count still belongs to the census, and the gap must name the node
    and the module rather than vanish."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    mat = pkg.material("main")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    assert index["packages"][pkg.name]["renderers"] == 1
    assert any(entry["module"] == "ParticleSystemRenderer"
               and entry["node"] == "furniture"
               and "no ParticleSystem" in entry["reason"]
               for entry in document["unsupported"])


def test_orphan_system_without_renderer_is_reported(tmp_path, monkeypatch):
    """Red when a system with no renderer on its node is silently dropped: the
    system's count still belongs to the census, and the gap must name the node
    rather than vanish."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    pkg.particle_system(goid)
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    assert index["packages"][pkg.name]["emitters"] == 1
    assert any(entry["node"] == "furniture"
               and "no ParticleSystemRenderer" in entry["reason"]
               for entry in document["unsupported"])


def test_zero_particle_package_leaves_no_document(tmp_path, monkeypatch):
    """Red when a package with no particle systems, or with a corrupted raw,
    produces a document anyway: the index must record it with count zero and no
    file, and the run must not raise."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    assert _document(out_path, pkg.name) is None
    meta = index["packages"][pkg.name]
    assert meta["emitters"] == 0
    assert meta["file"] is None
    assert meta["missing"] is False


def test_unresolved_material_stays_visible(tmp_path, monkeypatch):
    """Red when a material pointer the store cannot follow becomes null: the
    renderer must say so, with the archive it wanted, and the count of
    unresolved slots must move with it."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    pkg.renderer(goid, [{"m_FileID": 3, "m_PathID": 424242}])
    pkg.particle_system(goid)
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    material = document["emitters"][0]["renderer"]["material"]
    assert material["external"] is True
    assert material["fileId"] == 3
    assert material["pathId"] == 424242
    assert "not resolve" in material["reason"]
    assert index["packages"][pkg.name]["materialSlotsUnresolved"] == 1


def test_trail_material_is_the_second_slot(tmp_path, monkeypatch):
    """Red when the renderer's second material slot is dropped: the trail
    material is what a running trail module is drawn with, and only the second
    slot's presence says a trail exists on disk."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    one = pkg.material("one")
    two = pkg.material("two")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": one},
                        {"m_FileID": 0, "m_PathID": two}])
    pkg.particle_system(goid, extra={"TrailModule": {"enabled": True,
                                                     "mode": 0}})
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    renderer = document["emitters"][0]["renderer"]
    assert renderer["material"]["name"] == "one"
    assert renderer["trailMaterial"]["name"] == "two"
    assert document["emitters"][0]["system"]["trails"]["mode"] == "perParticle"


def test_mesh_drawn_renderer_carries_its_mesh(tmp_path, monkeypatch):
    """Red when a mesh-drawn renderer's mesh pointer vanishes: without the mesh
    there is nothing to draw particles as, and the reference must stay attached
    to the renderer with the slot it sits in."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    mat = pkg.material("main")
    mesh = pkg.mesh("shell-mesh")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}], render_mode=4,
                 mesh_pointer={"m_FileID": 0, "m_PathID": mesh})
    pkg.particle_system(goid)
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    renderer = document["emitters"][0]["renderer"]
    assert renderer["renderMode"] == "Mesh"
    assert len(renderer["meshes"]) == 1
    assert renderer["meshes"][0]["slot"] == 0
    assert renderer["meshes"][0]["resolved"] is True
    assert renderer["meshes"][0]["name"] == "shell-mesh"
    assert renderer["meshes"][0]["kind"] == "Mesh"


def test_duplicate_node_paths_across_parallel_roots_stay_separate(
        tmp_path, monkeypatch):
    """Red when two emitters that merely share a path are merged: several
    packages hold parallel root trees with the same names, and each emitter is
    identified by its game object id, never by its path alone."""
    pkg = _Package(FIXTURE)
    mat = pkg.material("main")
    for index, seed in enumerate((100, 50)):
        goid, _ = pkg.node("root")
        pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
        pkg.particle_system(goid, extra={"InitialModule": {
            "maxNumParticles": seed,
            "startLifetime": {"minMaxState": 0, "scalar": 2.0}}})
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    assert len(document["emitters"]) == 2
    assert document["emitters"][0]["node"] == "root"
    assert document["emitters"][1]["node"] == "root"
    assert document["emitters"][0]["gameObjectId"] \
        != document["emitters"][1]["gameObjectId"]
    assert sorted(entry["system"]["maxParticles"]
                  for entry in document["emitters"]) == [50, 100]


def test_sub_emitter_resolves_to_the_target_node(tmp_path, monkeypatch):
    """Red when a sub-emitter's raw path id leaks out instead of the node path:
    if the child system is in the package, the trigger names the child's node;
    only a pointer naming nothing is a gap."""
    pkg = _Package(FIXTURE)
    mat = pkg.material("main")
    parent_go, _ = pkg.node("parent")
    child_go, _ = pkg.node("child", parent_goid=parent_go)
    pkg.renderer(parent_go, [{"m_FileID": 0, "m_PathID": mat}])
    child_system = pkg.particle_system(child_go)
    pkg.particle_system(parent_go, extra={"SubModule": {
        "enabled": True,
        "subEmitters": [{"emitter": {"m_FileID": 0, "m_PathID": child_system},
                         "type": 2, "properties": 1, "emitProbability": 0.9}]}})
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    parent = next(entry for entry in document["emitters"]
                  if entry["node"] == "parent")
    assert parent["system"]["subEmitters"][0]["emitter"] == "parent/child"
    assert parent["system"]["subEmitters"][0]["type"] == "death"
    assert not [entry for entry in document["unsupported"]
                if entry["module"] == "SubModule"]


def test_sub_emitter_gap_names_the_reason(tmp_path, monkeypatch):
    """Red when an unresolvable sub-emitter pointer is reported as an empty
    value rather than as a gap: the entry must name the module and the reason,
    with the emitter's node attached."""
    pkg = _Package(FIXTURE)
    mat = pkg.material("main")
    goid, _ = pkg.node("parent")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
    pkg.particle_system(goid, extra={"SubModule": {
        "enabled": True,
        "subEmitters": [{"emitter": {"m_FileID": 0, "m_PathID": 999999},
                         "type": 2, "properties": 0, "emitProbability": 1.0}]}})
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    assert any(entry["module"] == "SubModule" and entry["node"] == "parent"
               and "not in this package" in entry["reason"]
               for entry in document["unsupported"])


def test_non_domain_packages_are_lookup_only(tmp_path, monkeypatch):
    """Red when a package another domain owns is extracted as well: the store
    is loaded whole so pointers can resolve against it, but only the three
    furniture domains' packages become documents."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    mat = pkg.material("main")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
    pkg.particle_system(goid)
    fx = _Package(FX)
    fx_goid, _ = fx.node("fx")
    fx_mat = fx.material("fx-mat")
    fx.renderer(fx_goid, [{"m_FileID": 0, "m_PathID": fx_mat}])
    fx.particle_system(fx_goid)
    site = _Package(SITE)
    index, out_path, summary = _run(tmp_path, monkeypatch,
                                    {pkg.name: pkg.finish(),
                                     fx.name: fx.finish(),
                                     site.name: site.finish()})
    assert set(index["packages"]) == {pkg.name}
    assert _document(out_path, fx.name) is None
    assert _document(out_path, site.name) is None
    assert summary["lookupPackages"] == 2


def test_material_across_packages_resolves(tmp_path, monkeypatch):
    """Red when a material in a companion package is reported unresolved: the
    fixture renderer's material pointers commonly name archives of the
    mysekai__effect__fixture__fx_* packages, which the store holds."""
    pkg = _Package(FIXTURE, archive_name="CAB-fixture-01")
    pkg.archive.externals = [SimpleNamespace(name="CAB-fx-01")]
    goid, _ = pkg.node("furniture")
    pkg.renderer(goid, [{"m_FileID": 1, "m_PathID": 6001}])
    pkg.particle_system(goid)
    fx = _Package(FX, archive_name="CAB-fx-01")
    fx.material("fx-mat", binding=(("_MainTex", TEXTURE_ID),), path_id=6001)
    fx.texture("fx-tex", path_id=TEXTURE_ID)
    index, out_path, _ = _run(tmp_path, monkeypatch,
                              {pkg.name: pkg.finish(), fx.name: fx.finish()})
    document = _document(out_path, pkg.name)
    material = document["emitters"][0]["renderer"]["material"]
    assert material["name"] == "fx-mat"
    assert not material.get("external")
    assert material["textures"]["_MainTex"]["name"] == "fx-tex"
    assert index["packages"][pkg.name]["materials"] == 1
    assert index["packages"][pkg.name]["textures"] == 1
    assert (out_path / "textures" / f"{fx.name}__fx-tex.png").is_file()


def test_unreadable_system_is_counted_and_named(tmp_path, monkeypatch):
    """Red when a particle system whose typetree will not read is dropped from
    the count: the census counts the object either way, so the package summary
    must carry it and a gap must say what failed."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    mat = pkg.material("main")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
    pkg.particle_system(goid, fail_read=True)
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    assert index["packages"][pkg.name]["emitters"] == 1
    assert index["packages"][pkg.name]["systemReadFailures"] == 1
    assert any(entry["module"] == "ParticleSystem"
               and "typetree could not be read" in entry["reason"]
               for entry in document["unsupported"])
    assert any(entry["systemError"] is not None
               for entry in document["emitters"])


def test_same_material_referenced_twice_counts_once(tmp_path, monkeypatch):
    """Red when one material object is counted per slot: a renderer's particle
    slot and trail slot can name the same object, and the material count is of
    distinct objects, not of bindings."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    one = pkg.material("one")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": one},
                        {"m_FileID": 0, "m_PathID": one}])
    pkg.particle_system(goid)
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    renderer = document["emitters"][0]["renderer"]
    assert renderer["material"] == renderer["trailMaterial"]
    assert index["packages"][pkg.name]["materials"] == 1


def test_every_texture_is_written_once(tmp_path, monkeypatch):
    """Red when the same image is saved again per material: the texture count
    is of distinct images, and the write happens once regardless of how many
    materials name it."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    tex = pkg.texture("shared")
    pkg.material("one", binding=(("_MainTex", tex),), path_id=6001)
    pkg.material("two", binding=(("_MainTex", tex),), path_id=6002)
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": 6001}])
    pkg.particle_system(goid)
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    assert index["packages"][pkg.name]["textures"] == 1
    assert len(list((out_path / "textures").glob("*.png"))) == 1


def test_unsupported_entries_always_carry_module_and_node(tmp_path,
                                                          monkeypatch):
    """Red when an unsupported entry is named without a module or a node: every
    gap is keyed by the module that gap is about and the node it sits on, and an
    empty bucket is not allowed to stand in for names."""
    pkg = _Package(FIXTURE)
    goid, _ = pkg.node("furniture")
    mat = pkg.material("main")
    pkg.renderer(goid, [{"m_FileID": 0, "m_PathID": mat}])
    pkg.particle_system(goid, extra={
        "ColorBySpeedModule": {"enabled": True},
        "ExternalForcesModule": {"enabled": True}})
    index, out_path, _ = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    document = _document(out_path, pkg.name)
    assert document["unsupported"], "gaps must not be silent"
    for entry in document["unsupported"]:
        assert isinstance(entry["module"], str) and entry["module"]
        assert isinstance(entry["node"], str) and entry["node"]
        assert entry["module"] != "other"
