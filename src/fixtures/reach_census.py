"""Reach census: which packages hold ParticleSystems that a timeline reaches,
and which of them the fixture particle export has no document for.

Recomputes :func:`fixtures.timeline_reach.package_reach` -- the rule the
extractor selects its timeline-reached packages by -- over every bundle file
of a directory, with no name filter, merged as ``reached_packages`` merges it: a ``ControlPlayableAsset`` clip's
target (through each director that plays its timeline, else the reference's
default value, and ``prefabGameObject``), an ``EffectClip`` template, and a
``SignalReceiver`` reaction's persistent-call target each reach the
ParticleSystems of their object's Transform subtree.  Each bundle is read in
its own store, as the rule reads it; a bundle that does not read (or does
not load) is counted and named, never dropped.

Then every reached package is looked up in a fixture particle index
(``fixture-particles-v2/index.json``): a package has a document when its
index entry names a file and that file is on disk beside the index.

Usage::

    python -m fixtures.reach_census --bundles <bundle dir> \\
        --index <root>/fixture-particles-v2/index.json [--out census.json]

The report prints a summary line and, with ``--out``, writes every reached
package with its row count, the clip kinds that reach it and the timeline
packages they belong to.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from core.assets.packages import PackageStore
from fixtures.timeline_reach import package_reach


def census(bundle_dir, index_path, progress=None):
    """The reach of every bundle file in *bundle_dir* against the index at
    *index_path*: ``{"bundles", "errors", "reached", "withDocument",
    "withoutDocument"}``."""
    with open(index_path, encoding="utf-8") as fh:
        index = json.load(fh)["packages"]
    documents = os.path.dirname(os.path.abspath(index_path))
    names = sorted(name for name in os.listdir(bundle_dir) if os.path.isfile(os.path.join(bundle_dir, name)))
    reached, errors = {}, {}
    for number, name in enumerate(names):
        try:
            rows = package_reach(PackageStore([os.path.join(bundle_dir, name)], bundle_dir), name)
        except Exception as exc:  # an unreadable bundle is named, not dropped
            errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        if rows is None:
            errors[name] = "not loaded"
            continue
        for row in rows:
            for package in sorted({particle[0] for particle in row["particles"]}):
                entry = reached.setdefault(package, {"rows": 0, "kinds": {}, "timelines": set()})
                entry["rows"] += 1
                entry["kinds"][row["kind"]] = entry["kinds"].get(row["kind"], 0) + 1
                entry["timelines"].add(name)
        if progress is not None:
            progress(number, len(names), name)

    def has_document(package):
        file = (index.get(package) or {}).get("file")
        return bool(file) and os.path.isfile(os.path.join(documents, file))

    packages = {name: dict(entry, timelines=sorted(entry["timelines"]), indexed=name in index,
                           document=has_document(name))
                for name, entry in sorted(reached.items())}
    return {
        "bundles": len(names),
        "errors": errors,
        "reached": packages,
        "withDocument": [name for name, entry in packages.items() if entry["document"]],
        "withoutDocument": [name for name, entry in packages.items() if not entry["document"]],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundles", required=True, help="Directory of decrypted bundles (every file is read)")
    parser.add_argument("--index", required=True, help="fixture-particles-v2/index.json of the root to check")
    parser.add_argument("--out", help="Write the full report here")
    parser.add_argument("--unity-version")
    args = parser.parse_args(argv)
    sys.setrecursionlimit(10000)  # the track walk recurses through nested groups
    from core.extract import _configure_unity_version
    _configure_unity_version(args.unity_version)

    def progress(number, total, name):
        if number % 500 == 0:
            print(f"{number}/{total} {name}", flush=True)

    report = census(args.bundles, args.index, progress)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
    print(f"bundles {report['bundles']}, unreadable {len(report['errors'])}, reached packages "
          f"{len(report['reached'])}, with a document {len(report['withDocument'])}, without "
          f"{len(report['withoutDocument'])}: {report['withoutDocument']}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
