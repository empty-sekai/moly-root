"""Fixture area arrays: the three ``D2Array<bool>`` a furniture ships.

A furniture bundle carries one ``FixtureBundleMeta`` ScriptableObject (the
``fixture_metadata.asset`` inside the bundle, never in the master table).  Besides
the scalar ``stackHeight`` it holds four ``D2Array<bool>`` — ``stackEnables``,
``motionArea``, ``cutsceneArea`` and ``AddUsingGrid`` — each of which becomes an
independent ``GridAreaData``.  This module reads those arrays and nothing else
about the package.

The arrays are read as the matrix they actually are.  A typetree element of type
``D2Array`` serialises as ``{"array": [{"columns": [...]}, {"columns": [...]}, …]}``
— the row dict's key is ``columns``, not ``array``.  Reading the inner key as
``array`` yields an empty list for every row, so the array silently reads as
empty: no error, every grid zero, and a run "succeeds" while reporting nothing.
The extractor therefore reads ``columns``, and keeps two kinds of nonconformance
apart so neither can be silently swallowed: rows whose ``columns`` lengths differ
are ``ragged``, and rows that carry no ``columns`` key at all (a row dict keyed
``array``, say) are counted in ``rowsWithoutColumns`` and flagged in ``anomaly``.
A uniform array of such rows — every row mis-keyed — is caught by that flag, not
"fixed" by being reported as ragged (it is not ragged: the lengths are equal), and
never padded, truncated, or dropped.

The array dimension is not the occupied footprint.  Only the cells that are
``true`` become ``GridAreaCell`` entries in the ``EnableTileList``; a ``false``
cell generates nothing.  So every array is reported with both the dimension and
the bounding box of its ``true`` cells, and the two are kept apart — for the
birthday cake's ``cutsceneArea`` the dimension is 18x8 but the bounding box is
12x8, and the two must not be conflated.
"""
import os
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore
from core.jsonio import write_json

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

FIXTURE_PREFIX = "mysekai__fixture__"
META_CONTAINER = "fixture_metadata"
ARRAY_KEYS = ("stackEnables", "motionArea", "cutsceneArea", "AddUsingGrid")

MISSING_BUNDLE = "bundle file not found; it was not opened"
NO_META = "no fixture_metadata asset in this bundle"
READ_FAILED = "fixture_metadata typetree could not be read"

SEMANTICS = {
    "stackHeight": ("the scalar serialized field of ``FixtureBundleMeta``, rounded "
                    "to four decimals, as authored"),
    "arrays": ("each of the four ``D2Array<bool>`` fields, read as a matrix of "
               "booleans and reported with its dimension, the count of ``true`` "
               "cells, the bounding box of those cells (kept separate from the "
               "dimension), and the cells themselves"),
    "dims": ("the array dimension as (rows, columns) of the serialized matrix — "
             "for a ragged array the column count is the widest row, and the "
             "raggedness is reported separately"),
    "bbox": ("the bounding box of the ``true`` cells — the smallest rectangle that "
             "covers them, as start/end row and column and its own row/column "
             "extent; it is ``None`` when the array is empty or has no ``true`` "
             "cell.  The dimension and the bounding box often differ"),
    "true": ("the number of ``true`` cells in the array — the cells that become "
             "``GridAreaCell`` entries, which is the footprint; the rest of the "
             "matrix is an authored canvas and produces nothing"),
    "ragged": ("row dictionaries whose ``columns`` lengths differ (or whose "
               "``columns`` key is absent — e.g. a row dict keyed ``array``: when that "
               "row is the odd one out its differing length is caught here too). "
               "Reported per row, never padded, truncated, or dropped"),
    "raggedRows": ("the rows that differ from the widest row, as ``{row, columns}`` "
                   "where ``columns`` is that row's actual length"),
    "rowsWithoutColumns": ("the number of rows whose dict carries no ``columns`` key "
                            "(a row dict keyed ``array``, say) — rows that read as zero "
                            "columns.  This is distinct from ragged: a uniform array of "
                            "such rows is not ragged, and is caught here"),
    "anomaly": ("``rows-without-columns`` when any row lacked a ``columns`` key, "
                 "else ``None``"),
}


