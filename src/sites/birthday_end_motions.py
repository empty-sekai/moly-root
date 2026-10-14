"""Export the waving motion the birthday party plays as it closes.

When the party's closing talk is over, the character waves: the client loads
one asset -- the list this module reads -- finds the row whose ``CharacterId``
matches the party's character unit, and plays that row's motion with its eye
and mouth patterns.  The list is a single ScriptableObject in a package of its
own, so there is nothing to derive it from: the row a character gets is in this
asset or nowhere.

The rows ride through verbatim and in source order, and ``byCharacterId`` is
the index a consumer looks a row up by, built the way the client looks one up
(in source order, so the low row wins a repeated id).  A repeated id is counted
rather than merged away: the client would silently keep the first, and a
snapshot that has two rows for one character is a fact about that snapshot.

``MotionName`` is kept as the empty string when the asset says so.  Whether the
engine plays anything for an empty name was not read; what was read is that the
field is present and empty, so it is exported as the empty string rather than
dropped -- a missing key would say the asset never wrote the field, which is a
different fact.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import UnityPy

from core.jsonio import write_json
from core.unity import configure_fallback_unity_version

# The decrypted packages carry no Unity version in their headers, so a load
# without a configured version raises rather than reading.  This module has an
# entry point of its own, the same precedent as the other two that do.
configure_fallback_unity_version()

PACKAGE = "mysekai__birthday__birthday_end_character_motion_data_list"
ASSET_NAME = "BirthdayEndCharacterMotionDataList"
ROW_FIELD = "BirthdayEndCharacterMotionDatas"
SCHEMA = "moly-birthday-end-character-motions/1"


def _row(index: int, row: Any) -> dict[str, Any]:
    """One source row, verbatim, after checking the fields a consumer reads."""
    if not isinstance(row, dict):
        raise ValueError(f"{ROW_FIELD}[{index}] must be an object")
    character = row.get("CharacterId")
    if isinstance(character, bool) or not isinstance(character, int):
        raise ValueError(f"{ROW_FIELD}[{index}].CharacterId: expected an "
                         f"integer character unit, got {character!r}")
    kept = {"CharacterId": character}
    for field in ("MotionName", "EyeName", "MouthName"):
        value = row.get(field)
        if not isinstance(value, str):
            raise ValueError(f"{ROW_FIELD}[{index}].{field}: expected a string, "
                             f"got {value!r}")
        kept[field] = value
    return kept


def parse_motion_list(tree: dict[str, Any]) -> dict[str, Any]:
    """Validate one motion-list typetree and make runtime JSON.

    Source row order is preserved exactly.  The index keeps the first row of a
    repeated character id -- the order the client's own lookup reads -- and the
    summary counts the repetitions, so the choice is a number rather than a
    rule a reader has to infer from the absence of a row.
    """
    if not isinstance(tree, dict):
        raise ValueError("birthday motion list typetree must be an object")
    rows = tree.get(ROW_FIELD)
    if not isinstance(rows, list):
        raise ValueError(f"birthday motion list lacks the {ROW_FIELD} array")
    parsed = [_row(index, row) for index, row in enumerate(rows)]
    by_character: dict[str, dict[str, Any]] = {}
    repeated = []
    for row in parsed:
        key = str(row["CharacterId"])
        if key in by_character:
            repeated.append(row["CharacterId"])
            continue
        by_character[key] = row
    return {
        "schema": SCHEMA,
        "source": {"package": PACKAGE, "asset": ASSET_NAME},
        "semantics": {
            "rows": (
                f"{ROW_FIELD} verbatim in source order; each row is "
                "{CharacterId, MotionName, EyeName, MouthName}"
            ),
            "characterId": (
                "the field is named CharacterId but the client compares it with "
                "the party's gameCharacterUnitId, so it is keyed that way here"
            ),
            "byCharacterId": (
                "the row a character is looked up by, in source order; a "
                "repeated CharacterId keeps the first row, the same choice the "
                "client's own lookup makes, and the repetitions are counted in "
                "summary.repeatedRows"
            ),
            "emptyMotionName": (
                "an empty MotionName is the value the asset carries, kept as an "
                "empty string rather than dropped or nulled"
            ),
        },
        "summary": {
            "rows": len(parsed),
            "characters": len(by_character),
            "repeatedRows": len(repeated),
            "emptyMotionNames": sum(1 for row in parsed if row["MotionName"] == ""),
        },
        "rows": parsed,
        "byCharacterId": by_character,
    }


def extract_birthday_end_motions(bundle: str | Path, out_path: str | Path) -> dict[str, Any]:
    """Read the exact source package and write ``birthday-end-character-motions.json``."""
    bundle = Path(bundle)
    if not bundle.is_file():
        raise FileNotFoundError(f"birthday motion list bundle not found: {bundle}")
    env = UnityPy.load(str(bundle))
    candidates: list[tuple[Any, dict[str, Any]]] = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        tree = obj.read_typetree()
        if tree.get("m_Name") == ASSET_NAME or ROW_FIELD in tree:
            candidates.append((obj, tree))
    if len(candidates) != 1:
        raise ValueError(f"{PACKAGE}: expected exactly one {ASSET_NAME!r} "
                         f"MonoBehaviour, got {len(candidates)}")
    obj, tree = candidates[0]
    document = parse_motion_list(tree)
    document["source"].update({
        "pathId": int(obj.path_id),
        "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
    })
    path = write_json(out_path, document)
    return {
        "json": str(path),
        "rows": document["summary"]["rows"],
        "characters": document["summary"]["characters"],
        "repeatedRows": document["summary"]["repeatedRows"],
    }
