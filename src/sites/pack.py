"""The site asset pack: nine places, their scenes, and everything around them.

`extract_sites` reads the packages under the game's site path and writes one pack:
the placement table that says where the nine sites are, one folder per package with
its geometry and its data, and a census in which every package appears.

The shape of the pack follows the shape of the domain rather than the shape of the
name list.  Six sites are one scene package each; three are the *same* package
placed three times, which is why the placement table is a table and not a field on
a scene; and the three room sites have no scene geometry at all — their walls and
floors are a kit assembled at runtime, so they get a kit, expansion modules and
walkable surfaces instead of a scene, and a consumer that expected one file per
site would have found nothing there.

Master tables decide which packages are sites and where they go.  Without them the
packages still extract — geometry, collision, navigation, materials and the census
are all in the packages themselves — and the placement table is reported as missing
with the reason, rather than being invented from the names.
"""
import os
from pathlib import Path

from core.assets.packages import PackageStore
from core.jsonio import write_json
from . import census
from .placement import (MASTER_TABLES, NO_MASTER, SEMANTICS as PLACEMENT_SEMANTICS,
                        constants_document, placement_document)
from .scenes import PackageExtract

# Where a package's artifacts go, by class.  Kept apart so a reader of the pack can
# tell a site scene from a room skin from a field object without a lookup table.
DIRECTORIES = {
    "scene": "scenes",
    "shell": "shell",
    "kit": "indoor",
    "roomModule": "indoor/modules",
    "roomNavModule": "indoor/navigation",
    "roomSkin": "skins",
    "prop": "props",
    "propMaterial": "props",
    "preview": "preview",
    "travel": "travel",
    "sitemap": "sitemap",
    "unclassified": "other",
}

# The one class whose folder is named for what it is rather than for its package.
STEMS = {"my_room_asset__common": "kit"}

SEMANTICS = dict(PLACEMENT_SEMANTICS, **{
    "files": "every file path in this index is relative to the index itself",
    "packages": ("every package under the site path is opened and appears in the "
                 "census, whatever its class; `objects` in each package document "
                 "accounts for each of its objects as exported, skipped with a "
                 "reason, or unsupported, and those three add up to the total"),
    "geometry": ("one glTF binary per package, with a scene per prefab root: a "
                 "package holds the scene the game places *and* the model assets "
                 "and dressing sets it was built from, and mesh data is shared "
                 "between the scenes rather than copied. `defaultScene` is the one "
                 "the game places"),
    "collision": ("collision surfaces are written one file per surface, outside the "
                  "visible scene, with the role their name states. A surface that is "
                  "also drawn is in both places and carries `visible: true`"),
    "navmesh": ("five packages ship a baked navigation mesh and the rest build one "
                "at runtime from the surfaces under `navmesh_target`; the two states "
                "are distinct and a site with no bake is not given an empty one. "
                "A bake's tiles are carried across as bytes and marked unparsed; its "
                "height mesh, where it has one, is exported as geometry"),
    "indoor": ("the three room sites have no scene geometry: their rooms are "
               "assembled at runtime from the kit, one expansion module per level, "
               "and a walkable surface per level. `indoor` states which module and "
               "which surface each level uses"),
    "materials": ("a material's shader is in a package this domain does not own, so "
                  "what is exported is the shader's name and subshader tags plus the "
                  "whole authored property block. The glTF material in the binary is "
                  "a preview approximation derived from those properties, and the "
                  "property block is the record"),
    "inactiveNodes": ("nodes that ship hidden. They are kept in the geometry, because "
                      "the pack is the authored scene, and listed so a consumer does "
                      "not draw what the game never shows"),
    "skins": ("a skinned renderer's joint binding: which glTF nodes drive the mesh, "
              "in the mesh's own bind-pose order, with the vertex influences and "
              "inverse bind matrices written into the binary. One entry per "
              "renderer, not per mesh -- one mesh can be driven by several "
              "renderers naming different bones, and the site packages do that"),
    "verbatimComponents": ("a component with no structured reader is exported with its "
                           "serialized fields as they are, pointers named where they "
                           "resolve; that is extraction without interpretation, not a "
                           "gap"),
    "timelineSockets": ("a director that ships with no playable asset is an empty "
                        "socket the runtime fills per phenomenon, not an unsupported "
                        "timeline; each entry says which of the two it is and, when "
                        "empty, what assigns it"),
    "fixtures": ("furniture is not in these packages and is not baked into a site: "
                 "not one scene package holds a behaviour whose class names a "
                 "fixture. Exactly one site package needs a furniture package at "
                 "all -- the delivery site's birthday cake -- and that is an "
                 "exception which must not be generalised"),
    "declaredDependencies": ("the packages this one references from inside its own "
                             "bundle. It is deliberately not what a download has "
                             "to fetch: the shipped manifest declares a larger, "
                             "flattened list, and for the delivery site the "
                             "furniture package it needs appears only there (12 "
                             "against 23). Work out what to download from the "
                             "manifest, and read this to know what the package "
                             "itself points at"),
})

