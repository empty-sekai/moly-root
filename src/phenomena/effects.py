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

A canvas renderer draws only what a graphic component submits to it; a bare
canvas renderer is recorded under ``omitted``. Collider components have their
own records and mesh references, even when there is no visible mesh renderer.
Their geometry participates in particle world queries, not scene drawing.

One script component *is* exported, because it decides when an effect stops
existing rather than how it looks: a lifecycle wrapper that plays every emitter
under it, and on teardown stops emission, then destroys the object after its authored
grace time. Particle lifetimes do not extend that destruction deadline.  It also carries the rotation rule for a
camera-attached effect, and that rule is **not uniform** — see ``effectiveRotation``.
"""
import math

from core.particles import (TRAIL_MATERIAL_SLOT, decode_renderer,  # noqa: F401
                            decode_system)
from core.assets.packages import pairs
from core.colliders import COLLIDER_COMPONENTS, decode_collider

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
                   "collision geometry is carried by the collider record"),
}

# Render mode that draws each particle as a copy of a mesh.
MESH_RENDER_MODE = "Mesh"
# The shape whose birth positions come off a mesh surface rather than a solid.
MESH_SHAPE = "Mesh"

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
    delay = tree.get("_timeUntilDestroy")
    return {"timeUntilDestroy": float(delay) if delay is not None else None,
            "rotationType": ROTATION_TYPES.get(rotation, rotation)}


def _effective_rotation(kind, effectors):
    """Resolve the host's Setup call, not merely the serialized rotation field.

    SiteEnvironmentUtility.EmitEffect calls Setup(Normal) when a root component
    exists; only the AddComponent branch forwards the caller's requested type.
    Camera callers request Fix, sky/site callers request Normal. A component on
    a child does not change the root lookup. See phenomena-effect-lifecycle.
    """
    if kind not in ("sky", "camera", "site"):
        return None
    if kind != CAMERA_KIND or any(entry["node"] == "" for entry in effectors):
        return "normal"
    return ADDED_ROTATION_TYPE


def effective_lifecycle(kind, effectors):
    """Explicit factory contract consumed by runtime, with no downstream default.

    The added component's 2-second constructor value is a source transcription
    (SiteEnvironmentEffector constructor; canon phenomena-effect-lifecycle),
    not Unity's generic particle lifetime. Root serialized values override it.
    Auxiliary/unclassified prefabs do not acquire this factory by inference.
    """
    if kind not in ("sky", "camera", "site"):
        return None
    roots = [entry for entry in effectors if entry["node"] == ""]
    if len(roots) > 1:
        return None
    if roots:
        delay = roots[0].get("timeUntilDestroy")
        origin = "serializedRoot"
    else:
        delay = 2.0
        origin = "addedComponentConstructor"
    if not isinstance(delay, (float, int)) or not math.isfinite(delay) or delay < 0:
        return None
    return {"stopBehavior": "stopEmitting", "timeUntilDestroy": float(delay),
            "delaySource": origin}


def _vec(node, keys):
    return [float(node.get(key, 0.0)) for key in keys]


def _component_ids(tree):
    """Component path ids of one game object, in either serialized form."""
    for entry in tree.get("m_Component") or []:
        if isinstance(entry, dict):
            pointer = entry.get("component", entry)
            yield (pointer or {}).get("m_PathID", 0)



# The pass tag that puts a material's output into the effect buffer.  The keys
# in the serialized form are upper case; the tag values are not normalized, so
# any comparison must be case-insensitive (one package writes MysekaiEffect,
# another MysekaiObject, and a shader carries SHADOWCASTER beside them).
LIGHTMODE_TAG = "LIGHTMODE"


def light_modes(parsed):
    """The LightMode tag of each pass of a shader's parsed form, in pass order.

    One entry per pass, flattened subshader after subshader as serialized, with
    the pass's LIGHTMODE tag value or None where the tag is absent.  The
    tags live under m_State, not on the pass: a pass's own m_Tags is
    empty on every shader in these packages, so reading it would yield a
    well-formed list of Nones that says nothing.  Values are kept verbatim.

    承诺：声明了哪些 LightMode。不承诺：实际执行哪个 pass、MysekaiEffect 与
    UniversalForward 两个 pass 的颜色是否等价、keyword 变体、fallback 链、
    主画面行为、以及 env 域以外的任何情形。
    """
    modes = []
    for subshader in parsed.get("m_SubShaders") or []:
        for passes in subshader.get("m_Passes") or []:
            state = passes.get("m_State") or {}
            tags = {}
            for key, value in pairs((state.get("m_Tags") or {}).get("tags")):
                tags[str(key)] = value
            modes.append(tags.get(LIGHTMODE_TAG))
    return modes

def hierarchy(root_id, kinds, trees):
    """Node tree rooted at one prefab's game object, parents first.

    *trees* maps a path id to its typetree; only the objects actually needed are
    looked up, so a lazy mapping works here.  Returns ``(nodes, node_of)`` where
    ``node_of`` maps a game-object id to the node path components sit on.  The
    root's own path is ``""``, so paths read the same way animation and effect
    paths do elsewhere in this repository.

    ``siblingIndex`` is the node's position in its parent transform's
    ``m_Children`` list (``None`` on the root).  Instantiating a prefab walks
    the hierarchy depth first in that order and assigns each produced object
    the next instance id, so the order in which the engine sorts objects of
    one clone by instance id follows from these indices; the path ids the
    children are queued by here say nothing about it.
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
    queue = [(root_transform, None, "", None)]
    while queue:
        transform, parent_path, path, sibling_index = queue.pop(0)
        game_object = owner[transform]
        tree = trees[transform]
        game_object_tree = trees.get(game_object, {})
        # Which class the transform is, and whether the object carries the
        # renderer that goes with the rectangle one.  Neither changes a pixel:
        # the rectangle fields are at their factory values on every one of them,
        # nothing in these packages explains those fields, and the same kind of
        # node is authored both ways across phenomena.  They are recorded so the
        # objects are represented by something read rather than left out of the
        # count.
        components = {kinds.get(cid) for cid in _component_ids(game_object_tree)}
        nodes.append({
            "name": str(game_object_tree.get("m_Name", "")),
            "path": path,
            "parent": parent_path,
            "active": bool(game_object_tree.get("m_IsActive", 1)),
            "layer": game_object_tree.get("m_Layer"),
            "gameObjectPathId": str(game_object),
            "transformType": kinds.get(transform, "Transform"),
            "canvasRenderer": "CanvasRenderer" in components,
            "position": _vec(tree.get("m_LocalPosition", {}), "xyz"),
            "rotation": _vec(tree.get("m_LocalRotation", {}), "xyzw"),
            "scale": _vec(tree.get("m_LocalScale", {}), "xyz"),
            "siblingIndex": sibling_index,
        })
        node_of[game_object] = path
        order = [ref.get("m_PathID", 0) for ref in tree.get("m_Children", [])]
        for child in sorted(children.get(transform, [])):
            name = str(trees.get(owner[child], {}).get("m_Name", ""))
            index = order.index(child) if order.count(child) == 1 else None
            queue.append((child, path, name if path == "" else f"{path}/{name}", index))
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


