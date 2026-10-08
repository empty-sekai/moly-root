"""Presentation metadata must not alter the furniture runtime contract."""
from __future__ import annotations

import json

import pytest

from fixtures.master_slice import export_fixture_master_slice


def fixture_row(**overrides):
    row = {
        "id": 157,
        "assetbundleName": "mdl_ext0002_fixture_fridge1",
        "mysekaiFixtureType": "normal",
        "mysekaiFixturePlayerActionType": "one_shot",
        "mysekaiFixturePutType": "none",
        "mysekaiFixtureHandleType": "none",
        "gridSize": {"width": 2, "depth": 2, "height": 4},
        "mysekaiFixturePutSoundId": 4,
    }
    row.update(overrides)
    return row


def export(tmp_path, rows):
    source = tmp_path / "master"
    source.mkdir()
    (source / "mysekaiFixtures.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8"
    )
    target = tmp_path / "mysekai-fixtures.json"
    report = export_fixture_master_slice(str(source), str(target))
    return json.loads(target.read_text(encoding="utf-8")), report


def test_localized_name_and_description_preserve_source_text(tmp_path):
    name = "冰箱"
    description = "第一行\n<color=#ff5599>第二行</color>"
    document, report = export(tmp_path, [fixture_row(name=name, flavorText=description)])
    row = document["fixtures"][0]
    assert row["name"] == name
    assert row["description"] == description
    assert row["assetbundleName"] == "mdl_ext0002_fixture_fridge1"
    assert row["playerActionTypeValue"] == 3
    assert (row["gridWidth"], row["gridDepth"], row["gridHeight"]) == (2, 2, 4)
    assert row["putSoundId"] == 4
    assert report["summary"]["playerActionType"] == {"one_shot": 1}


@pytest.mark.parametrize("optional", [{}, {"name": None, "flavorText": None}])
def test_missing_presentation_metadata_is_not_invented(tmp_path, optional):
    document, _ = export(tmp_path, [fixture_row(**optional)])
    assert document["fixtures"][0]["name"] is None
    assert document["fixtures"][0]["description"] is None


def test_empty_display_fields_are_preserved_not_replaced_by_asset_names(tmp_path):
    document, _ = export(tmp_path, [fixture_row(name="", flavorText="")])
    assert document["fixtures"][0]["name"] == ""
    assert document["fixtures"][0]["description"] == ""


@pytest.mark.parametrize("field", ["name", "flavorText"])
@pytest.mark.parametrize("value", [42, [], {}, False])
def test_invalid_display_field_types_are_rejected(tmp_path, field, value):
    with pytest.raises(ValueError, match=f"{field} must be text or null"):
        export(tmp_path, [fixture_row(**{field: value})])
    assert not (tmp_path / "mysekai-fixtures.json").exists()


def test_runtime_identity_and_order_do_not_depend_on_presentation(tmp_path):
    document, _ = export(tmp_path, [
        fixture_row(id=297, name="合成器", flavorText="合成器"),
        fixture_row(id=8, name="璀璨流星的椅子", flavorText=""),
    ])
    assert [row["id"] for row in document["fixtures"]] == [8, 297]
    assert all(row["fixtureTypeValue"] == 6 for row in document["fixtures"])
