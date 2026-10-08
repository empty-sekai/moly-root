#!/usr/bin/env python3
"""Shared read-only validator for the canonical spatial contract.

This tool validates metadata and the numeric source-to-canonical boundary.  It
never rewrites an asset and never infers a contract from a determinant or from
file names.  Missing metadata is an error, which keeps legacy snapshots on an
explicit adapter path instead of silently mixing bases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path

from core.fixture_collision_document import read_fixture_collision

CONTRACT = "moly-rh-y-up-reflect-x-v1"
SOURCE = "unity-lh-y-up-authored"
UNITS = "source-unity-unit"


def _reflect_position(value):
    return [-value[0], value[1], value[2]]


def _reflect_quaternion(value):
    return [value[0], -value[1], -value[2], value[3]]


def _approx(left, right, eps=2e-5):
    return len(left) == len(right) and all(
        math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a) - float(b)) <= eps
        for a, b in zip(left, right)
    )


def read_glb(path):
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(f"{path}: not a glTF binary")
    length = struct.unpack_from("<I", data, 8)[0]
    chunk_length, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON" or length > len(data) or 20 + chunk_length > len(data):
        raise ValueError(f"{path}: malformed JSON chunk")
    return json.loads(data[20:20 + chunk_length].decode("utf-8"))


def _check_contract(value, label, errors, units_field="coordinateUnits"):
    if not isinstance(value, dict):
        errors.append(f"{label}: missing extras object")
        return
    if value.get("coordinateContract") != CONTRACT:
        errors.append(f"{label}: coordinateContract is not {CONTRACT!r}")
    if value.get(units_field) != UNITS:
        errors.append(f"{label}: {units_field} is not {UNITS!r}")


def _source_vector(value, width):
    if isinstance(value, dict):
        value = [value[channel] for channel in "xyzw"[:width]]
    if not isinstance(value, (list, tuple)) or len(value) != width:
        raise ValueError(f"expected vector width {width}")
    return [float(component) for component in value]


def _check_source_pose(canonical, source, label, errors, position_key, rotation_key, scale_key):
    if not isinstance(source, dict) or source.get("coordinateSpace") != SOURCE:
        errors.append(f"{label}: sourceTransform is not tagged {SOURCE!r}")
        return
    for key, width, convert in ((position_key, 3, _reflect_position),
                                (rotation_key, 4, _reflect_quaternion),
                                (scale_key, 3, lambda value: value)):
        try:
            raw = _source_vector(source[key], width)
            actual = _source_vector(canonical[key], width)
            expected = convert(raw)
        except (KeyError, TypeError, ValueError):
            errors.append(f"{label}: malformed {key} or source evidence")
        else:
            if not _approx(actual, expected):
                errors.append(f"{label}: {key} does not match source conversion")


def _check_house_views(document, label, errors):
    _check_contract(document, label, errors)
    if document.get("schemaVersion") != 2:
        errors.append(f"{label}: houseViews schemaVersion must be 2")
    for index, row in enumerate(document.get("transforms", [])):
        if row.get("coordinateContract") != CONTRACT:
            errors.append(f"{label}:transform[{index}] contract missing")
        if "sourceTransform" in row:
            _check_source_pose(row, row["sourceTransform"], f"{label}:transform[{index}]",
                               errors, "localPosition", "localRotation", "localScale")


def _check_obstacles(rows, label, errors):
    for index, row in enumerate(rows):
        row_label = f"{label}[{index}]"
        _check_contract(row, row_label, errors)
        if row.get("schemaVersion") != 2:
            errors.append(f"{row_label}: obstacle schemaVersion must be 2")
        source = row.get("sourceGeometry")
        if source is not None:
            if source.get("coordinateSpace") != SOURCE:
                errors.append(f"{row_label}: sourceGeometry source space missing")
            for key, convert in (("center", _reflect_position), ("extents", lambda value: value)):
                try:
                    expected = convert(_source_vector(source[key], 3))
                    actual = _source_vector(row[key], 3)
                except (KeyError, TypeError, ValueError):
                    errors.append(f"{row_label}: malformed {key}")
                else:
                    if not _approx(actual, expected):
                        errors.append(f"{row_label}: {key} does not match source conversion")


def validate_glb(path, require_fixture_collision_ref=False):
    errors, checked = [], 0
    glb = read_glb(path)
    _check_contract(glb.get("asset", {}).get("extras"), f"{path}:asset", errors)
    for index, scene in enumerate(glb.get("scenes", [])):
        for root in scene.get("nodes", []):
            if not isinstance(root, int) or root < 0 or root >= len(glb.get("nodes", [])):
                errors.append(f"{path}:scene[{index}] invalid root node {root!r}")
                continue
            _check_contract(glb["nodes"][root].get("extras"), f"{path}:root[{root}]", errors)
    for index, node in enumerate(glb.get("nodes", [])):
        extras = node.get("extras", {})
        source = extras.get("sourceTransform")
        if source is not None:
            _check_source_pose(node, source, f"{path}:node[{index}]", errors,
                               "translation", "rotation", "scale")
        if "houseViews" in extras:
            _check_house_views(extras["houseViews"], f"{path}:node[{index}].houseViews", errors)
        if "navMeshObstacles" in extras:
            _check_obstacles(extras["navMeshObstacles"], f"{path}:node[{index}].navMeshObstacles", errors)
        checked += 1
    collision_extras = glb.get("extras", {})
    if require_fixture_collision_ref and (
            not isinstance(collision_extras.get("fixtureCollisionRef"), dict)
            or "fixtureCollision" in collision_extras):
        errors.append(f"{path}: fixture index v4 requires detached collision reference storage")
    if "fixtureCollision" in collision_extras or "fixtureCollisionRef" in collision_extras:
        try:
            collision = read_fixture_collision(glb)
        except ValueError as error:
            errors.append(f"{path}:{error}")
            collision = {}
        _check_contract(collision, f"{path}:fixtureCollision", errors, units_field="units")
        for geometry in collision.get("geometry", []):
            if geometry.get("coordinateContract", CONTRACT) != CONTRACT:
                errors.append(f"{path}:collision geometry {geometry.get('geometryId')}: wrong contract")
    if "houseViews" in glb.get("extras", {}):
        _check_house_views(glb["extras"]["houseViews"], f"{path}:houseViews", errors)
    return {"path": str(path), "checkedNodes": checked, "errors": errors}


def validate_index(index_path):
    index_path = Path(index_path)
    document = json.loads(index_path.read_text(encoding="utf-8"))
    errors = []
    if document.get("version") not in (3, 4) or document.get("coordinateContract") != CONTRACT:
        errors.append(f"{index_path}: index is not coordinate contract v3/v4")
    _check_contract(document, str(index_path), errors)
    reports = []
    for name, package in sorted(document.get("packages", {}).items()):
        if package.get("status") == "failed":
            errors.append(f"{index_path}:{name}: package export failed")
        if package.get("status") == "exported" and package.get("coordinateContract") != CONTRACT:
            errors.append(f"{index_path}:{name}: package contract missing")
        glb = package.get("glb")
        if package.get("status") == "exported":
            if not isinstance(glb, str) or not glb:
                errors.append(f"{index_path}:{name}: exported package has no GLB")
                continue
            glb_path = (index_path.parent / glb).resolve()
            if not glb_path.is_relative_to(index_path.parent.resolve()):
                errors.append(f"{index_path}:{name}: GLB escapes fixture directory")
                continue
            reports.append(_safe_validate(
                lambda path: validate_glb(path, require_fixture_collision_ref=document.get("version") == 4),
                glb_path))
    errors.extend(error for report in reports for error in report["errors"])
    return {"kind": "fixture-index", "path": str(index_path), "errors": errors, "glbs": reports}


def validate_attach(path):
    path = Path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    if document.get("version") != 2 or document.get("coordinateContract") != CONTRACT:
        errors.append(f"{path}: attach document is not coordinate contract v2")
    _check_contract(document, str(path), errors)
    for package, value in document.get("packages", {}).items():
        for view in value.get("views", []):
            if view.get("transform") is None:
                continue
            label = f"{path}:{package}:view[{view.get('component')}]"
            if view.get("coordinateContract") != CONTRACT or view.get("placementRoot") is not True:
                errors.append(f"{label}: missing canonical placement-root contract")
            source_scale = view.get("sourceLocalScale") or {}
            try:
                valid_scale = source_scale.get("coordinateSpace") == SOURCE and _approx(
                    _source_vector(view["localScale"], 3), _source_vector(source_scale["value"], 3))
            except (KeyError, ValueError, TypeError):
                valid_scale = False
            if not valid_scale:
                errors.append(f"{label}: localScale does not match tagged source scale")
        for entry in value.get("entries", []):
            for side in ("start", "end"):
                transform = (entry.get(side) or {}).get("transform")
                if transform is None:
                    continue
                if transform.get("coordinateSpace") != CONTRACT:
                    errors.append(f"{path}:{package}:{entry.get('id')}:{side}: wrong coordinate space")
                source = (entry.get(side) or {}).get("sourceTransform")
                if source is not None:
                    _check_source_pose(transform, source, f"{path}:{package}:{entry.get('id')}:{side}",
                                       errors, "position", "rotation", "scale")
    return {"kind": "attach-points", "path": str(path), "errors": errors}


def validate_json_subcontract(path, key):
    path = Path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    if key == "transforms":
        _check_house_views(document, str(path), errors)
    elif key == "navMeshObstacles":
        _check_obstacles(document.get(key, []), str(path), errors)
    return {"kind": key, "path": str(path), "errors": errors}


def _safe_validate(validate, path):
    try:
        return validate(path)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        return {"path": str(path), "errors": [f"{path}: {type(exc).__name__}: {exc}"]}


def _publication_path(relative):
    """Exclude evidence/backups, never silently exclude an ordinary GLB."""
    return all(not part.startswith(".") and not part.startswith("_previous")
               and not any(marker in part for marker in (".bak", ".staging", ".pre-"))
               for part in relative.parts)


def validate_snapshot(root):
    """Gate a complete runtime tree without writing or relabelling any asset.

    Metadata declares the exporter boundary. Existing tagged numeric source
    evidence must agree, but this does not reconstruct source mesh/animation
    values or prove Unity collision-cooking parity.
    """
    root = Path(root).resolve()
    reports = [
        _safe_validate(validate_index, root / "fixture-models/index.json"),
        _safe_validate(validate_attach, root / "fixture-attach/attach-points.json"),
    ]
    files = sorted((path for path in root.rglob("*.glb")
                    if _publication_path(path.relative_to(root))),
                   key=lambda path: path.relative_to(root).as_posix())
    if not files:
        reports.append({"path": str(root), "errors": [f"{root}: no spatial GLBs found"]})
    inventory = []
    for path in files:
        reports.append(_safe_validate(validate_glb, path))
        try:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            inventory.append({"path": path.relative_to(root).as_posix(), "sha256": digest})
        except OSError as exc:
            reports.append({"path": str(path), "errors": [f"{path}: {exc}"]})
    errors = [error for report in reports for error in report["errors"]]
    return {"coordinateContract": CONTRACT, "coordinateUnits": UNITS,
            "status": "ready" if not errors else "invalid", "root": str(root),
            "scope": "required fixture index/attach and every publishable GLB in the runtime tree",
            "notChecked": ["Unity native collision cooking/navigation parity",
                           "runtime source-placement overrides",
                           "numeric mesh/animation source parity without source evidence"],
            "checkedGlbs": len(files), "inventory": inventory, "reports": reports, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index")
    parser.add_argument("--attach")
    parser.add_argument("--house-views")
    parser.add_argument("--nav-obstacles")
    parser.add_argument("--snapshot", help="strict whole-runtime publication readiness check")
    args = parser.parse_args(argv)
    if not any((args.index, args.attach, args.house_views, args.nav_obstacles, args.snapshot)):
        parser.error("at least one explicitly scoped artifact is required")
    reports = []
    if args.snapshot:
        reports.append(validate_snapshot(args.snapshot))
    if args.index:
        reports.append(validate_index(args.index))
    if args.attach:
        reports.append(validate_attach(args.attach))
    if args.house_views:
        reports.append(validate_json_subcontract(args.house_views, "transforms"))
    if args.nav_obstacles:
        reports.append(validate_json_subcontract(args.nav_obstacles, "navMeshObstacles"))
    result = {"contract": CONTRACT,
              "scope": "only explicitly supplied artifacts and GLBs named by the supplied index",
              "notChecked": ["Unity native collision cooking/navigation parity", "runtime source-placement overrides",
                             "animation numeric samples without source curve evidence", "unprovided domains"],
              "reports": reports,
              "errors": [error for report in reports for error in report["errors"]]}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
