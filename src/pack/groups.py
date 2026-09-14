"""Build dependency-labelled asset groups using the shared content-addressed packer."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

from core.atomic import PUBLIC_FILE_MODE, exclusive_lock, json_bytes, write_bytes

from .build import build, iter_files
from .paths import asset_path, canonical_inputs, output_path, separate_output
from .verify import verify_catalog_data


ROOT_DOCUMENTS = (
    "manifest.json", "characters.json", "motion-library.glb", "motion-library.index.json",
    "alone-actions.json", "facial-tables.json", "client-config.json", "birthday-parties.json",
    "mysekai-blueprints.json", "mysekai-fixtures.json", "mysekai-items.json",
    "mysekai-music-records.json", "site-groups.json", "talks.json", "tweets.json",
    "tweet-tables.json", "wordings.json",
    "mysekai-tools.json", "mysekai-staminas.json", "mysekai-stamina-recovery.json",
    "mysekai-materials.json", "mysekai-fixture-possessions.json",
    "mysekai-material-possessions.json", "mysekai-system-fixtures.json",
    "mysekai-blueprint-material-costs.json", "mysekai-blueprint-terms.json",
    "mysekai-fixture-player-timelines.json", "mysekai-character-talk-fixture-timelines.json",
    "mysekai-character-talks.json",
    "mysekai-character-talk-no-talk-fixture-actions.json",
    "mysekai-character-talk-action-points.json", "mysekai-character-talk-conditions.json",
    "mysekai-character-talk-condition-groups.json", "mysekai-game-character-unit-groups.json",
)
ASSET_DIRECTORIES = (
    "actor-animations",
    "avatar", "avatar-parts", "camera", "cutscene-timeline", "emoticons", "fixture-areas",
    "fixture-attach", "fixture-interface", "fixture-meshes", "fixture-models",
    "fixture-gimmick", "fixture-particles-v2", "fixture-talks", "fixture-timeline", "perf-animations",
    "phenomena", "site", "ui", "ui-layout-v2",
)
FORMATS = {".json", ".glb", ".gltf", ".png", ".jpg", ".jpeg", ".webp", ".ktx2", ".ogg", ".wav", ".bin"}


def character_files(root: Path):
    if not (root / "manifest.json").is_file():
        return
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    for unit in manifest["units"]:
        for key in ("glb", "rig"):
            if unit.get(key):
                yield unit[key]
        if unit.get("rig"):
            rig_path = asset_path(root, unit["rig"])
            rig = json.loads(rig_path.read_text("utf-8"))
            for texture in rig.get("textures", []):
                yield rig_path.parent / texture
        path = asset_path(root, unit["glb"])
        data = path.read_bytes()
        length = int.from_bytes(data[12:16], "little")
        document = json.loads(data[20:20 + length])
        for row in document.get("images", []) + document.get("buffers", []):
            uri = row.get("uri", "")
            if uri and not uri.startswith("data:"):
                yield path.parent / uri


def group_of(path: str) -> tuple[str, str]:
    parts = path.split("/")
    top = parts[0]
    if len(parts) == 1:
        character = re.match(r"(?:tex_)?sd_(\d+)(?:[_.])", top)
        if character:
            return f"character/{character[1]}", "character"
        return ("common/motion" if top.startswith("motion-library") else "common/tables"), "common"
    if top in {"avatar", "avatar-parts"}:
        return "character/avatar", "character"
    if top == "actor-animations":
        return ((f"character/actions/{parts[1]}", "character") if len(parts) > 2
                else ("common/actor-animations", "common"))
    if top == "site" and len(parts) > 2 and parts[1] in {"scenes", "props"}:
        return f"site/{parts[1]}/{parts[2]}", "site"
    if top == "site" and len(parts) > 3 and parts[1:3] == ["indoor", "modules"]:
        return f"site/room/{parts[3]}", "site"
    if top.startswith("fixture-"):
        package = next((part.split(".")[0] for part in parts[1:] if part.startswith("mysekai__")), None)
        return (f"furniture/{package}", "furniture") if package else ("common/furniture", "common")
    if top == "phenomena" and re.match(r"\d+_", parts[1]):
        return f"weather/{parts[1]}", "weather"
    if top == "phenomena":
        return "common/weather", "common"
    if top in {"ui", "ui-layout-v2"}:
        return "common/ui", "common"
    if top == "emoticons":
        return "common/emoticons", "common"
    return f"common/{top}", "common"


def build_groups(source, output, version):
    source, output = separate_output(source, output)
    with exclusive_lock(output / ".publish.lock"):
        return _build_groups(source, output, version)


def _build_groups(source, output, version):
    current_path = output_path(output, "asset-packs.json")
    previous_bytes = current_path.read_bytes() if current_path.is_file() else None
    previous = json.loads(previous_bytes) if previous_bytes is not None else {}
    old_packages = {package["id"]: package["manifest"] for package in previous.get("packages", [])}
    paths = {path for path in ROOT_DOCUMENTS if (source / path).is_file()}
    paths.update(character_files(source))
    for directory in ASSET_DIRECTORIES:
        base = source / directory
        if not base.is_dir() or base.is_symlink() or getattr(base, "is_junction", lambda: False)():
            continue
        paths.update(p.relative_to(source).as_posix() for p in iter_files(base)
                     if p.suffix.lower() in FORMATS and ".pre-" not in p.as_posix())
    grouped = defaultdict(list)
    kinds = {}
    for path in canonical_inputs(source, paths):
        group, kind = group_of(path)
        grouped[group].append(path)
        kinds[group] = kind
    packages = []
    common = {group for group in grouped if kinds[group] == "common"}
    dependency_groups = {
        "character": ["common/tables", "common/motion", "common/emoticons"],
        "furniture": ["common/tables", "common/furniture"],
        "weather": ["common/tables", "common/weather"],
        "site": ["common/tables", "common/site", "common/weather"],
    }
    for group, files in sorted(grouped.items()):
        report = build(source, output, version, paths=files, manifest_name=None,
                       previous_manifest=old_packages.get(group),
                       categories_path=Path(__file__).with_name("groups.toml"))
        name = Path(report["manifest"]).relative_to(output).as_posix()
        packages.append({
            "id": group, "kind": kinds[group], "manifest": name,
            "dependencies": [key for key in dependency_groups.get(kinds[group], []) if key in common],
            "paths": files, "download_bytes": report["download_bytes"],
            "content_bytes": report["content_bytes"],
        })
        print(f"{group}: {len(files)} files, {report['download_bytes']} bytes", flush=True)
    catalog = {"schema": "moly-asset-packs/1", "version": version, "packages": packages}
    errors, _ = verify_catalog_data(catalog, output)
    if errors:
        raise RuntimeError("refusing to publish invalid catalog: " + "; ".join(errors))
    # Keep every previous publication as a GC root. Package manifests and blobs
    # are immutable, so even a reader holding the old catalog sees one version.
    if previous_bytes is not None:
        write_bytes(output_path(output, f"catalogs/{hashlib.sha256(previous_bytes).hexdigest()}.json"), previous_bytes, mode=PUBLIC_FILE_MODE)
    data = json_bytes(catalog)
    write_bytes(output_path(output, f"catalogs/{hashlib.sha256(data).hexdigest()}.json"), data, mode=PUBLIC_FILE_MODE)
    write_bytes(current_path, data, mode=PUBLIC_FILE_MODE)
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    build_groups(args.src, args.out, args.version)


if __name__ == "__main__":
    main()
