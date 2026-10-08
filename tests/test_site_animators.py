"""Duplicate unbound decoration must not prevent a site's geometry export."""
import zlib

import pytest

from sites.animators import _Scope


class Graph:
    transform_of = {10: 100, 11: 101, 12: 102, 13: 103}

    def node_paths(self, root):
        return {10: "", 11: "decoration", 12: "decoration", 13: "animated"}


def test_unused_duplicate_paths_do_not_prevent_unique_binding():
    scope = _Scope(Graph(), 100, {100: 0, 101: 1, 102: 2, 103: 3})
    digest = zlib.crc32(b"animated")
    assert scope.resolve(digest) == "animated"
    assert scope.node_for_hash(digest) == 3
    assert scope.resolve(0) == ""


def test_bound_duplicate_path_still_rejects_instead_of_choosing_node():
    scope = _Scope(Graph(), 100, {100: 0, 101: 1, 102: 2, 103: 3})
    digest = zlib.crc32(b"decoration")
    with pytest.raises(ValueError, match="referenced by Animator clip"):
        scope.resolve(digest)
    with pytest.raises(ValueError, match="referenced by Animator clip"):
        scope.node_for_hash(digest)
