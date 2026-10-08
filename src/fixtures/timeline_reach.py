"""Which packages hold ParticleSystems a timeline drives, whatever their family.

A ParticleSystem is drawn through the fixture particle schema when a player
owns it: its prefab plays it on awake (the furniture families, the effect
families), or a timeline drives it.  A timeline reaches a system three ways:

* a ``ControlPlayableAsset`` clip: its ``sourceGameObject`` resolves on the
  director that plays the timeline (``ExposedReference.Resolve``: the
  director's ``m_ExposedReferences`` by exposed name, else the reference's
  default value), or through ``prefabGameObject``; ``GetControllableParticleSystems``
  takes every ParticleSystem of that object's Transform subtree;
* an ``EffectClip`` (the cut-scene effect track): its ``template.prefab`` is
  instantiated and its systems play;
* a ``SignalReceiver`` reaction: each persistent call's target (a
  ParticleSystem's ``Play`` / ``Stop`` on a scene object).

The furniture and effect families are covered by their routed domain.  A
package of another family whose timelines reach a system -- a site package
whose scene carries a director, or whose step items play their own timeline --
is covered by this reach, not by a name: :func:`reached_packages` reads every
package it is given, so a package added later is found the same way.

The selection is name-blind and read from the bundles alone.  The scene's own
play-on-awake systems stay the scene reader's; only the systems a timeline
reaches are selected, through their package.
"""
from __future__ import annotations

import os

from core.assets.packages import PackageStore, pairs

TIMELINE_SCRIPT = "TimelineAsset"
RECEIVER_SCRIPT = "SignalReceiver"
CONTROL_CLIP = "ControlPlayableAsset"
EFFECT_CLIP = "EffectClip"


def _norm(key):
    """A PropertyName as the typetree gives it (an int, or a one-field struct)."""
    if isinstance(key, dict):
        key = key.get("id", next(iter(key.values()), None))
    return str(key)


def _ptr(value):
    return value if isinstance(value, dict) and "m_PathID" in value else None


def _walk_tracks(store, record, pointers, out, seen):
    for pointer in pointers or []:
        target = store.follow(record, _ptr(pointer))
        if target is None or target in seen:
            continue
        seen.add(target)
        out.append(target)
        _walk_tracks(store, target[0], target[0].tree(target[1]).get("m_Children"), out, seen)


def _game_object(store, record, path_id):
    if record.kinds.get(path_id) == "GameObject":
        return record, path_id
    return store.follow(record, record.tree(path_id).get("m_GameObject"))


def subtree_particles(store, record, game_object):
    """``(bundle, archive, component id)`` of every ParticleSystem of the
    GameObject's Transform subtree, inactive objects included."""
    found, stack, visited = [], [(record, game_object)], set()
    while stack:
        rec, go = stack.pop()
        if (rec.archive, go) in visited:
            continue
        visited.add((rec.archive, go))
        transform = None
        for item in rec.tree(go).get("m_Component") or []:
            pointer = item.get("component") if isinstance(item, dict) else item[1]
            target = store.follow(rec, pointer)
            if target is None:
                continue
            kind = target[0].kinds.get(target[1])
            if kind == "ParticleSystem":
                found.append((target[0].bundle, target[0].archive, target[1]))
            elif kind in ("Transform", "RectTransform"):
                transform = target
        if transform is None:
            continue
        for child in transform[0].tree(transform[1]).get("m_Children") or []:
            child_transform = store.follow(transform[0], child)
            if child_transform is None:
                continue
            owner = store.follow(child_transform[0], child_transform[0].tree(child_transform[1]).get("m_GameObject"))
            if owner is not None:
                stack.append(owner)
    return found


