"""HouseView's explicit door references, separate from furniture attach slots.

Action points are Transforms, not positions in a shared coordinate system.
Keep their parent chains so a consumer can resolve the current house instance
without baking a particular placement into reusable package metadata.
"""
from core.assets.identity import identity, reference


def _transform_chain(store, owner, pointer, transforms):
    selected = reference(store, owner, pointer)
    target = store.follow(owner, pointer) if pointer["m_PathID"] else None
    visited = set()
    while target is not None:
        record, path_id = target
        key = (record.archive, path_id)
        if key in visited:
            raise ValueError("cycle in HouseView action-point parent chain")
        visited.add(key)
        if key in transforms:
            break
        if record.kinds[path_id] not in ("Transform", "RectTransform"):
            raise ValueError("HouseView action point or parent is not a Transform")
        tree = record.tree(path_id)
        transforms[key] = {
            "asset": identity(record, path_id),
            "gameObject": reference(store, record, tree["m_GameObject"]),
            "parent": reference(store, record, tree["m_Father"]),
            "localPosition": dict(tree["m_LocalPosition"]),
            "localRotation": dict(tree["m_LocalRotation"]),
            "localScale": dict(tree["m_LocalScale"]),
        }
        parent = tree["m_Father"]
        target = store.follow(record, parent) if parent["m_PathID"] else None
    return selected


def read(store, package):
    """Read serialized HouseViews; do not emulate AttachComponents at runtime."""
    views, transforms = [], {}
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour" or record.script_of(path_id) != "HouseView":
                continue
            tree = record.tree(path_id)
            views.append({
                "asset": identity(record, path_id),
                "gameObject": reference(store, record, tree["m_GameObject"]),
                "enabled": tree["m_Enabled"],
                "animator": reference(store, record, tree["_animator"]),
                "insideDoorActionPoint": _transform_chain(
                    store, record, tree["insideDoorActionPoint"], transforms),
                "outsideDoorActionPoint": _transform_chain(
                    store, record, tree["outsideDoorActionPoint"], transforms),
            })
    return {"views": views, "transforms": list(transforms.values())}
