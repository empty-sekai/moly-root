"""Particle emitters of the furniture and cut-scene packages.

The fixture interface, the story cut-scene player, and the furniture timeline
views hold 2207 ``ParticleSystem`` components between them (the count the source
census reports: 669 in ``fixture-interface``, 1380 in ``cutscene-timeline``, 158
in ``fixture-timeline``).  None of them are exported by any extractor: the
fixture pass reads attach points, the placement grid and the geometry; the
timeline passes read track trees, clips and clip targets.  This module decodes
them with :mod:`core.particles` -- the same emitter normalisation the effect
packages decode with -- and writes one document per package that emits.

Each document holds one entry per ``ParticleSystem`` object, ``{node, system,
renderer}``, where the renderer is the ``ParticleSystemRenderer`` component of
the same game object (a renderer belongs to its node, and a node may carry
several systems, so the same renderer appears on every system that shares its
node).  The trail material is read out of the renderer's second material slot,
which is where a running trail module's material lives -- there is no separate
field on disk.

Two decisions define the node naming.  Node paths are built root-name-first:
the package root's own name is the path of the root node, so no emitter path is
ever an empty string, and a consumer can match emitters to the geometry export
by name.  Several packages hold two parallel root trees with identical names
(the model-import tree and the runtime prefab tree), and emitters can sit in
either or both; a path can therefore repeat.  Every emitter also carries the
game object's path id, which is what the geometry export records as
``extras.gameObjectId``, so a repeated path is still a single emitter and never
two entries merged into one.

Materials are commonly in a companion package rather than next to the emitter
(the ``mysekai__effect__fixture__fx_*`` packages), so the renderer's material
slots are resolved against the whole store: a slot resolves when the pointer's
archive is in the store, and otherwise stays visibly unresolved as
``{"external": true, ...}`` instead of coming out as null.  A slot whose
pointer carries no path id is an authored null -- an empty slot, not a gap --
and is not reported.  The same shape holds for the mesh a mesh-drawn renderer
samples, again inside the renderer entry rather than as null.

``unsupported`` lists what the decoder named as gaps or what this module could
not pair, every entry carrying a ``module`` and a ``node``.  Entries are never
folded into an "other" bucket: an enabled particle module this decoder does not
model is listed by its module name, a sub-emitter or collision plane that the
package cannot resolve by its reason, an unreadable component by the exception
text, and an unpaired system or renderer by what the node lacks.

The summary counts are per package: ``emitters`` is the number of
``ParticleSystem`` objects in the package (the count a source census must match),
``renderers`` the number of ``ParticleSystemRenderer`` objects, ``materials``
the distinct material objects the package's renderers resolve to, and
``textures`` the distinct images those materials reference (each written once,
keyed by the archive and path id of the image, regardless of how many materials
name it).

One document is written per package that emits; a package with no particle
system is recorded in the index with a zero count and no document, so a census
compare covers every package and a missing document reads as "contains nothing",
never as "not read".  A package whose bundle file is not on disk is recorded as
``missing`` with its reason rather than as a zero, because the loader answers a
missing path with zero objects and silence.
"""
from pathlib import Path

import UnityPy

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

from core.assets.router import route
from core.jsonio import write_json
from core.particles import TRAIL_MATERIAL_SLOT, decode_renderer, decode_system
from phenomena import texarray

TRANSFORMS = ("Transform", "RectTransform")

MISSING_BUNDLE = "bundle file not found; it was not opened"
SYSTEM_UNREADABLE = "particle system typetree could not be read"
RENDERER_UNREADABLE = "particle renderer typetree could not be read"
NO_RENDERER_ON_NODE = "no ParticleSystemRenderer on the emitter's node"
NO_SYSTEM_ON_NODE = "renderer has no ParticleSystem on its node"
MATERIAL_UNRESOLVED = "material pointer does not resolve in this store"
MESH_UNRESOLVED = "mesh pointer does not resolve in this store"

# The three domains whose packages this module extracts.  A package the router
# assigns to another domain can be in the store (loaded for its archives, so a
# material or mesh pointer can resolve against it) but is never extracted.
DOMAINS = ("fixture-interface", "cutscene-timeline", "fixture-timeline")

