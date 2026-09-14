"""Shared serialized Animator controllers, hierarchies, curves and events.

This reads source objects through PackageStore; it does not execute a state
machine or impose furniture/house-specific trigger semantics.
"""
import zlib

from chara.mecanim.clip import curve_index_map, decode
from core.assets.packages import pairs


def _data(value):
    return value.get("data", value)


def _json_identity(value):
    """Keep serialized object identities exact in JavaScript consumers."""
    if isinstance(value, dict):
        return {key: _json_identity(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_identity(item) for item in value]
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
        return str(value)
    return value


def _identity(record, path_id):
    tree = record.tree(path_id)
    kind = record.kinds[path_id]
    return {
        "file": record.archive,
        "pathId": str(path_id),
        "class": kind,
        "name": str(tree.get("m_Name", "")),
        **({"script": record.script_of(path_id)} if kind == "MonoBehaviour" else {}),
    }


def _reference(store, record, pointer):
    if not (pointer or {}).get("m_PathID"):
        return {"pointer": _json_identity(pointer), "source": None, "status": "null"}
    target = store.follow(record, pointer)
    if target is None:
        return {"pointer": _json_identity(pointer), "source": None, "status": "unresolved"}
    return {"pointer": _json_identity(pointer), "source": _identity(*target), "status": "resolved"}


def _components(store, record, game_object):
    for row in record.tree(game_object).get("m_Component", []):
        pointer = row.get("component", row)
        target = store.follow(record, pointer)
        if target is not None:
            yield target


def _node_tree(store, record, game_object):
    """One exact Animator-relative hierarchy, including the empty root path."""
    nodes, seen = [], set()

    def walk(owner, goid, relative):
        key = (owner.archive, goid)
        if key in seen:
            raise ValueError("cycle in furniture Animator hierarchy")
        seen.add(key)
        go = owner.tree(goid)
        components = list(_components(store, owner, goid))
        transforms = [(file, pid) for file, pid in components
                      if file.kinds[pid] in ("Transform", "RectTransform")]
        if len(transforms) != 1:
            raise ValueError("furniture GameObject does not have one Transform")
        transform_file, transform_id = transforms[0]
        transform = transform_file.tree(transform_id)
        node = {
            "path": relative,
            "pathHash": zlib.crc32(relative.encode("utf-8")) & 0xffffffff,
            "gameObject": _identity(owner, goid),
            "transform": _identity(transform_file, transform_id),
            "active": go.get("m_IsActive"),
            "localPosition": transform.get("m_LocalPosition"),
            "localRotation": transform.get("m_LocalRotation"),
            "localScale": transform.get("m_LocalScale"),
            "components": [],
        }
        for component_file, component_id in components:
            kind = component_file.kinds[component_id]
            obj = component_file.objects[component_id]
            component = {"source": _identity(component_file, component_id),
                         "typeId": int(obj.type.value)}
            if kind not in ("Transform", "RectTransform", "MeshFilter"):
                tree = component_file.tree(component_id)
                component["fields"] = _json_identity(tree)
                if "m_Materials" in tree:
                    component["materials"] = [_reference(store, component_file, pointer)
                                              for pointer in tree["m_Materials"]]
            node["components"].append(component)
        nodes.append(node)
        for pointer in transform.get("m_Children", []):
            child = store.follow(transform_file, pointer)
            if child is None:
                raise ValueError("unresolved child in furniture Animator hierarchy")
            child_file, child_id = child
            child_go = store.follow(child_file, child_file.tree(child_id)["m_GameObject"])
            if child_go is None:
                raise ValueError("unresolved GameObject in furniture Animator hierarchy")
            child_name = str(child_go[0].tree(child_go[1])["m_Name"])
            child_path = relative + "/" + child_name if relative else child_name
            walk(*child_go, child_path)

    walk(record, game_object, "")
    return nodes


def _transition(entry, names):
    source = _data(entry)
    conditions = []
    for entry in source.get("m_ConditionConstantArray", []):
        condition = _data(entry)
        conditions.append({
            "mode": condition["m_ConditionMode"],
            "parameterId": condition["m_EventID"],
            "parameterName": names.get(int(condition["m_EventID"])),
            "threshold": condition["m_EventThreshold"],
            "exitTime": condition["m_ExitTime"],
        })
    return {
        "conditions": conditions,
        "destinationState": source.get("m_DestinationState", source.get("m_Destination")),
        "duration": source.get("m_TransitionDuration"),
        "offset": source.get("m_TransitionOffset"),
        "exitTime": source.get("m_ExitTime"),
        "hasExitTime": source.get("m_HasExitTime"),
        "hasFixedDuration": source.get("m_HasFixedDuration"),
        "canTransitionToSelf": source.get("m_CanTransitionToSelf"),
        "raw": _json_identity(source),
    }


def _controller(store, record, path_id):
    tree = record.tree(path_id)
    if record.kinds[path_id] != "AnimatorController":
        return {"source": _identity(record, path_id), "status": "unsupported-controller-class",
                "raw": _json_identity(tree)}
    names = {int(key): str(value) for key, value in pairs(tree.get("m_TOS", []))}
    constant = tree["m_Controller"]
    clips = [_reference(store, record, pointer) for pointer in tree["m_AnimationClips"]]
    version_types = {"AnimatorController", "ControllerConstant", "StateConstant",
                     "BlendTreeNodeConstant", "LeafInfoConstant"}
    versions = {}
    for node in record.objects[path_id].serialized_type.node.traverse():
        if node.m_Type in version_types:
            versions.setdefault(node.m_Type, set()).add(node.m_Version)
    versions = {kind: sorted(values) for kind, values in versions.items()}
    # Modern state constants use the clip-binding array index directly. Older
    # state constants have a LeafInfo remapping pass; recording an index is not
    # permission to apply the modern lookup to those older records.
    direct_motion_indices = (versions.get("StateConstant") == [3]
                             and versions.get("BlendTreeNodeConstant") == [2])
    legacy_leaf_records = any(_data(state).get("m_LeafInfoArray")
                              for machine in constant["m_StateMachineArray"]
                              for state in _data(machine)["m_StateConstantArray"])
    direct_motion_indices = direct_motion_indices and not legacy_leaf_records
    machines = []
    for machine_index, entry in enumerate(constant["m_StateMachineArray"]):
        machine = _data(entry)
        states = []
        for state_index, entry in enumerate(machine["m_StateConstantArray"]):
            state = _data(entry)
            motions = []
            for tree_index, entry in enumerate(state.get("m_BlendTreeConstantArray", [])):
                for node_index, entry in enumerate(_data(entry).get("m_NodeArray", [])):
                    node = _data(entry)
                    clip_index = int(node["m_ClipID"])
                    motions.append({
                        "treeIndex": tree_index, "nodeIndex": node_index,
                        "clipIndex": clip_index,
                        "clip": (clips[clip_index] if direct_motion_indices
                                 and 0 <= clip_index < len(clips) else None),
                        "raw": _json_identity(node),
                    })
            states.append({
                "index": state_index,
                "name": names.get(int(state["m_NameID"])),
                "nameId": state["m_NameID"],
                "fullPath": names.get(int(state["m_FullPathID"])),
                "speed": state["m_Speed"], "loop": state["m_Loop"],
                "writeDefaultValues": state["m_WriteDefaultValues"],
                "motions": motions,
                "transitions": [_transition(item, names)
                                for item in state.get("m_TransitionConstantArray", [])],
                "raw": _json_identity(state),
            })
        machines.append({
            "index": machine_index, "defaultState": machine["m_DefaultState"],
            "states": states,
            "anyStateTransitions": [_transition(item, names)
                                    for item in machine.get("m_AnyStateTransitionConstantArray", [])],
            "selectorStates": _json_identity(machine.get("m_SelectorStateConstantArray", [])),
        })
    values = _data(constant["m_Values"])["m_ValueArray"]
    return {
        "source": _identity(record, path_id), "status": "decoded",
        "serializedVersions": versions,
        "motionMapping": {
            "status": "resolved" if direct_motion_indices else "unresolved-version-or-leaf-map",
            "kind": "clip-binding-array-index" if direct_motion_indices else None,
            "clipTableOrder": "serialized m_AnimationClips order",
            "nameInterpretation": "none",
        },
        "parameters": [{"id": value["m_ID"], "name": names.get(int(value["m_ID"])),
                        "type": value["m_Type"], "index": value["m_Index"]} for value in values],
        "defaultValues": _json_identity(_data(constant["m_DefaultValues"])),
        "layers": _json_identity(constant["m_LayerArray"]),
        "stateMachines": machines, "clipTable": clips,
        "strings": {str(key): value for key, value in names.items()},
        "behaviours": _json_identity(tree.get("m_StateMachineBehaviours", [])),
        "behaviourRanges": _json_identity(tree.get("m_StateMachineBehaviourVectorDescription")),
    }


def _clip(record, path_id, animators):
    tree = record.tree(path_id)
    muscle = tree["m_MuscleClip"]
    bindings = tree["m_ClipBindingConstant"]["genericBindings"]
    index, binding_slots = curve_index_map(bindings)
    decoded = decode(tree)
    curves, unresolved = [], []
    for slot, (kind, points) in sorted(decoded.items()):
        binding = index.get(slot)
        if binding is None:
            unresolved.append({"slot": slot, "reason": "no-binding-record"})
            curves.append({"slot": slot, "kind": kind, "points": points, "binding": None})
            continue
        type_id, attribute, path_hash, component = binding
        targets = []
        for animator in animators:
            for node in animator["nodes"]:
                if node["pathHash"] != path_hash:
                    continue
                component_targets = [item["source"] for item in node["components"]
                                     if item["typeId"] == type_id]
                if type_id == 1:
                    component_targets = [node["gameObject"]]
                targets.append({"animator": animator["source"], "path": node["path"],
                                "gameObject": node["gameObject"], "components": component_targets})
        if not targets:
            unresolved.append({"slot": slot, "reason": "path-hash-not-in-animator-hierarchy"})
        curves.append({"slot": slot, "kind": kind, "points": points,
                       "binding": {"typeId": type_id, "attribute": attribute,
                                   "pathHash": path_hash, "component": component},
                       "targets": targets})
    return {
        "source": _identity(record, path_id),
        "startTime": muscle["m_StartTime"], "stopTime": muscle["m_StopTime"],
        "duration": muscle["m_StopTime"] - muscle["m_StartTime"],
        "loopTime": muscle["m_LoopTime"], "sampleRate": tree["m_SampleRate"],
        "events": _json_identity(tree.get("m_Events", [])),
        "curves": curves,
        "bindings": _json_identity(tree["m_ClipBindingConstant"]),
        "floatCurves": _json_identity(tree.get("m_FloatCurves", [])),
        "pptrCurves": _json_identity(tree.get("m_PPtrCurves", [])),
        "transformCurves": {key: _json_identity(tree.get(key, [])) for key in
                            ("m_RotationCurves", "m_CompressedRotationCurves", "m_EulerCurves",
                             "m_PositionCurves", "m_ScaleCurves")},
        "accounting": {"bindingSlots": binding_slots, "decodedSlots": len(decoded),
                       "unresolved": unresolved},
    }
