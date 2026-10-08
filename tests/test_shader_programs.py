"""Compiled-program identity and exact selection; no proprietary test assets."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from shaders import programs


def document():
    return {"schemaVersion": 1, "name": "Same/DisplayName", "keywordNames": ["A", "B", "OTHER_PASS"],
            "passes": [{"subShaderIndex": 0, "passIndex": 0,
                        "programReferences": [
                            {"record": i, "platform": 9, "gpuProgramType": 4, "parameterRecord": 7}
                            for i in range(2)]}],
            "platforms": [{"platform": 9, "status": "extracted", "programs": [
                {"record": 0, "identity": "first", "keywords": ["A"], "localKeywords": []},
                {"record": 1, "identity": "second", "keywords": ["B"], "localKeywords": []}],
                "parameters": [{"record": 7, "bindings": [{"name": "source-sampler"}]}]}]}


def select(doc, keywords=("A",), **changes):
    return programs.select_program(doc, **dict(subshader=0, pass_index=0, platform=9,
                                               program_type=4, keywords=keywords, **changes))


def test_name_is_not_part_of_program_selection():
    doc = document()
    assert select(doc)["program"]["identity"] == "first"
    doc["name"] = "Completely/Different"
    assert select(doc)["program"]["identity"] == "first"
    assert select(doc, ["B"])["program"]["identity"] == "second"


def test_known_keywords_are_not_discarded_when_their_variant_was_stripped():
    with pytest.raises(programs.ProgramSelectionError, match="missing source variant"):
        select(document(), ["A", "OTHER_PASS"])
    with pytest.raises(programs.ProgramSelectionError, match="absent from source"):
        select(document(), ["A", "MISSPELLED"])


def test_closest_variant_is_never_selected():
    with pytest.raises(programs.ProgramSelectionError, match="missing source variant"):
        select(document(), ["A", "B"])


def test_duplicate_keyword_sets_remain_ambiguous_even_with_equal_code():
    doc = document()
    doc["platforms"][0]["programs"][1]["keywords"] = ["A"]
    with pytest.raises(programs.ProgramSelectionError, match="ambiguous source variant"):
        select(doc)


def test_unavailable_platform_is_not_an_empty_success():
    doc = document()
    doc["platforms"][0]["status"] = "unavailable"
    with pytest.raises(programs.ProgramSelectionError, match="platform unavailable"):
        select(doc)


def test_missing_referenced_program_is_an_error():
    doc = document()
    doc["platforms"][0]["programs"].pop()
    with pytest.raises(programs.ProgramSelectionError, match="declared source program is missing"):
        select(doc)


def test_selected_parameter_record_is_kept_and_result_does_not_mutate_source():
    doc = document()
    result = select(doc)
    assert result["parameters"]["bindings"] == [{"name": "source-sampler"}]
    result["program"]["keywords"].clear()
    assert doc["platforms"][0]["programs"][0]["keywords"] == ["A"]


def test_shader_identity_keeps_file_and_full_width_object_address():
    class Record:
        archive = "serialized-a"
    first = programs.object_identity(Record(), 9223372036854775701, {"bundle": "p", "sha256": "a", "bytes": 5})
    assert first["pathId"] == "9223372036854775701"
    Record.archive = "serialized-b"
    second = programs.object_identity(Record(), 9223372036854775701, first["package"])
    assert programs.digest(programs.canonical_bytes(first)) != programs.digest(programs.canonical_bytes(second))


def test_identical_program_content_deduplicates_without_overwriting(tmp_path):
    a = programs._publish(tmp_path, "shaders", b"shader body", ".glsl")
    b = programs._publish(tmp_path, "shaders", b"shader body", ".glsl")
    assert a == b and len(list(tmp_path.iterdir())) == 1
    (tmp_path / Path(a["file"]).name).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="conflicts"):
        programs._publish(tmp_path, "shaders", b"shader body", ".glsl")


def test_describe_keeps_segment_local_address_and_parameter_identity(monkeypatch):
    tree = {"m_ParsedForm": {"m_Name": "S", "m_PropInfo": {"m_Props": []},
            "m_SubShaders": [{"m_Passes": [{"m_State": {}, "m_Type": 0}]}]}}
    refs = [{"platform": 9, "record": 1, "subshader": 0, "pass": 0,
             "programBlock": "progVertex", "lightMode": "", "gpuProgramType": 4,
             "keywordIndices": [], "parameterRecord": 0}]
    records = [
        {"kind": "parameters", "offset": 28, "sourceOffset": 0, "segment": 1,
         "length": 4, "version": 202012090, "constantBuffers": [], "bindings": []},
        {"kind": "program", "offset": 32, "sourceOffset": 4, "segment": 1,
         "length": 4, "version": 202012090, "programType": 4, "keywords": [],
         "localKeywords": [], "code": b"body", "channelSourceMap": 3, "channels": [(0, 0)]}]
    monkeypatch.setattr(programs.objects, "subprograms", lambda _: refs)
    monkeypatch.setattr(programs.objects, "platform_segments", lambda _: [(9, (b"index", b"data"))])
    monkeypatch.setattr(programs.blob, "parse_segments", lambda *a: {"account": {"balanced": True}, "records": records})
    write = lambda data, suffix: {"file": "body" + suffix, "sha256": programs.digest(data), "bytes": len(data)}
    result = programs.describe(tree, {"pathId": "42"}, write)
    p = result["platforms"][0]["programs"][0]
    assert p["offset"] == 4 and p["segment"] == 1
    assert p["source"]["shader"] == {"pathId": "42"}
    assert result["passes"][0]["programReferences"][0]["parameterRecord"] == 0
    refs[0]["parameterRecord"] = 1
    failed = programs.describe(tree, {"pathId": "42"}, write)
    assert failed["platforms"][0]["status"] == "unavailable"
    assert failed["platforms"][0]["programs"] == []
    assert "invalid parameter record" in failed["platforms"][0]["error"]
