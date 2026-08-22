"""Alone-action performance parsing: scenario split, nominal timing, completeness.

The parser must keep mutually exclusive branches apart.  Concatenating them would
produce a timeline the content never plays, so the completeness check (every parsed
call accounted for exactly once) is the load-bearing guard here.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chara.alone_actions import (parse_constant_scalars, parse_constant_tables,
                                parse_script)

DEFINES = """
animation_blend_time = 0.5

MotionType = {
    _s = 0,
    _l = 1,
    _e = 2,
    _o = 3,
}

S = MotionType._s
E = MotionType._e

Characters = {
    Alpha = 12,
    Beta = 21,
}
Motions = {
    m_calm_idle = "mov_cm_calm_idle001",
    m_calm_angry = "mov_cm_calm_angry001",
    m_calm_nod = "mov_cm_calm_nod001",
}
EyePresets = {
    normal = "normal",
    close_normal = "close_normal",
    normal_sad = "normal_sad",
}
LipSyncPresets = {
    normal01 = "normal01",
    smile01 = "smile01",
}
"""

TIME_GATED = """
local timeLimit_10 = 10
local probability_15 = 0.15
local memoryDuration = 15
local MOTION_1 = 1

while (is_end(Characters.Alpha) == false) do
    if hasTimeElapsed(timeLimit_10) and shouldExecuteWithProbability(probability_15) and canSelectMotion(MOTION_1, nowTime) then
        change_npc_eye(Characters.Alpha, EyePresets.normal)
        change_npc_mouth(Characters.Alpha, LipSyncPresets.normal01)
        alone_wait_time(Characters.Alpha,0.1)
        change_animation(Characters.Alpha, Motions.m_calm_angry, 0)
        alone_wait_time(Characters.Alpha,4)
        change_npc_eye(Characters.Alpha, EyePresets.normal_sad)
        alone_wait_time(Characters.Alpha,2)
        lastSelectedTimes[MOTION_1] = nowTime
    end
    change_animation(Characters.Alpha, Motions.m_calm_idle, 0)
    alone_wait_time(Characters.Alpha,3)
end
"""

RANDOM_BRANCH = """
while (is_end(Characters.Beta) == false) do
    rand = math.random(0, 99)
    if (rand < 20) then
        change_npc_eye(Characters.Beta, EyePresets.normal)
        change_animation(Characters.Beta, Motions.m_calm_nod, 0)
        alone_wait_time(Characters.Beta,3)
    elseif (rand < 100) then
        change_npc_eye(Characters.Beta, EyePresets.close_normal)
        change_animation(Characters.Beta, Motions.m_calm_angry, 0)
        alone_wait_time(Characters.Beta,5)
    end
    change_animation(Characters.Beta, Motions.m_calm_idle, 0)
    alone_wait_time(Characters.Beta,3)
