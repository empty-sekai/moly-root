# tests/test_jp_decoders.py -- synthetic tests of the per-version JP decoder tables
"""Synthetic bytes only: each class gains the documented 7.0.0 field, and the
6.8.1 chain must leave exactly those bytes unread while the 7.0.0 chain
over-reads the 6.8.1 bytes.  Every test asserts the value of the added field,
so a chain that consumes the right byte count in the wrong order fails.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ui import editor_layout  # noqa: E402
from ui import jp_decoders as jd  # noqa: E402
from ui import talk  # noqa: E402
from ui import window_animation  # noqa: E402

# The base registry the JP table overrides, for the classes under test.
BASE = {**editor_layout.DECODERS, **window_animation.DECODERS}


def table(version):
    return {**BASE, **jd.DECODERS_BY_VERSION[version]}


def i32(v):
    return struct.pack("<i", v)


def f32(v):
    return struct.pack("<f", v)


def b4(v):
    return struct.pack("<I", 1 if v else 0)


def pptr(pid):
    return struct.pack("<iq", 0, pid)


def decode(version, name, raw):
    r = talk.Reader(raw)
    fields = table(version)[name](r)
    return fields, r.remaining()


def scroll_rect(pid, tolerance=None):
    """ScrollRect's serialized fields, then CustomScrollRect's own."""
    raw = (pptr(pid) + b4(True) + b4(False) + i32(2) + f32(0.1) + b4(True)
           + f32(0.135) + f32(1.0) + pptr(pid + 1) + pptr(0) + pptr(0)
           + i32(0) + i32(2) + f32(0) + f32(0) + i32(0)          # empty UnityEvent
           + b4(True) + b4(False) + b4(True) + pptr(pid + 2))
    if tolerance is not None:
        raw += f32(tolerance)
    return raw


def craft_preview(pid, purchase_limit):
    """The twenty 6.8.1 references; the four purchase-limit fields sit between
    the twentieth (``_craftTermText``) and the layout-cost pair."""
    head = [pid + n for n in range(20)]
    tail = [pid + 100, pid + 101]
    raw = b"".join(pptr(p) for p in head)
    if purchase_limit:
        raw += b"".join(pptr(pid + 200 + n) for n in range(4))
    return raw + b"".join(pptr(p) for p in tail)


def option_page(pid, bulk_harvest):
    names = [pid + n for n in range(6)]
    if bulk_harvest:
        names[2:2] = [pid + 50, pid + 51]
    return b"".join(pptr(p) for p in names)


def selector_cell(pid, fitter):
    """The selector cell's eleven references, two colours, two references."""
    raw = b"".join(pptr(pid + n) for n in range(11))
    raw += f32(1) * 4 + f32(2) * 4
    raw += b"".join(pptr(pid + 20 + n) for n in range(2))
    if fitter:
        raw += pptr(pid + 40)
    return raw


def honor_image(pid, word_and_medal):
    """The eight 6.8.1 references; ``wordImage`` follows ``coreImage`` and
    ``_medalView`` follows ``levelView``."""
    names = [pid, pid + 1, pid + 2, pid + 3, pid + 4, pid + 5, pid + 6, pid + 7]
    if word_and_medal:
        names = [pid, pid + 10, pid + 1, pid + 2, pid + 3, pid + 11,
                 pid + 4, pid + 5, pid + 6, pid + 7]
    return b"".join(pptr(p) for p in names)


# class -> (6.8.1 bytes, 7.0.0 bytes, added field, its expected value)
CASES = {
    "Sekai.UI.CustomScrollRect": (
        lambda pid: scroll_rect(pid), lambda pid: scroll_rect(pid, 0.01),
        "normalizedPositionTolerance", pytest.approx(0.01)),
    "Sekai.Mysekai.Inventory.CraftPreview": (
        lambda pid: craft_preview(pid, False), lambda pid: craft_preview(pid, True),
        "_purchaseLimitTextRoot", (0, 1200)),
    "Sekai.Mysekai.MysekaiInfoOption1Page": (
        lambda pid: option_page(pid, False), lambda pid: option_page(pid, True),
        "_bulkHarvestTargetSettingRoot", (0, 1050)),
    "Sekai.Mysekai.ContentList.ContentListSelectorCell": (
        lambda pid: selector_cell(pid, False), lambda pid: selector_cell(pid, True),
        "_contentSizeFitter", (0, 1040)),
    "Sekai.UIPartsHonorImage": (
        lambda pid: honor_image(pid, False), lambda pid: honor_image(pid, True),
        "wordImage", (0, 1010)),
}


def test_supported_versions_are_the_two_tables():
    assert jd.VERSIONS == ("6.8.1", "7.0.0")
    assert set(jd.DECODERS_BY_VERSION) == set(jd.VERSIONS)
    with pytest.raises(ValueError, match="6.8.1, 7.0.0"):
        jd.decoders_for("0.0.0")


@pytest.mark.parametrize("name", sorted(CASES))
def test_700_chain_reads_the_added_field(name):
    old, new, added, value = CASES[name]
    fields, remaining = decode("7.0.0", name, new(1000))
    assert remaining == 0
    assert fields[added] == value


@pytest.mark.parametrize("name", sorted(CASES))
def test_681_chain_leaves_the_added_field_unread(name):
    old, new, added, value = CASES[name]
    fields, remaining = decode("6.8.1", name, new(1000))
    assert remaining > 0
    assert added not in fields


@pytest.mark.parametrize("name", sorted(CASES))
def test_700_chain_over_reads_681_bytes(name):
    old, new, added, value = CASES[name]
    with pytest.raises((struct.error, ValueError, UnicodeDecodeError)):
        table("7.0.0")[name](talk.Reader(old(1000)))


def test_700_table_overrides_exactly_the_five_classes():
    """The 7.0.0 table differs from the 6.8.1 one in the five classes only.
    Three of them are 6.8.1-table keys; CraftPreview and the option page live
    in the base registry, so the 7.0.0 table adds rather than replaces them."""
    assert set(jd.DECODERS_700) - set(jd.DECODERS) == {
        "Sekai.UI.CustomScrollRect",
        "Sekai.Mysekai.ContentList.ContentListSelectorCell",
        "Sekai.UIPartsHonorImage",
    }
    assert (set(jd.DECODERS_700) ^ set(jd.DECODERS)) | {
        "Sekai.Mysekai.Inventory.CraftPreview",
        "Sekai.Mysekai.MysekaiInfoOption1Page",
    } == set(CASES)
    for name in set(jd.DECODERS_700) - set(CASES):
        assert jd.DECODERS_700[name] is jd.DECODERS[name]

