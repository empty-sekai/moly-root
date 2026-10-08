"""Site step items and scene directors: what the export writes and what it refuses.

The corpus is synthetic, as in :mod:`test_fixture_meshes`: a small site package
built in memory with a step prefab (its root carries the step item view and a
PlayableDirector, a child model carries an Animator), a sibling prefab, an
AnimationClip, and hand-written timeline tables in the readers' format.  The
walk, skin binding and animation embed are the furniture writer's own code
running against the fake objects.  A criterion that cannot go red is not a
criterion: each positive arm has its negative arm next to it.
"""
import json
import os
import struct
import sys
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from core.assets.packages import PackageStore
from fixtures import meshes as meshes_module
from sites import step_items
from test_fixture_meshes import _Package, _wire
from test_fixture_animations import _binding, _clip_tree

PKG = "mysekai__site__field__garden"
STEP = "tl_prop_step01"


def _crc(path):
    return zlib.crc32(path.encode("utf-8")) & 0xFFFFFFFF


def _component(pkg, goid, kind, tree):
    component = pkg.add(kind, dict(tree, m_GameObject={"m_FileID": 0, "m_PathID": goid}))
    obj = next(o for o in pkg.objects if o.path_id == goid)
    obj._tree["m_Component"].append({"component": {"m_FileID": 0, "m_PathID": component}})
    return component


def _node(pkg, name, parent_tp=None, position=(0.0, 0.0, 0.0)):
    """One GameObject and its Transform, each under its own id, linked both ways."""
    goid, tpid = pkg._id(), pkg._id()
    pkg.add("GameObject", {"m_Name": name, "m_IsActive": 1, "m_Component": [
        {"component": {"m_FileID": 0, "m_PathID": tpid}}]}, path_id=goid)
    pkg.add("Transform", {
        "m_GameObject": {"m_FileID": 0, "m_PathID": goid},
        "m_Father": {"m_FileID": 0, "m_PathID": parent_tp or 0},
        "m_LocalPosition": {"x": position[0], "y": position[1], "z": position[2]},
        "m_LocalRotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0},
        "m_Children": []}, path_id=tpid)
    if parent_tp is not None:
        parent = next(o for o in pkg.objects if o.path_id == parent_tp)
        parent._tree["m_Children"].append({"m_FileID": 0, "m_PathID": tpid})
    return goid, tpid


def _child(pkg, parent_tp, name, position=(0.0, 0.0, 0.0)):
    return _node(pkg, name, parent_tp, position)