EMPTY_SUMMARY = {
    "emitters": 0, "renderers": 0, "materials": 0, "textures": 0,
    "unsupported": 0, "materialSlotsUnresolved": 0,
    "meshPointersUnresolved": 0, "systemReadFailures": 0,
    "rendererReadFailures": 0,
}


def _archive(record, file_id):
    """The archive name an external file id names, or ``None``."""
    archives = [str(external).rsplit("/", 1)[-1] for external in record.externals]
    return archives[file_id - 1] if 0 < file_id <= len(archives) else None


def _paths(record):
    """The node path each game object sits at, root-name-first.

    Each package may hold several root trees, and the same node name can repeat
    across them, so the path is a name string and the game object id is the
    identity (it is what the geometry export records as ``extras.gameObjectId``).
    A game object with no transform of its own gets no path.
    """
    transforms, names = {}, {}
    for pid, kind in record.kinds.items():
        if kind in TRANSFORMS:
            transforms[pid] = record.tree(pid)
        elif kind == "GameObject":
            names[pid] = str(record.tree(pid).get("m_Name", ""))
    parent_of = {pid: (tree.get("m_Father") or {}).get("m_PathID", 0)
                 for pid, tree in transforms.items()}
    children = {}
    for pid in transforms:
        children.setdefault(parent_of[pid], []).append(pid)
    roots = [pid for pid in transforms if parent_of[pid] not in transforms]
    path_of = {}
    for root in sorted(roots):
        go = (transforms[root].get("m_GameObject") or {}).get("m_PathID", 0)
        queue = [(root, names.get(go) or str(root))]
        while queue:
            tpid, path = queue.pop(0)
            go = (transforms[tpid].get("m_GameObject") or {}).get("m_PathID", 0)
            path_of[go] = path
            for child in sorted(children.get(tpid, [])):
                cgo = (transforms[child].get("m_GameObject") or {}).get("m_PathID", 0)
                queue.append((child, f"{path}/{names.get(cgo) or child}"))
    return path_of


def _node_resolver(record, path_of):
    """Resolve a pointer at a component to the node path it names.

    Sub-emitters and collision planes point at other objects of the same
    package.  A raw path id means nothing to a consumer, and the node path is
    what the rest of the document is keyed by.  A pointer into another package
    cannot be followed from here and resolves to nothing, which the decoder
    reports as a gap.
    """
    def resolve(pointer):
        pointer = pointer or {}
        if pointer.get("m_FileID", 0):
            return None
        pid = pointer.get("m_PathID", 0)
        if pid not in record.kinds:
            return None
        tree = record.tree(pid)
        go = (tree.get("m_GameObject") or {}).get("m_PathID", 0)
        return path_of.get(go) or path_of.get(pid)
    return resolve


class _Images:
    """Write the images a material references, once each, into one directory."""

    def __init__(self, directory):
        self.directory = directory
        self.written = {}
        self.failed = []

    def _array(self, record, path_id, tree, name, stem):
        """Write every layer of a texture array, in layer order."""
        reference = {"name": name, "kind": "Texture2DArray",
                     "width": tree.get("m_Width"), "height": tree.get("m_Height"),
                     "layers": tree.get("m_Depth"),
                     "graphicsFormat": tree.get("m_Format"),
                     "colorSpace": tree.get("m_ColorSpace"),
                     "mipCount": tree.get("m_MipCount"), "files": []}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            layers = list(record.objects[path_id].read().images)
            for index, image in enumerate(layers):
                file_name = f"{stem}.{index}.png"
                image.save(self.directory / file_name)
                reference["files"].append(file_name)
            if len(layers) != reference["layers"]:
                self.failed.append({"image": name,
                                    "reason": f"{len(layers)} layers decoded but the "
                                              f"asset declares {reference['layers']}"})
        except Exception as exc:              # unreadable texture format
            self.failed.append({"image": name,
                                "reason": f"{type(exc).__name__}: {exc}"})
            reference["files"] = []
        return reference

    def reference(self, target):
        """Write the image a pointer resolves to and return how to find it.

        Returns ``None`` when the pointer resolves to nothing.  The file name is
        qualified by the package that owns the image, because texture names
        repeat across packages and a flat name would let one package overwrite
        another's image.  A ``Texture2DArray`` is written one file per layer and
        reported with ``files`` instead of ``file``, so a caller can keep it
        apart from a single-image binding.
        """
        if target is None:
            return None
        record, path_id = target
        key = (record.archive, path_id)
        if key in self.written:
            return dict(self.written[key])
        tree = record.tree(path_id)
        name = str(tree.get("m_Name", "") or "image")
        kind = record.kinds.get(path_id)
        if kind == "Texture2DArray":
            self.written[key] = self._array(record, path_id, tree, name,
                                            f"{record.bundle}__{name}")
            return dict(self.written[key])
        if kind != "Texture2D":
            self.failed.append({"image": name,
                                "reason": f"{kind} is not a single image"})
            self.written[key] = {"name": name, "file": None}
            return dict(self.written[key])
        reference = {"name": name, "file": f"{record.bundle}__{name}.png"}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            record.objects[path_id].read().image.save(
                self.directory / reference["file"])
        except Exception as exc:                # unreadable image format
            self.failed.append({"image": name,
                                "reason": f"{type(exc).__name__}: {exc}"})
            reference = {"name": name, "file": None}
        self.written[key] = reference
        return dict(reference)


