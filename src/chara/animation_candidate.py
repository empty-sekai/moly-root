"""Opt-in lossy rotation candidate, never called by the production exporter.

Only isolated float32 LINEAR rotation outputs become glTF core SNORM16. Clip
identity, channel targets, timestamps and every other accessor payload survive.
No quaternion hemisphere changes, key removal, resampling or tangent conversion.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import struct

from .rotation_encoding import encode_rotation_stream

FORMATS = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
           5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _uint(value, field):
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {field}")
    return value


def _index(value, collection, field):
    index = _uint(value, field)
    if index >= len(collection):
        raise ValueError(f"out-of-range {field}")
    return index


def parse_glb(raw):
    if len(raw) < 28 or raw[:4] != b"glTF" or struct.unpack_from("<II", raw, 4) != (2, len(raw)):
        raise ValueError("expected one complete GLB version 2")
    chunks, offset = [], 12
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("truncated GLB chunk")
        length, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        if length % 4 or offset + length > len(raw):
            raise ValueError("invalid GLB chunk length")
        chunks.append((kind, raw[offset:offset + length]))
        offset += length
    if [kind for kind, _ in chunks] != [0x4e4f534a, 0x004e4942]:
        raise ValueError("candidate requires exactly JSON and BIN chunks")
    return json.loads(chunks[0][1]), chunks[1][1]


def _view_bytes(document, binary, view):
    if view.get("buffer") != 0:
        raise ValueError("only the embedded buffer is supported")
    start = _uint(view.get("byteOffset", 0), "bufferView byteOffset")
    size = _uint(view["byteLength"], "bufferView byteLength")
    if start + size > document["buffers"][0]["byteLength"]:
        raise ValueError("bufferView exceeds the source buffer")
    return binary[start:start + size]


def accessor_bytes(document, binary, index):
    accessor = document["accessors"][_index(index, document["accessors"], "accessor index")]
    if "sparse" in accessor or "extensions" in accessor:
        raise ValueError("sparse/extension accessors require a different candidate adapter")
    component, size = FORMATS[accessor["componentType"]]
    width = WIDTHS[accessor["type"]]
    count = _uint(accessor["count"], "accessor count")
    view = document["bufferViews"][_index(accessor["bufferView"], document["bufferViews"], "bufferView index")]
    raw = _view_bytes(document, binary, view)
    start = _uint(accessor.get("byteOffset", 0), "accessor byteOffset")
    stride = _uint(view.get("byteStride", width * size), "bufferView byteStride")
    if stride < width * size or (count and start + (count - 1) * stride + width * size > len(raw)):
        raise ValueError("accessor exceeds its bufferView")
    packed = b"".join(raw[start + i * stride:start + i * stride + width * size] for i in range(count))
    return packed, "<" + component * width


def encode_bytes(raw, *, max_angle_degrees=0.01, max_norm_squared_error=2e-4):
    if not 0 < max_angle_degrees <= 0.01 or not 0 < max_norm_squared_error <= 2e-4:
        raise ValueError("candidate limits may be stricter, never weaker than 0.01 degrees / 2e-4")
    source, binary = parse_glb(raw)
    if (source.get("asset", {}).get("version") != "2.0" or not source.get("animations")
            or any(source.get(key) for key in ("meshes", "images", "textures", "materials", "skins",
                                               "extensionsUsed", "extensionsRequired"))):
        raise ValueError("input must be a pure core-glTF actor animation library")
    if len(source.get("buffers", [])) != 1 or "uri" in source["buffers"][0]:
        raise ValueError("one embedded buffer is required")
    declared = _uint(source["buffers"][0]["byteLength"], "buffer byteLength")
    if declared > len(binary) or len(binary) - declared > 3:
        raise ValueError("source buffer length disagrees with BIN chunk")
    document = deepcopy(source)
    accessors, views = source["accessors"], source["bufferViews"]
    uses, view_users, packed = {}, {}, {}
    for index, accessor in enumerate(accessors):
        view_users.setdefault(accessor["bufferView"], []).append(index)
        payload, fmt = accessor_bytes(source, binary, index)
        if accessor["componentType"] == 5126:
            if any(not math.isfinite(value) for row in struct.iter_unpack(fmt, payload) for value in row):
                raise ValueError(f"non-finite source animation accessor {index}")
        packed[index] = payload
    for animation in source["animations"]:
        used_samplers = set()
        for channel in animation["channels"]:
            sampler_index = _index(channel["sampler"], animation["samplers"], "channel sampler")
            sampler = animation["samplers"][sampler_index]
            used_samplers.add(sampler_index)
            output_index = _index(sampler["output"], accessors, "sampler output")
            uses.setdefault(output_index, set()).add((channel["target"]["path"], sampler.get("interpolation", "LINEAR")))
        for index, sampler in enumerate(animation["samplers"]):
            input_index = _index(sampler["input"], accessors, "sampler input")
            output_index = _index(sampler["output"], accessors, "sampler output")
            uses.setdefault(input_index, set()).add(("time", "input"))
            if index not in used_samplers:
                uses.setdefault(output_index, set()).add(("unbound", "sampler"))
            timestamp = accessors[input_index]
            if timestamp["componentType"] != 5126 or timestamp["type"] != "SCALAR":
                raise ValueError("animation timestamps must remain float32 SCALAR")
    replacements, changed, skipped, sample_count = {}, [], [], 0
    max_angle = max_norm = 0.0
    for index, usage in sorted(uses.items()):
        if not any(path == "rotation" for path, _ in usage):
            continue
        accessor = accessors[index]
        view_id = accessor["bufferView"]
        view = views[view_id]
        reason = None
        if usage != {("rotation", "LINEAR")}:
            reason = "not-exclusive-LINEAR-rotation"
        elif accessor["componentType"] != 5126 or accessor["type"] != "VEC4" or accessor.get("normalized", False):
            reason = "not-float32-VEC4"
        elif (view_users[view_id] != [index] or accessor.get("byteOffset", 0) != 0
              or "byteStride" in view or view["byteLength"] != len(packed[index])):
            reason = "shared-or-nonpacked-bufferView"
        if reason:
            skipped.append({"accessor": index, "reason": reason})
            continue
        try:
            output, stats = encode_rotation_stream(packed[index],
                max_angle_degrees=max_angle_degrees, max_norm_squared_error=max_norm_squared_error)
        except ValueError as error:
            raise ValueError(f"rotation accessor {index}: {error}") from error
        max_angle = max(max_angle, stats["maxKeyAngleDegrees"])
        max_norm = max(max_norm, stats["maxNormSquaredError"])
        sample_count += stats["keys"]
        target = document["accessors"][index]
        target.update(componentType=5122, normalized=True)
        if "min" in target:
            target["min"] = stats["min"]
        if "max" in target:
            target["max"] = stats["max"]
        replacements[view_id] = output
        changed.append(index)
    if not changed:
        raise ValueError("no eligible LINEAR rotation output; no candidate produced")
    output_binary = bytearray()
    for index, view in enumerate(views):
        output_binary.extend(b"\0" * (-len(output_binary) % 4))
        payload = replacements.get(index)
        if payload is None:
            payload = _view_bytes(source, binary, view)
        document["bufferViews"][index].update(byteOffset=len(output_binary), byteLength=len(payload))
        output_binary.extend(payload)
    output_binary.extend(b"\0" * (-len(output_binary) % 4))
    document["buffers"][0]["byteLength"] = len(output_binary)
    for index in range(len(accessors)):
        if index not in changed and accessor_bytes(document, output_binary, index)[0] != packed[index]:
            raise ValueError(f"protected accessor changed: {index}")
    encoded_json = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    encoded_json += b" " * (-len(encoded_json) % 4)
    candidate = (b"glTF" + struct.pack("<II", 2, 28 + len(encoded_json) + len(output_binary))
                 + struct.pack("<II", len(encoded_json), 0x4e4f534a) + encoded_json
                 + struct.pack("<II", len(output_binary), 0x004e4942) + output_binary)
    return candidate, {"schema": "moly-actor-animation-candidate/1", "lossy": True,
        "productionDefaultChanged": False, "encoding": "rotation-snorm16-linear-only",
        "rounding": "nearest, ties to even; no quaternion sign canonicalization or source normalization",
        "maxAllowedAngleDegrees": max_angle_degrees, "maxAllowedNormSquaredError": max_norm_squared_error,
        "maxMeasuredKeyAngleDegrees": max_angle, "maxMeasuredNormSquaredError": max_norm,
        "animationsPreserved": len(source["animations"]),
        "channelsPreserved": sum(len(a["channels"]) for a in source["animations"]),
        "quantizedAccessors": len(changed), "quantizedKeys": sample_count,
        "protectedAccessorsByteIdentical": len(accessors) - len(changed),
        "accessorPooling": "not applied; existing sharing preserved", "skippedRotationAccessors": skipped,
        "sourceBytes": len(raw), "candidateBytes": len(candidate),
        "sourceSha256": hashlib.sha256(raw).hexdigest(), "candidateSha256": hashlib.sha256(candidate).hexdigest(),
        "validationScope": "source structure and key error only; Bevy/WASM playback and interpolated/FK error are separate gates"}


def encode_candidate(source, output, *, allow_lossy=False, report_path=None, **limits):
    if not allow_lossy:
        raise ValueError("explicit lossy rotation opt-in is required")
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    report_path = Path(report_path).resolve() if report_path else output.with_suffix(".encoding.json")
    if output == source or report_path in {source, output} or output.exists() or report_path.exists():
        raise FileExistsError("candidate and report must be new, distinct paths; overwriting is forbidden")
    candidate, report = encode_bytes(source.read_bytes(), **limits)
    report.update(source=str(source), candidate=str(output))
    companion = source.with_suffix(".index.json")
    if companion.is_file():
        report["unchangedSourceIndex"] = {"path": str(companion), "sha256": hashlib.sha256(companion.read_bytes()).hexdigest()}
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(candidate)
    with report_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return report
