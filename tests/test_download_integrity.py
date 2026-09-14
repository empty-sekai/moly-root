"""Exercise real HTTP framing, resumptions and retries without game endpoints."""
import hashlib
import gzip
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from core import apk, download
from core.atomic import exclusive_lock
from core.fetch import BundleEntry, Manifest, download_one


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda _: None)


def entry(**fields):
    return BundleEntry.from_dict({"bundleName": "probe", **fields})


def partial():
    return {"body": b"abc", "headers": {"Content-Length": "6", "ETag": '"v1"'}}


def suffix(**headers):
    return {"status": 206, "body": b"def", "headers": {
        "Content-Range": "bytes 3-5/6", "ETag": '"v1"', **headers}}


def test_full_download_without_manifest_hash(http_source, tmp_path):
    base, replies, requests = http_source
    replies.append({"body": b"abcdef"})
    report = download_one(entry(fileSize=999), base, tmp_path, retries=1)
    assert report["bytes"] == 6
    assert report["sha256"] == hashlib.sha256(b"abcdef").hexdigest()
    assert (tmp_path / "probe").read_bytes() == b"abcdef"
    assert requests[0]["accept_encoding"] == "identity"
    assert not (tmp_path / "probe.part").exists()


@pytest.mark.parametrize("second", [suffix(), {"body": b"abcdef"},
                                    {"body": b"NEWNEW", "headers": {"ETag": '"v2"'}}])
def test_interrupted_download_resumes_or_restarts_on_full_response(http_source, tmp_path, second):
    base, replies, requests = http_source
    replies.extend([partial(), second])
    download_one(entry(), base, tmp_path, retries=2)
    assert requests[1]["range"] == "bytes=3-"
    assert requests[1]["if_range"] == '"v1"'
    assert (tmp_path / "probe").read_bytes() == (b"abcdef" if second["body"] == b"def" else second["body"])


@pytest.mark.parametrize("headers", [
    {"Content-Range": "bytes 0-2/6"}, {"Content-Range": "bytes 3-5/9"},
    {"Content-Range": "bytes 3-6/6"}, {"Content-Range": None},
    {"Content-Length": "2"}, {"ETag": '"v2"'}, {"ETag": None},
])
def test_invalid_resumption_never_replaces_the_destination(http_source, tmp_path, headers):
    base, replies, _ = http_source
    (tmp_path / "probe").write_bytes(b"previous complete object")
    replies.extend([partial(), suffix(**headers)])
    with pytest.raises(RuntimeError):
        download_one(entry(), base, tmp_path, retries=2)
    assert (tmp_path / "probe").read_bytes() == b"previous complete object"
    assert not (tmp_path / "probe.part").exists()
    assert not (tmp_path / "probe.part.json").exists()


@pytest.mark.parametrize("reply", [
    {"body": b"abc", "headers": {"Content-Length": "10"}},
    {"body": b"3\r\nabc\r\n", "headers": {"Content-Length": None, "Transfer-Encoding": "chunked"}},
    {"status": 206, "body": b"abc", "headers": {"Content-Range": "bytes 0-2/3"}},
    {"body": b"abc", "headers": {"Content-Length": "invalid"}},
    {"body": b"abc", "headers": {"Content-Encoding": "gzip"}},
])
def test_malformed_or_incomplete_body_is_not_published(http_source, tmp_path, reply):
    base, replies, _ = http_source
    replies.append(reply)
    with pytest.raises(RuntimeError):
        download_one(entry(), base, tmp_path, retries=1)
    assert not (tmp_path / "probe").exists()


def test_hash_failure_retries_from_zero(http_source, tmp_path):
    base, replies, requests = http_source
    replies.extend([{"body": b"BADBAD"}, {"body": b"GOODOK"}])
    download_one(entry(sha256=hashlib.sha256(b"GOODOK").hexdigest()), base, tmp_path, retries=2)
    assert [request["range"] for request in requests] == [None, None]
    assert (tmp_path / "probe").read_bytes() == b"GOODOK"


def test_416_without_a_verified_complete_hash_restarts(http_source, tmp_path):
    base, replies, requests = http_source
    replies.extend([partial(), {"status": 416, "headers": {"Content-Range": "bytes */3"}},
                    {"body": b"abcdef"}])
    download_one(entry(), base, tmp_path, retries=3)
    assert [request["range"] for request in requests] == [None, "bytes=3-", None]
    assert (tmp_path / "probe").read_bytes() == b"abcdef"


