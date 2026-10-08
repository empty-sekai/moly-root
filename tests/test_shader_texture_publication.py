"""The runtime publisher must keep texture descriptors and payloads together."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from pack.groups import build_groups, runtime_paths
from pack.shader_textures import validate_shader_texture_payloads
from pack.verify import verify_catalog


@pytest.fixture(params=["phenomena", "fixture-particles-v2"])
def texture_source(tmp_path, request):
    root = tmp_path / "source"
    asset_root = request.param
    directory = root / asset_root / "shader-textures"
    directory.mkdir(parents=True)
    document = {"schemaVersion": 1, "status": "decoded"}
    for field, suffix, raw in [("encoded", ".texture.bin", b"source pixels"),
                               ("pixels", ".rgba8", bytes(range(16)))]:
        digest = hashlib.sha256(raw).hexdigest()
        name = digest + suffix
        (directory / name).write_bytes(raw)
        document[field] = {"file": "shader-textures/" + name, "bytes": len(raw), "sha256": digest}
    descriptor = directory / ("a" * 64 + ".texture.json")
    descriptor.write_text(json.dumps(document), encoding="utf-8")
    return root, descriptor, document, asset_root


@pytest.mark.parametrize("region", ["cn", "jp"])
def test_both_region_publishers_include_and_verify_decoded_payload(texture_source, tmp_path, region):
    root, _, document, asset_root = texture_source
    members = runtime_paths(root)
    for field in ("encoded", "pixels"):
        assert asset_root + "/" + document[field]["file"] in members
    report = validate_shader_texture_payloads(root, members)
    assert report == {"descriptors": 1, "references": 2, "payloads": 2}
    out = tmp_path / "store"
    catalog = build_groups(root, out, "test", region=region)
    assert {name for package in catalog["packages"] for name in package["paths"]} == set(members)
    assert not verify_catalog(out / "asset-packs.json")[0]


@pytest.mark.parametrize("field", ["encoded", "pixels"])
def test_missing_payload_fails_before_publication(texture_source, field):
    root, _, document, asset_root = texture_source
    (root / asset_root / document[field]["file"]).unlink()
    with pytest.raises(ValueError, match="missing"):
        runtime_paths(root)


@pytest.mark.parametrize("damage", ["bytes", "sha256", "same-size-content", "traversal", "absent-reference"])
def test_invalid_payload_or_descriptor_fails_closed(texture_source, damage):
    root, descriptor, document, asset_root = texture_source
    if damage == "same-size-content":
        (root / asset_root / document["pixels"]["file"]).write_bytes(b"x" * 16)
    elif damage == "traversal":
        document["pixels"]["file"] = "shader-textures/../../escape.rgba8"
    elif damage == "absent-reference":
        del document["pixels"]
    else:
        document["pixels"][damage] = 17 if damage == "bytes" else "b" * 64
    descriptor.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="shader texture"):
        runtime_paths(root)


def test_membership_omission_fails_even_when_source_file_exists(texture_source):
    root, _, document, asset_root = texture_source
    members = runtime_paths(root)
    del members[asset_root + "/" + document["pixels"]["file"]]
    with pytest.raises(ValueError, match="omitted from runtime"):
        validate_shader_texture_payloads(root, members)


def test_explicit_pack_selection_cannot_drop_descriptor_payload(texture_source, tmp_path):
    root, _, document, asset_root = texture_source
    members = runtime_paths(root)
    del members[asset_root + "/" + document["pixels"]["file"]]
    with pytest.raises(ValueError, match="omitted from runtime"):
        build_groups(root, tmp_path / "store", "incomplete", region="cn", paths=members)


def test_unavailable_decode_can_keep_source_diagnostic(texture_source):
    root, descriptor, document, asset_root = texture_source
    (root / asset_root / document["pixels"]["file"]).unlink()
    document.pop("pixels")
    document.update(status="unavailable", error="unsupported source format")
    descriptor.write_text(json.dumps(document), encoding="utf-8")
    assert validate_shader_texture_payloads(root, runtime_paths(root)) == {
        "descriptors": 1, "references": 1, "payloads": 1,
    }
