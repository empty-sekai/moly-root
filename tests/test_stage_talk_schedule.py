"""The stage demo's talk schedule, exercised through node.

The schedule is what makes the furniture family play at all: those performances
carry no character animation on the timeline, so the character's motions come
from the talk script's call stream instead.  The stream has no absolute times --
only waits -- and one of the waits (``wait_click``) has no duration in the data,
so the demo supplies a stand-in.  These tests pin two things: that the clock
follows the waits the data *does* give, and that the invented part stays counted
separately instead of disappearing into the total.

The module is browser JavaScript, so it is run under node rather than
reimplemented here -- a Python mirror of it would be a second implementation to
disagree with.
"""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, "..", "examples", "stage")
MODULE = pathlib.Path(os.path.join(STAGE, "talk-schedule.js")).resolve().as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH")


def run_schedule(steps, options=None):
    """Build a schedule under node and return it as a dict."""
    script = (
        "import { buildSchedule, motionsWanted, animationAt, textAt, "
        "WAIT_CLICK_STAND_IN } from %s;\n"
        "const steps = %s;\n"
        "const options = %s;\n"
        "const schedule = buildSchedule(steps, options);\n"
        "const wanted = motionsWanted(schedule);\n"
        "process.stdout.write(JSON.stringify({\n"
        "  ...schedule,\n"
        "  standIn: WAIT_CLICK_STAND_IN,\n"
        "  wantedNames: [...wanted.names].sort(),\n"
        "  unresolvedTokens: [...wanted.unresolvedTokens].sort(),\n"
        "}));\n"
    ) % (json.dumps(MODULE),
         json.dumps(steps), json.dumps(options or {}))
    done = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise AssertionError(f"node failed: {done.stderr[:800]}")
    return json.loads(done.stdout)


def test_the_clock_follows_the_waits_the_data_gives():
    out = run_schedule([
        {"op": "change_animation", "who": 1, "motion": "mov_a"},
        {"op": "wait_time", "seconds": 1.5},
        {"op": "text", "text": "first"},
        {"op": "wait_time_on_auto_mode", "seconds": 0.5},
        {"op": "change_animation", "who": 1, "motion": "mov_b"},
    ])
    assert out["duration"] == 2.0
    assert out["dataSeconds"] == 2.0
    assert out["standInSeconds"] == 0
    assert out["dataWaits"] == 2
    assert out["clickWaits"] == 0
    at = {event["op"]: event["at"] for event in out["events"]}
    assert at["text"] == 1.5
    assert [e["at"] for e in out["events"] if e["op"] == "change_animation"] == [0, 2.0]


def test_the_invented_wait_is_counted_apart_from_the_data():
    # `wait_click` waits for a player click and carries no duration, so the
    # seconds it contributes are the demo's, not the game's.  Red when they are
    # folded into one total, which would make a schedule whose every beat was
    # invented look the same as one the data timed.
    out = run_schedule([
        {"op": "text", "text": "a"},
        {"op": "wait_click"},
        {"op": "text", "text": "b"},
        {"op": "wait_time", "seconds": 3},
    ], {"waitClickSeconds": 2})
    assert out["standInSeconds"] == 2
    assert out["dataSeconds"] == 3
    assert out["duration"] == 5
    assert out["clickWaits"] == 1
    assert out["dataWaits"] == 1


def test_a_schedule_timed_entirely_by_the_stand_in_says_so():
    out = run_schedule([
        {"op": "text", "text": "a"},
        {"op": "wait_click"},
        {"op": "text", "text": "b"},
        {"op": "wait_click"},
    ])
    assert out["dataSeconds"] == 0
    assert out["standInSeconds"] == out["duration"] > 0


def test_the_wanted_motions_are_the_resolved_names():
    # A motion still shaped like `Table.key` means the extraction did not resolve
    # that constant; it is reported on its own rather than counted as a name the
    # library is missing, because those are different failures.
    out = run_schedule([
        {"op": "change_animation", "who": 1, "motion": "mov_a"},
        {"op": "change_animation", "who": 1, "motion": "mov_b"},
        {"op": "change_animation", "who": 1, "motion": "Motions.unresolved"},
    ])
    assert out["wantedNames"] == ["mov_a", "mov_b"]
    assert out["unresolvedTokens"] == ["Motions.unresolved"]


def test_an_operator_the_schedule_does_not_place_is_reported_by_name():
    out = run_schedule([
        {"op": "text", "text": "a"},
        {"op": "some_operator_nobody_placed"},
        {"op": "some_operator_nobody_placed"},
    ])
    assert out["unscheduled"] == {"some_operator_nobody_placed": 2}
    assert [e["op"] for e in out["events"]] == ["text"]


def test_changing_animation_overrides_rather_than_stacks():
    # `change_animation` replaces the current motion.  Asserted through the
    # lookup the player uses, so a schedule that appended instead of replacing
    # would show the wrong motion at a time after the second call.
    script = (
        "import { buildSchedule, animationAt } from %s;\n"
        "const s = buildSchedule([\n"
        "  {op:'change_animation', motion:'mov_a'},\n"
        "  {op:'wait_time', seconds:1},\n"
        "  {op:'change_animation', motion:'mov_b'},\n"
        "  {op:'wait_time', seconds:1},\n"
        "]);\n"
        "process.stdout.write(JSON.stringify({\n"
        "  before: animationAt(s, 0.5)?.motion,\n"
        "  after: animationAt(s, 1.5)?.motion,\n"
        "  end: animationAt(s, 99)?.motion,\n"
        "}));\n"
    ) % json.dumps(MODULE)
    done = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr[:800]
    out = json.loads(done.stdout)
    assert out == {"before": "mov_a", "after": "mov_b", "end": "mov_b"}
