"""Effect prefabs of one phenomenon, exported as emitter parameters.

Rain, snow, meteors and the like are not baked animations: each is a small node
tree whose nodes carry particle emitters, so what a consumer needs is the emitter
parameters — shape, emission and bursts, lifetime, size and colour over lifetime,
and the material each emitter draws with — and then it simulates.  That encoding
is shared with the other effect packages in this repository, so the JSON below has
the same shape: one entry per emitting node, ``{node, system, renderer}``.

An emitter that draws its particles as copies of a mesh carries that mesh too, since
without it there is nothing to draw.

A phenomenon has three kinds of effect, and the kind decides where it is placed:
a *sky* effect is anchored to the sky, a *camera* effect is anchored to the
camera, and a *site* effect belongs to one named site.  Materials commonly live
in a companion package rather than next to the emitter, so resolution is handed
in by the caller; an unresolved pointer stays visible instead of becoming null.

Two components on these nodes are not exported, and both are decisions rather than
gaps.  A canvas renderer draws only what a *graphic* component submits to it, and
these prefabs carry no graphic of any kind, so it contributes nothing to draw.  A
mesh collider paired with a mesh filter and *no* mesh renderer is collision surface,
not geometry: it is invisible by construction, its mesh is a site's navigation
surface reached from another package, and what collides with it is a particle
module.  Both are reported under ``omitted`` with what they point at, so a consumer
can see they were read and judged rather than missed.

One script component *is* exported, because it decides when an effect stops
existing rather than how it looks: a lifecycle wrapper that plays every emitter
under it, and on teardown keeps the object alive until its emitters have finished
and a fixed grace time has passed.  It also carries the rotation rule for a
camera-attached effect, and that rule is **not uniform** — see ``effectiveRotation``.
"""
from core.particles import (TRAIL_MATERIAL_SLOT, decode_renderer,  # noqa: F401
                            decode_system)

# Components these prefabs use.  Anything else on a node is reported.
MODELLED_COMPONENTS = ("Transform", "RectTransform", "ParticleSystem",
                       "ParticleSystemRenderer")

# The lifecycle wrapper, and the rotation rule it can be set to.
EFFECTOR_SCRIPT = "SiteEnvironmentEffector"
ROTATION_TYPES = {0: "normal", 1: "fix"}
# What the host asks for when it has to add the component itself.
ADDED_ROTATION_TYPE = "fix"
# The kind of effect whose rotation rule is established.
CAMERA_KIND = "camera"

# Components read and deliberately not exported, with why.
OMITTED_COMPONENTS = {
    "CanvasRenderer": ("a canvas renderer draws only what a graphic component "
                       "submits to it, and these prefabs carry no graphic"),
    "MeshFilter": ("a mesh filter with no mesh renderer beside it draws nothing; "
                   "here it names the mesh of the collider on the same node"),
    "MeshCollider": ("collision surface, not geometry: the node has no mesh "
                     "renderer, and the mesh is a site's navigation surface in "
                     "another package"),
}

# Render mode that draws each particle as a copy of a mesh.
MESH_RENDER_MODE = "Mesh"

UNMODELLED_COMPONENT = "prefab component not modelled"


def _node_resolver(trees, node_of):
    """Resolve a pointer at a component to the node path that component sits on.

    Sub-emitters and collision planes point at other objects of the same prefab.
    A raw path id means nothing to a consumer, and the node path is what the rest
    of this document is keyed by.  A pointer into another package cannot be
    followed from here and resolves to nothing, which the caller reports.
    """
    def resolve(pointer):
        pointer = pointer or {}
        if pointer.get("m_FileID", 0):
            return None
        tree = trees.get(pointer.get("m_PathID", 0))
        if tree is None:
            return None
        return node_of.get((tree.get("m_GameObject") or {}).get("m_PathID", 0))
    return resolve


def _effector(tree):
    """The lifecycle wrapper's own fields.

    ``timeUntilDestroy`` is the grace time after the emitters stop before the
    effect is destroyed, so on a change of phenomenon the outgoing effect's
    particles finish their lives while the incoming one is already emitting.
    """
    rotation = tree.get("_initializeRotateType")
    return {"timeUntilDestroy": round(float(tree.get("_timeUntilDestroy", 0.0)), 6),
            "rotationType": ROTATION_TYPES.get(rotation, rotation)}


def _effective_rotation(kind, effectors):
    """Which rotation rule a camera-attached effect actually ends up with.

    The host looks for the lifecycle component **on the prefab root only**.  When
    it finds one it starts it with no argument, so the component's own serialized
    rotation type wins; when it does not, the host adds one and asks for ``fix``,
    which counter-rotates the effect every frame so it stays aligned to the world
    while its position still follows the camera.  The two branches disagree, so
    this is per-prefab and not a constant.

    ``None`` for any other kind of effect: how those are attached is not
    established here, and guessing would be worse than saying nothing.
    """
    if kind != CAMERA_KIND:
        return None
    for entry in effectors:
        if entry["node"] == "":
            return entry["rotationType"]
    return ADDED_ROTATION_TYPE



def _vec(node, keys):
    return [round(float(node.get(key, 0.0)), 6) for key in keys]


def _component_ids(tree):
    """Component path ids of one game object, in either serialized form."""
    for entry in tree.get("m_Component") or []:
        if isinstance(entry, dict):
            pointer = entry.get("component", entry)
            yield (pointer or {}).get("m_PathID", 0)


