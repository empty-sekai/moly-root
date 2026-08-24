"""Synthetic contracts for the clip-target resolver.

The fixtures are synthetic, deliberately small, and built to be readable.  They
test the resolver, not game semantics: the wrong corpus is constructed to fire
a specific red — an ``externals`` list shuffled against ``m_Dependencies``, a
pointer to a CAB that was never opened, a ``m_PathID`` that names no clip in a
loaded package, a null ``m_Clip`` pointer, a same-package clip.  No fixture is
used to infer what the game means.

The package store is faked the same way the other domains fake it: objects
answer ``type.name``, ``path_id``, ``assets_file`` and ``read_typetree()``, and
``UnityPy.load`` is replaced with a dispatcher that hands back the package
named by the path, so no real bundle is opened.
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module  # noqa: E402
from perf import clip_targets  # noqa: E402


class _Object:
    def __init__(self, kind, path_id, tree, archive):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = archive

    def read_typetree(self):
        return self._tree


class _Archive:
    """One fake serialized file: objects sharing one archive ``CAB-`` name."""

    def __init__(self, name):
        self.name = name
        self.externals = []
        self.objects = []
        self._next = 100

    def add(self, kind, tree, path_id=None):
        path_id = self._next + 100 if path_id is None else path_id
        self.objects.append(_Object(kind, path_id, tree, self))
        return path_id


class _Env:
    """One fake bundle: the objects ``UnityPy.load`` returns for a path."""

    def __init__(self, archive):
        self.archive = archive
        self.objects = []

    def add(self, kind, tree, path_id=None):
        self.archive.add(kind, tree, path_id)
        self.objects.append(self.archive.objects[-1])
        return path_id

    def script(self, path_id, class_name):
        """A MonoScript declaring *class_name*; behaviours point at it."""
        return self.add("MonoScript", {"m_ClassName": class_name}, path_id)

    def asset_bundle(self, dependencies):
        """An AssetBundle object declaring *dependencies* (bundle paths)."""
        return self.add("AssetBundle", {"m_Dependencies": list(dependencies),
                                        "m_Container": []})

    def behaviour(self, path_id, script, name, tree_extra):
        """One MonoBehaviour: script pointer plus whatever *tree_extra* carries."""
        data = {"m_Name": name,
                "m_Script": {"m_FileID": 0, "m_PathID": script}}
        data.update(tree_extra)
        return self.add("MonoBehaviour", data, path_id)


def _env(archive_name, externals=()):
    """A fresh fake package: one archive of that CAB name plus its externals.

    ``externals`` are the CAB names the file points into; the store reads them
    as FileIdentifier-like objects with a ``.name``, so each is wrapped.
    """
    env = _Env(_Archive(archive_name))
    env.archive.externals = [SimpleNamespace(name=str(e)) for e in externals]
    return env


def _wire(env_map):
    """A fake ``UnityPy.load`` that dispatches on the path's basename."""
    def loader(path):
        return env_map[os.path.basename(str(path))]
    return loader


def _run(tmp_path, monkeypatch, env_map, source_name, index_compare=False):
    """Write the world's package files and run the resolver over *source_name*."""
    root = tmp_path / "bundles"
    root.mkdir(parents=True, exist_ok=True)
    for name in env_map:
        (root / name).write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(packages_module.UnityPy, "load", _wire(env_map))
    out = tmp_path / "out"
    report = clip_targets.read_clip_targets(
        [str(root / source_name)], str(out), str(root),
        index_compare=index_compare)
    return report, report["packages"][0]


# -- the resolver, told apart from a naive index-aligned one -----------------