def package_reach(store, name):
    """The reach rows of one loaded package: ``(kind, clip, particles)`` per
    target, ``particles`` the reached ParticleSystems as in
    :func:`subtree_particles`, and the pointers that did not resolve."""
    package = store.package(name)
    if package is None:
        return None
    directors, timelines, receivers = [], [], []
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind == "PlayableDirector":
                directors.append((record, path_id))
            elif kind == "MonoBehaviour":
                script = record.script_of(path_id)
                if script == TIMELINE_SCRIPT:
                    timelines.append((record, path_id))
                elif script == RECEIVER_SCRIPT:
                    receivers.append((record, path_id))
    if not directors and not timelines and not receivers:
        return []
    store.load_dependencies([name])
    played_by = {}
    for record, path_id in directors:
        tree = record.tree(path_id)
        timeline = store.follow(record, _ptr(tree.get("m_PlayableAsset")))
        exposed = {_norm(key): value for key, value in
                   pairs((tree.get("m_ExposedReferences") or {}).get("m_References"))}
        played_by.setdefault(timeline, []).append((record, exposed))
    rows = []

    def reach(kind, clip, record, pointer):
        target = store.follow(record, pointer) if pointer and pointer.get("m_PathID") else None
        if target is None:
            rows.append({"kind": kind, "clip": clip, "particles": [],
                         "unresolved": bool(pointer and pointer.get("m_PathID"))})
            return
        owner = _game_object(store, *target)
        particles = subtree_particles(store, *owner) if owner else []
        rows.append({"kind": kind, "clip": clip, "particles": particles, "unresolved": False})

    for record, timeline in timelines:
        tree = record.tree(timeline)
        tracks = []
        _walk_tracks(store, record, tree.get("m_Tracks"), tracks, set())
        marker = store.follow(record, _ptr(tree.get("m_MarkerTrack")))
        if marker is not None:
            tracks.append(marker)
        players = played_by.get((record, timeline), [])
        for track_record, track in tracks:
            for clip in track_record.tree(track).get("m_Clips") or []:
                asset = store.follow(track_record, _ptr(clip.get("m_Asset")))
                if asset is None:
                    continue
                script = asset[0].script_of(asset[1])
                fields = asset[0].tree(asset[1])
                label = clip.get("m_DisplayName")
                if script == CONTROL_CLIP:
                    source = fields.get("sourceGameObject") or {}
                    exposed_name = _norm(source.get("exposedName"))
                    prefab = _ptr(fields.get("prefabGameObject"))
                    if prefab and prefab.get("m_PathID"):
                        reach("ControlPrefab", label, asset[0], prefab)
                    if not players:
                        reach("Control", label, asset[0], _ptr(source.get("defaultValue")))
                    for director_record, exposed in players:
                        if exposed_name in exposed:
                            reach("Control", label, director_record, _ptr(exposed[exposed_name]))
                        else:
                            reach("Control", label, asset[0], _ptr(source.get("defaultValue")))
                elif script == EFFECT_CLIP:
                    template = fields.get("template") or {}
                    reach("Effect", label, asset[0], _ptr(template.get("prefab")))
    for record, receiver in receivers:
        events = record.tree(receiver).get("m_Events") or {}
        for signal, reaction in zip(events.get("m_Signals") or [], events.get("m_Events") or []):
            for call in ((reaction or {}).get("m_PersistentCalls") or {}).get("m_Calls") or []:
                reach("Signal", call.get("m_MethodName"), record, _ptr(call.get("m_Target")))
    return rows


def reached_packages(paths, bundle_root=None, progress=None):
    """``{package: {"rows": n, "kinds": {...}, "timelines": [...]}}`` for every
    package holding a ParticleSystem that a timeline of the given packages
    reaches; each package in *paths* is read in its own store (its
    dependencies loaded from *bundle_root*), so the answer does not depend on
    which other packages were named."""
    reached = {}
    for index, path in enumerate(sorted(paths)):
        name = os.path.basename(str(path))
        store = PackageStore([str(path)], bundle_root)
        rows = package_reach(store, name) or []
        for row in rows:
            for bundle in sorted({particle[0] for particle in row["particles"]}):
                entry = reached.setdefault(bundle, {"rows": 0, "kinds": {}, "timelines": set()})
                entry["rows"] += 1
                entry["kinds"][row["kind"]] = entry["kinds"].get(row["kind"], 0) + 1
                entry["timelines"].add(name)
        if progress is not None:
            progress(index, name, rows)
    return {name: dict(entry, timelines=sorted(entry["timelines"])) for name, entry in sorted(reached.items())}
