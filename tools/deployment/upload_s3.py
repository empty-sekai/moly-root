"""Bounded S3-compatible publication adapter; local dry-run is the default.

No delete, ACL or multipart operations. --inspect performs signed reads only.
--execute requires complete capacity accounting and an exclusive publisher.
--copy-source-manifest lets an absent object be created by a server-side copy
of a byte-identical object an earlier publication already put in the bucket.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlsplit

import upload_oss as common

SafeFailure = common.SafeFailure
CAPACITY = 30 * 1024**3
MAX_PUT = 5 * 1024**3
UPLOAD_CHUNK = 64 * 1024


class Throttle:
    """One byte budget shared by every upload worker, so the process as a
    whole sends at most `rate` body bytes per second. `heartbeat`, when given,
    is touched about every 10 s while bytes move (a machine-wide upload lock
    reads it to tell a live holder from a dead one)."""

    def __init__(self, rate, heartbeat=None):
        self.rate = rate
        self.heartbeat = heartbeat
        self.lock = threading.Lock()
        self.next = time.monotonic()
        self.touched = 0.0

    def take(self, size):
        with self.lock:
            now = time.monotonic()
            start = max(self.next, now)
            self.next = start + size / self.rate
            touch = self.heartbeat is not None and now - self.touched >= 10
            if touch:
                self.touched = now
        if touch:
            Path(self.heartbeat).touch()
        if start > now:
            time.sleep(start - now)


class ThrottledBody:
    """A request body read through a shared `Throttle`; seeking and position
    are the file's, so an SDK retry rewinds it as usual."""

    def __init__(self, stream, throttle):
        self.stream = stream
        self.throttle = throttle

    def read(self, size=-1):
        if size is None or size < 0:
            parts = []
            while block := self.read(UPLOAD_CHUNK):
                parts.append(block)
            return b''.join(parts)
        block = self.stream.read(min(size, UPLOAD_CHUNK))
        if block:
            self.throttle.take(len(block))
        return block

    def seek(self, *args):
        return self.stream.seek(*args)

    def tell(self):
        return self.stream.tell()


THROTTLE = None


def destination(args):
    endpoint = urlsplit(args.endpoint)
    if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.path not in {'', '/'} or endpoint.query or endpoint.fragment):
        raise SafeFailure('endpoint_must_be_an_explicit_https_origin_without_bucket_path')
    if (not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', args.bucket)
            or '..' in args.bucket or '.-' in args.bucket or '-.' in args.bucket):
        raise SafeFailure('invalid_bucket')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', args.region):
        raise SafeFailure('explicit_signing_region_required')
    prefix = args.object_prefix
    if prefix and (not prefix.endswith('/') or any(p in {'', '.', '..'} for p in prefix[:-1].split('/'))
                   or any(ord(c) < 32 or c in '\\:%?#' for c in prefix)):
        raise SafeFailure('invalid_object_prefix')
    return {'provider': 's3', 'endpoint': endpoint._replace(path='', query='', fragment='').geturl(),
            'bucket': args.bucket, 'region': args.region, 'prefix': prefix,
            'addressingStyle': args.addressing_style}


def sanitized_error(error):
    if isinstance(error, SafeFailure):
        return {'code': error.code, **error.details}
    response = getattr(error, 'response', {}) or {}
    code = response.get('Error', {}).get('Code', '')
    status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
    return {'code': type(error).__name__,
            **({'s3Code': code} if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', code) else {}),
            **({'httpStatus': status} if type(status) is int else {})}


def create_client(args):
    # Imported and instantiated only for an explicitly requested cloud read/write.
    for name in ('boto3', 'botocore', 'urllib3'):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.CRITICAL)
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise SafeFailure('s3_sdk_not_installed', hint='Install boto3 locally; no credentials are accepted as CLI arguments.') from None
    client = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        's3', endpoint_url=args.endpoint, region_name=args.region,
        config=Config(signature_version='s3v4', s3={'addressing_style': args.addressing_style},
                      connect_timeout=args.timeout, read_timeout=args.timeout,
                      # One pooled connection per upload worker; a smaller pool
                      # opens a new TLS connection for every request above it.
                      max_pool_connections=max(10, getattr(args, 'threads', 10)),
                      request_checksum_calculation='when_required',
                      response_checksum_validation='when_required',
                      # Transport errors, throttling and 5xx are retried with the
                      # request unchanged, conditions included; a create that
                      # landed before its response was lost then reads as 412.
                      retries={'total_max_attempts': 3, 'mode': 'standard'}))
    # The SDK asks for identity. A CDN in front of the endpoint may then decode a
    # compressed object and answer HEAD without its length, encoding, metadata
    # and strong ETag, so every uploaded compressed object would fail
    # verification. Ask for the stored representation instead; for an object
    # stored as identity, asking for br/gzip lets the CDN compress it on the fly.
    for operation in ('HeadObject', 'GetObject'):
        client.meta.events.register(f'before-send.s3.{operation}', accept_stored_encoding)
    return client