def test_416_can_finish_a_complete_part_only_with_size_and_hash(http_source, tmp_path, monkeypatch):
    base, replies, requests = http_source
    target = tmp_path / "probe"
    real_replace = download.os.replace
    failed = False

    def replace(source, destination):
        nonlocal failed
        if Path(destination) == target and not failed:
            failed = True
            raise OSError("injected publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(download.os, "replace", replace)
    replies.extend([{"body": b"abcdef"}, {"status": 416, "headers": {"Content-Range": "bytes */6"}}])
    download_one(entry(sha256=hashlib.sha256(b"abcdef").hexdigest()), base, tmp_path, retries=2)
    assert requests[1]["range"] == "bytes=6-"
    assert target.read_bytes() == b"abcdef"


@pytest.mark.parametrize("change", ["url", "identity", "prefix"])
def test_stale_or_modified_checkpoint_is_discarded(http_source, tmp_path, change):
    base, replies, requests = http_source
    replies.append(partial())
    with pytest.raises(RuntimeError):
        download_one(entry(), base, tmp_path, retries=1)
    assert (tmp_path / "probe.part.json").is_file()
    if change == "prefix":
        (tmp_path / "probe.part").write_bytes(b"BAD")
    replies.append({"body": b"NEWNEW"})
    download_one(entry(crc=2) if change == "identity" else entry(),
                 base + "/new-source" if change == "url" else base, tmp_path, retries=1)
    assert requests[-1]["range"] is None
    assert (tmp_path / "probe").read_bytes() == b"NEWNEW"


def test_unidentified_legacy_part_is_not_resumed(http_source, tmp_path):
    base, replies, requests = http_source
    (tmp_path / "probe.part").write_bytes(b"OLD")
    replies.append({"body": b"new"})
    download_one(entry(), base, tmp_path, retries=1)
    assert requests[0]["range"] is None
    assert (tmp_path / "probe").read_bytes() == b"new"


def test_concurrent_writer_is_rejected_before_network(http_source, tmp_path):
    base, _, requests = http_source
    with exclusive_lock(tmp_path / "probe.lock"):
        with pytest.raises(OSError, match="another writer"):
            download_one(entry(), base, tmp_path, retries=1)
    assert not requests


def test_apk_download_uses_the_same_resume_and_hash_recovery(http_source, tmp_path):
    base, replies, requests = http_source
    replies.extend([{"body": b"BADBAD"}, {"body": b"GOODOK"}])
    target = apk.download(base + "/app.apk", tmp_path / "app.apk",
                          expected_hash="sha256:" + hashlib.sha256(b"GOODOK").hexdigest(), retries=2)
    assert target.read_bytes() == b"GOODOK"
    assert [request["range"] for request in requests] == [None, None]


def test_manifest_rejects_conflicts_and_deduplicates_identical_aliases():
    definition = {"downloadPath": "v1", "cacheFileName": "shared", "dependencies": []}
    manifest = Manifest([dict(definition, bundleName="a/b"), dict(definition, bundleName="a__b")])
    assert len(manifest.entries) == 1
    assert len(manifest.required_bundles(["a/b", "a__b"])) == 1
    with pytest.raises(ValueError, match="conflicting"):
        Manifest([dict(definition, bundleName="a/b"), dict(definition, bundleName="a__b", crc=42)])
    with pytest.raises(ValueError, match="conflicting"):
        Manifest([{"bundleName": "same", "crc": 1}, {"bundleName": "same", "crc": 2}])


@pytest.mark.parametrize("names", [["a/b", "a__b"], ["file", "FILE"],
                                    ["file", "file.part"], ["file", "file.lock"], ["file", "file.part.json"]])
def test_manifest_rejects_ambiguous_local_keys(names):
    with pytest.raises(ValueError):
        Manifest([{"bundleName": name} for name in names])


@pytest.mark.parametrize("name", ["", "a/../b", "/root", "a\\b", "C:escape", "NUL", "a."])
def test_manifest_rejects_nonportable_bundle_names(name):
    with pytest.raises(ValueError):
        Manifest([{"bundleName": name}])


def test_json_object_duplicates_cannot_hide_conflicting_bundle_definitions(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"bundles":{"same":{"crc":1},"same":{"crc":2}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        Manifest.load(manifest)


INVALID_FRAMING = [
    pytest.param([("Content-Length", "3"), ("Content-Length", "6")], b"abcdef", id="conflicting-lengths"),
    pytest.param([("Content-Length", "6"), ("content-length", "6")], b"abcdef", id="identical-lengths-rejected"),
    pytest.param([("Content-Length", "6, 6")], b"abcdef", id="identical-list-rejected"),
    pytest.param([("Content-Length", "3, 6")], b"abcdef", id="conflicting-list"),
    pytest.param([("Content-Length", "6, invalid")], b"abcdef", id="invalid-list"),
    pytest.param([("Transfer-Encoding", "gzip")], gzip.compress(b"abcdef", mtime=0), id="gzip-transfer"),
    pytest.param([("Transfer-Encoding", "gzip, chunked")], b"3\r\nabc\r\n0\r\n\r\n", id="stacked-transfer"),
    pytest.param([("Transfer-Encoding", "chunked"), ("Content-Length", "6")],
                 b"6\r\nabcdef\r\n0\r\n\r\n", id="transfer-and-length"),
    pytest.param([("Transfer-Encoding", "chunked"), ("Transfer-Encoding", "gzip")],
                 b"6\r\nabcdef\r\n0\r\n\r\n", id="repeated-transfer"),
    pytest.param([("Transfer-Encoding", "identity")], b"abcdef", id="unsupported-identity-transfer"),
]


@pytest.mark.parametrize("headers,body", INVALID_FRAMING)
@pytest.mark.parametrize("existing", [False, True])
def test_invalid_http_framing_never_publishes_or_overwrites(http_source, tmp_path, headers, body, existing):
    base, replies, _ = http_source
    target = tmp_path / "probe"
    if existing:
        target.write_bytes(b"PREVIOUS")
    replies.append({"raw_headers": headers, "body": body})
    with pytest.raises(RuntimeError):
        download_one(entry(), base, tmp_path, retries=1)
    if existing:
        assert target.read_bytes() == b"PREVIOUS"
    else:
        assert not target.exists()
    assert not (tmp_path / "probe.part").exists()
    assert not (tmp_path / "probe.part.json").exists()


@pytest.mark.parametrize("headers,body", INVALID_FRAMING)
def test_invalid_http_framing_recovers_with_a_clean_full_retry(http_source, tmp_path, headers, body):
    base, replies, requests = http_source
    replies.extend([{"raw_headers": headers, "body": body}, {"body": b"GOODOK"}])
    download_one(entry(), base, tmp_path, retries=2)
    assert (tmp_path / "probe").read_bytes() == b"GOODOK"
    assert [request["range"] for request in requests] == [None, None]


@pytest.mark.parametrize("coding", ["chunked", "CHUNKED"])
def test_supported_chunked_body_is_decoded_before_publication(http_source, tmp_path, coding):
    base, replies, _ = http_source
    replies.append({"raw_headers": [("Transfer-Encoding", coding)],
                    "body": b"3;extension=yes\r\nabc\r\n3\r\ndef\r\n0\r\nX-Probe: done\r\n\r\n"})
    report = download_one(entry(), base, tmp_path, retries=1)
    assert report["bytes"] == 6
    assert (tmp_path / "probe").read_bytes() == b"abcdef"


def test_conflicting_length_headers_are_rejected_even_with_a_trusted_hash(http_source, tmp_path):
    base, replies, _ = http_source
    replies.append({"raw_headers": [("Content-Length", "3"), ("Content-Length", "6")], "body": b"abcdef"})
    with pytest.raises(RuntimeError):
        download_one(entry(sha256=hashlib.sha256(b"abcdef").hexdigest()), base, tmp_path, retries=1)
    assert not (tmp_path / "probe").exists()


def test_ambiguous_resumption_discards_the_checkpoint_before_retrying(http_source, tmp_path):
    base, replies, requests = http_source
    replies.extend([partial(), {"status": 206, "body": b"def", "raw_headers": [
        ("Content-Range", "bytes 3-5/6"), ("Content-Length", "3"), ("Content-Length", "6"), ("ETag", '"v1"')]},
        {"body": b"abcdef"}])
    download_one(entry(), base, tmp_path, retries=3)
    assert (tmp_path / "probe").read_bytes() == b"abcdef"
    assert [request["range"] for request in requests] == [None, "bytes=3-", None]
