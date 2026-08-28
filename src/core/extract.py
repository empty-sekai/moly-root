"""Manifest-driven extraction orchestration."""
import json
import os
from pathlib import Path

from .assets.manifest import parse_manifest
from .assets.router import route

# Furniture geometry is the one pass whose output is measured in gigabytes: one
# ``.glb`` per package over the whole ``mysekai__fixture__`` family.  Making it
# unconditional would put that cost on every extraction, so it is opt-in -- and
# because "not asked for" and "does not exist" must stay distinguishable in the
# report, the artifact is then listed as ``skipped`` carrying this reason rather
# than left out (the same rule the player-data UI artifact keeps below).
FIXTURE_MESHES_SKIPPED = ("furniture geometry was not requested; it is one glb "
                          "per package over the whole fixture family, so the "
                          "pass is opt-in via fixture_meshes=True")

# The particle-emitter pass is second-order (it reads the ParticleSystem and
# ParticleSystemRenderer components of packages whose attach points are read
# for anything else) and its documents and texture copies are large in number
# even if small in bytes, so it is opt-in for the same reason the geometry
# pass is: off by default, its entry is reported ``skipped`` with this reason
# rather than left out.
FIXTURE_PARTICLES_SKIPPED = ("furniture particle emitters were not requested; "
                             "the pass is second-order over the fixture-"
                             "interface, cutscene-timeline and fixture-"
                             "timeline families, so it is opt-in via "
                             "fixture_particles=True")

# The version a bundle whose header carries none is read against.  Several
# extractor modules assign this at import, which is enough when the caller comes
# through the command line and nothing else, but the jobs that run before any of
# them is imported would then read with nothing set at all.
DEFAULT_UNITY_VERSION = "2022.3.62f3"


# Object types that are containers or metadata rather than content of their own,
# with the reason each is one.  A type is only allowed in here with a statement
# of which other rows carry what it holds -- "structural" is not a bucket for
# things nobody wanted to classify, and a type put here without that statement is
# the same silence this module exists to prevent.
STRUCTURAL_TYPES = {
    "GameObject": "a node's identity; its content is the components on it",
    "Transform": "a node's placement; exported as the node's TRS",
    "RectTransform": "a Transform for UI nodes; same",
    "MonoScript": "names the class a MonoBehaviour is; carries no instance data",
    "AssetBundle": "the package's own container object, one per package; it "
                   "lists contents rather than being content",
    "CanvasRenderer": "a draw record the Canvas owns; carries no authored data",
}

# What each domain's extractor declares it reads.  This is the promise the
# coverage criterion holds against the source census: a type present in the
# source but in no declaration is a type nobody is even claiming to read.
#
# Declaring is what makes the gap visible.  The failure this catches leaves no
# empty field behind to notice -- a whole object type can be absent from every
# product while every artifact reports success, because no extractor ever said
# it wanted them.
DOMAIN_OBJECT_TYPES = {
    "character": {"Mesh", "SkinnedMeshRenderer", "MeshFilter", "MeshRenderer",
                  "Material", "Texture2D", "Shader", "Avatar", "Animator",
                  "AnimatorController"},
    "motion": {"AnimationClip", "Avatar"},
    "facial": {"MonoBehaviour"},
    "performance": {"TextAsset"},
    "talk": {"TextAsset"},
    "talk-constants": {"TextAsset"},
    "camera": {"MonoBehaviour", "PlayableDirector"},
    "fixture-interface": {"MonoBehaviour", "Mesh", "MeshFilter", "MeshRenderer",
                          "SkinnedMeshRenderer", "Material", "Texture2D"},
    "cutscene-timeline": {"MonoBehaviour", "PlayableDirector", "AnimationClip"},
    "fixture-timeline": {"MonoBehaviour", "PlayableDirector", "AnimationClip"},
}


# Not every reader is a domain.  The animation export is a derived pass: what it
# reads is decided by the clip-target documents the timeline jobs write, never by
# a package name, so no domain declares its types and a domain-only model reports
# them as read by nobody.  Measured: 1781 AnimationClips sit in the furniture
# packages and this pass exports 1384 of them as glTF animations, the remainder
# being humanoid Animator curves it keeps verbatim instead -- so calling them
# unclaimed was this model's own false positive, not a gap.
#
# A pass declares the domains and types it covers explicitly.  No wildcard: a
# pass that quietly widened its claim would hide the next real gap.
DERIVED_PASS_COVERAGE = {
    "perf-animations/": {
        "fixture-interface": {"AnimationClip"},
        "fixture-timeline": {"AnimationClip"},
        "cutscene-timeline": {"AnimationClip"},
    },
}


def claimed_types(domain, declarations=None, derived=None):
    """Every type something declares it reads for *domain*, domains and passes."""
    declarations = DOMAIN_OBJECT_TYPES if declarations is None else declarations
    derived = DERIVED_PASS_COVERAGE if derived is None else derived
    claimed = set(declarations.get(domain) or ())
    for coverage in derived.values():
        claimed |= set(coverage.get(domain) or ())
    return claimed


def input_gaps(report):
    """Every artifact in *report* whose input was not supplied, with its reason.

    An artifact that reports ``skipped`` because nobody handed it its input, or
    that names missing dependencies, is a gap: the capability ran and wrote
    nothing.  Three of these have shipped as silently empty products -- a UI
    document with no player data, an icon set with no master tables, a family
    that raised on every package -- and each looked like a clean run.
    """
    gaps = []
    for key in ("derived", "playerData"):
        for entry in report.get(key) or []:
            reasons = []
            if entry.get("status") in ("skipped", "failed"):
                reasons.append(entry.get("status"))
            if entry.get("missingDependencies"):
                reasons.append("missingDependencies")
            if entry.get("missing"):
                reasons.append("missing")
            if reasons:
                gaps.append({"artifact": entry.get("artifact", "?"),
                             "why": ",".join(reasons),
                             "detail": entry.get("error", "")})
    return gaps


def check_input_gaps(report, accepted=()):
    """Gate: a gap the caller has not named is red.

    *accepted* is the list of artifact names the caller states it knows about
    and accepts.  Accepting is deliberately per-artifact and explicit: a blanket
    "ignore gaps" switch would put the run back to reporting success while
    writing nothing.  Returns ``(ok, gaps, unaccepted)``.
    """
    gaps = input_gaps(report)
    accepted = set(accepted or ())
    unaccepted = [gap for gap in gaps if gap["artifact"] not in accepted]
    return not unaccepted, gaps, unaccepted


