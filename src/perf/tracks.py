"""Timeline track trees: every track a TimelineAsset owns, by class.

A timeline's tracks are not all in one list.  ``TimelineAsset.m_Tracks`` is the
top level; ``TrackAsset.m_Children`` holds the tracks nested under a track;
and ``TimelineAsset.m_MarkerTrack`` is one track of its own.  A walk that read
only ``m_Tracks`` would report the top level and silently drop every group's
children — for the cut-scene family that is more than half of the tracks.

Classes are told apart by ``MonoScript.m_ClassName``, never by object name or
by a name substring.  Every field is read from the typetree, keyed by its
serialised name, because ``m_Children``/``m_Parent``/``m_Clips``/``m_Markers``
all live on the base class ``TrackAsset``: a track class whose per-class field
list looks empty still carries them.

A *track object* is any MonoBehaviour whose typetree carries ``m_Parent`` and
``m_Children``.  An object referenced from a timeline edge that does not carry
them is kept, class name and typetree together, in the package document's
``unread`` list rather than being dropped silently.

UnityPy is silent about a path that does not exist (it yields zero objects),
so every path the caller names is asserted to exist before the package is
reported on; a missing path is a reported ``missing`` entry, never an empty
document.
"""
import os
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore
from core.jsonio import write_json

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

TIMELINE_CLASS = "TimelineAsset"
GROUP_CLASS = "GroupTrack"

# ``TimelineAsset``'s own serialised settings, as opposed to its track list.
# Named explicitly: ``m_Tracks`` and ``m_MarkerTrack`` are pointers and are
# traversed instead, and a wildcard here would swallow them.
TIMELINE_SETTING_FIELDS = ("m_Version", "m_DurationMode", "m_FixedDuration",
                           "m_EditorSettings")


def _is_track(tree):
    """True when a MonoBehaviour's typetree carries the TrackAsset base fields."""
    return "m_Parent" in tree and "m_Children" in tree


def _resolve(store, record, pointer):
    """A pointer to ``(record, path id)``, or ``None`` when it names nothing."""
    if not pointer or not pointer.get("m_PathID"):
        return None
    return store.follow(record, pointer)


class _Walker:
    """One package's walk: visits tracks, records the reverse-edge check."""

    def __init__(self, store, document):
        self.store = store
        self.document = document
        self.nodes = {}
        self.bad = 0
        self.unresolved = 0

    def node(self, record, path_id, owner):
        """The tree node for one track; *owner* is the ``(record, path id)`` of
        the object that referenced it, or ``None`` if it has none."""
        tree = record.tree(path_id)
        key = (record, path_id)
        cls = record.script_of(path_id)
        if not _is_track(tree):
            self.document["unread"].append(
                {"class": cls, "name": tree.get("m_Name", ""), "tree": tree})
            return None
        expected = self.store.follow(record, tree.get("m_Parent"))
        if expected != owner:
            self.bad += 1
        node = self.nodes.get(key)
        if node is None:
            node = {"class": cls, "name": tree.get("m_Name", ""), "children": []}
            self.nodes[key] = node
            for child in tree.get("m_Children") or []:
                target = _resolve(self.store, record, child)
                if target is None:
                    self.unresolved += 1
                    continue
                child_node = self.node(target[0], target[1], key)
                if child_node is not None:
                    node["children"].append(child_node)
        return node


