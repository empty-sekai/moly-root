"""Player fixture-timeline clips, exported against the player avatar's rig.

A player fixture timeline (the prefab a ``mysekaiFixturePlayerTimelines``
row names) binds its ``CharacterAnimator`` track to the player avatar's own
animator.  The clips of that track ship in the fixture model bundles, and a
compiled clip names each transform by the CRC32 of its path from the
animator root.  Those paths are the avatar's (``Root/Hips``, ``Root/Hips/Spine``
and so on), so the clips resolve against the avatar skeleton in
``virtual_live/avatar/model/default`` and against nothing in the fixture
bundle that carries them.  Exporting them with the fixture model's own node
tree therefore gives clips with no channels.

This export takes, for each fixture-timeline package a player row names, the
``CharacterAnimator`` clips of its player prefabs -- selected by source file
and pathId from the formal clip-target table, never by name -- decodes them
against the avatar skeleton (built from the avatar model bundle alone) and
writes one clip file per package::

    fixture-timeline/avatar-clips/<package>.glb         the avatar node tree + the clips
    fixture-timeline/avatar-clips/<package>.index.json  one row per clip
    fixture-timeline/avatar-clips/manifest.json         package -> its two files

The avatar motion group (``avatar/motion``) is not touched: these clips are
the timeline's, not the player's own motions.

Each clip row carries the evidence for its binding: how many of its transform
bindings resolve on the avatar tree and how many on the node tree of the
package the clip ships in.  The clip file's scene root is the instantiated
avatar root (the same rule ``chara.player_avatar`` applies), so a loader that
keys curves by node names from the scene root keys them exactly as it keys
the avatar model's own nodes.

This module reuses (does not reimplement) ``perf.animations`` decoding and
``chara.player_avatar``'s tree selection and playback metadata.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
from pathlib import Path
import shutil

import UnityPy

from core.atomic import PUBLIC_FILE_MODE, write_json
from core.gltf import GLB
from perf.animations import (
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
from .fixture_actor_libraries import _asset, _template, _walk_tracks
from .player_avatar import (
    _clip_frame_info,
    _clip_playback_metadata,
    _scene_roots,
    read_player_mesh,
)

AVATAR_MODEL_PACKAGE = "virtual_live__avatar__model__default"
OUTPUT_DIRECTORY = Path("fixture-timeline") / "avatar-clips"
TIMELINE_PREFIX = "mysekai__fixture_timeline__mdl_"
PLAYER_ROWS = "mysekai-fixture-player-timelines.json"
TRANSFORM_TYPEID = 4


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _player_rows(root):
    document = _json(root / PLAYER_ROWS)
    entries = document["entries"]
    order = document.get("rowOrder") or list(entries)
    return [entries[str(key)] for key in order]


def build_plan(asset_root):
    """Every CharacterAnimator clip of every player prefab, per timeline package.

    Reads only the player rows and the three-table fixture-timeline export.
    A row whose prefab, director, timeline or clip target is missing becomes a
    named gap; nothing is substituted.
    """
    root = Path(asset_root)
    gaps = []
    packages = collections.defaultdict(lambda: {"prefabs": set(), "clips": {}})
    tables = {}
    for row in _player_rows(root):
        logical = row["assetbundleName"]
        package = TIMELINE_PREFIX + _template(logical)
        if package not in tables:
            tracks = root / "fixture-timeline" / "tracks" / f"{package}.json"
            targets = root / "fixture-timeline" / "clip-targets" / f"{package}.json"
            tables[package] = ((_json(tracks), _json(targets))
                               if tracks.is_file() and targets.is_file() else None)
        if tables[package] is None:
            gaps.append({"row": row["id"], "package": package,
                         "reason": "formal timeline tables are absent"})
            continue
        tracks, targets = tables[package]
        prefabs = [p for p in tracks.get("prefabs", [])
                   if Path(p["container"]).stem == logical]
        if len(prefabs) != 1:
            gaps.append({"row": row["id"], "package": package, "prefab": logical,
                         "reason": f"{len(prefabs)} prefabs of this exact name"})
            continue
        prefab = prefabs[0]
        by_asset = {_asset(t.get("asset")): t for t in tracks["timelines"]}
        keyed = collections.defaultdict(list)
        for keyed_clip in targets.get("keyedClips", []):
            keyed[_asset(keyed_clip.get("track"))].append(keyed_clip)
        active = {_asset(view.get("director")) for view in prefab.get("fixtureTimelineViews", [])}
        directors = [d for d in prefab.get("directors", [])
                     if not active or _asset(d.get("asset")) in active]
        if not directors:
            gaps.append({"row": row["id"], "package": package, "prefab": logical,
                         "reason": "prefab has no director"})
            continue
        entry = packages[package]
        entry["prefabs"].add(logical)
        for director in directors:
            timeline = by_asset.get(_asset(director.get("timeline")))
            if timeline is None:
                gaps.append({"row": row["id"], "package": package, "prefab": logical,
                             "reason": "director has no timeline table"})
                continue
            for track in _walk_tracks(timeline.get("tracks", [])):
                if track.get("class") != "AnimationTrack" or track.get("name") != "CharacterAnimator":
                    continue
                for keyed_clip in keyed.get(_asset(track.get("asset")), []):
                    target = keyed_clip.get("target")
                    identity = _asset((target or {}).get("sourceClip"))
                    if not target or not identity or not target.get("clipName") \
                            or not target.get("targetPackage"):
                        gaps.append({"row": row["id"], "package": package, "prefab": logical,
                                     "clipIndex": keyed_clip.get("clipIndex"),
                                     "reason": "clip target has no source identity"})
                        continue
                    entry["clips"][identity] = {
                        "name": target["clipName"],
                        "sourcePackage": target["targetPackage"],
                        "sourceClip": {"file": identity[0], "pathId": identity[1]},
                        "sourceStartTime": target.get("sourceStartTime"),
                        "sourceStopTime": target.get("sourceStopTime"),
                        "sourceLoopTime": target.get("sourceLoopTime"),
                        "sourceMetadataMissing": list(target.get("sourceMetadataMissing") or []),
                    }
    jobs = [{"package": package, "prefabs": sorted(entry["prefabs"]),
             "clips": [entry["clips"][key] for key in sorted(entry["clips"])]}
            for package, entry in sorted(packages.items()) if entry["clips"]]
    return {"version": 1, "jobs": jobs, "gaps": gaps,
            "summary": {"rows": len(_player_rows(root)), "packages": len(jobs),
                        "clips": sum(len(job["clips"]) for job in jobs),
                        "distinctClips": len({(c["sourceClip"]["file"], c["sourceClip"]["pathId"])
                                              for job in jobs for c in job["clips"]}),
                        "gaps": len(gaps)}}


def _transform_hashes(tree):
    bindings = (tree.get("m_ClipBindingConstant") or {}).get("genericBindings") or []
    return sorted({int(b.get("path", 0)) for b in bindings
                   if int(b.get("typeID", 0)) == TRANSFORM_TYPEID})


class _Source:
    """One fixture bundle: its AnimationClips by identity and its own tree."""

    def __init__(self, path):
        self.env = UnityPy.load(str(path))
        self.clips = {}
        for obj in self.env.objects:
            if obj.type.name == "AnimationClip" and obj.assets_file is not None:
                self.clips[(obj.assets_file.name, str(obj.path_id))] = obj
        self._tree = None

    @property
    def tree(self):
        if self._tree is None:
            self._tree = NodeHierarchy(list(self.env.objects), foreign=None)
        return self._tree


def _avatar(bundles):
    path = bundles / AVATAR_MODEL_PACKAGE
    if not path.is_file():
        raise FileNotFoundError(f"avatar model bundle is absent: {AVATAR_MODEL_PACKAGE}")
    env = UnityPy.load(str(path))
    hierarchy = NodeHierarchy(list(env.objects), foreign=None)
    # The body renderer marks the instantiated tree (not the import scaffold);
    # its mesh is not written to the clip files.
    body = read_player_mesh(env, GLB(), hierarchy)
    if body is None:
        raise ValueError("avatar model bundle has no body renderer to mark its instantiated tree")
    return hierarchy, body, hashlib.sha256(path.read_bytes()).hexdigest()


def _export_package(job, hierarchy, body, sources, bundles, staged):
    glb = GLB(generator="moly-root player timeline clips")
    rows = []
    for clip in job["clips"]:
        source_path = bundles / clip["sourcePackage"]
        if clip["sourcePackage"] not in sources:
            if not source_path.is_file():
                raise FileNotFoundError(f"source bundle is absent: {clip['sourcePackage']}")
            sources[clip["sourcePackage"]] = _Source(source_path)
        source = sources[clip["sourcePackage"]]
        identity = (clip["sourceClip"]["file"], clip["sourceClip"]["pathId"])
        obj = source.clips.get(identity)
        if obj is None:
            raise ValueError(f"{clip['name']}: no AnimationClip {identity} in {clip['sourcePackage']}")
        tree = obj.read_typetree()
        if str(tree.get("m_Name", "")) != clip["name"]:
            raise ValueError(f"{identity} is named {tree.get('m_Name')!r}, not {clip['name']!r}")
        hashes = _transform_hashes(tree)
        curves, channels, anomalies = decode_clip(tree, hierarchy)
        animation = len(glb.g.get("animations", []))
        _add_animation(glb, clip["name"], curves, channels, anomalies)
        sample_rate, duration, frame_count = _clip_frame_info(tree)
        rows.append({
            **clip,
            "animation": animation,
            "container": obj.container,
            "sampleRate": sample_rate, "durationSeconds": duration, "frameCount": frame_count,
            **_clip_playback_metadata(tree),
            "curves": len(curves), "gltfChannels": len(channels),
            "curveAccounting": curve_accounting(curves, channels),
            "anomalies": anomalies,
            "transformBindings": {
                "hashes": len(hashes),
                "resolvedOnAvatar": sum(1 for h in hashes if h in hierarchy.path_by_hash),
                "resolvedOnSourcePackage": sum(1 for h in hashes if h in source.tree.path_by_hash),
            },
        })
    roots = hierarchy.write_scene(glb)
    scene_roots = _scene_roots(hierarchy, roots, body)
    glb.g["scenes"][0]["nodes"] = scene_roots
    if len(scene_roots) != 1:
        raise ValueError(f"avatar clip file needs one scene root, has {len(scene_roots)}")
    root_name = hierarchy.nodes[scene_roots[0]]["name"]
    glb.g["asset"]["extras"] = {"binding": SEMANTICS, "formatVersion": FORMAT_VERSION,
                                "animatorPath": hierarchy.animator_path,
                                "anchors": hierarchy.anchors}
    accounting = collections.Counter()
    for row in rows:
        accounting.update(row["curveAccounting"])
    non_transform = sum(v for k, v in accounting.items()
                        if k == CLASS_NO_BINDING or k.startswith("typeid-"))
    denominator = sum(accounting.values()) - non_transform
    unresolved = accounting.get(CLASS_UNRESOLVED, 0) + accounting.get(CLASS_NO_NODE, 0)
    glb_name = f"{job['package']}.glb"
    index_name = f"{job['package']}.index.json"
    glb.save(str(staged / glb_name))
    record = {
        "version": 1, "formatVersion": FORMAT_VERSION, "binding": SEMANTICS,
        "package": job["package"], "prefabs": job["prefabs"],
        "bindingRootName": root_name, "avatarPackage": AVATAR_MODEL_PACKAGE,
        "sourcePackages": sorted({row["sourcePackage"] for row in rows}),
        "bindingCoverage": {"denominator": denominator, "resolved": denominator - unresolved,
                            "unresolved": unresolved, "nonTransformSlots": non_transform,
                            "assertionHolds": bool(denominator > 0 and unresolved == 0)},
        "clips": rows,
    }
    write_json(staged / index_name, record, mode=PUBLIC_FILE_MODE)
    return glb_name, index_name, record


def export_player_timeline_clips(asset_root, bundles_root, progress=None):
    """Export every player prefab's CharacterAnimator clips; see the module doc.

    The directory is written aside and swapped in whole once every package
    has exported; a failing package leaves the previous directory in place.
    """
    root, bundles = Path(asset_root), Path(bundles_root)
    plan = build_plan(root)
    hierarchy, body, avatar_sha = _avatar(bundles)
    output = root / OUTPUT_DIRECTORY
    staged = output.with_name(output.name + ".staging")
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    sources, packages, reports = {}, {}, []
    for job in plan["jobs"]:
        glb_name, index_name, record = _export_package(job, hierarchy, body, sources, bundles, staged)
        packages[job["package"]] = {
            "glb": glb_name, "index": index_name, "clips": len(record["clips"]),
            "glbSha256": hashlib.sha256((staged / glb_name).read_bytes()).hexdigest(),
            "indexSha256": hashlib.sha256((staged / index_name).read_bytes()).hexdigest(),
        }
        report = {"package": job["package"], "clips": len(record["clips"]),
                  "bindingCoverage": record["bindingCoverage"]}
        reports.append(report)
        if progress:
            progress(report)
    root_names = {_json(staged / p["index"])["bindingRootName"] for p in packages.values()}
    manifest = {
        "version": 1, "binding": SEMANTICS, "avatarPackage": AVATAR_MODEL_PACKAGE,
        "avatarPackageSha256": avatar_sha,
        "bindingRootName": root_names.pop() if len(root_names) == 1 else None,
        "packages": packages, "gaps": plan["gaps"], "summary": plan["summary"],
    }
    write_json(staged / "manifest.json", manifest, mode=PUBLIC_FILE_MODE)
    previous = output.with_name(output.name + ".previous")
    if previous.exists():
        shutil.rmtree(previous)
    if output.exists():
        output.rename(previous)
    staged.rename(output)
    if previous.exists():
        shutil.rmtree(previous)
    return {"directory": str(OUTPUT_DIRECTORY).replace(os.sep, "/"), "packages": reports,
            "summary": plan["summary"], "gaps": plan["gaps"]}
