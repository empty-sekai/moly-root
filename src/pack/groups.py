"""Build dependency-labelled asset groups using the shared content-addressed packer."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

from core.atomic import PUBLIC_FILE_MODE, exclusive_lock, json_bytes, write_bytes
from core.jp import public_release_provenance, public_source_document

from .build import build, iter_files
from .identity import CATALOG_V2, canonical_bytes
from .limits import MAX_RUNTIME_DOCUMENT_BYTES, MAX_RUNTIME_BLOB_BYTES, MAX_RUNTIME_ASSET_BYTES
from .release import commit_catalog, validate_selector
from .paths import asset_path, canonical_inputs, output_path, separate_output
from .verify import strict_json, verify_catalog_data
from .shader_textures import validate_shader_texture_payloads
from .shader_programs import close_shader_program_payloads


# Per-extension encoding table for release stores (see the table's header).
CATEGORIES = Path(__file__).with_name("groups.toml")

# A fixed list misses files the engine reads. tools/deployment/engine_reads.py
# checks it against the paths a browser run requested.
ROOT_DOCUMENTS = (
    "manifest.json", "source.json", "characters.json", "motion-library.glb", "motion-library.index.json",
    "alone-actions.json", "facial-tables.json", "client-config.json", "birthday-parties.json",
    "birthday-party-delivery.json",
    "mysekai-blueprints.json", "mysekai-fixtures.json", "mysekai-items.json",
    "mysekai-music-records.json", "site-groups.json", "talks.json", "tweets.json",
    "tweet-tables.json", "wordings.json",
    "mysekai-tools.json", "mysekai-staminas.json", "mysekai-stamina-recovery.json",
    "mysekai-materials.json", "mysekai-fixture-possessions.json",
    "mysekai-material-possessions.json", "mysekai-system-fixtures.json",
    "mysekai-blueprint-material-costs.json", "mysekai-blueprint-terms.json", "mysekai-ranks.json",
    "mysekai-rank-releases.json", "mysekai-fixture-put-limit-levels.json",
    "mysekai-fixture-player-timelines.json", "mysekai-character-talk-fixture-timelines.json",
    "mysekai-character-talks.json",
    "mysekai-character-talk-no-talk-fixture-actions.json",
    "mysekai-character-talk-action-points.json", "mysekai-character-talk-conditions.json",
    "mysekai-character-talk-condition-groups.json", "mysekai-game-character-unit-groups.json",
    "mysekai-site-footsteps.json", "mysekai-fixture-footsteps.json",
    "mysekai-site-harvest-unavailable-spots.json",
    "browser-base.json", "fixture-reactions.json",
    "birthday-end-character-motions.json",
    "configs.json", "avatar-costumes.json", "avatar-accessories.json",
    "avatar-skin-colors.json", "avatar-coordinates.json",
    "time-manager.json", "materials.json", "mysekai-fixture-put-sounds.json",
    "mysekai-character-talk-fixture-together-communications.json",
    "mysekai-site-housing-layout-unavailable-zones.json", "music-record-audio.json",
)
ASSET_DIRECTORIES = (
    "actor-animations", "fixture-thumbnails", "mysekai-item-icons", "custom-fixture-models",
    "avatar", "avatar-parts", "camera", "cutscene-timeline", "emoticons", "fixture-areas",
    "fixture-attach", "fixture-interface", "fixture-meshes", "fixture-models",
    "fixture-gimmick", "fixture-particles-v2", "fixture-talks", "fixture-timeline", "perf-animations",
    "phenomena", "site", "ui", "ui-layout-v2", "ui-jp",
    "field-camera", "house-transition", "live-transitioner",
    "effect-resources", "graphics-config", "layout-grid",
    "site-action-effects", "site-common-action-effects", "site-harvest-object-effects",
    "site-home-effects", "fixture-put-effects", "site-sound-wave", "site-timeline",
    "honor", "honor_frame", "material-thumbnails",
)
FORMATS = {".json", ".glb", ".gltf", ".png", ".jpg", ".jpeg", ".webp", ".ktx2", ".rgba8", ".ogg", ".wav", ".mp3", ".bin"}
BUILD_EVIDENCE_FILES = {"export-report.json", "export-plan.json", "export-progress.json",
                        "export-source.json", "extraction-report.json", "runtime-validation.json", "runtime-build.json"}


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
    if top.startswith("fixture-") or top == "custom-fixture-models":
        package = next((part.split(".")[0] for part in parts[1:] if part.startswith("mysekai__")), None)
        return (f"furniture/{package}", "furniture") if package else ("common/furniture", "common")
    if top == "phenomena" and re.match(r"\d+_", parts[1]):
        return f"weather/{parts[1]}", "weather"
    if top == "phenomena":
        return "common/weather", "common"
    if top in {"ui", "ui-layout-v2", "ui-jp"}:
        return "common/ui", "common"
    if top == "emoticons":
        return "common/emoticons", "common"
    return f"common/{top}", "common"


MAX_PACKAGE_ENTRIES = 4096
MAX_PACKAGE_METADATA_ESTIMATE = 8 * 1024 * 1024


def group_of_v2(path):
    """Audio banks are not weather packages; logical moly:// paths stay intact."""
    parts = path.split("/")
    if parts[:2] == ["phenomena", "audio"]:
        if len(parts) > 3:
            return "audio/" + parts[2], "audio"
        return "common/audio", "common"
    return group_of(path)


