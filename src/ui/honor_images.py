"""Honor images for the rewards a birthday-party delivery grants.

The delivery's total rewards (``birthdayPartyDeliveryTotalRewards``) name
resource boxes of purpose ``birthday_party_delivery_total_reward``; their
honor details name the honors a reward dialog shows.  The dialog builds each
image with ``HonorUtility.CreateHonorImage``:

- the image bundle is ``honor/<name>``, where ``<name>`` is the honor group's
  ``backgroundAssetbundleName`` when the group has one, else the honor's own
  ``assetbundleName`` (``UIPartsHonorImageViewModel.GetAssetBundleName``);
  from it the image loads ``degree_main`` or ``degree_sub`` by slot size;
- the frame is the sprite ``frame_degree_{m|s}_{n}`` of the bundle
  ``honor_frame/<group frameName>``, ``n`` following the honor's rarity
  (``HonorUtility.GetFrameSpriteName``); the same bundle carries the level
  pips ``frame_degree_level_{n}``.

Every Texture2D of every needed bundle is written as
``<bundle path>/<texture name>.png``; sprites are listed with their rects.
A needed bundle that is not supplied is reported by name, never skipped.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore
from core.jsonio import write_json

PURPOSE = "birthday_party_delivery_total_reward"


def _rows(master: Path, table: str):
    rows = json.loads((master / f"{table}.json").read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{table} must be a row list")
    return rows


def delivery_honor_set(master):
    """The honors the delivery total rewards grant, joined to their groups."""
    master = Path(master)
    rewards = _rows(master, "birthdayPartyDeliveryTotalRewards")
    box_ids = sorted({int(r["resourceBoxId"]) for r in rewards})
    if (master / "resourceBoxDetails.json").is_file():
        # The standalone detail table (CN) or the details embedded in each box (JP).
        boxes = {int(b["id"]) for b in _rows(master, "resourceBoxes") if b["resourceBoxPurpose"] == PURPOSE}
        details = [d for d in _rows(master, "resourceBoxDetails")
                   if d["resourceBoxPurpose"] == PURPOSE and int(d["resourceBoxId"]) in box_ids]
    else:
        rows = [b for b in _rows(master, "resourceBoxes")
                if b["resourceBoxPurpose"] == PURPOSE and int(b["id"]) in box_ids]
        boxes = {int(b["id"]) for b in rows}
        details = [d for b in rows for d in b.get("details") or []]
    missing_boxes = sorted(set(box_ids) - boxes)
    if missing_boxes:
        raise ValueError(f"delivery total rewards name resource boxes the master lacks: {missing_boxes}")
    granted = [d for d in details if d["resourceType"] == "honor"]
    honors = {int(r["id"]): r for r in _rows(master, "honors")}
    groups = {int(r["id"]): r for r in _rows(master, "honorGroups")}
    out = []
    for honor_id in sorted({int(d["resourceId"]) for d in granted}):
        honor = honors.get(honor_id)
        if honor is None:
            raise ValueError(f"a delivery reward names honor {honor_id}, which the honors master lacks")
        group = groups.get(int(honor["groupId"]))
        if group is None:
            raise ValueError(f"honor {honor_id} names group {honor['groupId']}, which the master lacks")
        image = group.get("backgroundAssetbundleName") or honor.get("assetbundleName")
        if not image:
            raise ValueError(f"honor {honor_id} has no image bundle name")
        out.append({"honorId": honor_id, "groupId": int(honor["groupId"]), "honorType": group["honorType"],
                    "honorRarity": honor.get("honorRarity"),
                    "levelsGranted": sorted({d.get("resourceLevel") for d in granted
                                             if int(d["resourceId"]) == honor_id}, key=lambda v: (v is None, v)),
                    "imageBundle": f"honor/{image}",
                    "frameBundle": f"honor_frame/{group['frameName']}" if group.get("frameName") else None})
    counts = {"totalRewardRows": len(rewards), "resourceBoxes": len(box_ids), "boxDetails": len(details),
              "honorDetails": len(granted), "honors": len(out)}
    return out, counts


def export_honor_images(master, bundles, out_dir, *, unity_version):
    """Write ``honor/index.json`` and the textures of every needed bundle."""
    master, bundles, out = Path(master), Path(bundles), Path(out_dir)
    honors, counts = delivery_honor_set(master)
    needed = sorted({h["imageBundle"] for h in honors} | {h["frameBundle"] for h in honors if h["frameBundle"]})
    UnityPy.config.FALLBACK_UNITY_VERSION = unity_version
    files = {name: bundles / name.replace("/", "__") for name in needed}
    missing = [name for name, path in files.items() if not path.is_file()]
    store = PackageStore([path for path in files.values() if path.is_file()])
    written = {}
    for name in needed:
        if name in missing:
            continue
        path = files[name]
        package = store.package(path.name)
        if package is None:
            raise ValueError(f"honor bundle could not be loaded: {path}")
        textures, sprites = {}, {}
        for file in package.files:
            for path_id, kind in sorted(file.kinds.items()):
                if kind == "Texture2D":
                    texture = file.objects[path_id].read()
                    tex_name = str(texture.m_Name)
                    if tex_name in textures:
                        raise ValueError(f"duplicate Texture2D {tex_name} in {name}")
                    relative = f"{name}/{tex_name}.png"
                    (out / relative).parent.mkdir(parents=True, exist_ok=True)
                    texture.image.save(out / relative, format="PNG")
                    textures[tex_name] = {"image": relative, "width": texture.m_Width, "height": texture.m_Height,
                                          "pathId": path_id,
                                          "sha256": hashlib.sha256((out / relative).read_bytes()).hexdigest()}
                elif kind == "Sprite":
                    tree = file.tree(path_id)
                    rect = tree["m_Rect"]
                    sprites[str(tree["m_Name"])] = {"rect": [rect["x"], rect["y"], rect["width"], rect["height"]],
                                                    "texturePathId": tree["m_RD"]["texture"]["m_PathID"]}
        by_path = {entry["pathId"]: tex for tex, entry in textures.items()}
        for sprite in sprites.values():
            sprite["texture"] = by_path.get(sprite.pop("texturePathId"))
        written[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "textures": textures, "sprites": sprites}
    result = {"version": 1, "unityVersion": unity_version,
              "semantics": {"honors": "the honors the birthday-party delivery total rewards grant, in id order",
                            "imageBundle": "honor/<group backgroundAssetbundleName, else the honor assetbundleName>; "
                                           "degree_main for the main slot, degree_sub for the sub slot",
                            "frameBundle": "honor_frame/<group frameName>; frame sprite frame_degree_{m|s}_{n}: m for "
                                           "slot 1 (the reward dialog's), s for slots 2 and 3; n is 1 + the rarity "
                                           "(low 0, middle 1, high 2, highest 3); level pips frame_degree_level_{n}",
                            "paths": "every image path is relative to the output directory"},
              "counts": counts, "honors": honors, "bundles": written, "missingBundles": missing}
    write_json(out / "honor" / "index.json", result)
    return result
