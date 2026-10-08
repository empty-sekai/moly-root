"""Actor storage has explicit encoding, stable source binding and cache identity."""
import copy
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from chara import fixture_actor_libraries as actors, motion_library as motion
from chara.motion_storage import MOTION_STORAGE_REVISION, MotionStorage
from chara.rotation_encoding import DEFAULT_ROTATION_ENCODING, encode_rotation_stream
from core.gltf import GLB


def accessor_bytes(glb, index):
    accessor = glb.g["accessors"][index]
    view = glb.g["bufferViews"][accessor["bufferView"]]
    start = view["byteOffset"]
    return ({k: v for k, v in accessor.items() if k != "bufferView"},
            bytes(glb.bin[start:start + view["byteLength"]]))


def animation_values(glb):
    animations = copy.deepcopy(glb.g["animations"])
    for animation in animations:
        for sampler in animation["samplers"]:
            for field in ("input", "output"):
                sampler[field] = accessor_bytes(glb, sampler[field])
    return animations


def test_accessor_identity_includes_every_layout_field_and_exact_float_bytes():
    glb = GLB()
    storage = MotionStorage(glb)
    payload = struct.pack("<4f", 0., 0., 0., 1.)
    first = storage.acc(payload, 5126, "VEC4", 1)
    assert storage.acc(bytes(bytearray(payload)), 5126, "VEC4", 1) == first
    variants = [(5125, "VEC4", 1, None, None),
                (5126, "SCALAR", 4, None, None),
                (5126, "SCALAR", 3, None, None),
                (5126, "VEC4", 1, 34962, None),
                (5126, "VEC4", 1, None, ([0., 0., 0., 1.], [0., 0., 0., 1.]))]
    indexes = {first}
    for ctype, atype, count, target, bounds in variants:
        indexes.add(storage.acc(payload, ctype, atype, count, target=target, minmax=bounds))
    indexes.add(storage.acc(struct.pack("<4f", -0., 0., 0., 1.), 5126, "VEC4", 1))
    indexes.add(storage.acc(struct.pack("<4f", 0., -0., -0., -1.), 5126, "VEC4", 1))
    assert len(indexes) == 8
    # Identical bytes with different bound spellings are not the same accessor.
    assert storage.acc(payload, 5126, "SCALAR", 4, minmax=([0.], [1.])) != storage.acc(
        payload, 5126, "SCALAR", 4, minmax=([-0.], [1.]))
    assert accessor_bytes(glb, first)[1] == payload


def _synthetic_export(monkeypatch, tmp_path, storage_type=MotionStorage, **options):
    nodes = [{"name": "Root", "t": (0., 0., 0.), "q": (0., 0., 0., 1.),
              "s": (1., 1., 1.), "parent": -1}]
    rig = SimpleNamespace(host_node=lambda bone: "Root" if bone == "Hips" else bone)
    monkeypatch.setattr(motion, "_reference_skeleton", lambda *args: (rig, nodes, {"Root": 0}, {}))
    monkeypatch.setattr(motion, "pose_root", lambda rig, muscles, body_q, body_p, **kw: (body_p, body_q))
    frames = [(0., {}, (0., 0., 0., 1.), (0., 0., 0.), {}, {}),
              (1., {}, (0., -.6, 0., -.8), (0., .25, 0.), {}, {})]
    samples = {name: {"rate": 1., "frames": frames, "sourceMetadata": {
        "sourceClip": {"file": "CAB-source", "pathId": str(i)},
        "sourceStartTime": 0., "sourceStopTime": 1., "sourceLoopTime": False,
        "sourceMetadataMissing": [], "sourceClipName": "same original name"}}
        for i, name in enumerate(("first_O", "second_O"), 1)}
    monkeypatch.setattr(motion, "discover_and_sample", lambda *args, **kw: (samples, []))
    monkeypatch.setattr(motion, "MotionStorage", storage_type)
    report = motion.export_motion_library("ref", "motions", tmp_path, **options)
    assert report["failed"] == 0
    blob = Path(report["glb"]).read_bytes()
    size = struct.unpack_from("<I", blob, 12)[0]
    result = SimpleNamespace(g=json.loads(blob[20:20 + size]), bin=blob[28 + size:])
    return result, json.loads(Path(report["index"]).read_text(encoding="utf-8"))


