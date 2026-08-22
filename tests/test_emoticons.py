"""Overhead-item extraction: hierarchy, sprite wiring, and the shared index.

Three properties matter to a consumer and none of them are visible in a summary
count: animation channels must name the node they drive (the paths are relative
to the animator's own node, not to the package root), every sprite and texture
must survive (a package can hold several of each, and two packages can name
their textures identically), and the index is written once per package so it has
to accumulate instead of replacing what earlier packages wrote.
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chara.emoticons as emoticon_module
from chara.emoticons import (ATTR_NAME, LABEL_TYPE, ROOT_TYPE, _channels, _hierarchy,
                            _phase_of, _resolve_material, _texture_file, write_document)


def test_sprite_renderer_node_carries_resolved_material():
    objects = [
        SimpleNamespace(type=SimpleNamespace(name="GameObject"), path_id=1),
        SimpleNamespace(type=SimpleNamespace(name="Transform"), path_id=2),
        SimpleNamespace(type=SimpleNamespace(name="SpriteRenderer"), path_id=3),
    ]
    trees = {
        1: {"m_Name": "Bubble", "m_Component": [{"component": {"m_PathID": 2}},
                                                      {"component": {"m_PathID": 3}}]},
        2: {"m_GameObject": {"m_PathID": 1}, "m_Father": {"m_PathID": 0},
            "m_LocalPosition": {}, "m_LocalRotation": {}, "m_LocalScale": {}},
        3: {"m_GameObject": {"m_PathID": 1}, "m_Materials": [{"m_PathID": 9}],
            "m_Sprite": {"m_PathID": 0}},
    }
    nodes, _, _, _ = _hierarchy(objects, trees, material_resolver=lambda tree: {"name": "mat"})
    assert nodes[0]["material"] == {"name": "mat"}


def test_external_material_resolves_from_dependency_bundle():
    pointer = {"m_PathID": 9, "m_FileID": 1}
    dependency = {"fileId": 9, "pathId": 9, "material": {"name": "shared"}}
    result = _resolve_material(
        {"m_Materials": [pointer]}, {}, {}, ["main.bundle"],
        external_materials={("main.bundle", 1, 9): dependency})
    assert result == {"name": "shared"}


def test_missing_dependency_material_remains_external():
    result = _resolve_material(
        {"m_Materials": [{"m_PathID": 9, "m_FileID": 1}]}, {}, {}, ["main.bundle"])
    assert result == {"external": True, "fileId": 1, "archive": "main.bundle"}


def test_phase_names_are_spelled_out():
    assert _phase_of("clip_emote_001_start") == "start"
    assert _phase_of("clip_emote_001_loop") == "loop"
    assert _phase_of("clip_emote_001_end") == "end"
    assert _phase_of("clip_emote_001_middle") is None      # unknown phase stays unnamed


def _clip(bindings, dense_values, curve_count, frames, rate=10.0, stop=0.2):
    """Minimal generic-clip typetree with a dense curve block."""
    return {
        "m_Name": "clip_probe",
        "m_SampleRate": rate,
        "m_ClipBindingConstant": {"genericBindings": bindings},
        "m_MuscleClip": {
            "m_StopTime": stop,
            "m_Clip": {"data": {
                "m_StreamedClip": {"data": [], "curveCount": 0},
                "m_DenseClip": {"m_CurveCount": curve_count, "m_FrameCount": frames,
                                "m_BeginTime": 0.0, "m_SampleRate": rate,
                                "m_SampleArray": dense_values},
                "m_ConstantClip": {"data": []},
            }},
        },
    }


def test_transform_channels_are_grouped_and_resampled():
    # one Transform position binding (3 components) over 3 dense frames
    bindings = [{"typeID": 4, "attribute": 1, "path": 123, "script": {"m_PathID": 0}}]
    dense = [0.0, 0.0, 0.0,
             1.0, 2.0, 3.0,
             2.0, 4.0, 6.0]
    result = _channels(_clip(bindings, dense, 3, 3), {123: "root/bubble"})
    assert result["rate"] == 10.0 and result["frames"] == 3
    assert len(result["channels"]) == 1
    channel = result["channels"][0]
    assert channel["property"] == ATTR_NAME[1] == "position"
    assert channel["path"] == "root/bubble" and channel["pathHash"] == 123
    assert channel["values"][0] == [0.0, 0.0, 0.0]
    assert channel["values"][2] == [2.0, 4.0, 6.0]
    assert result["unsupported"] == []


def test_non_transform_bindings_are_reported_not_dropped():
    bindings = [
        {"typeID": 4, "attribute": 3, "path": 7, "script": {"m_PathID": 0}},
        {"typeID": 212, "attribute": 55, "path": 7, "script": {"m_PathID": 0}},
    ]
    dense = [1.0, 1.0, 1.0, 0.5] * 2
    result = _channels(_clip(bindings, dense, 4, 2), {7: "mark"})
    assert [c["property"] for c in result["channels"]] == ["scale"]
    assert result["unsupported"] == [{"attribute": 55, "reason": "non-transform binding"}]


def test_unresolved_path_hash_is_reported_not_silently_null():
    bindings = [{"typeID": 4, "attribute": 1, "path": 999, "script": {"m_PathID": 0}}]
    result = _channels(_clip(bindings, [0.0] * 6, 3, 2), {123: "other"})
    assert result["channels"][0]["path"] is None
    assert result["channels"][0]["pathHash"] == 999
    assert result["unsupported"] == [{"pathHash": 999, "reason": "path hash unresolved"}]


def test_texture_files_are_namespaced_by_package():
    # Two packages name a texture identically; one flat file name would let the
    # second overwrite the first.
    assert _texture_file("fx_a", "tex_shared") != _texture_file("fx_b", "tex_shared")
    assert _texture_file("fx_a", "tex_shared").endswith(".png")


def test_lookup_only_dependency_resolves_material_without_item(tmp_path, monkeypatch):
    class FakeAssetFile:
        def __init__(self, name, externals=()):
            self.name = name
            self.externals = list(externals)

    class FakeObject:
        def __init__(self, kind, path_id, tree, asset_file):
            self.type = SimpleNamespace(name=kind)
            self.path_id = path_id
            self._tree = tree
            self.assets_file = asset_file

        def read_typetree(self):
            return self._tree

    shader_file = FakeAssetFile("shader.assets")
    target_file = FakeAssetFile("target.assets", [SimpleNamespace(name="shader.assets")])
    shader_objects = [
        FakeObject("Material", 30, {
            "m_Name": "dependency-material",
            "m_Shader": {"m_PathID": 40, "m_FileID": 0},
            "m_CustomRenderQueue": -1,
            "m_SavedProperties": {},
        }, shader_file),
        FakeObject("Shader", 40, {
            "m_ParsedForm": {"m_Name": "Synthetic/Shader"},
        }, shader_file),
    ]
    target_objects = [
        FakeObject("GameObject", 1, {
            "m_Name": "Bubble",
            "m_Component": [{"component": {"m_PathID": 2}},
                            {"component": {"m_PathID": 3}}],
        }, target_file),
        FakeObject("Transform", 2, {
            "m_GameObject": {"m_PathID": 1},
            "m_Father": {"m_PathID": 0},
            "m_LocalPosition": {}, "m_LocalRotation": {}, "m_LocalScale": {},
        }, target_file),
        FakeObject("SpriteRenderer", 3, {
            "m_GameObject": {"m_PathID": 1},
            "m_Materials": [{"m_PathID": 30, "m_FileID": 1}],
            "m_Sprite": {"m_PathID": 0},
        }, target_file),
    ]
    environments = {
        "mysekai__effect__emoticon__fx_synthetic": SimpleNamespace(objects=target_objects),
        "mysekai__shader": SimpleNamespace(objects=shader_objects),
    }
    monkeypatch.setattr(emoticon_module.UnityPy, "load",
                        lambda path: environments[path])

    result = emoticon_module.extract_emoticons(
        list(environments), str(tmp_path))
    document = json.loads((tmp_path / "emoticons.json").read_text(encoding="utf-8"))
    assert result["items"] == 1
    assert list(document["items"]) == ["fx_synthetic"]
    material = document["items"]["fx_synthetic"]["nodes"][0]["material"]
    assert material["name"] == "dependency-material"
    assert material["shader"] == "Synthetic/Shader"
    assert material["renderQueue"] == -1


def test_document_accumulates_across_packages(tmp_path):
    # Each package is its own extraction job writing the shared index.
    write_document(str(tmp_path), {"fx_a": {"textures": [{"name": "t1"}], "unsupported": []}})
    write_document(str(tmp_path), {"fx_b": {"textures": [{"name": "t2"}],
                                           "unsupported": [{"reason": "particles"}]}})
    doc = json.loads((tmp_path / "emoticons.json").read_text(encoding="utf-8"))
    assert sorted(doc["items"]) == ["fx_a", "fx_b"]
    assert doc["summary"]["items"] == 2
    assert doc["summary"]["textures"] == 2
    assert doc["summary"]["unsupported"] == [{"item": "fx_b", "reason": "particles"}]


def test_document_rewrites_an_item_without_duplicating_it(tmp_path):
    write_document(str(tmp_path), {"fx_a": {"textures": [{"name": "t1"}], "unsupported": []}})
    write_document(str(tmp_path), {"fx_a": {"textures": [{"name": "t1b"}], "unsupported": []}})
    doc = json.loads((tmp_path / "emoticons.json").read_text(encoding="utf-8"))
    assert list(doc["items"]) == ["fx_a"]
    assert doc["items"]["fx_a"]["textures"] == [{"name": "t1b"}]
    assert doc["summary"]["textures"] == 1


def test_enum_names_are_spelled_out_for_consumers():
    assert LABEL_TYPE[0] == "PlaySE"
    assert ROOT_TYPE[0] == "Face" and ROOT_TYPE[2] == "Hips"


def test_a_previous_runs_index_is_not_carried_forward(tmp_path):
    """One run's index must hold exactly that run's items.

    The document accumulates across jobs on purpose, so without dropping a stale
    index an item removed from the manifest would linger in the next run's output.
    """
    from core.extract import extract_manifest

    packs = tmp_path / "out"
    (packs / "emoticons").mkdir(parents=True)
    write_document(str(packs / "emoticons"), {"fx_gone": {"textures": [], "unsupported": []}})
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("mysekai/effect/emoticon/fx_absent\n", encoding="utf-8")
    extract_manifest(str(manifest), str(tmp_path / "bundles"), str(packs))
    # The named package is not on disk, so no job rewrites the index; what must
    # not happen is the previous run's index surviving into this run's output.
    assert not (packs / "emoticons" / "emoticons.json").exists()
