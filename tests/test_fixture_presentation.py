"""Room surfaces use the client's regex and color identity, not door materials."""
import json

import pytest

from fixtures.presentation import surface_bindings
from fixtures.animation_refresh import plan_animation_refresh


def test_surface_texture_color_and_uv_are_separate_and_need_no_material(tmp_path):
    names = ["textures/tex_env0008_wall_wall1_uvset2_1-12345678.png",
             "textures/tex_env0008_wall_wall1_uvset2_3-23456789.png",
             "textures/tex_env0008_floor_floor1_uvset1_1-34567890.png"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"image")
    rows = surface_bindings({"textures": names}, "env0008", "wall", [1, 3],
                            "tex_{0}_wall_wall1_uvset(.*)_{1}", tmp_path)
    assert [row["colorId"] for row in rows] == [1, 3]
    assert all(row["uvSet"] == 2 and row["texCoord"] == 1 for row in rows)
    assert rows[0]["shaderProperties"] == {"_BaseTextureMappingMode": 2}


def test_ambiguous_surface_is_rejected_instead_of_choosing_first(tmp_path):
    names = [f"textures/tex_skin_floor_floor1_uvset{uv}_1-12345678.png" for uv in (1, 2)]
    (tmp_path / "textures").mkdir()
    for name in names:
        (tmp_path / name).write_bytes(b"image")
    with pytest.raises(ValueError, match="ambiguous"):
        surface_bindings({"textures": names}, "skin", "floor", [1],
                         "tex_{0}_floor_floor1_uvset(.*)_{1}", tmp_path)


def test_fixture_refresh_joins_only_fixture_track_source_objects(tmp_path):
    package = "mysekai__fixture__mdl_test"
    timeline = "mysekai__fixture_timeline__mdl_test"
    values = {
        "fixture-models/index.json": {"packages": {package: {"animations": {"clips": []}}}},
        f"fixture-timeline/tracks/{timeline}.json": {"timelines": [{"tracks": [
            {"class": "AnimationTrack", "name": "Fixture1", "asset": {"file": "CAB", "pathId": "1"}},
            {"class": "AnimationTrack", "name": "CharacterAnimator", "asset": {"file": "CAB", "pathId": "2"}},
        ]}]},
        f"fixture-timeline/clip-targets/{timeline}.json": {"keyedClips": [
            {"track": {"file": "CAB", "pathId": str(track)}, "target": {
                "targetPackage": timeline, "sourceClip": {"file": "CAB", "pathId": str(track + 10)}}}
            for track in (1, 2)]},
    }
    for name, value in values.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    rows = plan_animation_refresh(tmp_path)
    assert rows == [{"package": package, "constantPose": False, "externalClipSource": True,
                     "sources": [(timeline, "CAB", "11")]}]
