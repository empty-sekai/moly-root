"""Check a runtime's published resources and report actual playback coverage."""
from pathlib import Path
import hashlib
import json

from .atomic import PUBLIC_FILE_MODE, write_json
from .runtime_contract import RUNTIME_ENTRYPOINTS, validate_camera


def validate_runtime(asset_root, *, verify_actor_hashes=False):
    root = Path(asset_root).resolve()
    missing = []

    def read(relative):
        path = root / relative
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            missing.append({"path": relative, "reason": type(exc).__name__})
            return {}

    def exists(relative):
        if not isinstance(relative, str):
            return False
        path = (root / relative).resolve()
        valid = path.is_relative_to(root) and path.is_file()
        if not valid:
            missing.append({"path": relative, "reason": "referenced resource is absent or outside root"})
        return valid

    for entrypoint in RUNTIME_ENTRYPOINTS:
        exists(entrypoint)
    camera = root / "camera/out/mysekai__camera.json"
    if camera.is_file():
        try:
            validate_camera(read("camera/out/mysekai__camera.json"))
        except ValueError as exc:
            missing.append({"path": "camera/out/mysekai__camera.json", "reason": str(exc)})

    models = read("fixture-models/index.json").get("packages", {})
    for model in models.values():
        if model.get("status") == "exported":
            exists("fixture-models/" + model["glb"])
    master = read("mysekai-fixtures.json")
    fixtures = master.get("fixtures", [])
    source_routes = 0
    for fixture in fixtures:
        preview = fixture.get("preview") or {}
        if preview.get("available"):
            source_routes += 1
        if preview.get("kind") == "surface":
            path = preview["sourceFile"]
            doc = read(path)
            for binding in doc.get("surfaceBindings", {}).get(preview["channel"], []):
                exists((Path(path).parent / binding["texture"]).as_posix())
        if preview.get("kind") == "custom":
            for variant in preview.get("variants", []):
                if variant.get("available"):
                    exists(variant["ornamentFile"])
    actors = read("actor-animations/index.json")
    for library in actors.get("libraries", []):
        for key in ("glb", "index"):
            if exists(library[key]) and verify_actor_hashes:
                actual = hashlib.sha256((root / library[key]).read_bytes()).hexdigest()
                if actual != library[key + "Sha256"]:
                    missing.append({"path": library[key], "reason": "actor receipt hash mismatch"})
    loop = read("phenomena/audio/loop.json")
    available_cues = set()
    for package in loop.get("packages", []):
        for stream in package.get("streams", []):
            file = stream.get("ogg") or stream.get("wav")
            if file and exists("phenomena/" + file):
                available_cues.add(stream["cue"])
    general = read("talks.json")
    furniture = read("fixture-talks/out/fixture-talks.json")
    talks = furniture.get("talks", []) + [talk for unit in general.get("units", {}).values()
                                          for talk in unit.get("talks", [])]
    requested_cues = {cue for talk in talks for cue in talk.get("voices", [])}
    absent_cues = sorted(requested_cues - available_cues)
    report = {"version": 1, "region": master.get("region"), "gameVersion": master.get("gameVersion"),
              "status": "failed" if missing else "ready-with-source-gaps" if absent_cues else "ready",
              "fixtures": {"definitions": len(fixtures), "sourcePresentationRoutes": source_routes},
              "actors": {"libraries": len(actors.get("libraries", [])),
                         "clipBindings": len(actors.get("clipBindings", [])),
                         "hashesVerified": verify_actor_hashes},
              "audio": {"requestedCues": len(requested_cues),
                        "availableCues": len(requested_cues & available_cues), "missingCues": absent_cues},
              "missingResources": missing}
    write_json(root / "runtime-validation.json", report, mode=PUBLIC_FILE_MODE)
    return report
