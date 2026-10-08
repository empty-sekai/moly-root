"""Synthetic contracts for the timeline clip reader.

The fixtures are synthetic.  Real cut-scene and fixture-timeline packages are
large and not in this repository, so every check is written against a small
in-memory corpus.  Three checks inject a deliberate violation — a track holding
both an infinite clip and clips, a clip whose ``m_ParentTrack`` names another
track, and a curve given without its keyframes — and each must go red when the
reader stops reporting it, and green when the reader does; the rest assert the
honest, lossless reading of a well-formed corpus.  Synthetic fixtures test the
code only; game semantics are never inferred from them.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from perf import clips


# -- synthetic packages ------------------------------------------------------


class _Object:
    def __init__(self, kind, path_id, tree, archive):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = archive

    def read_typetree(self):
        return self._tree


class _Bundle:
    """One fake serialized file: objects sharing one archive file."""

    def __init__(self):
        self.name = "CAB-synth"
        self.externals = []
        self.objects = []
        self._next = 1000

    def add(self, kind, tree, path_id=None):
        path_id = self._next + 100 if path_id is None else path_id
        self.objects.append(_Object(kind, path_id, tree, self))
        return path_id

    def get(self, path_id):
        """The object with *path_id*, as the reader would address it."""
        return next(obj for obj in self.objects if obj.path_id == path_id)


def _script(bundle, path_id, class_name):
    """A MonoScript declaring *class_name*; behaviours point at it."""
    bundle.add("MonoScript", {"m_ClassName": class_name}, path_id)


def _curve(keyframes=(), pre=2, post=2, rot=4):
    """One AnimationCurve as a typetree would serialise it."""
    return {"m_Curve": list(keyframes), "m_PreInfinity": pre,
            "m_PostInfinity": post, "m_RotationOrder": rot}


def _keyframe(time=0.0, value=0.0):
    return {"time": time, "value": value, "inSlope": 0.0, "outSlope": 0.0,
            "weightedMode": 0, "inWeight": 0.0, "outWeight": 0.0}


def _clip(track_pid, parent=None, start=0.0, duration=1.0, timescale=1.0,
          mixin=None, mixout=None, asset=0):
    """One inline TimelineClip.  *parent* defaults to *track_pid*."""
    parent = track_pid if parent is None else parent
    return {
        "m_Version": 1, "m_Start": start, "m_ClipIn": 0.0,
        "m_Asset": {"m_FileID": 0, "m_PathID": asset},
        "m_Duration": duration, "m_TimeScale": timescale,
        "m_ParentTrack": {"m_FileID": 0, "m_PathID": parent},
        "m_EaseInDuration": 0.0, "m_EaseOutDuration": 0.0,
        "m_BlendInDuration": -1.0, "m_BlendOutDuration": -1.0,
        "m_MixInCurve": mixin if mixin is not None else _curve(),
        "m_MixOutCurve": mixout if mixout is not None else _curve(),
        "m_BlendInCurveMode": 0, "m_BlendOutCurveMode": 0,
        "m_ExposedParameterNames": [],
        "m_AnimationCurves": {"m_FileID": 0, "m_PathID": 0},
        "m_Recordable": 0, "m_PostExtrapolationMode": 0,
        "m_PreExtrapolationMode": 0, "m_PostExtrapolationTime": 0.0,
        "m_PreExtrapolationTime": 0.0, "m_DisplayName": "clip",
    }


def _track(bundle, class_name, name, clips=(), infinite=0, animclip=0,
           parent=0, children=(), markers=()):
    """One track-shaped MonoBehaviour: TrackAsset fields plus the class."""
    bundle._next += 1
    path_id = bundle._next
    _script(bundle, bundle._next + 900000, class_name)
    data = {
        "m_Name": name, "m_Script": {"m_FileID": 0, "m_PathID": bundle._next + 900000},
        "m_Parent": {"m_FileID": 0, "m_PathID": parent},
        "m_Children": [{"m_FileID": 0, "m_PathID": child} for child in children],
        "m_AnimClip": {"m_FileID": 0, "m_PathID": animclip},
        "m_InfiniteClip": {"m_FileID": 0, "m_PathID": infinite},
        "m_Clips": list(clips),
        "m_Markers": {"m_Objects": list(markers)},
    }
    return bundle.add("MonoBehaviour", data, path_id)


def _read(tmp_path, monkeypatch, bundle, name="mysekai__cut_scene__synth"):
    out = tmp_path / "out" / "clips"
    bundle_path = tmp_path / "bundles" / name
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(packages_module.UnityPy, "load", lambda path: bundle)
    report = clips.read_timeline_clips([str(bundle_path)], str(out))
    return report, report["aggregate"], out


def _clip_track_bundle(bad_parent=None, clip_only=True):
    """An AnimationTrack with a clip, plus a standalone infinite-only track.

    *bad_parent* re-homes the first clip's ``m_ParentTrack`` to another track,
    so the reverse-edge check must trip.  When *clip_only*, the clip-bearing
    track holds clips and no infinite clip (the well-formed case); when False
    it holds both, tripping the XOR bucket.
    """
    bundle = _Bundle()
    clip_track = _track(bundle, "AnimationTrack", "anim")
    _track(bundle, "AnimationTrack", "inf", infinite=12345)
    clip = _clip(clip_track, parent=bad_parent)
    if not clip_only:
        bundle.get(clip_track)._tree["m_InfiniteClip"] = \
            {"m_FileID": 0, "m_PathID": 9999}
    bundle.get(clip_track)._tree["m_Clips"] = [clip]
    return bundle


# -- honest reading of a well-formed corpus ----------------------------------


def test_the_xor_invariant_holds_on_a_clean_corpus(tmp_path, monkeypatch):
    """"A clean corpus is counted exactly: one clip-only track, one
    infinite-only track, no track holding both or neither.  Red when the reader
    drops a bucket or merges it into another."""
    report, agg, _ = _read(tmp_path, monkeypatch, _clip_track_bundle())
    assert agg["infiniteOnly"] == 1
    assert agg["clipOnly"] == 1
    assert agg["bothOrNeither"] == 0
    assert agg["clips"] == 1


def test_double_timing_values_are_not_truncated(tmp_path, monkeypatch):
    """"A double with many digits survives exactly.  Red when the reader runs a
    value through ``float()`` or rounds it."""
    bundle = _Bundle()
    track = _track(bundle, "AnimationTrack", "anim")
    start = 5.947452109927935
    duration = 0.16666666666666785
    clip = _clip(track, start=start, duration=duration, timescale=1.003)
    bundle.get(track)._tree["m_Clips"] = [clip]
    report, agg, out = _read(tmp_path, monkeypatch, bundle)
    assert agg["doubleExact"] is True
    assert agg["doubleChecked"] == 1
    document = (out / "mysekai__cut_scene__synth.json").read_text(encoding="utf-8")
    assert str(start) in document and str(duration) in document


def test_a_curve_is_exported_with_its_keyframes(tmp_path, monkeypatch):
    """"A mix curve is exported keyframe-for-keyframe, not as a bare flag.  Red
    when the reader collapses a curve to its keyframe count or drops it."""
    bundle = _Bundle()
    track = _track(bundle, "AnimationTrack", "anim")
    mixin = _curve(keyframes=[_keyframe(0.0, 0.0), _keyframe(1.0, 1.0)],
                   pre=1, post=3, rot=2)
    clip = _clip(track, mixin=mixin)
    bundle.get(track)._tree["m_Clips"] = [clip]
    report, agg, out = _read(tmp_path, monkeypatch, bundle)
    document = json.loads((out / "mysekai__cut_scene__synth.json")
                          .read_text(encoding="utf-8"))
    curve = document["tracks"][0]["clips"][0]["m_MixInCurve"]
    assert len(curve["curve"]) == 2
    assert curve["curve"][0]["time"] == 0.0 and curve["curve"][0]["value"] == 0.0
    assert curve["curve"][1]["time"] == 1.0 and curve["curve"][1]["value"] == 1.0
    assert curve["m_PreInfinity"] == 1 and curve["m_PostInfinity"] == 3
    assert curve["m_RotationOrder"] == 2


# -- injected violations: the reader must report them, not swallow them --------


def test_a_track_holding_both_infinite_clip_and_clips_is_reported(
        tmp_path, monkeypatch):
    """"Red when the reader fails to report the XOR violation: a track with
    both ``m_InfiniteClip`` and ``m_Clips`` must land in ``bothOrNeither``,
    never silently in one of the exclusive buckets."""
    report, agg, _ = _read(tmp_path, monkeypatch, _clip_track_bundle(clip_only=False))
    assert agg["bothOrNeither"] == 1
    assert agg["infiniteOnly"] == 1          # the standalone infinite track
    assert agg["clipOnly"] == 0
    assert agg["animationTracks"] == 2


def test_a_clip_pointing_at_another_track_is_caught(tmp_path, monkeypatch):
    """"Red when the reverse-edge check stops counting: a clip whose
    ``m_ParentTrack`` names a different track must be reported as *bad*."""
    report, agg, _ = _read(tmp_path, monkeypatch, _clip_track_bundle(bad_parent=9900))
    assert agg["parentBad"] == 1
    assert agg["parentUnresolved"] == 0


def test_a_curve_given_without_keyframes_still_exports_what_it_has(
        tmp_path, monkeypatch):
    """"Red when a curve is reduced to its count: a curve with keyframes is
    exported with them, so the reader never turns keyframes into a bare
    ``curve`` list that only knows how many there are."""
    bundle = _Bundle()
    track = _track(bundle, "AnimationTrack", "anim")
    mixin = _curve(keyframes=[_keyframe(0.0, 0.0)], pre=0, post=0, rot=0)
    clip = _clip(track, mixin=mixin, mixout=_curve())
    bundle.get(track)._tree["m_Clips"] = [clip]
    report, agg, out = _read(tmp_path, monkeypatch, bundle)
    document = json.loads((out / "mysekai__cut_scene__synth.json")
                          .read_text(encoding="utf-8"))
    curve = document["tracks"][0]["clips"][0]["m_MixInCurve"]
    assert len(curve["curve"]) == 1
    assert curve["curve"][0]["value"] == 0.0
    assert curve["m_PreInfinity"] == 0 and curve["m_RotationOrder"] == 0


def test_no_clip_key_is_silently_dropped(tmp_path, monkeypatch):
    """"A serialised key the stub does not list is still exported raw, so a
    field the contract never saw cannot be lost.  Red when the reader exports a
    subset of what a clip carries."""
    bundle = _Bundle()
    track = _track(bundle, "AnimationTrack", "anim")
    clip = _clip(track)
    clip["m_FutureField"] = 7              # not in STUB_CLIP_FIELDS
    bundle.get(track)._tree["m_Clips"] = [clip]
    report, agg, out = _read(tmp_path, monkeypatch, bundle)
    assert agg["droppedKeys"] == 0
    document = json.loads((out / "mysekai__cut_scene__synth.json")
                          .read_text(encoding="utf-8"))
    exported = document["tracks"][0]["clips"][0]
    assert exported["m_FutureField"] == 7
    assert "m_FutureField" in report["clipFieldSyncDiff"]["inTypetreeOnly"]


# -- the track's own settings and its recorded infinite clip ------------------


def _streamed(frames):
    """A StreamedClip payload: big-endian frames of (time, count, keys)."""
    import struct
    payload = b""
    for time, keys in frames:
        payload += struct.pack(">fi", time, len(keys))
        for index, coefficients in keys:
            payload += struct.pack(">i4f", index, *coefficients)
    words = [int.from_bytes(payload[i:i + 4], "big") for i in range(0, len(payload), 4)]
    return {"data": words, "curveCount": 6}


def _recorded_clip():
    """A generic clip on the Animator's own object: position and euler."""
    sentinel = -3.4028234663852886e+38
    frames = [
        (sentinel, [(0, (0.0, 0.0, 0.0, 3.9))]),
        (0.0, [(0, (0.0, 0.0, 0.0, 3.9)), (1, (0.0, 0.0, 0.0, 0.0)),
               (2, (0.0, 0.0, 0.25, -3.0)), (3, (0.0, 0.0, 0.0, 0.0)),
               (4, (0.0, 0.0, 0.0, 180.0)), (5, (0.0, 0.0, 0.0, 0.0))]),
        (2.0, [(0, (0.0, 0.0, 0.0, 3.9)), (1, (0.0, 0.0, 0.0, 0.0)),
               (2, (0.0, 0.0, 0.0, -2.5)), (3, (0.0, 0.0, 0.0, 0.0)),
               (4, (0.0, 0.0, 0.0, 0.0)), (5, (0.0, 0.0, 0.0, 0.0))]),
    ]
    return {
        "m_Name": "Recorded", "m_Legacy": False, "m_SampleRate": 60.0,
        "m_MuscleClip": {
            "m_StartTime": 0.0, "m_StopTime": 2.0, "m_LoopTime": 0,
            "m_Clip": {"data": {
                "m_StreamedClip": _streamed(frames),
                "m_DenseClip": {"m_CurveCount": 0, "m_FrameCount": 0,
                                "m_SampleRate": 60.0, "m_BeginTime": 0.0,
                                "m_SampleArray": []},
                "m_ConstantClip": {"data": []}}}},
        "m_ClipBindingConstant": {"genericBindings": [
            {"path": 0, "attribute": 1, "typeID": 4, "customType": 0, "isPPtrCurve": 0},
            {"path": 0, "attribute": 4, "typeID": 4, "customType": 4, "isPPtrCurve": 0},
        ]},
    }


