"""Export humanoid motion libraries with source-owned playback metadata.

Index format v2 stores authored sourceLoopTime separately from the informational
legacySuffixLoop name convention. UseSourceAsset playback must use the source
value, never infer looping from a clip's suffix.

A motion package can hold two AnimationClips with the same name and different
curves. What a character plays is what its AnimatorController references, by
serialized file and path ID, so the shared library is selected through the
character controllers when they are supplied: exactly the referenced clips,
each under its own name, each carrying its source identity. Selecting by name
alone keeps whichever duplicate the package lists first, which is not the
referenced one in general; that mode remains for callers without controllers
and names every duplicate it dropped.
"""
import json
import math
import os
import re
import struct
import time

from core.gltf import GLB, unity_to_gltf_pos, unity_to_gltf_quat
from .mecanim import Rig, rig_doc, pose_bone, pose_root, sample_frames
from .mecanim.clip import ANIMATOR_TYPEID
from .mecanim.traits import GAME_TO_HUMAN
from .characters import CharacterAssets, read_scene
from .motion_storage import MOTION_STORAGE_REVISION, MotionStorage, validate_rotation_encoding
from .rotation_encoding import DEFAULT_ROTATION_ENCODING

_SUFFIX = re.compile(r"_(Start|Loop|End|OneShot|S|L|E|O)$", re.IGNORECASE)
INDEX_FORMAT_VERSION = 2
# Part of source-qualified export receipts: old name-targeted clips must not
# survive a cache hit when a humanoid slot is hosted by another scene node.
POSE_BINDING_REVISION = 2
INDEX_SEMANTICS = {
    "sourceClip": "original serialized-file name and signed path ID of the AnimationClip",
    "sourceStartTime": "verbatim AnimationClip.m_MuscleClip.m_StartTime; null when missing",
    "sourceStopTime": "verbatim AnimationClip.m_MuscleClip.m_StopTime; null when missing",
    "sourceLoopTime": ("verbatim AnimationClip.m_MuscleClip.m_LoopTime; null when missing; "
                       "the source value used by UseSourceAsset, with no suffix fallback"),
    "sourceMetadataMissing": "original field paths absent from the source record",
    "legacySuffixLoop": ("informational name-suffix convention only; must not determine "
                         "UseSourceAsset looping"),
    "duration": "last sampled frame time, distinct from the authored sourceStopTime",
}


def _clip_meta(name, frames, rate, source_metadata):
    match = _SUFFIX.search(name)
    suffix = (match.group(1) if match else "").upper()
    suffix = {"START": "S", "LOOP": "L", "END": "E", "ONESHOT": "O"}.get(suffix, suffix)
    base = name[:match.start()] if match else name
    return {"name": name, "base": base, "suffix": suffix or None,
            "frames": len(frames), "duration": frames[-1][0] if frames else 0.0,
            "sampleRate": rate, "legacySuffixLoop": suffix == "L",
            **source_metadata}


def _source_clip_metadata(obj, tree):
    """Clip ownership and the three authored MuscleClip timing fields.

    A false LoopTime is a real value. Missing fields remain None and are named;
    neither a clip-name suffix nor the resampled frame range fills them in.
    """
    source_file = getattr(getattr(obj, "assets_file", None), "name", None)
    path_id = getattr(obj, "path_id", None)
    missing = []
    if source_file is None:
        missing.append("sourceClip.file")
    if path_id is None:
        missing.append("sourceClip.pathId")
    metadata = {"sourceClip": {"file": source_file,
                               "pathId": str(path_id) if path_id is not None else None}}
    muscle = tree.get("m_MuscleClip")
    for field, source in (("sourceStartTime", "m_StartTime"),
                          ("sourceStopTime", "m_StopTime"),
                          ("sourceLoopTime", "m_LoopTime")):
        if isinstance(muscle, dict) and source in muscle:
            metadata[field] = muscle[source]
        else:
            metadata[field] = None
            missing.append(f"m_MuscleClip.{source}")
    metadata["sourceMetadataMissing"] = missing
    return metadata


