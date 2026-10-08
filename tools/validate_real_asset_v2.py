"""Build/measure complete real regional runtime selections in one shared store.

Never edits a source tree. Publication is private validation output. Requires the
per-file index produced by measure_asset_v2.py so changed input cannot masquerade
as the measured corpus. Proprietary binary payloads are not part of the report.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pack.groups as groups
from pack.build import build
from pack.identity import canonical_bytes
from pack.release import activate
from pack.verify import verify_catalog, strict_json


def sha(data): return hashlib.sha256(data).hexdigest()


def analyze(catalog, output, source_index):
    blobs, manifests, contents, entries = {}, {}, set(), {}
    for package in catalog["packages"]:
        raw = (output / package["manifest"]).read_bytes()
        doc = strict_json(raw)
        manifests[package["manifest"]] = len(raw); contents.add(doc["content_id"])
        for row in doc["entries"]:
            assert row["path"] in source_index, row["path"]
            expected = source_index[row["path"]]
            assert (row["bytes"], row["content_sha256"]) == (expected["bytes"], expected["sha256"]), "source changed since measured index: " + row["path"]
            blobs[row["blob"]] = row["blob_bytes"]; entries[row["path"]] = row
    assert entries.keys() == source_index.keys(), "release omits measured runtime paths"
    return {"blobs": blobs, "manifests": manifests, "contents": contents, "entries": entries}


def probes(index):
    choices = [
        ("fixture", lambda n: n.startswith("fixture-models/") and n.endswith(".glb")),
        ("character", lambda n: n.startswith("sd_") and n.endswith(".glb")),
        ("site", lambda n: n.startswith("site/scenes/") and n.endswith(".glb")),
        ("ui", lambda n: n.startswith("ui/") and n.endswith(".png")),
        ("audio", lambda n: n.startswith("phenomena/audio/") and n.endswith((".ogg", ".wav"))),
        ("weather", lambda n: n.startswith("phenomena/001_") and n.endswith(".json")),
        ("fixture-thumbnail", lambda n: n.startswith("fixture-thumbnails/") and n.endswith(".png")),
    ]
    result = []
    for kind, predicate in choices:
        selected = sorted((row["bytes"], name) for name, row in index.items() if predicate(name) and row["bytes"] >= 64)
        if not selected: raise ValueError("missing real probe domain: " + kind)
        size, name = selected[0]
        result.append({"domain": kind, "path": name, **index[name]})
    for name in ["manifest.json", "characters.json", "mysekai-fixtures.json", "fixture-gimmick/browser-index.json"]:
        if name in index: result.append({"domain": "table", "path": name, **index[name]})
    large_audio = [(row["bytes"], name) for name, row in index.items()
                   if name.startswith("phenomena/audio/") and name.endswith((".ogg", ".wav"))
                   and row["bytes"] > 16 * 1024 * 1024]
    if large_audio:
        _, name = max(large_audio)
        result.append({"domain": "large-audio", "path": name, **index[name]})
    for domain, name in [
        ("fixture-bike", "fixture-models/mysekai__fixture__mdl_ext0013_fixture_bike1.glb"),
        ("site-full", "site/scenes/grasslands/grasslands.glb"),
    ]:
        if name in index and not any(row["path"] == name for row in result):
            result.append({"domain": domain, "path": name, **index[name]})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--reuse-selected", action="store_true",
                        help="reuse an existing pinned region selection only when every measured path/hash/size matches")
    parser.add_argument("--membership-correction", help="JSON evidence of excluded extraction-only runtime paths")
    args = parser.parse_args(argv)
    sources = {region: Path(directory).resolve() for region, directory in (item.split("=", 1) for item in args.source)}
    indexes = strict_json(Path(args.index).read_bytes()); out = Path(args.out).resolve()
    catalogs, measurements, analyzed, provenance = {}, {}, {}, {}
    real_build = groups.build; counter = 0
    def observed_build(*args, **kwargs):
        nonlocal counter
        result = real_build(*args, **kwargs); counter += 1
        if counter % 25 == 0:
            print("completed package builds", counter, "last entries", result.get("entries"), flush=True)
        return result
    groups.build = observed_build
    for region, source in sources.items():
        metadata = strict_json((source / "mysekai-fixtures.json").read_bytes())
        provenance[region] = {"validation": "complete-runtime-asset-v2",
            "runtime_index_sha256": sha(canonical_bytes(indexes[region])),
            "runtime_source": strict_json((source / "source.json").read_bytes()) if (source / "source.json").is_file() else {"region": region, "runtime_game_version": metadata["gameVersion"]}}
        print("BUILD", region, metadata["gameVersion"], "files", len(indexes[region]), flush=True)
        begin = time.monotonic()
        catalog = None
        mode = "built"
        selector = out / f"channels/{region}/stable.json"
        if args.reuse_selected and selector.is_file():
            pointer = strict_json(selector.read_bytes())
            raw = (out / pointer["catalog"]).read_bytes()
            if Path(pointer["catalog"]).stem != sha(raw):
                raise ValueError("existing selected catalog content address mismatch")
            previous = strict_json(raw)
            if previous.get("region") != region:
                raise ValueError("existing selected catalog region mismatch")
            if previous.get("version") == metadata["gameVersion"]:
                old_paths = {name for package in previous["packages"] for name in package["paths"]}
                if old_paths == indexes[region].keys():
                    # analyze checks every file's decoded identity against the
                    # fresh read-only source scan. Final independent verification
                    # still reads/hashes/decodes every retained store object.
                    analyze(previous, out, indexes[region])
                    catalog = previous
                    provenance[region] = previous["provenance"]
                    mode = "existing selection matches fresh measured source"
        if catalog is None:
            catalog = groups.build_groups(source, out, metadata["gameVersion"], region=region, provenance=provenance[region])
        elapsed = time.monotonic() - begin
        catalogs[region] = catalog; data = analyze(catalog, out, indexes[region]); analyzed[region] = data
        measurements[region] = {"version": catalog["version"], "catalog_sha256": sha(canonical_bytes(catalog)),
            "catalog_bytes": len(canonical_bytes(catalog)), "packages": len(data["manifests"]),
            "package_manifest_bytes": sum(data["manifests"].values()), "maximum_manifest_bytes": max(data["manifests"].values()),
            "unique_encoded_blobs": len(data["blobs"]), "unique_encoded_bytes": sum(data["blobs"].values()),
            "logical_entries": len(data["entries"]), "build_seconds": round(elapsed, 3) if mode == "built" else None,
            "selection_mode": mode, "selection_seconds": round(elapsed, 3), "probes": probes(indexes[region])}
        (out.parent / f"real-probe-paths-{region}.json").write_bytes(canonical_bytes([row["path"] for row in measurements[region]["probes"]]))
        print("BUILT", region, json.dumps({key: value for key, value in measurements[region].items() if key != "probes"}), flush=True)
        (out.parent / "real-selected-catalogs.json").write_bytes(canonical_bytes({
            key: {"catalog_sha256": value["catalog_sha256"], "version": value["version"], "logical_entries": value["logical_entries"]}
            for key, value in measurements.items()}))
    first = next(iter(sources)); original = catalogs[first]
    print("WARM repeat", first, flush=True)
    repeat = groups.build_groups(sources[first], out, original["version"], region=first, provenance=provenance[first])
    assert canonical_bytes(repeat) == canonical_bytes(original), "warm build changed immutable release bytes"
    print("METADATA-ONLY repeat", first, flush=True)
    changed = groups.build_groups(sources[first], out, original["version"] + "+metadata-validation", region=first,
                                  provenance={**provenance[first], "different_release_receipt": True})
    assert changed["packages"] == original["packages"], "release metadata changed package identities"
    assert sha(canonical_bytes(changed)) != sha(canonical_bytes(original))
    activate(out, sha(canonical_bytes(original)))
    cold = []
    # Rebuild real complete packages with no prior manifest/encoded-object cache.
    chosen = {}
    for package in original["packages"]:
        if package["kind"] not in chosen and package["content_bytes"] <= 12 * 1048576:
            chosen[package["kind"]] = package
    for kind, package in chosen.items():
        directory = out.parent / "cold-packages" / kind
        result = build(sources[first], directory, "unrelated-cold-release", paths=package["paths"], manifest_name=None,
                       categories_path=Path(groups.__file__).with_name("groups.toml"), format_version=2)
        assert result["manifest_sha256"] == Path(package["manifest"]).stem, kind
        cold.append({"kind": kind, "id": package["id"], "manifest_sha256": result["manifest_sha256"], "content_id": result["content_id"]})
    print("VERIFY default + channels + retained history", flush=True)
    errors, info = verify_catalog(out / "asset-packs.json")
    if errors: raise ValueError("; ".join(errors))
    assert not info["orphan_blobs"] and not info["orphan_manifests"]
    union = {name: size for data in analyzed.values() for name, size in data["blobs"].items()}
    manifests = {name: size for data in analyzed.values() for name, size in data["manifests"].items()}
    physical_blobs = {path.relative_to(out / "blobs").as_posix(): path.stat().st_size
                      for path in (out / "blobs").rglob("*") if path.is_file()}
    physical_manifests = {path.relative_to(out).as_posix(): path.stat().st_size
                          for path in (out / "packages").glob("*.json") if path.is_file()}
    separate = sum(sum(data["blobs"].values()) for data in analyzed.values())
    regions = list(sources); pairs = []
    for i, left in enumerate(regions):
        for right in regions[i + 1:]:
            a, b = analyzed[left], analyzed[right]
            shared = a["blobs"].keys() & b["blobs"].keys()
            shared_packages = a["manifests"].keys() & b["manifests"].keys()
            left_labels = {row["id"]: row for row in catalogs[left]["packages"]}
            right_labels = {row["id"]: row for row in catalogs[right]["packages"]}
            common_labels = left_labels.keys() & right_labels.keys()
            kind_counts = {}
            for row in catalogs[left]["packages"]:
                if row["manifest"] in shared_packages:
                    kind_counts[row["kind"]] = kind_counts.get(row["kind"], 0) + 1
            pairs.append({"left": left, "right": right, "shared_encoded_blobs": len(shared),
                "shared_encoded_bytes": sum(a["blobs"][name] for name in shared),
                "shared_package_manifests": len(shared_packages), "shared_content_ids": len(a["contents"] & b["contents"]),
                "shared_package_manifest_bytes": sum(a["manifests"][name] for name in shared_packages),
                "common_package_labels": len(common_labels),
                "same_paths_in_common_label": sum(left_labels[name]["paths"] == right_labels[name]["paths"] for name in common_labels),
                "identical_package_kind_counts": kind_counts,
                "shared_package_examples": [row for row in catalogs[left]["packages"] if row["manifest"] in shared_packages][:12]})
    report = {"schema": "moly-asset-real-pack-validation/1", "regions": measurements, "pairs": pairs,
        "separate_region_encoded_bytes": separate, "shared_store_encoded_bytes": sum(union.values()),
        "cross_region_saved_encoded_bytes": separate - sum(union.values()),
        "cross_region_saved_encoded_fraction": (separate - sum(union.values())) / separate,
        "shared_store_package_manifest_bytes": sum(manifests.values()), "cold_package_rebuilds": cold,
        "storage_scope": "shared_store_encoded_bytes and shared_store_package_manifest_bytes are the union of current selected regions; retained historical storage is listed separately",
        "physical_store_encoded_bytes": sum(physical_blobs.values()),
        "physical_store_encoded_blobs": len(physical_blobs),
        "physical_store_package_manifest_bytes": sum(physical_manifests.values()),
        "physical_store_package_manifests": len(physical_manifests),
        "retained_history_extra_encoded_bytes": sum(size for name, size in physical_blobs.items() if name not in union),
        "retained_history_extra_encoded_blobs": len(physical_blobs.keys() - union.keys()),
        "retained_history_extra_package_manifest_bytes": sum(size for name, size in physical_manifests.items() if name not in manifests),
        "retained_history_extra_package_manifests": len(physical_manifests.keys() - manifests.keys()),
        "warm_repeat_byte_identical": True, "metadata_only_packages_identical": True,
        "verified_channel_roots": info["channel_roots"], "retained_catalog_count": info["retained_catalogs"],
        "gc_orphan_blobs": 0, "gc_orphan_manifests": 0,
        "qualification": "Complete runtime_paths membership from the measured source roots; actual encoded CAS bytes and immutable package files. Not a full-game raw asset claim. Source UI donor reuse is preserved, not disguised as independent extraction. Wall times are measurements, not performance guarantees."}
    if args.membership_correction:
        report["membership_correction"] = strict_json(Path(args.membership_correction).read_bytes())
    Path(args.report).write_bytes(canonical_bytes(report))
    print("COMPLETE", json.dumps({key: value for key, value in report.items() if key not in {"regions", "pairs"}}, indent=2), flush=True)


if __name__ == "__main__": main()