COUNTERS = ("packages", "scenes", "geometryFiles", "meshes", "vertices", "triangles",
            "collisionSurfaces", "navmeshes", "navmeshTiles", "heightMeshes",
            "materials", "textures", "emitters", "clips", "skins", "omitted",
            "unsupported")


def _stem(key):
    return STEMS.get(key, key.rsplit("__", 1)[-1])


def _master_rows(master, master_cache):
    """Read the site master tables; report which of them are absent."""
    from core.master import Master, MissingTable
    source = Master(master, cache_dir=master_cache)
    rows, absent = {}, []
    for table in MASTER_TABLES:
        try:
            rows[table] = source.table(table)
        except MissingTable:
            absent.append(table)
    return rows, absent


def indoor_document(documents):
    """How a room is assembled, from the packages rather than from a convention.

    A room's visible floor and wall come from one expansion module, and its
    walkable surface from a second package with one prefab per level.  Both name
    kit meshes, so the mapping is read off the collision and geometry entries the
    modules produced rather than assumed from the level number.
    """
    kit = next((d for d in documents if d["kind"] == "kit"), None)
    modules = sorted((d for d in documents if d["kind"] == "roomModule"),
                     key=lambda d: d["key"])
    navigation = next((d for d in documents if d["kind"] == "roomNavModule"), None)
    levels = {}
    for module in modules:
        level = module["key"].rsplit("_", 1)[-1]
        levels.setdefault(level, {})["module"] = {
            "package": module["package"],
            "prefabs": [{"root": root["name"],
                         "meshes": root["meshes"],
                         "vertices": root["vertices"]}
                        for root in module["roots"]],
            "colliders": [entry.get("mesh") for entry in module["collision"]]}
    for root in (navigation or {}).get("roots", []):
        level = str(root["name"]).rsplit("_", 1)[-1]
        surfaces = [entry for entry in (navigation or {}).get("collision", [])
                    if entry.get("root") == root["name"]]
        levels.setdefault(level, {})["walkable"] = {
            "package": navigation["package"], "prefab": root["name"],
            "surfaces": [{"node": entry.get("node"), "mesh": entry.get("mesh"),
                          "file": entry.get("file"), "reason": entry.get("reason")}
                         for entry in surfaces]}
    return {
        "kit": None if kit is None else {
            "package": kit["package"],
            "meshes": (kit["geometry"] or {}).get("uniqueMeshes"),
            "geometry": (kit["geometry"] or {}).get("file"),
            "collisionSurfaces": len(kit["collision"])},
        "levels": {level: levels[level] for level in sorted(levels)},
        "assembly": ("a room is the kit's meshes placed by one expansion module per "
                     "level plus that level's walkable surface; the site package of "
                     "a room site holds no wall or floor geometry at all"),
    }


