"""Lower an extracted GLES program's binding ABI, not its shader arithmetic.

The original body stays content-addressed. This adapter assigns explicit
locations, replaces standalone uniforms with reflected 16-byte slots, and
separates textures from samplers. Unsupported syntax fails before publication.
An external, pinned Naga compiler validates the lowered stages and their WGSL
roundtrip. Successful conversion is not a claim about renderer ownership,
sampler evidence, GPU precision or final-pixel equivalence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess

from .programs import canonical_bytes, digest, _publish

ABI_VERSION = 1
VERTEX_LOCATIONS = {"in_POSITION0": 0, "in_NORMAL0": 1, "in_TEXCOORD0": 2,
                    "in_COLOR0": 5, "in_TEXCOORD1": 8, "in_TEXCOORD2": 9}
_NUMERIC = {"float": ".x", "vec2": ".xy", "vec3": ".xyz", "vec4": "",
            "int": ".x", "uint": ".x", "bool": ".x", "ivec2": ".xy",
            "ivec3": ".xyz", "ivec4": "", "uvec2": ".xy", "uvec3": ".xyz", "uvec4": ""}


class BridgeError(ValueError):
    """An unsupported source construct or failed compiler validation."""


@dataclass(frozen=True)
class PreparedProgram:
    abi: dict
    stages: dict[str, str]


def _stage_body(text, stage):
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "#ifdef " + stage]
    if len(starts) != 1:
        raise BridgeError(f"expected one compiled {stage} stage, found {len(starts)}")
    start = starts[0]
    depth = 1
    for end in range(start + 1, len(lines)):
        if re.match(r"\s*#\s*if(?:def|ndef)?\b", lines[end]):
            depth += 1
        if re.match(r"\s*#\s*endif\b", lines[end]):
            depth -= 1
        if depth == 0:
            return "\n".join(lines[start + 1:end]) + "\n"
    raise BridgeError(f"unterminated compiled {stage} stage")


# The source uniform blocks whose members are plain numeric declarations. The
# engine fills UnityPerDraw per draw; UnityPerMaterial carries the material's
# own property values, so its members are marked with their block for the
# renderer to fill from the material.
FLAT_BLOCKS = ("UnityPerDraw", "UnityPerMaterial")
_BLOCK = re.compile(r"#if HLSLCC_ENABLE_UNIFORM_BUFFERS\s*\nUNITY_BINDING\(\d+\) uniform (\w+) \{\s*\n#endif\n"
                    r"(.*?)#if HLSLCC_ENABLE_UNIFORM_BUFFERS\s*\n\};\s*\n#endif", re.S)
_MEMBER = re.compile(r"^\s*UNITY_UNIFORM\s+(?:(?:highp|mediump|lowp)\s+)?\w+\s+(\w+)(?:\[\d+\])?\s*;\s*$")


def _uniforms(body):
    blocks = re.findall(r"#if HLSLCC_ENABLE_UNIFORM_BUFFERS\s*\nUNITY_BINDING\(\d+\) uniform (\w+) \{\s*\n#endif", body)
    # Other layouts include instancing arrays/structured buffers. They need
    # their own source ABI; they must not be flattened into this one.
    if any(name not in FLAT_BLOCKS for name in blocks):
        raise BridgeError(f"unsupported source uniform blocks: {blocks}")
    # Every member of a material block is counted from the program itself: a
    # line in the block that is not one plain declaration fails the program.
    membership = {}
    spans = _BLOCK.findall(body)
    if len(spans) != len(blocks):
        raise BridgeError(f"unterminated source uniform block: {blocks}")
    for name, text in spans:
        members = [line for line in text.splitlines() if line.strip()]
        parsed = [_MEMBER.match(line) for line in members]
        if not all(parsed):
            raise BridgeError(f"unparsed member of source uniform block {name}: "
                              f"{[line for line, match in zip(members, parsed) if not match]}")
        if name != "UnityPerDraw":
            membership.update({match[1]: name for match in parsed})
    body = re.sub(r"#if HLSLCC_ENABLE_UNIFORM_BUFFERS\s*\nUNITY_BINDING\(\d+\) uniform \w+ \{\s*\n#endif", "", body)
    body = re.sub(r"#if HLSLCC_ENABLE_UNIFORM_BUFFERS\s*\n\};\s*\n#endif", "", body)
    body = re.sub(r"(?m)^\s*UNITY_UNIFORM\s+", "uniform ", body)
    body = re.sub(r"(?m)^#version[^\n]*\n", "", body)
    fields = []

    def field(match):
        entry = {"type": match[2], "name": match[3],
                 "array": int(match[4]) if match[4] else None,
                 "precision": match[1] or None}
        if match[3] in membership:
            entry["block"] = membership[match[3]]
        fields.append(entry)
        return ""

    body = re.sub(r"(?m)^(?:UNITY_LOCATION\(\d+\) )?uniform\s+(?:(highp|mediump|lowp)\s+)?(\w+)\s+(\w+)(?:\[(\d+)\])?\s*;", field, body)
    unknown = [line for line in body.splitlines()
               if re.search(r"\buniform\b", line) and not line.lstrip().startswith("#")]
    if unknown:
        raise BridgeError(f"unparsed source uniform declarations: {unknown}")
    # Only declarations with no reads are removed. Their names and types are
    # retained separately in the receipt; an active field is never defaulted.
    active, unused = [], []
    for entry in fields:
        target = active if re.search(r"\b" + re.escape(entry["name"]) + r"\b", body) else unused
        target.append(entry)
    return body, active, unused


def prepare(source: bytes, expected_sha256: str, *, bind_group=0) -> PreparedProgram:
    """Prepare both stages of one exact extracted program.

    Clip coordinates and texture V stay in the source GLES convention. The
    rendering adapter must explicitly account for both conventions; changing
    them here would contaminate same-API arithmetic comparisons.
    """
    if digest(source) != expected_sha256.lower():
        raise BridgeError("source program SHA-256 mismatch")
    if not isinstance(bind_group, int) or not 0 <= bind_group <= 3:
        raise BridgeError("invalid shader binding group")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BridgeError("compiled program is not UTF-8 GLES") from exc
    parts, fields, unused = {}, {}, {}
    for stage in ("vertex", "fragment"):
        body, declarations, ignored = _uniforms(_stage_body(text, stage.upper()))
        parts[stage] = body
        unused[stage] = ignored
        for entry in declarations:
            old = fields.get(entry["name"])
            if old and (old["type"], old["array"]) != (entry["type"], entry["array"]):
                raise BridgeError(f"cross-stage uniform type conflict: {entry['name']}")
            if not old:
                fields[entry["name"]] = {**entry, "stages": []}
            fields[entry["name"]]["stages"].append(stage)
    numeric = sorted((v for v in fields.values() if not v["type"].startswith("sampler")), key=lambda v: v["name"])
    textures = sorted((v for v in fields.values() if v["type"].startswith("sampler")), key=lambda v: v["name"])
    members, defines, offset = [], [], 0
    for index, entry in enumerate(numeric):
        kind, count = entry["type"], entry["array"]
        if kind not in _NUMERIC or (count is not None and (kind != "vec4" or count < 1)):
            raise BridgeError(f"unsupported uniform type: {entry}")
        integer = kind in ("int", "uint", "bool") or kind.startswith(("ivec", "uvec"))
        member = f"moly_slot_{index}"
        members.append(f"    {'uvec4' if integer else 'vec4'} {member}" + (f"[{count}]" if count else "") + ";")
        expression = f"moly_source.{member}{_NUMERIC[kind]}"
        if kind == "bool":
            expression = f"({expression} != 0u)"
        elif integer:
            expression = f"{kind}({expression})"
        defines.append(f"#define {entry['name']} ({expression})")
        entry.update(member=member, offset=offset, bytes=16 * (count or 1))
        offset += entry["bytes"]
    if not numeric:
        raise BridgeError("source program without numeric uniforms needs a separate binding ABI")
    header = ["#version 450", f"layout(set={bind_group},binding=0,std140) uniform MolySourceUniforms {{",
              *members, "} moly_source;", *defines]
    for index, entry in enumerate(textures):
        if entry["type"] not in ("sampler2D", "sampler2DArray") or entry["array"] is not None:
            raise BridgeError(f"unsupported source texture dimension: {entry}")
        array = entry["type"] == "sampler2DArray"
        entry.update(textureBinding=1 + 2 * index, samplerBinding=2 + 2 * index,
                     dimension="2d-array" if array else "2d")
        header += [f"layout(set={bind_group},binding={entry['textureBinding']}) uniform texture{'2DArray' if array else '2D'} moly_texture_{index};",
                   f"layout(set={bind_group},binding={entry['samplerBinding']}) uniform sampler moly_sampler_{index};"]
        ctor = f"{entry['type']}(moly_texture_{index},moly_sampler_{index})"
        uv_type = "vec3" if array else "vec2"
        coordinates = (f"vec3(uv.xy,clamp(floor(uv.z+0.5),0.0,float(textureSize({ctor},0).z-1)))"
                       if array else "uv")
        header += [f"vec4 moly_sample_{index}({uv_type} uv) {{return texture({ctor},{coordinates});}}",
                   f"vec4 moly_sample_{index}_lod({uv_type} uv,float lod) {{return textureLod({ctor},{coordinates},lod);}}",
                   "#ifdef FRAGMENT",
                   f"vec4 moly_sample_{index}({uv_type} uv,float bias) {{return texture({ctor},{coordinates},bias);}}", "#endif"]
        for stage, body in parts.items():
            for function, replacement in (("texture", f"moly_sample_{index}"), ("textureLod", f"moly_sample_{index}_lod")):
                body = re.sub(r"\b" + function + r"\(\s*" + re.escape(entry["name"]) + r"\s*,", replacement + "(", body)
            if re.search(r"\b" + re.escape(entry["name"]) + r"\b", body):
                raise BridgeError(f"unconsumed source sampler: {entry['name']}")
            parts[stage] = body
    varyings = sorted(set(re.findall(r"(?m)^(?:flat )?(?:in|out) (?:highp |mediump |lowp )?\w+ (vs_\w+);", "".join(parts.values()))))
    varying_locations = {name: index for index, name in enumerate(varyings)}
    interfaces, output = {}, {}
    for stage, body in parts.items():
        declarations = []

        def interface(match):
            flat, direction, precision, kind, name = match.groups()
            if name.startswith("vs_"):
                location = varying_locations[name]
            elif stage == "vertex" and direction == "in" and name in VERTEX_LOCATIONS:
                location = VERTEX_LOCATIONS[name]
            elif stage == "fragment" and direction == "out" and name == "SV_Target0":
                location = 0
            else:
                raise BridgeError(f"unmapped source interface: {stage} {match[0]}")
            entry = {"name": name, "direction": direction, "type": kind,
                     "precision": precision, "location": location,
                     "interpolation": "flat" if flat else "perspective"}
            declarations.append(entry)
            if name == "in_POSITION0":
                # The vertex buffer carries a vec3 position. A source that reads
                # it as vec4 gets w = 1 from the adapter; a source that declares
                # vec3 reads the attribute as it is.
                if kind == "vec3":
                    entry["adapter"] = "vec3-position"
                    return f"layout(location={location}) in vec3 in_POSITION0;"
                if kind != "vec4":
                    raise BridgeError("unexpected source position type")
                entry["adapter"] = "vec3-position-w-one"
                return f"layout(location={location}) in vec3 moly_position;\nvec4 in_POSITION0;"
            return f"layout(location={location}) {flat or ''}{direction} {kind} {name};"

        body = re.sub(r"(?m)^(flat )?(in|out) (?:(highp|mediump|lowp) )?(\w+) (\w+);", interface, body)
        # Colour outputs the compiler declared with an explicit location; a
        # pass that writes more than one render target declares one per target.
        explicit = r"layout\(location = (\d+)\) out (?:mediump |highp )?vec4 SV_Target(\d+);"
        for location, target in re.findall(explicit, body):
            if stage != "fragment":
                raise BridgeError("fragment output declared in vertex stage")
            if location != target:
                raise BridgeError(f"fragment output SV_Target{target} at location {location}")
            declarations.append({"name": f"SV_Target{target}", "direction": "out", "type": "vec4",
                                 "location": int(location), "precision": None, "interpolation": "perspective"})
        body = re.sub(explicit, lambda m: f"layout(location={m[1]}) out vec4 SV_Target{m[2]};", body)
        if stage == "vertex":
            position = [d for d in declarations if d["name"] == "in_POSITION0"]
            if not position:
                raise BridgeError("source position attribute is missing")
            if position[0]["adapter"] == "vec3-position-w-one":
                body, count = re.subn(r"void main\(\)\s*\{", "void main()\n{\n    in_POSITION0=vec4(moly_position,1.0);", body)
            else:
                count = len(re.findall(r"void main\(\)\s*\{", body))
            if count != 1:
                raise BridgeError("source vertex entry point is not unique")
        interfaces[stage] = declarations
        output[stage] = "\n".join(header) + "\n" + body
    vertex_varyings = {d["name"]: (d["type"], d["interpolation"]) for d in interfaces["vertex"] if d["direction"] == "out"}
    for d in interfaces["fragment"]:
        if d["direction"] == "in" and vertex_varyings.get(d["name"]) != (d["type"], d["interpolation"]):
            raise BridgeError(f"cross-stage interpolator mismatch: {d['name']}")
    abi = {"version": ABI_VERSION, "bindGroup": bind_group, "uniformBinding": 0,
           "uniformBytes": offset, "uniforms": numeric, "textures": textures,
           "interfaces": interfaces, "unusedUniforms": unused,
           "sourceClipSpace": "gl-minus-one-to-one", "textureDataOrigin": "unity-lower-left",
           "arrayLayerRule": "clamp(floor(layer+0.5),0,layers-1)",
           "arithmeticPrecision": "wgsl-f32; source precision qualifiers retained in receipt"}
    return PreparedProgram(abi, output)


class Compiler:
    """Invoke only the explicitly supplied offline compiler, without a shell."""

    def __init__(self, executable, *, timeout=120):
        self.executable = Path(executable).resolve(strict=True)
        if not self.executable.is_file():
            raise BridgeError("shader compiler must be an executable file")
        self.timeout = timeout
        self.executable_sha256 = digest(self.executable.read_bytes())

    def compile(self, prepared: PreparedProgram):
        request = {"schemaVersion": 1, "stages": [
            {"stage": stage, "source": source, "sourceSha256": digest(source.encode())}
            for stage, source in prepared.stages.items()]}
        try:
            result = subprocess.run([str(self.executable)], input=canonical_bytes(request),
                                    capture_output=True, check=False, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError(f"shader compiler failed: {type(exc).__name__}") from exc
        if result.returncode:
            raise BridgeError("shader compiler rejected program: " + result.stderr.decode("utf-8", "replace")[:4096])
        try:
            response = json.loads(result.stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise BridgeError("shader compiler returned an invalid receipt") from exc
        if response.get("schemaVersion") != 1 or response.get("compiler") != "naga 27.0.3":
            raise BridgeError("unsupported shader compiler receipt/version")
        stages = response.get("stages") or []
        if {s.get("stage") for s in stages} != set(prepared.stages) or len(stages) != 2:
            raise BridgeError("compiler receipt does not cover both source stages exactly once")
        for stage in stages:
            source = prepared.stages[stage["stage"]].encode()
            if stage.get("sourceSha256") != digest(source) or not stage.get("roundTripValidated"):
                raise BridgeError("compiler receipt does not validate the supplied stage")
            if digest(stage["wgsl"].encode()) != stage.get("wgslSha256"):
                raise BridgeError("compiler WGSL hash mismatch")
            if stage["stage"] == "vertex":
                adapter = stage.get("renderer") or {}
                if (adapter.get("schemaVersion") != 1 or not adapter.get("validated")
                        or adapter.get("sourceClipSpace") != "gl-minus-one-to-one"
                        or adapter.get("targetClipSpace") != "reverse-zero-to-one"
                        or adapter.get("operation") != "z=(w-z)*0.5"
                        or digest(adapter.get("wgsl", "").encode()) != adapter.get("wgslSha256")):
                    raise BridgeError("compiler renderer clip adapter is absent or invalid")
            expected = {u["member"]: u["offset"] for u in prepared.abi["uniforms"]}
            blocks = [b for b in stage["sourceBindings"] if b["group"] == prepared.abi["bindGroup"] and b["binding"] == 0]
            if (len(blocks) != 1 or blocks[0].get("bytes") != prepared.abi["uniformBytes"]
                    or {f["name"]: f["offset"] for f in blocks[0]["fields"]} != expected):
                raise BridgeError("compiler uniform reflection disagrees with the binding ABI")
        response["executableSha256"] = self.executable_sha256
        return response


def publish_conversion(selected, source, compiler, directory, prefix):
    """Publish a verified conversion receipt linked to the exact source pass."""
    prepared = prepare(source, selected["program"]["code"]["sha256"])
    compiled = compiler.compile(prepared)
    stages = {}
    for stage in compiled["stages"]:
        wgsl = stage.pop("wgsl").encode("utf-8")
        roundtrip = stage.pop("roundtripGlsl").encode("utf-8")
        if stage.get("renderer"):
            adapter = stage["renderer"]
            adapter["shader"] = _publish(Path(directory), prefix,
                adapter.pop("wgsl").encode("utf-8"), ".renderer.wgsl")
        stages[stage["stage"]] = {**stage,
            "shader": _publish(Path(directory), prefix, wgsl, ".wgsl"),
            "roundtrip": _publish(Path(directory), prefix, roundtrip, ".roundtrip.glsl")}
    document = {"schemaVersion": 1, "source": selected, "abi": prepared.abi,
                "stages": stages, "conversion": {k: v for k, v in compiled.items() if k != "stages"}}
    return _publish(Path(directory), prefix, canonical_bytes(document), ".program.json")
