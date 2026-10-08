"""Furniture particle payloads use the same source contract as weather effects.

Paths are relative to the fixture particle extraction root. Material snapshots,
shader catalogues and complete source mip payloads are supplied by the shared
libraries; no family-specific shader reconstruction or texture approximation is
introduced here.
"""
import json
from pathlib import Path

from phenomena.meshes import Store as MeshStore
from shaders.materials import MaterialLibrary
from shaders.programs import ProgramLibrary
from shaders.textures import TextureLibrary


class Geometry:
    def __init__(self, directory):
        self.store = MeshStore(directory, "models")

    @property
    def entries(self):
        return self.store.entries

    def references(self, record, path_id, slot=0):
        if record.kinds.get(path_id) != "Mesh":
            raise ValueError("particle mesh pointer does not name a Mesh")
        mesh = self.store.mesh(record, path_id)
        references = [{"file": mesh["file"], "node": item["node"],
                       "geometrySchemaVersion": 1, "meshSlot": slot,
                       "coordinates": "gltf-reflect-x-flip-v",
                       "sha256": mesh["sha256"], "bytes": mesh["bytes"],
                       "source": mesh["meshSource"], "bounds": mesh["sourceBounds"]}
                      for item in mesh.get("meshes", [])]
        if not references:
            raise ValueError("particle Mesh exported no drawable geometry")
        return references


class SourceAssets:
    def __init__(self, store, directory, *, shader_compiler=None):
        from shaders.bridge import Compiler
        directory = Path(directory)
        compiler = Compiler(shader_compiler) if shader_compiler is not None else None
        self.programs = ProgramLibrary(store, directory / "shaders", compiler=compiler)
        self.textures = TextureLibrary(store, directory / "shader-textures")
        self.materials = MaterialLibrary(store, self.programs, self.textures)
        self.geometry = Geometry(directory / "models")
        self.directory = directory
        self.catalogues = {}
        self.material_uses_without_catalogue = 0

    def note_material(self, material):
        """Report source-only output honestly, separately from emitter census."""
        reference = (material.get("shaderProgram") or {}).get("variants")
        if not reference:
            self.material_uses_without_catalogue += 1
            return
        file = reference["file"]
        if file in self.catalogues:
            return
        catalogue = json.loads((self.directory / file).read_text(encoding="utf-8"))
        variants = [variant for source_pass in catalogue["passes"]
                    for variant in source_pass["variants"]
                    if variant["reference"]["platform"] == 9
                    and variant["reference"]["gpuProgramType"] == 4]
        self.catalogues[file] = {
            "gles3Variants": len(variants),
            "converted": sum(variant["status"] == "converted" for variant in variants),
            "sourceErrors": len(catalogue.get("sourceErrors") or []),
        }

    def program_summary(self):
        supplied = self.programs.compiler is not None
        variants = sum(row["gles3Variants"] for row in self.catalogues.values())
        converted = sum(row["converted"] for row in self.catalogues.values())
        errors = sum(row["sourceErrors"] for row in self.catalogues.values())
        status = ("source-only" if not supplied else "programs-incomplete"
                  if errors or variants != converted or self.material_uses_without_catalogue
                  else "programs-converted")
        return {
            "status": status, "compilerSupplied": supplied,
            "catalogues": len(self.catalogues), "gles3Variants": variants,
            "converted": converted, "unavailable": variants - converted,
            "catalogueSourceErrors": errors,
            "materialUsesWithoutCatalogue": self.material_uses_without_catalogue,
            "runtimeValidation": "not-performed",
            "note": ("No --shader-compiler: source data only, not playable particle assets; "
                     "no legacy shader fallback is supplied." if not supplied else
                     "Offline program conversion is not runtime playback validation; "
                     "selected variants and their dependencies still require admission."),
        }
