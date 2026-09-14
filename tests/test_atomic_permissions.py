"""POSIX publication permissions, including reads by a separate service UID."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core import atomic
from core.download import download_file
from core.master import Master
from pack.groups import build_groups

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX permission semantics")


@contextmanager
def umask(value):
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o600])
def test_default_replacement_preserves_existing_permission_bits(tmp_path, mode):
    target = tmp_path / "document.json"
    target.write_bytes(b"old")
    target.chmod(mode)
    atomic.write_json(target, {"new": True})
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert json.loads(target.read_bytes()) == {"new": True}


def test_new_private_and_explicit_public_files_have_different_policies(tmp_path):
    with umask(0o077):
        atomic.write_json(tmp_path / "private.json", {"private": True})
        atomic.write_bytes(tmp_path / "public.bin", b"public", mode=atomic.PUBLIC_FILE_MODE)
    assert stat.S_IMODE((tmp_path / "private.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "public.bin").stat().st_mode) == 0o644


def test_mode_is_set_before_replacement_and_failure_preserves_old_file(tmp_path, monkeypatch):
    target = tmp_path / "catalog.json"
    target.write_bytes(b"old")
    target.chmod(0o644)

    def fail(source, destination):
        assert Path(destination) == target
        assert stat.S_IMODE(Path(source).stat().st_mode) == 0o644
        assert target.read_bytes() == b"old"
        raise OSError("injected replace failure")

    monkeypatch.setattr(atomic.os, "replace", fail)
    with pytest.raises(OSError, match="injected"):
        atomic.write_bytes(target, b"new", mode=0o644)
    assert target.read_bytes() == b"old"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert not list(tmp_path.glob("*.tmp"))


def test_cache_and_download_checkpoint_remain_private(http_source, tmp_path):
    base, replies, _ = http_source
    replies.extend([{"body": b'[{"id": 1}]'},
                    {"body": b"abc", "headers": {"Content-Length": "6", "ETag": '"v1"'}}])
    with umask(0o022):
        master = Master(base, cache_dir=tmp_path / "cache")
        master.table("probe")
        with pytest.raises(RuntimeError):
            download_file(base + "/asset", tmp_path / "asset", retries=1)
    cached = tmp_path / "cache" / master.source_id / "probe.json"
    assert stat.S_IMODE(cached.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "asset.part.json").stat().st_mode) == 0o600


def test_another_uid_can_read_new_and_updated_release_objects():
    if os.geteuid() != 0:
        pytest.skip("requires root to launch a reader with another UID")
    with tempfile.TemporaryDirectory(prefix="moly-publication-") as directory, umask(0o022):
        root = Path(directory)
        root.chmod(0o755)
        source, output = root / "source", root / "release"
        source.mkdir()
        (source / "manifest.json").write_text('{"units":[]}', encoding="utf-8")
        (source / "characters.json").write_text('{"version":"v1"}', encoding="utf-8")
        private = root / "private.json"
        atomic.write_json(private, {"private": True})
        reader = r'''
import json, os, pathlib, sys
result = {"uid": os.getuid(), "readable": {}}
for name in sys.argv[1:]:
    try:
        pathlib.Path(name).read_bytes()
        result["readable"][name] = True
    except PermissionError:
        result["readable"][name] = False
print(json.dumps(result))
'''
        for version in ("v1", "v2"):
            (source / "characters.json").write_text(json.dumps({"version": version}), encoding="utf-8")
            build_groups(source, output, version)
            public = [output / "asset-packs.json", *sorted((output / "packages").glob("*.json")),
                      *sorted((output / "catalogs").glob("*.json")),
                      *sorted(path for path in (output / "blobs").rglob("*") if path.is_file())]
            assert all(stat.S_IMODE(path.stat().st_mode) == 0o644 for path in public)
            result = subprocess.run([sys.executable, "-I", "-c", reader, *map(str, public), str(private)],
                                    user=65534, group=65534, extra_groups=[], cwd=root,
                                    capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            observed = json.loads(result.stdout)
            assert observed["uid"] == 65534
            assert all(observed["readable"][str(path)] for path in public)
            assert observed["readable"][str(private)] is False
            if version == "v1":
                # Simulate outputs from the earlier private-publication bug.
                # The unchanged manifest blob must be reused yet made readable.
                (output / "asset-packs.json").chmod(0o600)
                catalog = json.loads((output / "asset-packs.json").read_bytes())
                manifest = json.loads((output / catalog["packages"][0]["manifest"]).read_bytes())
                unchanged = next(entry for entry in manifest["entries"] if entry["path"] == "manifest.json")
                (output / "blobs" / unchanged["blob"]).chmod(0o600)
