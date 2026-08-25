"""Furniture-side conversations and their head-top tweets.

A *furniture-side talk* is a ``mysekaiCharacterTalks`` row whose condition group
gates on a fixture (``mysekai_fixture_id``).  These are the talks that play when
an NPC is near furniture — the demo's 4768 of them.  The same scripts were left
out of the single-character non-furniture extraction (``chara.talks``), and they
are the batch this lane extracts.

Two things are new here that the single-character extraction never touched:

1. *The fixture and the character.*  A furniture talk is about a particular
   ``mysekaiFixtureId`` (from its fixture condition) and a particular
   ``gameCharacterUnitId`` (from its unit group).  A talk may be a group of more
   than one character; the association is the Cartesian product of fixture and
   unit.  Every talk must produce at least one ``(fixtureId, unitId)`` pair —
   residual 0 is the c4 gate.

2. *The form.*  A talk either opens the bottom dialogue window (form 2, player
   participation) or shows only a head-top tweet and an emote bubble (form 1,
   NoTalk / self-initiated).  The game picks which by a runtime lottery against
   ``ClientConfig.Mysekai.NPCLotteryNoneTalkFixtureActionPercent``; this lane does
   not run the lottery.  The static, game-side signal for "can this fixture-unit
   pairing be shown as NoTalk" is ``mysekaiCharacterTalkNoTalkMysekaiFixtureActions``
   — a talk whose ``(fixtureId, unitId)`` pair appears there is labelled form 1,
   otherwise form 2.  A talk that would need the lottery left completely
   unlabelled is a c5 failure.
"""
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import UnityPy

from core.jsonio import write_json
from core.master import Master
from chara import talks as talks_chara

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

SCRIPT_VOCAB = talks_chara.ALL_CALL_OPS
_SCRIPT_VOCAB_SET = set(SCRIPT_VOCAB)

# The nine furniture operators.  On the already-extracted 1412 single-character
# talks every one of them counts zero; on this batch they must all be non-zero
# or the script set is the wrong one.
FURNITURE_OPS = (
    "change_fixture_timeline", "fixture_voice", "look_at_fixture",
    "show_fixture_emoticon", "play_fixture_gimmick", "stop_fixture_gimmick",
    "change_fixture_character_eye", "change_fixture_character_mouth",
    "look_at_to_npc",
)

FIXTURE_CONDITION = "mysekai_fixture_id"
EXPECTED_TALKS = 4768
EXPECTED_TWO_PERSON = 409
EXPECTED_FOUR_PERSON = 5

# The host furniture (the fixtureId a talk is gated on) each furniture operator
# appears on, per an independent lua-side lane.  The five closed-set operators
# run on the four 8xx fixtures; ``change_fixture_character_mouth`` excludes
# 838 (大嘴吉) because ``defines.lua``'s LipSyncPresets block has no ``egg2_``
# entry, so that piece never changes mouth; the two gimmick operators run only
# on 423 (宁宁机器人摆件).  A c8 divergence means the parameter placeholder is
# wrong or the selection missed a script — not something to filter away.
FURNITURE_HOSTS = {
    "change_fixture_timeline": {837, 838, 839, 840},
    "change_fixture_character_eye": {837, 838, 839, 840},
    "fixture_voice": {837, 838, 839, 840},
    "look_at_fixture": {837, 838, 839, 840},
    "look_at_to_npc": {837, 838, 839, 840},
    "change_fixture_character_mouth": {837, 839, 840},
    "play_fixture_gimmick": {423},
    "stop_fixture_gimmick": {423},
}


def _unit_group_members(row, group_row):
    """The non-null ``gameCharacterUnitId1..5`` values of a group row."""
    return [group_row.get(f"gameCharacterUnitId{i}")
            for i in range(1, 6)
            if group_row.get(f"gameCharacterUnitId{i}")]


def conditions_map_value(conditions, cond_id):
    """The ``mysekaiCharacterTalkConditionTypeValue`` of one condition id."""
    for cond in conditions:
        if cond.get("id") == cond_id:
            return cond.get("mysekaiCharacterTalkConditionTypeValue")
    return None


