"""Prepare one immutable resource catalogue without an app release or pointer."""
from pathlib import Path
import argparse
import os
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'src'))
from pack.groups import runtime_paths
from pack.http_delivery import choose_representation
from prepare_deployment import browser_membership, content_type, delivery_profile, write_json
from resource_publication import catalog_closure, digest, CACHE_CONTROL


def prepare(source, store, catalog_id, output, *, object_prefix='', url_prefix='/moly/', encoding='best', stage=False):
    source, store, output = Path(source).resolve(strict=True), Path(store).resolve(strict=True), Path(output).resolve()
    for left, right in [(source, store), (source, output), (store, output)]:
        if left.is_relative_to(right) or right.is_relative_to(left):
            raise ValueError('source, store and staging directories must be disjoint')
    if ((object_prefix and (not object_prefix.endswith('/') or object_prefix.startswith('/')
                           or any(part in {'', '.', '..'} for part in object_prefix[:-1].split('/'))))
            or not url_prefix.startswith('/') or not url_prefix.endswith('/')
            or any(part in {'.', '..'} for part in url_prefix.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 or c in '\\%?#:' for c in object_prefix + url_prefix)):
        raise ValueError('invalid publication prefix')
    identity, objects, entries, verified = catalog_closure(store, catalog_id)
    members, audit = browser_membership(source, identity['region'], identity['version'], runtime_paths)
    if audit['missing'] or audit['browserBaseMismatch'] or members.keys() != entries.keys():
        raise ValueError('resource catalogue does not hold the complete browser membership')
    for name, path in members.items():
        data = path.read_bytes()
        if len(data) != entries[name]['bytes'] or digest(data) != entries[name]['content_sha256']:
            raise ValueError('resource catalogue content differs from the supplied source: ' + name)
    profile = delivery_profile(encoding, True)
    rows, invalid_sidecars = [], []
    for name, item in sorted(objects.items()):
        logical = 'asset-store/' + name
        representation = choose_representation(item['source'], logical, output / 'representations', mode=encoding)
        if representation.decoded_sha256 != item['sha256'] or representation.decoded_bytes != item['bytes']:
            raise ValueError('store changed while preparing HTTP representations')
        row = {'path': logical, 'objectKey': object_prefix + logical, 'urlPath': url_prefix + logical,
               'contentType': content_type(logical, item['source']), 'cacheControl': CACHE_CONTROL,
               **representation.manifest_fields}
        invalid_sidecars.extend(representation.invalid_sidecars)
        if stage:
            target = output / 'staging' / representation.encoding / logical
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != representation.body:
                    raise ValueError('staging content changed; use a fresh directory')
            else:
                try:
                    os.link(representation.source, target)
                except OSError:
                    with target.open('xb') as stream:
                        stream.write(representation.body)
            row['staging'] = str(target)
            row['source'] = str(target)
        rows.append(row)
    manifest = {'schema': 'moly-object-upload/1', 'deliveryMode': 'fixed', 'objectPrefix': object_prefix,
                'urlPrefix': url_prefix, 'snapshots': [], 'resourceCatalog': identity, 'files': rows,
                'totals': {'files': len(rows), 'bytes': sum(row['bytes'] for row in rows),
                           'decodedBytes': sum(row['decodedBytes'] for row in rows)}, 'deliveryProfile': profile}
    write_json(output / 'audit.json', {'resourceCatalog': identity, 'source': audit,
                                     'verifiedStore': verified, 'invalidSidecars': invalid_sidecars})
    write_json(output / 'upload-manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--object-prefix', default='')
    parser.add_argument('--url-prefix', default='/moly/')
    parser.add_argument('--encoding', choices=['identity', 'gzip', 'best'], default='best')
    parser.add_argument('--stage', action='store_true')
    args = parser.parse_args()
    result = prepare(args.source, args.store, args.catalog, args.output, object_prefix=args.object_prefix,
                     url_prefix=args.url_prefix, encoding=args.encoding, stage=args.stage)
    import json
    print(json.dumps({'resourceCatalog': result['resourceCatalog'], 'totals': result['totals']}))


if __name__ == '__main__':
    main()
