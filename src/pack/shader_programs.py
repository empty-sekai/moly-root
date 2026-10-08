"""Concrete source-shader descriptor closure, not a broad extension allowlist."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .paths import asset_path

SOURCE_SHADER_ROOTS = ("phenomena", "fixture-particles-v2")


def content_references(value):
    if isinstance(value, dict):
        if 'file' in value:
            yield value
        for child in value.values():
            yield from content_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from content_references(child)


def close_shader_program_payloads(source, members, *, extend=False):
    """Verify every declared source/compiled/proof dependency and optionally add it.

    Shader source text, adapted WGSL and round-trip proofs carry independent
    hash/size identities in the descriptor. Only referenced, verified objects
    enter membership; unrelated .glsl/.wgsl/.spv files stay excluded.
    """
    source = Path(source).resolve()
    result = dict(members)
    prefixes = tuple(root + '/shaders/' for root in SOURCE_SHADER_ROOTS)
    queue = [name for name in result if name.startswith(prefixes) and
             name.endswith(('.shader.json', '.variants.json', '.program.json'))]
    seen, checked = set(), {}
    references = 0
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        asset_root = name.split('/', 1)[0]
        raw = asset_path(source, name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != PurePosixPath(name).name.split('.')[0]:
            raise ValueError(f'shader descriptor content identity mismatch: {name}')
        document = json.loads(raw)
        for reference in content_references(document):
            relative = reference.get('file')
            if (not isinstance(relative, str) or
                    not re.fullmatch(r'shaders/(?:programs/|compiled/)?[a-f0-9]{64}(?:\.[a-z0-9-]+)+', relative) or
                    PurePosixPath(relative).suffix not in {'.json', '.glsl', '.wgsl', '.spv', '.bin'}):
                raise ValueError(f'invalid shader program dependency path: {name}')
            size, digest = reference.get('bytes'), reference.get('sha256')
            if (type(size) is not int or size < 0 or not isinstance(digest, str) or
                    not re.fullmatch(r'[a-f0-9]{64}', digest) or
                    PurePosixPath(relative).name.split('.')[0] != digest):
                raise ValueError(f'invalid shader program dependency identity: {name}')
            logical = asset_root + '/' + relative
            payload = asset_path(source, logical)
            if payload.relative_to(source).as_posix() != logical:
                raise ValueError(f'shader dependency path aliases another object: {logical}')
            if logical not in checked:
                body = payload.read_bytes()
                checked[logical] = len(body), hashlib.sha256(body).hexdigest()
            if checked[logical] != (size, digest):
                raise ValueError(f'shader program dependency hash/size mismatch: {logical}')
            if logical not in result:
                if not extend:
                    raise ValueError(f'shader program dependency omitted from runtime: {logical}')
                result[logical] = payload
            if logical.endswith('.json'):
                queue.append(logical)
            references += 1
    return result, {'descriptors': len(seen), 'references': references,
                    'payloads': len(checked), 'added': len(result) - len(members)}
