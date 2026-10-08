"""Weather animation assets and their exact source scopes.

An AnimationClip being present in a model is not a playback instruction. The
model scope below comes only from the AssetBundle container's shared asset key;
Animator scopes come only from the component's GameObject. Both preserve source
bindings, while only an Animator/controller edge establishes automatic playback.
"""
import zlib

from chara.mecanim.clip import curve_index_map, decode
from core.animator import _controller
from core.assets.identity import json_pointers, object_identity, source_reference
from .effects import hierarchy


def _scope(record, root_id, source, kind, asset=None):
    nodes, _ = hierarchy(root_id, record.kinds, record.trees)
    transforms = {
        tree.get("m_GameObject", {}).get("m_PathID"): path_id
        for path_id, component in record.kinds.items()
        if component in ("Transform", "RectTransform")
        for tree in [record.tree(path_id)]
    }
    result = {"kind": kind, "source": source,
              "gameObject": object_identity(record, root_id), "nodes": []}
    if asset is not None:
        result["asset"] = asset
    for node in nodes:
        goid = int(node["gameObjectPathId"])
        result["nodes"].append({**node,
            "pathHash": zlib.crc32(node["path"].encode("utf-8")) & 0xffffffff,
            "source": object_identity(record, goid),
            "transform": object_identity(record, transforms[goid])})
    return result


def _clip(record, path_id, scopes):
    tree = record.tree(path_id)
    muscle = tree["m_MuscleClip"]
    index, binding_slots = curve_index_map(tree["m_ClipBindingConstant"]["genericBindings"])
    curves, unresolved = [], []
    for slot, (kind, points) in sorted(decode(tree).items()):
        binding = index.get(slot)
        targets = []
        if binding is not None:
            type_id, attribute, path_hash, component = binding
            # Transform and GameObject bindings need no speculative component
            # name/leaf fallback. Other class bindings remain explicit gaps.
            if type_id in (1, 4):
                for scope in scopes:
                    for node in scope["nodes"]:
                        if node["pathHash"] == path_hash:
                            targets.append({"scope": scope["source"], "scopeKind": scope["kind"],
                                "path": node["path"], "gameObject": node["source"],
                                "target": node["source"] if type_id == 1 else node["transform"]})
            binding = {"typeId": type_id, "attribute": attribute,
                       "pathHash": path_hash, "component": component}
        if not targets:
            unresolved.append({"slot": slot, "reason": "no-binding-record" if binding is None
                               else "binding-not-in-declared-scope"})
        curves.append({"slot": slot, "kind": kind, "points": points,
                       "binding": binding, "targets": targets})
    return {"source": object_identity(record, path_id), "name": tree.get("m_Name", ""),
            "startTime": muscle["m_StartTime"], "stopTime": muscle["m_StopTime"],
            "duration": muscle["m_StopTime"] - muscle["m_StartTime"],
            "loopTime": muscle["m_LoopTime"], "sampleRate": tree["m_SampleRate"],
            # An empty reference-pose array selects the native delta-start
            # fallback; it is different from a missing field or explicit pose.
            # Keep native field names so future consumers can audit every input
            # to reference subtraction, loop blending and root motion.
            "muscleClip": {key: json_pointers(muscle.get(key)) for key in (
                "m_ValueArrayDelta", "m_ValueArrayReferencePose", "m_DeltaPose",
                "m_StartX", "m_StopX", "m_LeftFootStartX", "m_RightFootStartX",
                "m_AverageSpeed", "m_AverageAngularSpeed", "m_OrientationOffsetY",
                "m_Level", "m_CycleOffset", "m_Mirror", "m_LoopTime",
                "m_LoopBlend", "m_LoopBlendOrientation", "m_LoopBlendPositionY",
                "m_LoopBlendPositionXZ", "m_StartAtOrigin", "m_KeepOriginalOrientation",
                "m_KeepOriginalPositionY", "m_KeepOriginalPositionXZ", "m_HeightFromFeet")},
            "legacy": tree.get("m_Legacy"), "wrapMode": tree.get("m_WrapMode"),
            "events": json_pointers(tree.get("m_Events", [])),
            "bindings": json_pointers(tree["m_ClipBindingConstant"]), "curves": curves,
            "floatCurves": json_pointers(tree.get("m_FloatCurves", [])),
            "pptrCurves": json_pointers(tree.get("m_PPtrCurves", [])),
            "transformCurves": {key: json_pointers(tree.get(key, [])) for key in
                ("m_RotationCurves", "m_CompressedRotationCurves", "m_EulerCurves",
                 "m_PositionCurves", "m_ScaleCurves")},
            "scopes": [{key: scope[key] for key in ("kind", "source", "gameObject")}
                       for scope in scopes],
            "accounting": {"bindingSlots": binding_slots, "decodedSlots": len(curves),
                           "unresolved": unresolved}}


