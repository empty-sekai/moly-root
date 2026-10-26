"""An asset-only publication carries exactly one verified immutable graph."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pack.groups import build_groups


@pytest.fixture
def publication(tmp_path, monkeypatch):
    directory = Path(__file__).parents[1] / 'tools/deployment'
    monkeypatch.syspath_prepend(str(directory))
    # Other deployment tests load these names too. Each test uses this source tree.
    for name in ['prepare_resources', 'resource_publication', 'upload_oss', 'upload_s3', 'prepare_deployment']:
        sys.modules.pop(name, None)
    import prepare_resources
    import upload_s3
    monkeypatch.setattr(upload_s3, 'create_client', lambda *_: pytest.fail('No cloud call in local preparation'))
    source, store, output = tmp_path / 'source', tmp_path / 'store', tmp_path / 'staging'
    files = {
        'source.json': b'{"source":{"region":"jp","appVersion":"7.0.0"}}',
        'mysekai-fixtures.json': b'{"region":"jp","gameVersion":"7.0.0"}',
        'browser-base.json': b'{"files":[]}',
        'phenomena/audio/loop.json': b'{"packages":[]}',
        'ui/audio/loop.json': b'{"packages":[]}',
        'ui/test.json': json.dumps({'example': 'text ' * 2000}).encode(),
    }
    for name, body in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    build_groups(source, store, '7.0.0', region='jp')
    pointer = json.loads((store / 'channels/jp/stable.json').read_bytes())
    catalog_id = Path(pointer['catalog']).stem
    doc = prepare_resources.prepare(source, store, catalog_id, output, encoding='gzip', stage=True)
    manifest = output / 'upload-manifest.json'
    arguments = ['--manifest', str(manifest), '--endpoint', 'https://objects.example.invalid',
                 '--bucket', 'example-assets', '--region', 'test-region-1', '--object-prefix=']
    def select(*extra):
        manifest.write_text(json.dumps(doc), encoding='utf8')
        return upload_s3.selection(upload_s3.parse_args([*arguments, *extra]))[1]
    return SimpleNamespace(source=source, store=store, output=output, catalog_id=catalog_id,
                           doc=doc, manifest=manifest, select=select, args=arguments,
                           uploader=upload_s3, prepare=prepare_resources)


def test_resource_only_preparation_and_selection_need_no_release_or_pointer(publication, capsys):
    rows = publication.select()
    assert rows and len(rows) == len(publication.doc['files'])
    assert publication.doc['snapshots'] == [] and 'release' not in publication.doc
    assert publication.doc['resourceCatalog']['region'] == 'jp'
    assert all(row['path'].startswith('asset-store/') and 'no-transform' in row['cacheControl'] for row in rows)
    assert not any('channels/' in row['path'] for row in rows)
    assert any(row['contentEncoding'] == 'gzip' for row in rows)
    assert publication.uploader.main(publication.args) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['cloudRequests'] == 0


@pytest.mark.parametrize('scope', ['release', 'descriptors', 'mutable'])
def test_resource_catalogue_rejects_every_nonstable_scope(publication, scope):
    with pytest.raises(publication.uploader.SafeFailure, match='complete_stable_scope'):
        publication.select('--scope', scope)


def test_resource_catalogue_cannot_select_a_partial_closure(publication):
    with pytest.raises(publication.uploader.SafeFailure, match='complete_stable_scope'):
        publication.select('--limit', '1')


@pytest.mark.parametrize('kind', ['extra_blob', 'missing_blob', 'unrelated_pointer', 'provenance', 'http_headers', 'source_alias', 'encoded_body'])
def test_resource_upload_rechecks_closure_hashes_and_representations(publication, tmp_path, kind):
    doc = publication.doc
    blob = next(row for row in doc['files'] if '/blobs/' in row['path'])
    if kind == 'extra_blob':
        extra = copy.deepcopy(blob)
        extra['path'] = 'asset-store/blobs/ff/' + 'f' * 64 + '.bin'
        extra['objectKey'] = extra['path']
        extra['urlPath'] = '/moly/' + extra['path']
        doc['files'].append(extra)
    elif kind == 'missing_blob':
        doc['files'].remove(blob)
    elif kind == 'unrelated_pointer':
        extra = copy.deepcopy(blob)
        extra['path'] = extra['objectKey'] = 'manifest.json'
        extra['urlPath'] = '/moly/manifest.json'
        doc['files'].append(extra)
    elif kind == 'provenance':
        doc['resourceCatalog']['provenance']['runtime_source']['source']['appVersion'] = '8.0.0'
    elif kind == 'http_headers':
        blob['cacheControl'] = 'public, max-age=60'
    elif kind == 'source_alias':
        other = tmp_path / 'other'
        other.write_bytes(b'wrong bytes')
        blob['source'] = str(other)
    else:
        other = tmp_path / 'encoded'
        body = b'wrong bytes'
        other.write_bytes(body)
        blob['source'] = blob['staging'] = str(other)
        blob['bytes'] = len(body)
        blob['sha256'] = hashlib.sha256(body).hexdigest()
    with pytest.raises(publication.uploader.SafeFailure):
        publication.select()


def test_changed_addressed_blob_is_rejected(publication):
    row = next(row for row in publication.doc['files'] if '/blobs/' in row['path'])
    # Replace with a fresh inode; the staging hard link must not change with it.
    path = Path(row['sourceRaw'])
    other = path.with_suffix('.new')
    other.write_bytes(b'bad blob')
    other.replace(path)
    with pytest.raises(publication.uploader.SafeFailure, match='catalogue_closure'):
        publication.select()


@pytest.mark.parametrize('change', ['added', 'content'])
def test_prepare_compares_actual_source_bytes_and_membership(publication, tmp_path, change):
    path = publication.source / ('ui/extra.json' if change == 'added' else 'ui/test.json')
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='membership|differs'):
        publication.prepare.prepare(publication.source, publication.store, publication.catalog_id, tmp_path / 'another')


def test_runtime_publication_refuses_legacy_http_brotli(publication, tmp_path, monkeypatch):
    from pack import groups
    category = tmp_path / 'legacy-http-br.toml'
    category.write_text('[default]\ncodec = "identity"\nhttp_encoding = "br"\n')
    monkeypatch.setattr(groups, 'CATEGORIES', category)
    store = tmp_path / 'legacy-store'
    groups.build_groups(publication.source, store, '7.0.0', region='jp')
    pointer = json.loads((store / 'channels/jp/stable.json').read_bytes())
    identity = Path(pointer['catalog']).stem
    with pytest.raises(ValueError, match='unsupported by native/Web'):
        publication.prepare.prepare(publication.source, store, identity, tmp_path / 'legacy-output')