_stored_encoding = threading.local()


def accept_stored_encoding(request, **_):
    stored = getattr(_stored_encoding, 'value', None)
    request.headers['Accept-Encoding'] = 'identity' if stored == 'identity' else 'br, gzip'
    # Added after signing: a CDN filling its cache may drop the range, and an
    # unsigned header leaves the signature valid either way.
    if getattr(_stored_encoding, 'first_byte', False) and request.method == 'GET':
        request.headers['Range'] = 'bytes=0-0'


def require_conditions(client):
    members = client.meta.service_model.operation_model('PutObject').input_shape.members
    if not {'IfMatch', 'IfNoneMatch'}.issubset(members):
        raise SafeFailure('sdk_lacks_conditional_put', hint='Upgrade boto3; unconditional fallback is forbidden.')


def require_copy_conditions(client):
    members = client.meta.service_model.operation_model('CopyObject').input_shape.members
    if not {'IfNoneMatch', 'CopySourceIfMatch', 'MetadataDirective'}.issubset(members):
        raise SafeFailure('sdk_lacks_conditional_copy', hint='Upgrade boto3 or publish without --copy-source-manifest.')


def content_identity(row):
    """The stored bytes and the headers a copy carries over. The provider of
    the publication bucket keeps the source's Content-Type and Cache-Control on
    a copy even with MetadataDirective REPLACE (every copy whose headers
    differed kept the source's), so only a source listed with the row's own
    headers is a candidate."""
    return (row['sha256'], row['bytes'], row['contentEncoding'], row['contentType'], row['cacheControl'])


def load_copy_sources(paths, object_prefix):
    """Map a content identity to the key of an immutable object an earlier
    publication listed. A listed object is only a candidate: the copy is
    conditional on the stored source holding exactly the row's bytes."""
    sources = {}
    for path in paths:
        try:
            doc = json.loads(Path(path).read_bytes())
        except Exception as exc:
            raise SafeFailure('cannot_read_copy_source_manifest', errorType=type(exc).__name__) from None
        if doc.get('schema') != 'moly-object-upload/1' or doc.get('objectPrefix') != object_prefix:
            raise SafeFailure('copy_source_manifest_schema_or_prefix_mismatch')
        for row in doc.get('files', []):
            key, logical = row.get('objectKey'), row.get('path')
            if (not isinstance(key, str) or not isinstance(logical, str)
                    or key != object_prefix + logical or not logical or logical.startswith('/')
                    or any(part in {'', '.', '..'} for part in logical.split('/'))):
                raise SafeFailure('copy_source_manifest_invalid_key')
            if logical in common.MUTABLE:
                continue
            try:
                identity = content_identity(row)
            except KeyError:
                raise SafeFailure('copy_source_manifest_incomplete_row', path=logical) from None
            if not common.SHA256.fullmatch(str(identity[0])) or type(identity[1]) is not int:
                raise SafeFailure('copy_source_manifest_invalid_hash', path=logical)
            sources.setdefault(identity, key)
    return sources


def head(client, target, key, encoding=None):
    """Stored headers of `key`, read with the Accept-Encoding of `encoding`,
    the representation the manifest expects there."""
    _stored_encoding.value = encoding
    try:
        return stored_headers(client, target, key)
    finally:
        _stored_encoding.value = None


