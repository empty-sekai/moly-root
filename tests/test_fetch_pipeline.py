import hashlib
import inspect
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core import fetch
from core.fetch import BundleEntry, Manifest, build_download_url, decrypt_bundle

ASSET_BASE_URL = "https://example.invalid/AssetBundle/1.0.0/Release/example_online"
USER_AGENT = "UnityPlayer/2022.3.62f3"


def test_decrypt_bundle_changes_wrapped_payload_to_unityfs():
    plain = b"UnityFS" + bytes(range(128))
    encrypted = bytearray(b"\x10\x00\x00\x00" + plain)
    for start in range(0, 128, 8):
        for index in range(start + 4, min(start + 9, len(encrypted))):
            encrypted[index] = (~encrypted[index]) & 0xFF
    assert decrypt_bundle(bytes(encrypted)) == plain


def test_manifest_resolves_asset_bundle_dependencies_and_url_fields():
    manifest = Manifest({
        "root": {"bundleName": "root", "downloadPath": "bundles", "cacheFileName": "root.bin", "dependencies": ["dep"]},
        "dep": {"bundleName": "dep", "downloadPath": "bundles", "cacheFileName": "dep.bin", "dependencies": []},
    })
    assert [entry.bundle_name for entry in manifest.required_bundles(["root"])] == ["dep", "root"]
    entry = manifest.entries["dep"]
    entry_url = build_download_url("https://example.invalid/AssetBundle/1.0.0/Release/example_online/", entry)
    assert entry_url == "https://example.invalid/AssetBundle/1.0.0/Release/example_online/bundles/dep"


def test_decrypt_bundle_rejects_unwrapped_non_unity_payload():
    with pytest.raises(ValueError, match="unknown bundle header"):
        decrypt_bundle(b"not-a-bundle")


def test_offline_encrypted_sample_decrypts_byte_for_byte():
    sample = os.environ.get("MOLY_ENCRYPTED_SAMPLE")
    if not sample:
        pytest.skip("MOLY_ENCRYPTED_SAMPLE is not configured")
    path = Path(sample)
    if not path.is_file():
        pytest.skip("MOLY_ENCRYPTED_SAMPLE does not point to a file")
    decrypted = decrypt_bundle(path.read_bytes())
    assert decrypted.startswith(b"UnityFS")
    assert hashlib.sha256(decrypted).hexdigest() == "8b2c623972e3e9dd6dbe07ab2ed9074adc454e6c88f76f0cbbc9331db6333b1c"


def entry_for(**fields) -> BundleEntry:
    return BundleEntry.from_dict(fields)


class FakeResponse:
    status = 200

    def __init__(self, payload: bytes):
        self.stream = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def test_download_url_joins_only_the_prefix_download_path_and_bundle_name():
    entry = entry_for(bundleName="mysekai/character/mdl_sd_101_001",
                      downloadPath="c3f0/2024", cacheFileName="unused-cache-name.bundle")
    assert build_download_url(ASSET_BASE_URL, entry) == \
        f"{ASSET_BASE_URL}/c3f0/2024/mysekai/character/mdl_sd_101_001"


def test_download_url_keeps_the_prefix_opaque_and_injects_no_segments():
    entry = entry_for(bundleName="b", downloadPath="p")
    assert build_download_url("https://mirror.example.invalid/one/two", entry) == \
        "https://mirror.example.invalid/one/two/p/b"


def test_download_url_omits_an_empty_download_path():
    assert build_download_url(ASSET_BASE_URL, entry_for(bundleName="b")) == f"{ASSET_BASE_URL}/b"


def test_download_url_requires_an_asset_base_url():
    entry = entry_for(bundleName="b", downloadPath="p")
    for missing in ("", "   "):
        with pytest.raises(ValueError, match="asset base URL"):
            build_download_url(missing, entry)


