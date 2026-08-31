"""Phenomena (weather) extraction: flattened config, ramp, profile, overrides, effects.

Four properties decide whether a phenomena package is usable and none of them show
up in a summary count.  The configuration asset must arrive **flattened but
complete** — a consumer that silently loses one of the five parameter groups gets
a plausible-looking sky with the wrong character shading.  The sky ramp is a
one-pixel-tall gradient, so a consumer must be able to trust its dimensions.
Site overrides are a two-level lookup, so a site that has an override must be
discoverable as *having* one rather than merged into the global values.  And
effect prefabs must go through the shared particle plan encoding, with any module
that is enabled but not modelled staying visible.
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from PIL import Image

import phenomena.environments as environments
from core import mesh as core_mesh
from pathlib import Path
from phenomena import audio, texarray
from phenomena.config import flatten_config, volume_profile
from phenomena.effects import decode_effect, light_modes


# -- synthetic bundles ------------------------------------------------------
#
# A phenomena bundle is a container of named assets plus a dependency list, so a
# fixture only needs objects that answer `type.name`, `path_id`,
# `read_typetree()`, and — for images — `read().image`.


class _AssetFile:
    def __init__(self, name, externals=()):
        self.name = name
        self.externals = [SimpleNamespace(name=e) for e in externals]


class _Object:
    def __init__(self, kind, path_id, tree, asset_file, image=None, images=None):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = asset_file
        self._image = image
        self._images = images

    def read_typetree(self):
        return self._tree

    def read(self):
        return SimpleNamespace(image=self._image, images=self._images or [])


def _color(r, g, b, a=1.0):
    return {"r": r, "g": g, "b": b, "a": a}


def _config_tree(name, angle_xz=80.0, home_active=1):
    """One `SiteEnvironmentConfig` asset, with every group at full width."""
    return {
        "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
        "m_Enabled": 1,
        "m_Script": {"m_FileID": 0, "m_PathID": 900},
        "m_Name": name,
        "description": "probe",
        "rendererType": 0,
        "_gridColorKey": "0_Default",
        "_emissionType": 1,
        "lightSettings": {
            "characterDirectionalLightColor": _color(1.0, 1.0, 1.0),
            "characterShadeSkinColor": _color(0.95, 0.89, 0.89),
            "characterBodyShadeColor": _color(0.93, 0.85, 0.85),
            "phenomenaDirectionalLightColor": _color(1.0, 1.0, 1.0),
            "phenomenaShadeColor": _color(0.82, 0.82, 0.94),
            "angleXZ": angle_xz,
            "angleY": 57.9,
            "_homeSiteLightAngleData": {"_isActive": home_active, "_angleXZ": 240.0,
                                        "_angleY": 70.0},
            "dropShadowColor1": _color(0.51, 0.55, 0.70, 0.33),
            "dropShadowColor2": _color(0.86, 0.12, 0.10, 0.23),
            "dropShadowEdgeSmoothness": 1.0,
        },
        "cloudSettings": {
            "cloudShadowTexture": {"m_FileID": 0, "m_PathID": 310},
            "cloudShadowOpacity": 0.674,
            "cloudShadowTextureSize": 0.2,
            "cloudScrollVelocity": {"x": 1.0, "y": 0.0},
            "cloudScrollSpeed": 0.757,
        },
        "globalCharacterSettings": {
            "outlineWidth": 0.0015, "outlineDepthOffset": 0.0,
            "outlineWidthMaxRate": 1.0, "outlineWidthMinRate": 1.0,
            "outlineColor": _color(0.0, 0.0, 0.0),
        },
        "globalFixtureSettings": {
            "_outlineWidth": 0.0, "_outlineDepthOffset": 0.0,
            "_outlineWidthMaxRate": 1.0, "_outlineWidthMinRate": 1.0,
            "_outlineColor": _color(0.0, 0.0, 0.0),
        },
        "globalWindSettingsData": {
            "windSpeed": 2.0, "windColor": _color(0.98, 1.0, 0.62, 0.27),
            "vertexWaveAnimationAmount": 0.01, "vertexWaveExponent": 2.0,
            "vertexRandomAnimationAmount": 0.1, "vertexRandomAnimationSpeed": 0.05,
            "windWaveDistortionAmount": 0.35, "windWaveDistortionFrequency": 1.0,
            "windNoiseTexture": {"m_FileID": 0, "m_PathID": 311},
        },
    }


def _fog_tree(density=1.0):
    return {
        "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
        "m_Enabled": 1,
        "m_Script": {"m_FileID": 0, "m_PathID": 901},
        "m_Name": "MysekaiFogVolume",
        "active": 1,
        "enabled": {"m_OverrideState": 1, "m_Value": 1},
        "density": {"m_OverrideState": 1, "m_Value": density},
        "nearColor": {"m_OverrideState": 0, "m_Value": _color(0.0, 0.0, 0.0, 0.0)},
        "nearDensity": {"m_OverrideState": 1, "m_Value": 0.0},
        "farColor": {"m_OverrideState": 1, "m_Value": _color(0.32, 0.75, 1.0)},
        "farDensity": {"m_OverrideState": 1, "m_Value": 0.5},
        "fogStartDistance": {"m_OverrideState": 1, "m_Value": 10.0},
        "fogEndDistance": {"m_OverrideState": 1, "m_Value": 30.0},
        "fogHeight": {"m_OverrideState": 1, "m_Value": 15.0},
    }


def _particle_tree(game_object, extra_modules=None):
    """A minimal emitter: two-constant lifetime, one burst, one enabled shape."""
    tree = {
        "m_GameObject": {"m_FileID": 0, "m_PathID": game_object},
        "lengthInSec": 5.0, "looping": 1, "prewarm": 0, "playOnAwake": 1,
        "simulationSpeed": 1.0, "moveWithTransform": 1, "randomSeed": 7,
        "InitialModule": {
            "enabled": 1, "maxNumParticles": 500,
            "startLifetime": {"minMaxState": 3, "minScalar": 1.0, "scalar": 2.0},
            "startSpeed": {"minMaxState": 0, "scalar": 3.0},
            "startSize": {"minMaxState": 0, "scalar": 0.5},
            "startRotation": {"minMaxState": 0, "scalar": 0.0},
            "startColor": {"minMaxState": 0, "maxColor": _color(1.0, 1.0, 1.0)},
            "gravityModifier": {"minMaxState": 0, "scalar": 0.0},
            "size3D": 0, "rotation3D": 0,
        },
        "EmissionModule": {
            "enabled": 1,
            "rateOverTime": {"minMaxState": 0, "scalar": 0.0},
            "rateOverDistance": {"minMaxState": 0, "scalar": 0.0},
            "m_Bursts": [{"time": 0.0, "countCurve": {"minMaxState": 0, "scalar": 12.0},
                          "cycleCount": 0, "repeatInterval": 1.5, "probability": 1.0}],
        },
        "ShapeModule": {"enabled": 1, "type": 5, "radius": {"value": 1.0},
                        "radiusThickness": 1.0, "angle": 25.0, "length": 5.0,
                        "arc": {"value": 360.0}, "boxThickness": {},
                        "donutRadius": 0.2, "m_Position": {}, "m_Rotation": {},
                        "m_Scale": {"x": 10.0, "y": 1.0, "z": 10.0},
                        "alignToDirection": 0, "randomDirectionAmount": 0.0,
                        "sphericalDirectionAmount": 0.0},
    }
    for module in extra_modules or ():
        tree[module] = {"enabled": 1}
    return tree


def _global_bundle(phenomenon, ramp_image=None, extra_modules=None):
    """A phenomenon's global variant: config, ramp, profile, and a sky effect."""
    archive = _AssetFile(f"{phenomenon}_global.assets")
    ramp_image = ramp_image or Image.new("RGBA", (32, 1), (10, 20, 30, 255))
    prefix = ("assets/sekai/assetbundle/resources/ondemand/mysekai/effect/site/"
              f"environment/{phenomenon}/global/")
    objects = [
        _Object("MonoScript", 900, {"m_ClassName": "SiteEnvironmentConfig"}, archive),
        _Object("MonoScript", 901, {"m_ClassName": "MysekaiFogVolume"}, archive),
        _Object("MonoScript", 902, {"m_ClassName": "VolumeProfile"}, archive),
        _Object("MonoScript", 903, {"m_ClassName": "RampTexture"}, archive),
        _Object("MonoBehaviour", 100, _config_tree(f"env_{phenomenon}"), archive),
        _Object("MonoBehaviour", 101, _fog_tree(), archive),
        _Object("MonoBehaviour", 102, {
            "m_Script": {"m_FileID": 0, "m_PathID": 902},
            "m_Name": f"postprocess_{phenomenon}",
            "components": [{"m_FileID": 0, "m_PathID": 101}]}, archive),
        _Object("MonoBehaviour", 103, {
            "m_Script": {"m_FileID": 0, "m_PathID": 903},
            "m_Name": f"ramp_sky_{phenomenon}",
            "_widthNpot2": 5,
            # The gradient the runtime reads the sky's bottom colour off. Its end
            # is a different colour from the picture's last pixel here, the way it
            # is in the real data for one phenomenon, so a reader that took the
            # pixel instead would show up.
            "_gradient": {"m_NumColorKeys": 2, "m_NumAlphaKeys": 2,
                          "key0": {"r": 1.0, "g": 0.0, "b": 0.0, "a": 1.0},
                          "key1": {"r": 0.25, "g": 0.5, "b": 0.75, "a": 0.5}},
            "_texture": {"m_FileID": 0, "m_PathID": 300}}, archive),
        _Object("Texture2D", 300, {"m_Name": "ramp", "m_Width": 32, "m_Height": 1},
                archive, image=ramp_image),
        _Object("Texture2D", 310, {"m_Name": f"tex_cloud_shadow_{phenomenon}",
                                   "m_Width": 4, "m_Height": 4}, archive,
                image=Image.new("RGBA", (4, 4))),
        _Object("Texture2D", 311, {"m_Name": f"tex_noise_{phenomenon}",
                                   "m_Width": 4, "m_Height": 4}, archive,
                image=Image.new("RGBA", (4, 4))),
        # sky effect: a root that draws nothing plus one emitting child
        _Object("GameObject", 200, {"m_Name": f"fx_env_sky_{phenomenon}",
                                    "m_Component": [{"component": {"m_PathID": 201}}]},
                archive),
        _Object("RectTransform", 201, {"m_GameObject": {"m_PathID": 200},
                                       "m_Father": {"m_PathID": 0},
                                       "m_LocalPosition": {}, "m_LocalRotation": {},
                                       "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0}},
                archive),
        _Object("GameObject", 210, {"m_Name": "drops", "m_IsActive": 1,
                                    "m_Component": [{"component": {"m_PathID": 211}},
                                                    {"component": {"m_PathID": 212}},
                                                    {"component": {"m_PathID": 213}}]},
                archive),
        _Object("Transform", 211, {"m_GameObject": {"m_PathID": 210},
                                   "m_Father": {"m_PathID": 201},
                                   "m_LocalPosition": {"x": 0.0, "y": 3.0, "z": 0.0},
                                   "m_LocalRotation": {}, "m_LocalScale": {}}, archive),
        _Object("ParticleSystem", 212, _particle_tree(210, extra_modules), archive),
        _Object("ParticleSystemRenderer", 213, {"m_GameObject": {"m_PathID": 210},
                                                "m_RenderMode": 0, "m_Materials": [],
                                                "m_Pivot": {}}, archive),
        _Object("AssetBundle", 1, {
            "m_Name": f"mysekai/effect/site/environment/{phenomenon}/global",
            "m_Dependencies": [],
            "m_Container": [
                [prefix + f"env_{phenomenon}.asset",
                 {"asset": {"m_FileID": 0, "m_PathID": 100}}],
                [prefix + f"postprocess_{phenomenon}.asset",
                 {"asset": {"m_FileID": 0, "m_PathID": 102}}],
                [prefix + f"ramp_sky_{phenomenon}.asset",
                 {"asset": {"m_FileID": 0, "m_PathID": 103}}],
                [prefix + f"tex_cloud_shadow_{phenomenon}.png",
                 {"asset": {"m_FileID": 0, "m_PathID": 310}}],
                [prefix + f"tex_noise_{phenomenon}.png",
                 {"asset": {"m_FileID": 0, "m_PathID": 311}}],
                [prefix + f"fx_env_sky_{phenomenon}.prefab",
                 {"asset": {"m_FileID": 0, "m_PathID": 200}}],
            ]}, archive),
    ]
    return SimpleNamespace(objects=objects)


def _first_floor_bundle(phenomenon):
    """The one site variant that carries overriding config and profile."""
    archive = _AssetFile(f"{phenomenon}_first_floor.assets")
    prefix = ("assets/sekai/assetbundle/resources/ondemand/mysekai/effect/site/"
              f"environment/{phenomenon}/unique/first_floor/")
    objects = [
        _Object("MonoScript", 900, {"m_ClassName": "SiteEnvironmentConfig"}, archive),
        _Object("MonoScript", 901, {"m_ClassName": "MysekaiFogVolume"}, archive),
        _Object("MonoScript", 902, {"m_ClassName": "VolumeProfile"}, archive),
        _Object("MonoBehaviour", 100,
                _config_tree(f"env_{phenomenon}", angle_xz=80.0, home_active=0), archive),
        _Object("MonoBehaviour", 101, _fog_tree(density=0.25), archive),
        _Object("MonoBehaviour", 102, {
            "m_Script": {"m_FileID": 0, "m_PathID": 902},
            "m_Name": f"postprocess_{phenomenon}",
            "components": [{"m_FileID": 0, "m_PathID": 101}]}, archive),
        _Object("AssetBundle", 1, {
            "m_Name": (f"mysekai/effect/site/environment/{phenomenon}/unique/first_floor"),
            "m_Dependencies": [f"mysekai/effect/site/environment/{phenomenon}/global"],
            "m_Container": [
                [prefix + f"env_{phenomenon}.asset",
                 {"asset": {"m_FileID": 0, "m_PathID": 100}}],
                [prefix + f"postprocess_{phenomenon}.asset",
                 {"asset": {"m_FileID": 0, "m_PathID": 102}}],
            ]}, archive),
    ]
    return SimpleNamespace(objects=objects)


def _site_bundle(phenomenon, site):
    """A site variant that carries only an effect prefab."""
    archive = _AssetFile(f"{phenomenon}_{site}.assets")
    prefix = ("assets/sekai/assetbundle/resources/ondemand/mysekai/effect/site/"
              f"environment/{phenomenon}/unique/{site}/")
    objects = [
        _Object("GameObject", 200, {"m_Name": f"fx_env_site_{phenomenon}_{site}",
                                    "m_Component": [{"component": {"m_PathID": 201}}]},
                archive),
        _Object("Transform", 201, {"m_GameObject": {"m_PathID": 200},
                                   "m_Father": {"m_PathID": 0}, "m_LocalPosition": {},
                                   "m_LocalRotation": {}, "m_LocalScale": {}}, archive),
        _Object("GameObject", 210, {"m_Name": "puff",
                                    "m_Component": [{"component": {"m_PathID": 211}},
                                                    {"component": {"m_PathID": 212}}]},
                archive),
        _Object("Transform", 211, {"m_GameObject": {"m_PathID": 210},
                                   "m_Father": {"m_PathID": 201}, "m_LocalPosition": {},
                                   "m_LocalRotation": {}, "m_LocalScale": {}}, archive),
        _Object("ParticleSystem", 212, _particle_tree(210), archive),
        _Object("AssetBundle", 1, {
            "m_Name": f"mysekai/effect/site/environment/{phenomenon}/unique/{site}",
            "m_Dependencies": [],
            "m_Container": [[prefix + f"fx_env_site_{phenomenon}_{site}.prefab",
                             {"asset": {"m_FileID": 0, "m_PathID": 200}}]]}, archive),
    ]
    return SimpleNamespace(objects=objects)


def _thumbnail_bundle(names):
    archive = _AssetFile("thumbnail.assets")
    objects = []
    container = []
    for index, name in enumerate(names):
        path_id = 400 + index
        objects.append(_Object("Texture2D", path_id,
                               {"m_Name": name, "m_Width": 152, "m_Height": 152},
                               archive, image=Image.new("RGBA", (152, 152))))
        container.append([f"assets/sekai/assetbundle/resources/ondemand/mysekai/"
                          f"thumbnail/phenomena/{name}.png",
                          {"asset": {"m_FileID": 0, "m_PathID": path_id}}])
    objects.append(_Object("AssetBundle", 1, {"m_Name": "mysekai/thumbnail/phenomena",
                                              "m_Dependencies": [],
                                              "m_Container": container}, archive))
    return SimpleNamespace(objects=objects)


def _store(monkeypatch, mapping):
    """Serve fixture bundles by path, the way the loader reads them from disk."""
    monkeypatch.setattr(environments.UnityPy, "load",
                        lambda path: mapping[os.path.basename(str(path))])

def _run(tmp_path, monkeypatch, mapping, engine=None, **kwargs):
    """*engine* holds files the loader serves that are **not** packages of the run.

    They are handed in the way a caller hands in the engine's own containers: as
    extra pointer targets, never as bundles to extract.  Left out, the run is the
    default one.
    """
    _store(monkeypatch, {**mapping, **(engine or {})})
    out = tmp_path / "out" / "phenomena"
    if engine:
        kwargs["extra_archives"] = [str(tmp_path / "engine" / name) for name in engine]
    result = environments.extract_phenomena(
        [str(tmp_path / "bundles" / name) for name in mapping], str(out), **kwargs)
    return result, out, json.loads((out / "index.json").read_text(encoding="utf-8"))


def _shared_bundle(phenomenon):
    """The companion package the effect materials and images actually live in."""
    archive = _AssetFile(f"{phenomenon}_common.assets")
    prefix = ("assets/sekai/assetbundle/resources/ondemand/mysekai/effect/site/"
              f"environment/{phenomenon}/common/")
    objects = [
        _Object("Material", 500, {
            "m_Name": f"mat_env_{phenomenon}_drop",
            "m_Shader": {"m_FileID": 0, "m_PathID": 501},
            "m_CustomRenderQueue": 3000,
            "m_SavedProperties": {
                "m_TexEnvs": [["_BaseMap", {"m_Texture": {"m_FileID": 0,
                                                          "m_PathID": 502}}]],
                "m_Floats": [["_Cutoff", 0.5]],
                "m_Colors": [["_BaseColor", _color(1.0, 1.0, 1.0)]]}}, archive),
        _Object("Shader", 501, {"m_ParsedForm": {"m_Name": "Probe/Effect/Unlit"}},
                archive),
        _Object("Texture2D", 502, {"m_Name": f"tex_env_{phenomenon}_drop",
                                   "m_Width": 8, "m_Height": 8}, archive,
                image=Image.new("RGBA", (8, 8))),
        _Object("AssetBundle", 1, {
            "m_Name": f"mysekai/effect/site/environment/{phenomenon}/common",
            "m_Dependencies": [],
            "m_Container": [[prefix + f"mat_env_{phenomenon}_drop.mat",
                             {"asset": {"m_FileID": 0, "m_PathID": 500}}]]}, archive),
    ]
    return SimpleNamespace(objects=objects)


def _dependent_site_bundle(phenomenon, site):
    """A site effect whose material pointer crosses into the shared package."""
    bundle = _site_bundle(phenomenon, site)
    archive = bundle.objects[0].assets_file
    archive.externals = [SimpleNamespace(name=f"{phenomenon}_common.assets")]
    bundle.objects.insert(-1, _Object(
        "ParticleSystemRenderer", 213,
        {"m_GameObject": {"m_PathID": 210}, "m_RenderMode": 0, "m_Pivot": {},
         "m_Materials": [{"m_FileID": 1, "m_PathID": 500}]}, archive))
    for obj in bundle.objects:
        if obj.type.name == "GameObject" and obj.path_id == 210:
            obj._tree["m_Component"].append({"component": {"m_PathID": 213}})
        if obj.type.name == "AssetBundle":
            obj._tree["m_Dependencies"] = [
                f"mysekai/effect/site/environment/{phenomenon}/common"]
    return bundle


# -- configuration ----------------------------------------------------------


def test_config_keeps_every_group_at_full_width():
    document, unsupported = flatten_config(_config_tree("env_001_sunny"))
    assert unsupported == []
    assert len(document["light"]) == 11
    assert len(document["cloud"]) == 5
    assert len(document["character"]) == 5
    assert len(document["fixture"]) == 5
    assert len(document["wind"]) == 9
    # The leading underscores of the source fields are not part of the contract.
    assert document["gridColorKey"] == "0_Default"
    assert document["emissionType"] == 1
    assert document["light"]["homeSiteLightAngle"] == {"active": True, "angleXZ": 240.0,
                                                       "angleY": 70.0}
    assert document["light"]["dropShadowColor1"] == [0.51, 0.55, 0.70, 0.33]
    assert document["cloud"]["cloudScrollVelocity"] == [1.0, 0.0]


def test_config_values_are_not_rounded():
    tree = _config_tree("env_001_sunny")
    tree["lightSettings"]["characterShadeSkinColor"]["r"] = 0.9528301954269409
    document, _ = flatten_config(tree)
    assert document["light"]["characterShadeSkinColor"][0] == 0.9528301954269409


def test_config_reports_a_field_this_extractor_does_not_model():
    tree = _config_tree("env_001_sunny")
    tree["lightSettings"]["moonHaloColor"] = _color(1.0, 1.0, 1.0)
    _, unsupported = flatten_config(tree)
    assert unsupported == [{"group": "lightSettings", "field": "moonHaloColor",
                            "reason": "configuration field not modelled"}]


def test_config_reports_a_field_the_asset_does_not_carry():
    tree = _config_tree("env_001_sunny")
    del tree["globalWindSettingsData"]["windNoiseTexture"]
    document, unsupported = flatten_config(tree)
    assert len(document["wind"]) == 8
    assert unsupported == [{"group": "globalWindSettingsData", "field": "windNoiseTexture",
                            "reason": "configuration field absent from the asset"}]


def test_config_texture_pointers_resolve_to_files():
    document, _ = flatten_config(
        _config_tree("env_001_sunny"),
        texture_ref=lambda pointer: {"name": "tex", "file": "textures/tex.png"})
    assert document["cloud"]["cloudShadowTexture"] == {"name": "tex",
                                                       "file": "textures/tex.png"}
    assert document["wind"]["windNoiseTexture"]["file"] == "textures/tex.png"


# -- post-processing profile ------------------------------------------------


def test_volume_profile_keeps_override_state_per_parameter():
    document, unsupported = volume_profile(
        [("MysekaiFogVolume", "MysekaiFogVolume", _fog_tree())])
    assert unsupported == []
    component = document["components"][0]
    assert component["name"] == "MysekaiFogVolume" and component["active"] is True
    assert len(component["parameters"]) == 9
    assert component["parameters"]["density"] == {"overrideState": True, "value": 1.0}
    # An off override state is the difference between "the profile sets this" and
    # "the profile leaves the inherited value alone".
    assert component["parameters"]["nearColor"] == {"overrideState": False,
                                                    "value": [0.0, 0.0, 0.0, 0.0]}


def test_volume_profile_reports_a_parameter_shape_it_cannot_decode():
    tree = _fog_tree()
    tree["fogCurve"] = {"m_OverrideState": 1, "m_Value": {"m_Curve": []}}
    _, unsupported = volume_profile([("MysekaiFogVolume", "MysekaiFogVolume", tree)])
    assert unsupported == [{"component": "MysekaiFogVolume", "parameter": "fogCurve",
                            "reason": "parameter value shape not modelled"}]


def test_volume_profile_reports_a_field_that_is_not_a_parameter():
    tree = _fog_tree()
    tree["someFlag"] = 3
    _, unsupported = volume_profile([("MysekaiFogVolume", "MysekaiFogVolume", tree)])
    assert unsupported == [{"component": "MysekaiFogVolume", "field": "someFlag",
                            "reason": "component field is not a volume parameter"}]


# -- effect prefabs ---------------------------------------------------------


def _no_material(renderer_tree, slot=0):
    """A resolver for prefabs whose renderers name no material at all."""
    return None


def test_effect_particles_use_the_shared_plan_encoding():
    kinds, trees = {}, {}
    for obj in _site_bundle("006_rain", "grasslands").objects:
        kinds[obj.path_id] = obj.type.name
        trees[obj.path_id] = obj.read_typetree()
    effect, unsupported = decode_effect(200, kinds, trees, _no_material)
    assert unsupported == []
    assert [node["path"] for node in effect["nodes"]] == ["", "puff"]
    assert len(effect["particles"]) == 1
    system = effect["particles"][0]["system"]
    assert effect["particles"][0]["node"] == "puff"
    assert system["start"]["lifetime"] == {"mode": "twoConstants", "min": 1.0, "max": 2.0}
    assert system["emission"]["bursts"][0]["count"] == {"mode": "constant", "value": 12.0}
    assert system["emission"]["bursts"][0]["cycleCount"] == 0
    assert system["shape"]["type"] == "Box"


def test_effect_reports_an_enabled_module_it_does_not_model():
    bundle = _global_bundle("006_rain", extra_modules=("LightsModule",))
    kinds = {obj.path_id: obj.type.name for obj in bundle.objects}
    trees = {obj.path_id: obj.read_typetree() for obj in bundle.objects}
    _, unsupported = decode_effect(200, kinds, trees, _no_material)
    assert {"node": "drops", "module": "LightsModule",
            "reason": "particle module not modelled"} in unsupported


def _read(bundle):
    """A prefab's components by id, plus the class each script instantiates."""
    kinds = {obj.path_id: obj.type.name for obj in bundle.objects}
    trees = {obj.path_id: obj.read_typetree() for obj in bundle.objects}
    scripts = {pid: str(tree.get("m_ClassName", ""))
               for pid, tree in trees.items() if kinds[pid] == "MonoScript"}

    def script_of(path_id):
        return scripts.get(trees[path_id].get("m_Script", {}).get("m_PathID", 0))
    return kinds, trees, script_of


def _with_effector(bundle, rotation_type=0):
    """Put the lifecycle component on the prefab root, where the host looks."""
    archive = bundle.objects[0].assets_file
    for obj in bundle.objects:
        if obj.type.name == "GameObject" and obj.path_id == 200:
            obj._tree["m_Component"].append({"component": {"m_PathID": 250}})
    bundle.objects.insert(-1, _Object("MonoScript", 904,
                                      {"m_ClassName": "SiteEnvironmentEffector"},
                                      archive))
    bundle.objects.insert(-1, _Object("MonoBehaviour", 250, {
        "m_GameObject": {"m_PathID": 200},
        "m_Script": {"m_FileID": 0, "m_PathID": 904},
        "m_Name": "",
        "_timeUntilDestroy": 2.0,
        "_initializeRotateType": rotation_type}, archive))
    return bundle


def test_a_sub_emitter_resolves_to_the_node_that_holds_it():
    # A raw path id is useless to a consumer; the node path is what the rest of
    # the document is keyed by.
    bundle = _global_bundle("006_rain")
    kinds, trees, _ = _read(bundle)
    trees[212]["SubModule"] = {
        "enabled": 1,
        "subEmitters": [{"emitter": {"m_FileID": 0, "m_PathID": 212}, "type": 2,
                         "properties": 0, "emitProbability": 1.0}]}
    effect, unsupported = decode_effect(200, kinds, trees, _no_material)
    assert unsupported == []
    entry = effect["particles"][0]["system"]["subEmitters"][0]
    assert entry["emitter"] == "drops" and entry["type"] == "death"


def test_a_sub_emitter_in_another_package_is_reported_not_silently_dropped():
    bundle = _global_bundle("006_rain")
    kinds, trees, _ = _read(bundle)
    trees[212]["SubModule"] = {
        "enabled": 1,
        "subEmitters": [{"emitter": {"m_FileID": 2, "m_PathID": 212}, "type": 0,
                         "properties": 0, "emitProbability": 1.0}]}
    effect, unsupported = decode_effect(200, kinds, trees, _no_material)
    assert effect["particles"][0]["system"]["subEmitters"][0]["emitter"] is None
    assert {"node": "drops", "module": "SubModule",
            "reason": "sub-emitter is not in this package"} in unsupported


def test_the_trail_material_is_resolved_from_the_second_slot():
    bundle = _global_bundle("006_rain")
    kinds, trees, _ = _read(bundle)
    trees[212]["TrailModule"] = {"enabled": 1, "mode": 0, "ratio": 1.0,
                                 "lifetime": {"minMaxState": 0, "scalar": 0.1}}
    trees[213]["m_Materials"] = [{"m_FileID": 0, "m_PathID": 41},
                                 {"m_FileID": 0, "m_PathID": 42}]
    effect, unsupported = decode_effect(
        200, kinds, trees, lambda tree, slot=0: {"slot": slot})
    assert unsupported == []
    renderer = effect["particles"][0]["renderer"]
    assert renderer["material"] == {"slot": 0}
    assert renderer["trailMaterial"] == {"slot": 1}


def test_a_camera_effect_that_carries_the_component_keeps_its_own_rotation_type():
    # The host starts a component it finds with no argument, so the serialized
    # rotation type wins and the effect turns with the camera.
    kinds, trees, script_of = _read(_with_effector(_global_bundle("004_fine")))
    effect, unsupported = decode_effect(200, kinds, trees, _no_material,
                                        kind="camera", script_of=script_of)
    assert effect["effectors"] == [{"node": "", "timeUntilDestroy": 2.0,
                                    "rotationType": "normal"}]
    assert effect["effectiveRotation"] == "normal"
    # and it is no longer reported as a component this exporter cannot handle
    assert [row for row in unsupported if row.get("component")] == []


def test_a_camera_effect_without_the_component_counter_rotates_instead():
    kinds, trees, script_of = _read(_global_bundle("001_sunny"))
    effect, _ = decode_effect(200, kinds, trees, _no_material, kind="camera",
                              script_of=script_of)
    assert effect["effectors"] == []
    assert effect["effectiveRotation"] == "fix"


def test_a_camera_effect_whose_component_asks_to_be_fixed_says_fixed():
    kinds, trees, script_of = _read(_with_effector(_global_bundle("004_fine"),
                                                   rotation_type=1))
    effect, _ = decode_effect(200, kinds, trees, _no_material, kind="camera",
                              script_of=script_of)
    assert effect["effectiveRotation"] == "fix"


def _every_document(out):
    """Every JSON document the run wrote, as (path, text)."""
    return [(path, path.read_text(encoding="utf-8"))
            for path in sorted(Path(out).rglob("*.json"))]


def _strict(text):
    """Parse text the way a browser does: no bare Infinity, -Infinity or NaN."""
    def reject(word):
        raise AssertionError(f"not JSON any strict parser accepts: {word}")
    return json.loads(text, parse_constant=reject)


def test_every_document_is_json_a_strict_parser_accepts(tmp_path, monkeypatch):
    # Python's encoder writes bare `Infinity` and `NaN`, which are not JSON and
    # which JSON.parse rejects outright — one such value makes a whole document
    # unreadable, not just that field.
    mapping = {"mysekai__effect__site__environment__006_rain__global":
               _global_bundle("006_rain"),
               "mysekai__effect__site__environment__006_rain__unique__grasslands":
               _site_bundle("006_rain", "grasslands")}
    _, out, _ = _run(tmp_path, monkeypatch, mapping)
    documents = _every_document(out)
    assert documents
    for path, text in documents:
        _strict(text)


def test_an_unbounded_value_survives_as_a_name_and_not_as_a_dropped_field(tmp_path,
                                                                         monkeypatch):
    # An emitter whose particles never expire by age, and whose burst has no next
    # cycle, is authored with infinities.  Clamping or dropping them would change
    # what the asset says, so they are written as names a reader can coerce back.
    mapping = {"mysekai__effect__site__environment__006_rain__global":
               _global_bundle("006_rain")}
    _store(monkeypatch, mapping)
    bundle = mapping["mysekai__effect__site__environment__006_rain__global"]
    for obj in bundle.objects:
        if obj.type.name == "ParticleSystem":
            obj._tree["InitialModule"]["startLifetime"] = {"minMaxState": 0,
                                                           "scalar": float("inf"),
                                                           "minScalar": 5.0}
            obj._tree["EmissionModule"]["m_Bursts"][0]["repeatInterval"] = float("inf")
    out = tmp_path / "out" / "phenomena"
    environments.extract_phenomena(
        [str(tmp_path / "bundles" / name) for name in mapping], str(out))
    text = (out / "006_rain" / "fx" / "effects.json").read_text(encoding="utf-8")
    document = _strict(text)
    system = document["effects"]["fx_env_sky_006_rain"]["particles"][0]["system"]
    assert system["start"]["lifetime"] == {"mode": "constant", "value": "Infinity"}
    assert system["emission"]["bursts"][0]["repeatInterval"] == "Infinity"


def test_no_document_carries_a_path_from_the_machine_that_wrote_it(tmp_path,
                                                                  monkeypatch):
    # These documents are published; a path from the machine that produced them
    # is both a leak and useless to whoever reads them.
    mapping = {"mysekai__effect__site__environment__006_rain__global":
               _global_bundle("006_rain"),
               "mysekai__effect__site__environment__006_rain__unique__grasslands":
               _site_bundle("006_rain", "grasslands")}
    _, out, _ = _run(tmp_path, monkeypatch, mapping)
    documents = _every_document(out)
    assert documents
    roots = (str(tmp_path), str(tmp_path).replace(os.sep, "/"),
             str(Path(tmp_path).as_posix()))
    for path, text in documents:
        for root in roots:
            assert root not in text, f"{path.name} carries a host path"
        for value in _paths_in(_strict(text)):
            assert not os.path.isabs(value), f"{path.name} carries {value!r}"


def _paths_in(document):
    """Strings in a document that look like a filesystem path."""
    found = []
    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and ("/" in node or "\\" in node):
            found.append(node)
    walk(document)
    return found


def test_an_effect_that_is_not_camera_attached_claims_no_rotation_rule():
    kinds, trees, script_of = _read(_with_effector(_global_bundle("015_cloud")))
    effect, _ = decode_effect(200, kinds, trees, _no_material, kind="sky",
                              script_of=script_of)
    # the component is still reported, but how a sky effect is attached is not
    # established here, so no rule is claimed
    assert effect["effectors"][0]["timeUntilDestroy"] == 2.0
    assert effect["effectiveRotation"] is None


# -- package layout ---------------------------------------------------------


def test_ramp_is_written_as_the_one_pixel_tall_gradient(tmp_path, monkeypatch):
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny")}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    ramp = out / "001_sunny" / "ramp.png"
    assert Image.open(ramp).size == (32, 1)
    assert index["phenomena"]["001_sunny"]["ramp"] == {
        "file": "001_sunny/ramp.png", "width": 32, "height": 1,
        # The gradient's end, not the picture's last pixel: the two differ here.
        "skyBottomColor": [0.25, 0.5, 0.75, 0.5]}
    assert result["phenomena"] == 1 and result["ramps"] == 1


def test_site_override_is_reported_as_an_override_not_merged(tmp_path, monkeypatch):
    mapping = {
        "mysekai__effect__site__environment__001_sunny__global":
            _global_bundle("001_sunny"),
        "mysekai__effect__site__environment__001_sunny__unique__first_floor":
            _first_floor_bundle("001_sunny"),
        "mysekai__effect__site__environment__001_sunny__unique__grasslands":
            _site_bundle("001_sunny", "grasslands"),
    }
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    entry = index["phenomena"]["001_sunny"]
    assert sorted(entry["overrides"]) == ["first_floor"]
    override = json.loads((out / "001_sunny" / "overrides" / "first_floor"
                           / "config.json").read_text(encoding="utf-8"))
    base = json.loads((out / "001_sunny" / "config.json").read_text(encoding="utf-8"))
    # The override carries the whole configuration, and it really differs.
    assert len(override["light"]) == 11
    assert override["light"]["homeSiteLightAngle"]["active"] is False
    assert base["light"]["homeSiteLightAngle"]["active"] is True
    assert entry["overrides"]["first_floor"]["postprocess"] == (
        "001_sunny/overrides/first_floor/postprocess.json")
    # The site that only holds an effect is not an override.
    assert "grasslands" not in entry["overrides"]
    assert entry["fx"]["site"] == 1 and entry["fx"]["sky"] == 1
    assert result["overrides"] == 1


def test_effects_land_next_to_the_configuration(tmp_path, monkeypatch):
    mapping = {"mysekai__effect__site__environment__006_rain__global":
               _global_bundle("006_rain"),
               "mysekai__effect__site__environment__006_rain__unique__beach":
               _site_bundle("006_rain", "beach")}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    document = json.loads((out / "006_rain" / "fx" / "effects.json")
                          .read_text(encoding="utf-8"))
    assert sorted(document["effects"]) == ["fx_env_site_006_rain_beach",
                                           "fx_env_sky_006_rain"]
    assert document["effects"]["fx_env_site_006_rain_beach"]["kind"] == "site"
    assert document["effects"]["fx_env_site_006_rain_beach"]["site"] == "beach"
    assert document["effects"]["fx_env_sky_006_rain"]["kind"] == "sky"
    assert document["summary"]["emitters"] == 2
    assert index["phenomena"]["006_rain"]["fx"]["emitters"] == 2
    assert result["emitters"] == 2


def test_configuration_textures_are_written_and_referenced(tmp_path, monkeypatch):
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny")}
    _, out, index = _run(tmp_path, monkeypatch, mapping)
    config = json.loads((out / "001_sunny" / "config.json").read_text(encoding="utf-8"))
    reference = config["cloud"]["cloudShadowTexture"]
    assert reference["name"] == "tex_cloud_shadow_001_sunny"
    assert (out / reference["file"]).exists()
    assert reference["file"].startswith("001_sunny/textures/")


def test_a_declared_dependency_resolves_the_material_it_holds(tmp_path, monkeypatch):
    """Effect materials live in a companion package, so dependencies must be followed.

    Extracting a package on its own leaves every cross-package material pointer
    unresolved, which looks like an effect drawing with the engine default rather
    than like a missing input — so the unresolved state must stay distinguishable,
    and following the package's own dependency list must resolve it.
    """
    site = "mysekai__effect__site__environment__006_rain__unique__grasslands"
    shared = "mysekai__effect__site__environment__006_rain__common"
    mapping = {site: _dependent_site_bundle("006_rain", "grasslands"),
               shared: _shared_bundle("006_rain")}

    _store(monkeypatch, mapping)
    alone = tmp_path / "alone" / "phenomena"
    environments.extract_phenomena([str(tmp_path / "bundles" / site)], str(alone))
    document = json.loads((alone / "006_rain" / "fx" / "effects.json")
                          .read_text(encoding="utf-8"))
    material = document["effects"]["fx_env_site_006_rain_grasslands"]["particles"][0][
        "renderer"]["material"]
    assert material == {"external": True, "fileId": 1,
                        "archive": "006_rain_common.assets"}
    index = json.loads((alone / "index.json").read_text(encoding="utf-8"))
    assert index["summary"]["missing"]["dependencies"] == [shared]

    result, out, index = _run(tmp_path, monkeypatch, mapping,
                              bundle_root=str(tmp_path / "bundles"))
    document = json.loads((out / "006_rain" / "fx" / "effects.json")
                          .read_text(encoding="utf-8"))
    material = document["effects"]["fx_env_site_006_rain_grasslands"]["particles"][0][
        "renderer"]["material"]
    assert material["name"] == "mat_env_006_rain_drop"
    assert material["shader"] == "Probe/Effect/Unlit"
    assert material["renderQueue"] == 3000
    assert material["floats"] == {"_Cutoff": 0.5}
    # The image the material samples came out of the dependency, named after it.
    image = material["textures"]["_BaseMap"]
    assert image == "006_rain/textures/006_rain_common__tex_env_006_rain_drop.png"
    assert (out / image).exists()
    assert "dependencies" not in index["summary"]["missing"]


def test_light_modes_reads_each_pass_state_tag_in_order():
    """The pass list one material commits to: state tags, pass by pass.

    The tags live on m_State, not on the pass itself: a pass's own m_Tags
    is empty on every shader in these packages, so reading the pass level would
    yield a well-formed list of Nones that says nothing.  A pass without the
    tag keeps its position with None rather than being dropped, and a
    subshader with no passes contributes nothing.
    """
    parsed = {"m_Name": "Probe/Effect/Unlit", "m_SubShaders": [{
        "m_Passes": [
            {"m_Tags": {"tags": []},
             "m_State": {"m_Tags": {"tags": [["LIGHTMODE", "UniversalForward"],
                                             ["RenderType", "Opaque"]]}}},
            {"m_State": {"m_Tags": {"tags": [["LIGHTMODE", "SHADOWCASTER"]]}}},
            {"m_State": {}},
        ]}]}
    assert light_modes(parsed) == ["UniversalForward", "SHADOWCASTER", None]
    assert light_modes({}) == []


def test_a_resolved_material_carries_its_shader_light_modes(tmp_path, monkeypatch):
    """Every resolved material reports the LIGHTMODE tags of its shader's passes."""
    site = "mysekai__effect__site__environment__006_rain__unique__grasslands"
    shared = "mysekai__effect__site__environment__006_rain__common"
    mapping = {site: _dependent_site_bundle("006_rain", "grasslands"),
               shared: _shared_bundle("006_rain")}
    result, out, index = _run(tmp_path, monkeypatch, mapping,
                              bundle_root=str(tmp_path / "bundles"))
    document = json.loads((out / "006_rain" / "fx" / "effects.json")
                          .read_text(encoding="utf-8"))
    material = document["effects"]["fx_env_site_006_rain_grasslands"]["particles"][0][
        "renderer"]["material"]
    assert material["lightModes"] == []


def test_a_material_whose_shader_is_unresolvable_fails_the_run(tmp_path, monkeypatch):
    """An unreadable pass list must not come out as an empty claim.

    The field commits to what a shader declares; a shader that cannot be read
    from the supplied packages declares nothing *to us*, which an empty array
    would quietly present as a fact about the shader.  The run fails instead, so
    the missing input stays visible.
    """
    site = "mysekai__effect__site__environment__006_rain__unique__grasslands"
    shared = "mysekai__effect__site__environment__006_rain__common"
    bundle = _shared_bundle("006_rain")
    for obj in bundle.objects:
        if obj.path_id == 500:                     # the material
            obj._tree["m_Shader"] = {"m_FileID": 0, "m_PathID": 999}
    with pytest.raises(ValueError, match="names a shader no supplied package"):
        _run(tmp_path, monkeypatch, {site: _dependent_site_bundle("006_rain",
                                                                  "grasslands"),
                                     shared: bundle},
             bundle_root=str(tmp_path / "bundles"))


def test_a_shader_without_a_parsed_form_fails_the_run(tmp_path, monkeypatch):
    bundle = _shared_bundle("006_rain")
    for obj in bundle.objects:
        if obj.path_id == 501:                     # the shader
            obj._tree["m_ParsedForm"] = None
    mapping = {"mysekai__effect__site__environment__006_rain__unique__grasslands":
               _dependent_site_bundle("006_rain", "grasslands"),
               "mysekai__effect__site__environment__006_rain__common": bundle}
    with pytest.raises(ValueError, match="has no parsed form"):
        _run(tmp_path, monkeypatch, mapping, bundle_root=str(tmp_path / "bundles"))


def test_icons_come_from_the_shared_thumbnail_package(tmp_path, monkeypatch):
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny"),
               "mysekai__thumbnail__phenomena":
               _thumbnail_bundle(["env_sunny", "env_default"])}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    assert (out / "icons" / "env_sunny.png").exists()
    assert Image.open(out / "icons" / "env_default.png").size == (152, 152)
    assert result["icons"] == 2
    # Without master tables the icon of a phenomenon is not guessed from its name.
    assert index["phenomena"]["001_sunny"]["icon"] is None
    assert index["icons"] == ["env_default", "env_sunny"]