class _Materials:
    """Resolve one material slot of a renderer against the store, once each.

    Keys are the ''(archive, path id)'' of the resolved material object, so the
    same object referenced by several renderers -- or by the renderer's own
    particle slot and trail slot -- is decoded once and counted once.
    """

    def __init__(self, store, images):
        self.store = store
        self.images = images
        self.cache = {}

    def _decode(self, record, path_id):
        """Material name, shader, queue, texture bindings, and scalars.

        Texture arrays are kept in their own map: they are sampled with a layer
        coordinate, so putting them next to single-image bindings would let a
        consumer treat one as the other.  Their sampling parameters are resolved
        here (:mod:`phenomena.texarray`) so the consumer does not have to know
        the encoding.
        """
        tree = record.tree(path_id)
        properties = tree.get("m_SavedProperties") or {}
        keywords = [str(word) for word in tree.get("m_ValidKeywords") or []]
        floats = {name: round(float(value), 6)
                  for name, value in _pairs(properties.get("m_Floats"))
                  if isinstance(value, (int, float))}
        textures = {}
        arrays = {}
        scale_offset = {}
        for name, value in _pairs(properties.get("m_TexEnvs")):
            entry = value or {}
            scale = entry.get("m_Scale") or {}
            offset = entry.get("m_Offset") or {}
            scale_offset[name] = [round(float(scale.get("x", 1.0)), 6),
                                  round(float(scale.get("y", 1.0)), 6),
                                  round(float(offset.get("x", 0.0)), 6),
                                  round(float(offset.get("y", 0.0)), 6)]
            reference = self.images.reference(
                self.store.follow(record, entry.get("m_Texture") or {}))
            if reference is not None and "files" in reference:
                prefix = texarray.slot_prefix(str(name)) or str(name)
                arrays[name] = dict(reference,
                                    sampling=texarray.sampling(prefix, floats,
                                                              keywords))
                continue
            if reference is not None:
                textures[name] = reference
        shader = None
        shader_target = self.store.follow(record, tree.get("m_Shader") or {})
        if shader_target is not None:
            shader_record, shader_id = shader_target
            if shader_record.kinds.get(shader_id) == "Shader":
                parsed = shader_record.tree(shader_id).get("m_ParsedForm") or {}
                shader = (str(parsed.get("m_Name"))
                          if parsed.get("m_Name")
                          else str(shader_record.tree(shader_id).get("m_Name", "")))
                shader = str(shader) or None
        return {
            "name": tree.get("m_Name"),
            "shader": shader,
            "renderQueue": tree.get("m_CustomRenderQueue", -1),
            "textures": textures,
            "textureArrays": arrays,
            "textureScaleOffset": scale_offset,
            "floats": floats,
            "colors": {name: [round(value.get(component, 0.0), 6)
                              for component in "rgba"]
                       for name, value in _pairs(properties.get("m_Colors"))
                       if isinstance(value, dict)},
        }

    def resolve(self, record, renderer_tree, slot=0):
        """One material slot of one renderer, keeping unresolved states visible."""
        materials = renderer_tree.get("m_Materials") or []
        if slot >= len(materials):
            return None
        pointer = materials[slot] or {}
        if not pointer.get("m_PathID", 0):
            return None
        target = self.store.follow(record, pointer)
        if target is None:
            return {"external": True, "fileId": pointer.get("m_FileID", 0),
                    "pathId": pointer.get("m_PathID", 0),
                    "archive": _archive(record, pointer.get("m_FileID", 0)),
                    "reason": MATERIAL_UNRESOLVED}
        material_record, material_id = target
        key = (material_record.archive, material_id)
        if key not in self.cache:
            self.cache[key] = self._decode(material_record, material_id)
        return self.cache[key]