def test_export_shares_across_clips_without_changing_channels_metadata_or_values(monkeypatch, tmp_path):
    compact, index = _synthetic_export(monkeypatch, tmp_path / "compact", rotation_encoding="float32")
    # GLB.acc is the exact previous writer, with no interning or quantization.
    class OriginalStorage:
        def __init__(self, glb):
            self.acc = glb.acc

        def rotation(self, data, count, *, rotation_encoding):
            assert rotation_encoding == "float32"
            return self.acc(data, 5126, "VEC4", count)

    original, original_index = _synthetic_export(monkeypatch, tmp_path / "original", OriginalStorage,
                                                 rotation_encoding="float32")
    assert animation_values(compact) == animation_values(original)
    assert index == original_index
    assert compact.g["asset"] == original.g["asset"]
    assert compact.g["nodes"] == original.g["nodes"]
    assert len(compact.g["animations"]) == 2
    assert len(compact.g["accessors"]) == 3
    assert len(original.g["accessors"]) == 6
    assert len(compact.bin) * 2 == len(original.bin)
    assert index["motionStorageRevision"] == MOTION_STORAGE_REVISION
    # A new GLB has an independent table, even if all stream bytes are equal.
    another, _ = _synthetic_export(monkeypatch, tmp_path / "another", rotation_encoding="float32")
    assert another.bin == compact.bin
    assert another.g == compact.g


def test_normalized_metadata_is_part_of_intern_identity():
    glb = GLB()
    storage = MotionStorage(glb)
    payload = struct.pack("<4h", 0, 0, 0, 32767)
    bare = storage.acc(payload, 5122, "VEC4", 1)
    normalized = storage.acc(payload, 5122, "VEC4", 1, normalized=True)
    plain = storage.acc(payload, 5122, "VEC4", 1, normalized=False)
    assert len({bare, normalized, plain}) == 3
    assert storage.acc(payload, 5122, "VEC4", 1, normalized=True) == normalized
    assert "normalized" not in glb.g["accessors"][bare]
    assert glb.g["accessors"][normalized]["normalized"] is True
    assert glb.g["accessors"][plain]["normalized"] is False
    with pytest.raises(ValueError, match="normalized"):
        storage.acc(payload, 5122, "VEC4", 1, normalized=1)


def test_default_snorm16_only_changes_rotation_output_storage(monkeypatch, tmp_path):
    encoded, encoded_index = _synthetic_export(monkeypatch, tmp_path / "encoded")
    original, original_index = _synthetic_export(monkeypatch, tmp_path / "original",
                                                 rotation_encoding="float32")
    assert DEFAULT_ROTATION_ENCODING == "snorm16"
    assert encoded_index["rotationEncoding"] == "snorm16"
    assert original_index["rotationEncoding"] == "float32"
    assert encoded.g["asset"]["extras"]["rotationEncoding"] == "snorm16"
    assert encoded.g["asset"]["extras"]["motionStorageRevision"] == MOTION_STORAGE_REVISION
    assert {k: v for k, v in encoded_index.items() if k != "rotationEncoding"} == {
        k: v for k, v in original_index.items() if k != "rotationEncoding"}
    assert encoded.g["nodes"] == original.g["nodes"]
    assert encoded.g["animations"] == original.g["animations"]
    assert len(encoded.bin) < len(original.bin)
    for animation in encoded.g["animations"]:
        for channel in animation["channels"]:
            sampler = animation["samplers"][channel["sampler"]]
            assert sampler["interpolation"] == "LINEAR"
            assert accessor_bytes(encoded, sampler["input"]) == accessor_bytes(original, sampler["input"])
            layout, payload = accessor_bytes(encoded, sampler["output"])
            old_layout, old_payload = accessor_bytes(original, sampler["output"])
            if channel["target"]["path"] == "rotation":
                assert layout["normalized"] is True and layout["componentType"] == 5122
                assert old_layout["componentType"] == 5126 and "normalized" not in old_layout
                assert payload == encode_rotation_stream(old_payload)[0]
                assert len(payload) * 2 == len(old_payload)
                # Do not canonicalize the negative authored quaternion hemisphere.
                assert struct.unpack_from("<4h", payload, 8)[3] < 0
            else:
                assert (layout, payload) == (old_layout, old_payload)


