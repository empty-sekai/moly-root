"""Fixture area arrays: what the extractor must report, and what makes it fail.

The fixtures here are synthetic.  A real furniture bundle is not in this
repository, so the checks are written against a small corpus built in-process.
Several tests plant a *wrong* array on purpose and assert the extractor reports it
rather than silently swallowing it — a row dict keyed ``array`` instead of
``columns``, a ragged row, and a bounding box that must not be conflated with the
array dimension.  A criterion that cannot go red is not a criterion.
"""
import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.assets import packages as packages_module
from core.assets.packages import PackageStore
from fixtures.areas import (
    MISSING_BUNDLE, NO_META, _grid, aggregate,
)

SCRIPT_ID = 9001
META_CONTAINER = "assets/sekai/assetbundle/resources/ondemand/mysekai/fixture"


class _AssetFile:
    def __init__(self, name, externals=()):
        self.name = name
        self.externals = [SimpleNamespace(name=e) for e in externals]


class _Object:
    def __init__(self, kind, path_id, tree, asset_file, container=""):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self._tree = tree
        self.assets_file = asset_file
        self.container = container

    def read_typetree(self):
        return self._tree


class _Package:
    """A furniture package under construction: objects and one meta behaviour."""

    def __init__(self, name):
        self.name = name
        self.archive = _AssetFile(f"CAB-{name}")
        self.objects = []
        self._next = 1000

    def _id(self):
        self._next += 1
        return self._next

    def add(self, kind, tree, container="", path_id=None):
        path_id = self._id() if path_id is None else path_id
        self.objects.append(_Object(kind, path_id, tree, self.archive, container))
        return path_id

    def meta(self, tree, container=META_CONTAINER):
        """The ``fixture_metadata`` ScriptableObject, with the container path."""
        return self.add("MonoBehaviour",
                        {"m_GameObject": {"m_FileID": 0, "m_PathID": 0},
                         "m_Enabled": 1, "m_Script": {"m_FileID": 0,
                                                      "m_PathID": SCRIPT_ID},
                         "m_Name": "", **tree},
                        container=f"{container}/fixture_metadata.asset")

    def finish(self):
        return SimpleNamespace(objects=self.objects)


def _run(bundles, missing=()):
    """Aggregate *bundles* (name -> finished ``_Package``) as a synthetic store."""
    store = PackageStore([f"bundle/{name}" for name in sorted(bundles)])
    with mock.patch.object(
            packages_module.UnityPy, "load",
            lambda path: bundles[os.path.basename(str(path))]):
        with mock.patch.object(os.path, "exists",
                               lambda p: os.path.basename(str(p)) not in missing):
            summary, packages = aggregate(store)
    return summary, {"summary": summary, "packages": packages}


def _rect(rows, cols, value):
    """A rectangular array with every cell set to *value*."""
    return {"array": [{"columns": [value] * cols} for _ in range(rows)]}


def _cake_cutscene():
    """The birthday cake's 18x8 cutscene area: a 4x4 hole at row 7..10 col 2..5."""
    cells = []
    for row in range(18):
        if row <= 6:
            cells.append([True] * 8)
        elif 7 <= row <= 10:
            cells.append([True, True, False, False, False, False, True, True])
        elif row == 11:
            cells.append([True] * 8)
        else:
            cells.append([False] * 8)
    return {"array": [{"columns": row} for row in cells]}


def test_grid_reads_the_columns_key_not_array():
    """Red when the row dict is keyed ``array``: the ``D2Array`` serialises each
    row as ``{"columns": [...]}``, and reading the inner key as ``array`` silently
    yields an empty row — so every grid reads zero while the run "succeeds"."""
    grid = _grid({"array": [{"columns": [True, False, True]},
                            {"columns": [False, True, False]}]})
    assert grid["dims"] == {"rows": 2, "cols": 3}
    assert grid["true"] == 3
    assert grid["bbox"] == {"rowStart": 0, "rowEnd": 1, "colStart": 0,
                            "colEnd": 2, "rows": 2, "cols": 3}
    assert all(len(row) == 3 for row in grid["cells"])


