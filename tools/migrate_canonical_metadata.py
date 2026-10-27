#!/usr/bin/env python3
"""Explicit, hash-pinned metadata migration for reviewed already-canonical producers.

This is NOT a basis converter. Old fixtures/custom fixtures/sites are refused.
It writes a separate overlay and proves full original JSON and binary equality
apart from the new asset/root contract metadata. Source paths are never written.
"""
from __future__ import annotations
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from core.atomic import write_json
from core.coordinate_contract import CONTRACT, UNITS, validate_glb

# Fingerprints of the producer code covered by this metadata migration.
# They keep the guard usable in source archives and installed source copies.
# Update them only after reviewing the coordinate-writing code.
REVIEWED_PRODUCERS = {
    # Adds source controller/clip identities to the rig only. Removing that
    # read-only helper, its call and the one sidecar field reproduces the prior
    # reviewed AST; mesh, animation, skinning and GLB writes are unchanged.
    'src/chara/characters.py': '435068f0dd34ff6cb3c329196e0a4165ff78fa409ee93469dec881aff0d7ad70',
    'src/chara/motion_library.py': '3ca5702e6ff3d02ee065c32dc5a2e1fe24716999976c135ffa77e52a3b26465c',
    'src/chara/player_avatar.py': 'f10f702f6fc3b58f23c1f1a5a388bbe94789e27fec7d93d2b8316abf13a012e1',
    'src/chara/avatar_parts.py': '74a42b5519bae8b645850e0281420c707ebac33fa08df3f5bd6326a4dcf89fc3',
    'src/chara/avatar_compose.py': '366c5f5e1253728dbbcbb6f476f26dfddaa2062cbe7a6e0ccab5bffd85ec4ca7',
    'src/chara/sd_switch.py': '3943cde0837981f94dc9721271313a96dae6a6df6bc928802e2bd82a1f0d473c',
    # Only the CLI's default output location changed (MOLY_EXTRACT_OUT);
    # reverting that return expression reproduces the previous reviewed hash.
    # The coordinate-writing implementation is byte-identical.
    'src/perf/animations.py': '16e31e5b719d47bc04077db95236f3e1efa4295d96fe57433a03f94154e9e851',
    'src/core/mesh.py': 'e310116a5d0a905f9cb4f89e2e6c8a5057fd1feb86ded789f00abc4d076fa882',
    'src/phenomena/meshes.py': 'd4a20e8f9b0b1d0b9ce7a00ea52ebd63745851c5b9d4b28a6b2d39a46f481a8f',
    'src/core/gltf.py': '11129743aab66fa0ed7c6997c7f779f1a06d4cc88a021bfb17a1a42d506451fd',
}
REVIEWED_GEOMETRY_SYMBOLS = {
    'Graph': '695e191967cb70f443462e676d37db6e43ffecd369712382ddf75acacbc42fd0',
    'Builder': '3c0aec4986d9f2a0822674a351f92a8f63c585a0b1aa38605c79f73edfb49b5e',
    'single_mesh_blob': '5a4a35021764d74cefa38b270234d9e7bac06650b521f4a292a6128042c158a3',
}
UNCHANGED = tuple(REVIEWED_PRODUCERS)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def unpack(data):
    if len(data) < 20 or data[:4] != b"glTF" or struct.unpack_from("<II", data, 4) != (2, len(data)):
        raise ValueError("invalid GLB header")
    size, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON" or 20 + size > len(data):
        raise ValueError("invalid GLB JSON")
    return json.loads(data[20:20 + size]), data[20 + size:]


def family(path, document):
    """Path AND producer AND existing explicit evidence, never suffix alone."""
    p = path.as_posix()
    asset = document.get("asset", {})
    generator = asset.get("generator")
    if re.fullmatch(r"sd_\d+\.glb", p) and generator == "moly-root character extractor":
        if asset.get("extras", {}).get("coordinates") != "unity x-axis reflected":
            raise ValueError(f"{p}: missing old explicit reflection evidence")
        return "character"
    if (p == "motion-library.glb" or p.startswith("actor-animations/")) and generator == "moly-root shared motion library":
        if asset.get("extras", {}).get("binding") != "humanoid-bone-name":
            raise ValueError(f"{p}: wrong actor binding producer")
        return "humanoid-motion"
    if (p.startswith("avatar-parts/") or p.startswith("avatar/")) and generator == "moly-root avatar-parts":
        return "avatar-parts"
    if p == "avatar/motion/mysekai__player_avatar.glb" and generator == "moly-root player avatar":
        return "player-avatar"
    if p == "avatar/motion/sd-switch-simple.glb" and generator == "moly product simple-switch adapter v1":
        return "avatar-switch"
    if p.startswith("phenomena/models/") and generator == "moly-root":
        return "phenomena-model"
    # Harvest tools use the site Builder but not source NavMesh sidecars. Its
    # mesh/TRS conversion code is fingerprinted separately below.
    if p.startswith("avatar/tools/") and generator == "moly-root":
        if any("navMeshObstacles" in node.get("extras", {}) for node in document.get("nodes", [])):
            raise ValueError(f"{p}: mixed raw NavMesh extras require re-export")
        return "harvest-tool"
    return None