def _walk_package(store, name, out):
    """One package: its timelines, its documents, and its structural counts."""
    package = store.package(name)
    document = {"package": name, "timelines": [], "unread": []}
    counts = {"package": name, "missing": False, "timelineAssets": 0,
              "top": 0, "marker": 0, "children": 0, "childrenOfGroup": 0,
              "childrenOfNonGroup": 0, "nested": 0, "groupTracks": 0,
              "trackObjects": 0, "visited": 0, "residual": 0,
              "bad": 0, "unresolved": 0, "unread": 0,
              "classes": {}, "unreadClasses": {}}

    all_tracks, children_owner, children_targets = {}, {}, set()
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour":
                continue
            tree = record.tree(path_id)
            if not _is_track(tree):
                continue
            all_tracks[record, path_id] = (record, path_id)
            cls = record.script_of(path_id)
            if cls == GROUP_CLASS:
                counts["groupTracks"] += 1
            for child in tree.get("m_Children") or []:
                children_owner[cls] = children_owner.get(cls, 0) + 1
                target = _resolve(store, record, child)
                if target is not None:
                    children_targets.add(target)
    counts["trackObjects"] = len(all_tracks)
    counts["children"] = sum(children_owner.values())
    counts["childrenOfGroup"] = children_owner.get(GROUP_CLASS, 0)
    counts["childrenOfNonGroup"] = counts["children"] - counts["childrenOfGroup"]
    counts["nested"] = sum(
        1 for target in children_targets
        if target in all_tracks and len(target[0].tree(target[1]).get("m_Children") or []) > 0)

    walker = _Walker(store, document)
    timelines = []
    seen_timelines = set()
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour":
                continue
            if record.script_of(path_id) != TIMELINE_CLASS:
                continue
            if (record, path_id) in seen_timelines:
                continue
            seen_timelines.add((record, path_id))
            timelines.append((record, path_id))
    counts["timelineAssets"] = len(timelines)
    for trecord, tpid in timelines:
        ttree = trecord.tree(tpid)
        timeline = {"name": ttree.get("m_Name", ""), "tracks": [], "marker": None}
        # The asset's own settings, not just its track list.  `m_DurationMode`
        # is the one that decides what a timeline's length *is*: BasedOnClips=0
        # takes it from the clip extents, FixedLength=1 takes it from
        # `m_FixedDuration`, and a consumer computing length from clips is only
        # right in the first case.  Measured over this corpus: 774 of 774 are
        # BasedOnClips and none is FixedLength, so computing from clips happens
        # to be correct here -- **but that is a fact about the data, not a
        # property of the format**, and it was being assumed rather than read.
        # Carrying it means a corpus that does use a fixed length cannot pass
        # unnoticed.
        settings = {key: value for key, value in ttree.items()
                    if key in TIMELINE_SETTING_FIELDS}
        if settings:
            timeline["settings"] = settings
        for pointer in ttree.get("m_Tracks") or []:
            target = _resolve(store, trecord, pointer)
            if target is None:
                walker.unresolved += 1
                continue
            counts["top"] += 1
            wrapper = walker.node(target[0], target[1], (trecord, tpid))
            if wrapper is not None:
                timeline["tracks"].append(wrapper)
        marker = _resolve(store, trecord, ttree.get("m_MarkerTrack"))
        if marker is not None:
            counts["marker"] += 1
            timeline["marker"] = walker.node(marker[0], marker[1], (trecord, tpid))
        document["timelines"].append(timeline)

    for (record, path_id), node in walker.nodes.items():
        cls = record.script_of(path_id)
        counts["classes"][cls] = counts["classes"].get(cls, 0) + 1
    counts["visited"] = len(walker.nodes)
    counts["residual"] = counts["trackObjects"] - counts["visited"]
    counts["bad"] = walker.bad
    counts["unresolved"] = walker.unresolved
    counts["unread"] = len(document["unread"])
    for entry in document["unread"]:
        cls = entry["class"]
        counts["unreadClasses"][cls] = counts["unreadClasses"].get(cls, 0) + 1
    if counts["trackObjects"] or document["timelines"] or document["unread"]:
        write_json(out / f"{name}.json", document)
    return counts


def read_track_trees(bundles, out_dir, bundle_root=None):
    """Read every timeline's track tree from *bundles*, one JSON per package.

    *bundles* names the packages to read; the last path segment of each is the
    package name.  *out_dir* receives one document per package: its timelines,
    each with the track tree (class name, track name, parent-child nesting,
    marker track) and the ``unread`` list of referenced objects that carry no
    structured track fields.  A package whose path does not exist is reported
    in ``missing`` and never claimed to hold zero timelines.

    Returns a report: per package the three edge counts (``top``, ``marker``,
    ``children``), how many track objects are left unvisited by the three
    edges (``residual``), the reverse-edge checks (``bad`` parent pointers,
    ``nested`` children, tracks that own children without being a
    ``GroupTrack``), the class histogram of the visited tracks, and the class
    histogram of the objects the walk could not read.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    by_name = {os.path.basename(str(path)): str(path) for path in bundles}
    names = sorted(by_name)
    store = PackageStore(bundles, bundle_root)
    records = []
    for name in names:
        if not os.path.exists(by_name[name]):
            records.append({"package": name, "missing": True})
            continue
        records.append(_walk_package(store, name, out))
    classes, unread_classes = {}, {}
    for record in records:
        for cls, count in record.get("classes", {}).items():
            classes[cls] = classes.get(cls, 0) + count
        for cls, count in record.get("unreadClasses", {}).items():
            unread_classes[cls] = unread_classes.get(cls, 0) + count
    return {"packages": records, "classes": classes,
            "unreadClasses": unread_classes,
            "missing": [r["package"] for r in records if r.get("missing")]}


if __name__ == "__main__":
    raise SystemExit("track-tree reader is a library; call read_track_trees")
