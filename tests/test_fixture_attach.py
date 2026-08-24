"""Furniture attach points: what the extractor must report, and what makes it fail.

The fixtures here are synthetic.  A real furniture package is a bundle of scene
objects and is not in this repository, so every check is written against a small
corpus built in-process — which also means each one can be shown to fail: several
tests plant a *wrong* corpus on purpose (a self-referential pair, a pair whose
names mismatch, an attach-named object no pair references, a dangling pointer)
and assert the extractor reports it as an anomaly instead of swallowing or
"fixing" it.  A criterion that cannot go red is not a criterion.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from core.assets.packages import PackageStore
from fixtures.attach import MISSING_BUNDLE, UNRESOLVED_POINTER
from fixtures.interface import extract

SCRIPT_ID = 9001

MASTER_TABLE = "mysekaiCharacterTalkActionPoints"
MASTER_SLOT = "gameCharacterUnitId1ActionPoint"


class _AssetFile:
    def __init__(self, name, externals=()):
        self.name = name
        self.externals = [SimpleNamespace(name=e) for e in externals]


class _Object:
    def __init__(self, kind, path_id, tree, asset_file):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = asset_file

    def read_typetree(self):
        return self._tree


class _Package:
    """A fixture package under construction: objects, script, attach nodes."""

    def __init__(self, name):
        self.name = name
        self.archive = _AssetFile(f"CAB-{name}")
        self.objects = []
        self._next = 1000

    def _id(self):
        self._next += 1
        return self._next

    def add(self, kind, tree, path_id=None):
        path_id = self._id() if path_id is None else path_id
        self.objects.append(_Object(kind, path_id, tree, self.archive))
        return path_id

    def script(self):
        """The script object whose class name is the fixture view behaviour."""
        return self.add("MonoScript",
                        {"m_ClassName": "FixtureView", "m_Name": "FixtureView"},
                        path_id=SCRIPT_ID)

    def attach_node(self, name, position=(0.0, 0.0, 0.0), rotation=None):
        """One named attach game object with its local transform."""
        rotation = rotation or (0.0, 0.0, 0.0, 1.0)
        game_object = self._id()
        transform = self._id()
        self.add("GameObject",
                 {"m_Name": name, "m_IsActive": 1,
                  "m_Component": [{"component": {"m_FileID": 0,
                                                 "m_PathID": transform}}]},
                 path_id=game_object)
        self.add("Transform",
                 {"m_GameObject": {"m_FileID": 0, "m_PathID": game_object},
                  "m_Father": {"m_FileID": 0, "m_PathID": 0},
                  "m_LocalPosition": {"x": position[0], "y": position[1],
                                      "z": position[2]},
                  "m_LocalRotation": {"x": rotation[0], "y": rotation[1],
                                      "z": rotation[2], "w": rotation[3]},
                  "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0}},
                 path_id=transform)
        return game_object

    def fixture_view(self, pairs):
        """One ``FixtureView`` behaviour; *pairs* are (start_id, end_id) pointers."""
        attach_points = [{"StartLoc": {"m_FileID": 0, "m_PathID": start},
                          "EndLoc": {"m_FileID": 0, "m_PathID": end},
                          "IsUsing": 0, "CharacterId": 0}
                         for start, end in pairs]
        self.add("MonoBehaviour",
                 {"m_GameObject": {"m_FileID": 0, "m_PathID": 0},
                  "m_Enabled": 1,
                  "m_Script": {"m_FileID": 0, "m_PathID": SCRIPT_ID},
                  "m_Name": "", "_isTree": True,
                  "_attachPoints": attach_points})

    def finish(self):
        return SimpleNamespace(objects=self.objects)


def _run(tmp_path, monkeypatch, packages, master=None, missing_table=False,
         touch=True):
    monkeypatch.setattr(packages_module.UnityPy, "load",
                        lambda path: packages[os.path.basename(str(path))])
    if touch:
        for name in packages:
            (tmp_path / name).touch()
    if master is not None:
        directory = tmp_path / "master"
        directory.mkdir(parents=True, exist_ok=True)
        if not missing_table:
            (directory / f"{MASTER_TABLE}.json").write_text(
                json.dumps(master), encoding="utf-8")
        master = str(directory)
    store = PackageStore([str(tmp_path / name) for name in packages])
    result = extract(store, master, str(tmp_path / "out"))
    document = json.loads((tmp_path / "out" / "attach-points.json")
                          .read_text(encoding="utf-8"))
    return result, document


def _main_corpus():
    """Three packages: two clean ones and one with the planted violation shape."""
    table = _Package("mysekai__fixture__mdl_desk")
    table.script()
    a = table.attach_node("loc_start013", position=(-0.35, 0.0, -0.47))
    b = table.attach_node("loc_end013", rotation=(0.0, 1.0, 0.0, 0.0))
    c = table.attach_node("loc_start023_01", position=(0.35, 0.0, -0.47))
    d = table.attach_node("loc_end023_01", position=(0.35, 0.0, -0.47),
                          rotation=(0.0, 1.0, 0.0, 0.0))
    table.fixture_view([(a, b), (c, d)])

    chair = _Package("mysekai__fixture__mdl_chair")
    chair.script()
    e = chair.attach_node("loc_start012", position=(0.0, 0.3, 0.0))
    f = chair.attach_node("loc_end012")
    chair.fixture_view([(e, f)])

    frozen = _Package("mysekai__fixture__mdl_frozen")
    frozen.script()
    g = frozen.attach_node("loc_start013", position=(1.0, 0.0, 0.0))
    h = frozen.attach_node("loc_end013", rotation=(0.0, 1.0, 0.0, 0.0))
    i = frozen.attach_node("loc_end023_01")          # both ends point at this one
    j = frozen.attach_node("loc_start023_01")        # and this one is never referenced
    frozen.fixture_view([(g, h), (i, i)])

    return {table.name: table.finish(),
            chair.name: chair.finish(),
            frozen.name: frozen.finish()}


def _packages(document, name):
    return {pkg: entry for pkg, entry in document["packages"].items()
            if pkg == name}


def test_counts_and_ids_of_the_main_corpus(tmp_path, monkeypatch):
    """Red when a pair is dropped or a code is missed: the extractor must read
    every ``_attachPoints`` entry of every ``FixtureView`` behaviour."""
    result, document = _run(tmp_path, monkeypatch, _main_corpus())
    assert result["withFixtureView"] == 3
    assert result["pairs"] == 5
    assert result["packagesWithPairs"] == 3
    assert result["ids"] == {"count": 3, "values": ["012", "013", "023"]}
    assert len(document["packages"]) == 3


def test_a_self_referential_pair_is_reported_not_swallowed(tmp_path, monkeypatch):
    """Red when the planted corruption passes silently: a pair whose start and
    end name the same object must be listed as an anomaly and kept, not dropped."""
    result, document = _run(tmp_path, monkeypatch, _main_corpus())
    frozen = document["packages"]["mysekai__fixture__mdl_frozen"]
    pairs = [entry for entry in frozen["entries"] if entry["id"] == "023"]
    assert len(pairs) == 1
    types = {anomaly["type"] for anomaly in pairs[0]["anomalies"]}
    assert "self-reference" in types
    assert "start-not-pattern" in types
    assert pairs[0]["start"]["name"] == "loc_end023_01"
    assert result["anomalies"]["selfReferences"] == 1


def test_an_unreferenced_attach_named_object_is_reported(tmp_path, monkeypatch):
    """Red when the planted orphan is silently ignored: an attach-named game
    object no pair references must be listed on the package, never dropped."""
    result, document = _run(tmp_path, monkeypatch, _main_corpus())
    frozen = document["packages"]["mysekai__fixture__mdl_frozen"]
    assert {"type": "unreferenced-attach", "names": ["loc_start023_01"]} \
        in frozen["anomalies"]
    assert result["anomalies"]["unreferenced"] == 1


def test_a_code_or_suffix_mismatch_is_reported(tmp_path, monkeypatch):
    """Red when names are normalised instead of reported: a pair whose codes or
    suffixes differ must be flagged, with both names kept as authored."""
    pkg = _Package("mysekai__fixture__mdl_mismatch")
    pkg.script()
    a = pkg.attach_node("loc_start011_01")
    b = pkg.attach_node("loc_end013_01")
    c = pkg.attach_node("loc_start012_01")
    d = pkg.attach_node("loc_end012_02")
    pkg.fixture_view([(a, b), (c, d)])
    _result, document = _run(tmp_path, monkeypatch,
                             {pkg.name: pkg.finish()})
    entries = document["packages"]["mysekai__fixture__mdl_mismatch"]["entries"]
    assert {anomaly["type"] for anomaly in entries[0]["anomalies"]} \
        == {"id-mismatch"}
    assert {anomaly["type"] for anomaly in entries[1]["anomalies"]} \
        == {"suffix-mismatch"}
    assert entries[0]["id"] == "013"          # the code comes from the end side
    assert entries[1]["suffix"] == "02"


def test_transforms_are_rounded_to_six_decimals(tmp_path, monkeypatch):
    """Red when floats are written verbatim: the exact float the editor stored
    (e.g. ``0.3499999940395355``) is an approximation artifact and must round to
    the authored decimal; a tiny epsilon must become zero, not negative zero."""
    pkg = _Package("mysekai__fixture__mdl_rounding")
    pkg.script()
    a = pkg.attach_node("loc_start013", position=(-0.3499999940395355, 0.0, -0.47))
    b = pkg.attach_node("loc_end013",
                        rotation=(6.123234262925839e-17, 1.0,
                                  -6.123234262925839e-17, -6.123234262925839e-17))
    pkg.fixture_view([(a, b)])
    _result, document = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    entry = document["packages"]["mysekai__fixture__mdl_rounding"]["entries"][0]
    assert entry["start"]["transform"]["position"] == [-0.35, 0.0, -0.47]
    assert entry["end"]["transform"]["rotation"] == [0.0, 1.0, 0.0, 0.0]


def test_a_dangling_pointer_is_reported(tmp_path, monkeypatch):
    """Red when a broken pointer is silently null: an ``_attachPoints`` pointer
    to a path id that exists nowhere must be reported as unresolved, with the
    side kept in place rather than the pair dropped."""
    pkg = _Package("mysekai__fixture__mdl_dangling")
    pkg.script()
    a = pkg.attach_node("loc_start013")
    b = pkg.attach_node("loc_end013")
    pkg.fixture_view([(a, b)])
    pkg.add("MonoBehaviour",
            {"m_GameObject": {"m_FileID": 0, "m_PathID": 0},
             "m_Enabled": 1, "m_Script": {"m_FileID": 0, "m_PathID": SCRIPT_ID},
             "m_Name": "", "_attachPoints": [
                 {"StartLoc": {"m_FileID": 0, "m_PathID": 424242},
                  "EndLoc": {"m_FileID": 0, "m_PathID": 0},
                  "IsUsing": 0, "CharacterId": 0}]})
    _result, document = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    entry = document["packages"]["mysekai__fixture__mdl_dangling"]["entries"][1]
    types = {anomaly["type"] for anomaly in entry["anomalies"]}
    assert "unresolved" in types and "null" in types
    assert entry["start"]["name"] is None
    assert entry["start"]["reason"] == UNRESOLVED_POINTER


def test_a_package_without_the_behaviour_has_no_entries(tmp_path, monkeypatch):
    """Red if a package without a ``FixtureView`` behaviour were given empty
    entries or were counted as a package with pairs."""
    pkg = _Package("mysekai__fixture__mdl_bare")
    pkg.attach_node("loc_start013")
    pkg.attach_node("loc_end013")
    _result, document = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()})
    entry = document["packages"]["mysekai__fixture__mdl_bare"]
    assert entry["hasFixtureView"] is False
    assert entry["entries"] == []
    assert entry["anomalies"] == []


def test_without_master_tables_the_gap_is_reported_unavailable(tmp_path, monkeypatch):
    """Red if the gap were invented or silently omitted: without the reference
    table the mismatch cannot be computed and must be reported as unavailable,
    alongside the still-valid extraction counts."""
    result, document = _run(tmp_path, monkeypatch, _main_corpus())
    assert result["pairs"] == 5
    assert result["unresolvedIds"]["available"] is False
    assert result["unresolvedIds"]["reason"] == "no master directory supplied"
    assert "unresolvedIds" in document["summary"]


def test_a_missing_bundle_file_is_reported(tmp_path, monkeypatch):
    """Red when a bundle whose file is absent is opened into silence or given an
    empty result: without a fixture file the loader yields zero objects, so the
    extractor must check the file exists itself and report the reason."""
    pkg = _Package("mysekai__fixture__mdl_lost")
    pkg.script()
    _result, document = _run(tmp_path, monkeypatch, {pkg.name: pkg.finish()},
                             touch=False)
    assert document["packages"]["mysekai__fixture__mdl_lost"]["anomalies"] == \
        [{"type": "bundle-not-found", "detail": MISSING_BUNDLE}]


def test_a_missing_reference_table_is_reported(tmp_path, monkeypatch):
    """Red when a missing table looks like an empty domain: ``MissingTable``
    must surface as the reason, not as a silent zero."""
    result, _document = _run(tmp_path, monkeypatch, _main_corpus(), master=[],
                             missing_table=True)
    assert result["unresolvedIds"]["available"] is False
    assert "master table not found" in result["unresolvedIds"]["reason"]


def test_the_reference_domain_gap(tmp_path, monkeypatch):
    """Red when the gap between the reference domain and the corpus is filled
    with defaults: the codes that appear in the reference table but in no pair
    are listed as unresolved, with the reason stated, and never guessed."""
    domain = [{"gameCharacterUnitId1ActionPoint": value}
              for value in (11, 12, 13, 14, 15)]
    result, _document = _run(tmp_path, monkeypatch, _main_corpus(), master=domain)
    gap = result["unresolvedIds"]
    assert gap["available"] is True
    assert gap["domain"] == 5
    assert gap["found"] == 2
    assert gap["missing"] == 3
    assert gap["missingValues"] == ["011", "014", "015"]
    assert "cause is not established" in gap["reason"]