def check_type_coverage(census, declarations=None, structural=None,
                        adjudicated=(), derived=None):
    """Gate: an object type in the source that no domain claims to read is red.

    *census* is ``{domain: {type: count}}`` counted from the **source packages**,
    never from the products -- a census of our own output cannot show what our
    own output dropped.  *adjudicated* names ``"domain:Type"`` pairs that have
    been ruled on, so a known gap is carried as a named decision rather than
    disappearing.

    This is the half that has no empty field to give it away.  The other gate
    reads a gap out of an artifact that at least reported something; here the
    extractor never said it wanted the type, so nothing anywhere is empty --
    2207 particle systems can be absent from every product while every artifact
    reports success.

    Returns ``(ok, uncovered)`` where *uncovered* lists
    ``{domain, type, count}`` for each type nobody reads and nobody has ruled on.
    """
    declarations = DOMAIN_OBJECT_TYPES if declarations is None else declarations
    structural = STRUCTURAL_TYPES if structural is None else structural
    derived = DERIVED_PASS_COVERAGE if derived is None else derived
    ruled = set(adjudicated or ())
    uncovered = []
    for domain in sorted(census):
        claimed = claimed_types(domain, declarations, derived)
        for type_name, count in sorted((census.get(domain) or {}).items()):
            if type_name in structural or type_name in claimed:
                continue
            if f"{domain}:{type_name}" in ruled:
                continue
            uncovered.append({"domain": domain, "type": type_name,
                              "count": count})
    return not uncovered, uncovered


def _configure_unity_version(unity_version):
    """Set the fallback Unity version for this run.

    An explicit *unity_version* always wins.  With None, whatever the caller
    already configured is kept -- the command line sets it before calling -- and
    only a wholly unconfigured process gets :data:`DEFAULT_UNITY_VERSION`, so a
    direct call does not depend on which extractor module was imported first.
    """
    import UnityPy.config
    if unity_version:
        UnityPy.config.FALLBACK_UNITY_VERSION = unity_version
    elif not getattr(UnityPy.config, "FALLBACK_UNITY_VERSION", None):
        UnityPy.config.FALLBACK_UNITY_VERSION = DEFAULT_UNITY_VERSION
    return UnityPy.config.FALLBACK_UNITY_VERSION

# Every output path this module's jobs write that does *not* vary with a package
# or unit name, relative to the output directory, with a trailing ``/`` on the
# ones that are directories the jobs fill with per-package files.  The name says
# "fixed" because that is all it covers: ``sd_<unit>.glb``, ``sd_<unit>.rig.json``
# and the ``<package>.json`` documents inside the listed directories are named
# after their subject and are not enumerated here.
#
# It exists so a consumer's declared read paths can be checked against what the
# pipeline actually writes.  A demo that reads ``fixture-attach/attach-points.json``
# while the pipeline writes ``fixture-interface/attach-points.json`` is broken in
# a way no amount of green extraction reveals: both sides pass their own tests
# and the consumer still shows nothing.
FIXED_ARTIFACT_PATHS = (
    "extraction-report.json",
    "manifest.json",
    "characters.json",
    "motion-library.glb",
    "motion-library.index.json",
    "facial-tables.json",
    "alone-actions.json",
    "talks.json",
    "emoticons/emoticons.json",
    "phenomena/index.json",
    "site/index.json",
    "fixture-interface/attach-points.json",
    "fixture-interface/areas.json",
    "fixture-models/",
    "fixture-particles/index.json",
    "fixture-talks/talks.json",
    "cutscene-timeline/tracks/",
    "cutscene-timeline/clips/",
    "cutscene-timeline/clip-targets/",
    "fixture-timeline/tracks/",
    "fixture-timeline/clips/",
    "fixture-timeline/clip-targets/",
    "camera/",
    "perf-animations/",
    "ui/talk.json",
)


def _bundle_path(bundles, logical_name):
    root = Path(bundles)
    candidates = [root / logical_name, root / logical_name.replace("__", "/")]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"bundle not found: {logical_name}")


def _optional_pass_status(asked, skipped_reason, error, counts):
    """One caller-gated pass's status: ``skipped``/``failed``/``succeeded``.

    A pass the caller did not ask for is reported ``skipped`` with the reason
    (never omitted, so a reader can tell "nobody asked" from "this artifact
    does not exist").  Asked for and raising is ``failed`` with the exception.
    Asked for, raising nothing, but producing an empty *counts* is *also*
    ``failed`` -- that shape is what a pass whose call was silently skipped
    (or removed) looks like from the report alone, so it must not read as a
    success just because nothing raised.
    """
    if not asked:
        return "skipped", skipped_reason
    if error:
        return "failed", error
    if not counts:
        return "failed", "pass produced no counts"
    return "succeeded", ""


def _unit_id(bundle_name):
    """Character unit id carried by a model bundle name, or ``None``.

    The trailing model number encodes the unit with a leading digit that is not
    part of it, exactly as the game's own decoder reads it back.
    """
    stem = bundle_name.replace("__", "/").rsplit("/", 1)[-1]
    parts = stem.split("_")
    if len(parts) < 3 or not parts[2].startswith("1"):
        return None
    try:
        return int(parts[2][1:])
    except ValueError:
        return None


def _registry_artifact(names, out, master, master_cache=None):
    """Build ``characters.json`` when master tables are supplied."""
    entry = {"artifact": "characters.json", "status": "skipped", "counts": {},
             "error": "no master directory supplied; identity and locomotion are "
                      "only in caller-supplied master tables"}
    if not master:
        return entry
    units = sorted({u for n in names
                    if route(n) and route(n).domain == "character"
                    for u in [_unit_id(n)] if u is not None})
    try:
        from .master import Master, MissingTable
        from chara.registry import build_registry
        index_path = out / "motion-library.index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else None
        source = Master(master, cache_dir=master_cache)
        try:
            client_configs = source.client_configs()
        except MissingTable:
            client_configs = None
        document = build_registry(source, units, index, client_configs=client_configs)
        path = out / "characters.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False) + "\n",
                        encoding="utf-8", newline="\n")
        entry.update(status="succeeded", error="",
                     counts={k: v for k, v in document["summary"].items()
                             if isinstance(v, (int, bool))})
        entry["path"] = str(path)
    except Exception as exc:
        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return entry


