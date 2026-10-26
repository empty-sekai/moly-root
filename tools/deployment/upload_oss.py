"""Bounded OSS publication uploader. Default is a local dry-run, never a write.

Credentials are read from the active official Alibaba CLI OAuth profile and stay
in memory. This tool neither changes ACLs nor deletes/overwrites immutable keys.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import sys
import threading
import time
import warnings

BUCKET = 'pjsk-stems'
REGION = 'cn-shanghai'
ENDPOINT = 'https://oss-cn-shanghai.aliyuncs.com'
PREFIX = 'moly/alpha/'
MUTABLE = {'manifest.json', 'cache-worker.mjs', 'game/manifest.json', 'fonts/latest.json'}
TRANSLATION_ROOT = re.compile(r'translations/(jp|cn|tw|en|kr)/(zh-CN|zh-TW|en-US|ja-JP|ko-KR)/latest\.json')


def mutable_path(value):
    if value not in MUTABLE and not TRANSLATION_ROOT.fullmatch(value):
        raise argparse.ArgumentTypeError('expected a known publication pointer')
    return value
IDENTIFIER = re.compile(r'^[a-z0-9][a-z0-9._-]{0,95}$')
SHA256 = re.compile(r'^[a-f0-9]{64}$')


class SafeFailure(Exception):
    def __init__(self, code, **details):
        self.code, self.details = code, details


def emit(event, **fields):
    print(json.dumps({'event': event, **fields}, ensure_ascii=False), flush=True)


def sanitized_error(exc):
    if isinstance(exc, SafeFailure):
        return {'code': exc.code, **exc.details}
    # Never include exception strings, signed request URLs, headers or bodies.
    return {'code': type(exc).__name__,
            **({'httpStatus': exc.status} if isinstance(getattr(exc, 'status', None), int) else {}),
            **({'ossCode': exc.code} if isinstance(getattr(exc, 'code', None), str) else {}),
            **({'requestId': exc.request_id} if isinstance(getattr(exc, 'request_id', None), str) else {})}


def sdk():
    for name in ('oss2', 'urllib3', 'requests'):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.CRITICAL)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            import oss2
        except ImportError:
            raise SafeFailure('deployment_sdk_not_installed',
                              hint='Install dependencies with python -m pip install -e ".[deployment]"') from None
    return oss2


class CurrentOAuthCredentials:
    """SDK provider with read-only refresh from the CLI's own config file."""
    def __init__(self, oss, config, profile=None):
        self.oss, self.config, self.profile = oss, config, profile
        self.lock = threading.Lock()
        self.cached = None
        self.last_read = 0.0
        self.expiration = 0.0
        self.active_profile = None

    def get_credentials(self):
        with self.lock:
            now = time.time()
            if self.cached and now - self.last_read < 30 and self.expiration - now > 120:
                return self.cached
            try:
                doc = json.loads(self.config.read_bytes())
                name = self.profile or doc['current']
                row = next(p for p in doc['profiles'] if p.get('name') == name)
                if row.get('mode') != 'OAuth':
                    raise SafeFailure('active_cli_profile_is_not_oauth')
                expiration = float(row.get('sts_expiration', 0))
                if expiration - now < 120:
                    raise SafeFailure('cli_oauth_credentials_expired_or_expiring',
                                      hint='Refresh the official CLI OAuth session with a read-only CLI call, then retry.')
                fields = [row.get(k) for k in ('access_key_id', 'access_key_secret', 'sts_token')]
                if not all(isinstance(v, str) and v for v in fields):
                    raise SafeFailure('cli_oauth_temporary_credentials_missing')
                self.cached = self.oss.credentials.Credentials(*fields)
                self.expiration, self.last_read, self.active_profile = expiration, now, name
                return self.cached
            except SafeFailure:
                raise
            except Exception as exc:
                raise SafeFailure('cannot_read_cli_oauth_config', errorType=type(exc).__name__) from None