def controller_clip_references(controller_bundles):
    """AnimationClip identities the AnimatorController of each bundle references.

    Each bundle must hold exactly one AnimatorController. A clip reference is a
    PPtr: file ID 0 is the controller's own serialized file, any other file ID
    indexes the file's external list, whose entry names the serialized file the
    clip lives in. Returns ``(rows, identities)``: one row per bundle and the
    union of ``(serialized file, path ID)`` pairs.
    """
    import UnityPy
    rows, union = [], set()
    for bundle in controller_bundles:
        env = UnityPy.load(str(bundle))
        controllers = [obj for obj in env.objects if obj.type.name == "AnimatorController"]
        if len(controllers) != 1:
            raise ValueError(f"{os.path.basename(str(bundle))}: expected one "
                             f"AnimatorController, found {len(controllers)}")
        obj = controllers[0]
        tree = obj.read_typetree()
        externals = [ext.path for ext in obj.assets_file.externals]
        refs = []
        for ref in tree.get("m_AnimationClips") or []:
            file_id, path_id = ref.get("m_FileID"), ref.get("m_PathID")
            if not path_id:
                raise ValueError(f"{tree.get('m_Name')}: null clip reference")
            if file_id == 0:
                file = obj.assets_file.name
            elif isinstance(file_id, int) and 0 < file_id <= len(externals):
                file = externals[file_id - 1].replace(chr(92), "/").rsplit("/", 1)[-1]
            else:
                raise ValueError(f"{tree.get('m_Name')}: clip file ID {file_id!r} "
                                 f"has no external entry")
            refs.append((file, str(path_id)))
        rows.append({"bundle": os.path.basename(str(bundle)),
                     "controller": tree.get("m_Name"),
                     "controllerPathId": str(obj.path_id),
                     "clips": len(refs), "distinct": len(set(refs))})
        union.update(refs)
    return rows, union


