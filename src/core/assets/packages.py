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
from UnityPy.environment import simplify_name

# Serialized files the *engine* owns rather than any asset package.  A pointer
# into one of these carries an archive name exactly the way a pointer into a
# package does, so nothing about the pointer says it is special — only the name
# does.  No package ships them, so such a pointer resolves only when the caller
# supplies the file; where that file lives is the caller's to say and is never
# assumed here.
BUILTIN_ARCHIVES = ("unity default resources", "unity_builtin_extra")


def is_builtin_archive(name):
    """Whether a serialized file's name is one the engine ships, not a package."""
    return str(name) in BUILTIN_ARCHIVES


def builtin_archive_paths(values):
    """Engine-owned container files a caller offers, as paths to load.

    Each value is either a container file itself or a directory holding the
    engine's containers; a directory contributes those containers and nothing
    else, so naming one cannot drag unrelated files into the store.  A value
    that names neither is passed through unchanged and simply fails to match any
    pointer, which leaves the pointer as visibly unresolved as it was before.
    """
    paths = []
    for value in values or []:
        path = Path(value)
        if path.is_dir():
            paths.extend(str(path / name) for name in BUILTIN_ARCHIVES
                         if (path / name).is_file())
        else:
            paths.append(str(path))
    return paths


def require_paths(paths, what="bundle"):
    """Every caller-named path must exist; raise FileNotFoundError otherwise.

    ``UnityPy.load`` answers a path that is not there with an empty
    environment and no error, so a caller that hands over a bare package name
    instead of a path gets a run that reports zero objects and exits green.
    Callers whose argument is meant to be a path check it here, at the
    entrance, so that mistake fails loudly before any work is done.
    """
    missing = [str(path) for path in paths or [] if not Path(path).exists()]
    if missing:
        raise FileNotFoundError(
            f"{what} path does not exist: {', '.join(missing)}")


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
        # The store this file was loaded by, so a script pointer into another
        # serialized file can be followed.  Bundles keep their MonoScripts in
        # the same file; player data keeps them in a shared one.
        self.store = None

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
        if pointer.get("m_FileID", 0) and self.store is not None:
            target = self.store.follow(self, pointer)
            if target is None:
                return ""
            owner, script = target
            return str(owner.tree(script).get("m_ClassName", ""))
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