def test_pull_requires_an_asset_base_url_argument(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(TypeError):
        fetch.pull(str(manifest))
    with pytest.raises(TypeError):
        fetch.pull(str(manifest), tmp_path / "out")


def test_pull_rejects_an_empty_asset_base_url_before_touching_the_output(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="asset base URL"):
        fetch.pull(str(manifest), tmp_path / "out", "")
    assert not (tmp_path / "out").exists()


def test_fetch_carries_no_built_in_endpoint():
    assert not hasattr(fetch, "DEFAULT_BASE_URL")
    embedded = sorted(name for name, value in vars(fetch).items()
                      if isinstance(value, str) and not name.startswith("__") and "://" in value)
    assert not embedded
    assert "://" not in Path(fetch.__file__).read_text(encoding="utf-8")


def test_download_one_uses_the_manifest_url_and_unity_player_user_agent(tmp_path, monkeypatch):
    entry = entry_for(bundleName="mysekai/character/mdl_sd_101_001",
                      downloadPath="c3f0/2024", cacheFileName="unused-cache-name.bundle")
    payload = b"UnityFS" + bytes(range(8))
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["user_agent"] = request.get_header("User-agent")
        seen["range"] = request.get_header("Range")
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    report = fetch.download_one(entry, ASSET_BASE_URL, tmp_path, retries=1)
    assert seen["url"] == f"{ASSET_BASE_URL}/c3f0/2024/mysekai/character/mdl_sd_101_001"
    assert seen["user_agent"] == USER_AGENT
    assert seen["range"] is None
    assert report["url"] == seen["url"]
    assert (tmp_path / "mysekai__character__mdl_sd_101_001").read_bytes() == payload


def test_pull_command_line_requires_the_asset_base_url_flag():
    from core import cli
    with pytest.raises(SystemExit) as raised:
        cli.main(["pull", "--manifest", "manifest.json"])
    assert raised.value.code == 2


@pytest.mark.parametrize("removed", ["--base-url", "--ab-version", "--online-path"])
def test_pull_command_line_rejects_removed_endpoint_flags(removed):
    from core import cli
    with pytest.raises(SystemExit):
        cli.main(["pull", "--manifest", "manifest.json", "--asset-base-url", ASSET_BASE_URL, removed, "value"])


def test_pull_command_line_forwards_the_asset_base_url(monkeypatch, capsys):
    from core import cli
    captured: dict[str, object] = {}

    def fake_pull(manifest_path, out, asset_base_url, workers=8, retries=4, master=None,
                  master_cache=None, extract_out=None):
        captured.update(manifest_path=manifest_path, out=out, asset_base_url=asset_base_url,
                        workers=workers, retries=retries, master=master,
                        master_cache=master_cache, extract_out=extract_out)
        return {"requiredBundles": 0, "downloads": 0, "extraction": {"summary": {"failed": 0}}}

    # The stand-in must accept exactly what the real entry point accepts.
    inspect.signature(fetch.pull).bind("manifest.json", "out", ASSET_BASE_URL, 8, 4, "master-dir")
    monkeypatch.setattr(fetch, "pull", fake_pull)
    exit_code = cli.main(["pull", "--manifest", "manifest.json", "--out", "out",
                          "--asset-base-url", ASSET_BASE_URL, "--master", "master-dir",
                          "--json"])
    assert exit_code == 0
    # master 也必须被转发:身份与移动人格只在使用者自备的 master 表里,漏传就静默少产物。
    assert captured == {"manifest_path": "manifest.json", "out": "out",
                        "asset_base_url": ASSET_BASE_URL, "workers": 8, "retries": 4,
                        "master": "master-dir", "master_cache": None, "extract_out": None}
    assert json.loads(capsys.readouterr().out)["requiredBundles"] == 0


def test_fetch_command_line_requires_the_asset_base_url_flag():
    with pytest.raises(SystemExit):
        fetch.main(["--manifest", "manifest.json"])
