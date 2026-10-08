"""Source-qualified humanoid libraries for every authored furniture actor.

The plan joins master actor groups to actual prefab directors, then to the
three-table Timeline clip identities. It does not assign actions by filenames
or retarget every clip to an arbitrary actor. Outputs retain source file/pathId
and are checked before becoming visible through actor-animations/index.json.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

from core.atomic import PUBLIC_FILE_MODE, write_json
from core.download import exclusive_lock
from .actor_libraries import build_actor_library_index, _gltf, _roots
from .motion_library import MOTION_STORAGE_REVISION, POSE_BINDING_REVISION, export_motion_library
from .motion_storage import validate_rotation_encoding
from .rotation_encoding import DEFAULT_ROTATION_ENCODING


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _asset(value):
    if not isinstance(value, dict) or not value.get("file") or value.get("pathId") in (None, "0", 0):
        return None
    return value["file"], str(value["pathId"])


def _walk_tracks(rows):
    for row in rows:
        yield row
        yield from _walk_tracks(row.get("children", []))


def _template(logical):
    pieces = logical.split("_")
    if len(pieces) < 5 or pieces[0] != "tl":
        raise ValueError(f"invalid source fixture timeline name: {logical}")
    return "_".join(pieces[1:4])


def _prefab_names(logical):
    template = _template(logical)
    # All three authored attachment heights are exported. Runtime still selects
    # exactly one using PutType and the actual ViewObject.localPosition.y.
    return {logical, logical.replace(template, template + "-ground", 1),
            logical.replace(template, template + "-low", 1)}


def _source_relations(master):
    """Yield real (unit, timelineGroup) references; multi-actor groups keep slots."""
    for row in _json(master / "mysekaiCharacterTalkNoTalkMysekaiFixtureActions.json"):
        yield row["gameCharacterUnitId"], row["mysekaiCharacterTalkFixtureTimelineGroupId"]
    units = {row["id"]: row for row in _json(master / "mysekaiGameCharacterUnitGroups.json")}
    talks = {row["id"]: row for row in _json(master / "mysekaiCharacterTalks.json")}
    for row in _json(master / "mysekaiCharacterTalkPreActions.json"):
        group = row.get("mysekaiCharacterTalkFixtureTimelineGroupId")
        talk = talks.get(row.get("mysekaiCharacterTalkId"))
        if not group or not talk:
            continue
        members = units.get(talk.get("mysekaiGameCharacterUnitGroupId"), {})
        for slot in range(1, 6):
            unit = members.get(f"gameCharacterUnitId{slot}")
            if unit and unit > 0:
                yield unit, group


CUT_SCENE_PREFIX = "mysekai__cut_scene__"

# The library directory of a relation's job under ``unit-NN/``: the furniture
# relation keeps the source package's own name; the cut-scene relation's
# library is a separate one, so adding it neither changes nor re-exports a
# furniture library.  A clip a furniture job of the unit already carries is
# not repeated in the cut-scene library (one source identity, one route).
RELATION_LIBRARY = {"fixture": "{package}", "cut_scene": "cut-scene__{package}"}

# A planned job whose SD reference model or motion bundle is not in the
# bundles directory.  Unlike the other plan gaps (a unit the release has no
# model for, a table the source lacks) this is a hole in the caller's input,
# not a fact of the source: the export refuses a run that selects such a job
# instead of publishing a route index without that library.
BUNDLE_ABSENT = "original reference or motion bundle absent"


def _add_targets(jobs, gaps, unit, package, targets):
    """Queue each resolved clip of one actor track for *unit*; count them."""
    added = 0
    for target in targets:
        identity = _asset(target.get("sourceClip"))
        source = target.get("targetPackage", "")
        clip = target.get("clipName")
        if not identity or not clip or not re.fullmatch(r"[A-Za-z0-9_.-]+", source):
            gaps.append({"unit": unit, "package": package, "reason": "clip identity is incomplete"})
            continue
        if target.get("sourceMetadataMissing"):
            gaps.append({"unit": unit, "package": source, "clip": clip,
                         "reason": "authored playback metadata is missing"})
            continue
        jobs[(unit, source)][identity] = {"name": clip, "file": identity[0], "pathId": identity[1]}
        added += 1
    return added


def _cut_scene_relations(root, master, real_units, jobs, gaps):
    """The cut-scene character slots: every unit-named animation track.

    ``CutSceneView.BindAnimator`` binds a character's Animator to each
    Animator output whose stream name is that character's unit id, so a
    track named after a unit plays that unit's clips.  The packages are the
    ones the cut-scene master names; a master without the table adds none.
    Their clips are queued in *jobs* of their own: each forms a library of
    its own beside the furniture ones (see :data:`RELATION_LIBRARY`).
    """
    table = master / "mysekaiCutScenes.json"
    if not table.is_file():
        return 0
    packages = sorted({CUT_SCENE_PREFIX + row["timelineAssetbundleName"]
                       for row in _json(table)})
    relationships = 0
    for package in packages:
        track_path = root / "cutscene-timeline" / "tracks" / f"{package}.json"
        target_path = root / "cutscene-timeline" / "clip-targets" / f"{package}.json"
        if not track_path.is_file() or not target_path.is_file():
            gaps.append({"package": package, "reason": "formal timeline tables are absent"})
            continue
        tracks, targets = _json(track_path), _json(target_path)
        by_asset = {_asset(row.get("asset")): row for row in tracks["timelines"]}
        keyed = defaultdict(list)
        for row in targets.get("keyedClips", []):
            if row.get("target"):
                keyed[_asset(row.get("track"))].append(row["target"])
        for prefab in tracks.get("prefabs", []):
            for director in prefab.get("directors", []):
                timeline = by_asset.get(_asset(director.get("timeline")))
                if timeline is None:
                    gaps.append({"package": package, "reason": "selected director has no timeline table"})
                    continue
                for track in _walk_tracks(timeline.get("tracks", [])):
                    name = track.get("name", "")
                    if track.get("class") != "AnimationTrack" or not name.isdigit():
                        continue
                    unit = int(name)
                    if unit not in real_units:
                        gaps.append({"unit": unit, "package": package,
                                     "reason": "character model is not in this source release"})
                        continue
                    relationships += _add_targets(jobs, gaps, unit, package,
                                                  keyed.get(_asset(track.get("asset")), []))
    return relationships


def build_plan(asset_root, master_root, bundles_root, *, rotation_encoding=DEFAULT_ROTATION_ENCODING):
    validate_rotation_encoding(rotation_encoding)
    root, master, bundles = map(Path, (asset_root, master_root, bundles_root))
    manifest = _json(root / "manifest.json")
    real_units = {int(row["unit"]) - 100 for row in manifest["units"]}
    roots = _roots(_gltf(root / "motion-library.glb"))
    if len(roots) != 1:
        raise ValueError("shared animation binding needs exactly one source root")
    binding_root = roots[0]
    timeline_groups = defaultdict(list)
    timeline_file = master / "mysekaiCharacterTalkFixtureTimelines.json"
    if timeline_file.is_file():
        timeline_rows = _json(timeline_file)
    else:
        ordered = _json(root / "mysekai-character-talk-fixture-timelines.json")
        timeline_rows = [ordered["entries"][str(key)] for key in ordered["rowOrder"]]
    for row in timeline_rows:
        timeline_groups[row["groupId"]].append(row["assetbundleName"])
    requested = defaultdict(set)
    gaps = []
    for unit, group in set(_source_relations(master)):
        if unit not in real_units:
            gaps.append({"unit": unit, "group": group, "reason": "character model is not in this source release"})
            continue
        if group not in timeline_groups:
            gaps.append({"unit": unit, "group": group, "reason": "source timeline group is absent"})
        for logical in timeline_groups[group]:
            package = "mysekai__fixture_timeline__mdl_" + _template(logical)
            requested[package].add((unit, logical))
    jobs = defaultdict(dict)
    relationships = 0
    for package, references in sorted(requested.items()):
        track_path = root / "fixture-timeline" / "tracks" / f"{package}.json"
        target_path = root / "fixture-timeline" / "clip-targets" / f"{package}.json"
        if not track_path.is_file() or not target_path.is_file():
            gaps.append({"package": package, "reason": "formal timeline tables are absent"})
            continue
        tracks, targets = _json(track_path), _json(target_path)
        by_asset = {_asset(row.get("asset")): row for row in tracks["timelines"]}
        keyed = defaultdict(list)
        for row in targets.get("keyedClips", []):
            if row.get("target"):
                keyed[_asset(row.get("track"))].append(row["target"])
        for unit, logical in sorted(references):
            prefabs = [row for row in tracks.get("prefabs", [])
                       if Path(row["container"]).stem in _prefab_names(logical)]
            if not prefabs:
                gaps.append({"unit": unit, "package": package, "prefab": logical,
                             "reason": "no exact authored prefab or attachment-height variant"})
                continue
            for prefab in prefabs:
                active_directors = {_asset(view.get("director")) for view in prefab.get("fixtureTimelineViews", [])}
                directors = [row for row in prefab.get("directors", [])
                             if not active_directors or _asset(row.get("asset")) in active_directors]
                for director in directors:
                    timeline = by_asset.get(_asset(director.get("timeline")))
                    if timeline is None:
                        gaps.append({"unit": unit, "package": package, "prefab": logical,
                                     "reason": "selected director has no timeline table"})
                        continue
                    for track in _walk_tracks(timeline.get("tracks", [])):
                        if track.get("class") != "AnimationTrack":
                            continue
                        # CharacterAnimator is the formal single-actor binding;
                        # numeric multi-actor tracks bind their authored unit only.
                        if track.get("name") != "CharacterAnimator" and track.get("name") != str(unit):
                            continue
                        relationships += _add_targets(
                            jobs, gaps, unit, package, keyed.get(_asset(track.get("asset")), []))
    cut_scene_jobs = defaultdict(dict)
    relationships += _cut_scene_relations(root, master, real_units, cut_scene_jobs, gaps)
    rows = []
    globally_seen = set()
    ordered = [("fixture", key, clips) for key, clips in sorted(jobs.items())]
    ordered += [("cut_scene", key, clips) for key, clips in sorted(cut_scene_jobs.items())]
    for relation, (unit, package), clips in ordered:
        unique = []
        for identity, clip in sorted(clips.items()):
            key = (unit, *identity)
            if key not in globally_seen:
                globally_seen.add(key)
                unique.append(clip)
        if not unique:
            continue
        reference = bundles / f"mysekai__character__mdl_sd_{unit + 100:03d}_001"
        source = bundles / package
        if not reference.is_file() or not source.is_file():
            gaps.append({"unit": unit, "package": package, "reason": BUNDLE_ABSENT,
                         "relation": relation, "library": RELATION_LIBRARY[relation].format(package=package),
                         "absent": [path.name for path in (reference, source) if not path.is_file()]})
            continue
        row = {"unit": unit, "package": package, "reference": str(reference.resolve()),
               "bundle": str(source.resolve()), "bindingRoot": binding_root, "clips": unique,
               "motionStorageRevision": MOTION_STORAGE_REVISION, "rotationEncoding": rotation_encoding}
        facts = {
            "unit": unit, "binding": binding_root, "clips": unique,
            "referenceSha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
            "bundleSha256": hashlib.sha256(source.read_bytes()).hexdigest(), "format": 2,
            "poseBindingRevision": POSE_BINDING_REVISION,
            "motionStorageRevision": MOTION_STORAGE_REVISION,
            "rotationEncoding": rotation_encoding,
        }
        if relation != "fixture":
            row["relation"] = relation
            row["library"] = RELATION_LIBRARY[relation].format(package=package)
            facts["library"] = row["library"]
        row["identity"] = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
        rows.append(row)
    return {"version": 1, "bindingRoot": binding_root, "jobs": rows, "gaps": gaps,
            "motionStorageRevision": MOTION_STORAGE_REVISION, "rotationEncoding": rotation_encoding,
            "summary": {"units": len({row['unit'] for row in rows}), "jobs": len(rows),
                        "clips": sum(len(row['clips']) for row in rows),
                        "authoredReferences": relationships, "gaps": len(gaps)}}


def _receipt_valid(folder, identity, *, rotation_encoding=None):
    marker = folder / "export-source.json"
    if not marker.is_file():
        return False
    if folder.is_symlink() or marker.is_symlink():
        return False
    try:
        previous = _json(marker)
    except (OSError, ValueError):
        return False
    if rotation_encoding is not None and (
            previous.get("motionStorageRevision") != MOTION_STORAGE_REVISION or
            previous.get("rotationEncoding") != rotation_encoding):
        return False
    if set(previous.get("files", {})) != {"actor.glb", "actor.index.json"}:
        return False
    if any((folder / name).is_symlink() for name in previous["files"]):
        return False
    return previous.get("identity") == identity and bool(previous.get("files")) and all(
        (folder / name).is_file() and hashlib.sha256((folder / name).read_bytes()).hexdigest() == value
        for name, value in previous.get("files", {}).items())


def _library(job):
    return job.get("library", job["package"])


def _publish_directory(staged, folder, output, job):
    if folder.exists():
        backup = Path(output).parent / "_previous" / f"unit-{job['unit']:02d}" / (_library(job) + f"-{time.time_ns()}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        folder.rename(backup)
    # Native indexers may briefly hold freshly written GLBs. Retrying a
    # verified same-directory rename does not change permissions or identity.
    for attempt in range(8):
        try:
            staged.rename(folder)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.1 * (attempt + 1))


def _run_job(job, output, unity_version):
    rotation_encoding = validate_rotation_encoding(job.get("rotationEncoding"))
    if job.get("motionStorageRevision") != MOTION_STORAGE_REVISION:
        raise ValueError("actor job motion storage revision is stale; rebuild the export plan")
    import UnityPy.config
    UnityPy.config.FALLBACK_UNITY_VERSION = unity_version
    parent = Path(output) / f"unit-{job['unit']:02d}"
    folder = parent / _library(job)
    if _receipt_valid(folder, job["identity"], rotation_encoding=rotation_encoding):
        return {"unit": job["unit"], "package": job["package"], "status": "cached", "clips": len(job["clips"])}
    parent.mkdir(parents=True, exist_ok=True)
    staged = parent / (_library(job) + ".staging")
    staged.mkdir(exist_ok=True)
    if _receipt_valid(staged, job["identity"], rotation_encoding=rotation_encoding):
        _publish_directory(staged, folder, output, job)
        return {"unit": job["unit"], "package": job["package"], "status": "recovered", "clips": len(job["clips"])}
    names = {row["name"] for row in job["clips"]}
    report = export_motion_library(job["reference"], job["bundle"], str(staged), "actor",
                                   names=names, binding_root=job["bindingRoot"],
                                   source_clips={(row["file"], row["pathId"]) for row in job["clips"]},
                                   rotation_encoding=rotation_encoding)
    if report["failed"]:
        raise ValueError(f"humanoid export failed for unit {job['unit']}/{job['package']}: {report['failures']}")
    index = _json(staged / "actor.index.json")
    actual = {(segment["sourceClip"]["file"], str(segment["sourceClip"]["pathId"]))
              for family in index["clips"].values() for segment in family["segments"].values()}
    expected = {(row["file"], row["pathId"]) for row in job["clips"]}
    if actual != expected:
        raise ValueError(f"source identity changed during actor export: {job['package']}")
    receipt = {"identity": job["identity"], "motionStorageRevision": MOTION_STORAGE_REVISION,
        "rotationEncoding": rotation_encoding, "files": {
        name: hashlib.sha256((staged / name).read_bytes()).hexdigest()
        for name in ("actor.glb", "actor.index.json")}}
    write_json(staged / "export-source.json", receipt, mode=PUBLIC_FILE_MODE)
    # Each job is private until its complete receipt and curves exist. The
    # global route index is published only after all selected jobs terminate.
    _publish_directory(staged, folder, output, job)
    return {"unit": job["unit"], "package": job["package"], "status": "exported",
            "clips": report["exported"], "seconds": round(report["elapsedSeconds"], 3), "bytes": report["glbBytes"]}


def export_fixture_actor_libraries(asset_root, master_root, bundles_root, *, workers=2,
                                  unity_version="2022.3.62f3", progress=None, units=None,
                                  rotation_encoding=DEFAULT_ROTATION_ENCODING, relations=None):
    validate_rotation_encoding(rotation_encoding)
    if not 1 <= workers <= 8:
        raise ValueError("actor export workers must be between 1 and 8")
    root = Path(asset_root)
    output = root / "actor-animations"
    output.mkdir(parents=True, exist_ok=True)
    generation = output / "generated"
    generation.mkdir(exist_ok=True)
    plan = build_plan(root, Path(master_root), Path(bundles_root), rotation_encoding=rotation_encoding)
    # *relations* limits the run to the named relations' jobs (for example the
    # cut-scene libraries alone); the other libraries on disk stay as they are
    # and the route index is still built from every library present.
    def selected(row):
        return ((units is None or row["unit"] in units)
                and (relations is None or row.get("relation", "fixture") in relations))

    jobs = [row for row in plan["jobs"] if selected(row)]
    write_json(output / "export-plan.json", plan, mode=PUBLIC_FILE_MODE)
    # A selected library whose input bundles are absent is refused before any
    # job runs: exporting the rest and publishing the route index would drop
    # that unit's clips from the catalogue without a failure, and the runtime
    # would later refuse the timeline that needs them, far from the cause.
    absent = [gap for gap in plan["gaps"] if gap.get("reason") == BUNDLE_ABSENT and selected(gap)]
    if absent:
        report = {"version": 1, "requested": len(jobs), "completed": 0,
                  "motionStorageRevision": MOTION_STORAGE_REVISION, "rotationEncoding": rotation_encoding,
                  "failed": len(absent), "failures": absent, "results": [], "sourceGaps": plan["gaps"]}
        write_json(output / "export-report.json", report, mode=PUBLIC_FILE_MODE)
        return report
    results, failures = [], []
    with exclusive_lock(output / ".export.lock"):
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_job, row, str(generation), unity_version): row for row in jobs}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    result = {"unit": row["unit"], "package": row["package"], "error": str(exc)}
                    failures.append(result)
                if progress:
                    progress(result)
                write_json(output / "export-progress.json", {"requested": len(jobs),
                    "completed": len(results), "failed": len(failures), "failures": failures}, mode=PUBLIC_FILE_MODE)
        if not failures:
            build_actor_library_index(root, generation)
            write_json(output / "index.json", _json(generation / "index.json"), mode=PUBLIC_FILE_MODE)
        report = {"version": 1, "requested": len(jobs), "completed": len(results),
                  "motionStorageRevision": MOTION_STORAGE_REVISION, "rotationEncoding": rotation_encoding,
                  "failed": len(failures), "failures": failures, "results": results, "sourceGaps": plan["gaps"]}
        write_json(output / "export-report.json", report, mode=PUBLIC_FILE_MODE)
    return report
