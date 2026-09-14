"""Single command-line entry point for bundle extraction."""
import argparse
import warnings
import json
import sys
from pathlib import Path


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


def _write_programs(directory, entries, platform=None):
    """Write each program body to its own file; return how many were written.

    A body is named by shader, platform and record index rather than by content
    hash, so a caller reading a file knows which variant produced it; identical
    variants therefore appear more than once, which is the honest shape -- the
    census reports the distinct count separately.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = 0
    for entry in entries:
        stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in entry["shader"])
        for block in entry["platforms"]:
            if platform is not None and block["platform"] != platform:
                continue
            for row in block["programs"]:
                suffix = "glsl" if row["isText"] else "bin"
                name = f"{stem}__platform{block['platform']}__record{row['record']}.{suffix}"
                (directory / name).write_bytes(row.get("code") or b"")
                written += 1
    return written


def _extract_failed(report):
    """A failed derived artifact is a failed extraction, not a skipped option."""
    return bool(report["summary"]["failed"]) or any(
        entry.get("status") == "failed" for entry in report.get("derived", []))


def _print_extract_summary(report, out_dir):
    s = report["summary"]
    print(f"extracted {s['succeeded']}/{s['requested']} bundles -> {out_dir} "
          f"(failed {s['failed']}, unsupported {s['unsupported']})")
    for entry in report["bundles"]:
        if entry["status"] == "failed":
            print(f"  failed: {entry['bundle']}: {entry['error']}")
    for entry in report.get("derived", []):
        if entry.get("status") == "failed":
            print(f"  failed artifact: {entry['artifact']}: {entry.get('error', '')}")
    print(f"report: {report['report']}")
    if str(out_dir) == "local-data":
        print("view: python -m http.server 8000 (from the repository root), then open\n"
              "      http://localhost:8000/examples/viewer/index.html?base=../../local-data")
    else:
        print("view: serve the repository root over HTTP and open "
              "examples/viewer/index.html?base=<path to your output directory>")


def main(argv=None):
    # UnityPy warns about asset shapes it does not model. Silencing that is
    # this command's choice to make, so it is made here, once, for this
    # process. It used to sit at module scope in four extractors, where
    # importing one rewrote the warning filters of any program that used this
    # package as a library -- including filters that program had set itself.
    warnings.filterwarnings("ignore")
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
    x.add_argument("--player-data",
                   help="path to the APK player data root; supplies the "
                        "built-in UI and screen-layer assets that the "
                        "download path does not carry (extract_manifest "
                        "accepts it; the CLI used to drop it)")
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
                        "directory holding it; some particle-system renderers "
                        "and some phenomena emitters draw a copy of a "
                        "built-in primitive mesh and no package ships those. "
                        "Repeatable. Without it those mesh slots stay listed "
                        "as unresolved in fixture-particles/ and in the "
                        "phenomena index, exactly as before")
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
    k.add_argument("--lib", help="decrypted lib package carrying the constant "
                                 "tables the scripts name; without it every "
                                 "Table.key token stays a source token")
    k.add_argument("--out", required=True)

    tw = sub.add_parser("tweets", help="extract tweet texts and per-character after-edit pools")
    tw.add_argument("--master", help="directory of caller-supplied master tables")
    tw.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    tw.add_argument("--master-cache", help="where fetched tables are cached")
    tw.add_argument("--out", required=True)

    tt = sub.add_parser("tweet-tables",
                        help="extract the tweet selection tables (greetings, "
                             "greeting conditions, site entries, talk pre-actions)")
    tt.add_argument("--master", help="directory of caller-supplied master tables")
    tt.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    tt.add_argument("--master-cache", help="where fetched tables are cached")
    tt.add_argument("--out", required=True)

    sg = sub.add_parser("site-groups",
                        help="extract the mysekai site-group table "
                             "(siteGroupId -> the site ids it holds)")
    sg.add_argument("--master", help="directory of caller-supplied master tables")
    sg.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    sg.add_argument("--master-cache", help="where fetched tables are cached")
    sg.add_argument("--out", required=True)

    bp = sub.add_parser("birthday-parties",
                        help="extract the birthday-party campaign table the "
                             "site-map festival gate reads (startAt/closedAt windows)")
    bp.add_argument("--master", help="directory of caller-supplied master tables")
    bp.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    bp.add_argument("--master-cache", help="where fetched tables are cached")
    bp.add_argument("--out", required=True)

    cc = sub.add_parser("client-config",
                        help="extract the ClientConfig deliverable panel "
                             "(the four typed dictionaries, keyed by id)")
    cc.add_argument("--master", help="directory of caller-supplied master tables")
    cc.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    cc.add_argument("--master-cache", help="where fetched tables are cached")
    cc.add_argument("--out", required=True)

    from core.master_tables import REGISTERED_TABLES
    mt = sub.add_parser("master-tables",
                        help="extract selected key-addressed mysekai master "
                             "tables, defaulting to blueprints, items, music "
                             "records and wordings")
    mt.add_argument("--master", help="directory of caller-supplied master tables")
    mt.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    mt.add_argument("--master-cache", help="where fetched tables are cached")
    mt.add_argument("--table", dest="tables", action="append",
                    choices=tuple(REGISTERED_TABLES),
                    help="registered table to export; repeat to select more "
                         "(omitting this exports only the original four tables)")
    mt.add_argument("--out", required=True,
                    help="directory the selected documents are written into")

    e = sub.add_parser("emoticons", help="extract overhead-item effect packages")
    e.add_argument("--bundle", action="append", required=True); e.add_argument("--out-dir", required=True)
    v = sub.add_parser("avatar-parts", help="extract player-appearance packages (skin/decoration/penlight)")
    v.add_argument("--bundle", action="append", required=True); v.add_argument("--out-dir", required=True)
    ht = sub.add_parser("harvest-tools", help="extract original harvest-tool prefabs and their material data")
    ht.add_argument("--bundle", action="append", required=True)
    ht.add_argument("--bundle-root", help="directory containing declared shader dependencies")
    ht.add_argument("--out-dir", required=True, help="tool directory, normally <assets>/avatar/tools")
    pa = sub.add_parser("player-avatar",
                        help="export the player avatar composite (audience model + skeleton + motion clips) as one glb")
    pa.add_argument("--bundle", action="append", required=True,
                    help="the avatar model bundle first, then every motion bundle whose clips "
                         "are to be resolved against it (the order export_player_avatar takes); "
                         "repeat --bundle per package")
    pa.add_argument("--out-dir", required=True)
    pa.add_argument("--name", default="mysekai__player_avatar",
                    help="output basename (glb + .index.json + .motion-manifest.json)")
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
    a = sub.add_parser("mysekai-audio",
                       help="extract the rest of the sound corpus: talk voices "
                            "named by talks.json, plus the remaining se, bgm "
                            "and part-voice packages")
    a.add_argument("--talks", required=True,
                   help="a talk corpus in either shape this repo's extractors "
                        "write (talks grouped under units, or a flat talks "
                        "list); its voice cues are the talk-voice denominator, "
                        "and only those cues are decoded")
    a.add_argument("--manifest", required=True,
                   help="asset bundle manifest giving the family denominators")
    a.add_argument("--bundle-root", required=True,
                   help="directory holding the decrypted sound packages")
    a.add_argument("--out-dir", required=True,
                   help="the phenomena output directory whose audio/ receives "
                        "the products and whose loop.json is merged into")
    a.add_argument("--vgmstream", help="path to the external audio decoder "
                                       "(vgmstream-cli), or the directory holding it")
    a.add_argument("--ffmpeg", help="path to ffmpeg, used only to write a compressed "
                                    "copy of each decoded sound")
    r = sub.add_parser("partvoice-routes",
                       help="build the speaker-to-partvoice-package routing "
                            "table from master tables and the roster")
    r.add_argument("--master", help="directory of caller-supplied master tables")
    r.add_argument("--master-url", nargs="?", const="", default=None,
                   help="base URL to append <table>.json to; no value uses the public default base")
    r.add_argument("--master-cache", help="where fetched tables are cached")
    r.add_argument("--roster", required=True,
                   help="characters.json, the registry artifact; its "
                        "characters are the participants' universe and order")
    r.add_argument("--out-dir", required=True,
                   help="the phenomena output directory whose audio/ receives "
                        "partvoice.json")
    u = sub.add_parser("ui-action-icons",
                       help="export the standalone action-button icon bundles "
                            "as one PNG per bundle plus a manifest")
    u.add_argument("--bundle", action="append", required=True,
                   help="an action-icon bundle; repeat for as many as wanted")
    u.add_argument("--out-dir", required=True)
    lay = sub.add_parser("ui-layout",
                         help="extract the RectTransform hierarchy of the UI "
                              "prefabs (screen layers, dialogs) the player data "
                              "builds, plus a census of the UI package families")
    lay.add_argument("--player-data", required=True,
                     help="the APK player data file; every screen and dialog "
                          "prefab lives in its resources.assets (Resources.Load "
                          "fetches them, so no bundle name routes to them)")
    lay.add_argument("--out-dir", required=True,
                     help="the output directory (ui-layout/) that receives one "
                          "document per prefab plus census.json")
    lay.add_argument("--bundles", default=None,
                     help="optional decrypted-package directory; named so the "
                          "census also reports what the per-screen download "
                          "packages contain (textures and effects, not layout)")
    lay.add_argument("--bundle-manifest", default=None,
                     help="optional AssetBundleInfoNew.json; supplies the "
                          "isBuiltin split and declared sizes for the census")
    f = sub.add_parser("fixture-master-slice",
                       help="slice mysekaiFixtures down to the columns the "
                            "runtime reads (button gating and footprint)")
    f.add_argument("--master", help="directory of caller-supplied master tables")
    f.add_argument("--master-url", nargs="?", const="", default=None,
                   help="base URL to append <table>.json to; no value uses the public default base")
    f.add_argument("--master-cache", help="where fetched tables are cached")
    f.add_argument("--out", required=True)
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
    ua = sub.add_parser("ui-audio", help="extract embedded UI cue sheets with the shared audio decoder")
    ua.add_argument("--apk", required=True)
    ua.add_argument("--out", required=True)
    ua.add_argument("--vgmstream")
    ua.add_argument("--ffmpeg")
    a = sub.add_parser("fetch-apk", help="discover, download, or inspect an Android APK")
    a.add_argument("--endpoint", default=None); a.add_argument("--timeout", type=float, default=30.0); a.add_argument("--retries", type=int, default=3)
    asub = a.add_subparsers(dest="apk_command", required=True)
    asub.add_parser("latest")
    ad = asub.add_parser("download"); ad.add_argument("destination"); ad.add_argument("--url"); ad.add_argument("--sha256")
    ai = asub.add_parser("inspect"); ai.add_argument("apk")
    s = sub.add_parser("shader", help="what a package's shaders declare and contain")
    s.add_argument("package", help="one AssetBundle file")
    s.add_argument("--platform", type=int, default=None,
                   help="count only this ShaderCompilerPlatform (9 is GLSL text)")
    s.add_argument("--out", help="write the full census here as JSON")
    s.add_argument("--programs", metavar="DIR",
                   help="write each program body here, named by shader, platform and record")
    s.add_argument("--json", action="store_true", help="print the counts as JSON")

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
        return 1 if _extract_failed(report["extraction"]) else 0
    if args.cmd == "shader":
        import UnityPy
        from shaders import census as shader_census
        entries = shader_census.census(UnityPy.load(args.package),
                                       keep_code=bool(args.programs))
        counts = shader_census.totals(entries, args.platform)
        if args.out:
            from .jsonio import write_json
            write_json(args.out, {"package": args.package,
                                  "shaders": shader_census.without_code(entries)})
        if args.programs:
            written = _write_programs(args.programs, entries, args.platform)
            counts["bodiesWritten"] = written
        if args.json:
            print(json.dumps(counts, ensure_ascii=False))
        else:
            scope = "all platforms" if args.platform is None else f"platform {args.platform}"
            print(f"{counts['shaders']} shaders, {counts['records']} program records, "
                  f"{counts['uniquePrograms']} distinct programs ({scope})")
            if counts["platformErrors"]:
                print(f"{counts['platformErrors']} platform blob(s) failed to parse")
            for name, records, unique in shader_census.by_shader(entries, args.platform)[:10]:
                print(f"  {unique:6d} distinct / {records:6d} records  {name}")
        return 1 if counts["platformErrors"] else 0
    if args.cmd == "ui-audio":
        from ui.audio import extract_ui_audio
        report = extract_ui_audio(args.apk, args.out, decoder=args.vgmstream, transcoder=args.ffmpeg)
        print(json.dumps(report, ensure_ascii=False))
        return 0
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
                                  player_data=args.player_data,
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
        return 1 if _extract_failed(report) else 0
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
                                       master_cache=cache,
                                       lib_bundle=args.lib), ensure_ascii=False))
        return 0

    if args.cmd == "tweets":
        source, cache = _master_source(args)
        if not source:
            ap.error("tweets needs master tables: pass --master <dir> or --master-url")
        from chara.tweets import extract_tweets
        print(json.dumps(extract_tweets(source, args.out, master_cache=cache),
                         ensure_ascii=False))
        return 0

    if args.cmd == "tweet-tables":
        source, cache = _master_source(args)
        if not source:
            ap.error("tweet-tables needs master tables: "
                     "pass --master <dir> or --master-url")
        from chara.tweet_tables import extract_tweet_tables
        print(json.dumps(extract_tweet_tables(source, args.out, master_cache=cache),
                         ensure_ascii=False))
        return 0

    if args.cmd == "site-groups":
        source, cache = _master_source(args)
        if not source:
            ap.error("site-groups needs master tables: "
                     "pass --master <dir> or --master-url")
        from sites.site_groups import extract_site_groups
        print(json.dumps(extract_site_groups(source, args.out, master_cache=cache),
                         ensure_ascii=False))
        return 0

    if args.cmd == "ui-action-icons":
        from ui.action_icon import export_action_icons
        print(json.dumps(export_action_icons(args.bundle, args.out_dir),
                         ensure_ascii=False))
        return 0

    if args.cmd == "ui-layout":
        from ui.prefab_layout import extract_layout
        result = extract_layout(args.player_data, args.out_dir,
                                bundles_root=args.bundles,
                                bundle_manifest=args.bundle_manifest)
        for item in result["written"]:
            summary = item["summary"]
            print(f"layout: {item['family']}/{item['prefab']} "
                  f"nodes={summary['nodes']} sprites={summary['spriteReferences']} "
                  f"texts={summary['textComponents']} "
                  f"partial={len(summary['partialComponents'])}")
        for item in result["failures"]:
            print(f"layout FAILED {item['prefab']}: {item['reason']}")
        print(f"census: {result['censusPath']}")
        return 0 if not result["failures"] else 1

    if args.cmd == "fixture-master-slice":
        source, cache = _master_source(args)
        if not source:
            ap.error("fixture-master-slice needs master tables: "
                     "pass --master <dir> or --master-url")
        from fixtures.master_slice import export_fixture_master_slice
        print(json.dumps(export_fixture_master_slice(source, args.out, master_cache=cache),
                         ensure_ascii=False))
        return 0

    if args.cmd == "birthday-parties":
        source, cache = _master_source(args)
        if not source:
            ap.error("birthday-parties needs master tables: "
                     "pass --master <dir> or --master-url")
        from sites.birthday_parties import extract_birthday_parties
        print(json.dumps(extract_birthday_parties(source, args.out, master_cache=cache),
                         ensure_ascii=False))
        return 0

    if args.cmd == "client-config":
        source, cache = _master_source(args)
        if not source:
            ap.error("client-config needs master tables: "
                     "pass --master <dir> or --master-url")
        from core.client_config import extract_client_config
        print(json.dumps(extract_client_config(source, args.out, master_cache=cache),
                         ensure_ascii=False))
        return 0

    if args.cmd == "master-tables":
        source, cache = _master_source(args)
        if not source:
            ap.error("master-tables needs master tables: "
                     "pass --master <dir> or --master-url")
        from core.master_tables import extract_master_tables
        print(json.dumps(extract_master_tables(source, args.out,
                                               master_cache=cache, tables=args.tables),
                         ensure_ascii=False))
        return 0

    if args.cmd == "emoticons":
        from chara.emoticons import extract_emoticons
        print(json.dumps(extract_emoticons(args.bundle, args.out_dir), ensure_ascii=False))
        return 0
    if args.cmd == "avatar-parts":
        from chara.avatar_parts import extract_avatar_parts
        print(json.dumps(extract_avatar_parts(args.bundle, args.out_dir), ensure_ascii=False))
        return 0
    if args.cmd == "harvest-tools":
        from chara.harvest_tools import extract_harvest_tools
        result = extract_harvest_tools(args.bundle, args.out_dir, bundle_root=args.bundle_root)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if any(row["status"] == "failed" for row in result["perBundle"].values()) else 0
    if args.cmd == "player-avatar":
        from chara.player_avatar import export_player_avatar, write_motion_manifest
        record = export_player_avatar(args.bundle, args.out_dir, name=args.name)
        manifest_path = str(Path(args.out_dir) / f"{args.name}.motion-manifest.json")
        write_motion_manifest(record, manifest_path)
        print(json.dumps({
            "glb": record["glb"], "index": record["index"],
            "motionManifest": manifest_path,
            "sourcePackages": record["sourcePackages"],
            "playerMesh": record["playerMesh"],
            "counts": record["counts"]}, ensure_ascii=False))
        return 0
    if args.cmd == "phenomena":
        from .assets.packages import builtin_archive_paths, require_paths
        from phenomena.environments import extract_phenomena
        source, cache = _master_source(args)
        # --bundle takes paths, not bare package names: PackageStore keys the
        # store by basename and UnityPy.load answers a path that is not there
        # with an empty environment and no error, so a bare name runs green
        # over zero objects.  Refuse it here, before any work is done.
        require_paths(args.bundle)
        report = extract_phenomena(args.bundle, args.out_dir,
                                   bundle_root=args.bundle_root, master=source,
                                   master_cache=cache, vgmstream=args.vgmstream,
                                   ffmpeg=args.ffmpeg,
                                   extra_archives=builtin_archive_paths(
                                       args.builtin_resources))
        print(json.dumps({k: v for k, v in report.items() if k != "perBundle"},
                         ensure_ascii=False))
        return 0
    if args.cmd == "mysekai-audio":
        from phenomena.audio_corpus import extract_audio_corpus
        report = extract_audio_corpus(args.talks, args.manifest,
                                      args.bundle_root, args.out_dir,
                                      decoder=args.vgmstream,
                                      transcoder=args.ffmpeg)
        print(json.dumps({k: v for k, v in report.items() if k != "audio"},
                         ensure_ascii=False))
        return 0
    if args.cmd == "partvoice-routes":
        source, cache = _master_source(args)
        if not source:
            ap.error("partvoice-routes needs master tables: "
                     "pass --master <dir> or --master-url")
        from .master import Master
        from phenomena.partvoice import (build_partvoice_routes,
                                         route_counts, write_partvoice_routes)
        roster = json.loads(Path(args.roster).read_text(encoding="utf-8"))
        document = build_partvoice_routes(Master(source, cache_dir=cache),
                                          roster)
        path = write_partvoice_routes(document, args.out_dir)
        print(json.dumps({**route_counts(document), "file": str(path)},
                         ensure_ascii=False))
        return 0
    if args.cmd == "site":
        from .assets.packages import require_paths
        from sites.pack import extract_sites
        source, cache = _master_source(args)
        # Same refusal as the phenomena command: a bare package name under
        # --bundle would extract zero objects and report green.
        require_paths(args.bundle)
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
