"""Manifest-driven extraction orchestration."""
import json
import os
from pathlib import Path

from .assets.manifest import parse_manifest
from .assets.router import route


def _bundle_path(bundles, logical_name):
    root = Path(bundles)
    candidates = [root / logical_name, root / logical_name.replace("__", "/")]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"bundle not found: {logical_name}")


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
                     master_cache=None, vgmstream=None, ffmpeg=None):
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
    """
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
    if fixture_paths:
        try:
            from core.assets.packages import PackageStore
            from fixtures.interface import extract as extract_fixtures
            store = PackageStore([str(path) for path in fixture_paths.values()],
                                 root=bundles)
            fixture_result = extract_fixtures(
                store, master, str(out / "fixture-interface"))
        except Exception as exc:
            fixture_error = f"{type(exc).__name__}: {exc}"
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
                # Pack artifacts are named after the character (sd_<unit>),
                # matching the viewer contract, not after the model bundle.
                stem = name.split("__")[-1]
                parts = stem.split("_")
                if len(parts) >= 3 and parts[0] == "mdl":
                    stem = f"{parts[1]}_{parts[2]}"
                result = extract_character(str(bundle), str(out), stem, sampled=sampled, facial=facial_data)
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
                result = extract_talks(master, str(bundle), str(out / "talks.json"),
                                       master_cache=master_cache)
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
    pack_manifest = write_pack_manifest(out)
    report["derived"].append({
        "artifact": "manifest.json",
        "status": "succeeded" if pack_manifest else "skipped",
        "counts": {"units": len(json.loads(pack_manifest.read_text(encoding="utf-8"))["units"])} if pack_manifest else {},
        "error": "" if pack_manifest else "no character artifacts on disk"})
    report_path = out / "extraction-report.json"
    report["report"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    return report
