"""Single command-line entry point for bundle extraction."""
import argparse
import os
import warnings
import json
import sys
from pathlib import Path

# Extraction output root; MOLY_EXTRACT_OUT lets a workspace keep it outside the checkout.
DEFAULT_DATA_ROOT = os.environ.get("MOLY_EXTRACT_OUT") or "local-data"


PLAYER_DATA_HELP = ("the player data: the container file (assets/bin/Data/data.unity3d), "
                    "or, for a build without one, each APK whose assets/bin/Data members "
                    "are its loose player files; repeat for the base APK and the asset pack")


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
    from core.master import DEFAULT_MASTER_URL
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
    """A failed derived artifact is a failed extraction, not a skipped option.

    So is a failed player-data artifact: its input was supplied and it could
    not be read, which is a failure of the run, while a player-data artifact
    whose input nobody supplied stays ``skipped`` and is named as an input gap.
    """
    return bool(report["summary"]["failed"]) or any(
        entry.get("status") == "failed"
        for entry in report.get("derived", []) + report.get("playerData", []))


def _print_extract_summary(report, out_dir):
    s = report["summary"]
    print(f"extracted {s['succeeded']}/{s['requested']} bundles -> {out_dir} "
          f"(failed {s['failed']}, unsupported {s['unsupported']})")
    for entry in report["bundles"]:
        if entry["status"] == "failed":
            print(f"  failed: {entry['bundle']}: {entry['error']}")
    for entry in report.get("derived", []) + report.get("playerData", []):
        if entry.get("status") == "failed":
            print(f"  failed artifact: {entry['artifact']}: {entry.get('error', '')}")
        programs = entry.get("sourcePrograms")
        if programs:
            print(f"  particle source programs: {programs['status']} "
                  f"({programs['converted']}/{programs['gles3Variants']} GLES3 variants converted, "
                  f"unavailable {programs['unavailable']})")
            print(f"  {programs['note']}")
    # An artifact whose input nobody supplied is not a failure of the run, but
    # it is not silent either: each one is named with its reason (a failed one
    # is already named above).
    for gap in (report.get("inputGaps") or {}).get("unaccepted", []):
        if gap.get("why") != "failed":
            print(f"  input gap: {gap['artifact']}: {gap.get('detail', '')}")
    print(f"report: {report['report']}")
    if str(out_dir) == "local-data":
        print("view: python -m http.server 8000 (from the repository root), then open\n"
              "      http://localhost:8000/examples/viewer/index.html?base=../../local-data")
    else:
        print("view: serve the repository root over HTTP and open "
              "examples/viewer/index.html?base=<path to your output directory>")


