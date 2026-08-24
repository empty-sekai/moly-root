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
                  master_cache=None, extract_out=None, vgmstream=None, ffmpeg=None):
        captured.update(manifest_path=manifest_path, out=out, asset_base_url=asset_base_url,
                        workers=workers, retries=retries, master=master,
                        master_cache=master_cache, extract_out=extract_out,
                        vgmstream=vgmstream, ffmpeg=ffmpeg)
        return {"requiredBundles": 0, "downloads": 0, "extraction": {"summary": {"failed": 0}}}

    # The stand-in must accept exactly what the real entry point accepts.
    inspect.signature(fetch.pull).bind("manifest.json", "out", ASSET_BASE_URL, 8, 4, "master-dir")
    monkeypatch.setattr(fetch, "pull", fake_pull)
    exit_code = cli.main(["pull", "--manifest", "manifest.json", "--out", "out",
                          "--asset-base-url", ASSET_BASE_URL, "--master", "master-dir",
                          "--json", "--vgmstream", "tools/vgmstream"])
    assert exit_code == 0
    # master 也必须被转发:身份与移动人格只在使用者自备的 master 表里,漏传就静默少产物。
    # The audio decoder is the same kind of promise: named on the command line and
    # dropped on the way through, the run reports skipped audio for no reason.
    assert captured == {"manifest_path": "manifest.json", "out": "out",
                        "asset_base_url": ASSET_BASE_URL, "workers": 8, "retries": 4,
                        "master": "master-dir", "master_cache": None,
                        "extract_out": None, "vgmstream": "tools/vgmstream",
                        "ffmpeg": None}
    assert json.loads(capsys.readouterr().out)["requiredBundles"] == 0


def test_fetch_command_line_requires_the_asset_base_url_flag():
    with pytest.raises(SystemExit):
        fetch.main(["--manifest", "manifest.json"])


# -- the sound packages a caller's master tables name ------------------------
#
# Every other domain is found by the shape of its bundle name, so the router
# alone decides the root set.  Sound is the one domain that cannot work that
# way: which packages hold the music and ambience of a world is stated by master
# rows, and no package declares them as a dependency.  Left to the name shape,
# a root list either takes every sound package in the manifest or none of them;
# taking none is what made `pull` finish with every picture and no sound.


def _audio_master(directory: Path) -> Path:
    """Synthetic audio tables, in the shapes the game's own tables have."""
    directory.mkdir(parents=True, exist_ok=True)
    tables = {
        # A music row names the leaf of the package its own archive is in.
        "mysekaiPhenomenaBgms": [
            {"id": 1, "mysekaiPhenomenaId": 1, "assetbundleName": "bgm_probe_weather",
             "cue": "bgm_probe_weather"}],
        # The layer under it: one row per site and brightness, several rows
        # naming the same package.
        "mysekaiSiteBgms": [
            {"id": 1, "mysekaiSiteId": 1, "mysekaiPhenomenaBrightnessType": "normal",
             "assetbundleName": "bgm_probe_site", "cue": "bgm_probe_site_1"},
            {"id": 2, "mysekaiSiteId": 1, "mysekaiPhenomenaBrightnessType": "dark",
             "assetbundleName": "bgm_probe_site", "cue": "bgm_probe_site_2"}],
        # An ambience row names a cue and never a package.
        "mysekaiSiteMysekaiPhenomenaSounds": [
            {"id": 1, "mysekaiSiteId": 5, "cue": "se_probe_wind",
             "mysekaiPhenomenaSoundConditionType": "other"}],
    }
    for name, rows in tables.items():
        (directory / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")
    return directory


AUDIO_MANIFEST_NAMES = (
    "mysekai/effect/site/environment/001_sunny/global",
    "mysekai/sound/bgm/bgm_probe_weather",
    "mysekai/sound/bgm/bgm_probe_site",
    "mysekai/sound/se/se_mysekai",
    # A sound package no row names: in the manifest, never a root.
    "mysekai/sound/bgm/music0001",
)

AUDIO_ROOT_NAMES = ["mysekai__sound__bgm__bgm_probe_site",
                    "mysekai__sound__bgm__bgm_probe_weather",
                    "mysekai__sound__se__se_mysekai"]


def _manifest_document(names=AUDIO_MANIFEST_NAMES) -> dict:
    return {name: {"bundleName": name, "downloadPath": "b",
                   "cacheFileName": name.rsplit("/", 1)[-1], "dependencies": []}
            for name in names}


def _audio_manifest(names=AUDIO_MANIFEST_NAMES) -> Manifest:
    return Manifest(_manifest_document(names))


def test_without_master_tables_no_sound_package_is_a_root_and_the_run_says_why():
    """No tables means nothing names a sound package, and that must be visible."""
    names, report = fetch.sound_roots(None)
    assert names == []
    assert report["status"] == "skipped" and "master" in report["error"]
    assert [name for name in _audio_manifest().roots() if "/sound/" in name] == []


def test_master_tables_name_the_sound_packages_and_they_become_roots(tmp_path):
    names, report = fetch.sound_roots(str(_audio_master(tmp_path / "master")))
    assert names == AUDIO_ROOT_NAMES
    assert report["status"] == "succeeded" and report["absentTables"] == []
    # Two site rows naming one package are one package, not two.
    assert report["tables"] == {"mysekaiPhenomenaBgms": 1, "mysekaiSiteBgms": 1,
                                "mysekaiSiteMysekaiPhenomenaSounds": 1}
    roots = _audio_manifest().roots(audio=names)
    assert "mysekai/sound/bgm/bgm_probe_weather" in roots
    assert "mysekai/sound/bgm/bgm_probe_site" in roots
    assert "mysekai/sound/se/se_mysekai" in roots
    # The rows decide, not the name shape: a sound package no row names stays out.
    assert "mysekai/sound/bgm/music0001" not in roots
    assert "mysekai/effect/site/environment/001_sunny/global" in roots


def test_an_absent_audio_table_is_named_and_the_others_still_name_their_packages(tmp_path):
    master = _audio_master(tmp_path / "master")
    (master / "mysekaiSiteBgms.json").unlink()
    names, report = fetch.sound_roots(str(master))
    assert names == ["mysekai__sound__bgm__bgm_probe_weather",
                     "mysekai__sound__se__se_mysekai"]
    assert report["absentTables"] == ["mysekaiSiteBgms"]
    assert report["status"] == "succeeded"


def test_a_named_sound_package_the_manifest_does_not_carry_is_reported_not_requested():
    """A row may name a package this manifest has no entry for.

    Asked for as a root it would abort the whole download with a missing
    dependency; dropped silently it would look as if the row never existed.
    """
    manifest = _audio_manifest([name for name in AUDIO_MANIFEST_NAMES
                                if not name.endswith("se_mysekai")])
    present, absent = manifest.sound_entries(AUDIO_ROOT_NAMES)
    assert present == ["mysekai/sound/bgm/bgm_probe_site",
                       "mysekai/sound/bgm/bgm_probe_weather"]
    assert absent == ["mysekai__sound__se__se_mysekai"]
    assert manifest.required_bundles(manifest.roots(audio=AUDIO_ROOT_NAMES))


def _fake_network(monkeypatch, payload=b"UnityFS" + bytes(range(16))):
    """Serve every download from memory; nothing leaves this process."""
    urls = []

    def fake_urlopen(request, timeout=None):
        urls.append(request.full_url)
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return urls


def _fake_pull(tmp_path, monkeypatch, **kwargs):
    _fake_network(monkeypatch)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest_document()), encoding="utf-8")
    report = fetch.pull(str(manifest), tmp_path / "work", ASSET_BASE_URL, workers=1,
                        extract_out=tmp_path / "out", **kwargs)
    downloaded = {row["bundleName"] for row in json.loads(
        (tmp_path / "work" / "downloads.json").read_text(encoding="utf-8"))}
    return report, downloaded


