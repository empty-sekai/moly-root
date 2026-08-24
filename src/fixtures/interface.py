"""Extract the minimal furniture performance interface."""

from .areas import extract_areas
from .attach import extract_from_store


def extract(store, master, out_dir):
    """Extract fixture performance data into ``out_dir``."""
    extract_areas(store, out_dir)
    return extract_from_store(store, out_dir, master)