def _unit_group_size(unit_ids):
    return len(unit_ids)


def _all_call_names(text, script_name=None):
    """Yield every call name — known or not — in source order.

    This mirrors ``chara.talks._calls`` but does *not* filter to the known
    vocabulary, so an operator the vocabulary has never seen is reported instead
    of being silently skipped.
    """
    text = (text or "").lstrip("\ufeff")
    i = 0
    name_re = talks_chara._IDENT
    while i < len(text):
        skipped = talks_chara._skip_noncode(text, i)
        if skipped is not None:
            i = skipped
            continue
        match = name_re.match(text, i)
        if not match:
            i += 1
            continue
        name, start, i = match.group(0), i, match.end()
        # Same guards as _calls: skip method-style / compound names.
        if start and (text[start - 1].isalnum() or text[start - 1] in "_."):
            continue
        whitespace = re.match(r"\s*", text[i:])
        open_index = i + len(whitespace.group(0))
        if open_index >= len(text) or text[open_index] != "(":
            continue
        yield name


def classify_ops(text, script_name=None):
    """Split a script's calls into known and unknown operation counters.

    Returns ``(known, unknown)`` where each is a ``Counter`` keyed by operator
    name.  *known* is exactly the set the vocabulary recognises; every other
    call name lands in *unknown* — nothing is folded into an "other" bucket.
    """
    names = Counter(_all_call_names(text, script_name))
    known = Counter({op: cnt for op, cnt in names.items() if op in _SCRIPT_VOCAB_SET})
    unknown = Counter({op: cnt for op, cnt in names.items() if op not in _SCRIPT_VOCAB_SET})
    return known, unknown


def _text_assets(bundle):
    """``{TextAsset name: script text}``."""
    out = {}
    for obj in UnityPy.load(bundle).objects:
        if obj.type.name != "TextAsset":
            continue
        tree = obj.read_typetree()
        script = tree.get("m_Script", "")
        if isinstance(script, (bytes, bytearray)):
            script = bytes(script).decode("utf-8-sig", "replace")
        out[str(tree.get("m_Name", ""))] = script
    return out


def _asset_named(assets, name):
    """A script by its master-side lua name, adding ``.lua`` if needed."""
    if name in assets:
        return assets[name]
    suffixed = name if name.endswith(".lua") else name + ".lua"
    return assets.get(suffixed)


def _tweet(row):
    if row is None:
        return None
    return {
        "id": row.get("id"),
        "text": row.get("text"),
        "motion": row.get("motionName"),
        "eye": row.get("expressionEyeName"),
        "mouth": row.get("expressionMouthName"),
    }


def _semantics(resolved):
    return {
        "selection": (
            "Only talks whose condition group contains a "
            f"{FIXTURE_CONDITION} condition are included (the furniture-side "
            "talks the single-character extraction excluded)."
        ),
        "form": (
            "form is the static NoTalk-capability label.  The rule: read the "
            "source table mysekaiCharacterTalkNoTalkMysekaiFixtureActions; a "
            "talk is form 1 when its (fixtureId, unitId) pair appears there "
            "(the NoTalk lane: head-top tweet + emote bubble, no dialogue "
            "window), otherwise form 2 (bottom dialogue window, player "
            "participation).  The runtime lottery "
            "(ClientConfig.Mysekai.NPCLotteryNoneTalkFixtureActionPercent) that "
            "picks the actual form is NOT run here, so a talk that would need "
            "the lottery is never left unlabelled.  Every record carries its "
            "fixtureIds, unitIds and pairs, so the form can be recomputed from "
            "any other (fixtureId, unitId) signal by relabelling — the rule is "
            "descriptive, not part of the data."
        ),
        "steps": "steps are the source-order call stream; no runtime timestamp is inferred",
        "text": "text is preserved as decoded source text, including newline characters",
        "voiceCues": "voice cue names occur in scripts; audio bytes are not in the talk bundle",
        "tweet": (
            "tweet is the separate text, motion, eye, and mouth pairing from the "
            "master tables, kept out of the dialog step sequence"
        ),
        "unknownOperations": (
            "any call outside the vocabulary is reported by name and count; it is "
            "never folded into an 'other' bucket or dropped"
        ),
        "fixtureOps": (
            "fixtureOps maps each fixtureId to the furniture-operator occurrence "
            "count on the talks gated on it.  Use it to answer \"does this "
            "furniture have an operational script\" (count > 0) versus talks that "
            "merely happen beside it."
        ),
        "constants": {"resolved": resolved, "unresolvedTokensPreserved": True},
    }


