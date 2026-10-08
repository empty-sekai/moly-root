import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fixtures.coverage import build_coverage, write_coverage


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_definitions_models_thumbnails_and_runtime_are_counted_separately(tmp_path):
    put(tmp_path / "mysekai-fixtures.json", {"region": "cn", "fixtures": [
        {"id": 1, "fixtureType": "normal", "assetbundleName": "mdl_ok"},
        {"id": 2, "fixtureType": "surface_appearance", "assetbundleName": "wall"},
        {"id": 3, "fixtureType": "custom", "assetbundleName": "mdl_empty"},
    ]})
    put(tmp_path / "fixture-models/index.json", {"packages": {
        "mysekai__fixture__mdl_ok": {"status": "exported", "glb": "ok.glb", "vertexCount": 3,
                                      "meshCount": 1, "variants": [{"rootName": "ok"}],
                                      "animations": {"clipCount": 1, "gltfChannels": 2, "unresolvedSlots": 0}},
        "mysekai__fixture__mdl_empty": {"status": "no-mesh", "vertexCount": 0},
    }})
    put(tmp_path / "fixture-thumbnails/fixture-thumbnails.json", {"fixtures": [
        {"fixtureId": 1, "variants": [{"textureId": 1, "image": "one.png"}]}
    ]})
    for name in ("areas.json", "attach-points.json"):
        put(tmp_path / "fixture-interface" / name, {"packages": {"mysekai__fixture__mdl_ok": {}}})
    put(tmp_path / "fixture-particles/index.json", {"packages": {"mysekai__fixture__mdl_ok": {}}})
    report = build_coverage(tmp_path)
    assert report["summary"]["definitions"] == 3
    assert report["summary"]["models"] == {"available": 1, "missing": 1, "unsupported": 1, "failed": 0}
    assert report["summary"]["thumbnails"] == {"available": 1, "missing": 2}
    assert report["summary"]["runtime"] == {"available": 1, "missing": 1, "unsupported": 1, "failed": 0}
    assert report["fixtures"][1]["model"]["reason"] == "no extracted package with the master assetbundleName"
    assert report["fixtures"][2]["runtime"]["status"] == "unsupported"


def test_write_is_atomic_public_json(tmp_path):
    put(tmp_path / "mysekai-fixtures.json", {"fixtures": []})
    path, report = write_coverage(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == report
