"""Objects the player build ships in its Resources, read by their load names.

``Resources.Load(name)`` resolves through the one ResourceManager in the player's
global game managers: its container maps lower-case load names to objects in the
player's serialized files.  This module opens the player data through the same
package store the bundle domains use, resolves a load name through that
container, and hands the object on as data.

A build ships its player data in one of two layouts.  Most pack every
serialized file into one ``data.unity3d`` container.  A build that delivers its
data through an install-time asset pack has no container: ``globalgamemanagers``
sits in the base APK under ``assets/bin/Data/``, and each Resources asset is a
serialized file of its own, named by a GUID, in the asset pack.  The container
entries then point through ``globalgamemanagers``' externals at those files, so
the store loads each one from the APK members only when a pointer reaches it.

Player data differs from a bundle in two ways that matter here, and both are
handled rather than assumed away:

* **MonoBehaviours carry no typetree.**  Their fields are read with a typetree
  generated from the same build's managed assembly stubs, with the engine's own
  MonoBehaviour header spliced on (the generator the render-pipeline evidence
  reader uses).  UnityPy's read checks the node consumed the object's bytes
  exactly, so a stub that does not match the serialized layout fails loudly; such
  an object is reported by name with its byte count, never guessed.
* **One serialized file holds thousands of unrelated objects.**  A prefab is
  therefore extracted as a *view*: the objects reachable from its root by the
  pointers its own objects hold, where reaching any node of a prefab brings in
  that prefab's whole hierarchy.  The view is a package of its own, so the site
  package reader walks it exactly as it walks a bundle, and its accounting covers
  the view, not the whole player file.
"""
from pathlib import Path

from .assets.packages import (Package, PackageFile, PackageStore, is_builtin_archive,
                              require_paths)

RESOURCE_MANAGER = "ResourceManager"
# The serialized file the ResourceManager lives in when the player data is
# loose files rather than one container.
GLOBAL_GAME_MANAGERS = "globalgamemanagers"
TRANSFORMS = ("Transform", "RectTransform")

# Object kinds a view does not follow pointers *out of*, with why.  They are the
# objects that point at far more than the prefab needs: an atlas lists every
# sprite packed with it, a script and a shader are type identities, and the
# engine-owned managers index the whole build.
STOP_KINDS = {
    "SpriteAtlas": "a sprite atlas lists every sprite packed into it, not the ones this prefab uses",
    "MonoScript": "a script's type identity",
    "Shader": "a shader's code and its own dependencies; materials name it",
    "ResourceManager": "the build's resource index",
    "AssetBundle": "a bundle manifest",
    "PreloadData": "a scene preload list",
}


def generator_hook(types):
    """An environment hook that attaches a typetree generator for the build.

    The Unity version is read from the loaded serialized files themselves, and a
    set of files that disagree on it is refused rather than read at one of them.
    The engine's own resource files (:data:`BUILTIN_ARCHIVES`) are not the
    build's: the engine ships ``unity default resources`` prebuilt, and its
    header states the editor version that file was made with (2022.3.54f1 in a
    2022.3.62f2 build, say).  It holds engine objects only, read with the class
    database of its own stated version, and no behaviour whose typetree this
    generator makes, so it takes no part in the agreement, and an environment
    holding only such a file (a container build's, loaded beside it) gets no
    generator at all.
    """
    from UnityPy.files import SerializedFile
    from core.player_render_pipeline import NativeMonoBehaviourTypeTreeGenerator

    types = Path(types)
    if not types.is_dir():
        raise FileNotFoundError(f"managed assembly stubs not found: {types}")
    state = {}

    def hook(environment):
        versions, builtin = set(), 0
        pending = list(environment.files.values())
        while pending:
            item = pending.pop()
            if isinstance(item, SerializedFile):
                if not is_builtin_archive(str(getattr(item, "name", "")).rsplit("/", 1)[-1]):
                    versions.add(str(item.unity_version))
                else:
                    builtin += 1
            else:
                pending.extend((getattr(item, "files", None) or {}).values())
        if not versions and builtin:
            # A built-in resource file loaded on its own (a container build keeps
            # it beside the container): engine objects only, so no generator.
            return
        if len(versions) != 1:
            raise ValueError(f"player data states {len(versions)} Unity versions: "
                             f"{sorted(versions)}")
        version = versions.pop()
        generator = state.get(version)
        if generator is None:
            generator = NativeMonoBehaviourTypeTreeGenerator(version)
            generator.load_local_dll_folder(str(types))
            state[version] = generator
        environment.typetree_generator = generator
        state["version"] = version

    hook.state = state
    return hook


