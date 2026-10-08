"""Check browser membership against the paths the engine requested in a browser run.

The membership lists in pack.groups are fixed, and a fixed list silently misses
files the engine reads. This takes the engine's read set from the logs of a
browser run and names every requested path the browser membership lacks:

* every logical asset URL fetched below `snapshots/<id>/assets/`. Only an
  unpacked snapshot serves logical URLs; a packed run fetches store blobs, so it
  contributes only its not-found set;
* every path the asset server reported as `Path not found: <path>`.

A request log is any text holding those URLs, one request per line. A browser
console log is read for the not-found lines. A requested path that the root holds
but the membership lacks is `unpublished`, and the check fails on it. A path the
root itself lacks is `absentFromRoot`: it is named, but no membership can fix it.

    python tools/deployment/engine_reads.py --source jp=ROOT --log jp=requests.log --log jp=console.log
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sys
from urllib.parse import unquote

ASSET_URL = re.compile(r'/snapshots/[^/\s]+/assets/([^\s?#"\')]+)')
NOT_FOUND = re.compile(r'Path not found: (\S+)')


def requested_paths(lines):
    """Logical asset paths requested in these log lines, and the not-found
    values that are not paths below the asset root (masters, native paths)."""
    paths, other = set(), set()
    for line in lines:
        for match in ASSET_URL.finditer(line):
            paths.add(unquote(match[1]))
        for match in NOT_FOUND.finditer(line):
            value = match[1].rstrip(':),;')
            if ASSET_URL.search(value):
                continue
            value = value.split('#', 1)[0]
            parts = PurePosixPath(value).parts
            if (':' in value or value.startswith('/') or '\\' in value
                    or any(part in ('..', '.') for part in parts)):
                other.add(value)
            else:
                paths.add(value)
    return paths, other


def read_logs(files):
    paths, other = set(), set()
    for file in files:
        with open(file, encoding='utf-8', errors='replace') as stream:
            found, rest = requested_paths(stream)
        paths |= found
        other |= rest
    return paths, other


def engine_read_audit(root, members, files):
    """Name the requested paths that the browser membership of `root` lacks."""
    requested, other = read_logs(files)
    lacking = sorted(path for path in requested if path not in members)
    unpublished = [path for path in lacking if (root / path).is_file()]
    absent = [path for path in lacking if not (root / path).is_file()]
    return {'logs': [str(file) for file in files], 'requested': len(requested),
            'unpublished': unpublished, 'absentFromRoot': absent, 'notAssetPaths': sorted(other)}


def parse_region_pairs(values, option):
    pairs = {}
    for value in values:
        region, separator, path = value.partition('=')
        if not separator or region not in {'cn', 'jp'} or not path:
            raise ValueError(f'Each {option} must be cn=PATH or jp=PATH')
        pairs.setdefault(region, []).append(Path(path).resolve())
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', action='append', default=[], metavar='REGION=PATH', help='Release root of a region')
    parser.add_argument('--log', action='append', default=[], metavar='REGION=FILE', help='Request or console log of a browser run')
    parser.add_argument('--exporter', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    sources = parse_region_pairs(args.source, '--source')
    logs = parse_region_pairs(args.log, '--log')
    if any(len(paths) != 1 for paths in sources.values()) or not sources or set(logs) != set(sources):
        raise ValueError('Give one --source and at least one --log for each region')
    sys.path.insert(0, str(args.exporter.resolve() / 'src'))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pack.groups import runtime_paths
    from prepare_deployment import browser_membership
    results = {}
    for region, (root,) in sorted(sources.items()):
        version = json.loads((root / 'mysekai-fixtures.json').read_bytes())['gameVersion']
        members, _ = browser_membership(root, region, version, runtime_paths)
        results[region] = engine_read_audit(root, members, logs[region])
        row = results[region]
        print(json.dumps({region: {'requested': row['requested'], 'unpublished': len(row['unpublished']),
                                   'absentFromRoot': len(row['absentFromRoot'])}}), flush=True)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if any(row['unpublished'] for row in results.values()):
        sys.exit(1)


if __name__ == '__main__':
    main()
