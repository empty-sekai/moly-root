"""Where master tables come from: a local directory or a base URL.

The tables are the caller's input either way — nothing is bundled here.  With a
URL each table is fetched by appending ``<table>.json``; a cache directory turns
the second run into a local read.  A table that cannot be retrieved must raise
with its own name in the message, never come back empty.
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import master as master_mod
from core.master import DEFAULT_MASTER_URL, Master, MissingTable

ROWS = [{"id": 1, "gameCharacterId": 1, "unit": "light_sound"}]
CLIENT_CONFIG_ROWS = [
    {"id": 77, "type": "Float", "value": "2.5"},
    {"id": 78, "type": "Float", "value": "2.5"},
    {"id": 95, "type": "Float", "value": "1.75"},
    {"id": 101, "type": "Int", "value": "12"},
    {"id": 102, "type": "String", "value": "hello"},
    {"id": 103, "type": "Bool", "value": "true"},
]


def _fake_urlopen(recorder, payload=None, fail=False):
    class _Response:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(url, timeout=None):
        recorder.append((url, timeout))
        if fail:
            raise OSError("HTTP Error 404: Not Found")
        return _Response(json.dumps(payload if payload is not None else ROWS).encode("utf-8"))

    return urlopen


def test_client_configs_parse_declared_value_types(tmp_path):
    with io.open(tmp_path / "clientConfigs.json", "w", encoding="utf-8", newline="\n") as h:
        json.dump(CLIENT_CONFIG_ROWS, h)
    assert Master(str(tmp_path)).client_configs() == {
        77: 2.5, 78: 2.5, 95: 1.75, 101: 12, 102: "hello", 103: True,
    }


def test_a_local_directory_is_read_as_files(tmp_path):
    with io.open(tmp_path / "gameCharacterUnits.json", "w", encoding="utf-8", newline="\n") as h:
        json.dump(ROWS, h)
    source = Master(str(tmp_path))
    assert source.remote is False
    assert source.table("gameCharacterUnits") == ROWS


def test_a_base_url_gets_the_table_name_appended(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(master_mod.urllib.request, "urlopen", _fake_urlopen(calls))
    source = Master("https://example.invalid/master/", cache_dir=str(tmp_path))
    assert source.remote is True
    assert source.table("gameCharacterUnits") == ROWS
    # 末尾斜杠不该产生双斜杠
    assert calls == [("https://example.invalid/master/gameCharacterUnits.json", 30.0)]


def test_a_fetched_table_is_cached_and_not_fetched_twice(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(master_mod.urllib.request, "urlopen", _fake_urlopen(calls))
    Master("https://example.invalid/m", cache_dir=str(tmp_path)).table("gameCharacterUnits")
    assert (tmp_path / "gameCharacterUnits.json").is_file()
    # 新实例走缓存,不再发请求
    second = Master("https://example.invalid/m", cache_dir=str(tmp_path))
    assert second.table("gameCharacterUnits") == ROWS
    assert second.fetched == [] and len(calls) == 1


def test_an_unreachable_table_raises_with_its_name(tmp_path, monkeypatch):
    monkeypatch.setattr(master_mod.urllib.request, "urlopen", _fake_urlopen([], fail=True))
    with pytest.raises(MissingTable) as err:
        Master("https://example.invalid/m", cache_dir=str(tmp_path)).table("gameCharacterUnits")
    assert "gameCharacterUnits" in str(err.value)


def test_the_default_base_url_is_a_raw_file_host():
    # 默认基址只有在调用方明确选择远端时才会被用到。
    assert DEFAULT_MASTER_URL.startswith("https://")
    assert DEFAULT_MASTER_URL.endswith("/master")


def test_no_request_is_made_for_a_local_source(tmp_path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("本地目录不该联网")

    monkeypatch.setattr(master_mod.urllib.request, "urlopen", explode)
    with io.open(tmp_path / "t.json", "w", encoding="utf-8", newline="\n") as h:
        json.dump(ROWS, h)
    assert Master(str(tmp_path)).table("t") == ROWS


def test_registry_reads_real_master_accessors(tmp_path):
    """build_registry must run against the real Master class, not only stubs.

    Regression: an edit once deleted Master.solo_actions while adding
    client_configs; stub-based registry tests stayed green and characters.json
    broke for every caller that supplied real master tables.
    """
    tables = {
        "gameCharacterUnits.json": [{"id": 1, "gameCharacterId": 1, "unit": "light_sound",
                                     "colorCode": "#fff", "skinColorCode": "#fee",
                                     "skinShadowColorCode1": "#edd", "skinShadowColorCode2": "#dcc"}],
        "mysekaiCharacterTalkMotions.json": [{"gameCharacterUnitId": 1, "idleMotion": "i",
                                              "walkMotion": "w", "runMotion": "r",
                                              "walkSpeed": 400, "runSpeed": 1000,
                                              "runOccurRate": 50, "pauseMilliSeconds": 15000,
                                              "changeMotionMilliSeconds": 500}],
        "mysekaiCharacterTalkSoloActions.json": [{"gameCharacterUnitId": 1, "lua": "solo_01"}],
        "clientConfigs.json": CLIENT_CONFIG_ROWS,
    }
    for name, rows in tables.items():
        with io.open(tmp_path / name, "w", encoding="utf-8", newline="\n") as h:
            json.dump(rows, h)
    from chara.registry import build_registry
    doc = build_registry(Master(str(tmp_path)), [1])
    entry = doc["characters"]["1"]
    assert entry["soloAction"] == "solo_01"
    assert entry["locomotion"]["walkSpeedMetersPerSecond"] == 0.4
    assert doc["player"]["derived"]["dashSpeedMetersPerSecond"] == 4.375