def player_data_paths(player_data):
    """The caller's player-data input as a list of paths.

    One path names a container file (``assets/bin/Data/data.unity3d``); a
    build without that container ships its player data as loose serialized
    files inside its APKs, and is named by those APKs (the base APK and the
    install-time asset pack), every one of them.
    """
    if player_data is None:
        return []
    if isinstance(player_data, (str, Path)):
        return [str(player_data)]
    return [str(path) for path in player_data]


def is_loose_player_data(paths):
    """Whether *paths* name APKs whose loose members are the player data.

    A mix of APKs and other files is refused: the player reads one layout.
    """
    import zipfile
    kinds = {zipfile.is_zipfile(path) for path in paths}
    if len(kinds) != 1:
        raise ValueError("player data mixes APKs with other files: "
                         + ", ".join(Path(path).name for path in paths))
    loose = kinds.pop()
    if not loose and len(paths) != 1:
        raise ValueError(f"player data names {len(paths)} container files; "
                         "a container build has exactly one")
    return loose


def open_player_data(player_data, types, builtin_resources=()):
    """``(store, package, unity version)`` for the player data.

    *player_data* is one container file, or the APKs of a build whose player
    data is loose files (see :func:`player_data_paths`).  The package is the
    one serialized file set that holds the ResourceManager: the container's
    files, or ``globalgamemanagers`` among the loose files, whose externals
    are then loaded on demand from the APK members the pointers name.

    A container build keeps the engine's built-in resource files (``unity
    default resources``, ``unity_builtin_extra``) beside the container in
    ``assets/bin/Data/``, not inside it, so an object that points into them
    (the default cube mesh a particle renderer draws, say) resolves only when
    *builtin_resources* names them.  A loose build carries them among its APK
    members, so naming them again is refused.
    """
    paths = player_data_paths(player_data)
    if not paths:
        raise ValueError("no player data supplied")
    require_paths(paths, "player data")
    builtins = [str(path) for path in builtin_resources or ()]
    require_paths(builtins, "built-in resource")
    hook = generator_hook(types)
    if is_loose_player_data(paths):
        if builtins:
            raise ValueError("a build whose player data is loose APK members carries its "
                             "built-in resource files among them; do not name them separately")
        from ui.player_source import ApkPlayerData
        store = PackageStore([], environment_hook=hook, members=ApkPlayerData(paths))
        package = store.package(GLOBAL_GAME_MANAGERS)
        if package is None:
            raise FileNotFoundError(f"no {GLOBAL_GAME_MANAGERS} in the APKs: "
                                    + ", ".join(Path(path).name for path in paths))
        return store, package, hook.state["version"]
    store = PackageStore(paths + builtins, environment_hook=hook)
    package = store.package(Path(paths[0]).name)
    if package is None:
        raise FileNotFoundError(f"player data not loaded: {paths[0]}")
    return store, package, hook.state["version"]


def resource_manager(package):
    managers = [(record, path_id) for record in package.files
                for path_id, kind in record.kinds.items() if kind == RESOURCE_MANAGER]
    if len(managers) != 1:
        raise ValueError(f"expected one ResourceManager, found {len(managers)}")
    return managers[0]


