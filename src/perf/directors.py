"""Prefab-owned PlayableDirectors and their actual timeline bindings.

Object names are presentation labels, not asset identities. Follow each
container's GameObject hierarchy and preserve component references separately.
"""
from core.assets.packages import pairs
from core.assets.identity import identity, reference


def _director(store, record, path_id, owner, hierarchy):
    tree = record.tree(path_id)
    return {
        "asset": identity(record, path_id),
        "gameObject": identity(*owner),
        "hierarchy": hierarchy,
        "timeline": reference(store, record, tree.get("m_PlayableAsset")),
        "enabled": tree.get("m_Enabled"),
        "updateMode": tree.get("m_DirectorUpdateMode"),
        "wrapMode": tree.get("m_WrapMode"),
        "initialState": tree.get("m_InitialState"),
        "initialTime": tree.get("m_InitialTime"),
        "sceneBindings": [
            {"key": reference(store, record, key), "value": reference(store, record, value)}
            for key, value in _scene_bindings(tree.get("m_SceneBindings"))
        ],
        "exposedReferences": [
            {"name": key, "value": reference(store, record, value)}
            for key, value in pairs((tree.get("m_ExposedReferences") or {}).get("m_References"))
        ],
    }


def _scene_bindings(entries):
    # PlayableDirector serializes this struct as key/value, not the
    # first/second property-map pair used by materials and AssetBundle.
    for entry in entries or []:
        if not isinstance(entry, dict) or "key" not in entry or "value" not in entry:
            raise ValueError("unsupported PlayableDirector scene-binding record")
        yield entry["key"], entry["value"]


def _fixture_timeline_view(store, record, component_id, owner, hierarchy):
    """Retain the view's own director and its ordered runtime effect bindings.

    BindEffects matches the ControlPlayableAsset object, then searches the
    instantiated view's parent for a ParticleSystem named BindName. Extraction
    supplies that relation; it does not pretend to have a runtime parent/target.
    """
    tree = record.tree(component_id)
    effects = []
    for row in tree["_effectDataList"]:
        bind_name, exposed_name = row["BindName"], row["ExposedName"]
        if not isinstance(bind_name, str) or not isinstance(exposed_name, str):
            raise ValueError("TimelineEffectData names must retain source strings")
        effects.append({
            "playable": reference(store, record, row["ControlPlayableAsset"]),
            "bindName": bind_name,
            "exposedName": exposed_name,
        })
    return {
        "asset": identity(record, component_id),
        "gameObject": identity(*owner),
        "hierarchy": hierarchy,
        "enabled": tree["m_Enabled"],
        "director": reference(store, record, tree["_playableDirector"]),
        "effectBindings": effects,
    }


def read_prefab_directors(store, package):
    result = []
    for source in package.files:
        for asset_id, kind in source.kinds.items():
            if kind != "AssetBundle":
                continue
            for path, entry in pairs(source.tree(asset_id).get("m_Container")):
                if not path.endswith(".prefab"):
                    continue
                root = store.follow(source, entry.get("asset"))
                if root is None or root[0].kinds[root[1]] != "GameObject":
                    raise ValueError(f"prefab does not resolve to its GameObject: {path}")
                prefab = {"container": path, "asset": identity(*root),
                          "directors": [], "fixtureTimelineViews": []}
                pending = [(root, [])]
                visited = set()
                while pending:
                    owner, ancestors = pending.pop()
                    record, game_object = owner
                    if owner in visited:
                        raise ValueError(f"repeated GameObject in prefab hierarchy: {path}")
                    visited.add(owner)
                    tree = record.tree(game_object)
                    hierarchy = [*ancestors, tree.get("m_Name", "")]
                    for item in tree.get("m_Component") or []:
                        pointer = item.get("component") if isinstance(item, dict) else item[1]
                        component = store.follow(record, pointer)
                        if component is None:
                            raise ValueError(f"unresolved prefab component: {path}, {hierarchy}")
                        component_file, component_id = component
                        component_kind = component_file.kinds[component_id]
                        if component_kind == "PlayableDirector":
                            prefab["directors"].append(_director(
                                store, component_file, component_id, owner, hierarchy))
                        elif (component_kind == "MonoBehaviour"
                              and component_file.script_of(component_id) == "NPCFixtureTimelineView"):
                            prefab["fixtureTimelineViews"].append(_fixture_timeline_view(
                                store, component_file, component_id, owner, hierarchy))
                        elif component_kind in ("Transform", "RectTransform"):
                            children = component_file.tree(component_id).get("m_Children") or []
                            for child in reversed(children):
                                target = store.follow(component_file, child)
                                if target is None:
                                    raise ValueError(f"unresolved prefab child: {path}, {hierarchy}")
                                child_owner = store.follow(target[0], target[0].tree(target[1])["m_GameObject"])
                                if child_owner is None:
                                    raise ValueError(f"unresolved child GameObject: {path}, {hierarchy}")
                                pending.append((child_owner, hierarchy))
                result.append(prefab)
    return result