def discover_and_sample(motion_bundle, names=None, source_clips=None, referenced=None,
                        selection=None):
    """Decode every requested (or every present) AnimationClip.

    Returns successful samples and a structured, per-clip failure list.
    Generic Transform clips need their own binding decoder; absence of Animator
    curves must not be converted into an apparently successful default pose.

    ``referenced`` (a set of (serialized file, path ID) pairs, as returned by
    :func:`controller_clip_references`) keeps exactly those clips under their
    own names; two referenced clips sharing a name is an error, since one name
    cannot carry both. Without it and without ``source_clips`` the first clip
    of each name is kept. A ``selection`` dict, when given, receives every
    same-name group of the package with the identity kept for it.
    """
    import UnityPy
    env = UnityPy.load(motion_bundle)
    wanted = set(names) if names else None
    exact = {(str(file), str(path_id)) for file, path_id in source_clips} if source_clips is not None else None
    chosen = {(str(file), str(path_id)) for file, path_id in referenced} if referenced is not None else None
    samples, failures, seen = {}, [], set()
    found_sources = set()
    by_name = {}
    for obj in env.objects:
        if obj.type.name != "AnimationClip":
            continue
        name = "<unknown>"
        try:
            tt = obj.read_typetree()
            name = str(tt.get("m_Name", ""))
            identity = (str(obj.assets_file.name), str(obj.path_id))
            by_name.setdefault(name, []).append(identity)
            if not name or (wanted is not None and name not in wanted):
                continue
            if exact is not None:
                if identity not in exact:
                    continue
                found_sources.add(identity)
            elif chosen is not None:
                if identity not in chosen:
                    continue
                if name in seen:
                    raise ValueError("two referenced clips share this name")
                found_sources.add(identity)
            elif name in seen:
                continue
            seen.add(name)
            bindings = (tt.get("m_ClipBindingConstant") or {}).get("genericBindings") or []
            if not any(binding.get("typeID") == ANIMATOR_TYPEID for binding in bindings):
                failures.append({
                    "name": name,
                    "reason": "unsupported",
                    "detail": "non-humanoid generic requires its own Transform binding decoder",
                    "sourceFile": obj.assets_file.name,
                    "pathId": str(obj.path_id),
                    "bindingTypes": sorted({binding.get("typeID") for binding in bindings
                                            if isinstance(binding.get("typeID"), int)}),
                    "bindingCount": len(bindings),
                })
                continue
            rate, frames = sample_frames(tt)
            if not frames:
                raise ValueError("empty sampled frame set")
            exported_name = name
            if exact is not None:
                # Multiple AnimationClips in one serialized source legitimately
                # share names, or collapse onto the same legacy S/L/E suffix.
                # Source-qualified libraries never deduplicate those identities.
                import hashlib
                token = hashlib.sha256(identity[0].encode()).hexdigest()[:12]
                exported_name = f"{name}__src_{token}_{identity[1]}"
            samples[exported_name] = {"rate": rate, "frames": frames,
                             "sourceMetadata": {**_source_clip_metadata(obj, tt), "sourceClipName": name}}
        except Exception as exc:
            failures.append({"name": name, "reason": type(exc).__name__, "detail": str(exc)})
    if wanted is not None:
        for missing in sorted(wanted - seen):
            failures.append({"name": missing, "reason": "missing_clip",
                             "detail": "not present in motion bundle"})
    if exact is not None:
        for file, path_id in sorted(exact - found_sources):
            failures.append({"name": "<source identity>", "reason": "missing_clip",
                             "sourceFile": file, "pathId": path_id,
                             "detail": "exact source clip is not present in motion bundle"})
    if chosen is not None:
        for file, path_id in sorted(chosen - found_sources):
            failures.append({"name": "<source identity>", "reason": "missing_clip",
                             "sourceFile": file, "pathId": path_id,
                             "detail": "controller-referenced clip is not present in motion bundle"})
    if selection is not None:
        kept = {(meta["sourceMetadata"]["sourceClip"]["file"],
                 meta["sourceMetadata"]["sourceClip"]["pathId"]) for meta in samples.values()}
        selection["duplicateNames"] = [
            {"name": name, "variants": [
                {"file": file, "pathId": path_id,
                 **({"referenced": (file, path_id) in chosen} if chosen is not None else {}),
                 "kept": (file, path_id) in kept}
                for file, path_id in identities]}
            for name, identities in sorted(by_name.items()) if len(identities) > 1]
        selection["packageClips"] = sum(len(identities) for identities in by_name.values())
        selection["packageNames"] = len(by_name)
        if chosen is not None:
            selection["unreferencedClips"] = sum(
                1 for identities in by_name.values() for identity in identities
                if identity not in chosen)
    return samples, failures


def _reference_skeleton(bundle, aux=()):
    assets = CharacterAssets(bundle, aux)
    avatar = assets.one("Avatar").read_typetree()
    rig = Rig(rig_doc(avatar))
    nodes, _ = read_scene(assets)
    by_name, paths = {}, {}
    for i, node in enumerate(nodes):
        by_name.setdefault(node["name"], i)
        if node["path"]:
            paths[node["path"]] = i
    by_hash = {}
    for entry in avatar["m_TOS"]:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            node = paths.get(entry[1])
            if node is not None:
                by_hash[int(entry[0])] = node
    return rig, nodes, by_name, by_hash


