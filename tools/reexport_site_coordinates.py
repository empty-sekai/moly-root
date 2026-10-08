#!/usr/bin/env python3
"""Re-export a prior site's exact corpus into a new isolated canonical tree.

Each package runs in a fresh process, keeping UnityPy state bounded. Existing
site placement/master-derived indices and supplemental assets are copied, not
hard-linked. Every GLB must be regenerated and its geometry compared to the
prior output; compressed old sidecars are deliberately not copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import warnings

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from core.atomic import write_json
from core.coordinate_contract import CONTRACT, validate_glb


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def glb_parts(path):
    blob = Path(path).read_bytes()
    length, = struct.unpack_from("<I", blob, 12)
    document = json.loads(blob[20:20 + length])
    offset = 20 + length
    binary = blob[offset + 8:] if len(blob) > offset else b""
    return document, binary


def without_extras(value):
    if isinstance(value, dict):
        return {key: without_extras(item) for key, item in value.items() if key != "extras"}
    if isinstance(value, list):
        return [without_extras(item) for item in value]
    return value


def collision_identity(row):
    return json.dumps({key: row.get(key) for key in
                       ("root", "node", "component", "mesh", "meshPointer")}, sort_keys=True)


def compare_glb(old, new):
    a, a_bin = glb_parts(old)
    b, b_bin = glb_parts(new)
    fields = ("nodes", "meshes", "accessors", "bufferViews", "skins", "animations", "scenes", "scene")
    changed = [field for field in fields
               if without_extras(a.get(field)) != without_extras(b.get(field))]
    unchanged_existing = all(without_extras(a.get(field, [])) ==
                             without_extras(b.get(field, [])[:len(a.get(field, []))])
                             for field in ("accessors", "bufferViews", "animations"))
    existing_bytes_equal = all(
        a_bin[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]] ==
        b_bin[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
        for view in a.get("bufferViews", []))
    added_animations = b.get("animations", [])[len(a.get("animations", [])):]
    animation_additions_only = (set(changed) <= {"accessors", "bufferViews", "animations"}
        and unchanged_existing and existing_bytes_equal and bool(added_animations)
        and all(animation.get("extras", {}).get("sourceClip") for animation in added_animations))
    errors = validate_glb(new)["errors"]
    obstacles = [entry for node in b.get("nodes", [])
                 for entry in node.get("extras", {}).get("navMeshObstacles", [])]
    return {"oldSha256": sha(old), "newSha256": sha(new),
            "binaryEqual": a_bin == b_bin, "changedGeometryFields": changed,
            "existingBufferBytesEqual": existing_bytes_equal,
            "sourceAnimationAdditionsOnly": animation_additions_only,
            "addedSourceAnimations": [animation.get("extras", {}).get("sourceClip")
                                      for animation in added_animations],
            "contractErrors": errors, "obstacles": len(obstacles),
            "obstacleVersions": sorted({entry.get("schemaVersion", 1) for entry in obstacles})}


def worker(args):
    warnings.filterwarnings("ignore")
    from core.unity import configure_fallback_unity_version
    configure_fallback_unity_version()
    from core.assets.packages import PackageStore
    from sites.scenes import PackageExtract
    prior = read(args.source_site / "packages.json")["packages"][args.package]
    directory = args.out / "site" / prior["directory"]
    # Enumerate old files first; generated receipts are based on actual writes.
    before = {path: path.stat().st_mtime_ns for path in directory.rglob("*") if path.is_file()}
    store = PackageStore([args.raw_root / args.package], root=args.raw_root)
    store.load_dependencies([args.package])
    document = PackageExtract(store, args.package, directory, prior["directory"]).run()
    old_document_path = args.source_site / prior["document"]
    recovered_package = (not old_document_path.is_file() and prior.get("inventory") is None
                         and prior.get("unsupported", 0) > 0
                         and not prior.get("artifacts", {}).get("geometry"))
    old_document = {} if recovered_package else read(old_document_path)
    old_collisions = {collision_identity(row): row for row in old_document.get("collision", [])}
    aliases = {}
    for row in document.get("collision", []):
        old_row = old_collisions.get(collision_identity(row))
        if not old_row or not old_row.get("file") or not row.get("file"):
            continue
        if old_row["file"] != row["file"]:
            # The GLB's content hash changes when contract extras are added.
            # Preserve the historical URL as an alias of verified new bytes.
            new_path, old_path = directory / row["file"], directory / old_row["file"]
            aliases[new_path.relative_to(args.out / "site").as_posix()] = old_path.relative_to(args.out / "site").as_posix()
            shutil.copy2(new_path, old_path)
    written = [path for path in directory.rglob("*") if path.is_file()
               and (path not in before or path.stat().st_mtime_ns != before[path])]
    comparisons = {}
    failures = []
    for path in written:
        if path.suffix != ".glb":
            continue
        relative = path.relative_to(args.out / "site").as_posix()
        old = args.source_site / aliases.get(relative, relative)
        if not old.is_file():
            if recovered_package:
                comparisons[relative] = {"recoveredPriorFailure": True,
                                        "newSha256": sha(path),
                                        "contractErrors": validate_glb(path)["errors"]}
                if comparisons[relative]["contractErrors"]:
                    failures.append(f"new GLB contract failed: {relative}")
                continue
            failures.append(f"unexpected GLB path: {relative}")
            continue
        comparison = compare_glb(old, path)
        comparisons[relative] = comparison
        geometry_changed = (not comparison["binaryEqual"] or comparison["changedGeometryFields"])
        if ((geometry_changed and not comparison["sourceAnimationAdditionsOnly"])
                or comparison["contractErrors"]
                or any(version != 2 for version in comparison["obstacleVersions"])):
            failures.append(f"GLB comparison failed: {relative}")
    for field in ("meshes", "vertices", "triangles", "scenes", "defaultScene"):
        if not recovered_package and (old_document.get("geometry") or {}).get(field) != (document.get("geometry") or {}).get(field):
            failures.append(f"geometry count changed: {field}")
    old_objects, new_objects = old_document.get("objects") or {}, document.get("objects") or {}
    if (not new_objects.get("accountedFor") or (not recovered_package and
            (new_objects.get("total") != old_objects.get("total")
             or new_objects.get("unsupported", 0) > old_objects.get("unsupported", 0)))):
        failures.append("object accounting lost or unsupported objects increased")
    if not recovered_package and len(document.get("unsupported", [])) > len(old_document.get("unsupported", [])):
        failures.append("unsupported extraction gaps increased")
    prior_missing = read(args.source_site / "index.json").get("summary", {}).get("missing", {}).get("dependencies", [])
    new_missing = sorted(set(store.missing) - set(prior_missing))
    if new_missing:
        failures.append(f"new missing dependencies: {new_missing}")
    report = {"package": args.package, "rawSha256": sha(args.raw_root / args.package),
              "document": document["file"], "written": [path.relative_to(args.out / "site").as_posix()
              for path in written], "glbs": comparisons, "failures": failures,
              "objects": new_objects, "oldObjects": old_objects, "legacyCollisionAliases": aliases,
              "recoveredPriorFailure": recovered_package,
              "preservedMissingDependencies": sorted(set(store.missing) & set(prior_missing)),
              "unsupported": len(document.get("unsupported", []))}
    write_json(args.out / "receipts" / f"{args.package}.json", report)
    print(json.dumps({"package": args.package, "glbs": len(comparisons), "failures": failures}), flush=True)
    return int(bool(failures))


def refresh_indices(args, packages):
    """Refresh extraction-derived census fields; preserve all master joins."""
    from sites.harvest import harvest_document
    site = args.out / "site"
    census = read(site / "packages.json")
    index = read(site / "index.json")
    documents = []
    for name, prior in packages.items():
        document = read(site / prior["document"])
        document["file"] = prior["document"]
        documents.append(document)
        geometry = document.get("geometry") or {}
        entry = census["packages"][name]
        entry.update(inventory=document["inventory"], objects=document["objects"],
                     skipped=document.get("skipped", []), unsupported=len(document["unsupported"]))
        entry["artifacts"] = {"geometry": geometry.get("file"),
            "collision": len(document["collision"]), "navmesh": len(document["navmesh"]),
            "textures": len(document["textures"]), "materials": len(document["materials"]),
            "clips": len(document["animations"]["clips"])}
        for family in index["families"].values():
            for row in family:
                if row["package"] == name:
                    row["geometry"] = (f"{prior['directory']}/{geometry['file']}" if geometry else None)
    totals = index["summary"]
    for key in ("geometryFiles", "meshes", "vertices", "triangles", "collisionSurfaces", "navmeshes",
                "navmeshTiles", "heightMeshes", "materials", "textures", "emitters", "clips", "skins"):
        totals[key] = 0
    totals["unsupported"], totals["omitted"] = [], []
    for document in documents:
        geometry = document.get("geometry") or {}
        totals["geometryFiles"] += bool(geometry)
        for key in ("meshes", "vertices", "triangles"):
            totals[key] += geometry.get(key, 0)
        for target, source in (("collisionSurfaces", "collision"), ("navmeshes", "navmesh"),
                               ("materials", "materials"), ("textures", "textures"),
                               ("emitters", "particles"), ("skins", "skins")):
            totals[target] += len(document.get(source, []))
        totals["clips"] += len(document["animations"]["clips"])
        for nav in document["navmesh"]:
            totals["navmeshTiles"] += nav["tiles"]["count"]
            totals["heightMeshes"] += len(nav["heightMeshes"])
        for key in ("unsupported", "omitted"):
            totals[key].extend(dict(gap, package=document["package"]) for gap in document.get(key, []))
    harvest = read(site / "harvest.json")
    refreshed = harvest_document(documents)
    for name, entry in refreshed["packages"].items():
        old = harvest["packages"].get(name, {})
        for field in ("masterRows", "materialRows"):
            entry[field] = old.get(field)
    harvest["packages"] = refreshed["packages"]
    harvest["summary"].update(refreshed["summary"])
    write_json(site / "packages.json", census)
    write_json(site / "index.json", index)
    write_json(site / "harvest.json", harvest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-site", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--package", help=argparse.SUPPRESS)
    parser.add_argument("--resume", action="store_true", help="Resume this tool's own scratch plan only")
    args = parser.parse_args(argv)
    if args.package:
        return worker(args)
    if args.out.exists() and not args.resume:
        parser.error("--out must not exist; existing outputs are never overwritten")
    packages = read(args.source_site / "packages.json")["packages"]
    missing = [name for name in packages if not (args.raw_root / name).is_file()]
    if missing:
        parser.error(f"original raw bundles missing: {missing}")
    started = time.monotonic()
    plan = {"coordinateContract": CONTRACT, "sourceSite": str(args.source_site.resolve()),
            "rawRoot": str(args.raw_root.resolve()), "packages": sorted(packages),
            "policy": "Exact prior selection; original bytes re-exported; master-derived indices preserved"}
    if args.resume:
        if read(args.out / "plan.json") != plan:
            parser.error("resume plan differs from the existing scratch export")
    else:
        shutil.copytree(args.source_site, args.out / "site", ignore=shutil.ignore_patterns("*.gz"))
        (args.out / "receipts").mkdir()
        write_json(args.out / "plan.json", plan)
    failed = []
    written = set()
    glbs = {}
    for index, name in enumerate(sorted(packages), 1):
        receipt = args.out / "receipts" / f"{name}.json"
        if args.resume and receipt.is_file():
            report = read(receipt)
            if not report["failures"]:
                written.update(report["written"])
                glbs.update(report["glbs"])
                continue
        command = [sys.executable, str(Path(__file__).resolve()), "--source-site", str(args.source_site),
                   "--raw-root", str(args.raw_root), "--out", str(args.out), "--package", name]
        result = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if receipt.is_file():
            report = read(receipt)
            written.update(report["written"])
            glbs.update(report["glbs"])
        if result.returncode:
            failed.append({"package": name, "exit": result.returncode,
                           "stderr": result.stderr[-4000:], "stdout": result.stdout[-4000:]})
        print(f"[{index}/{len(packages)}] {name}: {'FAIL' if result.returncode else 'ok'}", flush=True)
        write_json(args.out / "progress.json", {"done": index, "total": len(packages), "failed": failed})
    old_glbs = {path.relative_to(args.source_site).as_posix() for path in args.source_site.rglob("*.glb")}
    not_regenerated = sorted(old_glbs - set(glbs))
    if not failed and not not_regenerated:
        refresh_indices(args, packages)
    preserved = sorted(path.relative_to(args.out / "site").as_posix()
                       for path in (args.out / "site").rglob("*") if path.is_file()
                       and path.relative_to(args.out / "site").as_posix() not in written)
    summary = {**plan, "seconds": round(time.monotonic() - started, 2), "failed": failed,
               "glbCount": len(glbs), "notRegenerated": not_regenerated,
               "preservedFiles": preserved, "glbs": glbs,
               "passed": not failed and not not_regenerated}
    write_json(args.out / "report.json", summary)
    print(json.dumps({key: summary[key] for key in ("passed", "glbCount", "notRegenerated", "seconds")}), flush=True)
    return int(not summary["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
