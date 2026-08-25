"""Particle-system parameters, decoded into a form a renderer can consume directly.

Unity stores every animatable particle value as a *min-max curve*: a state flag
that says whether the value is a constant, a random range, one curve, or a random
pair of curves, plus the scalar and curve data for that state.  Colours use the
same idea with gradients.  This module normalises both into explicit dicts —
``{"mode": "twoConstants", "min": ..., "max": ...}`` — so a consumer never has to
know which serialized field is live for which state.

Only modules that are switched on are reported.  A module that is on but not
modelled here is listed by name so the gap is visible instead of looking like an
effect that simply does nothing.

Angular values are radians per second, as serialized.

Serialized field names and the names the engine's scripting API exposes differ
in several places, and a lookup under the scripting name returns nothing without
complaining.  This module always reads the serialized name and reports the value
under the readable one, so ``m_EnergyLossOnCollision`` is emitted as
``lifetimeLoss`` and ``octaves`` keeps its serialized spelling rather than
``octaveCount``.
"""

CURVE_MODES = {0: "constant", 1: "curve", 2: "twoCurves", 3: "twoConstants"}
GRADIENT_MODES = {0: "color", 1: "gradient", 2: "twoColors", 3: "twoGradients",
                  4: "randomColor"}
# Shape kinds these packages use; the full Unity set is larger.
SHAPE_TYPES = {0: "Sphere", 1: "SphereShell", 2: "Hemisphere", 3: "HemisphereShell",
               4: "Cone", 5: "Box", 6: "Mesh", 7: "ConeShell", 8: "ConeVolume",
               9: "ConeVolumeShell", 10: "Circle", 11: "CircleEdge",
               12: "SingleSidedEdge", 15: "BoxShell", 16: "BoxEdge", 17: "Donut",
               18: "Rectangle", 19: "Sprite"}
RENDER_MODES = {0: "Billboard", 1: "Stretch", 2: "HorizontalBillboard",
                3: "VerticalBillboard", 4: "Mesh", 5: "None"}
SIMULATION_SPACES = {0: "Local", 1: "World", 2: "Custom"}
GRADIENT_TIME_SCALE = 65535.0

# Custom data: two streams, each either off, four independent scalar curves, or
# one colour.  All four component curves are serialized whatever the count says.
CUSTOM_DATA_MODES = {0: "disabled", 1: "vector", 2: "color"}
CUSTOM_DATA_COMPONENTS = 4

# Sub-emitters: the trigger that makes a child emit, and the flags saying what
# the child takes from its parent.
SUB_EMITTER_TYPES = {0: "birth", 1: "collision", 2: "death", 3: "trigger",
                     4: "manual"}
SUB_EMITTER_INHERIT = (("color", 1), ("size", 2), ("rotation", 4),
                       ("lifetime", 8), ("duration", 16))

# Noise quality is the number of dimensions the field is sampled in, not a
# level of detail: 3 samples a 3D field, 1 and 2 sample a 2D one.
NOISE_QUALITY = {0: "low", 1: "medium", 2: "high"}
NOISE_DIMENSIONS = {0: 1, 1: 2, 2: 3}

# Collision: what is collided with, in how many dimensions, and how carefully.
COLLISION_TYPES = {0: "planes", 1: "world"}
COLLISION_MODES = {0: "3d", 1: "2d"}
COLLISION_QUALITY = {0: "high", 1: "medium", 2: "low"}

TRAIL_MODES = {0: "perParticle", 1: "ribbon"}
TRAIL_TEXTURE_MODES = {0: "stretch", 1: "tile", 2: "distributePerSegment",
                       3: "repeatPerSegment", 4: "static"}

# The renderer's material slots: the particles are drawn with the first, and a
# trail — when the trail module is on — with the second.  There is no separate
# trail-material field on disk.
TRAIL_MATERIAL_SLOT = 1

UNMODELLED_MODULE = "particle module not modelled"
UNRESOLVED_SUB_EMITTER = "sub-emitter is not in this package"
UNRESOLVED_PLANE = "collision plane is not in this package"

