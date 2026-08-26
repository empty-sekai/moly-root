"""Timeline clips and their timing: when each clip acts, not who it acts on.

A track owns zero or more ``TimelineClip`` edges (``m_Clips``) and, on an
``AnimationTrack``, optionally one track-level ``m_InfiniteClip``.  Reading the
clips answers the *when* question — clip start, clip-in, duration, easing and
blending windows, extrapolation — while the *who* question (which clip's asset
holds the animation, i.e. ``AnimationPlayableAsset.m_Clip``) is answered by the
E1c clip-targets reader.  The two are kept separate so a change in one never
silently changes the other.

Every time value is a ``double`` in Unity and is exported as such: no
``float()``, no rounding.  Animation curves are exported with their keyframes,
not as a bare "has a curve" flag, because the shape of the easing curve is the
thing the timeline says.  Markers live on the track (``TrackAsset.m_Markers``),
not on any clip, and are read out into a class histogram.

Three fields must not be confused (this lane re-produces the distinction the F6
contract fixed; it must not re-derive it):

* ``TrackAsset.m_AnimClip``           — a legacy slot.  On both families' every
  ``AnimationTrack`` it is zero (grep of the stub shows the field at ``0x20``,
  typed ``AnimationClip``, marked ``[Obsolete]`` and
  ``[FormerlySerializedAs("m_animClip")]``); judging emptiness on it would drop
  every recorded track-level animation.
* ``AnimationTrack.m_InfiniteClip``@0x100 — the real track-level animation.
* ``AnimationPlayableAsset.m_Clip``   — the clip's asset (E1c lane, not here).

An animation track therefore holds either clips or an infinite clip, never both
and never neither; the XOR invariant below is re-checked per family, and the
``m_AnimClip`` all-zero check is paired with a positive control on
``m_InfiniteClip`` so that an all-zero reading cannot be mistaken for a broken
reader.

``m_Clips`` are serialised inline inside each track's list — they are not separate
objects — so each clip is read from the track's own typetree.  The same edge
list is what ``m_ParentTrack`` must point back at, so the reverse reference is
checked for every clip.
"""
import json
import os
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore
from core.jsonio import write_json

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

ANIMATION_TRACK_CLASS = "AnimationTrack"
LEGACY_ANIMCLIP = "m_AnimClip"
INFINITE_CLIP = "m_InfiniteClip"

# The serialised field table of TimelineClip, from the stub, used as the
# reference for the field-symmetric-difference check.  An actual typetree that
# carries a key not listed here is reported and still exported, never dropped.
STUB_CLIP_FIELDS = [
    "m_Version", "m_Start", "m_ClipIn", "m_Asset", "m_Duration", "m_TimeScale",
    "m_ParentTrack", "m_EaseInDuration", "m_EaseOutDuration", "m_BlendInDuration",
    "m_BlendOutDuration", "m_MixInCurve", "m_MixOutCurve", "m_BlendInCurveMode",
    "m_BlendOutCurveMode", "m_ExposedParameterNames", "m_AnimationCurves",
    "m_Recordable", "m_PostExtrapolationMode", "m_PreExtrapolationMode",
    "m_PostExtrapolationTime", "m_PreExtrapolationTime", "m_DisplayName",
]

CURVE_CLIP_FIELDS = ("m_MixInCurve", "m_MixOutCurve")
DOUBLE_CHECK_FIELDS = ("m_Start", "m_Duration", "m_TimeScale")

# Engine bookkeeping every MonoBehaviour carries; not part of an asset's own
# content.  Listed rather than pattern-matched, so a real field starting with
# ``m_`` cannot be absorbed by a wildcard.
ASSET_ENGINE_FIELDS = frozenset({
    "m_GameObject", "m_Enabled", "m_Script", "m_ObjectHideFlags",
    "m_CorrespondingSourceObject", "m_PrefabInstance", "m_PrefabAsset",
})

