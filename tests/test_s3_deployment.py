"""S3 publication safety using only local files and an in-memory fake client."""
import base64
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def s3(monkeypatch):
    directory = Path(__file__).parents[1] / "tools" / "deployment"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("s3_deployment_under_test", directory / "upload_s3.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "create_client", lambda *_: pytest.fail("A real SDK client must never be created"))
    return module


class FakeError(Exception):
    def __init__(self, status, code):
        super().__init__("untrusted service message must not enter receipts")
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": code}}


class FakeS3:
    def __init__(self, objects=None, conditions=("IfMatch", "IfNoneMatch")):
        shape = SimpleNamespace(members=dict.fromkeys(conditions))
        self.meta = SimpleNamespace(service_model=SimpleNamespace(
            operation_model=lambda name: SimpleNamespace(input_shape=shape)))
        self.objects = deepcopy(objects or {})
        self.calls = []
        self.put_error = None
        self.copy_error = None
        self.raced_object = None
        self.versioning = {}
        self.multipart = {"IsTruncated": False}
        self.pages = [{"Contents": [], "IsTruncated": False}]

    def head_object(self, **request):
        self.calls.append(("head", request))
        if request["Key"] not in self.objects:
            raise FakeError(404, "NoSuchKey")
        return deepcopy(self.objects[request["Key"]])

    def put_object(self, **request):
        body = request["Body"].read()
        self.calls.append(("put", {**request, "Body": body}))
        if self.put_error:
            if self.raced_object is not None:
                self.objects[request["Key"]] = deepcopy(self.raced_object)
            raise self.put_error
        self.objects[request["Key"]] = {
            key: deepcopy(request[key]) for key in
            ("ContentLength", "ContentType", "CacheControl", "Metadata", "ContentEncoding") if key in request
        }
        self.objects[request["Key"]]["ETag"] = '"stored-etag"'
        return {"ETag": '"stored-etag"'}

    def copy_object(self, **request):
        self.calls.append(("copy", request))
        if self.copy_error:
            if self.raced_object is not None:
                self.objects[request["Key"]] = deepcopy(self.raced_object)
            raise self.copy_error
        source = self.objects.get(request["CopySource"]["Key"])
        if source is None:
            raise FakeError(404, "NoSuchKey")
        if request.get("CopySourceIfMatch") != source.get("ETag"):
            raise FakeError(412, "PreconditionFailed")
        if request.get("IfNoneMatch") == "*" and request["Key"] in self.objects:
            raise FakeError(412, "PreconditionFailed")
        assert request["MetadataDirective"] == "REPLACE"
        stored = {"ContentLength": source["ContentLength"], "ETag": source["ETag"]}
        stored.update({key: deepcopy(request[key]) for key in
                       ("ContentType", "CacheControl", "Metadata", "ContentEncoding") if key in request})
        # As measured on the publication bucket: a copy keeps the source's
        # Content-Type and Cache-Control whatever the request asks.
        stored.update({key: deepcopy(source[key]) for key in ("ContentType", "CacheControl") if key in source})
        self.objects[request["Key"]] = stored
        return {"CopyObjectResult": {"ETag": source["ETag"]}}

    def get_bucket_versioning(self, **request):
        self.calls.append(("versioning", request))
        if isinstance(self.versioning, Exception):
            raise self.versioning
        return self.versioning

    def list_multipart_uploads(self, **request):
        self.calls.append(("multipart", request))
        if isinstance(self.multipart, Exception):
            raise self.multipart
        return self.multipart

    def list_objects_v2(self, **request):
        self.calls.append(("list", request))
        return deepcopy(self.pages[int(request.get("ContinuationToken", "0"))])


def row_at(tmp_path, logical="snapshots/cn-test/assets/asset.glb", *, encoding="identity", prefix=""):
    raw = b"original scene bytes " * 80
    body = gzip.compress(raw, mtime=0) if encoding == "gzip" else raw
    source = tmp_path / (hashlib.sha256(body).hexdigest() + ".bin")
    source.write_bytes(body)
    return {"path": logical, "objectKey": prefix + logical, "source": str(source),
            "sourceRaw": str(source), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
            "decodedBytes": len(raw), "decodedSha256": hashlib.sha256(raw).hexdigest(),
            "contentEncoding": encoding, "contentType": "model/gltf-binary",
            "cacheControl": "public, max-age=31536000, immutable", "deliveryMode": "fixed"}


def manifest_at(tmp_path, rows, prefix=""):
    path = tmp_path / "upload-manifest.json"
    path.write_text(json.dumps({"schema": "moly-object-upload/1", "objectPrefix": prefix,
        "deliveryMode": "fixed", "release": "stage-test", "snapshots": ["cn-test"], "files": rows}), "utf-8")
    return path


def cli(manifest, *extra):
    return ["--manifest", str(manifest), "--endpoint", "https://storage.example.test",
            "--bucket", "test-assets", "--region", "test-region-1", "--object-prefix", "", *extra]


def head_for(s3, row):
    result = {"ContentLength": row["bytes"], "ContentType": row["contentType"],
              "CacheControl": row["cacheControl"], "Metadata": s3.metadata(row), "ETag": '"old-etag"'}
    if row["contentEncoding"] != "identity":
        result["ContentEncoding"] = row["contentEncoding"]
    return result


def assert_failure(s3, code, call):
    with pytest.raises(s3.SafeFailure) as caught:
        call()
    assert caught.value.code == code


def test_local_dry_run_never_creates_client_or_receipt(s3, tmp_path, capsys):
    manifest = manifest_at(tmp_path, [row_at(tmp_path)])
    before = set(tmp_path.iterdir())
    assert s3.main(cli(manifest)) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["event"] == "local-dry-run"
    assert events[-1]["cloudRequests"] == 0
    assert events[-1]["capacityVerified"] is False
    assert events[0]["destination"]["prefix"] == ""
    assert set(tmp_path.iterdir()) == before


def test_translation_publication_selects_only_its_immutable_objects_or_one_pointer(s3, tmp_path, capsys):
    from pipeline.text_product import build_product
    from prepare_translations import prepare
    source = {'schema': 'moly-text-source/1', 'region': 'jp', 'revision': 'a' * 64,
              'scripts': [{'key': 'sample', 'contents': [], 'lines': [
                  {'id': 'one', 'sourceHash': 'b' * 64, 'kind': 'text', 'step': 0, 'text': 'Hello'}]}]}
    overlay = {'schema': 'moly-text-overlay/1', 'sourceRegion': 'jp', 'sourceRevision': 'a' * 64,
               'locale': 'zh-CN', 'entries': [{'id': 'one', 'sourceHash': 'b' * 64, 'text': 'Welcome', 'source': 'official_cn'}]}
    product = tmp_path / 'product'
    for path, body in build_product(source, [overlay]).items():
        file = product / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(body)
    staging = tmp_path / 'staging'
    prepare(product / 'translations/jp/zh-CN', staging, object_prefix='text/', url_prefix='/moly/')
    manifest = staging / 'upload-manifest.json'
    arguments = cli(manifest)
    arguments[arguments.index('--object-prefix') + 1] = 'text/'
    args = s3.parse_args(arguments)
    _, stable = s3.selection(args)
    assert stable and all('/objects/' in row['path'] or '/revisions/' in row['path'] for row in stable)
    assert s3.main(arguments) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['cloudRequests'] == 0
    args = s3.parse_args(arguments + ['--scope', 'mutable', '--allow-mutable-update',
                                    '--mutable-path', 'translations/jp/zh-CN/latest.json'])
    _, pointers = s3.selection(args)
    assert [row['path'] for row in pointers] == ['translations/jp/zh-CN/latest.json']
    doc = json.loads(manifest.read_bytes())
    doc['files'].append(row_at(tmp_path, 'snapshots/unrelated/assets/x.bin', prefix='text/'))
    manifest.write_text(json.dumps(doc), encoding='utf-8')
    assert_failure(s3, 'translation_manifest_contains_unrelated_object', lambda: s3.selection(args))


def test_s3_selection_allows_literal_object_key_delimiters(s3, tmp_path):
    row = row_at(tmp_path, "snapshots/cn-test/assets/atlas/balloon#40.png")
    manifest = manifest_at(tmp_path, [row])
    args = s3.parse_args(cli(manifest))
    assert s3.selection(args)[1][0]["objectKey"].endswith("balloon#40.png")


def test_create_is_conditional_and_preserves_encoded_identity(s3, tmp_path):
    for encoding in ("identity", "gzip"):
        row = row_at(tmp_path, encoding=encoding)
        args = s3.parse_args(cli(manifest_at(tmp_path, [row])))
        client = FakeS3()
        assert s3.put_one(client, s3.destination(args), row, args)["status"] == "uploaded"
        request = next(value for kind, value in client.calls if kind == "put")
        assert request["IfNoneMatch"] == "*" and "IfMatch" not in request
        assert "ACL" not in request and ("ContentEncoding" in request) == (encoding != "identity")
        assert request.get("ContentEncoding", "identity") == encoding
        assert request["Metadata"] == s3.metadata(row)
        assert len(request["Body"]) == request["ContentLength"] == row["bytes"]
        assert hashlib.sha256(request["Body"]).hexdigest() == row["sha256"]
        assert request["ContentMD5"] == base64.b64encode(hashlib.md5(request["Body"], usedforsecurity=False).digest()).decode()
        assert s3.put_one(client, s3.destination(args), row, args)["status"] == "verified-skip"
        assert sum(kind == "put" for kind, _ in client.calls) == 1
        client.objects[row["objectKey"]]["ContentType"] = "application/octet-stream"
        assert_failure(s3, "immutable_object_conflict", lambda: s3.put_one(client, s3.destination(args), row, args))
    Path(row["source"]).write_bytes(b"changed after planning")
    client = FakeS3()
    assert_failure(s3, "local_upload_body_changed", lambda: s3.put_one(client, s3.destination(args), row, args))
    assert all(kind != "put" for kind, _ in client.calls)


def test_mutable_update_requires_previous_digest_and_exact_etag(s3, tmp_path):
    row = row_at(tmp_path, "cache-worker.mjs")
    previous = "a" * 64
    args = s3.parse_args(cli(manifest_at(tmp_path, [row]), "--scope", "mutable",
        "--mutable-path", "cache-worker.mjs", "--allow-mutable-update", "--expect-previous-sha256", previous))
    target = s3.destination(args)
    existing = head_for(s3, row)
    existing["Metadata"]["moly-sha256"] = previous
    client = FakeS3({row["objectKey"]: existing})
    assert s3.put_one(client, target, row, args)["status"] == "uploaded"
    request = next(value for kind, value in client.calls if kind == "put")
    assert request["IfMatch"] == '"old-etag"' and "IfNoneMatch" not in request
    for old, code in (({**existing, "Metadata": {"moly-sha256": "b" * 64}}, "mutable_previous_hash_not_confirmed"),
                      ({k: v for k, v in existing.items() if k != "ETag"}, "mutable_etag_missing")):
        client = FakeS3({row["objectKey"]: old})
        assert_failure(s3, code, lambda: s3.put_one(client, target, row, args))
        assert all(kind != "put" for kind, _ in client.calls)


class TranscodingCdn(FakeS3):
    """As measured in front of the publication bucket: asked for br or gzip,
    the CDN compresses an identity-stored body itself and answers with a weak
    ETag and no length. The origin checks If-Match against the stored ETag."""
    def __init__(self, module, objects):
        super().__init__(objects)
        self.module = module

    def head_object(self, **request):
        stored = super().head_object(**request)
        if getattr(self.module._stored_encoding, "value", None) != "identity" and "ContentEncoding" not in stored:
            stored = {**stored, "ETag": "W/" + stored["ETag"], "ContentEncoding": "br"}
            stored.pop("ContentLength", None)
        return stored

    def put_object(self, **request):
        current = self.objects.get(request["Key"])
        if "IfMatch" in request and (current is None or request["IfMatch"] != current["ETag"]):
            raise FakeError(412, "PreconditionFailed")
        return super().put_object(**request)


def test_mutable_replace_that_changes_encoding_matches_the_stored_etag(s3, tmp_path):
    row = row_at(tmp_path, "cache-worker.mjs", encoding="gzip")
    previous = "a" * 64
    args = s3.parse_args(cli(manifest_at(tmp_path, [row]), "--scope", "mutable",
        "--mutable-path", "cache-worker.mjs", "--allow-mutable-update", "--expect-previous-sha256", previous))
    target = s3.destination(args)
    stored = {"ContentLength": 23557, "ContentType": "text/javascript", "ETag": '"old-etag"',
              "CacheControl": "public, max-age=60, must-revalidate", "Metadata": {"moly-sha256": previous}}
    client = TranscodingCdn(s3, {row["objectKey"]: stored})
    assert s3.put_one(client, target, row, args)["status"] == "uploaded"
    request = next(value for kind, value in client.calls if kind == "put")
    assert request["IfMatch"] == '"old-etag"'
    # The previous digest is confirmed on the untransformed read too.
    client = TranscodingCdn(s3, {row["objectKey"]: {**stored, "Metadata": {"moly-sha256": "b" * 64}}})
    assert_failure(s3, "mutable_previous_hash_not_confirmed", lambda: s3.put_one(client, target, row, args))
    assert all(kind != "put" for kind, _ in client.calls)


def test_conditional_conflicts_never_retry_without_condition(s3, tmp_path):
    row = row_at(tmp_path)
    args = s3.parse_args(cli(manifest_at(tmp_path, [row])))
    for status in (409, 412):
        for same_body in (False, True):
            client = FakeS3()
            client.put_error = FakeError(status, "ConditionalRequestConflict")
            client.raced_object = head_for(s3, row)
            if not same_body:
                client.raced_object["Metadata"]["moly-sha256"] = "b" * 64
            if same_body:
                assert s3.put_one(client, s3.destination(args), row, args)["status"] == "verified-skip"
            else:
                with pytest.raises(FakeError) as caught:
                    s3.put_one(client, s3.destination(args), row, args)
                assert caught.value is client.put_error
            puts = [value for kind, value in client.calls if kind == "put"]
            assert len(puts) == 1 and puts[0]["IfNoneMatch"] == "*"


def test_unsupported_sdk_or_service_conditions_fail_closed(s3, tmp_path):
    row = row_at(tmp_path)
    args = s3.parse_args(cli(manifest_at(tmp_path, [row])))
    for conditions in (("IfMatch",), ("IfNoneMatch",)):
        client = FakeS3(conditions=conditions)
        assert_failure(s3, "sdk_lacks_conditional_put", lambda: s3.put_one(client, s3.destination(args), row, args))
        assert client.calls == []
    client = FakeS3()
    client.put_error = FakeError(501, "NotImplemented")
    with pytest.raises(FakeError):
        s3.put_one(client, s3.destination(args), row, args)
    assert sum(kind == "put" for kind, _ in client.calls) == 1
    assert s3.sanitized_error(client.put_error) == {"code": "FakeError", "s3Code": "NotImplemented", "httpStatus": 501}


def test_capacity_is_whole_bucket_encoded_and_incomplete_plans_cannot_execute(s3, tmp_path, monkeypatch):
    row = row_at(tmp_path, encoding="gzip")
    manifest = manifest_at(tmp_path, [row])
    args = s3.parse_args(cli(manifest))
    client = FakeS3()
    client.pages = [{"Contents": [{"Key": "unrelated-prefix/other.bin", "Size": 100}],
                     "IsTruncated": True, "NextContinuationToken": "1"},
                    {"Contents": [{"Key": "another-prefix/other.bin", "Size": 25}], "IsTruncated": False}]
    plan = s3.inspect_capacity(client, s3.destination(args), [row])
    assert plan["currentObjectBytes"] == 125 and plan["objects"] == 2
    assert plan["additionalBytesUpperBound"] == row["bytes"] < row["decodedBytes"]
    assert plan["projectedBytesUpperBound"] == 125 + row["bytes"]
    requests = [value for kind, value in client.calls if kind == "list"]
    assert all("Prefix" not in request for request in requests)
    assert requests[1]["ContinuationToken"] == "1"
    client.objects[row["objectKey"]] = head_for(s3, row)
    client.pages = [{"Contents": [{"Key": row["objectKey"], "Size": row["bytes"]}],
                    "IsTruncated": False}]
    assert s3.inspect_capacity(client, s3.destination(args), [row])["additionalBytesUpperBound"] == 0
    for versioning, multipart in (({"Status": "Enabled"}, {}), ({"Status": "Suspended"}, {}),
                                  (FakeError(403, "AccessDenied"), {}), ({}, {"Uploads": [{"Key": "unfinished"}]}),
                                  ({}, FakeError(403, "AccessDenied"))):
        blocked = FakeS3()
        blocked.versioning, blocked.multipart = versioning, multipart
        monkeypatch.setattr(s3, "create_client", lambda _, client=blocked: client)
        assert_failure(s3, "capacity_unverified_or_exceeded", lambda: s3.main(cli(manifest, "--execute", "--exclusive-writer")))
        assert all(kind != "put" for kind, _ in blocked.calls)
    conflict = {**row, "path": "snapshots/cn-test/assets/zz-conflict.glb",
                "objectKey": "snapshots/cn-test/assets/zz-conflict.glb"}
    existing = head_for(s3, conflict)
    existing["Metadata"]["moly-sha256"] = "b" * 64
    blocked = FakeS3({conflict["objectKey"]: existing})
    blocked.pages = [{"Contents": [{"Key": conflict["objectKey"], "Size": conflict["bytes"]}],
                     "IsTruncated": False}]
    manifest_at(tmp_path, [row, conflict])
    monkeypatch.setattr(s3, "create_client", lambda _: blocked)
    assert_failure(s3, "immutable_object_conflicts", lambda: s3.main(cli(manifest, "--execute", "--exclusive-writer")))
    assert all(kind != "put" for kind, _ in blocked.calls), "Earlier absent objects must not upload before the whole-scope conflict check"
    manifest_at(tmp_path, [row])
    monkeypatch.setattr(s3, "CAPACITY", row["bytes"] - 1)
    over = FakeS3()
    monkeypatch.setattr(s3, "create_client", lambda _: over)
    assert_failure(s3, "capacity_unverified_or_exceeded", lambda: s3.main(cli(manifest, "--execute", "--exclusive-writer")))
    assert all(kind != "put" for kind, _ in over.calls)


def test_receipts_are_destination_bound_and_never_replace_remote_verification(s3, tmp_path):
    row = row_at(tmp_path)
    manifest = manifest_at(tmp_path, [row])
    args = s3.parse_args(cli(manifest))
    target = s3.destination(args)
    identity = s3.publication_identity(target, manifest)
    default = s3.receipt_path(args, identity)
    for field in ("endpoint", "bucket", "region", "prefix", "addressingStyle", "manifestSha256"):
        assert s3.receipt_path(args, {**identity, field: identity[field] + "different"}) != default
    args.receipt = tmp_path / "receipt.jsonl"
    previous = json.dumps({"destination": {**identity, "bucket": "different"}}).encode() + b"\n"
    args.receipt.write_bytes(previous)
    client = FakeS3()
    assert_failure(s3, "receipt_destination_or_publication_mismatch", lambda: s3.execute(client, target, [row], args, identity))
    assert client.calls == [] and args.receipt.read_bytes() == previous
    assert not args.receipt.with_suffix(".jsonl.lock").exists()
    args.receipt.write_text(json.dumps({"destination": identity, "status": "uploaded", "key": row["objectKey"]}) + "\n", "utf-8")
    existing = head_for(s3, row)
    existing["Metadata"]["moly-sha256"] = "b" * 64
    client = FakeS3({row["objectKey"]: existing})
    assert s3.execute(client, target, [row], args, identity) == 1
    assert client.calls[0][0] == "head" and all(kind != "put" for kind, _ in client.calls)
    torn = b'{"status": "uploa'
    args.receipt.write_bytes(torn)
    client = FakeS3({row["objectKey"]: head_for(s3, row)})
    assert s3.execute(client, target, [row], args, identity) == 0
    assert args.receipt.read_bytes().startswith(torn + b"\n")
    assert s3.execute(client, target, [row], args, identity) == 0
    assert sum(kind == "head" for kind, _ in client.calls) == 2
    assert all(kind != "put" for kind, _ in client.calls)


def test_selection_and_destination_reject_ambiguous_paths_and_lengths(s3, tmp_path):
    row = row_at(tmp_path)
    manifest = manifest_at(tmp_path, [row])
    args = s3.parse_args(cli(manifest))
    assert s3.selection(args)[1] == [row]
    for logical in ("snapshots/cn-test/assets/./a", "snapshots/cn-test/assets//a", "../a", "/a", "a%2fb", "a?b", "a\\b", "a\nb"):
        manifest_at(tmp_path, [{**row, "path": logical, "objectKey": logical}])
        assert_failure(s3, "unsafe_object_path", lambda: s3.selection(args))
    for field in ("bytes", "decodedBytes"):
        manifest_at(tmp_path, [{**row, field: True}])
        assert_failure(s3, "invalid_object_length", lambda: s3.selection(args))
    manifest_at(tmp_path, [row, row])
    assert_failure(s3, "object_key_mismatch_or_duplicate", lambda: s3.selection(args))
    manifest_at(tmp_path, [{**row, "bytes": s3.MAX_PUT + 1}])
    assert_failure(s3, "single_put_limit_exceeded", lambda: s3.selection(args))
    for prefix in ("/", "a//", "a/../", "a/%2f/", "a\\b/"):
        assert_failure(s3, "invalid_object_prefix", lambda: s3.destination(SimpleNamespace(**{**vars(args), "object_prefix": prefix})))
    assert_failure(s3, "endpoint_must_be_an_explicit_https_origin_without_bucket_path", lambda: s3.destination(
        SimpleNamespace(**{**vars(args), "endpoint": "https://storage.example.test/bucket/"})))


COPY_CONDITIONS = ("IfMatch", "IfNoneMatch", "CopySourceIfMatch", "MetadataDirective")


def stored_source(s3, row):
    """The object an earlier upload of the row left: single-part ETag = MD5 of the body."""
    body = Path(row["source"]).read_bytes()
    return {**head_for(s3, row), "ETag": '"%s"' % hashlib.md5(body, usedforsecurity=False).hexdigest()}


def copy_setup(s3, tmp_path, encoding="identity"):
    old = row_at(tmp_path, "snapshots/cn-old/assets/asset.glb", encoding=encoding)
    new = {**old, "path": "snapshots/cn-new/assets/asset.glb", "objectKey": "snapshots/cn-new/assets/asset.glb"}
    previous = tmp_path / "previous-upload-manifest.json"
    previous.write_text(json.dumps({"schema": "moly-object-upload/1", "objectPrefix": "",
        "snapshots": ["cn-old"], "files": [old]}), "utf-8")
    manifest = tmp_path / "upload-manifest.json"
    manifest.write_text(json.dumps({"schema": "moly-object-upload/1", "objectPrefix": "",
        "deliveryMode": "fixed", "release": "stage-test", "snapshots": ["cn-new"], "files": [new]}), "utf-8")
    args = s3.parse_args(cli(manifest, "--copy-source-manifest", str(previous)))
    return old, new, args, s3.load_copy_sources(args.copy_source_manifest, "")


def test_absent_identical_objects_are_copied_with_conditions_and_verified(s3, tmp_path, capsys):
    for encoding in ("identity", "gzip"):
        old, new, args, sources = copy_setup(s3, tmp_path, encoding)
        client = FakeS3({old["objectKey"]: stored_source(s3, old)}, conditions=COPY_CONDITIONS)
        result = s3.put_one(client, s3.destination(args), new, args, known_absent=True, copy_sources=sources)
        assert result["status"] == "copied" and result["source"] == old["objectKey"]
        assert all(kind not in {"put", "head"} or value["Key"] in {new["objectKey"], old["objectKey"]}
                   for kind, value in client.calls)
        request = next(value for kind, value in client.calls if kind == "copy")
        assert request["Key"] == new["objectKey"] and request["CopySource"]["Key"] == old["objectKey"]
        assert request["IfNoneMatch"] == "*" and request["CopySourceIfMatch"] == client.objects[old["objectKey"]]["ETag"]
        assert request["MetadataDirective"] == "REPLACE" and request["Metadata"] == s3.metadata(new)
        assert request.get("ContentEncoding", "identity") == encoding and "ACL" not in request
        assert s3.matches(client.objects[new["objectKey"]], new)
    assert s3.main(cli(tmp_path / "upload-manifest.json", "--copy-source-manifest",
                       str(tmp_path / "previous-upload-manifest.json"))) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]["copyCandidates"] == 1 and events[0]["copyCandidateBytes"] == new["bytes"]


