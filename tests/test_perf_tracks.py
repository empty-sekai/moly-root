"""Synthetic contracts for the timeline track-tree reader.

The fixtures here are synthetic.  Real cut-scene and fixture-timeline packages
are large and are not in this repository, so every check is written against a
small in-memory corpus — and, as with the other domains, the wrong corpus is
deliberately built to show a check going red: a child re-homed to the timeline
must trip the reverse-edge check, a non-group track that owns children must
trip the nesting invariant, and a referenced object that carries no track
fields must surface in the unread list rather than vanish.

A track is identified by its script's ``m_ClassName``, and a track object is
any MonoBehaviour whose typetree carries the ``TrackAsset`` base fields
``m_Parent`` and ``m_Children``.  The package store is faked the same way the
other domains fake it: objects answer ``type.name``, ``path_id``,
``assets_file`` and ``read_typetree()``, and ``UnityPy.load`` is replaced, so
no real bundle is opened.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from perf import tracks


# -- synthetic packages -------------------------------------------------------


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
        self._next = 100

    def add(self, kind, tree, path_id=None):
        path_id = self._next + 100 if path_id is None else path_id
        self.objects.append(_Object(kind, path_id, tree, self))
        return path_id


def _script(bundle, path_id, class_name):
    """A MonoScript declaring *class_name*; behaviours point at it."""
    bundle.add("MonoScript", {"m_ClassName": class_name}, path_id)


def _tree(tree):
    """The typetree of a behaviour, without the TrackAsset fields."""
    return dict(tree)


def _behaviour(bundle, path_id, script, name, parent, children=(), extra=None):
    """One MonoBehaviour shaped as a TrackAsset: script pointer, parent, children."""
    data = {"m_Name": name, "m_Script": {"m_FileID": 0, "m_PathID": script},
            "m_Parent": {"m_FileID": 0, "m_PathID": parent},
            "m_Children": [{"m_FileID": 0, "m_PathID": child}
                           for child in children]}
    if extra:
        data.update(extra)
    bundle.add("MonoBehaviour", data, path_id)
    return path_id


def _cut_scene_bundle(bad_child_parent=None, children_under_control=False,
                      stray_reference=False, clip_has_track_fields=False):
    """One cut-scene-shaped bundle.

    Timeline (500) owns AnimationTrack (510) and ControlTrack (520) on its
    top level, a GroupTrack (530) whose children are two AnimationTracks
    (541, 542) and a MarkerTrack (560).  *bad_child_parent* re-homes child
    541: its parent pointer names the timeline instead of the group.
    *children_under_control* moves both children under the ControlTrack
    instead of the GroupTrack.  *stray_reference* makes the timeline name a
    non-track object (a SEClip) as if it were a track.
    """
    bundle = _Bundle()
    timeline, anim, ctl, grp, marker = 500, 510, 520, 530, 560
    inner1, inner2 = 541, 542
    scripts = {"anim": 9001, "ctl": 9002, "grp": 9003, "marker": 9004,
               "timeline": 9005, "clip": 9006}
    for class_name, script in (("AnimationTrack", scripts["anim"]),
                               ("ControlTrack", scripts["ctl"]),
                               ("GroupTrack", scripts["grp"]),
                               ("MarkerTrack", scripts["marker"]),
                               ("TimelineAsset", scripts["timeline"]),
                               ("SEClip", scripts["clip"])):
        _script(bundle, script, class_name)

    owner = ctl if children_under_control else grp
    _behaviour(bundle, anim, scripts["anim"], "anim", timeline)
    _behaviour(bundle, ctl, scripts["ctl"], "ctl", timeline,
               children=(inner1, inner2) if children_under_control else ())
    _behaviour(bundle, grp, scripts["grp"], "grp", timeline,
               children=() if children_under_control else (inner1, inner2))
    inner_parent = bad_child_parent if bad_child_parent is not None else owner
    _behaviour(bundle, inner1, scripts["anim"], "inner1", inner_parent)
    _behaviour(bundle, inner2, scripts["anim"], "inner2", owner)
    _behaviour(bundle, marker, scripts["marker"], "markers", timeline)

    tracks = [anim, ctl, grp]
    if stray_reference:
        if clip_has_track_fields:
            _behaviour(bundle, 9100, scripts["clip"], "not a track", timeline)
        else:
            bundle.add("MonoBehaviour", {
                "m_Name": "not a track",
                "m_Script": {"m_FileID": 0, "m_PathID": scripts["clip"]}},
                path_id=9100)
        tracks.append(9100)
    bundle.add("MonoBehaviour", {
        "m_Name": "cut_001", "m_Script": {"m_FileID": 0, "m_PathID": scripts["timeline"]},
        "m_Tracks": [{"m_FileID": 0, "m_PathID": t} for t in tracks],
        "m_MarkerTrack": {"m_FileID": 0, "m_PathID": marker}}, path_id=timeline)
    return bundle


def _write(path, bundle):
    """A placeholder file on disk; UnityPy.load is replaced by the test."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("placeholder", encoding="utf-8")
    return bundle


