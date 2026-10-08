"""Source MySekai item icon pixels, keyed by the package path the client loads.

Two families of per-icon packages, one Texture2D each:

- ``mysekai/item_preview/...``: ``MysekaiResourceUtility.NoticeCollectItem``
  shows a collected thing's icon through ``UITextureLoader.LoadAsync(bundle,
  resource)``:
  a MySekai material ``AssetBundleNames.GetMysekaiMaterialPreview`` =
  ``mysekai/item_preview/material/`` + ``iconAssetbundleName``; a MySekai item
  ``GetMysekaiItemPreview`` = ``mysekai/item_preview/item/`` +
  ``iconAssetbundleName``; a plant fixture ``GetMysekaiFixturePlantPreviewImage``
  = ``mysekai/item_preview/fixture/{assetbundleName}_{id}``.  The resource is
  the package's last path segment.
- ``mysekai/thumbnail/material/`` and ``mysekai/thumbnail/item/`` +
  ``iconAssetbundleName`` (``GetMysekaiMaterialThumbnail`` /
  ``GetMysekaiItemThumbnail``), the inventory cells' ``UIPartsItemThumbnail``.
- ``mysekai/thumbnail/tool/`` + the tool's ``assetbundleName``
  (``GetMysekaiToolThumbnail``) and the same plus ``_t``
  (``GetMysekaiToolTransparentThumbnail``, resource
  ``GetMysekaiToolTransparentThumbnailFileName``), which the harvest HUD
  shows.  ``GetMysekaiToolCircleThumbnail`` names ``_c`` packages; the
  coverage counts only the two forms above.

Each package lists one container entry, its last path segment plus ``.png``,
naming a Texture2D of that name.  The index maps the package path to the
exported file and its pixel size.  Master rows whose package is absent, and
packages no master row names, are both reported, never guessed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore, require_paths
from core.extract import DEFAULT_UNITY_VERSION
from core.jsonio import write_json

FAMILIES = {
    "item_preview": ("material", "item", "fixture"),
    "thumbnail": ("material", "item", "tool"),
}
TOOL_SUFFIXES = ("", "_t")
PACKAGE_GLOBS = tuple(f"mysekai__{family}__{kind}__*"
                      for family, kinds in FAMILIES.items() for kind in kinds)
PLANT_FIXTURE_TYPE = "plant"


def _rows(document, table):
    """Rows of an exported master document (keyed entries, a row list, or a table key)."""
    if document is None:
        return None
    if isinstance(document, list):
        return document
    entries = document.get("entries")
    if isinstance(entries, dict):
        return list(entries.values())
    if isinstance(entries, list):
        return entries
    rows = document.get(table)
    if isinstance(rows, list):
        return rows
    raise ValueError(f"master document for {table} has no row list")


def expected_packages(materials=None, items=None, fixtures=None, tools=None):
    """The package paths the master rows name, by ``family/kind`` (absent masters skipped)."""
    expected = {}
    for table, document, kind in (("mysekaiMaterials", materials, "material"),
                                  ("mysekaiItems", items, "item")):
        rows = _rows(document, table)
        if rows is None:
            continue
        icons = {row["iconAssetbundleName"] for row in rows if row.get("iconAssetbundleName")}
        for family in FAMILIES:
            expected[f"{family}/{kind}"] = {f"mysekai/{family}/{kind}/{icon}" for icon in icons}
    rows = _rows(fixtures, "fixtures")
    if rows is not None:
        expected["item_preview/fixture"] = {
            f"mysekai/item_preview/fixture/{row['assetbundleName']}_{int(row['id'])}"
            for row in rows if row.get("fixtureType") == PLANT_FIXTURE_TYPE}
    rows = _rows(tools, "mysekaiTools")
    if rows is not None:
        expected["thumbnail/tool"] = {
            f"mysekai/thumbnail/tool/{row['assetbundleName']}{suffix}"
            for row in rows if row.get("assetbundleName") for suffix in TOOL_SUFFIXES}
    return expected


def package_paths(decrypted):
    """Every package of the families under a decrypted-package directory."""
    root = Path(decrypted)
    return sorted({path for pattern in PACKAGE_GLOBS for path in root.glob(pattern)})


def export_item_icons(packages, out_dir, *, materials=None, items=None, fixtures=None,
                      tools=None):
    """Write ``index.json`` and ``textures/<family>/<kind>/<resource>.png`` per package."""
    paths = sorted(Path(value) for value in packages)
    if not paths:
        raise ValueError("item icon export needs at least one mysekai item icon package")
    require_paths(paths)
    UnityPy.config.FALLBACK_UNITY_VERSION = DEFAULT_UNITY_VERSION
    store = PackageStore(paths)
    out = Path(out_dir)
    icons = {}
    for path in paths:
        name = path.name
        segments = name.split("__")
        if (len(segments) != 4 or segments[0] != "mysekai" or segments[1] not in FAMILIES
                or segments[2] not in FAMILIES[segments[1]] or not segments[3]):
            raise ValueError(f"not a mysekai item icon package: {name}")
        _, family, kind, resource = segments
        bundle = "/".join(segments)
        package = store.package(name)
        if package is None:
            raise ValueError(f"item icon package could not be loaded: {path}")
        listed = [(file, path_id) for asset, file, path_id in package.contents
                  if asset == f"{resource}.png"]
        if len(listed) != 1:
            raise ValueError(f"{bundle}: expected one container entry {resource}.png, found {len(listed)}")
        file, path_id = listed[0]
        if file.kinds.get(path_id) != "Texture2D":
            raise ValueError(f"{bundle}: container entry {resource}.png is a {file.kinds.get(path_id)}")
        texture = file.objects[path_id].read()
        if texture.m_Name != resource:
            raise ValueError(f"{bundle}: Texture2D is named {texture.m_Name!r}, not {resource!r}")
        image = texture.image
        relative = f"textures/{family}/{kind}/{resource}.png"
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        icons[bundle] = {
            "family": family, "kind": kind, "resource": resource, "image": relative,
            "width": texture.m_Width, "height": texture.m_Height,
            "decodedSize": [image.width, image.height],
            "serializedFile": file.archive, "pathId": path_id,
            "bundleSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        store.forget(name)
    exported = {}
    for key, entry in icons.items():
        exported.setdefault(f"{entry['family']}/{entry['kind']}", set()).add(key)
    coverage = {"packages": len(icons),
                "byFamily": {f"{family}/{kind}": len(exported.get(f"{family}/{kind}", ()))
                             for family, kinds in FAMILIES.items() for kind in kinds}}
    for group, wanted in expected_packages(materials, items, fixtures, tools).items():
        have = exported.get(group, set())
        coverage[group] = {"masterPackages": len(wanted),
                           "missingPackages": sorted(wanted - have),
                           "unreferencedPackages": sorted(have - wanted)}
    result = {
        "version": 1,
        "unityVersion": DEFAULT_UNITY_VERSION,
        "source": {"families": {family: list(kinds) for family, kinds in FAMILIES.items()},
                   "key": "the package path the client's texture loader is given",
                   "resource": "the Texture2D the package's container lists"},
        "icons": dict(sorted(icons.items())),
        "coverage": coverage,
    }
    write_json(out / "index.json", result)
    return result
