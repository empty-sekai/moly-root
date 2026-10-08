"""Compact source requirements per authored Timeline prefab, without support claims."""
import hashlib
import json
from pathlib import Path

from core.atomic import PUBLIC_FILE_MODE, write_json


def _asset(value):
    return (value.get("file"), str(value.get("pathId"))) if isinstance(value, dict) else None


def _tracks(rows):
    for row in rows:
        yield row
        yield from _tracks(row.get("children", []))


def build_capabilities(asset_root):
    root = Path(asset_root)
    prefabs, sources = [], []
    for track_path in sorted((root / "fixture-timeline/tracks").glob("*.json")):
        clip_path = root / "fixture-timeline/clips" / track_path.name
        if not clip_path.is_file():
            continue
        tracks_raw, clips_raw = track_path.read_bytes(), clip_path.read_bytes()
        track_data, clip_data = json.loads(tracks_raw), json.loads(clips_raw)
        timelines = {_asset(row.get("asset")): row for row in track_data.get("timelines", [])}
        clips = {_asset(row.get("asset")): row for row in clip_data.get("tracks", [])}
        assets = clip_data.get("assets", [])
        sources.append({"package": track_path.stem,
                        "tracksSha256": hashlib.sha256(tracks_raw).hexdigest(),
                        "clipsSha256": hashlib.sha256(clips_raw).hexdigest()})
        for prefab in track_data.get("prefabs", []):
            classes, track_classes, count, missing = set(), set(), 0, []
            active = {_asset(row.get("director")) for row in prefab.get("fixtureTimelineViews", [])}
            for director in prefab.get("directors", []):
                if active and _asset(director.get("asset")) not in active:
                    continue
                timeline = timelines.get(_asset(director.get("timeline")))
                if timeline is None:
                    missing.append("director timeline is absent")
                    continue
                for track in _tracks(timeline.get("tracks", [])):
                    row = clips.get(_asset(track.get("asset")))
                    if row is None:
                        continue
                    for clip in row.get("clips", []):
                        reference = clip.get("assetRef")
                        if type(reference) is not int or not 0 <= reference < len(assets):
                            missing.append("clip payload is absent")
                            continue
                        count += 1
                        classes.add(assets[reference]["class"])
                        track_classes.add(track.get("class", ""))
            name = Path(prefab["container"]).stem
            prefabs.append({"package": track_path.stem, "prefab": prefab["container"],
                            "prefabName": name, "asset": prefab.get("asset"),
                            "clipClasses": sorted(classes), "trackClasses": sorted(track_classes),
                            "sourceClipCount": count, "missing": sorted(set(missing))})
    report = {"version": 1, "schema": "moly-timeline-capabilities/1",
              "semantics": "source payload requirements only; the runtime decides supported playback",
              "prefabs": prefabs, "sources": sources}
    write_json(root / "fixture-timeline/capabilities.json", report, mode=PUBLIC_FILE_MODE)
    return report