class _Site:
    """The package, its object ids, and the timeline tables that name them."""

    def __init__(self):
        pkg = _Package(PKG)
        self.pkg = pkg
        script = pkg.add("MonoScript", {"m_ClassName": step_items.STEP_VIEW})
        # The step prefab: root (view + director) / model (Animator) / joint.
        self.root_go, self.root_tp = _node(pkg, STEP)
        self.model_go, self.model_tp = _child(pkg, self.root_tp, "mdl_prop")
        self.joint_go, self.joint_tp = _child(pkg, self.model_tp, "joint", (0.0, 1.0, 0.0))
        pkg.renderer(self.model_go, pkg.mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2]),
                     [pkg.material()])
        # The builder's renderer replaces the component list; the Transform comes back first.
        model = next(o for o in pkg.objects if o.path_id == self.model_go)
        model._tree["m_Component"].insert(0, {"component": {"m_FileID": 0, "m_PathID": self.model_tp}})
        self.animator = _component(pkg, self.model_go, "Animator", {})
        self.director = _component(pkg, self.root_go, "PlayableDirector", {})
        self.view = _component(pkg, self.root_go, "MonoBehaviour", {
            "m_Script": {"m_FileID": 0, "m_PathID": script},
            "_playableDirector": {"m_FileID": 0, "m_PathID": self.director}})
        # A sibling prefab of the same package that must not be written.
        self.other_go, self.other_tp = _node(pkg, "site_root")
        self.flower_a_go, self.flower_a_tp = _child(pkg, self.other_tp, "flower")
        self.petal_a_go, _ = _child(pkg, self.flower_a_tp, "petal", (0.0, 0.5, 0.0))
        self.flower_b_go, self.flower_b_tp = _child(pkg, self.other_tp, "flower (1)")
        self.petal_b_go, _ = _child(pkg, self.flower_b_tp, "petal", (0.0, 0.5, 0.0))
        self.animator_a = _component(pkg, self.flower_a_go, "Animator", {})
        self.animator_b = _component(pkg, self.flower_b_go, "Animator", {})
        self.site_director = _component(pkg, self.other_go, "PlayableDirector", {})
        # The prop clip animates joint relative to the model's Animator.
        self.clip = pkg.add("AnimationClip", _clip_tree(
            "prop_step01_S", [_binding("joint", 1)], constant=[0.0, 2.0, 0.0]))
        self.flower_clip = pkg.add("AnimationClip", _clip_tree(
            "mov_flower01", [_binding("petal", 1)], constant=[0.0, 0.7, 0.0]))
        pkg.add("AssetBundle", {"m_Container": [
            [f"assets/x/timeline/{STEP}.prefab",
             {"asset": {"m_FileID": 0, "m_PathID": self.root_go}}],
            ["assets/x/site_root.prefab",
             {"asset": {"m_FileID": 0, "m_PathID": self.other_go}}]],
            "m_Dependencies": []})
        self.file = pkg.archive.name

    def ident(self, path_id):
        return {"file": self.file, "pathId": str(path_id)}

    def tables(self, *, fixture_binding=True, clip_package=PKG, site_bindings=True):
        track, character, flower_a, flower_b = 91, 92, 93, 94
        tracks = {"package": PKG, "timelines": [
            {"name": STEP, "asset": self.ident(81), "tracks": [
                {"class": "AnimationTrack", "name": "CharacterAnimator",
                 "asset": self.ident(character), "children": []},
                {"class": "AnimationTrack", "name": "Fixture1",
                 "asset": self.ident(track), "children": []}]},
            {"name": "tl_site", "asset": self.ident(82), "tracks": [
                {"class": "AnimationTrack", "name": "Animation Track",
                 "asset": self.ident(flower_a), "children": []},
                {"class": "AnimationTrack", "name": "Animation Track (1)",
                 "asset": self.ident(flower_b), "children": []}]}],
            "prefabs": [
                {"container": f"assets/x/timeline/{STEP}.prefab", "directors": [
                    {"asset": self.ident(self.director), "timeline": self.ident(81),
                     "sceneBindings": [
                         {"key": self.ident(character), "value": None},
                         {"key": self.ident(track),
                          "value": self.ident(self.animator) if fixture_binding else None}],
                     "exposedReferences": []}]},
                {"container": "assets/x/site_root.prefab", "directors": [
                    {"asset": self.ident(self.site_director), "timeline": self.ident(82),
                     "hierarchy": ["site_root"],
                     "sceneBindings": [
                         {"key": self.ident(flower_a),
                          "value": self.ident(self.animator_a) if site_bindings else None},
                         {"key": self.ident(flower_b),
                          "value": self.ident(self.animator_b) if site_bindings else None}],
                     "exposedReferences": []}]}]}
        keyed = [
            {"clipIndex": 0, "track": self.ident(character), "target": {
                "clipName": "mov_u000_step01_S", "targetPackage": "mysekai__player__motion__unique",
                "sourceClip": {"file": "CAB-player", "pathId": "5"}}},
            {"clipIndex": 0, "track": self.ident(track), "target": {
                "clipName": "prop_step01_S", "targetPackage": clip_package,
                "sourceClip": self.ident(self.clip)}},
            {"clipIndex": 0, "track": self.ident(flower_a), "target": {
                "clipName": "mov_flower01", "targetPackage": PKG,
                "sourceClip": self.ident(self.flower_clip)}},
            {"clipIndex": 0, "track": self.ident(flower_b), "target": {
                "clipName": "mov_flower01", "targetPackage": PKG,
                "sourceClip": self.ident(self.flower_clip)}}]
        return tracks, {"package": PKG, "keyedClips": keyed}


def _store(tmp_path, monkeypatch, site):
    (tmp_path / PKG).touch()
    _wire(meshes_module, packages_module, {PKG: site.pkg.finish()}, monkeypatch)
    store = PackageStore([str(tmp_path / PKG)])
    package = store.package(PKG)
    return store, step_items._Records(store, package)


def _glb(path):
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length])


def _scene_names(document):
    names, pending = [], list(document["scenes"][document["scene"]]["nodes"])
    while pending:
        node = document["nodes"][pending.pop()]
        names.append(node["name"])
        pending.extend(node.get("children", []))
    return sorted(names)


