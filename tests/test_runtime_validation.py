import json

from core.runtime_validation import validate_runtime


def test_validation_reports_missing_audio_as_source_gap_and_missing_file_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("core.runtime_validation.RUNTIME_ENTRYPOINTS", ())
    values = {
        "fixture-models/index.json": {"packages": {}},
        "mysekai-fixtures.json": {"region": "jp", "fixtures": []},
        "actor-animations/index.json": {"libraries": [], "clipBindings": []},
        "phenomena/audio/loop.json": {"packages": []},
        "talks.json": {"units": {"1": {"talks": [{"voices": ["source_cue"]}]}}},
        "fixture-talks/out/fixture-talks.json": {"talks": []},
    }
    for name, value in values.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    report = validate_runtime(tmp_path)
    assert report["status"] == "ready-with-source-gaps"
    assert report["audio"]["missingCues"] == ["source_cue"]
    assert report["missingResources"] == []
    (tmp_path / "actor-animations/index.json").write_text(json.dumps({"libraries": [{
        "glb": "actor-animations/missing.glb", "index": "actor-animations/missing.index.json"}]}))
    report = validate_runtime(tmp_path)
    assert report["status"] == "failed"
    assert len(report["missingResources"]) == 2
