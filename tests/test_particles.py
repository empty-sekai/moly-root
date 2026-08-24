"""Particle module decoding.

Unity's serialized field names and the names its scripting API exposes are not
the same, and a lookup under the scripting name succeeds silently with nothing
in it.  Every such pair below is pinned by a fixture that carries a **decoy**
under the scripting name: an implementation reading the wrong key does not fall
back to a default, it reads the decoy, and the assertion says so.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.jsonio import dumps, normalize  # noqa: E402
from core.particles import (MODELLED_MODULES, decode_renderer,  # noqa: E402
                            decode_system)


def _curve(value):
    return {"minMaxState": 0, "scalar": value, "minScalar": 0.0}


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
    assert unsupported == []


def test_a_sub_emitter_this_package_cannot_resolve_is_reported():
    entries = [{"emitter": {"m_FileID": 0, "m_PathID": 55}, "type": 0,
                "properties": 0, "emitProbability": 1.0}]
    out, unsupported = decode_system(_system(SubModule=_sub(entries)),
                                     resolve_node=lambda pointer: None)
    assert out["subEmitters"][0]["emitter"] is None
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
        "inherit": {"color": False, "size": False, "rotation": False,
                    "lifetime": False, "duration": False},
        "emitProbability": 0.5}


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
    assert text == '{"lifetime": "Infinity", "drift": "-Infinity", "broken": "NaN"}'
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
    assert dumps(document, indent=None).startswith('{"on": true, "off": false')
