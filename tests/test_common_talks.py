"""Source-predicate probes over caller-supplied synthetic master tables.

The source accepts two through five members, tests only the fixture-id
condition, and requires a matching association. Keep the boundary cases
alongside positive rows so dropping the whole family cannot satisfy them.
"""
import json

from chara.common_talks import common_talks, select_common_talks
from core.master import Master
from phenomena.audio_corpus import voice_requests


def source(tmp_path):
    def write(name, rows):
        (tmp_path / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")

    groups = []
    for size in range(1, 6):
        groups.append({"id": size, **{f"gameCharacterUnitId{i}": i for i in range(1, size + 1)}})
    write("mysekaiGameCharacterUnitGroups", groups)
    kinds = ["mysekai_character_visit_count", "mysekai_fixture_id", "mysekai_fixture_tag_id"]
    write("mysekaiCharacterTalkConditions", [
        {"id": i, "mysekaiCharacterTalkConditionType": kind,
         "mysekaiCharacterTalkConditionTypeValue": 1}
        for i, kind in enumerate(kinds, 1)])
    write("mysekaiCharacterTalkConditionGroups", [
        {"id": i, "groupId": i, "mysekaiCharacterTalkConditionId": i} for i in range(1, 4)])
    rows = [
        {"id": identity, "mysekaiGameCharacterUnitGroupId": size,
         "mysekaiCharacterTalkConditionGroupId": condition,
         "mysekaiSiteGroupId": 1, "mysekaiCharacterTalkTermId": 1, "lua": f"sample_{identity}"}
        for identity, size, condition in [(1, 1, 1), (2, 2, 1), (3, 5, 1),
                                           (4, 2, 2), (5, 2, 3), (6, 2, 1)]]
    write("mysekaiCharacterTalks", rows)
    write("mysekaiCharacterTalkSomeCharacterTalks", [
        {"id": identity, "mysekaiCharacterTalkId": identity, "mainGameCharacterUnitId": 2}
        for identity in range(1, 6)])
    write("mysekaiCharacterTalkPreActions", [])
    write("mysekaiCharacterTalkTweets", [])
    return Master(tmp_path)


def test_native_membership_boundaries_and_exact_fixture_predicate(tmp_path):
    rows, report = select_common_talks(source(tmp_path))
    assert [row["talk"]["id"] for row in rows] == [2, 3, 5]
    assert rows[1]["unitIds"] == [1, 2, 3, 4, 5]
    assert report["withoutAssociation"] == [6]


def test_shared_parser_preserves_both_speakers_and_routes_common_voice_banks(tmp_path):
    scripts = {f"sample_{i}.lua": f'''label("Alpha")
voice("talk", "voice_mysekai_sample_{i}_01_001", Characters.Alpha)
text("First")
wait_click()
label("Beta")
voice("talk", "voice_mysekai_sample_{i}_02_002", Characters.Beta)
text("Second")
wait_click()
''' for i in [2, 3, 5]}
    block, report = common_talks(source(tmp_path), scripts, {"Characters": {"Alpha": 1, "Beta": 2}}, {})
    assert report["talks"] == 3
    assert [s["who"] for s in block["talks"][0]["steps"] if s["op"] == "voice"] == [1, 2]
    assert block["talks"][0]["someCharacterTalks"][0]["mainGameCharacterUnitId"] == 2
    assert "mainUnitId" not in block["talks"][0]
    requests, cues = voice_requests({"commonSomeCharacter": block})
    assert len(requests) == 3
    assert len(cues) == 6


def test_unrecognized_source_call_is_reported_before_export(tmp_path):
    import pytest
    scripts = {"sample_2.lua": 'new_source_operation("value")'}
    with pytest.raises(ValueError, match="unknown script operations"):
        common_talks(source(tmp_path), scripts, {}, {})
