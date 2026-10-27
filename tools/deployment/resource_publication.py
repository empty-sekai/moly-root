"""The immutable asset catalogue closure, independent of an application release."""
from pathlib import Path
import hashlib
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from core.jp import public_release_provenance
from pack.identity import canonical_bytes
from pack.limits import MAX_RUNTIME_BLOB_BYTES, MAX_RUNTIME_DOCUMENT_BYTES
from pack.verify import strict_json, verify_catalog_data, _decode_content

HASH = re.compile(r'[a-f0-9]{64}')
CACHE_CONTROL = 'public, max-age=31536000, immutable, no-transform'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_store_file(store, relative, maximum):
    path = store / relative
    if (not relative or relative.startswith('/') or '\\' in relative or ':' in relative
            or any(part in {'', '.', '..'} for part in relative.split('/'))):
        raise ValueError('invalid store object path')
    for component in (path, *path.parents):
        if component == store:
            break
        if component.is_symlink() or getattr(component, 'is_junction', lambda: False)():
            raise ValueError('linked store object path')
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(store):
        raise ValueError('store object escaped root')
    with resolved.open('rb') as stream:
        # Read to EOF or the first excess byte without allocating the entire
        # runtime budget for every small object in the catalogue.
        remaining = maximum + 1
        chunks = []
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
    if len(data) > maximum:
        raise ValueError('store object exceeds runtime budget')
    return resolved, data


def catalog_closure(store, catalog_id):
    """Verify the catalogue, package graph, source identity and all stored blobs."""
    store = Path(store).resolve(strict=True)
    if not isinstance(catalog_id, str) or not HASH.fullmatch(catalog_id):
        raise ValueError('invalid resource catalogue hash')
    name = f'catalogs/{catalog_id}.json'
    path, raw = read_store_file(store, name, MAX_RUNTIME_DOCUMENT_BYTES)
    catalog = strict_json(raw)
    if (digest(raw) != catalog_id or catalog.get('schema') != 'moly-asset-packs/2'
            or catalog.get('region') not in {'jp', 'cn', 'tw', 'en', 'kr'}
            or not re.fullmatch(r'\d+\.\d+\.\d+', catalog.get('version', ''))
            or raw != canonical_bytes(catalog)):
        raise ValueError('invalid resource catalogue identity')
    if public_release_provenance(catalog.get('provenance')) != catalog.get('provenance'):
        raise ValueError('resource catalogue provenance is not portable')
    errors, info = verify_catalog_data(catalog, store)
    if errors:
        raise ValueError('invalid resource catalogue closure: ' + '; '.join(errors[:8]))
    objects = {name: {'source': path, 'sha256': catalog_id, 'bytes': len(raw)}}
    entries = {}
    for package in catalog['packages']:
        name = package['manifest']
        path, raw = read_store_file(store, name, MAX_RUNTIME_DOCUMENT_BYTES)
        if digest(raw) != Path(name).stem:
            raise ValueError('package changed during resource verification')
        objects[name] = {'source': path, 'sha256': digest(raw), 'bytes': len(raw)}
        for entry in strict_json(raw)['entries']:
            # The v2 native and Web readers share this representation contract.
            # The archive verifier also accepts legacy Brotli forms, but those
            # cannot be published as a usable runtime catalogue.
            if (entry['codec'] not in {'identity', 'gzip'}
                    or entry['http_encoding'] != 'identity' or entry['xf'] is not None):
                raise ValueError('resource catalogue uses a representation unsupported by native/Web runtime')
            entries[entry['path']] = entry
            name = 'blobs/' + entry['blob']
            path, raw = read_store_file(store, name, MAX_RUNTIME_BLOB_BYTES)
            if len(raw) != entry['blob_bytes'] or digest(raw) != entry['blob_sha256']:
                raise ValueError('blob changed during resource verification')
            objects[name] = {'source': path, 'sha256': entry['blob_sha256'], 'bytes': entry['blob_bytes']}
    source_entry = entries.get('source.json')
    provenance = catalog.get('provenance', {}).get('runtime_source')
    if source_entry is None or not isinstance(provenance, dict):
        raise ValueError('resource catalogue requires its runtime source document')
    _, source_blob = read_store_file(store, 'blobs/' + source_entry['blob'], MAX_RUNTIME_BLOB_BYTES)
    source_raw = _decode_content(source_blob, source_entry['codec'], source_entry['http_encoding'], source_entry['bytes'])
    if strict_json(source_raw) != provenance:
        raise ValueError('catalogue provenance differs from the packaged source document')
    source_identity = provenance.get('source', {})
    if (source_identity.get('region') != catalog['region']
            or source_identity.get('appVersion') != catalog['version']):
        raise ValueError('packaged source region/version differs from its resource catalogue')
    identity = {'path': f'asset-store/catalogs/{catalog_id}.json', 'sha256': catalog_id,
                'bytes': objects[f'catalogs/{catalog_id}.json']['bytes'],
                'region': catalog['region'], 'version': catalog['version'],
                'provenance': catalog['provenance']}
    return identity, objects, entries, info


