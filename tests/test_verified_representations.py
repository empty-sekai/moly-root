"""An explicit immutable representation index preserves bytes and exact headers."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).parents[1] / 'tools/deployment'))
from verified_representations import VerifiedRepresentations, validate_reused_row, SCHEMA


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, raw=b'original bytes ' * 1000, encoding='gzip'):
    source = tmp_path / 'source.bin'
    source.write_bytes(raw)
    body = gzip.compress(raw, compresslevel=9, mtime=1680000000) if encoding == 'gzip' else raw
    (tmp_path / 'origin-body.bin').write_bytes(body)
    logical = f'asset-store/blobs/{sha(raw)[:2]}/{sha(raw)}.bin'
    row = {'path': logical, 'objectKey': logical, 'body': 'origin-body.bin',
           'sha256': sha(body), 'bytes': len(body), 'decodedSha256': sha(raw), 'decodedBytes': len(raw),
           'headers': {'ContentLength': len(body), 'ContentType': 'application/octet-stream',
                       'ContentEncoding': None if encoding == 'identity' else encoding,
                       'CacheControl': 'public, max-age=31536000, immutable',
                       'Metadata': {'moly-sha256': sha(body), 'moly-decoded-sha256': sha(raw),
                                    'moly-decoded-bytes': str(len(raw))}}}
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'schema': SCHEMA, 'files': [row]}), encoding='utf-8')
    return source, index, row, body


@pytest.mark.parametrize('encoding', ['identity', 'gzip'])
def test_exact_origin_body_and_headers_win_over_fresh_compression(tmp_path, encoding):
    source, index, evidence, body = fixture(tmp_path, encoding=encoding)
    reuse = VerifiedRepresentations(index)
    result = reuse.select(source, evidence['path'], evidence['objectKey'], 'application/octet-stream')
    row = {'path': evidence['path'], 'objectKey': evidence['objectKey'], **result.representation.manifest_fields}
    result.apply(row)
    assert result.representation.body == body
    assert row['cacheControl'] == evidence['headers']['CacheControl']
    assert row['sha256'] == sha(body) and not row['generatedRepresentation']
    assert row['contentEncoding'] == encoding
    assert validate_reused_row(row, source, 'application/octet-stream').representation.body == body
    assert reuse.audit()['reused'] == [evidence['path']]
    assert not reuse.audit()['unused']


@pytest.mark.parametrize('change', ['stored_hash', 'stored_length', 'decoded_hash', 'decoded_length',
                                    'header_length', 'header_metadata', 'unsafe_body', 'duplicate'])
def test_index_rejects_tampered_identity_and_headers(tmp_path, change):
    source, index, row, body = fixture(tmp_path)
    if change == 'stored_hash': row['sha256'] = 'a' * 64
    elif change == 'stored_length': row['bytes'] += 1
    elif change == 'decoded_hash': row['decodedSha256'] = 'a' * 64
    elif change == 'decoded_length': row['decodedBytes'] += 1
    elif change == 'header_length': row['headers']['ContentLength'] += 1
    elif change == 'header_metadata': row['headers']['Metadata']['moly-decoded-bytes'] = 'wrong'
    elif change == 'unsafe_body': row['body'] = '../origin-body.bin'
    rows = [row, copy.deepcopy(row)] if change == 'duplicate' else [row]
    index.write_text(json.dumps({'schema': SCHEMA, 'files': rows}), encoding='utf-8')
    with pytest.raises(ValueError):
        VerifiedRepresentations(index).select(source, row['path'], row['objectKey'], 'application/octet-stream')


def test_actual_body_source_and_compression_are_checked_not_boolean_claims(tmp_path):
    source, index, row, body = fixture(tmp_path)
    (tmp_path / row['body']).write_bytes(body[:-1] + b'x')
    with pytest.raises(ValueError, match='stored body'):
        VerifiedRepresentations(index).select(source, row['path'], row['objectKey'], 'application/octet-stream')
    (tmp_path / row['body']).write_bytes(body)
    source.write_bytes(b'changed source')
    with pytest.raises(ValueError, match='decoded source'):
        VerifiedRepresentations(index).select(source, row['path'], row['objectKey'], 'application/octet-stream')


def test_request_key_and_media_type_cannot_alias_reuse(tmp_path):
    source, index, row, _ = fixture(tmp_path)
    reuse = VerifiedRepresentations(index)
    with pytest.raises(ValueError, match='URL or media type'):
        reuse.select(source, row['path'], 'another-prefix/' + row['objectKey'], 'application/octet-stream')
    with pytest.raises(ValueError, match='URL or media type'):
        reuse.select(source, row['path'], row['objectKey'], 'image/png')
    assert VerifiedRepresentations().select(source, row['path'], row['objectKey'], 'application/octet-stream') is None


def test_upload_rechecks_index_hash_and_exact_selected_fields(tmp_path):
    source, index, evidence, _ = fixture(tmp_path)
    reused = VerifiedRepresentations(index).select(source, evidence['path'], evidence['objectKey'], 'application/octet-stream')
    row = {'path': evidence['path'], 'objectKey': evidence['objectKey'], **reused.representation.manifest_fields}
    reused.apply(row)
    for field, value in [('cacheControl', 'public, max-age=60'), ('sha256', '0' * 64), ('bytes', 1)]:
        altered = {**row, field: value}
        with pytest.raises(ValueError): validate_reused_row(altered, source, 'application/octet-stream')
    index.write_text(index.read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match='index changed'):
        validate_reused_row(row, source, 'application/octet-stream')


def test_resource_prepare_and_validator_preserve_existing_immutable_header(tmp_path, monkeypatch):
    import prepare_resources
    from pack.groups import build_groups
    from resource_publication import validate_upload
    source, store = tmp_path / 'root', tmp_path / 'store'
    files = {'source.json': b'{"source":{"region":"jp","appVersion":"7.0.0"}}',
             'mysekai-fixtures.json': b'{"region":"jp","gameVersion":"7.0.0"}',
             'browser-base.json': b'{"files":[]}', 'phenomena/audio/loop.json': b'{"packages":[]}',
             'ui/audio/loop.json': b'{"packages":[]}', 'ui/test.json': b'{"test":"content"}'}
    for name, body in files.items():
        path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
    build_groups(source, store, '7.0.0', region='jp')
    selector = json.loads((store / 'channels/jp/stable.json').read_bytes())
    catalog = Path(selector['catalog']).stem
    initial = prepare_resources.prepare(source, store, catalog, tmp_path / 'initial', encoding='identity', stage=True)
    existing = next(row for row in initial['files'] if '/blobs/' in row['path'])
    evidence_dir = tmp_path / 'evidence'; evidence_dir.mkdir()
    _, index, evidence, body = fixture(evidence_dir, Path(existing['sourceRaw']).read_bytes(), 'identity')
    evidence['headers']['ContentType'] = existing['contentType']
    index.write_text(json.dumps({'schema': SCHEMA, 'files': [evidence]}), encoding='utf-8')
    reused = prepare_resources.prepare(source, store, catalog, tmp_path / 'reused', encoding='gzip', stage=True, verified_representations=index)
    validate_upload(reused)
    row = next(row for row in reused['files'] if row['path'] == existing['path'])
    assert row['cacheControl'] == 'public, max-age=31536000, immutable'
    assert Path(row['source']).read_bytes() == body
    row['cacheControl'] += ', no-transform'
    with pytest.raises(ValueError, match='headers differ'):
        validate_upload(reused)


def test_deployment_preparation_reuses_addressed_bundle_and_exact_origin_headers(tmp_path, monkeypatch):
    import prepare_deployment
    publication, output = tmp_path / 'publication', tmp_path / 'output'
    root = publication / 'snapshots/jp-test/assets'
    release = publication / 'releases/stage-test'
    release.mkdir(parents=True)
    script = b'export const ready = true;\n'
    (release / 'stage.mjs').write_bytes(script)
    (release / 'integrity.json').write_text(json.dumps({'hashes': {'stage.mjs': sha(script)}}))
    (publication / 'manifest.json').write_text(json.dumps({'release': {'id': 'stage-test'},
        'snapshots': [{'id': 'jp-test', 'region': 'jp', 'version': '7.0.0'}]}))
    (publication / 'cache-worker.mjs').write_text('// worker\n')
    for name, body in {'mysekai-fixtures.json': b'{"region":"jp","gameVersion":"7.0.0"}',
                       'browser-base.json': b'{"files":[]}',
                       'phenomena/audio/loop.json': b'{"packages":[]}',
                       'ui/audio/loop.json': b'{"packages":[]}'}.items():
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(body)
    evidence_dir = tmp_path / 'evidence'; evidence_dir.mkdir()
    raw = b'{"sharedDetails":[]}' + b' ' * 2048
    _, index, evidence, body = fixture(evidence_dir, raw, 'gzip')
    logical = f'catalog-store/{sha(raw)}.json'
    evidence['path'] = evidence['objectKey'] = logical
    target = publication / logical; target.parent.mkdir(); target.write_bytes(raw)
    evidence['headers']['ContentType'] = prepare_deployment.content_type(logical, target)
    index.write_text(json.dumps({'schema': SCHEMA, 'files': [evidence]}))
    catalog = publication / 'snapshots/jp-test/catalog'; catalog.mkdir()
    (catalog / 'index.json').write_text(json.dumps({'schemaVersion': 2, 'details': ['/moly/' + logical]}))
    monkeypatch.setattr(sys, 'argv', ['prepare_deployment.py', '--publication', str(publication),
        '--output', str(output), '--exporter', str(Path(__file__).parents[1]), '--provider', 's3',
        '--object-prefix=', '--encoding', 'identity', '--stage', '--verified-representations', str(index)])
    prepare_deployment.main()
    manifest = json.loads((output / 'upload-manifest.json').read_bytes())
    row = next(row for row in manifest['files'] if row['path'] == logical)
    assert row['contentEncoding'] == 'gzip'
    assert row['cacheControl'] == evidence['headers']['CacheControl']
    assert Path(row['staging']).read_bytes() == body
    assert row['sha256'] == sha(body)
