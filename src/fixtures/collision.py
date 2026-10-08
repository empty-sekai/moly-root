"""Fixture collision and navigation component export.

The game builds fixture navigation from ``PhysicsCollider`` sources.  Visible
meshes and logical grid occupancy are not substitutes for that source set.  This
module records the serialized collider graph next to the fixture GLB and carries
the actual mesh used by a MeshCollider, including meshes that no renderer draws.

Fixture geometry is authored in Unity's left-handed local frame while the GLB
consumer uses the canonical right-handed frame.  Spatial conversion is therefore
performed exactly once here: X is reflected, triangle winding is reversed, and
quaternions use the same conjugation as the visual exporter.  UVs are unrelated
to this spatial conversion and remain tagged as Unity bottom-left coordinates.
"""

from __future__ import annotations

import math

from UnityPy.helpers.MeshHelper import MeshHandler

from core.gltf import COORDINATE_SPACE, UV_ORIGIN, reflect_position


SCHEMA_VERSION = 1
COORDINATE_CONTRACT = COORDINATE_SPACE
UV_CONVENTION = UV_ORIGIN
MESH_KINDS = {"MeshCollider", "BoxCollider", "SphereCollider", "CapsuleCollider"}
MODIFIER_SCRIPT = "NavMeshModifier"
VIEW_SCRIPT = "FixtureView"
SOURCE_RULES = [
    "CN 6.0.0 NavMeshField.Initialize: CollectObjects.Volume, PhysicsColliders, MysekaiNavMeshBuildTarget",
    "FixtureController.Initialize calls AttachComponents only when it creates a missing FixtureView; serialized views retain their authored collider graph",
    "JP 6.7.0 NavMeshSurface.CollectSources: modifier affectedAgents/ignoreFromBuild/area; ignore sources with same-object agent/obstacle",
]


def _vec(tree, key, width=3):
    value = tree.get(key)
    if not isinstance(value, dict):
        return None
    channels = "xyz" if width == 3 else "xyzw"
    if any(channel not in value for channel in channels):
        return None
    return [float(value[channel]) for channel in channels]


def _bool(tree, key):
    value = tree.get(key)
    return bool(value) if value is not None else None


def _point(tree, key):
    point = _vec(tree, key)
    return list(reflect_position(point)) if point is not None else None


def _pointer(pointer):
    if not isinstance(pointer, dict):
        return None
    path_id = pointer.get("m_PathID", 0)
    if not path_id:
        return None
    return {"fileId": int(pointer.get("m_FileID", 0)), "pathId": str(path_id)}


def _component_ids(record, goid):
    tree = record.tree(goid)
    for entry in tree.get("m_Component") or []:
        pointer = entry.get("component", entry) if isinstance(entry, dict) else entry
        path_id = (pointer or {}).get("m_PathID", 0)
        if path_id in record.kinds:
            yield path_id, record.kinds[path_id]


def _script(record, path_id):
    return record.script_of(path_id) if record.kinds.get(path_id) == "MonoBehaviour" else ""


def _active_hierarchy(graph, gameobjects, transform):
    current = transform
    seen = set()
    active = True
    while current and current not in seen:
        seen.add(current)
        if current not in graph.owner:
            return None
        goid = graph.owner[current]
        value = _bool(gameobjects[goid], "m_IsActive")
        if value is False:
            return False
        if value is None:
            active = None
        current = graph.father.get(current, 0)
    return None if current else active


def _ordered_subtree(record, graph, root):
    """Unity child order, not the path-ID ordering in the archival Graph."""
    result, pending, seen = [], [root], set()
    while pending:
        current = pending.pop()
        if current in seen:
            raise ValueError("cycle in fixture transform children")
        seen.add(current)
        result.append(current)
        children = [entry.get("m_PathID") for entry in record.tree(current).get("m_Children", [])]
        expected = set(graph.children.get(current, []))
        if len(children) != len(set(children)) or set(children) != expected:
            raise ValueError("fixture transform child list disagrees with parent links")
        pending.extend(reversed(children))
    return result