def fixture_talks(master_source, master_cache=None):
    """Select the furniture-side talks and map each to fixture and unit.

    Returns ``(records, report)``.  Each record carries the talk row, its
    ``fixtureIds``, ``unitIds``, ``pairs``, ``form`` and ``tweetId``.  The report
    counts the talks per unit-group size and the number the pairing could not
    be associated (the c4 residual).
    """
    master = Master(master_source, cache_dir=master_cache)
    talks = master.table("mysekaiCharacterTalks")
    conditions = master.table("mysekaiCharacterTalkConditions")
    condition_groups = master.table("mysekaiCharacterTalkConditionGroups")
    unit_groups = master.table("mysekaiGameCharacterUnitGroups")
    pre_actions = master.table("mysekaiCharacterTalkPreActions")
    no_talk = master.table("mysekaiCharacterTalkNoTalkMysekaiFixtureActions")

    fixture_condition_ids = {
        cond["id"] for cond in conditions
        if cond.get("mysekaiCharacterTalkConditionType") == FIXTURE_CONDITION
    }
    fixture_groups = {
        group["groupId"]
        for group in condition_groups
        if group.get("mysekaiCharacterTalkConditionId") in fixture_condition_ids
    }

    groups_by_id = {row["id"]: row for row in unit_groups}
    group_types = {}
    for group in condition_groups:
        group_types.setdefault(group["groupId"], []).append(
            group.get("mysekaiCharacterTalkConditionId"))
    fixture_of_group = {}
    for group_id, cond_ids in group_types.items():
        values = []
        for cond_id in cond_ids:
            if cond_id in fixture_condition_ids:
                value = conditions_map_value(conditions, cond_id)
                if value is not None:
                    values.append(value)
        if values:
            fixture_of_group[group_id] = values

    tweet_of = {
        row["mysekaiCharacterTalkId"]: row["mysekaiCharacterTalkTweetId"]
        for row in pre_actions
    }
    no_talk_pairs = {
        (row["mysekaiFixtureId"], row["gameCharacterUnitId"]) for row in no_talk
    }

    records, report_counts, residual = [], Counter(), []
    for talk in talks:
        group_id = talk.get("mysekaiCharacterTalkConditionGroupId")
        if group_id not in fixture_groups:
            continue
        group_row = groups_by_id.get(talk.get("mysekaiGameCharacterUnitGroupId"))
        unit_ids = _unit_group_members(talk, group_row) if group_row else []
        fixture_ids = fixture_of_group.get(group_id, [])
        pairs = [(f, u) for f in fixture_ids for u in unit_ids]
        size = _unit_group_size(unit_ids)
        report_counts[size] += 1
        if not pairs:
            residual.append(talk["id"])
            continue
        form = 1 if any(p in no_talk_pairs for p in pairs) else 2
        records.append({
            "talk": talk,
            "fixtureIds": fixture_ids,
            "unitIds": unit_ids,
            "pairs": pairs,
            "form": form,
            "tweetId": tweet_of.get(talk["id"]),
        })

    report = {
        "talks": len(records),
        "bySize": {str(k): v for k, v in sorted(report_counts.items())},
        "residual": residual,
    }
    return records, report


