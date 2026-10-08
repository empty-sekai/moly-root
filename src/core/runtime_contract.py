"""Static runtime resource entry points, independent from renderer internals."""
import json
import math
from pathlib import Path

from .atomic import PUBLIC_FILE_MODE, write_bytes


RUNTIME_ENTRYPOINTS = (
    "alone-actions.json", "avatar/motion/sd-switch-simple.glb", "birthday-parties.json",
    "camera/out/mysekai__camera.json", "characters.json", "client-config.json", "facial-tables.json",
    "fixture-areas/areas.json", "fixture-attach/attach-points.json", "fixture-gimmick/gimmicks.json",
    "fixture-models/index.json", "fixture-models/player-data.json", "fixture-particles-v2/index.json",
    "fixture-talks/out/fixture-talks.json", "fixture-thumbnails/fixture-thumbnails.json",
    "fixture-timeline/capabilities.json", "manifest.json", "motion-library.glb",
    "mysekai-blueprints.json", "mysekai-character-talk-action-points.json",
    "mysekai-character-talk-condition-groups.json", "mysekai-character-talk-conditions.json",
    "mysekai-character-talk-fixture-timelines.json", "mysekai-character-talk-no-talk-fixture-actions.json",
    "mysekai-character-talks.json", "mysekai-fixture-player-timelines.json", "mysekai-fixtures.json",
    "mysekai-game-character-unit-groups.json", "mysekai-items.json", "mysekai-music-records.json",
    "phenomena/001_sunny/postprocess.json", "phenomena/audio/corpus.json",
    "phenomena/audio/loop.json", "phenomena/audio/partvoice.json", "phenomena/index.json",
    "site-groups.json", "site/harvest.json", "site/index.json",
    "site/indoor/navigation/navigation_mesh/navigation_mesh.json", "site/packages.json", "site/sites.json",
    "talks.json", "tweet-tables.json", "tweets.json", "ui-layout-v2/runtime-sprites.json",
    "ui/audio/loop.json", "wordings.json",
)


def validate_camera(document):
    fields = document.get("fieldCameras")
    if not isinstance(fields, list) or len(fields) != 1 or fields[0].get("class") != "FieldCamera":
        raise ValueError("camera extraction must contain exactly one FieldCamera")
    field = fields[0]
    for key, table in (("setting", "cameraSettings"), ("cameraParam", "cameraParams")):
        identity = field.get(key)
        if not isinstance(identity, dict) or not identity.get("file") or identity.get("pathId") in (None, "0", 0):
            raise ValueError(f"FieldCamera has incomplete {key} identity")
        if sum(row.get("asset") == identity for row in document.get(table, [])) != 1:
            raise ValueError(f"FieldCamera {key} reference is unresolved or duplicated")
    view = field.get("view") or {}
    near, far = view.get("nearClipPlane"), view.get("farClipPlane")
    if view.get("orthographic") is not False or any(
        type(value) not in (float, int) or not math.isfinite(value) for value in (near, far)
    ) or not 0 < near < far:
        raise ValueError("FieldCamera projection is not a valid perspective camera")


def publish_runtime_aliases(asset_root):
    """Alias exact same-root source documents after checking their contracts."""
    root = Path(asset_root)
    aliases = (("camera/mysekai__camera.json", "camera/out/mysekai__camera.json"),
               ("fixture-interface/attach-points.json", "fixture-attach/attach-points.json"))
    rows = []
    for original, canonical in aliases:
        body = (root / original).read_bytes()
        document = json.loads(body)
        if original.startswith("camera/"):
            validate_camera(document)
        elif not isinstance(document.get("packages"), dict):
            raise ValueError("fixture attachment extraction lacks its package index")
        write_bytes(root / canonical, body, mode=PUBLIC_FILE_MODE)
        rows.append({"source": original, "runtimePath": canonical})
    return {"aliases": rows}
