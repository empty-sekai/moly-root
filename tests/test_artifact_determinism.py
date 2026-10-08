"""Concrete emitter reproducibility: object order is not authored array order."""
import json
from pathlib import Path
import random
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from core.atomic import json_bytes, write_json
from core.gltf import GLB
from core import jsonio


def reordered(value, seed):
    rng = random.Random(seed)
    def visit(item):
        if isinstance(item, dict):
            keys = list(item)
            rng.shuffle(keys)
            return {key: visit(item[key]) for key in keys}
        if isinstance(item, list):
            return [visit(row) for row in item]
        return item
    return visit(value)


@pytest.mark.parametrize("seed", [1, 11, 59])
def test_json_artifacts_are_identical_across_nested_mapping_order_and_paths(tmp_path, seed):
    facts = {"name": "雪ミク", "authoredSteps": [{"target": 2, "time": 0.5}, {"target": 1, "time": 0.0}],
             "properties": {"enabled": True, "binding": None, "translation": [2.0, -0.0, 1.0]}}
    first, second = tmp_path / "cn" / "output.json", tmp_path / "jp" / "moved" / "output.json"
    write_json(first, facts)
    write_json(second, reordered(facts, seed))
    assert first.read_bytes() == second.read_bytes() == json_bytes(facts)
    assert "雪ミク".encode() in first.read_bytes()
    assert json.loads(first.read_bytes()) == facts
    assert json_bytes({**facts, "authoredSteps": list(reversed(facts["authoredSteps"]))}) != first.read_bytes()


@pytest.mark.parametrize("seed", [2, 23, 71])
def test_glb_bytes_preserve_geometry_and_authored_array_order_across_mapping_order(seed):
    model = GLB()
    geometry = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    positions = model.acc(geometry, 5126, "VEC3", 3, target=34962, minmax=([0, 0, 0], [1, 1, 0]))
    model.g["meshes"] = [{"primitives": [{"attributes": {"POSITION": positions}, "mode": 4}]}]
    model.g["nodes"] = [{"name": "fixture", "mesh": 0, "extras": {"binding": "道具", "visible": True}},
                        {"name": "attach", "translation": [1, 2, 3]}]
    model.g["scenes"] = [{"nodes": [0, 1]}]
    original = model.blob()
    model.g = reordered(model.g, seed)
    assert model.blob() == original == model.blob()
    length = struct.unpack_from("<I", original, 12)[0]
    decoded = json.loads(original[20:20 + length])
    assert decoded == model.g
    binary_length = struct.unpack_from("<I", original, 20 + length)[0]
    assert original[28 + length:28 + length + binary_length] == geometry
    model.g["scenes"][0]["nodes"].reverse()
    assert model.blob() != original


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_json_and_glb_emitters_reject_nonfinite_facts(number):
    with pytest.raises(ValueError):
        json_bytes({"value": number})
    model = GLB()
    model.g["extras"] = {"value": number}
    with pytest.raises(ValueError):
        model.blob()


@pytest.mark.parametrize("seed", [3, 31, 73])
def test_domain_json_preserves_named_nonfinite_values_and_array_order_across_mapping_order(tmp_path, seed):
    facts = {"name": "雪ミク", "authoredSteps": [{"time": float("inf"), "index": 2},
             {"time": float("-inf"), "index": 1}], "properties": {"missing": None,
             "unresolved": float("nan"), "enabled": True, "levels": (2, 1)}}
    first, second = tmp_path / "cn/domain.json", tmp_path / "jp/moved/domain.json"
    jsonio.write_json(first, facts)
    jsonio.write_json(second, reordered(facts, seed))
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == (jsonio.dumps(facts) + "\n").encode("utf-8")
    decoded = json.loads(first.read_bytes(), parse_constant=lambda value: pytest.fail("bare nonfinite value: " + value))
    assert decoded == {"name": "雪ミク", "authoredSteps": [{"time": "Infinity", "index": 2},
                       {"time": "-Infinity", "index": 1}], "properties": {"missing": None,
                       "unresolved": "NaN", "enabled": True, "levels": [2, 1]}}
    assert jsonio.dumps({**facts, "authoredSteps": facts["authoredSteps"][::-1]}) != jsonio.dumps(facts)