# Modules this decoder understands; anything else that is enabled is reported.
MODELLED_MODULES = {
    "InitialModule", "EmissionModule", "ShapeModule", "SizeModule", "ColorModule",
    "RotationModule", "VelocityModule", "ClampVelocityModule", "UVModule",
    "CustomDataModule", "SubModule", "NoiseModule", "ForceModule",
    "CollisionModule", "TrailModule",
}


def _keys(curve):
    return [{"time": round(k["time"], 6), "value": round(k["value"], 6),
             "inSlope": None if k["inSlope"] in (float("inf"), float("-inf")) else round(k["inSlope"], 6),
             "outSlope": None if k["outSlope"] in (float("inf"), float("-inf")) else round(k["outSlope"], 6)}
            for k in curve.get("m_Curve", [])]


def min_max_curve(node):
    """Normalise one min-max curve.

    ``scalar`` holds the authored constant (and the curve multiplier);
    ``minScalar`` is only live in the two-constants state.
    """
    state = node.get("minMaxState", 0)
    mode = CURVE_MODES.get(state, f"state{state}")
    out = {"mode": mode}
    if mode == "constant":
        out["value"] = round(float(node.get("scalar", 0.0)), 6)
    elif mode == "twoConstants":
        out["min"] = round(float(node.get("minScalar", 0.0)), 6)
        out["max"] = round(float(node.get("scalar", 0.0)), 6)
    elif mode == "curve":
        out["multiplier"] = round(float(node.get("scalar", 1.0)), 6)
        out["keys"] = _keys(node.get("maxCurve", {}))
    else:
        out["multiplier"] = round(float(node.get("scalar", 1.0)), 6)
        out["minKeys"] = _keys(node.get("minCurve", {}))
        out["maxKeys"] = _keys(node.get("maxCurve", {}))
    return out


def _gradient(node):
    """Colour and alpha keys of one gradient, on a 0..1 time axis.

    Colour keys carry rgb and their own times; alpha keys reuse the same colour
    slots' alpha with a separate time axis.
    """
    colors, alphas = [], []
    for i in range(int(node.get("m_NumColorKeys", 0))):
        key = node.get(f"key{i}", {})
        colors.append({"time": round(node.get(f"ctime{i}", 0) / GRADIENT_TIME_SCALE, 6),
                       "color": [round(key.get(c, 0.0), 6) for c in "rgb"]})
    for i in range(int(node.get("m_NumAlphaKeys", 0))):
        key = node.get(f"key{i}", {})
        alphas.append({"time": round(node.get(f"atime{i}", 0) / GRADIENT_TIME_SCALE, 6),
                       "alpha": round(key.get("a", 0.0), 6)})
    return {"colorKeys": colors, "alphaKeys": alphas}


def _color(node):
    return [round(node.get(c, 0.0), 6) for c in "rgba"]


def min_max_gradient(node):
    """Normalise one min-max gradient."""
    state = node.get("minMaxState", 0)
    mode = GRADIENT_MODES.get(state, f"state{state}")
    out = {"mode": mode}
    if mode in ("color", "randomColor"):
        out["color"] = _color(node.get("maxColor", {}))
    elif mode == "twoColors":
        out["min"] = _color(node.get("minColor", {}))
        out["max"] = _color(node.get("maxColor", {}))
    elif mode == "gradient":
        out["gradient"] = _gradient(node.get("maxGradient", {}))
    else:
        out["minGradient"] = _gradient(node.get("minGradient", {}))
        out["maxGradient"] = _gradient(node.get("maxGradient", {}))
    return out


def _vec(node, keys="xyz"):
    return [round(float(node.get(k, 0.0)), 6) for k in keys]


def _number(node, key, default=0.0):
    return round(float(node.get(key, default)), 6)


def _enum(table, value):
    return table.get(value, value)


