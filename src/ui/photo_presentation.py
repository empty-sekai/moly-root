"""Playable PhotoShot curves and the master's frame image inventory.

The inventory is not a possession list. Possession is a separate client input.
Curve targets are resolved from the animator's actual bindings to the exported
screen identities; no hierarchy-name lookup is needed by the runtime player.
"""
from pathlib import Path
import hashlib
import json

import UnityPy
from core.jsonio import write_json


def export_presentation(document, master_dir, bundle_root, out):
    nodes = document['nodes']
    animation = next(c for n in nodes for c in n['components']
                     if c['class'] == 'Sekai.Mysekai.MysekaiPhotoShotAnimation')
    animator_id = animation['fields']['_photoShotAnimator'][1]
    animator = next(c for n in nodes for c in n['components'] if c['pathId'] == animator_id)
    source = animator['animatorController']
    state = next(s for m in source['controller']['stateMachines'] for s in m['states']
                 if s['name'] == 'PhotoShot')
    if len(state['motions']) != 1 or state['loop'] or state['speed'] != 1:
        raise ValueError('PhotoShot requires one non-looping motion at authored speed')
    motion = state['motions'][0]
    clip = next(c for c in source['clips'] if c['source'] == motion['clip']['source'])
    if clip['accounting']['unresolved'] or clip['accounting']['bindingSlots'] != len(clip['curves']):
        raise ValueError('PhotoShot has unresolved animation slots')
    properties = {
        (224, 1460864421): ('position', 0), (224, 538195251): ('position', 1),
        (114, 2526845255): ('color', 0), (114, 4215373228): ('color', 1),
        (114, 2334886179): ('color', 2), (114, 304273561): ('color', 3),
        (225, 1574349066): ('alpha', 0), (1, 2086281974): ('active', 0),
    }
    targets = {}
    curves = []
    for curve in clip['curves']:
        if len(curve['targets']) != 1 or curve['kind'] not in ('cubic', 'const', 'linear'):
            raise ValueError('PhotoShot curve needs one exact target and supported interpolation')
        target, binding = curve['targets'][0], curve['binding']
        game_object_id = int(target['gameObject']['pathId'])
        node = next(n for n in nodes if n['gameObjectId'] == game_object_id)
        if binding['typeId'] == 4 and binding['attribute'] in (3, 4):
            prop, channel = ('scale' if binding['attribute'] == 3 else 'euler'), binding['component']
        else:
            prop, channel = properties[(binding['typeId'], binding['attribute'])]
        component_id = None
        if prop == 'color':
            if len(target['components']) != 1:
                raise ValueError('PhotoShot color needs its exact Graphic component')
            component_id = int(target['components'][0]['pathId'])
        key = node['transformId']
        if key not in targets:
            graphic = next((c for c in node['components'] if c['pathId'] == component_id), None)
            targets[key] = {'transformId': node['transformId'], 'graphicId': component_id,
                            'selectorId': next((c['pathId'] for c in node['components']
                                                if c.get('fields', {}).get('m_Color') is not None),
                                               node['transformId']),
                            'position': node['rect']['anchoredPosition'], 'scale': node['rect']['localScale'],
                            'color': graphic['fields']['m_Color'] if graphic else [1., 1., 1., 1.]}
        if component_id is not None:
            graphic = next(c for c in node['components'] if c['pathId'] == component_id)
            targets[key]['graphicId'] = component_id
            targets[key]['color'] = graphic['fields']['m_Color']
        curves.append({'target': list(targets).index(key), 'property': prop, 'channel': channel,
                       'kind': curve['kind'], 'points': curve['points'], 'sourceSlot': curve['slot']})
    frames = []
    output = Path(out)
    for row in json.loads((Path(master_dir) / 'mysekaiPhotoDecorations.json').read_text(encoding='utf-8')):
        frame = {'id': row['id'], 'name': row['name'], 'assetbundleName': row['assetbundleName']}
        for role in ('preview', 'thumbnail'):
            package = f"mysekai__photo_frame__{role}__{row['assetbundleName']}"
            path = Path(bundle_root) / package
            data = path.read_bytes()
            env = UnityPy.load(data)
            matches = [o for o in env.objects if o.type.name == 'Texture2D'
                       and o.read_typetree()['m_Name'] == row['assetbundleName']]
            if len(matches) != 1:
                raise ValueError(f'{package} needs exactly one named frame texture')
            texture = matches[0]
            relative = f"photo-shot/frames/{row['id']}-{role}.png"
            (output / relative).parent.mkdir(parents=True, exist_ok=True)
            texture.read().image.save(output / relative)
            frame[role] = relative
            frame[f'{role}Source'] = {'package': package, 'pathId': texture.path_id,
                                    'sha256': hashlib.sha256(data).hexdigest()}
        frames.append(frame)
    result = {'version': 1, 'duration': clip['duration'], 'targets': list(targets.values()),
              'curves': curves, 'events': clip['events'], 'frames': frames,
              'source': {'controller': source['controller']['source'], 'clip': clip['source'],
                         'animatorId': animator_id, 'clipIndex': motion['clipIndex'],
                         'accounting': clip['accounting']}}
    relative = 'photo-shot/presentation.json'
    path = write_json(output / relative, result)
    return {'document': relative, 'kind': 'photo-presentation',
            'documentSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'curves': len(curves), 'frames': len(frames)}
