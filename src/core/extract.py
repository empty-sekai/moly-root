"""Manifest-driven extraction orchestration."""
import json
import os
from pathlib import Path

from chara.rotation_encoding import DEFAULT_ROTATION_ENCODING
from .assets.manifest import parse_manifest
from .assets.router import route
from .master import record_master_inputs
from .player_resources import is_loose_player_data, player_data_paths

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
                             "timeline families (plus the site-move, foot and "
                             "harvest-object effect packages and the travel "
                             "cannon), so it is opt-in via fixture_particles=True")

# The particle-source pass covers the three furniture families and, with the
# same document schema, the three site effect families (the site-move effects,
# the player's foot effects and the harvest-object effects, whose emitters the
# runtime reads through this schema).  Of the site domain it covers the travel
# cannon, named here, and every package whose timelines drive a
# ParticleSystem (a scene director's Control clips and Signal reactions, a step
# item's own timeline; see :mod:`fixtures.timeline_reach`), found by that reach
# over every package of the run rather than by name.  A domain-wide claim on
# ``site`` would export every scene's particles; the scenes' own play-on-awake
# systems stay the scene reader's.
PARTICLE_DOMAINS = ("fixture-interface", "cutscene-timeline", "fixture-timeline",
                    "site-action-effect", "site-common-action-effect",
                    "site-harvest-object-effect", "site-home-effect",
                    "fixture-put-effect")
PARTICLE_PACKAGES = ("mysekai__site__move__cannon",)
# Of the site domain it also covers every harvest prop and field object
# (``mysekai/site/field/object/<prefab>``): the rocks, trees, flowers, drops and
# treasure boxes carry their own emitters, which the runtime installs by package
# name through this schema.  Selected by the prefix, so a prop added under it is
# covered; the prefix names no scene, so the scenes stay out.
PARTICLE_PACKAGE_PREFIXES = ("mysekai__site__field__object__",)

# The site effect families, each exported by its own function of
# :mod:`sites.action_effects` into its own directory with its own index:
# ``(routed domain, function, output directory)``.
EFFECT_FAMILY_JOBS = (
    ("site-action-effect", "extract_site_action_effects", "site-action-effects"),
    ("site-common-action-effect", "extract_site_common_action_effects",
     "site-common-action-effects"),
    ("site-harvest-object-effect", "extract_site_harvest_object_effects",
     "site-harvest-object-effects"),
    ("site-home-effect", "extract_site_home_effects", "site-home-effects"),
    ("fixture-put-effect", "extract_fixture_put_effects", "fixture-put-effects"),
)

# Prefabs the game loads by an exact name out of a package whose domain is read
# for something else, or that no other reader claims.  FieldCameraManager's
# SetupFieldCamera instantiates the asset FieldCamera of the camera package (the
# field camera, carrying the speed-line canvas of the site move); the door
# transitioner manager's Init loads SiteTransitionerNormal from the house
# transition package.  Each is exported with the site package reader.
FIELD_CAMERA_PREFAB = "fieldcamera.prefab"
HOUSE_TRANSITION_PREFAB = "sitetransitionernormal.prefab"

# A prefab the player build ships in its Resources: the live transitioner, the
# cover shown while the game enters MySekai (UIUtility.PlayLiveTransition loads
# it by this name).  Its MonoBehaviours carry no typetree in the player data, so
# reading it needs the same build's managed assembly stubs as well.
LIVE_TRANSITIONER_LOAD_NAME = "Common/Transition/LiveTransitioner"