def _geometry_references(mesh, slot=0):
    """Per-source mesh references; a reused content hash never steals provenance."""
    return [{"file": mesh["file"], "node": item["node"],
             "geometrySchemaVersion": 1, "meshSlot": slot,
             "coordinates": "gltf-reflect-x-flip-v",
             "sha256": mesh.get("sha256"), "bytes": mesh.get("bytes"),
             "source": mesh.get("meshSource"), "bounds": mesh.get("sourceBounds")}
            for item in mesh.get("meshes", [])]


def decode_effect(root_id, kinds, trees, resolve_material, kind=None, site=None,
                  script_of=None, resolve_mesh=None, resolve_animation=None):
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
    colliders = []
    animation_components = []
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
                shape = system.get("shape") or {}
                if shape.get("type") == MESH_SHAPE and resolve_mesh is not None:
                    # A mesh-shaped emitter draws its birth positions from a
                    # mesh's surface.  Without the reference there is no surface
                    # to draw from, and the shape cannot be carried out at all.
                    mesh, gap = resolve_mesh(
                        (trees[component].get("ShapeModule") or {}).get("m_Mesh") or {})
                    shape["meshes"] = (_geometry_references(mesh) if mesh is not None else [])
                    if gap is not None:
                        unsupported.append(dict(gap, node=path))
                entry = emitters.setdefault(path, {"node": path})
                entry["system"] = system
                entry["systemPathId"] = str(component)
                for gap in gaps:
                    unsupported.append(dict(gap, node=path))
            elif component_kind == "ParticleSystemRenderer":
                tree = trees[component]
                entry = emitters.setdefault(path, {"node": path})
                slots = len(tree.get("m_Materials") or [])
                trail = (resolve_material(tree, TRAIL_MATERIAL_SLOT)
                         if slots > TRAIL_MATERIAL_SLOT else None)
                entry["renderer"] = decode_renderer(tree, resolve_material(tree), trail)
                entry["rendererPathId"] = str(component)
                if entry["renderer"]["renderMode"] == MESH_RENDER_MODE:
                    renderer = entry["renderer"]
                    renderer["meshes"], renderer["meshSlots"] = [], []
                    # Preserve all serialized slots and their positions. A null
                    # secondary slot is not a missing-source error or mesh zero.
                    for slot in range(4):
                        suffix = str(slot) if slot else ""
                        pointer = tree.get(f"m_Mesh{suffix}") or {}
                        reference = {"fileId": int(pointer.get("m_FileID", 0)),
                                     "pathId": str(pointer.get("m_PathID", 0))}
                        descriptor = {"slot": slot, "reference": reference,
                            "weight": tree.get(f"m_MeshWeighting{suffix}"), "meshes": []}
                        renderer["meshSlots"].append(descriptor)
                        if slot and not pointer.get("m_PathID"):
                            continue
                        mesh, gap = ((None, None) if resolve_mesh is None else resolve_mesh(pointer))
                        if mesh is not None:
                            descriptor["meshes"] = _geometry_references(mesh, slot=slot)
                            renderer["meshes"].extend(descriptor["meshes"])
                        if gap is not None:
                            unsupported.append(dict(gap, node=path, meshSlot=slot))
            elif component_kind in COLLIDER_COMPONENTS:
                tree = trees[component]
                entry = decode_collider(tree, trees.get(game_object, {}),
                                        component_kind, path)
                entry["componentPathId"] = str(component)
                entry["meshes"] = []
                if component_kind == "MeshCollider":
                    pointer = tree.get("m_Mesh") or {}
                    if resolve_mesh is None:
                        unsupported.append({"node": path, "component": component_kind,
                                            "reason": "collider mesh resolver not supplied"})
                    else:
                        mesh, gap = resolve_mesh(pointer)
                        if mesh is not None:
                            entry["meshes"] = _geometry_references(mesh)
                        if gap is not None:
                            unsupported.append(dict(gap, node=path,
                                                    component=component_kind))
                colliders.append(entry)
            elif component_kind in ("Animator", "PlayableDirector") and resolve_animation is not None:
                reference = resolve_animation(component)
                if reference is not None:
                    animation_components.append({"node": path, "component": component_kind,
                                                 **reference})
                else:
                    unsupported.append({"node": path, "component": component_kind,
                                        "reason": "animation component source record unresolved"})
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
              "colliders": colliders,
              "collisionSchemaVersion": 1,
              "animationSchemaVersion": 1,
              "animationComponents": animation_components,
              "effectors": effectors,
              "effectiveRotation": _effective_rotation(kind, effectors),
              "lifecycle": effective_lifecycle(kind, effectors),
              "omitted": omitted}
    return effect, unsupported
