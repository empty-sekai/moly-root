"""Each animation segment's clip: which package, which AnimationClip.

A TimelineClip names an asset through its ``m_Asset`` pointer; on an
``AnimationTrack`` that asset is an ``AnimationPlayableAsset``, and that asset
names the actual ``AnimationClip`` through ``m_Clip``.  Resolving ``m_Clip``
means following two pointers across package boundaries — the playable asset is
often in the same package as the timeline, but the clip it plays usually is
not — and it must do so by CAB name, never by index, because a serialized
file's ``externals`` order and the bundle's ``m_Dependencies`` order are
different orderings of different things.

Every ``m_Clip`` is reported once.  A pointer that is empty (``m_PathID`` of 0)
is a legal state — a fixture-timeline segment that owns no clip — and is
reported as ``null`` rather than as a failure.  A pointer that names a CAB the
store has not opened is an unresolved reference and is reported with the
archive it wanted, never silently dropped.

The playable asset is told apart from the other clip assets (a VoiceClip, a
ChangeEyePresetClip, a SkipNextClip) by ``MonoScript.m_ClassName``, never by an
object name or by a name substring.  Only ``AnimationPlayableAsset`` assets are
walked for a clip; the others carry no ``m_Clip`` and are not part of the
count.  The track that carries such a clip is *not* selected on: an animation
segment is one whose asset is an ``AnimationPlayableAsset``, on whatever track
class the timeline puts it, and several packages carry their whole animation on
a track class other than ``AnimationTrack``.

Each package's report holds the same records twice.  ``clips`` is the flat list
in walk order — the order the serialized file stores its objects in, which no
consumer reproduces — and its positions mean nothing outside this reader.
``keyedClips`` carries the same records with the key a consumer pairs on: the
owning track's path id (``trackPathId``) and the clip's index within that
track's ``m_Clips`` (``clipIndex``).  A playable asset shared by two clips is
still reported once, keyed at the first clip that named it.
"""
import os
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore
from core.jsonio import write_json

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

PLAYABLE_CLASS = "AnimationPlayableAsset"

# The one track class a class-whitelist consumer would keep.  Nothing in this
# reader selects on it: an animation segment is one whose asset is an
# AnimationPlayableAsset, whatever track carries it.  It exists only so
# :func:`pair_targets_by_track_class` can reproduce the wrong reading.
NAIVE_TRACK_CLASS = "AnimationTrack"


def _is_track(tree):
    """True when a MonoBehaviour's typetree carries the TrackAsset base fields."""
    return "m_Parent" in tree and "m_Children" in tree


def _resolve(store, record, pointer):
    """A pointer to ``(record, path id)``, or ``None`` when it names nothing."""
    if not pointer or not pointer.get("m_PathID"):
        return None
    return store.follow(record, pointer)


def resolve_target_by_index(package, record, pointer):
    """The package that *index alignment* would name for a pointer.

    This is the deliberately-wrong resolver criterion c7's counter-check uses.
    The correct resolver looks up the CAB name in ``record.externals[m_FileID-1]``
    and asks the store which package owns it.  This one takes the *same index*
    into ``package.dependencies`` instead and returns that dependency's logical
    name.  When a serialized file's ``externals`` and a bundle's
    ``m_Dependencies`` are not in the same order (and they are different
    things), the two name different packages and the disagreement is visible.

    Returns ``None`` when the index is out of range; ``m_FileID`` of 0 is a
    same-file pointer and is not a cross-package dependency at all.
    """
    index = (pointer or {}).get("m_FileID", 0) - 1
    if index < 0 or index >= len(package.dependencies):
        return None
    return package.dependencies[index].replace("/", "__")


def pair_targets_by_key(clip_document, target_document):
    """Pair every timeline clip with its animation target by explicit key.

    A target document lists one entry per resolved ``AnimationPlayableAsset``
    in the order the package's objects happen to be walked, which is not the
    order any consumer reads tracks in.  So a consumer must pair by the key the
    two documents share — the owning track's ``pathId`` and the clip's index in
    that track's ``m_Clips`` — never by position in the two lists and never by
    track name (names repeat within a package).

    Returns the tally for one package: how many targets the producer emitted
    (``produced``, non-null only), how many clips found an entry (``matched``)
    and how many of those carry a non-null target (``resolved``), how many
    clips the consumer walked (``clips``), and how many keys the producer
    emitted twice (``duplicateKeys``, which must be zero for the key to be a
    key at all).
    """
    entries = (target_document or {}).get("keyedClips") or []
    keyed, duplicates = {}, 0
    for entry in entries:
        key = (str(entry.get("trackPathId")), entry.get("clipIndex"))
        if key in keyed:
            duplicates += 1
        keyed[key] = entry.get("target")
    matched = resolved = clips = 0
    for track in (clip_document or {}).get("tracks") or []:
        for index in range(len(track.get("clips") or [])):
            clips += 1
            key = (str(track.get("pathId")), index)
            if key not in keyed:
                continue
            matched += 1
            if keyed[key]:
                resolved += 1
    return {"produced": sum(1 for entry in entries if entry.get("target")),
            "entries": len(entries), "matched": matched, "resolved": resolved,
            "clips": clips, "duplicateKeys": duplicates}


