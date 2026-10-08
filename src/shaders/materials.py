"""Connect material object identity to compiled shader and texture payloads.

Preview images remain useful to viewers, but are not a shader's texture source:
that source includes its mip chain, layers, colour encoding and sampler settings.
No pointer, material property or absent compiler is replaced with a guessed value.
"""
from __future__ import annotations

from copy import deepcopy

from core.assets.packages import pairs
from .programs import object_identity


class MaterialLibrary:
    """Source references shared by all instances of a material in an extraction."""

    def __init__(self, store, programs, textures):
        self.store = store
        self.programs = programs
        self.textures = textures
        self._receipts = {}
        self._materials = {}

    def _identity(self, record, path_id):
        if record.bundle not in self._receipts:
            self._receipts[record.bundle] = self.store.source_receipt(record.bundle)
        return object_identity(record, path_id, self._receipts[record.bundle])

    def describe(self, record, path_id, shader_target):
        key = (record.bundle, record.archive, path_id)
        if key in self._materials:
            return deepcopy(self._materials[key])
        tree = record.tree(path_id)
        properties = tree.get("m_SavedProperties") or {}
        bindings = {}
        for name, entry in pairs(properties.get("m_TexEnvs") or []):
            entry = entry or {}
            pointer = entry.get("m_Texture") or {}
            binding = {"pointer": deepcopy(pointer),
                       "scale": deepcopy(entry.get("m_Scale")),
                       "offset": deepcopy(entry.get("m_Offset"))}
            bindings[str(name)] = binding
            if not pointer.get("m_PathID", 0):
                # A null property binding can use a declared shader default or
                # a runtime writer. Neither is established by a null pointer.
                binding.update(status="unassigned")
                continue
            target = self.store.follow(record, pointer)
            if target is None:
                binding.update(status="unavailable", error="source texture pointer is unresolved")
                continue
            binding["texture"] = self.textures.reference(*target)
            binding["status"] = binding["texture"]["status"]
            if binding["texture"].get("error"):
                binding["error"] = binding["texture"]["error"]
        result = {
            "sourceMaterial": {
                "schemaVersion": 1, "source": self._identity(record, path_id),
                "shaderPointer": deepcopy(tree.get("m_Shader")),
                "savedProperties": deepcopy(properties),
                "validKeywords": deepcopy(tree.get("m_ValidKeywords")),
                "invalidKeywords": deepcopy(tree.get("m_InvalidKeywords")),
                "legacyKeywords": tree.get("m_ShaderKeywords"),
                "disabledShaderPasses": deepcopy(tree.get("disabledShaderPasses")),
                "stringTagMap": deepcopy(tree.get("stringTagMap")),
                "customRenderQueue": tree.get("m_CustomRenderQueue"),
                "enableInstancingVariants": tree.get("m_EnableInstancingVariants"),
                "doubleSidedGI": tree.get("m_DoubleSidedGI"),
                "lightmapFlags": tree.get("m_LightmapFlags"),
            },
            "shaderProgram": self.programs.reference(*shader_target),
            "textureSources": bindings,
            "ints": {str(name): value for name, value in pairs(properties.get("m_Ints") or [])},
        }
        self._materials[key] = result
        return deepcopy(result)