# BlendCurveMode { Auto=0, Manual=1 }.
BLEND_CURVE_MODE = ["Auto", "Manual"]
# ClipExtrapolation { None=0, Hold=1, Loop=2, PingPong=3, Continue=4 }.
CLIP_EXTRAPOLATION = ["None", "Hold", "Loop", "PingPong", "Continue"]
# Serialised keyframe fields of an AnimationCurve, by their typetree name.
CURVE_LEVEL_FIELDS = ("m_PreInfinity", "m_PostInfinity", "m_RotationOrder")

KEYS = ("animationTracks", "animClipNonzero", "infiniteClipNonzero",
        "infiniteOnly", "clipOnly", "bothOrNeither", "clips", "parentBad",
        "parentUnresolved", "droppedKeys")


def _is_track(tree):
    """True when a MonoBehaviour's typetree carries the TrackAsset base fields."""
    return "m_Parent" in tree and "m_Children" in tree


def _resolve(store, record, pointer):
    """A pointer to ``(record, path id)``, or ``None`` when it names nothing."""
    if not pointer or not pointer.get("m_PathID"):
        return None
    return store.follow(record, pointer)


def _enum(value, names):
    """One enum as ``{"value": n, "name": "..."}``, both spelled out."""
    value = int(value) if value else 0
    return {"value": value,
            "name": names[value] if 0 <= value < len(names) else "?"}


