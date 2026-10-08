"""Animator controllers of UI layout nodes, read with the shared controller readers.

An Animator on a layout node names its controller by ``m_Controller``.  The
controller and the clips it lists are read with :mod:`core.animator` -- the
readers the furniture gimmick metadata uses -- from the serialized file set the
layout was read from, and attached to the Animator's component record, so a
consumer finds the states and curves next to the node that plays them.

Player data carries no embedded typetrees; the engine classes read here
(GameObject, the transforms, Animator, AnimatorController, AnimationClip) are
read with the engine's own class layouts.  A clip's bindings name their targets
by the CRC32 of the path relative to the Animator's GameObject, so the
Animator's own hierarchy is walked to resolve them.  MonoBehaviour fields are
not read for that walk: a binding names a component by its engine type id.
"""
from __future__ import annotations

import zlib

from core.animator import _clip, _controller, _identity, _reference, _json_identity
from ui.player_source import external_file_name


class FileRecord:
    """One loaded serialized file, in the shape :mod:`core.animator` reads."""

    def __init__(self, assets_file):
        self.source = assets_file
        self.archive = str(assets_file.name).rsplit("/", 1)[-1]
        self.objects = dict(assets_file.objects)
        self.kinds = {path_id: obj.type.name for path_id, obj in self.objects.items()}
        self.externals = [str(getattr(item, "path", "")) for item in assets_file.externals]
        self._trees = {}

    def tree(self, path_id):
        if path_id not in self._trees:
            self._trees[path_id] = self.objects[path_id].read_typetree()
        return self._trees[path_id]

    def script_of(self, path_id):
        return None


class FileStore:
    """Pointer resolution over serialized files looked up by name."""

    def __init__(self, lookup):
        self._lookup = lookup
        self._records = {}

    def record(self, assets_file):
        key = id(assets_file)
        if key not in self._records:
            self._records[key] = FileRecord(assets_file)
        return self._records[key]

    def follow(self, record, pointer):
        pointer = pointer or {}
        path_id = pointer.get("m_PathID", 0)
        file_id = pointer.get("m_FileID", 0)
        if not path_id:
            return None
        if file_id:
            if not 1 <= file_id <= len(record.externals):
                return None
            target = self._lookup(external_file_name(record.externals[file_id - 1]))
            if target is None:
                return None
            record = self.record(target)
        return (record, path_id) if path_id in record.kinds else None


def _component_identity(record, path_id):
    """A component's identity without reading a MonoBehaviour's fields."""
    kind = record.kinds[path_id]
    if kind == "MonoBehaviour":
        return {"file": record.archive, "pathId": str(path_id), "class": kind}
    return _identity(record, path_id)


def _hierarchy(store, record, game_object):
    """The Animator-relative node list the clip reader resolves bindings against."""
    nodes, seen = [], set()

    def walk(owner, goid, relative):
        key = (owner.archive, goid)
        if key in seen:
            raise ValueError("cycle in the Animator hierarchy")
        seen.add(key)
        components, transform = [], None
        for row in owner.tree(goid).get("m_Component", []):
            target = store.follow(owner, row.get("component", row))
            if target is None:
                continue
            file, pid = target
            components.append({"source": _component_identity(file, pid),
                               "typeId": int(file.objects[pid].type.value)})
            if file.kinds[pid] in ("Transform", "RectTransform"):
                transform = target
        if transform is None:
            raise ValueError("Animator hierarchy node without a transform")
        nodes.append({"path": relative,
                      "pathHash": zlib.crc32(relative.encode("utf-8")) & 0xffffffff,
                      "gameObject": _identity(owner, goid), "components": components})
        transform_file, transform_id = transform
        for pointer in transform_file.tree(transform_id).get("m_Children", []):
            child = store.follow(transform_file, pointer)
            if child is None:
                raise ValueError("unresolved child in the Animator hierarchy")
            child_file, child_id = child
            child_go = store.follow(child_file, child_file.tree(child_id)["m_GameObject"])
            if child_go is None:
                raise ValueError("unresolved GameObject in the Animator hierarchy")
            name = str(child_go[0].tree(child_go[1])["m_Name"])
            walk(*child_go, f"{relative}/{name}" if relative else name)

    walk(record, game_object, "")
    return nodes


def animator_controller(obj, lookup):
    """The controller record of the Animator component *obj*, or ``None``.

    *lookup* maps a serialized file name to the loaded file (the container's
    files for a single player data file, the environment's files for loose
    player data).  Returns ``None`` when the Animator names no controller.
    """
    store = FileStore(lookup)
    record = store.record(obj.assets_file)
    tree = record.tree(obj.path_id)
    pointer = tree.get("m_Controller") or {}
    if not pointer.get("m_PathID"):
        return None
    reference = _reference(store, record, pointer)
    target = store.follow(record, pointer)
    if target is None:
        return {"reference": reference, "status": "unresolved"}
    owner = store.follow(record, tree["m_GameObject"])
    if owner is None:
        raise ValueError("Animator has no resolvable owner GameObject")
    animator = {"source": _identity(record, obj.path_id), "gameObject": _identity(*owner),
                "nodes": _hierarchy(store, *owner)}
    controller = _controller(store, *target)
    clips = []
    controller_record = target[0]
    for clip_pointer in controller_record.tree(target[1]).get("m_AnimationClips") or []:
        clip = store.follow(controller_record, clip_pointer)
        if clip is None:
            clips.append({"reference": _json_identity(clip_pointer), "status": "unresolved"})
            continue
        clips.append(_clip(clip[0], clip[1], [animator]))
    return {"reference": reference, "status": "decoded", "controller": controller,
            "clips": clips, "hierarchy": [{"path": node["path"], "pathHash": node["pathHash"]}
                                          for node in animator["nodes"]]}