# The version a bundle whose header carries none is read against.  Several
# extractor modules assign this at import, which is enough when the caller comes
# through the command line and nothing else, but the jobs that run before any of
# them is imported would then read with nothing set at all.
from core.unity import (  # re-exported: callers import it from here
    DEFAULT_UNITY_VERSION,
    configure_fallback_unity_version,
)


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
    "communication": {"TextAsset"},
    "talk": {"TextAsset"},
    "talk-constants": {"TextAsset"},
    "camera": {"MonoBehaviour", "PlayableDirector"},
    "fixture-reaction": {"MonoBehaviour"},
    # The waving motion a party plays as it closes: the list is one
    # ScriptableObject, so the domain declares that one behaviour type.
    "birthday-end-motion": {"MonoBehaviour"},
    "fixture-interface": {"MonoBehaviour", "Mesh", "MeshFilter", "MeshRenderer",
                          "SkinnedMeshRenderer", "Material", "Texture2D"},
    "cutscene-timeline": {"MonoBehaviour", "PlayableDirector", "AnimationClip"},
    "fixture-timeline": {"MonoBehaviour", "PlayableDirector", "AnimationClip"},
    "avatar-part": {"Mesh", "MeshFilter", "MeshRenderer", "Material", "Texture2D",
                    "Shader"},
    "harvest-tool": {"Mesh", "MeshFilter", "MeshRenderer", "Material", "Texture2D",
                     "Shader"},
    "player-tool-model": {"Mesh", "MeshFilter", "MeshRenderer", "Material", "Texture2D",
                          "Shader"},
    "site-action-effect": {"ParticleSystem", "ParticleSystemRenderer", "Mesh",
                           "MeshFilter", "MeshRenderer", "Material", "Texture2D",
                           "MonoBehaviour"},
    "site-common-action-effect": {"ParticleSystem", "ParticleSystemRenderer", "Mesh",
                                  "MeshFilter", "MeshRenderer", "Material", "Texture2D",
                                  "MonoBehaviour"},
    "site-harvest-object-effect": {"ParticleSystem", "ParticleSystemRenderer", "Mesh",
                                   "MeshFilter", "MeshRenderer", "Material",
                                   "Texture2D", "MonoBehaviour"},
    "site-home-effect": {"ParticleSystem", "ParticleSystemRenderer", "Mesh",
                         "MeshFilter", "MeshRenderer", "Material", "Texture2D",
                         "MonoBehaviour"},
    "fixture-put-effect": {"ParticleSystem", "ParticleSystemRenderer", "Mesh",
                           "MeshFilter", "MeshRenderer", "Material", "Texture2D",
                           "MonoBehaviour"},
    "house-transition": {"Canvas", "MonoBehaviour", "Material", "Texture2D", "Sprite",
                         "Animator", "AnimationClip"},
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
    "fixture-gimmick/gimmicks.json": {
        "fixture-interface": {"Animator", "AnimatorController",
                              "AnimatorOverrideController", "AnimationClip"},
    },
    # The field camera prefab is a second read of the camera package, through
    # the site package reader; its behaviours are already the camera domain's.
    "field-camera/": {
        "camera": {"Camera", "Canvas", "Material"},
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
    return configure_fallback_unity_version(unity_version)

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
    "communication-actions.json",
    "talks.json",
    "tweets.json",
    "emoticons/emoticons.json",
    "avatar-parts/avatar-parts.json",
    "avatar/tools/index.json",
    "avatar/player-tool-models/index.json",
    "site-action-effects/index.json",
    "site-common-action-effects/index.json",
    "site-harvest-object-effects/index.json",
    "site-home-effects/index.json",
    "fixture-put-effects/index.json",
    "phenomena/index.json",
    "site/index.json",
    "site/harvest.json",
    "site-sound-wave/site-sound-wave.json",
    "field-camera/",
    "house-transition/",
    "live-transitioner/",
    "effect-resources/",
    "graphics-config/",
    "layout-grid/",
    "fixture-interface/attach-points.json",
    "fixture-interface/areas.json",
    "fixture-reactions.json",
    "birthday-end-character-motions.json",
    "fixture-models/",
    "fixture-particles/index.json",
    "fixture-gimmick/gimmicks.json",
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
    "ui/atlas.json",
    "ui/atlas/textures/",
    "site/sitemap/screen_layer/screen_layer.json",
    "ui/atlas/sprites/",
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


# Why the container-only UI readers skip a loose-file player data.
LOOSE_PLAYER_UI = ("the player data is loose files in the APKs, not one container; "
                   "the dialogue UI, atlas and screen-layer readers take a container, "
                   "and this layout's UI documents are read by `ui-layout --region jp`")

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


def _sitemap_screen_layer_artifact(out, player_data):
    """Build ``site/sitemap/screen_layer/screen_layer.json`` from player data.

    The world-map screen's layout lives in the APK player data (the screen
    layer is fetched with Resources.Load, not from a bundle), so without the
    caller-supplied file the artifact is ``skipped`` *with the reason* -- the
    same distinction the other player-data artifacts keep.
    """
    entry = {"artifact": "site/sitemap/screen_layer/screen_layer.json",
             "domain": "site", "status": "skipped", "counts": {},
             "error": "no player-data file supplied; the screen-layer layout "
                      "is built into the APK player data, which is not a "
                      "downloadable package"}
    if not player_data:
        return entry
    try:
        from sites.screen_layer import extract_screen_layer
        if not Path(player_data).exists():
            raise FileNotFoundError(f"player data not found: {player_data}")
        result = extract_screen_layer(str(player_data),
                                      out / "site" / "sitemap" / "screen_layer")
        entry.update(status="succeeded", error="", counts=result["counts"],
                     path=result["path"])
    except Exception as exc:
        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return entry


def _ui_atlas_artifact(out, player_data):
    """Build ``ui/atlas.json`` and the atlas images from the player-data file.

    Like the dialogue-UI artifact, the sprite atlases live in the APK's player
    data and in no downloadable package, so the input path has to come from the
    caller and without it the artifact is ``skipped`` *with the reason* rather
    than left out.  Every atlas is decoded and inventoried, and the counts
    carry how many sprite rectangles matched the packed rect the sprite
    itself records, plus how many were trimmed by tight packing.
    """
    entry = {"artifact": "ui/atlas.json", "domain": "ui", "status": "skipped",
             "counts": {},
             "error": "no player-data file supplied; the UI sprite atlases are "
                      "built into the APK player data, which is not a "
                      "downloadable package and cannot be routed by bundle name"}
    if not player_data:
        return entry
    try:
        import ui.atlas as atlas
        if not Path(player_data).exists():
            raise FileNotFoundError(f"player data not found: {player_data}")
        summary = atlas.extract_atlas(str(player_data), out / "ui" / "atlas")
        entry.update(status="succeeded", error="", counts=summary,
                     path=str(out / "ui" / "atlas" / "atlas.json"))
    except Exception as exc:
        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return entry


def _live_transitioner_artifact(out, player_data, player_types):
    """Build ``live-transitioner/`` from the player data's Resources.

    The live transitioner (see :data:`LIVE_TRANSITIONER_LOAD_NAME`) is loaded
    from the player build's Resources, not from any downloadable package, so
    like the other player-data artifacts it is ``skipped`` *with the reason*
    when the file is not supplied.  It has a second input the other artifacts
    do not: its MonoBehaviours carry no typetree in the player data, and their
    fields are read with a typetree generated from the same build's managed
    assembly stubs, so without *player_types* it is skipped with that reason.
    """
    entry = {"artifact": "live-transitioner/", "domain": "resource-prefab",
             "status": "skipped", "counts": {},
             "error": "no player-data file supplied; the live transitioner is "
                      "loaded from the APK player data's Resources, which is not "
                      "a downloadable package"}
    if not player_data:
        return entry
    if not player_types:
        entry["error"] = ("no managed assembly stubs supplied (player_types); the "
                          "live transitioner's behaviours carry no typetree in the "
                          "player data and are read with one generated from the "
                          "same build's stubs")
        return entry
    try:
        from .resource_exports import export_resource_prefab
        missing = [path for path in player_data if not Path(path).is_file()]
        if missing:
            raise FileNotFoundError(f"player data file not found: {', '.join(missing)}")
        document = export_resource_prefab(player_data, str(player_types),
                                          LIVE_TRANSITIONER_LOAD_NAME,
                                          out / "live-transitioner")
        objects = document.get("objects") or {}
        counts = {"objects": objects.get("total", 0),
                  "exported": objects.get("exported", 0),
                  "skipped": objects.get("skipped", 0),
                  "unsupported": len(document.get("unsupported") or []),
                  "particles": len(document.get("particles") or []),
                  "textures": len(document.get("textures") or [])}
        entry.update(status="succeeded", error="", counts=counts,
                     path=document["file"])
    except Exception as exc:
        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return entry


def _resources_entry(artifact, what, player_data, player_types):
    """A Resources-object artifact's entry, or ``None`` when it can run.

    Like the live transitioner, these objects are loaded from the player data's
    Resources, and their MonoBehaviour fields are read with a typetree generated
    from the build's assembly stubs, so each input left out is its own reason.
    """
    entry = {"artifact": artifact, "domain": "resource-object", "status": "skipped",
             "counts": {},
             "error": (f"no player-data file supplied; {what} is loaded from the APK "
                       "player data's Resources, which is not a downloadable package")}
    if not player_data:
        return entry
    if not player_types:
        entry["error"] = (f"no managed assembly stubs supplied (player_types); {what} "
                          "carries no typetree in the player data and is read with one "
                          "generated from the same build's stubs")
        return entry
    missing = [path for path in player_data if not Path(path).is_file()]
    if missing:
        entry.update(status="failed",
                     error=f"FileNotFoundError: player data file not found: {', '.join(missing)}")
        return entry
    return None


def _effect_resources_artifact(out, player_data, player_types, declarations, bundles):
    """Build ``effect-resources/`` -- the effect manager's type table.

    The table (effect type -> bundle, prefab, pool size) is in no package; the
    effect manager loads it from Resources.  With *declarations* each type's
    declared name is attached; each row's bundle and prefab is checked against
    the run's bundle directory, and a row whose bundle is not there is listed
    under ``missing`` (an input gap, not a guessed row).
    """
    what = "the effect manager's type table"
    entry = _resources_entry("effect-resources/", what, player_data, player_types)
    if entry is not None:
        return entry
    entry = {"artifact": "effect-resources/", "domain": "resource-object",
             "status": "failed", "counts": {}, "error": ""}
    try:
        from .resource_exports import export_effect_resources
        path = out / "effect-resources" / "effect-resources.json"
        summary = export_effect_resources(player_data, str(player_types), path,
                                          declarations=declarations,
                                          bundle_root=str(bundles))
        document = json.loads(path.read_text(encoding="utf-8"))
        absent = sorted(row["assetBundleName"] for row in document["resources"]
                        if row["bundle"] is not None and not row["bundle"]["onDisk"])
        counts = {key: summary[key] for key in ("rows", "duplicates", "bundlesChecked",
                                                "bundlesOnDisk", "prefabsFound")}
        counts["namedTypes"] = sum(1 for row in document["resources"]
                                   if row["effectTypeName"] is not None)
        entry.update(status="succeeded", counts=counts, path=str(path))
        if absent:
            entry["missing"] = absent
            entry["error"] = ("rows whose bundle is not in the bundle directory: "
                              + ", ".join(absent))
        if not declarations:
            entry["error"] = (entry["error"] + "; " if entry["error"] else "") + (
                "no declaration dump supplied (player_declarations): effect type "
                "names are null, the numbers are exact")
    except Exception as exc:
        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return entry


def _graphics_config_artifact(out, player_data, player_types):
    """Build ``graphics-config/drop-item-shade.json``,
    ``graphics-config/stencil-shadow.json``, ``graphics-config/npc-dither.json``
    and ``graphics-config/site-object-dither.json`` from the player data.

    The drop-item shading values are a field of the MySekai graphics
    configuration, a ScriptableObject in the player data's Resources; the
    document names the global shader property each value is written to. The
    stencil-shadow document carries the same object's character stencil-shadow
    colour and its per-attribute material rendering settings. The NPC dither
    document carries the four distances of the NPC view's camera dither, the
    site-object dither document the distances and far alpha of the site
    objects' and tree fixtures' camera dither.
    """
    what = "the graphics configuration"
    entry = _resources_entry("graphics-config/", what, player_data, player_types)
    if entry is not None:
        return entry
    entry = {"artifact": "graphics-config/", "domain": "resource-object",
             "status": "failed", "counts": {}, "error": ""}
    try:
        from .resource_exports import (export_drop_item_shade, export_npc_dither,
                                       export_site_object_dither, export_stencil_shadow)
        path = out / "graphics-config" / "drop-item-shade.json"
        summary = export_drop_item_shade(player_data, str(player_types), path)
        stencil = export_stencil_shadow(player_data, str(player_types),
                                        out / "graphics-config" / "stencil-shadow.json")
        dither = export_npc_dither(player_data, str(player_types),
                                   out / "graphics-config" / "npc-dither.json")
        site_dither = export_site_object_dither(
            player_data, str(player_types), out / "graphics-config" / "site-object-dither.json")
        entry.update(status="succeeded", path=str(path),
                     counts={"values": summary["values"],
                             "byteSize": summary["byteSize"],
                             "stencilShadowAttributes": stencil["attributes"],
                             "npcDitherValues": dither["values"],
                             "siteObjectDitherValues": site_dither["values"]})
    except Exception as exc:
        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return entry


def _layout_grid_artifact(out, player_data, player_types):
    """Build ``layout-grid/grid.json`` -- the layout editor's placement grid.

    The grid settings (line sizes, offsets, a material per grid part) and the
    colour profile table are ScriptableObjects in the player data's Resources;
    each material's shader pass state and GL program come with them.
    """
    what = "the layout grid's settings and colour profiles"
    entry = _resources_entry("layout-grid/", what, player_data, player_types)
    if entry is not None:
        return entry
    entry = {"artifact": "layout-grid/", "domain": "resource-object",
             "status": "failed", "counts": {}, "error": ""}
    try:
        from .layout_grid import export_layout_grid
        summary = export_layout_grid(player_data, str(player_types),
                                     out / "layout-grid")
        entry.update(status="succeeded", path=summary["path"],
                     counts={key: summary[key] for key in
                             ("materials", "emptySlots", "shaders", "programs", "profiles")})
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


@record_master_inputs
def extract_manifest(manifest, bundles, out, unity_version=None, master=None,
                     player_data=None,
                     fixture_meshes=False,
                     fixture_particles=False,
                     builtin_resources=None,
                     master_cache=None, vgmstream=None, ffmpeg=None,
                     accept_input_gaps=(), shader_compiler=None,
                     rotation_encoding=DEFAULT_ROTATION_ENCODING,
                     player_types=None, player_declarations=None):
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

    *player_data* is the caller-supplied APK player data: the container file,
    or the APKs of a build whose player data is loose files (see
    :func:`core.player_resources.player_data_paths`).  The dialogue UI
    lives there and in no downloadable package, so it is not a routed domain at
    all; without the path its artifact is likewise reported as skipped with the
    reason, never left out.

    *player_types* is the directory of the same player build's managed assembly
    stubs.  The live transitioner prefab, the effect manager's type table and
    the graphics configuration's drop-item shading are read out of the player
    data's Resources, whose MonoBehaviours carry no typetree, so their fields
    are read with a typetree generated from these stubs; without them those
    artifacts are skipped with the reason, never left out.

    *player_declarations* is the same build's declaration dump; it only names
    the effect types in the type table (the numbers are exact without it, and
    the entry says so).

    Furniture controller/event metadata is always written for the routed
    furniture packages in this run, using the same opened PackageStore as
    the interface pass. No placement list or independent package discovery
    is used. Unsupported controllers and unresolved bindings stay in that
    metadata and are counted separately from extraction failures.

    *fixture_meshes* asks for the furniture geometry pass, which writes one glTF
    binary per package of the whole fixture family into ``fixture-models/``.  It
    is off by default because that is the heaviest artifact the pipeline makes;
    off, its entry is reported ``skipped`` with the reason, never omitted, so a
    reader can tell "nobody asked for it" from "this domain does not exist".

    *fixture_particles* asks for the furniture particle-emitter pass, which
    writes one document per particle-emitting package of the fixture-interface,
    cutscene-timeline and fixture-timeline families, of the site-move and foot
    effect families, of the travel cannon and of the site's field objects
    (:data:`PARTICLE_DOMAINS`, :data:`PARTICLE_PACKAGES`,
    :data:`PARTICLE_PACKAGE_PREFIXES`) into ``fixture-particles/``.
    It is off by default for the same reason as the geometry pass; off, its
    entry is likewise reported ``skipped`` with the reason, never omitted.

    *builtin_resources* names the engine's own built-in resource containers (a
    container file, or a directory holding one), resolved with
    :func:`core.assets.packages.builtin_archive_paths` by the caller.  Some
    particle-system renderers of the fixture/cut-scene family, and some emitters
    of the phenomena family, draw a copy of a built-in primitive mesh that no
    package ships; without this, those mesh slots are reported unresolved
    rather than left unparsed.

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
    from chara.motion_storage import validate_rotation_encoding
    validate_rotation_encoding(rotation_encoding)
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
    # Each avatar-part package is fully self-contained (own textures, own
    # materials, own in-package shaders, probed) so unlike the phenomena or
    # site families there is no cross-package lookup pass -- but the family
    # still writes one shared index across every package extracted this run,
    # so, like the overhead-item packages above, every package is collected
    # first and the job runs once over all of them.
    avatar_part_paths = {}
    avatar_part_errors = {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "avatar-part":
            continue
        try:
            avatar_part_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            avatar_part_errors[name] = f"{type(exc).__name__}: {exc}"
    avatar_part_result = None
    avatar_part_error = None
    if avatar_part_paths:
        try:
            from chara.avatar_parts import extract_avatar_parts
            avatar_part_result = extract_avatar_parts(
                [str(path) for path in avatar_part_paths.values()],
                str(out / "avatar-parts"))
        except Exception as exc:
            avatar_part_error = f"{type(exc).__name__}: {exc}"
    # Harvest tools share the package reader with scenes, but remain a player
    # domain with their own source-prefab identity and persistent index.
    harvest_tool_paths, harvest_tool_errors = {}, {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "harvest-tool":
            continue
        try:
            harvest_tool_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            harvest_tool_errors[name] = f"{type(exc).__name__}: {exc}"
    harvest_tool_result, harvest_tool_error = None, None
    if harvest_tool_paths:
        try:
            from chara.harvest_tools import extract_harvest_tools
            harvest_tool_result = extract_harvest_tools(
                [str(path) for path in harvest_tool_paths.values()],
                str(out / "avatar" / "tools"), bundle_root=bundles)
        except Exception as exc:
            harvest_tool_error = f"{type(exc).__name__}: {exc}"
    # The avatar's hand items share the tool reader and index shape, in their
    # own directory.
    player_tool_paths, player_tool_errors = {}, {}
    for name in names:
        target = route(name)
        if target is None or target.domain != "player-tool-model":
            continue
        try:
            player_tool_paths[name] = _bundle_path(bundles, name)
        except Exception as exc:
            player_tool_errors[name] = f"{type(exc).__name__}: {exc}"
    player_tool_result, player_tool_error = None, None
    if player_tool_paths:
        try:
            from chara.harvest_tools import extract_player_tool_models
            player_tool_result = extract_player_tool_models(
                [str(path) for path in player_tool_paths.values()],
                str(out / "avatar" / "player-tool-models"), bundle_root=bundles)
        except Exception as exc:
            player_tool_error = f"{type(exc).__name__}: {exc}"
    # The site effect families share the package reader with the site packages
    # (the travel cannon's particle rows come from the same walk); each family
    # is extracted together so its index is written once.  One job per family,
    # in this table's order.
    effect_jobs = {}
    for domain, function, directory in EFFECT_FAMILY_JOBS:
        paths, errors = {}, {}
        for name in names:
            target = route(name)
            if target is None or target.domain != domain:
                continue
            try:
                paths[name] = _bundle_path(bundles, name)
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
        result, error = None, None
        if paths:
            try:
                from sites import action_effects
                result = getattr(action_effects, function)(
                    [str(path) for path in paths.values()],
                    str(out / directory), bundle_root=bundles)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        effect_jobs[domain] = {"errors": errors, "result": result, "error": error}
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
            # The environment effects draw built-in primitives too: some of
            # their particle renderers take their mesh from the engine's own
            # containers, which no package ships -- the same shape the particle
            # pass below loads ``builtin_resources`` for, so the same resolved
            # list is handed to this job as extra pointer targets.
            phenomena_result = extract_phenomena(
                [str(path) for path in phenomena_paths.values()],
                str(out / "phenomena"), bundle_root=bundles, master=master,
                master_cache=master_cache, vgmstream=vgmstream, ffmpeg=ffmpeg,
                extra_archives=builtin_resources, shader_compiler=shader_compiler)
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
    # The footstep surface of each site scene (the mesh its site view's
    # ``_soundWaveMeshFilter`` names) is a second read of the scene packages the
    # site job was given, with its own document and its own outcome: the scene
    # geometry keeps the pointer only, and this pass resolves it.
    sound_wave_scenes = {}
    if site_paths:
        from sites.census import scene_name
        sound_wave_scenes = {name: path for name, path in site_paths.items()
                             if scene_name(name) is not None}
    sound_wave_result, sound_wave_error = None, None
    if sound_wave_scenes:
        try:
            from sites.sound_wave import extract_site_sound_wave
            sound_wave_result = extract_site_sound_wave(
                [str(path) for path in sound_wave_scenes.values()],
                out / "site-sound-wave" / "site-sound-wave.json", bundle_root=bundles)
        except Exception as exc:
            sound_wave_error = f"{type(exc).__name__}: {exc}"
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
    fixture_gimmick_result = None
    fixture_gimmick_error = None
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
        # Controller metadata is another view of these same source packages,
        # not an opt-in geometry job or a consumer-specific on/off whitelist.
        # Pass only the manifest/discovery selection, not dependency packages
        # the shared store may have opened while resolving references.
        try:
            from core.assets.packages import PackageStore
            from fixtures.gimmick import extract as extract_gimmicks
            if fixture_store is None:
                fixture_store = PackageStore(
                    [str(path) for path in fixture_paths.values()], root=bundles)
            fixture_gimmick_result = extract_gimmicks(
                fixture_store, list(fixture_paths),
                out / "fixture-gimmick" / "gimmicks.json")
        except Exception as exc:
            fixture_gimmick_error = f"{type(exc).__name__}: {exc}"
        # Geometry is a separate read of this same package family, and it reads
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
    # Particle emitters are a second-order read over the furniture families --
    # fixture-interface, cutscene-timeline and fixture-timeline -- because a
    # package's ParticleSystem and ParticleSystemRenderer components are read
    # across the same families whose attach points, tracks and clips are each
    # read as their own job elsewhere in this function; the same schema also
    # carries the site-move and foot effect families and the travel cannon
    # (:data:`PARTICLE_DOMAINS`, :data:`PARTICLE_PACKAGES`).  The
    # ``fixture_store`` opened above only holds the fixture-interface family,
    # so it is not reused here; a second, wider store is opened instead, which
    # pays the fixture-interface packages' read cost a second time.
    # fixtures.particles.extract_from_store does its own per-domain filtering
    # and counting once every target package is in the store, so this driver
    # only gathers their paths rather than re-implementing that filtering.  The
    # domain stays ``fixture-interface`` in the report (the furniture family is
    # the subject of the pass), the same reason the geometry entry keeps it.
    particle_paths = {}
    named_particle_packages = set(PARTICLE_PACKAGES)
    reach_candidates = []
    for name in names:
        target = route(name)
        named = name in PARTICLE_PACKAGES or name.startswith(PARTICLE_PACKAGE_PREFIXES)
        covered = target is not None and (target.domain in PARTICLE_DOMAINS or named)
        try:
            path = _bundle_path(bundles, name)
        except Exception:
            continue
        if covered:
            particle_paths[name] = path
            if named:
                named_particle_packages.add(name)
        else:
            reach_candidates.append(path)
    particle_asked = bool(fixture_paths or particle_paths)
    timeline_reach = None
    if fixture_particles and particle_paths:
        try:
            from core.assets.packages import PackageStore
            from fixtures.interface import extract_particles
            from fixtures.timeline_reach import reached_packages
            # The packages outside the pass's domains whose timelines drive a
            # ParticleSystem (every other package of the run is read for it).
            timeline_reach = reached_packages(reach_candidates, bundles)
            reach_packages = tuple(sorted(name for name in timeline_reach
                                          if name not in particle_paths))
            for name in reach_packages:
                particle_paths[name] = _bundle_path(bundles, name)
            # A renderer's material slots, and a mesh-drawn renderer's own
            # mesh, commonly point into a companion package this domain's own
            # selection never names on its own: an ``mysekai__effect__*``
            # package supplies the fixture and cut-scene family's materials and
            # meshes, and the shared ``mysekai__shader`` package supplies their
            # shaders.  The router does not assign most of them to one of this
            # pass's domains, and discovery may not have selected them into
            # *names* at all, so the bundle directory is scanned by name
            # directly; every match that is not itself a target is loaded to
            # serve as a pointer target and is never extracted.
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
                particle_store, str(out / "fixture-particles"),
                shader_compiler=shader_compiler, domains=PARTICLE_DOMAINS,
                packages=tuple(sorted(named_particle_packages | set(reach_packages))))
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
    # The camera package feeds a second reader: the field camera prefab the
    # site scene instantiates (see FIELD_CAMERA_PREFAB) is exported with the
    # site package reader, its canvas and material included.  Its outcome is
    # its own derived entry, so a failure there is not read as a failure of the
    # camera curves, nor hidden behind their success.
    field_camera_asked = bool(camera_paths or camera_errors)
    field_camera_result, field_camera_error = None, None
    if field_camera_asked:
        try:
            if not camera_paths:
                raise FileNotFoundError("; ".join(sorted(camera_errors.values())))
            from sites.prefab_package import extract_prefab_package
            field_camera_result = extract_prefab_package(
                str(next(iter(camera_paths.values()))), FIELD_CAMERA_PREFAB,
                str(out / "field-camera"), bundle_root=bundles)
        except Exception as exc:
            field_camera_error = f"{type(exc).__name__}: {exc}"
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
    # The party scripts ride in the same document as the ordinary corpus but
    # are selected by a table of their own, which a master partition may lack.
    # Their outcome is a row of its own for the same reason as the furniture
    # family's: a partition without the table must read as an input gap, not
    # as a talk document that happens to hold no party.
    birthday_talks_asked = False
    birthday_talks_result = None
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
                characters = [n for n in names if route(n) and route(n).domain == "character"]
                if not characters:
                    raise ValueError("motion extraction requires a character reference bundle in manifest")
                # The characters' AnimatorControllers name the clips they play
                # by serialized identity; same-name duplicates in the motion
                # package are resolved by that reference, never by listing order.
                result = export_motion_library(str(_bundle_path(bundles, characters[0])), str(bundle), str(out),
                                               rotation_encoding=rotation_encoding,
                                               controller_bundles=[str(_bundle_path(bundles, n))
                                                                   for n in characters])
            elif target.domain == "emoticon":
                if name in emoticon_errors:
                    raise FileNotFoundError(emoticon_errors[name])
                if emoticon_error:
                    raise RuntimeError(emoticon_error)
                result = emoticon_result or {}
            elif target.domain == "avatar-part":
                if name in avatar_part_errors:
                    raise FileNotFoundError(avatar_part_errors[name])
                if avatar_part_error:
                    raise RuntimeError(avatar_part_error)
                result = avatar_part_result or {}
            elif target.domain == "harvest-tool":
                if name in harvest_tool_errors:
                    raise FileNotFoundError(harvest_tool_errors[name])
                if harvest_tool_error:
                    raise RuntimeError(harvest_tool_error)
                result = dict((harvest_tool_result or {}).get("perBundle", {}).get(name, {}))
                if result.get("status") != "succeeded":
                    raise RuntimeError(result.get("error", "harvest-tool package did not produce a result"))
                result["index"] = harvest_tool_result["path"]
            elif target.domain == "player-tool-model":
                if name in player_tool_errors:
                    raise FileNotFoundError(player_tool_errors[name])
                if player_tool_error:
                    raise RuntimeError(player_tool_error)
                result = dict((player_tool_result or {}).get("perBundle", {}).get(name, {}))
                if result.get("status") != "succeeded":
                    raise RuntimeError(result.get(
                        "error", "player-tool-model package did not produce a result"))
                result["index"] = player_tool_result["path"]
            elif target.domain in effect_jobs:
                job = effect_jobs[target.domain]
                if name in job["errors"]:
                    raise FileNotFoundError(job["errors"][name])
                if job["error"]:
                    raise RuntimeError(job["error"])
                result = dict((job["result"] or {}).get("perBundle", {}).get(name, {}))
                if result.get("status") != "succeeded":
                    raise RuntimeError(result.get(
                        "error", f"{target.domain} package did not produce a result"))
                result["index"] = job["result"]["path"]
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
            elif target.domain == "fixture-reaction":
                from fixtures.reactions import extract_fixture_reactions
                result = extract_fixture_reactions(str(bundle), out / "fixture-reactions.json")
            elif target.domain == "birthday-end-motion":
                from sites.birthday_end_motions import extract_birthday_end_motions
                result = extract_birthday_end_motions(
                    str(bundle), out / "birthday-end-character-motions.json")
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
            elif target.domain == "house-transition":
                # One package, one prefab the door transitioner loads by name
                # (HOUSE_TRANSITION_PREFAB); the package's other prefab is kept
                # as a scene of its own by the reader.
                from sites.prefab_package import extract_prefab_package
                result = extract_prefab_package(str(bundle), HOUSE_TRANSITION_PREFAB,
                                                str(out / "house-transition"),
                                                bundle_root=bundles)
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
                birthday_talks_asked = True
                birthday_talks_result = (result or {}).get("birthdayParty")
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
            elif target.domain == "communication":
                if not master:
                    raise ValueError("communication actions require master tables")
                from chara.communication_actions import extract_communication_actions
                result = extract_communication_actions(master, str(bundle), str(out / "communication-actions.json"), master_cache)
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
    if sound_wave_scenes:
        failed_scenes = (sound_wave_result or {}).get("failed") or {}
        report["derived"].append({
            "artifact": "site-sound-wave/site-sound-wave.json",
            "domain": "site",
            "status": "failed" if (sound_wave_error or failed_scenes) else "succeeded",
            "counts": {k: v for k, v in (sound_wave_result or {}).items()
                       if isinstance(v, int)},
            "error": sound_wave_error or "; ".join(
                f"{name}: {error}" for name, error in sorted(failed_scenes.items()))})
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
    # Always present when the run selected a package the pass covers, never
    # omitted, for the same reason as the geometry entry above:
    # skipped-with-a-reason when the caller did not ask for the particle-emitter
    # pass, otherwise the outcome of the pass -- and the same
    # failed-not-succeeded shape for a pass that produced no counts at all (see
    # ``_optional_pass_status``).
    if particle_asked:
        particle_counts = {k: v for k, v in (fixture_particle_result or {}).items()
                           if isinstance(v, int)}
        particle_status, particle_error = _optional_pass_status(
            fixture_particles, FIXTURE_PARTICLES_SKIPPED, fixture_particle_error,
            particle_counts)
        report["derived"].append({
            "artifact": "fixture-particles/index.json",
            "domain": "fixture-interface",
            "status": particle_status,
            "sourcePrograms": (fixture_particle_result or {}).get("sourcePrograms"),
            "timelineReach": {"candidates": len(reach_candidates), "reached": timeline_reach},
            "counts": particle_counts,
            "error": particle_error})
    if fixture_paths or fixture_errors:
        gimmick_packages = (fixture_gimmick_result or {}).get("packages", [])
        unresolved_controllers = [
            {"package": package["name"], "source": controller.get("source"),
             "status": controller.get("status"),
             "motionMapping": (controller.get("motionMapping") or {}).get("status")}
            for package in gimmick_packages
            for controller in package.get("controllers", [])
            if controller.get("status") != "decoded"
            or (controller.get("motionMapping") or {}).get("status") != "resolved"]
        gimmick_error = fixture_gimmick_error or (
            "; ".join(f"{name}: {error}" for name, error in sorted(fixture_errors.items()))
            if fixture_errors else
            "controller pass returned no catalog" if fixture_gimmick_result is None else "")
        report["derived"].append({
            "artifact": "fixture-gimmick/gimmicks.json",
            "domain": "fixture-interface",
            "status": "failed" if gimmick_error else "succeeded",
            "counts": {
                "requestedPackages": len(fixture_paths) + len(fixture_errors),
                "packages": len(gimmick_packages),
                "controllers": sum(p["coverage"]["controllerCount"] for p in gimmick_packages),
                "clips": sum(p["coverage"]["clipCount"] for p in gimmick_packages),
                "events": sum(p["coverage"]["eventCount"] for p in gimmick_packages),
                "decodedCurveSlots": sum(p["coverage"]["decodedCurveSlots"] for p in gimmick_packages),
                "unresolvedCurveSlots": sum(p["coverage"]["unresolvedCurveSlots"] for p in gimmick_packages),
                "unresolvedControllers": len(unresolved_controllers)},
            "packageCoverage": {p["name"]: p["coverage"] for p in gimmick_packages},
            "unresolvedControllers": unresolved_controllers,
            "missingDependencies": sorted(fixture_errors),
            "error": gimmick_error})
    if birthday_talks_asked:
        party = birthday_talks_result or {}
        if not party:
            party_status = "failed"
            party_error = ("the talk extraction returned no account of the "
                           "birthday-party block")
        elif party.get("status") == "absent":
            party_status = "skipped"
            party_error = ("the master tables carry no birthdayPartyScenarios; "
                           "talks.json holds the block as absent")
        else:
            party_status, party_error = "succeeded", ""
        report["derived"].append({
            "artifact": "talks.json#birthdayParty",
            "status": party_status,
            "counts": {k: v for k, v in party.items() if isinstance(v, int)},
            "error": party_error})
    if fixture_talks_asked:
        report["derived"].append({
            "artifact": "fixture-talks/talks.json",
            "status": "failed" if fixture_talks_error else "succeeded",
            "counts": {k: v for k, v in (fixture_talks_result or {}).items()
                       if isinstance(v, int)},
            "error": fixture_talks_error or ""})
    if field_camera_asked:
        report["derived"].append({
            "artifact": "field-camera/",
            "domain": "camera",
            "status": "failed" if field_camera_error else "succeeded",
            "counts": {k: v for k, v in (field_camera_result or {}).items()
                       if isinstance(v, int)},
            "path": (field_camera_result or {}).get("json"),
            "error": field_camera_error or ""})
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
    # A build whose player data is loose files in its APKs has no container
    # for the UI readers below, which take one; its UI documents are read by
    # the region's own layout reader, so those three say so and are skipped.
    player_paths = player_data_paths(player_data)
    loose = bool(player_paths) and is_loose_player_data(player_paths)
    container = player_paths[0] if player_paths and not loose else None
    ui_rows = [_ui_artifact(out, container), _ui_atlas_artifact(out, container),
               _sitemap_screen_layer_artifact(out, container)]
    if loose:
        for row in ui_rows:
            row["error"] = LOOSE_PLAYER_UI
    report["playerData"] = [*ui_rows,
                            _live_transitioner_artifact(out, player_paths, player_types),
                            _effect_resources_artifact(out, player_paths, player_types,
                                                       player_declarations, bundles),
                            _graphics_config_artifact(out, player_paths, player_types),
                            _layout_grid_artifact(out, player_paths, player_types)]
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
