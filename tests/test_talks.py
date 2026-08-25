"""Synthetic contracts for direct-talk extraction."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chara import talks


DEFINES = """
Characters = { Alpha = 12, Beta = 21 }
EyePresets = { normal = "eye-normal" }
LipSyncPresets = { smile = "mouth-smile" }
Motions = { calm = "mov_calm" }
Emoticons = { joy = "emo-joy" }
"""

# A constant table whose entries carry line comments, the shape the shipped
# tables are written in: a comment sits on its own line directly above the entry
# it annotates, so after a comma split the comment leads the next entry.
COMMENTED_DEFINES = """
Motions = {
    calm = "mov_calm",
    -- turning around
    turn45_r = "mov_turn45_r",
    turn45_l = "mov_turn45_l",
    -- added later
    rotate01 = "mov_rotate001",
}
"""

SCRIPT = '''
label("Alpha")
voice("talk", "cue.alpha", Characters.Alpha)
look_at_body(Characters.Alpha, Characters.Beta, 0.5)
change_npc_eye(Characters.Alpha, EyePresets.normal)
change_npc_mouth(Characters.Alpha, LipSyncPresets.smile)
change_animation(Characters.Alpha, Motions.calm, 0)
text("first line\\nsecond line")
wait_click()
'''


@pytest.fixture
def master_dir(tmp_path):
    def write(name, rows):
        (tmp_path / (name + ".json")).write_text(
            json.dumps(rows), encoding="utf-8", newline="\n"
        )

    write("mysekaiGameCharacterUnitGroups", [{"id": 1, "gameCharacterUnitId1": 12}])
    write("mysekaiCharacterTalkConditions", [
        {"id": 10, "mysekaiCharacterTalkConditionType": "mysekai_phenomena_id"},
    ])
    write("mysekaiCharacterTalkConditionGroups", [
        {"id": 10, "groupId": 20, "mysekaiCharacterTalkConditionId": 10},
    ])
    write("mysekaiCharacterTalks", [{
        "id": 7,
        "mysekaiGameCharacterUnitGroupId": 1,
        "mysekaiCharacterTalkConditionGroupId": 20,
        "mysekaiSiteGroupId": 4,
        "mysekaiCharacterTalkTermId": 1,
        "lua": "talk_alpha",
    }])
    write("mysekaiCharacterTalkPreActions", [{
        "id": 7,
        "mysekaiCharacterTalkId": 7,
        "mysekaiCharacterTalkTweetId": None,
    }])
    write("mysekaiCharacterTalkTweets", [])
    return tmp_path


def test_a_table_entry_annotated_by_a_line_comment_is_still_parsed():
    # A comment above an entry lands at the head of the next comma-separated
    # item, so a reader that takes everything before the `=` as the key gets
    # "-- turning around\n    turn45_r" and drops the entry -- silently, because
    # a key that is not an identifier is skipped rather than reported.  Red when
    # the comment is not removed before the key is read.
    tables = talks.parse_constant_tables(COMMENTED_DEFINES)
    assert tables["Motions"] == {
        "calm": "mov_calm",
        "turn45_r": "mov_turn45_r",
        "turn45_l": "mov_turn45_l",
        "rotate01": "mov_rotate001",
    }


def test_the_two_constant_table_parsers_read_the_same_entries():
    # The two readers of the same file exist for two callers, so a disagreement
    # between them means one caller resolves a constant the other leaves as a
    # source token -- the same step, read two ways.  Symmetric difference is the
    # gate; it is what exposed the comment case above.
    from chara import alone_actions

    for text in (DEFINES, COMMENTED_DEFINES):
        mine = talks.parse_constant_tables(text)
        theirs = alone_actions.parse_constant_tables(text)
        assert set(mine) == set(theirs), (sorted(mine), sorted(theirs))
        for name in mine:
            assert mine[name] == theirs[name], (
                name, sorted(set(mine[name]) ^ set(theirs[name])))


def test_parse_script_resolves_constants_and_zero_speed():
    parsed = talks.parse_script(SCRIPT, talks.parse_constant_tables(DEFINES),
                                script_name="synthetic.lua")
    assert parsed["callCount"] == 8
    steps = parsed["steps"]
    assert steps[0] == {"op": "label", "name": "Alpha"}
    assert steps[1]["cue"] == "cue.alpha"
    assert steps[1]["who"] == 12
    assert steps[2]["target"] == 21
    assert steps[3]["pattern"] == "eye-normal"
    assert steps[4]["pattern"] == "mouth-smile"
    assert steps[5]["motion"] == "mov_calm"
    assert steps[5]["speed"] == 0
    assert steps[5]["playbackSpeed"] == 1.0
    assert steps[6]["text"] == "first line\nsecond line"
    assert parsed["voices"] == ["cue.alpha"]


def test_parse_script_keeps_constants_when_defines_are_absent():
    parsed = talks.parse_script(
        'change_animation(Characters.Alpha, Motions.calm, 0)',
        {},
        script_name="no-defines.lua",
    )
    step = parsed["steps"][0]
    assert step["who"] == "Characters.Alpha"
    assert step["motion"] == "Motions.calm"
    assert step["alias"] == "Motions.calm"


def test_completeness_assertion_reports_script_and_difference():
    with pytest.raises(ValueError, match=r"broken\.lua.*difference"):
        talks.parse_script(
            'label("x")\ntext("y")\nwait_click()',
            {},
            script_name="broken.lua",
            _drop_ops={"text"},
        )


def test_extract_collects_voice_cues_and_null_tweet(monkeypatch, master_dir, tmp_path):
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: {
        "talk_alpha.lua": SCRIPT,
    })
    out = tmp_path / "talks.json"
    report = talks.extract_talks(master_dir, "synthetic.bundle", out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    talk = doc["units"]["12"]["talks"][0]
    assert talk["lua"] == "talk_alpha"
    assert talk["conditions"] == ["mysekai_phenomena_id"]
    assert talk["tweet"] is None
    assert talk["voices"] == ["cue.alpha"]
    assert report["voiceCues"] == 1
    assert report["uniqueVoiceCues"] == 1
    assert doc["summary"]["operations"]["change_animation"] == 1


def test_multiline_call_is_one_step():
    script = "change_animation(Characters.Alpha, Motions.calm,\n 0)"
    parsed = talks.parse_script(script, {}, script_name="multiline.lua")
    assert parsed["callCount"] == 1
    assert parsed["steps"][0]["op"] == "change_animation"


def test_all_known_operations_are_counted():
    script = """
    change_npc_eye(Characters.Alpha, EyePresets.normal)
    change_npc_mouth(Characters.Alpha, LipSyncPresets.smile)
    change_animation(Characters.Alpha, "mov_direct", 0)
    label("label")
    text("text")
    wait_click()
    look_at_body(Characters.Alpha, Characters.Beta, 0.5)
    voice("talk", "voice.cue", Characters.Alpha)
    wait_time(0.5, false)
    emoticon(Characters.Alpha, Emoticons.joy, 0, 2, true)
    look_at_fixture(Characters.Alpha)
    look_at_to_npc(837, Characters.Alpha, 0.5)
    fixture_voice("fixture.cue", 837)
    change_fixture_character_eye(837, EyePresets.normal)
    change_fixture_character_mouth(837, LipSyncPresets.smile)
    change_fixture_timeline(837, "timeline")
    show_fixture_emoticon(837, Emoticons.joy)
    play_animation(Characters.Alpha, "mov_direct", 0)
    play_fixture_gimmick("gimmick")
    stop_fixture_gimmick("gimmick", 4)
    hide_emoticon(Characters.Alpha)
    hide_talk_window()
    show_talk_window()
    wait_time_on_auto_mode(3)
    """
    parsed = talks.parse_script(script, {}, script_name="all-ops.lua")
    assert parsed["callCount"] == len(parsed["steps"]) == len(talks.CALL_OPS)
    assert {step["op"] for step in parsed["steps"]} == set(talks.CALL_OPS)