def test_icons_are_skipped_when_the_thumbnail_package_is_absent(tmp_path, monkeypatch):
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny")}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    assert result["icons"] == 0
    assert not (out / "icons").exists()
    assert index["summary"]["missing"]["icons"] == (
        "the shared phenomena thumbnail package was not supplied")


def test_an_image_that_is_not_a_single_picture_is_counted_as_absent(tmp_path,
                                                                   monkeypatch):
    """The image count must equal the files on disk, not the pointers followed."""
    bundle = _global_bundle("001_sunny")
    for obj in bundle.objects:
        if obj.path_id == 310:                     # the cloud shadow image
            obj.type = SimpleNamespace(name="Cubemap")
    mapping = {"mysekai__effect__site__environment__001_sunny__global": bundle}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    written = list((out / "001_sunny" / "textures").glob("*.png"))
    assert result["images"] == len(written) == 1
    config = json.loads((out / "001_sunny" / "config.json").read_text(encoding="utf-8"))
    assert config["cloud"]["cloudShadowTexture"]["file"] is None
    assert {"image": "tex_cloud_shadow_001_sunny",
            "reason": "Cubemap is not a single image",
            "phenomenon": "001_sunny"} in index["summary"]["unsupported"]


# -- index and master tables ------------------------------------------------


