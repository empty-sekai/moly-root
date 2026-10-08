#!/usr/bin/env python3
"""Independent read-only closure checks for a composed canonical snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.coordinate_contract import validate_snapshot, read_glb


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(root):
    root = Path(root).resolve()
    coordinate = validate_snapshot(root)
    errors = list(coordinate["errors"])
    image_count = external_images = 0
    for row in coordinate["inventory"]:
        path = root / row["path"]
        document = read_glb(path)
        for image in document.get("images", []):
            image_count += 1
            uri = image.get("uri")
            if not uri or uri.startswith("data:"):
                continue
            external_images += 1
            target = (path.parent / unquote(uri)).resolve()
            if "://" in uri or not target.is_relative_to(root) or not target.is_file():
                errors.append(f"{row['path']}: missing/unsafe image URI {uri}")
    routes = read(root / "actor-animations/index.json")
    for row in routes.get("libraries", []):
        for key, hash_key in (("glb", "glbSha256"), ("index", "indexSha256")):
            if digest(root / row[key]) != row[hash_key]:
                errors.append(f"actor route hash differs: {row[key]}")
    source = read(root / "source.json")
    if digest(root / "actor-animations/index.json") != source.get("actorAnimations", {}).get("indexSha256"):
        errors.append("source actorAnimations.indexSha256 differs")
    if source.get("weather", {}).get("indexSha256") and digest(root / "phenomena/index.json") != source["weather"]["indexSha256"]:
        errors.append("source weather.indexSha256 differs")
    fixture = read(root / "fixture-models/index.json")
    attach = read(root / "fixture-attach/attach-points.json")
    missing = sorted(set(fixture["packages"]) - set(attach["packages"]))
    if missing:
        errors.append(f"fixture packages absent from attach: {missing[:10]}")
    views = [view for row in attach["packages"].values() for view in row.get("views", [])]
    pairs = sum(len(row.get("entries", [])) for row in attach["packages"].values())
    if pairs != attach["summary"]["pairs"]:
        errors.append("attach summary pair count differs")
    return {"root": str(root), "checkedGlbs": coordinate["checkedGlbs"],
            "images": image_count, "externalImageReferences": external_images,
            "actorLibraries": len(routes.get("libraries", [])), "fixturePackages": len(fixture["packages"]),
            "attachViews": len(views), "attachPairs": pairs, "errors": errors,
            "notChecked": coordinate["notChecked"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    report = audit(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(bool(report["errors"]))
