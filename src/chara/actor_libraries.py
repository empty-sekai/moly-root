"""Index extracted actor clips by their source identity and actual target rig."""
import hashlib
import json
from pathlib import Path

from core.jsonio import write_json


def _gltf(path):
    data = path.read_bytes()
    if data[:4] != b"glTF" or data[16:20] != b"JSON":
        raise ValueError(f"not a binary glTF with a JSON chunk: {path.name}")
    length = int.from_bytes(data[12:16], "little")
    return json.loads(data[20:20 + length])


def _roots(document):
    scene = document["scenes"][document.get("scene", 0)]
    return [document["nodes"][index]["name"] for index in scene["nodes"]]


def build_actor_library_index(asset_root, library_root):
    """Join library reference roots to the same character manifest the host uses.

    This does not pick actors, substitute motions, or modify sampled poses.
    Playback facts stay in each v2 index, while this catalog supplies routes.
    """
    root = Path(asset_root).resolve()
    libraries_root = Path(library_root).resolve()
    if not libraries_root.is_relative_to(root):
        raise ValueError("actor libraries must remain inside the asset root")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    units = {}
    for entry in manifest["units"]:
        model = (root / entry["glb"]).resolve()
        if not model.is_relative_to(root):
            raise ValueError("character model escapes asset root")
        for name in _roots(_gltf(model)):
            # Character-pack codes and game-unit ids use the existing host's
            # unit+100 boundary. The reference root must match a real model.
            unit = int(entry["unit"]) - 100
            if unit <= 0 or name in units:
                raise ValueError(f"ambiguous character root: {name}")
            units[name] = unit
    binding_roots = _roots(_gltf(root / "motion-library.glb"))
    if len(binding_roots) != 1:
        raise ValueError("shared motion library must have one binding root")
    libraries, bindings, seen = [], [], set()
    for index_path in sorted(libraries_root.rglob("*.index.json")):
        if index_path.is_symlink() or not index_path.resolve().is_relative_to(libraries_root):
            raise ValueError("linked actor library index is not followed")
        document = json.loads(index_path.read_text(encoding="utf-8"))
        if document.get("version") != 2:
            raise ValueError(f"actor library needs source playback metadata v2: {index_path.name}")
        reference = document["binding"]["sourceRootName"]
        unit = units.get(reference)
        if unit is None or document["binding"]["bindingRootName"] != binding_roots[0]:
            raise ValueError(f"actor library has no matching character/binding root: {index_path.name}")
        glb_path = index_path.with_name(index_path.name.removesuffix(".index.json") + ".glb")
        geometry = _gltf(glb_path)
        animations = {animation["name"]: animation for animation in geometry.get("animations", [])}
        library = len(libraries)
        libraries.append({
            "unitId": unit, "sourceRootName": reference,
            "bindingRootName": binding_roots[0],
            "glb": glb_path.relative_to(root).as_posix(),
            "index": index_path.relative_to(root).as_posix(),
            "glbSha256": hashlib.sha256(glb_path.read_bytes()).hexdigest(),
            "indexSha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        })
        for family in document["clips"].values():
            for segment in family["segments"].values():
                source = segment["sourceClip"]
                key = (unit, source["file"], source["pathId"])
                if key in seen:
                    raise ValueError(f"ambiguous actor clip source identity: {key}")
                seen.add(key)
                animation = animations.get(segment["name"])
                if not animation or not animation.get("channels") or segment["sourceMetadataMissing"]:
                    raise ValueError(f"actor clip has incomplete curves/metadata: {segment['name']}")
                bindings.append({"unitId": unit, "sourceClip": source,
                                 "library": library, "clipName": segment["name"]})
    return write_json(libraries_root / "index.json", {
        "version": 1,
        "semantics": "routes only; source timing and looping live in the referenced v2 indexes",
        "libraries": libraries, "clipBindings": bindings,
    })
