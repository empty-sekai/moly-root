"""What the site packages are, and the roll-call every one of them appears in.

The nine sites are eight scene packages, and the manifest ships **109** packages
under the site path.  The other hundred are not spare parts: they are the indoor
kit the three room sites are assembled from, the room-skin sets, the harvest drop
objects a field spawns, the travel cannon, the world-map screen, and two shells the
site system itself is hung on.  A product that extracted the eight and said nothing
about the rest would be hiding the majority of the domain, so every package is
classified here and every one appears in the census with its object count, its type
histogram and what became of it.

The classification is by package **name**, from the path the game builds bundle names
from, and it is deliberately shaped so that a package this repository has never seen
lands in a named class rather than in a silent default: a name under the site path
that matches nothing here is classed ``unclassified`` and is reported as such.
"""

SITE_PREFIX = "mysekai__site__"

# One class per family, with what a consumer is looking at.
KINDS = {
    "scene": ("a site's scene package: the prefab the game places for that site, "
              "plus the model assets and dressing sets it is built from"),
    "shell": ("the site system's own objects: the root the site controllers are "
              "attached to and the environment view they share. No site geometry"),
    "kit": ("the indoor kit: the floor, wall and entrance meshes and their "
            "materials that the three room sites are assembled from at runtime"),
    "roomModule": ("one room expansion stage: a floor prefab and a wall prefab whose "
                   "meshes come from the indoor kit"),
    "roomNavModule": ("the walkable surface of one room expansion stage, as "
                      "collision-only prefabs"),
    "roomSkin": ("one room skin set: the wallpaper, flooring, ceiling or door "
                 "variant a player can apply to a room"),
    "prop": ("a field object: harvest drops, plants, rocks and tools a site spawns "
             "on its grid rather than baking into its scene"),
    "propMaterial": "a shared material and texture set field objects draw with",
    "preview": ("the housing preview stage: the surfaces a fixture is previewed on "
                "outside a real site"),
    "travel": "the cannon the player travels between sites with",
    "sitemap": "the world-map screen: its UI prefab, textures, materials and clips",
    "unclassified": ("a package under the site path this extractor has no class for; "
                     "it is still opened and counted"),
}

# Scene packages are `field/<name>`; everything deeper under `field/` is another
# family.  Kept as a rule rather than a list so a new site is picked up.
SCENE_PARENT = "field"

# Families keyed by an exact name.
EXACT = {
    "root": "shell",
    "environment__common": "shell",
    "my_room_asset__common": "kit",
    "house__navigation_mesh": "roomNavModule",
    "house__preview": "preview",
}

# Families keyed by a name prefix, longest first.
PREFIXES = (
    ("field__my_room_asset__skin__", "roomSkin"),
    ("field__my_room_asset__", "kit"),
    ("field__object__", "prop"),
    ("field__common_asset__", "propMaterial"),
    ("common_asset__", "propMaterial"),
    ("my_room_asset__", "preview"),
    ("house__lv_", "roomModule"),
    ("move__", "travel"),
    ("sitemap__", "sitemap"),
)


def is_site_package(name):
    """True when a logical bundle name belongs to the site domain."""
    return str(name).startswith(SITE_PREFIX)


def key_of(name):
    """The part of a site package's name that is not the shared prefix."""
    name = str(name)
    return name[len(SITE_PREFIX):] if is_site_package(name) else name


def classify(name):
    """``(kind, key)`` for one site package."""
    key = key_of(name)
    if key in EXACT:
        return EXACT[key], key
    parts = key.split("__")
    if len(parts) == 2 and parts[0] == SCENE_PARENT:
        return "scene", parts[1]
    for prefix, kind in PREFIXES:
        if key.startswith(prefix):
            return kind, key
    return "unclassified", key


def scene_name(name):
    """The site name a scene package carries, or ``None``."""
    kind, key = classify(name)
    return key if kind == "scene" else None


# Where a package's dependency list comes from, and what it is *not*.  Measured:
# the delivery site declares 12 dependencies inside its own bundle while the
# shipped manifest declares 23 for it, and the one furniture package it needs
# appears only in the manifest.  So this list is what the package itself says, and
# a consumer working out what to download must use the manifest instead.
DEPENDENCY_SOURCE = ("the package's own AssetBundle object. The shipped manifest "
                     "declares a larger, flattened list for the same package -- "
                     "measured on the delivery site: 12 here against 23 there, with "
                     "its one furniture dependency named only by the manifest -- so "
                     "this is what the package references, not the set a "
                     "download has to fetch")


def inventory(package):
    """Object counts and type histogram of one loaded package."""
    kinds, scripts = {}, {}
    for record in package.files:
        for path_id, kind in record.kinds.items():
            kinds[kind] = kinds.get(kind, 0) + 1
            if kind == "MonoBehaviour":
                name = record.script_of(path_id) or "<script not in package>"
                scripts[name] = scripts.get(name, 0) + 1
    return {"objects": sum(kinds.values()),
            "types": dict(sorted(kinds.items())),
            "scripts": dict(sorted(scripts.items())),
            "serializedFiles": len(package.files),
            "assets": len({name for name, _, _ in package.contents}),
            "declaredDependencies": sorted({str(dependency)
                                            for dependency in package.dependencies}),
            "dependencySource": DEPENDENCY_SOURCE}