def resource_targets(store, package, load_name):
    """Every container entry for *load_name*, resolved.

    ``Resources.Load`` compares load names without case, and the container
    stores them lower-case, so the comparison is a case fold.  One load name can
    have several entries (a prefab and the sprites cut from one image share a
    name); all are returned with their pointers, and the caller picks by type.
    """
    record, path_id = resource_manager(package)
    wanted = str(load_name).casefold()
    rows = []
    for name, pointer in record.tree(path_id).get("m_Container") or []:
        if str(name).casefold() != wanted:
            continue
        target = store.follow(record, pointer)
        rows.append({"loadName": str(name), "pointer": dict(pointer),
                     "target": target,
                     "archive": store.archive_of(record, pointer)})
    return rows


def _pointers(value):
    if isinstance(value, dict):
        if set(value) == {"m_FileID", "m_PathID"}:
            yield value
            return
        for item in value.values():
            yield from _pointers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _pointers(item)


class ViewFile(PackageFile):
    """A subset of one loaded serialized file, sharing its reads."""

    def __init__(self, full, keep):
        super().__init__(full.bundle, full.archive, full.externals)
        self.full = full
        self.store = full.store
        self.kinds = {path_id: full.kinds[path_id] for path_id in sorted(keep)}
        self.objects = {path_id: full.objects[path_id] for path_id in self.kinds}
        self._trees = full._trees         # one cache for both views of an object


def prefab_view(store, targets, name):
    """A package holding exactly the objects reachable from *targets*.

    *targets* are ``(record, path id)`` pairs.  Every pointer of every reached
    object is followed, except out of the kinds in :data:`STOP_KINDS`; reaching a
    game object or a transform brings in its whole prefab, from its root down, so
    no hierarchy is cut in half.  Pointers into other serialized files are
    followed too, and the objects they reach form views of those files.
    Returns ``(package, report)``; the report counts what was not followed and
    names every object whose fields could not be read, with its byte count.
    """
    reached = {}                          # archive -> (full record, set of ids)
    queue = list(targets)
    stopped, unreadable, atlas_names = {}, [], []
    unfollowed = {}                       # archive a pointer names but the store lacks -> count

    def add(record, path_id):
        full = getattr(record, "full", record)
        slot = reached.setdefault(full.archive, (full, set()))
        if path_id in slot[1]:
            return False
        slot[1].add(path_id)
        return True

    def prefab_of(record, path_id):
        """Every object of the prefab hierarchy *path_id* belongs to."""
        kind = record.kinds.get(path_id)
        tree = record.tree(path_id)
        if kind == "GameObject":
            transform = next((component for component in _component_ids(tree)
                              if record.kinds.get(component) in TRANSFORMS), None)
        else:
            transform = path_id
        if transform is None:
            return [path_id]
        while True:
            father = (record.tree(transform).get("m_Father") or {}).get("m_PathID", 0)
            if not father:
                break
            transform = father
        out, pending = [], [transform]
        while pending:
            current = pending.pop()
            node = record.tree(current)
            game_object = (node.get("m_GameObject") or {}).get("m_PathID", 0)
            out.append(current)
            if game_object:
                out.append(game_object)
                out.extend(_component_ids(record.tree(game_object)))
            pending.extend((child or {}).get("m_PathID", 0)
                           for child in node.get("m_Children") or [])
        return out

    while queue:
        record, path_id = queue.pop()
        if not add(record, path_id):
            continue
        full = getattr(record, "full", record)
        kind = full.kinds.get(path_id)
        if kind in TRANSFORMS or kind == "GameObject":
            for member in prefab_of(full, path_id):
                queue.append((full, member))
        if kind in STOP_KINDS:
            stopped[kind] = stopped.get(kind, 0) + 1
            continue
        try:
            tree = full.tree(path_id)
        except Exception as exc:          # a stub that does not match the bytes
            unreadable.append({"archive": full.archive, "pathId": str(path_id),
                               "type": kind,
                               "class": (_script_name(full, path_id)
                                         if kind == "MonoBehaviour" else None),
                               "bytes": int(full.objects[path_id].byte_size),
                               "reason": f"{type(exc).__name__}: {exc}"})
            continue
        for pointer in _pointers(tree):
            if not pointer.get("m_PathID", 0):
                continue
            target = store.follow(full, pointer)
            if target is None:
                if pointer.get("m_FileID", 0):
                    wanted = str(store.archive_of(full, pointer)).rsplit("/", 1)[-1]
                    unfollowed[wanted] = unfollowed.get(wanted, 0) + 1
                continue
            queue.append(target)
        joined = _atlas_sprite(store, full, path_id, tree)
        if joined is not None:
            row, target = joined
            atlas_names.append(row)
            if target is not None:
                queue.append(target)
    files = [ViewFile(full, keep) for _, (full, keep)
             in sorted(reached.items())]
    package = Package(name, files, [], [])
    report = {"objects": sum(len(file.kinds) for file in files),
              "files": {file.archive: len(file.kinds) for file in files},
              "stoppedAt": dict(sorted(stopped.items())),
              "unreadable": unreadable,
              "atlasSpriteNames": sorted(atlas_names, key=lambda row: (
                  row["archive"], row["pathId"])),
              "stopKinds": STOP_KINDS}
    if unfollowed:
        # Pointers into a serialized file the store does not hold: named, not dropped.
        report["unfollowedArchives"] = dict(sorted(unfollowed.items()))
    return package, report