def _node_path(pointer, resolve_node, gaps, module, reason):
    """The prefab node a pointer names, or ``None`` when it names none.

    A pointer that is null on disk is an authored state — that entry does
    nothing — so it is not a gap.  A pointer that names something this package
    does not hold is a gap, and is reported rather than flattened to the same
    ``None``.
    """
    if not (pointer or {}).get("m_PathID", 0):
        return None
    path = None if resolve_node is None else resolve_node(pointer)
    if path is None:
        gaps.append({"module": module, "reason": reason})
    return path


def _custom_data_stream(module, stream):
    """One custom-data stream: four independent curves, or one colour.

    ``componentCount`` says how many components are *evaluated*; all four are
    serialized regardless, so all four are reported and none of the authored
    data is dropped.
    """
    mode = module.get(f"mode{stream}")
    return {
        "mode": _enum(CUSTOM_DATA_MODES, mode),
        "componentCount": module.get(f"vectorComponentCount{stream}"),
        "components": [min_max_curve(module.get(f"vector{stream}_{component}", {}))
                       for component in range(CUSTOM_DATA_COMPONENTS)],
        "color": min_max_gradient(module.get(f"color{stream}", {})),
    }


def _sub_emitter(entry, resolve_node, gaps):
    """One sub-emitter: which system, on what trigger, inheriting what."""
    properties = entry.get("properties", 0)
    return {
        "emitter": _node_path(entry.get("emitter"), resolve_node, gaps,
                              "SubModule", UNRESOLVED_SUB_EMITTER),
        "type": _enum(SUB_EMITTER_TYPES, entry.get("type")),
        "properties": properties,
        "inherit": {name: bool(properties & bit) for name, bit in SUB_EMITTER_INHERIT},
        "emitProbability": _number(entry, "emitProbability", 1.0),
    }


def _noise(module):
    """Noise field parameters, with the dimension count `quality` selects."""
    quality = module.get("quality")
    out = {
        "separateAxes": bool(module.get("separateAxes")),
        "strength": min_max_curve(module.get("strength", {})),
        "strengthY": min_max_curve(module.get("strengthY", {})),
        "strengthZ": min_max_curve(module.get("strengthZ", {})),
        "frequency": _number(module, "frequency"),
        "damping": bool(module.get("damping")),
        "octaves": module.get("octaves"),
        "octaveMultiplier": _number(module, "octaveMultiplier"),
        "octaveScale": _number(module, "octaveScale"),
        "quality": _enum(NOISE_QUALITY, quality),
        "dimensions": _enum(NOISE_DIMENSIONS, quality),
        "scrollSpeed": min_max_curve(module.get("scrollSpeed", {})),
        "remapEnabled": bool(module.get("remapEnabled")),
        "remap": min_max_curve(module.get("remap", {})),
        "remapY": min_max_curve(module.get("remapY", {})),
        "remapZ": min_max_curve(module.get("remapZ", {})),
        "positionAmount": min_max_curve(module.get("positionAmount", {})),
        "rotationAmount": min_max_curve(module.get("rotationAmount", {})),
        "sizeAmount": min_max_curve(module.get("sizeAmount", {})),
    }
    return out