def main(argv=None):
    from chara.rotation_encoding import DEFAULT_ROTATION_ENCODING, ROTATION_ENCODINGS
    # UnityPy warns about asset shapes it does not model. Silencing that is
    # this command's choice to make, so it is made here, once, for this
    # process. It used to sit at module scope in four extractors, where
    # importing one rewrote the warning filters of any program that used this
    # package as a library -- including filters that program had set itself.
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(prog="moly", description="Extract Unity humanoid assets",
        epilog="Grouped workflows: moly source --help; moly export talks --help; moly export text-runtime --help; moly depot --help. Flat exports remain compatible.")
    ap.add_argument("--unity-version", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="download, decrypt, and extract character asset packs")
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", help="download/decrypt workspace (default: moly-pull-output)")
    p.add_argument("--extract-out", default=None,
                   help="where extraction artifacts go (default: $MOLY_EXTRACT_OUT, else local-data, which stays out of version control)")
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
    x.add_argument("--out", default=DEFAULT_DATA_ROOT,
                   help="output directory (default: $MOLY_EXTRACT_OUT, else local-data, which stays out of version control)")
    x.add_argument("--json", action="store_true",
                   help="print the full JSON report to stdout instead of a summary")
    x.add_argument("--master", help="directory of caller-supplied master tables")
    x.add_argument("--player-data", action="append",
                   help="the APK player data: the container file "
                        "(assets/bin/Data/data.unity3d), or, for a build "
                        "without one, each APK whose assets/bin/Data members "
                        "are its loose player files (repeat: the base APK and "
                        "the asset pack). It supplies the built-in UI and "
                        "screen-layer assets that the download path does not "
                        "carry (container builds only) and the Resources "
                        "objects: the live transitioner, the effect type "
                        "table, the graphics configuration, the layout grid")
    x.add_argument("--player-types", metavar="DIR",
                   help="managed assembly stubs of the same player build (a DummyDll "
                        "directory); the Resources objects read from --player-data "
                        "(the live transitioner, the effect type table, the drop-item "
                        "shading) need them, and without them those artifacts are "
                        "skipped with the reason")
    x.add_argument("--player-declarations", metavar="FILE",
                   help="the same build's declaration dump; names the effect types in "
                        "effect-resources/ (without it the names are null)")
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
                        "skipped. Playable output requires --shader-compiler; "
                        "without it only source data is saved, not playable assets")
    x.add_argument("--shader-compiler", metavar="PATH",
                   help="pinned offline source shader compiler required for playable "
                        "furniture particles; omitting it saves source-only data")
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
    x.add_argument("--rotation-encoding", choices=ROTATION_ENCODINGS, default=DEFAULT_ROTATION_ENCODING,
                   help="motion rotation key storage (default: snorm16); float32 disables lossy encoding")
    c = sub.add_parser("characters", help="extract one character bundle")
    c.add_argument("--bundle", required=True); c.add_argument("--out-dir", required=True); c.add_argument("--name", required=True)
    c.add_argument("--aux-bundle", action="append", default=[]); c.add_argument("--motion-bundle"); c.add_argument("--clips"); c.add_argument("--unit-id", type=int); c.add_argument("--facial-tables"); c.add_argument("--atlas-cell", default="512x256"); c.add_argument("--no-cloth", action="store_true")
    m = sub.add_parser("motion-library", help="export the shared humanoid motion library")
    m.add_argument("--reference-bundle", required=True); m.add_argument("--motion-bundle", required=True); m.add_argument("--aux-bundle", action="append", default=[]); m.add_argument("--clips"); m.add_argument("--out-dir", required=True); m.add_argument("--name", default="motion-library")
    m.add_argument("--controller-bundle", action="append", default=None,
                   help="character package whose AnimatorController selects the clips "
                        "(by serialized identity); repeat per package. Without it the "
                        "first clip of each name is kept and duplicates are listed")
    m.add_argument("--rotation-encoding", choices=ROTATION_ENCODINGS, default=DEFAULT_ROTATION_ENCODING,
                   help="rotation key storage (default: snorm16); float32 disables lossy encoding")
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

    k = sub.add_parser("talks", help="extract ordinary solo and multi-character talk scripts")
    k.add_argument("--master", help="directory of caller-supplied master tables")
    k.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    k.add_argument("--master-cache", help="where fetched tables are cached")
    k.add_argument("--bundle", required=True, help="talk scenario bundle")
    k.add_argument("--lib", help="decrypted lib package carrying the constant "
                                 "tables the scripts name; without it every "
                                 "Table.key token stays a source token")
    k.add_argument("--out", required=True)

    tc = sub.add_parser("text-catalog", help="export versioned display-text identities for translation")
    tc.add_argument("--corpus", action="append", required=True, help="extracted talk document; repeatable")
    tc.add_argument("--region", required=True, choices=("jp", "cn", "tw", "en", "kr"))
    tc.add_argument("--source-revision", required=True, help="explicit source resource revision")
    tc.add_argument("--previous", help="previous same-region text catalogue; preserves stable IDs")
    tc.add_argument("--out", required=True)
    ot = sub.add_parser("official-text", help="match official display text from another source region")
    ot.add_argument("--source", required=True, help="source text catalogue")
    ot.add_argument("--donor", required=True, help="official regional text catalogue")
    ot.add_argument("--locale", required=True, choices=("zh-CN", "zh-TW", "en-US", "ko-KR"))
    ot.add_argument("--out", required=True)

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

    bd = sub.add_parser("birthday-party-delivery",
                        help="extract the birthday-party delivery tables the "
                             "delivery site reads (rewards, point bonuses, total rewards)")
    bd.add_argument("--master", help="directory of caller-supplied master tables")
    bd.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    bd.add_argument("--master-cache", help="where fetched tables are cached")
    bd.add_argument("--out", required=True)

    gv = sub.add_parser("gate-visit-policy",
                        help="extract the gate visit tables as policy data for the "
                             "server mock (not a client character input)")
    gv.add_argument("--master", help="directory of caller-supplied master tables")
    gv.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    gv.add_argument("--master-cache", help="where fetched tables are cached")
    gv.add_argument("--out", required=True)

    tm = sub.add_parser("time-manager",
                        help="extract the project TimeManager settings (fixed step, "
                             "maximum allowed timestep, time scale, particle step) "
                             "from an APK or a globalgamemanagers file")
    tm.add_argument("--source", required=True,
                    help="an APK, or a player-data globalgamemanagers file")
    tm.add_argument("--out", required=True)

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
    ptm = sub.add_parser("player-tool-models",
                         help="extract the avatar's hand-item prefabs and their material data "
                              "(mysekai/player_tool_model/*)")
    ptm.add_argument("--bundle", action="append", required=True)
    ptm.add_argument("--bundle-root", help="directory containing declared shader dependencies")
    ptm.add_argument("--out-dir", required=True,
                     help="model directory, normally <assets>/avatar/player-tool-models")
    er = sub.add_parser("effect-resources",
                        help="export the effect manager's type table "
                             "(Resources Mysekai/Effects/EffectResourceData) from player data")
    er.add_argument("--player-data", required=True, action="append",
                    help=PLAYER_DATA_HELP)
    er.add_argument("--types", required=True,
                    help="managed assembly stubs of the same build (DummyDll directory)")
    er.add_argument("--declarations",
                    help="the same build's declaration dump, for effect type names")
    er.add_argument("--bundle-root",
                    help="bundle directory to check each row's bundle and prefab against")
    er.add_argument("--out", required=True, help="output JSON path")
    dis = sub.add_parser("drop-item-shade",
                         help="export the drop-item shading settings of the graphics "
                              "configuration (Resources Graphics/Data/MysekaiGraphicsConfig)")
    dis.add_argument("--player-data", required=True, action="append",
                     help=PLAYER_DATA_HELP)
    dis.add_argument("--types", required=True,
                     help="managed assembly stubs of the same build (DummyDll directory)")
    dis.add_argument("--out", required=True, help="output JSON path")
    sts = sub.add_parser("stencil-shadow",
                         help="export the character stencil-shadow colour and the material "
                              "rendering settings of the graphics configuration")
    sts.add_argument("--player-data", required=True, action="append",
                     help=PLAYER_DATA_HELP)
    sts.add_argument("--types", required=True,
                     help="managed assembly stubs of the same build (DummyDll directory)")
    sts.add_argument("--out", required=True, help="output JSON path")
    lgr = sub.add_parser("layout-grid",
                         help="export the layout editor's placement grid: its settings, "
                              "materials, shader programs and colour profiles "
                              "(Resources Data/MysekaiGridSettings and "
                              "Data/GridColorProfileTable)")
    lgr.add_argument("--player-data", required=True, action="append",
                     help=PLAYER_DATA_HELP)
    lgr.add_argument("--types", required=True,
                     help="managed assembly stubs of the same build (DummyDll directory)")
    lgr.add_argument("--out-dir", required=True,
                     help="output directory, normally <assets>/layout-grid")
    rpf = sub.add_parser("resource-prefab",
                         help="extract one prefab the player data ships in Resources, by load name")
    rpf.add_argument("--player-data", required=True, action="append",
                     help=PLAYER_DATA_HELP)
    rpf.add_argument("--types", required=True,
                     help="managed assembly stubs of the same build (DummyDll directory)")
    rpf.add_argument("--load-name", required=True, help="the Resources.Load path")
    rpf.add_argument("--out-dir", required=True,
                     help="directory receiving <prefab>/<prefab>.{json,glb} and textures")
    rpf.add_argument("--builtin-resources", action="append", default=[], metavar="PATH",
                     help="the engine's built-in resource file (unity default resources, "
                          "unity_builtin_extra) or a directory holding them; a container "
                          "build keeps them beside data.unity3d; repeat for each")
    rpr = sub.add_parser("resource-programs",
                         help="write the compiled programs of every shader a Resources "
                              "prefab's materials use (the program library layout)")
    rpr.add_argument("--player-data", required=True, action="append",
                     help=PLAYER_DATA_HELP)
    rpr.add_argument("--types", required=True,
                     help="managed assembly stubs of the same build (DummyDll directory)")
    rpr.add_argument("--load-name", required=True, help="the Resources.Load path")
    rpr.add_argument("--out-dir", required=True,
                     help="directory receiving <prefab>/shaders/ (the resource-prefab out-dir)")
    rpr.add_argument("--shader-compiler",
                     help="offline shader compiler executable; without it the programs are "
                          "written as source only (no conversions)")
    fp = sub.add_parser("fixture-particles",
                        help="write particle source-schema documents (the fixture-particles "
                             "layout) for explicitly named bundles")
    fp.add_argument("--bundle", action="append", required=True)
    fp.add_argument("--bundle-root", help="directory the bundles' dependencies are read from")
    fp.add_argument("--domain", action="append",
                    help="routed domain to extract; repeat; default the three furniture domains")
    fp.add_argument("--shader-compiler", metavar="PATH",
                    help="pinned offline shader compiler; without it programs stay source-only")
    fp.add_argument("--builtin-resources", action="append", default=[], metavar="PATH",
                    help="the engine's built-in resource container (or its directory), "
                         "for renderers that point at a built-in mesh")
    fp.add_argument("--out-dir", required=True)
    pp = sub.add_parser("prefab-package",
                        help="extract one named prefab of a bundle with the site package reader")
    pp.add_argument("--bundle", required=True, help="the bundle file")
    pp.add_argument("--prefab", required=True,
                    help="the prefab's container basename, e.g. fieldcamera.prefab")
    pp.add_argument("--bundle-root", help="directory the bundle's dependencies are read from")
    pp.add_argument("--out-dir", required=True,
                    help="directory receiving <prefab>/<prefab>.{json,glb} and textures")
    sae = sub.add_parser("site-action-effects",
                         help="extract the site-move and harvest-action effect prefabs "
                              "(mysekai/effect/site/harvest/action/*)")
    sae.add_argument("--bundle", action="append", required=True)
    sae.add_argument("--bundle-root",
                     help="directory containing declared effect, mesh, texture and shader dependencies")
    sae.add_argument("--out-dir", required=True,
                     help="effect directory, normally <assets>/site-action-effects")
    she = sub.add_parser("site-home-effects",
                         help="extract the home-site effect prefabs, the craft effect among "
                              "them (mysekai/effect/site/home/*)")
    she.add_argument("--bundle", action="append", required=True)
    she.add_argument("--bundle-root",
                     help="directory containing declared effect, mesh, texture and shader dependencies")
    she.add_argument("--out-dir", required=True,
                     help="effect directory, normally <assets>/site-home-effects")
    fpe = sub.add_parser("fixture-put-effects",
                         help="extract the furniture placement effect prefab "
                              "(mysekai/effect/fixture/fx_fixture_put_01)")
    fpe.add_argument("--bundle", action="append", required=True)
    fpe.add_argument("--bundle-root",
                     help="directory containing declared effect, mesh, texture and shader dependencies")
    fpe.add_argument("--out-dir", required=True,
                     help="effect directory, normally <assets>/fixture-put-effects")
    sep = sub.add_parser("site-edit-pick",
                         help="export tagged editor raycast colliders in authored Unity coordinates")
    sep.add_argument("--bundle", required=True,
                     help="one mysekai__site__field__<scene> source bundle")
    sep.add_argument("--apk", action="append", required=True,
                     help="matching client APK containing TagManager; repeat for split APKs")
    sep.add_argument("--out", required=True,
                     help="output sidecar, normally <assets>/site/edit-pick/<scene>.json")
    ssw = sub.add_parser("site-sound-wave",
                         help="resolve MysekaiSiteView._soundWaveMeshFilter (the footstep "
                              "surface) of site scene packages: node, mesh and vertex colours")
    ssw.add_argument("--bundle", action="append", required=True,
                     help="a mysekai__site__field__<scene> package; repeat")
    ssw.add_argument("--bundle-root", help="directory the scene packages' dependencies are read from")
    ssw.add_argument("--out", required=True, help="output JSON path")
    sce = sub.add_parser("site-common-action-effects",
                         help="extract the player's foot effect prefabs "
                              "(mysekai/effect/site/common/action/*)")
    sce.add_argument("--bundle", action="append", required=True)
    sce.add_argument("--bundle-root",
                     help="directory containing declared effect, texture and shader dependencies")
    sce.add_argument("--out-dir", required=True,
                     help="effect directory, normally <assets>/site-common-action-effects")
    sho = sub.add_parser("site-harvest-object-effects",
                         help="extract the harvest-object hit, delete and stay effect prefabs "
                              "(mysekai/effect/site/harvest/object/<hit|delete|stay>/*)")
    sho.add_argument("--bundle", action="append", required=True)
    sho.add_argument("--bundle-root",
                     help="directory containing declared effect, mesh, texture and shader dependencies")
    sho.add_argument("--out-dir", required=True,
                     help="effect directory, normally <assets>/site-harvest-object-effects")
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
    w.add_argument("--shader-compiler", metavar="PATH",
                   help="moly-shader-bridge executable; without it source programs "
                        "are preserved and their conversion is explicitly unavailable")
    w.add_argument("--site-bundle", action="append", default=None, metavar="PATH",
                   help="a site scene package whose site prefab colliders go into "
                        "collision.json; repeatable. Without it every scene package "
                        "under --bundle-root is used")
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
    ca = sub.add_parser("cutscene-audio",
                        help="decode the cues a cut-scene's own timeline names: its "
                             "voice clips from the cut-scene's birthday voice bank, "
                             "its music and effect clips from the packages they name")
    ca.add_argument("--clips", required=True, action="append",
                    help="a cut-scene clip document (cutscene-timeline/clips/"
                         "<package>.json); repeat for each cut-scene")
    ca.add_argument("--cut-scenes", required=True,
                    help="the cut-scene master table (mysekaiCutScenes.json), "
                         "which routes voice clips to their voice bank")
    ca.add_argument("--manifest", required=True,
                    help="asset bundle manifest the named packages are checked against")
    ca.add_argument("--bundle-root", required=True,
                    help="directory holding the decrypted sound packages")
    ca.add_argument("--out-dir", required=True,
                    help="the phenomena output directory whose audio/ receives "
                         "the products and whose loop.json and corpus.json are merged into")
    ca.add_argument("--vgmstream", help="path to the external audio decoder "
                                        "(vgmstream-cli), or the directory holding it")
    ca.add_argument("--ffmpeg", help="path to ffmpeg, used only to write a compressed "
                                     "copy of each decoded sound")
    sq = sub.add_parser("audio-sequences",
                        help="attach the cues' sequence structure (type, repeat, "
                             "per-track delay, track -> stream) to an existing "
                             "loop sidecar, reading the archives again from the "
                             "decrypted tree; writes a copy, never in place")
    sq.add_argument("--loop", required=True, help="the loop sidecar to read")
    sq.add_argument("--bundle-root", required=True, action="append",
                    help="directory holding the decrypted sound packages; repeat "
                         "to give further trees, tried in order per entry")
    sq.add_argument("--out", required=True,
                    help="where the annotated copy is written (must not be --loop)")
    ra = sub.add_parser("record-audio",
                        help="read the waveform facts (length, loop region or none, "
                             "cue commands) of every package a music record can "
                             "play; the audio itself is not written")
    ra.add_argument("--master", help="directory of caller-supplied master tables")
    ra.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    ra.add_argument("--master-cache", help="where fetched tables are cached")
    ra.add_argument("--bundle-root", required=True, action="append",
                    help="directory holding decrypted bundles; repeat to give further trees")
    ra.add_argument("--bundle-version", required=True,
                    help="the asset version the bundles were downloaded at (written on every row)")
    ra.add_argument("--only", choices=("music", "music_sound_track"),
                    help="read only the packages of this record track type")
    ra.add_argument("--vgmstream", help="path to the external audio decoder "
                                        "(vgmstream-cli), or the directory holding it")
    ra.add_argument("--out", required=True, help="the document to write")
    rs = sub.add_parser("record-audio-storage",
                        help="add each row's storage mirror audio file: sha256 and size of "
                             "the downloaded bytes and the listing's version")
    rs.add_argument("input", help="a record-audio document")
    rs.add_argument("--server", required=True, choices=("jp", "cn"))
    rs.add_argument("--workers", type=int, default=4)
    rs.add_argument("--out", required=True)
    rm = sub.add_parser("record-audio-merge",
                        help="combine record-audio documents of separate runs")
    rm.add_argument("inputs", nargs="+")
    rm.add_argument("--out", required=True)
    rc = sub.add_parser("record-audio-carry",
                        help="carry a read record-audio document's facts to another "
                             "region's packages whose bundles have the same crc and size")
    rc.add_argument("--master", help="directory of the target region's master tables")
    rc.add_argument("--master-url", nargs="?", const="", default=None,
                    help="base URL to append <table>.json to; no value uses the public default base")
    rc.add_argument("--master-cache", help="where fetched tables are cached")
    rc.add_argument("--manifest", required=True,
                    help="the target region's package list (AssetBundleInfoNew)")
    rc.add_argument("--source", required=True,
                    help="a record-audio document with storage attached")
    rc.add_argument("--source-manifest", required=True,
                    help="the package list of the bundles the source document was read from")
    rc.add_argument("--region", required=True, choices=("jp", "cn"),
                    help="the target region, written on the document")
    rc.add_argument("--out", required=True)
    rf = sub.add_parser("record-audio-fetch",
                        help="download and check (size, uncompressed crc) the bundles of "
                             "the packages a record-audio document lists as absent")
    rf.add_argument("document", help="a record-audio document (its absent list is fetched)")
    rf.add_argument("--manifest", required=True,
                    help="the package list (AssetBundleInfoNew) the bundles are fetched by")
    rf.add_argument("--asset-base-url", required=True,
                    help="the prefix before each entry's downloadPath")
    rf.add_argument("--workers", type=int, default=4)
    rf.add_argument("--out", required=True, help="workspace; bundles land in <out>/decrypted")
    ac = sub.add_parser("audio-categories",
                        help="write the sound configuration's category table (name, "
                             "index, volume) the runtime scales cues by")
    ac.add_argument("--configuration", required=True,
                    help="the sound configuration (.acf), or the application "
                         "package (zip) that holds assets/*.acf")
    ac.add_argument("--out", required=True,
                    help="where categories.json is written (the runtime reads "
                         "phenomena/audio/categories.json)")
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
    portraits = sub.add_parser("browser-assets",
                               help="export source character portraits for the browser catalog")
    portraits.add_argument("--player-data", required=True,
                          help="the APK player data containing CharacterSDIconAtlas")
    portraits.add_argument("--asset-root", required=True,
                          help="runtime resource root containing characters.json")
    portraits.add_argument("--source-region", required=True, choices=("cn", "jp"))
    portraits.add_argument("--source-game-version", required=True,
                          help="game version of the supplied UI player data")
    presentation = sub.add_parser("fixture-presentation",
                                  help="resolve model, wallpaper, floor and custom furniture source routes")
    presentation.add_argument("--asset-root", required=True)
    presentation.add_argument("--master", required=True)
    presentation.add_argument("--bundles", required=True)
    validate = sub.add_parser("validate-runtime", help="verify referenced resources and exact audio cue coverage")
    validate.add_argument("--asset-root", required=True)
    validate.add_argument("--verify-actor-hashes", action="store_true")
    fixture_animations = sub.add_parser("fixture-animations",
                                        help="refresh source-bound fixture clips and their authored time domain")
    fixture_animations.add_argument("--asset-root", required=True)
    fixture_animations.add_argument("--bundles", required=True)
    fixture_animations.add_argument("--package", action="append")
    fixture_animations.add_argument("--plan", action="store_true")
    capabilities = sub.add_parser("timeline-capabilities", help="index source payload requirements per fixture prefab")
    capabilities.add_argument("--asset-root", required=True)
    fonts = sub.add_parser("text-fonts", help="prepare an independent display-font catalogue")
    fonts.add_argument("--base-font", action="append", required=True)
    fonts.add_argument("--font", action="append", required=True)
    fonts.add_argument("--license", required=True)
    fonts.add_argument("--out-dir", required=True)
    tutorial = sub.add_parser("tutorial-placements", help="export source NPC tutorial placement poses")
    tutorial.add_argument("--apk", action="append", required=True)
    tutorial.add_argument("--client-version", required=True)
    tutorial.add_argument("--out-dir", required=True)
    tutorial.add_argument("--processor-input", help="optional source-qualified canonical intro pose configuration")
    lay = sub.add_parser("ui-layout",
                         help="extract the RectTransform hierarchy of the UI "
                              "prefabs (screen layers, dialogs) the player data "
                              "builds, plus a census of the UI package families")
    lay.add_argument("--region", choices=("cn", "jp"), default="cn",
                     help="cn: one player data file whose resources.assets holds "
                          "every prefab; jp: the base APK and the asset pack, "
                          "whose Resources index points at one serialized file "
                          "per prefab")
    lay.add_argument("--player-data", default=None,
                     help="cn: the APK player data file; every screen and dialog "
                          "prefab lives in its resources.assets (Resources.Load "
                          "fetches them, so no bundle name routes to them)")
    lay.add_argument("--apk", action="append", default=None,
                     help="jp: an APK whose assets/bin/Data members are player "
                          "files; give the base APK and the asset pack")
    lay.add_argument("--client-version", default=None,
                     help="jp: the client version the manifest and every document "
                          "name (ui/jp_decoders.py names the supported ones; each "
                          "object's chain comes from its serialized type hash); cn: "
                          "the client version the palette and tween defaults name")
    lay.add_argument("--master", default=None,
                     help="jp: directory of master tables holding wordings.json")
    lay.add_argument("--prefab", action="append", default=None,
                     help="a prefab name to extract (repeatable); a targeted "
                          "run writes those documents and omits the broad census")
    lay.add_argument("--out-dir", required=True,
                     help="the output directory (ui-layout/) that receives one "
                          "document per prefab plus census.json")
    lay.add_argument("--bundles", default=None,
                     help="optional decrypted-package directory; named so the "
                          "census also reports what the per-screen download "
                          "packages contain (textures and effects, not layout)")
    lay.add_argument("--font-bundles", default=None,
                     help="jp: decrypted-package directory holding font/common "
                          "(jp download --root font/common); defaults to --bundles")
    lay.add_argument("--bundle-manifest", default=None,
                     help="optional AssetBundleInfoNew.json; supplies the "
                          "isBuiltin split and declared sizes for the census")
    words = sub.add_parser("ui-wordings",
                           help="write the runtime wording dictionary of a container build: "
                                "the built-in wording CSV of its language, then the master "
                                "wordings over it (the document a shared root reads as its "
                                "wordings.json)")
    words.add_argument("--player-data", required=True,
                       help="the APK player data file whose Resources index holds the wording CSV")
    words.add_argument("--master", required=True,
                       help="directory of master tables holding wordings.json")
    words.add_argument("--client-version", required=True,
                       help="the client version the document's source block names")
    words.add_argument("--out", required=True, help="the document path")
    reactions = sub.add_parser("fixture-reactions",
                               help="export the native furniture reaction-character groups")
    reactions.add_argument("--bundle", required=True,
                           help="decrypted mysekai__system__fixture_reaction_data bundle")
    reactions.add_argument("--out", required=True,
                           help="output fixture-reactions.json path")
    bem = sub.add_parser("birthday-end-motions",
                         help="export the waving motion the birthday party "
                              "plays as it closes (row per character unit)")
    bem.add_argument("--bundle", required=True,
                     help="decrypted mysekai__birthday__birthday_end_character_motion_data_list bundle")
    bem.add_argument("--out", required=True,
                     help="output birthday-end-character-motions.json path")
    bpt = sub.add_parser("birthday-party-talks",
                         help="attach the birthday-party scripts one master "
                              "table names to an existing talks.json")
    bpt.add_argument("--master", help="directory of caller-supplied master tables")
    bpt.add_argument("--master-url", nargs="?", const="", default=None,
                     help="base URL to append <table>.json to; no value uses the public default base")
    bpt.add_argument("--master-cache", help="where fetched tables are cached")
    bpt.add_argument("--bundle", required=True, help="talk scenario bundle")
    bpt.add_argument("--lib", help="decrypted lib package carrying the constant "
                                   "tables the scripts name")
    bpt.add_argument("--talks", required=True,
                     help="an existing talks.json whose birthdayParty block is "
                          "computed from this master partition")
    bpt.add_argument("--out", required=True)
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
    sls = sub.add_parser("sitemap-screen-layer",
                         help="write site/sitemap/screen_layer/screen_layer.json from APK "
                              "members (player data without resources.assets)")
    sls.add_argument("--apk", action="append", required=True,
                     help="an APK whose assets/bin/Data members are player files; "
                          "give the base APK and the asset pack")
    sls.add_argument("--client-version", required=True,
                     help="the client version the field chains are for")
    sls.add_argument("--out-dir", required=True,
                     help="runtime root; the document lands at "
                          "site/sitemap/screen_layer/screen_layer.json under it")
    mth = sub.add_parser("material-thumbnails",
                         help="write material-thumbnails.json and textures/material{id}.png "
                              "from the thumbnail/material package, one per materials row")
    mth.add_argument("--bundle", required=True, help="the decrypted thumbnail/material package")
    mth.add_argument("--master", required=True,
                     help="directory of master tables holding materials.json")
    mth.add_argument("--out-dir", required=True,
                     help="output directory (material-thumbnails/ of a runtime root)")
    hon = sub.add_parser("honor-images",
                         help="write honor/index.json and the textures of the honor bundles "
                              "the birthday-party delivery rewards' honors draw")
    hon.add_argument("--master", required=True,
                     help="directory of master tables (the delivery total rewards, resource boxes, honors, honor groups)")
    hon.add_argument("--bundles", required=True, help="directory of decrypted honor and honor_frame packages")
    hon.add_argument("--out-dir", required=True, help="runtime root; images land under honor/ and honor_frame/")
    ipv = sub.add_parser("item-icons",
                         help="write index.json and textures/<family>/<kind>/<resource>.png from "
                              "the mysekai item_preview and thumbnail material/item/tool packages, "
                              "keyed by the package path")
    ipv.add_argument("--decrypted", required=True,
                     help="directory holding the decrypted mysekai item icon packages")
    ipv.add_argument("--root", default=None,
                     help="runtime root whose mysekai-materials/items/fixtures/tools documents "
                          "give the coverage report")
    ipv.add_argument("--out-dir", required=True,
                     help="output directory (mysekai-item-icons/ of a runtime root)")
    a = sub.add_parser("fetch-apk", help="discover, download, or inspect an Android APK")
    a.add_argument("--endpoint", default=None); a.add_argument("--timeout", type=float, default=30.0); a.add_argument("--retries", type=int, default=3)
    asub = a.add_subparsers(dest="apk_command", required=True)
    asub.add_parser("latest")
    ad = asub.add_parser("download"); ad.add_argument("destination"); ad.add_argument("--url"); ad.add_argument("--sha256")
    ai = asub.add_parser("inspect"); ai.add_argument("apk")
    rp = sub.add_parser(
        "player-render-pipeline",
        help="extract the player-shipped MYSEKAI renderer pipeline and feature settings",
    )
    rp.add_argument(
        "--player-data", required=True,
        help="extracted assets/bin/Data root containing globalgamemanagers",
    )
    rp.add_argument(
        "--types", required=True,
        help="decoded managed assemblies for the same player build",
    )
    rp.add_argument(
        "--asset-pack", required=True,
        help="APK/zip containing serialized resource files referenced by ResourceManager",
    )
    rp.add_argument("--game-version", help="caller-known game version recorded as provenance")
    rp.add_argument("--out", required=True, help="output JSON evidence path")
    fal = sub.add_parser("fixture-actors", help="export all source-qualified character furniture animations")
    fal.add_argument("--assets", required=True); fal.add_argument("--master", required=True)
    fal.add_argument("--bundles", required=True); fal.add_argument("--workers", type=int, default=2)
    fal.add_argument("--plan", action="store_true", help="write and summarize the source dependency plan without exporting")
    fal.add_argument("--rotation-encoding", choices=ROTATION_ENCODINGS, default=DEFAULT_ROTATION_ENCODING,
                     help="rotation key storage (default: snorm16); float32 disables lossy encoding")
    fal.add_argument("--relation", action="append", choices=("fixture", "cut_scene"), default=None,
                     help="export only these relations' libraries (repeatable; default: all)")
    ptc = sub.add_parser("player-timeline-clips",
                         help="export each player fixture timeline's CharacterAnimator clips against the "
                              "player avatar rig, one clip file per timeline package")
    ptc.add_argument("--assets", required=True,
                     help="asset root holding fixture-timeline/ and the player timeline rows")
    ptc.add_argument("--bundles", required=True,
                     help="directory of decrypted bundles (the avatar model and the fixture models)")
    ptc.add_argument("--plan", action="store_true", help="summarize the clip plan without exporting")
    stl = sub.add_parser("site-timelines",
                         help="export a site package's timeline tables, its step items (each prefab its "
                              "own glb with its prop clips) and its scene directors' rigs")
    stl.add_argument("--assets", required=True, help="asset root receiving site-timeline/")
    stl.add_argument("--bundles", required=True,
                     help="directory of decrypted bundles (the site packages and their dependencies)")
    stl.add_argument("--package", action="append", required=True,
                     help="site package to export, e.g. mysekai__site__field__festivalgarden; repeatable")
    fc = sub.add_parser("fixture-coverage", help="join every fixture master ID to extracted coverage")
    fc.add_argument("--data-root", default=DEFAULT_DATA_ROOT); fc.add_argument("--out")
    fc.add_argument("--region"); fc.add_argument("--source-version"); fc.add_argument("--json", action="store_true")
    jp = sub.add_parser("jp", help="discover and download Japanese-server original bundles")
    jsub = jp.add_subparsers(dest="jp_command", required=True)
    jd = jsub.add_parser("discover", help="compare bootstrap metadata with the live official version list")
    jd.add_argument("--bootstrap-url", default=None); jd.add_argument("--timeout", type=float, default=30.0)
    jd.add_argument("--retries", type=int, default=3)
    jm = jsub.add_parser("manifest", help="create a versioned JP bundle descriptor")
    jm.add_argument("--app-version"); jm.add_argument("--app-hash")
    jm.add_argument("--asset-version"); jm.add_argument("--asset-hash")
    jm.add_argument("--versions-url", default=None)
    jm.add_argument("--guest", action="store_true", help="explicitly create a guest only when public asset identity is unavailable")
    jm.add_argument("--session", help="private guest-session JSON path; required with --guest")
    jm.add_argument("--out", required=True); jm.add_argument("--timeout", type=float, default=30.0)
    jm.add_argument("--retries", type=int, default=3)
    jm.add_argument("--json", action="store_true", help="print the full descriptor as well as writing --out")
    jpl = jsub.add_parser("plan", help="dependency-closed dry run from an online or offline descriptor")
    jpl.add_argument("--manifest", required=True); jpl.add_argument("--family", action="append", default=[])
    jpl.add_argument("--root", action="append", default=[], help="exact root bundle; repeatable")
    jpl.add_argument("--json", action="store_true", help="print root and bundle lists, not only counts")
    jrt = jsub.add_parser("prepare", help="assemble a region-qualified Moly runtime in a separate staging directory")
    jrt.add_argument("--manifest", required=True); jrt.add_argument("--workspace", required=True)
    jrt.add_argument("--out", required=True); jrt.add_argument("--ui-from", required=True)
    jrt.add_argument("--ui-jp-from", required=True,
                     help="JP UI extraction (ui-layout --region jp); its documents replace their donor copies")
    jrt.add_argument("--vgmstream"); jrt.add_argument("--ffmpeg")
    jrt.add_argument("--master-from", help="completed source-pinned master directory; defaults to Haruki when available")
    jrt.add_argument("--actor-workers", type=int, default=2)
    jrt.add_argument("--rotation-encoding", choices=ROTATION_ENCODINGS, default=DEFAULT_ROTATION_ENCODING,
                     help="motion rotation key storage (default: snorm16); float32 disables lossy encoding")
    jms = jsub.add_parser("master", help="download an immutable, bundle-matched JP master snapshot")
    jms.add_argument("--manifest", required=True); jms.add_argument("--out", required=True)
    jms.add_argument("--revision", default="main"); jms.add_argument("--workers", type=int, default=6)
    jms.add_argument("--provider", choices=("haruki", "sekai-world"), default="haruki")
    jms.add_argument("--all-tables", action="store_true", help="fetch all master tables, not only Moly dependencies")
    jdl = jsub.add_parser("download", help="download and verify original JP bundles without publishing an extracted release")
    jdl.add_argument("--manifest", required=True); jdl.add_argument("--out")
    jdl.add_argument("--workers", type=int, default=8); jdl.add_argument("--retries", type=int, default=4)
    jdl.add_argument("--root", action="append", default=[])
    jdl.add_argument("--family", action="append", default=[])
    jpu = jsub.add_parser("pull", help="download, decrypt and extract a JP descriptor")
    jpu.add_argument("--manifest", required=True); jpu.add_argument("--out")
    jpu.add_argument("--extract-out", default="local-data-jp")
    jpu.add_argument("--workers", type=int, default=4); jpu.add_argument("--retries", type=int, default=4)
    jpu.add_argument("--master"); jpu.add_argument("--master-cache")
    jpu.add_argument("--root", action="append", default=[], help="exact root bundle; repeatable")
    s = sub.add_parser("shader", help="what a package's shaders declare and contain")
    s.add_argument("package", help="one AssetBundle file")
    s.add_argument("--platform", type=int, default=None,
                   help="count only this ShaderCompilerPlatform (9 is GLSL text)")
    s.add_argument("--out", help="write the full census here as JSON")
    s.add_argument("--programs", metavar="DIR",
                   help="write each program body here, named by shader, platform and record")
    s.add_argument("--json", action="store_true", help="print the counts as JSON")

    args = ap.parse_args(argv); _configure_unity(args.unity_version)
    if args.cmd == "fixture-actors":
        from chara.fixture_actor_libraries import build_plan, export_fixture_actor_libraries
        if args.plan:
            report = build_plan(args.assets, args.master, args.bundles, rotation_encoding=args.rotation_encoding)
            print(json.dumps({"summary": report["summary"], "gaps": report["gaps"]}, ensure_ascii=False))
            return 1 if report["gaps"] else 0
        report = export_fixture_actor_libraries(args.assets, args.master, args.bundles,
            workers=args.workers, unity_version=args.unity_version or "2022.3.62f3",
            rotation_encoding=args.rotation_encoding, relations=args.relation,
            progress=lambda row: print(json.dumps(row, ensure_ascii=False), file=sys.stderr))
        print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False))
        return 1 if report["failed"] else 0
    if args.cmd == "player-timeline-clips":
        from chara.player_timeline_clips import build_plan, export_player_timeline_clips
        if args.plan:
            plan = build_plan(args.assets)
            print(json.dumps({"summary": plan["summary"], "gaps": plan["gaps"]}, ensure_ascii=False))
            return 1 if plan["gaps"] else 0
        report = export_player_timeline_clips(args.assets, args.bundles,
            progress=lambda row: print(json.dumps(row, ensure_ascii=False), file=sys.stderr))
        print(json.dumps({key: value for key, value in report.items() if key != "packages"},
                         ensure_ascii=False))
        return 1 if report["gaps"] else 0
    if args.cmd == "site-timelines":
        from sites.step_items import export_site_timelines
        report = export_site_timelines(args.bundles, args.assets, list(args.package))
        print(json.dumps(report, ensure_ascii=False))
        return 0
    if args.cmd == "fixture-coverage":
        from fixtures.coverage import write_coverage
        path, report = write_coverage(args.data_root, args.out, region=args.region,
                                      source_version=args.source_version)
        print(json.dumps(report if args.json else {**report["summary"], "report": str(path)}, ensure_ascii=False))
        return 1 if report["summary"]["runtime"]["failed"] else 0
    if args.cmd == "jp":
        from core import jp as jp_assets
        try:
            if args.jp_command == "discover":
                options = {"timeout": args.timeout, "retries": args.retries}
                if args.bootstrap_url: options["bootstrap_url"] = args.bootstrap_url
                report = jp_assets.discover(**options)
            elif args.jp_command == "manifest":
                report = jp_assets.fetch_manifest(args.out, app_version=args.app_version,
                                                  app_hash=args.app_hash, session_file=args.session,
                                                  asset_version=args.asset_version,
                                                  asset_hash=args.asset_hash, guest=args.guest,
                                                  **({"versions_url": args.versions_url}
                                                     if args.versions_url else {}),
                                                  timeout=args.timeout, retries=args.retries)
            elif args.jp_command == "plan":
                report = jp_assets.plan(args.manifest, families=args.family, include=args.root)
            elif args.jp_command == "prepare":
                from core.runtime_pack import prepare_runtime
                report = prepare_runtime(args.manifest, args.workspace, args.out, ui_from=args.ui_from,
                                         ui_jp_from=args.ui_jp_from,
                                         decoder=args.vgmstream, transcoder=args.ffmpeg,
                                         master_from=args.master_from, actor_workers=args.actor_workers,
                                         rotation_encoding=args.rotation_encoding,
                                         progress=lambda label: print(label, file=sys.stderr))
            elif args.jp_command == "master":
                from core.jp_master import download_master_snapshot
                report = download_master_snapshot(args.manifest, args.out, revision=args.revision, workers=args.workers, provider=args.provider, all_tables=args.all_tables)
                report = {"identity": report["identity"], "tableCount": report["tableCount"], "out": args.out}
            elif args.jp_command == "download":
                from core.jp_download import download_descriptor
                report = download_descriptor(args.manifest, out=args.out, workers=args.workers,
                                             retries=args.retries, include=args.root, families=args.family,
                                             progress=lambda row: print(f"{row['status']}: {row['completed']}/{row['requested']} verified, {row['failed']} failed", file=sys.stderr))
            else:
                report = jp_assets.pull_descriptor(args.manifest, out=args.out,
                                                   extract_out=args.extract_out, workers=args.workers,
                                                   retries=args.retries, master=args.master,
                                                   master_cache=args.master_cache,
                                                   include=args.root)
            if args.jp_command == "manifest" and not args.json:
                shown = {"source": report["source"], "bundles": len(report["bundles"]), "descriptor": args.out}
            elif args.jp_command == "plan" and not args.json:
                shown = {"source": report["source"], **report["summary"]}
            else:
                shown = report
            print(json.dumps(shown, ensure_ascii=False)); return 0
        except (jp_assets.JPError, OSError, ValueError, KeyError) as exc:
            print(f"error: {exc}", file=sys.stderr); return 1
    if args.cmd == "pull":
        from core.fetch import pull
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
            _print_extract_summary(report["extraction"], args.extract_out or DEFAULT_DATA_ROOT)
        return 1 if _extract_failed(report["extraction"]) else 0
    if args.cmd == "shader":
        import UnityPy
        from shaders import census as shader_census
        entries = shader_census.census(UnityPy.load(args.package),
                                       keep_code=bool(args.programs))
        counts = shader_census.totals(entries, args.platform)
        if args.out:
            from core.jsonio import write_json
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
        from core import apk
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
    if args.cmd == "player-render-pipeline":
        from core.player_render_pipeline import (
            extract_mysekai_render_pipeline,
            write_mysekai_render_pipeline,
        )
        try:
            document = extract_mysekai_render_pipeline(
                args.player_data,
                args.types,
                args.asset_pack,
                unity_version=args.unity_version,
                game_version=args.game_version,
            )
            write_mysekai_render_pipeline(args.out, document)
        except (OSError, ValueError, KeyError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        first = document["rendererSlots"][0]["objectKey"] if document["rendererSlots"] else None
        count = len(document["renderers"].get(first, {}).get("features", [])) if first else 0
        print(json.dumps({
            "unityVersion": document["unityVersion"],
            "gameVersion": document["gameVersion"],
            "rendererSlots": len(document["rendererSlots"]),
            "primaryRendererFeatures": count,
            "out": args.out,
        }, ensure_ascii=False))
        return 0
    if args.cmd == "extract":
        from core.extract import extract_manifest
        from core.assets.packages import builtin_archive_paths
        source, cache = _master_source(args)
        report = extract_manifest(args.manifest, args.bundles, args.out, args.unity_version,
                                  master=source, master_cache=cache,
                                  player_data=args.player_data,
                                  player_types=args.player_types,
                                  player_declarations=args.player_declarations,
                                  fixture_meshes=args.fixture_meshes,
                                  fixture_particles=args.fixture_particles,
                                  rotation_encoding=args.rotation_encoding,
                                  shader_compiler=args.shader_compiler,
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
        report = export_motion_library(args.reference_bundle, args.motion_bundle, args.out_dir, args.name,
                                       aux=tuple(args.aux_bundle), names=names,
                                       rotation_encoding=args.rotation_encoding,
                                       controller_bundles=args.controller_bundle)
        print(json.dumps(report, ensure_ascii=False)); return 0
    if args.cmd == "registry":
        source, cache = _master_source(args)
        if not source:
            ap.error("registry needs master tables: pass --master <dir> or --master-url")
        from core.assets.manifest import parse_manifest
        from core.assets.router import route
        from core.extract import _unit_id
        from core.master import Master, MissingTable
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

    if args.cmd == "official-text":
        from chara.text_catalog import match_official
        from core.jsonio import write_json
        with open(args.source, encoding="utf-8") as handle:
            source = json.load(handle)
        with open(args.donor, encoding="utf-8") as handle:
            donor = json.load(handle)
        result = match_official(source, donor, locale=args.locale)
        write_json(args.out, result)
        print(json.dumps({"entries": len(result["entries"]), "issues": len(result["issues"]),
                          "revision": result["revision"]}))
        return 0

    if args.cmd == "text-catalog":
        from chara.text_catalog import export_catalog
        print(json.dumps(export_catalog(args.corpus, args.out, region=args.region,
                                         source_revision=args.source_revision,
                                         previous=args.previous), ensure_ascii=False))
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

    if args.cmd == "browser-assets":
        from ui.character_portrait import export_character_portraits
        result = export_character_portraits(args.player_data, args.asset_root,
                                            source_region=args.source_region,
                                            source_game_version=args.source_game_version)
        print(json.dumps(result["coverage"], ensure_ascii=False))
        return 0

    if args.cmd == "fixture-presentation":
        from fixtures.presentation import extract_custom_geometry, enrich_presentation
        extracted = extract_custom_geometry(args.bundles, Path(args.asset_root) / "custom-fixture-models")
        if extracted.get("failed"):
            print(json.dumps({"customGeometry": extracted}, ensure_ascii=False))
            return 1
        print(json.dumps(enrich_presentation(args.master, args.asset_root), ensure_ascii=False))
        return 0

    if args.cmd == "validate-runtime":
        from core.runtime_validation import validate_runtime
        report = validate_runtime(args.asset_root, verify_actor_hashes=args.verify_actor_hashes)
        print(json.dumps(report, ensure_ascii=False))
        return 1 if report["missingResources"] else 0

    if args.cmd == "fixture-animations":
        from fixtures.animation_refresh import plan_animation_refresh, refresh_fixture_animations
        report = plan_animation_refresh(args.asset_root) if args.plan else refresh_fixture_animations(
            args.asset_root, args.bundles, packages=args.package,
            progress=lambda row: print(json.dumps(row), file=sys.stderr))
        print(json.dumps(report, ensure_ascii=False))
        return 0

    if args.cmd == "timeline-capabilities":
        from fixtures.timeline_capabilities import build_capabilities
        report = build_capabilities(args.asset_root)
        print(json.dumps({"prefabs": len(report["prefabs"]), "packages": len(report["sources"])}))
        return 0

    if args.cmd == "text-fonts":
        from ui.text_fonts import build_font_catalog
        result = build_font_catalog(args.base_font, args.font, args.license, args.out_dir)
        print(json.dumps({"revision": result['revision'], "fonts": len(result['fonts'])}))
        return 0

    if args.cmd == "tutorial-placements":
        from chara.tutorial import export_placements
        result = export_placements(args.apk, args.out_dir, client_version=args.client_version,
                                   processor_input=args.processor_input)
        print(json.dumps({"placements": len(result['placements']),
                          "participants": sum(len(rows) for rows in result['placements'].values())}))
        return 0

    if args.cmd == "ui-layout" and args.region == "jp":
        from ui.prefab_layout import extract_layout_jp
        if not (args.apk and args.client_version and args.master and args.prefab):
            ap.error("ui-layout --region jp needs --apk (base and asset pack), "
                     "--client-version, --master and at least one --prefab")
        result = extract_layout_jp(args.apk, args.out_dir, client_version=args.client_version,
                                   master_dir=args.master, prefabs=args.prefab,
                                   bundles_root=args.bundles,
                                   font_bundles_root=args.font_bundles)
        for item in result["written"]:
            summary = item["summary"]
            print(f"layout: {item['family']}/{item['prefab']} "
                  f"nodes={summary['nodes']} sprites={summary['spriteReferences']} "
                  f"texts={summary['textComponents']} "
                  f"partial={len(summary['partialComponents'])} "
                  f"unresolved={len(summary['unresolvedPointers'])}")
        for item in result["failures"]:
            print(f"layout FAILED {item['prefab']}: {item['reason']}")
        print(f"manifest: {result['manifestPath']}")
        return 0 if not result["failures"] else 1

    if args.cmd == "ui-layout":
        from ui.prefab_layout import extract_layout
        if not args.player_data:
            ap.error("ui-layout --region cn needs --player-data")
        result = extract_layout(args.player_data, args.out_dir,
                                bundles_root=args.bundles,
                                bundle_manifest=args.bundle_manifest,
                                prefabs=args.prefab,
                                client_version=args.client_version)
        for item in result["settings"]:
            print(f"settings: {item['document']} from {item['serializedFile']}:{item['pathId']}")
        if not result["settings"]:
            print("settings: palette.json and tween-settings.json not written "
                  "(they name the client version: pass --client-version)")
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

    if args.cmd == "ui-wordings":
        from ui.prefab_layout import extract_container_wordings
        result = extract_container_wordings(args.player_data, args.master, args.out, args.client_version)
        print(f"wordings: {result['rows']} entries ({result['builtinRows']} built-in, "
              f"{result['masterRows']} master rows, {result['masterOverwroteBuiltin']} built-in "
              f"overwritten, {result['masterValuesRewritten']} master values rewritten, "
              f"{result['builtinRejectedLines']} rejected lines) from "
              f"{result['serializedFile']}:{result['pathId']} -> {result['path']}")
        return 0

    if args.cmd == "fixture-reactions":
        from fixtures.reactions import extract_fixture_reactions
        print(json.dumps(extract_fixture_reactions(args.bundle, args.out), ensure_ascii=False))
        return 0

    if args.cmd == "birthday-end-motions":
        from sites.birthday_end_motions import extract_birthday_end_motions
        print(json.dumps(extract_birthday_end_motions(args.bundle, args.out),
                         ensure_ascii=False))
        return 0

    if args.cmd == "birthday-party-talks":
        source, cache = _master_source(args)
        if not source:
            ap.error("birthday-party-talks needs master tables: "
                     "pass --master <dir> or --master-url")
        from chara.talks import attach_birthday_talks
        print(json.dumps(attach_birthday_talks(source, args.bundle, args.talks,
                                               args.out, master_cache=cache,
                                               lib_bundle=args.lib),
                         ensure_ascii=False))
        return 0

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

    if args.cmd == "birthday-party-delivery":
        source, cache = _master_source(args)
        if not source:
            ap.error("birthday-party-delivery needs master tables: "
                     "pass --master <dir> or --master-url")
        from sites.birthday_party_delivery import extract_birthday_party_delivery
        print(json.dumps(extract_birthday_party_delivery(source, args.out, master_cache=cache),
                         ensure_ascii=False))
        return 0

    if args.cmd == "gate-visit-policy":
        source, cache = _master_source(args)
        if not source:
            ap.error("gate-visit-policy needs master tables: "
                     "pass --master <dir> or --master-url")
        from sites.gate_visit_policy import extract_gate_visit_policy
        print(json.dumps(extract_gate_visit_policy(source, args.out, master_cache=cache),
                         ensure_ascii=False))
        return 0
    if args.cmd == "time-manager":
        from core.time_manager import extract_time_manager
        document = extract_time_manager(args.source, args.out)
        print(json.dumps({"source": document["source"], "timeManager": document["timeManager"]},
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
    if args.cmd == "player-tool-models":
        from chara.harvest_tools import extract_player_tool_models
        result = extract_player_tool_models(args.bundle, args.out_dir,
                                            bundle_root=args.bundle_root)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if any(row["status"] == "failed" for row in result["perBundle"].values()) else 0
    if args.cmd == "effect-resources":
        from core.resource_exports import export_effect_resources
        print(json.dumps(export_effect_resources(
            args.player_data, args.types, args.out, declarations=args.declarations,
            bundle_root=args.bundle_root), ensure_ascii=False))
        return 0
    if args.cmd == "layout-grid":
        from core.layout_grid import export_layout_grid
        print(json.dumps(export_layout_grid(args.player_data, args.types, args.out_dir),
                         ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "drop-item-shade":
        from core.resource_exports import export_drop_item_shade
        print(json.dumps(export_drop_item_shade(args.player_data, args.types, args.out),
                         ensure_ascii=False))
        return 0
    if args.cmd == "stencil-shadow":
        from core.resource_exports import export_stencil_shadow
        print(json.dumps(export_stencil_shadow(args.player_data, args.types, args.out),
                         ensure_ascii=False))
        return 0
    if args.cmd == "resource-prefab":
        from core.resource_exports import export_resource_prefab
        from core.assets.packages import builtin_archive_paths
        document = export_resource_prefab(args.player_data, args.types,
                                          args.load_name, args.out_dir,
                                          builtin_archive_paths(args.builtin_resources))
        print(json.dumps({"file": document["file"], "objects": document["objects"],
                          "roots": [root["name"] for root in document["roots"]],
                          "particles": len(document["particles"]),
                          "unsupported": len(document["unsupported"])},
                         ensure_ascii=False))
        return 0
    if args.cmd == "resource-programs":
        from core.resource_exports import export_resource_programs
        document = export_resource_programs(args.player_data, args.types, args.load_name,
                                            args.out_dir, args.shader_compiler)
        print(json.dumps({"file": document["file"],
                          "shaders": [(row["name"], row["state"], len(row["materials"]))
                                      for row in document["shaders"]]}, ensure_ascii=False))
        return 0
    if args.cmd == "fixture-particles":
        from core.assets.packages import PackageStore, builtin_archive_paths, require_paths
        from fixtures.particles import DOMAINS, extract_from_store
        require_paths(args.bundle)
        builtins = builtin_archive_paths(args.builtin_resources)
        require_paths(builtins, "built-in resource")
        store = PackageStore(list(args.bundle) + builtins, args.bundle_root)
        result = extract_from_store(store, args.out_dir,
                                    shader_compiler=args.shader_compiler,
                                    domains=tuple(args.domain or DOMAINS))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.cmd == "prefab-package":
        from sites.prefab_package import extract_prefab_package
        print(json.dumps(extract_prefab_package(args.bundle, args.prefab, args.out_dir,
                                                bundle_root=args.bundle_root),
                         ensure_ascii=False))
        return 0
    if args.cmd == "site-edit-pick":
        from sites.edit_pick import extract_site_edit_pick
        print(json.dumps(extract_site_edit_pick(args.bundle, args.apk, args.out),
                         ensure_ascii=False))
        return 0
    if args.cmd == "site-sound-wave":
        from sites.sound_wave import extract_site_sound_wave
        result = extract_site_sound_wave(args.bundle, args.out, bundle_root=args.bundle_root)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["failed"] else 0
    if args.cmd == "site-common-action-effects":
        from sites.action_effects import extract_site_common_action_effects
        result = extract_site_common_action_effects(args.bundle, args.out_dir,
                                                    bundle_root=args.bundle_root)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if any(row["status"] == "failed" for row in result["perBundle"].values()) else 0
    if args.cmd == "site-harvest-object-effects":
        from sites.action_effects import extract_site_harvest_object_effects
        result = extract_site_harvest_object_effects(args.bundle, args.out_dir,
                                                     bundle_root=args.bundle_root)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if any(row["status"] == "failed" for row in result["perBundle"].values()) else 0
    if args.cmd == "site-action-effects":
        from sites.action_effects import extract_site_action_effects
        result = extract_site_action_effects(args.bundle, args.out_dir,
                                             bundle_root=args.bundle_root)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if any(row["status"] == "failed" for row in result["perBundle"].values()) else 0
    if args.cmd == "site-home-effects":
        from sites.action_effects import extract_site_home_effects
        result = extract_site_home_effects(args.bundle, args.out_dir,
                                           bundle_root=args.bundle_root)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if any(row["status"] == "failed" for row in result["perBundle"].values()) else 0
    if args.cmd == "fixture-put-effects":
        from sites.action_effects import extract_fixture_put_effects
        result = extract_fixture_put_effects(args.bundle, args.out_dir,
                                             bundle_root=args.bundle_root)
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
        from core.assets.packages import builtin_archive_paths, require_paths
        from phenomena.environments import extract_phenomena
        source, cache = _master_source(args)
        # --bundle takes paths, not bare package names: PackageStore keys the
        # store by basename and UnityPy.load answers a path that is not there
        # with an empty environment and no error, so a bare name runs green
        # over zero objects.  Refuse it here, before any work is done.
        require_paths(args.bundle)
        require_paths(args.site_bundle)
        report = extract_phenomena(args.bundle, args.out_dir,
                                   bundle_root=args.bundle_root, master=source,
                                   master_cache=cache, vgmstream=args.vgmstream,
                                   ffmpeg=args.ffmpeg,
                                   shader_compiler=args.shader_compiler,
                                   extra_archives=builtin_archive_paths(
                                       args.builtin_resources),
                                   site_bundles=args.site_bundle)
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
    if args.cmd == "cutscene-audio":
        from phenomena.cutscene_audio import extract_cutscene_audio
        report = extract_cutscene_audio(args.clips, args.cut_scenes, args.manifest,
                                        args.bundle_root, args.out_dir,
                                        decoder=args.vgmstream, transcoder=args.ffmpeg)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    if args.cmd == "audio-sequences":
        if Path(args.out).resolve() == Path(args.loop).resolve():
            ap.error("audio-sequences writes a copy: --out must differ from --loop")
        from core.unity import configure_fallback_unity_version
        configure_fallback_unity_version(getattr(args, "unity_version", None))
        from phenomena.audio_sequences import annotate_loop
        report = annotate_loop(args.loop, args.bundle_root, args.out)
        print(json.dumps({"counts": report["counts"],
                          "structuredByType": report["structuredByType"],
                          "archiveCuesByReferenceType": report["archiveCuesByReferenceType"],
                          "archiveSequencesByType": report["archiveSequencesByType"],
                          "answeredByTree": {k: len(v) for k, v
                                             in report["answeredByTree"].items()},
                          "failures": len(report["failures"]),
                          "disagreements": len(report["disagreements"]),
                          "sampleDifferences": len(report["sampleDifferences"])},
                         ensure_ascii=False))
        return 0
    if args.cmd == "record-audio":
        source, cache = _master_source(args)
        if not source:
            ap.error("record-audio needs master tables: "
                     "pass --master <dir> or --master-url")
        from core.unity import configure_fallback_unity_version
        configure_fallback_unity_version(getattr(args, "unity_version", None))
        from phenomena.record_audio import extract_record_audio
        document = extract_record_audio(source, args.bundle_root, args.out,
                                        bundle_version=args.bundle_version,
                                        decoder=args.vgmstream, master_cache=cache,
                                        only=args.only)
        print(json.dumps(document["summary"], ensure_ascii=False))
        return 0
    if args.cmd == "record-audio-storage":
        from phenomena.record_audio import attach_storage
        document = attach_storage(args.input, args.out, server=args.server,
                                  workers=args.workers)
        print(json.dumps(document["summary"], ensure_ascii=False))
        return 0
    if args.cmd == "record-audio-carry":
        source, cache = _master_source(args)
        if not source:
            ap.error("record-audio-carry needs the target master tables: "
                     "pass --master <dir> or --master-url")
        from phenomena.record_audio import carry_record_audio
        document = carry_record_audio(source, args.manifest, args.source,
                                      args.source_manifest, args.out,
                                      region=args.region, master_cache=cache)
        print(json.dumps(document["summary"], ensure_ascii=False))
        return 0
    if args.cmd == "record-audio-fetch":
        from core.unity import configure_fallback_unity_version
        configure_fallback_unity_version(getattr(args, "unity_version", None))
        from phenomena.record_audio import fetch_record_bundles
        receipt = fetch_record_bundles(args.manifest, args.document, args.asset_base_url,
                                       args.out, workers=args.workers)
        print(json.dumps(receipt["summary"], ensure_ascii=False))
        return 0
    if args.cmd == "record-audio-merge":
        from phenomena.record_audio import merge_record_audio
        print(json.dumps(merge_record_audio(args.inputs, args.out)["summary"],
                         ensure_ascii=False))
        return 0
    if args.cmd == "audio-categories":
        from phenomena.audio_categories import write_category_table
        document = write_category_table(args.configuration, args.out)
        print(json.dumps({"source": document["source"],
                          "categories": [(row["name"], row["index"], row["volume"])
                                         for row in document["categories"]]},
                         ensure_ascii=False))
        return 0
    if args.cmd == "partvoice-routes":
        source, cache = _master_source(args)
        if not source:
            ap.error("partvoice-routes needs master tables: "
                     "pass --master <dir> or --master-url")
        from core.master import Master
        from phenomena.partvoice import (build_partvoice_routes,
                                         route_counts, write_partvoice_routes)
        roster = json.loads(Path(args.roster).read_text(encoding="utf-8"))
        document = build_partvoice_routes(Master(source, cache_dir=cache),
                                          roster)
        path = write_partvoice_routes(document, args.out_dir)
        print(json.dumps({**route_counts(document), "file": str(path)},
                         ensure_ascii=False))
        return 0
    if args.cmd == "material-thumbnails":
        from ui.material_thumbnail import export_material_thumbnails
        from core.extract import DEFAULT_UNITY_VERSION
        result = export_material_thumbnails(
            args.bundle, Path(args.master) / "materials.json", args.out_dir,
            unity_version=args.unity_version or DEFAULT_UNITY_VERSION)
        print(json.dumps(result["coverage"], ensure_ascii=False))
        return 0 if not result["coverage"]["missingMaterialIds"] else 1
    if args.cmd == "honor-images":
        from ui.honor_images import export_honor_images
        from core.extract import DEFAULT_UNITY_VERSION
        result = export_honor_images(args.master, args.bundles, args.out_dir,
                                     unity_version=args.unity_version or DEFAULT_UNITY_VERSION)
        print(json.dumps({"counts": result["counts"], "bundles": len(result["bundles"]),
                          "missingBundles": result["missingBundles"]}, ensure_ascii=False))
        return 0 if not result["missingBundles"] else 1
    if args.cmd == "item-icons":
        from ui.item_icon import export_item_icons, package_paths
        packages = package_paths(args.decrypted)
        masters = {}
        if args.root:
            for key, name in (("materials", "mysekai-materials.json"),
                              ("items", "mysekai-items.json"),
                              ("fixtures", "mysekai-fixtures.json"),
                              ("tools", "mysekai-tools.json")):
                path = Path(args.root) / name
                masters[key] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        result = export_item_icons(packages, args.out_dir, **masters)
        print(json.dumps(result["coverage"], ensure_ascii=False))
        return 0
    if args.cmd == "sitemap-screen-layer":
        from sites.screen_layer import extract_screen_layer_jp
        result = extract_screen_layer_jp(
            args.apk, Path(args.out_dir) / "site" / "sitemap" / "screen_layer",
            client_version=args.client_version)
        print(json.dumps({"counts": result["counts"], "path": result["path"]},
                         ensure_ascii=False))
        return 0
    if args.cmd == "site":
        from core.assets.packages import require_paths
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