class _Walker:
    """One package's clip walk: reads every track's clips, keeps the counts."""

    def __init__(self, store, document, marker_classes):
        self.store = store
        self.document = document
        self.marker_classes = marker_classes
        self.typetree_keys = set()
        self.parent_bad = 0
        self.parent_unresolved = 0
        self.double_ok = True
        self.double_checked = 0
        self.dropped = 0
        # The assets clips point at, deduplicated by content.  A clip's
        # ``m_Asset`` was exported as a bare pointer and the object behind it
        # was never read, so every field it carries -- the whole preset table of
        # a lip-sync or eye clip, for one -- was outside every criterion: a
        # value nothing carries cannot make any statement false.
        self.assets = []
        self.asset_index = {}
        self.asset_classes = {}
        self.assets_followed = 0
        self.assets_unresolved = 0

    def _asset_ref(self, record, pointer):
        """Read the object a clip's ``m_Asset`` names; return its table index.

        Deduplicated on the field payload **excluding** ``m_Name``: the shipped
        names are ``ChangeLipSyncPresetClip(Clone)(Clone)...`` with a different
        number of suffixes per clip, so including it would make every payload
        unique and defeat the sharing entirely -- measured 381 objects collapsing
        to 31 payloads, 1254 KiB to 102 KiB.  The name is not dropped: it rides
        on the clip as ``assetName``, so nothing is lost and the table still
        shares.
        """
        if not pointer or not pointer.get("m_PathID"):
            return None, None
        target = self.store.follow(record, pointer)
        if target is None:
            self.assets_unresolved += 1
            return None, None
        asset_record, asset_id = target
        tree = asset_record.tree(asset_id) or {}
        self.assets_followed += 1
        class_name = asset_record.script_of(asset_id)
        self.asset_classes[class_name] = self.asset_classes.get(class_name, 0) + 1
        fields = {key: value for key, value in tree.items()
                  if key not in ASSET_ENGINE_FIELDS and key != "m_Name"}
        key = json.dumps({"class": class_name, "fields": fields},
                         ensure_ascii=False, sort_keys=True)
        index = self.asset_index.get(key)
        if index is None:
            index = len(self.assets)
            self.asset_index[key] = index
            self.assets.append({"class": class_name, "fields": fields})
        return index, tree.get("m_Name")

    def _read_curve(self, value):
        """One AnimationCurve as JSON, keyframes included, or ``None``.

        Every keyframe field present in the typetree is exported, so a curve
        can never be reduced to a bare "has a curve" flag; the curve-level
        ``m_PreInfinity`` / ``m_PostInfinity`` / ``m_RotationOrder`` are kept.
        """
        if not value:
            return None
        curve = {field: value.get(field, 0) for field in CURVE_LEVEL_FIELDS}
        curve["curve"] = [dict(keyframe) for keyframe in value.get("m_Curve") or []]
        return curve

    def _read_clip(self, record, clip, track_pid):
        """The timing record for one ``TimelineClip``, exported key-for-key.

        Every serialised key present in the clip's typetree is exported — known
        fields through their structured handler (curves expanded, enums as
        ``{"value", "name"}``, pointers kept), unknown keys raw — so nothing a
        track carries is silently dropped.  The ``m_ParentTrack`` reverse
        reference is checked here and a mismatch is counted.
        """
        exported = {}
        for key, value in clip.items():
            if key in CURVE_CLIP_FIELDS:
                exported[key] = self._read_curve(value)
            elif key in ("m_BlendInCurveMode", "m_BlendOutCurveMode"):
                exported[key] = _enum(value, BLEND_CURVE_MODE)
            elif key in ("m_PostExtrapolationMode", "m_PreExtrapolationMode"):
                exported[key] = _enum(value, CLIP_EXTRAPOLATION)
            else:
                exported[key] = value
            self.typetree_keys.add(key)

        parent = clip.get("m_ParentTrack") or {}
        if parent.get("m_FileID", 0) == 0 and parent.get("m_PathID") == track_pid:
            pass
        elif not parent.get("m_PathID"):
            self.parent_unresolved += 1
        else:
            self.parent_bad += 1

        # The object `m_Asset` names, read for its own fields.  The pointer is
        # still exported verbatim beside it; `assetRef` indexes the package's
        # deduplicated asset table and `assetName` keeps this clip's own name.
        ref, name = self._asset_ref(record, clip.get("m_Asset"))
        exported["assetRef"] = ref
        exported["assetName"] = name

        self.double_checked += 1
        for field in DOUBLE_CHECK_FIELDS:
            if clip.get(field) is not None and exported.get(field) != clip.get(field):
                self.double_ok = False
        # A serialised key the export does not carry is a silent loss.
        self.dropped += sum(1 for key in clip if key not in exported)
        return exported

    def _read_track(self, record, path_id):
        """One track: its identity, class, name, clips, infinite clip, markers.

        ``pathId`` is the track's serialized path id — the only thing that
        identifies a track.  ``m_Name`` does not: most packages carry repeated
        track names (several tracks named ``3``, several named ``13``), so a
        consumer that pairs per-track data by name pairs it wrongly.  It is
        exported as a decimal string because a path id is an ``int64`` and a
        JSON consumer reading numbers as doubles cannot hold one exactly.
        """
        tree = record.tree(path_id)
        clips = [self._read_clip(record, clip, path_id)
                 for clip in tree.get("m_Clips") or []]
        track = {
            "class": record.script_of(path_id),
            "name": tree.get("m_Name", ""),
            "pathId": str(path_id),
            LEGACY_ANIMCLIP: tree.get(LEGACY_ANIMCLIP),
            INFINITE_CLIP: tree.get(INFINITE_CLIP),
            "clips": clips,
        }
        self._read_markers(record, tree.get("m_Markers"))
        return track

    def _read_markers(self, record, markers):
        """Count markers by ``MonoScript.m_ClassName``; unresolved = unread."""
        if not isinstance(markers, dict):
            return
        for entry in markers.get("m_Objects") or []:
            pointer = entry.get("ptr_Value") or entry
            target = _resolve(self.store, record, pointer)
            if target is None:
                self.document["unread"].append({"kind": "marker", "pointer": pointer})
                continue
            cls = target[0].script_of(target[1])
            self.marker_classes[cls] = self.marker_classes.get(cls, 0) + 1


def _walk_package(store, name, out):
    """One package: its tracks, clips, markers, and the XOR counts."""
    package = store.package(name)
    document = {"package": name, "tracks": [], "unread": []}
    marker_classes = {}
    walker = _Walker(store, document, marker_classes)
    tracks = []
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour":
                continue
            tree = record.tree(path_id)
            if not _is_track(tree):
                continue
            tracks.append(walker._read_track(record, path_id))
    document["tracks"] = tracks
    # The assets the clips point at, shared across the package.  Kept beside the
    # tracks rather than inline: measured 381 objects collapsing to 31 payloads
    # (1254 KiB -> 102 KiB), because a lip-sync clip carries the whole preset
    # table and every clip of a package carries the same one.
    document["assets"] = walker.assets

    anim = [t for t in tracks if t["class"] == ANIMATION_TRACK_CLASS]
    counts = _counts(tracks, anim, walker, marker_classes)
    if document["tracks"] or document["unread"] or document["assets"]:
        write_json(out / f"{name}.json", document)
    return counts


