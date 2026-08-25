"""Extract the minimal furniture performance interface."""

from .areas import extract_areas
from .attach import extract_from_store


def extract(store, master, out_dir):
    """Extract fixture performance data into ``out_dir``."""
    extract_areas(store, out_dir)
    return extract_from_store(store, out_dir, master)


def extract_geometry(store, out_dir):
    """Extract the furniture geometry of the same packages into ``out_dir``.

    The geometry is a separate entry point rather than part of :func:`extract`
    because it is a separate decision: it writes one glTF binary per package for
    the whole fixture family, which is orders of magnitude larger than the two
    index documents :func:`extract` writes, and a caller that wants the attach
    points does not necessarily want a few gigabytes of meshes.  The store is
    the same one, so nothing is opened twice.

    The import is deferred to the call: the mesh reader pulls in UnityPy's mesh
    helper, and a caller that never asks for geometry should not have to have it
    importable.
    """
    from .meshes import extract_meshes
    return extract_meshes(store, out_dir)


def extract_particles(store, out_dir):
    """Extract the particle emitters of the same packages into ``out_dir``.

    A separate entry point rather than part of :func:`extract` for the same
    reasons as the geometry: the emitter pass is second-order (it reads the
    ParticleSystem and ParticleSystemRenderer components of packages whose
    attach points are read for anything else), its documents and texture copies
    are large in number even if small in bytes, and :func:`extract` has
    deliberately never claimed it.  The store is the same one, so nothing is
    opened twice.

    The import is deferred to the call for the same reason as the geometry's,
    and because the pass additionally pulls in the shared emitter decoder and
    takes a while to run; a caller that only wants the attach points should not
    import it by loading :func:`extract`.
    """
    from .particles import extract_from_store
    return extract_from_store(store, out_dir)