def test_a_source_that_is_not_the_same_bytes_is_uploaded_instead(s3, tmp_path):
    old, new, args, sources = copy_setup(s3, tmp_path)
    # Other bytes (or a provider whose ETag is not the body MD5) fail the source condition.
    for stored in ({**stored_source(s3, old), "ETag": '"other-bytes"'}, None):
        client = FakeS3({old["objectKey"]: stored} if stored else {}, conditions=COPY_CONDITIONS)
        assert s3.put_one(client, s3.destination(args), new, args, known_absent=True, copy_sources=sources)["status"] == "uploaded"
        assert sum(kind == "copy" for kind, _ in client.calls) == (1 if stored else 0)
        assert s3.matches(client.objects[new["objectKey"]], new)
    # A copy would keep the source's headers: a source stored with other
    # headers is uploaded over, and one listed with other headers is no candidate.
    client = FakeS3({old["objectKey"]: {**stored_source(s3, old), "CacheControl": "no-store"}}, conditions=COPY_CONDITIONS)
    assert s3.put_one(client, s3.destination(args), new, args, known_absent=True, copy_sources=sources)["status"] == "uploaded"
    assert all(kind != "copy" for kind, _ in client.calls)
    assert client.objects[new["objectKey"]]["CacheControl"] == new["cacheControl"]
    assert s3.content_identity({**new, "cacheControl": new["cacheControl"] + ", no-transform"}) not in sources
    Path(new["source"]).write_bytes(b"changed after planning")
    client = FakeS3({old["objectKey"]: stored_source(s3, old)}, conditions=COPY_CONDITIONS)
    assert_failure(s3, "local_upload_body_changed", lambda: s3.put_one(
        client, s3.destination(args), new, args, known_absent=True, copy_sources=sources))
    assert all(kind not in {"put", "copy"} for kind, _ in client.calls)


