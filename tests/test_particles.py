"""Particle module decoding.

Unity's serialized field names and the names its scripting API exposes are not
the same, and a lookup under the scripting name succeeds silently with nothing
in it.  Every such pair below is pinned by a fixture that carries a **decoy**
under the scripting name: an implementation reading the wrong key does not fall
back to a default, it reads the decoy, and the assertion says so.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.jsonio import dumps, normalize  # noqa: E402
from core.particles import (MODELLED_MODULES, decode_renderer,  # noqa: E402
                            decode_system)


def test_curve_export_preserves_signed_step_tangents():
    import json
    from core.particles import min_max_curve
    source = {"minMaxState": 1, "scalar": 1.0, "maxCurve": {"m_Curve": [
        {"time": 0.0, "value": 2.0, "inSlope": float("inf"),
         "outSlope": float("-inf"), "weightedMode": 0},
    ]}}
    encoded = json.loads(dumps(min_max_curve(source)))
    key = encoded["keys"][0]
    assert key["inSlope"] == "Infinity"
    assert key["outSlope"] == "-Infinity"


def _curve(value):
    return {"minMaxState": 0, "scalar": value, "minScalar": 0.0}


def test_seed_ownership_preserves_missing_null_and_authored_values():
    for source in ({}, {"randomSeed": None, "autoRandomSeed": None}):
        system, _ = decode_system(source)
        assert system["randomSeed"] is None
        assert system["autoRandomSeed"] is None
    for seed, unsigned in [(0, 0), (1, 1), (-1, 4294967295),
                           (-2147483648, 2147483648), (2147483647, 2147483647),
                           (4294967295, 4294967295), (-1029634089, 3265333207)]:
        for automatic in (False, True):
            system, _ = decode_system({"randomSeed": seed, "autoRandomSeed": automatic})
            assert system["randomSeed"] == unsigned
            assert system["autoRandomSeed"] is automatic


@pytest.mark.parametrize("seed", [-2147483649, 4294967296, 0.5, 0.0,
                                  True, False, "0", [], {}, float("inf")])
def test_invalid_seed_values_are_not_wrapped_or_coerced(seed):
    with pytest.raises(ValueError, match=r"ParticleSystem\.randomSeed"):
        decode_system({"randomSeed": seed})


@pytest.mark.parametrize("automatic", [0, 1, 0.0, "false", [], {}])
def test_invalid_auto_seed_values_are_not_truthiness_defaults(automatic):
    with pytest.raises(ValueError, match=r"ParticleSystem\.autoRandomSeed"):
        decode_system({"autoRandomSeed": automatic})


def test_inherited_velocity_reads_serialized_controls_and_keeps_unknowns():
    source = {"InheritVelocityModule": {"enabled": True, "m_Mode": 1,
              "m_Curve": _curve(0.75), "mode": 0, "curve": _curve(9.0)}}
    decoded, gaps = decode_system(source)
    assert decoded["inheritVelocity"] == {
        "mode": "current", "curve": {"mode": "constant", "value": 0.75}}
    assert "InheritVelocityModule" in decoded["sourceModules"]["enabled"]
    assert not any(g.get("module") == "InheritVelocityModule" for g in gaps)
    unknown, _ = decode_system({"InheritVelocityModule": {"enabled": True}})
    assert unknown["inheritVelocity"] == {"mode": None, "curve": None}
    disabled, _ = decode_system({"InheritVelocityModule": {"enabled": False}})
    assert "inheritVelocity" not in disabled


def _keyed(*values):
    """A one-curve value whose keys are distinguishable from any constant."""
    return {"minMaxState": 1, "scalar": 2.0, "minScalar": 0.0,
            "maxCurve": {"m_Curve": [{"time": t, "value": v, "inSlope": 0.0,
                                      "outSlope": 0.0}
                                     for t, v in values]}}


def _system(**modules):
    """A system with only the modules under test switched on."""
    tree = {
        "lengthInSec": 5.0, "looping": 1, "prewarm": 0, "playOnAwake": 1,
        "simulationSpeed": 1.0, "moveWithTransform": 1, "randomSeed": 7,
        "InitialModule": {
            "enabled": 1, "maxNumParticles": 500,
            "startLifetime": _curve(1.0), "startSpeed": _curve(1.0),
            "startSize": _curve(1.0), "startRotation": _curve(0.0),
            "startColor": {"minMaxState": 0, "maxColor": {"r": 1.0, "g": 1.0,
                                                          "b": 1.0, "a": 1.0}},
            "gravityModifier": _curve(0.0), "size3D": 0, "rotation3D": 0,
        },
    }
    tree.update(modules)
    return tree


def test_disabled_shape_is_distinct_from_missing_source_module():
    disabled, _ = decode_system(_system(ShapeModule={"enabled": 0, "type": 0}))
    missing, _ = decode_system(_system())
    enabled, _ = decode_system(_system(ShapeModule={"enabled": 1, "type": 0}))
    assert disabled["shapeEnabled"] is False
    assert not disabled.get("shape")
    assert missing["shapeEnabled"] is None
    assert enabled["shapeEnabled"] is True
    assert enabled["shape"]


def test_orbital_velocity_survives_and_missing_curves_are_not_zero():
    module = {"enabled": 1, "x": _curve(1), "y": _curve(2), "z": _curve(3),
              "speedModifier": _curve(1), "inWorldSpace": False,
              "orbitalX": _curve(.15), "orbitalY": _curve(-.02),
              "orbitalZ": _curve(0), "radial": _curve(.01)}
    decoded = decode_system(_system(VelocityModule=module))[0]["velocityOverLifetime"]
    assert decoded["orbitalX"] == {"mode": "constant", "value": .15}
    assert decoded["orbitalY"] == {"mode": "constant", "value": -.02}
    assert decoded["orbitalZ"] == {"mode": "constant", "value": 0}
    assert decoded["radial"] == {"mode": "constant", "value": .01}
    assert decoded["orbitalOffsetX"] is None


def test_drag_multipliers_use_serialized_particle_field_names():
    module = {"enabled": 1, "multiplyDragByParticleSize": True,
              "multiplyDragByParticleVelocity": False,
              "multiplyDragBySize": False, "multiplyDragByVelocity": True}
    decoded = decode_system(_system(ClampVelocityModule=module))[0]["limitVelocity"]
    assert decoded["multiplyDragBySize"] is True
    assert decoded["multiplyDragByVelocity"] is False
    missing = decode_system(_system(ClampVelocityModule={"enabled": 1}))[0]["limitVelocity"]
    assert missing["multiplyDragBySize"] is None
    assert missing["multiplyDragByVelocity"] is None


def _custom_data(mode0=1, count0=4, mode1=0, count1=4, first=None):
    module = {"enabled": 1, "mode0": mode0, "vectorComponentCount0": count0,
              "mode1": mode1, "vectorComponentCount1": count1,
              "color0": {"minMaxState": 0, "maxColor": {"r": 0.0, "g": 0.0,
                                                        "b": 0.0, "a": 0.0}},
              "color1": {"minMaxState": 0, "maxColor": {"r": 0.0, "g": 0.0,
                                                        "b": 0.0, "a": 0.0}}}
    for stream in (0, 1):
        for component in range(4):
            module[f"vector{stream}_{component}"] = _curve(float(component))
    if first is not None:
        module["vector0_0"] = first
    return module


def _noise(**over):
    module = {"enabled": 1, "strength": _curve(0.1), "strengthY": _curve(0.1),
              "strengthZ": _curve(0.1), "separateAxes": 0, "frequency": 0.75,
              "damping": 1, "octaves": 3, "octaveMultiplier": 0.5,
              "octaveScale": 2.0, "quality": 2, "scrollSpeed": _curve(0.0),
              "remap": _curve(1.0), "remapY": _curve(1.0), "remapZ": _curve(1.0),
              "remapEnabled": 0, "positionAmount": _curve(1.0),
              "rotationAmount": _curve(0.0), "sizeAmount": _curve(0.0),
              # decoys under the scripting-API names
              "octaveCount": 99, "strengthMultiplier": 99.0}
    module.update(over)
    return module


def _collision(planes=(), **over):
    module = {"enabled": 1, "type": 1, "collisionMode": 1, "colliderForce": 0.0,
              "multiplyColliderForceByParticleSize": False,
              "multiplyColliderForceByParticleSpeed": False,
              "multiplyColliderForceByCollisionAngle": True,
              "m_Planes": list(planes),
              "m_Dampen": _curve(0.25), "m_Bounce": _curve(0.5),
              "m_EnergyLossOnCollision": _curve(1.0),
              "minKillSpeed": 0.0, "maxKillSpeed": 10000.0, "radiusScale": 0.16,
              "collidesWith": {"m_Bits": 8}, "maxCollisionShapes": 256,
              "quality": 0, "voxelSize": 0.5, "collisionMessages": False,
              "collidesWithDynamic": True, "interiorCollisions": False,
              # decoys under the scripting-API names
              "dampen": _curve(-1.0), "bounce": _curve(-1.0),
              "lifetimeLoss": _curve(-1.0), "mode": 0, "planes": ["decoy"]}
    module.update(over)
    return module


def _force(**over):
    module = {"enabled": 1, "x": _curve(0.0), "y": _curve(0.001),
              "z": _curve(0.0), "inWorldSpace": True, "randomizePerFrame": True,
              # decoy under the scripting-API name
              "randomized": False}
    module.update(over)
    return module


def _trail(**over):
    module = {"enabled": 1, "mode": 0, "ratio": 1.0, "lifetime": _curve(0.1),
              "minVertexDistance": 0.2, "textureMode": 0,
              "textureScale": {"x": 1.0, "y": 1.0}, "ribbonCount": 1,
              "shadowBias": 0.5, "worldSpace": False, "dieWithParticles": True,
              "sizeAffectsWidth": False, "sizeAffectsLifetime": False,
              "inheritParticleColor": True, "generateLightingData": False,
              "splitSubEmitterRibbons": False, "attachRibbonsToTransform": False,
              "colorOverLifetime": {"minMaxState": 0,
                                    "maxColor": {"r": 1.0, "g": 1.0, "b": 1.0,
                                                 "a": 1.0}},
              "widthOverTrail": _curve(0.09),
              "colorOverTrail": {"minMaxState": 0,
                                 "maxColor": {"r": 1.0, "g": 1.0, "b": 1.0,
                                              "a": 1.0}}}
    module.update(over)
    return module


def _sub(entries):
    return {"enabled": 1, "subEmitters": list(entries)}


NULL = {"m_FileID": 0, "m_PathID": 0}


# -- the six modules are modelled -------------------------------------------


def test_the_six_modules_are_no_longer_reported_as_gaps():
    tree = _system(CustomDataModule=_custom_data(), SubModule=_sub([]),
                   NoiseModule=_noise(), ForceModule=_force(),
                   CollisionModule=_collision(), TrailModule=_trail())
    out, unsupported = decode_system(tree)
    assert unsupported == []
    for name in ("CustomDataModule", "SubModule", "NoiseModule", "ForceModule",
                 "CollisionModule", "TrailModule"):
        assert name in MODELLED_MODULES
    for key in ("customData", "subEmitters", "noise", "forceOverLifetime",
                "collision", "trails"):
        assert key in out


def test_a_module_this_decoder_does_not_know_is_still_reported():
    out, unsupported = decode_system(_system(LightsModule={"enabled": 1}))
    assert unsupported == [{"module": "LightsModule",
                            "reason": "particle module not modelled"}]
    assert "lights" not in out


def test_a_module_that_is_switched_off_produces_nothing():
    tree = _system(NoiseModule=dict(_noise(), enabled=0))
    out, unsupported = decode_system(tree)
    assert unsupported == [] and "noise" not in out


# -- custom data ------------------------------------------------------------


def test_custom_data_keeps_the_curve_mode_so_the_value_still_animates():
    # A curve flattened to its constant still lands in a plausible range, so the
    # failure is silent: the value simply stops moving.  Pin the mode and keys.
    tree = _system(CustomDataModule=_custom_data(first=_keyed((0.0, 0.0),
                                                              (1.0, 1.0))))
    out, _ = decode_system(tree)
    first = out["customData"]["custom1"]["components"][0]
    assert first["mode"] == "curve"
    assert first["multiplier"] == 2.0
    assert [key["time"] for key in first["keys"]] == [0.0, 1.0]
    assert [key["value"] for key in first["keys"]] == [0.0, 1.0]


def test_custom_data_carries_every_serialized_component_and_the_evaluated_count():
    tree = _system(CustomDataModule=_custom_data(count0=2))
    stream = decode_system(tree)[0]["customData"]["custom1"]
    assert stream["componentCount"] == 2
    # All four are on disk; the count says how many are evaluated, not how many
    # are stored.  Dropping the other two would lose authored data.
    assert len(stream["components"]) == 4
    assert [c["value"] for c in stream["components"]] == [0.0, 1.0, 2.0, 3.0]


def test_a_custom_data_stream_that_is_off_says_so():
    tree = _system(CustomDataModule=_custom_data(mode1=0))
    streams = decode_system(tree)[0]["customData"]
    assert streams["custom1"]["mode"] == "vector"
    assert streams["custom2"]["mode"] == "disabled"


def test_a_custom_data_stream_in_colour_mode_uses_the_gradient_encoding():
    module = _custom_data(mode0=2)
    module["color0"] = {"minMaxState": 0,
                        "maxColor": {"r": 0.5, "g": 0.25, "b": 0.125, "a": 1.0}}
    stream = decode_system(_system(CustomDataModule=module))[0]["customData"]["custom1"]
    assert stream["mode"] == "color"
    assert stream["color"] == {"mode": "color",
                               "color": [0.5, 0.25, 0.125, 1.0]}


# -- sub-emitters -----------------------------------------------------------


def test_a_sub_emitter_keeps_the_trigger_that_decides_when_it_fires():
    # Birth fires from the per-frame update; death and collision do not, so an
    # implementation that walks one list needs to be able to tell them apart.
    entries = [{"emitter": NULL, "type": 0, "properties": 0, "emitProbability": 1.0},
               {"emitter": NULL, "type": 1, "properties": 0, "emitProbability": 1.0},
               {"emitter": NULL, "type": 2, "properties": 0, "emitProbability": 1.0}]
    out, _ = decode_system(_system(SubModule=_sub(entries)))
    assert [entry["type"] for entry in out["subEmitters"]] == ["birth",
                                                               "collision", "death"]


def test_a_null_sub_emitter_pointer_emits_nothing_and_is_not_a_gap():
    entries = [{"emitter": NULL, "type": 0, "properties": 0, "emitProbability": 1.0}]
    out, unsupported = decode_system(_system(SubModule=_sub(entries)))
    assert out["subEmitters"][0]["emitter"] is None
    assert out["subEmitters"][0]["sourcePointer"] == {"fileId": 0, "pathId": "0"}
    assert unsupported == []


def test_a_sub_emitter_this_package_cannot_resolve_is_reported():
    entries = [{"emitter": {"m_FileID": 0, "m_PathID": 55}, "type": 0,
                "properties": 0, "emitProbability": 1.0}]
    out, unsupported = decode_system(_system(SubModule=_sub(entries)),
                                     resolve_node=lambda pointer: None)
    assert out["subEmitters"][0]["emitter"] is None
    assert out["subEmitters"][0]["sourcePointer"] == {"fileId": 0, "pathId": "55"}
    assert unsupported == [{"module": "SubModule",
                            "reason": "sub-emitter is not in this package"}]


def test_a_sub_emitter_resolves_to_the_node_it_sits_on():
    entries = [{"emitter": {"m_FileID": 0, "m_PathID": 55}, "type": 2,
                "properties": 0, "emitProbability": 0.5}]
    out, unsupported = decode_system(_system(SubModule=_sub(entries)),
                                     resolve_node=lambda pointer: "splash")
    assert unsupported == []
    assert out["subEmitters"][0] == {
        "emitter": "splash", "type": "death", "properties": 0,
        "sourcePointer": {"fileId": 0, "pathId": "55"},
        "inherit": {"color": False, "size": False, "rotation": False,
                    "lifetime": False, "duration": False},
        "emitProbability": 0.5}


def test_sub_emitter_pointer_preserves_large_signed_ids_and_missing_identity():
    entries = [{"emitter": {"m_FileID": 3, "m_PathID": -9223372036854775001},
                "type": 0, "properties": 0, "emitProbability": 1.0},
               {"type": 0, "properties": 0, "emitProbability": 1.0}]
    out, _ = decode_system(_system(SubModule=_sub(entries)), resolve_node=lambda pointer: None)
    assert out["subEmitters"][0]["sourcePointer"] == {"fileId": 3, "pathId": "-9223372036854775001"}
    assert out["subEmitters"][1]["sourcePointer"] is None


def test_inherit_flags_are_read_bit_by_bit_and_the_raw_value_survives():
    # One package writes "inherit everything" as all-bits rather than the
    # enumeration's 31.  Decoding bitwise keeps that honest without rewriting it.
    entries = [{"emitter": NULL, "type": 0, "properties": -1, "emitProbability": 1.0},
               {"emitter": NULL, "type": 0, "properties": 10, "emitProbability": 1.0}]
    out, _ = decode_system(_system(SubModule=_sub(entries)))
    every, some = out["subEmitters"]
    assert every["properties"] == -1
    assert every["inherit"] == {"color": True, "size": True, "rotation": True,
                                "lifetime": True, "duration": True}
    assert some["inherit"] == {"color": False, "size": True, "rotation": False,
                               "lifetime": True, "duration": False}


# -- noise ------------------------------------------------------------------


def test_noise_reads_the_serialized_octave_count_not_the_scripting_name():
    out, _ = decode_system(_system(NoiseModule=_noise()))
    assert out["noise"]["octaves"] == 3          # the decoy under `octaveCount` is 99


def test_noise_quality_is_a_dimension_count_not_a_level_of_detail():
    for quality, name, dimensions in ((0, "low", 1), (1, "medium", 2),
                                      (2, "high", 3)):
        out, _ = decode_system(_system(NoiseModule=_noise(quality=quality)))
        assert out["noise"]["quality"] == name
        assert out["noise"]["dimensions"] == dimensions


def test_noise_damping_is_a_flag_and_the_curves_keep_their_shape():
    out, _ = decode_system(_system(NoiseModule=_noise(
        damping=0, strength=_keyed((0.0, 1.0), (1.0, 0.0)))))
    noise = out["noise"]
    assert noise["damping"] is False
    assert noise["strength"]["mode"] == "curve"
    assert noise["frequency"] == 0.75
    assert noise["remapEnabled"] is False
    assert noise["separateAxes"] is False


# -- force ------------------------------------------------------------------


def test_force_reads_randomize_per_frame_not_the_scripting_name():
    out, _ = decode_system(_system(ForceModule=_force()))
    force = out["forceOverLifetime"]
    assert force["randomizePerFrame"] is True    # the decoy under `randomized` is False
    assert force["inWorldSpace"] is True
    assert force["y"] == {"mode": "constant", "value": 0.001}


# -- collision --------------------------------------------------------------


def test_a_plane_slot_holding_no_plane_is_not_a_plane():
    # The slot is serialized but empty.  Reading it as "there is one plane"
    # invents a plane at the origin, and everything splashes on an invisible
    # floor at y = 0.
    out, unsupported = decode_system(_system(CollisionModule=_collision(
        planes=[NULL])))
    assert out["collision"]["planes"] == []
    assert out["collision"]["planeSlots"] == 1
    assert unsupported == []


def test_a_plane_that_names_a_node_is_kept():
    out, _ = decode_system(
        _system(CollisionModule=_collision(planes=[{"m_FileID": 0, "m_PathID": 9}])),
        resolve_node=lambda pointer: "floor")
    assert out["collision"]["planes"] == ["floor"]
    assert out["collision"]["planeSlots"] == 1


def test_collision_reads_the_serialized_names_not_the_scripting_names():
    out, _ = decode_system(_system(CollisionModule=_collision()))
    collision = out["collision"]
    # each decoy below is -1.0 under the scripting-API name
    assert collision["dampen"] == {"mode": "constant", "value": 0.25}
    assert collision["bounce"] == {"mode": "constant", "value": 0.5}
    assert collision["lifetimeLoss"] == {"mode": "constant", "value": 1.0}


def test_collision_type_and_mode_are_two_different_enumerations():
    out, _ = decode_system(_system(CollisionModule=_collision()))
    collision = out["collision"]
    # `type` says what is collided with; `mode` says in how many dimensions.
    # The decoy `mode` on the module is 0, which would read as "3d".
    assert collision["type"] == "world"
    assert collision["mode"] == "2d"


def test_collision_keeps_the_layer_mask_and_the_quality_it_was_authored_with():
    out, _ = decode_system(_system(CollisionModule=_collision()))
    collision = out["collision"]
    assert collision["collidesWith"] == 8
    assert collision["quality"] == "high"
    assert collision["radiusScale"] == 0.16
    assert collision["maxKillSpeed"] == 10000.0
    assert collision["multiplyColliderForceByCollisionAngle"] is True
    assert collision["multiplyColliderForceByParticleSize"] is False


# -- trails and the second material slot ------------------------------------


def test_trails_keep_the_two_modes_apart():
    out, _ = decode_system(_system(TrailModule=_trail()))
    trails = out["trails"]
    assert trails["mode"] == "perParticle"
    assert trails["textureMode"] == "stretch"
    assert trails["minVertexDistance"] == 0.2
    assert trails["dieWithParticles"] is True
    assert trails["worldSpace"] is False
    assert trails["textureScale"] == [1.0, 1.0]
    assert trails["lifetime"] == {"mode": "constant", "value": 0.1}
    assert trails["colorOverTrail"]["mode"] == "color"


def _renderer(slots, **over):
    tree = {"m_RenderMode": 0, "m_SortMode": 0, "m_SortingOrder": 0,
            "m_MinParticleSize": 0.0, "m_MaxParticleSize": 0.5,
            "m_LengthScale": 2.0, "m_VelocityScale": 0.0,
            "m_CameraVelocityScale": 0.0, "m_Pivot": {}, "m_RenderAlignment": 0,
            "m_Materials": [{"m_FileID": 0, "m_PathID": 10 + i}
                            for i in range(slots)],
            # there is no trail-material field on disk; this decoy stands in for
            # the one an implementation might expect to find
            "m_TrailMaterial": {"name": "decoy"}}
    tree.update(over)
    return tree


def test_the_trail_material_is_the_second_material_slot():
    out = decode_renderer(_renderer(2), {"name": "head"}, {"name": "trail"})
    assert out["material"] == {"name": "head"}
    assert out["trailMaterial"] == {"name": "trail"}


def test_a_renderer_with_one_slot_has_no_trail_material():
    out = decode_renderer(_renderer(1), {"name": "head"})
    assert out["material"] == {"name": "head"}
    assert "trailMaterial" not in out


def test_a_disabled_renderer_is_recorded_as_disabled():
    # The renderer's own switch is the only per-renderer gate there is: the
    # particle system component carries no enabled flag.  A consumer that cannot
    # see this draws emitters the original never shows.
    assert decode_renderer(_renderer(1, m_Enabled=0), None)["enabled"] is False
    assert decode_renderer(_renderer(1, m_Enabled=1), None)["enabled"] is True


def test_a_renderer_with_no_enabled_field_is_treated_as_enabled():
    tree = _renderer(1)
    tree.pop("m_Enabled", None)
    assert decode_renderer(tree, None)["enabled"] is True


# -- writing these values out -----------------------------------------------


def test_a_non_finite_number_is_written_as_its_name():
    text = dumps({"lifetime": float("inf"), "drift": float("-inf"),
                  "broken": float("nan")}, indent=None)
    assert text == '{"broken": "NaN", "drift": "-Infinity", "lifetime": "Infinity"}'
    # and it reads back as a number in any language that coerces numeric strings
    assert float(dumps(float("inf"), indent=None).strip('"')) == float("inf")


def test_writing_refuses_to_emit_something_no_parser_can_read():
    # The normaliser is the fix; this is the backstop for a value that reaches
    # the encoder without it.
    import json
    try:
        json.dumps({"x": float("inf")}, allow_nan=False)
    except ValueError:
        pass
    else:
        raise AssertionError("a bare Infinity was accepted")


def test_normalising_leaves_finite_numbers_and_flags_alone():
    document = {"on": True, "off": False, "none": None, "zero": 0.0,
                "count": 3, "nested": [{"value": 1.5}]}
    assert normalize(document) == document
    import json
    decoded = json.loads(dumps(document, indent=None))
    assert decoded == document
    assert decoded["on"] is True and decoded["off"] is False
    assert decoded["none"] is None
    assert isinstance(decoded["zero"], float)


def test_mesh_sampling_controls_preserve_serialized_owners_and_unknowns():
    shape = {"enabled": True, "type": 6, "placementMode": 2,
             "m_MeshSpawn": {"mode": 0, "spread": 0.0, "speed": _curve(1.0)},
             "m_UseMeshColors": False, "m_UseMeshMaterialIndex": False,
             "m_Texture": {"m_FileID": 0, "m_PathID": 0},
             "meshSpawnMode": 3, "useMeshColors": True,
             "arc": {"value": 360.0, "mode": 3}}
    result = decode_system(_system(ShapeModule=shape))[0]["shape"]["meshEmission"]
    assert result == {"sourceVersion": 1, "mode": "Random", "spread": 0.0,
                      "speed": {"mode": "constant", "value": 1.0},
                      "useColors": False, "useMaterialIndex": False,
                      "texture": {"fileId": 0, "pathId": "0"}}
    shape["m_MeshSpawn"] = {"mode": 3, "spread": .25, "speed": _curve(2.0)}
    shape["m_Texture"] = {"m_FileID": 7, "m_PathID": 9223372036854775801}
    shape["m_UseMeshColors"] = True
    result = decode_system(_system(ShapeModule=shape))[0]["shape"]["meshEmission"]
    assert result["mode"] == "BurstSpread" and result["spread"] == .25
    assert result["texture"] == {"fileId": 7, "pathId": "9223372036854775801"}
    assert result["useColors"] is True
    missing = decode_system(_system(ShapeModule={"enabled": True, "type": 6}))[0]["shape"]["meshEmission"]
    assert all(missing[key] is None for key in ["mode", "spread", "speed", "useColors", "useMaterialIndex", "texture"])


def test_enabled_unmodelled_modules_are_carried_to_runtime_not_report_only():
    tree = _system(LightsModule={"enabled": True, "mode": 1},
                   NoiseModule={"enabled": False})
    result, gaps = decode_system(tree)
    inventory = result["sourceModules"]
    assert inventory["version"] == 1
    assert "InitialModule" in inventory["enabled"]
    assert "LightsModule" in inventory["enabled"]
    assert "NoiseModule" not in inventory["enabled"]
    assert inventory["unsupported"] == gaps
    assert any(gap["module"] == "LightsModule" for gap in gaps)
    clean, gaps = decode_system(_system())
    assert clean["sourceModules"]["unsupported"] == gaps == []


# -- inputs the native route decision reads -----------------------------------


def _lane(pre, post, *times):
    return {"m_Curve": [{"time": t, "value": 1.0, "inSlope": 0.0, "outSlope": 0.0}
                        for t in times],
            "m_PreInfinity": pre, "m_PostInfinity": post, "m_RotationOrder": 4}


def _emission(enabled, rate_over_distance):
    return {"enabled": enabled, "rateOverTime": _curve(10.0),
            "rateOverDistance": rate_over_distance, "m_Bursts": []}


def test_distance_rate_is_reported_whether_or_not_emission_is_on():
    # The native decision reads the distance rate's scalar without testing the
    # emission switch, so a disabled module must still carry it; the emission
    # block itself keeps meaning "switched on".
    on, _ = decode_system(_system(EmissionModule=_emission(1, _curve(2.0))))
    assert on["emissionRateOverDistance"] == {"mode": "constant", "value": 2.0}
    assert on["emission"]["rateOverDistance"] == on["emissionRateOverDistance"]
    off, _ = decode_system(_system(EmissionModule=_emission(0, _curve(5.0))))
    assert off["emissionRateOverDistance"] == {"mode": "constant", "value": 5.0}
    assert "emission" not in off
    ranged = {"minMaxState": 3, "scalar": 8.0, "minScalar": 1.0}
    off, _ = decode_system(_system(EmissionModule=_emission(False, ranged)))
    assert off["emissionRateOverDistance"] == {"mode": "twoConstants",
                                               "min": 1.0, "max": 8.0}
    # Missing source data is unknown, not an authored zero rate.
    assert decode_system(_system())[0]["emissionRateOverDistance"] is None
    bare, _ = decode_system(_system(EmissionModule={"enabled": 0}))
    assert bare["emissionRateOverDistance"] is None


def test_curve_lanes_carry_their_own_serialized_wrap_modes():
    from core.particles import min_max_curve
    # Every lane and end holds a different value, so reading the other lane or
    # swapping pre/post changes the result instead of passing by coincidence.
    one = min_max_curve({"minMaxState": 1, "scalar": 2.0,
                         "maxCurve": _lane(0, 3, 0.25, 0.5),
                         "minCurve": _lane(1, 4, 0.0, 1.0)})
    assert (one["preInfinity"], one["postInfinity"]) == (0, 3)
    assert not any(k.startswith(("min", "max")) for k in one)
    two = min_max_curve({"minMaxState": 2, "scalar": 2.0,
                         "maxCurve": _lane(3, 0, 0.25, 0.5),
                         "minCurve": _lane(1, 4, 0.0, 0.6)})
    assert (two["maxPreInfinity"], two["maxPostInfinity"]) == (3, 0)
    assert (two["minPreInfinity"], two["minPostInfinity"]) == (1, 4)
    assert "preInfinity" not in two and "postInfinity" not in two
    for state in (0, 3):
        flat = min_max_curve({"minMaxState": state, "scalar": 1.0, "minScalar": 0.0,
                              "maxCurve": _lane(0, 3, 0.5), "minCurve": _lane(1, 4, 0.5)})
        assert not any("Infinity" in k for k in flat)
    unknown = min_max_curve({"minMaxState": 1, "scalar": 1.0,
                             "maxCurve": {"m_Curve": []}})
    assert unknown["preInfinity"] is None and unknown["postInfinity"] is None
    # Serialized raw integers only: a coerced value is refused, not guessed.
    for bad in (2.0, True, "2"):
        with pytest.raises(ValueError, match=r"AnimationCurve\.m_PreInfinity"):
            min_max_curve({"minMaxState": 1, "maxCurve": _lane(bad, 2, 0.0)})


def test_wrap_modes_reach_the_decoded_system_and_not_its_gradients():
    import json
    velocity = {"enabled": 1, "x": _curve(0.0), "z": _curve(0.0),
                "y": {"minMaxState": 2, "scalar": 1.0,
                      "maxCurve": _lane(2, 2, 0.0, 1.0),
                      "minCurve": _lane(2, 1, 0.0, 0.6)},
                "speedModifier": _curve(1.0), "inWorldSpace": False}
    gradient = {"minMaxState": 1, "maxGradient": {"m_NumColorKeys": 0,
                                                  "m_NumAlphaKeys": 0}}
    out, _ = decode_system(_system(VelocityModule=velocity,
                                   ColorModule={"enabled": 1, "gradient": gradient}))
    lane = json.loads(dumps(out))["velocityOverLifetime"]["y"]
    assert lane["mode"] == "twoCurves"
    assert [lane[k] for k in ("maxPreInfinity", "maxPostInfinity",
                              "minPreInfinity", "minPostInfinity")] == [2, 2, 2, 1]
    assert not any("Infinity" in k for k in out["colorOverLifetime"])
