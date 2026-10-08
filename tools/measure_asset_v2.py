"""Measure converted runtime-artifact overlap; never infer whole-game dedup."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pack.groups import runtime_paths, group_of_v2
from pack.identity import canonical_bytes


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()


def measure(sources):
    indexes, descriptions, unique = {}, {}, {}
    for region, source in sources.items():
        source = Path(source).resolve()
        paths = runtime_paths(source)
        index = {}
        for number, name in enumerate(sorted(paths)):
            file = source / name
            before = file.stat()
            index[name] = {"bytes": before.st_size, "sha256": digest_file(file)}
            after = file.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError("source changed during measurement: " + name)
            if number % 2000 == 0: print(f"{region}: hashed {number}/{len(paths)} files", flush=True)
        indexes[region] = index
        unique[region] = {row["sha256"]: row["bytes"] for row in index.values()}
        master = json.loads((source / "mysekai-fixtures.json").read_bytes()) if (source / "mysekai-fixtures.json").is_file() else {}
        receipt = json.loads((source / "source.json").read_bytes()) if (source / "source.json").is_file() else {}
        descriptions[region] = {"runtime_version": master.get("gameVersion"), "logical_files": len(index),
            "logical_bytes": sum(row["bytes"] for row in index.values()),
            "unique_decoded_objects": len(unique[region]), "unique_decoded_bytes": sum(unique[region].values()),
            "runtime_index_sha256": hashlib.sha256(canonical_bytes(index)).hexdigest(),
            "has_shared_ui_provenance": bool(receipt.get("sharedUi")),
            "scope": "src/pack/groups.runtime_paths converted runtime membership; excludes backups/raw Unity/build reports"}
        print(region, json.dumps(descriptions[region]), flush=True)
    union = {hash_: size for rows in unique.values() for hash_, size in rows.items()}
    regions = list(sources)
    pairs = []
    for i, left in enumerate(regions):
        for right in regions[i + 1:]:
            shared = unique[left].keys() & unique[right].keys()
            common_paths = indexes[left].keys() & indexes[right].keys()
            identical_paths = [name for name in common_paths if indexes[left][name]["sha256"] == indexes[right][name]["sha256"]]
            domains = defaultdict(lambda: {"identical_paths": 0, "logical_bytes": 0})
            for name in identical_paths:
                group = group_of_v2(name)[1]
                domains[group]["identical_paths"] += 1
                domains[group]["logical_bytes"] += indexes[left][name]["bytes"]
            pairs.append({"left": left, "right": right, "shared_unique_decoded_objects": len(shared),
                "shared_unique_decoded_bytes": sum(unique[left][key] for key in shared),
                "common_paths": len(common_paths), "identical_common_paths": len(identical_paths),
                "same_path_shared_domains": dict(sorted(domains.items()))})
    separate = sum(sum(rows.values()) for rows in unique.values())
    report = {"schema": "moly-asset-dedup-measurement/1", "sources": descriptions, "pairs": pairs,
        "separate_region_unique_decoded_bytes": separate, "global_unique_decoded_bytes": sum(union.values()),
        "cross_region_saved_decoded_bytes": separate - sum(union.values()),
        "cross_region_saved_fraction": (separate - sum(union.values())) / separate if separate else 0,
        "qualification": "Measured decoded bytes of the selected converted runtime roots, not a guess, full-game corpus, network transfer ratio, or independent UI extraction. Runtime donor UI reuse is included when present; representation/package measurements are recorded separately after actual builds."}
    return report, indexes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, help="REGION=runtime-directory")
    parser.add_argument("--report", required=True)
    parser.add_argument("--private-index", required=True)
    args = parser.parse_args(argv)
    sources = dict(value.split("=", 1) for value in args.source)
    if len(sources) != len(args.source): parser.error("duplicate source region")
    report, indexes = measure(sources)
    for name, value in [(args.report, report), (args.private_index, indexes)]:
        output = Path(name); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical_bytes(value))
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
