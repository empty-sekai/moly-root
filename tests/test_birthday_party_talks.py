"""Synthetic contracts for the birthday-party talk partition.

The party scripts ride in the ordinary talk document but are selected by a
master table of their own, so the two things worth pinning here are that the
ordinary corpus is not moved by them and that their voice cues are asked of the
bank the engine actually loads.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chara import talks


PARTY_SCRIPT = '''
label("line")
voice("talk", "voice_party_001_01_006", Characters.Alpha)
text("hello")
wait_click()
'''


def _master(tmp_path, scenarios):
    """A master directory with the ordinary talk tables and *scenarios*."""
    tmp_path.mkdir(parents=True, exist_ok=True)

    def write(name, rows):
        (tmp_path / (name + ".json")).write_text(
            json.dumps(rows), encoding="utf-8", newline="\n")

    write("mysekaiGameCharacterUnitGroups", [{"id": 1, "gameCharacterUnitId1": 12}])
    write("mysekaiCharacterTalkConditions", [
        {"id": 10, "mysekaiCharacterTalkConditionType": "mysekai_phenomena_id",
         "mysekaiCharacterTalkConditionTypeValue": 3},
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
        "id": 7, "mysekaiCharacterTalkId": 7, "mysekaiCharacterTalkTweetId": None,
    }])
    write("mysekaiCharacterTalkTweets", [])
    if scenarios is not None:
        write("birthdayPartyScenarios", scenarios)
    return tmp_path


def _assets():
    return {"talk_alpha.lua": PARTY_SCRIPT.replace("party_001", "talk_alpha"),
            "party_001.lua": PARTY_SCRIPT}


def test_the_party_block_names_each_script_with_its_voice_bank(tmp_path, monkeypatch):
    master = _master(tmp_path, [
        {"id": 1, "birthdayPartyId": 1, "seq": 1, "lua": "party_001"},
        {"id": 2, "birthdayPartyId": 1, "seq": 2, "lua": "party_002"},
    ])
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: {
        **_assets(), "party_002.lua": PARTY_SCRIPT.replace("party_001", "party_002")})
    out = tmp_path / "talks.json"
    report = talks.extract_talks(master, "synthetic.bundle", out)
    doc = json.loads(out.read_text(encoding="utf-8"))

    block = doc["birthdayParty"]
    assert block["status"] == "present"
    assert block["index"] == {"1": {"1": "party_001", "2": "party_002"}}
    assert sorted(block["talks"]) == ["party_001", "party_002"]
    assert block["talks"]["party_001"]["voiceBank"] == "mysekai/talk/birthday/voice/party_001"
    assert block["talks"]["party_001"]["voices"] == ["voice_party_001_01_006"]
    assert block["summary"]["scenarios"] == 2
    assert block["summary"]["scripts"] == 2
    assert report["birthdayParty"]["status"] == "present"
    # The bank is in the block's own semantics, not only in the reader.
    assert "mysekai/talk/birthday/voice/" in block["semantics"]["voiceBank"]


def test_the_party_block_is_the_document_key_the_ordinary_corpus_keeps(tmp_path, monkeypatch):
    # The ordinary corpus is the same bytes whether the partition has the party
    # table or not: the block is an added key and never a rewritten one.
    scripts = _assets()
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: scripts)
    with_party = _master(tmp_path / "with", [{"id": 1, "birthdayPartyId": 1,
                                              "seq": 1, "lua": "party_001"}])
    without = _master(tmp_path / "without", None)
    first = tmp_path / "with.json"
    second = tmp_path / "without.json"
    talks.extract_talks(with_party, "synthetic.bundle", first)
    talks.extract_talks(without, "synthetic.bundle", second)
    a = json.loads(first.read_text(encoding="utf-8"))
    b = json.loads(second.read_text(encoding="utf-8"))
    for key in ("units", "summary", "semantics", "version"):
        assert a[key] == b[key], key
    assert b["birthdayParty"]["status"] == "absent"
    assert b["birthdayParty"]["reason"]
    assert b["birthdayParty"]["talks"] == {}


def test_an_absent_party_table_is_stated_not_silently_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: _assets())
    master = _master(tmp_path, None)
    out = tmp_path / "talks.json"
    report = talks.extract_talks(master, "synthetic.bundle", out)
    assert report["birthdayParty"]["status"] == "absent"
    block = json.loads(out.read_text(encoding="utf-8"))["birthdayParty"]
    assert "birthdayPartyScenarios" in block["reason"]


def test_party_and_common_blocks_preserve_the_ordinary_corpus_keys(tmp_path, monkeypatch):
    # Additional families have their own blocks; ordinary corpus keys stay
    # present and ordered before them.
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: _assets())
    master = _master(tmp_path, [{"id": 1, "birthdayPartyId": 1, "seq": 1,
                                 "lua": "party_001"}])
    out = tmp_path / "talks.json"
    talks.extract_talks(master, "synthetic.bundle", out)
    document = json.loads(out.read_text(encoding="utf-8"))
    assert list(document) == ["version", "semantics", "units", "summary",
                              "birthdayParty", "commonSomeCharacter", "tutorial"]
    assert document["birthdayParty"]["semantics"]["table"].startswith(
        "birthdayPartyScenarios")


def test_a_party_row_whose_script_is_missing_is_an_error(tmp_path, monkeypatch):
    # A row that names a script the package does not carry is a broken input;
    # dropping the row would export a party that cannot be played and look
    # exactly like a party with no authored script.
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: _assets())
    master = _master(tmp_path, [{"id": 1, "birthdayPartyId": 1, "seq": 1,
                                 "lua": "party_003"}])
    with pytest.raises(LookupError, match="party_003"):
        talks.extract_talks(master, "synthetic.bundle", tmp_path / "talks.json")


def test_a_repeated_party_sequence_keeps_the_first_row(tmp_path, monkeypatch):
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: {
        **_assets(), "party_002.lua": PARTY_SCRIPT.replace("party_001", "party_002")})
    master = _master(tmp_path, [
        {"id": 1, "birthdayPartyId": 1, "seq": 1, "lua": "party_001"},
        {"id": 2, "birthdayPartyId": 1, "seq": 1, "lua": "party_002"},
    ])
    out = tmp_path / "talks.json"
    talks.extract_talks(master, "synthetic.bundle", out)
    block = json.loads(out.read_text(encoding="utf-8"))["birthdayParty"]
    assert block["index"]["1"]["1"] == "party_001"
    assert block["summary"]["shadowedRows"] == 1
    assert block["shadowedRows"][0]["lua"] == "party_002"


def test_attach_leaves_every_other_key_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: _assets())
    # The corpus is written from a partition without the party table, the way
    # an older run wrote it; attaching computes the block from one that has it.
    corpus = tmp_path / "talks.json"
    talks.extract_talks(_master(tmp_path / "old", None), "synthetic.bundle", corpus)
    original = corpus.read_bytes()
    document = json.loads(original)
    assert document["birthdayParty"]["status"] == "absent"
    master = _master(tmp_path / "new", [{"id": 1, "birthdayPartyId": 1,
                                         "seq": 1, "lua": "party_001"}])
    out = tmp_path / "attached.json"
    report = talks.attach_birthday_talks(master, "synthetic.bundle", corpus, out)
    after = json.loads(out.read_text(encoding="utf-8"))
    assert after["birthdayParty"]["status"] == "present"
    assert after["birthdayParty"]["talks"]["party_001"]["voices"] == ["voice_party_001_01_006"]
    assert report["preservedKeys"] == ["version", "semantics", "units", "summary",
                                       "commonSomeCharacter", "tutorial"]
    # Byte for byte: with the block taken back out, the attached document is
    # the corpus the extraction wrote, minus that run's own absent block.
    del after["birthdayParty"]
    del document["birthdayParty"]
    assert after == document
    assert corpus.read_bytes() == original


def test_attaching_with_other_constant_tables_is_refused(tmp_path, monkeypatch):
    # A party script read without the tables the ordinary corpus was resolved
    # against would carry source tokens where the others carry values: one
    # document, two readings of one step.  Refused rather than written.
    monkeypatch.setattr(talks, "_text_assets", lambda bundle: _assets())
    monkeypatch.setattr(talks, "read_constants", lambda lib: (
        {"Characters": {"Alpha": 12}}, {}, "defines.lua"))
    master = _master(tmp_path, [{"id": 1, "birthdayPartyId": 1, "seq": 1,
                                 "lua": "party_001"}])
    document = {"version": 1, "units": {}, "summary": {"constants": {"source": None}}}
    stale = tmp_path / "talks.json"
    stale.write_text(json.dumps(document), encoding="utf-8")
    out = tmp_path / "out.json"
    with pytest.raises(ValueError, match="resolved"):
        talks.attach_birthday_talks(master, "synthetic.bundle", stale, out,
                                    lib_bundle="lib.bundle")
    assert not out.exists()


def test_party_cues_are_asked_of_the_party_voice_bank():
    from phenomena.audio_corpus import voice_requests

    document = {"units": {"12": {"talks": [
        {"lua": "talk_alpha", "voices": ["voice_talk_alpha_01_001"]}]}},
        "birthdayParty": {"talks": {"party_001": {
            "voiceBank": "mysekai/talk/birthday/voice/party_001",
            "voices": ["voice_party_001_01_006"],
            "steps": []}}}}
    requests, cues = voice_requests(document)
    assert requests["mysekai/talk/birthday/voice/party_001"] == ["voice_party_001_01_006"]
    assert requests["mysekai/talk/voice/talk_alpha"] == ["voice_talk_alpha_01_001"]
    assert sorted(cues) == ["voice_party_001_01_006", "voice_talk_alpha_01_001"]


def test_a_party_script_without_a_voice_bank_is_refused():
    from phenomena.audio_corpus import voice_requests

    document = {"units": {}, "birthdayParty": {"talks": {"party_001": {
        "voices": ["voice_party_001_01_006"], "steps": []}}}}
    with pytest.raises(ValueError, match="voice bank"):
        voice_requests(document)
