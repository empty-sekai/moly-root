"""Single command-line entry point for bundle extraction."""
import argparse
import json
import sys


def _configure_unity(fallback_version):
    import UnityPy.config
    if fallback_version:
        UnityPy.config.FALLBACK_UNITY_VERSION = fallback_version


def _master_source(args):
    """Directory or base URL for the caller's master tables, plus a cache dir.

    ``--master`` names a local directory; ``--master-url`` fetches each table by
    appending its name to a base URL (with no value, the public default base).
    Neither is implied: without one of them nothing is read and nothing is
    fetched.
    """
    from .master import DEFAULT_MASTER_URL
    url = getattr(args, "master_url", None)
    if url is not None:
        return url or DEFAULT_MASTER_URL, getattr(args, "master_cache", None)
    return getattr(args, "master", None), None


def _print_extract_summary(report, out_dir):
    s = report["summary"]
    print(f"extracted {s['succeeded']}/{s['requested']} bundles -> {out_dir} "
          f"(failed {s['failed']}, unsupported {s['unsupported']})")
    for entry in report["bundles"]:
        if entry["status"] == "failed":
            print(f"  failed: {entry['bundle']}: {entry['error']}")
    print(f"report: {report['report']}")
    if str(out_dir) == "local-data":
        print("view: python -m http.server 8000 (from the repository root), then open\n"
              "      http://localhost:8000/examples/viewer/index.html?base=../../local-data")
    else:
        print("view: serve the repository root over HTTP and open "
              "examples/viewer/index.html?base=<path to your output directory>")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="moly", description="Extract Unity humanoid assets")
    ap.add_argument("--unity-version", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="download, decrypt, and extract character asset packs")
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", help="download/decrypt workspace (default: moly-pull-output)")
    p.add_argument("--extract-out", default=None,
                   help="where extraction artifacts go (default: local-data, which stays out of version control)")
    p.add_argument("--json", action="store_true",
                   help="print the full JSON report to stdout instead of a summary")
    p.add_argument("--asset-base-url", required=True)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--master", help="directory of caller-supplied master tables")
    p.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    p.add_argument("--master-cache", help="where fetched tables are cached")
    p.add_argument("--vgmstream", help="path to the external audio decoder "
                                       "(vgmstream-cli), or the directory holding it")
    p.add_argument("--ffmpeg", help="path to ffmpeg, used only to write a compressed copy of each decoded sound")
    x = sub.add_parser("extract", help="extract bundles listed in a manifest")
    x.add_argument("--manifest",
                   help="optional bundle list; omitted = select every supported "
                        "asset-pack bundle found under --bundles")
    x.add_argument("--bundles", required=True)
    x.add_argument("--out", default="local-data",
                   help="output directory (default: local-data, which stays out of version control)")
    x.add_argument("--json", action="store_true",
                   help="print the full JSON report to stdout instead of a summary")
    x.add_argument("--master", help="directory of caller-supplied master tables")
    x.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    x.add_argument("--master-cache", help="where fetched tables are cached")
    x.add_argument("--fixture-meshes", action="store_true",
                   help="also write furniture geometry (fixture-models/), one "
                        "glTF binary per package; without it that pass is "
                        "skipped and the passes that need the geometry report "
                        "it as a missing dependency")
    x.add_argument("--fixture-particles", action="store_true",
                   help="also write furniture particle emitters "
                        "(fixture-particles/), one document per package "
                        "across the fixture-interface, cutscene-timeline and "
                        "fixture-timeline families; without it that pass is "
                        "skipped")
    x.add_argument("--builtin-resources", action="append", default=[],
                   metavar="PATH",
                   help="the engine's own built-in resource container, or a "
                        "directory holding it; some particle-system "
                        "renderers draw a copy of a built-in primitive mesh "
                        "and no package ships those. Repeatable. Without it "
                        "those mesh slots stay listed as unresolved in "
                        "fixture-particles/, exactly as before")
    x.add_argument("--vgmstream", help="path to the external audio decoder "
                                       "(vgmstream-cli), or the directory holding it")
    x.add_argument("--ffmpeg", help="path to ffmpeg, used only to write a compressed copy of each decoded sound")
    c = sub.add_parser("characters", help="extract one character bundle")
    c.add_argument("--bundle", required=True); c.add_argument("--out-dir", required=True); c.add_argument("--name", required=True)
    c.add_argument("--aux-bundle", action="append", default=[]); c.add_argument("--motion-bundle"); c.add_argument("--clips"); c.add_argument("--unit-id", type=int); c.add_argument("--facial-tables"); c.add_argument("--atlas-cell", default="512x256"); c.add_argument("--no-cloth", action="store_true")
    m = sub.add_parser("motion-library", help="export the shared humanoid motion library")
    m.add_argument("--reference-bundle", required=True); m.add_argument("--motion-bundle", required=True); m.add_argument("--aux-bundle", action="append", default=[]); m.add_argument("--clips"); m.add_argument("--out-dir", required=True); m.add_argument("--name", default="motion-library")
    t = sub.add_parser("facial-tables", help="dump facial tables")
    t.add_argument("--bundle", required=True); t.add_argument("--out", required=True)
    n = sub.add_parser("alone-actions", help="dump per-character performance data (motion x facial x timing)")
    n.add_argument("--bundle", required=True); n.add_argument("--out", required=True)
    g = sub.add_parser("registry", help="build the character registry from master tables")
    g.add_argument("--master", help="directory of caller-supplied master tables")
    g.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    g.add_argument("--master-cache", help="where fetched tables are cached")
    g.add_argument("--manifest", required=True, help="manifest naming the character bundles")
    g.add_argument("--motion-index", help="shared motion library index, to check motion names")
    g.add_argument("--out", required=True)

    k = sub.add_parser("talks", help="extract single-character direct-talk corpus")
    k.add_argument("--master", help="directory of caller-supplied master tables")
    k.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    k.add_argument("--master-cache", help="where fetched tables are cached")
    k.add_argument("--bundle", required=True, help="talk scenario bundle")
    k.add_argument("--out", required=True)

    e = sub.add_parser("emoticons", help="extract overhead-item effect packages")
    e.add_argument("--bundle", action="append", required=True); e.add_argument("--out-dir", required=True)
    w = sub.add_parser("phenomena", help="extract weather (phenomena) environment packages")
    w.add_argument("--bundle", action="append", required=True,
                   help="an environment package, or the shared phenomena thumbnail package")
    w.add_argument("--bundle-root",
                   help="directory the packages a bundle depends on are read from")
    w.add_argument("--builtin-resources", action="append", default=[],
                   metavar="PATH",
                   help="the engine's own built-in resource container, or a "
                        "directory holding it; some emitters draw copies of a "
                        "built-in primitive and no package ships those. Repeatable. "
                        "Without it those meshes stay listed under `unsupported`, "
                        "exactly as before")
    w.add_argument("--out-dir", required=True)
    w.add_argument("--master", help="directory of caller-supplied master tables")
    w.add_argument("--master-url", nargs="?", const="", default=None,
                   help="base URL to append <table>.json to; no value uses the public default base")
    w.add_argument("--master-cache", help="where fetched tables are cached")
    w.add_argument("--vgmstream", help="path to the external audio decoder "
                                      "(vgmstream-cli), or the directory holding it; "
                                      "without it PATH is searched and the audio "
                                      "entry says what is missing")
    w.add_argument("--ffmpeg", help="path to ffmpeg, used only to write a compressed "
                                    "copy of each decoded sound")
    q = sub.add_parser("site", help="extract the site (place) asset packages")
    q.add_argument("--bundle", action="append", required=True,
                   help="a package under the site path; repeat for as many as wanted")
    q.add_argument("--bundle-root",
                   help="directory the packages a bundle depends on are read from")
    q.add_argument("--out-dir", required=True)
    q.add_argument("--master", help="directory of caller-supplied master tables")
    q.add_argument("--master-url", nargs="?", const="", default=None,
                   help="base URL to append <table>.json to; no value uses the public default base")
    q.add_argument("--master-cache", help="where fetched tables are cached")
    a = sub.add_parser("fetch-apk", help="discover, download, or inspect an Android APK")
    a.add_argument("--endpoint", default=None); a.add_argument("--timeout", type=float, default=30.0); a.add_argument("--retries", type=int, default=3)
    asub = a.add_subparsers(dest="apk_command", required=True)
    asub.add_parser("latest")
    ad = asub.add_parser("download"); ad.add_argument("destination"); ad.add_argument("--url"); ad.add_argument("--sha256")
    ai = asub.add_parser("inspect"); ai.add_argument("apk")
    args = ap.parse_args(argv); _configure_unity(args.unity_version)
    if args.cmd == "pull":
        from .fetch import pull
        source, cache = _master_source(args)
        report = pull(args.manifest, args.out, args.asset_base_url, args.workers, args.retries,
                      master=source, master_cache=cache, extract_out=args.extract_out,
                      vgmstream=args.vgmstream, ffmpeg=args.ffmpeg)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"downloaded {report['downloads']}/{report['requiredBundles']} bundles")
            # The sound packages are the one part of the set that master rows name,
            # so "no audio" has a reason the caller must see without asking for JSON.
            audio = report.get("audio") or {}
            if audio.get("roots"):
                print(f"audio: {len(audio['roots'])} sound packages named by master tables")
            elif audio.get("error"):
                print(f"audio: no sound package pulled ({audio['error']})")
            if audio.get("notInManifest"):
                print(f"audio: {len(audio['notInManifest'])} named sound packages "
                      f"are not in this manifest: "
                      f"{', '.join(audio['notInManifest'])}")
            _print_extract_summary(report["extraction"], args.extract_out or "local-data")
        return 0 if not report["extraction"]["summary"]["failed"] else 1
    if args.cmd == "fetch-apk":
        from . import apk
        forwarded = []
        if args.endpoint: forwarded += ["--endpoint", args.endpoint]
        forwarded += ["--timeout", str(args.timeout), "--retries", str(args.retries), args.apk_command]
        if args.apk_command == "download":
            forwarded += [args.destination]
            if args.url: forwarded += ["--url", args.url]
            if args.sha256: forwarded += ["--sha256", args.sha256]
        elif args.apk_command == "inspect":
            forwarded += [args.apk]
        return apk.main(forwarded)
    if args.cmd == "extract":
        from .extract import extract_manifest
        from .assets.packages import builtin_archive_paths
        source, cache = _master_source(args)
        report = extract_manifest(args.manifest, args.bundles, args.out, args.unity_version,
                                  master=source, master_cache=cache,
                                  fixture_meshes=args.fixture_meshes,
                                  fixture_particles=args.fixture_particles,
                                  builtin_resources=builtin_archive_paths(
                                      args.builtin_resources),
                                  vgmstream=args.vgmstream, ffmpeg=args.ffmpeg)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            if report.get("discovery"):
                d = report["discovery"]
                print(f"discovered {d['selected']} pack bundles under {args.bundles} "
                      f"(ignored {d['ignored']} unrelated files)")
            _print_extract_summary(report, args.out)
        return 0 if not report["summary"]["failed"] else 1
    from chara import characters
    if args.cmd == "motion-library":
        from chara.motion_library import export_motion_library
        names = [s for s in args.clips.split(",") if s] if args.clips else None
        report = export_motion_library(args.reference_bundle, args.motion_bundle, args.out_dir, args.name, aux=tuple(args.aux_bundle), names=names)
        print(json.dumps(report, ensure_ascii=False)); return 0
    if args.cmd == "registry":
        source, cache = _master_source(args)
        if not source:
            ap.error("registry needs master tables: pass --master <dir> or --master-url")
        from .assets.manifest import parse_manifest
        from .assets.router import route
        from .extract import _unit_id
        from .master import Master, MissingTable
        from chara.registry import build_registry
        names = parse_manifest(args.manifest)
        units = sorted({u for n in names if route(n) and route(n).domain == "character"
                        for u in [_unit_id(n)] if u is not None})
        index = None
        if args.motion_index:
            with open(args.motion_index, encoding="utf-8") as handle:
                index = json.load(handle)
        source, cache = _master_source(args)
        master = Master(source, cache_dir=cache)
        try:
            client_configs = master.client_configs()
        except MissingTable:
            client_configs = None
        document = build_registry(master, units, index, client_configs=client_configs)
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=1, allow_nan=False)
            handle.write("\n")
        print(json.dumps(document["summary"], ensure_ascii=False))
        return 0

    if args.cmd == "talks":
        source, cache = _master_source(args)
        if not source:
            ap.error("talks needs master tables: pass --master <dir> or --master-url")
        from chara.talks import extract_talks
        source, cache = _master_source(args)
        print(json.dumps(extract_talks(source, args.bundle, args.out,
                                       master_cache=cache), ensure_ascii=False))
        return 0

    if args.cmd == "emoticons":
        from chara.emoticons import extract_emoticons
        print(json.dumps(extract_emoticons(args.bundle, args.out_dir), ensure_ascii=False))
        return 0
    if args.cmd == "phenomena":
        from .assets.packages import builtin_archive_paths
        from phenomena.environments import extract_phenomena
        source, cache = _master_source(args)
        report = extract_phenomena(args.bundle, args.out_dir,
                                   bundle_root=args.bundle_root, master=source,
                                   master_cache=cache, vgmstream=args.vgmstream,
                                   ffmpeg=args.ffmpeg,
                                   extra_archives=builtin_archive_paths(
                                       args.builtin_resources))
        print(json.dumps({k: v for k, v in report.items() if k != "perBundle"},
                         ensure_ascii=False))
        return 0
    if args.cmd == "site":
        from sites.pack import extract_sites
        source, cache = _master_source(args)
        report = extract_sites(args.bundle, args.out_dir,
                               bundle_root=args.bundle_root, master=source,
                               master_cache=cache)
        print(json.dumps({k: v for k, v in report.items() if k != "perBundle"},
                         ensure_ascii=False))
        return 0
    if args.cmd == "alone-actions":
        from chara.alone_actions import write_alone_actions
        print(json.dumps(write_alone_actions(args.bundle, args.out), ensure_ascii=False))
        return 0
    if args.cmd == "facial-tables":
        tables = characters.facial_tables(args.bundle)
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(tables, fh, ensure_ascii=False, indent=1, allow_nan=False); fh.write("\n")
        print(json.dumps({k: len(v) for k, v in tables.items()})); return 0
    sampled = None
    if args.motion_bundle and args.clips:
        sampled = characters.sample_clips(args.motion_bundle, [s for s in args.clips.split(",") if s])
    facial = None
    if args.facial_tables:
        with open(args.facial_tables, encoding="utf-8") as fh: facial = json.load(fh)
    cw, ch = args.atlas_cell.lower().split("x")
    report = characters.extract_character(args.bundle, args.out_dir, args.name, sampled=sampled, unit_id=args.unit_id, facial=facial, atlas_cell=(int(cw), int(ch)), with_cloth=not args.no_cloth, aux=tuple(args.aux_bundle))
    print(json.dumps(report, ensure_ascii=False)); return 0


if __name__ == "__main__":
    sys.exit(main())
