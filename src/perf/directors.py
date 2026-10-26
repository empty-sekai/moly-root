"""Prefab-owned PlayableDirectors and their actual timeline bindings.

Object names are presentation labels, not asset identities. Follow each
container's GameObject hierarchy and preserve component references separately.

Besides the directors and the fixture timeline views, each prefab record
carries what its timelines reach inside the prefab:

* ``cinemachine``: every Cinemachine component (virtual cameras and the
  pipeline components under them), with its serialized fields and its
  GameObject's local transform chain from the prefab root down;
* ``signalReceivers``: each ``SignalReceiver`` and its reaction table (signal
  asset identity and name, and each persistent call's target, method, mode
  and arguments);
* ``views``: the scripts in ``SCENE_VIEW_SCRIPTS`` with their fields;
* ``rootScripts`` and ``particleSystems``: the root's script names and every
  ParticleSystem component in the prefab.

:func:`read_effect_prefabs` lists the prefabs the package's effect clips
instantiate (``EffectClip.template.prefab``), which need not be containers
of the package: each with its name, root scripts and particle systems.
"""
from core.assets.packages import pairs
from core.assets.identity import identity, json_pointers, reference

# Engine bookkeeping every component carries.
ENGINE_FIELDS = frozenset({
    "m_GameObject", "m_Enabled", "m_Script", "m_ObjectHideFlags",
    "m_CorrespondingSourceObject", "m_PrefabInstance", "m_PrefabAsset",
})

# Views on a timeline prefab whose serialized fields a player reads: the
# cut-scene view (start/end fades, fixture hiding distance and lists).
SCENE_VIEW_SCRIPTS = ("CutSceneView",)


def _fields(tree):
    return json_pointers({key: value for key, value in tree.items()
                          if key not in ENGINE_FIELDS})


def _local(tree, name):
    """One Transform's local pose, exactly as serialized."""
    return {"name": name,
            "position": dict(tree.get("m_LocalPosition") or {}),
            "rotation": dict(tree.get("m_LocalRotation") or {}),
            "scale": dict(tree.get("m_LocalScale") or {})}


def _object_name(store, record, pointer):
    """The name and identity of the GameObject a component pointer belongs to."""
    target = store.follow(record, pointer) if pointer and pointer.get("m_PathID") else None
    if target is None:
        return None, None
    tree = target[0].tree(target[1]) or {}
    owner = tree.get("m_GameObject")
    if owner is None:
        return tree.get("m_Name"), identity(*target)
    game_object = store.follow(target[0], owner)
    if game_object is None:
        return None, None
    return (game_object[0].tree(game_object[1]) or {}).get("m_Name"), identity(*game_object)


def _signal_receiver(store, record, component_id, owner, hierarchy):
    """A SignalReceiver's reaction table: signal i reacts with event i."""
    tree = record.tree(component_id)
    events = tree.get("m_Events") or {}
    signals = events.get("m_Signals") or []
    reactions = events.get("m_Events") or []
    if len(signals) != len(reactions):
        raise ValueError("SignalReceiver signal and reaction lists differ in length")
    rows = []
    for signal, reaction in zip(signals, reactions):
        target = store.follow(record, signal) if signal.get("m_PathID") else None
        calls = []
        for call in (reaction.get("m_PersistentCalls") or {}).get("m_Calls") or []:
            target_name, target_object = _object_name(store, record, call.get("m_Target"))
            calls.append({
                "target": reference(store, record, call.get("m_Target")),
                "targetGameObject": target_object,
                "targetName": target_name,
                "targetType": call.get("m_TargetAssemblyTypeName"),
                "method": call.get("m_MethodName"),
                "mode": call.get("m_Mode"),
                "arguments": json_pointers(call.get("m_Arguments") or {}),
                "callState": call.get("m_CallState"),
            })
        rows.append({
            "signal": reference(store, record, signal),
            "signalName": (target[0].tree(target[1]) or {}).get("m_Name") if target else None,
            "calls": calls,
        })
    return {"asset": identity(record, component_id), "gameObject": identity(*owner),
            "hierarchy": hierarchy, "reactions": rows}


