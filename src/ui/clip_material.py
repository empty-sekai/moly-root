"""Resolve the actual Graphic material family before UI JSON loses its PPtrs.

Only the inputs needed to reproduce RectMask2D's shader term are emitted here.
This does not claim that the host's bitmap text is the source SDF renderer.
"""
from UnityPy.classes.PPtr import PPtr
from shaders.objects import shader_name


def _pointer(value, assetsfile):
    if isinstance(value, dict):
        file_id, path_id = value["m_FileID"], value["m_PathID"]
    else:
        file_id, path_id = value
    return PPtr(m_FileID=file_id, m_PathID=path_id, assetsfile=assetsfile)


def component_clip_material(obj, cls, fields, resolver):
    text = "m_fontSize" in fields
    image = cls.endswith("Image") and "m_Color" in fields
    if not (text or image):
        return None
    reference = fields.get("m_sharedMaterial" if text else "m_Material", (0, 0))
    pointer = _pointer(reference, obj.assets_file)
    if not pointer:
        if text:
            # A runtime-assigned font material must be supplied by that host.
            # Do not label a pending TMP material as default Image material.
            return None
        return {"shader": "UI/Default", "source": "Graphic.defaultGraphicMaterial"}

    material = pointer.deref()
    cache = getattr(resolver, "_clip_material_cache", None)
    if cache is None:
        cache = resolver._clip_material_cache = {}
    key = (material.assets_file.name, material.path_id)
    if key in cache:
        return cache[key]
    tree = material.read_typetree()
    shader = _pointer(tree["m_Shader"], material.assets_file).deref()
    name = shader_name(shader.read_typetree())
    result = {"shader": name, "source": {"file": key[0], "pathId": key[1]},
              "shaderSource": {"file": shader.assets_file.name, "pathId": shader.path_id}}
    if name in ("TextMeshPro/Distance Field", "TextMeshPro/Mobile/Distance Field"):
        floats = dict(tree["m_SavedProperties"]["m_Floats"])
        # Do not substitute defaults for absent source properties.
        result["softness"] = [floats["_MaskSoftnessX"], floats["_MaskSoftnessY"]]
        result["scale"] = [floats["_ScaleX"], floats["_ScaleY"]]
    cache[key] = result
    return result
