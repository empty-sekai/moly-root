"""Source furniture-thumbnail pixels, joined by master identity and color.

UIPartsItemThumbnail.SetupMysekaiFixture (CN 6.0.0) uses the shared
mysekai/thumbnail/fixture bundle and the name ``{assetbundleName}_{textureId}``
for normal/system furniture. Surface appearances and growing plants have
different source routes and are intentionally not guessed by this exporter.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore, require_paths
from core.extract import DEFAULT_UNITY_VERSION
from core.jsonio import write_json


def export_fixture_thumbnails(bundle, master_json, out_dir, *, fixture_ids):
    bundle, master_json, out = Path(bundle), Path(master_json), Path(out_dir)
    require_paths((bundle, master_json))
    requested = {int(value) for value in fixture_ids}
    if not requested:
        raise ValueError("fixture thumbnail export needs an explicit nonempty fixture selection")
    masters = json.loads(master_json.read_text(encoding="utf-8"))
    rows = {int(row["id"]): row for row in masters["fixtures"] if int(row["id"]) in requested}
    if set(rows) != requested:
        raise ValueError(f"fixture master rows absent: {sorted(requested - set(rows))}")
    for row in rows.values():
        if row["fixtureType"] not in ("normal", "system"):
            raise ValueError(f"fixture {row['id']} needs its source-specific thumbnail route: {row['fixtureType']}")
    UnityPy.config.FALLBACK_UNITY_VERSION = DEFAULT_UNITY_VERSION
    store = PackageStore((bundle,))
    package = store.package(bundle.name)
    if package is None:
        raise ValueError(f"fixture thumbnail package could not be loaded: {bundle}")
    texture_objects = {}
    for file in package.files:
        for path_id, kind in file.kinds.items():
            if kind != "Texture2D":
                continue
            obj = file.objects[path_id]
            name = str(file.tree(path_id)["m_Name"])
            if name in texture_objects:
                raise ValueError(f"duplicate Texture2D name in fixture thumbnails: {name}")
            texture_objects[name] = (file, obj)
    out.mkdir(parents=True, exist_ok=True)
    (out / "textures").mkdir(exist_ok=True)
    fixtures = []
    for fixture_id, row in sorted(rows.items()):
        pattern = re.compile(re.escape(row["assetbundleName"]) + r"_(\d+)$")
        variants = []
        for name, (file, obj) in texture_objects.items():
            matched = pattern.fullmatch(name)
            if matched is None:
                continue
            texture_id = int(matched[1])
            texture = obj.read()
            image = texture.image
            relative = f"textures/{name}.png"
            target = out / relative
            image.save(target, format="PNG")
            variants.append({"textureId": texture_id, "name": name, "image": relative,
                             "width": texture.m_Width, "height": texture.m_Height,
                             "decodedSize": [image.width, image.height],
                             "serializedFile": file.archive, "pathId": obj.path_id,
                             "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        if not variants:
            raise ValueError(f"no source thumbnail for selected fixture {fixture_id}: {row['assetbundleName']}")
        variants.sort(key=lambda value: value["textureId"])
        fixtures.append({"fixtureId": fixture_id, "assetbundleName": row["assetbundleName"],
                         "variants": variants})
    result = {"version": 1, "region": masters["region"], "gameVersion": masters["gameVersion"],
              "unityVersion": DEFAULT_UNITY_VERSION,
              "source": {"bundle": bundle.name.replace("__", "/"),
                         "bundleSha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                         "nameFormat": "{assetbundleName}_{textureId}",
                         "route": "normal/system fixture shared thumbnail route"},
              "fixtures": fixtures}
    write_json(out / "fixture-thumbnails.json", result)
    return result


def export_fixture_thumbnail_packages(bundles, masters, out_dir):
    """Export exact per-texture JP fixture packages without guessing absent routes."""
    paths = [Path(value) for value in bundles]
    require_paths(paths)
    rows = masters.get("fixtures", masters) if isinstance(masters, dict) else masters
    if not isinstance(rows, list):
        raise ValueError("fixture thumbnail master input must be a row list")
    UnityPy.config.FALLBACK_UNITY_VERSION = DEFAULT_UNITY_VERSION
    store = PackageStore(paths)
    texture_objects = {}
    sources = []
    for path in paths:
        package = store.package(path.name)
        if package is None:
            raise ValueError(f"fixture thumbnail package could not be loaded: {path}")
        sources.append({"bundle": path.name.replace("__", "/"),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        for file in package.files:
            for path_id, kind in file.kinds.items():
                if kind != "Texture2D":
                    continue
                obj = file.objects[path_id]
                name = str(file.tree(path_id)["m_Name"])
                if name in texture_objects:
                    raise ValueError(f"duplicate Texture2D name in fixture thumbnails: {name}")
                texture_objects[name] = (file, obj)

    out = Path(out_dir)
    (out / "textures").mkdir(parents=True, exist_ok=True)
    fixtures = []
    matched_names = set()
    for row in sorted(rows, key=lambda value: int(value["id"])):
        fixture_id, asset = int(row["id"]), row["assetbundleName"]
        normal = re.compile(re.escape(asset) + r"_(\d+)$")
        specific = re.compile(re.escape(asset) + rf"_{fixture_id}_(\d+)$")
        candidates = []
        for name, pair in texture_objects.items():
            match = specific.fullmatch(name)
            if match is None and row.get("fixtureType") not in ("plant", "house_plant"):
                match = normal.fullmatch(name)
            if match is not None:
                candidates.append((int(match[1]), name, pair))
        variants = []
        for texture_id, name, (file, obj) in sorted(candidates):
            texture = obj.read(); image = texture.image
            relative = f"textures/{name}.png"; target = out / relative
            image.save(target, format="PNG"); matched_names.add(name)
            variants.append({"textureId": texture_id, "name": name, "image": relative,
                             "width": texture.m_Width, "height": texture.m_Height,
                             "decodedSize": [image.width, image.height],
                             "serializedFile": file.archive, "pathId": obj.path_id,
                             "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        if variants:
            fixtures.append({"fixtureId": fixture_id, "assetbundleName": asset,
                             "fixtureType": row.get("fixtureType"), "variants": variants})
    unused = sorted(set(texture_objects) - matched_names)
    result = {
        "version": 2,
        "region": masters.get("region") if isinstance(masters, dict) else "jp",
        "gameVersion": masters.get("gameVersion") if isinstance(masters, dict) else None,
        "unityVersion": DEFAULT_UNITY_VERSION,
        "source": {"bundles": sources,
                   "nameFormats": ["{assetbundleName}_{textureId}",
                                   "{assetbundleName}_{fixtureId}_{textureId}"],
                   "route": "exact per-texture fixture thumbnail packages"},
        "fixtures": fixtures,
        "coverage": {"masterDefinitions": len(rows), "exportedFixtures": len(fixtures),
                     "exportedVariants": sum(len(row["variants"]) for row in fixtures),
                     "unusedSourceTextures": unused,
                     "missingFixtureIds": sorted(set(int(row["id"]) for row in rows)
                                                 - set(row["fixtureId"] for row in fixtures))},
    }
    write_json(out / "fixture-thumbnails.json", result)
    return result
