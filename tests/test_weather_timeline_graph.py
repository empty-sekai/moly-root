"""Structural source tests: labels, cross-file PPtrs and malformed graphs."""
from copy import deepcopy
from phenomena.timeline import decode_timeline


class Record:
    def __init__(self, name):
        self.bundle = name
        self.archive = name + ".assets"
        self.trees = {}
        self.external_records = {}

    @property
    def externals(self):
        # PackageFile stores the serialized external-file name array separately
        # from the mock resolver's record map.
        return [self.external_records[i].archive for i in range(1, len(self.external_records) + 1)]

    def tree(self, path_id):
        return self.trees[path_id]

    def add(self, path_id, kind, **tree):
        script_id = path_id + 10000
        self.trees[script_id] = {"m_ClassName": kind}
        self.trees[path_id] = {"m_Script": ptr(script_id), **tree}


def ptr(path_id, file_id=0):
    return {"m_FileID": file_id, "m_PathID": path_id}


def follow(record, pointer):
    target = record if pointer.get("m_FileID", 0) == 0 else record.external_records.get(pointer["m_FileID"])
    path_id = pointer.get("m_PathID", 0)
    return (target, path_id) if target and path_id in target.trees else None


def graph():
    root, child = Record("root"), Record("external")
    root.external_records[1] = child
    child.external_records[1] = root
    root.add(100, "TimelineAsset", m_Name="environment", m_FixedDuration=3.0,
             m_DurationMode=1, m_EditorSettings={"m_Framerate": 60},
             m_Tracks=[ptr(200, 1), ptr(201, 1)])
    for parent, noise, target in ((200, 300, 1), (201, 301, 2)):
        child.add(parent, "SiteEnvironmentValueTrack", m_Name="same label",
                  m_Parent=ptr(100, 1), m_Children=[ptr(noise)], _scale=1.0,
                  _controlTarget=target, m_Clips=[])
        child.add(noise, "ValueNoiseTrack", m_Name="same noise label",
                  m_Parent=ptr(parent), m_Children=[], m_Clips=[])
    return root, child


def test_same_labels_do_not_determine_parent_identity():
    root, child = graph()
    document, gaps = decode_timeline(100, root, follow)
    assert not gaps
    assert document["schemaVersion"] == 3
    parents = [t["parentId"] for t in document["tracks"]]
    assert parents == [None, None, 0, 1]
    assert [t["source"]["pathId"] for t in document["tracks"]] == ["200", "201", "300", "301"]
    for tree in child.trees.values():
        tree["m_Name"] = "renamed"
    renamed, gaps = decode_timeline(100, root, follow)
    assert not gaps
    assert [t["parentId"] for t in renamed["tracks"]] == parents
    assert all("role" not in t for t in renamed["tracks"])


def test_clip_and_script_pointers_use_the_owning_file():
    root, child = graph()
    child.add(400, "SiteEnvironmentValueClip", _scale=0.7,
              _curve={"m_Curve": [], "m_PreInfinity": 2, "m_PostInfinity": 2})
    root.add(400, "WrongClipInRoot")
    child.trees[200]["m_Clips"] = [{"m_Asset": ptr(400), "m_Start": 0.2, "m_Duration": 0.4}]
    # Also resolve the MonoScript through an external serialized file.
    root.trees[900] = {"m_ClassName": "SiteEnvironmentValueClip"}
    child.trees[400]["m_Script"] = ptr(900, 1)
    document, gaps = decode_timeline(100, root, follow)
    assert not gaps
    clip = document["tracks"][0]["clips"][0]
    assert clip["class"] == "SiteEnvironmentValueClip"
    assert clip["asset"]["scale"] == 0.7
    assert clip["source"]["archive"] == "external.assets"


def test_cycles_and_conflicting_back_pointers_are_reported():
    root, child = graph()
    child.trees[300]["m_Children"] = [ptr(200)]
    child.trees[301]["m_Parent"] = ptr(200)
    document, gaps = decode_timeline(100, root, follow)
    assert len(document["tracks"]) == 4
    reasons = {gap["reason"] for gap in gaps}
    assert "duplicate or cyclic timeline track edge" in reasons
    assert "timeline parent/child PPtrs disagree" in reasons
    assert document["unsupported"] == gaps


