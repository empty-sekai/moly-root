"""Checks for the shader blob reader.

The unit checks below stand on their own.  The rest need real shader packages, which
are never part of this repository: point ``MOLY_SHADER_BUNDLES`` at one or more
decrypted bundles (separated by the platform path separator) and they run; without it
they skip.

    MOLY_SHADER_BUNDLES=/path/mysekai__shader pytest tests/test_shaderblob.py

Two of these checks are the reason the module exists.  The first is the byte account:
a blob record is handed to the engine with an explicit end pointer, so a correct
reading consumes it exactly, and a mis-sized field cannot balance.  The second is the
truncation check: the previous reader parsed the reflection table as if it were a
compiled program, which cut uniform names in half without raising -- and a half name
is worse than a missing one, because "the full name is nowhere in this shader" then
looks like a finding.
"""
import os
import re
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core.assets import shaderblob

BUNDLES_VARIABLE = "MOLY_SHADER_BUNDLES"
CLASSID_SHADER = 48

# Names the previous reader produced by cutting a longer name in half.  Each is a
# strict prefix of a name that really is in the data.
KNOWN_TRUNCATIONS = ("_GlobalChara", "_GlobalEdgeS", "_GlobalPheno",
                     "_GlobalPhenomenaSh", "_Mysekai", "_MysekaiTreasure")

# Families used for the comparison against a raw scan of the decompressed bytes.
FAMILIES = {"Mysekai": re.compile(r"_Mysekai[A-Za-z0-9_]*"),
            "Global": re.compile(r"_Global[A-Za-z0-9_]*")}

PROGRAM_STAGES = ("progVertex", "progFragment", "progGeometry", "progHull",
                  "progDomain", "progRayTracing")


def _table(entries, stride=shaderblob.ENTRY_STRIDE_SEGMENTED):
    """Serialise an index table so the account arithmetic can be exercised alone."""
    out = struct.pack("<i", len(entries))
    for offset, length, segment in entries:
        out += struct.pack("<3i", offset, length, segment)[:stride]
    return out


def test_account_balances_when_records_tile_the_blob():
    header = 4 + 2 * shaderblob.ENTRY_STRIDE_SEGMENTED
    entries = [(header, 8, 0), (header + 8, 12, 0)]
    balance = shaderblob.account(bytes(header + 20), entries, header)
    assert balance["balanced"]
    assert balance["gapBytes"] == 0
    assert balance["records"] == 20


def test_account_reports_a_gap_without_pretending_the_blob_is_covered():
    header = 4
    balance = shaderblob.account(bytes(40), [(header, 8, 0), (20, 20, 0)], header)
    assert balance["gaps"] == [(12, 20)]
    assert balance["gapBytes"] == 8
    assert balance["header"] + balance["records"] + balance["gapBytes"] == 40


def test_account_refuses_overlapping_records():
    header = 4
    balance = shaderblob.account(bytes(40), [(header, 20, 0), (16, 20, 0)], header)
    assert balance["overlaps"]
    assert not balance["balanced"]


def test_account_refuses_a_record_running_past_the_end():
    header = 4
    balance = shaderblob.account(bytes(20), [(header, 40, 0)], header)
    assert not balance["balanced"]


def test_entry_table_infers_the_stride_from_the_first_offset():
    blob = _table([(4 + 12, 4, 0)]) + bytes(4)
    entries, stride = shaderblob.entry_table(blob)
    assert stride == shaderblob.ENTRY_STRIDE_SEGMENTED
    assert entries == [(16, 4, 0)]


def test_entry_table_rejects_a_table_no_stride_explains():
    blob = struct.pack("<ii", 1, 999) + bytes(64)
    with pytest.raises(shaderblob.BlobError):
        shaderblob.entry_table(blob)


def test_parse_record_rejects_a_version_the_engine_would_not_read():
    blob = struct.pack("<i", shaderblob.VERSION_MIN - 1) + bytes(28)
    with pytest.raises(shaderblob.BlobError):
        shaderblob.parse_record(blob, 0, len(blob))


def test_binding_kinds_cover_the_dispatch_table():
    # The engine dispatches the binding kind through a five-entry jump table; a sixth
    # value is not a kind, and a record that claims one carries three ints like the
    # rest.  Keeping the map exactly five long is what makes that assumption visible.
    assert sorted(shaderblob.BIND_KINDS) == [0, 1, 2, 3, 4]


def _segments(values, index):
    value = values[index]
    return list(value) if isinstance(value, list) else [value]


