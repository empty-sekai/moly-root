"""Source node identity and runtime component bindings carried by glTF nodes.

The package walker already reads these components into the general sidecar.
This module supplies their lossless node-local representation: no name-based
binding, world-space radius estimate, or navigation behaviour is inferred here.
"""
from .harvest import CONTRACT_FIELD


def pointer_fields(value):
    """Keep PPtr file indices and signed 64-bit identities losslessly in JSON."""
    if isinstance(value, dict):
        if set(value) == {"m_FileID", "m_PathID"}:
            return {"file": value["m_FileID"], "id": str(value["m_PathID"])}
        return {key: pointer_fields(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [pointer_fields(item) for item in value]
    return value


def identity(record, graph, transform):
    """Identity and authored child order, independent of export traversal order."""
    tree = record.tree(transform)
    return {
        "sourceObject": {
            "file": record.archive,
            "gameObjectId": str(graph.owner[transform]),
            "transformId": str(transform),
        },
        "sourceChildOrder": [str(child["m_PathID"])
                             for child in tree.get("m_Children", [])],
        "sourceComponentIds": [str(component_id)
                               for _, component_id in graph.components(transform)],
    }


def attach_component(extras, component_id, kind, class_name, tree):
    """Attach runtime inputs while the existing generic component is read.

    Obstacle extents/center stay in source local coordinates. Enabled, shape and
    carving values retain their serialized types and values. Harvest view fields
    retain the entire typetree, with only PPtr spelling changed for exact IDs.
    """
    if kind == "NavMeshObstacle":
        fields = {
            "enabled": "m_Enabled",
            "shape": "m_Shape",
            "center": "m_Center",
            "extents": "m_Extents",
            "carve": "m_Carve",
            "onlyStationary": "m_CarveOnlyStationary",
            "moveThreshold": "m_MoveThreshold",
            "stationaryTime": "m_TimeToStationary",
        }
        extras.setdefault("navMeshObstacles", []).append({
            "componentId": str(component_id),
            **{key: tree[source] for key, source in fields.items()},
        })
    elif kind == "MonoBehaviour" and CONTRACT_FIELD in tree:
        if "harvestView" in extras:
            raise ValueError("one source node carries multiple harvest view components")
        extras["harvestView"] = {
            "class": class_name,
            "componentId": str(component_id),
            "fields": pointer_fields(tree),
        }