def test_unresolved_markers_remain_explicit_gaps():
    root, child = graph()
    child.trees[200]["m_Markers"] = {"m_Objects": [ptr(999)]}
    document, gaps = decode_timeline(100, root, follow)
    marker = document["tracks"][0]["markers"][0]
    assert marker["sourceOrder"] == 0
    assert marker["reference"]["owner"] == document["tracks"][0]["source"]
    assert marker["reference"]["pointer"] == {"fileId": 0, "pathId": "999"}
    assert marker["reference"]["target"] is None
    assert any(gap["reason"] == "timeline marker behavior not modelled" for gap in gaps)


def test_equal_start_time_clip_order_is_not_sorted_by_display_label():
    root, child = graph()
    child.add(400, "SiteEnvironmentValueClip", _scale=1.0)
    child.trees[200]["m_Clips"] = [
        {"m_Asset": ptr(400), "m_Start": 0.2, "m_Duration": 0.4, "m_DisplayName": label}
        for label in ("z-first", "a-second")]
    document, gaps = decode_timeline(100, root, follow)
    assert not gaps
    assert [c["label"] for c in document["tracks"][0]["clips"]] == ["z-first", "a-second"]


def test_environment_director_is_resolved_from_controller_pointer():
    from types import SimpleNamespace
    from phenomena.controller import extract_environment_controller
    record = Record("controller")
    record.add(10, "SiteEnvironmentViewController", _playableDirector=ptr(20))
    record.trees[20] = {"m_Enabled": 1, "m_InitialState": 1, "m_WrapMode": 1,
                        "m_DirectorUpdateMode": 1, "m_InitialTime": 0.0}
    record.kinds = {10: "MonoBehaviour", 20: "PlayableDirector"}
    store = SimpleNamespace(package=lambda *args, **kw: SimpleNamespace(files=[record]),
                            follow=follow, load_dependencies=lambda _: None)
    data, gaps = extract_environment_controller(store)
    assert not gaps
    assert data["source"]["pathId"] == "10"
    assert data["director"]["source"]["pathId"] == "20"
    assert data["director"]["wrapMode"] == 1
    record.trees[10]["_playableDirector"] = ptr(999)
    data, gaps = extract_environment_controller(store)
    assert data is None and any("PPtr unresolved" in gap["reason"] for gap in gaps)


def test_missing_director_never_acquires_assumed_loop_settings():
    from types import SimpleNamespace
    from phenomena.controller import extract_environment_controller
    data, gaps = extract_environment_controller(SimpleNamespace(package=lambda *a, **k: None))
    assert data is None and gaps


def test_both_ends_of_every_edge_and_sibling_slots_are_retained():
    root, child = graph()
    document, gaps = decode_timeline(100, root, follow)
    assert not gaps
    assert document["rootReferences"][0]["owner"] == document["source"]
    assert document["rootReferences"][0]["pointer"] == {"fileId": 1, "pathId": "200"}
    assert document["rootReferences"][0]["external"] == "external.assets"
    assert [t["siblingIndex"] for t in document["tracks"]] == [0, 1, 0, 0]
    for track in document["tracks"]:
        assert track["reference"]["target"] == track["source"]
        if track["parentId"] is not None:
            parent = document["tracks"][track["parentId"]]
            assert track["parentReference"]["target"] == parent["source"]
            assert parent["childReferences"][track["siblingIndex"]] == track["reference"]
        else:
            assert track["parentReference"]["target"] == document["source"]


def test_unresolved_clip_keeps_its_original_owner_and_source_slot():
    root, child = graph()
    child.trees[200]["m_Clips"] = [{"m_Asset": ptr(-9223372036854775807, 1)}]
    document, gaps = decode_timeline(100, root, follow)
    gap = next(g for g in gaps if g["reason"] == "timeline clip asset not in this package")
    assert gap["sourceOrder"] == 0
    assert gap["reference"]["pointer"]["pathId"] == "-9223372036854775807"
    assert gap["reference"]["owner"] == document["tracks"][0]["source"]
    assert gap["reference"]["external"] == "root.assets"
    assert gap["reference"]["target"] is None