def _add_animation(glb, rig, sampled, node_by_name, node_by_hash, storage=None,
                   rotation_encoding=DEFAULT_ROTATION_ENCODING, extras=None):
    validate_rotation_encoding(rotation_encoding)
    frames = sampled["frames"]
    times = [f[0] for f in frames]
    # Humanoid slots are semantic names, not necessarily scene node names.
    # For example sd_115/sd_130 put the hips slot on Root and retain Hips'
    # authored rest rotation. Overwriting Hips rotates the whole actor sideways.
    hosts = {bone: rig.host_node(bone) for bone in GAME_TO_HUMAN}
    bones = sorted(bone for bone, host in hosts.items() if host in node_by_name)
    rotations = {b: [] for b in bones}
    hips_t, twist, tdof_t = [], {}, {}
    for _, muscles, body_q, body_p, raw_twist, tdof in frames:
        locs = {b: pose_bone(rig, b, muscles) for b in bones if b != "Hips"}
        trans = rig.tdof_translations(tdof) if tdof else None
        ht, hq = pose_root(rig, muscles, body_q, body_p, locs=locs, trans=trans)
        for bone in bones:
            rotations[bone].append(hq if bone == "Hips" else locs[bone])
        hips_t.append(ht)
        if trans:
            for bone, t3 in trans.items():
                tdof_t.setdefault(bone, []).append(t3)
        for path_hash, q in raw_twist.items():
            node = node_by_hash.get(path_hash)
            if node is not None:
                norm = math.sqrt(sum(v * v for v in q)) or 1.0
                twist.setdefault(node, []).append(tuple(v / norm for v in q))
    storage = storage if storage is not None else MotionStorage(glb)
    time_accessor = storage.acc(b"".join(struct.pack("<f", t) for t in times), 5126,
                            "SCALAR", len(times), minmax=([times[0]], [times[-1]]))
    samplers, channels = [], []

    def channel(node, path, values, width):
        data = b"".join(struct.pack(f"<{width}f", *value) for value in values)
        if path == "rotation":
            accessor = storage.rotation(data, len(values), rotation_encoding=rotation_encoding)
        else:
            accessor = storage.acc(data, 5126, "VEC3", len(values))
        samplers.append({"input": time_accessor, "output": accessor, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": node, "path": path}})

    for bone in bones:
        channel(node_by_name[hosts[bone]], "rotation", [unity_to_gltf_quat(q) for q in rotations[bone]], 4)
    for node in sorted(twist):
        if len(twist[node]) != len(times):
            raise ValueError("auxiliary transform track has inconsistent frame count")
        channel(node, "rotation", [unity_to_gltf_quat(q) for q in twist[node]], 4)
    channel(node_by_name[hosts["Hips"]], "translation", [unity_to_gltf_pos(v) for v in hips_t], 3)
    for bone in sorted(tdof_t):
        if len(tdof_t[bone]) != len(times):
            raise ValueError("translation-DoF track has inconsistent frame count")
        channel(node_by_name[rig.host_node(bone)], "translation",
                [unity_to_gltf_pos(v) for v in tdof_t[bone]], 3)
    animation = {"name": sampled["name"], "samplers": samplers, "channels": channels}
    if extras:
        animation["extras"] = extras
    glb.g.setdefault("animations", []).append(animation)


def export_motion_library(reference_bundle, motion_bundle, out_dir, name="motion-library",
                          aux=(), names=None, binding_root=None, source_clips=None,
                          rotation_encoding=DEFAULT_ROTATION_ENCODING, controller_bundles=None):
    """Export ``name.glb``, ``name.index.json`` and return a complete report.

    Optional ``binding_root`` changes only the exported animation path prefix.
    Pose evaluation still uses the supplied reference bundle's Avatar, rest
    skeleton, bone lengths and muscles. Both root names are retained as metadata
    when an alias is requested; omitting it leaves root naming and pose evaluation
    unchanged. LINEAR rotation outputs use checked SNORM16 by default; pass
    ``rotation_encoding="float32"`` to keep their original float32 bytes.
    Times and translations always remain float32.

    ``controller_bundles`` (character packages, each with one
    AnimatorController) selects exactly the clips those controllers reference;
    each exported animation then carries its source identity in its extras.
    It cannot be combined with ``source_clips``.
    """
    validate_rotation_encoding(rotation_encoding)
    if controller_bundles is not None and source_clips is not None:
        raise ValueError("controller_bundles and source_clips are two selections; pass one")
    if binding_root is not None and (not isinstance(binding_root, str) or not binding_root):
        raise ValueError("binding_root must be a non-empty string")
    started = time.perf_counter()
    rig, nodes, by_name, by_hash = _reference_skeleton(reference_bundle, aux)
    glb = GLB(generator="moly-root shared motion library")
    for i, node in enumerate(nodes):
        entry = {"name": node["name"], "translation": list(unity_to_gltf_pos(node["t"])),
                 "rotation": list(unity_to_gltf_quat(node["q"])), "scale": list(node["s"])}
        children = [j for j, child in enumerate(nodes) if child["parent"] == i]
        if children:
            entry["children"] = children
        glb.g["nodes"].append(entry)
    roots = [i for i, node in enumerate(nodes) if node["parent"] < 0]
    glb.g["scenes"][0]["nodes"] = roots
    glb.g["asset"]["extras"] = {"binding": "humanoid-bone-name", "referenceSkeleton": "export-only",
                                "motionStorageRevision": MOTION_STORAGE_REVISION,
                                "rotationEncoding": rotation_encoding}
    binding_metadata = {
        "poseBindingRevision": POSE_BINDING_REVISION,
        "humanoidHosts": {bone: rig.host_node(bone) for bone in GAME_TO_HUMAN
                          if rig.host_node(bone) in by_name},
    }
    if binding_root is not None:
        if len(roots) != 1:
            raise ValueError("binding_root requires one reference skeleton root")
        root = glb.g["nodes"][roots[0]]
        binding_metadata.update({"sourceRootName": root["name"], "bindingRootName": binding_root})
        root["name"] = binding_root
    glb.g["asset"]["extras"].update(binding_metadata)

    referenced, controllers = None, None
    if controller_bundles is not None:
        controllers, referenced = controller_clip_references(controller_bundles)
        if not controllers:
            raise ValueError("controller selection needs at least one controller bundle")
    selection = {}
    samples, failures = discover_and_sample(motion_bundle, names, source_clips=source_clips,
                                            referenced=referenced, selection=selection)
    if controllers is not None:
        selection = {"mode": "controller-referenced", "controllers": controllers,
                     "referencedClips": len(referenced), **selection}
    elif source_clips is not None:
        selection = {"mode": "source-qualified", **selection}
    else:
        selection = {"mode": "first-by-name", **selection}
    index, baked = {}, 0
    storage = MotionStorage(glb)
    for clip_name in sorted(samples):
        try:
            item = dict(samples[clip_name])
            item["name"] = clip_name
            source = item["sourceMetadata"]["sourceClip"]
            _add_animation(glb, rig, item, by_name, by_hash, storage=storage,
                           rotation_encoding=rotation_encoding,
                           extras={"sourceClip": source} if controllers is not None else None)
            meta = _clip_meta(clip_name, item["frames"], item["rate"], item["sourceMetadata"])
            index.setdefault(meta["base"], {"segments": {}})["segments"][meta["suffix"] or "?"] = meta
            baked += 1
        except Exception as exc:
            failures.append({"name": clip_name, "reason": type(exc).__name__, "detail": str(exc)})
    # The intern table retains stream bytes only while building this GLB, not
    # during final JSON/BIN serialization or subsequent export jobs.
    del storage
    os.makedirs(out_dir, exist_ok=True)
    glb_path = os.path.join(out_dir, f"{name}.glb")
    index_path = os.path.join(out_dir, f"{name}.index.json")
    glb.save(glb_path)
    document = {"version": INDEX_FORMAT_VERSION, "semantics": INDEX_SEMANTICS,
                "motionStorageRevision": MOTION_STORAGE_REVISION,
                "rotationEncoding": rotation_encoding,
                "binding": {"type": "humanoid-bone-name",
                 "referenceSkeletonNodes": len(nodes), **binding_metadata},
                "selection": selection, "clips": index,
               "counts": {"discovered": len(samples) + len(failures), "exported": baked,
                           "failed": len(failures)}, "failures": failures}
    with open(index_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(document, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")
    return {"glb": glb_path, "index": index_path, "discovered": document["counts"]["discovered"],
            "exported": baked, "failed": len(failures), "failures": failures,
            "motionStorageRevision": MOTION_STORAGE_REVISION, "rotationEncoding": rotation_encoding,
            "selection": selection["mode"],
            "glbBytes": os.path.getsize(glb_path), "elapsedSeconds": time.perf_counter() - started}