def test_a_clip_is_resolved_to_its_motion_package(tmp_path, monkeypatch):
    """A cross-package ``m_Clip`` resolves to the package the CAB name owns,
    with the clip's own name.  This is the green the lane promises; it sits
    under c1..c6 and depends on ``load_dependencies`` having opened the CAB
    the pointer names."""
    src = _env("CAB-src", ["CAB-motion"])
    motion = _env("CAB-motion")
    track = src.script(9001, "AnimationTrack")
    playable = src.script(9002, "AnimationPlayableAsset")
    src.asset_bundle(["mysekai/character_motion"])
    src.behaviour(510, track, "anim", {
        "m_Parent": {"m_FileID": 0, "m_PathID": 1},
        "m_Children": [],
        "m_Clips": [{"m_Asset": {"m_FileID": 0, "m_PathID": 600}}],
    })
    src.behaviour(600, playable, "playable", {
        "m_Clip": {"m_FileID": 1, "m_PathID": 700},
    })
    motion.add("AnimationClip", {"m_Name": "clipA"}, 700)

    _, package = _run(tmp_path, monkeypatch,
                      {"mysekai__cut_scene__synth": src,
                       "mysekai__character_motion": motion},
                      "mysekai__cut_scene__synth")
    assert package["clips"] == 1
    assert package["cross"] == 1 and package["same"] == 0
    assert package["null"] == 0 and package["unresolved"] == 0
    assert package["targetKinds"] == {"mysekai__character_motion": 1}


def test_a_null_m_clip_is_a_legal_empty_clip_not_a_failure(tmp_path, monkeypatch):
    """A playable asset whose ``m_Clip`` has ``m_PathID`` of 0 is a legal empty
    segment — 808 of them in fixture_timeline — and must be ``null``, never an
    unresolved failure.  Red when a reader counts it as a failure or drops it."""
    src = _env("CAB-src", ["CAB-motion"])
    motion = _env("CAB-motion")
    track = src.script(9001, "AnimationTrack")
    playable = src.script(9002, "AnimationPlayableAsset")
    src.asset_bundle(["mysekai/character_motion"])
    src.behaviour(510, track, "anim", {
        "m_Parent": {"m_FileID": 0, "m_PathID": 1},
        "m_Children": [],
        "m_Clips": [{"m_Asset": {"m_FileID": 0, "m_PathID": 600}}],
    })
    src.behaviour(600, playable, "playable", {
        "m_Clip": {"m_FileID": 0, "m_PathID": 0},
    })
    _, package = _run(tmp_path, monkeypatch,
                      {"mysekai__cut_scene__synth": src,
                       "mysekai__character_motion": motion},
                      "mysekai__cut_scene__synth")
    assert package["clips"] == 1
    assert package["null"] == 1 and package["unresolved"] == 0
    assert package["cross"] == 0 and package["same"] == 0


def test_a_same_package_clip_is_reported_as_same(tmp_path, monkeypatch):
    """A clip whose ``m_Clip`` points back into its own package is ``same``, not
    cross — the 3 cut-scene / 20 fixture same-package clips.  Red if a reader
    counts it as cross because it never compares the target to the source."""
    src = _env("CAB-src")
    track = src.script(9001, "AnimationTrack")
    playable = src.script(9002, "AnimationPlayableAsset")
    src.asset_bundle([])
    src.behaviour(510, track, "anim", {
        "m_Parent": {"m_FileID": 0, "m_PathID": 1},
        "m_Children": [],
        "m_Clips": [{"m_Asset": {"m_FileID": 0, "m_PathID": 600}}],
    })
    src.behaviour(600, playable, "playable", {
        "m_Clip": {"m_FileID": 0, "m_PathID": 700},
    })
    src.add("AnimationClip", {"m_Name": "local"}, 700)

    _, package = _run(tmp_path, monkeypatch,
                      {"mysekai__cut_scene__synth": src},
                      "mysekai__cut_scene__synth")
    assert package["clips"] == 1
    assert package["same"] == 1 and package["cross"] == 0
    assert package["targetKinds"]["mysekai__cut_scene__synth"] == 1


# -- planted violations ------------------------------------------------------