def pair_targets_by_track_class(clip_document, target_document,
                                track_class=NAIVE_TRACK_CLASS):
    """The tally a class-whitelist, position-cursor consumer gets instead.

    This is the deliberately-wrong pairer the criteria use as a counter-check,
    the same way :func:`resolve_target_by_index` is the deliberately-wrong
    resolver.  It keeps only tracks whose class name is *track_class* and hands
    each of their clips the next entry of the flat target list.  Two things go
    wrong at once: the flat list is not in track order, and an animation
    carried by any other track class is not seen at all — a package whose
    animation lives on a differently-named track scores zero here while every
    one of its targets was in fact produced.

    Returns the same tally shape as :func:`pair_targets_by_key`.
    """
    targets = (target_document or {}).get("clips") or []
    cursor = matched = resolved = clips = 0
    for track in (clip_document or {}).get("tracks") or []:
        for _ in track.get("clips") or []:
            clips += 1
            if track.get("class") != track_class:
                continue
            target = targets[cursor] if cursor < len(targets) else None
            cursor += 1
            matched += 1
            if target:
                resolved += 1
    return {"produced": sum(1 for target in targets if target),
            "entries": len(targets), "matched": matched, "resolved": resolved,
            "clips": clips, "duplicateKeys": 0}


def _closure_provenance(store, roots):
    """Every declared-but-absent package in the closure, with its provenance.

    Walks the dependency closure of *roots* (logical package names), tracking
    which bundle's ``m_Dependencies`` declared each dependency.  For every
    package a bundle declares but the store cannot open (not on disk) it
    returns a record: the package's ``name``, the raw ``dependency`` path it
    was declared as, the ``declaring`` bundle, and the shortest ``chain`` of
    package names from a root down to it.  `store.missing` lists these names
    already; this adds the provenance the next step (re-ingesting the originals
    by the game's own package list) needs.
    """
    parent = {}     # child -> declaring package logical name
    dep_of = {}     # child -> raw dependency path as declared
    seen = set(roots)
    queue = list(roots)
    while queue:
        name = queue.pop(0)
        package = store.package(name)
        if package is None:
            continue
        for dep in package.dependencies:
            child = dep.replace("/", "__")
            if child not in parent:
                parent[child] = name
                dep_of[child] = dep
            if child not in seen:
                seen.add(child)
                queue.append(child)
    missing = []
    for node in sorted(seen):
        if store.package(node) is not None:
            continue
        chain = []
        cur = node
        while cur in parent:
            chain.append(cur)
            cur = parent[cur]
        chain.append(cur)
        chain.reverse()
        missing.append({"name": node, "dependency": dep_of.get(node),
                        "declaring": parent.get(node), "chain": chain})
    return missing


def _clip_record(store, record, clip):
    """The report record for one ``m_Clip`` pointer, per the lane's shape.

    A null pointer (``m_PathID`` of 0) is a legal empty clip and is ``None``.
    A pointer that names a CAB the store has not opened is an ``unresolved``
    record carrying the archive it wanted.  Anything else is the resolved
    ``targetPackage`` (the owning package's logical name) and ``clipName``.
    """
    m_clip = clip.get("m_Clip") if isinstance(clip, dict) else None
    if not m_clip or not m_clip.get("m_PathID"):
        return None
    target = store.follow(record, m_clip)
    if target is None:
        return {"unresolved": True,
                "wantedArchive": store.archive_of(record, m_clip)}
    return {"targetPackage": target[0].bundle,
            "clipName": target[0].tree(target[1]).get("m_Name", "")}