def _pairs(entries):
    """Unity serialises property maps as (name, value) pairs; accept either form."""
    for entry in entries or []:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            yield entry[0], entry[1]
        elif isinstance(entry, dict):
            yield entry.get("first"), entry.get("second")


def _pointer(store, record, pointer):
    """One pointer as ``{fileId, pathId, archive, resolved, name, kind}``.

    ``resolved`` is false for a pointer whose archive is not in the store; the
    archive name is recorded so the gap says *which* archive it wanted.  Returns
    ``None`` for a pointer with no path id (an authored null, not a gap).
    """
    pointer = pointer or {}
    if not pointer.get("m_PathID", 0):
        return None
    target = store.follow(record, pointer)
    if target is None:
        return {"fileId": pointer.get("m_FileID", 0),
                "pathId": pointer.get("m_PathID", 0),
                "archive": _archive(record, pointer.get("m_FileID", 0)),
                "resolved": False, "name": None, "kind": None}
    target_record, path_id = target
    try:
        name = str(target_record.tree(path_id).get("m_Name", "") or path_id)
    except Exception:                                       # unreadable object
        name = str(path_id)
    return {"fileId": pointer.get("m_FileID", 0), "pathId": path_id,
            "archive": target_record.archive, "resolved": True,
            "name": name,
            "kind": target_record.kinds.get(path_id)}


def _mesh_points(store, record, renderer_tree):
    """The mesh slots of one renderer, as resolved-or-explicit references.

    The four slots are sampled in the renderer's own distribution order; slot 0
    is the plain ``m_Mesh``.  An unresolved pointer stays visible inside the
    renderer entry, never nulled away.
    """
    slots, unresolved = [], 0
    for slot, key in enumerate(("m_Mesh", "m_Mesh1", "m_Mesh2", "m_Mesh3")):
        pointer = _pointer(store, record, renderer_tree.get(key) or {})
        if pointer is None:
            continue
        if not pointer["resolved"]:
            unresolved += 1
        slots.append({"slot": slot, **pointer})
    return slots, unresolved


def _shape_mesh(store, record, tree, system, path, unsupported):
    """The birth-position mesh of a mesh-shaped emitter.

    :func:`core.particles.decode_system` normalises the shape parameters but
    not the shape's own mesh pointer; it is attached here so a consumer is not
    left holding a ``Mesh`` shape with nowhere to sample from.
    """
    shape = system.get("shape")
    if not shape or shape.get("type") != "Mesh":
        return
    pointer = _pointer(store, record,
                       (tree.get("ShapeModule") or {}).get("m_Mesh") or {})
    shape["meshes"] = [pointer] if pointer is not None else []
    if pointer is None:
        unsupported.append({"node": path, "module": "ShapeModule",
                            "reason": "mesh-shaped emitter has no mesh pointer"})
    elif not pointer["resolved"]:
        unsupported.append({"node": path, "module": "ShapeModule",
                            "reason": MESH_UNRESOLVED,
                            "mesh": {"fileId": pointer["fileId"],
                                     "pathId": pointer["pathId"],
                                     "archive": pointer["archive"]}})


