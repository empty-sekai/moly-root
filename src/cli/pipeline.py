"""Source receipts, reproducible exports and depot inspection."""
import argparse
import json
from pathlib import Path

from core.atomic import write_json
from pack.verify import strict_json
from pipeline.depot import Depot
from pipeline.stages import catalog_task, communication_task, official_task, runtime_task, talk_task
from source.snapshot import file_input, read_inputs, snapshot


def main(argv):
    parser = argparse.ArgumentParser(prog="moly")
    groups = parser.add_subparsers(dest="group", required=True)
    source = groups.add_parser("source", help="record or verify a versioned source")
    sources = source.add_subparsers(dest="command", required=True)
    record = sources.add_parser("record")
    record.add_argument("--input", action="append", required=True, metavar="ROLE=PATH")
    record.add_argument("--region", required=True)
    record.add_argument("--version", required=True)
    record.add_argument("--out", required=True)
    check = sources.add_parser("verify")
    check.add_argument("receipt")
    export = groups.add_parser("export", help="export products through declared tasks")
    exports = export.add_subparsers(dest="command", required=True)
    talks = exports.add_parser("talks")
    talks.add_argument("--master", required=True)
    talks.add_argument("--bundle", required=True)
    talks.add_argument("--lib", required=True)
    talks.add_argument("--family", choices=("general", "fixture"), default="general")
    talks.add_argument("--unity-version", default="2022.3.62f3")
    communication = exports.add_parser("communication-actions")
    communication.add_argument("--master", required=True)
    communication.add_argument("--bundle", required=True)
    communication.add_argument("--unity-version", default="2022.3.62f3")
    catalog = exports.add_parser("text-catalog")
    catalog.add_argument("--corpus", action="append", required=True)
    catalog.add_argument("--region", required=True)
    catalog.add_argument("--source-revision", required=True)
    catalog.add_argument("--previous")
    official = exports.add_parser("official-text")
    official.add_argument("--source", required=True)
    official.add_argument("--donor", required=True)
    official.add_argument("--locale", required=True)
    runtime = exports.add_parser("text-runtime")
    runtime.add_argument("--source", required=True)
    runtime.add_argument("--overlay", action="append", default=[])
    for command in (talks, communication, catalog, official, runtime):
        command.add_argument("--depot", required=True)
        command.add_argument("--out", required=True, help="layout directory, outside the depot")
        command.add_argument("--plan", action="store_true", help="inspect reuse without extraction or layout")
        command.add_argument("--emit-task", help="save the portable task description")
        command.add_argument("--force", action="store_true", help="recompute and compare the existing result")
    depot = groups.add_parser("depot", help="inspect, run, verify and lay out tasks")
    depots = depot.add_subparsers(dest="command", required=True)
    for name in ("plan", "run", "verify", "layout", "gc"):
        command = depots.add_parser(name)
        command.add_argument("--depot", required=True)
        if name in ("plan", "run"):
            command.add_argument("--task", required=True)
        if name == "plan":
            command.add_argument("--previous", help="previous task for invalidation reasons")
        if name == "run":
            command.add_argument("--force", action="store_true")
        if name == "layout":
            command.add_argument("--key", required=True)
            command.add_argument("--out", required=True)
        if name == "gc":
            command.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.group == "source":
            if args.command == "record":
                inputs = []
                for item in args.input:
                    role, separator, path = item.partition("=")
                    if not separator or not role or not path:
                        raise ValueError("--input expects ROLE=PATH")
                    inputs.append(file_input(role, path))
                result = snapshot(inputs, region=args.region, version=args.version)
                write_json(args.out, result)
                report = {"revision": result["revision"], "inputs": len(inputs)}
            else:
                result = strict_json(Path(args.receipt).read_bytes())
                rebuilt = snapshot(result["inputs"], region=result["region"], version=result["version"])
                if result != rebuilt:
                    raise ValueError("source receipt identity differs from its inputs")
                read_inputs(result["inputs"])
                report = {"revision": result["revision"], "verified": True}
        elif args.group == "export":
            if args.command == "talks":
                task = talk_task(args.master, args.bundle, args.lib, family=args.family,
                                 unity_version=args.unity_version)
            elif args.command == "communication-actions":
                task = communication_task(args.master, args.bundle, unity_version=args.unity_version)
            elif args.command == "text-catalog":
                task = catalog_task(args.corpus, region=args.region, source_revision=args.source_revision,
                                    previous=args.previous)
            elif args.command == "official-text":
                task = official_task(args.source, args.donor, locale=args.locale)
            else:
                task = runtime_task(args.source, args.overlay)
            depot = Depot(args.depot)
            if args.emit_task:
                write_json(args.emit_task, task)
            if args.plan:
                report = depot.plan(task)
            else:
                run = depot.run(task, force=args.force)
                report = {"status": run["status"], **depot.materialize(task["key"], args.out)}
        else:
            depot = Depot(args.depot)
            if args.command in {"plan", "run"}:
                task = strict_json(Path(args.task).read_bytes())
                if args.command == "plan":
                    previous = strict_json(Path(args.previous).read_bytes()) if args.previous else None
                    report = depot.plan(task, previous=previous)
                else:
                    report = depot.run(task, force=args.force)
            elif args.command == "layout":
                report = depot.materialize(args.key, args.out)
            elif args.command == "gc":
                from pack.gc import collect_garbage
                report = collect_garbage(args.depot, apply=args.apply)
            else:
                report = depot.verify()
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"moly: {exc}\n")
