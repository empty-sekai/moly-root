"""Furniture attach points: the ``loc_start``/``loc_end`` pairs a prefab ships.

A furniture package holds one or more prefabs.  Every prefab that a character can
sit on, stand at or interact with carries a ``FixtureView`` behaviour whose
``_attachPoints`` array names the pairs: a start position, an end position, and for
each of them a game object named ``loc_startNNN`` / ``loc_endNNN`` (a zero-padded
three-digit code, with an optional ``_NN`` suffix).  This module extracts those
pairs — the code, and the local transform of each of the two objects — and nothing
else about the package.

The extractor reads only what the pairs say.  When a pair does not behave like the
pattern — a code mismatch, a suffix mismatch, a start pointing at an object named
``loc_end…``, a start and an end naming the same object, an object named like an
attach point that no pair references — it is recorded in ``anomalies`` and left as
authored, never normalised, never dropped.
"""
import os
import re
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore
from core.jsonio import write_json

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

FIXTURE_PREFIX = "mysekai__fixture__"
FIXTURE_VIEW = "FixtureView"
START_KEYS = ("m_Position", "m_LocalPosition")
ROTATION_KEYS = ("m_Rotation", "m_LocalRotation")
SCALE_KEYS = ("m_Scale", "m_LocalScale")
START = re.compile(r"^loc_start([0-9]{3})(?:_([0-9]+))?$")
END = re.compile(r"^loc_end([0-9]{3})(?:_([0-9]+))?$")
TRANSFORMS = ("Transform", "RectTransform")
ACTION_POINT_TABLE = "mysekaiCharacterTalkActionPoints"
ACTION_POINT_SLOTS = ("gameCharacterUnitId1ActionPoint", "gameCharacterUnitId2ActionPoint",
                      "gameCharacterUnitId3ActionPoint", "gameCharacterUnitId4ActionPoint")

MISSING_BUNDLE = "bundle file not found; it was not opened"
UNRESOLVED_POINTER = "attach point pointer resolves to no game object here"
NOT_A_GAME_OBJECT = "attach point pointer resolves to an object that is not a game object"
NO_TRANSFORM = "the game object carries no local transform"

SEMANTICS = {
    "pairs": ("every `_attachPoints` entry of every `FixtureView` behaviour is one pair. "
              "A pair whose objects do not match the `loc_start`/`loc_end` pattern is "
              "still a pair; it is additionally listed in `anomalies`"),
    "ids": ("the three-digit code of a pair, as authored (`id`) and as an integer "
            "without the zero padding (`idValue`)"),
    "transforms": ("each side's local position, rotation (quaternion) and scale as "
                   "authored on its transform, rounded to six decimals"),
    "anomalies": ("authored facts that do not fit the pattern: a self-reference (both "
                  "ends name the same object), a start whose object is named "
                  "`loc_end…`, a code or suffix mismatch, an unresolved pointer, and "
                  "attach-named objects no pair references.  They are reported, never "
                  "fixed or dropped"),
    "unresolvedIds": ("the reference domain's codes that no package carries an attach "
                      "point for.  The cause is not established (packages not in the "
                      "supplied set, or the position is computed at runtime and never "
                      "serialized), so the list is marked unresolved and carries no "
                      "defaults"),
}


def _vector(tree, keys, channels):
    """One rounded vector, with negative-zero written as zero."""
    source = next((tree.get(key) for key in keys if tree.get(key)), {}) or {}
    out = [round(float(source.get(channel, 0.0) or 0.0), 6) for channel in channels]
    return [value if value else 0.0 for value in out]


def _local(record, path_id):
    """The local transform of a game object: ``(transform_tree, component_kind)``."""
    tree = record.tree(path_id)
    for entry in tree.get("m_Component") or []:
        pointer = (entry.get("component") or entry) if isinstance(entry, dict) else entry
        component = (pointer or {}).get("m_PathID", 0)
        kind = record.kinds.get(component)
        if kind in TRANSFORMS:
            return record.tree(component), kind
    return None, None