def _emitters(store, record, materials):
    """All emitters of one serialized file, plus the file's gaps.

    Returns ``(emitters, unsupported, counts)``: ``emitters`` holds exactly one
    entry per ``ParticleSystem`` object (an entry whose system could not be read
    carries ``systemError``), each carrying the ``ParticleSystemRenderer`` that
    sits on the same node or no renderer at all.  A node can hold several
    systems and one renderer, so the renderer repeats on each of its systems
    rather than each system being its own entry with its own renderer.  Every
    ``unsupported`` entry carries a ``module`` and a ``node``, and ``counts``
    holds the per-file tallies that the package sums.
    """
    path_of = _paths(record)
    resolve_node = _node_resolver(record, path_of)
    systems = []
    renderer_of = {}
    renderer_failed = set()
    counts = {"systems": 0, "renderers": 0, "systemReadFailures": 0,
              "rendererReadFailures": 0, "materialSlotsUnresolved": 0,
              "meshPointersUnresolved": 0}
    unsupported = []

    def node_of(tree):
        """Node path of the game object a component sits on, or ``None``."""
        return path_of.get((tree.get("m_GameObject") or {}).get("m_PathID", 0))

    for path_id, kind in sorted(record.kinds.items()):
        if kind not in ("ParticleSystem", "ParticleSystemRenderer"):
            continue
        try:
            tree = record.tree(path_id)
        except Exception as exc:
            # The typetree would not read, but the object's raw form still holds
            # the game object pointer, so the component's node is reported.
            node, go = None, 0
            try:
                raw = record.objects[path_id].read()
                go = (getattr(raw, "m_GameObject", None) or {}).get("m_PathID", 0)
                node = path_of.get(go)
            except Exception:
                pass
            if kind == "ParticleSystem":
                counts["systems"] += 1
                counts["systemReadFailures"] += 1
                gap_kind = SYSTEM_UNREADABLE
                systems.append({"node": node, "gameObjectId": go,
                                "systemPathId": path_id, "system": None,
                                "systemError": f"{type(exc).__name__}: {exc}"})
            else:
                counts["renderers"] += 1
                counts["rendererReadFailures"] += 1
                gap_kind = RENDERER_UNREADABLE
                if go:
                    renderer_failed.add(go)
            unsupported.append({"node": node, "module": kind,
                                "reason": f"{kind} {gap_kind}: {exc}",
                                "pathId": path_id})
            continue
        go = (tree.get("m_GameObject") or {}).get("m_PathID", 0)
        node = node_of(tree)
        if kind == "ParticleSystem":
            counts["systems"] += 1
            try:
                decoded, gaps = decode_system(tree, resolve_node)
            except Exception as exc:
                systems.append({"node": node, "gameObjectId": go,
                                "systemPathId": path_id,
                                "system": None,
                                "systemError": f"{type(exc).__name__}: {exc}"})
                counts["systemReadFailures"] += 1
                unsupported.append({"node": node, "module": kind,
                                    "reason": f"{kind} {SYSTEM_UNREADABLE}: {exc}",
                                    "pathId": path_id})
                continue
            system = decoded
            _shape_mesh(store, record, tree, system, node, unsupported)
            for gap in gaps:
                unsupported.append(dict(gap, node=node))
            systems.append({"node": node, "gameObjectId": go,
                            "systemPathId": path_id, "system": system,
                            "systemError": None})
        else:
            counts["renderers"] += 1
            if go in renderer_of:
                unsupported.append({"node": node, "module": kind,
                                    "reason": "more than one renderer on a node "
                                              "is not modelled; the first one "
                                              "is reported",
                                    "pathId": path_id})
                continue
            try:
                slots = list(tree.get("m_Materials") or [])
                trail = (materials.resolve(record, tree, TRAIL_MATERIAL_SLOT)
                         if len(slots) > TRAIL_MATERIAL_SLOT else None)
                material = materials.resolve(record, tree)
                if isinstance(material, dict) and material.get("external"):
                    counts["materialSlotsUnresolved"] += 1
                decoded = decode_renderer(tree, material, trail)
            except Exception as exc:
                counts["rendererReadFailures"] += 1
                unsupported.append({"node": node, "module": kind,
                                    "reason": f"{kind} {RENDERER_UNREADABLE}: {exc}",
                                    "pathId": path_id})
                continue
            renderer = decoded
            mesh_slots, mesh_unresolved = _mesh_points(store, record, tree)
            renderer["meshes"] = mesh_slots
            renderer["meshDistribution"] = tree.get("m_MeshDistribution")
            counts["meshPointersUnresolved"] += mesh_unresolved
            renderer_of[go] = {"renderer": renderer, "pathId": path_id,
                               "node": node}

    emitters = []
    for entry in sorted(systems,
                        key=lambda e: (e["node"] is None, e["node"] or "",
                                       e["gameObjectId"], e["systemPathId"])):
        entry["renderer"] = renderer_of[entry["gameObjectId"]]["renderer"] \
            if entry["gameObjectId"] in renderer_of else None
        entry["rendererPathId"] = renderer_of[entry["gameObjectId"]]["pathId"] \
            if entry["gameObjectId"] in renderer_of else None
        if entry["renderer"] is None and entry["system"] is not None \
                and entry["gameObjectId"] not in renderer_failed:
            unsupported.append({"node": entry["node"],
                                "module": "ParticleSystemRenderer",
                                "reason": NO_RENDERER_ON_NODE})
        emitters.append(entry)
    for go, renderer_entry in renderer_of.items():
        if not any(e["gameObjectId"] == go for e in systems):
            unsupported.append({"node": renderer_entry["node"],
                                "module": "ParticleSystemRenderer",
                                "reason": NO_SYSTEM_ON_NODE,
                                "pathId": renderer_entry["pathId"]})
    return emitters, unsupported, counts


