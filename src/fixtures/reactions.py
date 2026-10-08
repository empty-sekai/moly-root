"""Export the native MySekai furniture reaction-character catalogue.

The client does not derive this screen from talk scripts.  It loads the exact
``mysekai/system/fixture_reaction_data`` asset and ``FixtureReactionDescription``
iterates its ``FixtureReactionDataSet`` rows in source order.  Each furniture row
contains one or more character-unit groups (one-, two-, three-, four-person,
etc.) that are rendered as CharacterBand entries in the native detail dialog.

Keep this artifact deliberately narrow: it is the source list used by that UI,
not a reconstructed catalogue and not a statement that every group has an
authored conversation in every region/version.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import UnityPy

from core.jsonio import write_json

PACKAGE = "mysekai__system__fixture_reaction_data"
ASSET_NAME = "fixture_reaction_data"
SCHEMA = "moly-fixture-reactions/1"


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field}: expected a positive integer, got {value!r}")
    return value


def parse_fixture_reactions(tree: dict[str, Any]) -> dict[str, Any]:
    """Validate one ``FixtureReactionDataSet`` typetree and make runtime JSON.

    Source row order and per-row group order are preserved exactly.  IDs are not
    sorted or deduplicated: a duplicate is source corruption and therefore red.
    """
    if not isinstance(tree, dict):
        raise ValueError("fixture reaction typetree must be an object")
    rows = tree.get("FixturerRactions")
    if not isinstance(rows, list):
        raise ValueError("fixture reaction data lacks FixturerRactions array")

    fixtures: list[dict[str, Any]] = []
    fixture_ids: set[int] = set()
    group_count = 0
    max_participants = 0
    units: set[int] = set()

    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"FixturerRactions[{row_index}] must be an object")
        fixture_id = _positive_int(row.get("FixtureId"), f"FixturerRactions[{row_index}].FixtureId")
        if fixture_id in fixture_ids:
            raise ValueError(f"duplicate fixture reaction row: {fixture_id}")
        fixture_ids.add(fixture_id)

        raw_groups = row.get("ReactionCharacter")
        if not isinstance(raw_groups, list):
            raise ValueError(f"fixture {fixture_id}: ReactionCharacter must be an array")
        groups: list[list[int]] = []
        seen_groups: set[tuple[int, ...]] = set()
        for group_index, group in enumerate(raw_groups):
            if not isinstance(group, dict):
                raise ValueError(f"fixture {fixture_id} group {group_index}: expected an object")
            raw_units = group.get("CharacterUnitIds")
            if not isinstance(raw_units, list) or not raw_units:
                raise ValueError(f"fixture {fixture_id} group {group_index}: CharacterUnitIds must be non-empty")
            member_ids = [
                _positive_int(value, f"fixture {fixture_id} group {group_index} unit")
                for value in raw_units
            ]
            if len(set(member_ids)) != len(member_ids):
                raise ValueError(f"fixture {fixture_id} group {group_index}: duplicate character unit")
            identity = tuple(member_ids)
            if identity in seen_groups:
                raise ValueError(f"fixture {fixture_id}: duplicate reaction group {member_ids}")
            seen_groups.add(identity)
            groups.append(member_ids)
            units.update(member_ids)
            group_count += 1
            max_participants = max(max_participants, len(member_ids))

        fixtures.append({"fixtureId": fixture_id, "groups": groups})

    return {
        "schema": SCHEMA,
        "source": {"package": PACKAGE, "asset": ASSET_NAME},
        "summary": {
            "fixtures": len(fixtures),
            "groups": group_count,
            "characterUnits": len(units),
            "maxParticipants": max_participants,
        },
        "fixtures": fixtures,
    }


def extract_fixture_reactions(bundle: str | Path, out_path: str | Path) -> dict[str, Any]:
    """Read the exact source bundle and write ``fixture-reactions.json``."""
    bundle = Path(bundle)
    if not bundle.is_file():
        raise FileNotFoundError(f"fixture reaction bundle not found: {bundle}")

    env = UnityPy.load(str(bundle))
    candidates: list[tuple[Any, dict[str, Any]]] = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        tree = obj.read_typetree()
        if tree.get("m_Name") == ASSET_NAME or "FixturerRactions" in tree:
            candidates.append((obj, tree))
    if len(candidates) != 1:
        raise ValueError(
            f"{PACKAGE}: expected exactly one {ASSET_NAME!r} MonoBehaviour, got {len(candidates)}"
        )

    obj, tree = candidates[0]
    document = parse_fixture_reactions(tree)
    document["source"].update(
        {
            "pathId": int(obj.path_id),
            "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        }
    )
    path = write_json(out_path, document, indent=2)
    return {
        "json": str(path),
        "fixtures": document["summary"]["fixtures"],
        "groups": document["summary"]["groups"],
        "characterUnits": document["summary"]["characterUnits"],
        "maxParticipants": document["summary"]["maxParticipants"],
    }