def test_index_without_master_says_why_the_rows_are_absent(tmp_path, monkeypatch):
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny")}
    _, _, index = _run(tmp_path, monkeypatch, mapping)
    entry = index["phenomena"]["001_sunny"]
    assert entry["id"] is None and entry["master"] is None
    assert entry["bgms"] is None and entry["siteSounds"] is None
    assert index["refreshTimePeriods"] is None
    assert index["summary"]["missing"]["master"]
    assert "master" in index["summary"]["missing"]["master"]


def _master_dir(tmp_path):
    directory = tmp_path / "master"
    directory.mkdir()
    tables = {
        "mysekaiPhenomenas": [
            {"id": 1, "assetbundleName": "001_sunny", "name": "hare",
             "englishName": "SUNNY", "description": "sunny sky",
             "iconAssetbundleName": "env_sunny",
             "rampTextureAssetbundleName": "001_sunny",
             "mysekaiPhenomenaTimePeriodType": "daytime",
             "mysekaiPhenomenaBrightnessType": "normal",
             "mysekaiPhenomenaBackgroundColorId": 1}],
        "mysekaiPhenomenaBgms": [
            {"id": 1, "mysekaiPhenomenaId": 1, "assetbundleName": "bgm_probe",
             "cue": "bgm_probe"}],
        "mysekaiSiteMysekaiPhenomenaSounds": [
            {"id": 1, "mysekaiSiteId": 5, "cue": "se_probe_wind",
             "mysekaiPhenomenaSoundConditionType": "other"},
            {"id": 301, "mysekaiSiteId": 5, "mysekaiPhenomenaId": 1,
             "cue": "se_probe_specific",
             "mysekaiPhenomenaSoundConditionType": "specific"}],
        "mysekaiRefreshTimePeriods": [{"id": 1, "startHour": 5, "endHour": 17},
                                      {"id": 2, "startHour": 17, "endHour": 29}],
        "clientConfigs": [{"id": 170, "type": "Int", "value": "99"},
                          {"id": 171, "type": "String", "value": "999_festivalgarden"}],
    }
    for name, rows in tables.items():
        (directory / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")
    return directory


def test_index_with_master_joins_the_rows(tmp_path, monkeypatch):
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny"),
               "mysekai__thumbnail__phenomena": _thumbnail_bundle(["env_sunny"])}
    _, _, index = _run(tmp_path, monkeypatch, mapping,
                       master=str(_master_dir(tmp_path)))
    entry = index["phenomena"]["001_sunny"]
    assert entry["id"] == 1
    assert entry["master"]["englishName"] == "SUNNY"
    assert entry["master"]["timePeriodType"] == "daytime"
    assert entry["master"]["brightnessType"] == "normal"
    assert entry["icon"] == "icons/env_sunny.png"
    assert [row["cue"] for row in entry["bgms"]] == ["bgm_probe"]
    assert [row["cue"] for row in entry["siteSounds"]] == ["se_probe_specific"]
    assert [row["cue"] for row in index["siteSoundFallbacks"]] == ["se_probe_wind"]
    assert index["refreshTimePeriods"] == [{"id": 1, "startHour": 5, "endHour": 17},
                                           {"id": 2, "startHour": 17, "endHour": 29}]
    assert "master" not in index["summary"]["missing"]