class CollisionExport:
    """Collect one package's collision graph while the GLB node tree is walked."""

    def __init__(self, store, mesh_handler=MeshHandler):
        self.store = store
        self.mesh_handler = mesh_handler
        self._records = {}
        self._geometries = {}
        self._gaps = []
        self._prepared = set()
        self._nav_layer = set()
        self._failed_geometries = set()

    def prepare(self, record, graph, fixture_view_goids=()):
        key = id(record)
        if key in self._prepared:
            return
        self._prepared.add(key)
        gameobjects = {goid: graph.game_object(tpid) for tpid, goid in graph.owner.items()}
        view_goids = set(fixture_view_goids)
        for goid in gameobjects:
            if any(_script(record, cid) == VIEW_SCRIPT for cid, _ in _component_ids(record, goid)):
                view_goids.add(goid)
        for view_goid in view_goids:
            if view_goid not in graph.transform_of:
                continue
            for transform in _ordered_subtree(record, graph, graph.transform_of[view_goid]):
                self._nav_layer.add((id(record), transform))
        for transform, goid in graph.owner.items():
            self._records[(id(record), transform)] = self._node_record(
                record, graph, gameobjects, transform, goid
            )

    def _node_record(self, record, graph, gameobjects, transform, goid):
        components = list(_component_ids(record, goid))
        colliders = []
        geometry_ids = []
        for component_id, kind in components:
            if kind not in MESH_KINDS:
                continue
            cell = self._collider(record, component_id, kind, "serialized")
            colliders.append(cell)
            if cell.get("geometryId"):
                geometry_ids.append(cell["geometryId"])
        modifiers = []
        obstacles = []
        agents = []
        has_agent = False
        for component_id, kind in components:
            script = _script(record, component_id)
            if kind == "NavMeshAgent" or script == "NavMeshAgent":
                has_agent = True
                tree = record.tree(component_id)
                agents.append({"componentId": str(component_id),
                               "enabled": _bool(tree, "m_Enabled"),
                               "agentTypeId": tree.get("m_AgentTypeID"),
                               "radius": tree.get("m_Radius"), "height": tree.get("m_Height"),
                               "baseOffset": tree.get("m_BaseOffset")})
            if script == MODIFIER_SCRIPT:
                tree = record.tree(component_id)
                modifiers.append({
                    "componentId": str(component_id),
                    "origin": "serialized",
                    "enabled": _bool(tree, "m_Enabled"),
                    "overrideArea": _bool(tree, "m_OverrideArea"),
                    "area": tree.get("m_Area"),
                    "ignoreFromBuild": _bool(tree, "m_IgnoreFromBuild"),
                    "affectedAgents": tree.get("m_AffectedAgents"),
                })
            if kind == "NavMeshObstacle":
                obstacles.append(self._obstacle(record, component_id))
        tree = gameobjects[goid]
        return {
            "schemaVersion": SCHEMA_VERSION,
            "coordinateContract": COORDINATE_CONTRACT,
            "units": "source-unity-unit",
            "uvConvention": UV_CONVENTION,
            "gameObjectId": str(goid),
            "transformId": str(transform),
            "activeSelf": _bool(tree, "m_IsActive"),
            "activeInHierarchy": _active_hierarchy(graph, gameobjects, transform),
            "authoredLayer": tree.get("m_Layer"),
            "fixtureNavLayer": (id(record), transform) in self._nav_layer,
            "colliders": colliders,
            "geometryIds": sorted(set(geometry_ids)),
            "modifiers": modifiers,
            "hasNavMeshAgent": has_agent,
            "navMeshAgents": agents,
            "navMeshObstacles": obstacles,
        }

    def _collider(self, record, component_id, kind, origin):
        tree = record.tree(component_id)
        cell = {
            "componentId": str(component_id),
            "kind": kind,
            "origin": origin,
            "enabled": _bool(tree, "m_Enabled"),
            "isTrigger": _bool(tree, "m_IsTrigger"),
            "includeLayers": (tree.get("m_IncludeLayers") or {}).get("m_Bits"),
            "excludeLayers": (tree.get("m_ExcludeLayers") or {}).get("m_Bits"),
            "layerOverridePriority": tree.get("m_LayerOverridePriority"),
            "providesContacts": _bool(tree, "m_ProvidesContacts"),
            "convex": _bool(tree, "m_Convex") if kind == "MeshCollider" else None,
            "cookingOptions": tree.get("m_CookingOptions") if kind == "MeshCollider" else None,
            "mesh": None,
            "geometryId": None,
            "gap": None,
        }
        if kind == "MeshCollider":
            pointer = tree.get("m_Mesh")
            cell["mesh"] = _pointer(pointer)
            cell["geometryId"] = self._geometry(record, pointer)
            if cell["mesh"] and not cell["geometryId"]:
                cell["gap"] = "MeshCollider mesh pointer is unresolved"
        elif kind == "BoxCollider":
            cell.update({"center": _point(tree, "m_Center"),
                         "size": _vec(tree, "m_Size")})
        elif kind == "SphereCollider":
            cell.update({"center": _point(tree, "m_Center"),
                         "radius": tree.get("m_Radius")})
        elif kind == "CapsuleCollider":
            cell.update({"center": _point(tree, "m_Center"),
                         "radius": tree.get("m_Radius"), "height": tree.get("m_Height"),
                         "direction": tree.get("m_Direction")})
        return cell

    @staticmethod
    def _obstacle(record, component_id):
        tree = record.tree(component_id)
        return {
            "componentId": str(component_id),
            "enabled": _bool(tree, "m_Enabled"),
            "shape": tree.get("m_Shape"),
            "center": _point(tree, "m_Center"),
            "extents": _vec(tree, "m_Extents"),
            "carve": _bool(tree, "m_Carve"),
            "onlyStationary": _bool(tree, "m_CarveOnlyStationary"),
            "moveThreshold": tree.get("m_MoveThreshold"),
            "stationaryTime": tree.get("m_TimeToStationary"),
        }

    def _geometry(self, record, pointer):
        if not pointer or not pointer.get("m_PathID"):
            return None
        target = self.store.follow(record, pointer)
        if target is None:
            self._gaps.append({"type": "collider-mesh-unresolved", "file": record.archive,
                               "pointer": _pointer(pointer)})
            return None
        target_record, path_id = target
        key = (target_record.archive, int(path_id))
        geometry_id = f"{key[0]}:{key[1]}"
        if geometry_id in self._geometries:
            return geometry_id
        if geometry_id in self._failed_geometries:
            return None
        obj = target_record.objects.get(path_id)
        try:
            if target_record.kinds.get(path_id) != "Mesh" or obj is None:
                raise ValueError("collider mesh pointer does not name a Mesh")
            handler = self.mesh_handler(obj.read())
            handler.process()
            positions = [list(reflect_position([float(v[0]), float(v[1]), float(v[2])]))
                         for v in (handler.m_Vertices or [])]
            triangles = []
            submeshes = target_record.tree(path_id).get("m_SubMeshes", [])
            for index, submesh in enumerate(handler.get_triangles()):
                base_vertex = int(submeshes[index].get("baseVertex", 0)) if index < len(submeshes) else 0
                for tri in submesh:
                    if len(tri) == 3:
                        triangles.append([int(tri[0]) + base_vertex, int(tri[2]) + base_vertex,
                                          int(tri[1]) + base_vertex])
            if not positions or not triangles:
                raise ValueError("collider mesh has no triangle geometry")
            if any(not math.isfinite(value) for point in positions for value in point):
                raise ValueError("collider mesh contains nonfinite positions")
            if any(index < 0 or index >= len(positions) for tri in triangles for index in tri):
                raise ValueError("collider mesh triangle index out of range")
            self._geometries[geometry_id] = {
                "geometryId": geometry_id,
                "source": {"file": target_record.archive, "pathId": str(path_id)},
                "positions": positions,
                "triangles": triangles,
                "uvConvention": UV_CONVENTION,
            }
            return geometry_id
        except Exception as exc:
            self._failed_geometries.add(geometry_id)
            self._gaps.append({"type": "collider-mesh-unreadable", "geometryId": geometry_id,
                               "reason": f"{type(exc).__name__}: {exc}"})
            return None

    def node(self, record, transform):
        return self._records.get((id(record), transform))

    def document(self):
        return {
            "schemaVersion": SCHEMA_VERSION,
            "coordinateContract": COORDINATE_CONTRACT,
            "uvConvention": UV_CONVENTION,
            "units": "source-unity-unit",
            "geometry": [self._geometries[key] for key in sorted(self._geometries)],
            "gaps": self._gaps,
            "nativeParity": False,
            "sourceRules": SOURCE_RULES,
            "nativeBoundaries": [
                "convex=true requests native hull cooking; exported geometry is the input mesh, not the cooked hull",
                "Unity CollectSources trigger/enabled and primitive tessellation semantics require runtime validation",
                "FixtureController branch that creates a missing FixtureView is not materialized from a prefab-only export",
            ],
        }
