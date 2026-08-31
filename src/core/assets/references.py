"""Why a cross-package pointer did not resolve, said in terms a caller can act on.

A pointer that leaves its own serialized file names an **archive** -- the file's
internal name, not the package's.  :class:`~core.assets.packages.PackageStore`
resolves such a pointer only when the archive belongs to a package it has
already loaded; otherwise it tries the archive *as a package name*, which is a
name no package file has, and the pointer stays unresolved.

An unresolved pointer is then reported as an archive name and nothing else, and
that single shape covers four situations a caller must tell apart:

* the archive is in a package sitting in the asset root that this run did not
  load -- **name it as an input**, there is nothing missing;
* the archive is in a package the manifest lists as downloadable and the root
  does not have -- **fetch it**;
* the archive is in a package the manifest marks as part of the player build,
  which is never on the download path -- **there is nothing to fetch**, and
  looking for the package is looking for something that does not ship;
* nothing establishes any of the above -- **neither claim is supported**, and
  making one would be inventing evidence.

Collapsing the third into the second is the expensive one: it sends a reader
looking for a download that does not exist, and the search returns empty in a
way that looks exactly like a broken pipeline.  Collapsing the first into the
second is the embarrassing one: it reports a fetch for a file already on disk.

Two names, one word
-------------------

``isBuiltin`` in a bundle manifest and
:func:`~core.assets.packages.is_builtin_archive` are different subjects that
share a word.  The first says a *package* was compiled into the player build.
The second says a *serialized file* is one the engine itself ships
(``unity default resources`` and its sibling), which no package ever contains.
Nothing here calls the manifest flag "builtin"; it is ``inPlayerBuild``.

What is measured
----------------

Against one asset root of 6,510 package files:

* 8,431 archives were indexed with no load failure;
* 1,921 packages hold **more than one** archive, so a package-keyed map would
  lose references and the index is keyed by archive;
* **no archive was claimed by two packages**, so the map is a function on that
  root.  It is not assumed to be one: :meth:`ArchiveIndex.of` reports a
  collision instead of picking a side, because picking silently would make a
  wrong answer indistinguishable from a right one.

Indexing reads each package's file table and no object, which measured at about
6 ms per package -- the whole root in well under a minute.
"""
from pathlib import Path

#: The archive is in a package that is present but was not among this run's
#: inputs.  Nothing is missing from the root; the store was simply not given the
#: package, and naming it is the whole fix.
NOT_LOADED = "notLoaded"

#: No indexed package holds the archive, and a package the referrer declares as
#: a dependency is both absent from the root and listed as downloadable.  This
#: is the only reason that names a fetchable gap.
NOT_SUPPLIED = "notSupplied"

#: No indexed package holds the archive, and every declared dependency absent
#: from the root is part of the player build.  Such a package is not on the
#: download path at all, so there is nothing to fetch and searching for one
#: returns empty in a way that looks exactly like a broken pipeline.
IN_PLAYER_BUILD = "inPlayerBuild"

#: The evidence stops short.  Distinct from every reason above: it says what is
#: not known rather than what is true.
UNEXPLAINED = "unexplained"

#: The archive is one the engine itself ships rather than any package.
ENGINE_ARCHIVE = "engineArchive"


def archives_in(path, loader=None):
    """Names of the serialized files one package file contains.

    A package holds its files under their archive names, which is what a pointer
    names; the package's own file name appears nowhere in a pointer.  Reading the
    file table costs no object reads.
    """
    if loader is None:
        import UnityPy
        loader = UnityPy.load
    names = []
    for container in loader(str(path)).files.values():
        inner = getattr(container, "files", None)
        if isinstance(inner, dict):
            names.extend(str(name) for name in inner)
    return names