def stored_headers(client, target, key):
    try:
        return client.head_object(Bucket=target['bucket'], Key=key)
    except Exception as error:
        status, code = error_status(error)
        if status == 404 and code in {'404', 'NoSuchKey', 'NotFound'}:
            return None
        if status != 403:
            raise
    # A CDN in front of the endpoint may forward a HEAD for a cacheable file
    # type as a GET. The signature covers the method, so that HEAD fails as 403,
    # also for an absent object. A signed GET keeps its method. It asks for the
    # first byte only; the stored length is then the total of its Content-Range.
    _stored_encoding.first_byte = True
    try:
        response = client.get_object(Bucket=target['bucket'], Key=key)
    except Exception as error:
        status, code = error_status(error)
        if status == 404 and code in {'404', 'NoSuchKey', 'NotFound'}:
            return None
        if status != 416 or code not in {'416', 'InvalidRange'}:
            raise
        # An empty object has no first byte to select.
        _stored_encoding.first_byte = False
        response = client.get_object(Bucket=target['bucket'], Key=key)
    finally:
        _stored_encoding.first_byte = False
    body = response.pop('Body', None)
    if body is not None:
        body.close()
    content_range = response.pop('ContentRange', None)
    if content_range is not None:
        total = content_range.rpartition('/')[2]
        if not total.isdigit():
            raise SafeFailure('stored_object_length_unknown', key=key)
        response['ContentLength'] = int(total)
    return response


def error_status(error):
    # Transport errors carry no service response; some set the attribute to None.
    response = getattr(error, 'response', None) or {}
    return response.get('ResponseMetadata', {}).get('HTTPStatusCode'), response.get('Error', {}).get('Code')


def metadata(row):
    return {'moly-sha256': row['sha256'], 'moly-decoded-sha256': row['decodedSha256'],
            'moly-decoded-bytes': str(row['decodedBytes'])}


def matches(existing, row, *, allow_provider_normalization=True):
    encoding_matches = existing is not None and (
        existing.get('ContentEncoding', 'identity') == row['contentEncoding'] or
        (allow_provider_normalization and row['contentEncoding'] != 'identity' and
         'ContentEncoding' not in existing and existing.get('ContentLength') == row['bytes'])
    )
    return (existing is not None and existing.get('ContentLength') == row['bytes']
            and existing.get('ContentType') == row['contentType']
            and existing.get('CacheControl') == row['cacheControl']
            and encoding_matches
            and all(existing.get('Metadata', {}).get(k) == v for k, v in metadata(row).items()))


def selection(args):
    doc, rows = common.load_selection(args, object_prefix=args.object_prefix,
        schemas=('moly-object-upload/1', 'moly-oss-upload/1'))
    if any(row['bytes'] > MAX_PUT for row in rows):
        raise SafeFailure('single_put_limit_exceeded', maximumBytes=MAX_PUT)
    return doc, rows