class ClientPool:
    def __init__(self, config, profile, timeout):
        self.oss = sdk()
        self.provider = CurrentOAuthCredentials(self.oss, config, profile)
        self.auth = self.oss.ProviderAuthV4(self.provider)
        self.local = threading.local()
        self.timeout = timeout

    def get(self):
        if not hasattr(self.local, 'bucket'):
            self.local.bucket = self.oss.Bucket(
                self.auth, ENDPOINT, BUCKET, region=REGION,
                session=self.oss.Session(pool_size=2), connect_timeout=self.timeout, enable_crc=True,
                app_name='moly-alpha-publication')
        return self.local.bucket

    def verify(self):
        bucket = self.get()
        info = bucket.get_bucket_info()
        location = getattr(info, 'location', None)
        if location not in {'oss-cn-shanghai', 'cn-shanghai'}:
            raise SafeFailure('unexpected_bucket_region', location=location)
        listing = bucket.list_objects_v2(prefix=PREFIX, max_keys=1)
        listed = list(listing.object_list)
        key = listed[0].key if listed else PREFIX + '__moly_upload_readonly_missing_probe__'
        try:
            head = bucket.head_object(key)
            head_status = head.status
        except self.oss.exceptions.NoSuchKey:
            head_status = 404
        self.provider.get_credentials()
        return {'bucket': BUCKET, 'region': REGION, 'prefix': PREFIX,
                'bucketInfoStatus': info.status, 'listStatus': listing.status, 'headStatus': head_status,
                'profile': self.provider.active_profile,
                'credentialExpiresAtUtc': datetime.fromtimestamp(self.provider.expiration, timezone.utc).isoformat(),
                'readOnly': True}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--scope', choices=['stable', 'release', 'descriptors', 'mutable'], default='stable')
    p.add_argument('--release-id', help='Required explicit release ID for --scope release')
    p.add_argument('--mutable-path', type=mutable_path, action='append', default=[])
    p.add_argument('--allow-mutable-update', action='store_true')
    p.add_argument('--expect-previous-sha256', help='Required to replace an existing mutable object; one mutable path per run')
    p.add_argument('--threads', type=int, default=16, choices=range(1, 17), metavar='1..16')
    p.add_argument('--limit', type=int, help='Optional bounded canary object count')
    p.add_argument('--receipt', type=Path, help='Append-only result journal; defaults next to the manifest')
    p.add_argument('--config', type=Path, default=Path.home() / '.aliyun/config.json')
    p.add_argument('--profile', help='Defaults to the official CLI current profile')
    p.add_argument('--timeout', type=int, default=60)
    p.add_argument('--retries', type=int, default=3, choices=range(0, 6))
    p.add_argument('--verify-auth', action='store_true', help='Signed read-only bucket/list/head verification')
    p.add_argument('--execute', action='store_true', help='Actually upload the selected objects; otherwise dry-run')
    return p.parse_args()


def validate_font_publication(doc):
    # Preparation is not a trust boundary: manifests and staging paths can
    # change before execution. Reuse its catalogue contract, then bind every
    # declared input and the actual upload stream to the full root closure.
    from prepare_fonts import parse_catalog, content_type, cache_control

    catalog = doc['fontCatalog']
    rows = {row['path']: row for row in doc['files']}
    root_row = rows.get(catalog['path'])
    if root_row is None:
        raise SafeFailure('font_pointer_identity_mismatch')
    try:
        with Path(root_row['sourceRaw']).open('rb') as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        root, references = parse_catalog(raw)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SafeFailure('invalid_local_font_catalogue', errorType=type(error).__name__) from None
    if (root['revision'] != catalog['revision']
            or references['latest.json']['sha256'] != catalog['sha256']):
        raise SafeFailure('font_pointer_identity_mismatch')
    expected_paths = {'fonts/' + name for name in references}
    if rows.keys() != expected_paths:
        raise SafeFailure('font_publication_closure_mismatch',
                          missing=sorted(expected_paths - rows.keys()),
                          extra=sorted(rows.keys() - expected_paths))
    url_prefix = doc.get('urlPrefix')
    if (not isinstance(url_prefix, str) or not url_prefix.startswith('/') or not url_prefix.endswith('/')
            or any(part in {'.', '..'} for part in url_prefix.split('/'))
            or any(char in url_prefix for char in '\\%?#:')
            or any(ord(char) < 32 or ord(char) == 127 for char in url_prefix)):
        raise SafeFailure('invalid_font_url_prefix')
    verified = set()
    for name, expected in references.items():
        logical = 'fonts/' + name
        row = rows[logical]
        if (row['sha256'] != expected['sha256'] or row['decodedSha256'] != expected['sha256']
                or row['bytes'] != expected['bytes'] or row['decodedBytes'] != expected['bytes']
                or row['contentEncoding'] != 'identity' or row['contentType'] != content_type(name)
                or row['cacheControl'] != cache_control(name)
                or row.get('urlPath') != url_prefix + logical):
            raise SafeFailure('font_object_metadata_mismatch', path=logical)
        # All aliases are checked; sourceRaw must not validate one document
        # while staging/source supplies a different body to put_one().
        aliases = [row.get('sourceRaw'), row.get('source')]
        if 'staging' in row:
            aliases.append(row['staging'])
        for alias in aliases:
            if not isinstance(alias, str) or not alias:
                raise SafeFailure('invalid_font_object_source', path=logical)
            try:
                path = Path(alias).resolve(strict=True)
                identity = (path, expected['sha256'], expected['bytes'])
                if identity in verified:
                    continue
                digest, size = hashlib.sha256(), 0
                with path.open('rb') as stream:
                    while block := stream.read(1024 * 1024):
                        size += len(block)
                        if size > expected['bytes']:
                            raise SafeFailure('local_font_object_changed', path=logical)
                        digest.update(block)
                if size != expected['bytes'] or digest.hexdigest() != expected['sha256']:
                    raise SafeFailure('local_font_object_changed', path=logical)
                verified.add(identity)
            except OSError as error:
                raise SafeFailure('cannot_read_font_object', path=logical,
                                  errorType=type(error).__name__) from None


