"""Producer metadata through the viewer adapter and real Three.js mixer."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from chara.motion_library import INDEX_FORMAT_VERSION, _clip_meta, _source_clip_metadata


def source_index(loop, *, missing=False):
    obj = SimpleNamespace(assets_file=SimpleNamespace(name="synthetic"), path_id=123)
    muscle = {"m_StartTime": 0.0, "m_StopTime": 1.0}
    if not missing:
        muscle["m_LoopTime"] = loop
    metadata = _source_clip_metadata(obj, {"m_MuscleClip": muscle})
    segment = _clip_meta("demo_L", [(0.0,), (1.0,)], 30, metadata)
    return {"version": INDEX_FORMAT_VERSION, "clips": {"demo": {"segments": {"L": segment}}}}


PROGRAM = r'''
import fs from 'node:fs';
process.on('uncaughtException', error => { console.error(error.stack); process.exitCode = 1; });
const Seg = await import(process.argv[1]);
const T = await import(process.argv[2]);
function animation(name) {
  return new T.AnimationClip(name, 1, [new T.NumberKeyframeTrack('.position[x]', [0, 1], [0, 1])]);
}
const results = {};
for (const [name, index] of Object.entries(JSON.parse(fs.readFileSync(0, 'utf8')))) {
  for (const mode of ['single', 'family']) {
    const clip = animation('demo_L');
    const loops = Seg.loopByNameFromIndex(index, [clip]);
    const mixer = new T.AnimationMixer(new T.Object3D());
    const controller = new Seg.SegmentController(mixer, new Map([[clip.name, clip]]), loops);
    const family = Seg.groupClips([clip])[0];
    const started = mode === 'single' ? controller.playSegment(clip.name, family) : controller.playFamily(family);
    const action = controller.current;
    mixer.update(2.25);
    results[`${name}/${mode}`] = {
      loopMetadata: loops.get(clip.name), started,
      mode: action ? (action.loop === T.LoopRepeat ? 'repeat' : 'once') : null,
      running: action ? action.isRunning() : false,
      phase: controller.phase, unresolved: controller.unresolved,
    };
  }
}
// An unresolved request does not reconfigure or replace the playing action.
{
  const first = animation('known_O'), unknown = animation('unknown_L');
  const mixer = new T.AnimationMixer(new T.Object3D());
  const ctl = new Seg.SegmentController(mixer, new Map([[first.name, first], [unknown.name, unknown]]),
                                       new Map([[first.name, false], [unknown.name, null]]));
  ctl.playSegment(first.name);
  const action = ctl.current;
  const started = ctl.playSegment(unknown.name);
  results.preserve = { started, sameAction: ctl.current === action, clip: ctl.currentName,
                       unresolved: ctl.unresolved };
  ctl.playFamily(Seg.groupClips([unknown])[0]);
  results.recovered = { clip: ctl.currentName, unresolved: ctl.unresolved };
}
// Family S/L/E sequencing is an explicit playback policy, separate from the
// single-segment source flag. Exercise its event transitions in the real mixer.
{
  const clips = ['demo_S', 'demo_L', 'demo_E'].map(animation);
  const mixer = new T.AnimationMixer(new T.Object3D());
  const ctl = new Seg.SegmentController(mixer, new Map(clips.map(c => [c.name, c])),
                                       new Map(clips.map(c => [c.name, null])));
  ctl.playFamily(Seg.groupClips(clips)[0]);
  const phases = [ctl.phase];
  mixer.update(1.1); phases.push(ctl.phase);
  ctl.stopToEnd(); phases.push(ctl.phase);
  mixer.update(1.1); phases.push(ctl.phase);
  results.sequence = phases;
}
try {
  Seg.loopByNameFromIndex({version: 99, clips: {}});
  results.unknownVersion = false;
} catch { results.unknownVersion = true; }
process.stdout.write(JSON.stringify(results));
'''


@pytest.fixture(scope="module")
def playback(tmp_path_factory):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the real viewer contract")
    cases = {
        "v1_true": {"version": 1, "clips": {"demo": {"segments": {"L": {"name": "demo_L", "loop": True}}}}},
        "v1_false": {"version": 1, "clips": {"demo": {"segments": {"L": {"name": "demo_L", "loop": False}}}}},
        "legacy": {"clips": {"demo": {"segments": {"L": {"name": "demo_L", "loop": True}}}}},
        "v2_true": source_index(True),
        "v2_false": source_index(False),
        "v2_missing": source_index(None, missing=True),
        "v2_absent": source_index(None, missing=True),
        "v2_invalid": source_index("false"),
        "v2_conflicting": source_index(False),
        "v2_unindexed": {"version": 2, "clips": {}},
    }
    cases["v2_absent"]["clips"]["demo"]["segments"]["L"].pop("sourceLoopTime")
    cases["v2_conflicting"]["clips"]["demo"]["segments"]["L"]["loop"] = True
    viewer = Path(__file__).parents[1] / "examples" / "viewer"
    # Run byte-identical browser modules inside an explicit ESM scope. This
    # supports older Node versions without changing the browser package, and
    # keeps Node's async module reads on the native test filesystem under WSL.
    modules = tmp_path_factory.mktemp("viewer-esm")
    for name in ("segments.js", "three.module.min.js"):
        (modules / name).write_bytes((viewer / name).read_bytes())
    (modules / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    result = subprocess.run([node, "--input-type=module", "-e", PROGRAM,
                             (modules / "segments.js").as_uri(), (modules / "three.module.min.js").as_uri()],
                            input=json.dumps(cases), text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return json.loads(result.stdout)


@pytest.mark.parametrize("case,metadata", [
    ("v1_true", True), ("v1_false", False), ("legacy", True),
    ("v2_true", True), ("v2_false", False), ("v2_missing", None),
    ("v2_absent", None), ("v2_invalid", None), ("v2_conflicting", False),
    ("v2_unindexed", None),
])
@pytest.mark.parametrize("mode", ["single", "family"])
def test_produced_loop_metadata_controls_single_playback_without_changing_family_policy(playback, case, metadata, mode):
    result = playback[f"{case}/{mode}"]
    assert result["loopMetadata"] is metadata
    if mode == "single" and metadata is None:
        assert result["started"] is False
        assert result["mode"] is None
        assert result["unresolved"] == {"clip": "demo_L", "reason": "missing loop metadata"}
        assert result["phase"] == "idle"
    else:
        loop = mode == "family" or metadata
        assert result["started"] is True
        assert result["mode"] == ("repeat" if loop else "once")
        assert result["running"] is bool(loop)
        assert result["unresolved"] is None


def test_unresolved_selection_preserves_current_playback_and_can_recover(playback):
    assert playback["preserve"]["started"] is False
    assert playback["preserve"]["sameAction"] is True
    assert playback["preserve"]["clip"] == "known_O"
    assert playback["preserve"]["unresolved"]["clip"] == "unknown_L"
    assert playback["recovered"] == {"clip": "unknown_L", "unresolved": None}


def test_family_transition_sequence_and_unknown_index_version(playback):
    assert playback["sequence"] == ["S", "L", "E", "idle"]
    assert playback["unknownVersion"] is True
