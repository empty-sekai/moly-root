"""Export source master data required to import a player's housing layouts.

Run with ``python -m fixtures.player_data --master DIR --out ASSET_ROOT``.
The document belongs to the furniture catalog and contains no player data.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from core.jsonio import write_json
from core.master import Master
from core.unity import configure_fallback_unity_version


TABLES = (
    "mysekaiFixtures", "mysekaiCustomFixtures", "mysekaiSites",
    "mysekaiSiteLevels", "mysekaiSiteLayouts", "mysekaiRankReleases",
)


def export(master_source, out_dir, *, region="cn", game_version="6.0.0", bundles=None, unity_version=None):
    master = Master(master_source)
    tables = {}
    for name in TABLES:
        rows = master.table(name)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{name}: expected a non-empty master table")
        ids = [row.get("id") for row in rows]
        if None in ids or len(set(ids)) != len(ids):
            raise ValueError(f"{name}: missing or duplicate master ID")
        tables[name] = rows
    document = {"version": 1, "region": region, "gameVersion": game_version,
                "tables": tables}
    if bundles is not None:
        configure_fallback_unity_version(unity_version)
        document["colorTextures"] = export_colors(tables["mysekaiFixtures"], Path(bundles), Path(out_dir))
    destination = Path(out_dir) / "fixture-models" / "player-data.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json(destination, document)
    return document


def export_colors(fixtures, bundles, out):
    """Export the named color textures loaded by FixtureColorDataCollectionFactory."""
    from core.assets.packages import PackageStore
    from core.gltf import unity_sampler

    requirements = {}
    for fixture in fixtures:
        variants = fixture.get("mysekaiFixtureAnotherColors") or []
        asset = fixture.get("assetbundleName", "")
        if not variants or not asset.startswith("mdl_"):
            continue
        requirements.setdefault(asset, {1}).update(int(row["textureId"]) for row in variants)

    result = {}
    for asset, texture_ids in sorted(requirements.items()):
        name = "mysekai__fixture__" + asset
        store = PackageStore([], root=bundles)
        package = store.package(name)
        if package is None:
            raise ValueError(f"Missing furniture package for color export: {name}")
        textures = {}
        for record in package.files:
            for path_id, kind in record.kinds.items():
                if kind != "Texture2D":
                    continue
                obj = record.objects[path_id]
                texture_name = obj.peek_name()
                if texture_name in textures:
                    raise ValueError(f"Duplicate texture name in {name}: {texture_name}")
                textures[texture_name] = obj
        written = {}

        def write_texture(texture_name):
            if texture_name not in textures:
                return None
            if texture_name in written:
                return written[texture_name]
            if not texture_name or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in texture_name):
                raise ValueError(f"Texture name is not a file identifier: {texture_name!r}")
            texture = textures[texture_name].read()
            relative = Path("fixture-models") / "colors" / name / f"{texture_name}.png"
            destination = out / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            texture.image.convert("RGBA").save(destination, format="PNG")
            settings = texture.m_TextureSettings
            entry = {"path": relative.as_posix(),
                     "sampler": unity_sampler(settings.m_FilterMode, settings.m_WrapU,
                                              settings.m_WrapV, texture.m_MipCount)}
            written[texture_name] = entry
            return entry

        colors = {}
        base = "tex_" + asset[len("mdl_"):]
        for texture_id in sorted(texture_ids):
            main = write_texture(f"{base}_{texture_id}")
            emission = None
            for suffix in ("always", "dark", "bright"):
                emission = write_texture(f"{base}_emi_{suffix}_{texture_id}")
                if emission is not None:
                    break
            # A missing named texture is the source LoadTexture null result;
            # SetColor leaves existing materials intact in that case.
            colors[str(texture_id)] = {"main": main, "emission": emission}
        result[name] = colors
        if len(result) % 50 == 0:
            print(f"Exported color textures for {len(result)} furniture packages", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--region", choices=("cn", "jp"), default="cn")
    parser.add_argument("--game-version", default="6.0.0")
    parser.add_argument("--bundles", help="Decrypted furniture bundle directory, including color variants")
    parser.add_argument("--unity-version")
    args = parser.parse_args()
    document = export(args.master, args.out, region=args.region,
                      game_version=args.game_version, bundles=args.bundles, unity_version=args.unity_version)
    print("Exported player import catalog:",
          ", ".join(f"{name}={len(rows)}" for name, rows in document["tables"].items()))


if __name__ == "__main__":
    main()