def _export_package(store, name, out, domain, images_factory):
    """One package to ``<out>/<name>.json``, or a missing record with no file."""
    package = store.package(name)
    document = {"version": 1, "name": name, "domain": domain, "missing": False}
    if package is None:
        document.update(emitters=[], unsupported=[], missing=True,
                        reason=MISSING_BUNDLE, file=None,
                        summary=dict(EMPTY_SUMMARY))
        return document, None
    images = images_factory(out / "textures")
    materials = _Materials(store, images)
    emitters, unsupported, counts = [], [], {}
    for record in package.files:
        file_emitters, file_unsupported, file_counts = _emitters(
            store, record, materials)
        emitters.extend(file_emitters)
        unsupported.extend(file_unsupported)
        for key, value in file_counts.items():
            counts[key] = counts.get(key, 0) + value
    summary = dict(EMPTY_SUMMARY)
    summary.update(emitters=counts["systems"], renderers=counts["renderers"],
                   materials=len(materials.cache),
                   textures=sum(1 for reference in images.written.values()
                                if reference.get("file")),
                   unsupported=len(unsupported),
                   materialSlotsUnresolved=counts["materialSlotsUnresolved"],
                   meshPointersUnresolved=counts["meshPointersUnresolved"],
                   systemReadFailures=counts["systemReadFailures"],
                   rendererReadFailures=counts["rendererReadFailures"])
    document.update(emitters=emitters, unsupported=unsupported, file=None,
                    summary=summary)
    if not emitters and not unsupported:
        return document, None
    document["file"] = f"{name}.json"
    return document, document["file"]