def test_step_view_is_found_on_the_prefab_root(tmp_path, monkeypatch):
    site = _Site()
    _, records = _store(tmp_path, monkeypatch, site)
    views = step_items.step_views(records)
    assert [(v["file"], v["director"]) for v in views] == [(STEP, (site.file, str(site.director)))]


def test_step_item_glb_holds_only_its_prefab_and_its_prop_clip(tmp_path, monkeypatch):
    site = _Site()
    store, records = _store(tmp_path, monkeypatch, site)
    tracks, targets = site.tables()
    out = tmp_path / "out"
    items, _ = step_items.export_step_items(store, records, PKG, tracks, targets, out)
    row = items[f"mysekai/site/field/garden|{STEP}"]
    assert row["glb"] == f"{STEP}.glb" and row["prefab"] == STEP
    assert row["view"]["director"] == site.ident(site.director)
    # Only the Fixture1 clip, by source identity; the CharacterAnimator one is the avatar's.
    assert [(c["name"], c["sourceClip"]) for c in row["clips"]] == [("prop_step01_S", site.ident(site.clip))]
    document = _glb(out / row["glb"])
    assert _scene_names(document) == sorted([STEP, "mdl_prop", "joint"])
    assert all(node["name"] not in {"site_root", "flower", "petal"} for node in document["nodes"])
    root = document["nodes"][document["scenes"][0]["nodes"][0]]
    assert root["extras"]["coordinateContract"] == "moly-rh-y-up-reflect-x-v1"
    [animation] = document["animations"]
    assert animation["name"] == "prop_step01_S"
    target = document["nodes"][animation["channels"][0]["target"]["node"]]
    assert target["name"] == "joint"


def test_a_prop_clip_of_another_package_is_refused_by_name(tmp_path, monkeypatch):
    site = _Site()
    store, records = _store(tmp_path, monkeypatch, site)
    tracks, targets = site.tables(clip_package="mysekai__site__field__elsewhere")
    with pytest.raises(ValueError, match="prop_step01_S ships in mysekai__site__field__elsewhere"):
        step_items.export_step_items(store, records, PKG, tracks, targets, tmp_path / "out")


def test_an_unbound_prop_track_is_refused(tmp_path, monkeypatch):
    site = _Site()
    store, records = _store(tmp_path, monkeypatch, site)
    tracks, targets = site.tables(fixture_binding=False)
    with pytest.raises(ValueError, match="binds no Animator"):
        step_items.export_step_items(store, records, PKG, tracks, targets, tmp_path / "out")


def test_a_prop_track_bound_outside_the_prefab_is_refused(tmp_path, monkeypatch):
    site = _Site()
    store, records = _store(tmp_path, monkeypatch, site)
    tracks, targets = site.tables()
    tracks["prefabs"][0]["directors"][0]["sceneBindings"][1]["value"] = site.ident(site.animator_a)
    with pytest.raises(ValueError, match="outside the prefab"):
        step_items.export_step_items(store, records, PKG, tracks, targets, tmp_path / "out")


def test_scene_director_rig_is_the_first_bound_animator_subtree(tmp_path, monkeypatch):
    site = _Site()
    store, records = _store(tmp_path, monkeypatch, site)
    tracks, targets = site.tables()
    out = tmp_path / "out"
    directors, _ = step_items.export_scene_directors(
        store, records, PKG, tracks, targets, out, {f"assets/x/timeline/{STEP}.prefab"})
    row = directors["mysekai/site/field/garden|site_root"]
    [rig] = row["rigs"]
    assert rig["root"] == "flower"
    assert rig["animators"] == [site.ident(site.animator_a), site.ident(site.animator_b)]
    document = _glb(out / rig["glb"])
    assert _scene_names(document) == ["flower", "petal"]
    [animation] = document["animations"]
    assert document["nodes"][animation["channels"][0]["target"]["node"]]["name"] == "petal"


def test_a_template_director_with_no_bound_track_is_skipped(tmp_path, monkeypatch):
    site = _Site()
    store, records = _store(tmp_path, monkeypatch, site)
    tracks, targets = site.tables(site_bindings=False)
    directors, reports = step_items.export_scene_directors(
        store, records, PKG, tracks, targets, tmp_path / "out", {f"assets/x/timeline/{STEP}.prefab"})
    assert directors == {}
    assert reports == [{"director": "site_root", "skipped": "no track is bound in the prefab"}]
