"""Reproducible product adaptation of the source player's switch gesture.

This is deliberately NOT a Humanoid furniture action. Moly's player uses an
SD104 body whereas the original switch gesture uses the audience skeleton.
Only the previously approved global-rest-delta rotation mapping is adapted;
no translation, mesh, root motion or guessed contact/IK is introduced.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
from pathlib import Path
import struct

from core.atomic import PUBLIC_FILE_MODE, write_json
from core.gltf import GLB

SOURCE_CLIP = "c_000_mov_fixture_action_01_01"
OUTPUT_CLIP = "product_sd104_switch_from_c_000_mov_fixture_action_01_01"
DURATION = struct.unpack("<f", struct.pack("<f", 1.0000001192092896))[0]
PAIRS = {
    "Root": "Root",
    "Root/Hips": "Root/Hips",
    "Root/Hips/Spine": "Root/Hips/Spine",
    "Root/Hips/Spine/Head": "Root/Hips/Spine/Spine1/Head",
    "Root/Hips/UpLeg_L": "Root/Hips/LeftUpLeg",
    "Root/Hips/UpLeg_L/Leg_L": "Root/Hips/LeftUpLeg/LeftLeg",
    "Root/Hips/UpLeg_L/Leg_L/Foot_L": "Root/Hips/LeftUpLeg/LeftLeg/LeftFoot",
    "Root/Hips/UpReg_R": "Root/Hips/RightUpLeg",
    "Root/Hips/UpReg_R/Reg_R": "Root/Hips/RightUpLeg/RightLeg",
    "Root/Hips/UpReg_R/Reg_R/Foot_R": "Root/Hips/RightUpLeg/RightLeg/RightFoot",
    "Root/Hips/Spine/Arm_L": "Root/Hips/Spine/Spine1/LeftArm",
    "Root/Hips/Spine/Arm_L/ForeArm_L": "Root/Hips/Spine/Spine1/LeftArm/LeftForeArm",
    "Root/Hips/Spine/Arm_R": "Root/Hips/Spine/Spine1/RightArm",
    "Root/Hips/Spine/Arm_R/ForeArm_R": "Root/Hips/Spine/Spine1/RightArm/RightForeArm",
}
REST_ONLY = "Root/Hips/Spine/Spine1"


def read_glb(path):
    raw = Path(path).read_bytes()
    if len(raw) < 20 or raw[:4] != b"glTF" or struct.unpack_from("<II", raw, 4) != (2, len(raw)):
        raise ValueError("invalid glTF binary framing")
    offset, document, binary = 12, None, None
    while offset < len(raw):
        size, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        body = raw[offset:offset + size]
        if len(body) != size:
            raise ValueError("truncated glTF chunk")
        if kind == 0x4E4F534A:
            document = json.loads(body)
        elif kind == 0x004E4942:
            binary = body
        offset += size
    if document is None or binary is None:
        raise ValueError("glTF needs JSON and BIN chunks")
    return document, binary


def _accessor(document, binary, index):
    accessor = document["accessors"][index]
    if accessor["componentType"] != 5126 or "sparse" in accessor:
        raise ValueError("switch animation expects dense float accessors")
    width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[accessor["type"]]
    view = document["bufferViews"][accessor["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise ValueError("external switch buffers are not read")
    stride = view.get("byteStride", width * 4)
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    return [struct.unpack_from("<" + "f" * width, binary, start + i * stride)
            for i in range(accessor["count"])]


def _normal(q):
    length = math.sqrt(sum(value * value for value in q))
    if not math.isfinite(length) or length < 1e-10:
        raise ValueError("invalid rotation quaternion")
    return tuple(value / length for value in q)


def _mul(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (w * X + x * W + y * Z - z * Y, w * Y - x * Z + y * W + z * X,
            w * Z + x * Y - y * X + z * W, w * W - x * X - y * Y - z * Z)


def _inverse(q):
    x, y, z, w = _normal(q)
    return -x, -y, -z, w


def _slerp(a, b, alpha):
    a, b = _normal(a), _normal(b)
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = tuple(-value for value in b), -dot
    if dot > 0.9995:
        return _normal(tuple(x + alpha * (y - x) for x, y in zip(a, b)))
    angle = math.acos(min(1., dot))
    left, right = math.sin((1 - alpha) * angle), math.sin(alpha * angle)
    return _normal(tuple(left * x + right * y for x, y in zip(a, b)))


def sample_rotation(times, values, interpolation, at):
    if not times or len(times) != (len(values) // 3 if interpolation == "CUBICSPLINE" else len(values)):
        raise ValueError("inconsistent animation sampler")
    index = max(0, min(len(times) - 1, bisect.bisect_right(times, at) - 1))
    value = values[index * 3 + 1] if interpolation == "CUBICSPLINE" else values[index]
    if index == len(times) - 1 or at <= times[0] or interpolation == "STEP":
        return _normal(value)
    span = times[index + 1] - times[index]
    if span <= 0:
        raise ValueError("non-increasing switch sample times")
    alpha = (at - times[index]) / span
    if interpolation == "LINEAR":
        return _slerp(value, values[index + 1], alpha)
    if interpolation != "CUBICSPLINE":
        raise ValueError(f"unsupported switch interpolation: {interpolation}")
    t2, t3 = alpha * alpha, alpha * alpha * alpha
    coeff = (2 * t3 - 3 * t2 + 1, t3 - 2 * t2 + alpha,
             -2 * t3 + 3 * t2, t3 - t2)
    tangent_out, next_value, tangent_in = values[index * 3 + 2], values[(index + 1) * 3 + 1], values[(index + 1) * 3]
    return _normal(tuple(coeff[0] * value[i] + coeff[1] * span * tangent_out[i]
                         + coeff[2] * next_value[i] + coeff[3] * span * tangent_in[i] for i in range(4)))


def _skeleton(document, root_name):
    nodes = document["nodes"]
    candidates = [i for i in document["scenes"][document.get("scene", 0)]["nodes"]
                  if nodes[i].get("name") == root_name]
    if len(candidates) != 1:
        raise ValueError(f"expected one instantiated source skeleton: {root_name}")
    paths, parents, rest, order = {}, {}, {}, []
    def visit(index, path, parent):
        if index in parents or path in paths:
            raise ValueError("ambiguous or cyclic instantiated skeleton")
        paths[path] = index
        parents[index] = parent
        order.append(index)
        local = _normal(nodes[index].get("rotation", [0., 0., 0., 1.]))
        rest[index] = _mul(rest[parent], local) if parent is not None else local
        for child in nodes[index].get("children", []):
            visit(child, path + "/" + nodes[child]["name"], index)
    visit(candidates[0], root_name, None)
    return paths, parents, rest, order


def export_sd_switch(source_glb, target_glb, binding_glb, output):
    source, binary = read_glb(source_glb)
    target, _ = read_glb(target_glb)
    binding, _ = read_glb(binding_glb)
    source_paths, source_parents, source_rest, source_order = _skeleton(source, "audience")
    target_name = "mdl_sd_104_001"
    target_paths, target_parents, target_rest, target_order = _skeleton(target, target_name)
    roots = binding["scenes"][binding.get("scene", 0)]["nodes"]
    if len(roots) != 1:
        raise ValueError("ambiguous shared animation binding root")
    binding_name = binding["nodes"][roots[0]]["name"]
    animations = [a for a in source.get("animations", []) if a.get("name") == SOURCE_CLIP]
    if len(animations) != 1:
        raise ValueError("the original switch clip is missing or ambiguous")
    animation = animations[0]
    curves, interpolations = {}, set()
    for channel in animation["channels"]:
        if channel["target"]["path"] != "rotation":
            continue
        node = channel["target"]["node"]
        if node in curves:
            raise ValueError("duplicate source rotation channel")
        sampler = animation["samplers"][channel["sampler"]]
        times = [v[0] for v in _accessor(source, binary, sampler["input"])]
        mode = sampler.get("interpolation", "LINEAR")
        curves[node] = (times, _accessor(source, binary, sampler["output"]), mode)
        interpolations.add(mode)
    if not curves or abs(max(times[-1] for times, _, _ in curves.values()) - DURATION) > 1e-5:
        raise ValueError("source switch duration differs from the owned one-second gesture")
    mapped = {target_paths[target_name + "/" + destination]: source_paths["audience/" + origin]
              for origin, destination in PAIRS.items()}
    rest_only = target_paths[target_name + "/" + REST_ONLY]
    retained = set(mapped) | {rest_only}
    for node in list(retained):
        parent = target_parents[node]
        while parent is not None:
            retained.add(parent)
            parent = target_parents[parent]
    ordered = [index for index in target_order if index in retained]
    glb = GLB(generator="moly product simple-switch adapter v1")
    new_index = {node: i for i, node in enumerate(ordered)}
    for node in ordered:
        entry = {key: copy.deepcopy(value) for key, value in target["nodes"][node].items()
                 if key in ("name", "translation", "rotation", "scale")}
        if target_parents[node] is None:
            entry["name"] = binding_name
        children = [new_index[c] for c in target["nodes"][node].get("children", []) if c in retained]
        if children:
            entry["children"] = children
        glb.g["nodes"].append(entry)
    glb.g["scenes"][0]["nodes"] = [0]
    times = [DURATION * i / 60 for i in range(61)]
    output_tracks = {node: [] for node in ordered if node in mapped or node == rest_only}
    for at in times:
        source_global = {}
        for node in source_order:
            local = sample_rotation(*curves[node], at) if node in curves else _normal(source["nodes"][node].get("rotation", [0., 0., 0., 1.]))
            parent = source_parents[node]
            source_global[node] = _mul(source_global[parent], local) if parent is not None else local
        target_global = {}
        for node in ordered:
            parent = target_parents[node]
            if node in mapped:
                source_node = mapped[node]
                desired = _mul(_mul(source_global[source_node], _inverse(source_rest[source_node])), target_rest[node])
                local = _mul(_inverse(target_global[parent]), desired) if parent is not None else desired
            else:
                local = target["nodes"][node].get("rotation", [0., 0., 0., 1.])
            local = _normal(local)
            target_global[node] = _mul(target_global[parent], local) if parent is not None else local
            if node in output_tracks:
                previous = output_tracks[node][-1] if output_tracks[node] else None
                if previous is not None and sum(a * b for a, b in zip(previous, local)) < 0:
                    local = tuple(-v for v in local)
                output_tracks[node].append(local)
    input_id = glb.acc(struct.pack("<" + "f" * len(times), *times), 5126, "SCALAR", len(times), minmax=([0.], [DURATION]))
    samplers, channels = [], []
    for node, values in output_tracks.items():
        output_id = glb.acc(b"".join(struct.pack("<4f", *q) for q in values), 5126, "VEC4", len(values))
        samplers.append({"input": input_id, "output": output_id, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": new_index[node], "path": "rotation"}})
    glb.g["animations"] = [{"name": OUTPUT_CLIP, "samplers": samplers, "channels": channels,
                            "extras": {"sourceClipName": SOURCE_CLIP, "loopTime": False}}]
    provenance = {"adaptation": "product-owned-simple-global-rest-delta-rotation-only-v1",
                  "sourceClipName": SOURCE_CLIP, "sourcePackage": "mysekai__player__motion__unique",
                  "sourceGlbSha256": hashlib.sha256(Path(source_glb).read_bytes()).hexdigest(),
                  "targetModelSha256": hashlib.sha256(Path(target_glb).read_bytes()).hexdigest(),
                  "bindingReferenceGlbSha256": hashlib.sha256(Path(binding_glb).read_bytes()).hexdigest(),
                  "targetUnitId": 4, "bindingReferenceRoot": binding_name,
                  "durationSeconds": DURATION, "sampleCount": len(times),
                  "sourceRotationInterpolation": sorted(interpolations), "outputRotationTracks": len(channels),
                  "pairs": PAIRS, "omitted": ["translation", "scale", "hands and fingers", "IK and contact", "root motion"]}
    glb.g["asset"]["extras"] = provenance
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = output.with_name(output.name + ".tmp")
    glb.save(str(staged))
    staged.replace(output)
    write_json(output.with_suffix(".source.json"), provenance, mode=PUBLIC_FILE_MODE)
    return provenance