class ArchiveIndex:
    """Which package holds each archive, across a directory of packages.

    Built by reading every package's file table once.  A caller that only wants
    to explain one pointer still pays for the whole directory, which is the
    honest cost: the answer "no package here holds it" is only trustworthy when
    every package has been looked at.
    """

    def __init__(self, mapping=None, scanned=0, failures=None):
        self._mapping = {name: list(holders) for name, holders in (mapping or {}).items()}
        self.scanned = scanned
        #: ``[(package file name, error)]`` for packages that would not load.  A
        #: failure is kept because an archive missing from the index means
        #: something different when a package could not be read at all.
        self.failures = list(failures or [])

    @classmethod
    def build(cls, paths, loader=None):
        """Index the given package files.

        *paths* is any iterable of package files; a directory is not walked here,
        so a caller states exactly which files it is claiming to have looked at.
        """
        mapping, failures, scanned = {}, [], 0
        for path in paths:
            scanned += 1
            try:
                names = archives_in(path, loader)
            except Exception as exc:                                # noqa: BLE001
                failures.append((Path(path).name, f"{type(exc).__name__}: {exc}"))
                continue
            for name in names:
                mapping.setdefault(name, []).append(Path(path).name)
        return cls(mapping, scanned, failures)

    @classmethod
    def of_directory(cls, root, loader=None):
        """Index every file directly inside *root*.

        A root that cannot be listed yields an empty index carrying that as a
        failure, rather than raising.  This index exists to explain a gap, and
        something that explains gaps must not become one: raised from inside a
        run, it would abort the very extraction it was asked about, and the
        wreckage would look like a fault in the extraction rather than in the
        explanation.
        """
        try:
            paths = sorted(p for p in Path(root).iterdir() if p.is_file())
        except OSError as exc:
            return cls({}, 0, [(str(root), f"{type(exc).__name__}: {exc}")])
        return cls.build(paths, loader)

    def of(self, archive):
        """The package holding *archive*, ``None`` if unknown.

        Raises when two packages claim the same archive rather than returning
        either: on the root this was measured against no archive was claimed
        twice, and a silent pick would make a wrong answer look like a right one.
        """
        holders = self._mapping.get(str(archive))
        if not holders:
            return None
        if len(holders) > 1:
            raise LookupError(f"archive {archive!r} is claimed by {len(holders)} "
                              f"packages: {sorted(holders)}")
        return holders[0]

    @property
    def packages(self):
        """Names of the package files that were indexed."""
        return {holder for holders in self._mapping.values() for holder in holders}

    def __len__(self):
        return len(self._mapping)

    def __contains__(self, archive):
        return str(archive) in self._mapping


def _flatten(name):
    return str(name).replace("/", "__")


def _entry_of(manifest, package):
    entries = getattr(manifest, "entries", None) or {}
    for key in (package, str(package).replace("__", "/")):
        if key in entries:
            return entries[key]
    return None


def explain(archive, index=None, manifest=None, dependencies=(), engine_archives=()):
    """Why a pointer naming *archive* did not resolve, and what would fix it.

    Returns ``{"archive", "reason"}``, plus ``"package"`` when the archive was
    located and ``"via"`` when the answer came from the referring package's
    dependencies.  *manifest* is anything with an ``entries`` mapping of package
    name to an entry exposing ``in_player_build``
    (:class:`core.fetch.Manifest`).  *dependencies* are the packages the
    *referring* package declares.

    Every argument only ever narrows the answer.  With none of them the reason
    is :data:`UNEXPLAINED`, and it stays :data:`UNEXPLAINED` rather than
    becoming a guess whenever the evidence stops short.

    A located archive is never reported as a fetchable gap.  Its package is on
    disk by definition -- that is what being in the index means -- so the fix is
    to load it, and saying "fetch it" would send a reader after a download for a
    file already sitting in the root.

    When no indexed package holds the archive, the referring package's
    dependencies decide, by elimination over the ones absent from the root: all
    of them in the player build means nothing on the download path would produce
    the archive; any downloadable one among them means a real fetch is missing.
    That step assumes a package declares every package it points into, which is
    what a manifest's dependency list is for; it is refused outright when any
    package failed to index, because then "no indexed package holds it" is not a
    fact about the root but about a partial read of it.
    """
    archive = str(archive)
    if archive in set(engine_archives):
        return {"archive": archive, "reason": ENGINE_ARCHIVE}

    package = index.of(archive) if index is not None else None
    if package is not None:
        return {"archive": archive, "package": package, "reason": NOT_LOADED}

    if index is None or index.failures or not dependencies:
        return {"archive": archive, "reason": UNEXPLAINED}
    absent = [name for name in dependencies if _flatten(name) not in index.packages]
    if not absent:
        return {"archive": archive, "reason": UNEXPLAINED}
    fetchable = [name for name in absent
                 if not getattr(_entry_of(manifest, _flatten(name)), "in_player_build", False)]
    if not fetchable:
        return {"archive": archive, "reason": IN_PLAYER_BUILD, "via": sorted(absent)}
    if any(_entry_of(manifest, _flatten(name)) is None for name in fetchable):
        return {"archive": archive, "reason": UNEXPLAINED, "via": sorted(absent)}
    return {"archive": archive, "reason": NOT_SUPPLIED, "via": sorted(fetchable)}
