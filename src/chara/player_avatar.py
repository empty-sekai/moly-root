"""Player avatar (audience rig) skeleton + motion export.

The player avatar reuses the virtual-live concert audience rig -- the model,
skeleton and base mesh in ``audience.prefab``/``audienceAvatar`` under
``virtual_live/avatar/model/default`` -- and plays clips from two separate
motion bundles on top of it: ``virtual_live/avatar/motion`` (the shared base
movement/gesture set) and ``mysekai/player/motion/unique`` (site-interaction
and home clips specific to this mode). Every AnimationClip in either motion
bundle binds to a transform by CRC32 hash of its path *from the animator
root* (see ``perf.animations.SEMANTICS``), never by a readable string, so the
hash table can only be built by walking the real scene hierarchy -- which
lives only in the model bundle.

``UnityPy.load(*paths)`` merges any number of bundle files into one
``Environment``, and ``perf.animations.NodeHierarchy`` builds its transform
forest and CRC32 table purely from the ``Transform``/``RectTransform``/
``GameObject``/``Animator`` objects handed to it, independent of which single
file they came from. Loading the model bundle together with both motion
bundles therefore gives ``NodeHierarchy`` the real audience skeleton once, and
clips in either motion bundle resolve against it directly, with no
foreign-rig fallback constructed. Not every clip binds fully against this one
skeleton: a subset of ``virtual_live/avatar/motion``'s clips carry transform
bindings whose CRC32 hash matches no path in the audience hierarchy at all
(``bindingCoverage`` in the exported record reports this exactly, per clip
via ``curveAccounting``); every clip in ``mysekai/player/motion/unique``
binds fully.

This module reuses (does not reimplement) the decode/export internals of
``perf.animations``: ``NodeHierarchy``, ``decode_clip``, ``_add_animation``,
``curve_accounting``, ``CLASS_GLTF``. New logic here is the multi-bundle
load and, per clip: ``container`` and ``sourcePackage`` (read directly off
each Unity object, not looked up), ``family`` (the container path's folder
segment, where the source package has one), ``guessedFamily`` (guessed from
the clip name prefix alone, for packages that ship no folder to read), and
``phaseBase``/``phase`` (the ``_S``/``_L``/``_E``/``_O`` suffix split used by
``examples/viewer/segments.js``). A single-package export has no notion of
any of these, since its caller already knows its own package's layout.
"""
import collections
import os
import re

import UnityPy

from core.gltf import GLB
from core.jsonio import write_json
from perf.animations import (
    CLASS_GLTF,
    CLASS_NO_BINDING,
    CLASS_NO_NODE,
    CLASS_UNRESOLVED,
    FORMAT_VERSION,
    SEMANTICS,
    NodeHierarchy,
    _add_animation,
    curve_accounting,
    decode_clip,
)

# examples/viewer/segments.js:3-5 (accepted demo, not a true-source citation):
# "The public clip convention uses _S, _L, _E, and _O suffixes for Start,
# Loop, End, and OneShot." Same regex as that file's ``splitName``.
_PHASE_RE = re.compile(r"^(.*)_(S|L|E|O)$")
_PHASE_PROVENANCE = ("examples/viewer/segments.js:3-5 (demo convention, "
                     "not confirmed against a true source)")


def _phase_of(clip_name):
    m = _PHASE_RE.match(clip_name)
    if not m:
        return clip_name, None
    return m.group(1), m.group(2)


def group_phases(names):
    """Group clip names into S/L/E/O families, same grouping as
    ``examples/viewer/segments.js``'s ``groupClips`` (``_PHASE_PROVENANCE``
    applies: this grouping is a demo convention, not a true-source fact).
    """
    fams = {}
    for n in names:
        base, phase = _phase_of(n)
        fam = fams.setdefault(base, {"base": base, "segs": {}, "plain": None})
        if phase:
            fam["segs"][phase] = n
        else:
            fam["plain"] = n
    return list(fams.values())


# Family guessed from the clip name alone (NOT read from a container path) --
# only usable as a cross-check on packages shipping their clips flat, never
# as a substitute for the container-derived family where one is available.
_GUESS_RULES = (
    ("harvest", ("site_",)),
    ("conversation", ("hou_",)),
    ("home", ("myroom_", "fixture_", "house_open", "other_house")),
    ("c_000", ("c_000_",)),
)


