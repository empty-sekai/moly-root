"""Source-addressed compiled programs for runtime consumers.

The program's identity includes the supplied package, serialized file, object,
compiler platform and record address. Content deduplication never substitutes
for that identity. Pass references keep their source indices and parameter
records; selection is separate from extraction and never uses a shader name.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from core.shader_passes import declared_passes
from . import blob, objects

SCHEMA_VERSION = 1


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def object_identity(record, path_id, receipt):
    return {"package": dict(receipt), "serializedFile": record.archive,
            "pathId": str(path_id)}


def _publish(directory, prefix, data, suffix):
    sha = digest(data)
    path = directory / f"{sha}{suffix}"
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("shader content identity conflicts with existing bytes")
    else:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"file": f"{prefix}/{path.name}", "sha256": sha, "bytes": len(data)}


def _properties(parsed):
    # Retain the complete declaration. A source numeric default is distinct
    # from an absent material override, and a texture default is not a file.
    return deepcopy((parsed.get("m_PropInfo") or {}).get("m_Props") or [])


def describe(tree, identity, write_program):
    """Decode a Shader object and connect every declared program to its bytes.

    ``write_program`` is an output store, not a source resolver. It receives
    the exact body and returns a content-addressed file reference. An unreadable
    platform remains explicitly unavailable and cannot masquerade as no passes.
    """
    parsed = objects.parsed_form(tree)
    result = {"schemaVersion": SCHEMA_VERSION, "source": identity,
              "name": objects.shader_name(tree), "properties": _properties(parsed),
              "keywordNames": list(parsed.get("m_KeywordNames") or []),
              "keywordFlags": list(parsed.get("m_KeywordFlags") or []),
              "passes": declared_passes(parsed), "platforms": []}
    refs = objects.subprograms(tree)
    for target in result["passes"]:
        sub = target["subShaderIndex"]
        index = target["passIndex"]
        source_pass = parsed["m_SubShaders"][sub]["m_Passes"][index]
        # Fixed-function pass fields not yet interpreted are still retained.
        # A renderer must reject an unimplemented non-neutral state explicitly.
        target["serializedState"] = deepcopy(source_pass.get("m_State") or {})
        target["passType"] = source_pass.get("m_Type")
        target["programBlocks"] = {
            name: {"serializedKeywordStateMask": deepcopy(value.get("m_SerializedKeywordStateMask")),
                   "commonParameters": deepcopy(value.get("m_CommonParameters"))}
            for name, value in source_pass.items()
            if name.startswith("prog") and isinstance(value, dict)}
        target["programReferences"] = [deepcopy(ref) for ref in refs
                                        if (ref["subshader"], ref["pass"]) == (sub, index)]
        # Preserve the actual serialized declaration and its array coordinates.
        # Two equal record references in different slots are still two owners.
        addresses = {}
        for block_name, block in source_pass.items():
            if not block_name.startswith("prog") or not isinstance(block, dict):
                continue
            for group_index, group in enumerate(block.get("m_PlayerSubPrograms") or []):
                for slot_index, declaration in enumerate(group):
                    key = (block_name, declaration.get("m_BlobIndex"), declaration.get("m_GpuProgramType"))
                    addresses.setdefault(key, []).append((group_index, slot_index, declaration))
        for reference in target["programReferences"]:
            key = (reference["programBlock"], reference["record"], reference["gpuProgramType"])
            declarations = addresses.get(key, [])
            if declarations:
                group, slot, declaration = declarations.pop(0)
                reference.update(platformGroupIndex=group, subprogramIndex=slot,
                                 shaderRequirements=declaration.get("m_ShaderRequirements"),
                                 serializedDeclaration=deepcopy(declaration))
    try:
        platforms = objects.platform_segments(tree)
    except (ValueError, KeyError, TypeError) as exc:
        result["error"] = f"shader container: {type(exc).__name__}: {exc}"
        return result
    for platform, segments in platforms:
        entry = {"platform": platform, "segmentBytes": list(map(len, segments)),
                 "segmentSha256": [digest(part) for part in segments],
                 "programs": [], "parameters": []}
        result["platforms"].append(entry)
        try:
            decoded = blob.parse_segments(segments, platform)
            entry["byteAccount"] = decoded["account"]
            records = decoded["records"]
            for ref in (r for r in refs if r["platform"] == platform):
                index = ref["record"]
                if not isinstance(index, int) or not 0 <= index < len(records):
                    raise ValueError(f"pass references missing program record {index}")
                program = records[index]
                if (program.get("kind") != "program"
                        or program.get("programType") != ref["gpuProgramType"]):
                    raise ValueError(f"pass/program type mismatch at record {index}")
                parameter = ref["parameterRecord"]
                if parameter is not None:
                    if (not isinstance(parameter, int) or not 0 <= parameter < len(records)
                            or records[parameter].get("kind") != "parameters"):
                        raise ValueError(f"pass references invalid parameter record {parameter}")
            for index, record in enumerate(records):
                address = {"record": index, "segment": record.get("segment", 0),
                           "offset": record.get("sourceOffset", record["offset"]),
                           "bytes": record["length"], "version": record["version"]}
                if record["kind"] == "parameters":
                    entry["parameters"].append({**address,
                        "constantBuffers": deepcopy(record["constantBuffers"]),
                        "bindings": deepcopy(record["bindings"])})
                    continue
                code = bytes(record["code"])
                body = write_program(code, ".glsl" if record["programType"] in blob.GL_TEXT_TYPES else ".bin")
                source = {"shader": identity, "platform": platform, **address,
                          "codeSha256": digest(code)}
                entry["programs"].append({**address, "identity": digest(canonical_bytes(source)),
                    "source": source, "programType": record["programType"],
                    "keywords": sorted(record.get("keywords") or []),
                    "localKeywords": sorted(record.get("localKeywords") or []),
                    "vertexChannelMask": record["channelSourceMap"],
                    "vertexChannels": [list(pair) for pair in record["channels"]],
                    "code": body})
            entry["status"] = "extracted"
        except (ValueError, KeyError, TypeError) as exc:
            entry["programs"] = []
            entry["parameters"] = []
            entry["status"] = "unavailable"
            entry["error"] = f"{type(exc).__name__}: {exc}"
    return result


class ProgramLibrary:
    """One content store per extraction, shared across all material instances."""

    def __init__(self, store, directory, prefix="shaders", compiler=None):
        self.store = store
        self.directory = Path(directory)
        self.prefix = prefix
        self.compiler = compiler
        self._receipts = {}
        self._references = {}

    def _catalogue(self, document, source_reference):
        # The compact catalogue is loaded by a runtime. Full binary reflection
        # stays in the source document and the selected conversion receipt.
        from .bridge import BridgeError, publish_conversion
        catalogue = {"schemaVersion": SCHEMA_VERSION, "source": document["source"],
                     "name": document["name"], "sourceDocument": source_reference,
                     "keywordNames": document["keywordNames"],
                     "keywordFlags": document["keywordFlags"],
                     "properties": deepcopy(document["properties"]), "passes": []}
        platforms = {block["platform"]: block for block in document["platforms"]}
        for source_pass in document["passes"]:
            target = {k: deepcopy(v) for k, v in source_pass.items() if k != "programReferences"}
            target["variants"] = []
            catalogue["passes"].append(target)
            for ref in source_pass["programReferences"]:
                # Native Vulkan records are retained in the source document,
                # not mislabeled as text shaders accepted by this adapter.
                if ref["platform"] != objects.PLATFORM_GLSL:
                    continue
                variant = {"reference": deepcopy(ref), "status": "unavailable"}
                target["variants"].append(variant)
                try:
                    block = platforms.get(ref["platform"])
                    if not block or block.get("status") != "extracted":
                        raise BridgeError("source compiler platform is unavailable")
                    program = next(p for p in block["programs"] if p["record"] == ref["record"])
                    parameter = next((p for p in block["parameters"] if p["record"] == ref["parameterRecord"]), None)
                    variant.update(identity=program["identity"], keywords=program["keywords"],
                                   localKeywords=program["localKeywords"], code=program["code"],
                                   vertexChannelMask=program["vertexChannelMask"],
                                   vertexChannels=program["vertexChannels"])
                    if ref["gpuProgramType"] != 4:
                        raise BridgeError("compiled program is not the GLES3 web-adapter language")
                    if self.compiler is None:
                        raise BridgeError("offline shader compiler was not supplied")
                    selected = {"pass": {k: deepcopy(v) for k, v in source_pass.items() if k != "programReferences"},
                                "reference": deepcopy(ref), "program": deepcopy(program),
                                "parameters": deepcopy(parameter),
                                "effectiveKeywords": sorted(set(program["keywords"]) | set(program["localKeywords"]))}
                    source_bytes = (self.directory / "programs" / Path(program["code"]["file"]).name).read_bytes()
                    conversion = publish_conversion(selected, source_bytes, self.compiler,
                        self.directory / "compiled", f"{self.prefix}/compiled")
                    variant.update(status="converted", conversion=conversion)
                except (BridgeError, StopIteration) as exc:
                    variant["error"] = str(exc) or "source program record is missing"
        catalogue["sourceErrors"] = [block["error"] for block in document["platforms"] if block.get("error")]
        if document.get("error"):
            catalogue["sourceErrors"].append(document["error"])
        if not catalogue["passes"]:
            catalogue["sourceErrors"].append("source shader declares no readable passes")
        return _publish(self.directory, self.prefix, canonical_bytes(catalogue), ".variants.json")

    def reference(self, record, path_id):
        key = (record.bundle, record.archive, path_id)
        if key not in self._references:
            if record.bundle not in self._receipts:
                self._receipts[record.bundle] = self.store.source_receipt(record.bundle)
            identity = object_identity(record, path_id, self._receipts[record.bundle])
            document = describe(record.tree(path_id), identity,
                lambda data, suffix: _publish(self.directory / "programs",
                    f"{self.prefix}/programs", data, suffix))
            reference = _publish(self.directory, self.prefix,
                                 canonical_bytes(document), ".shader.json")
            variants = self._catalogue(document, reference)
            self._references[key] = {**reference, "schemaVersion": SCHEMA_VERSION,
                                     "source": identity, "variants": variants}
        return deepcopy(self._references[key])


class ProgramSelectionError(ValueError):
    """The requested source program is unavailable or not uniquely identified."""


def select_program(document, *, subshader, pass_index, platform, program_type, keywords):
    """Resolve exact effective pass keywords, never a family or closest match.

    The caller supplies the effective source keyword state. No key is removed
    just because it is absent from the compiled corpus: stripping a variant is
    not evidence that the keyword is irrelevant. Two surviving identities are
    ambiguous even when their generated code happens to be identical.
    """
    if document.get("schemaVersion") != SCHEMA_VERSION or document.get("error"):
        raise ProgramSelectionError("source shader contract unavailable")
    passes = [p for p in document["passes"]
              if (p["subShaderIndex"], p["passIndex"]) == (subshader, pass_index)]
    if len(passes) != 1:
        raise ProgramSelectionError("source pass identity is not unique")
    blocks = [p for p in document["platforms"] if p["platform"] == platform]
    if len(blocks) != 1 or blocks[0].get("status") != "extracted":
        raise ProgramSelectionError("source compiler platform unavailable")
    programs = {p["record"]: p for p in blocks[0]["programs"]}
    known = set(document.get("keywordNames") or [])
    for block in document["platforms"]:
        for program in block["programs"]:
            known.update(program["keywords"])
            known.update(program["localKeywords"])
    requested = set(keywords)
    unknown = requested - known
    if unknown:
        raise ProgramSelectionError(f"keywords absent from source shader: {sorted(unknown)}")
    candidates = []
    for ref in passes[0]["programReferences"]:
        if ref["platform"] == platform and ref["gpuProgramType"] == program_type:
            if ref["record"] not in programs:
                raise ProgramSelectionError("declared source program is missing")
            candidates.append((ref, programs[ref["record"]]))
    effective = requested
    selected = [(r, p) for r, p in candidates
                if set(p["keywords"]) | set(p["localKeywords"]) == effective]
    if len(selected) != 1:
        reason = "missing" if not selected else "ambiguous"
        raise ProgramSelectionError(f"{reason} source variant for effective keywords {sorted(effective)}")
    ref, program = selected[0]
    parameters = next((p for p in blocks[0]["parameters"]
                       if p["record"] == ref["parameterRecord"]), None)
    return {"pass": deepcopy({k: v for k, v in passes[0].items() if k != "programReferences"}), "reference": deepcopy(ref),
            "program": deepcopy(program), "parameters": deepcopy(parameters),
            "effectiveKeywords": sorted(effective)}