def test_an_ambience_cue_is_not_a_package_name(tmp_path, monkeypatch):
    """Music and ambience are addressed differently, so both name their package.

    A music row names its own sound package, but every ambience cue lives in one
    shared package — so reading an ambience cue as a package name looks for a
    package that does not exist and reports a real cue as missing audio.
    """
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny")}
    _, _, index = _run(tmp_path, monkeypatch, mapping,
                       master=str(_master_dir(tmp_path)))
    entry = index["phenomena"]["001_sunny"]
    assert entry["bgms"][0]["package"] == "mysekai__sound__bgm__bgm_probe"
    shared = "mysekai__sound__se__se_mysekai"
    assert index["ambiencePackage"] == shared
    assert entry["siteSounds"][0]["package"] == shared
    assert index["siteSoundFallbacks"][0]["package"] == shared
    # The cue is not a package, and it is not reported as an absent one either.
    assert "se_probe_specific" not in json.dumps(index["summary"]["missing"])
    assert shared not in index["summary"]["missing"].get("dependencies", [])
    # A sound package no caller supplied is reported as that package, by name.
    assert {"package": shared, "cues": ["se_probe_specific", "se_probe_wind"],
            "reason": "sound package was not supplied or reachable"
            } in index["summary"]["unsupported"]
    assert index["summary"]["missing"]["audio"] == environments.NO_AUDIO_PACKAGES


def test_the_delivery_phenomenon_has_no_master_row(tmp_path, monkeypatch):
    mapping = {"mysekai__effect__site__environment__999_festivalgarden__global":
               _global_bundle("999_festivalgarden")}
    _, _, index = _run(tmp_path, monkeypatch, mapping,
                       master=str(_master_dir(tmp_path)))
    entry = index["phenomena"]["999_festivalgarden"]
    assert entry["master"] is None
    assert entry["id"] == 99                      # named by the client configuration
    assert "client config" in entry["note"]




# -- texture arrays, geometry, timeline, audio ------------------------------
#
# Four kinds of asset in these packages are not a picture, a number or a curve,
# and each of them is a way for an export to look complete while being wrong: a
# texture array read as one picture draws a white quad, a model asset left out
# turns authored geometry into an approximation, a mesh-mode emitter without its
# mesh draws nothing, and a timeline left out makes the one phenomenon that
# flashes a static one.  Audio is the opposite case: the archive is here but
# decoding it needs a program this repository does not ship, so the property is
# that the run stays honest and unbroken without it.


def _array_object(archive, path_id, name, layers=4, size=(8, 8)):
    """One texture array: N distinct pictures of the same size and format."""
    return _Object("Texture2DArray", path_id,
                   {"m_Name": name, "m_Width": size[0], "m_Height": size[1],
                    "m_Depth": layers, "m_Format": 134, "m_ColorSpace": 1,
                    "m_MipCount": 1,
                    "m_StreamData": {"offset": 0, "size": 0, "path": ""}},
                   archive,
                   images=[Image.new("RGBA", size, (20 + 40 * i, 0, 0, 255))
                           for i in range(layers)])


def _array_common_bundle(phenomenon, mode=1.0, coord=1.0, slices=4.0, layers=4,
                         progress=0.0):
    """A common package whose material binds a texture array, not a picture."""
    archive = _AssetFile(f"{phenomenon}_common.assets")
    prefix = ("assets/sekai/assetbundle/resources/ondemand/mysekai/effect/site/"
              f"environment/{phenomenon}/common/")
    keywords = ["_BASE_MAP_MODE_2D_ARRAY"] if mode == 1.0 else ["_BASE_MAP_MODE_2D"]
    objects = [
        _Object("Material", 500, {
            "m_Name": f"mat_env_{phenomenon}_pt",
            "m_Shader": {"m_FileID": 0, "m_PathID": 501},
            "m_CustomRenderQueue": 3000,
            "m_ValidKeywords": keywords,
            "m_SavedProperties": {
                "m_TexEnvs": [
                    ["_BaseMap", {"m_Texture": {"m_FileID": 0, "m_PathID": 0}}],
                    ["_BaseMap2DArray", {"m_Texture": {"m_FileID": 0,
                                                       "m_PathID": 503}}]],
                "m_Floats": [["_BaseMapMode", mode],
                             ["_BaseMapSliceCount", slices],
                             ["_BaseMapProgress", progress],
                             ["_BaseMapProgressCoord", coord],
                             ["_BaseMapOffsetXCoord", 0.0],
                             ["_BaseMapOffsetYCoord", 0.0]],
                "m_Colors": [["_BaseColor", _color(1.0, 1.0, 1.0)]]}}, archive),
        _Object("Shader", 501, {"m_ParsedForm": {"m_Name": "Probe/Effect/UberUnlit"}},
                archive),
        _array_object(archive, 503, f"tex_env_{phenomenon}_pt_01", layers=layers),
        _Object("AssetBundle", 1, {
            "m_Name": f"mysekai/effect/site/environment/{phenomenon}/common",
            "m_Dependencies": [],
            "m_Container": [[prefix + f"mat_env_{phenomenon}_pt.mat",
                             {"asset": {"m_FileID": 0, "m_PathID": 500}}]]}, archive),
    ]
    return SimpleNamespace(objects=objects)


class _StubMeshHandler:
    """The vertex arrays of a fixture mesh, in place of the reader's own decoder."""

    ARRAYS = {}

    def __init__(self, mesh):
        self._name = getattr(mesh, "name", None)

    def process(self):
        data = self.ARRAYS[self._name]
        self.m_VertexCount = len(data["vertices"])
        self.m_Vertices = data["vertices"]
        self.m_Normals = data.get("normals") or []
        self.m_UV0 = data.get("uv0") or []
        self.m_IndexBuffer = data["indices"]
        self.m_Colors = None


def _mesh_object(archive, path_id, name, vertices, indices):
    _StubMeshHandler.ARRAYS[name] = {
        "vertices": vertices,
        "normals": [(0.0, 1.0, 0.0)] * len(vertices),
        "uv0": [(0.0, 0.0)] * len(vertices),
        "indices": indices}
    tree = {"m_Name": name,
            "m_SubMeshes": [{"topology": 0, "indexCount": len(indices)}]}
    obj = _Object("Mesh", path_id, tree, archive)
    obj.read = lambda: SimpleNamespace(name=name)
    return obj


def _model_common_bundle(phenomenon, mesh_name="dome_01"):
    """A common package holding one model asset: a node tree with a mesh on it."""
    archive = _AssetFile(f"{phenomenon}_common.assets")
    prefix = ("assets/sekai/assetbundle/resources/ondemand/mysekai/effect/site/"
              f"environment/{phenomenon}/common/")
    objects = [
        _Object("GameObject", 600, {"m_Name": f"fbx_env_{phenomenon}_dome",
                                    "m_IsActive": 1,
                                    "m_Component": [{"component": {"m_PathID": 601}}]},
                archive),
        _Object("Transform", 601, {"m_GameObject": {"m_PathID": 600},
                                   "m_Father": {"m_PathID": 0},
                                   "m_LocalPosition": {}, "m_LocalRotation": {"w": 1.0},
                                   "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0}},
                archive),
        _Object("GameObject", 610, {"m_Name": mesh_name, "m_IsActive": 1,
                                    "m_Component": [{"component": {"m_PathID": 611}},
                                                    {"component": {"m_PathID": 612}},
                                                    {"component": {"m_PathID": 613}}]},
                archive),
        _Object("Transform", 611, {"m_GameObject": {"m_PathID": 610},
                                   "m_Father": {"m_PathID": 601},
                                   "m_LocalPosition": {"x": 1.0, "y": 2.0, "z": 3.0},
                                   "m_LocalRotation": {"w": 1.0},
                                   "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0}},
                archive),
        _Object("MeshFilter", 612, {"m_GameObject": {"m_PathID": 610},
                                    "m_Mesh": {"m_FileID": 0, "m_PathID": 620}},
                archive),
        _Object("MeshRenderer", 613, {"m_GameObject": {"m_PathID": 610},
                                      "m_Materials": [{"m_FileID": 0,
                                                       "m_PathID": 630}]}, archive),
        _mesh_object(archive, 620, mesh_name,
                     [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)], [0, 1, 2]),
        _Object("Material", 630, {"m_Name": "Lit", "m_SavedProperties": {}}, archive),
        _Object("AssetBundle", 1, {
            "m_Name": f"mysekai/effect/site/environment/{phenomenon}/common",
            "m_Dependencies": [],
            "m_Container": [
                [prefix + f"fbx_env_{phenomenon}_dome.fbx",
                 {"asset": {"m_FileID": 0, "m_PathID": 600}}],
                [prefix + f"fbx_env_{phenomenon}_dome.fbx",
                 {"asset": {"m_FileID": 0, "m_PathID": 620}}],
            ]}, archive),
    ]
    return SimpleNamespace(objects=objects)


def _mesh_emitter_bundle(phenomenon, site, mesh_file_id=0, mesh_path_id=620):
    """A site effect whose emitter draws copies of a mesh instead of quads."""
    bundle = _site_bundle(phenomenon, site)
    archive = bundle.objects[0].assets_file
    archive.externals = [SimpleNamespace(name="unity default resources")]
    bundle.objects.insert(-1, _Object(
        "ParticleSystemRenderer", 213,
        {"m_GameObject": {"m_PathID": 210}, "m_RenderMode": 4, "m_Pivot": {},
         "m_Materials": [], "m_Mesh": {"m_FileID": mesh_file_id,
                                       "m_PathID": mesh_path_id}},
        archive))
    if mesh_file_id == 0:
        bundle.objects.insert(-1, _mesh_object(
            archive, mesh_path_id, f"pt_{phenomenon}",
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)],
            [0, 1, 2, 2, 1, 3]))
    for obj in bundle.objects:
        if obj.type.name == "GameObject" and obj.path_id == 210:
            obj._tree["m_Component"].append({"component": {"m_PathID": 213}})
    return bundle


