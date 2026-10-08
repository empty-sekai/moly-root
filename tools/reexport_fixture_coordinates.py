#!/usr/bin/env python3
"""Re-export an exact prior fixture corpus to a NEW canonical output tree.

The prior index supplies selection and clip provenance only. Every GLB and
locator transform is read again from original bundles. Per-package workers have
fresh PackageStores and are replaced after a bounded number of packages; no
shared asset source file is edited or hardlink overwritten.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import gc
import json
from pathlib import Path
import sys
import time
import warnings

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from core.atomic import write_json
from core.fixture_collision_document import INDEX_VERSION
from core.gltf import COORDINATE_SPACE


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _clip_identities(model):
    return {(row["sourceClip"]["file"], str(row["sourceClip"]["pathId"]))
            for row in model.get("animations", {}).get("clips", []) if row.get("sourceClip")}


def _one(task):
    warnings.filterwarnings("ignore")
    from core.assets.packages import PackageStore
    from fixtures.attach import extract_from_store as extract_attach
    from fixtures.meshes import _export_package
    warnings.filterwarnings("ignore")
    name, raw_root, output, animation_sources, expected = task
    begin = time.monotonic()
    output, raw_root = Path(output), Path(raw_root)
    try:
        store = PackageStore([raw_root / name], root=raw_root)
        model = _export_package(store, name, output / "fixture-models",
                                animation_sources=animation_sources)
        missing = sorted(set(map(tuple, expected)) - _clip_identities(model))
        if missing:
            raise ValueError(f"new export lost source animation identities: {missing}")
        receipt_dir = output / "receipts" / name
        attach_result = extract_attach(store, receipt_dir)
        attach_document = _read(attach_result["path"])
        return {"name": name, "model": model, "attach": attach_document["packages"][name],
                "seconds": round(time.monotonic() - begin, 3), "error": None}
    except Exception as exc:
        return {"name": name, "seconds": round(time.monotonic() - begin, 3),
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        gc.collect()


def _summary(packages):
    return {
        "bundles": len(packages),
        "exported": sum(row.get("status") == "exported" for row in packages.values()),
        "noMesh": sum(row.get("status") == "no-mesh" for row in packages.values()),
        "failed": sum(row.get("status") == "failed" for row in packages.values()),
        "prefabs": sum(len(row.get("variants", [])) for row in packages.values()),
        "skins": sum(row.get("skinCount", 0) for row in packages.values()),
        "animationClips": sum(row.get("animations", {}).get("clipCount", 0) for row in packages.values()),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--workers", default=2, type=int)
    parser.add_argument("--max-tasks-per-child", default=16, type=int)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("--out must not already exist; existing outputs are never overwritten")
    if not 1 <= args.workers <= 4 or args.max_tasks_per_child < 1:
        parser.error("workers must be 1..4 and max-tasks-per-child positive")
    prior = _read(args.source_root / "fixture-models/index.json")
    names = sorted(prior["packages"])
    missing = [name for name in names if not (args.raw_root / name).is_file()]
    if missing:
        parser.error(f"original raw bundles missing ({len(missing)}): {missing[:12]}")
    from fixtures.animation_refresh import plan_animation_refresh
    animation_sources = {row["package"]: row["sources"]
                         for row in plan_animation_refresh(args.source_root)}
    args.out.mkdir(parents=True)
    (args.out / "fixture-models").mkdir()
    (args.out / "fixture-attach").mkdir()
    (args.out / "receipts").mkdir()
    plan = {"coordinateContract": COORDINATE_SPACE,
            "sourceRoot": str(args.source_root.resolve()), "rawRoot": str(args.raw_root.resolve()),
            "packages": names, "animationSources": animation_sources,
            "workers": args.workers, "maxTasksPerChild": args.max_tasks_per_child}
    write_json(args.out / "reexport-plan.json", plan)
    tasks = [(name, str(args.raw_root.resolve()), str(args.out.resolve()), animation_sources.get(name, []),
              sorted(_clip_identities(prior["packages"][name]))) for name in names]
    models, attachments, outcomes = {}, {}, []
    begin = time.monotonic()
    with (args.out / "progress.jsonl").open("x", encoding="utf-8") as log:
        # Recreate a whole executor after each bounded batch. Python 3.13 on
        # Windows can strand queued futures when max_tasks_per_child retires
        # every worker simultaneously; bounded executors avoid that path.
        batch_size = args.workers * args.max_tasks_per_child
        for start in range(0, len(tasks), batch_size):
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(_one, task) for task in tasks[start:start + batch_size]]
                for future in as_completed(futures):
                    row = future.result()
                    name = row["name"]
                    if row["error"] is None:
                        models[name] = row.pop("model")
                        attachments[name] = row.pop("attach")
                    else:
                        models[name] = {"name": name, "status": "failed", "reason": row["error"]}
                    outcomes.append(row)
                    status = {"completed": len(outcomes), "requested": len(names), **row}
                    log.write(json.dumps(status, ensure_ascii=False) + "\n")
                    log.flush()
                    if row["error"] or len(outcomes) % 25 == 0 or len(outcomes) == len(names):
                        print(json.dumps(status, ensure_ascii=False), flush=True)
    summary = _summary(models)
    write_json(args.out / "fixture-models/index.json", {
        "version": INDEX_VERSION, "coordinateContract": COORDINATE_SPACE,
        "coordinateUnits": "source-unity-unit", "uvConvention": "unity-bottom-left",
        "summary": summary, "packages": models})
    prior_attach = _read(args.source_root / "fixture-attach/attach-points.json")
    from fixtures.attach import SEMANTICS
    attach_summary = dict(prior_attach.get("summary", {}))
    attach_summary.update(bundles=len(attachments),
                          pairs=sum(len(row.get("entries", [])) for row in attachments.values()))
    write_json(args.out / "fixture-attach/attach-points.json", {
        "version": 2, "coordinateContract": COORDINATE_SPACE,
        "coordinateUnits": "source-unity-unit", "uvConvention": "unity-bottom-left",
        "semantics": SEMANTICS, "summary": attach_summary, "packages": attachments})
    report = {"coordinateContract": COORDINATE_SPACE, "requested": len(names),
              "completed": len(outcomes), "summary": summary,
              "elapsedSeconds": round(time.monotonic() - begin, 3),
              "failures": [row for row in outcomes if row["error"]],
              "animationIdentityChecks": "all prior sourceClip identities must remain present",
              "scope": "fixture GLBs and attach only; other source domains are not modified"}
    write_json(args.out / "reexport-report.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
