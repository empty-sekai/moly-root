"""Prepare an independent immutable text-font publication; no cloud writes."""
import argparse
import hashlib
import json
from pathlib import Path
import re

HASH = re.compile(r'[a-f0-9]{64}')


def content_type(path):
    return ('font/ttf' if path.endswith('.ttf') else 'text/plain; charset=utf-8'
            if path.endswith('.txt') else 'application/json; charset=utf-8')


def cache_control(path):
    return ('public, max-age=30, must-revalidate' if path == 'latest.json'
            else 'public, max-age=31536000, immutable')


def parse_catalog(data):
    """Validate the root and return its complete, content-addressed file closure.

    Used both during preparation and immediately before an upload selection.
    Font bytes remain with the caller so upload validation can stream them.
    """
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('font catalogue exceeds document limit')
    # Browser/Go consumers decode UTF-8, whereas json.loads(bytes) also accepts
    # UTF-16/32. Reject encodings that would publish successfully but not load.
    root = json.loads(data.decode('utf-8', errors='strict'))

    def is_hash(value):
        return isinstance(value, str) and HASH.fullmatch(value)

    if (not isinstance(root, dict) or root.get('schema') != 'moly-text-fonts/1'
            or not is_hash(root.get('revision'))
            or not isinstance(root.get('baseFonts'), dict) or not 1 <= len(root['baseFonts']) <= 16
            or not isinstance(root.get('fonts'), list) or not 1 <= len(root['fonts']) <= 8):
        raise ValueError('invalid font catalogue')
    identity = {k: v for k, v in root.items() if k != 'revision'}
    canonical = (json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if hashlib.sha256(canonical).hexdigest() != root['revision']:
        raise ValueError('font catalogue revision mismatch')

    def coverage(rows):
        if not isinstance(rows, list) or not rows or len(rows) > 65536:
            raise ValueError('invalid font coverage ranges')
        previous = -1
        for row in rows:
            if (not isinstance(row, list) or len(row) != 2 or any(type(n) is not int for n in row)
                    or not previous < row[0] <= row[1] <= 0x10FFFF
                    or (row[0] <= 0xDFFF and row[1] >= 0xD800)):
                raise ValueError('invalid or overlapping font coverage')
            previous = row[1]

    for sha, entry in root['baseFonts'].items():
        if not is_hash(sha) or not isinstance(entry, dict):
            raise ValueError('invalid base font identity')
        coverage(entry.get('coverage'))
    root_ref = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    files = {'latest.json': root_ref, f"revisions/{root['revision']}.json": root_ref}

    def reference(ref, suffix, maximum):
        if (not isinstance(ref, dict) or not is_hash(ref.get('sha256'))
                or ref.get('path') != f"objects/{ref['sha256']}.{suffix}"
                or type(ref.get('bytes')) is not int or not 0 < ref['bytes'] <= maximum):
            raise ValueError('invalid immutable font object')
        expected = {'sha256': ref['sha256'], 'bytes': ref['bytes']}
        if ref['path'] in files and files[ref['path']] != expected:
            raise ValueError('conflicting font object reference')
        files[ref['path']] = expected

    seen = set()
    for entry in root['fonts']:
        if not isinstance(entry, dict):
            raise ValueError('invalid supplemental font')
        coverage(entry.get('coverage'))
        reference(entry, 'ttf', 32 * 1024 * 1024)
        reference(entry.get('license'), 'txt', 65536)
        if entry['sha256'] in seen:
            raise ValueError('duplicate supplemental font')
        seen.add(entry['sha256'])
    return root, files


def prepare(publication, output, *, object_prefix, url_prefix):
    publication = Path(publication).resolve()
    output = Path(output).resolve()
    data = (publication / 'latest.json').read_bytes()
    root, references = parse_catalog(data)
    files = {}
    for name, ref in references.items():
        path = (publication / name).resolve()
        if not path.is_relative_to(publication):
            raise ValueError('font object escapes publication')
        raw = path.read_bytes()
        if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('font object integrity mismatch')
        files[name] = raw
    if (not object_prefix.endswith('/') or object_prefix.startswith('/') or not url_prefix.startswith('/')
            or not url_prefix.endswith('/') or any(part in {'.', '..'} for value in [object_prefix, url_prefix] for part in value.split('/'))
            or any(char in object_prefix + url_prefix for char in '\\%?#:')):
        raise ValueError('publication prefixes must be directories')
    rows = []
    for name, raw in sorted(files.items()):
        logical = 'fonts/' + name
        target = output / 'staging' / logical
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('staging bytes differ; choose a fresh directory')
        if not target.exists():
            target.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        rows.append({'path': logical, 'urlPath': url_prefix + logical, 'objectKey': object_prefix + logical,
                     'source': str(target), 'sourceRaw': str(target), 'staging': str(target),
                     'sha256': sha, 'decodedSha256': sha, 'bytes': len(raw), 'decodedBytes': len(raw),
                     'contentEncoding': 'identity', 'contentType': content_type(name),
                     'cacheControl': cache_control(name)})
    result = {'schema': 'moly-object-upload/1', 'deliveryMode': 'fixed', 'objectPrefix': object_prefix,
              'urlPrefix': url_prefix, 'snapshots': [], 'fontCatalog': {'path': 'fonts/latest.json',
              'revision': root['revision'], 'sha256': hashlib.sha256(data).hexdigest()},
              'files': rows, 'totals': {'files': len(rows), 'bytes': sum(row['bytes'] for row in rows)}}
    (output / 'upload-manifest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-prefix', default='sekai-extra-assets/')
    parser.add_argument('--url-prefix', default='/moly/')
    args = parser.parse_args()
    result = prepare(args.publication, args.output, object_prefix=args.object_prefix, url_prefix=args.url_prefix)
    print(json.dumps({'fontCatalog': result['fontCatalog'], 'totals': result['totals']}))
