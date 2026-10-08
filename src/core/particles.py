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
# How the emission point is placed along the arc (or the radius of an edge
# shape, or a mesh's spawn positions: one serialized struct, one value domain).
# Random draws the point anew within the arc for every particle; Loop and
# PingPong animate it around the arc at the arc's speed curve, PingPong
# reversing where Loop wraps; BurstSpread spaces the particles out along the
# arc instead of placing them randomly.
ARC_MODES = {0: "Random", 1: "Loop", 2: "PingPong", 3: "BurstSpread"}
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
    "CollisionModule", "TrailModule", "InheritVelocityModule",
    "RotationBySpeedModule",
}


def _keys(curve):
    return [{"time": k["time"], "value": k["value"],
             # jsonio preserves signed infinity as a numeric string. Collapsing
             # both signs to null loses which endpoint a stepped segment uses.
             "inSlope": k["inSlope"],
             "outSlope": k["outSlope"],
             "weightedMode": k.get("weightedMode"),
             "inWeight": float(k.get("inWeight", 0.0)),
             "outWeight": float(k.get("outWeight", 0.0))}
            for k in curve.get("m_Curve", [])]


def _wrap(curve):
    """An animation curve's serialized ``(m_PreInfinity, m_PostInfinity)``.

    The integers are kept verbatim: the engine's polynomial-curve test compares
    these serialized values themselves when a curve does not span the whole
    0..1 lifetime, so a translated enum name would be a second encoding with
    nothing to check it against.  A missing field stays ``None`` (unknown), not
    a default wrap mode.
    """
    out = []
    for name in ("m_PreInfinity", "m_PostInfinity"):
        value = curve.get(name)
        if value is not None and type(value) is not int:
            raise ValueError(f"AnimationCurve.{name}: serialized integer expected")
        out.append(value)
    return tuple(out)


def min_max_curve(node):
    """Normalise one min-max curve.

    ``scalar`` holds the authored constant (and the curve multiplier);
    ``minScalar`` is only live in the two-constants state.  Each curve lane
    carries its serialized wrap modes next to its keys.
    """
    state = node.get("minMaxState", 0)
    mode = CURVE_MODES.get(state, f"state{state}")
    out = {"mode": mode}
    if mode == "constant":
        out["value"] = float(node.get("scalar", 0.0))
    elif mode == "twoConstants":
        out["min"] = float(node.get("minScalar", 0.0))
        out["max"] = float(node.get("scalar", 0.0))
    elif mode == "curve":
        out["multiplier"] = float(node.get("scalar", 1.0))
        out["keys"] = _keys(node.get("maxCurve", {}))
        out["preInfinity"], out["postInfinity"] = _wrap(node.get("maxCurve", {}))
    else:
        out["multiplier"] = float(node.get("scalar", 1.0))
        out["minKeys"] = _keys(node.get("minCurve", {}))
        out["maxKeys"] = _keys(node.get("maxCurve", {}))
        if mode == "twoCurves":
            out["minPreInfinity"], out["minPostInfinity"] = _wrap(node.get("minCurve", {}))
            out["maxPreInfinity"], out["maxPostInfinity"] = _wrap(node.get("maxCurve", {}))
    return out


def _gradient(node):
    """Colour and alpha keys of one gradient, on a 0..1 time axis.

    Colour keys carry rgb and their own times; alpha keys reuse the same colour
    slots' alpha with a separate time axis.
    """
    colors, alphas = [], []
    for i in range(int(node.get("m_NumColorKeys", 0))):
        key = node.get(f"key{i}", {})
        colors.append({"time": node.get(f"ctime{i}", 0) / GRADIENT_TIME_SCALE,
                       "color": [key.get(c, 0.0) for c in "rgb"]})
    for i in range(int(node.get("m_NumAlphaKeys", 0))):
        key = node.get(f"key{i}", {})
        alphas.append({"time": node.get(f"atime{i}", 0) / GRADIENT_TIME_SCALE,
                       "alpha": key.get("a", 0.0)})
    return {"colorKeys": colors, "alphaKeys": alphas,
            "interpolation": {0: "blend", 1: "fixed", 2: "perceptualBlend"}.get(
                node.get("m_Mode"), node.get("m_Mode")),
            "colorSpace": node.get("m_ColorSpace")}