def _side(record, path_id):
    """One attach-point side: its name and local transform, or why there is none."""
    tree = record.tree(path_id)
    side = {"name": str(tree.get("m_Name", ""))}
    transform, _kind = _local(record, path_id)
    if transform is None:
        side["transform"] = None
        side["reason"] = NO_TRANSFORM
        return side
    side["transform"] = {
        "position": _vector(transform, START_KEYS, "xyz"),
        "rotation": _vector(transform, ROTATION_KEYS, "xyzw"),
        "scale": _vector(transform, SCALE_KEYS, "xyz"),
        "kind": _kind,
    }
    return side


def _pair(store, record, entry):
    """One ``_attachPoints`` entry, with the anomalies it carries."""
    start_pointer = entry.get("StartLoc") or {}
    end_pointer = entry.get("EndLoc") or {}
    start = store.follow(record, start_pointer)
    end = store.follow(record, end_pointer)
    anomalies = []

    def side(pointer, target, role):
        if target is None:
            anomalies.append({"type": "unresolved" if pointer.get("m_PathID", 0)
                              else "null", "side": role,
                              "detail": UNRESOLVED_POINTER
                              if pointer.get("m_PathID", 0)
                              else f"{role} pointer is null"})
            return {"name": None, "transform": None,
                    "reason": UNRESOLVED_POINTER}
        target_record, path_id = target
        if target_record.kinds.get(path_id) != "GameObject":
            anomalies.append({"type": "not-game-object", "side": role,
                              "detail": NOT_A_GAME_OBJECT})
            return {"name": None, "transform": None,
                    "reason": NOT_A_GAME_OBJECT}
        return _side(target_record, path_id)

    start_side = side(start_pointer, start, "start")
    end_side = side(end_pointer, end, "end")

    start_name = start_side["name"] or ""
    end_name = end_side["name"] or ""
    start_code = START.match(start_name)
    end_code = END.match(end_name)
    if start and end and start[1] == end[1]:
        anomalies.append({"type": "self-reference",
                          "detail": f"start and end name the same object {end_name}"})
    if not start_code:
        anomalies.append({"type": "start-not-pattern",
                          "detail": f"start object name {start_name!r} does not match "
                                    "the loc_start pattern"})
    if not end_code:
        anomalies.append({"type": "end-not-pattern",
                          "detail": f"end object name {end_name!r} does not match "
                                    "the loc_end pattern"})
    if start_code and end_code:
        if start_code.group(1) != end_code.group(1):
            anomalies.append({"type": "id-mismatch",
                              "detail": f"codes differ: {start_name} / {end_name}"})
        if (start_code.group(2) or "") != (end_code.group(2) or ""):
            anomalies.append({"type": "suffix-mismatch",
                              "detail": f"suffixes differ: {start_name} / {end_name}"})

    code = end_code or start_code
    if code is None:
        pair = {"id": None, "idValue": None, "suffix": None,
                "start": start_side, "end": end_side, "anomalies": anomalies}
    else:
        pair = {"id": code.group(1), "idValue": int(code.group(1)),
                "suffix": code.group(2) or "",
                "start": start_side, "end": end_side, "anomalies": anomalies}
    return pair, (start, end)


def _unreferenced(records, referenced):
    """Attach-named game objects no pair references, per package."""
    out = []
    for record in records:
        for path_id, kind in record.kinds.items():
            if kind != "GameObject":
                continue
            name = str(record.tree(path_id).get("m_Name", ""))
            if not (START.match(name) or END.match(name)):
                continue
            if (record, path_id) in referenced:
                continue
            out.append(name)
    return sorted(set(out))


def _reference_domain(master):
    """The codes of the reference table, or why they are not known.

    The reference table is supplied by the caller like every other master table; its
    absence is reported rather than substituted, because the gap between the two
    domains is part of the result and must not be filled with an invented list.
    """
    if not master:
        return None, "no master directory supplied"
    try:
        from core.master import Master, MissingTable
        rows = Master(master).table(ACTION_POINT_TABLE)
    except MissingTable as exc:
        return None, str(exc)
    values = sorted({int(row[slot]) for row in rows
                     for slot in ACTION_POINT_SLOTS if row.get(slot)})
    return values, None


