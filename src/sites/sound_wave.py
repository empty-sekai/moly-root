"""The footstep surface of every site scene: ``MysekaiSiteView._soundWaveMeshFilter``.

Each site scene package's root carries one site view (``MysekaiSiteView``, or the
delivery site's subclass of it).  Its serialized field ``_soundWaveMeshFilter``
points at the MeshFilter whose mesh the footstep controller samples: the
controller keeps that mesh's vertex colours and world vertex positions and looks
up the colour of the vertex nearest the foot, which the site footstep master
maps to a cue.  The site package document keeps the field only as a pointer (a
MeshFilter path id), and names neither the node nor the mesh behind it; this
document resolves that pointer in the source bytes.

For each scene it records the node the MeshFilter sits on (as a path from the
package root, the form the scene glb's node names take), every component on that
node, the mesh it names (package, container, path id) and the mesh's colour
channel with every distinct colour and how many vertices carry it.  The colours
are the channel's raw bytes when the channel is byte-packed, which is the only
format observed here; a float channel is reported as floats and no bytes, and
any other format is refused with the extractor's own colour rule.  Nothing is
joined to the footstep master here: the master is its own document.
"""
import collections
from pathlib import Path

from UnityPy.helpers.MeshHelper import MeshHandler

from core.assets.packages import PackageStore, require_paths
from core.jsonio import write_json
from core.mesh import _normalized_colors

KIND = "site-sound-wave-meshes"
FIELD = "_soundWaveMeshFilter"
SCENE_PREFIX = "mysekai__site__field__"
COLOR_CHANNEL = 3          # Unity 2018+ vertex channel order: position, normal, tangent, color
UNORM8 = 2
SEMANTICS = {
    "view": ("the one MonoBehaviour of the package that serializes `_soundWaveMeshFilter` "
             "(MysekaiSiteView, or DeliverySiteView which derives from it); a package with "
             "any other number of them is refused"),
    "node": ("path of the MeshFilter's GameObject from the package root, names joined by "
             "`/`, root name first: the form the scene glb's node paths take"),
    "components": ("every component on that GameObject in authored order; a MeshCollider "
                   "entry says whether it collides with the same mesh the filter names"),
    "mesh": ("the Mesh the MeshFilter names: the package holding it (a dependency when the "
             "pointer leaves the scene package), its container basename when it has one, "
             "and its path id"),
    "colours": ("distinct values of the mesh's vertex colour channel with the number of "
                "vertices carrying each. `unorm8` is the raw RGBA bytes of a byte-packed "
                "channel; `normalized` is the value the extractor writes to glTF COLOR_0 "
                "(bytes / 255). A mesh without a colour channel has `colourChannel` null"),
}


def _node_path(record, transform_id):
    names = []
    seen = set()
    while transform_id:
        if transform_id in seen:
            raise ValueError("transform parent chain loops")
        seen.add(transform_id)
        transform = record.tree(transform_id)
        names.append(str(record.tree(transform["m_GameObject"]["m_PathID"])["m_Name"]))
        father = transform.get("m_Father") or {}
        if father.get("m_FileID", 0):
            raise ValueError("transform parent lies in another file")
        transform_id = father.get("m_PathID", 0)
    return "/".join(reversed(names))


def _container_of(store, package_name, record, path_id):
    package = store.package(package_name, record_missing=False)
    if package is None:
        return None
    for asset, owner, target in package.contents:
        if owner is record and target == path_id:
            return asset
    return None


def _package_of(store, record):
    for name, package in store._packages.items():
        if package is not None and record in package.files:
            return name
    return None


def _colours(record, path_id):
    mesh = record.objects[path_id].read()
    handler = MeshHandler(mesh)
    handler.process()
    channels = mesh.m_VertexData.m_Channels
    channel = channels[COLOR_CHANNEL] if len(channels) > COLOR_CHANNEL else None
    result = {"name": str(mesh.m_Name), "vertices": handler.m_VertexCount,
              "colourChannel": None, "colours": []}
    if channel is None or not channel.dimension:
        return result
    result["colourChannel"] = {"stream": channel.stream, "offset": channel.offset,
                               "format": channel.format, "dimension": channel.dimension}
    normalized = _normalized_colors(handler)       # refuses unhandled formats
    raw = handler.m_Colors
    counts = collections.Counter()
    for index, value in enumerate(normalized):
        key = (tuple(int(v) for v in raw[index][:4]) if channel.format == UNORM8 else None,
               tuple(float(v) for v in value[:4]))
        counts[key] += 1
    for (unorm8, value), count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][1])):
        result["colours"].append({"unorm8": None if unorm8 is None else list(unorm8),
                                  "normalized": list(value), "vertices": count})
    return result