def _counts(tracks, anim, walker, marker_classes):
    """The per-package structural counts, including the animation-track XOR."""
    counts = {key: 0 for key in KEYS}
    counts["animationTracks"] = len(anim)
    counts["animClipNonzero"] = sum(
        1 for t in anim if (t[LEGACY_ANIMCLIP] or {}).get("m_PathID"))
    counts["infiniteClipNonzero"] = sum(
        1 for t in anim if (t[INFINITE_CLIP] or {}).get("m_PathID"))
    for t in anim:
        infinite = bool((t[INFINITE_CLIP] or {}).get("m_PathID"))
        clips_nonempty = bool(t["clips"])
        if infinite == clips_nonempty:
            counts["bothOrNeither"] += 1
        elif infinite:
            counts["infiniteOnly"] += 1
        else:
            counts["clipOnly"] += 1
    counts["clips"] = sum(len(t["clips"]) for t in tracks)
    counts["parentBad"] = walker.parent_bad
    counts["parentUnresolved"] = walker.parent_unresolved
    counts["droppedKeys"] = walker.dropped
    counts["doubleExact"] = walker.double_ok
    counts["doubleChecked"] = walker.double_checked
    # Asset account: how many pointers were followed, how many distinct payloads
    # that collapsed to, and how many named something unreachable.  The three
    # are separate numbers because "followed" and "unique" answer different
    # questions and an unresolved pointer is neither.
    counts["assetsFollowed"] = walker.assets_followed
    counts["assetsUnique"] = len(walker.assets)
    counts["assetsUnresolved"] = walker.assets_unresolved
    counts["assetClasses"] = dict(sorted(walker.asset_classes.items()))
    counts["clipTypetreeKeys"] = sorted(walker.typetree_keys)
    counts["markerClasses"] = dict(marker_classes)
    return counts


def read_timeline_clips(bundles, out_dir, bundle_root=None):
    """Read every timeline clip's timing from *bundles*, one JSON per package.

    *bundles* names the packages to read; the last path segment of each is the
    package name.  *out_dir* receives one document per package: each track's
    class and name, its clips (with every timing field and both mix curves,
    keyframes included), the track-level ``m_AnimClip`` legacy slot and
    ``m_InfiniteClip``, and the marker class histogram.  It also returns the
    per-package counts that reproduce the animation-track XOR invariant and the
    ``m_AnimClip`` positive control, plus the marker class histogram and the
    field-table symmetric difference.

    Returns a report with per-package counts, the aggregated counts, the marker
    class histogram, the clip field symmetric difference, and whether the
    boundary family is fully present.
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

    present = [r for r in records if not r.get("missing")]
    agg = {key: sum(r.get(key, 0) for r in present) for key in KEYS}
    agg["doubleExact"] = all(r.get("doubleExact", True) for r in present)
    agg["doubleChecked"] = sum(r.get("doubleChecked", 0) for r in present)

    clip_keys, seen = set(STUB_CLIP_FIELDS), set()
    marker_classes = {}
    for r in present:
        seen.update(r.get("clipTypetreeKeys", []))
        for cls, count in r.get("markerClasses", {}).items():
            marker_classes[cls] = marker_classes.get(cls, 0) + count
    sym_diff = {"inStubOnly": sorted(clip_keys - seen),
                "inTypetreeOnly": sorted(seen - clip_keys)}
    return {"packages": records, "aggregate": agg,
            "clipFieldSyncDiff": sym_diff, "markerClasses": marker_classes,
            "missing": [r["package"] for r in records if r.get("missing")],
            "families": bool(present) and len(present) == len(records)}


if __name__ == "__main__":
    raise SystemExit("clip reader is a library; call read_timeline_clips")
