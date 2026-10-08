"""Deterministic furniture coverage joined by master fixture ID.

This module deliberately distinguishes a definition from a runtime-usable model.
It never turns an absent route or a mesh-free authored package into a success.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from core.atomic import PUBLIC_FILE_MODE, write_json


def _load(path: Path, *, required: bool = False):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise
        return {}


def _package_status(package: str, packages: dict) -> tuple[str, str | None, dict]:
    record = packages.get(package)
    if record is None:
        return "missing", "no extracted package with the master assetbundleName", {}
    status = record.get("status")
    if status == "exported" and record.get("vertexCount", 0) > 0:
        return "available", None, record
    if status == "no-mesh":
        return "unsupported", "source package contains no Mesh or SkinnedMeshRenderer geometry", record
    if status == "exported":
        return "unsupported", "exported package has no vertices", record
    return "failed", record.get("error") or f"fixture-model index status is {status!r}", record


def _component(package: str, records: dict, *, absent: str):
    if package not in records:
        return {"status": "missing", "reason": absent}
    return {"status": "available", "reason": None}


def build_coverage(data_root, *, region=None, source_version=None):
    """Build exact master/model/thumbnail/runtime coverage from extracted indexes."""
    root = Path(data_root)
    master = _load(root / "mysekai-fixtures.json", required=True)
    masters = master.get("fixtures") if isinstance(master, dict) else master
    if not isinstance(masters, list):
        raise ValueError("mysekai-fixtures.json must contain a fixtures array")
    ids = [row.get("id") for row in masters]
    if any(type(value) is not int or value <= 0 for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("fixture master IDs must be unique positive integers")

    models = _load(root / "fixture-models" / "index.json").get("packages", {})
    thumbs_doc = _load(root / "fixture-thumbnails" / "fixture-thumbnails.json")
    thumbs = {row["fixtureId"]: row for row in thumbs_doc.get("fixtures", [])}
    areas = _load(root / "fixture-interface" / "areas.json").get("packages", {})
    attach = _load(root / "fixture-interface" / "attach-points.json").get("packages", {})
    particles = _load(root / "fixture-particles" / "index.json").get("packages", {})
    gimmicks_doc = _load(root / "fixture-gimmick" / "gimmicks.json")
    gimmicks = {row.get("name"): row for row in gimmicks_doc.get("packages", [])}

    rows = []
    counts = Counter()
    for master_row in sorted(masters, key=lambda row: row["id"]):
        fixture_id = master_row["id"]
        asset = master_row.get("assetbundleName")
        if not isinstance(asset, str) or not asset:
            package = None
            model_status, model_reason, model = "missing", "master row has no assetbundleName", {}
        else:
            package = "mysekai__fixture__" + asset
            model_status, model_reason, model = _package_status(package, models)
        thumb = thumbs.get(fixture_id)
        thumbnail = ({"status": "available", "reason": None,
                      "variants": thumb.get("variants", [])}
                     if thumb else {"status": "missing",
                                    "reason": "no exact thumbnail route joined this master ID",
                                    "variants": []})
        presentation = master_row.get("preview")
        runtime = "available" if model_status == "available" else model_status
        if isinstance(presentation, dict) and presentation.get("kind") in ("surface", "custom"):
            runtime = "available" if presentation.get("available") else "missing"
        route = presentation.get("kind", "model") if isinstance(presentation, dict) else "model"
        counts["definitions"] += 1
        counts[f"models.{model_status}"] += 1
        counts[f"thumbnails.{thumbnail['status']}"] += 1
        counts[f"runtime.{runtime}"] += 1
        counts[f"presentation.{route}"] += 1
        counts["modelVariants"] += len(model.get("variants", []))
        counts["thumbnailVariants"] += len(thumbnail["variants"])
        animation = model.get("animations", {})
        rows.append({
            "fixtureId": fixture_id,
            "fixtureType": master_row.get("fixtureType"),
            "assetbundleName": asset,
            "package": package,
            "definition": {"status": "available", "reason": None},
            "model": {"status": model_status, "reason": model_reason,
                      "file": model.get("glb"), "sourceVariants": model.get("variants", []),
                      "vertices": model.get("vertexCount", 0), "meshes": model.get("meshCount", 0)},
            "thumbnail": thumbnail,
            "areas": (_component(package, areas, absent="package has no extracted area record")
                      if package else {"status": "missing", "reason": "master has no package identity"}),
            "attachments": (_component(package, attach, absent="package has no extracted attachment record")
                            if package else {"status": "missing", "reason": "master has no package identity"}),
            "animations": {"status": "available" if animation.get("clipCount", 0) else "not-present",
                           "reason": None if animation.get("clipCount", 0) else "source package has no animation clips",
                           "clips": animation.get("clipCount", 0),
                           "channels": animation.get("gltfChannels", 0),
                           "unresolvedSlots": animation.get("unresolvedSlots", 0)},
            "particles": (_component(package, particles, absent="package has no particle census record")
                          if package else {"status": "missing", "reason": "master has no package identity"}),
            "gimmick": (_component(package, gimmicks, absent="source package has no extracted gimmick metadata")
                        if package else {"status": "missing", "reason": "master has no package identity"}),
            "runtime": {"status": runtime,
                        "reason": None if runtime == "available" else model_reason},
            "presentation": presentation,
        })

    source = {
        "region": region or master.get("region") or thumbs_doc.get("region"),
        "sourceVersion": source_version or master.get("gameVersion") or thumbs_doc.get("gameVersion"),
    }
    summary = {
        "definitions": counts["definitions"],
        "distinctMasterBundles": len({row["assetbundleName"] for row in rows if row["assetbundleName"]}),
        "models": {key: counts[f"models.{key}"] for key in ("available", "missing", "unsupported", "failed")},
        "modelVariants": counts["modelVariants"],
        "thumbnails": {key: counts[f"thumbnails.{key}"] for key in ("available", "missing")},
        "thumbnailVariants": counts["thumbnailVariants"],
        "runtime": {key: counts[f"runtime.{key}"] for key in ("available", "missing", "unsupported", "failed")},
        "presentationRoutes": {key: counts[f"presentation.{key}"] for key in ("model", "surface", "custom")},
    }
    return {"version": 1, "kind": "fixture-coverage", "source": source,
            "summary": summary, "fixtures": rows}


def write_coverage(data_root, output=None, **identity):
    root = Path(data_root)
    target = Path(output) if output else root / "fixture-coverage.json"
    document = build_coverage(root, **identity)
    write_json(target, document, mode=PUBLIC_FILE_MODE)
    return target, document
