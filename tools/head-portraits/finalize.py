"""Validate and finalize chara viewer captures; never crop a body screenshot."""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageDraw, __version__ as pillow_version

GENERATOR = "moly-root-chara-head-v1"
TOOL_ROOT = Path(__file__).resolve().parent
VIEWER_ROOT = TOOL_ROOT.parents[1] / "examples" / "viewer"


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def local_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name or any(part in ("..", "") for part in name.split("/")):
        raise ValueError(f"Expected a relative asset path: {name}")
    resolved = (root / name).resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise ValueError(f"Asset escapes source directory: {name}")
    return resolved


def check_hashes(root: Path, values: dict[str, str]) -> None:
    if not values:
        raise ValueError("Missing input SHA-256 inventory")
    for name, expected in values.items():
        if digest(local_file(root, name).read_bytes()) != expected:
            raise ValueError(f"SHA-256 mismatch: {name}")


def image_metrics(image: Image.Image, projected: dict | None = None) -> dict:
    if image.mode != "RGBA" or image.size != (512, 512):
        raise ValueError("Portrait must be 512 x 512 RGBA")
    alpha = image.getchannel("A")
    counts = alpha.histogram()
    if not counts[0] or counts[255] <= 10000:
        raise ValueError("Portrait must contain both transparent background and an opaque head")
    # Hair may leave the frame on any side; the head may not. The capture
    # reports the head's own projected rectangle, so that rectangle — and not
    # "any opaque pixel" — is what this validator protects. `headBoundaryPixels`
    # therefore stays the count of pixels where the HEAD reaches a border (0 by
    # the rectangle proof below), while hair overflow is reported separately.
    border = sum(sum(alpha.crop(box).histogram()[1:]) for box in
                 ((0, 0, 512, 1), (0, 0, 1, 512), (511, 0, 512, 512), (0, 511, 512, 512)))
    boundary = 0
    if projected is None:
        # Legacy captures predate the projected rectangle: fall back to the
        # stricter "nothing on the top or the sides" rule they were made under.
        boundary = sum(sum(alpha.crop(box).histogram()[1:]) for box in
                       ((0, 0, 512, 1), (0, 0, 1, 512), (511, 0, 512, 512)))
        if boundary:
            raise ValueError("Head boundary clipping (capture has no projected rectangle)")
        # The bottom edge is permitted trailing hair in the legacy contract.
        # Keep it in hairOnBorderPixels, not the protected head-boundary count.
    else:
        margin = 1
        if (projected["left"] < margin or projected["top"] < margin
                or projected["right"] > 512 - margin or projected["bottom"] > 512 - margin):
            raise ValueError(f"Head clipping: projected head rectangle {projected} leaves the frame")
    return {"width": 512, "height": 512, "transparentPixels": counts[0], "opaquePixels": counts[255],
            "edgePixels": sum(counts[1:255]), "bounds": list(alpha.getbbox()),
            "headBoundaryPixels": boundary, "hairOnBorderPixels": border, "projectedHead": projected}


def validate_capture(capture: dict, source: Path, out: Path) -> None:
    if capture.get("generator") != GENERATOR:
        raise ValueError("Unsupported portrait generator")
    check_hashes(source, capture["sourceSha256"])
    check_hashes(VIEWER_ROOT, capture["rendererSha256"])
    check_hashes(TOOL_ROOT, capture["toolingSha256"])
    meta = json.loads(local_file(source, "mysekai-fixtures.json").read_text(encoding="utf-8"))
    if (meta["region"], meta["gameVersion"]) != (capture["region"], capture["version"]):
        raise ValueError("Region/version provenance mismatch")
    roster = json.loads(local_file(source, "manifest.json").read_text(encoding="utf-8"))["units"]
    source_entries = {int(entry["unit"]): entry for entry in roster}
    rows = capture["portraits"]
    if len(rows) != capture["expected"] or not rows or len(roster) != capture["rosterCount"]:
        raise ValueError("Portrait coverage mismatch")
    if len({row["unit"] for row in rows}) != len(rows) or len({row["unitCode"] for row in rows}) != len(rows):
        raise ValueError("Duplicate portrait unit")
    for row in rows:
        entry = source_entries[row["unitCode"]]
        rig = json.loads(local_file(source, row["rig"]).read_text(encoding="utf-8"))
        if row["unit"] != (rig.get("unitId") or row["unitCode"] - 100):
            raise ValueError("Rig unit identity mismatch")
        if entry["glb"] != row["model"] or entry["rig"] != row["rig"]:
            raise ValueError("Model/rig source identity mismatch")
        for field in ("model", "rig"):
            if capture["sourceSha256"].get(row[field]) != row[field + "Sha256"]:
                raise ValueError(f"Missing/mismatched {field} source hash")
        if digest(local_file(out, row["sourceFile"]).read_bytes()) != row["sourceSha256"]:
            raise ValueError(f"Capture SHA-256 mismatch: {row['sourceFile']}")
        diagnostics = row["diagnostics"]
        if diagnostics.get("mode") != "head-only" or diagnostics.get("unit") != row["unit"]:
            raise ValueError("Capture is not a head-only viewer render")
        if not sum(mesh["keptTriangles"] for mesh in diagnostics["meshes"]):
            raise ValueError("Capture retained no head triangles")