def extract_attach_points(bundles, out_dir, master=None):
    """Extract every attach-point pair from *bundles* into ``out_dir``.

    *bundles* are the bundle file paths to read.  A bundle whose file is not on
    disk is reported as ``bundle-not-found`` rather than opened, because the loader
    answers a missing path with zero objects and silence.  *master* is a directory
    of caller-supplied master tables, used only for the reference code domain;
    without it that gap is reported as unavailable.
    """
    store = PackageStore(bundles)
    return extract_from_store(store, out_dir, master)


def extract_from_store(store, out_dir, master=None):
    """Extract every attach-point pair the packages held by *store* carry."""
    out = Path(out_dir)
    names = _list(store)

    packages = {}
    pairs_total = 0
    packages_with_pairs = 0
    fixture_view_packages = 0
    ids = set()
    self_references = 0
    unreferenced = 0
    id_domain, id_domain_reason = _reference_domain(master)

    for name in names:
        path = store.paths.get(name, "")
        if not path or not os.path.exists(path):
            packages[name] = {"hasFixtureView": False, "entries": [],
                              "anomalies": [{"type": "bundle-not-found",
                                             "detail": MISSING_BUNDLE}]}
            continue
        package = store.package(name)
        if package is None:
            packages[name] = {"hasFixtureView": False, "entries": [],
                              "anomalies": [{"type": "bundle-not-found",
                                             "detail": MISSING_BUNDLE}]}
            continue
        records = list(package.files)
        view_found = False
        entries = []
        referenced = set()
        package_anomalies = []
        for record in records:
            for path_id, kind in record.kinds.items():
                if kind != "MonoBehaviour":
                    continue
                if record.script_of(path_id) != FIXTURE_VIEW:
                    continue
                view_found = True
                tree = record.tree(path_id)
                for entry in tree.get("_attachPoints") or []:
                    pair, targets = _pair(store, record, entry)
                    referenced.update(t for t in targets if t is not None)
                    entries.append(pair)
                    pairs_total += 1
                    if pair["id"] is not None:
                        ids.add(pair["id"])
                    for anomaly in pair["anomalies"]:
                        if anomaly["type"] == "self-reference":
                            self_references += 1
        if view_found:
            fixture_view_packages += 1
        leftover = _unreferenced(records if view_found else [], referenced)
        if leftover:
            unreferenced += len(leftover)
            package_anomalies.append({"type": "unreferenced-attach",
                                      "names": leftover})
        if entries:
            packages_with_pairs += 1
        packages[name] = {"hasFixtureView": view_found, "entries": entries,
                          "anomalies": package_anomalies}

    found = sorted(ids)
    unresolved_ids = None
    if id_domain is not None:
        domain = set(id_domain)
        present = sorted(int(code) for code in found if int(code) in domain)
        missing_values = sorted(domain - set(present))
        missing_ids = [f"{value:03d}" for value in missing_values]
        unresolved_ids = {
            "available": True,
            "domain": len(domain),
            "found": len(present),
            "missing": len(missing_ids),
            "missingValues": missing_ids,
            "reason": "the codes without an attach point are reported as unresolved: "
                      "the cause is not established (packages not in the supplied "
                      "set, or the position is computed at runtime and never "
                      "serialized); no default values are supplied",
        }
    else:
        unresolved_ids = {
            "available": False,
            "reason": id_domain_reason,
        }

    summary = {
        "bundles": len(names),
        "withFixtureView": fixture_view_packages,
        "pairs": pairs_total,
        "packagesWithPairs": packages_with_pairs,
        "ids": {"count": len(found), "values": found},
        "anomalies": {"selfReferences": self_references,
                      "unreferenced": unreferenced},
        "unresolvedIds": unresolved_ids,
    }
    per_bundle = {}
    for name, package in packages.items():
        per_bundle[name] = {
            "pairs": len(package["entries"]),
            "hasFixtureView": package["hasFixtureView"],
            "anomalies": len(package["anomalies"]),
        }
    document = {
        "version": 1,
        "semantics": SEMANTICS,
        "summary": summary,
        "packages": packages,
    }
    path = write_json(out / "attach-points.json", document)
    return dict(summary, path=str(path), perBundle=per_bundle)


def _list(store):
    """The package names the store holds, in the order they will be extracted."""
    return sorted(name for name in store.paths if name.startswith(FIXTURE_PREFIX))