def extract_sites(bundles, out_dir, bundle_root=None, master=None, master_cache=None):
    """Extract the site packages into *out_dir*.

    *bundles* names the packages to extract; packages those declare as
    dependencies are read as lookup sources, from the same paths and — when given —
    from *bundle_root*.  A material's shader and a room module's meshes both live
    in other packages, so without those the pointers stay visibly unresolved
    instead of silently null.

    With *master*, the placement table carries the nine rows, their world
    positions, their levels and their grid extents; without it the packages are
    still extracted and the table is reported as missing.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = sorted({os.path.basename(str(path)) for path in bundles
                    if census.is_site_package(os.path.basename(str(path)))})
    store = PackageStore(bundles, bundle_root)
    store.load_dependencies(names)

    index = {"version": 1, "semantics": SEMANTICS, "constants": constants_document(),
             "placement": {"file": "sites.json"},
             "packages": {"file": "packages.json", "count": len(names), "byKind": {}},
             "scenes": {}, "indoor": None, "families": {},
             "timelineSockets": []}
    totals = {name: 0 for name in COUNTERS}
    documents, per_bundle, unsupported, omitted, missing = [], {}, [], [], {}

    for name in names:
        kind, key = census.classify(name)
        directory = out / DIRECTORIES[kind] / _stem(key)
        prefix = f"{DIRECTORIES[kind]}/{_stem(key)}"
        try:
            document = PackageExtract(store, name, directory, prefix).run()
        except Exception as exc:          # a package that cannot be opened at all
            document = {"package": name, "kind": kind, "key": key, "roots": [],
                        "collision": [], "navmesh": [], "materials": [],
                        "textures": [], "components": {}, "particles": [],
                        "skins": [],
                        "geometry": None, "objects": None,
                        "animations": {"clips": []}, "omitted": [],
                        "timelineSockets": [],
                        "unsupported": [{"package": name,
                                         "reason": f"{type(exc).__name__}: {exc}"}]}
        documents.append(document)
        totals["packages"] += 1
        index["packages"]["byKind"][kind] = index["packages"]["byKind"].get(kind, 0) + 1
        geometry = document.get("geometry") or {}
        totals["geometryFiles"] += int(bool(geometry))
        totals["meshes"] += geometry.get("meshes", 0)
        totals["vertices"] += geometry.get("vertices", 0)
        totals["triangles"] += geometry.get("triangles", 0)
        totals["collisionSurfaces"] += len(document["collision"])
        totals["navmeshes"] += len(document["navmesh"])
        totals["navmeshTiles"] += sum(entry["tiles"]["count"]
                                      for entry in document["navmesh"])
        totals["heightMeshes"] += sum(len(entry["heightMeshes"])
                                      for entry in document["navmesh"])
        totals["materials"] += len(document["materials"])
        totals["textures"] += len(document["textures"])
        totals["emitters"] += len(document["particles"])
        totals["clips"] += len(document["animations"]["clips"])
        totals["skins"] += len(document.get("skins") or [])
        unsupported.extend(dict(gap, package=name) for gap in document["unsupported"])
        omitted.extend(dict(gap, package=name) for gap in document.get("omitted") or [])
        per_bundle[name] = {
            "kind": kind,
            "meshes": geometry.get("meshes", 0),
            "vertices": geometry.get("vertices", 0),
            "triangles": geometry.get("triangles", 0),
            "collisionSurfaces": len(document["collision"]),
            "navmeshes": len(document["navmesh"]),
            "materials": len(document["materials"]),
            "textures": len(document["textures"]),
            "unsupported": len(document["unsupported"])}
        if kind == "scene":
            totals["scenes"] += 1
            index["scenes"][key] = {
                "package": name, "directory": prefix,
                "document": document.get("file") or f"{prefix}/{_stem(key)}.json",
                "geometry": None if not geometry else f"{prefix}/{geometry['file']}",
                "rootComponent": next((script for script in
                                       ("MysekaiSiteView", "DeliverySiteView")
                                       if script in document["components"]), None),
                "slots": [slot["name"] for slot in document["slots"]],
                "collision": [{"role": entry.get("role"), "mesh": entry.get("mesh"),
                               "visible": entry.get("visible", False),
                               "file": None if not entry.get("file")
                               else f"{prefix}/{entry['file']}"}
                              for entry in document["collision"]],
                "navmesh": [{"tiles": entry["tiles"]["count"],
                             "agentTypeID": entry["agentTypeID"],
                             "siteLocal": entry["siteLocal"],
                             "file": None if not entry["tiles"]["file"]
                             else f"{prefix}/{entry['tiles']['file']}"}
                            for entry in document["navmesh"]],
                "environments": sorted(document.get("environments") or {}),
                "timelineSockets": document.get("timelineSockets") or [],
                "declaredDependencies":
                    document["inventory"]["declaredDependencies"],
                "vertices": geometry.get("vertices", 0),
                "triangles": geometry.get("triangles", 0)}
        index["timelineSockets"].extend(
            dict(socket, package=name)
            for socket in document.get("timelineSockets") or [])
        index["families"].setdefault(kind, []).append(
            {"key": key, "package": name, "directory": prefix,
             "document": document.get("file") or f"{prefix}/{_stem(key)}.json",
             "geometry": None if not geometry else f"{prefix}/{geometry['file']}"})

    index["indoor"] = indoor_document(documents)

    master_rows, absent = {}, []
    if master:
        master_rows, absent = _master_rows(master, master_cache)
        if absent:
            missing["masterTables"] = absent
    else:
        missing["master"] = NO_MASTER
    placement = placement_document(master_rows, index["scenes"])
    placement.update(version=1, semantics=PLACEMENT_SEMANTICS,
                     constants=constants_document())
    if not master:
        placement["missing"] = NO_MASTER
    write_json(out / "sites.json", placement)
    totals["sites"] = len(placement["sites"])

    write_json(out / "packages.json",
               {"version": 1, "kinds": census.KINDS,
                "packages": {document["package"]: {
                    "kind": document["kind"], "key": document["key"],
                    "directory": f"{DIRECTORIES[document['kind']]}/{_stem(document['key'])}",
                    "inventory": document.get("inventory"),
                    "objects": document.get("objects"),
                    "artifacts": {
                        "geometry": (document.get("geometry") or {}).get("file"),
                        "collision": len(document["collision"]),
                        "navmesh": len(document["navmesh"]),
                        "textures": len(document["textures"]),
                        "materials": len(document["materials"]),
                        "clips": len(document["animations"]["clips"])},
                    "skipped": document.get("skipped", []),
                    "unsupported": len(document["unsupported"])}
                    for document in documents}})

    if store.missing:
        missing["dependencies"] = sorted(store.missing)
    totals["unsupported"] = len(unsupported)
    totals["omitted"] = len(omitted)
    index["summary"] = dict(totals, missing=missing, omitted=omitted,
                            unsupported=unsupported)
    path = write_json(out / "index.json", index)
    return dict(totals, path=str(path), perBundle=per_bundle)
