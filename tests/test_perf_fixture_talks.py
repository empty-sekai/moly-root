"""Tests for the furniture-side talk extraction gates (``src.perf.fixture_talks``).

The reader's gates are seven checks (c1–c7).  The tests below exercise two
things: the *op classification* that must never fold an unknown operator into an
"other" bucket, and the *gate functions* that must turn red on the three named
violations — an unknown operation, a talk whose form was left unlabelled (and
so could be silently defaulted), and a tweet that is not kept as a separate,
properly-tupled lane.

Every planted-violation test is written as a positive control: it builds a
document *with* the violation and asserts the relevant gate turns red, then a
document *without* it and asserts the same gate turns green.  The fixtures are
synthetic and must never be read back as if they described the game's talk
data; they only exercise the reader's code.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from perf import fixture_talks
from perf.fixture_talks import classify_ops, check_doc


def _tw(text, id_=1):
    return {"id": id_, "text": text, "motion": "m", "eye": "e", "mouth": "mo"}


def _base_doc():
    """A minimal, self-consistent document (c1 will be red by design)."""
    talks = [
        {"talkId": 1, "form": 1, "pairs": [[10, 1]],
         "tweet": _tw("alpha"), "steps": [{"op": "text", "text": "alpha"}]},
        {"talkId": 2, "form": 2, "pairs": [[10, 1]],
         "tweet": _tw("beta", 2), "steps": [{"op": "text", "text": "beta"}]},
    ]
    summary = {
        "selectedCount": 2, "talks": 2, "singlePerson": 2, "twoPerson": 0,
        "fourPerson": 0, "form1": 1, "form2": 1, "unlabeledForm": 0,
        "pairs": 2, "residualC4": 0, "tweets": 2,
        "operations": {op: 5 for op in fixture_talks.FURNITURE_OPS},
        "unknownOperations": {}, "missingScripts": [],
    }
    return {"summary": summary, "talks": talks}


def _gate(doc, name, library=None):
    results = dict((n, ok) for n, ok, _ in
                   check_doc(doc, motion_library=library or LIBRARY))
    return results[name]


# The synthetic motion library c10 checks against.  A name in a step's `motion`
# is a clip name after the constants resolve, so the library is a set of those.
LIBRARY = {"mov_synth_001", "mov_synth_002"}

# One token the constant source does not define, taken from the named set so the
# test proves the gate consults that set rather than a local copy of it.
UNDEFINED_MOTION = sorted(
    t for t in fixture_talks.UNDEFINED_TOKENS if t.startswith("Motions."))[0]


def _constants(source="pkg/defines.lua", referenced=None, resolved=None,
               unresolved=None, unexpected=()):
    """A summary.constants block, resolved everywhere unless told otherwise."""
    referenced = {"Motions": 100} if referenced is None else referenced
    resolved = dict(referenced) if resolved is None else resolved
    return {
        "source": source,
        "tables": {"Motions": 2},
        "scalars": 0,
        "referenced": referenced,
        "resolved": resolved,
        "unresolved": unresolved or {},
        "unresolvedOccurrences": sum(
            sum(keys.values()) for keys in (unresolved or {}).values()),
        "unexpectedTokens": sorted(unexpected),
    }


def _clean_full_doc():
    """A 4768-talk document on which every gate turns green."""
    CLOSED = (837, 838, 839, 840)   # the four 8xx furniture pieces
    N = fixture_talks.EXPECTED_TALKS
    talks = []
    form1 = 258
    for i in range(N):
        form = 1 if i < form1 else 2
        steps = [{"op": "text", "text": f"line {i}"}]
        fids = [837]
        if i < 120:                 # the ~120 operational scripts
            fid = CLOSED[i % 4]
            fids = [fid]
            steps += [
                {"op": "change_fixture_timeline", "fixture": fid, "name": f"tl_{i}"},
                {"op": "change_fixture_character_eye", "fixture": fid, "pattern": f"e{i}"},
                {"op": "fixture_voice", "cue": f"v{i}", "fixture": fid},
                {"op": "look_at_fixture", "fixture": "Characters.X"},
                {"op": "look_at_to_npc", "fixture": fid, "who": "w"},
                {"op": "show_fixture_emoticon", "fixture": fid, "name": "Emo.happy"},
            ]
            if fid != 838:          # 大嘴吉 never changes mouth
                steps.append({"op": "change_fixture_character_mouth",
                              "fixture": fid, "pattern": f"m{i}"})
        elif i == 120:              # the single gimmick script
            fids = [423]
            steps += [
                {"op": "play_fixture_gimmick", "fixture": "SetMysekaiEmissionOverride"},
                {"op": "stop_fixture_gimmick", "fixture": "SetMysekaiEmissionOverride", "name": 4},
            ]
        talks.append({
            "talkId": i + 1, "form": form, "pairs": [[10, (i % 30) + 1]],
            "tweet": _tw(f"tweet {i}", i + 1), "steps": steps, "fixtureIds": fids,
        })
    # One script asks for a motion the constant source does not define, which is
    # the shape the shipped scripts have: c10 classifies it as a named undefined
    # token rather than as a missing clip.
    talks[0]["steps"].append(
        {"op": "change_animation", "who": 1, "motion": UNDEFINED_MOTION,
         "alias": UNDEFINED_MOTION})
    summary = {
        "selectedCount": N, "talks": N,
        "singlePerson": 4354, "twoPerson": fixture_talks.EXPECTED_TWO_PERSON,
        "fourPerson": fixture_talks.EXPECTED_FOUR_PERSON,
        "form1": form1, "form2": N - form1, "unlabeledForm": 0,
        "pairs": N, "residualC4": 0, "tweets": N,
        "operations": {op: 10 for op in fixture_talks.FURNITURE_OPS},
        "unknownOperations": {}, "missingScripts": [],
        "fixtureOps": _fixture_ops(talks),
        "constants": _constants(),
        "fixtures": _fixtures(talks),
        "fixturesWithoutPackage": [],
    }
    return {"summary": summary, "talks": talks}


def _fixtures(talks):
    """A fixtureId -> package map covering exactly the ids the talks name."""
    ids = sorted({fid for t in talks for fid in t.get("fixtureIds", [])})
    return {str(fid): f"mdl_synth_fixture_{fid}" for fid in ids}


def _fixture_ops(talks):
    """The per-fixture furniture-operator occurrence table, as in summary."""
    table = {}
    for t in talks:
        for fid in t.get("fixtureIds", []):
            table.setdefault(fid, {})
            for s in t.get("steps", []):
                op = s.get("op")
                if op in fixture_talks.FURNITURE_OPS:
                    table[fid][op] = table[fid].get(op, 0) + 1
    return {str(fid): ops for fid, ops in sorted(table.items())}


# -- op classification -------------------------------------------------------


def test_classify_ops_reports_unknown_operator():
    """A call outside the vocabulary is reported by name, never bucketed."""
    script = ('label("x")\ntext("hi")\nmystery_vfx(1, 2)\n'
              'voice("talk", "cue", Characters.A)')
    known, unknown = classify_ops(script)
    assert known["label"] == 1
    assert known["text"] == 1
    assert known["voice"] == 1
    assert unknown["mystery_vfx"] == 1
    assert "mystery_vfx" not in known
    assert "text" not in unknown


def test_classify_ops_clean_script_has_no_unknown():
    script = 'text("hi")\nwait_click()\nchange_fixture_timeline(F, "g", 1)'
    known, unknown = classify_ops(script)
    assert not unknown
    assert "text" in known and "wait_click" in known
    assert "change_fixture_timeline" in known


# -- gate c3: unknown operation must turn red --------------------------------


def test_c3_red_on_unknown_operation_then_green():
    doc = _base_doc()
    doc["summary"]["unknownOperations"] = {"mystery_vfx": 1}
    assert _gate(doc, "c3") is False          # red
    assert _gate(_base_doc(), "c3") is True   # green


# -- gate c5: an unlabelled form must turn red -------------------------------


def test_c5_red_on_unlabeled_form_then_green():
    doc = _base_doc()
    doc["talks"][1]["form"] = 99
    doc["summary"]["form1"] = 1
    doc["summary"]["form2"] = 0
    doc["summary"]["unlabeledForm"] = 1
    assert _gate(doc, "c5") is False          # red
    assert _gate(_base_doc(), "c5") is True   # green


def test_c5_red_when_forms_do_not_partition_the_total():
    doc = _base_doc()
    doc["summary"]["form1"] = 0               # form1 + form2 = 1 != talks 2
    assert _gate(doc, "c5") is False          # red


# -- gate c6: a tweet that is not a separate tuple must turn red -------------


def test_c6_red_on_malformed_tweet_then_green():
    doc = _base_doc()
    del doc["talks"][0]["tweet"]["eye"]       # tweet lost its tuple shape
    assert _gate(doc, "c6") is False          # red
    assert _gate(_base_doc(), "c6") is True   # green


def test_c6_red_when_tweet_lane_empty():
    doc = _base_doc()
    doc["summary"]["tweets"] = 0
    doc["talks"][0]["tweet"] = None
    doc["talks"][1]["tweet"] = None
    assert _gate(doc, "c6") is False          # red


# -- gate c7: a silently dropped script must turn red ------------------------


def test_c7_red_on_silent_drop_then_green():
    doc = _base_doc()
    doc["summary"]["talks"] = 1               # selected 2 but only 1 kept
    assert _gate(doc, "c7") is False          # red (silent = 1)
    assert _gate(_base_doc(), "c7") is True   # green


def test_c7_green_when_gap_is_reported():
    doc = _base_doc()
    doc["summary"]["talks"] = 1
    doc["summary"]["missingScripts"] = ["missing.lua"]
    assert _gate(doc, "c7") is True           # gap reported, not silent


# -- gate c1/c2/c4 and the fully green document ------------------------------


def test_c1_red_on_wrong_selection():
    doc = _base_doc()
    doc["summary"]["selectedCount"] = 4767
    assert _gate(doc, "c1") is False          # red
    doc2 = _base_doc()
    doc2["summary"]["selectedCount"] = fixture_talks.EXPECTED_TALKS
    doc2["summary"]["twoPerson"] = fixture_talks.EXPECTED_TWO_PERSON
    doc2["summary"]["fourPerson"] = fixture_talks.EXPECTED_FOUR_PERSON
    assert _gate(doc2, "c1") is True          # green


def test_c2_red_when_furniture_operators_all_zero():
    doc = _base_doc()
    doc["summary"]["operations"] = {"text": 5, "voice": 5}
    assert _gate(doc, "c2") is False          # red
    assert _gate(_base_doc(), "c2") is True   # green (all 9 nonzero)


def test_c4_red_on_residual_pairs():
    doc = _base_doc()
    doc["summary"]["residualC4"] = 1
    assert _gate(doc, "c4") is False          # red
    assert _gate(_base_doc(), "c4") is True   # green


def test_c8_red_when_mouth_operates_on_838_then_green():
    """The mouth operator must never land on 838 (大嘴吉)."""
    doc = _clean_full_doc()
    # Plant the violation: give fixture-838 talks a mouth step.
    hit = False
    for t in doc["talks"]:
        if t["fixtureIds"] == [838]:
            if not any(s.get("op") == "change_fixture_character_mouth"
                       for s in t["steps"]):
                t["steps"].append({"op": "change_fixture_character_mouth",
                                   "fixture": 838, "pattern": "m"})
            hit = True
            break
    assert hit, "test fixture must contain a 838 talk"
    assert _gate(doc, "c8") is False          # red: mouth on 838
    assert _gate(_clean_full_doc(), "c8") is True   # green


def test_fixture_ops_table_answers_operational_question():
    """fixtureOps lets a consumer ask: does this fixture have an operational script."""
    fops = _clean_full_doc()["summary"]["fixtureOps"]
    # 423 (the gimmick piece) has the gimmick operators.
    assert "play_fixture_gimmick" in fops["423"]
    assert "stop_fixture_gimmick" in fops["423"]
    # 838 has no mouth operator, 837 does.
    assert "change_fixture_character_mouth" not in fops["838"]
    assert "change_fixture_character_mouth" in fops["837"]
    # 837 has the closed-set operators.
    assert "change_fixture_timeline" in fops["837"]


# --- c11: every fixture a talk is gated on names a package ---

def test_c11_red_when_a_referenced_fixture_has_no_package_then_green():
    # A talk carries a fixtureId and every geometry document is keyed by package
    # name, so without this join a consumer has to guess the pairing from names
    # that look related.  The violation is planted by removing one mapping.
    doc = _clean_full_doc()
    fixtures = doc["summary"]["fixtures"]
    victim = sorted(fixtures)[0]
    fixtures[victim] = None
    doc["summary"]["fixturesWithoutPackage"] = [victim]
    assert _gate(doc, "c11") is False
    fixtures[victim] = f"mdl_synth_fixture_{victim}"
    doc["summary"]["fixturesWithoutPackage"] = []
    assert _gate(doc, "c11") is True


def test_c11_red_when_a_referenced_fixture_is_missing_from_the_map():
    # Absent from the map is a different failure from present-but-null, and it
    # must not read as "nothing to resolve".
    doc = _clean_full_doc()
    victim = sorted(doc["summary"]["fixtures"])[0]
    del doc["summary"]["fixtures"][victim]
    assert _gate(doc, "c11") is False


def test_c11_red_when_the_map_is_empty():
    # What a missing master table looks like: no map at all, while the talks
    # still name fixtures.  Zero over zero must not pass.
    doc = _clean_full_doc()
    doc["summary"]["fixtures"] = {}
    assert _gate(doc, "c11") is False


def test_full_document_is_green():
    doc = _clean_full_doc()
    results = dict((n, ok) for n, ok, _ in
                   check_doc(doc, motion_library=LIBRARY))
    assert all(results.values()), str(results)


# --- c9: the constant tables were read, and only the named tokens are left ---

def test_c9_red_when_the_constant_source_was_not_read_then_green():
    # The violation is planted by taking the constant package away, not by
    # skipping the resolve step: a later fallback that guessed a value would
    # leave a skipped step green while this stays red.
    doc = _clean_full_doc()
    doc["summary"]["constants"] = _constants(source=None)
    assert _gate(doc, "c9") is False
    doc["summary"]["constants"] = _constants()
    assert _gate(doc, "c9") is True


def test_c9_red_when_nothing_was_referenced():
    # A document that resolved nothing because it looked at nothing must not
    # read as "all resolved": zero over zero is the vacuous pass this guards.
    doc = _clean_full_doc()
    doc["summary"]["constants"] = _constants(referenced={}, resolved={})
    assert _gate(doc, "c9") is False


def test_c9_red_on_a_token_the_named_set_does_not_carry_then_green():
    # Reading the constants from a package that has fewer entries leaves tokens
    # over that UNDEFINED_TOKENS does not name -- which is what happens with the
    # copy that carries no egg entries.
    doc = _clean_full_doc()
    doc["summary"]["constants"] = _constants(
        referenced={"EyePresets": 100}, resolved={"EyePresets": 60},
        unresolved={"EyePresets": {"egg1_normal": 40}},
        unexpected=["EyePresets.egg1_normal"])
    assert _gate(doc, "c9") is False
    doc["summary"]["constants"] = _constants(
        referenced={"EyePresets": 100}, resolved={"EyePresets": 100})
    assert _gate(doc, "c9") is True


# --- c10: every motion a step asks for names a clip the library holds ---

def test_c10_red_when_a_resolved_motion_is_not_in_the_library_then_green():
    doc = _clean_full_doc()
    doc["talks"][1]["steps"].append(
        {"op": "change_animation", "who": 1, "motion": "mov_absent_999"})
    assert _gate(doc, "c10") is False
    doc["talks"][1]["steps"].pop()
    assert _gate(doc, "c10") is True


def test_c10_red_when_the_library_is_absent():
    # No library and every motion present are the same colour unless absence is
    # reported, so the criterion says it could not run instead of passing.
    doc = _clean_full_doc()
    results = dict((n, ok) for n, ok, _ in check_doc(doc, motion_library=None))
    assert results["c10"] is False


def test_c10_red_when_a_motion_is_still_an_unnamed_constant_token():
    # A motion left as a Table.key token that UNDEFINED_TOKENS does not name
    # means the tables were read from the wrong place -- separate from the named
    # tokens no source defines, which stay green.
    doc = _clean_full_doc()
    doc["talks"][1]["steps"].append(
        {"op": "change_animation", "who": 1, "motion": "Motions.some_key"})
    assert _gate(doc, "c10") is False


def test_c10_classes_sum_to_the_motions_asked_for():
    # Every asked-for motion lands in exactly one class; a residual would mean a
    # motion was counted in none of them and quietly left out of the check.
    doc = _clean_full_doc()
    detail = dict((n, d) for n, _, d in
                  check_doc(doc, motion_library=LIBRARY))["c10"]
    assert "residual=0" in detail, detail
    assert "notInLibrary=0" in detail, detail
    assert "undefinedToken=1" in detail, detail


# --- the census that feeds c9 ---

def test_constant_census_counts_references_from_the_script_source():
    # The census reads the scripts, not the steps: a resolved token no longer
    # looks like one in a step, so a census of the output could only ever see
    # the failures.
    tables = {"Motions": {"calm": "mov_calm"}, "Characters": {"A": 1}}
    script = 'change_animation(Characters.A, Motions.calm, 0)\n' \
             'change_animation(Characters.A, Motions.absent, 0)\n'
    census = fixture_talks.constant_census([script], tables)
    assert census["referenced"] == {"Characters": 2, "Motions": 2}
    assert census["resolved"] == {"Characters": 2, "Motions": 1}
    assert census["unresolved"] == {"Motions": {"absent": 1}}


def test_constant_census_does_not_count_a_token_named_only_in_a_comment():
    tables = {"Motions": {"calm": "mov_calm"}}
    script = '-- Motions.absent is not used here\nlabel("x")\n'
    census = fixture_talks.constant_census([script], tables)
    assert census["referenced"] == {}
    assert census["unresolved"] == {}


def test_read_constants_without_a_package_reports_no_source():
    # Not an error and not a silent empty: the caller gets an explicit "no
    # source", which is what turns c9 red rather than making it vacuous.
    tables, scalars, source = fixture_talks.read_constants(None)
    assert (tables, scalars, source) == ({}, {}, None)