def _collision(module, resolve_node, gaps):
    """Collision parameters, with the plane slots counted separately from the
    planes that are actually in them."""
    slots = module.get("m_Planes") or []
    planes = [_node_path(pointer, resolve_node, gaps, "CollisionModule",
                         UNRESOLVED_PLANE) for pointer in slots]
    return {
        "type": _enum(COLLISION_TYPES, module.get("type")),
        "mode": _enum(COLLISION_MODES, module.get("collisionMode")),
        "dampen": min_max_curve(module.get("m_Dampen", {})),
        "bounce": min_max_curve(module.get("m_Bounce", {})),
        "lifetimeLoss": min_max_curve(module.get("m_EnergyLossOnCollision", {})),
        "minKillSpeed": _number(module, "minKillSpeed"),
        "maxKillSpeed": _number(module, "maxKillSpeed"),
        "radiusScale": _number(module, "radiusScale"),
        "quality": _enum(COLLISION_QUALITY, module.get("quality")),
        "voxelSize": _number(module, "voxelSize"),
        "collidesWith": (module.get("collidesWith") or {}).get("m_Bits"),
        "collidesWithDynamic": bool(module.get("collidesWithDynamic")),
        "interiorCollisions": bool(module.get("interiorCollisions")),
        "maxCollisionShapes": module.get("maxCollisionShapes"),
        "collisionMessages": bool(module.get("collisionMessages")),
        "colliderForce": _number(module, "colliderForce"),
        "multiplyColliderForceByParticleSize":
            bool(module.get("multiplyColliderForceByParticleSize")),
        "multiplyColliderForceByParticleSpeed":
            bool(module.get("multiplyColliderForceByParticleSpeed")),
        "multiplyColliderForceByCollisionAngle":
            bool(module.get("multiplyColliderForceByCollisionAngle")),
        "planeSlots": len(slots),
        "planes": [path for path in planes if path is not None],
    }


def _trails(module):
    """Trail geometry and appearance."""
    return {
        "mode": _enum(TRAIL_MODES, module.get("mode")),
        "ratio": _number(module, "ratio", 1.0),
        "lifetime": min_max_curve(module.get("lifetime", {})),
        "minVertexDistance": _number(module, "minVertexDistance"),
        "textureMode": _enum(TRAIL_TEXTURE_MODES, module.get("textureMode")),
        "textureScale": _vec(module.get("textureScale", {}), "xy"),
        "ribbonCount": module.get("ribbonCount"),
        "shadowBias": _number(module, "shadowBias"),
        "worldSpace": bool(module.get("worldSpace")),
        "dieWithParticles": bool(module.get("dieWithParticles")),
        "sizeAffectsWidth": bool(module.get("sizeAffectsWidth")),
        "sizeAffectsLifetime": bool(module.get("sizeAffectsLifetime")),
        "inheritParticleColor": bool(module.get("inheritParticleColor")),
        "generateLightingData": bool(module.get("generateLightingData")),
        "splitSubEmitterRibbons": bool(module.get("splitSubEmitterRibbons")),
        "attachRibbonsToTransform": bool(module.get("attachRibbonsToTransform")),
        "colorOverLifetime": min_max_gradient(module.get("colorOverLifetime", {})),
        "widthOverTrail": min_max_curve(module.get("widthOverTrail", {})),
        "colorOverTrail": min_max_gradient(module.get("colorOverTrail", {})),
    }


