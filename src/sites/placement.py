"""Where the nine sites are, and the constants a consumer needs to place things.

The nine sites live in **one** world coordinate system.  A site's offset in it is
the three integers of its master row converted straight to float — no scaling, no
fixed point — and the game applies that offset to the site's root object at runtime.
Two facts follow and both are contract, not detail:

* **The offset is not in the geometry.**  Three of the nine rows name the *same*
  scene package and differ only in their vertical offset, so a consumer that bakes
  the offset into the mesh collapses those three into one and loses the second and
  third floors.  The geometry ships site-local; this table is what places it.
* **The vertical offset is a separation stride, not a storey height.**  A room is
  about two and a half units tall in this scale, and the floors are five hundred
  units apart: the number keeps two instances of one package from ever seeing each
  other.  Nothing in the geometry says "floor"; only the site type does.

Everything else here is the grid.  Furniture, roads and rugs are placed on integer
grid coordinates, and one cell is a quarter of a unit, so
``world = sitePosition + grid * 0.25``.  A grid coordinate is three *signed bytes*
packed into one integer, which is what bounds a site: 127 cells is 31.75 units from
the origin, and the closest two sites in the horizontal plane are 200 units apart,
so no two sites can overlap.

All of this is measured rather than assumed, and the numbers are restated in the
extracted table so a consumer never has to re-derive them.
"""

# Measured constants of the site space.  These are the game's own
# `MysekaiConstants`, read out of the shipped binary rather than inferred.
TILE_SIZE = 0.25
TILE_SCALE = 0.25
PLAYER_HEIGHT = 1.0
CHARACTER_SIZE_XZ = (1.0, 1.0)
NAVMESH_DATA_AREA_HEIGHT = 2.5
FIXTURE_TOUCH_SIZE_Y = 0.125
WARP_DELAY_TIME = 1.0

# A grid coordinate is packed as four signed bytes (a validity flag and x, y, z),
# so one axis reaches 127 cells from the origin.
GRID_AXIS_MIN = -128
GRID_AXIS_MAX = 127

# The site types the game switches on, in the order it declares them.  The master
# table carries this name per row, and joining on the *name* is what the game does;
# the id happens to be the position plus one in this snapshot, which is a
# coincidence and not a contract.
SITE_TYPES = ("home_site", "first_floor", "second_floor", "third_floor",
              "grassland", "shore", "flower_garden", "memorial_place",
              "festival_garden")

# Which controller the game attaches per site type, from its own switch.
SITE_CONTROLLERS = {
    "home_site": "HomeSiteController",
    "first_floor": "MyRoomSiteController",
    "second_floor": "MyRoomSiteController",
    "third_floor": "MyRoomSiteController",
    "grassland": "HarvestSiteController",
    "shore": "HarvestSiteController",
    "flower_garden": "HarvestSiteController",
    "memorial_place": "HarvestSiteController",
    "festival_garden": "DeliverySiteController",
}

SCENE_PACKAGE_PREFIX = "mysekai__site__field__"

MASTER_TABLES = ("mysekaiSites", "mysekaiSiteGroups", "mysekaiSiteLevels",
                 "mysekaiSiteLayouts", "mysekaiSiteFootsteps",
                 "mysekaiSiteHousingLayoutUnavailableZones")

NO_MASTER = ("no master directory supplied; a site's identity, its world position, "
             "its levels and its grid extents are only in caller-supplied master "
             "tables, so the placement table cannot be written from packages alone")

SEMANTICS = {
    "siteLocalGeometry": ("every exported mesh keeps its package's own origin: the "
                          "world offset of a site is `sitePosition` here and is "
                          "applied by the consumer, never baked into geometry. "
                          "Three rows share one scene package and differ only in "
                          "`sitePosition.y`, so baking it loses two of them"),
    "sitePosition": ("the three integers of the master row converted straight to "
                     "float, with no scaling; the game assigns it to the site "
                     "object's local position under a root that sits at the origin, "
                     "so it is also the world position"),
    "verticalStride": ("the vertical offsets of the three room rows are 0 / 500 / "
                       "1000 while a room is about 2.5 units tall, so the number is "
                       "a separation stride between two instances of one package, "
                       "not a storey height; the geometry has no floors"),
    "grid": ("furniture and ground layers are placed on integer grid coordinates, "
             "one cell being `tileSize` units: `world = sitePosition + grid * "
             "tileSize`. A coordinate is three signed bytes, so one axis reaches "
             "127 cells (31.75 units) from a site's origin"),
    "join": ("a row is joined to the site type by the `siteType` name, which is what "
             "the game switches on; the id being the type's position plus one holds "
             "in this snapshot by coincidence and must not be relied on"),
    "layouts": ("grid extents per site level, in cells, by layer: `floor` is the "
                "buildable volume, `rug` and `road` are one cell tall ground "
                "layers, and the four `wall_*` layers are the room walls. Multiply "
                "by `tileSize` for units"),
    "footsteps": ("a footstep cue is chosen by colour, not by mesh: the table maps "
                  "an RGB triple to a walk cue and a run cue. Which surface carries "
                  "the colour is not established here -- the `_footse` collision "
                  "mesh of each outdoor site is the candidate, and this lane did not "
                  "trace the consumer that samples it"),
    "levels": ("a site level is the site's expansion stage; the room sites reach "
               "level 5 and the second and third floors ship at level 5 only. "
               "Indoor geometry per level is in the indoor kit, not here"),
}