def _color(node):
    return [node.get(c, 0.0) for c in "rgba"]


def min_max_gradient(node):
    """Normalise one min-max gradient."""
    state = node.get("minMaxState", 0)
    mode = GRADIENT_MODES.get(state, f"state{state}")
    out = {"mode": mode}
    if mode == "color":
        out["color"] = _color(node.get("maxColor", {}))
    elif mode == "twoColors":
        out["min"] = _color(node.get("minColor", {}))
        out["max"] = _color(node.get("maxColor", {}))
    elif mode in ("gradient", "randomColor"):
        out["gradient"] = _gradient(node.get("maxGradient", {}))
    else:
        out["minGradient"] = _gradient(node.get("minGradient", {}))
        out["maxGradient"] = _gradient(node.get("maxGradient", {}))
    return out


def _vec(node, keys="xyz"):
    return [float(node.get(k, 0.0)) for k in keys]


def _number(node, key, default=0.0):
    return float(node.get(key, default))


def _seed_bits(value):
    """Unity serializes an int32; native seed arithmetic consumes its uint32 bits."""
    if value is None:
        return None
    if type(value) is not int or not -(1 << 31) <= value <= (1 << 32) - 1:
        raise ValueError("ParticleSystem.randomSeed: int32 or uint32 seed expected")
    return value if value >= 0 else value + (1 << 32)


def _optional_seed_auto(value):
    if value is None or type(value) is bool:
        return value
    raise ValueError("ParticleSystem.autoRandomSeed: bool or null expected")


def _enum(table, value):
    return table.get(value, value)


# The shape's texture controls, serialized beside its texture reference:
# (serialized name, readable name, value type).  Integers stay raw codes.
SHAPE_TEXTURE_CONTROLS = (
    ("m_TextureClipChannel", "textureClipChannel", int),
    ("m_TextureClipThreshold", "textureClipThreshold", float),
    ("m_TextureUVChannel", "textureUVChannel", int),
    ("m_TextureColorAffectsParticles", "textureColorAffectsParticles", bool),
    ("m_TextureAlphaAffectsParticles", "textureAlphaAffectsParticles", bool),
    ("m_TextureBilinearFiltering", "textureBilinearFiltering", bool),
)


def _shape_texture(shape):
    """The shape's texture reference and controls, for every shape type.

    The engine's per-shape start routines -- circle and edge, cone volume,
    donut and hemisphere among them, not only the mesh one -- test whether the
    shape holds a texture while starting new particles, and take a texture
    step only when it does.  A consumer can refuse that step only if the
    reference is there for every type, so it is reported beside the other
    shape fields and not only inside the mesh-spawn record (which keeps its
    own copy, in the form its consumer already reads).

    ``texture`` is ``None`` for the null reference (file and path id both
    zero) and ``{fileId, pathId}`` for anything else, so any non-null pair is
    kept and can be refused.  When the serialized field is missing the key is
    left out: unknown is not the same as no texture.  The controls are
    reported verbatim under their scripting names, ``None`` when missing.
    """
    out = {}
    if "m_Texture" in shape:
        pointer = shape["m_Texture"]
        if (not isinstance(pointer, dict) or type(pointer.get("m_FileID")) is not int
                or type(pointer.get("m_PathID")) is not int):
            raise ValueError("ShapeModule.m_Texture: serialized object reference expected")
        out["texture"] = (None if pointer["m_FileID"] == 0 and pointer["m_PathID"] == 0
                          else {"fileId": pointer["m_FileID"], "pathId": str(pointer["m_PathID"])})
    for name, readable, kind in SHAPE_TEXTURE_CONTROLS:
        value = shape.get(name)
        if value is not None:
            if kind is float and type(value) in (int, float):
                value = float(value)
            elif type(value) is not kind:
                raise ValueError(f"ShapeModule.{name}: serialized {kind.__name__} expected")
        out[readable] = value
    return out


