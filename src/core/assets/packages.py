"""Loading asset packages, and following pointers across package boundaries.

A package is one Unity AssetBundle file.  Inside it are one or more *serialized
files*, each holding objects addressed by a path id, and each declaring the other
serialized files it points into as *externals*.  A pointer is a
``(m_FileID, m_PathID)`` pair: file id 0 means "this file", and anything else
indexes that file's external list.  So resolving a pointer needs every package a
package depends on to be loaded, which is what :class:`PackageStore` does.

Typetrees are read on demand and cached: a scene package holds tens of thousands
of objects, and a run touches only the ones it exports.

Both asset domains in this repository read packages this way, so the loader lives
here rather than inside either of them.
"""
import os
from pathlib import Path

import UnityPy


def pairs(entries):
    """Unity serialises property maps as (name, value) pairs; accept either form."""
    for entry in entries or []:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            yield entry[0], entry[1]
        elif isinstance(entry, dict):
            yield entry.get("first"), entry.get("second")


class PackageFile:
    """One serialized file inside a package, with typetrees read on demand."""

    def __init__(self, bundle, archive, externals):
        self.bundle = bundle
        self.archive = archive
        self.externals = externals
        self.kinds = {}
        self.objects = {}
        self.trees = _Trees(self)
        self._trees = {}
        self._scripts = None

    def tree(self, path_id):
        if path_id not in self._trees:
            self._trees[path_id] = self.objects[path_id].read_typetree()
        return self._trees[path_id]

    def script_of(self, path_id):
        """Class name of the script a MonoBehaviour instantiates, or ``""``."""
        if self._scripts is None:
            self._scripts = {candidate: str(self.tree(candidate).get("m_ClassName", ""))
                             for candidate, kind in self.kinds.items()
                             if kind == "MonoScript"}
        pointer = self.tree(path_id).get("m_Script") or {}
        return self._scripts.get(pointer.get("m_PathID", 0), "")


class _Trees:
    """Read-on-demand view of one file's typetrees, keyed by path id."""

    def __init__(self, record):
        self._record = record

    def __getitem__(self, path_id):
        return self._record.tree(path_id)

    def get(self, path_id, default=None):
        if path_id not in self._record.kinds:
            return default
        return self._record.tree(path_id)


class Package:
    """One loaded package: its serialized files, contents, and dependencies."""

    def __init__(self, name, files, contents, dependencies):
        self.name = name
        self.files = files
        self.contents = contents          # [(asset file name, PackageFile, path id)]
        self.dependencies = dependencies


class PackageStore:
    """Packages by logical name, each loaded at most once.

    *paths* are the packages the caller named; *root* is an optional directory the
    dependencies of those packages are looked up in.
    """

    def __init__(self, paths, root=None):
        self.paths = {os.path.basename(str(path)): str(path) for path in paths}
        self.root = str(root) if root else None
        self.missing = []
        self._packages = {}
        self._archives = {}

    def _path_of(self, name):
        if name in self.paths:
            return self.paths[name]
        if not self.root:
            return None
        for candidate in (Path(self.root) / name,
                          Path(self.root) / name.replace("__", "/")):
            if candidate.exists():
                return str(candidate)
        return None

    def package(self, name, record_missing=True):
        """Load one package, or return ``None`` when it is not in the store.

        *record_missing* is false for a package that no bundle declared as a
        dependency — a sound package named by a master row, say — so that the
        dependency report keeps meaning "declared but not supplied".
        """
        if name in self._packages:
            return self._packages[name]
        path = self._path_of(name)
        if path is None:
            self._packages[name] = None
            if record_missing and name not in self.missing:
                self.missing.append(name)
            return None
        environment = UnityPy.load(path)
        files, contents, dependencies = {}, [], []
        for obj in environment.objects:
            source = getattr(obj, "assets_file", None)
            key = id(source)
            if key not in files:
                files[key] = PackageFile(
                    name, str(getattr(source, "name", "")).rsplit("/", 1)[-1],
                    [str(getattr(external, "name", ""))
                     for external in getattr(source, "externals", [])])
            record = files[key]
            record.kinds[obj.path_id] = obj.type.name
            record.objects[obj.path_id] = obj
        for record in files.values():
            if record.archive:
                self._archives[record.archive] = record
        for record in files.values():
            for path_id, kind in list(record.kinds.items()):
                if kind != "AssetBundle":
                    continue
                tree = record.tree(path_id)
                dependencies.extend(str(dep) for dep in tree.get("m_Dependencies") or [])
                for asset_path, info in tree.get("m_Container") or []:
                    target = self.follow(record, (info or {}).get("asset") or {})
                    if target is not None:
                        contents.append((str(asset_path).rsplit("/", 1)[-1], *target))
        package = Package(name, list(files.values()), contents, dependencies)
        self._packages[name] = package
        return package

    def follow(self, record, pointer):
        """Resolve a pointer to ``(file, path id)``, or ``None`` when it is not here."""
        pointer = pointer or {}
        path_id = pointer.get("m_PathID", 0)
        file_id = pointer.get("m_FileID", 0)
        if not path_id:
            return None
        if not file_id:
            return (record, path_id) if path_id in record.kinds else None
        index = file_id - 1
        if not 0 <= index < len(record.externals):
            return None
        archive = str(record.externals[index]).rsplit("/", 1)[-1]
        target = self._archives.get(archive)
        if target is None:
            # The pointer names an archive the store was not given up front.  A
            # caller may supply it as another input path (for example a built-in
            # container the engine ships next to the packages), so try to load
            # it once by name before reporting the pointer unresolved; a load
            # that fails for any reason still leaves the pointer unresolved.
            try:
                self.package(archive, record_missing=False)
            except Exception:
                target = None
            else:
                target = self._archives.get(archive)
        if target is None or path_id not in target.kinds:
            return None
        return target, path_id

    def archive_of(self, record, pointer):
        """Name of the serialized file a pointer names, or ``None``.

        Reported when a pointer does not resolve, so an unresolved reference says
        which archive it wanted rather than only that it failed.
        """
        index = (pointer or {}).get("m_FileID", 0) - 1
        if index < 0:
            return record.archive
        return record.externals[index] if index < len(record.externals) else None

    def load_dependencies(self, names):
        """Load *names* and everything they declare, as far as the store reaches."""
        pending, seen = list(names), set()
        while pending:
            name = pending.pop(0)
            if name in seen:
                continue
            seen.add(name)
            package = self.package(name)
            if package is None:
                continue
            pending.extend(dependency.replace("/", "__")
                           for dependency in package.dependencies)
