"""Synthetic contracts for the birthday-party waving-motion list.

The list is one ScriptableObject in a package of its own, so the reader's job is
to keep every row the asset carries and to say what it did when one character
has two rows.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sites.birthday_end_motions import ASSET_NAME, PACKAGE, parse_motion_list


def _tree(rows):
    return {"m_Name": ASSET_NAME, "BirthdayEndCharacterMotionDatas": rows}


def test_rows_ride_through_in_source_order():
    document = parse_motion_list(_tree([
        {"CharacterId": 6, "MotionName": "", "EyeName": "smile", "MouthName": "smile02"},
        {"CharacterId": 3, "MotionName": "w_cute", "EyeName": "normal", "MouthName": "normal"},
    ]))
    assert document["source"]["package"] == PACKAGE
    assert [row["CharacterId"] for row in document["rows"]] == [6, 3]
    assert document["byCharacterId"]["6"] == {
        "CharacterId": 6, "MotionName": "", "EyeName": "smile", "MouthName": "smile02"}
    assert document["summary"] == {"rows": 2, "characters": 2, "repeatedRows": 0,
                                   "emptyMotionNames": 1}


def test_an_empty_motion_name_is_a_value_not_an_omission():
    # The asset can carry an empty motion name; a reader that dropped it would
    # say the asset never wrote the field, which is a different fact.
    document = parse_motion_list(_tree([
        {"CharacterId": 6, "MotionName": "", "EyeName": "smile", "MouthName": "smile02"}]))
    row = document["byCharacterId"]["6"]
    assert row["MotionName"] == ""
    assert "MotionName" in row
    assert document["summary"]["emptyMotionNames"] == 1


def test_a_repeated_character_keeps_the_first_row_and_counts_the_rest():
    document = parse_motion_list(_tree([
        {"CharacterId": 6, "MotionName": "first", "EyeName": "a", "MouthName": "b"},
        {"CharacterId": 6, "MotionName": "second", "EyeName": "c", "MouthName": "d"},
    ]))
    assert document["byCharacterId"]["6"]["MotionName"] == "first"
    assert document["summary"]["repeatedRows"] == 1
    assert document["summary"]["rows"] == 2


@pytest.mark.parametrize("row", [
    {"CharacterId": "6", "MotionName": "", "EyeName": "smile", "MouthName": "smile02"},
    {"CharacterId": 6, "MotionName": None, "EyeName": "smile", "MouthName": "smile02"},
    {"CharacterId": 6, "MotionName": "", "EyeName": "smile"},
    {"CharacterId": True, "MotionName": "", "EyeName": "smile", "MouthName": "smile02"},
    "not an object",
])
def test_an_incomplete_row_is_an_error(row):
    with pytest.raises(ValueError):
        parse_motion_list(_tree([row]))


def test_a_tree_without_the_row_array_is_an_error():
    with pytest.raises(ValueError, match="BirthdayEndCharacterMotionDatas"):
        parse_motion_list({"m_Name": ASSET_NAME})


def test_the_bundle_name_is_claimed_by_the_exact_name_only():
    from core.assets.router import BIRTHDAY_END_MOTION_PACKAGE, route

    assert route(BIRTHDAY_END_MOTION_PACKAGE).domain == "birthday-end-motion"
    assert route(BIRTHDAY_END_MOTION_PACKAGE).extractor == "birthday-end-character-motions"
    for neighbour in (BIRTHDAY_END_MOTION_PACKAGE + "_extra",
                      "mysekai__birthday__airi_2026",
                      "mysekai__birthday__"):
        assert route(neighbour) is None, neighbour
