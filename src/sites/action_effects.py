"""Site action effects: the prefabs a site move, a harvest action and the
player's feet emit.

The game ships them one package per effect in two families:

* ``mysekai/effect/site/harvest/action/<leaf>`` (flying, landing, failed landing,
  boarding/takeoff, the harvest boost), each holding the prefab named after its
  leaf (the takeoff package also holds the boarding prefab, which stays a scene
  of its own);
* ``mysekai/effect/site/common/action/<leaf>`` (the dash dust, the water splash),
  named after the action rather than the prefab: ``dash`` holds
  ``fx_act_user_walking``.  Each of these packages holds exactly one prefab, and
  that prefab is the effect; a package of this family with any other number of
  prefabs is refused by name instead of guessed;
* ``mysekai/effect/site/harvest/object/<group>/<leaf>`` (a harvest object's hit,
  its disappearance, the barrel's resting effect), ``<group>`` one of ``hit``,
  ``delete``, ``stay``.  The prefab is named exactly as the last segment, and the
  group is kept as a directory level, so a package's output path is its source
  path below the family prefix.  A hit package may hold a second prefab (the
  looping variant) which, like the boarding prefab, is a scene of its own;
* ``mysekai/effect/site/home/<leaf>`` (the craft effect a workbench emits, the
  multiplayer avatar appearance), each holding the prefab named after its leaf
  (the appearance package also holds the cannon appearance prefab, a scene of
  its own);
* ``mysekai/effect/fixture/fx_fixture_put_01`` (the effect a decided furniture
  placement shows), one package claimed by its exact name, holding the prefab
  named after its leaf.  The field put effect loads it by that name, not the
  effect manager by type.

Nothing places them in a site: the effect manager emits them by type, or a site
behaviour instantiates them, so they are not site packages and the site census
does not list them.  Each family is claimed by its path prefix, so an effect
added under it later is extracted rather than silently ignored.

They are read with the site package reader, the one that exports the travel
cannon, so a particle row, a material block, a texture and a behaviour's serialized
fields here have exactly the cannon package's shape.  The index names which prefab
of each package is the effect and joins every behaviour pointer that names a
particle system to that system's row, by the pointer's path id rather than by
node name.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.assets.packages import PackageStore, require_paths
from core.assets.router import (FIXTURE_PUT_EFFECT_BUNDLE, FIXTURE_PUT_EFFECT_PREFIX,
                                SITE_ACTION_EFFECT_BUNDLE, SITE_ACTION_EFFECT_PREFIX,
                                SITE_COMMON_ACTION_EFFECT_BUNDLE,
                                SITE_COMMON_ACTION_EFFECT_PREFIX,
                                SITE_HARVEST_OBJECT_EFFECT_PREFIX,
                                SITE_HOME_EFFECT_BUNDLE, SITE_HOME_EFFECT_PREFIX)
from core.jsonio import write_json
from .scenes import PackageExtract

KIND = "site-action-effect"
COMMON_KIND = "site-common-action-effect"
OBJECT_KIND = "site-harvest-object-effect"
HOME_KIND = "site-home-effect"
FIXTURE_PUT_KIND = "fixture-put-effect"
OBJECT_GROUPS = ("hit", "delete", "stay")
# The package-name shape the object family reads: one group segment, then the
# leaf, which holds no ``__`` of its own, so the leaf is the last path segment and
# nothing deeper is folded into it.
OBJECT_EFFECT_NAME = re.compile(
    "^" + re.escape(SITE_HARVEST_OBJECT_EFFECT_PREFIX)
    + r"(" + "|".join(OBJECT_GROUPS) + r")__([a-z0-9]+(?:_[a-z0-9]+)*)$")
INDEX_NAME = "index.json"
SHADER_PACKAGE = "mysekai__shader"
LEAF_SELECTION = ("each package's effect is the prefab whose container basename is "
                  "`<leaf>.prefab`; `sourcePrefab.scene` is its glTF scene and the "
                  "document's default scene. Any other prefab root in the package is "
                  "a scene of its own, not part of the effect")
ONLY_PREFAB_SELECTION = ("each package holds exactly one prefab container and that "
                         "prefab is the effect (the package is named after the action, "
                         "not the prefab); `sourcePrefab.scene` is its glTF scene and "
                         "the document's default scene. A package with any other "
                         "number of prefabs is refused")
SEMANTICS = {
    "files": ("paths in this index are relative to it; paths inside a package "
              "document are relative to that document"),
    "selection": LEAF_SELECTION,
    "particles": ("one row per particle system, in the site package reader's shape "
                  "(system with source module inventory, renderer with its material "
                  "block, mesh name for mesh render mode). `scene` joins "
                  "`roots[].scene`"),
    "behaviours": ("every MonoBehaviour on the prefab, with its serialized fields "
                   "verbatim in the package document under `components`. Here each "
                   "field that points at a ParticleSystem is joined to the particle "
                   "row that system produced, by path id"),
    "emission": ("which effect type loads which package, and how it is emitted, is "
                 "not in these packages; it is decided by the effect manager's own "
                 "table and code"),
    "incremental": ("the index is rebuilt from every package document on disk, "
                    "including packages not requested in this run"),
}
GROUPED_FILES = ("each package's directory is its source path below the family "
                 "prefix, `<group>/<leaf>`, and that is its key in `effects`; paths "
                 "in this index are relative to it; paths inside a package document "
                 "are relative to that document")
GROUPED_SELECTION = ("each package's effect is the prefab whose container basename "
                     "is `<leaf>.prefab`, the leaf being the package path's last "
                     "segment; `sourcePrefab.scene` is its glTF scene and the "
                     "document's default scene. Any other prefab root in the package "
                     "is a scene of its own, not part of the effect")


@dataclass(frozen=True)
class Family:
    kind: str
    prefix: str
    pattern: object
    selection: str          # "leaf" or "only-prefab"
    grouped: bool = False   # the pattern's two groups are (group, leaf)

    def parts(self, name):
        """``(group, leaf)`` of a package name of this family, or ``None``.

        *group* is ``None`` for a family without a group level.
        """
        match = self.pattern.fullmatch(str(name))
        if match is None:
            return None
        if self.grouped:
            return match.group(1), match.group(2)
        return None, str(name)[len(self.prefix):]

    def leaf_of(self, name):
        parts = self.parts(name)
        return None if parts is None else parts[1]

    def relative(self, name):
        """The package's output directory relative to the family index."""
        parts = self.parts(name)
        if parts is None:
            return None
        group, leaf = parts
        return leaf if group is None else f"{group}/{leaf}"

    def semantics(self):
        if self.grouped:
            return dict(SEMANTICS, files=GROUPED_FILES, selection=GROUPED_SELECTION)
        if self.selection == "leaf":
            return SEMANTICS
        return dict(SEMANTICS, selection=ONLY_PREFAB_SELECTION)


