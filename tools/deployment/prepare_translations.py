"""Prepare an independent translation publication for the existing uploaders.

No resource snapshot is read or changed. This command performs no cloud writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

HASH = re.compile(r'[a-f0-9]{64}')


def prepare(publication, output, *, object_prefix, url_prefix):
    publication = Path(publication).resolve()
    output = Path(output).resolve()
    root_bytes = (publication / 'latest.json').read_bytes()
    if len(root_bytes) > 2 * 1024 * 1024:
        raise ValueError('translation root exceeds limit')
    root = json.loads(root_bytes)
    if (root.get('schema') != 'moly-text-publication/1' or root.get('region') not in {'jp', 'cn', 'tw', 'en', 'kr'}
            or root.get('locale') not in {'zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'ko-KR'}
            or not HASH.fullmatch(root.get('revision', '')) or root.get('routing') != 'sha256-utf8-prefix-2'
            or not isinstance(root.get('buckets'), dict) or len(root['buckets']) > 256
            or len(root.get('searchBuckets', {})) > 16 or root.get('fonts')):
        raise ValueError('invalid translation publication identity')
    base = f"translations/{root['region']}/{root['locale']}/"
    files = {'latest.json': root_bytes, f"revisions/{root['revision']}.json": root_bytes}

    def read(ref):
        if (not isinstance(ref, dict) or not HASH.fullmatch(ref.get('sha256', ''))
                or ref.get('path') != f"objects/{ref['sha256']}.json"
                or type(ref.get('bytes')) is not int or not 0 < ref['bytes'] <= 2 * 1024 * 1024):
            raise ValueError('invalid translation object reference')
        path = (publication / ref['path']).resolve()
        if not path.is_relative_to(publication):
            raise ValueError('translation path escaped publication')
        data = path.read_bytes()
        if len(data) != ref['bytes'] or hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise ValueError('translation object integrity mismatch')
        files[ref['path']] = data
        return json.loads(data)

    for prefix, ref in root['buckets'].items():
        if not re.fullmatch('[a-f0-9]{2}', prefix):
            raise ValueError('invalid translation route prefix')
        routes = read(ref)
        if routes.get('schema') != 'moly-text-translation-routes/1':
            raise ValueError('invalid translation routes')
        for key, entry in routes['scripts'].items():
            if hashlib.sha256(key.encode()).hexdigest()[:2] != prefix:
                raise ValueError('translation script in wrong bucket')
            overlay = read(entry)
            if (overlay.get('schema') != 'moly-text-overlay/1' or overlay.get('sourceRegion') != root['region']
                    or overlay.get('locale') != root['locale']):
                raise ValueError('translation overlay scope mismatch')
    for prefix, ref in root.get('searchBuckets', {}).items():
        search = read(ref)
        if (not re.fullmatch('[a-f0-9]', prefix) or search.get('schema') != 'moly-text-translation-search/1'
                or any(hashlib.sha256(key.encode()).hexdigest()[:1] != prefix for key in search['scripts'])):
            raise ValueError('invalid translation search shard')
    if (publication / f"revisions/{root['revision']}.json").read_bytes() != root_bytes:
        raise ValueError('archived publication differs from latest')
    if ((object_prefix and not object_prefix.endswith('/')) or object_prefix.startswith('/') or not url_prefix.startswith('/') or not url_prefix.endswith('/')
            or any(part in {'.', '..'} for prefix in [object_prefix, url_prefix] for part in prefix.split('/'))
            or any(char in object_prefix + url_prefix for char in '\\%?#:')):
        raise ValueError('publication prefixes must be directories')
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, data in sorted(files.items()):
        logical = base + name
        target = output / 'staging' / logical
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != data:
            raise ValueError('staging bytes differ; choose a fresh output directory')
        if not target.exists():
            target.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        rows.append({'path': logical, 'urlPath': url_prefix + logical, 'objectKey': object_prefix + logical,
                     'source': str(target), 'sourceRaw': str(target), 'staging': str(target),
                     'sha256': sha, 'decodedSha256': sha, 'bytes': len(data), 'decodedBytes': len(data),
                     'contentEncoding': 'identity', 'contentType': 'application/json; charset=utf-8',
                     'cacheControl': 'public, max-age=15, must-revalidate' if name == 'latest.json'
                                     else 'public, max-age=31536000, immutable'})
    result = {'schema': 'moly-object-upload/1', 'deliveryMode': 'fixed', 'objectPrefix': object_prefix,
              'urlPrefix': url_prefix, 'snapshots': [], 'translation': {'path': base + 'latest.json',
              'revision': root['revision'], 'sha256': hashlib.sha256(root_bytes).hexdigest()},
              'totals': {'files': len(rows), 'bytes': sum(row['bytes'] for row in rows)}, 'files': rows}
    (output / 'upload-manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', required=True, type=Path, help='Locale directory containing latest.json')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--object-prefix', default='sekai-extra-assets/')
    parser.add_argument('--url-prefix', default='/moly/')
    args = parser.parse_args()
    result = prepare(args.publication, args.output, object_prefix=args.object_prefix, url_prefix=args.url_prefix)
    print(json.dumps({'translation': result['translation'], 'totals': result['totals']}))


if __name__ == '__main__':
    main()
