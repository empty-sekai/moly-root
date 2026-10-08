"""Segment-address tests plus opt-in checks against actual supplied shader bytes.

Synthetic cases test addressing/bounds only; the package test independently
checks full program parsing, byte accounting and declared-program attribution.
"""
import os
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from shaders import blob, objects


def table(entries):
    return struct.pack("<i", len(entries)) + b"".join(
        struct.pack("<3i", *entry) for entry in entries)


def index_only_parser(monkeypatch):
    def read(data, offset, length):
        return {"offset": offset, "length": length, "body": data[offset:offset + length]}
    monkeypatch.setattr(blob, "parse_record", read)


def test_table_segment_and_program_segments_have_independent_offsets(monkeypatch):
    index_only_parser(monkeypatch)
    parts = [table([(0, 4, 1), (4, 4, 1), (0, 4, 2)]), b"AAAABBBB", b"CCCC"]
    parsed = blob.parse_segments(parts, 9)
    assert [r["body"] for r in parsed["records"]] == [b"AAAA", b"BBBB", b"CCCC"]
    assert [r["sourceOffset"] for r in parsed["records"]] == [0, 4, 0]
    assert [r["segment"] for r in parsed["records"]] == [1, 1, 2]
    assert [r["offset"] for r in parsed["records"]] == [40, 44, 48]
    assert parsed["account"]["balanced"]
    assert parsed["account"]["gapBytes"] == 0


def test_records_can_share_segment_zero_with_the_table(monkeypatch):
    index_only_parser(monkeypatch)
    parts = [table([(28, 4, 0), (0, 4, 1)]) + b"AAAA", b"BBBB"]
    parsed = blob.parse_segments(parts, 9)
    assert [r["body"] for r in parsed["records"]] == [b"AAAA", b"BBBB"]
    assert parsed["account"]["gapBytes"] == 0


@pytest.mark.parametrize("entries,parts,match", [
    ([(0, 4, 2)], [b"DATA"], "missing segment"),
    ([(-1, 4, 1)], [b"DATA"], "exceeds its source segment"),
    ([(0, -1, 1)], [b"DATA"], "exceeds its source segment"),
    ([(2, 4, 1)], [b"DATA", b"XXXX"], "exceeds its source segment"),
    ([(0, 4, 0)], [b"DATA"], "overlaps the index table"),
    ([(0, 4, 1), (2, 4, 1)], [b"ABCDEF"], "does not balance"),
])
def test_invalid_segment_addresses_fail_closed(entries, parts, match):
    with pytest.raises(blob.BlobError, match=match):
        blob.parse_segments([table(entries), *parts], 9)


def test_losing_segment_boundaries_is_not_accepted_as_a_flat_blob():
    with pytest.raises(blob.BlobError):
        blob.parse_blob(table([(0, 4, 1)]) + b"DATA", 9)


def test_nonzero_segment_in_an_apparently_flat_table_is_rejected():
    with pytest.raises(blob.BlobError, match="parse_segments"):
        blob.parse_blob(table([(16, 4, 1)]) + b"DATA", 9)


def test_empty_segments_and_truncated_tables_fail_closed():
    for parts in ([], [b"", b"DATA"], [struct.pack("<i", 2), b"DATA"]):
        with pytest.raises(blob.BlobError):
            blob.parse_segments(parts, 9)


def test_single_segment_keeps_old_index_layout(monkeypatch):
    index_only_parser(monkeypatch)
    raw = table([(16, 4, 0)]) + b"DATA"
    assert blob.parse_segments([raw], 9) == blob.parse_blob(raw, 9)


def test_pass_tags_are_read_from_the_actual_state_block():
    tree = {"m_ParsedForm": {"m_SubShaders": [{"m_Passes": [
        {"m_State": {"m_Name": "effect", "m_Tags": {"tags": [("LIGHTMODE", "MysekaiEffect")]}}},
        {"m_State": {"m_Name": "untagged", "m_Tags": {"tags": []}}},
    ]}]}}
    assert objects.passes(tree) == [(0, 0, "MysekaiEffect", "effect"), (0, 1, "", "untagged")]


def test_current_shader_packages_have_complete_segment_coverage():
    paths = [p for p in os.environ.get("MOLY_SHADER_BUNDLES", "").split(os.pathsep) if p]
    if not paths:
        pytest.skip("supply source packages via MOLY_SHADER_BUNDLES")
    import UnityPy
    from core.unity import configure_fallback_unity_version
    configure_fallback_unity_version()
    count = multi = programs = 0
    for path in paths:
        for _, tree in objects.shaders_in(UnityPy.load(path)):
            count += 1
            parsed = {}
            for platform, parts in objects.platform_segments(tree):
                data = blob.parse_segments(parts, platform)
                assert data["account"]["balanced"], objects.shader_name(tree)
                assert data["account"]["header"] + data["account"]["records"] + data["account"]["gapBytes"] == sum(map(len, parts))
                parsed[platform] = data
                multi += len(parts) > 1
                programs += sum(r["kind"] == "program" for r in data["records"])
            attribution = objects.attribution_check(tree, parsed)
            assert attribution["typeMismatch"] == attribution["outOfRange"] == 0, (objects.shader_name(tree), attribution)
            assert attribution["agree"] == attribution["checked"], attribution
    assert count > 0 and programs > 0
    print(f"source shaders={count}, multi-segment platforms={multi}, program records={programs}")
