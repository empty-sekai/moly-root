import json

from fixtures.timeline_capabilities import build_capabilities


def test_capabilities_follow_selected_director_and_nonempty_payloads(tmp_path):
    key = lambda i: {"file": "CAB", "pathId": str(i)}
    tracks = {"timelines": [{"asset": key(1), "tracks": [
        {"asset": key(10), "class": "AnimationTrack", "children": []},
        {"asset": key(11), "class": "UnknownEmptyTrack", "children": []},
    ]}, {"asset": key(2), "tracks": [
        {"asset": key(12), "class": "OtherTrack", "children": []}]}],
        "prefabs": [{"container": "source/tl_fixture.prefab", "asset": key(3),
                     "fixtureTimelineViews": [{"director": key(4)}],
                     "directors": [{"asset": key(4), "timeline": key(1)},
                                   {"asset": key(5), "timeline": key(2)}]}]}
    clips = {"tracks": [{"asset": key(10), "clips": [{"assetRef": 0}]},
                         {"asset": key(11), "clips": []},
                         {"asset": key(12), "clips": [{"assetRef": 1}]}],
             "assets": [{"class": "AnimationPlayableAsset"}, {"class": "UnsupportedClip"}]}
    for folder, document in (("tracks", tracks), ("clips", clips)):
        path = tmp_path / "fixture-timeline" / folder / "package.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(document))
    report = build_capabilities(tmp_path)
    assert report["prefabs"][0]["prefabName"] == "tl_fixture"
    assert report["prefabs"][0]["clipClasses"] == ["AnimationPlayableAsset"]
    assert report["prefabs"][0]["sourceClipCount"] == 1
    assert "supported" not in report["prefabs"][0]
