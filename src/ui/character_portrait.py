"""Publish source SD character sprites for the browser's roster and cast lists.

CharacterSDIconAtlas contains ``chr_sp_{mysekaiGameCharacterUnitId}``, including
the separate Virtual Singer incarnations. Joining through gameCharacterId would
collapse those distinct portraits. The existing atlas decoder preserves the
authored packing rotation, tight mesh and transparency; this exporter neither
redraws nor resizes the resulting pixels.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from core.atomic import PUBLIC_FILE_MODE, exclusive_lock, write_bytes, write_json
from .atlas import extract_atlas

ATLAS = "CharacterSDIconAtlas"
OUTPUT = Path("ui/character-portraits")


def _roster(document):
    characters = document.get("characters")
    if not isinstance(characters, dict):
        raise ValueError("characters.json must contain a characters object")
    result = []
    for key, row in characters.items():
        if not isinstance(row, dict):
            raise ValueError(f"invalid character row: {key}")
        unit = row.get("unitId")
        if type(unit) is not int or unit <= 0 or str(unit) != key:
            raise ValueError(f"character key and positive unitId must agree: {key}")
        result.append(row)
    return sorted(result, key=lambda row: row["unitId"])


def export_character_portraits(player_data, asset_root, *, source_region,
                              source_game_version):
    """Export only the supplied roster; explicitly record a shared UI donor.

    Every ``image`` is relative to asset_root. Missing source sprites are listed
    in coverage and omitted from characters, so clients can keep their normal
    initials fallback. New images are content-addressed and the manifest is
    atomically published last, preserving a previous valid run on failure.
    """
    if source_region not in ("cn", "jp") or not str(source_game_version).strip():
        raise ValueError("the portrait source needs its region and game version")
    source, root = Path(player_data), Path(asset_root)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    roster_bytes = (root / "characters.json").read_bytes()
    rows = _roster(json.loads(roster_bytes))
    destination = root / OUTPUT
    destination.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(destination / ".export.lock"):
        with tempfile.TemporaryDirectory(prefix="moly-character-atlas-") as temporary:
            decoded = Path(temporary)
            extract_atlas(source, decoded, decode_atlases=(ATLAS,),
                          crop_names=tuple(f"chr_sp_{row['unitId']}" for row in rows),
                          crop_prefixes=())
            document = json.loads((decoded / "atlas.json").read_text(encoding="utf-8"))
            sprites = {}
            for atlas in document["atlases"]:
                if atlas["name"] != ATLAS:
                    continue
                for sprite in atlas["sprites"]:
                    name = sprite["name"]
                    if name in sprites:
                        raise ValueError(f"ambiguous character sprite: {name}")
                    sprites[name] = (atlas, sprite)
            exported, missing, pending = [], [], []
            for row in rows:
                unit = row["unitId"]
                name = f"chr_sp_{unit}"
                entry = sprites.get(name)
                if not entry or not entry[1].get("crop"):
                    missing.append({"unitId": unit, "sprite": name,
                                    "reason": entry[1].get("cropReason") if entry
                                    else "sprite absent from source atlas"})
                    continue
                atlas, sprite = entry
                image_path = (decoded / sprite["crop"]).resolve()
                if not image_path.is_relative_to(decoded.resolve()):
                    raise ValueError("decoded portrait path leaves atlas output")
                data = image_path.read_bytes()
                if data[:8] != b"\x89PNG\r\n\x1a\n":
                    raise ValueError(f"decoded portrait is not PNG: {name}")
                digest = hashlib.sha256(data).hexdigest()
                relative = OUTPUT / f"{name}-{digest[:16]}.png"
                pending.append((root / relative, data))
                identity = row.get("identity") or {}
                exported.append({"unitId": unit,
                                 "gameCharacterId": identity.get("gameCharacterId"),
                                 "name": row.get("name"), "image": relative.as_posix(),
                                 "sha256": digest, "sprite": name,
                                 "source": {"atlas": ATLAS, "file": atlas["file"],
                                            "atlasPathId": atlas["pathId"],
                                            "spritePathId": sprite.get("sprite", {}).get("pathId")}})
            result = {"version": 1,
                      "source": {"playerData": source.name, "sha256": source_hash,
                                 "region": source_region, "gameVersion": str(source_game_version),
                                 "route": "CharacterSDIconAtlas/chr_sp_{unitId}",
                                 "kind": "shared-ui", "rosterSha256": hashlib.sha256(roster_bytes).hexdigest()},
                      "characters": exported,
                      "coverage": {"requested": len(rows), "exported": len(exported),
                                   "missing": missing}}
            for target, data in pending:
                write_bytes(target, data, mode=PUBLIC_FILE_MODE)
            write_json(destination / "character-portraits.json", result, mode=PUBLIC_FILE_MODE)
            return result