def decode_system(tree, resolve_node=None):
    """Emitter parameters of one particle system, plus its unmodelled modules.

    *resolve_node* maps a pointer to the prefab node path it names; sub-emitters
    and collision planes point at other objects of the same prefab and are
    useless to a consumer as raw ids.  Returns ``(system, unsupported)``, where
    each unsupported entry is ``{module, reason}``.
    """
    gaps = []
    initial = tree.get("InitialModule", {})
    out = {
        "duration": round(float(tree.get("lengthInSec", 0.0)), 6),
        "looping": bool(tree.get("looping")),
        "prewarm": bool(tree.get("prewarm")),
        "playOnAwake": bool(tree.get("playOnAwake")),
        "simulationSpeed": round(float(tree.get("simulationSpeed", 1.0)), 6),
        "simulationSpace": SIMULATION_SPACES.get(tree.get("moveWithTransform"),
                                                 tree.get("moveWithTransform")),
        "randomSeed": tree.get("randomSeed"),
        # A fixed seed is authored: the system replays the same random stream
        # every time rather than drawing a fresh one at play.
        "autoRandomSeed": bool(tree.get("autoRandomSeed", True)),
        # Delay before the system starts emitting, on its own clock.
        "startDelay": min_max_curve(tree.get("startDelay") or {}),
        # Ring-buffer mode changes the lifecycle rather than the look: a system
        # in one of these modes recycles its particles instead of retiring them
        # at the end of their lifetime, and the loop range says which part of
        # the lifetime it holds them in.
        "ringBufferMode": tree.get("ringBufferMode"),
        "ringBufferLoopRange": _vec(tree.get("ringBufferLoopRange") or {}, "xy"),
        # How the emitter transform's scale reaches the particles.
        "scalingMode": tree.get("scalingMode"),
        # Which velocity of the emitter the particles inherit.
        "emitterVelocityMode": tree.get("emitterVelocityMode"),
        "maxParticles": initial.get("maxNumParticles"),
        "start": {
            "lifetime": min_max_curve(initial.get("startLifetime", {})),
            "speed": min_max_curve(initial.get("startSpeed", {})),
            "size": min_max_curve(initial.get("startSize", {})),
            "rotation": min_max_curve(initial.get("startRotation", {})),
            "color": min_max_gradient(initial.get("startColor", {})),
            "gravityModifier": min_max_curve(initial.get("gravityModifier", {})),
            "size3D": bool(initial.get("size3D")),
            "rotation3D": bool(initial.get("rotation3D")),
        },
    }
    if initial.get("size3D"):
        out["start"]["sizeY"] = min_max_curve(initial.get("startSizeY", {}))
        out["start"]["sizeZ"] = min_max_curve(initial.get("startSizeZ", {}))

    emission = tree.get("EmissionModule", {})
    if emission.get("enabled"):
        out["emission"] = {
            "rateOverTime": min_max_curve(emission.get("rateOverTime", {})),
            "rateOverDistance": min_max_curve(emission.get("rateOverDistance", {})),
            "bursts": [{"time": round(float(b.get("time", 0.0)), 6),
                        "count": min_max_curve(b.get("countCurve", {})),
                        "cycleCount": b.get("cycleCount"),
                        "repeatInterval": round(float(b.get("repeatInterval", 0.0)), 6),
                        "probability": round(float(b.get("probability", 1.0)), 6)}
                       for b in emission.get("m_Bursts", [])],
        }

    shape = tree.get("ShapeModule", {})
    if shape.get("enabled"):
        out["shape"] = {
            "type": SHAPE_TYPES.get(shape.get("type"), shape.get("type")),
            "radius": round(float(shape.get("radius", {}).get("value", 0.0)
                                  if isinstance(shape.get("radius"), dict)
                                  else shape.get("radius", 0.0)), 6),
            "radiusThickness": round(float(shape.get("radiusThickness", 0.0)), 6),
            "angle": round(float(shape.get("angle", 0.0)), 6),
            "length": round(float(shape.get("length", 0.0)), 6),
            "arc": round(float(shape.get("arc", {}).get("value", 0.0)
                               if isinstance(shape.get("arc"), dict)
                               else shape.get("arc", 0.0)), 6),
            "boxThickness": _vec(shape.get("boxThickness", {})),
            "donutRadius": round(float(shape.get("donutRadius", 0.0)), 6),
            "position": _vec(shape.get("m_Position", {})),
            "rotation": _vec(shape.get("m_Rotation", {})),
            "scale": _vec(shape.get("m_Scale", {})),
            "alignToDirection": bool(shape.get("alignToDirection")),
            "randomDirectionAmount": round(float(shape.get("randomDirectionAmount", 0.0)), 6),
            "sphericalDirectionAmount": round(float(shape.get("sphericalDirectionAmount", 0.0)), 6),
            # Where on the mesh a particle is born, and how far off its surface.
            "meshPlacement": shape.get("placementMode"),
            "meshNormalOffset": round(float(shape.get("m_MeshNormalOffset", 0.0)), 6),
            "meshMaterialIndex": (shape.get("m_MeshMaterialIndex")
                                  if shape.get("m_UseMeshMaterialIndex") else None),
        }

    size = tree.get("SizeModule", {})
    if size.get("enabled"):
        out["sizeOverLifetime"] = {"separateAxes": bool(size.get("separateAxes")),
                                   "curve": min_max_curve(size.get("curve", {}))}
        if size.get("separateAxes"):
            out["sizeOverLifetime"]["y"] = min_max_curve(size.get("y", {}))
            out["sizeOverLifetime"]["z"] = min_max_curve(size.get("z", {}))

    color = tree.get("ColorModule", {})
    if color.get("enabled"):
        out["colorOverLifetime"] = min_max_gradient(color.get("gradient", {}))

    rotation = tree.get("RotationModule", {})
    if rotation.get("enabled"):
        out["rotationOverLifetime"] = {"separateAxes": bool(rotation.get("separateAxes")),
                                       "curve": min_max_curve(rotation.get("curve", {}))}

    velocity = tree.get("VelocityModule", {})
    if velocity.get("enabled"):
        out["velocityOverLifetime"] = {
            "x": min_max_curve(velocity.get("x", {})),
            "y": min_max_curve(velocity.get("y", {})),
            "z": min_max_curve(velocity.get("z", {})),
            "speedModifier": min_max_curve(velocity.get("speedModifier", {})),
            "inWorldSpace": bool(velocity.get("inWorldSpace")),
        }

    clamp = tree.get("ClampVelocityModule", {})
    if clamp.get("enabled"):
        out["limitVelocity"] = {
            "separateAxis": bool(clamp.get("separateAxis")),
            "magnitude": min_max_curve(clamp.get("magnitude", {})),
            "dampen": round(float(clamp.get("dampen", 0.0)), 6),
            "drag": min_max_curve(clamp.get("drag", {})) if clamp.get("drag") else None,
            "inWorldSpace": bool(clamp.get("inWorldSpace")),
        }

    uv = tree.get("UVModule", {})
    if uv.get("enabled"):
        out["textureSheet"] = {
            "tilesX": uv.get("tilesX"), "tilesY": uv.get("tilesY"),
            "animationType": uv.get("animationType"), "timeMode": uv.get("timeMode"),
            "fps": round(float(uv.get("fps", 0.0)), 6),
            "cycles": uv.get("cycles"), "rowIndex": uv.get("rowIndex"),
            "startFrame": min_max_curve(uv.get("startFrame", {})),
            "frameOverTime": min_max_curve(uv.get("frameOverTime", {})),
        }

    custom = tree.get("CustomDataModule", {})
    if custom.get("enabled"):
        out["customData"] = {"custom1": _custom_data_stream(custom, 0),
                             "custom2": _custom_data_stream(custom, 1)}

    sub = tree.get("SubModule", {})
    if sub.get("enabled"):
        out["subEmitters"] = [_sub_emitter(entry, resolve_node, gaps)
                              for entry in sub.get("subEmitters") or []]

    noise = tree.get("NoiseModule", {})
    if noise.get("enabled"):
        out["noise"] = _noise(noise)

    force = tree.get("ForceModule", {})
    if force.get("enabled"):
        out["forceOverLifetime"] = {
            "x": min_max_curve(force.get("x", {})),
            "y": min_max_curve(force.get("y", {})),
            "z": min_max_curve(force.get("z", {})),
            "inWorldSpace": bool(force.get("inWorldSpace")),
            "randomizePerFrame": bool(force.get("randomizePerFrame")),
        }

    collision = tree.get("CollisionModule", {})
    if collision.get("enabled"):
        out["collision"] = _collision(collision, resolve_node, gaps)

    trail = tree.get("TrailModule", {})
    if trail.get("enabled"):
        out["trails"] = _trails(trail)

    unsupported = [{"module": name, "reason": UNMODELLED_MODULE}
                   for name in sorted(name for name, value in tree.items()
                                      if isinstance(value, dict) and value.get("enabled")
                                      and name not in MODELLED_MODULES)]
    return out, unsupported + gaps


