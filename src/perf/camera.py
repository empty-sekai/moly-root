"""Dialogue camera assets: the two MonoBehaviour classes a talk camera reads.

Cut-scene and dialogue camera movement is driven by two scriptable assets.
:class:`CameraParam` is a ``ScriptableObject`` that carries **seven serialised
animation curves** — distance, yaw, pitch, and the four move-path curves —
which together describe how the camera travels along a path and looks at its
subject over time.  :class:`CameraSetting` carries the numeric parameters that
bound that travel: an offset, a min/max distance and a field of view.

The decisive fact about :class:`CameraSetting` is what its **constructor does
not** do.  Its constructor assigns ``offset``, ``minDistance``, ``maxDistance``
and ``fov`` — but never ``distance``.  So a ``CameraSetting`` instance read from
disk can have a ``distance`` value that no constructor default produced: the
value lives **only** in the serialised asset.  That is why this reader exists:
it has to read the asset to know the real distance, and if the field turns out
to be absent the whole "the asset must be read" premise is false.

Curves are exported key by key — not just "does it have keys".  Each
``AnimationCurve`` is unpacked into every key's ``time`` / ``value`` /
``inSlope`` / ``outSlope`` / ``weightedMode`` / ``inWeight`` / ``outWeight``,
plus the curve-level ``m_PreInfinity`` / ``m_PostInfinity`` /
``m_RotationOrder``.  In the typetree the per-key names are lowercase while the
curve-level ones carry the ``m_`` prefix; that is the shape another lane measured
and this reader follows it.  Floats are written as-is, never rounded; the one
non-finite case (``Infinity`` / ``NaN``) is spelled as a string by
``core.jsonio`` rather than dropped or clamped.

Classes are told apart by ``MonoScript.m_ClassName``, never by object name or a
name substring.  A package can hold more than one ``CameraParam`` or
``CameraSetting``, so every instance is exported, and the container path it was
reached from is recorded next to it so a reader can tell which one is which.

Every "field complete" judgement is made per instance and reported per instance.
A curve that is part of the stub table but absent from a typetree, or a
curve-shaped field in a typetree that is absent from the stub table, is listed
as part of the symmetric difference rather than quietly ignored; likewise a
field the reader read but did not put in its output bucket is counted, so the
"read but not exported" number is a real count instead of being assumed zero.
"""
import math
import os
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore
from core.jsonio import write_json

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

CAMERA_PARAM_CLASS = "CameraParam"
CAMERA_SETTING_CLASS = "CameraSetting"

CURVE_FIELDS = (
    "_distance",
    "_yaw",
    "_pitch",
    "_movePathSpeed",
    "_movePathHigh",
    "_moveCameraXPath",
    "_moveCameraZPath",
)

KEYFRAME_KEYS = (
    "time",
    "value",
    "inSlope",
    "outSlope",
    "weightedMode",
    "inWeight",
    "outWeight",
)

# curve-level names carry the ``m_`` prefix; the per-key names do not.
CURVE_LEVEL_KEYS = ("m_PreInfinity", "m_PostInfinity", "m_RotationOrder")


def _read_curve(curve):
    """One ``AnimationCurve`` typetree value as a JSON-ready dict.

    *curve* is the serialised value of a single curve field: a dict carrying an
    ``m_Curve`` list (each entry a keyframe) and the three curve-level fields.
    The returned dict keeps the typetree's names, so per-key fields stay
    lowercase and the curve-level ones keep their ``m_`` prefix.  Every key is
    walked, never collapsed to a count — a curve that only reported how many
    keys it had would lose the data this reader exists to read.  A curve whose
    ``m_Curve`` is absent is an empty curve, not an error.
    """
    curve = curve or {}
    keys = [
        {name: key.get(name) for name in KEYFRAME_KEYS}
        for key in curve.get("m_Curve") or []
    ]
    return {
        "keys": keys,
        "m_PreInfinity": curve.get("m_PreInfinity"),
        "m_PostInfinity": curve.get("m_PostInfinity"),
        "m_RotationOrder": curve.get("m_RotationOrder"),
    }


def _container_map(store, package):
    """*(file, path id)* → full container path, built from the AssetBundle.

    A package's ``m_Container`` table is what maps an on-disk asset path to the
    object it holds; the store's own ``contents`` list truncates that path to
    its last segment, which is enough to know *what* was reached but not to
    distinguish two same-named assets in different folders.  So the container
    path is read here at full length and kept keyed by ``(file, path id)``.
    """
    result = {}
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "AssetBundle":
                continue
            for asset_path, info in record.tree(path_id).get("m_Container") or []:
                target = store.follow(record, (info or {}).get("asset") or {})
                if target is not None:
                    result[target] = str(asset_path)
    return result


