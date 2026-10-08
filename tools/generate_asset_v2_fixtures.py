"""Generate small public conformance fixtures for native, Web and publishers.

All image, model and audio bytes here are generated from scratch, not game data.
The same logical content crosses real Python packer -> Rust/WASM/JS consumers.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import random
from pathlib import Path
import struct
import sys
import zlib
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pack.groups import build_groups
from pack.identity import canonical_bytes


def sha(data): return hashlib.sha256(data).hexdigest()


def model():
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"name": "public-conformance-node"}]}
    data = canonical_bytes(doc).rstrip(b"\n"); data += b" " * (-len(data) % 4)
    return struct.pack("<III", 0x46546c67, 2, 20 + len(data)) + struct.pack("<I4s", len(data), b"JSON") + data


def png():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0" + b"\x90\x60\xc0\xff" * 2 + b"\0" + b"\x60\xc0\x90\xff" * 2)) + chunk(b"IEND", b"")


def audio():
    data = b"\0\0" * 400
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16) + b"data" + struct.pack("<I", len(data)) + data


def large_png():
    # A valid >16 MiB identity PNG exposes wasm32 arithmetic regressions.
    width = 2048
    pixels = random.Random(0).randbytes(width * width * 4)
    rows = b"".join(b"\0" + pixels[offset:offset + width * 4]
                    for offset in range(0, len(pixels), width * 4))
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def generate(destination, *, limits=False):
    destination = Path(destination).resolve(); destination.mkdir(parents=True, exist_ok=True)
    shared = {
        "manifest.json": canonical_bytes({"units": [{"unit": 104, "glb": "sd_104.glb", "rig": "sd_104.rig.json"}]}),
        "characters.json": canonical_bytes({"characters": []}),
        "sd_104.glb": model(), "sd_104.rig.json": canonical_bytes({"textures": []}),
        "fixture-models/mysekai__public/model.glb": model(),
        "custom-fixture-models/mysekai__public/model.glb": model(),
        "fixture-thumbnails/mysekai__public.png": png(),
        "site/scenes/site01/site01.glb": model(),
        "site/scenes/site01/site01.json": canonical_bytes({"nodes": []}),
        "ui/action-icon/Talk.png": png(),
        # Python/Rust sort Unicode scalar values, while JS default sort uses
        # UTF-16 units: these two names exercise the interoperability boundary.
        "ui/action-icon/\ue000.png": png(),
        "ui/action-icon/\U0001f600.png": png(),
        "ui/character-portraits/character-portraits.json": canonical_bytes({"characters": [{"unitId": 104, "image": "ui/character-portraits/104.png"}]}),
        "ui/character-portraits/104.png": png(),
        "phenomena/audio/public/voice.wav": audio(),
        "phenomena/001_sunny/config.json": canonical_bytes({"intensity": 1}),
        "fixture-talks/talks.json": canonical_bytes({"talks": []}),
        "fixture-models/player-data.json": canonical_bytes({"fixtures": []}),
    }
    if limits:
        shared["ui/action-icon/large.png"] = large_png()
        shared["fixture-models/mysekai__public/zeros.json"] = canonical_bytes({"padding": "0" * 1048576})
    catalogues = {}
    expected = {}
    for region, version in [("cn", "1.0.0"), ("jp", "2.0.0"), ("tw", "3.0.0")]:
        source = destination / f"source-{region}"; source.mkdir(exist_ok=True)
        files = dict(shared)
        files["mysekai-fixtures.json"] = canonical_bytes({"region": region, "gameVersion": version, "fixtures": []})
        files["fixture-gimmick/browser-index.json"] = canonical_bytes({"schemaVersion": 1, "generator": "moly-gimmick-split-v1", "region": region, "gameVersion": version, "packages": []})
        base = [{"path": path, "sha256": sha(data), "decodedBytes": len(data), "downloadBytes": len(data), "encoding": "identity"} for path, data in files.items()]
        files["browser-base.json"] = canonical_bytes({"schemaVersion": 1, "generator": "moly-browser-base-v1", "region": region, "gameVersion": version, "files": base,
            "decodedBytes": sum(row["decodedBytes"] for row in base), "downloadBytes": sum(row["downloadBytes"] for row in base)})
        files["source.json"] = canonical_bytes({"source": {"region": region, "gameVersion": version}, "note": "Generated public integration fixture, not proprietary assets"})
        for name, data in files.items():
            file = source / name; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(data)
        catalog = build_groups(source, destination / "store", version, region=region)
        catalogues[region] = {"id": sha(canonical_bytes(catalog)), "version": version, "source": source.name}
        expected[region] = {name: {"bytes": len(data), "sha256": sha(data)} for name, data in files.items() if name != "source.json"}
        # Publisher browsing input (Rust projection identity, not runtime CAS).
        (destination / f"catalog-{region}.json").write_bytes(canonical_bytes({"schemaVersion": 1, "ready": True, "mode": "independent", "region": region, "version": version,
            "characters": [], "entries": [], "tabs": [], "facets": {}, "fixtures": []}))
    legacy = build_groups(destination / "source-cn", destination / "legacy", "legacy-release")
    result = {"schema": "moly-public-pack-fixtures/1", "catalogs": catalogues, "expected": expected,
              "legacy_catalog_sha256": sha((destination / "legacy/asset-packs.json").read_bytes()),
              "shared_logical_paths": sorted(shared)}
    (destination / "fixtures.json").write_bytes(canonical_bytes(result))
    print(json.dumps({"destination": str(destination), "catalogs": catalogues}, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--out", required=True)
    parser.add_argument("--limits", action="store_true", help="include actual >16 MiB identity and highly compressible assets")
    args = parser.parse_args(); generate(args.out, limits=args.limits)


if __name__ == "__main__": main()
