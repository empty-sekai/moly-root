"""Collider inputs stay distinct from rendering, occupancy and cooked hulls."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fixtures.collision import CollisionExport, COORDINATE_CONTRACT
from sites.geometry import Graph


def ptr(path_id):
    return {"m_FileID": 0, "m_PathID": path_id}


class Record:
    archive = "CAB-test"

    def __init__(self):
        self.kinds, self.trees, self.objects, self.scripts = {}, {}, {}, {}

    def add(self, path_id, kind, tree, script="", read=None):
        self.kinds[path_id] = kind
        self.trees[path_id] = tree
        self.scripts[path_id] = script
        self.objects[path_id] = SimpleNamespace(read=lambda: read)

    def tree(self, path_id):
        return self.trees[path_id]

    def script_of(self, path_id):
        return self.scripts[path_id]

    def node(self, goid, name, parent=0, active=1, layer=4):
        transform = goid + 100
        self.add(goid, "GameObject", {"m_Name": name, "m_IsActive": active,
                                     "m_Layer": layer, "m_Component": [{"component": ptr(transform)}]})
        self.add(transform, "Transform", {"m_GameObject": ptr(goid), "m_Father": ptr(parent),
                                         "m_Children": []})
        if parent:
            self.tree(parent)["m_Children"].append(ptr(transform))
        return transform

    def component(self, goid, component_id, kind, tree=None, script=""):
        self.add(component_id, kind, {"m_GameObject": ptr(goid), **(tree or {})}, script)
        self.tree(goid)["m_Component"].append({"component": ptr(component_id)})

    def mesh(self, mesh_id, vertices=None, triangles=None, base_vertex=0):
        vertices = vertices if vertices is not None else [(1, 0, 0), (2, 0, 0), (1, 1, 0)]
        triangles = triangles if triangles is not None else [[0, 1, 2]]
        self.add(mesh_id, "Mesh", {"m_SubMeshes": [{"baseVertex": base_vertex}]},
                 read=SimpleNamespace(vertices=vertices, triangles=triangles))


class Mesh:
    def __init__(self, source):
        self.source = source

    def process(self):
        self.m_Vertices = self.source.vertices

    def get_triangles(self):
        return [self.source.triangles]


def export(record):
    store = SimpleNamespace(follow=lambda rec, pointer: (rec, pointer["m_PathID"])
                            if pointer["m_PathID"] in rec.kinds else None)
    result = CollisionExport(store, mesh_handler=Mesh)
    result.prepare(record, Graph(record))
    return result


def mesh_collider(record, goid, component_id, mesh_id, **values):
    record.component(goid, component_id, "MeshCollider", {
        "m_Enabled": True, "m_IsTrigger": False, "m_Convex": True,
        "m_CookingOptions": 30, "m_Mesh": ptr(mesh_id), **values})


def renderer(record, goid, renderer_id, mesh_id):
    record.component(goid, renderer_id - 1, "MeshFilter", {"m_Mesh": ptr(mesh_id)})
    record.component(goid, renderer_id, "MeshRenderer", {"m_Enabled": True})


def test_collider_only_geometry_is_canonical_and_deduplicated():
    record = Record()
    root = record.node(1, "root")
    child = record.node(2, "hidden-collision", parent=root)
    record.mesh(500)
    mesh_collider(record, 1, 601, 500)
    mesh_collider(record, 2, 602, 500, m_Enabled=False, m_IsTrigger=True)
    result = export(record)
    doc = result.document()
    assert doc["coordinateContract"] == COORDINATE_CONTRACT
    assert doc["nativeParity"] is False
    assert len(doc["geometry"]) == 1
    assert doc["geometry"][0]["positions"] == [[-1, 0, 0], [-2, 0, 0], [-1, 1, 0]]
    assert doc["geometry"][0]["triangles"] == [[0, 2, 1]]
    assert result.node(record, child)["colliders"][0]["enabled"] is False
    assert result.node(record, child)["colliders"][0]["isTrigger"] is True
    assert result.node(record, root)["colliders"][0]["cookingOptions"] == 30


def test_serialized_view_does_not_create_colliders_from_same_name_renderers():
    record = Record()
    root = record.node(1, "chair")
    unrelated = record.node(2, "decoration", root)
    first = record.node(9, "chair", root)
    second = record.node(3, "chair", root)
    record.component(1, 701, "MonoBehaviour", {"m_Enabled": 1}, script="FixtureView")
    for goid, mesh_id in [(2, 502), (9, 509), (3, 503)]:
        record.mesh(mesh_id)
        renderer(record, goid, 800 + goid * 2, mesh_id)
    result = export(record)
    assert not result.node(record, unrelated)["colliders"]
    assert not result.node(record, second)["colliders"]
    assert not result.node(record, first)["colliders"]
    assert result.node(record, second)["fixtureNavLayer"] is True
    assert not result.node(record, first)["modifiers"]
    assert not result.document()["geometry"]


def test_authored_disabled_collider_prevents_fallback_and_is_preserved():
    record = Record()
    root = record.node(1, "chair")
    record.component(1, 701, "MonoBehaviour", script="FixtureView")
    record.mesh(500)
    renderer(record, 1, 800, 500)
    mesh_collider(record, 1, 601, 500, m_Enabled=False, m_Convex=False)
    result = export(record)
    colliders = result.node(record, root)["colliders"]
    assert len(colliders) == 1
    assert colliders[0]["origin"] == "serialized"
    assert colliders[0]["enabled"] is False
    assert colliders[0]["convex"] is False


def test_fence_excludes_only_fallback_not_authored_colliders_or_nav_layer():
    record = Record()
    root = record.node(1, "fence_wood")
    record.component(1, 701, "MonoBehaviour", script="FixtureView")
    record.mesh(500)
    renderer(record, 1, 800, 500)
    result = export(record)
    assert not result.node(record, root)["colliders"]
    assert result.node(record, root)["fixtureNavLayer"] is True
    mesh_collider(record, 1, 601, 500)
    assert len(export(record).node(record, root)["colliders"]) == 1


def test_serialized_view_preserves_absent_colliders_on_active_and_inactive_children():
    record = Record()
    root = record.node(1, "chair")
    hidden = record.node(2, "chair", root, active=0)
    visible = record.node(3, "chair", root)
    record.component(1, 701, "MonoBehaviour", script="FixtureView")
    for goid in [2, 3]:
        record.mesh(500 + goid)
        renderer(record, goid, 800 + goid * 2, 500 + goid)
    result = export(record)
    assert result.node(record, hidden)["activeInHierarchy"] is False
    assert not result.node(record, hidden)["colliders"]
    assert not result.node(record, visible)["colliders"]


def test_components_remain_distinct_and_missing_shape_values_are_not_fabricated():
    record = Record()
    root = record.node(1, "root")
    center = {"x": 2, "y": 3, "z": 4}
    record.component(1, 600, "BoxCollider", {"m_Enabled": True, "m_Center": center,
                                             "m_Size": {"x": 1, "y": 2, "z": 3}})
    record.component(1, 601, "SphereCollider", {"m_Radius": 0.5})
    record.component(1, 602, "NavMeshObstacle", {"m_Enabled": 0, "m_Center": center,
                                                 "m_Extents": {"x": 0.3, "y": 0.4, "z": 0.5},
                                                 "m_Shape": 1, "m_Carve": True})
    record.component(1, 603, "NavMeshAgent", {"m_Enabled": 0, "m_Radius": 0.15})
    record.component(1, 604, "MonoBehaviour", {"m_Enabled": 1, "m_OverrideArea": 1,
                                               "m_Area": 3, "m_IgnoreFromBuild": 1,
                                               "m_AffectedAgents": [42]}, script="NavMeshModifier")
    node = export(record).node(record, root)
    assert node["colliders"][0]["center"] == [-2, 3, 4]
    assert node["colliders"][0]["size"] == [1, 2, 3]
    assert node["colliders"][1]["center"] is None
    assert node["colliders"][1]["enabled"] is None
    assert node["navMeshObstacles"][0]["center"] == [-2, 3, 4]
    assert node["navMeshObstacles"][0]["extents"] == [0.3, 0.4, 0.5]
    assert node["navMeshAgents"][0]["enabled"] is False
    assert node["hasNavMeshAgent"] is True
    assert node["modifiers"][0]["ignoreFromBuild"] is True
    assert node["modifiers"][0]["affectedAgents"] == [42]


def test_unresolved_mesh_is_a_gap_not_empty_success_or_visible_mesh_fallback():
    record = Record()
    root = record.node(1, "chair")
    record.component(1, 701, "MonoBehaviour", script="FixtureView")
    record.mesh(500)
    renderer(record, 1, 800, 500)
    mesh_collider(record, 1, 601, 9999)
    result = export(record)
    assert not result.document()["geometry"]
    assert result.document()["gaps"][0]["type"] == "collider-mesh-unresolved"
    assert result.node(record, root)["colliders"][0]["gap"]


def test_mesh_submesh_base_vertex_is_applied_before_canonical_winding():
    record = Record()
    record.node(1, "root")
    record.mesh(500, vertices=[(0, 0, 0), (1, 0, 0), (2, 0, 0), (1, 1, 0)], base_vertex=1)
    mesh_collider(record, 1, 601, 500)
    assert export(record).document()["geometry"][0]["triangles"] == [[1, 3, 2]]


@pytest.mark.parametrize("vertices,triangles", [([(float("nan"), 0, 0)], [[0, 0, 0]]),
                                                ([(0, 0, 0)], [[0, 1, 2]])])
def test_invalid_geometry_is_reported(vertices, triangles):
    record = Record()
    record.node(1, "root")
    record.mesh(500, vertices=vertices, triangles=triangles)
    mesh_collider(record, 1, 601, 500)
    document = export(record).document()
    assert not document["geometry"]
    assert document["gaps"][0]["type"] == "collider-mesh-unreadable"