def _walk_package(store, name, out, index_compare=False):
    """One package: every ``AnimationPlayableAsset.m_Clip``, with its counts.

    With *index_compare* the walk also feeds each resolved ``m_Clip`` pointer
    to :func:`resolve_target_by_index` and tallies how many of those name a
    different package than the CAB-name resolver — criterion c7's
    counter-check, which is zero when a package declares exactly one dependency
    and would disagree otherwise.
    """
    package = store.package(name)
    if package is None:
        return {"package": name, "missing": True}
    document = {"package": name, "clips": [], "keyedClips": []}
    counts = {"package": name,
              "targetKinds": {}, "clips": 0, "null": 0,
              "unresolved": 0, "cross": 0, "same": 0,
              "targets": {}, "classes": {}, "empty": True,
              "indexDisagree": 0}
    seen = set()
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour":
                continue
            tree = record.tree(path_id)
            if not _is_track(tree) or "m_Clips" not in tree:
                continue
            for clip_index, clip in enumerate(tree.get("m_Clips") or []):
                asset = clip.get("m_Asset") if isinstance(clip, dict) else None
                target = _resolve(store, record, asset)
                if target is None:
                    continue
                trecord, tpid = target
                if trecord.script_of(tpid) != PLAYABLE_CLASS:
                    continue
                key = (trecord, tpid)
                if key in seen:
                    continue
                seen.add(key)
                counts["classes"][PLAYABLE_CLASS] = \
                    counts["classes"].get(PLAYABLE_CLASS, 0) + 1
                playable_tree = trecord.tree(tpid)
                record_ = _clip_record(store, record, playable_tree)
                counts["clips"] += 1
                document["clips"].append(record_)
                document["keyedClips"].append({"trackPathId": str(path_id),
                                               "clipIndex": clip_index,
                                               "target": record_})
                if record_ is None:
                    counts["null"] += 1
                elif record_.get("unresolved"):
                    counts["unresolved"] += 1
                else:
                    target_pkg = record_["targetPackage"]
                    if index_compare:
                        wrong = resolve_target_by_index(package, record,
                                                        playable_tree.get("m_Clip"))
                        if wrong is not None and wrong != target_pkg:
                            counts["indexDisagree"] += 1
                    counts["targets"][target_pkg] = \
                        counts["targets"].get(target_pkg, 0) + 1
                    if target_pkg == name:
                        counts["same"] += 1
                    else:
                        counts["cross"] += 1
    counts["targetKinds"] = {target: count for target, count
                             in counts["targets"].items() if count}
    counts["empty"] = counts["clips"] == 0
    if counts["clips"] or document["clips"]:
        write_json(out / f"{name}.json", document)
    return counts


def read_clip_targets(bundles, out_dir, bundle_root=None, load_deps=True,
                      index_compare=False):
    """Resolve every animation segment's ``m_Clip``, one JSON per package.

    *bundles* names the packages to read; the last path segment of each is the
    package name.  *out_dir* receives one document per package: its ``clips``
    list, one entry per ``AnimationPlayableAsset.m_Clip``, each ``None`` for a
    null pointer, an ``unresolved`` record for a pointer the store cannot
    follow, or ``{"targetPackage", "clipName"}`` for a resolved clip; and its
    ``keyedClips`` list, the same records in the same order but each carrying
    the ``trackPathId`` / ``clipIndex`` key a consumer pairs on.

    Dependencies are loaded first, because a package's pointers reach other
    packages and the store only knows a CAB once the package that owns it has
    been opened.  With *load_deps* false, the walk runs without that — the
    cross-package resolution collapses, which is what criterion c7's positive
    control uses.

    With *index_compare* the walk also tallies how many resolved clips the
    index-aligned resolver (criterion c7's counter-check) names differently
    than the CAB-name resolver.

    Returns a report: per package the clip counts, the null/unresolved/cross/
    same split, the histogram of target packages, and what classes were found.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    by_name = {os.path.basename(str(path)): str(path) for path in bundles}
    names = sorted(by_name)
    store = PackageStore(bundles, bundle_root)
    if load_deps:
        store.load_dependencies(names)
    records = []
    for name in names:
        if not os.path.exists(by_name[name]):
            records.append({"package": name, "missing": True})
            continue
        records.append(_walk_package(store, name, out,
                                     index_compare=index_compare))
    missing = [r["package"] for r in records if r.get("missing")]
    if load_deps:
        declared = _closure_provenance(store, names)
    else:
        declared = []
    return {"packages": records, "missing": missing,
            "declaredMissing": [r["name"] for r in declared],
            "declaredMissingChains": declared}
