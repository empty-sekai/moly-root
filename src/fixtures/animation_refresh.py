"""Refresh fixture animation curves from their exact Timeline source objects.

Some releases put the clips beside the Timeline instead of in the model bundle.
Only the formal Fixture1 AnimationTrack contributes clips to the paired model
(and, for the gate's timeline package, to every gate model; see
:data:`GATE_TIMELINE_PACKAGE`); character, camera and other fixture slots are
never inferred from clip names.
"""
from pathlib import Path
import json

from core.assets.packages import PackageStore
from core.atomic import PUBLIC_FILE_MODE, write_json


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _identity(value):
    return (value.get("file"), str(value.get("pathId"))) if isinstance(value, dict) else None


def _tracks(rows):
    for row in rows:
        yield row
        yield from _tracks(row.get("children", []))


# The gate is one fixture whose shown model follows the gate and its skin (the
# assetbundleName of the gate tables), while its timelines come from one
# package whatever model is shown.  The client's gate controller
# (MysekaiGateController, its visitor coroutine PlayGateCharacterAppearTimeline)
# passes the same string literal as the timeline bundle for the two visitor
# timelines and the closing one, together with the placed gate's
# FixtureController; that literal is the only gate timeline bundle name the
# client holds.  The fixture timeline controller (SetupAsync and
# SetupWithoutNPCAsync, then SetUpView) loads the timeline and hands it that
# gate's view, and NPCFixtureTimelineView.BindFixture binds the timeline's
# "Fixture1" output to the view's Animator.  Nothing on that path looks at the
# skin, so the package's Fixture1 clips play on every gate model, not only on
# the model whose name the package's name pairs with.
GATE_TIMELINE_PACKAGE = "mysekai__fixture_timeline__mdl_non0006_gate_lon1"
GATE_MODEL_TABLES = ("mysekaiGates", "mysekaiGateUnitSkins", "mysekaiGateCommonSkins")


def _gate_models(root, models):
    """The exported models a gate can show, from the gate tables of the root."""
    path = root / "fixture-models/player-data.json"
    if not path.is_file():
        raise ValueError("the gate timeline's clips need the gate tables of "
                         "fixture-models/player-data.json to name the gate models")
    tables = _json(path)["tables"]
    names = {"mysekai__fixture__" + row["assetbundleName"]
             for table in GATE_MODEL_TABLES for row in tables[table]}
    return sorted(name for name in names if name in models)


def plan_animation_refresh(asset_root):
    root = Path(asset_root)
    models = _json(root / "fixture-models/index.json")["packages"]
    sources = {}
    for track_path in sorted((root / "fixture-timeline/tracks").glob("*.json")):
        package = track_path.stem.replace("mysekai__fixture_timeline__", "mysekai__fixture__", 1)
        receivers = {package} if package in models else set()
        if track_path.stem == GATE_TIMELINE_PACKAGE:
            receivers.update(_gate_models(root, models))
        if not receivers:
            continue
        data = _json(track_path)
        tracks = {_identity(row.get("asset")) for timeline in data.get("timelines", [])
                  for row in _tracks(timeline.get("tracks", []))
                  if row.get("class") == "AnimationTrack" and row.get("name") == "Fixture1"}
        targets_path = root / "fixture-timeline/clip-targets" / track_path.name
        if not tracks or not targets_path.is_file():
            continue
        for row in _json(targets_path).get("keyedClips", []):
            target = row.get("target")
            if _identity(row.get("track")) not in tracks or not target:
                continue
            source = target.get("sourceClip")
            if not source or not source.get("file") or not source.get("pathId"):
                raise ValueError("fixture Timeline clip has no exact source identity")
            for receiver in receivers:
                sources.setdefault(receiver, set()).add(
                    (target["targetPackage"], source["file"], str(source["pathId"])))
    result = []
    for package, model in sorted(models.items()):
        clips = model.get("animations", {}).get("clips", [])
        constant = any(clip.get("channels", 0) > 0 and clip.get("keys") == clip.get("channels") for clip in clips)
        external = any(source[0] != package for source in sources.get(package, []))
        if constant or external:
            result.append({"package": package, "constantPose": constant,
                           "externalClipSource": external,
                           "sources": sorted(sources.get(package, []))})
    return result


def refresh_fixture_animations(asset_root, bundles_root, *, packages=None, progress=None):
    from .meshes import _export_package
    root, bundles = Path(asset_root).resolve(), Path(bundles_root).resolve()
    plan = plan_animation_refresh(root)
    if packages is not None:
        plan = [row for row in plan if row["package"] in set(packages)]
    index_path = root / "fixture-models/index.json"
    index = _json(index_path)
    completed = []
    for row in plan:
        package = row["package"]
        store = PackageStore([bundles / package], root=bundles)
        model = _export_package(store, package, root / "fixture-models", animation_sources=row["sources"])
        if model.get("status") != "exported":
            raise ValueError(f"fixture animation refresh lost source geometry: {package}")
        index["packages"][package] = model
        completed.append({"package": package, "animations": model["animations"]})
        if progress:
            progress({"package": package, "completed": len(completed), "requested": len(plan)})
    write_json(index_path, index, mode=PUBLIC_FILE_MODE)
    report = {"version": 1, "packages": completed, "completed": len(completed)}
    write_json(root / "fixture-models/animation-refresh.json", report, mode=PUBLIC_FILE_MODE)
    return report
