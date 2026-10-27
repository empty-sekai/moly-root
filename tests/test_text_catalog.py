"""Identity and official-text alignment tests with authored synthetic scripts."""
from copy import deepcopy

from chara.text_catalog import build_catalog, match_official


def document(texts=("Hello", "Again"), *, members=(1, 2)):
    steps = []
    for index, text in enumerate(texts):
        steps.extend([{"op": "label", "name": "Alpha"},
                      {"op": "voice", "who": 1, "cue": f"cue_{index}"},
                      {"op": "text", "text": text}, {"op": "wait_click"}])
    return {"commonSomeCharacter": {"talks": [{
        "talkId": 1, "lua": "sample", "unitIds": list(members), "steps": steps,
        "tweet": {"id": 1, "text": "Over here!"},
    }]}}


def catalog(doc, *, region="jp", previous=None):
    return build_catalog([doc], region=region, source_revision="sample-1", previous=previous)


def lines(value):
    return value["scripts"][0]["lines"]


def test_motion_insertion_changes_binding_not_text_identity():
    doc = document()
    before = catalog(doc)
    changed = deepcopy(doc)
    changed["commonSomeCharacter"]["talks"][0]["steps"].insert(0, {"op": "wait_time", "seconds": 2})
    after = catalog(changed, previous=before)
    assert [r["id"] for r in lines(after)] == [r["id"] for r in lines(before)]
    assert lines(after)[0]["step"] == lines(before)[0]["step"] + 1


def test_source_edit_keeps_voice_anchored_id_but_invalidates_translation_hash():
    before = catalog(document())
    after = catalog(document(("Changed", "Again")), previous=before)
    first_before, first_after = lines(before)[1], lines(after)[1]
    assert first_before["id"] == first_after["id"]
    assert first_before["sourceHash"] != first_after["sourceHash"]
    assert first_after["change"] == "changed"


def test_ambiguous_unvoiced_repetitions_after_edit_get_new_ids():
    doc = document()
    row = doc["commonSomeCharacter"]["talks"][0]
    row["steps"] = [{"op": "text", "text": "Same"}, {"op": "text", "text": "Same"}]
    before = catalog(doc)
    row["steps"].insert(1, {"op": "text", "text": "New"})
    after = catalog(doc, previous=before)
    assert not {r["id"] for r in lines(before)} & {r["id"] for r in lines(after)}
    assert len(after["scripts"][0]["retiredLines"]) == 2


def test_reimporting_same_catalogue_preserves_revision_and_retired_history():
    before = catalog(document())
    after = catalog(document(), previous=before)
    assert after["revision"] == before["revision"]


def test_same_script_can_have_distinct_tweet_bindings():
    doc = document()
    row = deepcopy(doc["commonSomeCharacter"]["talks"][0])
    row["talkId"] = 2
    row["tweet"] = {"id": 2, "text": "Hello there!"}
    doc["commonSomeCharacter"]["talks"].append(row)
    result = catalog(doc)
    assert len(result["scripts"]) == 3
    assert len(result["scripts"][0]["contents"]) == 2
    assert result["scripts"][0]["contents"][1]["tweetKey"] == "@tweet/2"


def test_official_text_requires_matching_cast_and_per_line_voice_sequence():
    source = catalog(document())
    donor = catalog(document(("你好", "再见")), region="cn")
    paired = match_official(source, donor, locale="zh-CN")
    assert [r["text"] for r in paired["entries"]] == ["你好", "再见"]
    wrong_cast = catalog(document(("你好", "再见"), members=(1, 3)), region="cn")
    assert not match_official(source, wrong_cast, locale="zh-CN")["entries"]
    wrong_voice = document(("你好", "再见"))
    wrong_voice["commonSomeCharacter"]["talks"][0]["steps"][1]["cue"] = "different"
    assert not match_official(source, catalog(wrong_voice, region="cn"), locale="zh-CN")["entries"]