# The engine fixes the path ids of its own primitives; a pointer into its
# container names one of those numbers, and no package has any say in it.
BUILTIN_PLANE = 10209


def _engine_container(path_id=BUILTIN_PLANE, name="Plane"):
    """The engine's own resource container: a primitive, and no package around it.

    It is a bare serialized file, not an asset bundle — no ``AssetBundle`` object
    inside it and nothing declaring it a dependency — so the only way it can be
    reached is a caller handing the file over.
    """
    archive = _AssetFile("unity default resources")
    return SimpleNamespace(objects=[
        _mesh_object(archive, path_id, name,
                     [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0),
                      (1.0, 0.0, 1.0)],
                     [0, 1, 2, 2, 1, 3])])


def _builtin_emitter_mapping():
    """One phenomenon whose mesh emitter draws a primitive out of the engine."""
    return {"mysekai__effect__site__environment__008_thunder__global":
            _global_bundle("008_thunder"),
            "mysekai__effect__site__environment__008_thunder__unique__beach":
            _mesh_emitter_bundle("008_thunder", "beach", mesh_file_id=1,
                                 mesh_path_id=BUILTIN_PLANE)}


def _builtin_model_bundle(phenomenon):
    """A model asset whose mesh node is an engine primitive, not an authored mesh."""
    bundle = _model_common_bundle(phenomenon)
    archive = bundle.objects[0].assets_file
    archive.externals = [SimpleNamespace(name="unity default resources")]
    bundle.objects = [obj for obj in bundle.objects
                      if not (obj.type.name == "Mesh" and obj.path_id == 620)]
    for obj in bundle.objects:
        if obj.type.name == "MeshFilter":
            obj._tree["m_Mesh"] = {"m_FileID": 1, "m_PathID": BUILTIN_PLANE}
        if obj.type.name == "AssetBundle":
            obj._tree["m_Container"] = [
                entry for entry in obj._tree["m_Container"]
                if entry[1]["asset"]["m_PathID"] != 620]
    return bundle


def _builtin_shape_bundle(phenomenon, site):
    """A site effect that is *born on* a primitive's surface, not drawn as one."""
    bundle = _site_bundle(phenomenon, site)
    archive = bundle.objects[0].assets_file
    archive.externals = [SimpleNamespace(name="unity default resources")]
    for obj in bundle.objects:
        if obj.type.name == "ParticleSystem":
            obj._tree["ShapeModule"]["type"] = 6          # born on a mesh surface
            obj._tree["ShapeModule"]["m_Mesh"] = {"m_FileID": 1,
                                                  "m_PathID": BUILTIN_PLANE}
    return bundle


def _inert_component_bundle(phenomenon, site):
    """A site effect carrying the components this exporter reads and omits."""
    bundle = _site_bundle(phenomenon, site)
    archive = bundle.objects[0].assets_file
    for obj in bundle.objects:
        if obj.type.name == "GameObject" and obj.path_id == 210:
            obj._tree["m_Component"].append({"component": {"m_PathID": 214}})
    bundle.objects.insert(-1, _Object("CanvasRenderer", 214,
                                      {"m_GameObject": {"m_PathID": 210}}, archive))
    bundle.objects.insert(-1, _Object(
        "GameObject", 220, {"m_Name": "nav_collider", "m_IsActive": 1,
                            "m_Component": [{"component": {"m_PathID": 221}},
                                            {"component": {"m_PathID": 222}},
                                            {"component": {"m_PathID": 223}}]},
        archive))
    bundle.objects.insert(-1, _Object("Transform", 221,
                                      {"m_GameObject": {"m_PathID": 220},
                                       "m_Father": {"m_PathID": 201},
                                       "m_LocalPosition": {}, "m_LocalRotation": {},
                                       "m_LocalScale": {}}, archive))
    bundle.objects.insert(-1, _Object("MeshFilter", 222,
                                      {"m_GameObject": {"m_PathID": 220},
                                       "m_Mesh": {"m_FileID": 3,
                                                  "m_PathID": 777}}, archive))
    bundle.objects.insert(-1, _Object("MeshCollider", 223,
                                      {"m_GameObject": {"m_PathID": 220},
                                       "m_Mesh": {"m_FileID": 3, "m_PathID": 777},
                                       "m_Convex": 0}, archive))
    return bundle


def _timeline_global_bundle(phenomenon):
    """A global variant that also carries the timeline driving this phenomenon."""
    bundle = _global_bundle(phenomenon)
    archive = bundle.objects[0].assets_file
    gradient = {"key0": _color(1.0, 0.9, 0.8), "key1": _color(0.0, 0.0, 0.0),
                "ctime0": 0, "ctime1": 65535, "atime0": 0, "atime1": 32768,
                "m_NumColorKeys": 2, "m_NumAlphaKeys": 2, "m_Mode": 0}
    curve = {"m_Curve": [{"time": 0.0, "value": 0.0, "inSlope": 0.0, "outSlope": 0.0,
                          "weightedMode": 0, "inWeight": 0.0, "outWeight": 0.0},
                         {"time": 1.0, "value": 1.0, "inSlope": float("inf"),
                          "outSlope": 0.0, "weightedMode": 0, "inWeight": 0.0,
                          "outWeight": 0.0}],
             "m_PreInfinity": 2, "m_PostInfinity": 2}
    extra = [
        _Object("MonoScript", 910, {"m_ClassName": "TimelineAsset"}, archive),
        _Object("MonoScript", 911, {"m_ClassName": "SiteEnvironmentColorTrack"},
                archive),
        _Object("MonoScript", 912, {"m_ClassName": "SiteEnvironmentValueTrack"},
                archive),
        _Object("MonoScript", 913, {"m_ClassName": "SiteEnvironmentColorClip"},
                archive),
        _Object("MonoScript", 914, {"m_ClassName": "SiteEnvironmentValueClip"},
                archive),
        _Object("MonoBehaviour", 700, {
            "m_Script": {"m_FileID": 0, "m_PathID": 910},
            "m_Name": f"env_{phenomenon}",
            "m_Tracks": [{"m_FileID": 0, "m_PathID": 710},
                         {"m_FileID": 0, "m_PathID": 720}],
            "m_FixedDuration": 23.333333, "m_DurationMode": 1,
            "m_EditorSettings": {"m_Framerate": 60.0},
            "m_MarkerTrack": {"m_FileID": 0, "m_PathID": 0}}, archive),
        _Object("MonoBehaviour", 710, {
            "m_Script": {"m_FileID": 0, "m_PathID": 911},
            "m_Name": "Sky Flash Color", "m_Muted": 0, "m_Locked": 0,
            "_controlTarget": 1, "m_Children": [],
            "m_Clips": [{"m_Start": 0.9, "m_Duration": 1.55, "m_ClipIn": 0.0,
                         "m_TimeScale": 1.0, "m_DisplayName": "SkyAdditiveColorClip",
                         "m_Asset": {"m_FileID": 0, "m_PathID": 730},
                         "m_BlendInDuration": 0.0, "m_BlendOutDuration": 0.0,
                         "m_EaseInDuration": 0.0, "m_EaseOutDuration": 0.0,
                         "m_PreExtrapolationMode": 0,
                         "m_PostExtrapolationMode": 0}]}, archive),
        _Object("MonoBehaviour", 720, {
            "m_Script": {"m_FileID": 0, "m_PathID": 912},
            "m_Name": "Light Flash", "m_Muted": 0, "m_Locked": 0,
            "_controlTarget": 2, "_scale": 2.56, "m_Children": [],
            "m_Clips": [{"m_Start": 5.0, "m_Duration": 1.2333, "m_ClipIn": 0.25,
                         "m_TimeScale": 1.0, "m_DisplayName": "LightAdditiveClip",
                         "m_Asset": {"m_FileID": 0, "m_PathID": 740},
                         "m_BlendInDuration": 0.0, "m_BlendOutDuration": 0.0,
                         "m_EaseInDuration": 0.0, "m_EaseOutDuration": 0.0,
                         "m_PreExtrapolationMode": 0,
                         "m_PostExtrapolationMode": 0}]}, archive),
        _Object("MonoBehaviour", 730, {"m_Script": {"m_FileID": 0, "m_PathID": 913},
                                       "m_Name": "SkyAdditiveColorClip",
                                       "_gradient": gradient}, archive),
        _Object("MonoBehaviour", 740, {"m_Script": {"m_FileID": 0, "m_PathID": 914},
                                       "m_Name": "SiteEnvironmentValueClip",
                                       "_scale": 1.0, "_curve": curve}, archive),
    ]
    prefix = ("assets/sekai/assetbundle/resources/ondemand/mysekai/effect/site/"
              f"environment/{phenomenon}/global/")
    for obj in bundle.objects:
        if obj.type.name == "AssetBundle":
            obj._tree["m_Container"].append(
                [prefix + f"env_{phenomenon}.playable",
                 {"asset": {"m_FileID": 0, "m_PathID": 700}}])
    bundle.objects[:0] = extra
    return bundle
def _sound_bundle(name):
    """A sound package: one encoded archive under a container path, nothing else."""
    archive = _AssetFile(f"{name}_sound.assets")
    prefix = ("assets/sekai/assetbundle/resources/ondemand/mysekai/sound/"
              f"{name}/")
    objects = [
        _Object("TextAsset", 800, {"m_Name": name, "m_Script": "@UTF probe"}, archive),
        _Object("AssetBundle", 1, {
            "m_Name": f"mysekai/sound/{name}", "m_Dependencies": [],
            "m_Container": [[prefix + f"{name}.acb.bytes",
                             {"asset": {"m_FileID": 0, "m_PathID": 800}}]]}, archive),
    ]
    return SimpleNamespace(objects=objects)


def _fake_decoder(monkeypatch, tmp_path, cues=None):
    """Stand in for the external decoder, printing the metadata it prints."""
    cues = cues or {1: ["se_probe_specific"], 2: ["se_probe_wind"],
                    3: ["se_probe_wind"]}
    monkeypatch.setattr(audio, "tool", lambda name, override=None: f"probe-{name}")

    def run(argv):
        if argv[0] == f"probe-{audio.TRANSCODER}":
            Path(argv[-1]).write_bytes(b"OggS probe")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        archive = Path(argv[-1])
        subsong = int(argv[argv.index("-s") + 1]) if "-s" in argv else None
        lines = ["sample rate: 44100 Hz", "channels: 2",
                 "stream total samples: 1764000 (0:40.000 seconds)",
                 "encoding: CRI HCA"]
        if archive.stem.startswith("se_"):
            lines.append(f"stream count: {len(cues)}")
            if subsong is not None:
                lines.append(f"stream index: {subsong}")
                lines.append("stream name: %s" % "; ".join(cues[subsong]))
        else:
            lines += ["loop start: 705600 samples (0:16.000 seconds)",
                      "loop end: 1764000 samples (0:40.000 seconds)",
                      f"stream name: {archive.stem}"]
        if "-m" in argv:
            return SimpleNamespace(returncode=0, stdout="\n".join(lines), stderr="")
        Path(argv[argv.index("-o") + 1]).write_bytes(b"RIFF probe")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(audio, "_run", run)

def test_a_texture_array_is_exported_layer_by_layer_not_as_one_picture(tmp_path,
                                                                      monkeypatch):
    """A texture array is N pictures; one file would be one of them, silently.

    The array must also stay out of the single-image map: a consumer that finds it
    under `textures` samples it with two coordinates and gets whatever the driver
    returns for a missing layer.
    """
    mapping = {"mysekai__effect__site__environment__010_snow__global":
               _global_bundle("010_snow"),
               "mysekai__effect__site__environment__010_snow__common":
               _array_common_bundle("010_snow", layers=4),
               "mysekai__effect__site__environment__010_snow__unique__beach":
               _dependent_site_bundle("010_snow", "beach")}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    effects = json.loads((out / "010_snow" / "fx" / "effects.json")
                         .read_text(encoding="utf-8"))
    material = (effects["effects"]["fx_env_site_010_snow_beach"]["particles"][0]
                ["renderer"]["material"])
    assert "_BaseMap2DArray" not in material["textures"]
    binding = material["textureArrays"]["_BaseMap2DArray"]
    assert binding["layers"] == 4 and binding["graphicsFormat"] == 134
    assert len(binding["files"]) == 4
    for index_of_layer, relative in enumerate(binding["files"]):
        assert relative.endswith(f".{index_of_layer}.png")
        assert (out / relative).exists()
    assert result["textureArrays"] == 1 and result["arrayLayers"] == 4
    # The layers are four different pictures, so writing one of them four times
    # would look the same in the counts.
    digests = {Image.open(out / relative).tobytes()
               for relative in binding["files"]}
    assert len(digests) == 4


def test_an_array_binding_records_the_parameters_that_choose_a_layer(tmp_path,
                                                                    monkeypatch):
    """The layer is computed, so the computation's inputs must be in the export."""
    mapping = {"mysekai__effect__site__environment__010_snow__global":
               _global_bundle("010_snow"),
               "mysekai__effect__site__environment__010_snow__common":
               _array_common_bundle("010_snow", mode=1.0, coord=31.0, slices=8.0,
                                    layers=8, progress=0.25),
               "mysekai__effect__site__environment__010_snow__unique__beach":
               _dependent_site_bundle("010_snow", "beach")}
    _, out, _ = _run(tmp_path, monkeypatch, mapping)
    effects = json.loads((out / "010_snow" / "fx" / "effects.json")
                         .read_text(encoding="utf-8"))
    sampling = ((effects["effects"]["fx_env_site_010_snow_beach"]["particles"][0]
                 ["renderer"]["material"]["textureArrays"]["_BaseMap2DArray"])
                ["sampling"])
    assert sampling["arrayMode"] is True and sampling["keywordEnabled"] is True
    assert sampling["keyword"] == "_BASE_MAP_MODE_2D_ARRAY"
    assert sampling["sliceCount"] == 8.0 and sampling["progress"] == 0.25
    # 31 = component 3 (w) * 10 + vector 1 (the first custom value).
    assert sampling["progressSource"] == {"vector": "custom1", "component": "w",
                                         "constant": False}


def test_an_array_bound_while_the_slot_samples_a_picture_says_so(tmp_path,
                                                                monkeypatch):
    """Most bindings in shipped data are inert; calling them live draws garbage."""
    mapping = {"mysekai__effect__site__environment__010_snow__global":
               _global_bundle("010_snow"),
               "mysekai__effect__site__environment__010_snow__common":
               _array_common_bundle("010_snow", mode=0.0, coord=1.0),
               "mysekai__effect__site__environment__010_snow__unique__beach":
               _dependent_site_bundle("010_snow", "beach")}
    _, out, _ = _run(tmp_path, monkeypatch, mapping)
    effects = json.loads((out / "010_snow" / "fx" / "effects.json")
                         .read_text(encoding="utf-8"))
    binding = ((effects["effects"]["fx_env_site_010_snow_beach"]["particles"][0]
                ["renderer"]["material"]["textureArrays"]["_BaseMap2DArray"]))
    assert binding["sampling"]["mode"] == 0.0
    assert binding["sampling"]["arrayMode"] is False
    assert binding["sampling"]["keywordEnabled"] is False
    # The pictures are still exported: the asset is there either way.
    assert len(binding["files"]) == 4


