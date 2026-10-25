"""Portable task execution, corruption boundaries and runtime text closure."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from core.content import canonical_bytes, content_hash
from pack.gc import catalog_garbage
from pipeline.depot import Depot
from pipeline.stages import catalog_task, official_task, runtime_task


def corpus(text="Hello"):
    return {"commonSomeCharacter": {"talks": [{"lua": "sample", "talkId": 1, "unitIds": [1, 2],
            "steps": [{"op": "label", "name": "Alpha"}, {"op": "voice", "who": 1, "cue": "one"},
                      {"op": "text", "text": text}, {"op": "wait_click"}]}]}}


def setup(tmp_path):
    source = tmp_path / "input.json"
    source.write_bytes(canonical_bytes(corpus()))
    task = catalog_task([source], region="jp", source_revision="sample-v1")
    return source, task, Depot(tmp_path / "store")


def test_relocated_inputs_and_outputs_share_a_verified_result(tmp_path):
    source, task, depot = setup(tmp_path)
    assert depot.plan(task)["status"] == "run"
    first = depot.run(task)
    relocated = tmp_path / "elsewhere.json"
    relocated.write_bytes(source.read_bytes())
    same = catalog_task([relocated], region="jp", source_revision="sample-v1")
    assert same["key"] == task["key"]
    assert depot.run(same)["status"] == "hit"
    fresh = Depot(tmp_path / "fresh")
    assert fresh.run(same)["result"] == first["result"]
    assert depot.run(same, force=True)["result"] == first["result"]
    depot.materialize(task["key"], tmp_path / "a")
    fresh.materialize(task["key"], tmp_path / "b")
    assert (tmp_path / "a/text-source.json").read_bytes() == (tmp_path / "b/text-source.json").read_bytes()
    assert str(tmp_path) not in canonical_bytes(first["result"]).decode()


def test_changed_inputs_invalidate_plan_and_stale_description_cannot_commit(tmp_path):
    source, task, depot = setup(tmp_path)
    source.write_bytes(canonical_bytes(corpus("Updated")))
    changed = catalog_task([source], region="jp", source_revision="sample-v1")
    assert task["key"] != changed["key"]
    assert "inputs-changed" in depot.plan(changed, previous=task)["reasons"]
    with pytest.raises(ValueError, match="content changed"):
        depot.run(task)
    assert not (depot.root / "tasks" / f"{task['key']}.json").exists()
    assert depot.run(changed)["status"] == "ran"


def test_corrupt_cache_is_neither_hit_nor_collectable(tmp_path):
    _, task, depot = setup(tmp_path)
    result = depot.run(task)["result"]
    blob = depot.root / "blobs" / result["artifacts"][0]["blob"]
    assert catalog_garbage(depot.root)["delete"] == []
    blob.write_bytes(b"broken")
    with pytest.raises(ValueError, match="corrupt"):
        depot.plan(task)
    with pytest.raises(ValueError, match="corrupt"):
        catalog_garbage(depot.root)


def test_task_hash_and_installed_reader_are_checked(tmp_path):
    _, task, depot = setup(tmp_path)
    changed = deepcopy(task)
    changed["params"]["region"] = "cn"
    with pytest.raises(ValueError, match="key"):
        depot.run(changed)
    from pipeline.contract import key_parts
    from core.content import document_hash
    changed = deepcopy(task)
    changed["atoms"]["implementation"] = "0" * 64
    changed["key"] = document_hash(key_parts(changed))
    with pytest.raises(ValueError, match="installation"):
        depot.run(changed)


def test_published_release_gc_retains_independent_task_artifacts(tmp_path):
    from pack.groups import build_groups, runtime_paths
    _, task, depot = setup(tmp_path)
    result = depot.run(task)["result"]
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "manifest.json").write_text('{"units": []}', encoding="utf-8")
    (runtime / "characters.json").write_text('{}', encoding="utf-8")
    (runtime / "text").mkdir()
    (runtime / "text/index.json").write_text('{}', encoding="utf-8")
    (runtime / "translations").mkdir()
    (runtime / "translations/latest.json").write_text('{}', encoding="utf-8")
    selected = {str(path).replace("\\", "/") for path in runtime_paths(runtime)}
    assert any(path.endswith("text/index.json") for path in selected)
    assert not any("translations" in path for path in selected)
    build_groups(runtime, depot.root, "synthetic", region="jp")
    garbage = catalog_garbage(depot.root)
    assert garbage["delete"] == []
    assert f"tasks/{task['key']}.json" in garbage["roots"]
    assert result["artifacts"][0]["blob"] in garbage["referenced_blobs"]


def test_runtime_only_loads_content_closure_and_matching_official_text(tmp_path):
    source, jp_task, depot = setup(tmp_path)
    depot.run(jp_task)
    depot.materialize(jp_task["key"], tmp_path / "jp")
    source.write_bytes(canonical_bytes(corpus("你好")))
    cn_task = catalog_task([source], region="cn", source_revision="cn-v1")
    depot.run(cn_task)
    depot.materialize(cn_task["key"], tmp_path / "cn")
    jp, cn = tmp_path / "jp/text-source.json", tmp_path / "cn/text-source.json"
    official = official_task(jp, cn, locale="zh-CN")
    depot.run(official)
    depot.materialize(official["key"], tmp_path / "official")
    runtime = runtime_task(jp, [tmp_path / "official/official-zh-CN.json"])
    depot.run(runtime)
    out = tmp_path / "runtime"
    depot.materialize(runtime["key"], out)

    def read(ref):
        data = (out / ref["path"]).read_bytes()
        assert content_hash(data) == ref["sha256"] and len(data) == ref["bytes"]
        return json.loads(data)

    index = json.loads((out / "text/index.json").read_bytes())
    bucket = content_hash(b"sample")[:2]
    route = read({**index["buckets"][bucket], "path": "text/" + index["buckets"][bucket]["path"]})["scripts"]["sample"]
    script = read({**route["source"], "path": "text/" + route["source"]["path"]})
    base = "translations/jp/zh-CN/"
    publication = json.loads((out / base / "latest.json").read_bytes())
    translation_route = read({**publication["buckets"][bucket],
                             "path": base + publication["buckets"][bucket]["path"]})["scripts"]["sample"]
    translated = read({**translation_route, "path": base + translation_route["path"]})
    assert [entry["text"] for entry in translated["entries"]] == ["你好"]
    assert translated["sourceRevision"] == script["revision"]
    assert all(set(line) == {"id", "kind", "step", "text", "sourceHash"}
               for row in script["scripts"] for line in row["lines"])
    assert all("steps" not in row for row in script["scripts"])

    # Translation publication changes have no path back into a resource
    # snapshot, its source index, or any unchanged source shard.
    from pipeline.text_product import build_product
    source_doc = json.loads(jp.read_bytes())
    overlay_doc = json.loads((tmp_path / "official/official-zh-CN.json").read_bytes())
    before = build_product(source_doc, [overlay_doc])
    overlay_doc["entries"][0]["text"] = "您好"
    after = build_product(source_doc, [overlay_doc])
    assert {p: b for p, b in before.items() if p.startswith("text/")} == {
        p: b for p, b in after.items() if p.startswith("text/")}
    assert before[base + "latest.json"] != after[base + "latest.json"]
