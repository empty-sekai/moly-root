"""Player data read straight from the APK members, for builds without resources.assets.

Some Android builds do not pack their Resources into one ``resources.assets``.
The ResourceManager in ``globalgamemanagers`` still indexes every Resources
path, but each entry points through ``globalgamemanagers``' own externals
table at a separately serialized file named by a GUID.  Those files sit next
to ``globalgamemanagers`` under ``assets/bin/Data/`` -- the base APK carries
the engine files, the install-time asset pack carries the GUID files -- and a
file larger than the split limit is stored as ``<name>.split0 .. .splitN``.

A GUID file is not self-contained: a prefab points at sprites, atlases,
materials and fonts through its own externals, and those files point on in
turn.  :meth:`PlayerEnvironment.add` loads the transitive externals closure
of the requested roots, so every PPtr a document follows resolves to
an actual object; an external that no APK member supplies is reported by
name, never skipped silently.
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import UnityPy
from core.unity import configure_fallback_unity_version


configure_fallback_unity_version()

DATA_PREFIX = "assets/bin/Data/"

# Two external spellings name engine files by an editor path: the player
# stores them under the bare file name ("unity default resources") or under
# Resources/ ("Resources/unity_builtin_extra").  Only the editor directory
# prefix is dropped; every other name is looked up exactly.
EDITOR_PREFIXES = ("Library/",)


class ApkPlayerData:
    """Serialized files indexed by their name under ``assets/bin/Data/``.

    Every APK given contributes its members; a name present in two APKs is an
    ambiguity and fails, since the player would read exactly one of them.
    """

    def __init__(self, apks):
        self.apks = [Path(p) for p in apks]
        if not self.apks:
            raise ValueError("at least one APK is required")
        self._zips = {}
        self._members = {}
        for path in self.apks:
            if not path.is_file():
                raise FileNotFoundError(f"APK not found: {path}")
            archive = zipfile.ZipFile(path)
            self._zips[path.name] = archive
            for member in archive.namelist():
                if not member.startswith(DATA_PREFIX) or member.endswith("/"):
                    continue
                name = member[len(DATA_PREFIX):]
                if name in self._members:
                    raise ValueError(f"player file {name} is in two APKs")
                self._members[name] = (path.name, member)
        self._cache = {}

    def _parts(self, name):
        """Member list for *name*: itself, or its contiguous .splitN run."""
        if name in self._members:
            return [self._members[name]]
        splits = {}
        prefix = name + ".split"
        for key, value in self._members.items():
            if key.startswith(prefix) and key[len(prefix):].isdigit():
                splits[int(key[len(prefix):])] = value
        if not splits:
            return None
        if sorted(splits) != list(range(len(splits))):
            raise ValueError(f"split run of {name} is not contiguous from 0: {sorted(splits)}")
        return [splits[i] for i in range(len(splits))]

    def resolve_name(self, external_path: str):
        """The member name the player reads for one externals entry, or None."""
        candidates = [external_path]
        for prefix in EDITOR_PREFIXES:
            if external_path.startswith(prefix):
                candidates.append(external_path[len(prefix):])
        for candidate in candidates:
            if self._parts(candidate) is not None:
                return candidate
        return None

    def read(self, name: str) -> bytes:
        if name in self._cache:
            return self._cache[name]
        parts = self._parts(name)
        if parts is None:
            raise FileNotFoundError(f"player file not in any APK: {name}")
        data = b"".join(self._zips[apk].read(member) for apk, member in parts)
        self._cache[name] = data
        return data

    def members_of(self, name: str):
        """Where one serialized file came from: APK and member names."""
        return [{"apk": apk, "member": member} for apk, member in self._parts(name)]

    def sha256(self, name: str) -> str:
        return hashlib.sha256(self.read(name)).hexdigest()

    def environment(self):
        """An empty :class:`PlayerEnvironment` over these APKs."""
        return PlayerEnvironment(self)


class PlayerEnvironment:
    """A UnityPy environment filled from the APK members on request.

    The environment has no search directory, so a PPtr into a file that is
    not loaded raises instead of walking the disk for a namesake.  ``loaded``
    maps each externals spelling to the member name it was loaded under;
    ``missing`` lists spellings no APK member supplies; ``shallow`` lists the
    files loaded without following their externals.
    """

    def __init__(self, store: ApkPlayerData):
        self.store = store
        self.env = UnityPy.Environment(path="")
        self.loaded = {}
        self.missing = []
        self.shallow = []
        self._members = {}

    def member(self, file_name: str) -> str:
        """The APK member name a loaded serialized file was read from."""
        return self._members[file_name]

    def add(self, spellings, *, follow=True):
        """Load *spellings*, and with *follow* their transitive externals closure."""
        queue = list(spellings)
        while queue:
            spelling = queue.pop(0)
            if spelling in self.missing:
                continue
            if spelling in self.loaded:
                if follow and spelling in self.shallow:
                    self.shallow.remove(spelling)
                    queue.extend(e.path for e in self.env.files[external_file_name(spelling)].externals)
                continue
            name = self.store.resolve_name(spelling)
            if name is None:
                self.missing.append(spelling)
                continue
            self.loaded[spelling] = name
            # Registered under the externals entry's file name (the last path
            # segment), the spelling every PPtr and MonoScript lookup uses.
            file_name = external_file_name(spelling)
            if self._members.get(file_name, name) != name:
                raise ValueError(f"two externals spellings load different files as {file_name}")
            self._members[file_name] = name
            serialized = self.env.load_file(self.store.read(name), name=file_name)
            if serialized is None:
                raise ValueError(f"player file could not be parsed: {name}")
            if not follow:
                self.shallow.append(spelling)
                continue
            queue.extend(external.path for external in serialized.externals
                         if external.path not in self.loaded)
        return self


def external_file_name(path: str) -> str:
    """The file name an externals entry is known by: its last path segment."""
    return path.rsplit("/", 1)[-1]


def serialized_file(env, name: str):
    """The loaded SerializedFile registered under *name*."""
    files = env.files
    if name in files:
        return files[name]
    raise KeyError(f"serialized file not loaded: {name}")


def resource_container(env, manager_file: str = "globalgamemanagers"):
    """The ResourceManager's Resources index: path -> [(fileId, pathId)].

    A Resources path can carry more than one entry (one per object in the
    asset); the list keeps them all, in container order.
    """
    manager_objects = [obj for obj in serialized_file(env, manager_file).objects.values()
                       if obj.type.name == "ResourceManager"]
    if len(manager_objects) != 1:
        raise ValueError(f"expected one ResourceManager, found {len(manager_objects)}")
    manager = manager_objects[0]
    container = {}
    for key, pointer in manager.read_typetree()["m_Container"]:
        container.setdefault(key, []).append((pointer["m_FileID"], pointer["m_PathID"]))
    return manager, container


def container_target(env, manager, pointer):
    """The (serialized file name, path id) one container entry points at."""
    fid, pid = pointer
    externals = manager.assets_file.externals
    if fid == 0:
        return manager.assets_file.name, pid
    if not 1 <= fid <= len(externals):
        raise ValueError(f"container file id {fid} outside the externals table")
    return external_file_name(externals[fid - 1].path), pid


def unity_version(env, name: str = "globalgamemanagers") -> str:
    return str(serialized_file(env, name).unity_version)
