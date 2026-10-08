"""One-shot corpus extraction must release both package and archive caches."""
import gc
from types import SimpleNamespace
import weakref

from core.assets.packages import Package, PackageFile, PackageStore


def test_forget_releases_archive_object_graph():
    store = PackageStore([])
    record = PackageFile("voice", "CAB-voice", [])
    payload = PackageFile("voice", "payload", [])
    record.objects[1] = payload
    reference = weakref.ref(payload)
    store._packages["voice"] = Package("voice", [record], [], [])
    store._archives[record.archive] = record
    del record, payload
    store.forget("voice")
    gc.collect()
    assert not store._packages and not store._archives
    assert reference() is None


def test_forget_does_not_remove_another_packages_archive_binding():
    store = PackageStore([])
    old = PackageFile("old", "CAB-shared-name", [])
    current = PackageFile("current", "CAB-shared-name", [])
    store._packages["old"] = Package("old", [old], [], [])
    store._packages["current"] = Package("current", [current], [], [])
    store._archives[old.archive] = current
    store.forget("old")
    assert store._archives[old.archive] is current
    assert store._packages["current"].files == [current]
    store.forget("absent")