def hierarchy(root_id, kinds, trees):
    """Node tree rooted at one prefab's game object, parents first.

    *trees* maps a path id to its typetree; only the objects actually needed are
    looked up, so a lazy mapping works here.  Returns ``(nodes, node_of)`` where
    ``node_of`` maps a game-object id to the node path components sit on.  The
    root's own path is ``""``, so paths read the same way animation and effect
    paths do elsewhere in this repository.
    """
    transform_of, owner = {}, {}
    for path_id, kind in kinds.items():
        if kind not in ("Transform", "RectTransform"):
            continue
        game_object = trees[path_id].get("m_GameObject", {}).get("m_PathID", 0)
        transform_of[game_object] = path_id
        owner[path_id] = game_object

    children = {}
    for path_id in owner:
        father = trees[path_id].get("m_Father", {}).get("m_PathID", 0)
        children.setdefault(father, []).append(path_id)

    nodes, node_of = [], {}
    root_transform = transform_of.get(root_id)
    if root_transform is None:
        return nodes, node_of
    queue = [(root_transform, None, "")]
    while queue:
        transform, parent_path, path = queue.pop(0)
        game_object = owner[transform]
        tree = trees[transform]
        game_object_tree = trees.get(game_object, {})
        nodes.append({
            "name": str(game_object_tree.get("m_Name", "")),
            "path": path,
            "parent": parent_path,
            "active": bool(game_object_tree.get("m_IsActive", 1)),
            "position": _vec(tree.get("m_LocalPosition", {}), "xyz"),
            "rotation": _vec(tree.get("m_LocalRotation", {}), "xyzw"),
            "scale": _vec(tree.get("m_LocalScale", {}), "xyz"),
        })
        node_of[game_object] = path
        for child in sorted(children.get(transform, [])):
            name = str(trees.get(owner[child], {}).get("m_Name", ""))
            queue.append((child, path, name if path == "" else f"{path}/{name}"))
    return nodes, node_of


def _omission(node, component, kind, kinds, trees, component_ids):
    """One deliberately unexported component, with what it points at.

    Returns ``None`` when the component is *not* one of the omitted cases here,
    which keeps the decision narrow: a mesh filter next to a mesh renderer is
    visible geometry and is not omitted.
    """
    if kind not in OMITTED_COMPONENTS:
        return None
    kinds_here = {kinds.get(other) for other in component_ids}
    if kind in ("MeshFilter", "MeshCollider") and "MeshRenderer" in kinds_here:
        return None
    entry = {"node": node, "component": kind, "reason": OMITTED_COMPONENTS[kind]}
    pointer = (trees.get(component) or {}).get("m_Mesh")
    if isinstance(pointer, dict):
        entry["mesh"] = {"fileId": pointer.get("m_FileID"),
                         "pathId": pointer.get("m_PathID")}
    return entry


def decode_effect(root_id, kinds, trees, resolve_material, kind=None, site=None,
                  script_of=None, resolve_mesh=None):
    """One effect prefab: its node tree and the emitters sitting on those nodes.

    *resolve_material* maps a renderer typetree and a material slot to the
    material that slot draws with, and *resolve_mesh* maps a mesh pointer to the
    geometry reference of a mesh-mode emitter.  *script_of* names the class a
    script component instantiates, so a component this module does not model is
    reported by what it is rather than as an anonymous behaviour.  Returns
    ``(effect, unsupported)``.
    """
    nodes, node_of = hierarchy(root_id, kinds, trees)
    resolve_node = _node_resolver(trees, node_of)
    unsupported = []
    omitted = []
    effectors = []
    emitters = {}
    for game_object, path in sorted(node_of.items(), key=lambda item: item[1]):
        component_ids = list(_component_ids(trees.get(game_object, {})))
        for component in component_ids:
            component_kind = kinds.get(component)
            if component_kind is None:
                unsupported.append({"node": path, "component": None,
                                    "reason": "component not in this package"})
                continue
            if component_kind in ("Transform", "RectTransform"):
                continue
            if component_kind == "ParticleSystem":
                system, gaps = decode_system(trees[component], resolve_node)
                emitters.setdefault(path, {"node": path})["system"] = system
                for gap in gaps:
                    unsupported.append(dict(gap, node=path))
            elif component_kind == "ParticleSystemRenderer":
                tree = trees[component]
                entry = emitters.setdefault(path, {"node": path})
                slots = len(tree.get("m_Materials") or [])
                trail = (resolve_material(tree, TRAIL_MATERIAL_SLOT)
                         if slots > TRAIL_MATERIAL_SLOT else None)
                entry["renderer"] = decode_renderer(tree, resolve_material(tree), trail)
                if entry["renderer"]["renderMode"] == MESH_RENDER_MODE:
                    mesh, gap = ((None, None) if resolve_mesh is None
                                 else resolve_mesh(tree.get("m_Mesh") or {}))
                    entry["renderer"]["meshes"] = []
                    if mesh is not None:
                        entry["renderer"]["meshes"] = [
                            {"file": mesh["file"], "node": item["node"]}
                            for item in mesh.get("meshes", [])
                        ]
                    if gap is not None:
                        unsupported.append(dict(gap, node=path))
            else:
                omission = _omission(path, component, component_kind, kinds,
                                     trees, component_ids)
                if omission is not None:
                    omitted.append(omission)
                    continue
                name = component_kind
                if component_kind == "MonoBehaviour" and script_of is not None:
                    name = script_of(component) or component_kind
                if name == EFFECTOR_SCRIPT:
                    effectors.append(dict(_effector(trees[component]), node=path))
                    continue
                unsupported.append({"node": path, "component": name,
                                    "reason": UNMODELLED_COMPONENT})
    effect = {"kind": kind, "site": site, "nodes": nodes,
              "particles": [emitters[path] for path in sorted(emitters)],
              "effectors": effectors,
              "effectiveRotation": _effective_rotation(kind, effectors),
              "omitted": omitted}
    return effect, unsupported