def bounded_groups(grouped, kinds, *, maximum=None):
    """Stable path-hash buckets bound metadata without order-sensitive chunks.

    Only oversized logical families are sharded. The hash is a routing bucket,
    not a second content store or an asset ABI. No clock or region enters it.
    """
    maximum = maximum or MAX_PACKAGE_ENTRIES
    result, result_kinds, families = {}, {}, {}
    for family, paths in sorted(grouped.items()):
        families[family] = []
        hashed = {name: hashlib.sha256(name.encode("utf-8")).hexdigest() for name in paths}
        def visit(members, prefix=""):
            estimate = sum(len(name.encode("utf-8")) + 700 for name in members) + 8192
            if len(members) <= maximum and estimate <= MAX_PACKAGE_METADATA_ESTIMATE:
                name = family + ("/shard-" + prefix if prefix else "")
                result[name] = sorted(members); result_kinds[name] = kinds[family]; families[family].append(name)
                return
            if len(prefix) >= 64 or len(members) < 2:
                raise ValueError("a runtime package cannot fit the bounded manifest contract")
            buckets = defaultdict(list)
            for member in members: buckets[hashed[member][len(prefix)]].append(member)
            for key, bucket in sorted(buckets.items()): visit(bucket, prefix + key)
        visit(paths)
    return result, result_kinds, families


def _source_stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def validate_public_source_document(document):
    """Reject private metadata before publishing either files or stored blobs."""
    try:
        if public_source_document(document) == document:
            return document
    except (TypeError, ValueError):
        pass
    raise ValueError(
        "source.json contains private or unsupported provenance; keep the original root and run "
        "python -m pack.groups --sanitize-source ORIGINAL/source.json "
        "--source-copy CLEAN/source.json in a separate runtime root, then rebuild with "
        "python -m pack.groups --src CLEAN --out NEW_STORE --version VERSION --region REGION; "
        "do not rewrite existing immutable blobs")


def _public_source_from_root(source):
    source_file = source / "source.json"
    if not source_file.is_file():
        return None
    return validate_public_source_document(strict_json(source_file.read_bytes()))


def runtime_paths(source):
    """Single runtime membership owner, excluding extraction/build evidence."""
    source = Path(source).resolve()
    _public_source_from_root(source)
    paths = {path for path in ROOT_DOCUMENTS if (source / path).is_file()}
    paths.update(character_files(source))
    for directory in ASSET_DIRECTORIES:
        base = source / directory
        if not base.is_dir() or base.is_symlink() or getattr(base, "is_junction", lambda: False)():
            continue
        paths.update(p.relative_to(source).as_posix() for p in iter_files(base)
                     if p.suffix.lower() in FORMATS and ".pre-" not in p.as_posix()
                     and not any(part.startswith(".") or part.endswith((".bak", ".staging")) or part == "_previous"
                                 for part in p.relative_to(source).parts)
                     and p.name not in BUILD_EVIDENCE_FILES)
    members = canonical_inputs(source, paths)
    members, _ = close_shader_program_payloads(source, members, extend=True)
    validate_shader_texture_payloads(source, members)
    return dict(sorted(members.items()))


def build_groups(source, output, version, *, region=None, provenance=None, channel="stable", paths=None):
    """Build v2 for an explicit region.

    The no-region Python branch is retained only for offline migration fixtures
    and the legacy integrity regression suite; public CLI publication is v2.
    """
    source, output = separate_output(source, output)
    if region is not None:
        validate_selector(region, channel)
        source_document = _public_source_from_root(source)
        if provenance is None:
            provenance = {"runtime_source": source_document} if source_document is not None else {}
        if not isinstance(provenance, dict):
            raise ValueError("release provenance must be a JSON object")
        provenance = public_release_provenance(provenance)
        metadata = source / "mysekai-fixtures.json"
        if metadata.is_file():
            declared = json.loads(metadata.read_bytes()).get("region")
            if declared is not None and declared != region:
                raise ValueError("runtime region differs from requested release region")
    elif provenance is not None:
        raise ValueError("provenance requires an explicit v2 region")
    with exclusive_lock(output / ".publish.lock"):
        return _build_groups(source, output, version, region=region, provenance=provenance, channel=channel, paths=paths)