def guessed_family_from_name(clip_name):
    for family, needles in _GUESS_RULES:
        if any(needle in clip_name for needle in needles):
            return family
    return "other"


def _container_family(container, source_package):
    """Domain family read from the container path folder, never guessed.

    For ``mysekai/player/motion/unique`` the folder right after ``unique/``
    is the family (``harvest``, ``conversation``, ``home``, ``common``,
    verified 2026-08-29 to enumerate exactly those four for this package).
    ``virtual_live/avatar/motion`` ships its clips flat -- no folder to read,
    so the family is reported as ``"(flat)"`` rather than inferred from the
    name.
    """
    if not container:
        return None
    parts = container.split("/")
    if "unique" in parts:
        idx = parts.index("unique")
        if idx + 1 < len(parts) - 1:
            return parts[idx + 1]
    return "(flat)"


def _clip_frame_info(tree):
    """Best-effort (sampleRate, durationSeconds, frameCount) from a clip typetree.

    ``m_SampleRate`` and ``m_MuscleClip.m_StopTime``/``m_StartTime`` are plain
    typetree fields (verified against the reference package: idle clip reads
    m_SampleRate=60.0, m_StopTime=0.8333333730697632, matching its recorded
    frameCount=51 via round(0.8333333730697632*60)+1 == 51).  A clip lacking
    ``m_MuscleClip`` (none observed in this corpus, but not guaranteed) yields
    ``None`` for duration/frameCount rather than a guessed number.
    """
    sample_rate = tree.get("m_SampleRate")
    muscle = tree.get("m_MuscleClip") or {}
    stop_time = muscle.get("m_StopTime")
    start_time = muscle.get("m_StartTime", 0.0)
    duration = frame_count = None
    if stop_time is not None and sample_rate:
        duration = stop_time - start_time
        frame_count = round(duration * sample_rate) + 1
    return sample_rate, duration, frame_count


