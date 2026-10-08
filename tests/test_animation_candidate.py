"""Small safety gates for a non-production, explicitly lossy candidate tool."""
import json
import math
import struct

import pytest

from chara.animation_candidate import accessor_bytes, encode_bytes, encode_candidate, parse_glb
from core.gltf import GLB


def sample_glb():
    glb = GLB()
    glb.g["nodes"] = [{"name": "Root"}]
    times = glb.acc(struct.pack("<2f", 0., 1.), 5126, "SCALAR", 2, minmax=([0.], [1.]))
    rotation = glb.acc(struct.pack("<8f", 0.1, 0.2, 0.3, math.sqrt(.86),
                                   -0.1, -0.2, -0.3, -math.sqrt(.86)), 5126, "VEC4", 2)
    translation = glb.acc(struct.pack("<6f", 1., 2., 3., -1., -2., -3.), 5126, "VEC3", 2)
    glb.g["animations"] = [{"name": "identity-preserved", "samplers": [
        {"input": times, "output": rotation, "interpolation": "LINEAR"},
        {"input": times, "output": translation, "interpolation": "LINEAR"}], "channels": [
        {"sampler": 0, "target": {"node": 0, "path": "rotation"}},
        {"sampler": 1, "target": {"node": 0, "path": "translation"}}]}]
    glb.g["buffers"] = [{"byteLength": len(glb.bin)}]
    return glb.g, bytes(glb.bin)


def pack(document, binary):
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\0" * (-len(binary) % 4)
    return (b"glTF" + struct.pack("<II", 2, 28 + len(encoded) + len(binary))
            + struct.pack("<II", len(encoded), 0x4e4f534a) + encoded
            + struct.pack("<II", len(binary), 0x004e4942) + binary)


def test_linear_rotation_only_and_identity_time_translation_sign_preserved():
    source, binary = sample_glb()
    result, report = encode_bytes(pack(source, binary))
    candidate, encoded = parse_glb(result)
    assert candidate["animations"] == source["animations"]
    assert candidate["nodes"] == source["nodes"]
    assert candidate["accessors"][1]["componentType"] == 5122
    assert candidate["accessors"][1]["normalized"] is True
    assert all(accessor_bytes(candidate, encoded, i)[0] == accessor_bytes(source, binary, i)[0]
               for i in (0, 2))
    values = list(struct.iter_unpack("<4h", accessor_bytes(candidate, encoded, 1)[0]))
    assert all(a > 0 and b < 0 for a, b in zip(*values))
    assert report["quantizedKeys"] == 2
    assert report["maxMeasuredKeyAngleDegrees"] <= .01
    assert report["maxMeasuredNormSquaredError"] <= 2e-4


@pytest.mark.parametrize("interpolation", ["STEP", "CUBICSPLINE"])
def test_non_linear_output_remains_unchanged(interpolation):
    source, binary = sample_glb()
    extra = dict(source["accessors"][1], bufferView=len(source["bufferViews"]))
    payload = accessor_bytes(source, binary, 1)[0]
    if interpolation == "CUBICSPLINE":
        # Two keys, with separate incoming/value/outgoing entries retained verbatim.
        payload *= 3
        extra["count"] *= 3
    source["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(payload)})
    source["accessors"].append(extra)
    binary += payload
    source["buffers"][0]["byteLength"] = len(binary)
    animation = source["animations"][0]
    animation["samplers"].append({"input": 0, "output": 3, "interpolation": interpolation})
    animation["channels"].append({"sampler": 2, "target": {"node": 0, "path": "rotation"}})
    candidate, report = encode_bytes(pack(source, binary))
    document, encoded = parse_glb(candidate)
    assert document["accessors"][3]["componentType"] == 5126
    assert accessor_bytes(document, encoded, 3)[0] == payload
    assert report["skippedRotationAccessors"] == [{"accessor": 3, "reason": "not-exclusive-LINEAR-rotation"}]


def test_mixed_usage_is_not_quantized():
    source, binary = sample_glb()
    source["animations"][0]["samplers"].append({"input": 0, "output": 1, "interpolation": "STEP"})
    source["animations"][0]["channels"].append({"sampler": 2, "target": {"node": 0, "path": "rotation"}})
    with pytest.raises(ValueError, match="no eligible"):
        encode_bytes(pack(source, binary))


def test_nonfinite_and_excessive_errors_are_rejected():
    source, binary = sample_glb()
    with pytest.raises(ValueError, match="error exceeds limit"):
        encode_bytes(pack(source, binary), max_angle_degrees=1e-10)
    offset = source["bufferViews"][1]["byteOffset"]
    binary = binary[:offset] + struct.pack("<f", float("nan")) + binary[offset + 4:]
    with pytest.raises(ValueError, match="non-finite"):
        encode_bytes(pack(source, binary))


@pytest.mark.parametrize("bad_index", [-1, True, 9999])
@pytest.mark.parametrize("location", ["view", "input", "output", "sampler"])
def test_invalid_indices_are_rejected(bad_index, location):
    source, binary = sample_glb()
    if location == "view":
        source["accessors"][1]["bufferView"] = bad_index
    elif location == "sampler":
        source["animations"][0]["channels"][0]["sampler"] = bad_index
    else:
        source["animations"][0]["samplers"][0][location] = bad_index
    with pytest.raises(ValueError):
        encode_bytes(pack(source, binary))


def test_opt_in_and_new_distinct_output_report_paths_required(tmp_path):
    source = tmp_path / "source.glb"
    original = pack(*sample_glb())
    source.write_bytes(original)
    output = tmp_path / "candidate.glb"
    with pytest.raises(ValueError, match="explicit lossy"):
        encode_candidate(source, output)
    with pytest.raises(FileExistsError):
        encode_candidate(source, source, allow_lossy=True)
    with pytest.raises(FileExistsError):
        encode_candidate(source, output, report_path=source, allow_lossy=True)
    with pytest.raises(FileExistsError):
        encode_candidate(source, output, report_path=output, allow_lossy=True)
    encode_candidate(source, output, allow_lossy=True)
    assert output.is_file() and output.with_suffix(".encoding.json").is_file()
    with pytest.raises(FileExistsError):
        encode_candidate(source, output, allow_lossy=True)
    assert source.read_bytes() == original