def test_a_row_keyed_array_is_reported_as_ragged_not_swallowed():
    """Red when a malformed row is silently dropped: a row whose dict does not
    carry a ``columns`` key reads as zero columns and must be listed, never
    padded, truncated, or counted as if it matched its neighbours."""
    grid = _grid({"array": [{"columns": [True, True, True]},
                            {"array": [True, True, True, True]},
                            {"columns": [True, True, True]}]})
    assert grid["ragged"] is True
    assert grid["raggedRows"] == [{"row": 1, "columns": 0}]
    assert grid["columnCounts"] == [3, 0, 3]
    assert len(grid["cells"][1]) == 0


def test_all_rows_keyed_array_are_flagged():
    """Red when a uniformly mis-keyed array is silently swallowed: if every row
    dict is keyed ``array`` (not ``columns``) the row lengths are equal, so it is
    not ragged — but the rows carry no column data at all.  It must be flagged in
    ``anomaly`` and counted in ``rowsWithoutColumns``, not left as a "rows yet no
    columns" grid that still counts as non-empty."""
    grid = _grid({"array": [{"array": [True, True]},
                            {"array": [True, False]},
                            {"array": [False, True]}]})
    assert grid["ragged"] is False            # uniform lengths — not ragged
    assert grid["rowsWithoutColumns"] == 3
    assert grid["anomaly"] == "rows-without-columns"
    assert grid["dims"] == {"rows": 3, "cols": 0}


def test_ragged_rows_are_listed_never_padded_or_truncated():
    """Red when a ragged row is normalised: rows whose ``columns`` lengths differ
    must be reported per row and kept at their authored length, not padded to the
    widest row or truncated to a nominal one."""
    grid = _grid({"array": [{"columns": [True, True, True, True]},
                            {"columns": [True, False]},
                            {"columns": [True, True, True, False]}]})
    assert grid["ragged"] is True
    assert grid["raggedRows"] == [{"row": 1, "columns": 2}]
    assert [len(row) for row in grid["cells"]] == [4, 2, 4]
    assert grid["dims"]["cols"] == 4


def test_bbox_is_kept_apart_from_the_dimension():
    """Red when the bounding box is conflated with the dimension: the birthday
    cake's cutscene area is 18 rows by 8 columns, but its ``true`` cells occupy
    only a 12x8 box (the last six rows are empty).  Reporting the dimension as the
    occupied footprint, or vice versa, must fail."""
    grid = _grid(_cake_cutscene())
    assert grid["dims"] == {"rows": 18, "cols": 8}
    assert grid["true"] == 80
    assert grid["bbox"] == {"rowStart": 0, "rowEnd": 11, "colStart": 0,
                            "colEnd": 7, "rows": 12, "cols": 8}
    assert grid["dims"]["rows"] != grid["bbox"]["rows"]


def test_the_cakes_hole_is_a_4x4_ring_matching_its_grid_size():
    """Red when the cavity is misread: the cake's cutscene area is a hollow ring
    whose 16 ``false`` cells sit in a 4x4 block (row 7..10 x col 2..5), matching
    the cake's own 4x4 occupation — and the height does not participate."""
    grid = _grid(_cake_cutscene())
    hole = [(r, c) for r, row in enumerate(grid["cells"])
            for c, value in enumerate(row) if not value]
    assert len(hole) == 64
    inside = [(r, c) for r, c in hole if 0 <= r < 12]
    assert len(inside) == 16
    rows = {r for r, _ in inside}
    cols = {c for _, c in inside}
    assert rows == {7, 8, 9, 10}
    assert cols == {2, 3, 4, 5}