def _platform_blobs(shader, decompress):
    """Every platform of one shader as (platform, decompressed blob bytes)."""
    out = []
    if not shader.compressedBlob:
        return out
    blob = bytes(shader.compressedBlob)
    for index, platform in enumerate(shader.platforms):
        if (index >= len(shader.compressedLengths)
                or index >= len(shader.decompressedLengths)):
            break
        packed = _segments(shader.compressedLengths, index)
        unpacked = _segments(shader.decompressedLengths, index)
        offsets = _segments(shader.offsets, index)
        parts = [bytes(decompress(blob[offsets[k]:offsets[k] + packed[k]],
                                  unpacked[k])) for k in range(len(packed))]
        out.append((int(platform), b"".join(parts)))
    return out


@pytest.fixture(scope="module")
def shader_blobs():
    """(bundle, shader name, shader object, platform, blob bytes) for every platform."""
    configured = os.environ.get(BUNDLES_VARIABLE)
    if not configured:
        pytest.skip(f"{BUNDLES_VARIABLE} is not configured")
    unitypy = pytest.importorskip("UnityPy")
    from UnityPy.helpers import CompressionHelper

    paths = [Path(part) for part in configured.split(os.pathsep) if part]
    present = [path for path in paths if path.is_file()]
    if not present:
        pytest.skip(f"{BUNDLES_VARIABLE} points at no readable file")
    unitypy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"
    collected = []
    for path in present:
        environment = unitypy.load(str(path))
        objects = [obj for obj in environment.objects
                   if int(obj.type) == CLASSID_SHADER]
        assert objects, f"UnityPy returned no shader objects for {path}"
        for obj in objects:
            shader = obj.read()
            for platform, raw in _platform_blobs(
                    shader, CompressionHelper.decompress_lz4):
                collected.append((path.name, shader.m_ParsedForm.m_Name, shader,
                                  platform, raw))
    assert collected, "the configured bundles hold no shader blobs"
    return collected


@pytest.fixture(scope="module")
def parsed_blobs(shader_blobs):
    return [(bundle, name, platform, raw, shaderblob.parse_blob(raw, platform))
            for bundle, name, _shader, platform, raw in shader_blobs]


def test_every_blob_balances_to_the_byte(parsed_blobs):
    for bundle, name, platform, raw, parsed in parsed_blobs:
        balance = parsed["account"]
        where = f"{bundle}:{name} platform {platform}"
        assert balance["balanced"], where
        assert not balance["overlaps"], where
        assert balance["gapBytes"] == 0, f"{where} leaves {balance['gaps']} unread"
        assert (balance["header"] + balance["records"]
                == len(raw)), f"{where} does not tile {len(raw)} bytes"


def test_both_record_kinds_occur_and_none_is_ambiguous(parsed_blobs):
    # parse_record raises when a record parses as both kinds or as neither, so
    # reaching this point at all means every record was decided by the byte account.
    kinds = {"program": 0, "parameters": 0}
    for *_ignored, parsed in parsed_blobs:
        for record in parsed["records"]:
            kinds[record["kind"]] += 1
    assert kinds["program"], "no program record parsed"
    assert kinds["parameters"], (
        "no parameter record parsed -- the split that caused the truncation bug is "
        "not being exercised")


def test_the_known_truncated_names_are_gone(parsed_blobs):
    names = set()
    for *_ignored, parsed in parsed_blobs:
        names |= shaderblob.uniform_names(parsed)
    still_there = [name for name in KNOWN_TRUNCATIONS if name in names]
    assert not still_there, f"truncated names back in the output: {still_there}"


def test_no_reported_name_is_a_prefix_of_a_name_in_the_raw_bytes(parsed_blobs):
    """The general form of the check above: no half names, whatever they are called.

    The comparison set comes from scanning the decompressed bytes directly, which
    needs no record parsing and so cannot inherit a parsing mistake.
    """
    reported, in_bytes = set(), set()
    for _bundle, _name, _platform, raw, parsed in parsed_blobs:
        reported |= shaderblob.uniform_names(parsed)
        text = raw.decode("latin-1")
        for pattern in FAMILIES.values():
            in_bytes.update(pattern.findall(text))
    truncated = sorted(
        name for name in reported
        if any(pattern.fullmatch(name) for pattern in FAMILIES.values())
        and any(other != name and other.startswith(name) for other in in_bytes))
    assert not truncated, f"names cut short: {truncated}"


def test_family_names_match_a_raw_scan_of_the_same_bytes(parsed_blobs):
    """Neither side may hold a family name the other misses."""
    reported, in_bytes = set(), set()
    for _bundle, _name, _platform, raw, parsed in parsed_blobs:
        reported |= shaderblob.uniform_names(parsed)
        text = raw.decode("latin-1")
        for pattern in FAMILIES.values():
            in_bytes.update(pattern.findall(text))
    for family, pattern in FAMILIES.items():
        mine = {name for name in reported if pattern.fullmatch(name)}
        theirs = {name for name in in_bytes if pattern.fullmatch(name)}
        assert mine == theirs, (f"{family}: missing {sorted(theirs - mine)}, "
                               f"unexplained {sorted(mine - theirs)}")