def extract_fixture_talks(master_source, talk_bundle, out_path, master_cache=None):
    """Write the furniture-side talks document to *out_path*.

    *master_source* is a directory of master tables or a base URL; *talk_bundle*
    is the decrypted ``mysekai__talk__scenario__talk`` package.  Returns the
    summary plus the selection report.
    """
    records, report = fixture_talks(master_source, master_cache=master_cache)
    master = Master(master_source, cache_dir=master_cache)
    tweets = {row["id"]: row for row in master.table("mysekaiCharacterTalkTweets")}
    assets = _text_assets(talk_bundle)

    operations, unknown_ops, voices = Counter(), Counter(), []
    fixture_ops = defaultdict(Counter)
    talks_doc = []
    missing_scripts = []
    total_steps = 0
    resolved = bool(assets)
    for record in records:
        talk = record["talk"]
        lua = talk.get("lua", "")
        script = _asset_named(assets, lua)
        if script is None:
            missing_scripts.append(lua)
            continue
        known, unknown = classify_ops(script, script_name=lua)
        parsed = talks_chara.parse_script(script, {}, {}, script_name=lua)
        # The classify_ops known counter must agree with the parser's own scan.
        if parsed["operations"] and known and set(parsed["operations"]) != set(known):
            raise ValueError(f"{lua}: vocabulary disagreement between scans")
        operations.update(known)
        unknown_ops.update(unknown)
        voices.extend(parsed["voices"])
        total_steps += len(parsed["steps"])
        for fid in record["fixtureIds"]:
            for step in parsed["steps"]:
                if step.get("op") in FURNITURE_OPS:
                    fixture_ops[fid][step["op"]] += 1
        tweet_id = record.get("tweetId")
        tweet = _tweet(tweets.get(tweet_id)) if tweet_id is not None else None
        talks_doc.append({
            "talkId": talk.get("id"),
            "lua": lua,
            "form": record["form"],
            "siteGroupId": talk.get("mysekaiSiteGroupId"),
            "termId": talk.get("mysekaiCharacterTalkTermId"),
            "conditionGroupId": talk.get("mysekaiCharacterTalkConditionGroupId"),
            "fixtureIds": record["fixtureIds"],
            "unitIds": record["unitIds"],
            "pairs": record["pairs"],
            "tweet": tweet,
            "voices": parsed["voices"],
            "steps": parsed["steps"],
        })

    summary = {
        "selectedCount": report["talks"],
        "talks": len(talks_doc),
        "singlePerson": report["bySize"].get("1", 0),
        "twoPerson": report["bySize"].get("2", 0),
        "fourPerson": report["bySize"].get("4", 0),
        "form1": sum(1 for t in talks_doc if t["form"] == 1),
        "form2": sum(1 for t in talks_doc if t["form"] == 2),
        "unlabeledForm": sum(
            1 for t in talks_doc if t["form"] not in (1, 2)),
        "pairs": sum(len(t["pairs"]) for t in talks_doc),
        "residualC4": len(report["residual"]),
        "tweets": sum(1 for t in talks_doc if t["tweet"]),
        "operations": dict(operations),
        "unknownOperations": dict(unknown_ops),
        "fixtureOps": {
            str(fid): dict(cnt) for fid, cnt in sorted(fixture_ops.items())
        },
        "totalSteps": total_steps,
        "voiceCues": len(voices),
        "missingScripts": missing_scripts,
    }
    doc = {
        "version": 1,
        "semantics": _semantics(resolved),
        "talks": talks_doc,
        "summary": summary,
    }
    write_json(out_path, doc)
    return {**summary, "report": report}