end
"""


@pytest.fixture(name="tables")
def _tables():
    return parse_constant_tables(DEFINES)


@pytest.fixture(name="scalars")
def _scalars(tables):
    return parse_constant_scalars(DEFINES, tables)


def test_constant_tables_resolve_names(tables):
    assert tables["Motions"]["m_calm_angry"] == "mov_cm_calm_angry001"
    assert tables["EyePresets"]["close_normal"] == "close_normal"
    assert tables["Characters"] == {"Alpha": 12, "Beta": 21}


def test_time_gated_scenario_carries_trigger_and_nominal_timeline(tables):
    parsed = parse_script(TIME_GATED, tables)
    assert [s["id"] for s in parsed["scenarios"]] == ["MOTION_1"]
    trigger = parsed["scenarios"][0]["trigger"]
    assert trigger["kind"] == "timeGated"
    assert trigger["timeLimitSeconds"] == 10
    assert trigger["probability"] == 0.15
    assert trigger["slotMemorySeconds"] == 15
    steps = parsed["scenarios"][0]["steps"]
    # waits accumulate; a write is stamped with the time reached before it
    assert [(s["t"], s["op"]) for s in steps] == [
        (0.0, "eye"), (0.0, "mouth"), (0.0, "wait"),
        (0.1, "animation"), (0.1, "wait"),
        (4.1, "eye"), (4.1, "wait"),
    ]
    assert steps[3]["motion"] == "mov_cm_calm_angry001"
    assert steps[3]["alias"] == "Motions.m_calm_angry"
    assert steps[5]["pattern"] == "normal_sad"
    # the loop tail is not a scenario and must not be folded into one
    assert [s["op"] for s in parsed["tail"]["steps"]] == ["animation", "wait"]
    assert parsed["tail"]["steps"][0]["motion"] == "mov_cm_calm_idle001"


def test_random_branches_stay_mutually_exclusive(tables):
    parsed = parse_script(RANDOM_BRANCH, tables)
    assert [s["id"] for s in parsed["scenarios"]] == ["rand<20", "rand<100"]
    weights = [s["trigger"]["weight"] for s in parsed["scenarios"]]
    assert weights == [0.2, 0.8]
    first, second = parsed["scenarios"]
    assert [s["op"] for s in first["steps"]] == ["eye", "animation", "wait"]
    assert first["steps"][1]["motion"] == "mov_cm_calm_nod001"
    assert second["steps"][1]["motion"] == "mov_cm_calm_angry001"
    # each branch starts its own clock rather than continuing the previous branch
    assert first["steps"][0]["t"] == 0.0 and second["steps"][0]["t"] == 0.0
    assert [s["op"] for s in parsed["tail"]["steps"]] == ["animation", "wait"]


def test_every_call_is_accounted_for_exactly_once(tables):
    for script in (TIME_GATED, RANDOM_BRANCH):
        parsed = parse_script(script, tables)
        counted = sum(len(b["steps"]) for b in parsed["scenarios"] + [parsed["tail"]])
        assert counted == parsed["callCount"]


def test_unresolvable_constant_is_an_error_not_a_guess(tables):
    bad = TIME_GATED.replace("Motions.m_calm_angry", "Motions.m_missing_motion")
    with pytest.raises(KeyError):
        parse_script(bad, tables)


def test_unterminated_call_is_an_error(tables):
    bad = TIME_GATED.replace("change_animation(Characters.Alpha, Motions.m_calm_idle, 0)",
                             "change_animation(Characters.Alpha, Motions.m_calm_idle, 0")
    with pytest.raises(ValueError):
        parse_script(bad, tables)


def test_bare_alias_resolves_to_a_motion_phase(tables, scalars):
    """Scripts pass the phase as a bare global aliasing a table member.

    Leaving it unresolved would hand the consumer a variable name instead of a
    phase, and the wrong segment would play.
    """
    assert scalars["E"] == 2 and scalars["animation_blend_time"] == 0.5
    script = TIME_GATED.replace(
        "change_animation(Characters.Alpha, Motions.m_calm_angry, 0)",
        "change_animation(Characters.Alpha, Motions.m_calm_angry, 0, 0, false, animation_blend_time, E)")
    step = next(s for s in parse_script(script, tables, scalars)["scenarios"][0]["steps"]
                if s["op"] == "animation")
    assert step["phase"] == "E"
    assert step["phaseSource"] == "E"
    assert step["blend"] == 0.5
    # The alias letters look exactly like the phase letters, so the phase value alone
    # cannot show whether it was resolved: only the source field can.
    bare = next(s for s in parse_script(script, tables)["scenarios"][0]["steps"]
                if s["op"] == "animation")
    assert bare["phaseSource"] is None
    assert bare["blend"] == "animation_blend_time"


def test_zero_speed_becomes_the_rate_the_runtime_actually_uses(tables, scalars):
    """Scripts pass 0 for the playback rate and the runtime reads that as 1.0.

    A consumer that applied the scripted value literally would freeze every
    motion, so the effective rate is recorded alongside the scripted one.
    """
    step = next(s for s in parse_script(TIME_GATED, tables, scalars)["scenarios"][0]["steps"]
                if s["op"] == "animation")
    assert step["speed"] == 0
    assert step["playbackSpeed"] == 1.0

    faster = TIME_GATED.replace("change_animation(Characters.Alpha, Motions.m_calm_angry, 0)",
                                "change_animation(Characters.Alpha, Motions.m_calm_angry, 1.5)")
    step = next(s for s in parse_script(faster, tables, scalars)["scenarios"][0]["steps"]
                if s["op"] == "animation")
    assert step["speed"] == 1.5 and step["playbackSpeed"] == 1.5


def test_omitted_blend_and_end_motion_fall_back_to_library_defaults(tables, scalars):
    plain = next(s for s in parse_script(TIME_GATED, tables, scalars)["scenarios"][0]["steps"]
                 if s["op"] == "animation")
    assert plain["blend"] == 0.5 and plain["playEndMotion"] is False

    script = TIME_GATED.replace(
        "change_animation(Characters.Alpha, Motions.m_calm_angry, 0)",
        "change_animation(Characters.Alpha, Motions.m_calm_angry, 0, 1, true, 0.25, E)")
    step = next(s for s in parse_script(script, tables, scalars)["scenarios"][0]["steps"]
                if s["op"] == "animation")
    assert step["blend"] == 0.25 and step["playEndMotion"] is True


def test_library_default_blend_is_read_from_the_shipped_library(tables):
    from chara.alone_actions import parse_library_defaults

    assert parse_library_defaults("local animation_blend_time = 0.3")["blend"] == 0.3
    assert parse_library_defaults(None)["blend"] == 0.5     # library absent