def _director(store, record, path_id, owner, hierarchy, transforms):
    tree = record.tree(path_id)
    return {
        "asset": identity(record, path_id),
        "gameObject": identity(*owner),
        "hierarchy": hierarchy,
        "transforms": transforms,
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


# The views whose director and effect list a timeline prefab root carries:
# the NPC view, and the player's own view on a player fixture timeline. Both
# serialize `_playableDirector` and `_effectDataList` (TimelineEffectData) and
# bind their effects the same way.
TIMELINE_VIEW_SCRIPTS = ("NPCFixtureTimelineView", "PlayerFixtureTimelineView")


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
        "script": record.script_of(component_id),
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
                          "name": (root[0].tree(root[1]) or {}).get("m_Name", ""),
                          "directors": [], "fixtureTimelineViews": [],
                          "cinemachine": [], "signalReceivers": [], "views": [],
                          "rootScripts": [], "particleSystems": []}
                pending = [(root, [], [])]
                visited = set()
                # Every GameObject of the prefab with its hierarchy and local
                # transform chain, and the views whose GameObject fields are
                # resolved against them once the whole prefab is walked.
                placed = {}
                view_sources = []
                while pending:
                    owner, ancestors, chain = pending.pop()
                    record, game_object = owner
                    if owner in visited:
                        raise ValueError(f"repeated GameObject in prefab hierarchy: {path}")
                    visited.add(owner)
                    tree = record.tree(game_object)
                    hierarchy = [*ancestors, tree.get("m_Name", "")]
                    # The local transform chain from the prefab root down to
                    # this GameObject (its own Transform last).
                    own = chain
                    for item in tree.get("m_Component") or []:
                        pointer = item.get("component") if isinstance(item, dict) else item[1]
                        component = store.follow(record, pointer)
                        if (component is not None
                                and component[0].kinds[component[1]] in ("Transform", "RectTransform")):
                            own = [*chain, _local(component[0].tree(component[1]), tree.get("m_Name", ""))]
                    placed[(record.archive, game_object)] = (owner, hierarchy, own)
                    for item in tree.get("m_Component") or []:
                        pointer = item.get("component") if isinstance(item, dict) else item[1]
                        component = store.follow(record, pointer)
                        if component is None:
                            raise ValueError(f"unresolved prefab component: {path}, {hierarchy}")
                        component_file, component_id = component
                        component_kind = component_file.kinds[component_id]
                        if component_kind == "PlayableDirector":
                            prefab["directors"].append(_director(
                                store, component_file, component_id, owner, hierarchy, own))
                        elif (component_kind == "MonoBehaviour"
                              and component_file.script_of(component_id) in TIMELINE_VIEW_SCRIPTS):
                            prefab["fixtureTimelineViews"].append(_fixture_timeline_view(
                                store, component_file, component_id, owner, hierarchy))
                        elif component_kind == "MonoBehaviour":
                            script = component_file.script_of(component_id)
                            if owner == root:
                                prefab["rootScripts"].append(script)
                            if script and script.startswith("Cinemachine"):
                                prefab["cinemachine"].append({
                                    "asset": identity(component_file, component_id),
                                    "script": script,
                                    "gameObject": identity(*owner),
                                    "hierarchy": hierarchy,
                                    "transforms": own,
                                    "fields": _fields(component_file.tree(component_id)),
                                })
                            elif script == "SignalReceiver":
                                prefab["signalReceivers"].append(_signal_receiver(
                                    store, component_file, component_id, owner, hierarchy))
                            elif script in SCENE_VIEW_SCRIPTS:
                                view = {
                                    "asset": identity(component_file, component_id),
                                    "script": script,
                                    "gameObject": identity(*owner),
                                    "hierarchy": hierarchy,
                                    "fields": _fields(component_file.tree(component_id)),
                                }
                                prefab["views"].append(view)
                                view_sources.append((view, component_file, component_id))
                        elif component_kind == "ParticleSystem":
                            prefab["particleSystems"].append({
                                "asset": identity(component_file, component_id),
                                "gameObject": identity(*owner),
                                "hierarchy": hierarchy,
                            })
                        elif component_kind in ("Transform", "RectTransform"):
                            children = component_file.tree(component_id).get("m_Children") or []
                            for child in reversed(children):
                                target = store.follow(component_file, child)
                                if target is None:
                                    raise ValueError(f"unresolved prefab child: {path}, {hierarchy}")
                                child_owner = store.follow(target[0], target[0].tree(target[1])["m_GameObject"])
                                if child_owner is None:
                                    raise ValueError(f"unresolved child GameObject: {path}, {hierarchy}")
                                pending.append((child_owner, hierarchy, own))
                for view, component_file, component_id in view_sources:
                    view["gameObjects"] = _view_game_objects(
                        store, component_file, component_file.tree(component_id), placed)
                result.append(prefab)
    return result


