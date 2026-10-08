"""Phenomenon thumbnail extraction, joined through authored master routes.

Both aggregate and per-icon source packages are supported. The asset container
route, its original PPtr and the resolved object are kept together. Conflicting
pictures never become a last-writer-wins association.
"""
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath

import PIL
import UnityPy

from core.assets.identity import object_identity, source_reference
from core.assets.packages import pairs
from core.assets.router import PHENOMENA_THUMBNAIL


def _references(store, package):
    # Do not use Package.contents: it intentionally exposes only resolved
    # targets and shortens container paths. An icon receipt needs both ends,
    # including unresolved references and the full authored asset route.
    for record in sorted(package.files, key=lambda r: (r.bundle, r.archive)):
        for owner_id, kind in sorted(record.kinds.items()):
            if kind != "AssetBundle":
                continue
            for asset_path, info in sorted(pairs(record.tree(owner_id).get("m_Container")),
                                           key=lambda pair: str(pair[0])):
                pointer = (info or {}).get("asset") or {}
                yield str(asset_path), record, owner_id, pointer


def _object_references(store, record, object_id):
    """Retain backing references, including Sprite atlas/texture references."""
    def walk(value, path):
        if isinstance(value, dict):
            if "m_PathID" in value and "m_FileID" in value:
                yield {"field": path, **source_reference(record, object_id, value, store.follow)}
            else:
                for key in sorted(value):
                    yield from walk(value[key], f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from walk(child, f"{path}[{index}]")
    return list(walk(record.tree(object_id), ""))


def extract_icons(store, out, entries, explicit_packages=()):
    wanted = {}
    for name, entry in entries.items():
        entry["icon"] = None
        key = (entry.get("master") or {}).get("iconAssetbundleName")
        if key:
            wanted.setdefault(str(key), []).append(name)
    packages = {PHENOMENA_THUMBNAIL, *explicit_packages}
    gaps, candidates, conflicts = [], {}, set()
    for key in sorted(wanted):
        if not _safe_name(key):
            gaps.append({"icon": key, "reason": "invalid source icon asset name"})
            continue
        packages.add(PHENOMENA_THUMBNAIL + "__" + key)
    input_receipts = {}
    for package_name in sorted(packages):
        package = store.package(package_name, record_missing=False)
        if package is None:
            continue
        store.load_dependencies([package_name])
        for asset_path, owner, owner_id, pointer in _references(store, package):
            name = PurePosixPath(asset_path.replace("\\", "/")).stem
            reference = source_reference(owner, owner_id, pointer, store.follow)
            target = store.follow(owner, pointer)
            if target is None:
                gaps.append({"icon": name, "reference": reference,
                             "reason": "unresolved icon container reference"})
                conflicts.add(name)
                continue
            record, path_id = target
            kind = record.kinds.get(path_id)
            if kind not in {"Texture2D", "Sprite"}:
                if name in wanted:
                    conflicts.add(name)
                    gaps.append({"icon": name, "reference": reference, "class": kind,
                                 "reason": "source icon route is not a picture"})
                continue
            if not _safe_name(name):
                gaps.append({"icon": name, "reason": "invalid source icon asset name"})
                continue
            source = {**object_identity(record, path_id),
                      "asset": PurePosixPath(asset_path).name, "assetPath": asset_path,
                      "objectName": str(record.tree(path_id).get("m_Name", "")),
                      "class": kind, "reference": reference}
            try:
                refs = _object_references(store, record, path_id)
                source["references"] = refs
                unresolved = [r for r in refs if r["pointer"]["pathId"] != "0" and r["target"] is None]
                if unresolved:
                    raise ValueError("unresolved backing icon references")
                dependency_names = {owner.bundle, record.bundle}
                dependency_names.update(r["target"]["bundle"] for r in refs if r["target"])
                for dependency in sorted(dependency_names):
                    if dependency not in input_receipts:
                        input_receipts[dependency] = store.source_receipt(dependency)
                source["inputs"] = [input_receipts[k] for k in sorted(dependency_names)]
                image = record.objects[path_id].read().image
                output = BytesIO()
                image.save(output, format="PNG", optimize=False, compress_level=9)
                payload = output.getvalue()
                receipt = {"schemaVersion": 2, "file": f"icons/{name}.png",
                           "sha256": sha256(payload).hexdigest(), "bytes": len(payload),
                           "width": image.width, "height": image.height,
                           "decoding": {"unityPy": UnityPy.__version__, "pillow": PIL.__version__,
                                        "format": "PNG", "compressionLevel": 9},
                           "sources": [source]}
            except Exception as exc:
                conflicts.add(name)
                gaps.append({"icon": name, "source": source,
                             "reason": f"{type(exc).__name__}: {exc}"})
                continue
            previous = candidates.get(name)
            if previous is not None:
                if previous[0]["sha256"] != receipt["sha256"]:
                    conflicts.add(name)
                    gaps.append({"icon": name, "reason": "conflicting source icon pictures",
                                 "sources": previous[0]["sources"] + [source]})
                else:
                    previous[0]["sources"].append(source)
            else:
                candidates[name] = receipt, payload
    receipts = {}
    for name in sorted(candidates):
        if name in conflicts:
            continue
        receipt, payload = candidates[name]
        path = out / receipt["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        receipts[name] = receipt
    for key in sorted(wanted):
        if key not in receipts:
            gaps.append({"icon": key, "phenomena": sorted(wanted[key]),
                         "reason": "master icon source missing or conflicting"})
            continue
        for name in wanted[key]:
            entries[name]["icon"] = receipts[key]["file"]
    return sorted(receipts), receipts, gaps


def _safe_name(value):
    return bool(value) and value not in {".", ".."} and not any(
        c in value for c in '/\\:?*<>|\0') and all(ord(c) >= 32 for c in value)
