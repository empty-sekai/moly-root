"""One named prefab of any bundle, through the site package reader.

Some prefabs the game loads by name live in bundles whose domain this repository
reads for something else (the field camera package is read for its camera curves)
or in bundles no domain claims (the house door transition).  This command reads
one such bundle with the site package reader, names the one prefab the game
loads as the primary root and default scene, and writes the package document
with every other root as a scene of its own.  It does not claim the bundle's
domain: routing is unchanged, and the output says which prefab was asked for.
"""
from pathlib import Path

from core.assets.packages import PackageStore, require_paths
from core.jsonio import write_json
from .scenes import PackageExtract

KIND = "prefab-package"


def _linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def extract_prefab_package(bundle, prefab, out_dir, bundle_root=None):
    """Export *prefab* (a container basename such as ``x.prefab``) of *bundle*."""
    from core.extract import _configure_unity_version
    _configure_unity_version(None)
    require_paths([bundle])
    if not str(prefab).endswith(".prefab"):
        raise ValueError("prefab must be a container basename ending in .prefab")
    name = Path(bundle).name
    stem = str(prefab)[:-len(".prefab")]
    out = Path(out_dir)
    if _linked(out):
        raise ValueError("output directory must not be a link")
    store = PackageStore([bundle], bundle_root)
    store.load_dependencies([name])
    package = store.package(name)
    missing = [dep for dep in package.dependencies
               if store.package(dep.replace("/", "__")) is None]
    if missing:
        raise FileNotFoundError(f"dependencies not supplied: {', '.join(missing)}")
    directory = out / stem
    if _linked(directory):
        raise ValueError("output directory must not be a link")
    document = PackageExtract(store, name, directory, stem,
                              classification=(KIND, stem),
                              primary_prefab=prefab).run()
    root = next(row for row in document["roots"] if row["primary"])
    document["sourcePrefab"] = {
        "asset": next(path for path in root["assets"]
                      if path.rsplit("/", 1)[-1] == prefab),
        "root": root["name"], "scene": root["scene"]}
    document["source"] = {
        "package": store.source_receipt(name),
        "dependencies": [store.source_receipt(dep.replace("/", "__"))
                         for dep in sorted(package.dependencies)]}
    write_json(directory / f"{stem}.json", document)
    return {"json": str(directory / f"{stem}.json"),
            "glb": None if not document["geometry"]
            else str(directory / document["geometry"]["file"]),
            "roots": [row["name"] for row in document["roots"]],
            "objects": document["objects"],
            "particles": len(document["particles"]),
            "materials": len(document["materials"]),
            "textures": len(document["textures"]),
            "unsupported": len(document["unsupported"])}
