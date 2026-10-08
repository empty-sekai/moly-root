"""Metadata migration is scoped, hash-pinned and numerically byte-preserving."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct

import pytest

spec = importlib.util.spec_from_file_location("migration", Path(__file__).resolve().parents[1]
                                            / "tools/migrate_canonical_metadata.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def blob(document, tail=b"\x08\x00\x00\x00BIN\0\x01\x02\x03\x04\x05\x06\x07\x08"):
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    return b"glTF" + struct.pack("<II", 2, 20 + len(encoded) + len(tail)) + struct.pack("<I4s", len(encoded), b"JSON") + encoded + tail


def test_metadata_only_keeps_every_old_field_and_binary_byte():
    document = {"asset": {"version": "2.0", "generator": "moly-root character extractor",
                           "extras": {"coordinates": "unity x-axis reflected"}},
                "nodes": [{"translation": [-2., 3., 4.], "rotation": [.1, -.2, -.3, .9],
                           "extras": {"unrelated": "preserve"}}], "scenes": [{"nodes": [0]}],
                "animations": [{"channels": [], "extras": {"sourceClip": "exact"}}],
                "skins": [{"inverseBindMatrices": 0}], "accessors": [{"min": [-2., 3., 4.]}]}
    original = blob(document)
    output, proof = migration.migrate_blob(original)
    got, tail = migration.unpack(output)
    assert tail == migration.unpack(original)[1]
    assert got["nodes"][0]["translation"] == [-2., 3., 4.]
    assert got["animations"] == document["animations"]
    assert got["nodes"][0]["extras"]["unrelated"] == "preserve"
    assert got["asset"]["extras"]["coordinateContract"] == migration.CONTRACT
    assert proof["binaryChunksSha256"] == migration.sha(tail)
    assert proof["outputBinaryChunksSha256"] == proof["binaryChunksSha256"]
    assert proof["outputJsonWithoutAddedMetadataSha256"] == proof["originalJsonSha256"]
    assert proof["machineCheck"]["originalJsonPreserved"] is True


def test_same_generator_never_admits_old_fixture_or_site():
    generic = {"asset": {"generator": "moly-root"}}
    assert migration.family(Path("phenomena/models/a.glb"), generic) == "phenomena-model"
    assert migration.family(Path("site/a.glb"), generic) is None
    assert migration.family(Path("unknown/a.glb"), generic) is None
    fixture = {"asset": {"generator": "moly-root fixture extractor"}}
    assert migration.family(Path("fixture-models/a.glb"), fixture) is None
    assert migration.family(Path("custom-fixture-models/a.glb"), fixture) is None


def test_character_requires_existing_explicit_reflection_evidence():
    document = {"asset": {"generator": "moly-root character extractor"}}
    with pytest.raises(ValueError, match="explicit reflection evidence"):
        migration.family(Path("sd_101.glb"), document)


def test_conflicting_contract_is_rejected():
    document = {"asset": {"version": "2.0", "extras": {"coordinateContract": "raw-unity"}},
                "nodes": [], "scenes": []}
    with pytest.raises(ValueError, match="conflicting coordinateContract"):
        migration.migrate_blob(blob(document))


def test_reviewed_producer_baseline_is_verified():
    evidence = migration.producer_evidence()
    assert len(evidence) >= len(migration.UNCHANGED)
    assert all(row.get("proof") for row in evidence)


def test_producer_guard_works_without_git_and_rejects_modified_source(tmp_path, monkeypatch):
    names = [*migration.REVIEWED_PRODUCERS, "src/sites/geometry.py"]
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(migration.REPO / name, target)
    monkeypatch.setattr(migration, "REPO", tmp_path)
    assert migration.producer_evidence()
    changed = tmp_path / names[0]
    changed.write_bytes(changed.read_bytes() + b"\n# changed producer\n")
    with pytest.raises(ValueError, match="producer fingerprint changed"):
        migration.producer_evidence()


def test_geometry_guard_rejects_modified_spatial_code(tmp_path, monkeypatch):
    names = [*migration.REVIEWED_PRODUCERS, "src/sites/geometry.py"]
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(migration.REPO / name, target)
    monkeypatch.setattr(migration, "REPO", tmp_path)
    geometry = tmp_path / "src/sites/geometry.py"
    geometry.write_text(geometry.read_text(encoding="utf-8") + "\nclass Graph: pass\n", encoding="utf-8")
    # Replace the original node too: a duplicate trailing class must not hide it.
    text = geometry.read_text(encoding="utf-8").replace("class Graph:", "class ChangedGraph:", 1)
    geometry.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="geometry fingerprint changed"):
        migration.producer_evidence()