HARVEST_ACTION = Family(KIND, SITE_ACTION_EFFECT_PREFIX, SITE_ACTION_EFFECT_BUNDLE, "leaf")
COMMON_ACTION = Family(COMMON_KIND, SITE_COMMON_ACTION_EFFECT_PREFIX,
                       SITE_COMMON_ACTION_EFFECT_BUNDLE, "only-prefab")
HARVEST_OBJECT = Family(OBJECT_KIND, SITE_HARVEST_OBJECT_EFFECT_PREFIX,
                        OBJECT_EFFECT_NAME, "leaf", grouped=True)
HOME = Family(HOME_KIND, SITE_HOME_EFFECT_PREFIX, SITE_HOME_EFFECT_BUNDLE, "leaf")
# One package by its exact name; the prefix is only the part cut off to get the
# leaf.  Any other package under the prefix is a furniture particle companion,
# which this family's pattern does not match.
FIXTURE_PUT = Family(FIXTURE_PUT_KIND, FIXTURE_PUT_EFFECT_PREFIX, FIXTURE_PUT_EFFECT_BUNDLE,
                     "leaf")


def leaf_of(name):
    """The effect leaf of a harvest-action package name, or ``None``."""
    return HARVEST_ACTION.leaf_of(name)


def _linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _pointer_fields(value, path=""):
    """``(field path, pointer)`` for every resolved pointer in a verbatim dump."""
    if isinstance(value, dict):
        if "pathId" in value and "fileId" in value:
            yield path, value
            return
        for key, item in value.items():
            yield from _pointer_fields(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _pointer_fields(item, f"{path}[{index}]")


def behaviours(document):
    """Each MonoBehaviour instance, with its particle-system pointers joined."""
    rows = {}
    for row in document["particles"]:
        if "pathId" in row:
            rows[str(row["pathId"])] = row
    result = []
    for name, entry in document["components"].items():
        if entry["type"] != "MonoBehaviour":
            continue
        for instance in entry["instances"]:
            pointers = []
            for field, pointer in _pointer_fields(instance["fields"]):
                joined = {"field": field, "type": pointer.get("type"),
                          "pathId": str(pointer["pathId"]),
                          "fileId": pointer["fileId"]}
                if pointer.get("type") == "ParticleSystem":
                    row = rows.get(str(pointer["pathId"])) if not pointer["fileId"] else None
                    joined["emitter"] = (None if row is None
                                         else {"node": row["node"], "scene": row["scene"]})
                pointers.append(joined)
            result.append({"class": name, "node": instance["node"],
                           "pointers": pointers})
    return result


def _primary_prefab(family, package, leaf):
    """The container basename of the prefab that is this package's effect."""
    if family.selection == "leaf":
        return f"{leaf}.prefab"
    prefabs = sorted({str(asset).rsplit("/", 1)[-1] for asset, _, _ in package.contents
                      if str(asset).endswith(".prefab")})
    if len(prefabs) != 1:
        raise ValueError(f"expected exactly one prefab in {package.name}, "
                         f"found {len(prefabs)}: {', '.join(prefabs) or 'none'}")
    return prefabs[0]


def _index_entry(family, relative, document):
    geometry = document["geometry"]
    prefab = document["sourcePrefab"]
    leaf = relative.rsplit("/", 1)[-1]
    if document["kind"] != family.kind or document["key"] != leaf:
        raise ValueError("package document does not identify this effect")
    if family.relative(document["package"]) != relative:
        raise ValueError("package document is not the package of this directory")
    if geometry is not None and geometry["defaultScene"] != prefab["scene"]:
        raise ValueError("effect geometry does not select its source prefab")
    scene = prefab["scene"]
    entry = {"package": document["package"]}
    if family.grouped:
        entry.update(group=relative.split("/", 1)[0], leaf=leaf)
    entry.update({
        "document": f"{relative}/{leaf}.json",
        "glb": None if geometry is None else f"{relative}/{geometry['file']}",
        "sourcePrefab": prefab,
        "particles": sum(1 for row in document["particles"] if row["scene"] == scene),
        "packageParticles": len(document["particles"]),
        "behaviours": document["behaviours"],
        "textures": [f"{relative}/{path}" for path in document["textures"]],
        "materials": len(document["materials"]),
        "declaredDependencies": document["inventory"]["declaredDependencies"],
        "objects": document["objects"],
        "unsupported": len(document["unsupported"]),
    })
    return entry


def _package_directories(family, out):
    """``(relative path, directory)`` of every candidate package directory.

    A grouped family's packages sit one level down, under a directory per
    group; any other top-level entry is offered as it is and then skipped with
    the reason, never silently passed over.
    """
    for directory in sorted(out.iterdir()):
        if not directory.is_dir():
            continue
        if not family.grouped or directory.name not in OBJECT_GROUPS or _linked(directory):
            yield directory.name, directory
            continue
        for inner in sorted(directory.iterdir()):
            if inner.is_dir():
                yield f"{directory.name}/{inner.name}", inner


def _index_from_disk(family, out):
    effects, skipped = {}, []
    for relative, directory in _package_directories(family, out):
        if _linked(directory):
            skipped.append({"directory": relative, "reason": "linked directory is not traversed"})
            continue
        if family.relative(family.prefix + relative.replace("/", "__")) != relative:
            skipped.append({"directory": relative, "reason": "not a package leaf of this family"})
            continue
        leaf = relative.rsplit("/", 1)[-1]
        path = directory / f"{leaf}.json"
        try:
            if _linked(path):
                raise ValueError("linked package document is not followed")
            document = json.loads(path.read_text(encoding="utf-8"))
            effects[relative] = _index_entry(family, relative, document)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            skipped.append({"directory": relative, "reason": f"{type(exc).__name__}: {exc}"})
    return {"version": 1, "semantics": family.semantics(), "effects": effects,
            "summary": {"packages": len(effects),
                        "particles": sum(row["particles"] for row in effects.values()),
                        "materials": sum(row["materials"] for row in effects.values()),
                        "textures": sum(len(row["textures"]) for row in effects.values()),
                        "unsupported": sum(row["unsupported"] for row in effects.values())},
            "skippedPackages": skipped}


def _extract(family, bundles, out_dir, bundle_root):
    from core.extract import _configure_unity_version
    _configure_unity_version(None)
    require_paths(bundles)
    paths = {Path(path).name: str(path) for path in bundles}
    names = sorted(name for name in paths if family.leaf_of(name))
    out = Path(out_dir)
    if _linked(out):
        raise ValueError("effect output directory must not be a link")
    out.mkdir(parents=True, exist_ok=True)
    store = PackageStore(paths.values(), bundle_root)
    store.load_dependencies(names)
    per_bundle = {name: {"status": "unsupported",
                         "error": f"not a {family.kind} package"}
                  for name in paths if name not in names and name != SHADER_PACKAGE}
    for name in per_bundle:
        if name.startswith(family.prefix):
            # Claimed by the family's prefix but not of the shape its reader
            # knows: a failure by name, not a package quietly passed over.
            per_bundle[name] = {"status": "failed",
                                "error": f"{name} is under the {family.kind} prefix "
                                         "but not a package name of that family"}
    for name in names:
        leaf = family.leaf_of(name)
        relative = family.relative(name)
        try:
            package = store.package(name)
            if package is None:
                raise FileNotFoundError(f"effect package not loaded: {name}")
            missing = [dep for dep in package.dependencies
                       if store.package(dep.replace("/", "__")) is None]
            if missing:
                raise FileNotFoundError(f"effect dependencies not supplied: {', '.join(missing)}")
            directory = out / relative
            if any(_linked(path) for path in (directory, directory.parent)):
                raise ValueError("effect package output directory must not be a link")
            prefab = _primary_prefab(family, package, leaf)
            document = PackageExtract(
                store, name, directory, relative, classification=(family.kind, leaf),
                primary_prefab=prefab).run()
            root = next(row for row in document["roots"] if row["primary"])
            document["sourcePrefab"] = {
                "asset": next(path for path in root["assets"]
                              if path.rsplit("/", 1)[-1] == prefab),
                "root": root["name"], "scene": root["scene"],
            }
            document["source"] = {
                "package": store.source_receipt(name),
                "dependencies": [store.source_receipt(dep.replace("/", "__"))
                                 for dep in sorted(package.dependencies)]}
            document["behaviours"] = behaviours(document)
            entry = _index_entry(family, relative, document)
            write_json(directory / f"{leaf}.json", document)
            per_bundle[name] = {"status": "succeeded",
                                "json": str(directory / f"{leaf}.json"),
                                "glb": None if entry["glb"] is None
                                else str(out / entry["glb"]),
                                "particles": entry["particles"],
                                "materials": entry["materials"],
                                "textures": len(entry["textures"]),
                                "objects": entry["objects"],
                                "unsupported": entry["unsupported"]}
        except Exception as exc:
            per_bundle[name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    index = _index_from_disk(family, out)
    index_path = out / INDEX_NAME
    write_json(index_path, index)
    return {"path": str(index_path), **index["summary"], "perBundle": per_bundle,
            "skippedPackages": index["skippedPackages"]}


def extract_site_action_effects(bundles, out_dir, bundle_root=None):
    """Export the supplied harvest-action effect packages and rebuild the index.

    Declared dependencies (the shared effect meshes and textures, the shader
    package, and for the failed landing the landing package) are read from the
    same paths or from *bundle_root*; one that cannot be found fails its package
    by name instead of leaving its pointers silently empty.
    """
    return _extract(HARVEST_ACTION, bundles, out_dir, bundle_root)


def extract_site_harvest_object_effects(bundles, out_dir, bundle_root=None):
    """Export the supplied harvest-object effect packages and rebuild their index.

    Same reader, document shape and dependency rule as
    :func:`extract_site_action_effects`; the package directory keeps the
    source path's group level (``hit``/``delete``/``stay``).  A package under the
    family prefix whose name does not have the ``<group>__<leaf>`` shape is
    reported unsupported by name rather than exported under a guessed leaf.
    """
    return _extract(HARVEST_OBJECT, bundles, out_dir, bundle_root)


def extract_site_common_action_effects(bundles, out_dir, bundle_root=None):
    """Export the supplied foot-effect packages and rebuild their index.

    Same reader, same document shape and the same dependency rule as
    :func:`extract_site_action_effects`; only the family (prefix, kind, index
    directory) and the prefab selection differ.
    """
    return _extract(COMMON_ACTION, bundles, out_dir, bundle_root)


def extract_site_home_effects(bundles, out_dir, bundle_root=None):
    """Export the supplied home-site effect packages and rebuild their index.

    Same reader, same document shape, the same leaf-named prefab selection and
    the same dependency rule as :func:`extract_site_action_effects`; only the
    family (prefix, kind, index directory) differs.
    """
    return _extract(HOME, bundles, out_dir, bundle_root)


def extract_fixture_put_effects(bundles, out_dir, bundle_root=None):
    """Export the furniture placement effect package and rebuild its index.

    Same reader, same document shape, the same leaf-named prefab selection and
    the same dependency rule as :func:`extract_site_action_effects`; only the
    family (its exact package name, kind, index directory) differs.
    """
    return _extract(FIXTURE_PUT, bundles, out_dir, bundle_root)
