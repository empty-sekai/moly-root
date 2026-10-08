"""Serialized collider controls shared by scene and effect extraction.

A disabled renderer does not disable a collider. Layer membership and component
state therefore belong to the collision record, independently of visible meshes.
Missing controls remain null rather than becoming guessed physics settings.
"""

COLLIDER_COMPONENTS = ("MeshCollider", "BoxCollider", "SphereCollider",
                       "CapsuleCollider")


def _pointer(value):
    if not isinstance(value, dict):
        return None
    return {"fileId": value.get("m_FileID"),
            "pathId": str(value["m_PathID"]) if "m_PathID" in value else None}


def _vector(value):
    if not isinstance(value, dict) or any(key not in value for key in "xyz"):
        return None
    return [float(value[key]) for key in "xyz"]


def _boolean(tree, key):
    return bool(tree[key]) if key in tree else None


def _mask(tree, key):
    value = tree.get(key)
    return value.get("m_Bits") if isinstance(value, dict) else None


def decode_collider(tree, game_object, kind, node_path):
    """Preserve a collider's controls without resolving its mesh or material."""
    if kind not in COLLIDER_COMPONENTS:
        raise ValueError(f"unsupported collider component {kind}")
    entry = {
        "sourceVersion": 1, "node": node_path, "component": kind,
        "enabled": _boolean(tree, "m_Enabled"),
        "layer": game_object.get("m_Layer"),
        "isTrigger": _boolean(tree, "m_IsTrigger"),
        "includeLayers": _mask(tree, "m_IncludeLayers"),
        "excludeLayers": _mask(tree, "m_ExcludeLayers"),
        "layerOverridePriority": tree.get("m_LayerOverridePriority"),
        "providesContacts": _boolean(tree, "m_ProvidesContacts"),
        "materialPointer": _pointer(tree.get("m_Material")),
    }
    if kind == "MeshCollider":
        entry.update(convex=_boolean(tree, "m_Convex"),
                     cookingOptions=tree.get("m_CookingOptions"),
                     meshPointer=_pointer(tree.get("m_Mesh")))
    else:
        entry["center"] = _vector(tree.get("m_Center"))
        if kind == "BoxCollider":
            entry["size"] = _vector(tree.get("m_Size"))
        else:
            entry["radius"] = tree.get("m_Radius")
            if kind == "CapsuleCollider":
                entry["height"] = tree.get("m_Height")
                entry["direction"] = tree.get("m_Direction")
    return entry