def test_copy_races_and_missing_sdk_conditions_fail_closed(s3, tmp_path):
    old, new, args, sources = copy_setup(s3, tmp_path)
    for same_body in (True, False):
        client = FakeS3({old["objectKey"]: stored_source(s3, old)}, conditions=COPY_CONDITIONS)
        client.copy_error = FakeError(412, "PreconditionFailed")
        client.raced_object = head_for(s3, new)
        if not same_body:
            client.raced_object["Metadata"]["moly-sha256"] = "b" * 64
            client.put_error = FakeError(412, "PreconditionFailed")
            # The racing object is not this row: the conditional create refuses it.
            with pytest.raises(FakeError):
                s3.put_one(client, s3.destination(args), new, args, known_absent=True, copy_sources=sources)
            assert [value["IfNoneMatch"] for kind, value in client.calls if kind == "put"] == ["*"]
        else:
            assert s3.put_one(client, s3.destination(args), new, args, known_absent=True,
                              copy_sources=sources)["status"] == "verified-skip"
            assert all(kind != "put" for kind, _ in client.calls)
    client = FakeS3({old["objectKey"]: stored_source(s3, old)})
    assert_failure(s3, "sdk_lacks_conditional_copy", lambda: s3.put_one(
        client, s3.destination(args), new, args, known_absent=True, copy_sources=sources))
    assert all(kind not in {"put", "copy"} for kind, _ in client.calls)


