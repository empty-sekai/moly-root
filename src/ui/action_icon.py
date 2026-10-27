"""Export the standalone action-button icon textures as portable pixels.

These icons are not packed into a sprite atlas.  ``MysekaiActionButtonBase``
resolves an icon file name from the button type and hands it to a texture
loader as ``mysekai/icon/action_icon/<name>``, so each icon is its own bundle
holding a single ``Texture2D`` bound to a ``RawImage``.  The sprite-atlas
exporter cannot see them: it walks ``Sprite`` objects, and there is no
``Sprite`` here.

One PNG per bundle, named after the ``Texture2D``.  The manifest records the
bundle each icon came from together with its declared size, so a consumer can
check the pixels it loaded against the source dimensions.

Do not name the files after the bundle instead: the two disagree in case.
``mysekai/icon/action_icon/icon_action_lostitem_wh`` holds a texture whose
``m_Name`` is ``icon_action_lostItem_wh`` (capital I).  A consumer that derives
the file name by lowercasing the bundle path, or a reader that lowercases the
texture name, loses exactly that one icon and nothing else -- which reads as a
missing asset rather than as a naming assumption.  The manifest carries both
strings per icon so a mismatch is visible instead of inferred.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import UnityPy

from core.unity import configure_fallback_unity_version
from core.jsonio import write_json

# Identity of the bundle root this toolchain is pinned to.  Both go into the
# manifest so a consumer can tell which region and game version the pixels
# came from.
REGION = "cn"
GAME_VERSION = "6.0.0"

_PNG_MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}


def _resolve_paths(inputs: Iterable[str | Path]) -> list[Path]:
    """Each input is one bundle file, or a directory of them."""
    paths: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            paths.extend(sorted(child for child in path.iterdir() if child.is_file()))
        else:
            paths.append(path)
    if not paths:
        raise ValueError("no input bundles resolved")
    return paths


def _logical_name(flat_name: str) -> str:
    """The in-game name of a bundle file: ``__`` is the path separator."""
    return flat_name.replace("__", "/")


def export_action_icons(
    bundles: Iterable[str | Path],
    out_dir: str | Path,
    *,
    region: str = REGION,
    game_version: str = GAME_VERSION,
) -> dict[str, Any]:
    """Write one PNG per action-icon bundle plus ``action-icon.json``.

    Fails loudly rather than skipping: a bundle that holds no Texture2D,
    holds more than one, or decodes to a mode PNG cannot represent is an
    error, because a silently absent icon is indistinguishable from a button
    that was never meant to have one.
    """
    paths = _resolve_paths(bundles)
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for path in paths:
        env = UnityPy.load(str(path))
        textures = [obj for obj in env.objects if obj.type.name == "Texture2D"]
        if not textures:
            raise ValueError(f"{path.name}: no Texture2D in bundle")
        if len(textures) > 1:
            raise ValueError(f"{path.name}: {len(textures)} Texture2D objects in bundle")
        bundle = _logical_name(path.name)
        data = textures[0].read()
        name = str(data.m_Name)
        if not name:
            raise ValueError(f"unnamed Texture2D in {bundle}")
        if name in seen:
            raise ValueError(f"duplicate icon name {name!r} in {bundle} and {seen[name]}")
        image = data.image
        if image.mode not in _PNG_MODES:
            raise ValueError(f"{name}: cannot encode mode {image.mode} as PNG")
        target = destination / f"{name}.png"
        image.save(target, format="PNG")
        seen[name] = bundle
        records.append(
            {
                "name": name,
                "bundle": bundle,
                "pathId": int(textures[0].path_id),
                "width": int(data.m_Width),
                "height": int(data.m_Height),
                "textureFormat": int(data.m_TextureFormat),
                "decodedMode": image.mode,
                "decodedWidth": int(image.width),
                "decodedHeight": int(image.height),
                "file": target.name,
            }
        )

    records.sort(key=lambda record: record["name"])
    manifest = {
        "version": 1,
        "region": region,
        "gameVersion": game_version,
        "unityVersion": configure_fallback_unity_version(),
        "summary": {"bundles": len(paths), "icons": len(records)},
        "icons": records,
    }
    write_json(destination / "action-icon.json", manifest, indent=2)
    return manifest