def final_image(out: Path, row: dict) -> tuple[Image.Image, bytes]:
    with Image.open(local_file(out, row["sourceFile"])) as source_image:
        if source_image.mode != "RGBA" or source_image.size != (1024, 1024):
            raise ValueError("Expected original 2x WebGL RGBA capture, without cropping")
        image = source_image.resize((512, 512), Image.Resampling.LANCZOS)
    image_metrics(image, projected_head(row))
    png = BytesIO()
    image.save(png, format="PNG", optimize=True)
    return image, png.getvalue()


def projected_head(row: dict) -> dict | None:
    """The head's own pixel rectangle, reported by the capture pipeline."""
    diagnostics = row.get("diagnostics") or {}
    normalization = diagnostics.get("normalization") or {}
    projected = normalization.get("projected")
    if projected is None:
        return None
    for key in ("left", "top", "right", "bottom"):
        if not isinstance(projected.get(key), int):
            raise ValueError(f"Projected head rectangle is missing {key}")
    return projected


def finalize(source: Path, out: Path, verify: bool = False) -> dict:
    capture = json.loads((out / "capture.json").read_text(encoding="utf-8"))
    validate_capture(capture, source, out)
    photos = []
    images = []
    # Validate every input before writing anything, then publish manifest last.
    for row in capture["portraits"]:
        image, png = final_image(out, row)
        filename = f"unit-{row['unit']}.png"
        photos.append({"unit": row["unit"], "model": row["model"], "rig": row["rig"], "file": filename,
                       **image_metrics(image, projected_head(row)), "sha256": digest(png), "modelSha256": row["modelSha256"],
                       "rigSha256": row["rigSha256"], "capture": row["diagnostics"], "facial": row["facial"]})
        images.append((filename, image, png))
    first = capture["portraits"][0]["diagnostics"]
    normalization = first.get("normalization") or {}
    manifest = {"schemaVersion": 1, "generator": GENERATOR, "region": capture["region"], "version": capture["version"],
                "expected": capture["expected"], "rosterCount": capture["rosterCount"], "size": [512, 512],
                "alpha": "RGBA straight; original WebGL canvas, no chroma key",
                "preset": {"id": "head-only-crown-to-chin-v2", "fovDegrees": normalization.get("fovDegrees", 35),
                           "supersampling": 2, "bodyGeometry": False,
                           "framing": "crown-to-chin normalized to a fixed frame fraction and centred; hair may leave the frame, the head is never cut",
                           "lighting": first.get("lighting"),
                           "normalization": {"heightFraction": normalization.get("heightFraction"),
                                             "framePixels": normalization.get("framePixels")},
                           "materials": "moly-root chara viewer toon/facial/stencil, unchanged",
                           "toneMapping": "None", "colorSpace": "LinearSRGB"},
                "browserVersion": capture["browserVersion"], "rendering": capture["rendering"], "pillowVersion": pillow_version,
                "sourceSha256": capture["sourceSha256"], "rendererSha256": capture["rendererSha256"],
                "toolingSha256": capture["toolingSha256"], "optionalMissingResources": capture["optionalMissingResources"],
                "portraits": photos}
    if verify:
        existing = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError("Manifest does not match the current captures, sources, renderer or image library")
        for filename, _, png in images:
            if local_file(out, filename).read_bytes() != png:
                raise ValueError(f"Final portrait differs from deterministic capture conversion: {filename}")
    else:
        for filename, _, png in images:
            (out / filename).write_bytes(png)
        sheets(out, photos, images)
        temporary = out / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(out / "manifest.json")
    return {"region": capture["region"], "count": len(photos), "rosterCount": capture["rosterCount"],
            "bytes": sum(len(png) for _, _, png in images),
            # The head rectangle is inside every frame (validated above); this
            # count is the strand-level overflow the preset deliberately allows.
            "zeroHeadBoundary": True, "hairOnBorderPixels": sum(p["hairOnBorderPixels"] for p in photos),
            "verified": verify}


def sheets(out: Path, photos: list, images: list) -> None:
    for theme, color, ink in (("light", (247, 249, 251, 255), (28, 35, 40)),
                              ("dark", (24, 28, 33, 255), (225, 233, 241))):
        sheet = Image.new("RGBA", (8 * 160, ((len(images) + 7) // 8) * 174), color)
        draw = ImageDraw.Draw(sheet)
        for index, (row, (_, image, _)) in enumerate(zip(photos, images)):
            x, y = (index % 8) * 160 + 11, (index // 8) * 174 + 7
            sheet.alpha_composite(image.resize((138, 138), Image.Resampling.LANCZOS), (x, y))
            draw.text((x, y + 141), f"unit {row['unit']:02d}", fill=ink)
        sheet.convert("RGB").save(out / f"qa-{theme}.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verify", action="store_true", help="Read-only: check source hashes, pixels, output hashes and manifest")
    args = parser.parse_args()
    print(json.dumps(finalize(args.source.resolve(), args.out.resolve(), args.verify), indent=2))


if __name__ == "__main__":
    main()