# The per-particle values a renderer hands the shader, in the order they are
# packed into the vertex.  A material addresses one of them by naming a slot and
# a component, so without this list those addresses point at nothing.
VERTEX_STREAMS = {
    0: "position", 1: "normal", 2: "tangent", 3: "color", 4: "uv", 5: "uv2",
    6: "uv3", 7: "uv4", 8: "animBlend", 9: "animFrame", 10: "center",
    11: "vertexId", 12: "sizeX", 13: "sizeXY", 14: "sizeXYZ", 15: "rotation",
    16: "rotation3D", 17: "rotationSpeed", 18: "rotationSpeed3D",
    19: "velocity", 20: "speed", 21: "agePercent", 22: "invStartLifetime",
    23: "stableRandomX", 24: "stableRandomXY", 25: "stableRandomXYZ",
    26: "stableRandomXYZW", 27: "varyingRandomX", 28: "varyingRandomXY",
    29: "varyingRandomXYZ", 30: "varyingRandomXYZW",
    31: "custom1X", 32: "custom1XY", 33: "custom1XYZ", 34: "custom1XYZW",
    35: "custom2X", 36: "custom2XY", 37: "custom2XYZ", 38: "custom2XYZW",
    39: "noiseSumX", 40: "noiseSumXY", 41: "noiseSumXYZ",
    42: "noiseImpulseX", 43: "noiseImpulseXY", 44: "noiseImpulseXYZ",
    45: "meshIndex", 46: "particleIndex", 47: "colorPackedAsTwoFloats",
    48: "meshAxisOfRotation", 49: "nextTrailCenter", 50: "previousTrailCenter",
}