def _view_game_objects(store, record, tree, placed):
    """The view's own GameObject fields that name a GameObject of the prefab.

    Each is given with its hierarchy and its local transform chain from the
    prefab root down (its own Transform last), so a player can place what the
    view parents under it (the cut-scene view's character root).  A field that
    names nothing, or an object outside the prefab, is not listed.
    """
    rows = {}
    for key, value in tree.items():
        if key in ENGINE_FIELDS or not isinstance(value, dict):
            continue
        if set(value) != {"m_FileID", "m_PathID"} or not value.get("m_PathID"):
            continue
        target = store.follow(record, value)
        if target is None:
            continue
        hit = placed.get((target[0].archive, target[1]))
        if hit is None:
            continue
        owner, hierarchy, chain = hit
        rows[key] = {"gameObject": identity(*owner), "hierarchy": hierarchy,
                     "transforms": chain}
    return rows


# The cut-scene clip that instantiates a prefab: `EffectClip.template.prefab`.
EFFECT_CLIP_SCRIPT = "EffectClip"


def _effect_prefab(store, root):
    """An instantiated effect prefab: its name, the root's scripts and every
    ParticleSystem in its hierarchy (with its GameObject path)."""
    record, game_object = root
    prefab = {"asset": identity(*root),
              "name": (record.tree(game_object) or {}).get("m_Name", ""),
              "rootScripts": [], "particleSystems": [], "unresolved": []}
    pending = [(root, [])]
    visited = set()
    while pending:
        owner, ancestors = pending.pop()
        if owner in visited:
            raise ValueError(f"repeated GameObject in an effect prefab: {prefab['name']}")
        visited.add(owner)
        owner_record, owner_id = owner
        tree = owner_record.tree(owner_id) or {}
        hierarchy = [*ancestors, tree.get("m_Name", "")]
        for item in tree.get("m_Component") or []:
            pointer = item.get("component") if isinstance(item, dict) else item[1]
            component = store.follow(owner_record, pointer)
            if component is None:
                prefab["unresolved"].append({"hierarchy": hierarchy,
                                             "pointer": json_pointers(pointer)})
                continue
            component_file, component_id = component
            kind = component_file.kinds[component_id]
            if kind == "MonoBehaviour" and owner == root:
                prefab["rootScripts"].append(component_file.script_of(component_id))
            elif kind == "ParticleSystem":
                prefab["particleSystems"].append({
                    "asset": identity(component_file, component_id),
                    "gameObject": identity(*owner),
                    "hierarchy": hierarchy,
                })
            elif kind in ("Transform", "RectTransform"):
                for child in reversed(component_file.tree(component_id).get("m_Children") or []):
                    target = store.follow(component_file, child)
                    child_owner = (store.follow(target[0], target[0].tree(target[1])["m_GameObject"])
                                   if target is not None else None)
                    if child_owner is None:
                        prefab["unresolved"].append({"hierarchy": hierarchy,
                                                     "pointer": json_pointers(child)})
                        continue
                    pending.append((child_owner, hierarchy))
    return prefab


def read_effect_prefabs(store, package):
    """The prefabs the package's effect clips instantiate, one record each,
    with the clips that name it. A null template is listed as such; a pointer
    the store cannot follow is kept unresolved, never dropped."""
    by_target, rows = {}, []
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour" or record.script_of(path_id) != EFFECT_CLIP_SCRIPT:
                continue
            clip = identity(record, path_id)
            pointer = ((record.tree(path_id) or {}).get("template") or {}).get("prefab") or {}
            if not pointer.get("m_PathID"):
                rows.append({"clips": [clip], "prefab": None})
                continue
            target = store.follow(record, pointer)
            if target is None:
                rows.append({"clips": [clip], "prefab": None,
                             "unresolved": reference(store, record, pointer)})
                continue
            if target in by_target:
                by_target[target]["clips"].append(clip)
                continue
            row = {"clips": [clip], "prefab": _effect_prefab(store, target)}
            by_target[target] = row
            rows.append(row)
    return rows