def _grid(raw):
    """One ``D2Array<bool>`` as a rectangular grid with its occupied-cell profile.

    *raw* is the typetree dict of the field.  The rows come from its ``array`` key;
    each row is a dict whose cells come from its ``columns`` key.  A row that does
    not carry a ``columns`` key (a row dict keyed ``array``, say) reads as zero
    columns and is counted in ``rowsWithoutColumns`` and flagged in ``anomaly`` —
    a uniform array of such rows is still flagged, never silently treated as an
    empty-but-valid grid, never padded, truncated, or dropped.
    """
    rows = (raw or {}).get("array") or []
    cells = []
    column_counts = []
    rows_without_columns = 0
    for row in rows:
        columns = row.get("columns") if isinstance(row, dict) else None
        if not isinstance(columns, list):
            columns = []
            rows_without_columns += 1
        column_counts.append(len(columns))
        cells.append([bool(value) for value in columns])

    width = max(column_counts) if column_counts else 0
    ragged = len(set(column_counts)) > 1
    ragged_rows = [{"row": index, "columns": length}
                   for index, length in enumerate(column_counts)
                   if length != width]

    true_cells = [(row_index, col_index)
                  for row_index, row in enumerate(cells)
                  for col_index, value in enumerate(row) if value]
    grid = {
        "dims": {"rows": len(cells), "cols": width},
        "true": len(true_cells),
        "bbox": None,
        "cells": cells,
        "ragged": ragged,
        "raggedRows": ragged_rows,
        "rowsWithoutColumns": rows_without_columns,
        "anomaly": "rows-without-columns" if rows_without_columns else None,
        "columnCounts": column_counts,
    }
    if true_cells:
        row_indexes = [rc[0] for rc in true_cells]
        col_indexes = [rc[1] for rc in true_cells]
        grid["bbox"] = {
            "rowStart": min(row_indexes), "rowEnd": max(row_indexes),
            "colStart": min(col_indexes), "colEnd": max(col_indexes),
            "rows": max(row_indexes) - min(row_indexes) + 1,
            "cols": max(col_indexes) - min(col_indexes) + 1,
        }
    return grid


def _empty(has_meta, reason=None):
    """A per-bundle record for a package with no readable meta."""
    return {"hasMeta": has_meta, "readError": reason, "stackHeight": None,
            **{key: None for key in ARRAY_KEYS}}


def _extract_one(package):
    """One furniture package's meta: its array profile, or why there is none.

    *package* is a ``Package`` from ``PackageStore.package``.  The meta is the
    ``MonoBehaviour`` whose container path names ``fixture_metadata``; a package
    without one (either no asset or no fixture file) is reported as absent rather
    than guessed, and a typetree that cannot be read is reported as a read error.
    """
    for file in package.files:
        for path_id, obj in file.objects.items():
            if obj.type.name != "MonoBehaviour":
                continue
            if META_CONTAINER not in (getattr(obj, "container", "") or ""):
                continue
            try:
                tree = obj.read_typetree()
            except Exception as exc:  # pragma: no cover - depends on the asset
                return _empty(True, f"{READ_FAILED}: {exc}")
            out = {"hasMeta": True, "readError": None,
                   "stackHeight": round(float(tree.get("stackHeight", 0.0)), 4)}
            for key in ARRAY_KEYS:
                out[key] = _grid(tree.get(key))
            return out
    return _empty(False, NO_META)


def aggregate(store):
    """The per-bundle array profile of every furniture package *store* holds."""
    packages = {}
    non_empty = {key: 0 for key in ARRAY_KEYS}
    read_failures = 0
    stack_height_zero = 0
    ragged_total = 0
    rows_without_columns = 0
    names = _list(store)

    for name in names:
        path = store.paths.get(name, "")
        if not path or not os.path.exists(path):
            packages[name] = _empty(False, MISSING_BUNDLE)
            continue
        package = store.package(name)
        if package is None:
            packages[name] = _empty(False, MISSING_BUNDLE)
            continue
        out = _extract_one(package)
        packages[name] = out
        if not out["hasMeta"]:
            continue
        if out["readError"]:
            read_failures += 1
            continue
        if out["stackHeight"] == 0.0:
            stack_height_zero += 1
        for key in ARRAY_KEYS:
            grid = out[key]
            if grid.get("dims", {}).get("rows") or grid.get("dims", {}).get("cols"):
                non_empty[key] += 1
            if grid.get("ragged"):
                ragged_total += len(grid.get("raggedRows") or [])
            rows_without_columns += grid.get("rowsWithoutColumns", 0)

    summary = {
        "bundles": len(names),
        "withMeta": sum(1 for out in packages.values() if out["hasMeta"]),
        "readFailures": read_failures,
        "nonEmptyCounts": non_empty,
        "stackHeightZero": stack_height_zero,
        "raggedTotal": ragged_total,
        "rowsWithoutColumns": rows_without_columns,
    }
    return summary, packages


def extract_from_store(store, out_dir):
    """Extract every furniture package's area arrays into ``out_dir``.

    *store* is a ``PackageStore`` of the bundle files to read.  A bundle whose file
    is not on disk is reported as ``bundle-not-found`` rather than opened, because
    the loader answers a missing path with zero objects and silence.
    """
    out = Path(out_dir)
    summary, packages = aggregate(store)
    document = {
        "version": 1,
        "semantics": SEMANTICS,
        "summary": summary,
        "packages": packages,
    }
    path = write_json(out / "areas.json", document)
    return dict(summary, path=str(path))


def extract_areas(store, out_dir):
    """Alias kept for the fixture interface's naming pattern."""
    return extract_from_store(store, out_dir)


def _list(store):
    """The package names the store holds, in the order they will be extracted."""
    return sorted(name for name in store.paths if name.startswith(FIXTURE_PREFIX))