def extract_from_store(store, out_dir):
    """Extract every particle-emitting package of the three domains.

    *store* is a ``PackageStore`` of the bundle files to read.  Packages the
    router assigns to this module's three domains are extracted, one document
    per package that holds a particle system; every other package the store
    holds is loaded so its archives can serve pointers, and is counted as a
    lookup source rather than extracted.  The index covers every domain package,
    with a zero count and no document for one that holds none.  Returns a
    summary dict.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Load everything up front: pointers between packages resolve against the
    # archives of *every* loaded package, so an extractor that reads a package
    # before its supplier has been opened would report resolvable pointers as
    # unresolved.
    names = list(store.paths)
    target_names, lookup_names = [], []
    for name in sorted(names):
        target = route(name)
        if target is not None and target.domain in DOMAINS:
            target_names.append(name)
        else:
            lookup_names.append(name)
    for name in target_names + lookup_names:
        store.package(name)
    documents = {}
    per_domain = {"fixture-interface": 0, "cutscene-timeline": 0,
                  "fixture-timeline": 0}
    totals = {"packages": len(target_names), "lookupPackages": len(lookup_names),
              "emitters": 0, "materials": 0, "textures": 0,
              "unsupported": 0, "missing": 0,
              "materialSlotsUnresolved": 0, "meshPointersUnresolved": 0,
              "systemReadFailures": 0, "rendererReadFailures": 0}
    for name in target_names:
        routed = route(name)
        document, file_name = _export_package(store, name, out, routed.domain,
                                              lambda directory: _Images(directory))
        if file_name is not None:
            write_json(out / file_name, document)
        summary = document["summary"]
        documents[name] = {"domain": document["domain"],
                           "emitters": summary["emitters"],
                           "renderers": summary["renderers"],
                           "materials": summary["materials"],
                           "textures": summary["textures"],
                           "unsupported": summary["unsupported"],
                           "materialSlotsUnresolved":
                               summary["materialSlotsUnresolved"],
                           "meshPointersUnresolved":
                               summary["meshPointersUnresolved"],
                           "systemReadFailures": summary["systemReadFailures"],
                           "rendererReadFailures":
                               summary["rendererReadFailures"],
                           "file": document["file"],
                           "missing": document["missing"],
                           "reason": document.get("reason")}
        per_domain[document["domain"]] += summary["emitters"]
        for key in ("emitters", "materials", "textures"):
            totals[key] += summary[key]
        totals["unsupported"] += len(document["unsupported"])
        totals["missing"] += 1 if document["missing"] else 0
        for key in ("materialSlotsUnresolved", "meshPointersUnresolved",
                    "systemReadFailures", "rendererReadFailures"):
            totals[key] += summary[key]
    index = {
        "version": 1,
        "semantics": {
            "domains": ("the three domains whose packages this index covers, as "
                        "the router reports them"),
            "packages": ("a package named by the bundle its document came from; "
                         "``file`` is ``None`` when the package holds no particle "
                         "system (its count is zero, not unread), and ``missing`` "
                         "with a reason when the bundle is not on disk"),
            "emitters": ("one entry per ParticleSystem object of the package: "
                         "``node`` is the path of the game object it sits on "
                         "(root-name-first, so it is never empty), "
                         "``gameObjectId`` is the identity the geometry export "
                         "records as ``extras.gameObjectId`` and disambiguates "
                         "repeated paths, ``system`` holds the decoded emitter "
                         "parameters and ``renderer`` the draw settings of the "
                         "same node's ParticleSystemRenderer"),
            "system": ("emitter parameters as :mod:`core.particles` normalises "
                       "them: every animatable value is a mode-tagged range, and "
                       "angles are radians per second as serialized"),
            "rendererMaterial": ("the renderer's first material slot is what the "
                                 "particles are drawn with; a second slot, only "
                                 "present when the trail module is on, is the "
                                 "trail material and is reported as "
                                 "``trailMaterial``"),
            "unresolved": ("a material or mesh pointer the store cannot resolve "
                           "is reported as ``external`` / ``resolved`` false with "
                           "the archive it wanted, never as null"),
            "unsupported": ("every gap names its module and node: an enabled "
                            "particle module with no normalisation, a "
                            "sub-emitter or collision plane the package cannot "
                            "resolve, an unreadable component, or an unpaired "
                            "system or renderer.  Nothing is folded into a "
                            "generic bucket"),
            "meshes": ("a mesh-drawn renderer carries the pointers to the mesh it "
                       "samples, four slots in the renderer's own distribution "
                       "order, and a mesh-shaped emitter carries the mesh its "
                       "particles spawn on"),
            "textures": ("image files are written once per image under a "
                         "package-qualified name, because texture names repeat "
                         "across packages"),
            "textureArrays": ("a Texture2DArray reference is written one file "
                              "per layer, in layer order, and is reported under "
                              "a material's ``textureArrays`` rather than its "
                              "``textures``, because a consumer must sample it "
                              "with a layer coordinate; ``sampling`` is resolved "
                              "the same way :mod:`phenomena.texarray` resolves "
                              "it for the effect packages"),
            "summary": ("per package: ``emitters`` is the number of "
                        "ParticleSystem objects, ``renderers`` the number of "
                        "ParticleSystemRenderer objects, ``materials`` the "
                        "distinct resolved material objects, ``textures`` the "
                        "distinct images those materials reference"),
        },
        "packages": documents,
        "summary": dict(totals, perDomain=per_domain),
    }
    path = write_json(out / "index.json", index)
    return dict(totals, path=str(path))


def extract_particles(store, out_dir):
    """Alias kept for the fixture interface's naming pattern."""
    return extract_from_store(store, out_dir)
