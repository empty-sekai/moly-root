"""Cache source identity, reproducible snapshots, and input provenance."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core import atomic
from core.master import Master, MissingTable, record_master_inputs


def reply(label):
    return {"body": json.dumps([{"source": label}]).encode()}


def test_two_http_sources_never_share_a_cached_table(http_source, tmp_path):
    base, replies, requests = http_source
    replies.extend([reply("A"), reply("B")])
    a = Master(base + "/a/", cache_dir=tmp_path)
    b = Master(base + "/b", cache_dir=tmp_path)
    assert a.table("probe") == [{"source": "A"}]
    assert b.table("probe") == [{"source": "B"}]
    assert [row["path"] for row in requests] == ["/a/probe.json", "/b/probe.json"]
    cached_a = Master(base + "/a", cache_dir=tmp_path)
    assert cached_a.table("probe") == [{"source": "A"}]
    assert not cached_a.fetched
    assert cached_a.source_id == a.source_id != b.source_id
    assert cached_a.provenance["probe"]["status"] == "cached"


def test_legacy_cache_is_not_assumed_to_belong_to_the_new_source(http_source, tmp_path):
    base, replies, _ = http_source
    (tmp_path / "probe.json").write_bytes(reply("legacy")["body"])
    replies.append(reply("correct"))
    assert Master(base, cache_dir=tmp_path).table("probe") == [{"source": "correct"}]


def test_snapshot_namespaces_are_frozen_until_explicitly_refreshed(http_source, tmp_path):
    base, replies, requests = http_source
    replies.extend([reply("v1"), reply("v2"), reply("refreshed")])
    assert Master(base, tmp_path, snapshot="one").table("probe") == [{"source": "v1"}]
    assert Master(base, tmp_path, snapshot="one").table("probe") == [{"source": "v1"}]
    assert Master(base, tmp_path, snapshot="two").table("probe") == [{"source": "v2"}]
    assert Master(base, tmp_path, snapshot="one", refresh=True).table("probe") == [{"source": "refreshed"}]
    assert len(requests) == 3


def test_cache_corruption_fails_closed_and_refresh_repairs_it(http_source, tmp_path):
    base, replies, _ = http_source
    replies.append(reply("correct"))
    source = Master(base, tmp_path)
    source.table("probe")
    cached = tmp_path / source.source_id / "probe.json"
    envelope = json.loads(cached.read_bytes())
    envelope["payload"] = "[]"
    cached.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid master cache"):
        Master(base, tmp_path).table("probe")
    replies.append(reply("repaired"))
    assert Master(base, tmp_path, refresh=True).table("probe") == [{"source": "repaired"}]


def test_interrupted_cache_replacement_keeps_the_previous_snapshot(http_source, tmp_path, monkeypatch):
    base, replies, _ = http_source
    replies.extend([reply("v1"), reply("v2")])
    source = Master(base, tmp_path)
    source.table("probe")
    cached = tmp_path / source.source_id / "probe.json"
    original = cached.read_bytes()
    replace = atomic.os.replace

    def fail(source, destination):
        if Path(destination) == cached:
            raise OSError("injected cache publication failure")
        return replace(source, destination)

    monkeypatch.setattr(atomic.os, "replace", fail)
    with pytest.raises(OSError, match="injected"):
        Master(base, tmp_path, refresh=True).table("probe")
    assert cached.read_bytes() == original
    assert Master(base, tmp_path).table("probe") == [{"source": "v1"}]


def test_query_version_stays_after_the_table_path(http_source, tmp_path):
    base, replies, requests = http_source
    replies.append(reply("v1"))
    Master(base + "/master?version=v1", tmp_path).table("probe")
    assert requests[0]["path"] == "/master/probe.json?version=v1"


def test_extraction_report_records_read_bytes_and_missing_inputs(tmp_path):
    payload = b'[{"id": 1}]\n'
    (tmp_path / "present.json").write_bytes(payload)
    master = Master(tmp_path, snapshot="example-version")
    report_path = tmp_path / "report.json"

    @record_master_inputs
    def extract():
        master.table("present")
        with pytest.raises(MissingTable):
            master.table("missing")
        return {"report": str(report_path)}

    report = extract()
    rows = {row["table"]: row for row in report["masterInputs"]}
    assert rows["present"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert rows["present"]["snapshot"] == "example-version"
    assert rows["missing"]["status"] == "missing"
    assert json.loads(report_path.read_bytes())["masterInputs"] == report["masterInputs"]