def export_player_avatar(bundle_paths, out_dir, name="mysekai__player_avatar"):
    """Export the player (audience) skeleton + every AnimationClip found across
    *bundle_paths*, merged into one UnityPy ``Environment``.

    *bundle_paths* should include the model bundle
    (``virtual_live/avatar/model/default``) plus every motion bundle whose
    clips are to be resolved against it; order does not matter to
    ``NodeHierarchy`` since it reads the whole combined object list.

    Returns the package record (also written as ``<name>.index.json``),
    field-shape isomorphic to ``perf.animations.export_package``'s record
    (same top-level keys), with these additions:

    - ``sourcePackages``: basenames of *bundle_paths*, for provenance (a
      single-package record only ever needed a bare ``package`` name).
    - Each ``clipRecords`` entry additionally carries ``container`` (the
      bundle-internal asset path, e.g. ``.../harvest/mov_u000_site_ax01_e.anim``),
      ``harvest`` (``"/harvest/" in container``), ``sampleRate``,
      ``durationSeconds`` and ``frameCount`` -- none of which
      ``export_package`` records, since a single-package caller already knows
      its own package's naming convention.
    - ``counts.harvest``: how many exported clips have a harvest container.
    """
    for p in bundle_paths:
        assert os.path.exists(p), f"bundle path does not exist: {p}"
    env = UnityPy.load(*bundle_paths)
    hierarchy = NodeHierarchy(list(env.objects), foreign=None, prefer=name)
    glb = GLB(generator="moly-root player avatar")

    clips, seen, duplicates = [], {}, []
    for obj in env.objects:
        if obj.type.name != "AnimationClip":
            continue
        # Per-object attributes (each object knows its own source file and
        # bundle-internal path directly; no reverse map over env.container
        # is needed): obj.container is the container path, and
        # obj.assets_file.parent.name is the bundle basename that produced
        # this object -- verified 2026-08-29 to match, per object, the
        # bundle passed to UnityPy.load for it.
        container = obj.container
        source_package = obj.assets_file.parent.name if obj.assets_file else None
        try:
            tree = obj.read_typetree()
        except Exception as exc:
            hierarchy.read_failures.append({
                "pathId": getattr(obj, "path_id", None), "type": "AnimationClip",
                "container": container, "sourcePackage": source_package,
                "reason": "typetree read failed", "detail": type(exc).__name__})
            continue
        clip_name = str(tree.get("m_Name", ""))
        if not clip_name:
            continue
        if clip_name in seen:
            duplicates.append({
                "clip": clip_name, "pathId": getattr(obj, "path_id", None),
                "container": container, "sourcePackage": source_package,
                "firstSourcePackage": seen[clip_name],
                "bindings": len(((tree.get("m_ClipBindingConstant") or {})
                                 .get("genericBindings") or [])),
                "reason": "duplicate clip name across merged bundles, first object kept"})
            continue
        seen[clip_name] = source_package
        sample_rate, duration, frame_count = _clip_frame_info(tree)
        harvest = bool(container and "/harvest/" in container)
        family = _container_family(container, source_package)
        base, phase = _phase_of(clip_name)
        common = {
            "container": container, "sourcePackage": source_package,
            "family": family, "guessedFamily": guessed_family_from_name(clip_name),
            "harvest": harvest,
            "phaseBase": base, "phase": phase,
            "sampleRate": sample_rate, "durationSeconds": duration,
            "frameCount": frame_count,
        }
        try:
            curves, channels, anomalies = decode_clip(tree, hierarchy)
            index = len(glb.g.get("animations", []))
            _add_animation(glb, clip_name, curves, channels, anomalies)
            accounting = curve_accounting(curves, channels)
            clips.append({
                "name": clip_name, "animation": index, "curves": len(curves),
                "keys": sum(len(c["times"]) for c in curves),
                "channels": len(curves), "gltfChannels": len(channels),
                "curveAccounting": accounting, "anomalies": anomalies,
                **common,
            })
        except Exception as exc:                     # malformed curve block
            clips.append({
                "name": clip_name, "animation": None, "curves": 0, "keys": 0,
                "channels": 0, "gltfChannels": 0, "curveAccounting": {},
                "anomalies": [{"clip": clip_name, "reason": "clip decode failed",
                               "detail": f"{type(exc).__name__}: {exc}"}],
                **common,
            })

    roots = hierarchy.write_scene(glb)
    glb.g["scenes"][0]["nodes"] = roots
    if hierarchy.foreign_roots:
        glb.g["scenes"].append({"name": "foreign-rigs",
                                "nodes": list(hierarchy.foreign_roots)})
    foreign_counts = hierarchy.foreign_counts()
    sources = hierarchy.resolution_sources()
    accounting = collections.Counter()
    for c in clips:
        accounting.update(c.get("curveAccounting") or {})

    # Binding-coverage guard (denominator excludes non-Transform typeIDs and
    # CLASS_NO_BINDING slots, since those were never candidates for a
    # transform-path resolution in the first place; "resolved == denominator"
    # is deliberately not asserted alone -- see denominator > 0 below):
    non_transform = sum(v for k, v in accounting.items()
                        if k == CLASS_NO_BINDING or k.startswith("typeid-"))
    denominator = sum(accounting.values()) - non_transform
    unresolved = accounting.get(CLASS_UNRESOLVED, 0) + accounting.get(CLASS_NO_NODE, 0)
    resolved = denominator - unresolved
    binding_coverage = {
        "denominator": denominator, "resolved": resolved, "unresolved": unresolved,
        "nonTransformSlots": non_transform,
        "assertion": "denominator > 0 and unresolved == 0",
        "assertionHolds": bool(denominator > 0 and unresolved == 0),
    }

    glb.g["asset"]["extras"] = {"binding": SEMANTICS,
                                "formatVersion": FORMAT_VERSION,
                                "animatorPath": hierarchy.animator_path,
                                "anchors": hierarchy.anchors,
                                "foreignResolved": foreign_counts,
                                "resolutionSources": sources}
    os.makedirs(out_dir, exist_ok=True)
    glb_path = os.path.join(out_dir, f"{name}.glb")
    index_path = os.path.join(out_dir, f"{name}.index.json")
    glb.save(glb_path)
    for c in clips:
        for a in c.get("anomalies", []):
            a.setdefault("clip", c["name"])
    record = {
        "version": 1, "formatVersion": FORMAT_VERSION, "binding": SEMANTICS,
        "package": name,
        "sourcePackages": [os.path.basename(p) for p in bundle_paths],
        "animatorPath": hierarchy.animator_path,
        "anchors": hierarchy.anchors,
        "nodes": len(hierarchy.nodes),
        "nodeCount": len(hierarchy.nodes),
        "nativeNodeCount": hierarchy.nativeNodes,
        "foreignResolved": foreign_counts,
        "resolutionSources": sources,
        "curveAccounting": {"classes": dict(sorted(accounting.items())),
                            "total": sum(accounting.values()),
                            "gltfChannelSlots": accounting.get(CLASS_GLTF, 0),
                            "residual": sum(c["channels"] for c in clips)
                                        - sum(accounting.values())},
        "foreignHashes": {str(h): v for h, v in
                          sorted(hierarchy.foreign_hits.items())},
        "readFailures": hierarchy.read_failures,
        "bindingCoverage": binding_coverage,
        "clips": {c["name"]: c["channels"] for c in clips},
        "clipRecords": clips,
        "anomalies": [a for c in clips for a in c["anomalies"]] +
                     list(hierarchy.read_failures) + duplicates,
        "duplicateNames": duplicates,
        "missingWanted": [],
        "exported": sum(1 for c in clips if c["channels"] > 0),
        "gltfExported": sum(1 for c in clips if c["gltfChannels"] > 0),
        "channeled": sum(c["channels"] for c in clips),
        "gltfChanneled": sum(c["gltfChannels"] for c in clips),
        "counts": {"discovered": len(seen),
                   "exported": sum(1 for c in clips if c["channels"] > 0),
                   "gltfExported": sum(1 for c in clips if c["gltfChannels"] > 0),
                   "channeled": sum(c["channels"] for c in clips),
                   "duplicateNames": len(duplicates),
                   "anomaly": sum(len(c["anomalies"]) for c in clips) +
                              len(duplicates),
                   "harvest": sum(1 for c in clips if c.get("harvest")),
                   "byContainerFamily": dict(sorted(collections.Counter(
                       c.get("family") for c in clips).items(),
                       key=lambda kv: (kv[0] is None, kv[0]))),
                   "byGuessedFamily": dict(sorted(collections.Counter(
                       c.get("guessedFamily") for c in clips).items()))},
        "glb": glb_path, "index": index_path,
        "glbBytes": os.path.getsize(glb_path),
    }
    write_json(index_path, record)
    return record


