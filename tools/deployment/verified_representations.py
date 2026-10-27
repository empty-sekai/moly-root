"""Reuse a previously observed immutable HTTP body without changing its URL.

The explicit index is local evidence, never a cloud write permission. Every use
rechecks stored bytes, lossless decoding, addressed identity, and exact headers.
The uploader still independently compares the selected representation to origin.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from pack.http_delivery import Representation
from pack.verify import _decode_content

SCHEMA = 'moly-verified-http-representations/1'
ADDRESS = re.compile(r'(?:asset-store/(?:blobs/[0-9a-f]{2}|packages|catalogs)|catalog-store)/([0-9a-f]{64})\.[a-z0-9]+')
HASH = re.compile(r'[0-9a-f]{64}')
CACHES = frozenset({'public, max-age=31536000, immutable',
                   'public, max-age=31536000, immutable, no-transform'})


def digest(body):
    return hashlib.sha256(body).hexdigest()


def relative(name):
    if (not isinstance(name, str) or not name or name.startswith('/')
            or any(char in name for char in '\\:%?#')
            or any(part in {'', '.', '..'} for part in name.split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise ValueError('unsafe representation evidence path')
    return PurePosixPath(name)


@dataclass(frozen=True)
class ReusedRepresentation:
    representation: Representation
    headers: dict
    proof: dict

    def apply(self, row):
        # These are precisely the headers recorded at origin, not freshly
        # chosen compression/cache policy. Identity means omit Content-Encoding.
        row.update(contentType=self.headers['ContentType'],
                   cacheControl=self.headers['CacheControl'],
                   representationReuse=self.proof)


class VerifiedRepresentations:
    def __init__(self, index=None, *, expected_sha256=None):
        self.index = None
        self.sha256 = None
        self.rows = {}
        self.used = set()
        if index is None:
            return
        self.index = Path(index).resolve(strict=True)
        raw = self.index.read_bytes()
        self.sha256 = digest(raw)
        if expected_sha256 is not None and self.sha256 != expected_sha256:
            raise ValueError('representation index changed')
        document = json.loads(raw)
        if set(document) != {'schema', 'files'} or document['schema'] != SCHEMA or not isinstance(document['files'], list):
            raise ValueError('invalid verified representation index')
        for row in document['files']:
            if not isinstance(row, dict) or set(row) != {'path', 'objectKey', 'body', 'sha256', 'bytes', 'decodedSha256', 'decodedBytes', 'headers'}:
                raise ValueError('invalid verified representation row')
            logical = row['path']
            address = ADDRESS.fullmatch(logical) if isinstance(logical, str) else None
            if not address or address[1] != row['decodedSha256'] or logical in self.rows:
                raise ValueError('representation identity is not a unique addressed object')
            relative(logical)
            relative(row['objectKey'])
            relative(row['body'])
            if any(not isinstance(row[key], str) or not HASH.fullmatch(row[key]) for key in ('sha256', 'decodedSha256')):
                raise ValueError('invalid representation hash')
            if any(type(row[key]) is not int or row[key] < 0 for key in ('bytes', 'decodedBytes')):
                raise ValueError('invalid representation length')
            headers = row['headers']
            if not isinstance(headers, dict) or set(headers) != {'ContentLength', 'ContentType', 'ContentEncoding', 'CacheControl', 'Metadata'}:
                raise ValueError('invalid representation headers')
            if (type(headers['ContentLength']) is not int or headers['ContentLength'] != row['bytes']
                    or headers['ContentEncoding'] not in {None, 'identity', 'gzip', 'br'}
                    or headers['CacheControl'] not in CACHES
                    or not isinstance(headers['ContentType'], str) or not headers['ContentType']
                    or any(ord(char) < 32 or ord(char) == 127 for char in headers['ContentType'])
                    or headers['Metadata'] != {'moly-sha256': row['sha256'],
                        'moly-decoded-sha256': row['decodedSha256'], 'moly-decoded-bytes': str(row['decodedBytes'])}):
                raise ValueError('representation headers disagree with verified identity')
            self.rows[logical] = row

    def select(self, source, logical, object_key, content_type):
        row = self.rows.get(logical)
        if row is None:
            return None
        if row['objectKey'] != object_key or row['headers']['ContentType'] != content_type:
            raise ValueError('representation URL or media type changed')
        source = Path(source).resolve(strict=True)
        raw = source.read_bytes()
        if len(raw) != row['decodedBytes'] or digest(raw) != row['decodedSha256']:
            raise ValueError('representation decoded source changed')
        body_path = self.index.parent / relative(row['body'])
        # Reject links before resolving, including an intermediate directory.
        if body_path.is_symlink() or any(parent.is_symlink() for parent in body_path.parents if parent != self.index.parent):
            raise ValueError('representation body must not use symlinks')
        body_path = body_path.resolve(strict=True)
        if not body_path.is_relative_to(self.index.parent) or not body_path.is_file():
            raise ValueError('representation body escapes evidence directory')
        with body_path.open('rb') as stream:
            body = stream.read(row['bytes'] + 1)
        if len(body) != row['bytes'] or digest(body) != row['sha256']:
            raise ValueError('representation stored body changed')
        encoding = row['headers']['ContentEncoding'] or 'identity'
        decoded = _decode_content(body, 'gzip' if encoding == 'gzip' else 'identity',
                                  'br' if encoding == 'br' else 'identity', row['decodedBytes'])
        if len(decoded) != row['decodedBytes'] or digest(decoded) != row['decodedSha256'] or decoded != raw:
            raise ValueError('representation does not decode to its addressed source')
        self.used.add(logical)
        representation = Representation(body_path, source, encoding, body, len(raw), digest(raw),
                                        (), False, 'verified-immutable-origin')
        return ReusedRepresentation(representation, row['headers'],
                                    {'index': str(self.index), 'sha256': self.sha256})

    def audit(self):
        return {'index': None if self.index is None else str(self.index), 'sha256': self.sha256,
                'reused': sorted(self.used), 'unused': sorted(self.rows.keys() - self.used)}


def validate_reused_row(row, source, content_type):
    """Revalidate reuse evidence immediately before an upload is selected."""
    proof = row.get('representationReuse')
    if not isinstance(proof, dict) or set(proof) != {'index', 'sha256'} or not isinstance(proof['sha256'], str) or not HASH.fullmatch(proof['sha256']):
        raise ValueError('invalid representation reuse proof')
    index = VerifiedRepresentations(proof['index'], expected_sha256=proof['sha256'])
    reused = index.select(source, row['path'], row['objectKey'], content_type)
    if reused is None:
        raise ValueError('representation reuse proof omits object')
    expected = reused.representation.manifest_fields
    for field in ('sha256', 'bytes', 'decodedSha256', 'decodedBytes', 'contentEncoding', 'deliveryMode', 'requiredAcceptEncoding'):
        if row.get(field) != expected[field]:
            raise ValueError('representation manifest differs from verified evidence')
    if row['cacheControl'] != reused.headers['CacheControl'] or row['contentType'] != reused.headers['ContentType']:
        raise ValueError('representation manifest headers differ from verified evidence')
    return reused
