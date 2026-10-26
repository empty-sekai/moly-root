"""Independent stage/game engines must both be in the verified release closure."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).parents[1] / 'tools/deployment'
CONTRACT = 'moly-rh-y-up-reflect-x-v1'


def load(name):
    spec = importlib.util.spec_from_file_location('independent_' + name, TOOLS / (name + '.py'))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def publication(tmp_path):
    root = tmp_path / 'publication'
    snapshot = {'id': 'cn-synthetic', 'region': 'cn', 'version': '6.0.0',
                'assets': '/moly/snapshots/cn-synthetic/assets/', 'packs': False, 'assetCatalog': None}
    manifest = {'publisher': 'moly-release-artifact-v1', 'schemaVersion': 2,
                'release': {'id': 'stage-synthetic', 'coordinateContract': CONTRACT}, 'snapshots': [snapshot]}
    write(root / 'manifest.json', manifest)
    game = {'publisher': 'moly-release-artifact-v1', 'schemaVersion': 1, 'prefix': '/moly/',
            'release': {'id': 'game-synthetic', 'module': '/moly/releases/game-synthetic/game-boot.mjs',
                        'abi': 2, 'coordinateContract': CONTRACT}, 'snapshots': [snapshot]}
    write(root / 'game/manifest.json', game)
    for release, module in [('stage-synthetic', 'embed.mjs'), ('game-synthetic', 'game-boot.mjs')]:
        base = root / 'releases' / release
        base.mkdir(parents=True)
        raw = f'export const name = "{release}";'.encode()
        (base / module).write_bytes(raw)
        write(base / 'integrity.json', {'schemaVersion': 1, 'releaseId': release, 'contractVersion': 2,
              'coordinateContract': CONTRACT, 'hashes': {module: hashlib.sha256(raw).hexdigest()}})
    (root / 'cache-worker.mjs').write_text('// synthetic worker')
    assets = root / 'snapshots/cn-synthetic/assets'
    for name, value in [('mysekai-fixtures.json', {'region': 'cn', 'gameVersion': '6.0.0'}),
                        ('browser-base.json', {'files': []}), ('phenomena/audio/loop.json', {'packages': []}),
                        ('ui/audio/loop.json', {'packages': []})]:
        write(assets / name, value)
    return SimpleNamespace(root=root, manifest=manifest, game=game)


def test_prepare_and_selection_include_both_pointer_releases(publication, tmp_path):
    (publication.root / 'releases/game-synthetic/untracked.mjs').write_text('// never release this')
    output = tmp_path / 'output'
    result = subprocess.run([sys.executable, str(TOOLS / 'prepare_deployment.py'),
        '--publication', str(publication.root), '--output', str(output), '--provider', 's3',
        '--object-prefix=', '--encoding', 'identity'], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    filename = output / 'upload-manifest.json'
    doc = json.loads(filename.read_bytes())
    assert doc['release'] == 'stage-synthetic'
    assert doc['releases'] == ['stage-synthetic', 'game-synthetic']
    assert 'releases/game-synthetic/untracked.mjs' not in {row['path'] for row in doc['files']}
    uploader = load('upload_oss')
    def select(release):
        args = SimpleNamespace(manifest=filename, scope='release', release_id=release, mutable_path=[],
                               allow_mutable_update=False, expect_previous_sha256=None, limit=None)
        return uploader.load_selection(args, object_prefix='', schemas=('moly-object-upload/1',))[1]
    assert {r['path'] for r in select('game-synthetic')} == {
        'releases/game-synthetic/game-boot.mjs', 'releases/game-synthetic/integrity.json'}
    assert all(r['path'].startswith('releases/stage-synthetic/') for r in select('stage-synthetic'))
    with pytest.raises(uploader.SafeFailure, match='explicit_release_id_must_match_manifest'):
        select('unrelated')
    doc['releases'][1] = 'unrelated'
    write(filename, doc)
    with pytest.raises(uploader.SafeFailure, match='release_pointers_do_not_match_manifest'):
        select('unrelated')


@pytest.mark.parametrize('mutation', ['module', 'snapshot', 'coordinate', 'identity', 'missing', 'bytes', 'path'])
def test_changed_game_release_or_pointer_is_rejected(publication, mutation):
    prepare = load('prepare_deployment')
    root, game = publication.root, publication.game
    if mutation == 'module':
        game['release']['module'] = '/moly/releases/unrelated/game-boot.mjs'
    elif mutation == 'snapshot':
        game['snapshots'][0]['id'] = 'cn-unrelated'
    elif mutation == 'coordinate':
        game['release']['coordinateContract'] = 'unrelated'
    elif mutation == 'identity':
        file = root / 'releases/game-synthetic/integrity.json'
        value = json.loads(file.read_bytes()); value['releaseId'] = 'unrelated'; write(file, value)
    elif mutation == 'missing':
        (root / 'releases/game-synthetic/game-boot.mjs').unlink()
    elif mutation == 'bytes':
        (root / 'releases/game-synthetic/game-boot.mjs').write_text('tampered')
    elif mutation == 'path':
        file = root / 'releases/game-synthetic/integrity.json'
        value = json.loads(file.read_bytes()); value['hashes']['../outside'] = 'a' * 64; write(file, value)
    write(root / 'game/manifest.json', game)
    # Read the original manifest, since Python fixture nested dictionaries may share identity.
    manifest = json.loads((root / 'manifest.json').read_bytes())
    with pytest.raises((ValueError, RuntimeError, OSError)):
        prepare.publication_release_files(root, manifest, '/moly/')


def test_single_release_publication_remains_supported(publication):
    (publication.root / 'game/manifest.json').unlink()
    releases, files, count = load('prepare_deployment').publication_release_files(
        publication.root, publication.manifest, '/moly/')
    assert releases == ['stage-synthetic']
    assert count == 1
    assert set(files) == {'releases/stage-synthetic/embed.mjs', 'releases/stage-synthetic/integrity.json'}


def test_combined_legacy_engine_is_verified_once(publication):
    root = publication.root
    stage = root / 'releases/stage-synthetic'
    body = (root / 'releases/game-synthetic/game-boot.mjs').read_bytes()
    (stage / 'game-boot.mjs').write_bytes(body)
    integrity = json.loads((stage / 'integrity.json').read_bytes())
    integrity['hashes']['game-boot.mjs'] = hashlib.sha256(body).hexdigest()
    write(stage / 'integrity.json', integrity)
    publication.game['release']['id'] = 'stage-synthetic'
    publication.game['release']['module'] = '/moly/releases/stage-synthetic/game-boot.mjs'
    write(root / 'game/manifest.json', publication.game)
    releases, files, count = load('prepare_deployment').publication_release_files(root, publication.manifest, '/moly/')
    assert releases == ['stage-synthetic']
    assert count == 2
    assert len(files) == 3
