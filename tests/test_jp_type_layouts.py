"""Synthetic tests of the JP per-object layout choice.

Every test input is invented here: a class name, a 32-hex-digit type hash and
synthetic bytes, never game data.  A layout's chain is checked on bytes this
file builds, and the choice is checked by the (class, hash) table's answer and
its refusal.
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

BASE = {**editor_layout.DECODERS, **window_animation.DECODERS}
SCROLL_RECT = "Sekai.UI.CustomScrollRect"


def i32(value):
    return struct.pack("<i", value)


def f32(value):
    return struct.pack("<f", value)


def b4(value):
    return struct.pack("<I", 1 if value else 0)


def pptr(path_id):
    return struct.pack("<iq", 0, path_id)


def scroll_rect(pid, tolerance=None):
    """ScrollRect's serialized fields, then CustomScrollRect's own."""
    raw = (pptr(pid) + b4(True) + b4(False) + i32(2) + f32(0.1) + b4(True)
           + f32(0.135) + f32(1.0) + pptr(pid + 1) + pptr(0) + pptr(0)
           + i32(0) + i32(2) + f32(0) + f32(0) + i32(0)          # empty UnityEvent
           + b4(True) + b4(False) + b4(True) + pptr(pid + 2))
    if tolerance is not None:
        raw += f32(tolerance)
    return raw


class TypeHash:
    """The one attribute the chain choice reads from a serialized type."""

    def __init__(self, digest):
        self.old_type_hash = None if digest is None else bytes.fromhex(digest)


class Object:
    """The object attributes the chain choice reads: file name and type."""

    def __init__(self, file_name, digest):
        self.serialized_type = TypeHash(digest)
        self.assets_file = type("File", (), {"name": file_name})()


def scroll_rect_rows():
    """{hash: layout} of the class whose declaration changed in 7.0.0."""
    return {digest: layout for (cls, digest), layout in jd.TYPE_LAYOUTS.items()
            if cls == SCROLL_RECT}


def read(chain, raw):
    reader = talk.Reader(raw)
    fields = chain(reader)
    return fields, reader.remaining()


def test_the_table_lists_a_layout_of_each_registered_hash():
    assert set(jd.TYPE_LAYOUTS.values()) == {"6.8.1", "7.0.0"}
    assert jd.LAYOUT_CLASSES == frozenset(cls for cls, _ in jd.TYPE_LAYOUTS)
    # A class with one layout in both clients lists exactly one row; a class
    # with two lists one row each.
    for cls in jd.LAYOUT_CLASSES:
        rows = [layout for (name, _), layout in jd.TYPE_LAYOUTS.items() if name == cls]
        assert len(rows) in (1, 2)
    # Every row names a chain: the row's layout table, or the shared base.
    for (cls, digest), layout in jd.TYPE_LAYOUTS.items():
        assert jd.chain_for(cls, digest, "f", BASE) is not None, (cls, digest)


def test_the_scroll_rect_rows_name_the_two_layouts():
    """The class with two serialized layouts shows one row per layout, and each
    row's chain reads its own layout to the object's end while the other
    layout's chain does not."""
    rows = scroll_rect_rows()
    assert len(rows) == 2
    assert set(rows.values()) == {"6.8.1", "7.0.0"}
    for digest, layout in rows.items():
        raw = scroll_rect(1000, 0.01 if layout == "7.0.0" else None)
        fields, remaining = read(jd.chain_for(SCROLL_RECT, digest, "f", BASE), raw)
        assert remaining == 0
        assert ("normalizedPositionTolerance" in fields) == (layout == "7.0.0")
        other = next(d for d, l in rows.items() if l != layout)
        try:
            _, other_remaining = read(jd.chain_for(SCROLL_RECT, other, "f", BASE), raw)
        except struct.error:
            other_remaining = -1
        assert other_remaining != 0


def test_an_objects_own_hash_chooses_its_chain():
    """The same class resolves to different chains by the object's hash."""
    rows = scroll_rect_rows()
    old_hash = next(digest for digest, layout in rows.items() if layout == "6.8.1")
    new_hash = next(digest for digest, layout in rows.items() if layout == "7.0.0")
    assert jd.chain_for(SCROLL_RECT, old_hash, "a", BASE).__name__ == \
        "decode_custom_scroll_rect"
    assert jd.chain_for(SCROLL_RECT, new_hash, "b", BASE).__name__ == \
        "decode_custom_scroll_rect_700"


def test_every_registered_class_reads_its_own_layout():
    """Each row's chain differs from the other layout's where the declarations
    differ, and is the same chain where the probes read one layout."""
    for cls in sorted(jd.LAYOUT_CLASSES):
        rows = {digest: layout for (name, digest), layout in jd.TYPE_LAYOUTS.items()
                if name == cls}
        chains = {layout: jd.chain_for(cls, digest, "f", BASE)
                  for digest, layout in rows.items()}
        assert all(chain is not None for chain in chains.values())
        if len(rows) == 1:
            assert len(set(map(id, chains.values()))) == 1
        else:
            assert len({id(chain) for chain in chains.values()}) == 2


def test_a_class_outside_the_table_reads_from_the_shared_base():
    sentinel = object()
    base = {"Not.A.Registered.Class": sentinel}
    assert jd.chain_for("Not.A.Registered.Class", "0" * 32, "f", base) is sentinel
    assert jd.chain_for("Another.Class", None, "f", base) is None


def test_an_unrecorded_hash_is_refused_with_class_hash_and_file():
    with pytest.raises(jd.UnknownLayout) as raised:
        jd.chain_for(SCROLL_RECT, "f" * 32, "cab-deadbeef", BASE)
    message = str(raised.value)
    assert SCROLL_RECT in message
    assert "f" * 32 in message
    assert "cab-deadbeef" in message
    # A file that predates the hash field is refused by name too, never read
    # with a fallback chain.
    with pytest.raises(jd.UnknownLayout, match="absent"):
        jd.chain_for(SCROLL_RECT, None, "cab-old", BASE)


def test_a_table_bound_to_an_object_reads_that_objects_hash():
    """talk.decode_object can hand each object to the table that chooses."""
    new_hash = next(digest for digest, layout in scroll_rect_rows().items()
                    if layout == "7.0.0")
    chains = jd.TypeHashChains(BASE)
    assert chains.chain_for(SCROLL_RECT, Object("cab-1", new_hash)).__name__ == \
        "decode_custom_scroll_rect_700"
    # The plain class lookup still answers the run-wide base table.
    assert chains.get(SCROLL_RECT) is BASE[SCROLL_RECT]


def test_a_client_version_with_no_layouts_is_refused():
    for version in jd.VERSIONS:
        jd.check_version(version)
    with pytest.raises(ValueError, match="6.8.1, 7.0.0"):
        jd.check_version("0.0.0")