def _read(tmp_path, monkeypatch, bundle, name="mysekai__cut_scene__synth"):
    out = tmp_path / "out" / "tracks"
    bundle_path = tmp_path / "bundles" / name
    _write(bundle_path, bundle)
    monkeypatch.setattr(packages_module.UnityPy, "load",
                        lambda path: bundle)
    report = tracks.read_track_trees([str(bundle_path)], str(out))
    return report, report["packages"][0], out


# -- the three edges, and the reverse-edge check ------------------------------

def test_the_three_edges_reach_every_track_object(tmp_path, monkeypatch):
    """"The three edges reach every track object: the total reached by
    ``m_Tracks`` plus ``m_Children`` plus ``m_MarkerTrack`` must equal the
    number of track objects in the package.  Red when one of the edges is
    skipped — a reader that read only ``m_Tracks`` would leave every group
    child unvisited and the residual would be nonzero."""
    _, package, _ = _read(tmp_path, monkeypatch, _cut_scene_bundle())
    assert package["timelineAssets"] == 1
    assert package["top"] == 3
    assert package["marker"] == 1
    assert package["children"] == 2
    assert package["trackObjects"] == 6          # 3 top + 2 children + marker
    assert package["visited"] == 6
    assert package["residual"] == 0
    assert package["top"] + package["children"] + package["marker"] \
        == package["trackObjects"]


def test_children_belong_to_a_group_and_nest_nowhere(tmp_path, monkeypatch):
    """Red when a child is owned by a non-group track, or when a child owns
    children of its own: cut-scene children live in GroupTracks, one level."""
    _, package, _ = _read(tmp_path, monkeypatch, _cut_scene_bundle())
    assert package["groupTracks"] == 1
    assert package["childrenOfGroup"] == package["children"]
    assert package["childrenOfNonGroup"] == 0
    assert package["nested"] == 0


def test_a_child_moved_outside_its_group_is_caught(tmp_path, monkeypatch):
    """Red when the reverse-edge check stops counting: a child whose parent
    pointer names the timeline has been silently re-homed, and the reader
    must report that as *bad* rather than pass it."""
    bundle = _cut_scene_bundle(bad_child_parent=500)
    _, package, _ = _read(tmp_path, monkeypatch, bundle)
    assert package["bad"] == 1


def test_a_non_group_track_owning_children_is_caught(tmp_path, monkeypatch):
    """Red when children under a non-group track are classed as belonging to
    a group: the cut-scene family keeps every child under a GroupTrack, so a
    corpus where ControlTrack owns children must be reported as such."""
    bundle = _cut_scene_bundle(children_under_control=True)
    _, package, _ = _read(tmp_path, monkeypatch, bundle)
    assert package["children"] > 0
    assert package["childrenOfGroup"] == 0
    assert package["childrenOfNonGroup"] == package["children"]
    assert package["nested"] == 0


def test_an_unread_object_is_kept_and_reported(tmp_path, monkeypatch):
    """Red when a referenced object with no track fields is dropped: the
    reader must list it, class name and typetree, rather than lose it."""
    bundle = _cut_scene_bundle(stray_reference=True)
    _, package, out = _read(tmp_path, monkeypatch, bundle)
    assert package["unread"] == 1
    assert package["unreadClasses"] == {"SEClip": 1}
    document = json.loads(
        (out / "mysekai__cut_scene__synth.json").read_text(encoding="utf-8"))
    assert [entry["class"] for entry in document["unread"]] == ["SEClip"]
    assert document["unread"][0]["tree"]["m_Name"] == "not a track"


def test_a_missing_path_is_reported_and_never_empty(tmp_path):
    """Red when a missing path is read silently: UnityPy yields no objects for
    it, so nothing may be claimed — the package is reported missing instead."""
    missing = str(tmp_path / "bundles" / "mysekai__cut_scene__gone")
    report = tracks.read_track_trees([missing], str(tmp_path / "out"))
    assert report["missing"] == ["mysekai__cut_scene__gone"]
    assert report["packages"] == [{"package": "mysekai__cut_scene__gone",
                                   "missing": True}]
    assert list(Path(tmp_path / "out").glob("*.json")) == []