# The dialogue window and the head-up display are the two things this UI lane is
# about, so these three behaviour classes are its roots.  Every other hand
# decoder in ``ui.talk`` is a *part* those trees are built from -- an image, a
# button, a layout group, a tween -- and those parts are used all over the game:
# decoding every one of them in the player data would put 104k objects behind a
# name that promises the talk UI.  So the roots are named, and the artifact holds
# exactly the trees hanging off them.
UI_WINDOW_ROOT_CLASSES = ("Sekai.TalkWindow",
                          "Sekai.Mysekai.MysekaiChatBalloon",
                          "Sekai.Mysekai.TweetHeadUpDisplay")


def _ui_artifact(out, player_data):
    """Build ``ui/talk.json`` when the caller supplies the player-data file.

    The dialogue UI is not in any downloadable package: it is built into the
    APK's player data, so no bundle name can name it and no route can claim it.
    The path therefore has to come from the caller, and without it the artifact
    is reported as ``skipped`` *with the reason* rather than being left out of
    the report -- an omitted entry would read as "this domain does not exist"
    instead of "nobody handed it its input", which is exactly the distinction
    the master-backed registry artifact above keeps.

    What is read is the window tree under each root behaviour: every component
    of every node, hand-decoded, each carrying its own leftover-bytes check.  A
    component whose class has no hand decoder is kept with the reader's own
    "partial" note and counted separately, never dropped.
    """
    entry = {"artifact": "ui/talk.json", "domain": "ui", "status": "skipped",
             "counts": {},
             "error": "no player-data file supplied; the dialogue UI is built "
                      "into the APK player data, which is not a downloadable "
                      "package and cannot be routed by bundle name"}
    if not player_data:
        return entry
    try:
        import ui.talk as talk
        from .jsonio import write_json
        if not Path(player_data).exists():
            raise FileNotFoundError(f"player data not found: {player_data}")
        env = talk.load_env(str(player_data))
        mono_index = talk.build_monoscript_index(env)
        roots = []
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            cls = talk.resolve_script_class(obj, mono_index)
            if cls not in UI_WINDOW_ROOT_CLASSES:
                continue
            _, game_object = talk.Reader(obj.get_raw_data()).pptr()
            roots.append((obj.assets_file.name, obj.path_id, cls, game_object))
        roots.sort()
        by_file = {}
        windows = []
        nodes_total = components_total = residual_zero = partial = 0
        classes = set()
        for file_name, path_id, cls, game_object in roots:
            objects = by_file.get(file_name)
            if objects is None:
                objects = by_file[file_name] = talk.objects_by_file(env, file_name)
            tree = talk.collect_tree(env, game_object, mono_index, file=file_name)
            nodes = []
            for node in tree:
                decoded = []
                for component in node["components"]:
                    target = objects.get(component["path_id"])
                    if target is None:
                        continue
                    record = talk.decode_object(env, target, mono_index,
                                                allow_partial=True)
                    record["pathId"] = component["path_id"]
                    decoded.append(record)
                    components_total += 1
                    classes.add(record["class"])
                    if record["residual"] == 0:
                        residual_zero += 1
                    if record.get("note"):
                        partial += 1
                nodes.append({"rectPathId": node["rect_pid"],
                              "goPathId": node["go_pid"],
                              "depth": node["depth"],
                              "parentGo": node["parent_go"],
                              "components": decoded})
            nodes_total += len(nodes)
            windows.append({"root": {"class": cls, "file": file_name,
                                     "pathId": path_id,
                                     "goPathId": game_object},
                            "nodes": nodes})
        counts = {"windows": len(windows), "nodes": nodes_total,
                  "components": components_total, "classes": len(classes),
                  "residualZero": residual_zero, "partial": partial}
        path = write_json(out / "ui" / "talk.json",
                          {"version": 1,
                           "rootClasses": list(UI_WINDOW_ROOT_CLASSES),
                           "windows": windows, "summary": counts})
        entry.update(status="succeeded", error="", counts=counts, path=str(path))
    except Exception as exc:
        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return entry


def _entry(name):
    return {"bundle": name, "status": "failed", "artifacts": [], "counts": {}, "error": ""}


# A sound package is read by the phenomena job, which asks only for the cues and
# packages master rows name.  So one can be present and still be asked for by
# nobody, which is a different answer from "unreadable" and must not look like one.
NO_SOUND_ROW = ("no master row names this sound package, so no cue was asked for "
                "from it; music and ambience rows are only in caller-supplied "
                "master tables and are read by the phenomena job")


def discover_bundles(bundles):
    """Select every bundle under *bundles* that belongs to a supported asset pack.

    Relative paths are normalized to logical double-underscore names; a file is
    kept when the router recognizes its domain, plus the shader lookup source.
    Character models sort first so the motion library always finds its
    reference skeleton. Returns ``(names, ignored_count)``.
    """
    root = Path(bundles)
    names = []
    ignored = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        logical = "__".join(path.relative_to(root).parts)
        if route(logical) is None and logical != "mysekai__shader":
            ignored += 1
            continue
        names.append(logical)
    names.sort(key=lambda n: (0 if (route(n) and route(n).domain == "character") else 1, n))
    return names, ignored


def _run_timeline_job(paths, out_dir, bundle_root=None):
    """One timeline family as one job: track trees, clips and clip targets.

    Both timeline families (the story cut-scene player and the furniture
    fixture-timeline views) need the same three reads, and each read must see
    the whole family together, because a package's pointers reach other packages
    in the same family and a per-package run would resolve neither.  The track
    read is the one that reports per package by name; the other two return their
    records in the same sorted-package order, which the caller's sorted name list
    recovers, so all three fold into one per-bundle map.
    """
    from perf.tracks import read_track_trees
    from perf.clips import read_timeline_clips
    from perf.clip_targets import read_clip_targets
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Each reader writes one JSON per package named after the package, so the
    # three reads get three subdirectories rather than overwriting each other's
    # ``<package>.json``.
    track = read_track_trees(paths, str(out / "tracks"), bundle_root=bundle_root)
    clips = read_timeline_clips(paths, str(out / "clips"), bundle_root=bundle_root)
    targets = read_clip_targets(paths, str(out / "clip-targets"), bundle_root=bundle_root)
    names = sorted(os.path.basename(str(p)) for p in paths)
    per_bundle = {}
    for record in track["packages"]:
        per_bundle[record.get("package")] = dict(record)
    for report in (clips, targets):
        for i, record in enumerate(report["packages"]):
            if not isinstance(record, dict):
                continue
            name = record.get("package")
            if name is None and i < len(names):
                name = names[i]
            base = per_bundle.get(name)
            if base is None:
                per_bundle[name] = dict(record)
            else:
                base.update(record)
    return {"perBundle": per_bundle, "tracks": track,
            "clips": clips, "targets": targets}