def test_the_layer_a_progress_value_selects_is_a_floor_not_a_rounding():
    """The shader subtracts a half so the graphics API's rounding becomes a floor.

    Rounding instead moves every boundary by an eighth of the range, which puts a
    quarter of the particles on the wrong layer — and looks fine on average.
    """
    params = {"sliceCount": 4.0, "progress": 0.0}
    assert [texarray.layer_of(value, params, 4)
            for value in (0.0, 0.2499, 0.25, 0.4999, 0.5, 0.7499, 0.75, 1.0)] == [
                0, 0, 1, 1, 2, 2, 3, 3]
    # The authored slice count can be smaller than the layers that exist, and the
    # graphics API clamps to the layers, not to the count.
    assert texarray.layer_of(0.99, {"sliceCount": 4.0, "progress": 0.0}, 8) == 3
    assert texarray.layer_of(0.99, {"sliceCount": 8.0, "progress": 0.0}, 8) == 7
    # A selector of 0 picks the zero vector, so only `progress` remains.
    assert texarray.progress_source(0.0) == {"vector": None, "component": None,
                                             "constant": True}


def test_no_lookup_texture_exists_and_the_index_says_so(tmp_path, monkeypatch):
    """Absence has to be stated, or a consumer cannot tell it from an omission."""
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny")}
    _, _, index = _run(tmp_path, monkeypatch, mapping)
    assert index["summary"]["missing"]["lut"] == environments.NO_LUT
    assert "3D texture" in index["semantics"]["lut"]


# -- model assets and emitter meshes ----------------------------------------


def test_a_model_asset_is_exported_as_geometry_not_reported_as_a_gap(tmp_path,
                                                                    monkeypatch):
    """Authored geometry left out of the export becomes an approximation."""
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    mapping = {"mysekai__effect__site__environment__014_sekai__global":
               _global_bundle("014_sekai"),
               "mysekai__effect__site__environment__014_sekai__common":
               _model_common_bundle("014_sekai")}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    assert result["models"] == 1 and result["meshes"] == 1
    entry = index["phenomena"]["014_sekai"]["models"][0]
    assert entry["asset"] == "fbx_env_014_sekai_dome.fbx"
    assert entry["nodes"] == 2 and entry["vertices"] == 3 and entry["triangles"] == 1
    assert entry["meshes"] == [{"node": "dome_01", "mesh": "dome_01",
                               "vertices": 3, "triangles": 1}]
    assert entry["materials"] == [{"node": "dome_01", "material": "Lit"}]
    assert (out / entry["file"]).exists()
    assert index["models"][0]["file"] == entry["file"]
    # Nothing about a model asset is left in the gap list any more.
    assert not [gap for gap in index["summary"]["unsupported"]
                if "model asset" in str(gap.get("reason"))]


def test_geometry_shared_by_two_phenomena_is_written_once(tmp_path, monkeypatch):
    """Meshes repeat across packages, so a per-phenomenon copy would multiply them."""
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    common = _model_common_bundle("014_sekai")
    mapping = {"mysekai__effect__site__environment__014_sekai__global":
               _global_bundle("014_sekai"),
               "mysekai__effect__site__environment__014_sekai__common": common,
               "mysekai__effect__site__environment__999_festivalgarden__global":
               _global_bundle("999_festivalgarden"),
               "mysekai__effect__site__environment__999_festivalgarden__common":
               common}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    assert result["models"] == 2                       # two phenomena reference it
    assert result["meshes"] == 1                       # one file on disk
    files = sorted(path.name for path in (out / "models").glob("*.glb"))
    assert len(files) == 1
    first = index["phenomena"]["014_sekai"]["models"][0]["file"]
    second = index["phenomena"]["999_festivalgarden"]["models"][0]["file"]
    assert first == second
    # The name carries the content digest, so different geometry cannot collide.
    assert first.endswith(index["models"][0]["sha256"][:8] + ".glb")


def test_mesh_mode_calls_resolver_and_adapts_geometry_to_renderer_contract():
    bundle = _mesh_emitter_bundle("009_meteorshower", "beach")
    kinds, trees, _ = _read(bundle)
    calls = []
    geometry = {"file": "models/probe-abcdef12.glb",
                "meshes": [{"node": "pt_009_meteorshower",
                             "mesh": "pt_009_meteorshower"}]}

    def resolve(pointer):
        calls.append(pointer)
        return geometry, None

    effect, unsupported = decode_effect(200, kinds, trees, _no_material,
                                        resolve_mesh=resolve)
    renderer = effect["particles"][0]["renderer"]
    assert calls == [{"m_FileID": 0, "m_PathID": 620}]
    assert unsupported == []
    assert renderer["meshes"] == [{"file": "models/probe-abcdef12.glb",
                                    "node": "pt_009_meteorshower"}]
    assert "mesh" not in effect["particles"][0]


def test_a_mesh_mode_emitter_carries_the_mesh_it_draws(tmp_path, monkeypatch):
    """A mesh-mode emitter without its mesh draws nothing at all."""
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    mapping = {"mysekai__effect__site__environment__009_meteorshower__global":
               _global_bundle("009_meteorshower"),
               "mysekai__effect__site__environment__009_meteorshower__unique__beach":
               _mesh_emitter_bundle("009_meteorshower", "beach")}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    effects = json.loads((out / "009_meteorshower" / "fx" / "effects.json")
                         .read_text(encoding="utf-8"))
    emitter = (effects["effects"]["fx_env_site_009_meteorshower_beach"]
               ["particles"][0])
    assert emitter["renderer"]["renderMode"] == "Mesh"
    assert len(emitter["renderer"]["meshes"]) == 1
    assert emitter["renderer"]["meshes"][0]["node"] == "pt_009_meteorshower"
    assert (out / emitter["renderer"]["meshes"][0]["file"]).exists()
    assert "mesh" not in emitter
    assert effects["summary"]["meshEmitters"] == 1


def test_an_emitter_mesh_no_package_ships_stays_an_unresolved_pointer(tmp_path,
                                                                     monkeypatch):
    """Some emitters point at the engine's built-in primitives, which are not here."""
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    mapping = {"mysekai__effect__site__environment__008_thunder__global":
               _global_bundle("008_thunder"),
               "mysekai__effect__site__environment__008_thunder__unique__beach":
               _mesh_emitter_bundle("008_thunder", "beach", mesh_file_id=1)}
    _, out, index = _run(tmp_path, monkeypatch, mapping)
    effects = json.loads((out / "008_thunder" / "fx" / "effects.json")
                         .read_text(encoding="utf-8"))
    emitter = effects["effects"]["fx_env_site_008_thunder_beach"]["particles"][0]
    assert emitter["renderer"]["renderMode"] == "Mesh"
    assert emitter["renderer"]["meshes"] == []
    assert "mesh" not in emitter
    gap = next(gap for gap in index["summary"]["unsupported"] if "mesh" in gap)
    assert gap["mesh"]["archive"] == "unity default resources"
    assert gap["reason"] == environments.meshes_module.NOT_IN_PACKAGE
    # The new input is inert when it is not given: no geometry appears out of
    # nowhere, and nothing acquires the provenance field either.
    assert not list((out / "models").glob("*.glb"))
    assert all("source" not in entry for entry in index["models"])


def test_a_built_in_primitive_resolves_once_the_engine_container_is_supplied(
        tmp_path, monkeypatch):
    """A mesh-mode emitter with no mesh emits nothing at all, so the whole emitter
    is lost — and with it, on the storm, every bolt of lightning.  The geometry is
    not the game's to ship, but it is the engine's to read, so a caller who has
    the engine can hand its container over and the pointer resolves like any
    other."""
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    result, out, index = _run(
        tmp_path, monkeypatch, _builtin_emitter_mapping(),
        engine={"unity default resources": _engine_container()})
    effects = json.loads((out / "008_thunder" / "fx" / "effects.json")
                         .read_text(encoding="utf-8"))
    emitter = effects["effects"]["fx_env_site_008_thunder_beach"]["particles"][0]
    assert emitter["renderer"]["renderMode"] == "Mesh"
    assert emitter["renderer"]["meshes"] == [{"file": index["models"][0]["file"],
                                              "node": "Plane"}]
    assert (out / emitter["renderer"]["meshes"][0]["file"]).exists()
    assert effects["summary"]["meshEmitters"] == 1
    assert result["meshes"] == 1
    assert not [gap for gap in index["summary"]["unsupported"]
                if gap.get("reason") == environments.meshes_module.NOT_IN_PACKAGE]


def test_geometry_out_of_the_engine_says_it_came_from_the_engine(tmp_path,
                                                                monkeypatch):
    """A built-in primitive is the engine's shape, not this game's asset.

    It is written under the same naming and into the same directory as everything
    else, because a consumer draws it the same way — so without a mark on the
    entry nothing in the products would distinguish an engine shape from an
    authored one.
    """
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    _, out, index = _run(tmp_path, monkeypatch, _builtin_emitter_mapping(),
                         engine={"unity default resources": _engine_container()})
    entry = index["models"][0]
    assert entry["source"] == environments.meshes_module.ENGINE_BUILTIN
    assert entry["name"] == "Plane"
    assert entry["file"] == f"models/Plane-{entry['sha256'][:8]}.glb"
    assert (out / entry["file"]).exists()


def test_a_packages_own_mesh_is_not_marked_as_the_engines(tmp_path, monkeypatch):
    """The mark has to stay narrow, or "no source" stops meaning anything."""
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    mapping = {"mysekai__effect__site__environment__009_meteorshower__global":
               _global_bundle("009_meteorshower"),
               "mysekai__effect__site__environment__009_meteorshower__unique__beach":
               _mesh_emitter_bundle("009_meteorshower", "beach")}
    _, _, index = _run(tmp_path, monkeypatch, mapping,
                       engine={"unity default resources": _engine_container()})
    entry = next(model for model in index["models"]
                 if model["name"] == "pt_009_meteorshower")
    assert "source" not in entry


def test_a_model_assets_engine_mesh_is_marked_inside_the_asset(tmp_path, monkeypatch):
    """A model asset can name a primitive too, and its node tree is one file.

    The geometry is inside that file rather than beside it, so the mark has to sit
    on the mesh row of the asset, or provenance is lost the moment the shape stops
    being an emitter's.
    """
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    mapping = {"mysekai__effect__site__environment__014_sekai__global":
               _global_bundle("014_sekai"),
               "mysekai__effect__site__environment__014_sekai__common":
               _builtin_model_bundle("014_sekai")}
    _, out, index = _run(tmp_path, monkeypatch, mapping,
                         engine={"unity default resources": _engine_container()})
    entry = index["phenomena"]["014_sekai"]["models"][0]
    assert entry["meshes"] == [{"node": "dome_01", "mesh": "Plane", "vertices": 4,
                                "triangles": 2,
                                "source": environments.meshes_module.ENGINE_BUILTIN}]
    assert (out / entry["file"]).exists()
    assert not [gap for gap in index["summary"]["unsupported"]
                if gap.get("reason") == environments.meshes_module.NOT_IN_PACKAGE]


def test_a_mesh_shaped_emitter_is_born_on_the_engines_surface_too(tmp_path,
                                                                  monkeypatch):
    """The other pointer at geometry is the shape a particle is born on.

    It is resolved through the same route, so leaving it out would give a
    phenomenon its particles back with nowhere to put them.
    """
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    mapping = {"mysekai__effect__site__environment__008_thunder__global":
               _global_bundle("008_thunder"),
               "mysekai__effect__site__environment__008_thunder__unique__beach":
               _builtin_shape_bundle("008_thunder", "beach")}
    _, out, index = _run(tmp_path, monkeypatch, mapping,
                         engine={"unity default resources": _engine_container()})
    effects = json.loads((out / "008_thunder" / "fx" / "effects.json")
                         .read_text(encoding="utf-8"))
    shape = (effects["effects"]["fx_env_site_008_thunder_beach"]["particles"][0]
             ["system"]["shape"])
    assert shape["type"] == "Mesh"
    assert shape["meshes"] == [{"file": index["models"][0]["file"], "node": "Plane"}]
    assert (out / shape["meshes"][0]["file"]).exists()
    assert index["models"][0]["source"] == environments.meshes_module.ENGINE_BUILTIN
    assert not [gap for gap in index["summary"]["unsupported"]
                if gap.get("reason") == environments.meshes_module.NOT_IN_PACKAGE]