def _build_groups(source, output, version, *, region=None, provenance=None, channel="stable", paths=None):
    current_path = output_path(output, "asset-packs.json")
    previous = json.loads(current_path.read_bytes()) if current_path.is_file() else {}
    # Prefer this region's previous generation over an unrelated default.
    if region is not None and channel is not None:
        selector = output_path(output, f"channels/{region}/{channel}.json")
        if selector.is_file():
            pointer = json.loads(selector.read_bytes())
            previous = json.loads(output_path(output, pointer["catalog"]).read_bytes())
    old_packages = {package["id"]: package["manifest"] for package in previous.get("packages", [])}
    paths = runtime_paths(source) if paths is None else canonical_inputs(source, paths)
    # Explicit path selections must satisfy the same concrete dependency set as the
    # automatic exporter membership used by direct-URL deployment staging.
    validate_shader_texture_payloads(source, paths)
    close_shader_program_payloads(source, paths)
    source_stamps = {name: _source_stamp(source / name) for name in paths}
    grouped = defaultdict(list)
    kinds = {}
    for path in canonical_inputs(source, paths):
        group, kind = (group_of_v2(path) if region is not None else group_of(path))
        grouped[group].append(path)
        kinds[group] = kind
    families = {name: [name] for name in grouped}
    if region is not None:
        grouped, kinds, families = bounded_groups(grouped, kinds)
    packages = []
    common = {group for group in grouped if kinds[group] == "common"}
    dependency_groups = {
        "character": ["common/tables", "common/motion", "common/emoticons"],
        "furniture": ["common/tables", "common/furniture"],
        "weather": ["common/tables", "common/weather"],
        "audio": ["common/tables", "common/audio"],
        "site": ["common/tables", "common/site", "common/weather"],
    }
    for group, files in sorted(grouped.items()):
        report = build(source, output, version, paths=files, manifest_name=None,
                       previous_manifest=old_packages.get(group),
                       categories_path=CATEGORIES,
                       format_version=2 if region is not None else 1)
        if region is not None:
            file = Path(report["manifest"])
            if file.stat().st_size > MAX_RUNTIME_DOCUMENT_BYTES:
                raise ValueError("runtime manifest exceeds the native/Web document budget")
            document = json.loads(file.read_bytes())
            for entry in document["entries"]:
                if entry["blob_bytes"] > MAX_RUNTIME_BLOB_BYTES or entry["bytes"] > MAX_RUNTIME_ASSET_BYTES:
                    raise ValueError("runtime asset exceeds native/Web reader budgets: " + entry["path"])
        name = Path(report["manifest"]).relative_to(output).as_posix()
        packages.append({
            "id": group, "kind": kinds[group], "manifest": name,
            **({"content_id": report["content_id"]} if region is not None else {}),
            "dependencies": sorted({target for family in dependency_groups.get(kinds[group], [])
                                    for target in families.get(family, []) if target in common and target != group}),
            "paths": files, "download_bytes": report["download_bytes"],
            "content_bytes": report["content_bytes"],
        })
        print(f"{group}: {len(files)} files, {report['download_bytes']} bytes", flush=True)
    catalog = {"schema": "moly-asset-packs/1", "version": version, "packages": packages}
    if region is not None:
        catalog.update(schema=CATALOG_V2, region=region, provenance=provenance)
    if region is not None and len(canonical_bytes(catalog)) > MAX_RUNTIME_DOCUMENT_BYTES:
        raise ValueError("release catalog exceeds the native/Web document budget")
    for name, stamp in source_stamps.items():
        if _source_stamp(source / name) != stamp:
            raise RuntimeError("source changed during package build: " + name)
    commit_catalog(catalog, output, channel=channel)
    return catalog


def write_public_source_copy(source, destination):
    """Create portable source metadata without editing the extraction original."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or (destination.exists() and source.samefile(destination)):
        raise ValueError("source-copy must differ from the original source.json")
    original = source.read_bytes()
    public = canonical_bytes(public_source_document(json.loads(original)))
    write_bytes(destination, public, mode=PUBLIC_FILE_MODE)
    return {"sourceSha256": hashlib.sha256(original).hexdigest(),
            "publicSha256": hashlib.sha256(public).hexdigest()}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--sanitize-source" in argv:
        parser = argparse.ArgumentParser(description="Write a portable source.json copy in a separate runtime root")
        parser.add_argument("--sanitize-source", required=True)
        parser.add_argument("--source-copy", required=True)
        args = parser.parse_args(argv)
        print(json.dumps(write_public_source_copy(args.sanitize_source, args.source_copy), sort_keys=True))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--region", required=True, help="explicit source region (cn, jp, tw, ...)")
    parser.add_argument("--provenance", help="JSON source identities, projected through the publication allowlist")
    parser.add_argument("--channel", default="stable")
    args = parser.parse_args(argv)
    provenance = json.loads(Path(args.provenance).read_bytes()) if args.provenance else None
    build_groups(args.src, args.out, args.version, region=args.region,
                 provenance=provenance, channel=args.channel)


if __name__ == "__main__":
    main()