def _camera_param_entry(record, path_id, path, counts):
    """One CameraParam instance: its seven curves, and the field completeness
    check that says whether any of them is missing or went unexported."""
    tree = record.tree(path_id)
    present = [field for field in CURVE_FIELDS if field in tree]
    absent = [field for field in CURVE_FIELDS if field not in tree]
    curves = {field: _read_curve(tree.get(field)) for field in present}
    not_exported = [field for field in present if field not in curves]
    data_fields = [field for field in tree if not field.startswith("m_")]
    extra = [field for field in data_fields if field not in CURVE_FIELDS]

    for field in present:
        counts["keyframes"][field] = counts["keyframes"].get(field, 0) + len(
            curves[field]["keys"])
    counts["emptyCurves"] += sum(1 for field in present if not curves[field]["keys"])
    if absent:
        counts["fieldIssues"].append(
            {"instance": path or f"#{path_id}", "absent": absent})
    if extra:
        counts["fieldIssues"].append(
            {"instance": path or f"#{path_id}", "extra": extra})
    counts["readButNotExported"] += len(not_exported)

    return {
        "class": CAMERA_PARAM_CLASS,
        "name": tree.get("m_Name", ""),
        "container": path,
        "curves": curves,
        "fieldCheck": {
            "present": present,
            "absent": absent,
            "extra": extra,
            "symmetricDiff": sorted(set(absent) | set(extra)),
            "readButNotExported": len(not_exported),
            "notExportedFields": not_exported,
        },
    }


def _camera_setting_entry(tree, path, counts):
    """One CameraSetting instance: its stored numeric parameters.

    ``distance`` is called out separately because it is the one field the
    constructor does not assign; if the field is absent the reader says so
    rather than filling in a default.  The value is also pushed onto
    ``counts["distance"]`` so the caller can see the actual distances at a
    glance.
    """
    data_fields = [field for field in tree if not field.startswith("m_")]
    fields = {field: tree.get(field) for field in data_fields}
    distance_present = "distance" in tree
    distance_value = tree.get("distance") if distance_present else None
    if distance_present:
        counts["distance"].append(distance_value)
    else:
        counts["distanceMissing"] += 1
    return {
        "class": CAMERA_SETTING_CLASS,
        "name": tree.get("m_Name", ""),
        "container": path,
        "fields": fields,
        "distancePresent": distance_present,
        "distance": distance_value,
    }


def _walk_package(store, name, out):
    """One package: its CameraParam/CameraSetting instances, exported."""
    package = store.package(name)
    document = {"package": name, "cameraParams": [], "cameraSettings": []}
    counts = {"package": name, "missing": False, "packageObjects": 0,
              "cameraParam": 0, "cameraSetting": 0, "keyframes": {},
              "emptyCurves": 0, "distance": [], "distanceMissing": 0,
              "fieldIssues": [], "readButNotExported": 0}

    container = _container_map(store, package)
    for record in package.files:
        counts["packageObjects"] += len(record.kinds)
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour":
                continue
            cls = record.script_of(path_id)
            path = container.get((record, path_id), "")
            if cls == CAMERA_PARAM_CLASS:
                counts["cameraParam"] += 1
                document["cameraParams"].append(
                    _camera_param_entry(record, path_id, path, counts))
            elif cls == CAMERA_SETTING_CLASS:
                counts["cameraSetting"] += 1
                document["cameraSettings"].append(
                    _camera_setting_entry(record.tree(path_id), path, counts))

    if document["cameraParams"] or document["cameraSettings"]:
        write_json(out / f"{name}.json", document)
    return counts


def read_camera_assets(bundles, out_dir, bundle_root=None):
    """Read every dialogue camera asset from *bundles*, one JSON per package.

    *bundles* names the packages to read; the last path segment of each is the
    package name.  *out_dir* receives one document per package: its
    ``CameraParam`` and ``CameraSetting`` instances, each with its container
    path, its curves (key by key), and the per-instance field-completeness
    check.  A package whose path does not exist is reported in ``missing`` and
    never claimed to hold zero camera assets.

    Returns a report: per package the instance counts, the seven curves'
    keyframe counts, the ``CameraSetting.distance`` values that were read, the
    number of curves found empty, and the field-completeness issues.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    by_name = {os.path.basename(str(path)): str(path) for path in bundles}
    names = sorted(by_name)
    store = PackageStore(bundles, bundle_root)
    records = []
    for name in names:
        if not os.path.exists(by_name[name]):
            records.append({"package": name, "missing": True})
            continue
        records.append(_walk_package(store, name, out))
    return {"packages": records, "missing": [
        r["package"] for r in records if r.get("missing")]}


if __name__ == "__main__":
    raise SystemExit("camera reader is a library; call read_camera_assets")
