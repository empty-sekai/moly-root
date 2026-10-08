"""Source material-thumbnail pixels, joined by the materials master's ids.

The game's general materials (the ``materials`` master table) show their icon
from the shared ``thumbnail/material`` package, where each icon is a Texture2D
named ``material{id}``.  Every master row is looked up by that name; a row with
no texture and a texture no row names are both reported, never guessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore, require_paths
from core.jsonio import write_json

NAME_FORMAT = "material{id}"


def export_material_thumbnails(bundle, materials_json, out_dir, *, unity_version):
    """Write ``material-thumbnails.json`` and ``textures/material{id}.png``."""
    bundle, materials_json, out = Path(bundle), Path(materials_json), Path(out_dir)
    require_paths((bundle, materials_json))
    rows = json.loads(materials_json.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("the materials master must be a row list")
    UnityPy.config.FALLBACK_UNITY_VERSION = unity_version
    store = PackageStore([bundle])
    package = store.package(bundle.name)
    if package is None:
        raise ValueError(f"material thumbnail package could not be loaded: {bundle}")
    textures = {}
    for file in package.files:
        for path_id, kind in file.kinds.items():
            if kind != "Texture2D":
                continue
            name = str(file.tree(path_id)["m_Name"])
            if name in textures:
                raise ValueError(f"duplicate Texture2D name in material thumbnails: {name}")
            textures[name] = (file, file.objects[path_id])
    (out / "textures").mkdir(parents=True, exist_ok=True)
    materials, missing, used = [], [], set()
    for row in sorted(rows, key=lambda value: int(value["id"])):
        material_id = int(row["id"])
        name = NAME_FORMAT.format(id=material_id)
        pair = textures.get(name)
        if pair is None:
            missing.append(material_id)
            continue
        file, obj = pair
        texture = obj.read()
        image = texture.image
        relative = f"textures/{name}.png"
        image.save(out / relative, format="PNG")
        used.add(name)
        materials.append({"materialId": material_id, "name": name, "image": relative,
                          "width": texture.m_Width, "height": texture.m_Height,
                          "serializedFile": file.archive, "pathId": obj.path_id,
                          "sha256": hashlib.sha256((out / relative).read_bytes()).hexdigest()})
    result = {
        "version": 1,
        "unityVersion": unity_version,
        "source": {"bundle": bundle.name.replace("__", "/"),
                   "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                   "nameFormat": NAME_FORMAT},
        "materials": materials,
        "coverage": {"masterRows": len(rows), "exported": len(materials),
                     "missingMaterialIds": missing,
                     "unusedSourceTextures": sorted(set(textures) - used)},
    }
    write_json(out / "material-thumbnails.json", result)
    return result
