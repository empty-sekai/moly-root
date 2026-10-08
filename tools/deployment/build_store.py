"""Build one region's content-addressed release store from a release root.

The store holds exactly the browser publication membership that
prepare_deployment.py audits for the same root, so a packed snapshot pinned to
the printed catalogue passes that closure check. The store's mutable channel
selector is updated; it is never published. This script never contacts a
cloud API.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from prepare_deployment import browser_membership  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, metavar='REGION=PATH',
                        help='release root of one region, e.g. cn=PATH')
    parser.add_argument('--store', type=Path, required=True, help='content-addressed store directory')
    parser.add_argument('--channel', default='stable')
    parser.add_argument('--exporter', type=Path, default=HERE.parents[1])
    args = parser.parse_args(argv)
    region, separator, source = args.source.partition('=')
    if not separator or region not in {'cn', 'jp'}:
        raise ValueError('--source must specify cn=PATH or jp=PATH')
    root = Path(source).resolve(strict=True)
    store = args.store.resolve()
    if store.is_relative_to(root) or root.is_relative_to(store):
        raise ValueError('Store and source roots must be disjoint')
    sys.path.insert(0, str(args.exporter.resolve() / 'src'))
    from pack.groups import build_groups, runtime_paths
    version = json.loads((root / 'mysekai-fixtures.json').read_bytes()).get('gameVersion')
    if not isinstance(version, str) or not version:
        raise ValueError('Release root names no game version')
    members, report = browser_membership(root, region, version, runtime_paths)
    if report['missing'] or report['browserBaseMismatch']:
        raise RuntimeError(f"Resource closure is incomplete: {len(report['missing'])} missing, "
                           f"{len(report['browserBaseMismatch'])} browser-base mismatches")
    catalog = build_groups(root, store, version, region=region, channel=args.channel,
                           paths=sorted(members))
    pointer = json.loads((store / 'channels' / region / f'{args.channel}.json').read_bytes())
    catalog_id = pointer['catalog'].removeprefix('catalogs/').removesuffix('.json')
    print(json.dumps({'region': region, 'version': version, 'assetCatalog': catalog_id,
                      'paths': len(members), 'packages': len(catalog['packages'])}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
