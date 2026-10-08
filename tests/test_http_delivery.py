"""Direct HTTP delivery remains lossless and separate from client-side codecs."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from pack import http_delivery


def test_browser_publication_omits_only_a_complete_split_controller_source(tmp_path):
    script = Path(__file__).parents[1] / "tools/deployment/prepare_deployment.py"
    spec = importlib.util.spec_from_file_location("delivery_prepare_split", script)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    folder = tmp_path / "fixture-gimmick"
    packages = folder / "by-package"
    packages.mkdir(parents=True)
    original = {"name": "fixture-one", "component": {"integer": 9007199254740993}}
    source = folder / "gimmicks.json"
    source.write_text(json.dumps({"version": 1, "packages": [original]}))
    body = json.dumps({"schemaVersion": 1, "region": "cn", "gameVersion": "6.0.0",
                       "package": original}).encode()
    digest = hashlib.sha256(body).hexdigest()
    package = packages / f"{digest}.json"
    package.write_bytes(body)
    index = folder / "browser-index.json"
    document = {"schemaVersion": 1, "generator": "moly-gimmick-split-v1",
                "region": "cn", "gameVersion": "6.0.0",
                "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "packages": [{"name": original["name"],
                              "path": f"fixture-gimmick/by-package/{digest}.json",
                              "sha256": digest, "decodedBytes": len(body)}]}
    index.write_text(json.dumps(document))
    (tmp_path / "browser-base.json").write_text('{"files":[]}')
    members = {"fixture-gimmick/gimmicks.json": source,
               "fixture-gimmick/browser-index.json": index,
               document["packages"][0]["path"]: package}
    report = {"files": 3, "bytes": sum(path.stat().st_size for path in members.values()),
              "excludedFiles": 0, "excludedBytes": 0,
              "excludedByExtension": {}, "additionalDependencies": []}
    incomplete = json.loads(index.read_text())
    incomplete["packages"] = []
    index.write_text(json.dumps(incomplete))
    with pytest.raises(ValueError, match="omits a native package"):
        prepare.omit_split_controller_source(tmp_path, members, report, "cn", "6.0.0")
    index.write_text(json.dumps(document))
    prepare.omit_split_controller_source(tmp_path, members, report, "cn", "6.0.0")
    assert "fixture-gimmick/gimmicks.json" not in members
    assert report["omittedLegacyGimmickBytes"] == source.stat().st_size
    assert report["files"] == 2


@pytest.mark.parametrize("suffix", [".glb", ".rgba8", ".json", ".glsl", ".wgsl"])
def test_generated_representation_keeps_decoded_identity_and_source(tmp_path, suffix):
    source = tmp_path / ("asset" + suffix)
    raw = b"glTF" + bytes(range(64)) * 400
    source.write_bytes(raw)
    result = http_delivery.choose_representation(source, source.name, tmp_path / "output")
    decode = gzip.decompress if result.encoding == "gzip" else http_delivery.brotli.decompress
    assert decode(result.body) == source.read_bytes() == raw
    assert result.generated and len(result.body) < len(raw)
    assert result.source.is_relative_to(tmp_path / "output")
    assert not Path(str(source) + ".gz").exists()
    assert not Path(str(source) + ".br").exists()
    assert result.manifest_fields["decodedSha256"] == hashlib.sha256(raw).hexdigest()
    assert result.manifest_fields["sha256"] == hashlib.sha256(result.body).hexdigest()
    assert result.manifest_fields["deliveryMode"] == "fixed"
    assert result.manifest_fields["requiredAcceptEncoding"] == result.encoding
    assert "vary" not in result.manifest_fields


def test_stale_sidecar_is_rejected_and_valid_sidecar_can_be_reused(tmp_path):
    source = tmp_path / "asset.glb"
    raw = b"glTF" + b"source geometry " * 1000
    source.write_bytes(raw)
    sidecar = Path(str(source) + ".gz")
    sidecar.write_bytes(gzip.compress(b"not the current source", mtime=0))
    result = http_delivery.choose_representation(source, source.name, tmp_path / "out", mode="gzip")
    assert len(result.invalid_sidecars) == 1
    assert gzip.decompress(result.body) == raw
    sidecar.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    result = http_delivery.choose_representation(source, source.name, tmp_path / "out", mode="gzip", generate=False)
    assert result.source == sidecar and not result.generated
    assert result.invalid_sidecars == ()


@pytest.mark.parametrize("logical,raw", [
    ("blobs/ab/client.gzz", b"client-decoded pack body" * 1000),
    ("blobs/ab/media.bin", b"OggS" + b"vorbis page" * 1000),
    ("payload.bin", gzip.compress(b"already gzip" * 1000, mtime=0)),
    ("image.png", b"already compressed image" * 1000),
    ("asset.glb.br", b"brotli payload" * 1000),
])
def test_never_wrap_client_encoded_blobs_or_media(tmp_path, logical, raw):
    source = tmp_path / "source"
    source.write_bytes(raw)
    result = http_delivery.choose_representation(source, logical, tmp_path / "out")
    assert result.encoding == "identity" and result.body == raw
    assert result.manifest_fields["requiredAcceptEncoding"] is None
    assert result.generation_skipped == "already-encoded-payload"
    assert not (tmp_path / "out").exists()


def test_identity_pack_blob_is_delivered_compressed(tmp_path):
    source = tmp_path / "source"
    raw = b"identity pack body" * 1000
    source.write_bytes(raw)
    result = http_delivery.choose_representation(source, "blobs/ab/content.bin", tmp_path / "out", mode="gzip")
    assert result.encoding == "gzip" and gzip.decompress(result.body) == raw
    assert result.decoded_sha256 == http_delivery.sha256(raw)


def test_incompressible_and_identity_mode_do_not_create_sidecars(tmp_path):
    source = tmp_path / "random.rgba8"
    source.write_bytes(random.Random(21).randbytes(16384))
    result = http_delivery.choose_representation(source, source.name, tmp_path / "out")
    assert result.encoding == "identity"
    source.write_bytes(b"highly compressible" * 1000)
    result = http_delivery.choose_representation(source, source.name, tmp_path / "out", mode="identity")
    assert result.encoding == "identity" and not (tmp_path / "out").exists()


def test_gzip_fallback_without_brotli_is_visible_in_prepare_summary(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.rgba8"
    source.write_bytes(b"rgba" * 4096)
    monkeypatch.setattr(http_delivery, "brotli", None)
    result = http_delivery.choose_representation(source, source.name, tmp_path / "out")
    assert result.encoding == "gzip"
    assert gzip.decompress(result.body) == source.read_bytes()
    path = Path(__file__).parents[1] / "tools/deployment/prepare_deployment.py"
    spec = importlib.util.spec_from_file_location("delivery_prepare", path)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    profile = prepare.delivery_profile("best", True)
    assert profile["brotli"] is None
    warning = capsys.readouterr().out
    assert "Brotli unavailable" in warning and "gzip/identity" in warning
    assert ".[deployment]" in warning


def test_store_blob_media_type_comes_from_its_signature(tmp_path):
    path = Path(__file__).parents[1] / "tools/deployment/prepare_deployment.py"
    spec = importlib.util.spec_from_file_location("blob_type_prepare", path)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    for name, raw, expected in [("png", prepare.BLOB_TYPES[0][0] + b"IHDR", "image/png"),
                                ("ogg", b"OggS" + bytes(8), "audio/ogg"), ("json", b'{"a": 1}', "application/octet-stream")]:
        source = tmp_path / name
        source.write_bytes(raw)
        assert prepare.content_type(f"asset-store/blobs/ab/{name}.bin", source) == expected
    assert prepare.content_type("snapshots/a/assets/b.png", tmp_path / "json") == "image/png"


def test_fixed_origin_headers_use_encoded_length_without_requiring_vary(tmp_path):
    path = Path(__file__).parents[1] / "tools/deployment/upload_oss.py"
    spec = importlib.util.spec_from_file_location("delivery_uploader", path)
    uploader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uploader)
    source = tmp_path / "source.glb"
    source.write_bytes(b"glTF geometry " * 2000)
    result = http_delivery.choose_representation(source, source.name, tmp_path / "out", mode="gzip")
    row = {**result.manifest_fields, "contentType": "model/gltf-binary",
           "cacheControl": "public, max-age=31536000, immutable, no-transform"}
    headers = uploader.wanted_headers(row)
    assert headers["Content-Type"] == "model/gltf-binary"
    assert headers["Content-Encoding"] == "gzip"
    assert headers["Content-Length"] == str(len(result.body))
    assert headers["x-oss-meta-moly-decoded-bytes"] == str(source.stat().st_size)
    assert headers["x-oss-meta-moly-decoded-sha256"] == result.decoded_sha256
    actual = {key.lower(): value for key, value in headers.items()}
    assert "Vary" not in headers
    assert uploader.matching_head(SimpleNamespace(headers=actual), row) == []
    actual["vary"] = "Origin"
    assert uploader.matching_head(SimpleNamespace(headers=actual), row) == []
    assert "Content-Encoding" not in uploader.wanted_headers({**row, "contentEncoding": "identity"})


def test_prepare_deployment_stages_same_url_with_verified_encoded_body(tmp_path):
    import json
    publication, output = tmp_path / "publication", tmp_path / "output"
    root = publication / "snapshots/cn-test/assets"
    release = publication / "releases/stage-test"
    release.mkdir(parents=True)
    (root / "fixture-models").mkdir(parents=True)
    script = b"export const stage = true;\n"
    (release / "stage.mjs").write_bytes(script)
    (release / "integrity.json").write_text(json.dumps({"hashes": {"stage.mjs": hashlib.sha256(script).hexdigest()}}))
    (publication / "manifest.json").write_text(json.dumps({"release": {"id": "stage-test"},
        "snapshots": [{"id": "cn-test", "region": "cn", "version": "6.0.0"}]}))
    (publication / "cache-worker.mjs").write_text("// synthetic worker\n")
    (root / "mysekai-fixtures.json").write_text('{"region":"cn","gameVersion":"6.0.0"}')
    (root / "browser-base.json").write_text('{"files":[]}')
    for name in ("phenomena/audio/loop.json", "ui/audio/loop.json"):
        target = root / name
        target.parent.mkdir(parents=True)
        target.write_text('{"packages":[]}')
    document = b'{"asset":{"version":"2.0"}}'
    document += b" " * (-len(document) % 4)
    binary = b"\0" * 8192
    raw = b"glTF" + struct.pack("<IIII", 2, 28 + len(document) + len(binary), len(document), 0x4e4f534a)
    raw += document + struct.pack("<II", len(binary), 0x004e4942) + binary
    source = root / "fixture-models/model.glb"
    source.write_bytes(raw)
    Path(str(source) + ".gz").write_bytes(gzip.compress(b"stale", mtime=0))
    script_path = Path(__file__).parents[1] / "tools/deployment/prepare_deployment.py"
    result = subprocess.run([sys.executable, str(script_path), "--publication", str(publication),
        "--output", str(output), "--encoding", "gzip", "--stage"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "upload-manifest.json").read_text())
    row = next(row for row in manifest["files"] if row["path"].endswith("model.glb"))
    assert row["urlPath"] == "/moly/snapshots/cn-test/assets/fixture-models/model.glb"
    assert row["contentType"] == "model/gltf-binary" and row["contentEncoding"] == "gzip"
    assert row["deliveryMode"] == "fixed" and row["requiredAcceptEncoding"] == "gzip"
    assert "vary" not in row and "no-transform" in row["cacheControl"]
    encoded = Path(row["staging"]).read_bytes()
    assert gzip.decompress(encoded) == source.read_bytes() == raw
    assert row["bytes"] == len(encoded) and row["decodedBytes"] == len(raw)
    assert row["decodedSha256"] == hashlib.sha256(raw).hexdigest()
    assert len(manifest["invalidSidecarsSkipped"]) == 1
