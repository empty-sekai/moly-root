"""The site pack: what it must contain, and what would make each check fail.

The fixtures here are synthetic.  Real site packages are tens of megabytes and are
not in this repository, so every check below is written against a small corpus built
in-process — which also means each one can be shown to fail: several tests build a
*wrong* corpus on purpose (a site whose offset is baked into its geometry, a
collision surface merged into the visible scene) and assert that the check catches
it.  A criterion that cannot go red is not a criterion.
"""
import json
import math
import os
import re
import struct
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image

from core import mesh as core_mesh
from core.assets import packages as packages_module
from core.assets.router import route
from sites import census, pack
from sites.placement import TILE_SIZE


# -- synthetic packages -----------------------------------------------------
#
# A package is a container of objects that answer `type.name`, `path_id`,
# `read_typetree()` and, for images, `read().image`.


class _AssetFile:
    def __init__(self, name, externals=()):
        self.name = name
        self.externals = [SimpleNamespace(name=e) for e in externals]


class _Object:
    def __init__(self, kind, path_id, tree, asset_file, image=None):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = asset_file
        self._image = image

    def read_typetree(self):
        return self._tree

    def read(self):
        return SimpleNamespace(image=self._image, name=self._tree.get("m_Name"))


class _StubMeshHandler:
    """Vertex arrays of a fixture mesh, in place of the reader's own decoder."""

    ARRAYS = {}

    def __init__(self, mesh):
        self._name = getattr(mesh, "name", None)

    def process(self):
        data = self.ARRAYS[self._name]
        self.m_VertexCount = len(data["vertices"])
        self.m_Vertices = data["vertices"]
        self.m_Normals = [(0.0, 1.0, 0.0)] * len(data["vertices"])
        self.m_UV0 = [(0.0, 0.0)] * len(data["vertices"])
        self.m_IndexBuffer = data["indices"]
        self.m_Colors = None


UNIT_QUAD = ([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0), (0.0, 0.0, 1.0)],
             [0, 1, 2, 0, 2, 3])