def inspect_capacity(client, target, rows, *, mutable=False, known_existing_keys=None, absent_keys=None):
    """Current encoded object bytes, all bucket prefixes; never decoded lengths.

    `absent_keys`, when given, receives the selected keys the complete listing
    does not contain."""
    incomplete = []
    try:
        if client.get_bucket_versioning(Bucket=target['bucket']).get('Status'):
            incomplete.append('versioned_or_suspended_bucket')
    except Exception:
        incomplete.append('versioning_state_unverified')
    try:
        multipart = client.list_multipart_uploads(Bucket=target['bucket'], MaxUploads=1)
        if type(multipart.get('IsTruncated')) is not bool:
            incomplete.append('multipart_listing_completeness_unverified')
        elif multipart.get('Uploads') or multipart.get('IsTruncated'):
            incomplete.append('multipart_storage_present')
    except Exception:
        incomplete.append('multipart_storage_unverified')
    used = count = 0
    listed_keys = set()
    continuation = None
    for _ in range(10000):
        response = client.list_objects_v2(Bucket=target['bucket'], MaxKeys=1000,
            **({'ContinuationToken': continuation} if continuation else {}))
        if type(response.get('IsTruncated')) is not bool:
            raise SafeFailure('bucket_listing_completeness_unverified')
        for item in response.get('Contents', []):
            size = item.get('Size')
            if type(size) is not int or size < 0:
                raise SafeFailure('invalid_bucket_object_size')
            used += size
            count += 1
            key = item.get('Key')
            if not isinstance(key, str) or not key:
                raise SafeFailure('invalid_bucket_object_key')
            listed_keys.add(key)
        if not response.get('IsTruncated'):
            break
        token = response.get('NextContinuationToken')
        if not token or token == continuation:
            raise SafeFailure('invalid_bucket_listing_cursor')
        continuation = token
    else:
        raise SafeFailure('bucket_listing_page_limit')
    additional = transfer = existing_count = 0
    conflicts = []
    conflict_count = 0
    for row in rows:
        if known_existing_keys and row['objectKey'] in known_existing_keys:
            existing_count += 1
            continue
        # A complete bucket listing is authoritative for object absence.
        # Avoid one read round-trip per selected object absent from it.
        existing = (head(client, target, row['objectKey'], row['contentEncoding'])
                    if row['objectKey'] in listed_keys else None)
        if existing is None and absent_keys is not None and row['objectKey'] not in listed_keys:
            absent_keys.add(row['objectKey'])
        if existing is None:
            additional += row['bytes']
            transfer += row['bytes']
        elif matches(existing, row):
            existing_count += 1
        else:
            if not mutable:
                conflict_count += 1
                if len(conflicts) < 10:
                    conflicts.append(row['objectKey'])
            # Count the complete replacement body conservatively: no assumed
            # reclamation of an old representation during the upload.
            additional += row['bytes']
            transfer += row['bytes']
    return {'capacityBytes': CAPACITY, 'currentObjectBytes': used, 'objects': count,
            'additionalBytesUpperBound': additional, 'transferBytes': transfer,
            'matchingSelectedObjects': existing_count, 'projectedBytesUpperBound': used + additional,
            'immutableConflictCount': conflict_count, 'immutableConflicts': conflicts,
            'complete': not incomplete, 'incompleteReasons': incomplete,
            'withinCapacity': not incomplete and used + additional <= CAPACITY,
            'scope': 'whole-bucket encoded object payload; requires an exclusive publisher; not a quota reservation'}


def previous_digest(client, target, key, existing):
    previous = existing.get('Metadata', {}).get('moly-sha256')
    if previous:
        return previous
    etag = existing.get('ETag')
    if not etag or existing.get('ContentLength', MAX_PUT) > 1048576:
        raise SafeFailure('legacy_mutable_identity_unverifiable', key=key)
    response = client.get_object(Bucket=target['bucket'], Key=key, IfMatch=etag)
    body = response['Body']
    try:
        raw = body.read(1048577)
    finally:
        body.close()
    if len(raw) > 1048576:
        raise SafeFailure('legacy_mutable_object_too_large', key=key)
    return hashlib.sha256(raw).hexdigest()


def untransformed(client, target, key, existing):
    """The stored object's own headers, which a conditional replace needs.

    `existing` was read with the Accept-Encoding of the new representation.
    When that differs from the stored one, a CDN in front of the endpoint may
    compress or decode the stored body on the way, and it then answers with a
    weak ETag (W/"..."), which the origin never matches: every replace that
    changes an object's encoding would fail its If-Match. The stored
    representation, asked for as itself, comes back as stored."""
    reads = (lambda: existing, lambda: head(client, target, key, 'identity'), lambda: head(client, target, key, 'br'))
    for read in reads:
        stored = read()
        if stored is None:
            raise SafeFailure('expected_previous_mutable_object_is_missing', key=key)
        etag = stored.get('ETag')
        if etag and not etag.startswith('W/'):
            return stored
    raise SafeFailure('mutable_etag_missing', key=key)


