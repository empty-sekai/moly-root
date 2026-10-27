"""Source collision surfaces used by the site's editor raycast.

The editor selects colliders by the RaycastTargetGroup tag. Its numeric value
belongs to the client build's TagManager, so the corresponding APK is an
explicit input. Model names and the visible or navigation meshes do not select
this surface. The small runtime sidecar keeps Unity's authored coordinates,
triangle winding and root-to-leaf transform chain without a glTF conversion.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from UnityPy.helpers.MeshHelper import MeshHandler

from core.assets.packages import PackageStore, require_paths
from core.unity import configure_fallback_unity_version
from sites.geometry import Graph
from ui.player_source import ApkPlayerData

TAG = "RaycastTargetGroup"
CUSTOM_TAG_BASE = 20000
SCENE_PREFIX = "mysekai__site__field__"
COORDINATE_CONTRACT = "unity-lh-y-up-authored"


def _tag_index(tree):
    tags = tree.get("tags")
    if not isinstance(tags, list):
        raise ValueError("TagManager has no tags array")
    matches = [i for i, tag in enumerate(tags) if tag == TAG]
    if len(matches) != 1:
        raise ValueError(f"expected one {TAG} tag, found {len(matches)}")
    return CUSTOM_TAG_BASE + matches[0]


def _tag_manager(apks):
    source = ApkPlayerData(apks)
    player = source.environment()
    player.add(["globalgamemanagers"], follow=False)
    managers = [obj for obj in player.env.objects if obj.type.name == "TagManager"]
    if len(managers) != 1:
        raise ValueError(f"expected one TagManager, found {len(managers)}")
    manager = managers[0]
    return _tag_index(manager.read_typetree()), {
        "file": "globalgamemanagers",
        "fileSha256": source.sha256("globalgamemanagers"),
        "objectId": str(manager.path_id),
    }


def _local_target(record, pointer, kind):
    pointer = pointer or {}
    if pointer.get("m_FileID", 0):
        raise ValueError(f"edit-pick v1 requires {kind} in the collider's file")
    path_id = pointer.get("m_PathID", 0)
    if not path_id or record.kinds.get(path_id) != kind:
        raise ValueError(f"unresolved {kind} pointer: {path_id}")
    return path_id


def _vector(tree, field, axes):
    try:
        result = [float(tree[field][axis]) for axis in axes]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"missing or invalid {field}") from exc
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"non-finite {field}")
    return result


def _flag(tree, field):
    value = tree.get(field)
    if value not in (0, 1) or not isinstance(value, (int, bool)):
        raise ValueError(f"missing or invalid {field}")
    return bool(value)


def _chain(record, graph, game_object):
    transform_id = graph.transform_of.get(game_object)
    if transform_id is None:
        raise ValueError(f"GameObject {game_object} has no Transform")
    chain, seen = [], set()
    while transform_id:
        if transform_id in seen:
            raise ValueError("transform parent chain loops")
        seen.add(transform_id)
        if record.kinds.get(transform_id) != "Transform":
            raise ValueError(f"unresolved Transform: {transform_id}")
        transform = record.tree(transform_id)
        owner = _local_target(record, transform.get("m_GameObject"), "GameObject")
        chain.append({
            "position": _vector(transform, "m_LocalPosition", "xyz"),
            "rotation": _vector(transform, "m_LocalRotation", "xyzw"),
            "scale": _vector(transform, "m_LocalScale", "xyz"),
            "active": _flag(record.tree(owner), "m_IsActive"),
        })
        parent = transform.get("m_Father")
        if not isinstance(parent, dict) or "m_PathID" not in parent:
            raise ValueError("Transform has no authored parent pointer")
        if parent.get("m_FileID", 0):
            raise ValueError("edit-pick v1 requires parent transforms in the collider's file")
        transform_id = parent.get("m_PathID", 0)
    return list(reversed(chain))


def _mesh_geometry(mesh):
    handler = MeshHandler(mesh)
    handler.process()
    positions = [[float(value) for value in vertex[:3]] for vertex in handler.m_Vertices]
    if (not positions or len(positions) != handler.m_VertexCount
            or any(len(vertex) != 3 or not all(math.isfinite(v) for v in vertex)
                   for vertex in positions)):
        raise ValueError("collider mesh has missing or invalid vertices")
    indices = handler.m_IndexBuffer
    if indices is None:
        raise ValueError("collider mesh has no indices")
    width = 2 if handler.m_Use16BitIndices else 4
    triangles = []
    for submesh in mesh.m_SubMeshes or []:
        if submesh.topology != 0:
            raise ValueError("edit-pick v1 requires authored triangle topology")
        start, count = submesh.firstByte, submesh.indexCount
        if start < 0 or start % width or count < 0 or count % 3:
            raise ValueError("invalid triangle submesh range")
        start //= width
        if start + count > len(indices):
            raise ValueError("triangle submesh exceeds the index buffer")
        # Unity's indices are relative to baseVertex. MeshHandler.get_triangles
        # does not apply that offset, so read the authored triangle ranges here.
        base = getattr(submesh, "baseVertex", None) or 0
        for offset in range(start, start + count, 3):
            triangle = [int(index) + base for index in indices[offset:offset + 3]]
            if any(index < 0 or index >= len(positions) for index in triangle):
                raise ValueError("triangle index outside the vertex buffer")
            triangles.append(triangle)
    if not triangles:
        raise ValueError("collider mesh has no triangles")
    return positions, triangles


def _colliders(package, tag_index):
    colliders = []
    for record in sorted(package.files, key=lambda item: item.archive):
        graph = None
        for path_id, kind in sorted(record.kinds.items()):
            if not (kind.endswith("Collider") or kind == "CharacterController"):
                continue
            collider = record.tree(path_id)
            owner = _local_target(record, collider.get("m_GameObject"), "GameObject")
            game_object = record.tree(owner)
            tag = game_object.get("m_Tag")
            if isinstance(tag, bool) or not isinstance(tag, int) or tag < 0:
                raise ValueError("missing or invalid GameObject tag")
            if tag != tag_index:
                continue
            if kind != "MeshCollider":
                raise ValueError(f"unsupported {TAG} component: {kind}")
            mesh_id = _local_target(record, collider.get("m_Mesh"), "Mesh")
            if graph is None:
                graph = Graph(record)
            positions, triangles = _mesh_geometry(record.objects[mesh_id].read())
            layer = game_object.get("m_Layer")
            if isinstance(layer, bool) or not isinstance(layer, int) or not 0 <= layer < 32:
                raise ValueError("missing or invalid GameObject layer")
            colliders.append({
                "tag": TAG, "tagIndex": tag_index,
                "enabled": _flag(collider, "m_Enabled"),
                "isTrigger": _flag(collider, "m_IsTrigger"),
                "convex": _flag(collider, "m_Convex"), "layer": layer,
                "chain": _chain(record, graph, owner),
                "positions": positions, "triangles": triangles,
                "source": {"file": record.archive, "componentId": str(path_id),
                           "gameObjectId": str(owner), "meshId": str(mesh_id)},
            })
    if not colliders:
        raise ValueError(f"scene has no {TAG} colliders")
    return colliders


def extract_site_edit_pick(bundle, apks, out):
    """Export one scene sidecar from its source bundle and matching client APKs.

    All pointers used by schema v1 must stay in the collider's serialized file;
    otherwise its single-file provenance would be incomplete, so fail rather
    than publish a partial surface. Other scene dependencies are not rendered
    or loaded. The explicit output file is replaced atomically after success.
    """
    configure_fallback_unity_version()
    bundle, apks, out = Path(bundle), [Path(apk) for apk in apks], Path(out)
    require_paths([bundle])
    name = bundle.name
    site = name.removeprefix(SCENE_PREFIX)
    if not name.startswith(SCENE_PREFIX) or not site:
        raise ValueError(f"bundle must be named {SCENE_PREFIX}<scene>")
    if out.resolve() in {path.resolve() for path in [bundle, *apks]}:
        raise ValueError("output must not replace an input")
    tag_index, tag_source = _tag_manager(apks)
    store = PackageStore([bundle])
    package = store.package(name)
    if package is None:
        raise ValueError(f"scene package not loaded: {name}")
    document = {
        "version": 1, "coordinateContract": COORDINATE_CONTRACT,
        "site": site, "package": name,
        "bundleSha256": store.source_receipt(name)["sha256"],
        "colliders": _colliders(package, tag_index), "tagManagerSource": tag_source,
    }
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=out.parent, prefix=f".{out.name}.",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, out)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"output": str(out), "site": site, "colliders": len(document["colliders"]),
            "vertices": sum(len(row["positions"]) for row in document["colliders"]),
            "triangles": sum(len(row["triangles"]) for row in document["colliders"])}
