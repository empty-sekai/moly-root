"""A store built from a release root is exactly what the packed publication stages."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

TOOLS = Path(__file__).parents[1] / "tools" / "deployment"


def glb():
    document = b'{"asset":{"version":"2.0"}}'
    document += b" " * (-len(document) % 4)
    binary = b"\0" * 4096
    raw = b"glTF" + struct.pack("<IIII", 2, 28 + len(document) + len(binary), len(document), 0x4e4f534a)
    return raw + document + struct.pack("<II", len(binary), 0x004e4942) + binary


def release_root(root):
    files = {
        "mysekai-fixtures.json": b'{"region":"cn","gameVersion":"6.0.0"}',
        "browser-base.json": b'{"files":[]}',
        "phenomena/audio/loop.json": b'{"packages":[]}',
        "ui/audio/loop.json": b'{"packages":[]}',
        "fixture-models/mysekai__test/model.glb": glb(),
        "fixture-thumbnails/mysekai__test/thumb.png": b"\x89PNG\r\n\x1a\nthumbnail bytes",
    }
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True)


def publication(tmp_path, catalog_id):
    publication = tmp_path / "publication"
    release = publication / "releases/stage-test"
    release.mkdir(parents=True)
    script = b"export const stage = true;\n"
    (release / "stage.mjs").write_bytes(script)
    (release / "integrity.json").write_text(json.dumps({"hashes": {"stage.mjs": hashlib.sha256(script).hexdigest()}}))
    (publication / "cache-worker.mjs").write_text("// synthetic worker\n")
    catalog = publication / "snapshots/cn-test/catalog"
    catalog.mkdir(parents=True)
    (catalog / "index.json").write_text('{"schemaVersion":1,"entries":[]}')
    (publication / "manifest.json").write_text(json.dumps({"release": {"id": "stage-test"}, "snapshots": [{
        "id": "cn-test", "region": "cn", "version": "6.0.0", "packs": True,
        "assets": "/moly/asset-store/", "assetCatalog": catalog_id}]}))
    return publication


def test_built_store_is_staged_as_the_packed_snapshot(tmp_path):
    root = tmp_path / "root"
    release_root(root)
    store = tmp_path / "publication" / "asset-store"
    built = run(TOOLS / "build_store.py", "--source", f"cn={root}", "--store", store)
    assert built.returncode == 0, built.stderr
    summary = json.loads(built.stdout.strip().splitlines()[-1])
    assert summary["region"] == "cn" and summary["version"] == "6.0.0"
    output = tmp_path / "output"
    staged = run(TOOLS / "prepare_deployment.py", "--publication", publication(tmp_path, summary["assetCatalog"]),
                 "--output", output, "--source", f"cn={root}", "--provider", "s3", "--object-prefix=",
                 "--encoding", "identity")
    assert staged.returncode == 0, staged.stderr
    rows = {row["path"]: row for row in json.loads((output / "upload-manifest.json").read_text())["files"]}
    assert f"asset-store/catalogs/{summary['assetCatalog']}.json" in rows
    assert not any(path.startswith("snapshots/cn-test/assets/") for path in rows)
    blobs = [row for path, row in rows.items() if path.startswith("asset-store/blobs/")]
    # Identical files (the two empty audio tables) share one blob.
    contents = {path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert summary["paths"] == 6 and len(blobs) == len(contents) == 5
    types = {row["contentType"] for row in blobs}
    assert {"image/png", "model/gltf-binary"} <= types
    assert all("no-transform" in row["cacheControl"] for row in blobs)
    assert not any(path.startswith("asset-store/channels/") or path == "asset-store/asset-packs.json" for path in rows)


def test_store_that_differs_from_the_root_is_refused(tmp_path):
    root = tmp_path / "root"
    release_root(root)
    store = tmp_path / "publication" / "asset-store"
    built = run(TOOLS / "build_store.py", "--source", f"cn={root}", "--store", store)
    assert built.returncode == 0, built.stderr
    catalog_id = json.loads(built.stdout.strip().splitlines()[-1])["assetCatalog"]
    (root / "fixture-models/mysekai__test/extra.glb").write_bytes(glb())
    staged = run(TOOLS / "prepare_deployment.py", "--publication", publication(tmp_path, catalog_id),
                 "--output", tmp_path / "output", "--source", f"cn={root}", "--provider", "s3",
                 "--object-prefix=", "--encoding", "identity")
    assert staged.returncode != 0
    assert "does not hold exactly the audited membership" in staged.stderr
