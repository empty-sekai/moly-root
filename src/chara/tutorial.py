"""NPC tutorial placements from the client Resources index."""
import hashlib
import json
import math
from pathlib import Path

from core.gltf import coordinate_contract, reflect_position, reflect_quaternion
from core.jsonio import write_json
from ui import talk
from ui.player_source import ApkPlayerData, container_target, resource_container, unity_version

PLACEMENT_CLASS = 'Sekai.Mysekai.MysekaiTutorialCharacterPlacementData'
PLACEMENT_TYPE = '3c921a0c7b9d90b022e9c5428407c181'
PLACEMENT_PREFIX = 'mysekai/tutorial/placement/'


def decode_placements(reader):
    count = reader.i32()
    if count < 0 or count > reader.remaining() // 32:
        raise ValueError('tutorial placement count exceeds its serialized object')
    return {'datas': [{'unitCharacterId': reader.i32(), 'position': reader.vec3(),
                       'rotation': reader.vec4()} for _ in range(count)]}


def processor_configuration(path, *, client_version):
    """Validate separately supplied client constants; never invent server state."""
    raw = Path(path).read_bytes()
    if len(raw) > 65536:
        raise ValueError('tutorial processor configuration exceeds its size limit')
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
        'schema', 'region', 'clientVersion', 'coordinateContract', 'initialOffstage',
        'initialPlayer', 'afterIntroNpc', 'afterTalkPlayerRotation',
    } or value['schema'] != 'moly-tutorial-processor/1' or value['region'] != 'jp' or value['clientVersion'] != client_version:
        raise ValueError('tutorial processor configuration has a different source identity')
    if value['coordinateContract'] != coordinate_contract()['space']:
        raise ValueError('tutorial processor coordinates must already be canonical')

    def vector(data, width):
        if not isinstance(data, list) or len(data) != width or any(
            type(v) not in {int, float} or not math.isfinite(v) for v in data
        ):
            raise ValueError('invalid tutorial processor pose')
        if width == 4 and not math.isclose(sum(v * v for v in data), 1, abs_tol=1e-4):
            raise ValueError('invalid tutorial processor rotation')

    vector(value['initialOffstage'], 3)
    vector(value['afterTalkPlayerRotation'], 4)
    for name in ['initialPlayer', 'afterIntroNpc']:
        pose = value[name]
        if not isinstance(pose, dict) or set(pose) != {'position', 'rotation'}:
            raise ValueError('invalid tutorial processor transform')
        vector(pose['position'], 3)
        vector(pose['rotation'], 4)
    return value


def export_placements(apks, out_dir, *, client_version, processor_input=None):
    if client_version != '7.0.0':
        raise ValueError('tutorial placement extraction requires a supported source version')
    processor = None if processor_input is None else processor_configuration(processor_input, client_version=client_version)
    data = ApkPlayerData(apks)
    player = data.environment()
    player.add(['globalgamemanagers'], follow=False)
    manager, container = resource_container(player.env)
    selected = []
    for key, pointers in sorted(container.items()):
        if not key.startswith(PLACEMENT_PREFIX):
            continue
        if len(pointers) != 1:
            raise ValueError(f'ambiguous tutorial placement resource {key}')
        selected.append((key, container_target(player.env, manager, pointers[0])))
    if not selected:
        raise ValueError('source has no tutorial placement resources')
    player.add([target[0] for _, target in selected])
    if player.missing:
        raise ValueError(f'tutorial placements have missing source files: {player.missing}')
    scripts = talk.build_monoscript_index(player.env)
    placements, sources = {}, []
    for key, (file_name, path_id) in selected:
        obj = player.env.files[file_name].objects[path_id]
        if talk.resolve_script_class(obj, scripts) != PLACEMENT_CLASS or talk.serialized_type_hash(obj) != PLACEMENT_TYPE:
            raise ValueError(f'unrecognized tutorial placement layout: {key}')
        decoded = talk.decode_object(player.env, obj, scripts, decoders={PLACEMENT_CLASS: decode_placements})
        if decoded['residual']:
            raise ValueError(f'tutorial placement has unread serialized bytes: {key}')
        rows = decoded['fields']['datas']
        ids = [row['unitCharacterId'] for row in rows]
        if any(value <= 0 for value in ids) or len(set(ids)) != len(ids):
            raise ValueError(f'invalid tutorial participant identities: {key}')
        for row in rows:
            if not all(math.isfinite(value) for value in row['position'] + row['rotation']):
                raise ValueError(f'non-finite tutorial pose: {key}')
            if not math.isclose(sum(v * v for v in row['rotation']), 1, abs_tol=1e-4):
                raise ValueError(f'invalid tutorial rotation: {key}')
        placements[key.removeprefix(PLACEMENT_PREFIX)] = [
            {'unitCharacterId': row['unitCharacterId'],
             'position': reflect_position(row['position']),
             'rotation': reflect_quaternion(row['rotation'])} for row in rows]
        sources.append({'resource': key, 'serializedFile': file_name, 'pathId': path_id,
                        'sourceSha256': hashlib.sha256(obj.get_raw_data()).hexdigest(),
                        'typeHash': PLACEMENT_TYPE, 'rows': len(rows), 'residual': 0})
    result = {'schema': 'moly-tutorial-placements/1', 'region': 'jp', 'clientVersion': client_version,
              'unityVersion': unity_version(player.env), 'coordinates': coordinate_contract(),
              'placements': placements, 'sources': sources}
    write_json(Path(out_dir) / 'placements.json', result)
    if processor is not None:
        write_json(Path(out_dir) / 'processor.json', processor)
    return result