def producer_evidence():
    evidence = []
    for name, expected in REVIEWED_PRODUCERS.items():
        current = (REPO / name).read_bytes().replace(b"\r\n", b"\n")
        actual = sha(current)
        if actual != expected:
            raise ValueError(f"reviewed producer fingerprint changed: {name}")
        evidence.append({"path": name, "baselineSha256": expected,
                         "currentSha256": actual,
                         "proof": "producer matches its reviewed source fingerprint"})
    name = "src/sites/geometry.py"
    nodes = ast.parse((REPO / name).read_text(encoding="utf-8")).body
    for symbol, expected in REVIEWED_GEOMETRY_SYMBOLS.items():
        node = next((node for node in nodes if getattr(node, "name", None) == symbol), None)
        if node is None or sha(ast.dump(node, include_attributes=False).encode()) != expected:
            raise ValueError(f"reviewed geometry fingerprint changed: {symbol}")
        evidence.append({"path": name, "symbol": symbol, "astSha256": expected,
                         "proof": "spatial producer matches its reviewed AST fingerprint"})
    return evidence


def migrate_blob(data):
    original, tail = unpack(data)
    document = copy.deepcopy(original)
    targets = [document["asset"]] + [document["nodes"][i]
        for scene in document.get("scenes", []) for i in scene.get("nodes", [])]
    for target in targets:
        extras = target.setdefault("extras", {})
        for key, value in (("coordinateContract", CONTRACT), ("coordinateUnits", UNITS)):
            if key in extras and extras[key] != value:
                raise ValueError(f"conflicting {key}")
            extras[key] = value
    # Prove all original JSON including extras remains exactly unchanged.
    restored = copy.deepcopy(document)
    pairs = [(original["asset"], restored["asset"])] + [
        (original["nodes"][i], restored["nodes"][i])
        for scene in original.get("scenes", []) for i in scene.get("nodes", [])]
    for before, after in pairs:
        for key in ("coordinateContract", "coordinateUnits"):
            if key not in before.get("extras", {}):
                after.get("extras", {}).pop(key, None)
        if "extras" not in before:
            after.pop("extras", None)
    if restored != original:
        raise ValueError("metadata migration changed existing JSON")
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    encoded += b" " * (-len(encoded) % 4)
    output = b"glTF" + struct.pack("<II", 2, 20 + len(encoded) + len(tail)) + struct.pack("<I4s", len(encoded), b"JSON") + encoded + tail
    _, output_tail = unpack(output)
    if output_tail != tail:
        raise ValueError("metadata migration changed GLB binary chunks")
    return output, {"binaryChunksSha256": sha(tail), "outputBinaryChunksSha256": sha(output_tail),
                    "originalJsonSha256": sha(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()),
                    "outputJsonWithoutAddedMetadataSha256": sha(json.dumps(restored, sort_keys=True, separators=(",", ":")).encode()),
                    "machineCheck": {"originalJsonPreserved": True, "binaryChunksPreserved": True,
                                     "addedMetadata": ["asset.extras.coordinateContract", "asset.extras.coordinateUnits",
                                                       "sceneRoots[].extras.coordinateContract", "sceneRoots[].extras.coordinateUnits"]},
                    "proof": "all original JSON and every binary chunk byte preserved; only new asset/root metadata"}


def json_paths(root):
    for path in root.rglob("*.json"):
        relative = path.relative_to(root)
        if any(p.startswith(".") or p.startswith("_previous") or ".bak" in p or ".pre-" in p for p in relative.parts):
            continue
        yield path