def test_mutable_objects_are_never_copy_sources(s3, tmp_path):
    worker = row_at(tmp_path, "cache-worker.mjs")
    previous = tmp_path / "previous-upload-manifest.json"
    previous.write_text(json.dumps({"schema": "moly-object-upload/1", "objectPrefix": "",
        "snapshots": ["cn-old"], "files": [worker]}), "utf-8")
    assert s3.load_copy_sources([previous], "") == {}
    previous.write_text(json.dumps({"schema": "moly-object-upload/1", "objectPrefix": "other/",
        "snapshots": ["cn-old"], "files": []}), "utf-8")
    assert_failure(s3, "copy_source_manifest_schema_or_prefix_mismatch", lambda: s3.load_copy_sources([previous], ""))


def test_transport_errors_without_a_service_response_keep_their_identity(s3, tmp_path):
    class TransportError(Exception):
        response = None
    row = row_at(tmp_path, "snapshots/cn-test/assets/atlas.png")
    args = s3.parse_args(cli(manifest_at(tmp_path, [row])))
    client = FakeS3()
    failure = TransportError("connection reset")
    client.head_object = lambda **request: (_ for _ in ()).throw(failure)
    with pytest.raises(TransportError) as caught:
        s3.put_one(client, s3.destination(args), row, args)
    assert caught.value is failure
    client = FakeS3()
    client.put_error = failure
    with pytest.raises(TransportError) as caught:
        s3.put_one(client, s3.destination(args), row, args, known_absent=True)
    assert caught.value is failure
    assert s3.sanitized_error(failure) == {"code": "TransportError"}
