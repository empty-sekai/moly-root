"""Source shader programs enter publication via declared, hashed references."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from pack.groups import runtime_paths, build_groups
from pack.shader_programs import close_shader_program_payloads
from pack.verify import verify_catalog


def publish(root, folder, suffix, body, asset_root='phenomena'):
    digest = hashlib.sha256(body).hexdigest()
    name = folder + '/' + digest + suffix
    target = root / asset_root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return {'file': name, 'bytes': len(body), 'sha256': digest}


@pytest.fixture(params=['phenomena', 'fixture-particles-v2'])
def source(tmp_path, request):
    root = tmp_path / 'source'
    asset_root = request.param
    def emit(folder, suffix, body):
        return publish(root, folder, suffix, body, asset_root)
    code = emit('shaders/programs', '.glsl', b'source shader')
    shader = emit('shaders/compiled', '.wgsl', b'compiled shader')
    renderer = emit('shaders/compiled', '.renderer.wgsl', b'renderer clip adapter')
    spv = emit('shaders/programs', '.spv', b'referenced binary')
    receipt = emit('shaders/compiled', '.program.json', json.dumps({
        'source': {'program': {'code': code}},
        'stages': {'vertex': {'shader': shader, 'renderer': {'shader': renderer}},
                   'fragment': {'shader': shader}}, 'proof': spv,
    }).encode())
    variants = emit('shaders', '.variants.json', json.dumps({
        'passes': [{'variants': [{'conversion': receipt}]}],
    }).encode())
    unused = emit('shaders/compiled', '.wgsl', b'unreferenced compiler intermediate')
    return root, [code, shader, renderer, spv, receipt, variants], unused, asset_root


@pytest.mark.parametrize('region', ['cn', 'jp'])
def test_closure_includes_only_referenced_shader_programs(source, tmp_path, region):
    root, refs, unused, asset_root = source
    members = runtime_paths(root)
    assert all(asset_root + '/' + row['file'] in members for row in refs)
    assert asset_root + '/' + unused['file'] not in members
    out = tmp_path / 'store'
    build_groups(root, out, 'closure', region=region)
    assert not verify_catalog(out / 'asset-packs.json')[0]


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'omitted'])
def test_shader_payload_failures_block_publication(source, damage):
    root, refs, _, asset_root = source
    members = runtime_paths(root)
    code = asset_root + '/' + refs[0]['file']
    if damage == 'missing':
        (root / code).unlink()
    elif damage == 'corrupt':
        (root / code).write_bytes(b'changed source!')
    else:
        members.pop(code)
    with pytest.raises(ValueError):
        close_shader_program_payloads(root, members)


def test_descriptor_identity_is_checked_before_following_references(source):
    root, refs, _, asset_root = source
    descriptor = root / asset_root / refs[-1]['file']
    descriptor.write_text('{}')
    with pytest.raises(ValueError, match='descriptor content identity'):
        runtime_paths(root)


def test_unsafe_hashed_descriptor_cannot_escape(source):
    root, _, _, asset_root = source
    publish(root, 'shaders', '.variants.json', json.dumps({'conversion': {
        'file': 'shaders/../../secret.wgsl', 'sha256': 'a' * 64, 'bytes': 1,
    }}).encode(), asset_root)
    with pytest.raises(ValueError, match='dependency path'):
        runtime_paths(root)
