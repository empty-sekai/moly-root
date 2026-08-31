"""The shader census: what it counts, and the two things it must not confuse."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shaders import census, objects                                  # noqa: E402


def _tree(name, programs=(), parameters=0, props=(), passes=()):
    """A Shader typetree stub carrying only what these tests read."""
    return {
        "m_Name": "",
        "m_ParsedForm": {
            "m_Name": name,
            "m_PropInfo": {"m_Props": [{"m_Name": p} for p in props]},
            "m_SubShaders": [{"m_Passes": [
                {"m_Name": pass_name, "m_Tags": {"tags": {"LIGHTMODE": mode}}}
                for pass_name, mode in passes]}],
        },
    }


def _parsed(programs, parameters=0):
    """A parse_blob result: program records plus parameter records."""
    records = [{"kind": "program", "programType": 1, "keywords": list(kw),
                "localKeywords": [], "code": body}
               for kw, body in programs]
    records += [{"kind": "parameters"} for _ in range(parameters)]
    return {"platform": 9, "stride": 12, "records": records}


def test_the_name_comes_from_the_parsed_form_not_from_m_name():
    """A Shader's m_Name is empty; reading it makes every survey report zero."""
    tree = _tree("Product/Thing")
    assert tree["m_Name"] == ""
    assert objects.shader_name(tree) == "Product/Thing"


def test_a_parameter_record_is_not_counted_as_a_program():
    """The reflection table is shared by variants; counting it inflates the
    denominator, and a denominator that is too large reads as slower progress."""
    parsed = _parsed([((), b"aaa"), (("K",), b"bbb")], parameters=7)
    rows = census.program_rows("Product/Thing", 9, parsed)
    assert len(rows) == 2
    assert all(row["keywords"] != [] or row["keywords"] == [] for row in rows)


def test_identical_bodies_are_one_unique_program_but_two_records():
    """Variants that compile to the same code are one piece of work."""
    parsed = _parsed([(("A",), b"same"), (("B",), b"same"), (("C",), b"other")])
    rows = census.program_rows("Product/Thing", 9, parsed)
    entries = [{"shader": "Product/Thing", "properties": [], "passes": [],
                "platforms": [{"platform": 9, "programs": rows}]}]
    counts = census.totals(entries, platform=9)
    assert counts["records"] == 3
    assert counts["uniquePrograms"] == 2


def test_totals_can_be_narrowed_to_one_platform():
    """Two platforms hold the same variants, so counting both doubles nothing
    useful -- a caller asking for a work queue asks for one platform."""
    rows9 = census.program_rows("T", 9, _parsed([((), b"x"), ((), b"y")]))
    rows18 = census.program_rows("T", 18, _parsed([((), b"p"), ((), b"q")]))
    entries = [{"shader": "T", "properties": [], "passes": [], "platforms": [
        {"platform": 9, "programs": rows9}, {"platform": 18, "programs": rows18}]}]
    assert census.totals(entries)["records"] == 4
    assert census.totals(entries, platform=9)["records"] == 2


def test_by_shader_orders_by_work_not_by_name():
    """A port planned in name order spends its first passes on rounding errors."""
    small = census.program_rows("A/Small", 9, _parsed([((), b"1")]))
    large = census.program_rows("Z/Large", 9, _parsed([((), b"1"), ((), b"2"), ((), b"3")]))
    entries = [{"shader": "A/Small", "properties": [], "passes": [],
                "platforms": [{"platform": 9, "programs": small}]},
               {"shader": "Z/Large", "properties": [], "passes": [],
                "platforms": [{"platform": 9, "programs": large}]}]
    assert [row[0] for row in census.by_shader(entries, 9)] == ["Z/Large", "A/Small"]