def constants_document():
    """The measured constants of the site space, as extracted data."""
    return {
        "tileSize": TILE_SIZE,
        "tileScale": TILE_SCALE,
        "playerHeight": PLAYER_HEIGHT,
        "characterSizeXZ": list(CHARACTER_SIZE_XZ),
        "navmeshDataAreaHeight": NAVMESH_DATA_AREA_HEIGHT,
        "fixtureTouchSizeY": FIXTURE_TOUCH_SIZE_Y,
        "warpDelayTime": WARP_DELAY_TIME,
        "gridAxis": {"min": GRID_AXIS_MIN, "max": GRID_AXIS_MAX,
                     "halfExtentUnits": round(GRID_AXIS_MAX * TILE_SIZE, 6)},
        "worldFromGrid": "world = sitePosition + grid * tileSize",
    }


def _row_numbers(row, *names):
    return [row.get(name) for name in names]


def placement_document(rows, scene_names=()):
    """The nine-row placement table, joined with levels, layouts and footsteps.

    *rows* maps a master table name to its rows; *scene_names* are the scene keys
    the extractor produced, so a row can point at the geometry that was written
    rather than at a package name a consumer then has to guess a path from.
    """
    sites = rows.get("mysekaiSites") or []
    levels = rows.get("mysekaiSiteLevels") or []
    layouts = rows.get("mysekaiSiteLayouts") or []
    zones = rows.get("mysekaiSiteHousingLayoutUnavailableZones") or []
    groups = rows.get("mysekaiSiteGroups") or []
    footsteps = rows.get("mysekaiSiteFootsteps") or []

    layouts_by_level = {}
    for row in layouts:
        layouts_by_level.setdefault(row.get("mysekaiSiteLevelId"), []).append(row)
    zones_by_layout = {}
    for row in zones:
        zones_by_layout.setdefault(row.get("mysekaiSiteLayoutId"), []).append(row)

    document = {"sites": [], "footsteps": [], "groups": [], "packageUsedBy": {}}
    for row in sites:
        site_type = row.get("mysekaiSiteType")
        bundle = str(row.get("assetbundleName") or "")
        entry = {
            "id": row.get("id"),
            "siteType": site_type,
            "siteTypeValue": (SITE_TYPES.index(site_type)
                              if site_type in SITE_TYPES else None),
            "controller": SITE_CONTROLLERS.get(site_type),
            "category": row.get("mysekaiSiteCategory"),
            "name": row.get("name"),
            "isBase": row.get("isBase"),
            "isEnabledForMulti": row.get("isEnabledForMulti"),
            "presetGroupId": row.get("presetGroupId"),
            "assetbundleName": bundle,
            "package": SCENE_PACKAGE_PREFIX + bundle.replace("/", "__"),
            "scene": bundle if bundle in set(scene_names) else None,
            "sitePosition": {"x": float(row.get("positionX", 0)),
                             "y": float(row.get("positionY", 0)),
                             "z": float(row.get("positionZ", 0))},
            "sitePositionRaw": {"positionX": row.get("positionX"),
                                "positionY": row.get("positionY"),
                                "positionZ": row.get("positionZ")},
            "groupId": next((g.get("groupId") for g in groups
                             if g.get("mysekaiSiteId") == row.get("id")), None),
            "levels": [],
        }
        for level in sorted((l for l in levels
                             if l.get("mysekaiSiteId") == row.get("id")),
                            key=lambda l: l.get("level") or 0):
            level_entry = {"id": level.get("id"), "level": level.get("level"),
                           "characterEntryMaxNum": level.get("characterEntryMaxNum"),
                           "layouts": []}
            for layout in layouts_by_level.get(level.get("id"), []):
                level_entry["layouts"].append({
                    "id": layout.get("id"),
                    "layoutType": layout.get("mysekaiLayoutType"),
                    "cells": {"width": layout.get("width"),
                              "height": layout.get("height"),
                              "depth": layout.get("depth")},
                    "units": {"width": round((layout.get("width") or 0) * TILE_SIZE, 6),
                              "height": round((layout.get("height") or 0) * TILE_SIZE, 6),
                              "depth": round((layout.get("depth") or 0) * TILE_SIZE, 6)},
                    "unavailableZones": [
                        {"id": zone.get("id"),
                         "start": _row_numbers(zone, "startX", "startY", "startZ"),
                         "cells": _row_numbers(zone, "width", "height", "depth")}
                        for zone in zones_by_layout.get(layout.get("id"), [])],
                })
            entry["levels"].append(level_entry)
        document["sites"].append(entry)
        document["packageUsedBy"].setdefault(entry["package"], []).append(row.get("id"))

    document["footsteps"] = [
        {"id": row.get("id"), "color": [row.get("red"), row.get("green"),
                                        row.get("blue")],
         "walkCue": row.get("walkCue"), "runCue": row.get("runCue")}
        for row in footsteps]
    document["groups"] = [{"id": row.get("id"), "groupId": row.get("groupId"),
                           "siteId": row.get("mysekaiSiteId")} for row in groups]
    return document