def verify_overlay(source, out):
    """Recheck original bytes, exact migration output and machine evidence."""
    receipt_path = out / "coordinate-migration-receipt.json"
    receipt = read(receipt_path)
    for row in receipt["files"]:
        original = (source / row["path"]).read_bytes()
        output = (out / row["path"]).read_bytes()
        if sha(original) != row["sourceSha256"] or sha(output) != row["outputSha256"]:
            raise ValueError(f"migration receipt hash mismatch: {row['path']}")
        expected, proof = migrate_blob(original)
        if output != expected:
            raise ValueError(f"migration differs from metadata-only operation: {row['path']}")
        row.update(proof)
    receipt["verifiedMetadataOnly"] = True
    write_json(receipt_path, receipt)
    return {"verifiedGlbs": len(receipt["files"]), "out": str(out)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--verify-existing-overlay", action="store_true")
    args = parser.parse_args(argv)
    source, out = args.source_root.resolve(), args.out.resolve()
    if args.verify_existing_overlay:
        if out == source or out.is_relative_to(source) or source.is_relative_to(out):
            parser.error("verification needs disjoint original and overlay")
        print(json.dumps(verify_overlay(source, out), indent=2))
        return 0
    if out.exists() or out == source or out.is_relative_to(source) or source.is_relative_to(out):
        parser.error("output must be a nonexistent disjoint overlay")
    evidence = producer_evidence()
    routes = read(source / "actor-animations/index.json")
    routed = {}
    for row in routes.get("libraries", []):
        for key, hash_key in (("glb", "glbSha256"), ("index", "indexSha256")):
            path = source / row[key]
            if sha(path.read_bytes()) != row[hash_key]:
                raise ValueError(f"actor source route hash mismatch: {row[key]}")
        routed[row["glb"]] = row["glbSha256"]
    jobs, skipped = [], []
    for path in sorted(source.rglob("*.glb")):
        relative = path.relative_to(source)
        if any(p.startswith(".") or p.startswith("_previous") or ".bak" in p or ".pre-" in p for p in relative.parts):
            continue
        data = path.read_bytes()
        document, _ = unpack(data)
        kind = family(relative, document)
        if kind is None:
            skipped.append(relative.as_posix())
            continue
        jobs.append({"path": relative.as_posix(), "sourceSha256": sha(data), "family": kind,
                     "generator": document["asset"]["generator"], "actorRouteHashVerified": relative.as_posix() in routed})
    out.mkdir(parents=True)
    producer_fingerprint = sha(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode())
    write_json(out / "coordinate-migration-allowlist.json", {"producerFingerprintSha256": producer_fingerprint,
        "coordinateContract": CONTRACT, "producers": evidence, "files": jobs,
        "excluded": skipped})
    changes = {}
    results = []
    for job in jobs:
        data = (source / job["path"]).read_bytes()
        if sha(data) != job["sourceSha256"]:
            raise ValueError(f"source changed after allowlist: {job['path']}")
        blob, proof = migrate_blob(data)
        path = out / job["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        report = validate_glb(path)
        if report["errors"]:
            raise ValueError(report["errors"])
        changes[job["sourceSha256"]] = (sha(blob), len(blob))
        results.append({**job, "outputSha256": sha(blob), **proof})
    # Update only exact verified old hash values. File paths stay stable, even
    # for content-named geometry: the original alias remains the lookup key.
    documents = [p.relative_to(source).as_posix() for p in json_paths(source)]
    updated = {}
    def patch(value):
        if isinstance(value, dict):
            digest = value.get("sha256")
            if digest in changes and isinstance(value.get("bytes"), int):
                value["bytes"] = changes[digest][1]
            return {k: patch(v) for k, v in value.items()}
        if isinstance(value, list):
            return [patch(v) for v in value]
        return changes[value][0] if isinstance(value, str) and value in changes else value
    for _ in range(12):
        advanced = False
        for relative in documents:
            original = (source / relative).read_bytes()
            current_path = out / relative
            document = read(current_path if current_path.is_file() else source / relative)
            revised = patch(copy.deepcopy(document))
            if revised == document:
                continue
            path = out / relative
            previous_hash = sha(path.read_bytes()) if path.is_file() else sha(original)
            write_json(path, revised)
            output_hash = sha(path.read_bytes())
            changes[previous_hash] = (output_hash, path.stat().st_size)
            changes[sha(original)] = (output_hash, path.stat().st_size)
            updated[relative] = {"path": relative, "sourceSha256": sha(original), "outputSha256": output_hash}
            advanced = True
        if not advanced:
            break
    else:
        raise ValueError("JSON hash references did not converge; possible cyclic receipt")
    receipt = {"coordinateContract": CONTRACT, "producerFingerprintSha256": producer_fingerprint, "status": "complete",
               "migratedGlbs": len(results), "files": results,
               "updatedJsonReferences": list(updated.values()), "excluded": skipped,
               "notClaimed": ["raw Unity re-export", "new numeric mesh/animation verification",
                              "unreviewed producer families", "whole snapshot readiness"]}
    write_json(out / "coordinate-migration-receipt.json", receipt)
    print(json.dumps({"out": str(out), "glbs": len(results), "jsonReferences": len(updated),
                      "excluded": len(skipped)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