def test_the_engine_container_is_the_only_thing_the_option_changes(tmp_path,
                                                                  monkeypatch):
    """A caller who does not pass it must get byte-for-byte what they got before.

    Compared file by file rather than by counts: a new input that quietly renamed
    a file, reordered a list or dropped a field would pass any count check and
    still break every consumer that was already reading the output.
    """
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    _, plain, _ = _run(tmp_path / "plain", monkeypatch, _builtin_emitter_mapping())
    _, supplied, _ = _run(tmp_path / "supplied", monkeypatch,
                          _builtin_emitter_mapping(),
                          engine={"unity default resources": _engine_container()})

    def files(root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    before, after = files(plain), files(supplied)
    added = sorted(set(after) - set(before))
    assert added and all(name.startswith("models/Plane-") for name in added)
    assert not set(before) - set(after)
    differing = sorted(name for name in before if before[name] != after[name])
    # Only the two documents that name the resolved geometry move.
    assert differing == ["008_thunder/fx/effects.json", "index.json"]


def test_a_directory_of_engine_containers_contributes_only_those_containers(tmp_path):
    """Where the engine's files live is the caller's to say, and only theirs.

    A caller naming the directory must not be able to sweep whatever else is in
    it into the pointer store, and naming a file must take that file as it is.
    """
    from core.assets.packages import BUILTIN_ARCHIVES, builtin_archive_paths
    directory = tmp_path / "engine-resources"
    directory.mkdir()
    for name in BUILTIN_ARCHIVES:
        (directory / name).write_bytes(b"")
    (directory / "something else").write_bytes(b"")
    assert builtin_archive_paths([str(directory)]) == [
        str(directory / name) for name in BUILTIN_ARCHIVES]
    one = directory / BUILTIN_ARCHIVES[0]
    assert builtin_archive_paths([str(one)]) == [str(one)]
    assert builtin_archive_paths(None) == []


def test_the_command_line_asks_for_the_container_and_never_assumes_one(tmp_path,
                                                                      monkeypatch):
    """The path can only come from the caller: nothing here has a default for it."""
    from core import cli
    seen = {}

    def fake(bundles, out_dir, **kwargs):
        seen.clear()
        seen.update(kwargs)
        return {"perBundle": {}, "meshes": 0}

    monkeypatch.setattr(environments, "extract_phenomena", fake)
    assert cli.main(["phenomena", "--bundle", "package", "--out-dir",
                     str(tmp_path)]) == 0
    assert seen["extra_archives"] == []
    container = tmp_path / "unity default resources"
    container.write_bytes(b"")
    assert cli.main(["phenomena", "--bundle", "package", "--out-dir", str(tmp_path),
                     "--builtin-resources", str(container)]) == 0
    assert seen["extra_archives"] == [str(container)]


# -- components read and deliberately not exported --------------------------


def test_a_canvas_renderer_with_no_graphic_is_omitted_not_unsupported(tmp_path,
                                                                     monkeypatch):
    """It draws only what a graphic submits, and these prefabs carry no graphic.

    Reported as unsupported it is noise that hides the real gaps; dropped silently
    it looks like it was never read.
    """
    mapping = {"mysekai__effect__site__environment__006_rain__global":
               _global_bundle("006_rain"),
               "mysekai__effect__site__environment__006_rain__unique__beach":
               _inert_component_bundle("006_rain", "beach")}
    _, out, index = _run(tmp_path, monkeypatch, mapping)
    omitted = index["summary"]["omitted"]
    canvas = next(entry for entry in omitted if entry["component"] == "CanvasRenderer")
    assert canvas["node"] == "puff" and "no graphic" in canvas["reason"]
    assert not [gap for gap in index["summary"]["unsupported"]
                if gap.get("component") in ("CanvasRenderer", "MeshFilter",
                                            "MeshCollider")]


def test_a_collider_mesh_with_no_renderer_is_omitted_with_what_it_points_at(
        tmp_path, monkeypatch):
    """Invisible collision surface, in another package: recorded, not exported."""
    mapping = {"mysekai__effect__site__environment__006_rain__global":
               _global_bundle("006_rain"),
               "mysekai__effect__site__environment__006_rain__unique__beach":
               _inert_component_bundle("006_rain", "beach")}
    result, out, index = _run(tmp_path, monkeypatch, mapping)
    omitted = {entry["component"]: entry for entry in index["summary"]["omitted"]}
    assert set(omitted) == {"CanvasRenderer", "MeshFilter", "MeshCollider"}
    assert omitted["MeshCollider"]["node"] == "nav_collider"
    assert omitted["MeshCollider"]["mesh"] == {"fileId": 3, "pathId": 777}
    assert "no mesh renderer" in omitted["MeshCollider"]["reason"]
    assert result["omitted"] == 3


def test_a_mesh_filter_next_to_a_renderer_is_not_omitted(tmp_path, monkeypatch):
    """The omission is narrow: visible geometry must not fall through it.

    A mesh filter is omitted because the node it sits on has no mesh renderer.  Put
    one there and the node draws, so the same component must stop being an omission
    and start being a gap — otherwise the widened rule would quietly swallow
    geometry a consumer can see.
    """
    bundle = _site_bundle("017_rainbow", "beach")
    archive = bundle.objects[0].assets_file
    bundle.objects.insert(-1, _Object(
        "GameObject", 230, {"m_Name": "ground_plate", "m_IsActive": 1,
                            "m_Component": [{"component": {"m_PathID": 231}},
                                            {"component": {"m_PathID": 232}},
                                            {"component": {"m_PathID": 233}}]},
        archive))
    bundle.objects.insert(-1, _Object("Transform", 231,
                                      {"m_GameObject": {"m_PathID": 230},
                                       "m_Father": {"m_PathID": 201},
                                       "m_LocalPosition": {}, "m_LocalRotation": {},
                                       "m_LocalScale": {}}, archive))
    bundle.objects.insert(-1, _Object("MeshFilter", 232,
                                      {"m_GameObject": {"m_PathID": 230},
                                       "m_Mesh": {"m_FileID": 0,
                                                  "m_PathID": 640}}, archive))
    bundle.objects.insert(-1, _Object("MeshRenderer", 233,
                                      {"m_GameObject": {"m_PathID": 230},
                                       "m_Materials": []}, archive))
    mapping = {"mysekai__effect__site__environment__017_rainbow__global":
               _global_bundle("017_rainbow"),
               "mysekai__effect__site__environment__017_rainbow__unique__beach":
               bundle}
    result, _, index = _run(tmp_path, monkeypatch, mapping)
    assert index["summary"]["omitted"] == [] and result["omitted"] == 0
    reported = {gap.get("component") for gap in index["summary"]["unsupported"]}
    assert {"MeshFilter", "MeshRenderer"} <= reported


# -- the timeline that drives one phenomenon --------------------------------


def test_the_timeline_says_which_value_each_track_drives(tmp_path, monkeypatch):
    """Track order and clip times are replayable only if the target is named."""
    mapping = {"mysekai__effect__site__environment__008_thunder__global":
               _timeline_global_bundle("008_thunder")}
    _, out, index = _run(tmp_path, monkeypatch, mapping)
    entry = index["phenomena"]["008_thunder"]["timeline"]
    assert entry == {"file": "008_thunder/timeline.json", "duration": 23.333333,
                     "tracks": 2, "clips": 2}
    document = json.loads((out / "008_thunder" / "timeline.json")
                          .read_text(encoding="utf-8"))
    assert document["name"] == "env_008_thunder" and document["frameRate"] == 60.0
    colour, value = document["tracks"]
    assert colour["target"] == "skyAdditiveColor" and colour["class"] == \
        "SiteEnvironmentColorTrack"
    assert value["target"] == "lightAdditiveIntensity" and value["scale"] == 2.56
    clip = colour["clips"][0]
    assert clip["start"] == 0.9 and clip["duration"] == 1.55
    assert clip["label"] == "SkyAdditiveColorClip"
    assert clip["asset"]["gradient"]["colorKeys"][0]["color"] == [1.0, 0.9, 0.8]
    # Gradient key times are stored as sixteenths of a thousandth, and the
    # shared encoding divides by 65535 rather than rounding to a nicer number.
    assert clip["asset"]["gradient"]["alphaKeys"][1]["time"] == 0.5000076295109483
    assert value["clips"][0]["clipIn"] == 0.25
    # An infinite slope is a stepped key, not a number a JSON reader can take.
    assert value["clips"][0]["asset"]["curve"]["keys"][1]["inSlope"] is None
    assert not [gap for gap in index["summary"]["unsupported"]
                if "TimelineAsset" in str(gap.get("script"))]


def test_a_timeline_clip_class_that_is_not_modelled_stays_visible(tmp_path,
                                                                 monkeypatch):
    bundle = _timeline_global_bundle("008_thunder")
    for obj in bundle.objects:
        if obj.path_id == 913:
            obj._tree["m_ClassName"] = "SiteEnvironmentFogClip"
    mapping = {"mysekai__effect__site__environment__008_thunder__global": bundle}
    _, out, index = _run(tmp_path, monkeypatch, mapping)
    assert {"variant": "global", "asset": "env_008_thunder.playable",
            "track": "Sky Flash Color", "clipClass": "SiteEnvironmentFogClip",
            "reason": "timeline clip class not modelled",
            "phenomenon": "008_thunder"} in index["summary"]["unsupported"]
    document = json.loads((out / "008_thunder" / "timeline.json")
                          .read_text(encoding="utf-8"))
    assert document["tracks"][0]["clips"][0]["asset"] is None
    # The clip's placement on the timeline is still known.
    assert document["tracks"][0]["clips"][0]["start"] == 0.9


# -- audio, which needs a decoder this repository does not ship -------------


def test_audio_without_the_decoder_is_skipped_and_keeps_the_archive(tmp_path,
                                                                   monkeypatch):
    """The archive needs nothing external; only decoding it does.

    A run that failed here, or that quietly wrote no audio field, would both be
    wrong in the same way: the caller could not tell what to install.
    """
    monkeypatch.setattr(audio, "tool", lambda name, override=None: None)
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny"),
               "mysekai__sound__bgm__bgm_probe": _sound_bundle("bgm_probe"),
               "mysekai__sound__se__se_mysekai": _sound_bundle("se_mysekai")}
    result, out, index = _run(tmp_path, monkeypatch, mapping,
                              master=str(_master_dir(tmp_path)))
    assert index["audio"]["status"] == "skipped"
    assert "vgmstream-cli" in index["audio"]["error"]
    assert result["audioStreams"] == 0
    archives = sorted(path.name for path in (out / "audio").rglob("*.acb"))
    assert archives == ["bgm_probe.acb", "se_mysekai.acb"]
    assert (out / "audio" / "bgm_probe" / "bgm_probe.acb").read_bytes() == b"@UTF probe"
    entry = next(row for row in index["audio"]["packages"]
                 if row["package"] == "mysekai__sound__bgm__bgm_probe")
    assert entry["status"] == "skipped" and entry["streams"] == []


def test_audio_with_a_decoder_writes_loop_points_in_seconds(tmp_path, monkeypatch):
    """Loop points are metadata, in samples; a web consumer loops on seconds."""
    _fake_decoder(monkeypatch, tmp_path)
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny"),
               "mysekai__sound__bgm__bgm_probe": _sound_bundle("bgm_probe"),
               "mysekai__sound__se__se_mysekai": _sound_bundle("se_mysekai")}
    result, out, index = _run(tmp_path, monkeypatch, mapping,
                              master=str(_master_dir(tmp_path)))
    assert index["audio"]["status"] == "succeeded"
    music = next(row for row in index["audio"]["packages"]
                 if row["package"] == "mysekai__sound__bgm__bgm_probe")
    stream = music["streams"][0]
    assert stream["cue"] == "bgm_probe" and stream["sampleRate"] == 44100
    assert stream["loop"] is True
    assert stream["loopStartSeconds"] == 16.0 and stream["loopEndSeconds"] == 40.0
    assert stream["durationSeconds"] == 40.0 and stream["channels"] == 2
    assert (out / stream["wav"]).exists()
    # Every cue a row named is asked for, and one cue can have several waveforms.
    ambience = next(row for row in index["audio"]["packages"]
                    if row["package"] == environments.AMBIENCE_PACKAGE)
    assert [stream["cue"] for stream in ambience["streams"]] == [
        "se_probe_specific", "se_probe_wind", "se_probe_wind"]
    assert [stream["subsong"] for stream in ambience["streams"]] == [1, 2, 3]
    assert result["audioStreams"] == 4
    loops = json.loads((out / "audio" / "loop.json").read_text(encoding="utf-8"))
    assert loops["status"] == "succeeded"
    entry = index["phenomena"]["001_sunny"]["audio"]
    assert sorted({stream["cue"] for stream in entry}) == ["bgm_probe",
                                                           "se_probe_specific"]


def test_a_phenomenon_with_no_audio_row_is_an_empty_list_not_a_null(tmp_path,
                                                                   monkeypatch):
    """Empty and unknown are different answers and must not share a value.

    A phenomenon with no music row keeps the site's own music, which is a fact
    about the rows; a run with no master tables knows nothing about rows at all.
    Reporting both as `null` makes a consumer unable to tell the two apart.
    """
    _fake_decoder(monkeypatch, tmp_path)
    master = _master_dir(tmp_path)
    # A second phenomenon that the tables do know about, but with no music row of
    # its own -- which is what "keeps the site's own music" looks like in the rows.
    table = master / "mysekaiPhenomenas.json"
    rows = json.loads(table.read_text(encoding="utf-8"))
    rows.append({"id": 8, "assetbundleName": "008_thunder", "name": "kaminari",
                 "englishName": "THUNDER", "description": "thunder",
                 "iconAssetbundleName": "env_thunder",
                 "rampTextureAssetbundleName": "008_thunder",
                 "mysekaiPhenomenaTimePeriodType": "daytime",
                 "mysekaiPhenomenaBrightnessType": "dark",
                 "mysekaiPhenomenaBackgroundColorId": 8})
    table.write_text(json.dumps(rows), encoding="utf-8")
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny"),
               "mysekai__effect__site__environment__008_thunder__global":
               _global_bundle("008_thunder"),
               "mysekai__sound__bgm__bgm_probe": _sound_bundle("bgm_probe"),
               "mysekai__sound__se__se_mysekai": _sound_bundle("se_mysekai")}
    _, _, index = _run(tmp_path, monkeypatch, mapping, master=str(master))
    assert index["phenomena"]["008_thunder"]["bgms"] == []
    assert index["phenomena"]["008_thunder"]["audio"] == []
    assert index["phenomena"]["001_sunny"]["audio"] != []
    # Without master tables the rows are unknown, and so is the audio.
    _, _, plain = _run(tmp_path / "plain", monkeypatch, mapping)
    assert plain["phenomena"]["008_thunder"]["bgms"] is None
    assert plain["phenomena"]["008_thunder"]["audio"] is None


def test_a_cue_no_waveform_carries_is_reported_as_that_cue(tmp_path, monkeypatch):
    """A cue a row names but the archive does not hold is a gap in the data."""
    _fake_decoder(monkeypatch, tmp_path, cues={1: ["se_probe_specific"]})
    mapping = {"mysekai__effect__site__environment__001_sunny__global":
               _global_bundle("001_sunny"),
               "mysekai__sound__bgm__bgm_probe": _sound_bundle("bgm_probe"),
               "mysekai__sound__se__se_mysekai": _sound_bundle("se_mysekai")}
    _, _, index = _run(tmp_path, monkeypatch, mapping,
                       master=str(_master_dir(tmp_path)))
    assert {"package": environments.AMBIENCE_PACKAGE, "cue": "se_probe_wind",
            "reason": audio.NO_CUE} in index["summary"]["unsupported"]


def test_loop_points_absent_from_the_metadata_are_not_invented():
    document = audio.loop_document({"sampleRate": 44100, "channels": 1,
                                    "samples": 22050})
    assert document == {"loop": False, "loopStartSeconds": None,
                        "loopEndSeconds": None, "loopStartSamples": None,
                        "loopEndSamples": None, "sampleRate": 44100, "channels": 1,
                        "samples": 22050, "durationSeconds": 0.5, "encoding": None}

# -- routing ----------------------------------------------------------------


