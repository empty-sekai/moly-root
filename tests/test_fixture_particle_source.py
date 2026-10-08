"""Small fixture/weather source-contract joins, without a bundled game corpus."""
import json
from pathlib import Path

from test_fixture_particles import FIXTURE, _Package, _document, _run
from fixtures.particle_source import Geometry


def test_node_inventory_keeps_control_components_and_unknown_is_not_empty():
    from types import SimpleNamespace
    from fixtures.particles import _component_classes
    tree = {"m_Component": [{"component": {"m_FileID": 0, "m_PathID": n}}
                            for n in (1, 2, 3, 4)]}
    record = SimpleNamespace(tree=lambda _: tree,
        kinds={1: "Transform", 2: "ParticleSystem", 3: "PlayableDirector", 4: "MonoBehaviour"})
    store = SimpleNamespace(follow=lambda _, p: (record, p["m_PathID"]))
    assert _component_classes(store, record, 9) == ["ParticleSystem", "PlayableDirector", "MonoBehaviour"]
    tree["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": 99}})
    assert _component_classes(store, record, 9) is None
    tree.clear()
    assert _component_classes(store, record, 9) is None
    tree["m_Component"] = []
    assert _component_classes(store, record, 9) == []
    tree["m_Component"] = [{"component": {"m_FileID": 1, "m_PathID": 7}}]
    store.follow = lambda *_: None
    assert _component_classes(store, record, 9) is None


def test_fixture_export_keeps_source_material_and_declares_absent_shader_programs(tmp_path, monkeypatch):
    package = _Package(FIXTURE)
    go, _ = package.node("fountain")
    material = package.material("water", shader_pid=package.shader())
    package.renderer(go, [{"m_FileID": 0, "m_PathID": material}])
    package.particle_system(go)
    index, out, _ = _run(tmp_path, monkeypatch, {package.name: package.finish()})
    document = _document(out, package.name)
    assert document["particleSourceSchemaVersion"] == index["particleSourceSchemaVersion"] == 1
    assert document["nodeCoordinates"] == "unity-lh-y-up-authored"
    assert index["sourcePrograms"]["status"] == "source-only"
    assert index["sourcePrograms"]["compilerSupplied"] is False
    assert index["sourcePrograms"]["converted"] == 0
    assert "not playable" in index["sourcePrograms"]["note"]
    emitter = document["emitters"][0]
    assert emitter["system"]["sourceModules"]["version"] == 1
    assert "shapeEnabled" in emitter["system"]
    material = emitter["renderer"]["material"]
    assert material["sourceMaterial"]["schemaVersion"] == 1
    assert material["sourceMaterial"]["savedProperties"]
    assert material["shaderProgram"]["variants"]["file"].startswith("shaders/")
    catalogue = json.loads((out / material["shaderProgram"]["variants"]["file"]).read_text())
    # The synthetic shader has no compiled programs. It must not become a
    # successful hand-written replacement merely because its name was resolved.
    assert catalogue["sourceErrors"] == ["source shader declares no readable passes"]


def test_geometry_reference_keeps_source_bounds_and_slots_without_transforming_them(tmp_path, monkeypatch):
    geometry = Geometry(tmp_path / "models")
    from types import SimpleNamespace
    record = SimpleNamespace(kinds={9: "Mesh"})
    mesh = {"file": "models/water.glb", "sha256": "a" * 64, "bytes": 120,
            "meshSource": {"bundle": "fountain", "archive": "CAB-fountain", "pathId": "9"},
            "sourceBounds": {"center": [1, 2, 3], "extents": [4, 5, 6]},
            "meshes": [{"node": "water"}]}
    monkeypatch.setattr(geometry.store, "mesh", lambda _record, _path: mesh)
    reference, = geometry.references(record, 9, slot=2)
    assert reference["meshSlot"] == 2
    assert reference["bounds"] == mesh["sourceBounds"]
    assert reference["source"] == mesh["meshSource"]
    assert reference["coordinates"] == "gltf-reflect-x-flip-v"
    assert reference["file"] == "models/water.glb"
    assert reference["geometrySchemaVersion"] == 1


def test_unreadable_mesh_remains_an_explicit_source_failure(tmp_path, monkeypatch):
    package = _Package(FIXTURE)
    go, _ = package.node("fountain")
    material = package.material("water")
    mesh = package.mesh("water")  # Identity exists; actual vertex buffers do not.
    package.renderer(go, [{"m_FileID": 0, "m_PathID": material}], render_mode=4,
                     mesh_pointer={"m_FileID": 0, "m_PathID": mesh})
    package.particle_system(go)
    _, out, _ = _run(tmp_path, monkeypatch, {package.name: package.finish()})
    document = _document(out, package.name)
    renderer = document["emitters"][0]["renderer"]
    assert renderer["meshes"][0]["resolved"]
    assert renderer["meshes"][0]["geometryError"]
    assert len(renderer["meshSlots"]) == 4
    assert renderer["meshSlots"][0]["meshes"] == []
    assert renderer["meshSlots"][1]["reference"]["pathId"] == "0"
    assert any(row["module"] == "ParticleSystemRenderer" and row["meshSlot"] == 0
               for row in document["unsupported"])


def test_declared_dependency_material_is_loaded_without_explicit_input(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core.assets import packages as packages_module
    from core.assets.packages import PackageStore
    from fixtures.particles import extract_from_store
    from test_fixture_particles import FX

    dependency = _Package(FX)
    material = dependency.material("shared-water", shader_pid=dependency.shader())
    package = _Package(FIXTURE)
    package.archive.externals = [SimpleNamespace(name=dependency.archive.name)]
    package.add("AssetBundle", {"m_Dependencies": [FX.replace("__", "/")]})
    go, _ = package.node("fountain")
    package.renderer(go, [{"m_FileID": 1, "m_PathID": material}])
    package.particle_system(go)
    corpus = {package.name: package.finish(), dependency.name: dependency.finish()}
    for name in corpus:
        (tmp_path / name).touch()
    monkeypatch.setattr(packages_module.UnityPy, "load",
                        lambda path: corpus[Path(path).name])
    store = PackageStore([tmp_path / package.name], root=tmp_path)
    out = tmp_path / "out"
    summary = extract_from_store(store, out)
    document = _document(out, package.name)
    material = document["emitters"][0]["renderer"]["material"]
    assert material["name"] == "shared-water"
    assert material["sourceMaterial"]["source"]["package"]["bundle"] == FX
    assert summary["materialSlotsUnresolved"] == 0


def test_cli_does_not_present_source_only_particles_as_playable(capsys):
    from core.cli import _print_extract_summary
    _print_extract_summary({
        "summary": {"succeeded": 1, "requested": 1, "failed": 0, "unsupported": 0},
        "bundles": [], "report": "report.json", "derived": [{
            "status": "succeeded", "artifact": "fixture-particles/index.json",
            "sourcePrograms": {"status": "source-only", "converted": 0,
                               "gles3Variants": 2, "unavailable": 2,
                               "note": "No --shader-compiler: source data only, not playable particle assets."},
        }],
    }, "out")
    output = capsys.readouterr().out
    assert "source-only" in output
    assert "unavailable 2" in output
    assert "not playable" in output