def validate_upload(doc):
    """Revalidate actual sources and representations before selecting any upload."""
    from prepare_deployment import content_type
    identity = doc['resourceCatalog']
    root_rows = [row for row in doc['files'] if row['path'] == identity['path']]
    if len(root_rows) != 1:
        raise ValueError('resource catalogue row is absent or duplicated')
    raw_path = Path(root_rows[0]['sourceRaw']).absolute()
    if raw_path.parent.name != 'catalogs' or raw_path.name != identity['sha256'] + '.json':
        raise ValueError('resource catalogue source is not its addressed store object')
    store = raw_path.parent.parent.resolve(strict=True)
    actual, objects, _, _ = catalog_closure(store, identity['sha256'])
    if actual != identity:
        raise ValueError('resource catalogue identity or provenance changed')
    rows = {row['path']: row for row in doc['files']}
    expected = {'asset-store/' + name for name in objects}
    if rows.keys() != expected:
        raise ValueError('resource publication object closure mismatch')
    url_prefix = doc.get('urlPrefix')
    if (not isinstance(url_prefix, str) or not url_prefix.startswith('/') or not url_prefix.endswith('/')
            or any(part in {'.', '..'} for part in url_prefix.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 or c in '\\%?#:' for c in url_prefix)):
        raise ValueError('invalid resource URL prefix')
    checked = set()
    for name, expected in objects.items():
        logical = 'asset-store/' + name
        row = rows[logical]
        media_type = content_type(logical, expected['source'])
        reused = None
        if 'representationReuse' in row:
            from verified_representations import validate_reused_row
            reused = validate_reused_row(row, expected['source'], media_type)
        if (Path(row['sourceRaw']).absolute() != expected['source']
                or row['decodedSha256'] != expected['sha256'] or row['decodedBytes'] != expected['bytes']
                or (reused is None and row['bytes'] > expected['bytes'])
                or row['contentType'] != content_type(logical, expected['source'])
                or (reused is None and row['cacheControl'] != CACHE_CONTROL) or row.get('urlPath') != url_prefix + logical):
            raise ValueError('resource object metadata differs from its catalogue')
        for alias in [row.get('source'), *([row['staging']] if 'staging' in row else [])]:
            if not isinstance(alias, str) or not alias:
                raise ValueError('invalid resource object source')
            path = Path(alias).resolve(strict=True)
            key = (path, row['sha256'], row['bytes'], expected['sha256'], expected['bytes'], row['contentEncoding'])
            if key in checked:
                continue
            with path.open('rb') as stream:
                body = stream.read(row['bytes'] + 1)
            if len(body) != row['bytes'] or digest(body) != row['sha256']:
                raise ValueError('local resource representation changed')
            # The HTTP representation expands to the stored blob/package bytes.
            # A pack codec, if present, is subsequently decoded by the runtime.
            encoding = row['contentEncoding']
            decoded = _decode_content(body, 'gzip' if encoding == 'gzip' else 'identity',
                                      'br' if encoding == 'br' else 'identity', expected['bytes'])
            if len(decoded) != expected['bytes'] or digest(decoded) != expected['sha256']:
                raise ValueError('resource HTTP representation differs from the addressed object')
            checked.add(key)
