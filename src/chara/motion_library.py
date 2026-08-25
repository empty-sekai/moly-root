"""Export the shared, character-independent humanoid motion library."""
import json
import math
import os
import re
import struct
import time

from core.gltf import GLB, unity_to_gltf_pos, unity_to_gltf_quat
from .mecanim import Rig, rig_doc, pose_bone, pose_root, sample_frames
from .mecanim.traits import GAME_TO_HUMAN
from .characters import CharacterAssets, read_scene

_SUFFIX = re.compile(r"_(Start|Loop|End|OneShot|S|L|E|O)$", re.IGNORECASE)


def _clip_meta(name, frames, rate):
    match = _SUFFIX.search(name)
    suffix = (match.group(1) if match else "").upper()
    suffix = {"START": "S", "LOOP": "L", "END": "E", "ONESHOT": "O"}.get(suffix, suffix)
    base = name[:match.start()] if match else name
    return {"name": name, "base": base, "suffix": suffix or None,
            "frames": len(frames), "duration": frames[-1][0] if frames else 0.0,
            "sampleRate": rate, "loop": suffix == "L"}


def discover_and_sample(motion_bundle, names=None):
    """Decode every requested (or every present) AnimationClip.

    Returns successful samples and a structured, per-clip failure list.
    """
    import UnityPy
    env = UnityPy.load(motion_bundle)
    wanted = set(names) if names else None
    samples, failures, seen = {}, [], set()
    for obj in env.objects:
        if obj.type.name != "AnimationClip":
            continue
        name = "<unknown>"
        try:
            tt = obj.read_typetree()
            name = str(tt.get("m_Name", ""))
            if not name or (wanted is not None and name not in wanted) or name in seen:
                continue
            seen.add(name)
            rate, frames = sample_frames(tt)
            if not frames:
                raise ValueError("empty sampled frame set")
            samples[name] = {"rate": rate, "frames": frames}
        except Exception as exc:
            failures.append({"name": name, "reason": type(exc).__name__, "detail": str(exc)})
    if wanted is not None:
        for missing in sorted(wanted - seen):
            failures.append({"name": missing, "reason": "missing_clip",
                             "detail": "not present in motion bundle"})
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


def _add_animation(glb, rig, sampled, node_by_name, node_by_hash):
    frames = sampled["frames"]
    times = [f[0] for f in frames]
    bones = sorted(b for b in GAME_TO_HUMAN if b in node_by_name)
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
    time_accessor = glb.acc(b"".join(struct.pack("<f", t) for t in times), 5126,
                            "SCALAR", len(times), minmax=([times[0]], [times[-1]]))
    samplers, channels = [], []

    def channel(node, path, values, width):
        accessor = glb.acc(b"".join(struct.pack(f"<{width}f", *value) for value in values),
                           5126, "VEC4" if width == 4 else "VEC3", len(values))
        samplers.append({"input": time_accessor, "output": accessor, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": node, "path": path}})

    for bone in bones:
        channel(node_by_name[bone], "rotation", [unity_to_gltf_quat(q) for q in rotations[bone]], 4)
    for node in sorted(twist):
        if len(twist[node]) != len(times):
            raise ValueError("auxiliary transform track has inconsistent frame count")
        channel(node, "rotation", [unity_to_gltf_quat(q) for q in twist[node]], 4)
    channel(node_by_name["Hips"], "translation", [unity_to_gltf_pos(v) for v in hips_t], 3)
    for bone in sorted(tdof_t):
        if len(tdof_t[bone]) != len(times):
            raise ValueError("translation-DoF track has inconsistent frame count")
        channel(node_by_name[bone], "translation",
                [unity_to_gltf_pos(v) for v in tdof_t[bone]], 3)
    glb.g.setdefault("animations", []).append({"name": sampled["name"],
        "samplers": samplers, "channels": channels})


def export_motion_library(reference_bundle, motion_bundle, out_dir, name="motion-library",
                          aux=(), names=None):
    """Export ``name.glb``, ``name.index.json`` and return a complete report."""
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
    glb.g["asset"]["extras"] = {"binding": "humanoid-bone-name", "referenceSkeleton": "export-only"}

    samples, failures = discover_and_sample(motion_bundle, names)
    index, baked = {}, 0
    for clip_name in sorted(samples):
        try:
            item = dict(samples[clip_name])
            item["name"] = clip_name
            _add_animation(glb, rig, item, by_name, by_hash)
            meta = _clip_meta(clip_name, item["frames"], item["rate"])
            index.setdefault(meta["base"], {"segments": {}})["segments"][meta["suffix"] or "?"] = meta
            baked += 1
        except Exception as exc:
            failures.append({"name": clip_name, "reason": type(exc).__name__, "detail": str(exc)})
    os.makedirs(out_dir, exist_ok=True)
    glb_path = os.path.join(out_dir, f"{name}.glb")
    index_path = os.path.join(out_dir, f"{name}.index.json")
    glb.save(glb_path)
    document = {"version": 1, "binding": {"type": "humanoid-bone-name",
                 "referenceSkeletonNodes": len(nodes)}, "clips": index,
               "counts": {"discovered": len(samples) + len(failures), "exported": baked,
                           "failed": len(failures)}, "failures": failures}
    with open(index_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(document, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")
    return {"glb": glb_path, "index": index_path, "discovered": document["counts"]["discovered"],
            "exported": baked, "failed": len(failures), "failures": failures,
            "glbBytes": os.path.getsize(glb_path), "elapsedSeconds": time.perf_counter() - started}