def _shape_controls(shape):
    # Keep missing old metadata distinct from a current authored zero/mode.
    radius = shape.get("radius") if isinstance(shape.get("radius"), dict) else {}
    arc = shape.get("arc") if isinstance(shape.get("arc"), dict) else {}
    return {
        "sourceVersion": 1,
        "arcSpread": arc.get("spread"),
        "arcSpeed": min_max_curve(arc["speed"]) if "speed" in arc else None,
        "radiusMode": _enum(ARC_MODES, radius.get("mode")),
        "radiusSpread": radius.get("spread"),
        "radiusSpeed": min_max_curve(radius["speed"]) if "speed" in radius else None,
        "randomPositionAmount": shape.get("randomPositionAmount"),
    }


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
    pointer = entry.get("emitter")
    return {
        "emitter": _node_path(pointer, resolve_node, gaps,
                              "SubModule", UNRESOLVED_SUB_EMITTER),
        "sourcePointer": ({"fileId": pointer.get("m_FileID"),
                           "pathId": (str(pointer["m_PathID"])
                                      if "m_PathID" in pointer else None)}
                          if isinstance(pointer, dict) else None),
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


def _plane_sources(slots, resolve_node, resolve_asset, gaps):
    """What each plane slot holds, in slot order.

    An empty slot is ``None``.  A slot naming a node of this prefab is
    ``{"node": path}``.  A slot naming a Transform of another package -- a
    model asset's own node, which the prefab points at without instantiating
    it -- is ``{"asset": ...}`` with that Transform's package, archive, node
    path in its own file and its local transform chain, root first (the node
    is never placed in a scene, so its chain is all the pose it has).  A slot
    that names something neither resolves is reported as a gap and recorded
    as ``{"unresolved": true}``.
    """
    sources = []
    for pointer in slots:
        if not (pointer or {}).get("m_PathID", 0):
            sources.append(None)
            continue
        path = None if resolve_node is None else resolve_node(pointer)
        if path is not None:
            sources.append({"node": path})
            continue
        asset = None if resolve_asset is None else resolve_asset(pointer)
        if asset is not None:
            sources.append({"asset": asset})
            continue
        gaps.append({"module": "CollisionModule", "reason": UNRESOLVED_PLANE})
        sources.append({"unresolved": True})
    return sources


def _collision(module, resolve_node, gaps, resolve_asset=None):
    """Collision parameters, with the plane slots counted separately from the
    planes that are actually in them.  ``planes`` lists the slots that name a
    node of this prefab; ``planeSources`` records every slot in order,
    including a Transform of another package (see ``_plane_sources``)."""
    slots = module.get("m_Planes") or []
    sources = _plane_sources(slots, resolve_node, resolve_asset, gaps)
    planes = [source["node"] for source in sources if source and "node" in source]
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
        "planes": planes,
        "planeSources": sources,
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


def decode_system(tree, resolve_node=None, resolve_asset=None):
    """Emitter parameters of one particle system, plus its unmodelled modules.

    *resolve_node* maps a pointer to the prefab node path it names; sub-emitters
    and collision planes point at other objects of the same prefab and are
    useless to a consumer as raw ids.  *resolve_asset* maps a collision plane
    pointer into another package to the Transform record it names (see
    ``_plane_sources``), or ``None``.  Returns ``(system, unsupported)``, where
    each unsupported entry is ``{module, reason}``.
    """
    gaps = []
    initial = tree.get("InitialModule", {})
    out = {
        "duration": float(tree.get("lengthInSec", 0.0)),
        "looping": bool(tree.get("looping")),
        "prewarm": bool(tree.get("prewarm")),
        "playOnAwake": bool(tree.get("playOnAwake")),
        "simulationSpeed": float(tree.get("simulationSpeed", 1.0)),
        # These govern when simulation advances and who owns its shutdown.
        # Missing metadata stays unknown; it is not an authored default.
        "useUnscaledTime": tree.get("useUnscaledTime"),
        "cullingMode": tree.get("cullingMode"),
        "stopAction": tree.get("stopAction"),
        "simulationSpace": SIMULATION_SPACES.get(tree.get("moveWithTransform"),
                                                 tree.get("moveWithTransform")),
        "randomSeed": _seed_bits(tree.get("randomSeed")),
        # A fixed seed is authored: the system replays the same random stream
        # every time rather than drawing a fresh one at play.
        "autoRandomSeed": _optional_seed_auto(tree.get("autoRandomSeed")),
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
            "randomizeRotationDirection": initial.get("randomizeRotationDirection"),
            "gravitySource": initial.get("gravitySource"),
            "customEmitterVelocity": (_vec(initial["customEmitterVelocity"])
                                      if "customEmitterVelocity" in initial else None),
            "size3D": bool(initial.get("size3D")),
            "rotation3D": bool(initial.get("rotation3D")),
        },
    }
    if initial.get("size3D"):
        out["start"]["sizeY"] = min_max_curve(initial.get("startSizeY", {}))
        out["start"]["sizeZ"] = min_max_curve(initial.get("startSizeZ", {}))
    # The start-rotation family serialises three curve slots -- `startRotation`,
    # `startRotationX`, `startRotationY` -- and there is no `startRotationZ`
    # field: the engine's own binding maps the unsuffixed slot to the z axis
    # (the size family is the mirror image: there the unsuffixed `startSize`
    # is the x axis).  So z is the `rotation` reported above, and 3D start
    # rotation adds x/y, exactly as size3D adds y/z.  The InitialModule key
    # set is constant across packages, 3D flag or not, so the slots are read
    # by their serialized names and only exported when the flag is on.
    if initial.get("rotation3D"):
        out["start"]["rotationX"] = min_max_curve(initial.get("startRotationX", {}))
        out["start"]["rotationY"] = min_max_curve(initial.get("startRotationY", {}))

    emission = tree.get("EmissionModule", {})
    # The native route decision reads the distance rate's scalar whether or not
    # emission is switched on, so it is reported for every system.  The
    # ``emission`` block below still appears only for an enabled module, and its
    # presence remains the switch.  A missing field stays ``None`` (unknown)
    # rather than becoming an authored zero.
    rate_over_distance = emission.get("rateOverDistance")
    out["emissionRateOverDistance"] = (min_max_curve(rate_over_distance)
                                       if isinstance(rate_over_distance, dict) else None)
    if emission.get("enabled"):
        out["emission"] = {
            "rateOverTime": min_max_curve(emission.get("rateOverTime", {})),
            "rateOverDistance": min_max_curve(emission.get("rateOverDistance", {})),
            "bursts": [{"time": float(b.get("time", 0.0)),
                        "count": min_max_curve(b.get("countCurve", {})),
                        "cycleCount": b.get("cycleCount"),
                        "repeatInterval": float(b.get("repeatInterval", 0.0)),
                        "probability": float(b.get("probability", 1.0))}
                       for b in emission.get("m_Bursts", [])],
        }

    shape = tree.get("ShapeModule", {})
    # Absence is unknown; an explicitly disabled module still emits from the
    # emitter origin, along its local +Z axis. Keep that distinction losslessly.
    out["shapeEnabled"] = (bool(shape["enabled"]) if "enabled" in shape else None)
    if shape.get("enabled"):
        out["shape"] = {
            "type": SHAPE_TYPES.get(shape.get("type"), shape.get("type")),
            "radius": float(shape.get("radius", {}).get("value", 0.0)
                                  if isinstance(shape.get("radius"), dict)
                                  else shape.get("radius", 0.0)),
            "radiusThickness": float(shape.get("radiusThickness", 0.0)),
            "angle": float(shape.get("angle", 0.0)),
            "length": float(shape.get("length", 0.0)),
            "arc": float(shape.get("arc", {}).get("value", 0.0)
                               if isinstance(shape.get("arc"), dict)
                               else shape.get("arc", 0.0)),
            # The arc is serialized as one struct holding the extent, how the
            # emission point moves along it, and that movement's spread and
            # speed; the mode is that struct's `mode`, not a sibling field.
            "arcMode": (ARC_MODES.get(shape["arc"].get("mode"),
                                      shape["arc"].get("mode"))
                        if isinstance(shape.get("arc"), dict) else None),
            **_shape_controls(shape),
            **_shape_texture(shape),
            "boxThickness": _vec(shape.get("boxThickness", {})),
            "donutRadius": float(shape.get("donutRadius", 0.0)),
            "position": _vec(shape.get("m_Position", {})),
            "rotation": _vec(shape.get("m_Rotation", {})),
            "scale": _vec(shape.get("m_Scale", {})),
            "alignToDirection": bool(shape.get("alignToDirection")),
            "randomDirectionAmount": float(shape.get("randomDirectionAmount", 0.0)),
            "sphericalDirectionAmount": float(shape.get("sphericalDirectionAmount", 0.0)),
            # Where on the mesh a particle is born, and how far off its surface.
            "meshPlacement": shape.get("placementMode"),
            # Mesh sampling is independent of the circular arc. Preserve its
            # serialized owner and distinguish absent metadata from authored
            # false/zero; consumers must not silently assume uniform vertices.
            "meshEmission": {
                "sourceVersion": 1,
                "mode": _enum(ARC_MODES, (shape.get("m_MeshSpawn") or {}).get("mode")),
                "spread": (shape.get("m_MeshSpawn") or {}).get("spread"),
                "speed": (min_max_curve(shape["m_MeshSpawn"]["speed"])
                          if isinstance((shape.get("m_MeshSpawn") or {}).get("speed"), dict) else None),
                "useColors": shape.get("m_UseMeshColors"),
                "useMaterialIndex": shape.get("m_UseMeshMaterialIndex"),
                "texture": ({"fileId": shape["m_Texture"].get("m_FileID"),
                             "pathId": str(shape["m_Texture"]["m_PathID"])
                             if "m_PathID" in shape["m_Texture"] else None}
                            if isinstance(shape.get("m_Texture"), dict) else None),
            } if shape.get("type") == 6 else None,
            "meshNormalOffset": float(shape.get("m_MeshNormalOffset", 0.0)),
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
        if rotation.get("separateAxes"):
            # `curve` is the z axis: the engine's rotation-module editor binds
            # its z field to the serialized name `curve`, while the size module
            # is the mirror image (there `curve` is x and y/z are the extras).
            # x/y exist as authored curves only in separate-axes mode, and an
            # all-zero pair is authored data, not a default -- it exports as
            # zero curves, exactly as authored.
            out["rotationOverLifetime"]["x"] = min_max_curve(rotation.get("x", {}))
            out["rotationOverLifetime"]["y"] = min_max_curve(rotation.get("y", {}))

    rotation_speed = tree.get("RotationBySpeedModule", {})
    if rotation_speed.get("enabled"):
        # Angular velocity driven by particle speed. Its serialized layout is
        # the rotation module's: the engine's own module editor binds the z
        # field to the unsuffixed `curve` and x/y to `x`/`y` (live only in
        # separate-axes mode), and `range` is the speed interval the curve's
        # 0..1 domain spans. Values are the serialized radians per second. A
        # missing range stays unknown rather than becoming an authored 0..0.
        out["rotationBySpeed"] = {
            "separateAxes": bool(rotation_speed.get("separateAxes")),
            "curve": min_max_curve(rotation_speed.get("curve", {})),
            "range": (_vec(rotation_speed["range"], "xy")
                      if isinstance(rotation_speed.get("range"), dict) else None),
        }
        if rotation_speed.get("separateAxes"):
            out["rotationBySpeed"]["x"] = min_max_curve(rotation_speed.get("x", {}))
            out["rotationBySpeed"]["y"] = min_max_curve(rotation_speed.get("y", {}))

    velocity = tree.get("VelocityModule", {})
    if velocity.get("enabled"):
        out["velocityOverLifetime"] = {
            "x": min_max_curve(velocity.get("x", {})),
            "y": min_max_curve(velocity.get("y", {})),
            "z": min_max_curve(velocity.get("z", {})),
            "speedModifier": min_max_curve(velocity.get("speedModifier", {})),
            "inWorldSpace": bool(velocity.get("inWorldSpace")),
        }
        # Orbital/radial motion belongs to this same enabled module. Omitting
        # these curves silently turns an orbiting particle into a linear one.
        for key in ("orbitalX", "orbitalY", "orbitalZ", "orbitalOffsetX",
                    "orbitalOffsetY", "orbitalOffsetZ", "radial"):
            out["velocityOverLifetime"][key] = (
                min_max_curve(velocity[key]) if key in velocity else None)

    clamp = tree.get("ClampVelocityModule", {})
    if clamp.get("enabled"):
        out["limitVelocity"] = {
            "separateAxis": bool(clamp.get("separateAxis")),
            "magnitude": min_max_curve(clamp.get("magnitude", {})),
            "dampen": float(clamp.get("dampen", 0.0)),
            "drag": min_max_curve(clamp.get("drag", {})) if clamp.get("drag") else None,
            "inWorldSpace": bool(clamp.get("inWorldSpace")),
            "multiplyDragBySize": clamp.get("multiplyDragByParticleSize"),
            "multiplyDragByVelocity": clamp.get("multiplyDragByParticleVelocity"),
        }
        for key in ("x", "y", "z"):
            out["limitVelocity"][key] = min_max_curve(clamp[key]) if key in clamp else None

    uv = tree.get("UVModule", {})
    if uv.get("enabled"):
        out["textureSheet"] = {
            "mode": uv.get("mode"),
            "tilesX": uv.get("tilesX"), "tilesY": uv.get("tilesY"),
            "animationType": uv.get("animationType"), "timeMode": uv.get("timeMode"),
            "fps": float(uv.get("fps", 0.0)),
            "cycles": uv.get("cycles"), "rowIndex": uv.get("rowIndex"),
            "startFrame": min_max_curve(uv.get("startFrame", {})),
            "frameOverTime": min_max_curve(uv.get("frameOverTime", {})),
            "speedRange": _vec(uv["speedRange"], "xy") if "speedRange" in uv else None,
            "uvChannelMask": uv.get("uvChannelMask"), "rowMode": uv.get("rowMode"),
            "flipU": uv.get("flipU"), "flipV": uv.get("flipV"),
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

    inherit = tree.get("InheritVelocityModule", {})
    if inherit.get("enabled"):
        # Serialized names differ from the scripting API. Missing controls stay
        # unknown so an enabled module cannot silently acquire a zero curve.
        out["inheritVelocity"] = {
            "mode": _enum({0: "initial", 1: "current"}, inherit.get("m_Mode")),
            "curve": (min_max_curve(inherit["m_Curve"])
                      if isinstance(inherit.get("m_Curve"), dict) else None),
        }

    collision = tree.get("CollisionModule", {})
    if collision.get("enabled"):
        out["collision"] = _collision(collision, resolve_node, gaps, resolve_asset)

    trail = tree.get("TrailModule", {})
    if trail.get("enabled"):
        out["trails"] = _trails(trail)

    unsupported = [{"module": name, "reason": UNMODELLED_MODULE}
                   for name in sorted(name for name, value in tree.items()
                                      if isinstance(value, dict) and value.get("enabled")
                                      and name not in MODELLED_MODULES)]
    # The consumer must see enabled-but-unmodelled modules too. A report-only
    # omission otherwise turns a source system into a different simulation.
    out["sourceModules"] = {
        "version": 1,
        "enabled": sorted(name for name, value in tree.items()
                          if isinstance(value, dict) and value.get("enabled")),
        "unsupported": unsupported + gaps,
    }
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
        "geometrySchemaVersion": 1,
        "enabled": bool(tree.get("m_Enabled", 1)),
        "normalDirection": tree.get("m_NormalDirection"),
        "flip": _vec(tree["m_Flip"]) if "m_Flip" in tree else None,
        "allowRoll": tree.get("m_AllowRoll"),
        "freeformStretching": tree.get("m_FreeformStretching"),
        "rotateWithStretchDirection": tree.get("m_RotateWithStretchDirection"),
        "applyActiveColorSpace": tree.get("m_ApplyActiveColorSpace"),
        "meshDistribution": tree.get("m_MeshDistribution"),
        "sortingFudge": tree.get("m_SortingFudge"),
        "sortingLayerId": tree.get("m_SortingLayerID"),
        "renderingLayerMask": tree.get("m_RenderingLayerMask"),
        "maskInteraction": tree.get("m_MaskInteraction"),
        "renderMode": RENDER_MODES.get(tree.get("m_RenderMode"), tree.get("m_RenderMode")),
        "sortMode": tree.get("m_SortMode"),
        "sortingOrder": tree.get("m_SortingOrder"),
        "minParticleSize": float(tree.get("m_MinParticleSize", 0.0)),
        "maxParticleSize": float(tree.get("m_MaxParticleSize", 0.0)),
        "lengthScale": float(tree.get("m_LengthScale", 0.0)),
        "velocityScale": float(tree.get("m_VelocityScale", 0.0)),
        "cameraVelocityScale": float(tree.get("m_CameraVelocityScale", 0.0)),
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
