#!/usr/bin/env python3
"""Write a NEW GLB-only overlay and measure detached collision-table storage.

This is not a deployment. Existing textures, shaders and animations stay in the
source snapshot; its files are never opened for writing. Every binary chunk is
copied byte-for-byte. JSON uses the producer's lossless Python serializer, not
float rounding or JavaScript's lossy conversion of 64-bit source IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from core.fixture_collision_document import (
    INDEX_VERSION, compact_fixture_collision, read_fixture_collision,
)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def semantic_fingerprint(document):
    """Fingerprint every original value except the relocated table envelopes."""
    result = dict(document)
    result["extras"] = dict(document.get("extras", {}))
    reference = result["extras"].pop("fixtureCollisionRef", None)
    result["extras"].pop("fixtureCollision", None)
    result["nodes"] = list(document.get("nodes", []))
    if reference is not None:
        if reference["node"] != len(result["nodes"]) - 1:
            raise ValueError("deduplicated metadata must be appended, never reorder source nodes")
        result["nodes"].pop()
    for scene in document.get("scenes", []):
        for index in scene.get("nodes", []):
            node = dict(result["nodes"][index])
            node["extras"] = dict(node.get("extras", {}))
            node["extras"].pop("fixtureCollision", None)
            node["extras"].pop("fixtureCollisionRef", None)
            result["nodes"][index] = node
    return hashlib.sha256(encoded(result)).hexdigest()


def rewrite(source, target):
    with source.open("rb") as reader:
        header = reader.read(20)
        if (len(header) != 20 or header[:4] != b"glTF"
                or struct.unpack_from("<II", header, 4) != (2, source.stat().st_size)
                or header[16:20] != b"JSON"):
            raise ValueError(f"invalid GLB header: {source}")
        json_size = struct.unpack_from("<I", header, 12)[0]
        raw = reader.read(json_size)
        if len(raw) != json_size:
            raise ValueError(f"truncated GLB JSON: {source}")
        document = json.loads(raw)
        old_nodes = len(document.get("nodes", []))
        table_hash = hashlib.sha256(encoded(read_fixture_collision(document))).hexdigest()
        semantic_hash = semantic_fingerprint(document)
        changed = compact_fixture_collision(document)
        if semantic_fingerprint(document) != semantic_hash:
            raise ValueError(f"source node/scene/animation semantics changed: {source}")
        if hashlib.sha256(encoded(read_fixture_collision(document))).hexdigest() != table_hash:
            raise ValueError(f"source collision values changed: {source}")
        payload = encoded(document)
        payload += b" " * (-len(payload) % 4)
        total = source.stat().st_size - json_size + len(payload)
        tail_hash = hashlib.sha256()
        with target.open("xb") as writer:
            writer.write(b"glTF" + struct.pack("<III", 2, total, len(payload)) + b"JSON" + payload)
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
                tail_hash.update(chunk)
    with target.open("rb") as reader:
        reader.seek(20 + len(payload))
        if hashlib.file_digest(reader, "sha256").hexdigest() != tail_hash.hexdigest():
            raise ValueError(f"binary chunks changed: {source}")
    return {"file": source.name, "changed": changed, "sourceNodes": old_nodes,
            "beforeBytes": source.stat().st_size, "afterBytes": total,
            "beforeJsonBytes": json_size, "afterJsonBytes": len(payload),
            "sourceSemanticsSha256": semantic_hash, "collisionSha256": table_hash,
            "binaryChunksSha256": tail_hash.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="canonical source root containing cn/jp")
    parser.add_argument("--output", type=Path, required=True, help="new non-existing GLB overlay directory")
    parser.add_argument("--regions", nargs="+", choices=("cn", "jp"), default=["cn", "jp"])
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        parser.error("output must be a new directory separate from the source tree")
    output.mkdir(parents=True)
    summaries = []
    for region in dict.fromkeys(args.regions):
        region_source = source / region / "fixture-models"
        region_output = output / region / "fixture-models"
        region_output.mkdir(parents=True)
        rows = []
        for index, path in enumerate(sorted(region_source.glob("*.glb"))):
            rows.append(rewrite(path, region_output / path.name))
            if (index + 1) % 100 == 0:
                print(f"{region}: {index + 1} GLBs verified", flush=True)
        if not rows:
            raise ValueError(f"no fixture GLBs in {region_source}")
        index_path = region_source / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if index.get("version") not in (3, INDEX_VERSION):
            raise ValueError(f"unsupported source fixture index: {index_path}")
        index["version"] = INDEX_VERSION
        (region_output / "index.json").write_bytes(encoded(index))
        summary = {"region": region, "files": len(rows),
                   **{key: sum(row[key] for row in rows) for key in
                      ("beforeBytes", "afterBytes", "beforeJsonBytes", "afterJsonBytes")}}
        summary["savedBytes"] = summary["beforeBytes"] - summary["afterBytes"]
        summary["savedPercent"] = 100 * summary["savedBytes"] / summary["beforeBytes"]
        summaries.append(summary)
        (output / f"{region}-receipts.json").write_bytes(encoded(rows))
        print(json.dumps(summary), flush=True)
    report = {"kind": "fixture-collision-dedup-overlay", "source": str(source),
              "output": str(output), "indexVersion": INDEX_VERSION,
              "notDeployment": True, "binaryChunksUnchanged": True,
              "allOtherJsonValuesUnchanged": True, "regions": summaries}
    (output / "report.json").write_bytes(encoded(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
