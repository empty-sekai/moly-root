from pathlib import Path
from types import SimpleNamespace

import pytest

from core.player_render_pipeline import (
    _fix_generated_string_arrays,
    _merge_native_monobehaviour_header,
    _strip_live_children,
    extract_mysekai_render_pipeline,
)


class _Node:
    def __init__(self, name, kind, children=()):
        self.m_Name = name
        self.m_Type = kind
        self.m_Children = list(children)

    def traverse(self):
        yield self
        for child in self.m_Children:
            yield from child.traverse()

    def to_dict_list(self):
        out = [{
            "m_Name": self.m_Name,
            "m_Type": self.m_Type,
            "m_Level": 0,
            "m_Children": self.m_Children,
        }]
        for child in self.m_Children:
            for row in child.to_dict_list():
                row = dict(row)
                row["m_Level"] += 1
                out.append(row)
        return out

    @classmethod
    def from_list(cls, rows):
        return SimpleNamespace(rows=rows)


def test_strip_live_children_removes_reconstruction_duplicate_source():
    rows = [
        {"m_Name": "root", "m_Children": [object()], "x": 1},
        {"m_Name": "field", "m_Children": [], "x": 2},
    ]
    assert _strip_live_children(rows) == [
        {"m_Name": "root", "x": 1},
        {"m_Name": "field", "x": 2},
    ]


def test_generated_string_array_outer_node_is_vector():
    element = _Node("data", "string")
    size = _Node("size", "int")
    array = _Node("Array", "Array", (size, element))
    values = _Node("PassNames", "string", (array,))
    root = _Node("Root", "Managed", (values,))

    assert _fix_generated_string_arrays(root) == 1
    assert values.m_Type == "vector"
    assert _fix_generated_string_arrays(root) == 0


def test_native_header_replaces_generated_header_and_keeps_script_fields():
    generated_header = [
        _Node("m_GameObject", "PPtr<GameObject>"),
        _Node("m_Enabled", "UInt8"),
        _Node("m_Script", "PPtr<MonoScript>"),
        _Node("m_Name", "string"),
    ]
    script_field = _Node("settings", "RenderObjectsSettings")
    original = _Node(
        "MyFeature", "MonoBehaviour", (*generated_header, script_field)
    )
    native = _Node(
        "MonoBehaviour",
        "MonoBehaviour",
        (
            _Node("m_GameObject", "PPtr<GameObject>"),
            _Node("m_Enabled", "UInt8"),
            _Node("m_Script", "PPtr<MonoScript>"),
            _Node("m_Name", "string"),
        ),
    )

    merged = _merge_native_monobehaviour_header(original, native)

    names = [row["m_Name"] for row in merged.rows]
    assert names.count("settings") == 1
    assert all("m_Children" not in row for row in merged.rows)


def test_native_header_mismatch_fails_closed():
    original = _Node(
        "Feature",
        "MonoBehaviour",
        (
            _Node("m_GameObject", "PPtr<GameObject>"),
            _Node("WRONG", "UInt8"),
        ),
    )
    native = _Node(
        "MonoBehaviour",
        "MonoBehaviour",
        (
            _Node("m_GameObject", "PPtr<GameObject>"),
            _Node("m_Enabled", "UInt8"),
        ),
    )
    with pytest.raises(ValueError, match="does not match native header"):
        _merge_native_monobehaviour_header(original, native)


def test_current_player_pipeline_when_source_is_supplied():
    import os

    root = os.environ.get("MOLY_PLAYER_SOURCE_ROOT")
    if not root:
        pytest.skip(
            "set MOLY_PLAYER_SOURCE_ROOT to a matching extracted player source"
        )

    root = Path(root)
    document = extract_mysekai_render_pipeline(
        root / "player-data",
        root / "types-decoded",
        root / "UnityDataAssetPack.apk",
        unity_version=os.environ.get(
            "MOLY_PLAYER_UNITY_VERSION", "2022.3.62f2"
        ),
        game_version=os.environ.get("MOLY_PLAYER_GAME_VERSION"),
    )

    pipeline = document["resources"]["mysekairenderpipelineasset"]["object"]
    assert pipeline["tree"]["m_RendererDataList"]
    assert document["rendererSlots"]
    first_key = document["rendererSlots"][0]["objectKey"]
    assert first_key in document["renderers"]
    features = document["renderers"][first_key]["features"]
    assert features
    assert all(
        feature["object"] is None or feature["object"]["script"]
        for feature in features
    )

    effect = [
        feature["object"]
        for feature in features
        if feature["object"] is not None
        and feature["object"]["script"].get("m_ClassName")
        == "MysekaiEffectFeature"
    ]
    assert len(effect) == 1
    settings = effect[0]["tree"]["settings"]
    assert isinstance(settings["Event"], int)
    assert isinstance(settings["filterSettings"]["PassNames"], list)
    assert settings["filterSettings"]["PassNames"]
