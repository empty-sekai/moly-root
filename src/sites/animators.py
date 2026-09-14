"""Scene-scoped Animator clip bindings using the shared source readers."""
import zlib

from core.animator import _controller, _reference
from core.assets.identity import identity, json_pointers
from perf.animations import decode_clip, _add_animation


def room_door_anchors(record, graph, indices):
    """Keep front-wall scope and authored traversal order for door locators.

    RoomController chooses a front wall instance before querying its children.
    This is per-wall metadata, never a package-wide same-name lookup.
    """
    result = []
    for front in indices:
        if graph.name(front) != "wall_front":
            continue
        pending, points = [front], []
        while pending:
            current = pending.pop()
            tree = record.tree(current)
            name = graph.name(current)
            if name.casefold() in ("loc_inside", "loc_door"):
                points.append({"name": name, "asset": identity(record, current),
                               "node": indices[current]})
            children = [p["m_PathID"] for p in tree["m_Children"]]
            pending.extend(reversed(children))
        result.append({"wall": identity(record, front),
                       "node": indices[front], "points": points})
    return result


class _Scope:
    def __init__(self, graph, root, indices):
        self.foreign_hits = {}
        self.paths = {}
        self.nodes = {}
        for owner, path in graph.node_paths(root).items():
            digest = zlib.crc32(path.encode("utf-8")) & 0xffffffff
            node = indices[graph.transform_of[owner]]
            if digest in self.nodes and self.nodes[digest] != node:
                raise ValueError("ambiguous source path hash in Animator scope")
            self.paths[digest], self.nodes[digest] = path, node

    def resolve(self, digest):
        return self.paths.get(digest)

    def node_for_hash(self, digest):
        return self.nodes.get(digest)


def embed(store, record, graph, indices, scene, glb):
    """Follow actual controllers; never choose a clip by its display name."""
    result, controllers = [], []
    for transform in indices:
        for kind, component in graph.components(transform):
            if kind != "Animator":
                continue
            tree = record.tree(component)
            controller = store.follow(record, tree["m_Controller"])
            row = {
                "asset": identity(record, component),
                "gameObject": identity(record, graph.owner[transform]),
                "node": indices[transform], "scene": scene,
                "controller": _reference(store, record, tree["m_Controller"]),
                "fields": json_pointers(tree), "clips": [],
            }
            result.append(row)
            if controller is None:
                continue
            row["controllerData"] = _controller(store, *controller)
            controllers.append(controller)
            controller_file, controller_id = controller
            if controller_file.kinds[controller_id] != "AnimatorController":
                continue
            scope = _Scope(graph, transform, indices)
            written = {}
            for slot, pointer in enumerate(controller_file.tree(controller_id)["m_AnimationClips"]):
                target = store.follow(controller_file, pointer)
                clip_row = {"slot": slot, "source": _reference(store, controller_file, pointer)}
                row["clips"].append(clip_row)
                if target is None:
                    continue
                source, clip_id = target
                key = (source.archive, clip_id)
                if key in written:
                    clip_row.update(written[key])
                    continue
                if source.kinds[clip_id] != "AnimationClip":
                    raise ValueError("Animator clip slot is not an AnimationClip")
                clip = source.tree(clip_id)
                curves, channels, anomalies = decode_clip(clip, scope)
                metadata = {
                    "asset": identity(source, clip_id),
                    "startTime": clip["m_MuscleClip"]["m_StartTime"],
                    "stopTime": clip["m_MuscleClip"]["m_StopTime"],
                    "events": json_pointers(clip["m_Events"]),
                    "animation": None, "anomalies": anomalies,
                }
                # An authored idle with duration but no channels remains a
                # metadata clip. glTF animation.channels may not be empty.
                if channels:
                    metadata["animation"] = len(glb.g.get("animations", []))
                    animation = _add_animation(glb, clip["m_Name"], curves, channels, anomalies)
                    animation["extras"].update(sourceClip=identity(source, clip_id),
                        sourceAnimator=identity(record, component), sourceScene=scene,
                        sourceEvents=metadata["events"])
                clip_row.update(metadata)
                written[key] = metadata
    return result, controllers