class MemberEnvironment(UnityPy.Environment):
    """One UnityPy environment over the serialized files of a member source.

    Every member file the store loads goes into this one environment, as a
    container build's files share one: UnityPy dereferences a MonoBehaviour's
    script pointer itself when it generates the behaviour's typetree, and it
    looks the target file up in the reading file's environment.  A file no
    pointer has reached yet is loaded from the member source when UnityPy asks
    for it (:meth:`find_file`), under the spelling the loaded files' externals
    give it, so nothing is read from the disk behind the caller's back and a
    file the source does not hold fails loudly.
    """

    def __init__(self, members):
        super().__init__(path="")
        self.members = members

    def load_member(self, name, member):
        """The serialized file *member* holds, loaded under *name* once.

        The stream-data files the engine keeps next to a serialized file
        (``<file>.resS``, ``<file>.resource``) are registered with it when the
        source holds them, so data that lives there reads from the same source.
        """
        loaded = self.files.get(name)
        if loaded is not None:
            return loaded
        loaded = self.load_file(self.members.read(member), name=name)
        if loaded is None or not hasattr(loaded, "objects"):
            raise ValueError(f"not a serialized file: {member}")
        stem = member[:-len(".assets")] if member.endswith(".assets") else member
        for companion in dict.fromkeys((member + ".resS", member + ".resource",
                                        stem + ".resource")):
            resolved = self.members.resolve_name(companion)
            if resolved is not None and resolved.rsplit("/", 1)[-1] not in self.files:
                self.load_file(self.members.read(resolved),
                               name=resolved.rsplit("/", 1)[-1])
        return loaded

    def find_file(self, name, is_dependency=True):
        cab = self.get_cab(name)
        if cab is not None:
            return cab
        # UnityPy asks by the lower-cased last segment; the source is asked by
        # the full spelling a loaded file's externals table gives that name.
        wanted = simplify_name(name)
        spellings = sorted({external.path for loaded in list(self.files.values())
                            for external in getattr(loaded, "externals", None) or []
                            if simplify_name(external.path) == wanted})
        members = sorted({member for member in map(self.members.resolve_name, spellings)
                          if member is not None})
        if len(members) != 1:
            raise FileNotFoundError(f"serialized file {name!r}: {len(members)} members "
                                    f"of the source answer the spellings {spellings}")
        return self.load_member(members[0].rsplit("/", 1)[-1], members[0])


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
    dependencies of those packages are looked up in.  *manifest* is optional and
    only ever makes an unresolved pointer say more: with one, :meth:`gap` can
    tell a package that has to be fetched from one that is part of the player
    build and never ships at all.

    :meth:`gap` also reads *root* to find which package holds an archive.  That
    index is built on the first unresolved pointer and not before, so a run in
    which everything resolves never pays for it, and a run that has something to
    explain pays once.
    """

    def __init__(self, paths, root=None, manifest=None, index=None,
                 environment_hook=None, members=None):
        # *environment_hook* is called with each freshly loaded UnityPy
        # environment before any object is read.  Player data needs it: its
        # MonoBehaviours carry no typetree, so a caller attaches a typetree
        # generator for the exact player build there.  Bundles never need it.
        self.environment_hook = environment_hook
        # *members* serves serialized files that are not files on disk: a
        # player build whose data ships as loose files inside its APKs.  It
        # answers ``resolve_name(external spelling)`` with the member it holds
        # under that spelling (or None) and ``read(member)`` with its bytes.
        # Such a file is a package of its own, named by the last segment of
        # the spelling, exactly as a pointer into it names its archive.
        self.members = members
        self._members_environment = None
        self.paths = {os.path.basename(str(path)): str(path) for path in paths}
        self.root = str(root) if root else None
        self.manifest = manifest
        self._index = index
        self._indexed = index is not None
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

    def _member_of(self, name, spelling=None):
        """The member *members* holds for a package name, or ``None``."""
        if self.members is None:
            return None
        for candidate in dict.fromkeys(item for item in (spelling, name) if item):
            member = self.members.resolve_name(str(candidate))
            if member is not None:
                return member
        return None

    def source_receipt(self, name):
        """Hash the actual input without publishing an installation-specific path."""
        from hashlib import file_digest, sha256
        path = self._path_of(name)
        member = self._member_of(name) if path is None else None
        if member is not None:
            data = self.members.read(member)
            return {"bundle": str(name), "bytes": len(data),
                    "sha256": sha256(data).hexdigest()}
        if path is None or not Path(path).is_file():
            raise FileNotFoundError(f"source package bytes unavailable: {name}")
        with open(path, "rb") as handle:
            digest = file_digest(handle, "sha256").hexdigest()
        return {"bundle": str(name), "bytes": Path(path).stat().st_size, "sha256": digest}

    def package(self, name, record_missing=True, spelling=None):
        """Load one package, or return ``None`` when it is not in the store.

        *record_missing* is false for a package that no bundle declared as a
        dependency — a sound package named by a master row, say — so that the
        dependency report keeps meaning "declared but not supplied".
        *spelling* is the full externals entry a pointer named the package by
        (``Resources/unity_builtin_extra``, say); a member source resolves
        that spelling, and the package keeps the name its last segment gives.
        """
        if name in self._packages:
            return self._packages[name]
        path = self._path_of(name)
        member = self._member_of(name, spelling) if path is None else None
        if path is None and member is None:
            self._packages[name] = None
            if record_missing and name not in self.missing:
                self.missing.append(name)
            return None
        if member is None:
            environment = UnityPy.load(path)
            objects = environment.objects
        else:
            if self._members_environment is None:
                self._members_environment = MemberEnvironment(self.members)
            environment = self._members_environment
            objects = list(environment.load_member(name, member).objects.values())
        if self.environment_hook is not None:
            self.environment_hook(environment)
        files, contents, dependencies = {}, [], []
        for obj in objects:
            source = getattr(obj, "assets_file", None)
            key = id(source)
            if key not in files:
                files[key] = PackageFile(
                    name, str(getattr(source, "name", "")).rsplit("/", 1)[-1],
                    [str(getattr(external, "name", ""))
                     for external in getattr(source, "externals", [])])
            record = files[key]
            record.store = self
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

    def adopt(self, package):
        """Register a package built by the caller (a view over loaded files).

        The files it holds must belong to packages this store loaded, so their
        pointers resolve through the same archive map.
        """
        if package.name in self._packages:
            raise ValueError(f"package already loaded: {package.name}")
        self._packages[package.name] = package
        return package

    def forget(self, name):
        """Drop a loaded package from the cache.

        A one-shot reader walking a large corpus (every talk voice package,
        say) consumes each package exactly once; releasing it as it goes keeps
        the run's memory to one package instead of the whole corpus.  Only
        packages already loaded are affected, so this never undoes a lookup
        that has not happened.
        """
        package = self._packages.pop(str(name), None)
        if package is not None:
            # The archive map also owns the PackageFile, and through its
            # objects every Unity reader/buffer. Leaving it behind retains the
            # whole corpus despite removing the package cache entry.
            for record in package.files:
                if self._archives.get(record.archive) is record:
                    self._archives.pop(record.archive)

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
        spelling = str(record.externals[index])
        archive = spelling.rsplit("/", 1)[-1]
        target = self._archives.get(archive)
        if target is None:
            # The pointer names an archive the store was not given up front.  A
            # caller may supply it as another input path (for example a built-in
            # container the engine ships next to the packages), or a member
            # source may hold it under the pointer's own spelling, so try to
            # load it once by name before reporting the pointer unresolved; a
            # load that fails for any reason still leaves the pointer unresolved.
            try:
                self.package(archive, record_missing=False, spelling=spelling)
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

    def archive_index(self):
        """Which package holds each archive under *root*, built once on demand.

        ``None`` when no root was given, which leaves :meth:`gap` reporting the
        archive name alone rather than guessing at what holds it.
        """
        if not self._indexed:
            self._indexed = True
            if self.root:
                from . import references
                self._index = references.ArchiveIndex.of_directory(self.root)
        return self._index

    def gap(self, record, pointer):
        """Why a pointer did not resolve, as much as this store can establish.

        Always carries the archive name, which is what an unresolved pointer
        could say on its own.  Given a root it also carries the package that
        holds the archive and a ``reason`` from :mod:`core.assets.references`,
        so a reader learns whether to name another input, fetch a package, or
        stop looking because the package is part of the player build.
        """
        from . import references
        archive = self.archive_of(record, pointer)
        answer = {"archive": archive, "fileId": (pointer or {}).get("m_FileID", 0)}
        index = self.archive_index()
        if archive is None or index is None:
            return answer
        package = self._packages.get(record.bundle)
        answer.update(references.explain(
            str(archive).rsplit("/", 1)[-1], index, self.manifest,
            dependencies=getattr(package, "dependencies", ()) or (),
            engine_archives=BUILTIN_ARCHIVES))
        return answer

    def dependents(self, name):
        """Every package loaded into the store that declares *name* a dependency.

        The dependency edges point from a consumer to what it consumes, so the
        packages that hold a thing's users are found by looking backwards along
        them.  Only packages the store already loaded are returned: this is a
        lookup over what is in hand, not a scan of the bundle directory.
        """
        logical = str(name)
        return [package for package in self._packages.values()
                if package is not None
                and logical in {dependency.replace("/", "__")
                                for dependency in package.dependencies}]

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