def local_md5(row):
    """MD5 of the row's local body, after checking its length and sha256."""
    with Path(row.get('staging') or row['source']).open('rb') as stream:
        sha, md5, size = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
        while block := stream.read(1024 * 1024):
            sha.update(block)
            md5.update(block)
            size += len(block)
    if size != row['bytes'] or sha.hexdigest() != row['sha256']:
        raise SafeFailure('local_upload_body_changed', key=row['objectKey'])
    return md5.hexdigest()


def copy_one(client, target, row, source_key):
    """Create an absent object as a server-side copy of source_key, or return
    None when the stored source does not hold exactly the row's bytes.

    The copy is conditional on the source ETag being the MD5 of the row's
    local body (the ETag of a single-part object) and on the destination
    being absent. It writes the row's own headers, and the result is read
    back like an upload. A failed source condition, whatever its cause,
    leaves the object to a normal upload, and so does a stored source whose
    headers are not the row's: the copy would keep them."""
    require_copy_conditions(client)
    key = row['objectKey']
    if not matches(head(client, target, source_key, row['contentEncoding']), row):
        return None
    request = dict(Bucket=target['bucket'], Key=key,
        CopySource={'Bucket': target['bucket'], 'Key': source_key}, CopySourceIfMatch=f'"{local_md5(row)}"',
        IfNoneMatch='*', MetadataDirective='REPLACE', ContentType=row['contentType'],
        CacheControl=row['cacheControl'], Metadata=metadata(row))
    if row['contentEncoding'] != 'identity':
        request['ContentEncoding'] = row['contentEncoding']
    try:
        client.copy_object(**request)
    except Exception as error:
        status, code = error_status(error)
        if status in {409, 412}:
            if matches(head(client, target, key, row['contentEncoding']), row):
                return {'status': 'verified-skip', 'key': key, 'sha256': row['sha256']}
            return None
        if status == 404 and code in {'404', 'NoSuchKey', 'NotFound'}:
            return None
        raise
    stored = head(client, target, key, row['contentEncoding'])
    if not matches(stored, row):
        raise SafeFailure('copied_object_verification_failed', key=key)
    return {'status': 'copied', 'key': key, 'sha256': row['sha256'], 'bytes': row['bytes'],
            'source': source_key, 'etag': stored.get('ETag')}


def put_one(client, target, row, args, *, known_absent=False, copy_sources=None):
    require_conditions(client)
    key = row['objectKey']
    # An object absent from the complete listing is written without a prior
    # read: the create stays conditional, and a racing object is verified below.
    existing = None if known_absent else head(client, target, key, row['contentEncoding'])
    if matches(existing, row):
        return {'status': 'verified-skip', 'key': key, 'sha256': row['sha256']}
    source_key = (copy_sources or {}).get(content_identity(row))
    if existing is None and args.scope != 'mutable' and source_key and source_key != key:
        copied = copy_one(client, target, row, source_key)
        if copied is not None:
            return copied
    condition = {'IfNoneMatch': '*'}
    if existing is not None:
        if args.scope != 'mutable':
            raise SafeFailure('immutable_object_conflict', key=key)
        if not args.expect_previous_sha256:
            raise SafeFailure('mutable_previous_hash_not_confirmed', key=key)
        stored = untransformed(client, target, key, existing)
        if previous_digest(client, target, key, stored) != args.expect_previous_sha256:
            raise SafeFailure('mutable_previous_hash_not_confirmed', key=key)
        condition = {'IfMatch': stored['ETag']}
    elif args.scope == 'mutable' and args.expect_previous_sha256:
        raise SafeFailure('expected_previous_mutable_object_is_missing', key=key)
    source = Path(row.get('staging') or row['source'])
    with source.open('rb') as stream:
        stat = os.fstat(stream.fileno())
        sha, md5, size = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
        while block := stream.read(1024 * 1024):
            sha.update(block)
            md5.update(block)
            size += len(block)
        if size != row['bytes'] or sha.hexdigest() != row['sha256']:
            raise SafeFailure('local_upload_body_changed', key=key)
        stream.seek(0)
        body = ThrottledBody(stream, THROTTLE) if THROTTLE else stream
        request = dict(Bucket=target['bucket'], Key=key, Body=body, ContentLength=size,
            ContentType=row['contentType'], CacheControl=row['cacheControl'], Metadata=metadata(row),
            ContentMD5=base64.b64encode(md5.digest()).decode('ascii'), **condition)
        if row['contentEncoding'] != 'identity':
            request['ContentEncoding'] = row['contentEncoding']
        try:
            client.put_object(**request)
        except Exception as error:
            status, _ = error_status(error)
            if status in {409, 412} and matches(head(client, target, key, row['contentEncoding']), row):
                return {'status': 'verified-skip', 'key': key, 'sha256': row['sha256']}
            # Unsupported conditions, conflicts and uncertain results all stop.
            # A retry never drops a condition or refreshes the expected ETag.
            raise
        after = os.fstat(stream.fileno())
        if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SafeFailure('local_file_changed_during_upload', key=key)
    stored = head(client, target, key, row['contentEncoding'])
    if not matches(stored, row):
        raise SafeFailure('uploaded_object_verification_failed', key=key)
    return {'status': 'uploaded', 'key': key, 'sha256': row['sha256'], 'bytes': size, 'etag': stored.get('ETag')}