def test_the_split_agrees_with_the_shader_own_blob_indices(shader_blobs):
    """Independent cross-check, from the serialized file instead of the blob.

    ``m_PlayerSubPrograms[*][*].m_BlobIndex`` names the code records and
    ``m_ParameterBlobIndices[*][*]`` the parameter records.  The outer index of those
    lists is not the platform index -- one inner list mixes platforms -- so a
    subprogram is mapped to a platform through the program type it declares, and is
    only checked when the parser saw that type on exactly one platform.
    """
    checked = 0
    by_shader = {}
    for bundle, name, shader, platform, raw in shader_blobs:
        entry = by_shader.setdefault((bundle, name, id(shader)),
                                     (name, shader, {}))
        entry[2][platform] = raw
    for name, shader, blobs in by_shader.values():
        code, parameters, platform_of_type = {}, {}, {}
        for platform, raw in blobs.items():
            parsed = shaderblob.parse_blob(raw, platform)
            code[platform] = {record["index"] for record in parsed["records"]
                              if record["kind"] == "program"}
            parameters[platform] = {record["index"] for record in parsed["records"]
                                    if record["kind"] == "parameters"}
            for record in parsed["records"]:
                if record["kind"] == "program":
                    platform_of_type.setdefault(record["programType"],
                                                set()).add(platform)
        for subshader in shader.m_ParsedForm.m_SubShaders:
            for shader_pass in subshader.m_Passes:
                for stage in PROGRAM_STAGES:
                    program = getattr(shader_pass, stage, None)
                    if program is None or not program.m_PlayerSubPrograms:
                        continue
                    blob_indices = program.m_ParameterBlobIndices or []
                    for outer, group in enumerate(program.m_PlayerSubPrograms):
                        expected = (list(blob_indices[outer])
                                    if outer < len(blob_indices) else [])
                        for inner, subprogram in enumerate(group):
                            platforms = platform_of_type.get(
                                int(subprogram.m_GpuProgramType), set())
                            if len(platforms) != 1:
                                continue
                            platform = next(iter(platforms))
                            checked += 1
                            assert int(subprogram.m_BlobIndex) in code[platform], (
                                f"{name}: platform {platform} record "
                                f"{subprogram.m_BlobIndex} is code to the shader but "
                                f"a parameter table to the parser")
                            if inner < len(expected):
                                assert int(expected[inner]) in parameters[platform], (
                                    f"{name}: platform {platform} record "
                                    f"{expected[inner]} is a parameter table to the "
                                    f"shader but code to the parser")
    assert checked, "no subprogram could be mapped to a platform"


def test_stretching_one_record_past_the_blob_is_rejected(parsed_blobs):
    """What makes it red: a length nobody can honour."""
    bundle, name, platform, raw, parsed = parsed_blobs[0]
    entries, stride = shaderblob.entry_table(raw)
    last = max(range(len(entries)), key=lambda index: entries[index][0])
    broken = bytearray(raw)
    struct.pack_into("<i", broken, 4 + last * stride + 4, entries[last][1] + 4)
    with pytest.raises(shaderblob.BlobError):
        shaderblob.parse_blob(bytes(broken), platform)


def test_overlapping_two_records_is_rejected(parsed_blobs):
    """What makes it red: a length that eats into the next record."""
    for _bundle, _name, platform, raw, _parsed in parsed_blobs:
        entries, stride = shaderblob.entry_table(raw)
        if len(entries) < 2:
            continue
        broken = bytearray(raw)
        struct.pack_into("<i", broken, 4 + stride + 4, entries[0][1] + 4)
        with pytest.raises(shaderblob.BlobError):
            shaderblob.parse_blob(bytes(broken), platform)
        return
    pytest.skip("no blob with two records to overlap")


def test_shortening_one_record_is_rejected(parsed_blobs):
    """What makes it red: a length that stops before the record does.

    This is the shape of the bug being fixed -- a reading that stops early leaves the
    tail of a name behind -- and it is caught by the record parse rather than by the
    table account, because dropping four bytes from a record just moves them into a
    gap.
    """
    for _bundle, _name, platform, raw, _parsed in parsed_blobs:
        entries, stride = shaderblob.entry_table(raw)
        if not entries or entries[0][1] < 8:
            continue
        broken = bytearray(raw)
        struct.pack_into("<i", broken, 4 + 4, entries[0][1] - 4)
        with pytest.raises(shaderblob.BlobError):
            shaderblob.parse_blob(bytes(broken), platform)
        return
    pytest.skip("no blob with a shortenable record")