def test_a_package_without_meta_is_reported_absent(tmp_path, monkeypatch):
    """Red when a package with no ``fixture_metadata`` asset is given an empty
    array set or treated as if it had one: "has metadata" and "has area data"
    are different facts."""
    pkg = _Package("mysekai__fixture__mdl_bare")
    pkg.add("MonoBehaviour",
            {"m_GameObject": {"m_FileID": 0, "m_PathID": 0}, "m_Enabled": 1,
             "m_Script": {"m_FileID": 0, "m_PathID": SCRIPT_ID},
             "m_Name": "", "stackHeight": 0.0},
            container="some/other/asset")
    summary, doc = _run({pkg.name: pkg.finish()})
    entry = doc["packages"]["mysekai__fixture__mdl_bare"]
    assert entry["hasMeta"] is False
    assert entry["readError"] == NO_META
    assert entry["stackHeight"] is None
    assert all(entry[key] is None for key in
               ("stackEnables", "motionArea", "cutsceneArea", "AddUsingGrid"))
    assert summary["withMeta"] == 0


def test_an_empty_array_is_a_valid_present_meta(tmp_path, monkeypatch):
    """Red when a present-but-empty meta is reported as absent: a furniture can
    carry the metadata asset and still have all four arrays empty (zero rows)."""
    pkg = _Package("mysekai__fixture__mdl_balloon")
    pkg.meta({"stackHeight": 0.0,
              "stackEnables": {"array": []},
              "motionArea": {"array": []},
              "cutsceneArea": {"array": []},
              "AddUsingGrid": {"array": []}})
    summary, doc = _run({pkg.name: pkg.finish()})
    entry = doc["packages"]["mysekai__fixture__mdl_balloon"]
    assert entry["hasMeta"] is True
    assert entry["readError"] is None
    assert entry["stackHeight"] == 0.0
    for key in ("stackEnables", "motionArea", "cutsceneArea", "AddUsingGrid"):
        assert entry[key]["dims"] == {"rows": 0, "cols": 0}
        assert entry[key]["true"] == 0
    assert summary["withMeta"] == 1
    assert summary["nonEmptyCounts"] == {"motionArea": 0, "stackEnables": 0,
                                         "AddUsingGrid": 0, "cutsceneArea": 0}


def test_missing_bundle_file_is_reported(tmp_path, monkeypatch):
    """Red when a bundle whose file is absent is opened into silence: without the
    file the loader yields zero objects, so the extractor checks the path itself
    and says why there is no meta."""
    pkg = _Package("mysekai__fixture__mdl_lost")
    pkg.meta({"stackHeight": 0.0})
    summary, doc = _run({pkg.name: pkg.finish()}, missing=(pkg.name,))
    entry = doc["packages"]["mysekai__fixture__mdl_lost"]
    assert entry["hasMeta"] is False
    assert entry["readError"] == MISSING_BUNDLE


def test_aggregate_counts_nonempty_arrays_and_zero_stack_height():
    """Red when a non-empty array is miscounted: only arrays with at least one
    row count as non-empty, and ``stackHeight`` zero is tracked separately."""
    cake = _Package("mysekai__fixture__mdl_cake")
    cake.meta({"stackHeight": 0.0, "motionArea": {"array": []},
               "stackEnables": _rect(2, 2, True),
               "cutsceneArea": _cake_cutscene(), "AddUsingGrid": {"array": []}})
    balloon = _Package("mysekai__fixture__mdl_balloon")
    balloon.meta({"stackHeight": 0.25, "motionArea": _rect(6, 4, False),
                  "stackEnables": {"array": []}, "cutsceneArea": {"array": []},
                  "AddUsingGrid": {"array": []}})
    summary, _doc = _run({cake.name: cake.finish(), balloon.name: balloon.finish()})
    assert summary["withMeta"] == 2
    assert summary["readFailures"] == 0
    assert summary["nonEmptyCounts"] == {"motionArea": 1, "stackEnables": 1,
                                         "AddUsingGrid": 0, "cutsceneArea": 1}
    assert summary["stackHeightZero"] == 1
