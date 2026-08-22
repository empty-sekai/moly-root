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

# Modules this decoder understands; anything else that is enabled is reported.
MODELLED_MODULES = {
    "InitialModule", "EmissionModule", "ShapeModule", "SizeModule", "ColorModule",
    "RotationModule", "VelocityModule", "ClampVelocityModule", "UVModule",
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


def decode_system(tree):
    """Emitter parameters of one particle system, plus its unmodelled modules."""
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

    unsupported = sorted(name for name, value in tree.items()
                         if isinstance(value, dict) and value.get("enabled")
                         and name not in MODELLED_MODULES)
    return out, unsupported


def decode_renderer(tree, material):
    """Draw settings of one particle renderer."""
    return {
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
        "material": material,
    }