def publication_identity(target, manifest):
    return {**target, 'manifestSha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}


def receipt_path(args, identity):
    tag = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    return args.receipt or args.manifest.with_name(f'upload-s3-{tag}-{args.scope}.jsonl')


def execute(client, target, rows, args, identity, absent_keys=frozenset(), copy_sources=None):
    receipt = receipt_path(args, identity)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    lock = receipt.with_suffix(receipt.suffix + '.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SafeFailure('receipt_in_use_or_interrupted_lock') from None
    os.close(descriptor)
    try:
        previous = receipt.read_bytes() if receipt.exists() else b''
        for line in previous.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                # A torn journal line is never evidence for skipping a HEAD.
                # Preserve it, including after a later resume appended records.
                continue
            if not isinstance(record, dict):
                raise SafeFailure('invalid_receipt_record')
            if record.get('destination') != identity:
                raise SafeFailure('receipt_destination_or_publication_mismatch')
        with receipt.open('a', encoding='utf-8', newline='\n') as journal:
            if previous and not previous.endswith(b'\n'):
                journal.write('\n')
            # A sliding window: a slow request holds one worker, not a batch.
            # After the first failure nothing new is started; requests already
            # in flight finish and are journaled before the run stops.
            workers = args.threads
            queue = iter(rows)
            pending = {}
            failed = False
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='moly-s3') as pool:
                def start_next():
                    row = next(queue, None)
                    if row is not None:
                        pending[pool.submit(put_one, client, target, row, args,
                                            known_absent=row['objectKey'] in absent_keys,
                                            copy_sources=copy_sources)] = row
                for _ in range(workers * 2):
                    start_next()
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        row = pending.pop(future)
                        try:
                            result = future.result()
                        except Exception as error:
                            result = {'status': 'failed', 'key': row['objectKey'], 'error': sanitized_error(error)}
                        record = {**result, 'destination': identity, 'atUtc': datetime.now(timezone.utc).isoformat()}
                        journal.write(json.dumps(record, ensure_ascii=False) + '\n')
                        journal.flush()
                        os.fsync(journal.fileno())
                        common.emit('object', **result)
                        failed = failed or result['status'] == 'failed'
                        if not failed:
                            start_next()
            if failed:
                return 1
        return 0
    finally:
        lock.unlink(missing_ok=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    for field in ('endpoint', 'bucket', 'region', 'object-prefix'):
        parser.add_argument('--' + field, required=True)
    parser.add_argument('--addressing-style', choices=['path', 'virtual'], default='path')
    parser.add_argument('--profile', help='Optional local AWS SDK profile; otherwise the standard credential chain')
    parser.add_argument('--scope', choices=['stable', 'release', 'descriptors', 'mutable'], default='stable')
    parser.add_argument('--release-id')
    parser.add_argument('--mutable-path', type=common.mutable_path, action='append', default=[])
    parser.add_argument('--allow-mutable-update', action='store_true')
    parser.add_argument('--expect-previous-sha256')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--threads', type=int, default=8, choices=range(1, 33), metavar='1..32',
                        help='Concurrent conditional uploads (default: 8)')
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--copy-source-manifest', type=Path, action='append', default=[],
                        help='An earlier upload manifest already published to this bucket and prefix; '
                             'absent objects byte-identical to one of its immutable objects are copied server-side')
    parser.add_argument('--timeout', type=int, default=30, choices=range(1, 61), metavar='1..60')
    parser.add_argument('--max-bytes-per-second', type=int, metavar='N',
                        help='Cap the body bytes all workers send together (an uplink shared with other use)')
    parser.add_argument('--heartbeat', type=Path,
                        help='File touched about every 10 s while bytes move (a machine-wide upload lock)')
    parser.add_argument('--inspect', action='store_true', help='Read-only whole-bucket capacity and object inspection')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--exclusive-writer', action='store_true', help='Confirm no concurrent publisher can consume the planned bucket capacity')
    return parser.parse_args(argv)


