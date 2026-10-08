"""One collision table per GLB, shared by explicitly referenced scene roots.

The detached metadata node is intentional: Bevy exposes GltfNode extras without
retaining the complete source JSON/BIN, but does not expose top-level extras on
the Gltf asset. Existing scene/node indices never change; the table node is
appended and must not be instanced through a scene or child relationship.
"""
from __future__ import annotations

METADATA_NODE_NAME = "__moly_fixture_collision"
REFERENCE_VERSION = 1
INDEX_VERSION = 4


def _scene_roots(gltf):
    nodes = gltf.get("nodes", [])
    for scene in gltf.get("scenes", []):
        for index in scene.get("nodes", []):
            if type(index) is not int or not 0 <= index < len(nodes):
                raise ValueError("fixture collision scene root index is invalid")
            yield index


def _referenced_document(gltf, reference):
    if (not isinstance(reference, dict)
            or set(reference) != {"schemaVersion", "node"}
            or type(reference["schemaVersion"]) is not int
            or reference["schemaVersion"] != REFERENCE_VERSION
            or type(reference["node"]) is not int):
        raise ValueError("fixture collision reference schema is unsupported")
    index = reference["node"]
    nodes = gltf.get("nodes", [])
    if not 0 <= index < len(nodes):
        raise ValueError("fixture collision reference node is out of range")
    node = nodes[index]
    if (not isinstance(node, dict) or node.get("name") != METADATA_NODE_NAME
            or set(node) != {"name", "extras"}
            or not isinstance(node["extras"], dict)
            or set(node["extras"]) != {"fixtureCollision"}):
        raise ValueError("fixture collision reference must name a metadata-only node")
    if index in _scene_roots(gltf) or any(index in row.get("children", []) for row in nodes):
        raise ValueError("fixture collision metadata node must not be instanced")
    if any(skin.get("skeleton") == index or index in skin.get("joints", [])
           for skin in gltf.get("skins", [])):
        raise ValueError("fixture collision metadata node must not be used by a skin")
    if any(channel.get("target", {}).get("node") == index
           for animation in gltf.get("animations", [])
           for channel in animation.get("channels", [])):
        raise ValueError("fixture collision metadata node must not be animated")
    return node["extras"]["fixtureCollision"]


def read_fixture_collision(gltf):
    """Read legacy inline or v4 detached-node storage, checking every root.

    No positional values or per-node source records are interpreted or changed.
    A wrong/dangling reference is an error, never a reason to ignore collision.
    """
    extras = gltf.get("extras", {})
    inline = extras.get("fixtureCollision")
    reference = extras.get("fixtureCollisionRef")
    if inline is not None and reference is not None:
        raise ValueError("fixture collision has ambiguous inline and referenced tables")
    if reference is not None:
        document = _referenced_document(gltf, reference)
    else:
        document = inline
    if not isinstance(document, dict):
        raise ValueError("fixture collision document is missing")
    for index in _scene_roots(gltf):
        root = gltf["nodes"][index].get("extras", {})
        if reference is None:
            if root.get("fixtureCollisionRef") is not None or root.get("fixtureCollision") != document:
                raise ValueError(f"scene root {index} collision table differs from document")
        elif root.get("fixtureCollision") is not None or root.get("fixtureCollisionRef") != reference:
            raise ValueError(f"scene root {index} collision reference differs from document")
    return document


def attach_fixture_collision(gltf, document):
    """Append a non-instanced table; leave all original nodes and indices intact."""
    roots = list(_scene_roots(gltf))
    nodes = gltf.setdefault("nodes", [])
    if any(node.get("name") == METADATA_NODE_NAME for node in nodes):
        raise ValueError("fixture collision metadata node already exists")
    reference = {"schemaVersion": REFERENCE_VERSION, "node": len(nodes)}
    nodes.append({"name": METADATA_NODE_NAME, "extras": {"fixtureCollision": document}})
    for extras in [gltf.setdefault("extras", {})] + [nodes[index].setdefault("extras", {}) for index in roots]:
        extras.pop("fixtureCollision", None)
        extras["fixtureCollisionRef"] = dict(reference)
    return reference


def compact_fixture_collision(gltf):
    """Losslessly replace verified duplicate inline tables; safe to call twice."""
    document = read_fixture_collision(gltf)
    if "fixtureCollisionRef" in gltf.get("extras", {}):
        return False
    attach_fixture_collision(gltf, document)
    return True
