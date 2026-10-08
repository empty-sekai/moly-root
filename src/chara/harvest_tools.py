"""Player-held tool prefabs through the existing package geometry/material reader.

Two families share this reader: the harvest tools (``mysekai/tool/<leaf>``,
the axe and pickaxe the harvest action attaches) and the player tool models
(``mysekai/player_tool_model/<leaf>``, the hand items the avatar item view
instantiates on an arm).  Each package holds the prefab ``<leaf>.prefab``.

Tool selection names one prefab, not every imported model root in its bundle.
The shared reader retains those other roots as separate scenes; the index names
the one source prefab and default scene the player attaches. Mesh decoding,
transforms, material properties and textures all remain in their existing readers.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from core.assets.packages import PackageStore, require_paths
from core.assets.router import (HARVEST_TOOL_BUNDLE, PLAYER_TOOL_MODEL_BUNDLE,
                                PLAYER_TOOL_MODEL_PREFIX)
from core.jsonio import write_json
from sites.scenes import PackageExtract


INDEX_NAME = "index.json"
SHADER_PACKAGE = "mysekai__shader"
SEMANTICS = {
    "files": "paths are relative to this index; paths inside a package document are relative to that document",
    "selection": "assetbundleName is the tool leaf; sourcePrefab names the exact same-leaf prefab container and its default glTF scene",
    "geometry": "the source prefab's hierarchy and local transforms are preserved; imported model roots are separate scenes, not additional player attachments",
    "materials": "package documents retain authored shader inputs and texture bindings; glTF PBR materials are previews, not a replacement for the tool shader",
    "incremental": "the index is rebuilt from all existing tool package documents, including packages not requested in this run",
}


@dataclass(frozen=True)
class ToolFamily:
    kind: str
    prefix: str
    pattern: object
    label: str          # how a reason names one package of the family


HARVEST_TOOL = ToolFamily("harvest-tool", "mysekai__tool__", HARVEST_TOOL_BUNDLE,
                          "harvest-tool")
PLAYER_TOOL_MODEL = ToolFamily("player-tool-model", PLAYER_TOOL_MODEL_PREFIX,
                               PLAYER_TOOL_MODEL_BUNDLE, "player tool model")


def _linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _index_entry(family, leaf, document):
    geometry = document["geometry"]
    prefab = document["sourcePrefab"]
    if document["kind"] != family.kind or document["key"] != leaf:
        raise ValueError(f"package document does not identify this {family.label}")
    if not geometry or geometry["defaultScene"] != prefab["scene"]:
        raise ValueError("tool geometry does not select its source prefab")
    return {
        "assetbundleName": leaf, "package": document["package"],
        "document": f"{leaf}/{leaf}.json", "glb": f"{leaf}/{geometry['file']}",
        "sourcePrefab": prefab,
        "textures": [f"{leaf}/{path}" for path in document["textures"]],
        "declaredDependencies": document["inventory"]["declaredDependencies"],
        "materials": len(document["materials"]),
        "meshes": geometry["meshes"], "vertices": geometry["vertices"],
        "triangles": geometry["triangles"],
        "unsupported": len(document["unsupported"]),
    }


def _index_from_disk(family, out):
    tools, skipped = {}, []
    for directory in sorted(out.iterdir()):
        if not directory.is_dir():
            continue
        leaf = directory.name
        if _linked(directory):
            skipped.append({"directory": leaf, "reason": "linked directory is not traversed"})
            continue
        if not family.pattern.fullmatch(f"{family.prefix}{leaf}"):
            skipped.append({"directory": leaf,
                            "reason": f"not an authored {family.label} package name"})
            continue
        path = directory / f"{leaf}.json"
        try:
            if _linked(path):
                raise ValueError("linked package document is not followed")
            document = json.loads(path.read_text(encoding="utf-8"))
            tools[leaf] = _index_entry(family, leaf, document)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            skipped.append({"directory": leaf, "reason": f"{type(exc).__name__}: {exc}"})
    return {"version": 1, "semantics": SEMANTICS, "tools": tools,
            "summary": {"packages": len(tools),
                        "materials": sum(row["materials"] for row in tools.values()),
                        "textures": sum(len(row["textures"]) for row in tools.values()),
                        "unsupported": sum(row["unsupported"] for row in tools.values())},
            "skippedPackages": skipped}


def extract_harvest_tools(bundles, out_dir, bundle_root=None):
    """Export explicitly supplied tool packages and rebuild their complete index.

    The shared shader package may be supplied as an auxiliary input or resolved
    from bundle_root. Unknown neighbours remain named in perBundle; they are not
    interpreted as tools. Failures are reported per package, not hidden by the
    successful packages in the same run.
    """
    return _extract(HARVEST_TOOL, bundles, out_dir, bundle_root)


def extract_player_tool_models(bundles, out_dir, bundle_root=None):
    """Export the supplied player tool model packages and rebuild their index.

    Same reader, document shape, index shape and dependency rule as
    :func:`extract_harvest_tools`; only the family (package prefix, kind, output
    directory) differs.
    """
    return _extract(PLAYER_TOOL_MODEL, bundles, out_dir, bundle_root)


def _extract(family, bundles, out_dir, bundle_root):
    from core.extract import _configure_unity_version
    _configure_unity_version(None)
    require_paths(bundles)
    paths = {Path(path).name: str(path) for path in bundles}
    names = sorted(name for name in paths if family.pattern.fullmatch(name))
    out = Path(out_dir)
    if _linked(out):
        raise ValueError("tool output directory must not be a link")
    out.mkdir(parents=True, exist_ok=True)
    store = PackageStore(paths.values(), bundle_root)
    store.load_dependencies(names)
    per_bundle = {name: {"status": "unsupported",
                         "error": f"no {family.label} prefab reader for this package"}
                  for name in paths if name not in names and name != SHADER_PACKAGE}
    for name in names:
        leaf = name[len(family.prefix):]
        try:
            package = store.package(name)
            if package is None:
                raise FileNotFoundError(f"tool package not loaded: {name}")
            missing = [dep for dep in package.dependencies
                       if store.package(dep.replace("/", "__")) is None]
            if missing:
                raise FileNotFoundError(f"tool dependencies not supplied: {', '.join(missing)}")
            directory = out / leaf
            if _linked(directory):
                raise ValueError("tool package output directory must not be a link")
            document = PackageExtract(
                store, name, directory, leaf,
                classification=(family.kind, leaf),
                primary_prefab=f"{leaf}.prefab").run()
            root = next(row for row in document["roots"] if row["primary"])
            document["sourcePrefab"] = {
                "asset": next(path for path in root["assets"]
                              if path.rsplit("/", 1)[-1] == f"{leaf}.prefab"),
                "root": root["name"], "scene": root["scene"],
            }
            document["inventory"]["dependencySource"] = (
                "the package's own AssetBundle object; shader dependencies are read "
                "as input, while the package document retains the material properties")
            entry = _index_entry(family, leaf, document)
            write_json(directory / f"{leaf}.json", document)
            per_bundle[name] = {"status": "succeeded", "json": str(directory / f"{leaf}.json"),
                                "glb": str(directory / document["geometry"]["file"]),
                                "materials": entry["materials"], "textures": len(entry["textures"]),
                                "meshes": entry["meshes"], "vertices": entry["vertices"],
                                "triangles": entry["triangles"], "unsupported": entry["unsupported"]}
        except Exception as exc:
            per_bundle[name] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    index = _index_from_disk(family, out)
    index_path = out / INDEX_NAME
    write_json(index_path, index)
    return {"path": str(index_path), **index["summary"], "perBundle": per_bundle,
            "skippedPackages": index["skippedPackages"]}