def _atlas_sprite(store, record, path_id, tree):
    """The packed sprite an image names by atlas and sprite name, or ``None``.

    The game's atlas image serializes a SpriteAtlas pointer ``atlas`` and a
    string ``spriteName`` next to the Image's own ``m_Sprite``; assigning the
    name looks the sprite up in that atlas by name.  A view does not follow an
    atlas's full packed-sprite list, so the one sprite a component names is
    joined here, through the atlas's own serialized name table.  Returns
    ``(row, target)``: the row says what was looked up and whether it matched
    exactly one packed sprite, and *target* is that sprite or ``None``.
    """
    name = tree.get("spriteName")
    pointer = tree.get("atlas")
    if not isinstance(name, str) or not name or not isinstance(pointer, dict)             or not pointer.get("m_PathID", 0):
        return None
    row = {"archive": record.archive, "pathId": str(path_id), "spriteName": name,
           "atlas": None, "sprite": None, "matches": 0}
    atlas = store.follow(record, pointer)
    if atlas is None or atlas[0].kinds.get(atlas[1]) != "SpriteAtlas":
        row["reason"] = "atlas pointer does not resolve to a SpriteAtlas"
        return row, None
    atlas_record, atlas_id = atlas
    atlas_tree = atlas_record.tree(atlas_id)
    row["atlas"] = str(atlas_tree.get("m_Name", ""))
    names = [str(item) for item in atlas_tree.get("m_PackedSpriteNamesToIndex") or []]
    sprites = atlas_tree.get("m_PackedSprites") or []
    indices = [index for index, item in enumerate(names) if item == name]
    row["matches"] = len(indices)
    if len(indices) != 1 or indices[0] >= len(sprites):
        row["reason"] = "the atlas name table does not name exactly one packed sprite"
        return row, None
    target = store.follow(atlas_record, sprites[indices[0]])
    if target is None:
        row["reason"] = "the packed sprite pointer does not resolve"
        return row, None
    row["sprite"] = {"archive": target[0].archive, "pathId": str(target[1])}
    return row, target


def _component_ids(game_object):
    out = []
    for entry in game_object.get("m_Component") or []:
        pointer = entry.get("component", entry) if isinstance(entry, dict) else entry
        if isinstance(pointer, dict) and pointer.get("m_PathID", 0):
            out.append(pointer["m_PathID"])
    return out


def _script_name(record, path_id):
    """A MonoBehaviour's script class, read from its native header only."""
    try:
        head = record.objects[path_id].parse_monobehaviour_head()
        script = head.m_Script.deref_parse_as_object()
        return ".".join(part for part in (script.m_Namespace, script.m_ClassName) if part)
    except Exception:                     # header unreadable too: say so
        return None
