"""Declared shader passes shared by all asset-domain exporters."""

from core.assets.packages import pairs


def texture_defaults(parsed):
    """Keep shader-declared defaults distinct from null material references."""
    return {p["m_Name"]: p["m_DefTexture"]["m_DefaultName"]
            for p in (parsed.get("m_PropInfo") or {}).get("m_Props", [])
            if p.get("m_Type") == 4}


def declared_passes(parsed):
    """Read LightMode from each pass, not from its enclosing subshader tags.

    A missing LightMode is an untagged pass, not an absent record. Retain the
    authored pass name as well; some versions store it in m_State.m_Name.
    """
    defaults = {p["m_Name"]: p.get("m_DefValue[0]")
                for p in (parsed.get("m_PropInfo") or {}).get("m_Props", [])}

    def scalar(value):
        if not isinstance(value, dict):
            return value
        result = dict(value)
        name = result.get("name")
        if name and name != "<noninit>" and name in defaults:
            result["default"] = defaults[name]
        return result

    result = []
    for subshader_index, subshader in enumerate(parsed.get("m_SubShaders") or []):
        subshader_tags = {str(key): value for key, value in pairs((subshader.get("m_Tags") or {}).get("tags"))}
        for pass_index, shader_pass in enumerate(subshader.get("m_Passes") or []):
            state = shader_pass.get("m_State") or {}
            tags = {str(key).upper(): value
                    for key, value in pairs((state.get("m_Tags") or {}).get("tags"))}
            name = (shader_pass.get("m_Name") or shader_pass.get("m_PassName")
                    or state.get("m_Name"))
            raster = {key: scalar(state[key]) for key in ("culling", "zTest", "zWrite")
                      if key in state}
            if "rtBlend0" in state:
                raster["blend"] = {key: scalar(value) for key, value in state["rtBlend0"].items()}
            result.append({"name": name or None, "lightMode": tags.get("LIGHTMODE"),
                           "subShaderIndex": subshader_index, "passIndex": pass_index,
                           "subShaderTags": subshader_tags, "tags": tags,
                           "renderState": raster})
    return result