def write_pack_manifest(out):
    """Rebuild the viewer-facing ``manifest.json`` from the artifacts on disk.

    The unit list mirrors exactly the ``sd_<unit>.glb`` files present, so
    repeated or partial runs stay truthful without merging any state.  Returns
    the manifest path, or ``None`` when no character artifact exists.
    """
    out = Path(out)
    units = []
    for glb in sorted(out.glob("sd_*.glb")):
        digits = glb.stem.split("_", 1)[1]
        if not digits.isdigit():
            continue
        entry = {"unit": digits, "glb": glb.name}
        rig = out / f"{glb.stem}.rig.json"
        if rig.exists():
            entry["rig"] = rig.name
        units.append(entry)
    if not units:
        return None
    units.sort(key=lambda u: int(u["unit"]))
    path = out / "manifest.json"
    path.write_text(json.dumps({"version": 1, "units": units}, ensure_ascii=False, indent=1, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def extract_manifest(manifest, bundles, out, unity_version=None, master=None,
                     player_data=None,
                     fixture_meshes=False,
                     fixture_particles=False,
                     builtin_resources=None,
                     master_cache=None, vgmstream=None, ffmpeg=None,
                     accept_input_gaps=()):
    """Extract every logical bundle in *manifest* and write a JSON report.

    With ``manifest=None`` the bundle set is discovered from the *bundles*
    directory instead: everything the router recognizes as part of a supported
    asset pack is selected, and the report carries a ``discovery`` entry saying
    how many files were scanned, selected, and ignored.

    Character jobs use the shared settings bundle when its name is present in
    the same manifest; animations live solely in the shared motion library
    artifact. A domain whose extraction writes one shared index is extracted in a
    single job over all of that domain's packages, rather than one job per
    package; which domains those are follows from the routing table. Unknown
    bundle domains remain visible as ``unsupported`` entries so future routers can
    be added without changing the report shape.

    *master* is a directory of caller-supplied master tables.  Identity and
    locomotion come only from there, so without it the registry artifact is
    reported as skipped rather than silently omitted.

    *player_data* is the caller-supplied APK player-data file.  The dialogue UI
    lives there and in no downloadable package, so it is not a routed domain at
    all; without the path its artifact is likewise reported as skipped with the
    reason, never left out.

    *fixture_meshes* asks for the furniture geometry pass, which writes one glTF
    binary per package of the whole fixture family into ``fixture-models/``.  It
    is off by default because that is the heaviest artifact the pipeline makes;
    off, its entry is reported ``skipped`` with the reason, never omitted, so a
    reader can tell "nobody asked for it" from "this domain does not exist".

    *fixture_particles* asks for the furniture particle-emitter pass, which
    writes one document per particle-emitting package of the fixture-interface,
    cutscene-timeline and fixture-timeline families into ``fixture-particles/``.
    It is off by default for the same reason as the geometry pass; off, its
    entry is likewise reported ``skipped`` with the reason, never omitted.

    *builtin_resources* names the engine's own built-in resource containers (a
    container file, or a directory holding one), resolved with
    :func:`core.assets.packages.builtin_archive_paths` by the caller.  Some
    particle-system renderers of the fixture/cut-scene family draw a copy of a
    built-in primitive mesh that no package ships; without this, those mesh
    slots are reported unresolved rather than left unparsed.

    *unity_version* is the version to read a bundle whose header carries none
    against.  It is applied here, for the whole run, rather than being left to
    whichever extractor module happens to be imported first: the domains that
    run before the per-bundle loop -- the overhead items, the phenomena family,
    the site family -- load bundles before any of the per-package modules is
    imported, so with nothing set they raise instead of reading.  Passing None
    keeps whatever the caller already configured, and configures the shipped
    default when the caller configured nothing, so a direct call behaves like
    the command line rather than depending on import order.
    """
    _configure_unity_version(unity_version)
    if manifest is None:
        names, ignored = discover_bundles(bundles)
        discovery = {"scanned": len(names) + ignored, "selected": len(names), "ignored": ignored}
    else:
        names = parse_manifest(manifest)
        discovery = None
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    facial = next((n for n in names if route(n) and route(n).domain == "facial"), None)
    # Overhead-item packages each write the same shared index, which therefore
    # accumulates across jobs.  Drop a previous run's index first so one run's
    # output holds exactly the items that run extracted.
    stale_index = out / "emoticons" / "emoticons.json"
    if stale_index.exists() and any(route(n) and route(n).domain == "emoticon" for n in names):
        stale_index.unlink()
    report = {"version": 1, "bundles": [], "summary": {"requested": len(names), "succeeded": 0, "failed": 0, "unsupported": 0}}
    if discovery is not None:
        report["discovery"] = discovery
    emoticon_paths = {}
    emoticon_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "emoticon":
            continue
        try:
            emoticon_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            emoticon_errors[name] = f"{type(exc).__name__}: {exc}"
    lookup_paths = []
    if emoticon_paths:
        for name in names:
            if name == "mysekai__shader":
                try:
                    lookup_paths.append(_bundle_path(bundles, name))
                except Exception:
                    pass
    emoticon_result = None
    emoticon_error = None
    if emoticon_paths:
        try:
            from chara.emoticons import extract_emoticons
            emoticon_result = extract_emoticons(
                [str(path) for path in emoticon_paths.values()],
                str(out / "emoticons"),
                lookup_bundles=[str(path) for path in lookup_paths])
        except Exception as exc:
            emoticon_error = f"{type(exc).__name__}: {exc}"
    # A phenomenon spans several packages (a global one, a shared one, and one per
    # site), so its packages are extracted together in one job rather than one at a
    # time, and the shared index is written once from what that job produced.
    phenomena_paths = {}
    phenomena_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "phenomena":
            continue
        try:
            phenomena_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            phenomena_errors[name] = f"{type(exc).__name__}: {exc}"
    phenomena_result = None
    phenomena_error = None
    if phenomena_paths:
        try:
            from phenomena.environments import extract_phenomena
            phenomena_result = extract_phenomena(
                [str(path) for path in phenomena_paths.values()],
                str(out / "phenomena"), bundle_root=bundles, master=master,
                master_cache=master_cache, vgmstream=vgmstream, ffmpeg=ffmpeg)
        except Exception as exc:
            phenomena_error = f"{type(exc).__name__}: {exc}"
    # The site domain is one job over all of its packages too: a room module's
    # meshes are in the kit package, a material's shader is in a shared package, and
    # the placement table spans every site, so extracting one package at a time
    # would resolve neither.
    site_paths = {}
    site_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "site":
            continue
        try:
            site_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            site_errors[name] = f"{type(exc).__name__}: {exc}"
    site_result = None
    site_error = None
    if site_paths:
        try:
            from sites.pack import extract_sites
            site_result = extract_sites(
                [str(path) for path in site_paths.values()], str(out / "site"),
                bundle_root=bundles, master=master, master_cache=master_cache)
        except Exception as exc:
            site_error = f"{type(exc).__name__}: {exc}"
    # The fixture-interface family is one job over all its packages: the
    # attach-points index and the per-fixture grid are each written once from
    # what that job produced, not once per package.
    fixture_paths = {}
    fixture_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "fixture-interface":
            continue
        try:
            fixture_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            fixture_errors[name] = f"{type(exc).__name__}: {exc}"
    fixture_result = None
    fixture_error = None
    fixture_store = None
    fixture_mesh_result = None
    fixture_mesh_error = None
    fixture_particle_result = None
    fixture_particle_error = None
    if fixture_paths:
        try:
            from core.assets.packages import PackageStore
            from fixtures.interface import extract as extract_fixtures
            store = PackageStore([str(path) for path in fixture_paths.values()],
                                 root=bundles)
            fixture_store = store
            fixture_result = extract_fixtures(
                store, master, str(out / "fixture-interface"))
        except Exception as exc:
            fixture_error = f"{type(exc).__name__}: {exc}"
        # Geometry is the third read of this same package family, and it reads
        # the same opened store: the attach points, the placement grid and the
        # meshes are three views of one prefab tree, so re-opening the bundles
        # would be paying the load cost twice for the same objects.  It is a
        # pass of its own with its own try, though -- a geometry failure must be
        # reported as a geometry failure, not fold into the attach job's status.
        # The domain stays ``fixture-interface`` (its subject is the routing);
        # the geometry is registered as its own artifact set under its own path.
        if fixture_meshes:
            try:
                from core.assets.packages import PackageStore
                from fixtures.interface import extract_geometry
                if fixture_store is None:
                    fixture_store = PackageStore(
                        [str(path) for path in fixture_paths.values()],
                        root=bundles)
                fixture_mesh_result = extract_geometry(
                    fixture_store, str(out / "fixture-models"))
            except Exception as exc:
                fixture_mesh_error = f"{type(exc).__name__}: {exc}"
        # Particle emitters are a second-order read over three domains --
        # fixture-interface, cutscene-timeline and fixture-timeline -- because
        # a package's ParticleSystem and ParticleSystemRenderer components are
        # read across the same families whose attach points, tracks and clips
        # are each read as their own job elsewhere in this function.  The
        # ``fixture_store`` opened above only holds the fixture-interface
        # family, so it is not reused here; a second, wider store is opened
        # instead, which pays the fixture-interface packages' read cost a
        # second time.  fixtures.particles.extract_from_store does its own
        # per-domain filtering and counting once every package of the three
        # domains is in the store, so this driver only gathers their paths
        # rather than re-implementing that filtering.  The domain stays
        # ``fixture-interface`` in the report (its subject is the routing that
        # gates this whole job), the same reason the geometry entry keeps it.
        if fixture_particles:
            try:
                from core.assets.packages import PackageStore
                from fixtures.interface import extract_particles
                particle_domains = ("fixture-interface", "cutscene-timeline",
                                    "fixture-timeline")
                particle_paths = dict(fixture_paths)
                for name in names:
                    if name in particle_paths:
                        continue
                    target = route(name)
                    if target is None or target.domain not in particle_domains:
                        continue
                    try:
                        particle_paths[name] = _bundle_path(bundles, name)
                    except Exception:
                        pass
                # A renderer's material slots, and a mesh-drawn renderer's own
                # mesh, commonly point into a companion package this domain's
                # own selection never names on its own: an
                # ``mysekai__effect__*`` package supplies the fixture and
                # cut-scene family's materials and meshes, and the shared
                # ``mysekai__shader`` package supplies their shaders.  The
                # router does not assign either to one of this pass's three
                # domains, and discovery may not have selected them into
                # *names* at all, so the bundle directory is scanned by name
                # directly; every match is loaded to serve as a pointer
                # target and is never itself extracted as a target package.
                supply_paths = {}
                try:
                    for entry in sorted(os.listdir(bundles)):
                        if entry in particle_paths:
                            continue
                        candidate = Path(bundles) / entry
                        if not candidate.is_file():
                            continue
                        if entry.startswith("mysekai__effect__") or entry == "mysekai__shader":
                            supply_paths[entry] = str(candidate)
                except OSError:
                    pass
                particle_store = PackageStore(
                    [str(path) for path in particle_paths.values()]
                    + list(supply_paths.values())
                    + list(builtin_resources or []),
                    root=bundles)
                fixture_particle_result = extract_particles(
                    particle_store, str(out / "fixture-particles"))
            except Exception as exc:
                fixture_particle_error = f"{type(exc).__name__}: {exc}"
    # The dialogue-camera domain is one package today, but the reader takes a
    # family and reports per package, so it is driven exactly like the other
    # shared-read domains rather than being special-cased on today's count.
    camera_paths = {}
    camera_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "camera":
            continue
        try:
            camera_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            camera_errors[name] = f"{type(exc).__name__}: {exc}"
    camera_result = None
    camera_error = None
    if camera_paths:
        try:
            from perf.camera import read_camera_assets
            camera_result = read_camera_assets(
                [str(path) for path in camera_paths.values()],
                str(out / "camera"), bundle_root=bundles)
        except Exception as exc:
            camera_error = f"{type(exc).__name__}: {exc}"
    # The two timeline families each run their three timeline reads as one job
    # over the whole family, for the same cross-package reason.
    cutscene_paths = {}
    cutscene_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "cutscene-timeline":
            continue
        try:
            cutscene_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            cutscene_errors[name] = f"{type(exc).__name__}: {exc}"
    cutscene_result = None
    cutscene_error = None
    if cutscene_paths:
        try:
            cutscene_result = _run_timeline_job(
                [str(path) for path in cutscene_paths.values()],
                str(out / "cutscene-timeline"), bundle_root=bundles)
        except Exception as exc:
            cutscene_error = f"{type(exc).__name__}: {exc}"
    fixture_timeline_paths = {}
    fixture_timeline_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "fixture-timeline":
            continue
        try:
            fixture_timeline_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            fixture_timeline_errors[name] = f"{type(exc).__name__}: {exc}"
    fixture_timeline_result = None
    fixture_timeline_error = None
    if fixture_timeline_paths:
        try:
            fixture_timeline_result = _run_timeline_job(
                [str(path) for path in fixture_timeline_paths.values()],
                str(out / "fixture-timeline"), bundle_root=bundles)
        except Exception as exc:
            fixture_timeline_error = f"{type(exc).__name__}: {exc}"
    # The animation-clip export is a derived pass, not a routed domain: what it
    # exports is decided by the clip-target documents the two timeline jobs have
    # just written, never by a package name.  Giving it a route would promise
    # that a bundle name selects it, which no bundle name does; so it runs here,
    # after both timeline jobs, over their output.
    animation_target_dirs = [out / "cutscene-timeline" / "clip-targets",
                             out / "fixture-timeline" / "clip-targets"]
    animation_asked = any(directory.is_dir() for directory in animation_target_dirs)
    animation_result = None
    animation_error = None
    animation_targets = 0
    animation_missing_deps = []
    # The export itself runs *after* the per-bundle loop, not here.  A clip's
    # channels name paths into hierarchies the animation package does not own —
    # the character rig (written as ``sd_<unit>.glb`` by the character domain)
    # and the fixture rig (written as ``fixture-models/`` by the geometry pass).
    # Running the export before those exist resolves nothing: measured on one
    # end-to-end run, ``character_motion`` came out with 371 animations over
    # **0** nodes, so the whole cut-scene family could not play.  Only an
    # end-to-end run shows this — each domain's own gate prepares its inputs by
    # hand and never hits the ordering.  See the call site below the loop.
    # The talk scripts resolve their arguments against constant tables that are
    # in a package of their own, so the tables are read before the loop: the
    # talk job needs them on its own turn, and reading them there would tie the
    # result to whichever of the two packages the manifest happens to list
    # first.  A run without the package is not an error — the talk documents
    # still carry every step — but the tokens stay unresolved, which the
    # documents state and their constants criterion turns red on.
    constants_paths = {}
    constants_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "talk-constants":
            continue
        try:
            constants_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            constants_errors[name] = f"{type(exc).__name__}: {exc}"
    constants_result = None
    constants_error = None
    if constants_paths:
        try:
            from perf.fixture_talks import read_constants
            path = next(iter(constants_paths.values()))
            tables, scalars, source = read_constants(str(path))
            constants_result = {
                "asset": source,
                "tables": {key: len(value) for key, value in sorted(tables.items())},
                "scalars": len(scalars),
                "entries": sum(len(value) for value in tables.values()),
            }
        except Exception as exc:
            constants_error = f"{type(exc).__name__}: {exc}"
    # The one talk package feeds two extractors; the second one's own outcome is
    # reported separately so that a failure there cannot be read as a failure of
    # the first, nor hide behind its success.
    fixture_talks_asked = False
    fixture_talks_result = None
    fixture_talks_error = None
    for name in names:
        entry = _entry(name)
        target = route(name)
        if target is None:
            entry["status"] = "unsupported"
            entry["error"] = "no extractor registered for bundle domain"
            report["summary"]["unsupported"] += 1
            report["bundles"].append(entry)
            continue
        try:
            bundle = _bundle_path(bundles, name)
            if target.domain == "character":
                from chara.characters import extract_character
                # Character packs carry no embedded animations: the shared
                # motion library is the single animation artifact (bound by
                # humanoid bone name), so embedding the full clip set into
                # every character would duplicate it once per character.
                sampled = None
                facial_data = None
                if facial:
                    from chara.characters import facial_tables
                    facial_data = facial_tables(str(_bundle_path(bundles, facial)))
                # Every character material's m_Shader is an external PPtr into
                # the one shared shader package (verified: all 95 materials
                # across the 31 real character bundles resolve through the
                # single CAB that package supplies) -- without it as an aux
                # bundle, extract_character can only report the shader as an
                # unresolved external, never its name.
                aux_bundles = []
                try:
                    aux_bundles.append(str(_bundle_path(bundles, "mysekai__shader")))
                except FileNotFoundError:
                    pass  # not supplied: shader stays reported as external, honestly
                # Pack artifacts are named after the character (sd_<unit>),
                # matching the viewer contract, not after the model bundle.
                stem = name.split("__")[-1]
                parts = stem.split("_")
                if len(parts) >= 3 and parts[0] == "mdl":
                    stem = f"{parts[1]}_{parts[2]}"
                result = extract_character(str(bundle), str(out), stem, sampled=sampled,
                                           facial=facial_data, aux=tuple(aux_bundles))
            elif target.domain == "motion":
                from chara.motion_library import export_motion_library
                reference = next((n for n in names if route(n) and route(n).domain == "character"), None)
                if not reference:
                    raise ValueError("motion extraction requires a character reference bundle in manifest")
                result = export_motion_library(str(_bundle_path(bundles, reference)), str(bundle), str(out))
            elif target.domain == "emoticon":
                if name in emoticon_errors:
                    raise FileNotFoundError(emoticon_errors[name])
                if emoticon_error:
                    raise RuntimeError(emoticon_error)
                result = emoticon_result or {}
            elif target.domain == "phenomena":
                if name in phenomena_errors:
                    raise FileNotFoundError(phenomena_errors[name])
                if phenomena_error:
                    raise RuntimeError(phenomena_error)
                result = dict((phenomena_result or {}).get("perBundle", {}).get(name, {}))
                result["index"] = (phenomena_result or {}).get("path", "")
            elif target.domain == "sound":
                # Sound packages have no job of their own: the phenomena job reads
                # them, because only the master rows it already reads say which
                # cues are wanted from which package.
                if phenomena_error:
                    raise RuntimeError(phenomena_error)
                asked = (phenomena_result or {}).get("soundPackages", {}).get(name)
                if asked is None:
                    entry["status"] = "unsupported"
                    entry["error"] = NO_SOUND_ROW
                    report["summary"]["unsupported"] += 1
                    report["bundles"].append(entry)
                    continue
                if asked["status"] != "succeeded":
                    raise RuntimeError(asked["error"])
                result = {"streams": asked["streams"], "cues": asked["cues"],
                          "archiveBytes": asked["archiveBytes"],
                          "index": (phenomena_result or {}).get("path", "")}
            elif target.domain == "site":
                if name in site_errors:
                    raise FileNotFoundError(site_errors[name])
                if site_error:
                    raise RuntimeError(site_error)
                result = dict((site_result or {}).get("perBundle", {}).get(name, {}))
                result["index"] = (site_result or {}).get("path", "")
            elif target.domain == "fixture-interface":
                if name in fixture_errors:
                    raise FileNotFoundError(fixture_errors[name])
                if fixture_error:
                    raise RuntimeError(fixture_error)
                result = dict((fixture_result or {}).get("perBundle", {}).get(name, {}))
                result["index"] = (fixture_result or {}).get("path", "")
            elif target.domain == "cutscene-timeline":
                if name in cutscene_errors:
                    raise FileNotFoundError(cutscene_errors[name])
                if cutscene_error:
                    raise RuntimeError(cutscene_error)
                result = dict((cutscene_result or {}).get("perBundle", {}).get(name, {}))
            elif target.domain == "fixture-timeline":
                if name in fixture_timeline_errors:
                    raise FileNotFoundError(fixture_timeline_errors[name])
                if fixture_timeline_error:
                    raise RuntimeError(fixture_timeline_error)
                result = dict((fixture_timeline_result or {}).get("perBundle", {}).get(name, {}))
            elif target.domain == "camera":
                if name in camera_errors:
                    raise FileNotFoundError(camera_errors[name])
                if camera_error:
                    raise RuntimeError(camera_error)
                record = next((r for r in (camera_result or {}).get("packages", [])
                               if r.get("package") == name), None)
                if record is None or record.get("missing"):
                    raise FileNotFoundError(
                        f"camera package not read: {name}")
                result = {k: v for k, v in record.items() if k != "package"}
                result["json"] = str(out / "camera" / f"{name}.json")
            elif target.domain == "talk":
                # Which talks belong to one character is decided by master tables,
                # so without them the corpus cannot be scoped and is not written.
                if not master:
                    entry["status"] = "unsupported"
                    entry["error"] = ("direct-talk extraction needs caller-supplied "
                                      "master tables to scope the corpus")
                    report["summary"]["unsupported"] += 1
                    report["bundles"].append(entry)
                    continue
                from chara.talks import extract_talks
                lib_bundle = (str(next(iter(constants_paths.values())))
                              if constants_paths else None)
                result = extract_talks(master, str(bundle), str(out / "talks.json"),
                                       master_cache=master_cache,
                                       lib_bundle=lib_bundle)
                # One package, two families.  ``chara/talks`` reads the
                # single-character talks that happen away from any fixture;
                # ``perf/fixture_talks`` reads the beside-the-fixture ones.
                # Both read this same package and write different artifacts, so
                # both run here.  A second route would have been the wrong shape:
                # one package cannot belong to two domains.
                fixture_talks_asked = True
                fixture_talks_path = out / "fixture-talks" / "talks.json"
                try:
                    from perf.fixture_talks import extract_fixture_talks
                    fixture_talks_result = extract_fixture_talks(
                        master, str(bundle), str(fixture_talks_path),
                        master_cache=master_cache,
                        lib_bundle=lib_bundle)
                    result = dict(result)
                    result["fixtureTalks"] = str(fixture_talks_path)
                except Exception as exc:
                    fixture_talks_error = f"{type(exc).__name__}: {exc}"
            elif target.domain == "talk-constants":
                # No product of its own: what this package contributes is the
                # constant tables the talk extractors resolve against, so the
                # entry carries their sizes.  A read failure is this package's
                # own failure and is not folded into the talk domain's.
                if name in constants_errors:
                    raise FileNotFoundError(constants_errors[name])
                if constants_error:
                    raise RuntimeError(constants_error)
                result = dict(constants_result or {})
            elif target.domain == "performance":
                from chara.alone_actions import write_alone_actions
                result = write_alone_actions(str(bundle), str(out / "alone-actions.json"))
            else:
                from chara.characters import facial_tables
                target_path = out / "facial-tables.json"
                target_path.write_text(json.dumps(facial_tables(str(bundle)), ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
                result = {"json": str(target_path)}
            entry["status"] = "succeeded"
            entry["artifacts"] = [str(v) for v in result.values() if isinstance(v, str) and Path(v).exists()]
            entry["counts"] = {k: v for k, v in result.items() if isinstance(v, (int, float))}
            report["summary"]["succeeded"] += 1
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            report["summary"]["failed"] += 1
        report["bundles"].append(entry)
    # Derived animation pass, deferred to here so the hierarchies its channels
    # name are already on disk (see the note where its inputs are declared).
    # A dependency that is absent is *reported*, never silently tolerated: an
    # export that resolves no rig still writes files and would otherwise be read
    # as a success while every clip plays over zero nodes.
    if animation_asked:
        if not list(Path(out).glob("sd_*.glb")):
            animation_missing_deps.append(
                "character rigs (sd_*.glb): foreign character-rig channels "
                "cannot resolve, so cut-scene clips export over zero nodes")
        if not (out / "fixture-models").is_dir():
            animation_missing_deps.append(
                "fixture geometry (fixture-models/): fixture-rig channels "
                "cannot resolve; pass fixture_meshes=True to write it")
        try:
            from perf.animations import (ForeignRigs, export_targets,
                                         load_clip_targets)
            by_target = {}
            for directory in animation_target_dirs:
                if not directory.is_dir():
                    continue
                _, part = load_clip_targets(str(directory))
                for target, clips in part.items():
                    by_target.setdefault(target, set()).update(clips)
            animation_targets = len(by_target)
            # The rigs a clip's channels reach into have to be handed in: with
            # none, every foreign path stays unresolved and the export still
            # writes a file, so the run looks successful while the clips play
            # over zero nodes.  That is why the pass is deferred to here — both
            # rig sources are outputs of this same run.
            fixture_models = out / "fixture-models"
            foreign = ForeignRigs.load(
                fixture_models_dir=(str(fixture_models)
                                    if fixture_models.is_dir() else None),
                character_rig_paths=[str(path) for path
                                     in sorted(Path(out).glob("sd_*.glb"))])
            animation_result = export_targets(by_target, str(bundles),
                                              str(out / "perf-animations"),
                                              foreign=foreign)
        except Exception as exc:
            animation_error = f"{type(exc).__name__}: {exc}"
    report["derived"] = [_registry_artifact(names, out, master, master_cache)]
    if phenomena_paths:
        counted = {k: v for k, v in (phenomena_result or {}).items()
                   if isinstance(v, int)}
        report["derived"].append({
            "artifact": "phenomena/index.json",
            "status": "failed" if phenomena_error else "succeeded",
            "counts": counted,
            "error": phenomena_error or ""})
    if site_paths:
        report["derived"].append({
            "artifact": "site/index.json",
            "status": "failed" if site_error else "succeeded",
            "counts": {k: v for k, v in (site_result or {}).items()
                       if isinstance(v, int)},
            "error": site_error or ""})
    if fixture_paths:
        report["derived"].append({
            "artifact": "fixture-interface/attach-points.json",
            "status": "failed" if fixture_error else "succeeded",
            "counts": {k: v for k, v in (fixture_result or {}).items()
                       if isinstance(v, int)},
            "error": fixture_error or ""})
        # Always present, never omitted: skipped-with-a-reason when the caller
        # did not ask for geometry, otherwise the outcome of the pass -- and a
        # pass that raised nothing but produced no counts at all is reported
        # failed, not succeeded, since that shape is indistinguishable from a
        # call that was never made (see ``_optional_pass_status``).
        geometry_counts = {k: v for k, v in (fixture_mesh_result or {}).items()
                           if isinstance(v, int)}
        geometry_status, geometry_error = _optional_pass_status(
            fixture_meshes, FIXTURE_MESHES_SKIPPED, fixture_mesh_error,
            geometry_counts)
        report["derived"].append({
            "artifact": "fixture-models/",
            "domain": "fixture-interface",
            "status": geometry_status,
            "counts": geometry_counts,
            "error": geometry_error})
        # Always present, never omitted, for the same reason as the geometry
        # entry above: skipped-with-a-reason when the caller did not ask for
        # the particle-emitter pass, otherwise the outcome of the pass -- and
        # the same failed-not-succeeded shape for a pass that produced no
        # counts at all (see ``_optional_pass_status``).
        particle_counts = {k: v for k, v in (fixture_particle_result or {}).items()
                           if isinstance(v, int)}
        particle_status, particle_error = _optional_pass_status(
            fixture_particles, FIXTURE_PARTICLES_SKIPPED, fixture_particle_error,
            particle_counts)
        report["derived"].append({
            "artifact": "fixture-particles/index.json",
            "domain": "fixture-interface",
            "status": particle_status,
            "counts": particle_counts,
            "error": particle_error})
    if fixture_talks_asked:
        report["derived"].append({
            "artifact": "fixture-talks/talks.json",
            "status": "failed" if fixture_talks_error else "succeeded",
            "counts": {k: v for k, v in (fixture_talks_result or {}).items()
                       if isinstance(v, int)},
            "error": fixture_talks_error or ""})
    if animation_asked:
        # A package that failed to export is reported as a failure of this pass,
        # not folded into the count of what did export.
        animation_failed = list((animation_result or {}).get("failed") or [])
        report["derived"].append({
            "artifact": "perf-animations/",
            "status": "failed" if (animation_error or animation_failed) else "succeeded",
            "counts": dict({"targets": animation_targets},
                           **{k: v for k, v in (animation_result or {}).items()
                              if isinstance(v, int)}),
            "missingDependencies": list(animation_missing_deps),
            "error": animation_error or (
                "; ".join(f"{f['package']}: {f['detail']}" for f in animation_failed)
                if animation_failed else "")
            or ("; ".join(animation_missing_deps) if animation_missing_deps else "")})
    # The dialogue UI is not derived from any bundle -- its input is a file the
    # caller hands in -- so it is reported under its own key rather than among
    # the artifacts derived from the extracted packages.  It is always present:
    # skipped-with-a-reason when no path was supplied, never absent.
    report["playerData"] = [_ui_artifact(out, player_data)]
    pack_manifest = write_pack_manifest(out)
    report["derived"].append({
        "artifact": "manifest.json",
        "status": "succeeded" if pack_manifest else "skipped",
        "counts": {"units": len(json.loads(pack_manifest.read_text(encoding="utf-8"))["units"])} if pack_manifest else {},
        "error": "" if pack_manifest else "no character artifacts on disk"})
    # The run's own account of what it did not get. Recorded on every run, so
    # "nobody handed it its input" is a number in the report rather than an
    # empty directory somebody notices months later.
    # Its own top-level block, not inside ``summary``: that one counts bundles by
    # outcome and a consumer reads its four keys as a closed set, so widening it
    # would change a shape the data contract fixes.  These are per-artifact rows,
    # a different kind of thing.
    ok, gaps, unaccepted = check_input_gaps(report, accepted=accept_input_gaps)
    report["inputGaps"] = {
        "gaps": gaps,
        "unaccepted": unaccepted,
        "accepted": sorted(accept_input_gaps or ()),
        "ok": ok,
    }
    report_path = out / "extraction-report.json"
    report["report"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    return report
