"""BulkHarvestToolTimelineView's runtime-bound hammer and camera streams.

The timeline names its clips by serialized identity. The tool and field camera
are supplied by the view at runtime, so they cannot be inferred from null scene
bindings. Keep that contract explicit, and never assign a source object identity
to the exported camera proxy.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

from core.atomic import PUBLIC_FILE_MODE, write_json
from core.gltf import GLB
from core.jsonio import normalize
from fixtures.animations import decode_clip, embed, _write_channels
from . import step_items as steps
from .scenes import PackageExtract

PACKAGE = "mysekai__player__motion__bulk_harvest"
PREFAB = "tl_bulk_harvest_main"
VIEW = "BulkHarvestToolTimelineView"
TOOL_PACKAGE = "mysekai__tool__mdl_site_tool_hammer01"
TOOL_PREFAB = "mdl_site_tool_hammer01"
CAMERA_NODE = "moly_bulk_camera"


def selected_tracks(tracks, targets, director, package):
    """Resolve the three explicitly runtime-bound animation streams."""
    timeline = steps._timeline(tracks, director)
    selected = {}
    bindings = {steps._asset(row.get("key")): row.get("value")
                for row in director.get("sceneBindings", [])}
    for track in steps._walk_tracks(timeline.get("tracks")):
        if track.get("class") != "AnimationTrack":
            continue
        name = track.get("name")
        if name not in ("CharacterAnimator", "Fixture1", "camera") or name in selected:
            raise ValueError(f"unsupported bulk animation stream: {name!r}")
        identity = steps._asset(track.get("asset"))
        if steps._asset(bindings.get(identity)) is not None:
            raise ValueError(f"bulk runtime stream {name!r} unexpectedly has a scene binding")
        clips = []
        for row in sorted((row for row in targets.get("keyedClips", [])
                           if steps._asset(row.get("track")) == identity),
                          key=lambda row: row.get("clipIndex", 0)):
            target = row.get("target") or {}
            source = steps._asset(target.get("sourceClip"))
            if source is None or not target.get("clipName") or not target.get("targetPackage"):
                raise ValueError(f"bulk stream {name!r} has an unresolved clip")
            clips.append({"name": target["clipName"], "sourceClip": {
                "file": source[0], "pathId": source[1]},
                "sourcePackage": target["targetPackage"]})
        if not clips:
            raise ValueError(f"bulk stream {name!r} has no clips")
        selected[name] = clips
    if set(selected) != {"CharacterAnimator", "Fixture1", "camera"}:
        raise ValueError("bulk timeline is missing a runtime animation stream")
    return selected


def clip_records(store, clips):
    files = {}
    for clip in clips:
        package = store.package(clip["sourcePackage"])
        if package is None:
            raise ValueError(f"missing clip package: {clip['sourcePackage']}")
        records = steps._Records(store, package)
        found = records.lookup(steps._asset(clip["sourceClip"]))
        if found is None or found[0].kinds[found[1]] != "AnimationClip":
            raise ValueError(f"clip source is not an AnimationClip: {clip['sourceClip']}")
        record, pid = found
        if str(record.tree(pid).get("m_Name")) != clip["name"]:
            raise ValueError(f"clip name disagrees with source: {clip['sourceClip']}")
        files[record.archive] = record
    return SimpleNamespace(lookup=lambda identity: (
        (files[identity[0]], int(identity[1]))
        if identity[0] in files and int(identity[1]) in files[identity[0]].kinds else None))


def _load_glb(path):
    data = path.read_bytes()
    if data[:4] != b"glTF" or struct.unpack_from("<I", data, 4)[0] != 2:
        raise ValueError("expected GLB 2")
    length = struct.unpack_from("<I", data, 12)[0]
    result = GLB()
    result.g = json.loads(data[20:20 + length])
    offset = 20 + length
    binary_length = struct.unpack_from("<I", data, offset)[0]
    if data[offset + 4:offset + 8] != b"BIN\x00":
        raise ValueError("GLB has no binary chunk")
    result.bin = bytearray(data[offset + 8:offset + 8 + binary_length])
    return result


def _table(glb):
    roots = glb.g["scenes"][glb.g["scene"]]["nodes"]
    if len(roots) != 1:
        raise ValueError("source prefab must have one root before adding the camera proxy")
    paths = {}
    def visit(index, parent=""):
        node = glb.g["nodes"][index]
        path = parent + "/" + node["name"] if parent else node["name"]
        if path in paths:
            raise ValueError(f"ambiguous animation path: {path}")
        paths[path] = index
        for child in node.get("children", []):
            visit(child, path)
    visit(roots[0])
    root = glb.g["nodes"][roots[0]]["name"]
    return {"root": root, "paths": paths, "animators": [root]}


def append_camera(glb, records, clips):
    """Add an independent reflect-X proxy; the renderer corrects camera forward."""
    if any(node.get("name") == CAMERA_NODE for node in glb.g["nodes"]):
        raise ValueError("camera proxy already exists")
    node = len(glb.g["nodes"])
    glb.g["nodes"].append({"name": CAMERA_NODE, "extras": {"exportedProxy": {
        "role": "runtime-bound-field-camera", "sourcePathHash": 0,
        "rotationBasis": "unity-transform-reflect-x",
        "cameraForwardCorrection": "runtime-y-pi"}}})
    glb.g["scenes"][glb.g["scene"]]["nodes"].append(node)
    for clip in clips:
        record, pid = records.lookup(steps._asset(clip["sourceClip"]))
        tree = record.tree(pid)
        channels, accounting, anomalies, _ = decode_clip(tree, {
            0: (node, CAMERA_NODE, 0, "explicit-runtime-camera-proxy")})
        if anomalies or set(accounting) != {"gltf-channel"} or not channels:
            raise ValueError(f"camera clip contains unsupported bindings: {clip['name']}: {accounting}")
        muscle = tree.get("m_MuscleClip") or {}
        stop = float(muscle.get("m_StopTime", 0.0))
        animation = _write_channels(glb, clip["name"], channels, duration=stop)
        animation.setdefault("extras", {})["sourceClip"] = clip["sourceClip"]
        animations = glb.g.setdefault("animations", [])
        clip.update(animation=len(animations), channels=len(channels),
                    sourceStopTime=stop, sourceLoopTime=bool(muscle.get("m_LoopTime", False)))
        animations.append(animation)


def export_bulk_harvest(store, records, tracks, targets, out_dir):
    """Export source documents first, then append only identity-selected clips."""
    found = [(container, record, go) for container, record, go in records.containers()
             if Path(container).name == PREFAB + ".prefab"]
    if len(found) != 1:
        raise ValueError("bulk main prefab is missing or ambiguous")
    container, record, go = found[0]
    views = [(owner, pid) for owner, pid in steps._components(store, record, go)
             if owner.kinds.get(pid) == "MonoBehaviour" and owner.script_of(pid) == VIEW]
    if len(views) != 1:
        raise ValueError("bulk main prefab lacks its view")
    owner, view_pid = views[0]
    director = store.follow(owner, owner.tree(view_pid).get("_playableDirector"))
    if director is None:
        raise ValueError("bulk view has no director")
    director_id = (director[0].archive, str(director[1]))
    row = steps._director_row(steps._prefab_row(tracks, container), director_id)
    selected = selected_tracks(tracks, targets, row, PACKAGE)
    all_clips = sum(selected.values(), [])
    selected_records = clip_records(store, all_clips)
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    documents = {}
    for package, prefab in ((PACKAGE, PREFAB), (TOOL_PACKAGE, TOOL_PREFAB)):
        if store.package(package) is None:
            raise ValueError(f"bulk asset package is missing: {package}")
        store.load_dependencies([package])
        document = PackageExtract(store, package, directory, prefab,
            classification=("bulk-harvest", prefab), primary_prefab=prefab + ".prefab").run()
        documents[prefab] = document
    tool_path = directory / (TOOL_PREFAB + ".glb")
    tool = _load_glb(tool_path)
    report = {"animations": []}
    summary = embed(tool, steps._clip_package(selected_records, selected["Fixture1"]),
                    [_table(tool)], report)
    if summary["unresolvedSlots"] or summary["anomalies"]:
        raise ValueError(f"bulk hammer clips do not bind: {summary}")
    exported = {row["name"]: row for row in report["animations"]}
    for clip in selected["Fixture1"]:
        row = exported.get(clip["name"])
        if row is None or not row["channels"]:
            raise ValueError(f"bulk hammer clip has no channels: {clip['name']}")
        clip.update({k: row[k] for k in ("animation", "channels", "sourceStopTime", "sourceLoopTime")})
    main_path = directory / (PREFAB + ".glb")
    main = _load_glb(main_path)
    append_camera(main, selected_records, selected["camera"])
    for prefab, glb in ((PREFAB, main), (TOOL_PREFAB, tool)):
        path = directory / (prefab + ".glb")
        path.write_bytes(glb.blob())
        doc = documents[prefab]
        doc["geometry"].update(bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        doc["runtimeAnimations"] = selected["camera" if prefab == PREFAB else "Fixture1"]
        write_json(directory / (prefab + ".json"), normalize(doc), mode=PUBLIC_FILE_MODE)
    key = f"{steps.logical_bundle(PACKAGE)}|{PREFAB}"
    item = {"package": PACKAGE, "prefab": PREFAB, "container": container,
        "glb": main_path.name, "glbSha256": documents[PREFAB]["geometry"]["sha256"],
        "root": PREFAB, "view": {"asset": {"file": owner.archive, "pathId": str(view_pid)},
        "script": VIEW, "director": {"file": director_id[0], "pathId": director_id[1]}},
        "clips": selected["camera"], "bulk_harvest": {
            "tool": {"package": TOOL_PACKAGE, "glb": tool_path.name,
                     "document": TOOL_PREFAB + ".json", "clips": selected["Fixture1"]},
            "camera": {"node": CAMERA_NODE, "rotationBasis": "unity-transform-reflect-x",
                       "cameraForwardCorrection": "runtime-y-pi", "fovAnimated": False},
            "characterClips": selected["CharacterAnimator"],
            "sourcePackages": [store.source_receipt(name) for name in sorted(
                {PACKAGE, TOOL_PACKAGE} | {clip["sourcePackage"] for clip in all_clips})]}}
    return {key: item}, [{"item": key, "clips": len(all_clips), "cameraClips": 2,
                          "toolClips": len(selected["Fixture1"]), "anomalies": 0}]