def test_shuffled_externals_and_dependencies_are_caught(tmp_path, monkeypatch):
    """Red when the resolver pairs ``externals[i]`` with ``m_Dependencies[i]``:
    here the two are deliberately in different orders, so an index-aligned
    resolver names ``pkg_aaa`` for the clip that the CAB name ``CAB-bbb``
    actually owns.  The correct resolver resolves the clip to ``pkg_bbb`` and
    the counter-check must report the disagreement."""
    src = _env("CAB-src", ["CAB-bbb", "CAB-aaa"])   # index 0 -> bbb
    aaa = _env("CAB-aaa")
    bbb = _env("CAB-bbb")
    track = src.script(9001, "AnimationTrack")
    playable = src.script(9002, "AnimationPlayableAsset")
    # m_Dependencies in the OPPOSITE order from externals: index 0 -> pkg_aaa.
    src.asset_bundle(["pkg_aaa", "pkg_bbb"])
    src.behaviour(510, track, "anim", {
        "m_Parent": {"m_FileID": 0, "m_PathID": 1},
        "m_Children": [],
        "m_Clips": [{"m_Asset": {"m_FileID": 0, "m_PathID": 600}}],
    })
    src.behaviour(600, playable, "playable", {
        "m_Clip": {"m_FileID": 1, "m_PathID": 700},
    })
    bbb.add("AnimationClip", {"m_Name": "clipBBB"}, 700)
    aaa.add("AnimationClip", {"m_Name": "clipAAA"}, 700)

    _, package = _run(tmp_path, monkeypatch,
                      {"mysekai__cut_scene__synth": src,
                       "pkg_aaa": aaa, "pkg_bbb": bbb},
                      "mysekai__cut_scene__synth",
                      index_compare=True)
    # CAB-name wins: the clip belongs to pkg_bbb, not the index-paired pkg_aaa.
    assert package["cross"] == 1
    assert package["targetKinds"] == {"pkg_bbb": 1}
    assert package["indexDisagree"] == 1
    # Both resolvers genuinely disagree: index-alignment names the other pkg.
    assert clip_targets.resolve_target_by_index(
        SimpleNamespace(dependencies=["pkg_aaa", "pkg_bbb"]), None,
        {"m_FileID": 1, "m_PathID": 700}) == "pkg_aaa"


def test_a_pointer_to_an_unopened_cab_is_reported_not_skipped(tmp_path, monkeypatch):
    """Red when a pointer whose CAB was never opened is silently skipped: the
    lane says an unresolved reference must be reported with the archive it
    wanted, so a clip whose ``m_Clip`` names a CAB the store did not open
    surfaces as ``unresolved`` with the archive it wanted, never vanishes."""
    src = _env("CAB-src", ["CAB-never-opened"])
    track = src.script(9001, "AnimationTrack")
    playable = src.script(9002, "AnimationPlayableAsset")
    src.asset_bundle([])          # no dependency loads that CAB
    src.behaviour(510, track, "anim", {
        "m_Parent": {"m_FileID": 0, "m_PathID": 1},
        "m_Children": [],
        "m_Clips": [{"m_Asset": {"m_FileID": 0, "m_PathID": 600}}],
    })
    src.behaviour(600, playable, "playable", {
        "m_Clip": {"m_FileID": 1, "m_PathID": 700},
    })
    _, package = _run(tmp_path, monkeypatch,
                      {"mysekai__cut_scene__synth": src},
                      "mysekai__cut_scene__synth")
    assert package["clips"] == 1
    assert package["unresolved"] == 1 and package["cross"] == 0


def test_a_path_id_missing_in_the_target_package_is_unresolved(tmp_path, monkeypatch):
    """Red when a ``m_PathID`` the target package does not contain is read as if
    it resolved: the store follows the CAB, finds the package, but the path id
    is absent, so it must be reported unresolved (with the archive it wanted),
    never a fake success."""
    src = _env("CAB-src", ["CAB-motion"])
    motion = _env("CAB-motion")
    track = src.script(9001, "AnimationTrack")
    playable = src.script(9002, "AnimationPlayableAsset")
    src.asset_bundle(["mysekai/character_motion"])
    src.behaviour(510, track, "anim", {
        "m_Parent": {"m_FileID": 0, "m_PathID": 1},
        "m_Children": [],
        "m_Clips": [{"m_Asset": {"m_FileID": 0, "m_PathID": 600}}],
    })
    src.behaviour(600, playable, "playable", {
        "m_Clip": {"m_FileID": 1, "m_PathID": 999},   # absent in motion
    })
    motion.add("AnimationClip", {"m_Name": "clipA"}, 700)  # only 700 exists

    _, package = _run(tmp_path, monkeypatch,
                      {"mysekai__cut_scene__synth": src,
                       "mysekai__character_motion": motion},
                      "mysekai__cut_scene__synth")
    assert package["clips"] == 1
    assert package["unresolved"] == 1 and package["cross"] == 0
