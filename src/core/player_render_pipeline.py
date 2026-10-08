"""Strict extractor for the player-shipped MYSEKAI render pipeline.

This module reads the renderer configuration from caller-supplied Android player
data. It starts at ResourceManager entries and follows serialized PPtrs; no
MYSEKAI renderer GUID, path id, feature order, queue, or render-pass event is
hard-coded here.

UnityPy's generated managed typetree is useful for script fields, but its
generated MonoBehaviour inheritance header is not authoritative for the native
serialized object layout. The source player serializes the engine-native
MonoBehaviour header first. We therefore splice the native header for the exact
Unity version onto the generated script fields, and fail if their inherited
field names do not agree. String arrays also need the generated node's outer
type corrected to vector so UnityPy consumes the serialized array rather than
one string.

The result is evidence, not a renderer implementation: complete serialized
trees, resolved MonoScript identity, renderer slots, feature objects, source
file hashes and byte sizes are preserved so downstream code need not guess.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import zipfile

import UnityPy
from UnityPy.enums import ClassIDType
from UnityPy.helpers.Tpk import get_typetree_node
from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
from UnityPy.helpers.UnityVersion import UnityVersion

from .unity import configure_fallback_unity_version


PIPELINE_RESOURCE = "mysekairenderpipelineasset"
GRAPHICS_CONFIG_RESOURCE = "graphics/data/mysekaigraphicsconfig"
DEFAULT_RESOURCES = (PIPELINE_RESOURCE, GRAPHICS_CONFIG_RESOURCE)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strip_live_children(items):
    """Drop live children before TypeTreeNode.from_list reconstructs them."""
    return [{k: v for k, v in item.items() if k != "m_Children"} for item in items]


def _fix_generated_string_arrays(root) -> int:
    """Correct managed string[] nodes generated as an outer string."""
    changed = 0
    for node in root.traverse():
        children = getattr(node, "m_Children", ())
        if getattr(node, "m_Type", None) != "string" or len(children) != 1:
            continue
        array = children[0]
        achildren = getattr(array, "m_Children", ())
        if (getattr(array, "m_Type", None) == "Array"
                and len(achildren) == 2
                and getattr(achildren[1], "m_Type", None) == "string"):
            node.m_Type = "vector"
            changed += 1
    return changed


def _merge_native_monobehaviour_header(original, native):
    """Replace generated inheritance bytes with the engine-native header."""
    _fix_generated_string_arrays(original)
    prefix = [node.m_Name for node in native.m_Children]
    generated_prefix = [node.m_Name for node in original.m_Children[:len(prefix)]]
    if generated_prefix != prefix:
        raise ValueError(
            "generated MonoBehaviour inheritance does not match native header: "
            f"generated={generated_prefix!r} native={prefix!r}"
        )

    flat = [original.to_dict_list()[0]]
    flat.extend(native.to_dict_list()[1:])
    for child in original.m_Children[len(prefix):]:
        flat.extend(child.to_dict_list())
    return type(original).from_list(_strip_live_children(flat))


class NativeMonoBehaviourTypeTreeGenerator(TypeTreeGenerator):
    """Managed type generator using the exact native MonoBehaviour header."""

    def __init__(self, unity_version: str):
        super().__init__(unity_version)
        self._moly_unity_version = unity_version

    def get_nodes_up(self, assembly, fullname):
        original = super().get_nodes_up(assembly, fullname)
        native = get_typetree_node(
            ClassIDType.MonoBehaviour,
            UnityVersion.from_str(self._moly_unity_version),
        )
        return _merge_native_monobehaviour_header(original, native)


@dataclass
class _Source:
    player_data: Path
    types: Path
    asset_pack: Path
    unity_version: str

    def __post_init__(self):
        for path, label in (
            (self.player_data, "player data"),
            (self.types, "decoded managed assemblies"),
            (self.asset_pack, "asset-pack APK"),
        ):
            if not path.exists():
                raise FileNotFoundError(f"{label} not found: {path}")
        base_paths = [
            self.player_data / "globalgamemanagers",
            self.player_data / "globalgamemanagers.assets",
        ]
        for path in base_paths:
            if not path.is_file():
                raise FileNotFoundError(f"required player-data file not found: {path}")

        configure_fallback_unity_version(self.unity_version)
        self.generator = NativeMonoBehaviourTypeTreeGenerator(self.unity_version)
        self.generator.load_local_dll_folder(str(self.types))

        self.environment = UnityPy.load(*(str(path) for path in base_paths))
        self.environment.typetree_generator = self.generator
        self.archive = zipfile.ZipFile(self.asset_pack)
        self.loaded = {
            obj.assets_file.name: obj.assets_file for obj in self.environment.objects
        }
        self.hashes = {path.name: _sha256(path.read_bytes()) for path in base_paths}
        self._record_cache = {}

    def close(self):
        self.archive.close()

    def _load_serialized_file(self, external_path: str):
        name = Path(external_path).name
        if name in self.loaded:
            return self.loaded[name]

        direct = self.player_data / name
        if direct.is_file():
            raw = direct.read_bytes()
        else:
            member = f"assets/bin/Data/{name}"
            try:
                raw = self.archive.read(member)
            except KeyError as exc:
                raise FileNotFoundError(
                    f"serialized dependency {name!r} is neither in {self.player_data} "
                    f"nor {self.asset_pack}::{member}"
                ) from exc
        self.hashes[name] = _sha256(raw)
        item = self.environment.load_file(raw, name=name)
        self.loaded[name] = item
        return item

    def dereference(self, owner, pointer):
        path_id = int(pointer["m_PathID"])
        if path_id == 0:
            return None
        file_id = int(pointer["m_FileID"])
        if file_id == 0:
            source_file = owner.assets_file
        else:
            externals = owner.assets_file.externals
            if file_id < 1 or file_id > len(externals):
                raise ValueError(
                    f"invalid PPtr file id {file_id} in {owner.assets_file.name}; "
                    f"external count={len(externals)}"
                )
            source_file = self._load_serialized_file(externals[file_id - 1].path)
        try:
            return source_file.objects[path_id]
        except KeyError as exc:
            raise KeyError(
                f"PPtr path id {path_id} not found in {source_file.name}"
            ) from exc

    @staticmethod
    def pointer(pointer):
        return {
            "m_FileID": int(pointer["m_FileID"]),
            "m_PathID": int(pointer["m_PathID"]),
        }

    @staticmethod
    def key(obj):
        return f"{obj.assets_file.name}:{obj.path_id}"

    def record(self, obj):
        key = self.key(obj)
        if key in self._record_cache:
            return self._record_cache[key]

        head = (
            obj.parse_monobehaviour_head()
            if obj.type.name == "MonoBehaviour"
            else None
        )
        tree = obj.read_typetree()
        script = None
        if head is not None:
            actual_script = self.pointer(tree["m_Script"])
            header_script = {
                "m_FileID": int(head.m_Script.m_FileID),
                "m_PathID": int(head.m_Script.m_PathID),
            }
            if actual_script != header_script:
                raise ValueError(
                    f"MonoBehaviour header/script mismatch for {key}: "
                    f"tree={actual_script!r} nativeHeader={header_script!r}"
                )
            script_obj = self.dereference(obj, actual_script)
            if script_obj is None:
                raise ValueError(f"MonoBehaviour {key} has a null m_Script")
            script = script_obj.read_typetree()

        record = {
            "key": key,
            "file": obj.assets_file.name,
            "pathId": int(obj.path_id),
            "byteSize": int(obj.byte_size),
            "type": obj.type.name,
            "script": script,
            "tree": tree,
        }
        self._record_cache[key] = record
        return record


def extract_mysekai_render_pipeline(
    player_data,
    types,
    asset_pack,
    *,
    unity_version,
    game_version=None,
    resources=DEFAULT_RESOURCES,
):
    """Read the player-shipped MYSEKAI pipeline and all referenced features."""
    source = _Source(
        Path(player_data),
        Path(types),
        Path(asset_pack),
        str(unity_version),
    )
    try:
        managers = [
            obj
            for obj in source.environment.objects
            if obj.type.name == "ResourceManager"
        ]
        if len(managers) != 1:
            raise ValueError(
                f"expected exactly one ResourceManager, found {len(managers)}"
            )
        manager = managers[0]
        container = manager.read_typetree()["m_Container"]

        selected = {}
        selected_objects = {}
        for resource_name in resources:
            pointers = [
                pointer for name, pointer in container if name == resource_name
            ]
            if len(pointers) != 1:
                raise ValueError(
                    f"resource {resource_name!r}: expected one ResourceManager entry, "
                    f"found {len(pointers)}"
                )
            target = source.dereference(manager, pointers[0])
            if target is None:
                raise ValueError(f"resource {resource_name!r} resolves to null")
            selected_objects[resource_name] = target
            selected[resource_name] = {
                "pointer": source.pointer(pointers[0]),
                "object": source.record(target),
            }

        pipeline_obj = selected_objects[PIPELINE_RESOURCE]
        pipeline = selected[PIPELINE_RESOURCE]["object"]
        renderer_slots = []
        renderers = {}
        for index, pointer in enumerate(
            pipeline["tree"].get("m_RendererDataList", [])
        ):
            slot = {
                "index": index,
                "pointer": source.pointer(pointer),
                "objectKey": None,
            }
            renderer = source.dereference(pipeline_obj, pointer)
            if renderer is not None:
                renderer_record = source.record(renderer)
                key = renderer_record["key"]
                slot["objectKey"] = key
                if key not in renderers:
                    features = []
                    for feature_index, feature_ptr in enumerate(
                        renderer_record["tree"].get("m_RendererFeatures", [])
                    ):
                        feature = source.dereference(renderer, feature_ptr)
                        features.append(
                            {
                                "index": feature_index,
                                "pointer": source.pointer(feature_ptr),
                                "object": (
                                    None
                                    if feature is None
                                    else source.record(feature)
                                ),
                            }
                        )
                    renderers[key] = {
                        "object": renderer_record,
                        "features": features,
                    }
            renderer_slots.append(slot)

        return {
            "schemaVersion": 1,
            "unityVersion": str(unity_version),
            "gameVersion": game_version,
            "sourceFilesSha256": dict(sorted(source.hashes.items())),
            "resources": selected,
            "rendererSlots": renderer_slots,
            "renderers": renderers,
        }
    finally:
        source.close()


def write_mysekai_render_pipeline(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return str(path)