def test_router_and_discovery_select_phenomena_bundles(tmp_path):
    from core.assets.router import route
    from core.extract import discover_bundles

    assert route("mysekai__effect__site__environment__001_sunny__global").domain == "phenomena"
    assert route("mysekai__effect__site__environment__001_sunny__common").domain == "phenomena"
    assert route(
        "mysekai__effect__site__environment__001_sunny__unique__first_floor").domain == "phenomena"
    assert route("mysekai__thumbnail__phenomena").domain == "phenomena"
    # Neighbouring names that are not phenomena packages.  The site path is its
    # own domain and must not be answered as this one: both spell "environment".
    assert route("mysekai__site__environment__common").domain == "site"
    assert route("mysekai__system__home_site_environment") is None

    store = tmp_path / "decrypted"
    store.mkdir()
    for name in ("mysekai__effect__site__environment__001_sunny__global",
                 "mysekai__effect__site__environment__001_sunny__unique__home",
                 "mysekai__thumbnail__phenomena", "mysekai__site__environment__common",
                 "mysekai__system__home_site_environment", "notes.txt"):
        (store / name).write_bytes(b"x")
    names, ignored = discover_bundles(store)
    assert {name for name in names
            if route(name).domain == "phenomena"} == {
        "mysekai__effect__site__environment__001_sunny__global",
        "mysekai__effect__site__environment__001_sunny__unique__home",
        "mysekai__thumbnail__phenomena"}
    assert ignored == 2


def test_manifest_extraction_writes_the_shared_index_once(tmp_path, monkeypatch):
    """The packages of one phenomenon are one job, not one job per package."""
    from core.extract import extract_manifest

    mapping = {
        "mysekai__effect__site__environment__001_sunny__global":
            _global_bundle("001_sunny"),
        "mysekai__effect__site__environment__001_sunny__unique__first_floor":
            _first_floor_bundle("001_sunny"),
        "mysekai__thumbnail__phenomena": _thumbnail_bundle(["env_sunny"]),
    }
    store = tmp_path / "decrypted"
    store.mkdir()
    for name in mapping:
        (store / name).write_bytes(b"x")
    _store(monkeypatch, mapping)
    report = extract_manifest(None, str(store), str(tmp_path / "out"))
    assert report["summary"]["succeeded"] == 3 and report["summary"]["failed"] == 0
    derived = [entry for entry in report["derived"]
               if entry["artifact"] == "phenomena/index.json"]
    assert len(derived) == 1 and derived[0]["status"] == "succeeded"
    assert derived[0]["counts"]["phenomena"] == 1
    assert derived[0]["counts"]["overrides"] == 1
    assert (tmp_path / "out" / "phenomena" / "index.json").exists()
    entry = next(b for b in report["bundles"] if b["bundle"].endswith("__global"))
    assert entry["counts"]["configs"] == 1 and entry["counts"]["ramps"] == 1
    override = next(b for b in report["bundles"] if b["bundle"].endswith("first_floor"))
    assert override["counts"] == {"configs": 1, "profiles": 1, "ramps": 0,
                                 "effects": 0, "emitters": 0, "models": 0,
                                 "omitted": 0, "unsupported": 0}
def test_pull_selects_every_domain_the_extractor_supports():
    """The download root list and the router must not be able to drift apart.

    `pull` downloads only what it names as a root, so a domain the extractor
    supports but the root list omits would be silently skipped: the run would
    report success while producing nothing for that domain.

    Sound is the one domain with an exception, and it is stated here so this test
    and the sound-root tests cannot contradict each other: a sound package is a
    root only when a master row names it, because the name shape cannot tell
    which packages hold a world's audio.
    """
    from core.fetch import Manifest

    names = ["mysekai/character/mdl_sd_112_001", "mysekai/character_motion",
             "mysekai/character_settings", "mysekai/character_alone_action",
             "mysekai/talk/scenario/talk", "mysekai/effect/emoticon/fx_emote_001",
             "mysekai/shader",
             "mysekai/effect/site/environment/001_sunny/global",
             "mysekai/effect/site/environment/001_sunny/common",
             "mysekai/effect/site/environment/001_sunny/unique/first_floor",
             "mysekai/thumbnail/phenomena",
             # A domain of its own, but only master rows name which of these hold
             # a world's audio, so it is not a root by name shape.
             "mysekai/sound/bgm/bgm_probe",
             # The site domain: every package under that path is one of its own.
             "mysekai/site/field/grasslands/grasslands",
             "mysekai/site/environment/common"]
    manifest = Manifest({name: {"bundleName": name, "downloadPath": "b",
                                "cacheFileName": name, "dependencies": []}
                         for name in names})
    roots = manifest.roots()
    assert "mysekai/effect/site/environment/001_sunny/global" in roots
    assert "mysekai/effect/site/environment/001_sunny/unique/first_floor" in roots
    assert "mysekai/thumbnail/phenomena" in roots
    assert "mysekai/site/field/grasslands/grasslands" in roots
    assert "mysekai/site/environment/common" in roots
    assert set(roots) == set(names) - {"mysekai/sound/bgm/bgm_probe"}
    # Named by a row, it is a root; the router still gives it a domain either way.
    assert manifest.roots(audio=["mysekai__sound__bgm__bgm_probe"]) == \
        sorted([*roots, "mysekai/sound/bgm/bgm_probe"])


def test_audio_document_carries_no_local_tool_paths(tmp_path):
    """The loop sidecar ships with the audio, so it must not name a machine's paths.

    Red condition: recording the resolved decoder/transcoder location (as an earlier
    version did) leaks one machine's directory layout into a published artifact.
    """
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..", "src"))
    from phenomena.audio import Library
    audio = Library(tmp_path, "prefix")
    audio.decoder = str(tmp_path / "decoder.exe")
    audio.transcoder = str(tmp_path / "transcoder.exe")
    document = audio.finish()
    flat = json.dumps(document, ensure_ascii=False)
    assert str(tmp_path) not in flat
    assert "decoderPath" not in document
    assert document["decoderPresent"] is True and document["transcoderPresent"] is True


# -- the sound packages, which master rows name and no package depends on ----
#
# Everything else a phenomenon needs is reachable from its own packages: the
# materials and images are declared dependencies, so a download that follows the
# dependency graph gets them.  The sound packages are outside that graph — master
# rows name them — so they need a root of their own, and the router needs a
# domain for them, or the whole pipeline finishes with every picture and no
# sound while reporting success.


def _site_music_master(tmp_path):
    """The master fixture plus the site-music layer under a phenomenon's own row."""
    master = _master_dir(tmp_path)
    (master / "mysekaiSiteBgms.json").write_text(json.dumps([
        {"id": 1, "mysekaiSiteId": 1, "mysekaiPhenomenaBrightnessType": "normal",
         "assetbundleName": "bgm_site_probe", "cue": "bgm_site_probe_1"},
        {"id": 2, "mysekaiSiteId": 1, "mysekaiPhenomenaBrightnessType": "dark",
         "assetbundleName": "bgm_site_probe", "cue": "bgm_site_probe_2"}]),
        encoding="utf-8")
    return master


def _audio_mapping():
    """A phenomenon, the two music packages its rows name, and the shared ambience."""
    return {"mysekai__effect__site__environment__001_sunny__global":
            _global_bundle("001_sunny"),
            "mysekai__sound__bgm__bgm_probe": _sound_bundle("bgm_probe"),
            "mysekai__sound__bgm__bgm_site_probe": _sound_bundle("bgm_site_probe"),
            "mysekai__sound__se__se_mysekai": _sound_bundle("se_mysekai"),
            # In the manifest and on disk, but named by no row.
            "mysekai__sound__bgm__music0001": _sound_bundle("music0001")}


def test_the_router_gives_the_sound_packages_a_domain_of_their_own():
    from core.assets.router import route

    assert route("mysekai__sound__bgm__bgm_probe").domain == "sound"
    assert route("mysekai__sound__se__se_mysekai").domain == "sound"
    # Neighbouring names that are not sound packages.
    assert route("mysekai__sound_setting") is None
    assert route("mysekai__effect__site__environment__001_sunny__global").domain \
        == "phenomena"


def test_the_site_music_layer_is_extracted_and_named_in_the_index(tmp_path,
                                                                 monkeypatch):
    """Music is two layers and a phenomenon's own row is only the upper one.

    Most phenomena have no music row at all and keep the site's music, so an
    extraction that reads only the phenomenon rows produces the exception and
    leaves out what actually plays most of the time.
    """
    _fake_decoder(monkeypatch, tmp_path)
    mapping = {name: bundle for name, bundle in _audio_mapping().items()
               if not name.endswith("music0001")}
    _, out, index = _run(tmp_path, monkeypatch, mapping,
                         master=str(_site_music_master(tmp_path)))
    assert [row["package"] for row in index["siteBgms"]] == \
        ["mysekai__sound__bgm__bgm_site_probe"] * 2
    assert [row["cue"] for row in index["siteBgms"]] == ["bgm_site_probe_1",
                                                         "bgm_site_probe_2"]
    assert index["siteBgms"][0]["brightnessType"] == "normal"
    assert index["siteBgms"][0]["siteId"] == 1
    packages = [row["package"] for row in index["audio"]["packages"]]
    assert "mysekai__sound__bgm__bgm_site_probe" in packages
    assert (out / "audio" / "bgm_site_probe" / "bgm_site_probe.wav").exists()
    # The base layer belongs to a site, not to a phenomenon, so it is not folded
    # into a phenomenon's own audio.
    assert [stream["package"] for stream in index["phenomena"]["001_sunny"]["audio"]
            if "bgm_site_probe" in stream["package"]] == []


def test_without_the_site_music_table_the_layer_is_unknown_not_empty(tmp_path,
                                                                    monkeypatch):
    _fake_decoder(monkeypatch, tmp_path)
    mapping = {name: bundle for name, bundle in _audio_mapping().items()
               if not name.endswith("music0001")}
    _, _, index = _run(tmp_path, monkeypatch, mapping,
                       master=str(_master_dir(tmp_path)))
    assert index["siteBgms"] is None
    assert "mysekaiSiteBgms" in index["summary"]["missing"]["masterTables"]


class _FakeResponse:
    """A download served from memory; nothing leaves this process."""

    status = 200

    def __init__(self, payload):
        self.payload = payload
        self.offset = 0

    def read(self, size=-1):
        end = len(self.payload) if size is None or size < 0 else self.offset + size
        chunk = self.payload[self.offset:end]
        self.offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _pull(tmp_path, monkeypatch, mapping, master=None):
    """Run the whole `pull` path with the network and the packages faked."""
    import urllib.request

    from core import fetch

    _store(monkeypatch, mapping)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout=None: _FakeResponse(b"UnityFS probe"))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(
        {name.replace("__", "/"): {"bundleName": name.replace("__", "/"),
                                   "downloadPath": "b", "dependencies": []}
         for name in mapping}), encoding="utf-8")
    report = fetch.pull(str(manifest), tmp_path / "work",
                        "https://example.invalid/base", workers=1, master=master,
                        extract_out=tmp_path / "local-data")
    out = tmp_path / "local-data" / "phenomena"
    return report, out, json.loads((out / "index.json").read_text(encoding="utf-8"))


def test_a_pull_fetches_the_sound_packages_and_lands_the_decoded_audio(tmp_path,
                                                                      monkeypatch):
    """The end the caller sees: one command, and the audio is on disk."""
    _fake_decoder(monkeypatch, tmp_path)
    report, out, index = _pull(tmp_path, monkeypatch, _audio_mapping(),
                               master=str(_site_music_master(tmp_path)))
    assert report["audio"]["roots"] == ["mysekai/sound/bgm/bgm_probe",
                                        "mysekai/sound/bgm/bgm_site_probe",
                                        "mysekai/sound/se/se_mysekai"]
    assert report["extraction"]["summary"]["failed"] == 0
    assert index["audio"]["status"] == "succeeded"
    assert (out / "audio" / "loop.json").exists()
    assert sorted(path.name for path in (out / "audio").rglob("*.wav")) == [
        "bgm_probe.wav", "bgm_site_probe.wav", "se_probe_specific.wav",
        "se_probe_wind.2.wav", "se_probe_wind.3.wav"]
    assert sorted(path.name for path in (out / "audio").rglob("*.acb")) == [
        "bgm_probe.acb", "bgm_site_probe.acb", "se_mysekai.acb"]
    # A sound package no row names is never downloaded, so it is not extracted.
    downloaded = {row["bundleName"] for row in json.loads(
        (tmp_path / "work" / "downloads.json").read_text(encoding="utf-8"))}
    assert "mysekai/sound/bgm/music0001" not in downloaded


def test_a_pull_without_the_decoder_still_lands_the_archives_and_does_not_fail(
        tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "tool", lambda name, override=None: None)
    report, out, index = _pull(tmp_path, monkeypatch, _audio_mapping(),
                               master=str(_site_music_master(tmp_path)))
    assert index["audio"]["status"] == "skipped"
    assert report["extraction"]["summary"]["failed"] == 0
    assert sorted(path.name for path in (out / "audio").rglob("*.acb")) == [
        "bgm_probe.acb", "bgm_site_probe.acb", "se_mysekai.acb"]
    assert list((out / "audio").rglob("*.wav")) == []


def test_the_extraction_report_keeps_its_shape_with_sound_packages_in_the_set(
        tmp_path, monkeypatch):
    """The report is a contract: a new domain may add entries, not change fields."""
    from core.extract import extract_manifest

    _fake_decoder(monkeypatch, tmp_path)
    mapping = _audio_mapping()
    store = tmp_path / "decrypted"
    store.mkdir()
    for name in mapping:
        (store / name).write_bytes(b"x")
    _store(monkeypatch, mapping)
    report = extract_manifest(None, str(store), str(tmp_path / "out"),
                              master=str(_site_music_master(tmp_path)))
    assert set(report["summary"]) == {"requested", "succeeded", "failed",
                                      "unsupported"}
    assert report["summary"]["requested"] == len(mapping) == (
        report["summary"]["succeeded"] + report["summary"]["failed"]
        + report["summary"]["unsupported"])
    assert report["summary"]["failed"] == 0
    assert [entry["artifact"] for entry in report["derived"]] == [
        "characters.json", "phenomena/index.json", "manifest.json"]
    assert all(set(entry) >= {"bundle", "status", "artifacts", "counts", "error"}
               for entry in report["bundles"])
    phenomenon = next(entry for entry in report["bundles"]
                      if entry["bundle"].endswith("__global"))
    assert phenomenon["counts"]["configs"] == 1 and phenomenon["counts"]["ramps"] == 1
    sound = next(entry for entry in report["bundles"]
                 if entry["bundle"].endswith("__bgm_probe"))
    assert sound["status"] == "succeeded" and sound["counts"]["streams"] == 1
    unasked = next(entry for entry in report["bundles"]
                   if entry["bundle"].endswith("music0001"))
    assert unasked["status"] == "unsupported" and "row" in unasked["error"]
