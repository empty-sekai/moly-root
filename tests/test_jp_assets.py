import json
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core import jp


def descriptor(tmp_path, bundles):
    path = tmp_path / "jp.json"
    path.write_text(json.dumps({
        "version": "asset-v1",
        "source": {"region": "jp", "assetVersion": "6.8.0.40",
                   "assetHash": "abc123", "appVersion": "6.8.1"},
        "assetBaseUrl": "https://production-deadbeef-assetbundle.sekai.colorfulpalette.org/6.8.0.40/abc123/android",
        "bundles": bundles,
    }), encoding="utf-8")
    return path


def test_offline_plan_is_dependency_closed_and_size_accounted(tmp_path):
    path = descriptor(tmp_path, {
        "mysekai/fixture/mdl_probe": {"fileSize": 7, "dependencies": ["mysekai/shader"]},
        "mysekai/shader": {"fileSize": 11, "dependencies": []},
        "music/long_mix": {"fileSize": 999, "dependencies": []},
    })
    report = jp.plan(path)
    assert report["roots"] == ["mysekai/fixture/mdl_probe", "mysekai/shader"]
    assert report["bundles"] == ["mysekai/shader", "mysekai/fixture/mdl_probe"]
    assert report["summary"] == {"roots": 2, "dependencyClosedBundles": 2,
                                  "builtinRequirements": 0, "declaredBytes": 18}


def test_family_filter_keeps_dependencies_but_drops_other_roots(tmp_path):
    path = descriptor(tmp_path, {
        "mysekai/fixture/mdl_probe": {"fileSize": 7, "dependencies": ["mysekai/shader"]},
        "mysekai/shader": {"fileSize": 11, "dependencies": []},
        "mysekai/site/scene/home": {"fileSize": 13, "dependencies": []},
    })
    report = jp.plan(path, families=["fixture-interface"])
    assert report["roots"] == ["mysekai/fixture/mdl_probe"]
    assert report["bundles"] == ["mysekai/shader", "mysekai/fixture/mdl_probe"]


@pytest.mark.parametrize("source", [{}, {"region": "cn", "assetVersion": "1", "assetHash": "x"}])
def test_descriptor_refuses_missing_or_cross_region_identity(tmp_path, source):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"source": source, "assetBaseUrl": "https://example.invalid", "bundles": {}}),
                    encoding="utf-8")
    with pytest.raises(jp.JPError):
        jp.load_descriptor(path)


def test_protocol_crypto_round_trip_and_strict_padding():
    payload = {"hello": "世界", "items": [1, 2, 3]}
    encrypted = jp._encrypt(payload)
    assert encrypted != json.dumps(payload).encode()
    assert jp._decrypt(encrypted) == payload
    with pytest.raises(jp.JPError, match="padding"):
        jp._decrypt(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))


def test_client_validation_happens_without_network():
    with pytest.raises(ValueError):
        jp.Client("../escape", "hash")
    with pytest.raises(ValueError):
        jp.Client("6.8.1", "hash", retries=0)


def test_fetch_manifest_writes_no_descriptor_when_version_is_stale(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.profile = "production"; self.host_hash = "abcd"; self.platform = "android"
        def issue_signature(self): pass
        def game_version(self): return {"profile": "production"}
        def system(self): return {"appVersions": [{"appVersion": "6.8.1", "appVersionStatus": "available"}]}
    monkeypatch.setattr(jp, "Client", FakeClient)
    monkeypatch.setattr(jp, "_bootstrap", lambda *a, **k: {
        "appVersion": "6.3.0", "appHash": "old", "assetVersion": "old", "assetHash": "old"})
    out = tmp_path / "manifest.json"
    with pytest.raises(jp.JPError, match="not available"):
        jp.fetch_manifest(out, app_version="6.3.0", app_hash="old",
                          session_file=tmp_path / "session.json")
    assert not out.exists()
    assert not (tmp_path / "session.json").exists()


def test_complete_mysekai_selection_includes_unrouted_voice_and_thumbnail(tmp_path):
    path = descriptor(tmp_path, {
        "mysekai/talk/voice/cue": {"fileSize": 5, "dependencies": []},
        "mysekai/thumbnail/fixture/icon": {"fileSize": 7, "dependencies": []},
        "unrelated/movie": {"fileSize": 1000, "dependencies": []},
    })
    report = jp.plan(path)
    assert report["roots"] == ["mysekai/talk/voice/cue", "mysekai/thumbnail/fixture/icon"]
    assert report["summary"]["declaredBytes"] == 12


def test_platform_is_an_explicit_protocol_choice():
    with pytest.raises(ValueError, match="platform"):
        jp.Client("6.8.1", "hash", platform="windows")


def test_default_plan_includes_runtime_scenario_partvoice_route(tmp_path):
    path = descriptor(tmp_path, {
        "sound/scenario/voice/part_voice_v2_21miku_light_sound": {"fileSize": 7, "dependencies": []},
        "sound/scenario/part_voice/21miku_light_sound": {"fileSize": 99, "dependencies": []},
    })
    assert jp.plan(path)["roots"] == ["sound/scenario/voice/part_voice_v2_21miku_light_sound"]


def test_pull_source_identity_does_not_depend_on_machine_directories(tmp_path, monkeypatch):
    path = descriptor(tmp_path, {"mysekai/fixture/probe": {"fileSize": 1, "dependencies": []}})
    document = json.loads(path.read_bytes())
    document["source"]["localDebug"] = {"directory": "/private-cache/source"}
    path.write_text(json.dumps(document), encoding="utf-8")
    class FakeClient:
        def __init__(self, *args, **kwargs): self.cookie = "synthetic-cookie"
        def issue_signature(self): pass
    monkeypatch.setattr(jp, "Client", FakeClient)
    monkeypatch.setattr("core.fetch.pull", lambda *args, **kwargs: {"downloads": 0})
    outputs = []
    for name in ("first-location", "different-location"):
        extracted = tmp_path / name / "extracted"
        jp.pull_descriptor(path, out=tmp_path / name / "workspace", extract_out=extracted)
        outputs.append((extracted / "source.json").read_bytes())
    assert outputs[0] == outputs[1]
    public = json.loads(outputs[0])
    assert public["source"]["assetHash"] == "abc123"
    assert public["descriptorSha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(public) == {"version", "kind", "source", "descriptorSha256"}
    assert "localDebug" not in public["source"]
    assert b"private-cache" not in outputs[0]