def _scene(store, name):
    package = store.package(name)
    if package is None:
        raise FileNotFoundError(f"scene package not loaded: {name}")
    missing = [dep for dep in package.dependencies
               if store.package(dep.replace("/", "__")) is None]
    if missing:
        raise FileNotFoundError(f"scene dependencies not supplied: {', '.join(missing)}")
    views = []
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind != "MonoBehaviour":
                continue
            tree = record.tree(path_id)
            if FIELD in tree:
                views.append((record, path_id, tree))
    if len(views) != 1:
        raise ValueError(f"expected one site view serializing {FIELD}, found {len(views)}")
    record, view_id, view = views[0]
    view_go = record.tree(view["m_GameObject"]["m_PathID"])
    pointer = view[FIELD]
    row = {"package": name, "source": store.source_receipt(name),
           "declaredDependencies": sorted(package.dependencies),
           "view": {"class": record.script_of(view_id), "pathId": str(view_id),
                    "gameObject": str(view_go["m_Name"])},
           "pointer": {"fileId": pointer.get("m_FileID", 0),
                       "pathId": str(pointer.get("m_PathID", 0))},
           "meshFilter": None, "mesh": None}
    target = store.follow(record, pointer)
    if target is None:
        if pointer.get("m_PathID", 0):
            raise ValueError(f"{FIELD} does not resolve: {row['pointer']}")
        return row                                   # null in the bytes
    mf_record, mf_id = target
    if mf_record.kinds[mf_id] != "MeshFilter":
        raise ValueError(f"{FIELD} names a {mf_record.kinds[mf_id]}")
    mf = mf_record.tree(mf_id)
    go = mf_record.tree(mf["m_GameObject"]["m_PathID"])
    mesh_target = store.follow(mf_record, mf.get("m_Mesh") or {})
    if mesh_target is None:
        raise ValueError("the sound-wave MeshFilter names no loadable mesh")
    mesh_record, mesh_id = mesh_target
    components, transform_id = [], None
    for entry in go.get("m_Component") or []:
        component = store.follow(mf_record, entry["component"])
        if component is None:
            components.append({"type": None, "unresolved": entry["component"]})
            continue
        c_record, c_id = component
        kind = c_record.kinds[c_id]
        item = {"type": kind, "pathId": str(c_id)}
        if kind in ("Transform", "RectTransform"):
            transform_id = c_id
        if kind == "MeshCollider":
            tree = c_record.tree(c_id)
            item["enabled"] = bool(tree.get("m_Enabled"))
            item["sameMesh"] = store.follow(c_record, tree.get("m_Mesh") or {}) == mesh_target
        if kind == "MeshRenderer":
            item["enabled"] = bool(c_record.tree(c_id).get("m_Enabled"))
        components.append(item)
    if transform_id is None:
        raise ValueError("the sound-wave MeshFilter's GameObject has no transform")
    row["meshFilter"] = {"pathId": str(mf_id), "node": _node_path(mf_record, transform_id),
                         "gameObjectActive": bool(go.get("m_IsActive")),
                         "components": components}
    holder = _package_of(store, mesh_record)
    container = _container_of(store, holder, mesh_record, mesh_id) if holder else None
    row["mesh"] = {"package": holder, "container": container, "pathId": str(mesh_id),
                   "inScenePackage": holder == name, **_colours(mesh_record, mesh_id)}
    return row


def extract_site_sound_wave(bundles, out_path, bundle_root=None):
    """Resolve the footstep surface of every supplied site scene package."""
    from core.extract import _configure_unity_version
    _configure_unity_version(None)
    require_paths(bundles)
    names = sorted(Path(path).name for path in bundles)
    refused = [name for name in names if not name.startswith(SCENE_PREFIX)]
    if refused:
        raise ValueError(f"not site scene packages: {', '.join(refused)}")
    store = PackageStore(bundles, bundle_root)
    store.load_dependencies(names)
    scenes, failed = {}, {}
    for name in names:
        try:
            scenes[name[len(SCENE_PREFIX):]] = _scene(store, name)
        except Exception as exc:
            failed[name] = f"{type(exc).__name__}: {exc}"
    document = {"version": 1, "kind": KIND, "semantics": SEMANTICS, "scenes": scenes,
                "failed": failed,
                "summary": {"scenes": len(scenes), "failed": len(failed),
                            "withMesh": sum(1 for row in scenes.values() if row["mesh"]),
                            "withColours": sum(1 for row in scenes.values()
                                               if row["mesh"] and row["mesh"]["colourChannel"])}}
    write_json(out_path, document)
    return {"path": str(out_path), **document["summary"], "failed": failed}