class _Package:
    """A package under construction: objects, container entries, dependencies."""

    def __init__(self, name, dependencies=(), externals=()):
        self.name = name
        self.archive = _AssetFile(f"CAB-{name}", externals)
        self.objects = []
        self.container = []
        self.dependencies = list(dependencies)
        self._next = 100

    def _id(self):
        self._next += 1
        return self._next

    def add(self, kind, tree, path_id=None, image=None, asset=None):
        path_id = self._id() if path_id is None else path_id
        self.objects.append(_Object(kind, path_id, tree, self.archive, image))
        if asset:
            self.container.append([asset, {"asset": {"m_FileID": 0,
                                                     "m_PathID": path_id}}])
        return path_id

    def mesh(self, name, vertices=None, indices=None, asset=None):
        vertices, indices = (vertices, indices) if vertices else UNIT_QUAD
        _StubMeshHandler.ARRAYS[name] = {"vertices": vertices, "indices": indices}
        return self.add("Mesh", {"m_Name": name,
                                 "m_SubMeshes": [{"topology": 0,
                                                  "indexCount": len(indices)}]},
                        asset=asset)

    def texture(self, name):
        return self.add("Texture2D", {"m_Name": name, "m_Width": 4, "m_Height": 4},
                        image=Image.new("RGBA", (4, 4)))

    def material(self, name, texture=None, shader=None, floats=None):
        return self.add("Material", {
            "m_Name": name,
            "m_Shader": shader or {"m_FileID": 0, "m_PathID": 0},
            "m_CustomRenderQueue": -1, "m_ValidKeywords": [],
            "m_SavedProperties": {
                "m_TexEnvs": [["_MainTex", {"m_Texture": {"m_FileID": 0,
                                                          "m_PathID": texture or 0},
                                            "m_Scale": {"x": 1.0, "y": 1.0},
                                            "m_Offset": {"x": 0.0, "y": 0.0}}]],
                "m_Floats": [[name, value] for name, value in (floats or {}).items()],
                "m_Colors": [["_Color", {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}]]}})

    def node(self, name, parent=None, position=(0.0, 0.0, 0.0), components=(),
             active=True, asset=None):
        """One game object and its transform; *components* are (kind, tree) pairs."""
        game_object = self._id()
        transform = self._id()
        ids = [transform]
        for kind, tree in components:
            ids.append(self.add(kind, dict(tree,
                                           m_GameObject={"m_FileID": 0,
                                                         "m_PathID": game_object})))
        self.add("GameObject", {"m_Name": name, "m_IsActive": int(active),
                                "m_Component": [{"component": {"m_FileID": 0,
                                                               "m_PathID": i}}
                                                for i in ids]},
                 path_id=game_object, asset=asset)
        self.add("Transform", {
            "m_GameObject": {"m_FileID": 0, "m_PathID": game_object},
            "m_Father": {"m_FileID": 0, "m_PathID": parent or 0},
            "m_LocalPosition": {"x": position[0], "y": position[1], "z": position[2]},
            "m_LocalRotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0}}, path_id=transform)
        return transform

    def finish(self):
        self.add("AssetBundle", {"m_Name": self.name.replace("__", "/"),
                                 "m_Dependencies": self.dependencies,
                                 "m_Container": self.container}, path_id=1)
        return SimpleNamespace(objects=self.objects)


def _renderer(material):
    return ("MeshRenderer", {"m_Enabled": 1,
                             "m_Materials": [{"m_FileID": 0, "m_PathID": material}]})


def _filter(mesh):
    return ("MeshFilter", {"m_Mesh": {"m_FileID": 0, "m_PathID": mesh}})


def _collider(mesh, file_id=0):
    return ("MeshCollider", {"m_Enabled": 1, "m_Convex": 0,
                             "m_Mesh": {"m_FileID": file_id, "m_PathID": mesh}})


def _navmesh(name, tiles=2, agent=0, height_mesh=True):
    tree = {
        "m_Name": name, "m_AgentTypeID": agent,
        "m_Position": {"x": 0.0, "y": 0.0, "z": 0.0},
        "m_Rotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        "m_SourceBounds": {"m_Center": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "m_Extent": {"x": 8.0, "y": 2.0, "z": 8.0}},
        "m_NavMeshBuildSettings": {"agentTypeID": agent, "agentRadius": 0.05,
                                   "agentHeight": 1.0, "agentSlope": 45.0,
                                   "agentClimb": 0.2, "minRegionArea": 2.0,
                                   "manualCellSize": 1, "cellSize": 0.03,
                                   "manualTileSize": 1, "tileSize": 256},
        "m_NavMeshTiles": [{"m_Hash": {f"bytes[{i}]": index for i in range(16)},
                            "m_MeshData": [index] * 8}
                           for index in range(tiles)],
        "m_Heightmaps": [], "m_OffMeshLinks": [], "m_HeightMeshes": []}
    if height_mesh:
        tree["m_HeightMeshes"] = [{
            "m_Vertices": [{"x": x, "y": 0.0, "z": z}
                           for x, y, z in UNIT_QUAD[0]],
            "m_Indices": UNIT_QUAD[1],
            "m_Nodes": [{"i": 0}],
            "m_Bounds": {"m_Center": {"x": 0.0, "y": 0.0, "z": 0.0},
                         "m_Extent": {"x": 1.0, "y": 0.0, "z": 1.0}}}]
    return tree


SHADER_PACKAGE = "mysekai__shader"


def _shader_package():
    package = _Package(SHADER_PACKAGE)
    package.add("Shader", {"m_ParsedForm": {
        "m_Name": "Mysekai/Site/Ground", "m_FallbackName": "",
        "m_SubShaders": [{"m_Tags": {"tags": [("QUEUE", "Geometry"),
                                              ("RenderType", "Opaque")]}}]}},
                path_id=7001)
    package.add("Shader", {"m_ParsedForm": {
        "m_Name": "Mysekai/Site/Tree", "m_FallbackName": "",
        "m_SubShaders": [{"m_Tags": {"tags": [("QUEUE", "Geometry"),
                                              ("RenderType", "Opaque")]}}]}},
                path_id=7002)
    return package.finish()


def _scene_package(name, root_position=(0.0, 0.0, 0.0), navmesh=True,
                   merge_collision=False, indoor=False):
    """One site scene package, in the shape the shipped ones have."""
    site = name.split("__")[-1]
    package = _Package(f"mysekai__site__field__{name}", ["mysekai/shader"],
                       externals=[f"CAB-{SHADER_PACKAGE}"])
    ground_texture = package.texture(f"tex_{site}_ground")
    ground = package.material(f"mat_{site}_ground", ground_texture,
                              shader={"m_FileID": 1, "m_PathID": 7001})
    foliage = package.material(f"mat_{site}_tree", ground_texture,
                               shader={"m_FileID": 1, "m_PathID": 7002},
                               floats={"_UseAlphaClip": 1.0, "_AlphaClip": 0.4,
                                       "_Cull": 0.0})
    ground_mesh = package.mesh(f"mdl_site_base_{site}_ground01")
    tree_mesh = package.mesh(f"mdl_site_prop_{site}_tree01")
    nav_ground = package.mesh(f"mdl_site_base_{site}_nav_ground")
    nav_cam = package.mesh(f"mdl_site_base_{site}_nav_cam")
    footse = package.mesh(f"mdl_site_base_{site}_footse")

    root = package.node(site, position=root_position,
                        components=[("MonoBehaviour", {"m_Enabled": 1,
                                                       "m_Script": {"m_FileID": 0,
                                                                    "m_PathID": 9001},
                                                       "m_Name": "",
                                                       "_soundWaveMeshFilter": None})],
                        asset=f"assets/.../mysekai/site/field/{name}/{site}.prefab")
    package.add("MonoScript", {"m_ClassName": "MysekaiSiteView"}, path_id=9001)

    if not indoor:
        base = package.node("base", parent=root)
        package.node(f"{site}_ground01", parent=base,
                     components=[_filter(ground_mesh), _renderer(ground)])
        package.node(f"{site}_tree01", parent=base, position=(1.0, 0.0, 2.0),
                     components=[_filter(tree_mesh), _renderer(foliage)])
        package.node("decoration", parent=root)
        package.node("hidden_prop", parent=root, active=False,
                     components=[_filter(tree_mesh), _renderer(foliage)])
    target = package.node("navmesh_target", parent=root)
    surfaces = [(f"mdl_site_base_{site}_nav_ground", nav_ground),
                (f"mdl_site_base_{site}_nav_cam", nav_cam),
                (f"mdl_site_base_{site}_footse", footse)]
    for surface_name, mesh in surfaces:
        components = [_filter(mesh), _collider(mesh)]
        if merge_collision:                # the wrong corpus: collision drawn too
            components.append(_renderer(ground))
        package.node(surface_name, parent=target, components=components)
    package.node("env", parent=root)
    package.node("collider", parent=root)

    package.add("PlayableDirector", {
        "m_GameObject": {"m_FileID": 0, "m_PathID": 0},
        "m_PlayableAsset": {"m_FileID": 0, "m_PathID": 0},
        "m_SceneBindings": [], "m_ExposedReferences": {"m_References": []},
        "m_WrapMode": 1, "m_InitialState": 1, "m_InitialTime": 0.0})
    package.add("MonoBehaviour", {"m_Enabled": 1, "m_Name": "env_001_sunny",
                                  "m_Script": {"m_FileID": 0, "m_PathID": 9002},
                                  "brightness": 1.0},
                asset=f"assets/.../mysekai/site/field/{name}/env/001_sunny/"
                      f"env_001_sunny.asset")
    package.add("MonoScript", {"m_ClassName": "SiteEnvironmentConfig"}, path_id=9002)
    if navmesh:
        package.add("NavMeshData", _navmesh("navmesh"),
                    asset=f"assets/.../mysekai/site/field/{name}/navmesh.asset")
    return package.finish()


def _kit_package():
    package = _Package("mysekai__site__my_room_asset__common", ["mysekai/shader"],
                       externals=[f"CAB-{SHADER_PACKAGE}"])
    texture = package.texture("tex_room")
    material = package.material("mat_room", texture,
                                shader={"m_FileID": 1, "m_PathID": 7001})
    floor = package.mesh("mdl_site_floor_small", asset="assets/.../floor_small.fbx")
    wall = package.mesh("wall_left", asset="assets/.../wall_left.fbx")
    for name, mesh in (("mdl_site_floor_small", floor), ("wall_left", wall)):
        root = package.node(name, asset=f"assets/.../{name}.prefab")
        package.node(f"{name}_geometry", parent=root,
                     components=[_filter(mesh), _renderer(material),
                                 _collider(mesh)])
    return package.finish(), floor, wall


def _module_package(level, floor, wall):
    package = _Package(f"mysekai__site__house__lv_0{level}",
                       ["mysekai/site/my_room_asset/common"],
                       externals=["CAB-mysekai__site__my_room_asset__common"])
    for name, mesh in (("mdl_static_floor", floor), ("mdl_static_wall", wall)):
        root = package.node(name, asset=f"assets/.../lv_0{level}/{name}.prefab")
        package.node(f"kit_{name}", parent=root,
                     components=[_filter({"m_FileID": 1, "m_PathID": mesh}
                                         and mesh), _collider(mesh, file_id=1)])
        # the mesh itself lives in the kit, reached through the external file id
        package.objects[-1]._tree["m_Mesh"] = {"m_FileID": 1, "m_PathID": mesh}
    return package.finish()


def _nav_module_package(floor):
    package = _Package("mysekai__site__house__navigation_mesh",
                       ["mysekai/site/my_room_asset/common"],
                       externals=["CAB-mysekai__site__my_room_asset__common"])
    for level, mesh in ((1, 0), (2, floor)):
        root = package.node(f"lv_0{level}", asset=f"assets/.../lv_0{level}.prefab")
        package.node(f"surface_lv_0{level}", parent=root,
                     components=[_collider(mesh, file_id=1 if mesh else 0)])
    return package.finish()


def _shell_package():
    package = _Package("mysekai__site__root", [])
    root = package.node("SiteRoot", asset="assets/.../mysekai/site/siteroot.prefab")
    package.node("Timeline", parent=root, components=[
        ("PlayableDirector", {"m_PlayableAsset": {"m_FileID": 0, "m_PathID": 0},
                              "m_SceneBindings": [],
                              "m_ExposedReferences": {"m_References": []},
                              "m_WrapMode": 1, "m_InitialState": 1,
                              "m_InitialTime": 0.0})])
    return package.finish()


MASTER_SITES = [
    {"id": 1, "mysekaiSiteType": "home_site", "mysekaiSiteCategory": "housing_home",
     "assetbundleName": "home", "name": "home", "isBase": True,
     "isEnabledForMulti": True, "positionX": 0, "positionY": 0, "positionZ": 0},
    {"id": 2, "mysekaiSiteType": "first_floor", "mysekaiSiteCategory": "housing_room",
     "assetbundleName": "first_floor", "name": "1F", "isBase": False,
     "isEnabledForMulti": True, "positionX": 400, "positionY": 0, "positionZ": 0},
    {"id": 3, "mysekaiSiteType": "second_floor", "mysekaiSiteCategory": "housing_room",
     "assetbundleName": "first_floor", "name": "2F", "isBase": False,
     "isEnabledForMulti": True, "positionX": 400, "positionY": 500, "positionZ": 0},
    {"id": 4, "mysekaiSiteType": "third_floor", "mysekaiSiteCategory": "housing_room",
     "assetbundleName": "first_floor", "name": "3F", "isBase": False,
     "isEnabledForMulti": True, "positionX": 400, "positionY": 1000, "positionZ": 0},
    {"id": 5, "mysekaiSiteType": "grassland", "mysekaiSiteCategory": "harvest",
     "assetbundleName": "grasslands", "name": "grassland", "isBase": True,
     "isEnabledForMulti": False, "positionX": 600, "positionY": 0, "positionZ": 0},
]
MASTER_LEVELS = [{"id": 6, "level": 1, "mysekaiSiteId": 2, "characterEntryMaxNum": 1},
                 {"id": 7, "level": 2, "mysekaiSiteId": 2, "characterEntryMaxNum": 2}]
MASTER_LAYOUTS = [{"id": 20, "mysekaiSiteLevelId": 6, "mysekaiLayoutType": "floor",
                   "width": 10, "height": 10, "depth": 10},
                  {"id": 21, "mysekaiSiteLevelId": 7, "mysekaiLayoutType": "floor",
                   "width": 12, "height": 10, "depth": 12}]
MASTER_FOOTSTEPS = [{"id": 1, "red": 255, "green": 0, "blue": 0,
                     "walkCue": "soil", "runCue": "soil"}]


def _master(tmp_path):
    directory = tmp_path / "master"
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in (("mysekaiSites", MASTER_SITES),
                       ("mysekaiSiteLevels", MASTER_LEVELS),
                       ("mysekaiSiteLayouts", MASTER_LAYOUTS),
                       ("mysekaiSiteFootsteps", MASTER_FOOTSTEPS),
                       ("mysekaiSiteGroups", [{"id": 1, "groupId": 1,
                                               "mysekaiSiteId": 1}]),
                       ("mysekaiSiteHousingLayoutUnavailableZones", [])):
        (directory / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")
    return str(directory)


def _corpus(**kwargs):
    """The whole synthetic corpus, keyed by package name."""
    kit, floor, wall = _kit_package()
    packages = {
        SHADER_PACKAGE: _shader_package(),
        "mysekai__site__field__home": _scene_package("home", **kwargs),
        "mysekai__site__field__grasslands": _scene_package("grasslands", **kwargs),
        "mysekai__site__field__first_floor": _scene_package(
            "first_floor", navmesh=False, indoor=True),
        "mysekai__site__my_room_asset__common": kit,
        "mysekai__site__house__lv_01": _module_package(1, floor, wall),
        "mysekai__site__house__lv_02": _module_package(2, floor, wall),
        "mysekai__site__house__navigation_mesh": _nav_module_package(floor),
        "mysekai__site__root": _shell_package(),
    }
    return packages


def _run(tmp_path, monkeypatch, packages=None, master=True):
    packages = _corpus() if packages is None else packages
    monkeypatch.setattr(core_mesh, "MeshHandler", _StubMeshHandler)
    monkeypatch.setattr(packages_module.UnityPy, "load",
                        lambda path: packages[os.path.basename(str(path))])
    out = tmp_path / "out" / "site"
    report = pack.extract_sites(
        [str(tmp_path / "bundles" / name) for name in packages],
        str(out), bundle_root=str(tmp_path / "bundles"),
        master=_master(tmp_path) if master else None)
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    sites = json.loads((out / "sites.json").read_text(encoding="utf-8"))
    return report, out, index, sites


@pytest.fixture
def extracted(tmp_path, monkeypatch):
    return _run(tmp_path, monkeypatch)


def _gltf(path):
    """The JSON chunk of a glTF binary, plus its declared total length."""
    blob = path.read_bytes()
    assert blob[:4] == b"glTF"
    total = struct.unpack("<I", blob[8:12])[0]
    length = struct.unpack("<I", blob[12:16])[0]
    return json.loads(blob[20:20 + length].decode("utf-8")), total, len(blob)


def _node_world(document, index, offset=(0.0, 0.0, 0.0)):
    """Every node's accumulated translation under *index*, parents applied."""
    node = document["nodes"][index]
    here = tuple(a + b for a, b in zip(offset, node.get("translation", [0, 0, 0])))
    yield node, here
    for child in node.get("children", []):
        yield from _node_world(document, child, here)


# -- the domain is routed and selected --------------------------------------

def test_every_site_package_is_routed_to_the_site_domain():
    """Red when a package under the site path routes elsewhere or to nothing:
    the pull would then not download it and the extractor would not open it."""
    for name in ("mysekai__site__field__grasslands", "mysekai__site__root",
                 "mysekai__site__house__lv_03", "mysekai__site__sitemap__prefab",
                 "mysekai__site__field__object__mdl_site_rock_common_stone01",
                 "mysekai__site__field__my_room_asset__skin__ext0001"):
        target = route(name)
        assert target is not None and target.domain == "site", name


def test_a_weather_package_is_not_captured_by_the_site_domain():
    """Red if the site prefix were widened to `mysekai__*site*`: the weather
    packages live under `mysekai__effect__site__environment__…` and belong to the
    phenomena domain, which would then lose them."""
    assert route("mysekai__effect__site__environment__001_sunny__global").domain \
        == "phenomena"


def test_scene_packages_are_the_ones_directly_under_the_field_path():
    """Red when a deeper package is classed as a site scene: `field/object/*` and
    `field/my_room_asset/skin/*` sit under the same path and are not sites."""
    assert census.classify("mysekai__site__field__beach") == ("scene", "beach")
    assert census.classify("mysekai__site__field__object__mdl_site_tool_barrel01") \
        == ("prop", "field__object__mdl_site_tool_barrel01")
    assert census.classify("mysekai__site__field__my_room_asset__skin__ext0001")[0] \
        == "roomSkin"
    assert census.classify("mysekai__site__house__lv_04")[0] == "roomModule"
    assert census.classify("mysekai__site__unheard__of")[0] == "unclassified"


# -- J1: the placement table ------------------------------------------------

def test_the_placement_table_has_one_row_per_site_and_fewer_packages(extracted):
    """J1. Red when the row count changes, or when the rows stop sharing packages:
    nine rows over seven packages is the shape, and one row per package would mean
    the shared indoor package had been split or a package smuggled in as a site."""
    _, _, _, sites = extracted
    assert len(sites["sites"]) == len(MASTER_SITES)
    packages = {row["package"] for row in sites["sites"]}
    assert len(packages) < len(sites["sites"])
    assert sites["packageUsedBy"]["mysekai__site__field__first_floor"] == [2, 3, 4]


def test_a_row_is_joined_to_its_type_by_name_not_by_arithmetic(extracted):
    """J1. Red if the join were `id - 1`: the ids happen to be the enum position
    plus one in this snapshot, and one inserted row would shift every controller."""
    _, _, _, sites = extracted
    by_id = {row["id"]: row for row in sites["sites"]}
    assert by_id[1]["controller"] == "HomeSiteController"
    assert by_id[2]["controller"] == by_id[3]["controller"] == "MyRoomSiteController"
    assert by_id[5]["controller"] == "HarvestSiteController"


# -- J2 / J3: the offset is never in the geometry ---------------------------

def test_the_site_offset_is_in_the_table_and_not_in_the_geometry(extracted):
    """J2. The load-bearing one. Red when any vertex or node of a scene carries a
    site's world offset: three rows share one package and differ only in their
    vertical offset, so a baked offset collapses them into one and the second and
    third floors are gone for good and unrecoverably."""
    _, out, index, sites = extracted
    offsets = {(row["sitePosition"]["x"], row["sitePosition"]["y"],
                row["sitePosition"]["z"]) for row in sites["sites"]}
    assert (400.0, 500.0, 0.0) in offsets          # the table carries them
    for scene in index["scenes"].values():
        document, _, _ = _gltf(out / scene["geometry"])
        root = document["scenes"][document["scene"]]["nodes"][0]
        for node, position in _node_world(document, root):
            assert max(abs(value) for value in position) < 100.0, \
                (scene["package"], node.get("name"), position)


def test_a_baked_offset_is_caught_by_that_same_check(tmp_path, monkeypatch):
    """J2, proved able to fail: a corpus whose scene root ships at the site's world
    position must trip the check above.  Without this, the check might be vacuous."""
    packages = _corpus(root_position=(400.0, 500.0, 0.0))
    _, out, index, _ = _run(tmp_path, monkeypatch, packages)
    scene = index["scenes"]["home"]
    document, _, _ = _gltf(out / scene["geometry"])
    root = document["scenes"][document["scene"]]["nodes"][0]
    worst = max(max(abs(value) for value in position)
                for _, position in _node_world(document, root))
    assert worst >= 100.0


def test_the_three_room_rows_are_three_placements_of_one_package(extracted):
    """J3. Red when the floors resolve to fewer than three placements, or to three
    different packages: they are one package placed three times."""
    _, _, index, sites = extracted
    floors = [row for row in sites["sites"]
              if row["siteType"] in ("first_floor", "second_floor", "third_floor")]
    assert len(floors) == 3
    assert len({row["package"] for row in floors}) == 1
    assert sorted(row["sitePosition"]["y"] for row in floors) == [0.0, 500.0, 1000.0]
    assert index["scenes"]["first_floor"]["geometry"]


# -- J4: the walkable surfaces are there and are separable -------------------

def test_every_scene_carries_its_navmesh_target_with_geometry_in_it(extracted):
    """J4. Red when a scene has no `navmesh_target` slot, or when the slot is
    empty: dropping the collision surfaces as "invisible" leaves a consumer with a
    site nobody can walk on."""
    _, _, index, _ = extracted
    for name, scene in index["scenes"].items():
        assert "navmesh_target" in scene["slots"], name
        assert scene["collision"], name


def test_each_collision_surface_is_its_own_file_with_its_role(extracted):
    """Red when the surfaces are merged, or lose their role: walkable ground,
    camera blocker and footstep surface mean different things, and a consumer that
    cannot tell them apart will either block the player or let the camera through."""
    _, out, index, _ = extracted
    roles = {entry["role"] for entry in index["scenes"]["home"]["collision"]}
    assert roles == {"walkableGround", "cameraBlocker", "footstepSurface"}
    files = [entry["file"] for entry in index["scenes"]["home"]["collision"]]
    assert len(set(files)) == len(files)
    for path in files:
        document, _, _ = _gltf(out / path)
        assert len(document["meshes"]) == 1


def test_a_collision_surface_that_is_also_drawn_is_flagged_where_it_appears(
        tmp_path, monkeypatch):
    """One shipped surface is both drawn and collided with, so "collision is never
    visible" is not an invariant to enforce.  Red if a drawn surface were silently
    excluded from the scene, or included without saying so."""
    packages = _corpus(merge_collision=True)
    _, out, index, _ = _run(tmp_path, monkeypatch, packages)
    entries = index["scenes"]["home"]["collision"]
    assert all(entry["visible"] for entry in entries)
    assert all(entry["file"] for entry in entries)


# -- J5: baked and runtime navigation are two different answers --------------

def test_a_baked_navigation_mesh_is_carried_across_and_marked_unparsed(extracted):
    """J5. Red when the tiles are dropped, or when they are claimed to be parsed:
    the tile format is Unity's own and this repository does not decode it."""
    _, out, index, _ = extracted
    document = json.loads((out / index["scenes"]["home"]["document"])
                          .read_text(encoding="utf-8"))
    entry = document["navmesh"][0]
    assert entry["tiles"]["count"] == 2
    assert entry["tiles"]["parsed"] is False and entry["tiles"]["reason"]
    assert (out / index["scenes"]["home"]["directory"]
            / entry["tiles"]["file"]).exists()
    assert entry["siteLocal"] is True


def test_a_bake_that_ships_a_height_mesh_gives_up_walkable_geometry(extracted):
    """Red when the height mesh is left inside the unparsed blob: it is an ordinary
    vertex and index buffer, and it is the only part of a bake a consumer can draw
    or sample without a navigation runtime."""
    _, out, index, _ = extracted
    directory = out / index["scenes"]["home"]["directory"]
    document = json.loads((directory.parent / "home" / "home.json")
                          .read_text(encoding="utf-8"))
    height = document["navmesh"][0]["heightMeshes"][0]
    assert height["file"] and height["exportedTriangles"] == 2
    mesh, _, _ = _gltf(directory / height["file"])
    assert mesh["meshes"][0]["name"] == "heightmesh"


def test_a_site_with_no_bake_is_not_given_an_empty_one(extracted):
    """J5. Red when a site that builds its navigation at runtime is reported with a
    bake — an empty tile list would read as "baked, and empty", which is the
    opposite of what ships."""
    _, _, index, _ = extracted
    assert index["scenes"]["first_floor"]["navmesh"] == []
    assert index["scenes"]["home"]["navmesh"]


# -- J6: an empty timeline socket is not an unsupported timeline -------------

def test_an_empty_timeline_socket_names_what_fills_it(extracted):
    """J6. Red when a director with no asset is reported as unsupported, or with no
    consumer named: it is an empty socket the runtime assigns per phenomenon, and
    "unsupported" would send a consumer looking for an asset that does not exist."""
    _, _, index, _ = extracted
    sockets = [socket for socket in index["timelineSockets"] if not socket["bound"]]
    assert sockets
    for socket in sockets:
        assert "unsupported" not in socket["state"].lower()
        assert "EnvironmentLoadData.PlayableAsset" in socket["state"]


# -- J7: furniture is not in a site -----------------------------------------

def test_no_scene_package_carries_a_furniture_behaviour(extracted):
    """J7. Red when a fixture behaviour turns up inside a site scene: furniture is
    instantiated at runtime, so one inside the scene would mean the pack had baked
    a player's belongings into the world."""
    _, out, index, _ = extracted
    for scene in index["scenes"].values():
        document = json.loads((out / scene["document"]).read_text(encoding="utf-8"))
        assert not [name for name in document["components"] if "Fixture" in name]


def test_the_census_records_a_dependency_on_a_furniture_package_as_an_exception(
        extracted):
    """J7. Red when a site's declared dependencies are dropped from the pack: one
    shipped site really does depend on one furniture package, and a consumer has to
    see it as the exception it is rather than infer a rule from it.

    The list is labelled as the package's own and not as the download closure,
    because the two really differ: the shipped manifest declares 23 dependencies
    for the delivery site where its bundle declares 12, and the furniture package
    is in the manifest's list only."""
    _, _, index, _ = extracted
    for scene in index["scenes"].values():
        assert isinstance(scene["declaredDependencies"], list)
    assert index["scenes"]["home"]["declaredDependencies"] == ["mysekai/shader"]


# -- J8: the grid arithmetic -------------------------------------------------

def test_the_pack_carries_the_grid_scale_and_the_formula_that_uses_it(extracted):
    """J8. Red on any other tile scale: a quarter of a unit is measured from the
    shipped binary, and a wrong one puts every piece of furniture in the wrong
    place by a factor."""
    _, _, index, sites = extracted
    assert index["constants"]["tileSize"] == TILE_SIZE == 0.25
    assert index["constants"]["worldFromGrid"] == "world = sitePosition + grid * tileSize"
    assert index["constants"]["gridAxis"]["halfExtentUnits"] == 31.75


def test_a_grid_coordinate_lands_where_the_formula_says(extracted):
    """J8. Red when the table's offsets or the tile scale drift apart from the
    formula the pack states."""
    _, _, index, sites = extracted
    row = next(row for row in sites["sites"] if row["id"] == 3)
    grid = (4, 0, -8)
    world = [row["sitePosition"][axis] + value * index["constants"]["tileSize"]
             for axis, value in zip("xyz", grid)]
    assert world == [401.0, 500.0, -2.0]


def test_a_room_level_states_its_grid_extents_in_cells_and_in_units(extracted):
    """J8. Red when the two disagree: a consumer sizing a room from the cell counts
    and one sizing it from the unit extents must get the same room."""
    _, _, _, sites = extracted
    level = next(level for row in sites["sites"] if row["id"] == 2
                 for level in row["levels"])
    layout = level["layouts"][0]
    for axis in ("width", "height", "depth"):
        assert layout["units"][axis] == layout["cells"][axis] * TILE_SIZE


# -- J9: materials name their shader family ---------------------------------

def test_every_material_names_the_shader_family_it_draws_with(extracted):
    """J9. Red when a shader pointer into the shared package is left unresolved: a
    terrain material reported with no family, or with a leftover editor shader, is
    a consumer's only clue about how to approximate it."""
    _, out, index, _ = extracted
    document = json.loads((out / index["scenes"]["home"]["document"])
                          .read_text(encoding="utf-8"))
    families = {material["shader"]["name"] for material in document["materials"]}
    assert families == {"Mysekai/Site/Ground", "Mysekai/Site/Tree"}
    for material in document["materials"]:
        assert material["shader"]["tags"]["QUEUE"] == "Geometry"


def test_the_preview_material_uses_the_authored_cutout_and_cull_settings(extracted):
    """Red when the preview material invents its own blending: the cutout switch,
    its threshold and the cull mode are authored properties, and a viewer drawing
    foliage as opaque squares or one-sided is showing something the game does not."""
    _, out, index, _ = extracted
    document, _, _ = _gltf(out / index["scenes"]["home"]["geometry"])
    foliage = next(material for material in document["materials"]
                   if material["name"].endswith("_tree"))
    assert foliage["alphaMode"] == "MASK"
    assert foliage["alphaCutoff"] == pytest.approx(0.4)
    assert foliage["doubleSided"] is True
    ground = next(material for material in document["materials"]
                  if material["name"].endswith("_ground"))
    assert "alphaMode" not in ground and not ground.get("doubleSided")


def test_a_preview_material_points_at_a_written_image_by_relative_path(extracted):
    """Red when an image is embedded twice or referenced by an absolute path: the
    pack writes each picture once beside the binary, and the binary points at it."""
    _, out, index, _ = extracted
    document, _, _ = _gltf(out / index["scenes"]["home"]["geometry"])
    directory = out / index["scenes"]["home"]["directory"]
    assert document["images"]
    for image in document["images"]:
        assert not os.path.isabs(image["uri"])
        assert (directory / image["uri"]).exists()


# -- J10: the room sites are a kit, not a scene ------------------------------

def test_the_room_sites_are_assembled_from_a_kit_and_per_level_modules(extracted):
    """J10. Red when the indoor entry is missing or empty: a room site's package
    holds no wall or floor geometry at all, so a pack shaped as one file per site
    would ship three sites with nothing in them and say nothing about it."""
    _, _, index, _ = extracted
    indoor = index["indoor"]
    assert indoor["kit"] and indoor["kit"]["meshes"]
    assert set(indoor["levels"]) >= {"01", "02"}
    assert indoor["levels"]["01"]["module"]["prefabs"]
    assert indoor["levels"]["01"]["walkable"]["prefab"] == "lv_01"


def test_a_room_scene_package_really_has_no_wall_or_floor_geometry(extracted):
    """J10. Red when a room site's scene is given geometry it does not ship: that
    would be fabricated, and it is what a one-file-per-site product would have to
    do to look complete."""
    _, out, index, _ = extracted
    document = json.loads((out / index["scenes"]["first_floor"]["document"])
                          .read_text(encoding="utf-8"))
    primary = next(root for root in document["roots"] if root["primary"])
    assert primary["renderers"] == 0
    assert not [slot for slot in document["slots"] if slot["name"] == "base"]


def test_a_walkable_surface_with_no_mesh_says_so_rather_than_vanishing(extracted):
    """Red when a module whose collider ships with a null mesh is dropped: two of
    the shipped room levels are exactly that, and a silent drop would read as those
    levels having no walkable surface for an unstated reason."""
    _, out, index, _ = extracted
    surfaces = index["indoor"]["levels"]["01"]["walkable"]["surfaces"]
    assert surfaces and surfaces[0]["mesh"] is None
    assert "null on disk" in surfaces[0]["reason"]


# -- the census: every package accounted for --------------------------------

def test_every_package_is_classified_and_appears_in_the_census(extracted):
    """Red when a package is skipped: the census is what makes "all of them were
    opened" a checkable statement rather than a claim."""
    _, out, index, _ = extracted
    census_document = json.loads((out / "packages.json").read_text(encoding="utf-8"))
    assert index["packages"]["count"] == len(census_document["packages"])
    for entry in census_document["packages"].values():
        assert "manifest" in entry["inventory"]["dependencySource"]
    assert sum(index["packages"]["byKind"].values()) == index["packages"]["count"]
    for name, entry in census_document["packages"].items():
        assert entry["kind"] in census.KINDS
        assert entry["inventory"]["objects"] > 0


def test_every_object_of_every_package_is_exported_skipped_or_unsupported(extracted):
    """The completeness invariant. Red when an object type is read and then
    forgotten: the three dispositions must add up to the package's own object
    count, so a new type in a later version shows up as an imbalance."""
    _, out, _, _ = extracted
    census_document = json.loads((out / "packages.json").read_text(encoding="utf-8"))
    for name, entry in census_document["packages"].items():
        objects = entry["objects"]
        assert objects["accountedFor"], name
        assert (objects["exported"] + objects["skipped"] + objects["unsupported"]
                == objects["total"]), name


def test_a_skipped_object_type_says_why_it_was_skipped(extracted):
    """Red when a type is dropped with no reason: "skipped" with no explanation is
    indistinguishable from an oversight."""
    _, out, _, _ = extracted
    census_document = json.loads((out / "packages.json").read_text(encoding="utf-8"))
    reasons = {reason for entry in census_document["packages"].values()
               for reason in entry["skipped"]}
    assert reasons
    assert all(len(reason) > 20 for reason in reasons)


def test_a_component_with_no_structured_reader_is_still_exported(extracted):
    """Red when an unmodelled component is dropped: its serialized fields are the
    authored data, and refusing to interpret them is not a reason to lose them."""
    _, out, index, _ = extracted
    document = json.loads((out / index["scenes"]["home"]["document"])
                          .read_text(encoding="utf-8"))
    assert "MysekaiSiteView" in document["components"]
    entry = document["components"]["MysekaiSiteView"]
    assert entry["count"] == 1 and entry["instances"][0]["fields"]


# -- the pack is a pack: parseable, relative, self-contained -----------------

def test_every_json_artifact_parses_strictly(extracted):
    """Red on a bare `Infinity` or `NaN`: Python writes those by default and no
    strict parser — a browser's own included — will read the file back."""
    _, out, _, _ = extracted
    documents = list(out.rglob("*.json"))
    assert documents
    for path in documents:
        text = path.read_text(encoding="utf-8")
        json.loads(text, parse_constant=_reject)


def _reject(literal):
    raise AssertionError(f"non-JSON literal in artifact: {literal}")


def test_no_artifact_carries_a_path_from_the_machine_that_built_it(extracted):
    """Red when a local path reaches the pack: these artifacts are published, and a
    build directory or a decoder's location has no meaning to a consumer and should
    not be handed to one."""
    _, out, _, _ = extracted
    pattern = re.compile(r"[A-Za-z]:[\\/]|(?:^|[\"'\s])/(?:home|Users|mnt|tmp)/")
    for path in out.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), path


def test_every_glb_declares_its_own_length(extracted):
    """Red on a truncated or mis-padded binary: the header length is what a reader
    trusts before it reads anything else."""
    _, out, _, _ = extracted
    files = list(out.rglob("*.glb"))
    assert files
    for path in files:
        document, total, size = _gltf(path)
        assert total == size, path
        assert document["asset"]["version"] == "2.0"


def test_one_binary_per_package_holds_a_scene_for_every_prefab_root(extracted):
    """Red when the sibling roots are dropped: a scene package holds the site
    prefab *and* the assets it was built from, and the default scene is the one the
    game places."""
    _, out, index, _ = extracted
    document, _, _ = _gltf(out / index["scenes"]["home"]["geometry"])
    names = [scene.get("name") for scene in document["scenes"]]
    assert names[document["scene"]] == "home"


def test_geometry_is_shared_between_the_scenes_of_one_binary(extracted):
    """Red when a mesh used twice is written twice: the vertex buffers dominate the
    pack's size, and a package repeats one prop mesh across hundreds of nodes."""
    _, out, index, _ = extracted
    document, _, _ = _gltf(out / index["scenes"]["home"]["geometry"])
    accessors = [primitive["attributes"]["POSITION"]
                 for mesh in document["meshes"] for primitive in mesh["primitives"]]
    assert len(set(accessors)) < len(document["nodes"])


def test_hidden_nodes_are_kept_and_listed(extracted):
    """Red when an inactive node is dropped, or kept with no way to tell: the pack
    is the authored scene, and a consumer drawing hidden dressing shows what the
    game never shows."""
    _, out, index, _ = extracted
    document = json.loads((out / index["scenes"]["home"]["document"])
                          .read_text(encoding="utf-8"))
    assert "hidden_prop" in document["inactiveNodes"]
    binary, _, _ = _gltf(out / index["scenes"]["home"]["geometry"])
    hidden = next(node for node in binary["nodes"] if node["name"] == "hidden_prop")
    assert hidden["extras"]["active"] is False


def test_the_per_site_environment_presets_are_grouped_by_phenomenon(extracted):
    """Red when the presets are flattened: there is no per-phenomenon environment
    package to look in — the game builds such a name and the manifest ships none —
    so these, inside the site package, are the only ones there are."""
    _, _, index, _ = extracted
    assert index["scenes"]["home"]["environments"] == ["001_sunny"]


# -- without master tables ---------------------------------------------------

def test_without_master_tables_the_packages_still_extract(tmp_path, monkeypatch):
    """Red when a missing master directory fails the run: the geometry, the
    collision surfaces and the census are all in the packages, and only the
    placement table is not."""
    report, out, index, sites = _run(tmp_path, monkeypatch, master=False)
    assert report["packages"] == sum(1 for name in _corpus()
                                    if census.is_site_package(name))
    assert index["scenes"]["home"]["geometry"]
    assert sites["sites"] == []
    assert sites["missing"] and "master" in index["summary"]["missing"]


def test_a_missing_dependency_is_named_rather_than_silently_unresolved(
        tmp_path, monkeypatch):
    """Red when an absent package leaves a null pointer with no explanation: one
    shipped site package depends on a package outside this domain, and a consumer
    must be able to tell "not supplied" from "not there"."""
    packages = _corpus()
    del packages[SHADER_PACKAGE]
    _, out, index, _ = _run(tmp_path, monkeypatch, packages)
    assert SHADER_PACKAGE in index["summary"]["missing"]["dependencies"]
    document = json.loads((out / index["scenes"]["home"]["document"])
                          .read_text(encoding="utf-8"))
    shader = document["materials"][0]["shader"]
    assert shader["name"] is None and shader["external"] is True
