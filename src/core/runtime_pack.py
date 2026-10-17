"""Assemble a source-qualified Moly runtime from an extracted JP snapshot.

No gameplay master, character, dialogue or furniture data is borrowed across
regions. An explicitly supplied UI donor contributes only the app's shared
visual UI resources; every such byte is recorded separately in provenance.
UI documents already extracted from JP bytes come from a separate,
region-tagged UI root; the donor copy of each such document is removed, so the
runtime cannot fall back to it.
The destination remains a staging directory until validation succeeds.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil

from .atomic import PUBLIC_FILE_MODE, exclusive_lock, write_json
from .jp import JPError, load_descriptor
from .master import Master
from chara.motion_storage import MOTION_STORAGE_REVISION
from chara.rotation_encoding import DEFAULT_ROTATION_ENCODING, ROTATION_ENCODINGS

SHARED_UI_DIRECTORIES = ("ui", "ui-layout-v2")
# The region-tagged UI root: its ui-manifest.json names the region and client
# version of every document in it.
REGION_UI_DIRECTORY = "ui-jp"
# The shared tree a donor layout document sits in; a JP document at
# <family>/<prefab>.json replaces the donor copy at the same path there.
DONOR_LAYOUT_DIRECTORY = "ui-layout-v2"
# JP UI documents the runtime must carry: every layout document the runtime
# loads.  Each replaces the donor copy at the same path under the donor tree.
REGION_UI_DOCUMENTS = {
    name: f"{DONOR_LAYOUT_DIRECTORY}/{name}" for name in (
        "menu/MysekaiMenuDialog.json", "info/OptionDialog.json",
        "info/ScreenLayerMysekaiInfo.json", "menu/MysekaiGetResourceSubWindowDialog.json",
        "hud/ScreenLayerMysekaiHUD.json", "talk/ScreenLayerMysekaiTalk.json",
        "menu/Common1ButtonDialog.json", "menu/Common2ButtonDialog.json",
        "shell/ScreenLayerMysekaiHome.json", "shell/ScreenLayerMysekaiMyRoom.json",
        "shell/ScreenLayerMysekaiHarvest.json", "shell/ScreenLayerMysekaiDelivery.json",
        "editor/ScreenLayerSiteEditMode.json", "editor/MysekaiEditSaveConfirmationDialog.json",
        "editor/SiteEditListSelectorCell.json", "editor/SiteEditOutDoorContentList.json",
        "editor/SiteEditFloorContentList.json", "editor/SiteEditWallContentList.json",
        "editor/SiteEnvironmentContentList.json", "editor/RoadFixtureContentList.json",
        "editor/FixtureSelectCell.json", "editor/UIPartsLeftTabListMysekaiContentCell.json",
        "editor/ScreenLayerHeader.json",
    )
}
# A donor document may also ship as a gzip twin of the same bytes.
DONOR_TWIN_SUFFIXES = ("", ".gz")
REQUIRED = ("characters.json", "manifest.json", "motion-library.glb", "talks.json",
            "fixture-talks/out/fixture-talks.json", "mysekai-fixtures.json",
            "fixture-reactions.json", "birthday-end-character-motions.json",
            "fixture-models/index.json", "fixture-models/player-data.json",
            "fixture-thumbnails/fixture-thumbnails.json", "client-config.json",
            "actor-animations/index.json", "avatar/motion/sd-switch-simple.glb", "site/sites.json", "site-groups.json", "tweet-tables.json", "tweets.json",
            f"{REGION_UI_DIRECTORY}/ui-manifest.json",
            *(f"{REGION_UI_DIRECTORY}/{name}" for name in REGION_UI_DOCUMENTS))


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _copy_tree(source, destination):
    if not source.is_dir():
        raise JPError(f"required source directory is missing: {source.name}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise JPError("linked runtime resources are not followed")
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _region_ui_source(ui_jp_from):
    """Check a JP UI extraction before any of it is copied; fail closed.

    Every required document must have a manifest row of this region whose
    document hash equals the file on disk.
    """
    if ui_jp_from is None:
        raise JPError("the JP runtime draws its field menu from JP bytes: supply --ui-jp-from "
                      "with a JP UI extraction (ui-layout --region jp)")
    root = Path(ui_jp_from).resolve()
    manifest_path = root / "ui-manifest.json"
    if not manifest_path.is_file():
        raise JPError("JP UI extraction has no ui-manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("version") != 1 or manifest.get("region") != "jp" or manifest.get("failures"):
        raise JPError("JP UI manifest is not a clean version-1 jp manifest")
    rows = {row.get("document"): row for row in manifest.get("documents", [])}
    for name in REGION_UI_DOCUMENTS:
        row = rows.get(name)
        if row is None or row.get("region") != "jp" or row.get("kind") != "prefab":
            raise JPError(f"JP UI manifest has no jp prefab row for {name}")
    for name, row in rows.items():
        if row.get("region") != "jp":
            raise JPError(f"JP UI manifest row is not jp: {name}")
        path = root / name
        if not path.is_file():
            raise JPError(f"JP UI document is missing: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != row.get("documentSha256"):
            raise JPError(f"JP UI document does not match its manifest row: {name}")
    return root, manifest, hashlib.sha256(manifest_bytes).hexdigest()


def _replaced_donor_copies(manifest):
    """Donor paths every JP layout document of *manifest* replaces."""
    return {row["document"]: f"{DONOR_LAYOUT_DIRECTORY}/{row['document']}"
            for row in manifest["documents"] if row.get("kind") == "prefab"}


def assemble_shared_ui(out, ui_from, region_ui):
    """Copy the donor UI trees, then the JP UI root, removing the donor copy of
    every document the JP root carries. Returns (provenance, step result)."""
    out = Path(out)
    if ui_from is None:
        raise JPError("runtime UI is an application resource: supply --ui-from with a source-qualified existing UI extraction")
    donor = Path(ui_from).resolve()
    if donor == out or out.is_relative_to(donor):
        raise JPError("UI donor must be a separate existing extraction")
    receipts = []
    for name in SHARED_UI_DIRECTORIES:
        _copy_tree(donor / name, out / name)
        for path in sorted((out / name).rglob("*")):
            if path.is_file():
                receipts.append({"path": path.relative_to(out).as_posix(),
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    provenance = {"purpose": "shared application UI only; no region-scoped gameplay tables", "files": receipts}
    if (donor / "mysekai-fixtures.json").exists():
        meta = _json(donor / "mysekai-fixtures.json")
        provenance["donorRegion"] = meta.get("region")
        provenance["donorVersion"] = meta.get("gameVersion")
    # Every layout document the JP UI root carries replaces its donor copy
    # (and a gzip twin of it), so the runtime has no donor copy to fall back to.
    region_root, region_manifest, _ = region_ui
    donor_sha = {row["path"]: row["sha256"] for row in receipts}
    replaced, removed_paths = [], set()
    for name, donor_copy in sorted(_replaced_donor_copies(region_manifest).items()):
        removed = []
        for suffix in DONOR_TWIN_SUFFIXES:
            path = f"{donor_copy}{suffix}"
            if (out / path).is_file():
                (out / path).unlink()
                removed.append({"path": path, "sha256": donor_sha[path]})
                removed_paths.add(path)
        replaced.append({"document": f"{REGION_UI_DIRECTORY}/{name}", "donorCopy": donor_copy,
                         "removedDonorFiles": removed})
    receipts = [row for row in receipts if row["path"] not in removed_paths]
    destination = out / REGION_UI_DIRECTORY
    if destination.exists():
        shutil.rmtree(destination)
    _copy_tree(region_root, destination)
    region_receipts = [{"path": path.relative_to(out).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                       for path in sorted(destination.rglob("*")) if path.is_file()]
    provenance["files"] = receipts
    provenance["regionUi"] = {
        "directory": REGION_UI_DIRECTORY, "region": region_manifest["region"],
        "clientVersion": region_manifest.get("clientVersion"),
        "unityVersion": region_manifest.get("unityVersion"),
        "replacesDonor": replaced, "files": region_receipts}
    for name, donor_copy in _replaced_donor_copies(region_manifest).items():
        if (any((out / f"{donor_copy}{suffix}").exists() for suffix in DONOR_TWIN_SUFFIXES)
                or not (destination / name).is_file()):
            raise JPError(f"runtime UI still carries the donor copy of {name}")
    return provenance, {"files": len(receipts), "regionFiles": len(region_receipts),
                        "replacedDonorDocuments": sum(1 for row in replaced if row["removedDonorFiles"])}


def _step_cached(state, label, cache_identity=None):
    return label in state["steps"] and (cache_identity is None
        or state.get("stepIdentities", {}).get(label) == cache_identity)


def prepare_runtime(descriptor_path, workspace, out, *, ui_from=None, ui_jp_from=None, audio=True,
                    decoder=None, transcoder=None, progress=None, master_from=None, actor_workers=2,
                    rotation_encoding=DEFAULT_ROTATION_ENCODING):
    if rotation_encoding not in ROTATION_ENCODINGS:
        raise ValueError("rotation_encoding must be snorm16 or float32")
    descriptor = load_descriptor(descriptor_path)
    source = descriptor["source"]
    workspace, out = Path(workspace).resolve(), Path(out).resolve()
    if out == workspace or out.is_relative_to(workspace / "raw") or out.is_relative_to(workspace / "decrypted"):
        raise JPError("runtime output must not replace original bundles or the workspace")
    original = workspace / "extracted"
    if out == original:
        raise JPError("runtime staging must be separate from extraction evidence")
    cache = _json(workspace / "cache-source.json")
    if cache["identity"]["assetHash"] != source["assetHash"] or cache["identity"]["region"] != "jp":
        raise JPError("raw cache and descriptor identities disagree")
    master_dir = Path(master_from).resolve() if master_from else (
        workspace / "master-haruki" if (workspace / "master-haruki" / "master-source.json").is_file()
        else workspace / "master")
    master_source = _json(master_dir / "master-source.json")
    if master_source["status"] != "complete" or master_source["identity"]["assetHash"] != source["assetHash"]:
        raise JPError("a completed, bundle-matched master snapshot is required")
    extraction = _json(original / "extraction-report.json")
    if extraction["summary"]["failed"] or any(item.get("status") == "failed" for item in extraction.get("derived", [])):
        raise JPError("extraction contains failed artifacts; repair them before runtime assembly")
    identity = {"region": "jp", "assetVersion": source["assetVersion"], "assetHash": source["assetHash"],
                "masterCommit": master_source["identity"]["commit"]}
    out.mkdir(parents=True, exist_ok=True)
    marker = out / "runtime-build.json"
    with exclusive_lock(out / ".runtime.lock"):
        if marker.exists():
            state = _json(marker)
            if state["identity"] != identity:
                raise JPError("runtime staging belongs to another source snapshot")
        elif any(p.name != ".runtime.lock" for p in out.iterdir()):
            raise JPError("runtime destination is nonempty and has no source journal")
        else:
            state = {"version": 1, "identity": identity, "status": "building", "steps": {}, "sharedUi": None}

        def checkpoint(label):
            state["currentStep"] = label
            write_json(marker, state, mode=PUBLIC_FILE_MODE)
            if progress is not None:
                progress(label)

        def step(label, run, *, cache_identity=None):
            if _step_cached(state, label, cache_identity):
                return state["steps"][label]
            # A policy change invalidates the old success stamp before writes.
            # If rebuilding fails, switching back cannot reuse a half-updated
            # output merely because a previous policy once completed here.
            state["steps"].pop(label, None)
            state.setdefault("stepIdentities", {}).pop(label, None)
            checkpoint(label)
            try:
                value = run()
            except Exception as exc:
                state["status"] = "failed"
                state["failure"] = {"step": label, "type": type(exc).__name__, "reason": str(exc)}
                checkpoint(label + ": failed")
                raise
            state.pop("failure", None)
            state["status"] = "building"
            state["steps"][label] = value
            if cache_identity is not None:
                state["stepIdentities"][label] = cache_identity
            checkpoint(label + ": complete")
            return value

        def base():
            # Actor exports have their own immutable receipts/publication stage;
            # work directories and interrupted attempts are never runtime data.
            for path in original.iterdir():
                if path.name == "actor-animations":
                    continue
                if path.is_symlink():
                    raise JPError("linked runtime resources are not followed")
                if path.is_dir():
                    _copy_tree(path, out / path.name)
                elif path.is_file():
                    shutil.copy2(path, out / path.name)
            return {"artifacts": len(list(original.rglob("*")))}
        step("extracted-artifacts", base)
        master = Master(str(master_dir))

        def master_outputs():
            from fixtures.master_slice import export_fixture_master_slice
            from fixtures.player_data import export
            from core.master_tables import (extract_master_tables, TABLES, OPTIONAL_TABLES,
                                            PROJECTED_TABLES)
            from core.client_config import extract_client_config
            from chara.tweets import extract_tweets
            from chara.tweet_tables import extract_tweet_tables
            from sites.site_groups import extract_site_groups
            from sites.birthday_parties import extract_birthday_parties
            from sites.birthday_party_delivery import extract_birthday_party_delivery
            from phenomena.partvoice import build_partvoice_routes, write_partvoice_routes
            export_fixture_master_slice(str(master_dir), str(out / "mysekai-fixtures.json"),
                                        region="jp", game_version=source["appVersion"])
            export(str(master_dir), str(out), region="jp", game_version=source["appVersion"],
                   bundles=str(workspace / "decrypted"), unity_version="2022.3.62f3")
            names = [name for name in {**TABLES, **OPTIONAL_TABLES} if (master_dir / (name + ".json")).is_file()]
            names += [name for name, (source_table, _, _) in PROJECTED_TABLES.items()
                      if (master_dir / (source_table + ".json")).is_file()]
            extract_master_tables(str(master_dir), str(out), tables=names)
            extract_client_config(str(master_dir), str(out / "client-config.json"))
            extract_tweets(str(master_dir), str(out / "tweets.json"))
            extract_tweet_tables(str(master_dir), str(out / "tweet-tables.json"))
            extract_site_groups(str(master_dir), str(out / "site-groups.json"))
            extract_birthday_parties(str(master_dir), str(out / "birthday-parties.json"))
            extract_birthday_party_delivery(str(master_dir), str(out / "birthday-party-delivery.json"))
            routes = build_partvoice_routes(master, _json(out / "characters.json"))
            write_partvoice_routes(routes, str(out / "phenomena"))
            return {"tables": len(names)}
        step("runtime-master-tables", master_outputs)

        def fixture_reactions():
            from fixtures.reactions import PACKAGE, extract_fixture_reactions
            bundle = workspace / "decrypted" / PACKAGE
            if not bundle.is_file():
                raise JPError(f"native furniture reaction source is missing: {PACKAGE}")
            return extract_fixture_reactions(bundle, out / "fixture-reactions.json")
        step("source-fixture-reactions-v1", fixture_reactions)

        def birthday_end_motions():
            from sites.birthday_end_motions import PACKAGE, extract_birthday_end_motions
            bundle = workspace / "decrypted" / PACKAGE
            if not bundle.is_file():
                raise JPError(f"birthday end-motion source is missing: {PACKAGE}")
            return extract_birthday_end_motions(bundle, out / "birthday-end-character-motions.json")
        step("source-birthday-end-motions-v1", birthday_end_motions)

        def birthday_party_talks():
            # The party scripts are selected by a master table, so the block is
            # computed against this runtime's master rather than copied with
            # the extraction, which may have been read from another partition.
            # Only that block changes; the ordinary corpus stays as extracted.
            from chara.talks import LIB_PACKAGE, attach_birthday_talks
            talk_bundle = workspace / "decrypted" / "mysekai__talk__scenario__talk"
            lib_bundle = workspace / "decrypted" / LIB_PACKAGE
            for bundle in (talk_bundle, lib_bundle):
                if not bundle.is_file():
                    raise JPError(f"birthday-party talk source is missing: {bundle.name}")
            report = attach_birthday_talks(str(master_dir), str(talk_bundle),
                                           str(out / "talks.json"), str(out / "talks.json"),
                                           lib_bundle=str(lib_bundle))
            if report.get("status") != "present":
                raise JPError("the runtime master carries no birthdayPartyScenarios")
            return {key: value for key, value in report.items() if isinstance(value, int)}
        step("birthday-party-talks-v1", birthday_party_talks)

        def thumbnail_outputs():
            from ui.fixture_thumbnail import export_fixture_thumbnail_packages
            packages = sorted((workspace / "decrypted").glob("mysekai__thumbnail__fixture__*"))
            report = export_fixture_thumbnail_packages(packages, _json(out / "mysekai-fixtures.json"), out / "fixture-thumbnails")
            return report["coverage"]
        step("fixture-thumbnails", thumbnail_outputs)

        def item_icon_outputs():
            from ui.item_icon import export_item_icons, package_paths
            masters = {key: (_json(out / name) if (out / name).is_file() else None)
                       for key, name in (("materials", "mysekai-materials.json"),
                                         ("items", "mysekai-items.json"),
                                         ("fixtures", "mysekai-fixtures.json"),
                                         ("tools", "mysekai-tools.json"))}
            report = export_item_icons(package_paths(workspace / "decrypted"),
                                       out / "mysekai-item-icons", **masters)
            return report["coverage"]
        step("mysekai-item-icons", item_icon_outputs)

        def aliases():
            for destination, original_name in (("fixture-areas", "fixture-interface"),
                                                ("fixture-particles-v2", "fixture-particles")):
                _copy_tree(out / original_name, out / destination)
            return {"aliases": ["fixture-areas", "fixture-particles-v2"]}
        step("runtime-resource-paths", aliases)
        from .runtime_contract import publish_runtime_aliases
        step("runtime-entrypoint-aliases-v1", lambda: publish_runtime_aliases(out))

        def conversation_paths():
            # The extractor's canonical source output and Moly's historical
            # reader location differ. Preserve one verified document, not a
            # second script parser or fabricated placeholder.
            source_talks = out / "fixture-talks/talks.json"
            destination = out / "fixture-talks/out/fixture-talks.json"
            data = _json(source_talks)
            if not isinstance(data.get("talks"), list):
                raise JPError("fixture conversation extraction has no authored talk list")
            write_json(destination, data, mode=PUBLIC_FILE_MODE)
            return {"talks": len(data["talks"]), "sourceSha256": hashlib.sha256(source_talks.read_bytes()).hexdigest()}
        step("canonical-conversation-paths-v1", conversation_paths)

        def actor_libraries():
            from chara.fixture_actor_libraries import export_fixture_actor_libraries
            # Revalidate each export job's revision/encoding/source identity.
            # An old aggregate "completed" report cannot admit float32 assets
            # under the new default, or a previous partial unit selection.
            receipt = export_fixture_actor_libraries(original, master_dir, workspace / "decrypted",
                workers=actor_workers, rotation_encoding=rotation_encoding,
                progress=(lambda row: progress("actor: " + json.dumps(row, ensure_ascii=False))) if progress else None)
            if receipt.get("failed") or receipt.get("completed") != receipt.get("requested"):
                raise JPError("furniture actor animation export is incomplete; inspect actor-animations/export-report.json")
            routes = _json(original / "actor-animations/index.json")
            if not routes.get("clipBindings"):
                raise JPError("no source-qualified actor motion routes were exported")
            files = set()
            for library in routes["libraries"]:
                for key, hash_key in (("glb", "glbSha256"), ("index", "indexSha256")):
                    relative = library[key]
                    path = (original / relative).resolve()
                    if not path.is_relative_to(original.resolve() / "actor-animations/generated") or path.is_symlink():
                        raise JPError("actor route is outside the generated source library")
                    if hashlib.sha256(path.read_bytes()).hexdigest() != library[hash_key]:
                        raise JPError("actor library receipt does not match the exported asset")
                    destination = out / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
                    files.add(relative)
            write_json(out / "actor-animations/index.json", routes, mode=PUBLIC_FILE_MODE)
            return {"libraries": len(routes["libraries"]), "clips": len(routes["clipBindings"]),
                    "files": len(files), **actor_storage}
        actor_storage = {"motionStorageRevision": MOTION_STORAGE_REVISION, "rotationEncoding": rotation_encoding}
        step("source-qualified-actor-libraries-v2", actor_libraries, cache_identity=actor_storage)

        def switch_motion():
            from chara.player_avatar import export_player_avatar
            from chara.sd_switch import export_sd_switch
            generic = out / "avatar/motion/mysekai__player_avatar.glb"
            if not generic.is_file():
                inputs = [workspace / "decrypted" / name for name in (
                    "virtual_live__avatar__model__default", "mysekai__player__motion__unique")]
                if not all(path.is_file() for path in inputs):
                    raise JPError("player gesture source is missing; jp download --root virtual_live/avatar/model/default --root mysekai/player/motion/unique")
                export_player_avatar([str(path) for path in inputs], str(generic.parent))
            return export_sd_switch(generic, out / "sd_104.glb", out / "motion-library.glb",
                                    out / "avatar/motion/sd-switch-simple.glb")
        step("source-player-switch-adaptation-v1", switch_motion)

        def fixture_animation_curves():
            from fixtures.animation_refresh import refresh_fixture_animations
            report = refresh_fixture_animations(out, workspace / "decrypted",
                progress=(lambda row: progress("fixture animation: " + json.dumps(row))) if progress else None)
            return {"packages": report["completed"]}
        step("source-fixture-animation-curves-v1", fixture_animation_curves)

        def fixture_presentation():
            from fixtures.presentation import extract_custom_geometry, enrich_presentation
            from ui.fixture_thumbnail import export_surface_thumbnail_routes
            geometry = extract_custom_geometry(workspace / "decrypted", out / "custom-fixture-models")
            if geometry.get("failed"):
                raise JPError("custom fixture source geometry extraction failed")
            report = enrich_presentation(master, out)
            thumbnails = out / "fixture-thumbnails/fixture-thumbnails.json"
            if thumbnails.is_file():
                export_surface_thumbnail_routes(out / "mysekai-fixtures.json", out, thumbnails.parent)
            return report
        step("fixture-presentation-v1", fixture_presentation)
        from fixtures.timeline_capabilities import build_capabilities
        step("fixture-timeline-capabilities-v1", lambda: {"prefabs": len(build_capabilities(out)["prefabs"])})

        def shared_ui():
            provenance, result = assemble_shared_ui(out, ui_from, region_ui)
            state["sharedUi"] = provenance
            return result
        region_ui = _region_ui_source(ui_jp_from)
        step("shared-application-ui-v2", shared_ui, cache_identity=region_ui[2])

        if audio:
            def sounds():
                from phenomena.audio_corpus import extract_audio_corpus
                general = _json(out / "talks.json")
                fixtures = _json(out / "fixture-talks/out/fixture-talks.json")
                combined = {"units": general.get("units", {}),
                            "talks": fixtures.get("talks", []),
                            "birthdayParty": general.get("birthdayParty", {})}
                request = workspace / "runtime-voice-requests.json"
                write_json(request, combined, mode=PUBLIC_FILE_MODE)
                result = extract_audio_corpus(request, descriptor_path, workspace / "decrypted", out / "phenomena",
                                              decoder=decoder, transcoder=transcoder,
                                              progress=(lambda row: progress("audio: " + json.dumps(row))) if progress else None)
                return {"families": result["families"], "partVoiceCues": result["partVoiceCues"]}
            step("runtime-sound-corpus-v3", sounds)
        checkpoint("validation")
        missing = [name for name in REQUIRED if not (out / name).is_file()]
        if missing:
            raise JPError("runtime is not ready; missing: " + ", ".join(missing))
        from fixtures.coverage import write_coverage
        _, coverage = write_coverage(out, region="jp", source_version=source["appVersion"])
        state["coverage"] = coverage.get("summary", {})
        from .runtime_validation import validate_runtime
        validation = validate_runtime(out, verify_actor_hashes=True)
        state["validation"] = validation
        if validation["missingResources"]:
            raise JPError("runtime has absent or damaged resources; inspect runtime-validation.json")
        # A source-level marker is a publication claim about the WHOLE spatial
        # tree, not merely newly exported fixtures. Legacy copied actors/sites
        # must be re-exported or independently migrated before this can pass.
        from .coordinate_contract import CONTRACT, UNITS, validate_snapshot
        coordinates = validate_snapshot(out)
        write_json(out / "coordinate-validation.json", coordinates, mode=PUBLIC_FILE_MODE)
        state["coordinateValidation"] = {"status": coordinates["status"],
                                         "checkedGlbs": coordinates["checkedGlbs"],
                                         "errors": len(coordinates["errors"])}
        if coordinates["errors"]:
            state["status"] = "failed"
            checkpoint("coordinate-validation: failed")
            raise JPError("runtime coordinates are not publication-ready; inspect coordinate-validation.json")
        state["status"] = "prepared"
        source_document = {"version": 1, "source": source, "master": {**master_source["identity"], "repository": master_source.get("repository"), "provider": master_source.get("provider")},
                           "sharedUi": state["sharedUi"], "runtimeStatus": "prepared",
                           "validationStatus": validation["status"],
                           "coordinateContract": CONTRACT, "coordinateUnits": UNITS}
        write_json(out / "source.json", source_document, mode=PUBLIC_FILE_MODE)
        checkpoint("prepared")
        return {"out": str(out), "identity": identity, "status": state["status"], "coverage": state["coverage"]}
