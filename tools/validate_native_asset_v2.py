"""Check actual native PackReader bytes against independently hashed inputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def validate(probe, store, catalog_id, expected):
    with tempfile.TemporaryDirectory(prefix="native-paths-", dir=store.parent) as scratch:
        paths = Path(scratch) / "paths.json"
        paths.write_text(json.dumps(sorted(expected), ensure_ascii=False), encoding="utf-8")
        result = subprocess.run([str(probe), str(store), catalog_id, str(paths)],
                                check=True, capture_output=True, encoding="utf-8", timeout=180)
    rows = json.loads(result.stdout)
    assert len(rows) == len(expected), "native reader omitted a requested path"
    for row in rows:
        assert row["path"] in expected
        assert {key: row[key] for key in ("bytes", "sha256")} == expected[row["path"]], row["path"]
    return {"catalog_sha256": catalog_id, "reads": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--store")
    parser.add_argument("--real-report")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if bool(args.store) != bool(args.real_report):
        parser.error("--store and --real-report must be supplied together")
    probe, fixtures = Path(args.probe).resolve(), Path(args.fixtures).resolve()
    index = json.loads((fixtures / "fixtures.json").read_bytes())
    result = {"schema": "moly-native-pack-conformance/1", "public": {}, "real": {}}
    # Select only actual runtime paths; source provenance and browser ledgers
    # intentionally remain outside the logical runtime asset set.
    for region, selection in index["catalogs"].items():
        catalog = json.loads((fixtures / "store/catalogs" / (selection["id"] + ".json")).read_bytes())
        expected = {path: index["expected"][region][path]
                    for package in catalog["packages"] for path in package["paths"]}
        result["public"][region] = validate(probe, fixtures / "store", selection["id"], expected)
    if args.store:
        report = json.loads(Path(args.real_report).read_bytes())
        for region, selection in report["regions"].items():
            expected = {row["path"]: {key: row[key] for key in ("bytes", "sha256")}
                        for row in selection["probes"]}
            result["real"][region] = validate(probe, Path(args.store).resolve(), selection["catalog_sha256"], expected)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({kind: {region: len(row["reads"]) for region, row in value.items()}
                      for kind, value in result.items() if kind != "schema"}))


if __name__ == "__main__":
    main()
