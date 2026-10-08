"""Material identity and texture bindings are source pointers, not image names."""
from copy import deepcopy
from shaders.materials import MaterialLibrary


class Record:
    bundle = "source-material-package"
    archive = "source-material-file"

    def tree(self, path_id):
        return {"m_Name": "shared-display-name", "m_Shader": {"m_FileID": 1, "m_PathID": 19},
            "m_ValidKeywords": ["ARRAY"], "m_InvalidKeywords": ["OLD"],
            "m_CustomRenderQueue": 3123, "disabledShaderPasses": ["disabled"],
            "m_EnableInstancingVariants": False,
            "m_SavedProperties": {
                "m_Ints": [["integer", 3]], "m_Floats": [["scalar", 0.75]],
                "m_Colors": [["vector", {"r": -1, "g": 2, "b": 3, "a": 4}]],
                "m_TexEnvs": [["array", {"m_Texture": {"m_FileID": 2, "m_PathID": 5},
                               "m_Scale": {"x": 2, "y": 3}, "m_Offset": {"x": 0.5, "y": -0.5}}],
                              ["unassigned", {"m_Texture": {"m_FileID": 0, "m_PathID": 0}}],
                              ["unresolved", {"m_Texture": {"m_FileID": 9, "m_PathID": 91}}]]}}


class Store:
    @staticmethod
    def source_receipt(bundle):
        return {"bundle": bundle, "sha256": "a" * 64, "bytes": 21}

    @staticmethod
    def follow(record, pointer):
        return (record, pointer["m_PathID"]) if pointer["m_FileID"] == 2 else None


class References:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def reference(self, record, path_id):
        self.calls.append((record.archive, path_id))
        return deepcopy(self.value)


def test_material_retains_exact_shader_pointer_keywords_properties_and_layered_texture():
    programs = References({"source": {"pathId": "19"}, "variants": {"file": "shaders/catalogue.variants.json"}})
    textures = References({"status": "decoded", "kind": "Texture2DArray", "file": "shader-textures/array.texture.json"})
    library = MaterialLibrary(Store(), programs, textures)
    record = Record()
    result = library.describe(record, 7, (record, 19))
    source = result["sourceMaterial"]
    assert source["source"]["pathId"] == "7"
    assert source["shaderPointer"] == {"m_FileID": 1, "m_PathID": 19}
    assert source["validKeywords"] == ["ARRAY"]
    assert source["invalidKeywords"] == ["OLD"]
    assert source["savedProperties"] == record.tree(7)["m_SavedProperties"]
    assert source["customRenderQueue"] == 3123
    assert source["disabledShaderPasses"] == ["disabled"]
    assert result["ints"] == {"integer": 3}
    array = result["textureSources"]["array"]
    assert array["texture"]["kind"] == "Texture2DArray"
    assert array["scale"] == {"x": 2, "y": 3}
    assert array["offset"] == {"x": 0.5, "y": -0.5}
    assert result["textureSources"]["unassigned"]["status"] == "unassigned"
    assert "texture" not in result["textureSources"]["unassigned"]
    assert result["textureSources"]["unresolved"]["status"] == "unavailable"
    assert "unresolved" in result["textureSources"]["unresolved"]["error"]
    assert programs.calls == [(record.archive, 19)]
    assert textures.calls == [(record.archive, 5)]


def test_cache_uses_material_identity_and_does_not_share_mutable_receipts():
    programs = References({"source": {"pathId": "19"}})
    textures = References({"status": "unavailable", "error": "unsupported source format"})
    library = MaterialLibrary(Store(), programs, textures)
    record = Record()
    first = library.describe(record, 7, (record, 19))
    first["sourceMaterial"]["source"]["pathId"] = "changed"
    first["textureSources"]["array"]["error"] = "changed"
    again = library.describe(record, 7, (record, 19))
    assert again["sourceMaterial"]["source"]["pathId"] == "7"
    assert again["textureSources"]["array"]["error"] == "unsupported source format"
    assert len(programs.calls) == 1
    other = library.describe(record, 8, (record, 19))
    assert other["sourceMaterial"]["source"]["pathId"] == "8"
    assert len(programs.calls) == 2
