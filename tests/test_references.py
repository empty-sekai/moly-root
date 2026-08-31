"""What an unresolved cross-package pointer is allowed to claim."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.assets import references                                  # noqa: E402
from core.fetch import Manifest                                     # noqa: E402


class _Inner:
    def __init__(self, archives):
        self.files = {name: object() for name in archives}


class _Loaded:
    """What the loader hands back: outer containers keyed by *path*, whose own
    file tables are keyed by archive name."""

    def __init__(self, archives):
        self.files = {"some/path/on/disk": _Inner(archives)}


def _loader(table):
    return lambda path: _Loaded(table[Path(path).name])


def _manifest(rows):
    return Manifest([{"bundleName": name, "isBuiltin": builtin}
                     for name, builtin in rows])


def test_a_package_is_read_for_its_archive_names_not_its_own_name():
    """The outer table is keyed by the path the package was loaded from, and a
    pointer never names that; reading one level too high indexes nothing a
    pointer can ask for."""
    names = references.archives_in("dir/some_package",
                                   _loader({"some_package": ["CAB-aaa"]}))
    assert names == ["CAB-aaa"]


def test_one_package_can_hold_several_archives():
    """Measured on a real root: 1,921 of 6,510 packages do.  A package-keyed map
    would drop every archive but one of them."""
    index = references.ArchiveIndex.build(
        ["dir/pack"], _loader({"pack": ["CAB-aaa", "CAB-bbb"]}))
    assert index.of("CAB-aaa") == index.of("CAB-bbb") == "pack"
    assert len(index) == 2


def test_a_contested_archive_raises_rather_than_picking_a_side():
    """No archive was claimed twice on the root this was measured against, so a
    collision means the assumption broke -- and a silent pick would make the
    wrong answer look exactly like the right one."""
    index = references.ArchiveIndex.build(
        ["dir/one", "dir/two"],
        _loader({"one": ["CAB-aaa"], "two": ["CAB-aaa"]}))
    with pytest.raises(LookupError):
        index.of("CAB-aaa")


def test_a_located_archive_is_never_reported_as_a_fetchable_gap():
    """Being in the index means the package is in the root, so "fetch it" sends
    a reader after a download for a file already on disk.  The fix is to load
    the package, and the answer has to name it."""
    index = references.ArchiveIndex.build(["dir/held"], _loader({"held": ["CAB-aaa"]}))
    for manifest in (_manifest([("held", False)]), _manifest([("held", True)]), None):
        answer = references.explain("CAB-aaa", index, manifest)
        assert answer["reason"] == references.NOT_LOADED
        assert answer["package"] == "held"


def test_an_archive_in_no_indexed_package_is_settled_by_elimination():
    """Nothing in the root holds it and every dependency absent from the root is
    in the player build, so there is no download that would produce it."""
    index = references.ArchiveIndex.build(["dir/referrer"],
                                          _loader({"referrer": ["CAB-self"]}))
    answer = references.explain("CAB-elsewhere", index,
                                _manifest([("referrer", False), ("shader/live", True)]),
                                dependencies=["shader/live"])
    assert answer["reason"] == references.IN_PLAYER_BUILD
    assert answer["via"] == ["shader/live"]


def test_a_downloadable_dependency_absent_from_the_root_is_the_fetchable_gap():
    """This is the one case where a download is the fix, and it is named."""
    index = references.ArchiveIndex.build(["dir/referrer"],
                                          _loader({"referrer": ["CAB-self"]}))
    answer = references.explain(
        "CAB-elsewhere", index,
        _manifest([("referrer", False), ("shader/live", True), ("other/pack", False)]),
        dependencies=["shader/live", "other/pack"])
    assert answer["reason"] == references.NOT_SUPPLIED
    assert answer["via"] == ["other/pack"]


def test_elimination_refuses_when_an_absent_dependency_has_no_manifest_row():
    """With no row there is nothing to say whether that package ships, so
    neither "fetch it" nor "it is in the player build" is established."""
    index = references.ArchiveIndex.build(["dir/referrer"],
                                          _loader({"referrer": ["CAB-self"]}))
    answer = references.explain("CAB-elsewhere", index,
                                _manifest([("shader/live", True)]),
                                dependencies=["shader/live", "unlisted/pack"])
    assert answer["reason"] == references.UNEXPLAINED
    assert answer["via"] == ["shader/live", "unlisted/pack"]


def test_elimination_refuses_when_any_package_failed_to_index():
    """With a package unread, "no indexed package holds it" is a fact about a
    partial read of the root rather than about the root."""
    def explode(path):
        if Path(path).name == "broken":
            raise ValueError("unreadable")
        return _Loaded(["CAB-self"])

    index = references.ArchiveIndex.build(["dir/referrer", "dir/broken"], explode)
    assert index.failures and index.scanned == 2
    answer = references.explain("CAB-elsewhere", index,
                                _manifest([("shader/live", True)]),
                                dependencies=["shader/live"])
    assert answer["reason"] == references.UNEXPLAINED


def test_an_unusable_root_is_a_recorded_failure_rather_than_a_raise(tmp_path):
    """This index explains gaps, so it must not become one: raised from inside a
    run it would abort the extraction it was asked about, and the wreckage would
    read as a fault in the extraction."""
    index = references.ArchiveIndex.of_directory(tmp_path / "never-created")
    assert len(index) == 0 and index.scanned == 0 and index.failures
    assert references.explain("CAB-aaa", index, _manifest([("shader/live", True)]),
                              dependencies=["shader/live"])["reason"] \
        == references.UNEXPLAINED


def test_an_engine_archive_is_not_a_missing_package():
    """No package ships it, so neither "fetch it" nor "it is in the player
    build" is the right thing to tell a caller."""
    answer = references.explain("unity default resources", engine_archives=(
        "unity default resources", "unity_builtin_extra"))
    assert answer["reason"] == references.ENGINE_ARCHIVE


def test_the_manifest_flag_is_read_under_a_name_that_is_not_builtin():
    """`isBuiltin` on a manifest row and an engine-shipped serialized file are
    different subjects; one word for both is how they get confused."""
    entry = _manifest([("shader/live", True)]).entries["shader/live"]
    assert entry.in_player_build is True
    assert entry.raw["isBuiltin"] is True
