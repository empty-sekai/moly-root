"""Resolve furniture presentation through authored type-specific source routes.

A wall/floor is an appearance applied to a room, not a zero-sized model.
Custom display furniture is a master-selected base plus an authored attachment;
its user-selected artwork is intentionally not fabricated by the extractor.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import json
import re

from core.atomic import PUBLIC_FILE_MODE, write_json
from core.assets.packages import PackageStore
from core.master import Master


def surface_bindings(document, skin, channel, color_ids, template, directory):
    """Resolve the same source texture regex used by the client's room setup."""
    rows = []
    for color_id in color_ids:
        pattern = re.compile(template.format(re.escape(skin), color_id))
        matches = []
        for relative in document.get("textures", []):
            if not relative.startswith("textures/") or ".." in Path(relative).parts:
                continue
            # The scene exporter suffix records the exact Texture2D pathId.
            name = re.sub(r"-[0-9a-f]{8}$", "", Path(relative).stem)
            match = pattern.fullmatch(name)
            if match and match.group(1) in ("1", "2", "3") and (directory / relative).is_file():
                matches.append((int(match.group(1)), relative))
        if len(matches) > 1:
            raise ValueError(f"ambiguous source {channel} texture for {skin}, color {color_id}")
        if matches:
            uv_set, texture = matches[0]
            shader_property = "_BaseTextureMappingMode" if channel == "wall" else "_UVSelection"
            value = ({1: 0, 2: 2, 3: 3} if channel == "wall" else {1: 0, 2: 1, 3: 2})[uv_set]
            rows.append({"colorId": color_id, "uvSet": uv_set, "texCoord": uv_set - 1, "texture": texture,
                         "shaderProperties": {shader_property: value}})
    return rows


def extract_custom_geometry(bundles, out):
    from fixtures.meshes import _export_package
    paths = sorted(Path(bundles).glob("mysekai__custom_fixture_attach__*"))
    store = PackageStore(paths, root=bundles)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    packages = {}
    for path in paths:
        try:
            packages[path.name] = _export_package(store, path.name, out)
        except Exception as exc:
            packages[path.name] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
        finally:
            store.forget(path.name)
    report = {"version": 2, "packages": packages,
              "summary": dict(Counter(row["status"] for row in packages.values()))}
    write_json(out / "index.json", report, mode=PUBLIC_FILE_MODE)
    return report["summary"]


def enrich_presentation(master, data_root):
    root = Path(data_root)
    source = Master(str(master)) if not isinstance(master, Master) else master
    raw = {int(row["id"]): row for row in source.table("mysekaiFixtures")}
    custom = source.table("mysekaiCustomFixtures")
    if not isinstance(custom, list):
        raise ValueError("custom furniture master must be an array")
    catalog_path = root / "mysekai-fixtures.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf8"))
    models = json.loads((root / "fixture-models/index.json").read_text(encoding="utf8"))["packages"]
    custom_path = root / "custom-fixture-models/index.json"
    attachments = json.loads(custom_path.read_text(encoding="utf8"))["packages"] if custom_path.exists() else {}
    configs = json.loads((root / "client-config.json").read_text(encoding="utf8"))["StringConfigs"]
    counts = Counter()
    missing = []
    for row in catalog["fixtures"]:
        original = raw[int(row["id"])]
        row["layoutType"] = original.get("mysekaiSettableLayoutType")
        row["siteType"] = original.get("mysekaiSettableSiteType")
        row["colorIds"] = sorted({1, *(int(value["textureId"]) for value in
                                      original.get("mysekaiFixtureAnotherColors", [])
                                      if type(value.get("textureId")) is int and value["textureId"] > 0)})
        if row["fixtureType"] == "surface_appearance":
            skin = row["assetbundleName"]
            channel = {"wall_appearance": "wall", "floor_appearance": "floor"}.get(row["layoutType"])
            safe = bool(skin) and all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in skin)
            relative = f"site/skins/{skin}/{skin}.json"
            available = safe and channel is not None and (root / relative).is_file()
            if available:
                document = json.loads((root / relative).read_text(encoding="utf8"))
                bindings = surface_bindings(document, skin, channel, row["colorIds"],
                                             configs["100" if channel == "wall" else "99"],
                                             (root / relative).parent)
                document.setdefault("surfaceBindings", {})[channel] = bindings
                write_json(root / relative, document, mode=PUBLIC_FILE_MODE)
                available = any(value["colorId"] == 1 for value in bindings)
            row["preview"] = {"kind": "surface", "channel": channel, "skin": skin,
                              "sourceFile": relative, "available": available,
                              "colorId": 1}
        elif row["fixtureType"] == "custom":
            variants = []
            for definition in sorted(custom, key=lambda value: value["id"]):
                if definition.get("mysekaiFixtureId") != row["id"]:
                    continue
                base = "mysekai__fixture__" + definition["baseAssetBundleName"]
                attachment = "mysekai__custom_fixture_attach__" + definition["ornamentAssetBundleName"]
                base_source, ornament_source = models.get(base, {}), attachments.get(attachment, {})
                valid = (base_source.get("status") == "exported" and base_source.get("hasFixtureView")
                         and ornament_source.get("status") == "exported")
                variants.append({"definitionId": definition["id"], "basePackage": base,
                                 "ornamentFile": f"custom-fixture-models/{attachment}.glb",
                                 "kind": definition["mysekaiCustomFixtureOrnamentType"],
                                 "collectionType": definition.get("customProfileResourceCollectionType"),
                                 "gridSize": {key: definition[key] for key in ("width", "height", "depth")},
                                 "available": bool(valid)})
            row["preview"] = {"kind": "custom", "requiresUserArtwork": True,
                              "available": any(item["available"] for item in variants), "variants": variants}
        else:
            package = "mysekai__fixture__" + row["assetbundleName"]
            entry = models.get(package, {})
            row["preview"] = {"kind": "model", "available": entry.get("status") == "exported" and bool(entry.get("hasFixtureView"))}
        counts[row["preview"]["kind"]] += 1
        if not row["preview"]["available"]:
            missing.append({"id": row["id"], "name": row["name"], "route": row["preview"]["kind"]})
    catalog["presentation"] = {"counts": dict(counts), "available": len(catalog["fixtures"]) - len(missing), "missing": missing}
    write_json(catalog_path, catalog, mode=PUBLIC_FILE_MODE)
    return catalog["presentation"]