def test_pull_without_master_downloads_no_sound_package_and_says_why(tmp_path,
                                                                     monkeypatch):
    report, downloaded = _fake_pull(tmp_path, monkeypatch)
    assert report["audio"]["status"] == "skipped"
    assert report["audio"]["roots"] == []
    assert "master" in report["audio"]["error"]
    assert [name for name in downloaded if "/sound/" in name] == []


def test_pull_with_master_downloads_exactly_the_sound_packages_named(tmp_path,
                                                                     monkeypatch):
    report, downloaded = _fake_pull(tmp_path, monkeypatch,
                                    master=str(_audio_master(tmp_path / "master")))
    assert report["audio"]["status"] == "succeeded"
    assert report["audio"]["roots"] == ["mysekai/sound/bgm/bgm_probe_site",
                                        "mysekai/sound/bgm/bgm_probe_weather",
                                        "mysekai/sound/se/se_mysekai"]
    assert report["audio"]["notInManifest"] == []
    assert downloaded == {"mysekai/effect/site/environment/001_sunny/global",
                          *report["audio"]["roots"]}


def test_the_pull_audio_report_names_packages_and_tables_only(tmp_path, monkeypatch):
    """This section is printed and pasted around, so it must carry no local path.

    Red condition: naming the master directory or the workspace here (as the loop
    sidecar once named the decoder's location) leaks one machine's layout into
    something meant to be shared.
    """
    report, _ = _fake_pull(tmp_path, monkeypatch,
                           master=str(_audio_master(tmp_path / "master")))
    assert set(report["audio"]) == {"status", "roots", "error", "tables",
                                    "absentTables", "notInManifest"}
    assert str(tmp_path) not in json.dumps(report["audio"], ensure_ascii=False)


def test_the_command_line_says_why_no_sound_package_was_pulled(monkeypatch, capsys):
    """Without --json the caller sees a summary, and that is where audio goes.

    The gap this closes was silent on exactly this path: the run said it had
    downloaded and extracted everything, and the audio was simply not there.
    """
    from core import cli

    def fake_pull(*args, **kwargs):
        names, audio = fetch.sound_roots(None)
        return {"requiredBundles": 1, "downloads": 1, "audio": audio,
                "extraction": {"summary": {"requested": 1, "succeeded": 1,
                                           "failed": 0, "unsupported": 0},
                               "bundles": [], "report": "r"}}

    monkeypatch.setattr(fetch, "pull", fake_pull)
    assert cli.main(["pull", "--manifest", "m.json",
                     "--asset-base-url", ASSET_BASE_URL]) == 0
    printed = capsys.readouterr().out
    assert "no sound package pulled" in printed and "master" in printed