def test_an_infinite_clip_is_exported_with_its_decoded_curves(tmp_path, monkeypatch):
    """"The recorded clip a track plays is exported slot by slot with each
    key's segment coefficients and its binding.  Red when the reader keeps
    only the pointer, resamples, or drops the binding columns."""
    bundle = _Bundle()
    clip_id = bundle.add("AnimationClip", _recorded_clip(), 777)
    track = _track(bundle, "AnimationTrack", "1", infinite=clip_id)
    bundle.get(track)._tree["m_InfiniteClipOffsetPosition"] = {"x": -1.25, "y": 0.0, "z": 0.5}
    bundle.get(track)._tree["m_InfiniteClipPostExtrapolation"] = 1
    report, agg, out = _read(tmp_path, monkeypatch, bundle)
    assert agg["infiniteClipsDecoded"] == 1
    assert agg["infiniteClipsUnresolved"] == 0
    row = json.loads((out / "mysekai__cut_scene__synth.json")
                     .read_text(encoding="utf-8"))["tracks"][0]
    assert row["fields"]["m_InfiniteClipOffsetPosition"] == {"x": -1.25, "y": 0.0, "z": 0.5}
    assert row["fields"]["m_InfiniteClipPostExtrapolation"] == 1
    assert "m_Clips" not in row["fields"] and "m_Name" not in row["fields"]
    clip = row["infiniteClip"]
    assert clip["asset"] == {"file": "CAB-synth", "pathId": "777"}
    assert clip["name"] == "Recorded"
    assert clip["sourceStopTime"] == 2.0 and clip["sourceLoopTime"] is False
    assert [c["slot"] for c in clip["curves"]] == [0, 1, 2, 3, 4, 5]
    z = clip["curves"][2]
    assert (z["typeID"], z["attribute"], z["path"], z["component"]) == (4, 1, 0, 2)
    assert z["kind"] == "cubic"
    assert z["keys"] == [[0.0, 0.0, 0.0, 0.25, -3.0], [2.0, 0.0, 0.0, 0.0, -2.5]]
    yaw = clip["curves"][4]
    assert (yaw["attribute"], yaw["component"]) == (4, 1)
    assert yaw["keys"][0][4] == 180.0 and yaw["keys"][1][4] == 0.0


def test_an_unresolved_infinite_clip_is_reported_not_dropped(tmp_path, monkeypatch):
    """"A pointer that names no object gives ``None`` and an unread entry.
    Red when the reader raises, or writes an empty curve table as if read."""
    report, agg, out = _read(tmp_path, monkeypatch, _clip_track_bundle())
    assert agg["infiniteClipsUnresolved"] == 1
    assert agg["infiniteClipsDecoded"] == 0
    document = json.loads((out / "mysekai__cut_scene__synth.json")
                          .read_text(encoding="utf-8"))
    rows = [t for t in document["tracks"] if t["name"] == "inf"]
    assert rows[0]["infiniteClip"] is None
    assert {"kind": "infiniteClip", "pointer": {"m_FileID": 0, "m_PathID": "12345"}} in document["unread"]
