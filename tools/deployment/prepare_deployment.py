"""Audit and stage a Moly browser release. This script never contacts a cloud API.

The exporter owns runtime membership. This applies a browser delivery policy to
that membership, closes concrete GLTF/rig/audio/base references, and leaves every
source asset untouched. Objects retain their original decoded bytes and URLs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import sys
from urllib.parse import unquote, urlsplit


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    os.replace(temporary, path)


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'Invalid relative asset name: {value!r}')
    parts = PurePosixPath(value).parts
    if value.startswith('/') or any(p in ('..', '.') for p in parts) or ':' in value:
        raise ValueError(f'Unsafe asset name: {value!r}')
    return value


def local_reference(root, owner, uri):
    if uri.startswith('data:'):
        return None
    split = urlsplit(uri)
    if split.scheme or split.netloc:
        raise ValueError(f'External URI in {owner}: {uri}')
    path = (root / owner).parent / unquote(split.path)
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f'Reference escapes source root: {owner}: {uri}')
    return resolved.relative_to(root).as_posix()


def gltf_document(path):
    if path.suffix.lower() == '.gltf':
        return json.loads(path.read_bytes())
    with path.open('rb') as stream:
        header = stream.read(20)
        if len(header) != 20 or header[:4] != b'glTF':
            raise ValueError(f'Invalid GLB: {path}')
        version, total, length, kind = struct.unpack('<IIII', header[4:])
        if version != 2 or kind != 0x4e4f534a or length > total - 20:
            raise ValueError(f'Invalid GLB JSON chunk: {path}')
        return json.loads(stream.read(length))


def audit_assets(root, runtime_paths):
    root = root.resolve()
    members = runtime_paths(root)
    original_count = len(members)
    original_bytes = sum(p.stat().st_size for p in members.values())
    print(f'Exporter membership: {original_count} files; checking concrete dependencies', flush=True)
    excluded = {name: path for name, path in members.items() if path.suffix.lower() in {'.wav', '.acb', '.awb'}}
    members = {name: path for name, path in members.items() if name not in excluded}
    missing, additions = [], []

    def require(name, reason):
        safe_relative(name)
        file = (root / name).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            missing.append({'path': name, 'reason': reason})
            return
        if name not in members:
            members[name] = file
            additions.append({'path': name, 'reason': reason})
            excluded.pop(name, None)

    base = json.loads((root / 'browser-base.json').read_bytes())
    base_errors = []
    for row in base['files']:
        require(row['path'], 'browser-base')
        path = root / row['path']
        if path.is_file():
            raw = path.read_bytes()
            if len(raw) != row['decodedBytes'] or digest(raw) != row['sha256']:
                base_errors.append(row['path'])

    audio = {'streams': 0, 'diagnostics': 0, 'diagnosticRows': [], 'oggStreams': 0, 'wavFallbacks': [], 'routes': []}
    for name, prefix in [('phenomena/audio/loop.json', 'phenomena/'), ('ui/audio/loop.json', '')]:
        require(name, 'runtime audio routing')
        doc = json.loads((root / name).read_bytes())
        audio['routes'].append({'path': name, 'packages': len(doc['packages'])})
        for package in doc['packages']:
            for stream in package['streams']:
                if 'error' in stream:
                    if set(stream) != {'cue', 'error'}:
                        raise ValueError(f'Malformed diagnostic audio stream: {name}, {stream}')
                    audio['diagnostics'] += 1
                    audio['diagnosticRows'].append({'route': name, 'package': package['package'], **stream})
                    continue
                source = stream.get('ogg') or stream.get('wav')
                if not source:
                    missing.append({'path': None, 'reason': 'audio stream lacks both ogg and wav', 'package': package['package'], 'cue': stream['cue']})
                    continue
                target = prefix + source
                audio['streams'] += 1
                if stream.get('ogg'):
                    audio['oggStreams'] += 1
                else:
                    audio['wavFallbacks'].append({'path': target, 'package': package['package'], 'cue': stream['cue']})
                require(target, f"audio cue {stream['cue']} in {package['package']}")

    # Every model is checked, including lazily loaded furniture and scene GLBs.
    model_count = reference_count = 0
    for name in list(members):
        path = members[name]
        if path.suffix.lower() in {'.glb', '.gltf'}:
            model_count += 1
            if model_count % 2000 == 0:
                print(f'Checked {model_count} model documents', flush=True)
            document = gltf_document(path)
            for row in document.get('images', []) + document.get('buffers', []):
                if uri := row.get('uri'):
                    ref = local_reference(root, name, uri)
                    if ref:
                        reference_count += 1
                        require(ref, f'GLTF URI in {name}')
        elif name.endswith('.rig.json'):
            for uri in json.loads(path.read_bytes()).get('textures', []):
                ref = local_reference(root, name, uri)
                if ref:
                    reference_count += 1
                    require(ref, f'rig texture in {name}')

    stats = {
        'source': str(root), 'producerRuntimeFiles': original_count, 'producerRuntimeBytes': original_bytes,
        'files': len(members), 'bytes': sum(p.stat().st_size for p in members.values()),
        'excludedFiles': len(excluded), 'excludedBytes': sum(p.stat().st_size for p in excluded.values()),
        'excludedByExtension': dict(Counter(Path(name).suffix for name in excluded)),
        'browserBaseFiles': len(base['files']), 'browserBaseMismatch': base_errors,
        'audio': audio, 'modelFilesChecked': model_count, 'externalModelReferences': reference_count,
        'additionalDependencies': additions, 'missing': missing,
    }
    return members, stats


def omit_split_controller_source(root, members, report, region, version):
    """Do not publish the native monolith beside its verified browser packages.

    Browser stages read the index and source-preserving package documents.
    Native extraction keeps the original file; only this browser publication
    closure omits it after proving that every package is represented exactly.
    """
    legacy_name = 'fixture-gimmick/gimmicks.json'
    index_name = 'fixture-gimmick/browser-index.json'
    legacy, index_path = members.get(legacy_name), members.get(index_name)
    if legacy is None or index_path is None:
        return
    raw = legacy.read_bytes()
    index = json.loads(index_path.read_bytes())
    if (index.get('schemaVersion') != 1 or index.get('generator') != 'moly-gimmick-split-v1'
            or index.get('region') != region or index.get('gameVersion') != version
            or index.get('sourceSha256') != digest(raw)):
        raise ValueError('Browser controller packages do not match the native source')
    source = json.loads(raw)
    packages = source.get('packages')
    rows = index.get('packages')
    if source.get('version') != 1 or not isinstance(packages, list) or not isinstance(rows, list):
        raise ValueError('Browser controller package coverage is invalid')
    originals = {}
    for package in packages:
        name = package.get('name') if isinstance(package, dict) else None
        if not isinstance(name, str) or not name or name in originals:
            raise ValueError('Native controller source has a duplicate package')
        originals[name] = package
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Browser controller index has an invalid row')
        name, path = row.get('name'), row.get('path')
        if (not isinstance(name, str) or name not in originals or name in seen
                or not isinstance(path, str)
                or not re.fullmatch(r'fixture-gimmick/by-package/[0-9a-f]{64}\.json', path)
                or safe_relative(path) != path):
            raise ValueError('Browser controller index has an invalid package identity')
        file = members.get(path)
        if file is None:
            raise ValueError(f'Browser controller package is absent: {path}')
        body = file.read_bytes()
        if (row.get('sha256') != digest(body) or row.get('decodedBytes') != len(body)):
            raise ValueError(f'Browser controller package differs from its index: {path}')
        document = json.loads(body)
        if (document.get('schemaVersion') != 1 or document.get('region') != region
                or document.get('gameVersion') != version
                or document.get('package') != originals[name]):
            raise ValueError(f'Browser controller package differs from its source: {path}')
        seen.add(name)
    if seen != set(originals):
        raise ValueError('Browser controller index omits a native package')
    base = json.loads((root / 'browser-base.json').read_bytes())
    if any(row.get('path') == legacy_name for row in base.get('files', [])):
        raise ValueError('Browser base still pins the native controller monolith')
    if any(row['path'] == legacy_name for row in report['additionalDependencies']):
        raise ValueError('Another browser resource still references the controller monolith')
    members.pop(legacy_name)
    report['files'] -= 1
    report['bytes'] -= len(raw)
    report['excludedFiles'] += 1
    report['excludedBytes'] += len(raw)
    report['excludedByExtension']['.json'] = report['excludedByExtension'].get('.json', 0) + 1
    report['omittedLegacyGimmickBytes'] = len(raw)


def browser_membership(root, region, version, runtime_paths):
    """The browser publication's asset membership of one release root: the
    exporter's runtime paths closed over their concrete references, without
    the native controller monolith. Also returns the audit report."""
    members, report = audit_assets(root, runtime_paths)
    omit_split_controller_source(root, members, report, region, version)
    master = json.loads((root / 'mysekai-fixtures.json').read_bytes())
    if master.get('region') != region or master.get('gameVersion') != version:
        raise ValueError(f'Source master region/version differs from {region} {version}')
    return members, report


# The pointers: the stage host's manifest.json, its scope worker and the game
# page's game/manifest.json. They are rewritten in place by a later switch,
# so they are served for revalidation; every other object is immutable.
MUTABLE = ('manifest.json', 'cache-worker.mjs', 'game/manifest.json')
STORE_OBJECT = re.compile(r'(?:asset-store/(?:blobs/[0-9a-f]{2}|packages|catalogs)|catalog-store)/([0-9a-f]{64})\.[a-z0-9]+')


def add_packed_snapshot(publication, snapshot, add):
    """Add every object the snapshot's pinned v2 asset catalog reaches and
    return its logical asset paths. Only that catalog's packages and blobs
    are published; other releases and mutable selectors in the store are not."""
    store = publication / 'asset-store'
    catalog_id = snapshot.get('assetCatalog', '')
    if not re.fullmatch(r'[0-9a-f]{64}', catalog_id) or snapshot.get('assets') != '/moly/asset-store/':
        raise ValueError(f"Packed snapshot {snapshot['id']} has no pinned asset catalog")
    catalog_name = f'catalogs/{catalog_id}.json'
    catalog_bytes = (store / catalog_name).read_bytes()
    catalog = json.loads(catalog_bytes)
    if (digest(catalog_bytes) != catalog_id or catalog.get('schema') != 'moly-asset-packs/2'
            or catalog.get('region') != snapshot['region']):
        raise ValueError(f"Asset catalog {catalog_id} is not the pinned {snapshot['region']} release")
    add('asset-store/' + catalog_name, store / catalog_name)
    paths = {}
    for package in catalog['packages']:
        manifest_name = safe_relative(package['manifest'])
        manifest_bytes = (store / manifest_name).read_bytes()
        if not re.fullmatch(r'packages/[0-9a-f]{64}\.json', manifest_name) or digest(manifest_bytes) != manifest_name[9:-5]:
            raise ValueError(f'Package manifest does not match its address: {manifest_name}')
        add('asset-store/' + manifest_name, store / manifest_name)
        for entry in json.loads(manifest_bytes)['entries']:
            blob = 'blobs/' + safe_relative(entry['blob'])
            if (store / blob).stat().st_size != entry['blob_bytes'] or entry['path'] in paths:
                raise ValueError(f"Store entry is inconsistent: {entry['path']}")
            add('asset-store/' + blob, store / blob)
            paths[entry['path']] = entry
    return paths


# Store blobs have no media suffix; a card image loaded cross-origin by <img>
# still needs its image type.
BLOB_TYPES = ((b'\x89PNG\r\n\x1a\n', 'image/png'), (b'OggS', 'audio/ogg'), (b'glTF', 'model/gltf-binary'))


def content_type(name, raw_source=None):
    if raw_source is not None and name.startswith('asset-store/blobs/'):
        with open(raw_source, 'rb') as file:
            head = file.read(8)
        return next((kind for signature, kind in BLOB_TYPES if head.startswith(signature)), 'application/octet-stream')
    overrides = {'.mjs': 'text/javascript', '.js': 'text/javascript', '.wasm': 'application/wasm',
                 '.json': 'application/json', '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json',
                 '.ogg': 'audio/ogg', '.css': 'text/css', '.html': 'text/html', '.ktx2': 'image/ktx2'}
    return overrides.get(Path(name).suffix.lower(), mimetypes.guess_type(name)[0] or 'application/octet-stream')


def delivery_profile(mode, generate):
    from pack.http_delivery import policy_profile
    profile = policy_profile(mode, generate)
    if mode == 'best' and profile['brotli'] is None:
        print('WARNING: Brotli unavailable; this best run can select only gzip/identity. '
              'Install the deployment extra: python -m pip install -e ".[deployment]"', flush=True)
    return profile



def publication_release_files(publication, manifest, url_prefix):
    """Verify only releases selected by the stage manifest and game pointer."""
    identifier = re.compile(r'[a-z0-9][a-z0-9._-]{0,95}')
    primary = manifest['release']['id']
    if not isinstance(primary, str) or not identifier.fullmatch(primary):
        raise ValueError('Invalid primary release identity')
    releases = [primary]
    game_path = publication / 'game/manifest.json'
    game = json.loads(game_path.read_bytes()) if game_path.is_file() else None
    if game is not None:
        record = game.get('release', {})
        game_id = record.get('id')
        expected_snapshots = [{
            'id': row['id'], 'region': row['region'], 'version': row['version'],
            'assets': row['assets'], 'packs': row.get('packs') is True,
            'assetCatalog': row.get('assetCatalog') if row.get('packs') is True else None,
        } for row in manifest['snapshots']]
        if (game.get('publisher') != 'moly-release-artifact-v1' or game.get('schemaVersion') != 1
                or game.get('prefix') != url_prefix
                or not isinstance(game_id, str) or not identifier.fullmatch(game_id)
                or record.get('module') != f'{url_prefix}releases/{game_id}/game-boot.mjs'
                or record.get('abi') != 2
                or not record.get('coordinateContract')
                or record['coordinateContract'] != manifest['release'].get('coordinateContract')
                or game.get('snapshots') != expected_snapshots):
            raise ValueError('Game pointer does not match the publication contract and snapshots')
        if game_id not in releases:
            releases.append(game_id)
    files, integrity_count = {}, 0
    for release_id in releases:
        root = publication / 'releases' / release_id
        if root.is_symlink() or not root.resolve().is_relative_to(publication.resolve()):
            raise ValueError('Release root escapes publication')
        integrity_file = root / 'integrity.json'
        if integrity_file.is_symlink():
            raise ValueError('Release integrity must be a regular local file')
        integrity = json.loads(integrity_file.read_bytes())
        if ('releaseId' in integrity and integrity['releaseId'] != release_id
                or 'contractVersion' in integrity and integrity['contractVersion'] != manifest.get('schemaVersion')
                or 'coordinateContract' in integrity and integrity['coordinateContract'] != manifest['release'].get('coordinateContract')):
            raise ValueError(f'Release integrity identity mismatch: {release_id}')
        hashes = integrity.get('hashes')
        if not isinstance(hashes, dict) or not hashes:
            raise ValueError(f'Release has no integrity membership: {release_id}')
        if game is not None and game['release']['id'] == release_id:
            if (integrity.get('schemaVersion') != 1 or integrity.get('releaseId') != release_id
                    or integrity.get('contractVersion') != manifest.get('schemaVersion')
                    or integrity.get('coordinateContract') != game['release']['coordinateContract']
                    or 'game-boot.mjs' not in hashes):
                raise ValueError('Game release has no matching integrity contract')
        for name, expected_hash in hashes.items():
            safe_relative(name)
            if (not re.fullmatch(r'[a-f0-9]{64}', expected_hash or '')
                    or name == 'integrity.json' or name.endswith(('.gz', '.br'))):
                raise ValueError(f'Invalid release integrity member: {name}')
            source = root / name
            if (not source.resolve().is_relative_to(root.resolve())
                    or source.is_symlink() or any(parent.is_symlink() for parent in source.parents if parent != publication)):
                raise ValueError(f'Release member escapes publication: {name}')
            if digest(source.read_bytes()) != expected_hash:
                raise RuntimeError(f'Release integrity mismatch: {release_id}/{name}')
            files[f'releases/{release_id}/{name}'] = source
            integrity_count += 1
        files[f'releases/{release_id}/integrity.json'] = integrity_file
    return releases, files, integrity_count

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', type=Path, required=True)
    parser.add_argument('--exporter', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', action='append', default=[], metavar='REGION=PATH',
                        help='Explicit audited asset source for a publication with separate immutable mounts')
    parser.add_argument('--object-prefix', default='moly/alpha/')
    parser.add_argument('--url-prefix', default='/moly/')
    parser.add_argument('--provider', choices=['oss', 's3'], default='oss',
                        help='Manifest adapter; s3 allows an explicitly empty object prefix')
    parser.add_argument('--stage', action='store_true', help='Create immutable hardlink staging grouped by content encoding')
    parser.add_argument('--encoding', choices=['identity', 'gzip', 'best'], default='best', help='Generate/select verified lossless HTTP representations; best allows Brotli')
    parser.add_argument('--existing-encodings-only', action='store_true', help='Audit/reuse existing sidecars without generating new representations')
    parser.add_argument('--engine-reads', action='append', default=[], metavar='REGION=FILE',
                        help='Request or console log of a browser run; the audit fails on a requested path the membership lacks (engine_reads.py)')
    args = parser.parse_args()
    publication = args.publication.resolve()
    output = args.output.resolve()
    if output.is_relative_to(publication) or publication.is_relative_to(output):
        raise ValueError('Output and publication roots must be disjoint')
    if not args.url_prefix.startswith('/') or not args.url_prefix.endswith('/'):
        raise ValueError('URL prefix must begin and end with /')
    prefix = ('' if args.provider == 's3' and args.object_prefix == ''
              else safe_relative(args.object_prefix.rstrip('/')) + '/')
    sys.path.insert(0, str(args.exporter.resolve() / 'src'))
    from pack.groups import runtime_paths
    from pack.http_delivery import choose_representation
    profile = delivery_profile(args.encoding, not args.existing_encodings_only)
    manifest = json.loads((publication / 'manifest.json').read_bytes())
    source_overrides = {}
    for selector in args.source:
        region, separator, source_path = selector.partition('=')
        if not separator or region not in {'cn', 'jp'} or region in source_overrides:
            raise ValueError('Each --source must uniquely specify cn=PATH or jp=PATH')
        source_overrides[region] = Path(source_path).resolve()
        if output.is_relative_to(source_overrides[region]) or source_overrides[region].is_relative_to(output):
            raise ValueError('Output and source override roots must be disjoint')
    if args.engine_reads:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from engine_reads import engine_read_audit, parse_region_pairs
        engine_logs = parse_region_pairs(args.engine_reads, '--engine-reads')
    files = {}
    def add(logical, source):
        safe_relative(logical)
        if not source.is_file():
            raise FileNotFoundError(source)
        files[logical] = source
    # Descriptors keep their logical `/moly/` paths in every layout: the host
    # validates manifest.json and maps `/moly/<tail>` onto its configured
    # resource base, and the runtime resolves snapshot.json the same way.
    add('manifest.json', publication / 'manifest.json')
    add('cache-worker.mjs', publication / 'cache-worker.mjs')
    # A publication with the game profile carries the game page's pointer.
    if (publication / 'game' / 'manifest.json').is_file():
        add('game/manifest.json', publication / 'game' / 'manifest.json')
    release_ids, release_files, integrity_count = publication_release_files(publication, manifest, args.url_prefix)
    release_id = release_ids[0]
    for logical, source in release_files.items():
        add(logical, source)
    reports = {}
    for snapshot in manifest['snapshots']:
        snapshot_id = safe_relative(snapshot['id'])
        snapshot_root = publication / 'snapshots' / snapshot_id
        root = source_overrides.get(snapshot['region'], (snapshot_root / 'assets').resolve())
        for field, name in [('fixtureMasterSha256', 'mysekai-fixtures.json'),
                            ('controllerIndexSha256', 'fixture-gimmick/browser-index.json'),
                            ('baseDescriptorSha256', 'browser-base.json')]:
            expected = snapshot.get('provenance', {}).get(field)
            if expected and digest((root / name).read_bytes()) != expected:
                raise ValueError(f'Source {name} differs from pinned {snapshot_id} provenance')
        print(f"Auditing {snapshot['region']}: {root}", flush=True)
        members, report = browser_membership(root, snapshot['region'], snapshot['version'], runtime_paths)
        reports[snapshot['region']] = report
        if args.engine_reads and snapshot['region'] in engine_logs:
            report['engineReads'] = engine_read_audit(root, members, engine_logs[snapshot['region']])
        if snapshot.get('packs'):
            # The audited source proves closure; the store must hold exactly it.
            if snapshot['region'] not in source_overrides:
                raise ValueError(f"Packed snapshot {snapshot_id} needs --source {snapshot['region']}=PATH for its closure audit")
            stored = add_packed_snapshot(publication, snapshot, add)
            if set(stored) != set(members):
                raise ValueError(f'Pinned asset catalog of {snapshot_id} does not hold exactly the audited membership')
            report['storeEntries'] = len(stored)
        else:
            for name, file in members.items():
                add(f'snapshots/{snapshot_id}/assets/{name}', file)
        index_path = snapshot_root / 'catalog' / 'index.json'
        index = json.loads(index_path.read_bytes()) if index_path.is_file() else {}
        if index.get('schemaVersion') == 2:
            # Entry details are shared, content-addressed bundles outside the snapshot.
            for path in index['details']:
                if not re.fullmatch(r'/moly/catalog-store/[0-9a-f]{64}\.json', path):
                    raise ValueError(f'Invalid detail bundle path in {snapshot_id}')
                add(path[len('/moly/'):], publication / path[len('/moly/'):])
        for child in snapshot_root.iterdir():
            if child.name == 'assets':
                continue
            for file in (child.rglob('*') if child.is_dir() else [child]):
                if file.is_file() and file.suffix not in {'.gz', '.br'}:
                    add(file.relative_to(publication).as_posix(), file)
        print(json.dumps({snapshot['region']: {k: report[k] for k in ['files', 'bytes', 'excludedBytes', 'missing', 'browserBaseMismatch']},
                          **({'engineReadsUnpublished': report['engineReads']['unpublished']} if 'engineReads' in report else {})}), flush=True)
    report_path = output / 'audit.json'
    write_json(report_path, {'regions': reports, 'releaseIntegrityFilesChecked': integrity_count})
    if args.engine_reads and set(engine_logs) - set(reports):
        raise ValueError(f'--engine-reads names a region without a snapshot: {sorted(set(engine_logs) - set(reports))}')
    if any(row['missing'] or row['browserBaseMismatch'] or row.get('engineReads', {}).get('unpublished')
           for row in reports.values()):
        raise RuntimeError(f'Resource closure is incomplete; see {report_path}')

    entries, invalid_sidecars = [], []
    # All preferred representations are validated against the exact raw bytes.
    for index, (logical, source) in enumerate(sorted(files.items())):
        representation = choose_representation(source, logical, output / 'representations',
            mode=args.encoding, generate=not args.existing_encodings_only)
        selected, encoding, body = representation.source, representation.encoding, representation.body
        invalid_sidecars.extend(representation.invalid_sidecars)
        entry = {
            'path': logical, 'urlPath': args.url_prefix + logical, 'objectKey': prefix + logical,
            'contentType': content_type(logical, representation.raw_source), **representation.manifest_fields,
            'cacheControl': 'public, max-age=60, must-revalidate' if logical in MUTABLE else 'public, max-age=31536000, immutable',
        }
        if encoding != 'identity' or logical.startswith('asset-store/'):
            entry['cacheControl'] += ', no-transform'
        address = STORE_OBJECT.fullmatch(logical)
        if address and entry['decodedSha256'] != address[1]:
            raise RuntimeError(f'Content-addressed object does not match its name: {logical}')
        if args.stage:
            # No write is ever made through a hardlink. Existing stages are
            # accepted only when bytes are identical, otherwise fail closed.
            target = output / 'staging' / encoding / logical
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.stat().st_size != len(body) or digest(target.read_bytes()) != entry['sha256']:
                    raise RuntimeError(f'Staging file differs; use a fresh output folder: {target}')
            else:
                try:
                    os.link(selected, target)
                except OSError:
                    shutil.copyfile(selected, target)
            entry['staging'] = str(target)
        entries.append(entry)
        if index and index % 10000 == 0:
            print(f'Hashed {index}/{len(files)} objects', flush=True)
    totals = {'files': len(entries), 'bytes': sum(r['bytes'] for r in entries),
              'decodedBytes': sum(r['decodedBytes'] for r in entries),
              'byEncoding': {kind: {'files': sum(r['contentEncoding'] == kind for r in entries),
                                   'bytes': sum(r['bytes'] for r in entries if r['contentEncoding'] == kind)}
                             for kind in ['identity', 'gzip', 'br']}}
    result = {'schema': 'moly-object-upload/1' if args.provider == 's3' else 'moly-oss-upload/1',
              'deliveryMode': 'fixed', 'bucket': None, 'objectPrefix': prefix, 'urlPrefix': args.url_prefix,
              'release': release_id, 'releases': release_ids, 'snapshots': [s['id'] for s in manifest['snapshots']],
              'encodingPolicy': args.encoding, 'httpDelivery': profile,
              'totals': totals, 'invalidSidecarsSkipped': invalid_sidecars,
              'notes': ['No cloud writes performed.', 'Upload Content-Encoding exactly as listed; identity means omit that header.',
                        'Each URL has one fixed representation: clients must accept requiredAcceptEncoding; there is no Accept-Encoding negotiation.',
                        'Vary is not required for fixed delivery. CDN must preserve pre-encoded objects without a second compression.',
                        'Existing immutable objects must not be overwritten to change representation; use new release/snapshot identities.',
                        'Upload immutable releases/snapshots before switching manifest.json.',
                        'The public resource base must map urlPrefix to the bucket/objectPrefix without altering suffixes.',
                        'Staging hardlinks must never be edited in place.'], 'files': entries}
    write_json(output / 'upload-manifest.json', result)
    write_json(report_path, {'regions': reports, 'totals': totals, 'invalidSidecarsSkipped': invalid_sidecars,
                            'releaseIntegrityFilesChecked': integrity_count})
    print(json.dumps({'totals': totals, 'invalidSidecars': len(invalid_sidecars), 'output': str(output)}), flush=True)


if __name__ == '__main__':
    main()