def decode_package(store, package, variant):
    """Preserve source clocks, controllers and curves without inventing playback."""
    document = {"bundle": package.name, "variant": variant, "animators": [],
                "controllers": [], "directors": [], "modelScopes": [], "clips": [],
                "unsupported": []}
    animators, controllers, clips, model_scopes = {}, {}, {}, {}
    for record in package.files:
        for path_id, kind in record.kinds.items():
            key = (record.archive, path_id)
            if kind == "AnimationClip":
                clips[key] = (record, path_id)
            elif kind == "Animator":
                tree = record.tree(path_id)
                owner = store.follow(record, tree.get("m_GameObject"))
                row = {"source": object_identity(record, path_id), "fields": json_pointers(tree),
                       "controller": source_reference(record, path_id, tree.get("m_Controller"), store.follow)}
                document["animators"].append(row)
                if owner is None:
                    document["unsupported"].append({"source": row["source"], "reason": "Animator owner unresolved"})
                    continue
                row["scope"] = _scope(*owner, row["source"], "animator")
                target = store.follow(record, tree.get("m_Controller"))
                if target is None:
                    continue
                controller_file, controller_id = target
                controllers[(controller_file.archive, controller_id)] = target
                for pointer in controller_file.tree(controller_id).get("m_AnimationClips", []):
                    clip = store.follow(controller_file, pointer)
                    if clip is not None:
                        ckey = (clip[0].archive, clip[1])
                        clips[ckey] = clip
                        animators.setdefault(ckey, []).append(row["scope"])
            elif kind == "AnimatorController":
                controllers[key] = (record, path_id)
            elif kind == "PlayableDirector":
                tree = record.tree(path_id)
                document["directors"].append({"source": object_identity(record, path_id),
                    "fields": json_pointers(tree),
                    "gameObject": source_reference(record, path_id, tree.get("m_GameObject"), store.follow),
                    "playableAsset": source_reference(record, path_id, tree.get("m_PlayableAsset"), store.follow),
                    "sceneBindings": [{"key": source_reference(record, path_id, row.get("key"), store.follow),
                                       "value": source_reference(record, path_id, row.get("value"), store.follow)}
                                      for row in tree.get("m_SceneBindings", [])]})
    # These are source container identities, not a comparison of clip/model names.
    roots = {}
    for asset, record, path_id in package.contents:
        if record.kinds.get(path_id) == "GameObject" and not asset.endswith(".prefab"):
            roots.setdefault((record.archive, asset), []).append((record, path_id))
    for asset, record, path_id in package.contents:
        if record.kinds.get(path_id) != "AnimationClip":
            continue
        candidates = roots.get((record.archive, asset), [])
        if len(candidates) != 1:
            if candidates:
                document["unsupported"].append({"source": object_identity(record, path_id),
                    "reason": "model animation container has ambiguous GameObject roots"})
            continue
        root = candidates[0]
        key = (root[0].archive, root[1])
        if key not in model_scopes:
            model_scopes[key] = _scope(*root, object_identity(*root), "modelAsset", asset)
        clips.setdefault((record.archive, path_id), (record, path_id))
        animators.setdefault((record.archive, path_id), []).append(model_scopes[key])
    for record, path_id in controllers.values():
        document["controllers"].append({"source": object_identity(record, path_id),
            "fields": json_pointers(record.tree(path_id)), "decoded": _controller(store, record, path_id)})
    document["modelScopes"] = list(model_scopes.values())
    for key, (record, path_id) in sorted(clips.items()):
        clip = _clip(record, path_id, animators.get(key, []))
        document["clips"].append(clip)
        for gap in clip["accounting"]["unresolved"]:
            document["unsupported"].append({"source": clip["source"], **gap})
    return document