def load_selection(args, *, object_prefix=PREFIX, schemas=('moly-oss-upload/1',)):
    try:
        doc = json.loads(args.manifest.read_bytes())
    except Exception as exc:
        raise SafeFailure('cannot_read_upload_manifest', errorType=type(exc).__name__) from None
    if doc.get('schema') not in schemas or doc.get('objectPrefix') != object_prefix:
        raise SafeFailure('unsupported_manifest_or_object_prefix')
    if doc.get('deliveryMode', 'fixed') != 'fixed':
        raise SafeFailure('unsupported_delivery_mode')
    snapshots = set(doc.get('snapshots', []))
    translation = doc.get('translation')
    font_catalog = doc.get('fontCatalog')
    resource_catalog = doc.get('resourceCatalog')
    if resource_catalog is not None:
        if (not isinstance(resource_catalog, dict) or snapshots or translation is not None
                or font_catalog is not None or doc.get('release') is not None
                or not SHA256.fullmatch(resource_catalog.get('sha256', ''))
                or resource_catalog.get('path') != f"asset-store/catalogs/{resource_catalog['sha256']}.json"):
            raise SafeFailure('invalid_resource_catalogue_publication')
        if args.scope != 'stable' or args.limit is not None:
            raise SafeFailure('resource_catalogue_requires_complete_stable_scope')
    if font_catalog is not None:
        if (not isinstance(font_catalog, dict) or snapshots or translation is not None
                or font_catalog.get('path') != 'fonts/latest.json'
                or not SHA256.fullmatch(font_catalog.get('revision', ''))
                or not SHA256.fullmatch(font_catalog.get('sha256', ''))):
            raise SafeFailure('invalid_font_publication')
    if translation is not None:
        if (not isinstance(translation, dict) or snapshots
                or not TRANSLATION_ROOT.fullmatch(translation.get('path', ''))
                or not SHA256.fullmatch(translation.get('revision', ''))
                or not SHA256.fullmatch(translation.get('sha256', ''))):
            raise SafeFailure('invalid_translation_publication')
        translation_base = translation['path'].removesuffix('latest.json')
    else:
        translation_base = None
    if (not snapshots and translation is None and font_catalog is None and resource_catalog is None) or any(not IDENTIFIER.fullmatch(s) for s in snapshots):
        raise SafeFailure('invalid_snapshot_identities')
    releases = doc.get('releases', [doc['release']] if doc.get('release') else [])
    if (not isinstance(releases, list) or len(releases) > 2
            or any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value) for value in releases)
            or len(set(releases)) != len(releases)
            or (doc.get('release') is not None and doc['release'] not in releases)
            or (releases and (resource_catalog is not None or font_catalog is not None or translation is not None))):
        raise SafeFailure('invalid_release_identities')
    if len(releases) > 1:
        # A second release must be the actual game pointer target, not an
        # arbitrary directory added to the upload inventory by an operator.
        pointers = {}
        for logical in ('manifest.json', 'game/manifest.json'):
            rows = [row for row in doc.get('files', []) if row.get('path') == logical]
            if len(rows) != 1:
                raise SafeFailure('missing_release_pointer', path=logical)
            try:
                raw = Path(rows[0]['sourceRaw']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != rows[0]['decodedSha256']:
                    raise SafeFailure('local_descriptor_hash_mismatch', path=logical)
                pointers[logical] = json.loads(raw)
            except (OSError, KeyError, TypeError, ValueError) as error:
                raise SafeFailure('cannot_read_release_pointer', path=logical) from None
        primary = pointers['manifest.json'].get('release', {}).get('id')
        game = pointers['game/manifest.json']
        game_id = game.get('release', {}).get('id')
        if (primary != doc.get('release') or releases != list(dict.fromkeys([primary, game_id]))
                or game.get('publisher') != 'moly-release-artifact-v1' or game.get('schemaVersion') != 1
                or game.get('prefix') != doc.get('urlPrefix')
                or game.get('release', {}).get('module') != f"{doc.get('urlPrefix')}releases/{game_id}/game-boot.mjs"):
            raise SafeFailure('release_pointers_do_not_match_manifest')
    if args.scope == 'release' and args.release_id not in releases:
        raise SafeFailure('explicit_release_id_must_match_manifest')
    if args.scope == 'mutable':
        if not args.allow_mutable_update or len(args.mutable_path) != 1:
            raise SafeFailure('mutable_scope_requires_one_explicit_path_and_allow_flag')
        if args.expect_previous_sha256 and not SHA256.fullmatch(args.expect_previous_sha256):
            raise SafeFailure('invalid_expected_previous_sha256')
    elif args.mutable_path or args.allow_mutable_update or args.expect_previous_sha256:
        raise SafeFailure('mutable_flags_only_allowed_in_mutable_scope')
    if args.release_id and args.scope != 'release':
        raise SafeFailure('release_id_only_allowed_in_release_scope')
    if args.limit is not None and args.limit <= 0:
        raise SafeFailure('limit_must_be_positive')
    selected, seen = [], set()
    for row in doc.get('files', []):
        logical = row.get('path', '')
        if resource_catalog is not None and not re.fullmatch(r'asset-store/(?:(?:catalogs|packages)/[a-f0-9]{64}\.json|blobs/[a-f0-9]{2}/[a-f0-9]{64}\.(?:bin|gzz|brz|br))', logical):
            raise SafeFailure('resource_catalogue_contains_unrelated_object')
        if font_catalog is not None and not re.fullmatch(r'fonts/(?:latest\.json|revisions/[a-f0-9]{64}\.json|objects/[a-f0-9]{64}\.(?:ttf|txt))', logical):
            raise SafeFailure('font_manifest_contains_unrelated_object')
        if translation_base and (not logical.startswith(translation_base) or not re.fullmatch(
                r'(?:latest\.json|(?:objects|revisions)/[a-f0-9]{64}\.json)', logical[len(translation_base):])):
            raise SafeFailure('translation_manifest_contains_unrelated_object')
        parts = PurePosixPath(logical).parts
        # This is an object key, not a URL. S3 permits a literal '#'; URL
        # consumers must percent-encode that segment when constructing a
        # request. Keep '?'/'%' blocked because they remain ambiguous in the
        # current public logical-path contract.
        if (not parts or logical.startswith('/') or '\\' in logical or ':' in logical
                or any(part in {'', '.', '..'} for part in logical.split('/'))
                or any(ord(c) < 32 or c == 0x7f or c in '%?' for c in logical)):
            raise SafeFailure('unsafe_object_path')
        if row.get('objectKey') != object_prefix + logical or logical in seen:
            raise SafeFailure('object_key_mismatch_or_duplicate', path=logical)
        seen.add(logical)
        for field in ('sha256', 'decodedSha256'):
            if not SHA256.fullmatch(row.get(field, '')):
                raise SafeFailure('invalid_object_hash', path=logical)
        if row.get('contentEncoding') not in {'identity', 'gzip', 'br'}:
            raise SafeFailure('unsupported_content_encoding', path=logical)
        if type(row.get('bytes')) is not int or row['bytes'] < 0 or type(row.get('decodedBytes')) is not int or row['decodedBytes'] < 0:
            raise SafeFailure('invalid_object_length', path=logical)
        if not all(isinstance(row.get(k), str) and row[k] and '\r' not in row[k] and '\n' not in row[k]
                   for k in ('contentType', 'cacheControl')):
            raise SafeFailure('invalid_object_http_metadata', path=logical)
        if row.get('deliveryMode', 'fixed') != 'fixed':
            raise SafeFailure('unsupported_delivery_mode', path=logical)
        if ('requiredAcceptEncoding' in row and row['requiredAcceptEncoding'] !=
                (None if row['contentEncoding'] == 'identity' else row['contentEncoding'])):
            raise SafeFailure('required_accept_encoding_mismatch', path=logical)
        decoded_metadata = (row.get('metadata') or {}).get('moly-decoded-sha256')
        if decoded_metadata is not None and decoded_metadata != row['decodedSha256']:
            raise SafeFailure('decoded_hash_metadata_mismatch', path=logical)
        is_snapshot = len(parts) >= 3 and parts[0] == 'snapshots' and parts[1] in snapshots
        # The shared content-addressed stores are immutable like snapshot payloads.
        shared_store = len(parts) >= 2 and parts[0] in {'asset-store', 'catalog-store'}
        translation_object = translation_base and logical.startswith(translation_base) and logical != translation['path']
        font_object = font_catalog is not None and logical != font_catalog['path']
        choose = ((args.scope == 'stable' and ((is_snapshot and parts[2] in {'assets', 'catalog', 'text'} and len(parts) >= 4)
                                               or shared_store or translation_object or font_object))
                  or (args.scope == 'descriptors' and is_snapshot and len(parts) == 3 and parts[2] in {'snapshot.json', 'provenance.json'})
                  or (args.scope == 'release' and len(parts) >= 3 and parts[:2] == ('releases', args.release_id))
                  or (args.scope == 'mutable' and logical in args.mutable_path))
        if choose:
            selected.append(row)
    selected.sort(key=lambda r: r['objectKey'])
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        raise SafeFailure('no_objects_selected')
    if font_catalog is not None:
        validate_font_publication(doc)
    if resource_catalog is not None:
        try:
            from resource_publication import validate_upload
            validate_upload(doc)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SafeFailure('invalid_resource_catalogue_closure', errorType=type(error).__name__) from None
    if translation is not None:
        roots = [row for row in doc['files'] if row['path'] == translation['path']]
        if len(roots) != 1 or roots[0]['decodedSha256'] != translation['sha256']:
            raise SafeFailure('translation_pointer_identity_mismatch')
        raw = Path(roots[0]['sourceRaw']).read_bytes()
        root = json.loads(raw)
        parts = translation['path'].split('/')
        if (hashlib.sha256(raw).hexdigest() != translation['sha256']
                or root.get('schema') != 'moly-text-publication/1' or root.get('region') != parts[1]
                or root.get('locale') != parts[2] or root.get('revision') != translation['revision']):
            raise SafeFailure('local_translation_pointer_changed')
    # Publishing descriptions requires the actual production descriptor bytes.
    for row in selected:
        if row['path'] in {'manifest.json', 'game/manifest.json'} or row['path'].endswith('/snapshot.json'):
            raw = Path(row['sourceRaw']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['decodedSha256']:
                raise SafeFailure('local_descriptor_hash_mismatch', path=row['path'])
            description = json.loads(raw)
            snaps = description.get('snapshots', [description])
            if any(s.get('provenance', {}).get('assetPolicy') == 'development-mount' for s in snaps):
                raise SafeFailure('development_descriptor_cannot_be_uploaded', path=row['path'])
    return doc, selected


def wanted_headers(row):
    headers = {'Content-Type': row['contentType'], 'Cache-Control': row['cacheControl'],
               'Content-Length': str(row['bytes']),
               'x-oss-meta-moly-decoded-bytes': str(row['decodedBytes']),
               'x-oss-meta-moly-sha256': row['sha256']}
    if row['contentEncoding'] != 'identity':
        headers['Content-Encoding'] = row['contentEncoding']
    if (row.get('metadata') or {}).get('moly-decoded-sha256'):
        headers['x-oss-meta-moly-decoded-sha256'] = row['metadata']['moly-decoded-sha256']
    return headers


def read_head(pool, key):
    try:
        return pool.get().head_object(key)
    except pool.oss.exceptions.NoSuchKey:
        return None


def matching_head(head, row):
    actual = {str(k).lower(): str(v) for k, v in head.headers.items()}
    expected = {k.lower(): v for k, v in wanted_headers(row).items()}
    bad = [key for key, value in expected.items() if actual.get(key) != value]
    if int(actual.get('content-length', -1)) != row['bytes']:
        bad.append('content-length')
    if row['contentEncoding'] == 'identity' and actual.get('content-encoding', '') not in {'', 'identity'}:
        bad.append('content-encoding')
    return bad


def put_one(pool, row, args, stop):
    if stop.is_set():
        return {'status': 'cancelled', 'key': row['objectKey']}
    key = row['objectKey']
    existing = read_head(pool, key)
    is_mutable = args.scope == 'mutable'
    if existing is not None:
        mismatches = matching_head(existing, row)
        if not mismatches:
            return {'status': 'verified-skip', 'key': key, 'sha256': row['sha256'], 'bytes': row['bytes'], 'etag': existing.etag}
        if not is_mutable:
            raise SafeFailure('immutable_object_conflict', key=key, fields=mismatches)
        previous = existing.headers.get('x-oss-meta-moly-sha256')
        # Legacy mutable pointers predate uploader metadata. Verify their
        # actual stored bytes, bounded to this tiny control-plane surface,
        # instead of treating absent metadata as proof of their identity.
        if previous is None and args.expect_previous_sha256 and is_mutable:
            response = pool.get().get_object(key)
            try:
                body = response.read(1048577)
            finally:
                response.close()
            if len(body) > 1048576:
                raise SafeFailure('legacy_mutable_object_too_large', key=key)
            current = read_head(pool, key)
            if current is None or current.etag != existing.etag:
                raise SafeFailure('mutable_object_changed_during_verification', key=key)
            previous = hashlib.sha256(body).hexdigest()
        if not args.expect_previous_sha256 or previous != args.expect_previous_sha256:
            raise SafeFailure('mutable_previous_hash_not_confirmed', key=key,
                              observedSha256=previous if previous and SHA256.fullmatch(previous) else None)
    elif is_mutable and args.expect_previous_sha256:
        raise SafeFailure('expected_previous_mutable_object_is_missing', key=key)
    source = Path(row.get('staging') or row['source'])
    with source.open('rb') as stream:
        before = os.fstat(stream.fileno())
        sha, md5, size = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
        while block := stream.read(1024 * 1024):
            sha.update(block); md5.update(block); size += len(block)
        if size != row['bytes'] or sha.hexdigest() != row['sha256']:
            raise SafeFailure('local_upload_body_changed', key=key)
        if stop.is_set():
            return {'status': 'cancelled', 'key': key}
        stream.seek(0)
        headers = wanted_headers(row)
        headers['Content-MD5'] = base64.b64encode(md5.digest()).decode('ascii')
        if not is_mutable or existing is None:
            headers['x-oss-forbid-overwrite'] = 'true'
        # OSS's supported PutObject overwrite guard is forbid-overwrite. Mutable
        # replacement is an explicitly authorized, single-publisher operation
        # after checking the expected previous digest; do not pretend an
        # undocumented If-Match header supplies an atomic compare-and-swap.
        try:
            result = pool.get().put_object(key, stream, headers=headers)
        except pool.oss.exceptions.ServerError as exc:
            if exc.status == 409:
                raced = read_head(pool, key)
                if raced is not None and not matching_head(raced, row):
                    return {'status': 'verified-skip', 'key': key, 'sha256': row['sha256'], 'bytes': row['bytes'], 'etag': raced.etag}
            raise
        after = os.fstat(stream.fileno())
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise SafeFailure('local_file_changed_during_upload', key=key)
    # PutObject validates Content-MD5; SDK CRC is also enabled. HEAD verifies
    # the stored HTTP representation/metadata before the receipt is committed.
    stored = read_head(pool, key)
    if stored is None or (bad := matching_head(stored, row)):
        raise SafeFailure('uploaded_object_verification_failed', key=key, fields=bad if stored else ['missing'])
    return {'status': 'uploaded', 'key': key, 'sha256': row['sha256'], 'bytes': row['bytes'],
            'etag': stored.etag, 'requestId': result.request_id}


def retry_one(pool, row, args, stop):
    for attempt in range(args.retries + 1):
        try:
            return put_one(pool, row, args, stop)
        except Exception as exc:
            transient = (not isinstance(exc, SafeFailure) and
                         (getattr(exc, 'status', None) in {429, 500, 502, 503, 504}
                          or isinstance(exc, pool.oss.exceptions.RequestError)))
            if not transient or attempt == args.retries:
                raise
            if stop.wait(min(2 ** attempt, 8)):
                return {'status': 'cancelled', 'key': row['objectKey']}


def execute(pool, rows, args):
    receipt = args.receipt or args.manifest.with_name(f'upload-{args.scope}-receipt.jsonl')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    lock = receipt.with_suffix(receipt.suffix + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SafeFailure('receipt_in_use_or_interrupted_lock', path=str(lock)) from None
    os.write(fd, str(os.getpid()).encode('ascii')); os.close(fd)
    stop, totals = threading.Event(), {'uploaded': 0, 'verified-skip': 0, 'cancelled': 0, 'failed': 0, 'uploadedBytes': 0}
    started = last_report = time.monotonic()
    try:
        previous, ignored = set(), 0
        if receipt.exists():
            with receipt.open(encoding='utf-8') as old:
                for line in old:
                    try:
                        record = json.loads(line)
                        if (record.get('bucket') == BUCKET and record.get('status') in {'uploaded', 'verified-skip'}
                                and isinstance(record.get('key'), str) and record['key'].startswith(PREFIX)):
                            previous.add((record['key'], record.get('sha256')))
                    except (ValueError, TypeError):
                        ignored += 1
        emit('resume', receipt=str(receipt), previousVerifiedObjects=len(previous),
             ignoredIncompleteReceiptLines=ignored, revalidateExistingWithHead=True)
        with receipt.open('a', encoding='utf-8', buffering=1) as journal, ThreadPoolExecutor(max_workers=args.threads) as executor:
            # A crash may leave an incomplete final JSONL record. Preserve it,
            # separate subsequent records, and never trust it to skip a HEAD.
            if receipt.stat().st_size:
                with receipt.open('rb') as tail:
                    tail.seek(-1, os.SEEK_END)
                    if tail.read(1) != b'\n':
                        journal.write('\n')
            iterator = iter(rows)
            pending = {}
            def fill():
                while not stop.is_set() and len(pending) < args.threads * 2:
                    row = next(iterator, None)
                    if row is None:
                        break
                    pending[executor.submit(retry_one, pool, row, args, stop)] = row
            fill()
            while pending:
                done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    row = pending.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        stop.set()
                        result = {'status': 'failed', 'key': row['objectKey'], 'error': sanitized_error(exc)}
                        emit('object-failed', **result)
                    totals[result['status']] += 1
                    if result['status'] == 'uploaded':
                        totals['uploadedBytes'] += result['bytes']
                    result['atUtc'] = datetime.now(timezone.utc).isoformat()
                    result['bucket'] = BUCKET
                    journal.write(json.dumps(result, ensure_ascii=False) + '\n')
                fill()
                now = time.monotonic()
                if now - last_report >= 10:
                    journal.flush(); os.fsync(journal.fileno())
                    emit('progress', **totals, elapsedSeconds=round(now - started, 1), selected=len(rows))
                    last_report = now
            journal.flush(); os.fsync(journal.fileno())
        emit('complete', **totals, selected=len(rows), elapsedSeconds=round(time.monotonic() - started, 1), receipt=str(receipt))
        return 1 if totals['failed'] else 0
    finally:
        lock.unlink(missing_ok=True)


def main():
    args = parse_args()
    doc, rows = load_selection(args)
    emit('selection', dryRun=not args.execute, scope=args.scope, bucket=BUCKET, region=REGION, prefix=PREFIX,
         objects=len(rows), uploadBytes=sum(r['bytes'] for r in rows), decodedBytes=sum(r['decodedBytes'] for r in rows),
         threads=args.threads, release=doc.get('release'),
         encodings={kind: sum(r['contentEncoding'] == kind for r in rows) for kind in ('identity', 'gzip', 'br')},
         writesOnlyAfterExecute=True, overwriteImmutable=False, deleteObjects=False, changeAcl=False)
    pool = None
    if args.verify_auth or args.execute:
        pool = ClientPool(args.config, args.profile, args.timeout)
        emit('authorization', **pool.verify())
    if args.execute:
        return execute(pool, rows, args)
    emit('dry-run-complete', selectedObjects=len(rows), cloudWrites=0)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        emit('interrupted', hint='Rerun with the same receipt; existing objects are HEAD-verified before skipping.')
        sys.exit(130)
    except Exception as error:
        emit('error', **sanitized_error(error))
        sys.exit(1)