def test_bad_rotation_or_encoding_never_silently_falls_back_or_writes(tmp_path):
    from core.extract import extract_manifest
    glb = GLB()
    storage = MotionStorage(glb)
    for payload in (struct.pack("<4f", 0, 0, 0, 0), struct.pack("<4f", float("nan"), 0, 0, 1)):
        with pytest.raises(ValueError):
            storage.rotation(payload, 1)
    with pytest.raises(ValueError, match="count"):
        storage.rotation(struct.pack("<4f", 0, 0, 0, 1), 2)
    assert not glb.bin and not glb.g["accessors"]
    for value in ("invalid", None, 16):
        with pytest.raises(ValueError, match="rotation_encoding"):
            motion.export_motion_library("absent", "absent", tmp_path / "out", rotation_encoding=value)
        with pytest.raises(ValueError, match="rotation_encoding"):
            actors.export_fixture_actor_libraries(tmp_path / "out", "absent", "absent", rotation_encoding=value)
        with pytest.raises(ValueError, match="rotation_encoding"):
            actors.build_plan("absent", "absent", "absent", rotation_encoding=value)
        with pytest.raises(ValueError, match="rotation_encoding"):
            extract_manifest("absent", "absent", tmp_path / "out", rotation_encoding=value)
    assert not (tmp_path / "out").exists()


def test_actor_job_propagates_encoding_and_checks_receipt_metadata(monkeypatch, tmp_path):
    job = {"unit": 1, "package": "source-package", "reference": "reference", "bundle": "bundle",
           "bindingRoot": "Root", "clips": [{"name": "clip_O", "file": "CAB-source", "pathId": "1"}],
           "identity": "float32-job", "motionStorageRevision": MOTION_STORAGE_REVISION,
           "rotationEncoding": "float32"}
    calls = []

    def export(reference, bundle, output, name, **options):
        calls.append(options)
        Path(output, "actor.glb").write_bytes(b"verified synthetic curves")
        Path(output, "actor.index.json").write_text(json.dumps({"clips": {
            "clip": {"segments": {"O": {"sourceClip": {"file": "CAB-source", "pathId": "1"}}}}}}),
            encoding="utf-8")
        return {"failed": 0, "exported": 1, "elapsedSeconds": .1, "glbBytes": 25}

    monkeypatch.setattr(actors, "export_motion_library", export)
    output = tmp_path / "generated"
    assert actors._run_job(job, output, "2022.3.62f3")["status"] == "exported"
    folder = output / "unit-01" / "source-package"
    receipt = json.loads((folder / "export-source.json").read_text(encoding="utf-8"))
    assert calls[0]["rotation_encoding"] == receipt["rotationEncoding"] == "float32"
    assert receipt["motionStorageRevision"] == MOTION_STORAGE_REVISION
    assert actors._run_job(job, output, "2022.3.62f3")["status"] == "cached"
    assert len(calls) == 1
    assert not actors._receipt_valid(folder, job["identity"], rotation_encoding="snorm16")
    with pytest.raises(ValueError, match="stale"):
        actors._run_job({**job, "motionStorageRevision": MOTION_STORAGE_REVISION - 1}, output, "2022.3.62f3")


