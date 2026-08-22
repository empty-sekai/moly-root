"""Master-table reading and the single-character talk predicate.

The predicate decides what belongs in one character's asset pack, so its two
halves are tested separately: a talk addressed to several characters is not one
character's, and a talk gated on furniture depends on something the pack does not
contain.  Both must drop the row, and the report must say which one did.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.master import FURNITURE_CONDITIONS, Master, MissingTable


def _write(directory, name, rows):
    with open(os.path.join(directory, f"{name}.json"), "w", encoding="utf-8", newline="\n") as h:
        json.dump(rows, h, ensure_ascii=False)


@pytest.fixture(name="master")
def _master(tmp_path):
    d = str(tmp_path)
    _write(d, "mysekaiGameCharacterUnitGroups", [
        {"id": 1, "gameCharacterUnitId1": 12},                        # 单人
        {"id": 2, "gameCharacterUnitId1": 12, "gameCharacterUnitId2": 21},   # 双人
    ])
    _write(d, "mysekaiCharacterTalkConditions", [
        {"id": 100, "mysekaiCharacterTalkConditionType": "mysekai_phenomena_id"},
        {"id": 200, "mysekaiCharacterTalkConditionType": "mysekai_fixture_id"},
    ])
    # 组表是映射表:一行一个 (组, 条件) 对,键是 groupId 而不是它自己的 id
    _write(d, "mysekaiCharacterTalkConditionGroups", [
        {"id": 1, "groupId": 10, "mysekaiCharacterTalkConditionId": 100},
        {"id": 2, "groupId": 20, "mysekaiCharacterTalkConditionId": 200},
    ])
    _write(d, "mysekaiCharacterTalks", [
        {"id": 1, "mysekaiGameCharacterUnitGroupId": 1, "mysekaiCharacterTalkConditionGroupId": 10,
         "lua": "talk_a", "assetbundleName": "b"},
        {"id": 2, "mysekaiGameCharacterUnitGroupId": 2, "mysekaiCharacterTalkConditionGroupId": 10,
         "lua": "talk_b", "assetbundleName": "b"},      # 多人组 -> 排除
        {"id": 3, "mysekaiGameCharacterUnitGroupId": 1, "mysekaiCharacterTalkConditionGroupId": 20,
         "lua": "talk_c", "assetbundleName": "b"},      # 家具条件 -> 排除
    ])
    _write(d, "mysekaiCharacterTalkPreActions", [{"id": 1, "mysekaiCharacterTalkId": 1,
                                                  "mysekaiCharacterTalkTweetId": 7}])
    _write(d, "mysekaiCharacterTalkTweets", [{"id": 7, "text": "x", "motionName": "m",
                                              "expressionEyeName": "normal",
                                              "expressionMouthName": "smile01"}])
    return Master(d)


def test_missing_table_names_itself(tmp_path):
    with pytest.raises(MissingTable) as err:
        Master(str(tmp_path)).table("gameCharacterUnits")
    assert "gameCharacterUnits" in str(err.value)


def test_only_single_character_groups_count_as_solo(master):
    assert master.solo_unit_groups() == {1: 12}


def test_condition_groups_are_keyed_by_group_not_by_row_id(master):
    # 用行自己的 id 当组 id 会把条件挂到错误的组上,家具门就漏判。
    assert master.condition_types() == {10: ["mysekai_phenomena_id"],
                                        20: ["mysekai_fixture_id"]}


def test_predicate_drops_both_kinds_and_says_which(master):
    kept, report = master.solo_talks()
    assert [row["talk"]["id"] for row in kept] == [1]
    assert kept[0]["unitId"] == 12 and kept[0]["tweetId"] == 7
    assert report["dropped"] == {"unit group holds more than one character": 1,
                                "gated on furniture": 1}
    assert report["kept"] == 1 and report["charactersCovered"] == 1
    assert report["withoutTweet"] == 0


def test_furniture_condition_set_is_the_documented_three(master):
    assert set(FURNITURE_CONDITIONS) == {"mysekai_fixture_id", "mysekai_fixture_tag_id",
                                        "after_set_fixture"}