def main(argv=None):
    global THROTTLE
    args = parse_args(argv)
    if args.max_bytes_per_second is not None:
        if args.max_bytes_per_second < UPLOAD_CHUNK:
            raise SafeFailure('max_bytes_per_second_below_one_chunk', minimum=UPLOAD_CHUNK)
        THROTTLE = Throttle(args.max_bytes_per_second, args.heartbeat)
    elif args.heartbeat is not None:
        raise SafeFailure('heartbeat_requires_max_bytes_per_second')
    target = destination(args)
    manifest_before = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    _, rows = selection(args)
    identity = publication_identity(target, args.manifest)
    if identity['manifestSha256'] != manifest_before:
        raise SafeFailure('upload_manifest_changed_during_selection')
    copy_sources = load_copy_sources(args.copy_source_manifest, args.object_prefix)
    copyable = [row for row in rows if copy_sources.get(content_identity(row), row['objectKey']) != row['objectKey']]
    common.emit('selection', destination=target, dryRun=not args.execute, objects=len(rows),
        selectedEncodedBytes=sum(row['bytes'] for row in rows), capacityBytes=CAPACITY,
        receipt=str(receipt_path(args, identity)), deleteObjects=False, changeAcl=False,
        copyCandidates=len(copyable), copyCandidateBytes=sum(row['bytes'] for row in copyable))
    if not args.inspect and not args.execute:
        common.emit('local-dry-run', cloudRequests=0, usedBytes=None,
                    newBytesUpperBound=sum(row['bytes'] for row in rows), capacityVerified=False)
        return 0
    if args.execute and not args.exclusive_writer:
        raise SafeFailure('execute_requires_exclusive_writer_capacity_assumption')
    known_existing_keys = set()
    receipt = receipt_path(args, identity)
    if args.execute and receipt.exists():
        for line in receipt.read_text('utf-8').splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get('destination') != identity:
                raise SafeFailure('receipt_destination_or_publication_mismatch')
            if record.get('status') in {'uploaded', 'copied', 'verified-skip'} and isinstance(record.get('key'), str):
                known_existing_keys.add(record['key'])
    client = create_client(args)
    if args.execute:
        require_conditions(client)
        if copyable:
            require_copy_conditions(client)
    absent_keys = set()
    plan = inspect_capacity(client, target, rows, mutable=args.scope == 'mutable',
                            known_existing_keys=known_existing_keys, absent_keys=absent_keys)
    common.emit('capacity-plan', **plan)
    if not args.execute:
        return 0
    if not plan['withinCapacity']:
        raise SafeFailure('capacity_unverified_or_exceeded', capacityBytes=CAPACITY)
    if plan['immutableConflictCount']:
        raise SafeFailure('immutable_object_conflicts', count=plan['immutableConflictCount'])
    return execute(client, target, rows, args, identity, absent_keys, copy_sources)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        common.emit('interrupted', hint='Resume the same publication; every object is HEAD-verified again.')
        sys.exit(130)
    except Exception as error:
        common.emit('error', **sanitized_error(error))
        sys.exit(1)