def write_motion_manifest(record, manifest_path):
    """Write the flat name/container/family/phase manifest.

    One row per exported clip (``clipRecords``), sorted by name, plus two
    family breakdowns and a phase-segment grouping.

    - ``family``: read from the clip's container path folder (``None``/
      ``"(flat)"`` where the package ships no folder to read).
    - ``guessedFamily``: guessed from the clip name prefix alone (see
      ``guessed_family_from_name``) -- a cross-check, not a substitute.
    - ``phaseFamilies``: clips grouped by the ``_S``/``_L``/_E``/``_O``
      suffix convention documented at ``examples/viewer/segments.js:3-5``
      (demo convention, not confirmed against a true source) -- each entry
      lists which of S/L/E/O/plain exist for one base clip name.
    """
    rows = []
    for c in record["clipRecords"]:
        rows.append({
            "name": c["name"],
            "container": c.get("container"),
            "sourcePackage": c.get("sourcePackage"),
            "family": c.get("family"),
            "guessedFamily": c.get("guessedFamily"),
            "phaseBase": c.get("phaseBase"),
            "phase": c.get("phase"),
            "harvest": c.get("harvest", False),
            "frameCount": c.get("frameCount"),
            "sampleRate": c.get("sampleRate"),
            "durationSeconds": c.get("durationSeconds"),
            "channels": c.get("channels"),
            "gltfChannels": c.get("gltfChannels"),
        })
    rows.sort(key=lambda r: r["name"])
    doc = {
        "count": len(rows),
        "harvestCount": sum(1 for r in rows if r["harvest"]),
        "familyCounts": {"method": "read from container path folder",
                         "counts": dict(sorted(collections.Counter(
                             r["family"] for r in rows).items(),
                             key=lambda kv: (kv[0] is None, kv[0])))},
        "guessedFamilyCounts": {"method": "guessed from clip name prefix",
                                "counts": dict(sorted(collections.Counter(
                                    r["guessedFamily"] for r in rows).items()))},
        "phaseFamilies": {
            "provenance": _PHASE_PROVENANCE,
            "groups": sorted(group_phases(r["name"] for r in rows),
                             key=lambda g: g["base"]),
        },
        "duplicateNames": record.get("duplicateNames", []),
        "clips": rows,
    }
    write_json(manifest_path, doc)
    return doc