@pytest.mark.parametrize("override", [[], ["--rotation-encoding", "float32"]])
def test_export_cli_forwards_rotation_encoding(monkeypatch, override):
    from core import cli, extract, runtime_pack
    expected = "float32" if override else "snorm16"
    monkeypatch.setattr(cli, "_configure_unity", lambda _: None)
    calls = []

    def exported(*args, **kwargs):
        calls.append(kwargs["rotation_encoding"])
        return {"failed": 0, "results": [], "summary": {"failed": 0}}

    monkeypatch.setattr(motion, "export_motion_library", exported)
    monkeypatch.setattr(actors, "export_fixture_actor_libraries", exported)
    monkeypatch.setattr(extract, "extract_manifest", exported)
    monkeypatch.setattr(runtime_pack, "prepare_runtime", exported)
    commands = [
        ["motion-library", "--reference-bundle", "reference", "--motion-bundle", "source", "--out-dir", "out"],
        ["fixture-actors", "--assets", "assets", "--master", "master", "--bundles", "bundles"],
        ["extract", "--bundles", "bundles", "--json"],
        ["jp", "prepare", "--manifest", "manifest", "--workspace", "workspace", "--out", "out", "--ui-from", "ui",
         "--ui-jp-from", "ui-jp"],
    ]
    for command in commands:
        assert cli.main(command + override) == 0
    assert calls == [expected] * 4


def test_actor_job_identity_invalidates_storage_revision_without_changing_pose(monkeypatch, tmp_path):
    root, master, bundles = (tmp_path / n for n in ("assets", "master", "bundles"))
    for directory in (root, master, bundles):
        directory.mkdir()
    logical = "tl_ext0002_fixture_coffeemaker1_01"
    package = "mysekai__fixture_timeline__mdl_ext0002_fixture_coffeemaker1"
    pointer = lambda n: {"file": "CAB-source", "pathId": str(n)}
    documents = {
        root / "manifest.json": {"units": [{"unit": 101}]},
        master / "mysekaiCharacterTalkFixtureTimelines.json": [{"groupId": 1, "assetbundleName": logical}],
        root / "fixture-timeline" / "tracks" / f"{package}.json": {
            "timelines": [{"asset": pointer(1), "tracks": [
                {"class": "AnimationTrack", "name": "CharacterAnimator", "asset": pointer(2)}]}],
            "prefabs": [{"container": logical + ".prefab", "directors": [{"timeline": pointer(1)}]}]},
        root / "fixture-timeline" / "clip-targets" / f"{package}.json": {"keyedClips": [
            {"track": pointer(2), "target": {"sourceClip": pointer(3), "targetPackage": package,
                                           "clipName": "source_O"}}]},
    }
    for path, document in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
    for name in ("mysekai__character__mdl_sd_101_001", package):
        (bundles / name).write_bytes(b"source bundle identity")
    monkeypatch.setattr(actors, "_gltf", lambda _: {"nodes": [{"name": "Root"}], "scenes": [{"nodes": [0]}]})
    monkeypatch.setattr(actors, "_source_relations", lambda _: [(1, 1)])
    original = actors.build_plan(root, master, bundles)
    float32 = actors.build_plan(root, master, bundles, rotation_encoding="float32")
    assert original["jobs"][0]["rotationEncoding"] == "snorm16"
    assert float32["jobs"][0]["rotationEncoding"] == "float32"
    assert original["jobs"][0]["identity"] != float32["jobs"][0]["identity"]
    monkeypatch.setattr(actors, "MOTION_STORAGE_REVISION", MOTION_STORAGE_REVISION + 1)
    changed = actors.build_plan(root, master, bundles)
    assert len(original["jobs"]) == len(changed["jobs"]) == 1
    assert original["jobs"][0]["identity"] != changed["jobs"][0]["identity"]
    assert original["jobs"][0]["clips"] == changed["jobs"][0]["clips"]
    assert actors.POSE_BINDING_REVISION == motion.POSE_BINDING_REVISION == 2
