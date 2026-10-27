"""Reject damaged or edited font releases before either upload scope writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def load_tool(name):
    path = Path(__file__).parents[1] / 'tools' / 'deployment' / (name + '.py')
    spec = importlib.util.spec_from_file_location('font_test_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=['font-test/', ''])
def publication(tmp_path, monkeypatch, request):
    object_prefix = request.param
    tool_dir = Path(__file__).parents[1] / 'tools' / 'deployment'
    monkeypatch.syspath_prepend(str(tool_dir))
    prepare, uploader = load_tool('prepare_fonts'), load_tool('upload_oss')
    root_dir = tmp_path / 'publication'
    (root_dir / 'objects').mkdir(parents=True)

    def object_ref(raw, suffix):
        sha = hashlib.sha256(raw).hexdigest()
        name = f'objects/{sha}.{suffix}'
        (root_dir / name).write_bytes(raw)
        return {'path': name, 'sha256': sha, 'bytes': len(raw)}

    license_ref = object_ref(b'Synthetic font license\n', 'txt')
    font = {**object_ref(b'\x00\x01\x00\x00synthetic font payload', 'ttf'),
            'coverage': [[32, 126], [0x4e02, 0x4e02]], 'license': license_ref}
    root = {'schema': 'moly-text-fonts/1', 'baseFonts': {'b' * 64: {'coverage': [[32, 126]]}},
            'fonts': [font]}
    root['revision'] = hashlib.sha256(canonical(root)).hexdigest()
    raw = canonical(root)
    (root_dir / 'revisions').mkdir()
    (root_dir / f"revisions/{root['revision']}.json").write_bytes(raw)
    (root_dir / 'latest.json').write_bytes(raw)
    output = tmp_path / 'prepared'
    doc = prepare.prepare(root_dir, output, object_prefix=object_prefix, url_prefix='/moly/')
    manifest = output / 'upload-manifest.json'

    def select(scope='stable', limit=None):
        manifest.write_bytes(canonical(doc))
        args = SimpleNamespace(manifest=manifest, scope=scope, release_id=None,
            mutable_path=['fonts/latest.json'] if scope == 'mutable' else [],
            allow_mutable_update=scope == 'mutable', expect_previous_sha256=None, limit=limit)
        return uploader.load_selection(args, object_prefix=object_prefix, schemas=('moly-object-upload/1',))[1]

    return SimpleNamespace(doc=doc, root=root, raw=raw, select=select, uploader=uploader,
                           prepare=prepare, directory=root_dir)


def row(publication, suffix):
    return next(value for value in publication.doc['files'] if value['path'].endswith(suffix))


def test_complete_publication_has_separate_immutable_and_pointer_scopes(publication):
    stable = publication.select()
    assert len(stable) == 3
    assert all(value['path'] != 'fonts/latest.json' for value in stable)
    assert [value['path'] for value in publication.select('mutable')] == ['fonts/latest.json']
    publication.doc['files'].reverse()
    assert publication.select() == stable


@pytest.mark.parametrize('suffix', ['.ttf', '.txt', '/latest.json', '/revisions/'])
@pytest.mark.parametrize('scope', ['stable', 'mutable'])
def test_missing_catalogue_dependency_is_refused(publication, suffix, scope):
    publication.doc['files'] = [value for value in publication.doc['files']
                                if not (suffix in value['path'] if suffix == '/revisions/'
                                        else value['path'].endswith(suffix))]
    with pytest.raises(publication.uploader.SafeFailure):
        publication.select(scope)


def test_extra_or_renamed_immutable_font_cannot_be_published(publication):
    extra = dict(row(publication, '.ttf'))
    extra['path'] = 'fonts/objects/' + '0' * 64 + '.ttf'
    extra['objectKey'] = publication.doc['objectPrefix'] + extra['path']
    extra['urlPath'] = publication.doc['urlPrefix'] + extra['path']
    publication.doc['files'].append(extra)
    with pytest.raises(publication.uploader.SafeFailure, match='font_publication_closure_mismatch'):
        publication.select()
    publication.doc['files'].remove(row(publication, '.ttf'))
    with pytest.raises(publication.uploader.SafeFailure, match='font_publication_closure_mismatch'):
        publication.select()


@pytest.mark.parametrize(('field', 'value'), [
    ('sha256', '0' * 64), ('decodedSha256', '0' * 64), ('bytes', 1), ('decodedBytes', 1),
    ('contentEncoding', 'gzip'), ('contentType', 'application/octet-stream'),
    ('cacheControl', 'public, max-age=0'), ('urlPath', '/other/font.ttf'),
])
def test_object_metadata_must_match_catalogue(publication, field, value):
    row(publication, '.ttf')[field] = value
    with pytest.raises(publication.uploader.SafeFailure, match='font_object_metadata_mismatch'):
        publication.select()


@pytest.mark.parametrize('alias', ['sourceRaw', 'source', 'staging'])
def test_source_alias_cannot_hide_different_upload_bytes(publication, alias):
    row(publication, '/latest.json')[alias] = row(publication, '.txt')['staging']
    with pytest.raises(publication.uploader.SafeFailure):
        publication.select('mutable')


def test_forged_upload_hash_cannot_override_validated_pointer(publication):
    latest, license_row = row(publication, '/latest.json'), row(publication, '.txt')
    for field in ('source', 'staging', 'sha256', 'bytes'):
        latest[field] = license_row[field]
    with pytest.raises(publication.uploader.SafeFailure, match='font_object_metadata_mismatch'):
        publication.select('mutable')


@pytest.mark.parametrize('suffix', ['.ttf', '.txt', '/latest.json', '/revisions/'])
def test_changed_source_bytes_fail_before_scope_or_canary_upload(publication, suffix):
    target = (next(value for value in publication.doc['files'] if suffix in value['path'])
              if suffix == '/revisions/' else row(publication, suffix))
    Path(target['staging']).write_bytes(b'changed after preparation')
    with pytest.raises(publication.uploader.SafeFailure):
        publication.select('mutable')
    with pytest.raises(publication.uploader.SafeFailure):
        publication.select(limit=1)


def test_root_cannot_reuse_revision_after_coverage_change(publication):
    publication.root['baseFonts']['b' * 64]['coverage'].append([0x4e02, 0x4e02])
    changed = canonical(publication.root)
    for entry in publication.doc['files']:
        if entry['path'].endswith('.json'):
            Path(entry['staging']).write_bytes(changed)
            entry['sha256'] = entry['decodedSha256'] = hashlib.sha256(changed).hexdigest()
            entry['bytes'] = entry['decodedBytes'] = len(changed)
    publication.doc['fontCatalog']['sha256'] = hashlib.sha256(changed).hexdigest()
    with pytest.raises(publication.uploader.SafeFailure, match='invalid_local_font_catalogue'):
        publication.select('mutable')


def test_pointer_row_cannot_publish_a_different_canonical_root(publication):
    # Keep the original sourceRaw and catalogue metadata, but point the upload
    # stream at a second, internally valid root. Both files parse; only one is
    # the publication selected for this run.
    publication.root.pop('revision')
    publication.root['baseFonts']['b' * 64]['coverage'] = [[32, 125]]
    publication.root['revision'] = hashlib.sha256(canonical(publication.root)).hexdigest()
    alternate = publication.directory / 'alternate.json'
    alternate.write_bytes(canonical(publication.root))
    latest = row(publication, '/latest.json')
    latest['staging'] = str(alternate)
    latest['sha256'] = hashlib.sha256(alternate.read_bytes()).hexdigest()
    latest['bytes'] = alternate.stat().st_size
    with pytest.raises(publication.uploader.SafeFailure, match='font_object_metadata_mismatch'):
        publication.select('mutable')


@pytest.mark.parametrize('encoding', ['utf-16', 'utf-32'])
def test_non_utf8_root_cannot_publish_to_utf8_consumers(publication, encoding):
    changed = publication.raw.decode('utf-8').encode(encoding)
    for entry in publication.doc['files']:
        if entry['path'].endswith('.json'):
            Path(entry['staging']).write_bytes(changed)
            entry['sha256'] = entry['decodedSha256'] = hashlib.sha256(changed).hexdigest()
            entry['bytes'] = entry['decodedBytes'] = len(changed)
    publication.doc['fontCatalog']['sha256'] = hashlib.sha256(changed).hexdigest()
    with pytest.raises(publication.uploader.SafeFailure, match='invalid_local_font_catalogue'):
        publication.select('mutable')


@pytest.mark.parametrize('mutate', [
    lambda root: root['fonts'].append(dict(root['fonts'][0])),
    lambda root: root['fonts'][0].update(coverage=[[100, 110], [99, 99]]),
    lambda root: root['fonts'][0].update(coverage=[[0xd800, 0xd800]]),
    lambda root: root['fonts'][0].update(path='objects/' + '0' * 64 + '.ttf'),
])
def test_preparation_and_uploader_share_invalid_catalogue_gate(publication, mutate):
    mutate(publication.root)
    publication.root.pop('revision')
    publication.root['revision'] = hashlib.sha256(canonical(publication.root)).hexdigest()
    with pytest.raises(ValueError):
        publication.prepare.parse_catalog(canonical(publication.root))