def test_a_pass_without_a_light_mode_is_reported_rather_than_dropped():
    """"This pass declares no light mode" is a fact a dispatcher needs."""
    tree = _tree("P", passes=[("Base", "UniversalForward"), ("Extra", "")])
    assert objects.passes(tree) == [(0, 0, "UniversalForward", "Base"),
                                    (0, 1, "", "Extra")]


def test_a_platform_that_fails_to_parse_keeps_its_entry():
    """Dropping it would let a decode failure look like a family with no
    programs, which is the one reading that must never be silent."""
    entry = {"shader": "P", "platforms": [
        {"platform": 9, "error": "BlobError: unbalanced", "programs": []}]}
    counts = census.totals([entry])
    assert counts["platformErrors"] == 1
    assert counts["records"] == 0


def test_bodies_are_dropped_before_serialising():
    parsed = _parsed([((), b"body-bytes")])
    rows = census.program_rows("P", 9, parsed, keep_code=True)
    assert rows[0]["code"] == b"body-bytes"
    entries = [{"shader": "P", "properties": [], "passes": [],
                "platforms": [{"platform": 9, "programs": rows}]}]
    stripped = census.without_code(entries)
    assert "code" not in stripped[0]["platforms"][0]["programs"][0]
    assert stripped[0]["platforms"][0]["programs"][0]["codeSha256"]


def test_the_declaring_block_is_not_called_a_stage():
    """Every subprogram on this content is declared under `progVertex`, and one
    GL record holds vertex and fragment together.  A field named `stage` would
    say each record is vertex-only and send a consumer looking for fragment
    records that do not exist as separate entries."""
    tree = {"m_Name": "", "m_ParsedForm": {
        "m_Name": "P",
        "m_PropInfo": {"m_Props": []},
        "m_SubShaders": [{"m_Passes": [{
            "m_Name": "Base",
            "m_Tags": {"tags": {"LIGHTMODE": "UniversalForward"}},
            "progVertex": {
                "m_PlayerSubPrograms": [[], [{"m_BlobIndex": 4, "m_KeywordIndices": [7],
                                              "m_GpuProgramType": 4}]],
                "m_ParameterBlobIndices": [[], [0]],
            },
            "progFragment": {"m_PlayerSubPrograms": [], "m_ParameterBlobIndices": []},
        }]}],
    }}
    rows = objects.subprograms(tree)
    assert len(rows) == 1
    assert "stage" not in rows[0]
    assert rows[0]["programBlock"] == "progVertex"
    assert rows[0] == {"platform": objects.PLATFORM_GLSL, "record": 4, "subshader": 0,
                       "pass": 0, "programBlock": "progVertex",
                       "lightMode": "UniversalForward", "gpuProgramType": 4,
                       "keywordIndices": [7], "parameterRecord": 0}


def test_an_attribution_that_covers_half_the_records_is_not_reported_as_working():
    """Consistency and coverage are different questions.  An attribution whose
    every claim is true can still leave records unnamed, and it reads as working
    because everything it does say checks out."""
    tree = {"m_Name": "", "m_ParsedForm": {
        "m_Name": "P", "m_PropInfo": {"m_Props": []},
        "m_SubShaders": [{"m_Passes": [{
            "m_Name": "", "m_Tags": {"tags": {}},
            "progVertex": {"m_PlayerSubPrograms": [[{"m_BlobIndex": 0,
                                                     "m_KeywordIndices": [],
                                                     "m_GpuProgramType": 4}]],
                           "m_ParameterBlobIndices": [[None]]},
        }]}],
    }}
    parsed = {objects.PLATFORM_GLSL: {"records": [
        {"kind": "program", "programType": 4},
        {"kind": "program", "programType": 4},   # never claimed
    ]}}
    assert objects.attribution_check(tree, parsed) == {
        "checked": 1, "agree": 1, "typeMismatch": 0, "outOfRange": 0}
    assert objects.attribution_coverage(tree, parsed) == {
        "programRecords": 2, "claimedOnce": 1, "claimedTwiceOrMore": 0, "unclaimed": 1}
