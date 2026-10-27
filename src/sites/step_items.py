"""A site package's timelines that play outside the fixture family.

Two kinds of director live in a site package:

Step items
    ``PlayerAvatarView.UpdateStepItemObject(bundle, file)`` loads
    ``file + ".prefab"`` from a site bundle and instantiates it under the
    player avatar's ``Root`` bone.  The prefab root carries
    ``PlayerAvatarItemTimelineView``, whose ``_playableDirector`` plays the
    prefab's own timeline: the ``CharacterAnimator`` stream is bound to the
    avatar at runtime, the other animation tracks are bound in the prefab to
    Animators inside it (the prop), and the Control clips reach objects of the
    prefab through the director's exposed-reference table.

Scene directors
    A director inside the site prefab itself (the delivery site's tree is the
    one ``DeliveryPlaceObjectView._playableDirector`` names) binds its
    animation tracks to Animators of the site scene.

Output, under the asset root; the ``site-timeline`` directory is written aside
and swapped in whole::

    site-timeline/{tracks,clips,clip-targets}/<package>.json
        the three timeline tables, read by the same readers (and in the same
        format) as ``fixture-timeline/``
    site-timeline/step-items/<prefab>.glb
        one step prefab alone -- the furniture geometry writer walking that
        one root -- with the clips of its own prop tracks embedded
    site-timeline/step-items/<prefab>__<clip>.glb
        a scene director's rig: the subtree of the first Animator a track
        binds, walked the same way, with that track's clip embedded
    site-timeline/step-items/manifest.json
        ``items["<bundle>|<file>"]`` for the step items and
        ``directors["<bundle>|<prefab>"]`` for the scene directors

Clips are selected by source identity from the clip-target table, never by
name, and every animation track's scene binding must name an Animator inside
the exported root; anything else is a named failure.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

from core.atomic import PUBLIC_FILE_MODE, write_json
from core.assets.packages import PackageStore, pairs, require_paths

OUTPUT = Path("site-timeline")
STEP_DIRECTORY = "step-items"
STEP_VIEW = "PlayerAvatarItemTimelineView"
SITE_PREFIX = "mysekai__site__"
CHARACTER_STREAM = "CharacterAnimator"
MANIFEST_VERSION = 1


def _asset(value):
    if not isinstance(value, dict) or "file" not in value or "pathId" not in value:
        return None
    return (str(value["file"]), str(value["pathId"]))


def _walk_tracks(tracks):
    for track in tracks or []:
        yield track
        yield from _walk_tracks(track.get("children"))


def logical_bundle(package):
    """``mysekai__site__field__x`` -> ``mysekai/site/field/x`` (the game's name)."""
    return package.replace("__", "/")


class _Records:
    """One package's serialized files, addressed by the tables' identities."""

    def __init__(self, store, package):
        self.store = store
        self.package = package
        self.by_archive = {record.archive: record for record in package.files if record.kinds}

    def lookup(self, identity):
        file, path_id = identity
        record = self.by_archive.get(file)
        if record is None or int(path_id) not in record.kinds:
            return None
        return record, int(path_id)

    def containers(self):
        """``(container path, record, root GameObject id)`` of every prefab."""
        for record in self.package.files:
            for asset_id, kind in record.kinds.items():
                if kind != "AssetBundle":
                    continue
                for path, entry in pairs(record.tree(asset_id).get("m_Container")):
                    if not str(path).endswith(".prefab"):
                        continue
                    root = self.store.follow(record, (entry or {}).get("asset"))
                    if root is None or root[0].kinds.get(root[1]) != "GameObject":
                        raise ValueError(f"prefab does not resolve to its GameObject: {path}")
                    yield str(path), root[0], root[1]


def _components(store, record, game_object):
    for item in record.tree(game_object).get("m_Component") or []:
        pointer = item.get("component") if isinstance(item, dict) else item[1]
        target = store.follow(record, pointer)
        if target is None:
            raise ValueError(f"unresolved component of GameObject {game_object}")
        yield target


def _transform_of(store, record, game_object):
    for owner, component in _components(store, record, game_object):
        if owner.kinds.get(component) in ("Transform", "RectTransform"):
            return owner, component
    raise ValueError(f"GameObject {game_object} has no Transform")


def step_views(records):
    """Every prefab whose root carries the step item view, with its director."""
    views = []
    for container, record, game_object in records.containers():
        for owner, component in _components(records.store, record, game_object):
            if owner.kinds.get(component) != "MonoBehaviour":
                continue
            if owner.script_of(component) != STEP_VIEW:
                continue
            director = records.store.follow(owner, owner.tree(component).get("_playableDirector"))
            if director is None:
                raise ValueError(f"{container}: the step item view names no director")
            views.append({"container": container,
                          "file": Path(container).stem,
                          "record": record, "gameObject": game_object,
                          "view": (owner.archive, str(component)),
                          "director": (director[0].archive, str(director[1]))})
    return views


def _prefab_row(tracks, container):
    rows = [row for row in tracks.get("prefabs", []) if row.get("container") == container]
    if len(rows) != 1:
        raise ValueError(f"{container}: {len(rows)} prefab rows in the track table")
    return rows[0]


def _director_row(prefab, director):
    rows = [row for row in prefab.get("directors", []) if _asset(row.get("asset")) == director]
    if len(rows) != 1:
        raise ValueError(f"{prefab['container']}: director {director} is not one of its directors")
    return rows[0]


def _timeline(tracks, director_row):
    identity = _asset(director_row.get("timeline"))
    rows = [row for row in tracks.get("timelines", []) if _asset(row.get("asset")) == identity]
    if len(rows) != 1:
        raise ValueError(f"director timeline {identity} is missing or duplicated in the track table")
    return rows[0]


def bound_animation_tracks(tracks, targets, director_row, package):
    """``(track, animator, clips)`` for every animation track the director binds.

    The CharacterAnimator stream is skipped: the view binds it to the avatar
    at runtime.  A track whose scene binding names nothing, or whose clip
    ships in another package, is a named failure.
    """
    timeline = _timeline(tracks, director_row)
    bindings = {_asset(row.get("key")): row.get("value")
                for row in director_row.get("sceneBindings", [])}
    keyed = {}
    for row in targets.get("keyedClips", []):
        keyed.setdefault(_asset(row.get("track")), []).append(row)
    out = []
    for track in _walk_tracks(timeline.get("tracks")):
        if track.get("class") != "AnimationTrack" or track.get("name") == CHARACTER_STREAM:
            continue
        identity = _asset(track.get("asset"))
        clips = []
        for row in sorted(keyed.get(identity, []), key=lambda row: row.get("clipIndex", 0)):
            target = row.get("target") or {}
            source = _asset(target.get("sourceClip"))
            if source is None or not target.get("clipName"):
                raise ValueError(f"track {track.get('name')!r}: clip {row.get('clipIndex')} has no source identity")
            if target.get("targetPackage") != package:
                raise ValueError(f"track {track.get('name')!r}: clip {target['clipName']} ships in "
                                 f"{target.get('targetPackage')}, not in {package}")
            clips.append({"name": target["clipName"], "sourceClip": {"file": source[0], "pathId": source[1]}})
        if not clips:
            continue
        animator = _asset(bindings.get(identity))
        if animator is None:
            raise ValueError(f"track {track.get('name')!r} binds no Animator in its director")
        out.append({"track": identity, "name": track.get("name"), "animator": animator, "clips": clips})
    return out


def _any_bound(director_row):
    return any(row.get("value") is not None for row in director_row.get("sceneBindings", []))


def _descends(record, transform, root):
    seen = set()
    while transform and transform not in seen:
        if transform == root:
            return True
        seen.add(transform)
        transform = (record.tree(transform).get("m_Father") or {}).get("m_PathID", 0)
    return False


def _clip_package(records, clips):
    """The selected AnimationClip objects, as the embed pass reads a package."""
    files = {}
    for clip in clips:
        found = records.lookup((clip["sourceClip"]["file"], clip["sourceClip"]["pathId"]))
        if found is None or found[0].kinds[found[1]] != "AnimationClip":
            raise ValueError(f"clip {clip['name']} ({clip['sourceClip']}) is not an AnimationClip of this package")
        record, path_id = found
        name = str(record.tree(path_id).get("m_Name", ""))
        if name != clip["name"]:
            raise ValueError(f"{clip['sourceClip']} is named {name!r}, not {clip['name']!r}")
        entry = files.setdefault(record.archive, SimpleNamespace(archive=record.archive, kinds={}, objects={}))
        entry.kinds[path_id] = "AnimationClip"
        entry.objects[path_id] = record.objects[path_id]
    return SimpleNamespace(files=list(files.values()))


def export_root(store, records, record, root_transform, clips, path):
    """One transform subtree as its own glb, with *clips* embedded.

    The furniture geometry writer's own walk, skin binding and animation embed
    run on this one root; nothing else of the package is written.  Returns the
    embedded clip rows (name, animation index, source identity) and the
    writer's anomalies.
    """
    from core.gltf import GLB, COORDINATE_SPACE, coordinate_contract
    from fixtures import meshes as writer
    from fixtures.animations import embed
    from fixtures.collision import CollisionExport
    from sites.geometry import Graph

    glb = GLB(generator="moly-root site step items")
    glb.g["asset"]["extras"] = {
        "coordinateContract": COORDINATE_SPACE,
        "coordinateUnits": "source-unity-unit",
        "uvConvention": "unity-bottom-left",
        "coordinateSemantics": coordinate_contract(),
    }
    report = {"name": path.stem, "nodeNames": set(), "vertexCount": 0, "meshCount": 0,
              "zeroVertexMeshes": 0, "hasGeometry": False, "skinCount": 0,
              "jointCount": 0, "anomalies": [], "animations": []}
    collision = CollisionExport(store)
    ctx = {"fixtureView": set(), "fvRoots": set(), "root": None, "collision": collision,
           "variantGoids": set(), "mesh_cache": {}, "material_cache": {}, "tex_cache": {},
           "skin_cache": {}, "channel_cache": {}, "nodeIndex": {}, "pendingSkins": [],
           "tables": [], "table": None, "report": report}
    transforms, gameobjects, _ = writer._graph(record)
    ctx["source_graph"] = Graph(record)
    ctx["transforms"] = transforms
    ctx["gameobjects"] = gameobjects
    ctx["fenceRoots"], ctx["fenceParts"] = writer._fence_views(record)
    ctx["roadCells"] = writer._road_cells(record)
    collision.prepare(record, ctx["source_graph"], ctx["fixtureView"])
    roots, variants = writer._variants(glb, record, store, transforms, gameobjects,
                                       [root_transform], ctx, writer._container(record))
    summary = embed(glb, _clip_package(records, clips), ctx["tables"], report)
    glb.g["scenes"] = [{"nodes": roots}]
    glb.g["scene"] = 0
    exported = {row["name"]: row for row in report["animations"]}
    missing = [clip["name"] for clip in clips if clip["name"] not in exported]
    if missing:
        raise ValueError(f"{path.name}: clips not embedded: {', '.join(missing)}")
    for clip in clips:
        row = exported[clip["name"]]
        if not row["channels"]:
            raise ValueError(f"{path.name}: clip {clip['name']} resolves no channel on this root")
        clip["animation"] = row["animation"]
        clip["channels"] = row["channels"]
        clip["sourceStopTime"] = row["sourceStopTime"]
        clip["sourceLoopTime"] = row["sourceLoopTime"]
    path.parent.mkdir(parents=True, exist_ok=True)
    data = glb.blob()
    path.write_bytes(data)
    os.chmod(path, PUBLIC_FILE_MODE)
    return {"glb": path.name, "sha256": hashlib.sha256(data).hexdigest(),
            "root": variants[0]["rootName"], "nodes": variants[0]["nodeCount"],
            "meshes": report["meshCount"], "skins": report["skinCount"],
            "unresolvedSlots": summary["unresolvedSlots"],
            "anomalies": report["anomalies"] + summary["anomalies"]}


def export_step_items(store, records, package, tracks, targets, out_dir):
    items, reports = {}, []
    for view in step_views(records):
        prefab = _prefab_row(tracks, view["container"])
        director = _director_row(prefab, view["director"])
        bound = bound_animation_tracks(tracks, targets, director, package)
        _, root_transform = _transform_of(store, view["record"], view["gameObject"])
        clips = []
        for track in bound:
            found = records.lookup(track["animator"])
            if found is None or found[0].kinds[found[1]] != "Animator":
                raise ValueError(f"{view['file']}: track {track['name']!r} binds {track['animator']}, not an Animator here")
            owner = (found[0].tree(found[1]).get("m_GameObject") or {}).get("m_PathID")
            _, animator_transform = _transform_of(store, found[0], owner)
            if found[0] is not view["record"] or not _descends(view["record"], animator_transform, root_transform):
                raise ValueError(f"{view['file']}: track {track['name']!r} binds an Animator outside the prefab")
            for clip in track["clips"]:
                if clip["sourceClip"] not in [c["sourceClip"] for c in clips]:
                    clips.append(dict(clip))
        exported = export_root(store, records, view["record"], root_transform, clips,
                               out_dir / f"{view['file']}.glb")
        key = f"{logical_bundle(package)}|{view['file']}"
        items[key] = {
            "package": package, "prefab": view["file"], "container": view["container"],
            "glb": exported["glb"], "glbSha256": exported["sha256"], "root": exported["root"],
            "view": {"asset": {"file": view["view"][0], "pathId": view["view"][1]},
                     "script": STEP_VIEW,
                     "director": {"file": view["director"][0], "pathId": view["director"][1]}},
            "clips": clips,
        }
        reports.append({"item": key, **{k: v for k, v in exported.items() if k != "anomalies"},
                        "anomalies": len(exported["anomalies"]), "clips": len(clips)})
    return items, reports


def export_scene_directors(store, records, package, tracks, targets, out_dir, step_containers):
    """Directors of the package's other prefabs that bind animation tracks.

    One rig per distinct clip: the subtree of the first Animator (in track
    order) that plays it, so that the clip's channels land on the same
    relative names every other bound Animator of that clip carries.
    """
    directors, reports = {}, []
    for prefab in tracks.get("prefabs", []):
        if prefab.get("container") in step_containers:
            continue
        for director in prefab.get("directors", []):
            if director.get("timeline") is None:
                continue
            stem = Path(prefab["container"]).stem
            if not _any_bound(director):
                # A template prefab: its tracks are bound where it is used.
                reports.append({"director": stem, "skipped": "no track is bound in the prefab"})
                continue
            bound = bound_animation_tracks(tracks, targets, director, package)
            if not bound:
                continue
            rigs, by_clip = [], {}
            for track in bound:
                found = records.lookup(track["animator"])
                if found is None or found[0].kinds[found[1]] != "Animator":
                    raise ValueError(f"{stem}: track {track['name']!r} binds {track['animator']}, not an Animator here")
                if len(track["clips"]) != 1:
                    raise ValueError(f"{stem}: track {track['name']!r} holds {len(track['clips'])} clips; "
                                     "one rig per track clip is the only reading here")
                clip = track["clips"][0]
                key = (clip["sourceClip"]["file"], clip["sourceClip"]["pathId"])
                if key not in by_clip:
                    owner = (found[0].tree(found[1]).get("m_GameObject") or {}).get("m_PathID")
                    _, transform = _transform_of(store, found[0], owner)
                    row = {"clips": [dict(clip)], "animators": [], "tracks": []}
                    exported = export_root(store, records, found[0], transform, row["clips"],
                                           out_dir / f"{stem}__{clip['name']}.glb")
                    row.update({"glb": exported["glb"], "glbSha256": exported["sha256"],
                                "root": exported["root"]})
                    reports.append({"director": stem, **{k: v for k, v in exported.items()
                                                         if k != "anomalies"},
                                    "anomalies": len(exported["anomalies"])})
                    by_clip[key] = row
                    rigs.append(row)
                by_clip[key]["animators"].append({"file": track["animator"][0], "pathId": track["animator"][1]})
                by_clip[key]["tracks"].append({"file": track["track"][0], "pathId": track["track"][1]})
            key = f"{logical_bundle(package)}|{stem}"
            directors[key] = {"package": package, "prefab": stem, "container": prefab["container"],
                              "director": director["asset"], "hierarchy": director.get("hierarchy"),
                              "rigs": rigs}
    return directors, reports


def export_site_timelines(bundles_root, asset_root, packages):
    """Export the site packages *packages*; see the module docstring."""
    from core.extract import _configure_unity_version, _run_timeline_job
    import json
    _configure_unity_version(None)
    bundles, root = Path(bundles_root), Path(asset_root)
    if not packages:
        raise ValueError("name at least one site package")
    paths = [bundles / name for name in packages]
    require_paths([str(path) for path in paths])
    for name in packages:
        from .bulk_harvest import PACKAGE as BULK_PACKAGE
        if not name.startswith(SITE_PREFIX) and name != BULK_PACKAGE:
            raise ValueError(f"{name} is not a site package")
    output = root / OUTPUT
    staged = output.with_name(output.name + ".staging")
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    job = _run_timeline_job([str(path) for path in paths], str(staged), bundle_root=str(bundles))
    store = PackageStore([str(path) for path in paths], str(bundles))
    store.load_dependencies(list(packages))
    step_dir = staged / STEP_DIRECTORY
    step_dir.mkdir()
    manifest = {"version": MANIFEST_VERSION, "items": {}, "directors": {}, "packages": {}}
    reports = []
    for name in packages:
        tracks_path = staged / "tracks" / f"{name}.json"
        targets_path = staged / "clip-targets" / f"{name}.json"
        if not tracks_path.is_file() or not targets_path.is_file():
            raise ValueError(f"{name}: the timeline readers wrote no tables")
        tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
        targets = json.loads(targets_path.read_text(encoding="utf-8"))
        package = store.package(name)
        records = _Records(store, package)
        if name == BULK_PACKAGE:
            from .bulk_harvest import export_bulk_harvest
            items, item_reports = export_bulk_harvest(store, records, tracks, targets, step_dir)
        else:
            items, item_reports = export_step_items(store, records, name, tracks, targets, step_dir)
        containers = {row["container"] for row in items.values()}
        directors, director_reports = export_scene_directors(store, records, name, tracks, targets,
                                                             step_dir, containers)
        manifest["items"].update(items)
        manifest["directors"].update(directors)
        manifest["packages"][name] = store.source_receipt(name)
        reports += item_reports + director_reports
    manifest["summary"] = {"items": len(manifest["items"]), "directors": len(manifest["directors"]),
                           "glbs": len(list(step_dir.glob("*.glb")))}
    write_json(step_dir / "manifest.json", manifest, mode=PUBLIC_FILE_MODE)
    previous = output.with_name(output.name + ".previous")
    if previous.exists():
        shutil.rmtree(previous)
    if output.exists():
        output.rename(previous)
    staged.rename(output)
    if previous.exists():
        shutil.rmtree(previous)
    per = job["perBundle"]
    return {"directory": str(OUTPUT).replace(os.sep, "/"),
            "tables": {name: {key: per.get(name, {}).get(key) for key in
                              ("timelineAssets", "trackObjects", "visited", "residual", "bad", "unresolved", "unread")}
                       for name in packages},
            "exports": reports, "summary": manifest["summary"]}