def check_doc(doc, sample_seed=0):
    """Run the c1–c7 gates over an extracted furniture-talks document.

    Returns a list of ``(name, ok, detail)``.  Each criterion states the input
    that would turn it red, and the detail carries what was measured.
    """
    summary = doc.get("summary", {})
    talks = doc.get("talks", [])
    checks = []

    def check(name, ok, detail):
        checks.append((name, ok, detail))
        return ok

    # c1: count and the multi-character split, against the master selection
    selected = summary.get("selectedCount")
    two = summary.get("twoPerson")
    four = summary.get("fourPerson")
    check("c1",
          selected == EXPECTED_TALKS and two == EXPECTED_TWO_PERSON
          and four == EXPECTED_FOUR_PERSON,
          f"selected={selected} twoPerson={two} fourPerson={four} "
          f"(expect {EXPECTED_TALKS}/{EXPECTED_TWO_PERSON}/{EXPECTED_FOUR_PERSON})")

    # c2: the furniture-operators negative control flips to positive
    operations = summary.get("operations", {})
    furniture_nonzero = sum(1 for op in FURNITURE_OPS if operations.get(op, 0) > 0)
    histogram = json.dumps({op: operations.get(op, 0) for op in sorted(operations)},
                           ensure_ascii=False)
    check("c2", furniture_nonzero >= 5,
          f"furniture operators nonzero={furniture_nonzero}/9 | {histogram}")

    # c3: no unknown operators
    unknown = summary.get("unknownOperations", {})
    check("c3", not unknown,
          (json.dumps(unknown, ensure_ascii=False) if unknown
           else "unknown operations = 0"))

    # c4: every talk associates to a (fixtureId, unitId) pair
    residual = summary.get("residualC4", 0)
    check("c4", residual == 0, f"residual (unpaired talks) = {residual}")

    # c5: form labelled on every talk, forms partition the total
    form1 = summary.get("form1")
    form2 = summary.get("form2")
    unlabeled = summary.get("unlabeledForm")
    talks_total = summary.get("talks")
    check("c5", unlabeled == 0 and (form1 + form2) == talks_total,
          f"form1={form1} form2={form2} unlabeled={unlabeled} "
          f"sum={form1 + form2} (talks={talks_total})")

    # c6: tweet lane populated; positive control prints three texts; and each
    # tweet is a proper separate tuple (id/text/motion/eye/mouth), never a bare
    # mention or a dialog step.
    tweets = summary.get("tweets", 0)
    TWEET_KEYS = ("id", "text", "motion", "eye", "mouth")
    malformed = []
    for t in talks:
        tw = t.get("tweet")
        if tw is not None and not all(k in tw for k in TWEET_KEYS):
            malformed.append(t.get("talkId"))
    c6_detail = f"tweets={tweets} malformed={len(malformed)}"
    if tweets > 0:
        import random
        random.seed(sample_seed)
        sample = random.sample([t for t in talks if t.get("tweet")],
                               min(3, tweets))
        c6_detail += " | sample: " + " | ".join(
            repr(t["tweet"]["text"][:20]) for t in sample)
    check("c6", tweets > 0 and not malformed, c6_detail)

    # c7: every selected script is either extracted or reported missing;
    # a silent drop (selected - talks > missing) is red.
    missing = summary.get("missingScripts", [])
    selected = summary.get("selectedCount", 0)
    silent = (selected - talks_total) - len(missing)
    check("c7", silent == 0,
          f"selected={selected} talks={talks_total} missing={len(missing)}"
          f" silent={silent}"
          + (" | " + ",".join(missing) if missing else ""))

    # c8: each furniture operator's host-fixture set matches the ground truth
    # from an independent lua-side lane.  The host is the fixtureId the talk is
    # gated on (the furniture the operator acts on).  A symmetric-difference
    # non-zero is red; in particular change_fixture_character_mouth must be
    # {837,839,840} without 838, or the parameter placeholder is wrong.
    all_host = {op: set() for op in FURNITURE_OPS}
    for t in talks:
        fids = set(t.get("fixtureIds", []) or [])
        for s in t.get("steps", []):
            op = s.get("op")
            if op in all_host:
                all_host[op] |= fids
    symdiff = {}
    for op, expected in FURNITURE_HOSTS.items():
        ds = all_host[op] ^ expected
        if ds:
            symdiff[op] = sorted(ds)
    c8_detail = " | ".join(
        f"{op}={sorted(all_host[op])}" for op in sorted(all_host))
    if symdiff:
        c8_detail += " | SYMDIFF=" + json.dumps(symdiff, ensure_ascii=False)
    check("c8", not symdiff, c8_detail)

    return checks


def verify(doc, out=None):
    """Run the gates and print them; returns the all-green boolean."""
    results = check_doc(doc)
    for name, ok, detail in results:
        print(f"[{name}] {'green' if ok else 'RED'}  {detail}")
    if out is not None:
        print(f"\nwrote: {out}")
    all_ok = all(ok for _, ok, _ in results)
    print("\nRESULT:", "GREEN" if all_ok else "RED")
    return all_ok


if __name__ == "__main__":
    raise SystemExit("fixture-talks extraction is a library; call "
                     "extract_fixture_talks / verify")
