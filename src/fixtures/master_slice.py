"""Slice the fixture master table down to the columns the runtime reads.

Three of this table's columns decide whether a placed fixture gets a proximity
action button, and no other source carries them:

- ``mysekaiFixturePlayerActionType`` -- the gimmick / timeline classification.
  The client asks two questions of it.  "Is this a gimmick fixture" is
  ``(value & ~2) == 1``, i.e. true for ``loop`` and ``one_shot``.  "Is this a
  timeline fixture" is ``value == 2``, i.e. ``timeline``.  A ``no_action``
  fixture is neither.
- ``mysekaiFixtureType`` -- the fallback gate.  A fixture that is neither
  gimmick nor timeline only gets a button when this is ``system`` or ``gate``.
- ``gridSize`` -- the footprint, which sets the half extents of the fixture
  collision box the proximity test runs against.

``assetbundleName`` is kept as the join key: the runtime addresses fixture
models by package name, which is this value behind a fixed prefix.

The whole table is 952 rows and most columns are inventory presentation, so
this writes the slice rather than a copy.  Adding a column here is cheaper
than teaching the runtime to read a table it does not need.
"""
from __future__ import annotations

from typing import Any

from core.jsonio import write_json
from core.master import Master

# Identity of the master mirror this toolchain is pinned to; both go into the
# document so a consumer can tell which region and game version it came from.
REGION = "cn"
GAME_VERSION = "6.0.0"

_COLUMNS = (
    "id",
    "assetbundleName",
    "mysekaiFixtureType",
    "mysekaiFixturePlayerActionType",
    "mysekaiFixturePutType",
    "mysekaiFixtureHandleType",
    "gridSize",
)

# Values as they are spelled in the table, with the integers the client
# compares against.  Kept explicit so a new value shows up as an error rather
# than as a fixture that quietly stops being interactive.
_ACTION_TYPES = {"no_action": 0, "loop": 1, "timeline": 2, "one_shot": 3}
_FIXTURE_TYPES = {
    "system": 0,
    "custom": 1,
    "plant": 2,
    "house_plant": 3,
    "surface_appearance": 4,
    "gate": 5,
    "normal": 6,
    "canvas": 7,
}


def export_fixture_master_slice(
    source: str,
    out_path: str,
    *,
    master_cache: str | None = None,
    region: str = REGION,
    game_version: str = GAME_VERSION,
) -> dict[str, Any]:
    """Read ``mysekaiFixtures`` from *source* and write the slice as one document."""
    rows = Master(source, cache_dir=master_cache).table("mysekaiFixtures")
    if not isinstance(rows, list) or not rows:
        raise ValueError("mysekaiFixtures: expected a non-empty array of rows")

    out: list[dict[str, Any]] = []
    for row in rows:
        missing = [column for column in _COLUMNS if column not in row]
        if missing:
            raise ValueError(f"row id={row.get('id')!r} lacks {missing}")
        action = row["mysekaiFixturePlayerActionType"]
        if action not in _ACTION_TYPES:
            raise ValueError(f"row id={row['id']!r}: unknown player action type {action!r}")
        kind = row["mysekaiFixtureType"]
        if kind not in _FIXTURE_TYPES:
            raise ValueError(f"row id={row['id']!r}: unknown fixture type {kind!r}")
        grid = row["gridSize"]
        for axis in ("width", "depth", "height"):
            if axis not in grid:
                raise ValueError(f"row id={row['id']!r}: gridSize lacks {axis}")
        out.append(
            {
                "id": int(row["id"]),
                "assetbundleName": str(row["assetbundleName"]),
                "fixtureType": kind,
                "fixtureTypeValue": _FIXTURE_TYPES[kind],
                "playerActionType": action,
                "playerActionTypeValue": _ACTION_TYPES[action],
                "putType": row["mysekaiFixturePutType"],
                "handleType": row["mysekaiFixtureHandleType"],
                "gridWidth": int(grid["width"]),
                "gridDepth": int(grid["depth"]),
                "gridHeight": int(grid["height"]),
            }
        )
    out.sort(key=lambda record: record["id"])

    counts: dict[str, int] = {}
    for record in out:
        counts[record["playerActionType"]] = counts.get(record["playerActionType"], 0) + 1
    kinds: dict[str, int] = {}
    for record in out:
        kinds[record["fixtureType"]] = kinds.get(record["fixtureType"], 0) + 1

    document = {
        "version": 1,
        "region": region,
        "gameVersion": game_version,
        "summary": {
            "rows": len(out),
            "playerActionType": counts,
            "fixtureType": kinds,
        },
        "fixtures": out,
    }
    path = write_json(out_path, document, indent=2)
    return {"version": 1, "output": str(path), "summary": document["summary"]}
