"""Texture arrays: how one asset holds several pictures, and how a layer is chosen.

Some effect textures are not one picture but a **texture array** — N pictures of the
same size and format stacked into one asset.  Sampling one needs three coordinates
``(u, v, layer)``, and here the layer is not a constant: the material stores a small
set of scalars that say which per-particle value to read the layer from and how to
turn it into an integer index.  A consumer that only followed the single-image
binding would sample the array's 2D companion slot, which is empty, and draw a flat
white quad instead.

So an array is exported layer by layer — one image file per layer, in layer order —
and the material records the binding *and* the resolved sampling parameters, so the
consumer never has to rediscover the encoding:

``progressCoord`` is a packed selector, ``component * 10 + vector``: the vector part
picks which per-particle custom value carries the layer progress (0 selects a
constant zero, meaning the slot is not driven at all), and the component part picks
x/y/z/w of it.  ``sliceCount`` is authored separately from the real layer count and
the two do disagree in shipped data, so both are reported: the arithmetic uses
``sliceCount`` and the graphics API then clamps to the layers that exist.

A slot in the single-image mode is reported as such: the array asset is bound but
the shader does not sample it, and calling that a picture would be wrong.
"""
import math

# The mode scalar that puts a slot in array mode; anything else samples the 2D slot.
ARRAY_MODE = 1.0

# Suffix that marks a texture property as the array companion of a 2D slot.
ARRAY_SUFFIX = "2DArray"

# Per-slot scalars, as (property suffix, exported name).
SAMPLING_FIELDS = (("Mode", "mode"), ("SliceCount", "sliceCount"),
                   ("Progress", "progress"), ("ProgressCoord", "progressCoord"),
                   ("OffsetXCoord", "offsetXCoord"),
                   ("OffsetYCoord", "offsetYCoord"))

# Vector part of the packed selector: which per-particle custom value is read.
PROGRESS_VECTORS = {0: None, 1: "custom1", 2: "custom2"}
COMPONENTS = "xyzw"

# The clamp the shader applies before wrapping, kept as authored.
PROGRESS_CLAMP = 0.999000013

LAYER_FORMULA = ("layer = min(layers - 1, max(0, floor(fract(clamp("
                 "progressSource + progress, 0, 0.999000013)) * sliceCount)))")


def _upper_snake(name):
    """``_BaseMap`` -> ``_BASE_MAP``: the shader keyword spelling of a property."""
    out = []
    for index, letter in enumerate(name):
        if letter.isupper() and index and name[index - 1] not in "_":
            out.append("_")
        out.append(letter.upper())
    return "".join(out)


def slot_prefix(slot):
    """``_BaseMap2DArray`` -> ``_BaseMap``, or ``None`` for a single-image slot."""
    if slot.endswith(ARRAY_SUFFIX) and len(slot) > len(ARRAY_SUFFIX):
        return slot[:-len(ARRAY_SUFFIX)]
    return None


def mode_keyword(prefix):
    """Shader keyword that selects the array variant of one slot."""
    return f"{_upper_snake(prefix)}_MODE_2D_ARRAY"


def progress_source(coord):
    """Decode the packed ``component * 10 + vector`` selector.

    Returns ``{"vector", "component", "constant"}``; ``constant`` is true when the
    selector picks the zero vector, so the layer progress is not driven by the
    particle at all and only ``progress`` remains.
    """
    if coord is None:
        return None
    value = int(round(float(coord)))
    vector = PROGRESS_VECTORS.get(value % 10, f"vector{value % 10}")
    index = value // 10
    component = COMPONENTS[index] if index < len(COMPONENTS) else f"component{index}"
    if vector is None:
        return {"vector": None, "component": None, "constant": True}
    return {"vector": vector, "component": component, "constant": False}


def sampling(prefix, floats, keywords):
    """Resolved sampling parameters of one array slot.

    *floats* is the material's scalar property map and *keywords* its enabled
    shader keywords.  ``arrayMode`` says whether the array is sampled at all;
    ``keyword`` is the keyword that agrees with it, reported so a consumer can
    cross-check the mode scalar against the shader variant.
    """
    out = {}
    for suffix, name in SAMPLING_FIELDS:
        value = floats.get(f"{prefix}{suffix}")
        out[name] = None if value is None else float(value)
    keyword = mode_keyword(prefix)
    out["arrayMode"] = out["mode"] == ARRAY_MODE
    out["keyword"] = keyword
    out["keywordEnabled"] = keyword in (keywords or ())
    out["progressSource"] = progress_source(out["progressCoord"])
    out["progressClamp"] = PROGRESS_CLAMP
    out["layerFormula"] = LAYER_FORMULA
    return out


def layer_of(progress_value, params, layers):
    """The layer a given progress value selects, by the formula above.

    Present so the exported parameters have one executable meaning: a consumer that
    reads ``layerFormula`` and a test that pins the arithmetic agree by construction.
    """
    slices = params.get("sliceCount") or 0.0
    value = float(progress_value) + float(params.get("progress") or 0.0)
    value = min(max(value, 0.0), PROGRESS_CLAMP)
    value = value - math.floor(value)                 # fract
    index = math.floor(value * float(slices))
    return min(max(index, 0), int(layers) - 1)