def vertex_streams(values):
    """One renderer's stream list, as names, with the raw codes kept alongside.

    A code this table does not know is reported as its number rather than
    dropped, so a stream list can never come out shorter than it is.
    """
    codes = [int(v) for v in (values or [])]
    return {"codes": codes,
            "names": [VERTEX_STREAMS.get(code, code) for code in codes]}


def decode_renderer(tree, material, trail_material=None):
    """Draw settings of one particle renderer.

    A renderer holds one material slot per thing it draws.  The particles are
    drawn with the first; a second slot exists only when the trail module is on,
    and it is the material the trail is drawn with — there is no separate
    trail-material field on disk.  The key is emitted whenever the slot exists,
    so a second material can never be dropped without it showing.

    ``enabled`` is the renderer's own switch.  It is the only per-renderer gate
    there is: the particle system component carries no enabled flag, so a
    renderer that ships disabled draws nothing no matter what its system says.
    A consumer that ignores it draws emitters the original never shows.
    """
    out = {
        "enabled": bool(tree.get("m_Enabled", 1)),
        "renderMode": RENDER_MODES.get(tree.get("m_RenderMode"), tree.get("m_RenderMode")),
        "sortMode": tree.get("m_SortMode"),
        "sortingOrder": tree.get("m_SortingOrder"),
        "minParticleSize": round(float(tree.get("m_MinParticleSize", 0.0)), 6),
        "maxParticleSize": round(float(tree.get("m_MaxParticleSize", 0.0)), 6),
        "lengthScale": round(float(tree.get("m_LengthScale", 0.0)), 6),
        "velocityScale": round(float(tree.get("m_VelocityScale", 0.0)), 6),
        "cameraVelocityScale": round(float(tree.get("m_CameraVelocityScale", 0.0)), 6),
        "pivot": _vec(tree.get("m_Pivot", {})),
        "alignment": tree.get("m_RenderAlignment"),
        # Custom streams are what a material's per-particle addresses resolve
        # against.  The switch is reported separately from the list, because a
        # list is authored whether or not it is switched on.
        "useCustomVertexStreams": bool(tree.get("m_UseCustomVertexStreams", False)),
        "vertexStreams": vertex_streams(tree.get("m_VertexStreams")),
        "useCustomTrailVertexStreams": bool(
            tree.get("m_UseCustomTrailVertexStreams", False)),
        "trailVertexStreams": vertex_streams(tree.get("m_TrailVertexStreams")),
        "material": material,
    }
    if len(tree.get("m_Materials") or []) > TRAIL_MATERIAL_SLOT:
        out["trailMaterial"] = trail_material
    return out
