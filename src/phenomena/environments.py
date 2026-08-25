"""Phenomena (weather) asset packages.

The game calls its weather *phenomena*.  One phenomenon is a named set of
packages: a **global** variant holding the configuration asset, the sky gradient,
the post-processing profile and the sky and camera effects; a **common** variant
holding shared meshes, materials and images that the effects draw with; and one
**unique** variant per site.  A site variant carries either that site's own effect
prefab, or — for the one indoor site — a configuration and profile that *override*
the global ones.  Both are found through the same two-level lookup: the site's own
package first, the phenomenon's global package second.

Icons are not part of any of those.  All phenomenon icons live together in one
shared thumbnail package, and which icon belongs to which phenomenon is stated by
the caller's master tables, not by the asset names — so without master tables the
icons are still exported, they are simply not attached to a phenomenon.

Materials and images are commonly reached across package boundaries, so this
module follows each package's own declared dependency list, reading those
packages from the paths it was given and from the store directory when one is
supplied.  A dependency that is not in the store leaves the pointer visibly
unresolved instead of silently null.
"""
import os
from pathlib import Path

import UnityPy

from core.assets.packages import PackageStore, pairs as _pairs
from core.assets.router import (AMBIENCE_PACKAGE, BGM_PACKAGE_PREFIX,
                                PHENOMENA_BUNDLE, PHENOMENA_THUMBNAIL)
from core.jsonio import write_json
from . import meshes as meshes_module
from . import texarray
from .audio import Library, archive_bytes
from .config import flatten_config, volume_profile
from .effects import decode_effect
from .timeline import TIMELINE_SCRIPT, decode_timeline

# Script classes that identify the authored assets inside a package.
CONFIG_SCRIPT = "SiteEnvironmentConfig"
PROFILE_SCRIPT = "VolumeProfile"
RAMP_SCRIPT = "RampTexture"


def _gradient_end(gradient):
    """A gradient's colour at its end, as the runtime evaluates it there.

    Colour keys and alpha keys run on their own lists, so the end colour is the
    last colour key and the end alpha is the last alpha key.  A gradient with no
    keys has no end to report.
    """
    colours = int(gradient.get("m_NumColorKeys") or 0)
    alphas = int(gradient.get("m_NumAlphaKeys") or 0)
    if colours <= 0:
        return None
    colour = gradient.get(f"key{colours - 1}") or {}
    alpha = gradient.get(f"key{max(0, alphas - 1)}") or {}
    return [round(float(colour.get(channel, 0.0)), 6) for channel in "rgb"] + [
        round(float(alpha.get("a", 1.0)), 6)]

# Effect placement, as told by the prefab name.
EFFECT_KINDS = (("fx_env_sky_", "sky"), ("fx_env_camera_", "camera"),
                ("fx_env_site_", "site"))

# Client-configuration rows that name the delivery site's phenomenon, which has
# no row of its own in the phenomena table.
DELIVERY_ID_CONFIG = 170
DELIVERY_ASSET_CONFIG = 171

# Switching phenomena cross-fades both configurations over this many seconds.
CROSS_FADE_SECONDS = 0.25

# Music and ambience are addressed differently.  A music row names its own package,
# whereas an ambience row names only a cue: every ambience cue lives together in one
# shared sound package, so the cue alone does not say where to look and the package
# is stated by the router, which is where bundle names are decided.
# (`BGM_PACKAGE_PREFIX` and `AMBIENCE_PACKAGE` are imported above.)

MASTER_TABLES = ("mysekaiPhenomenas", "mysekaiPhenomenaBgms", "mysekaiSiteBgms",
                 "mysekaiSiteMysekaiPhenomenaSounds", "mysekaiRefreshTimePeriods")

NO_MASTER = ("no master directory supplied; a phenomenon's identity, its time "
             "period and brightness, its music and its ambience rows are only in "
             "caller-supplied master tables")
NO_THUMBNAILS = "the shared phenomena thumbnail package was not supplied"
NO_AUDIO_PACKAGES = ("no sound package was supplied or reachable, so no audio "
                     "archive was extracted")
NO_AUDIO_ROWS = ("music and ambience rows are only in caller-supplied master "
                 "tables, so no cue was asked for")
NO_LUT = ("no colour-grading lookup texture exists in these packages: no profile "
          "carries a lookup component, and the whole corpus these packages come "
          "from holds no 3D texture at all; grading here is parametric")
UNMODELLED_CLIP_ASSET = "animation clip inside a model asset not modelled"
MESH_NOT_EXPORTED = "mesh asset not reached through any model asset"

SEMANTICS = {
    "files": "every file path in this index is relative to the index itself",
    "variants": ("one phenomenon spans a global package, a shared common package, "
                 "and one package per site"),
    "overrideLookup": ("a site's configuration and profile are looked up in that "
                       "site's own package first and in the phenomenon's global "
                       "package second; `overrides` lists the sites that really "
                       "carry one, so an absent entry means the site uses the global "
                       "values, not that the lookup failed"),
    "homeSiteLightAngle": ("the home site gets no override package: it uses the "
                           "global configuration and substitutes the sun angle baked "
                           "into `light.homeSiteLightAngle` when that entry is active"),
    "crossFadeSeconds": ("switching phenomena cross-fades the two configurations over "
                         "this long; a screenshot capture switches instantly instead"),
    "storedValues": ("configuration values are copied exactly as stored and are not "
                     "rescaled: the cloud shadow is sampled with the reciprocal of "
                     "`cloud.cloudShadowTextureSize`, and its scroll is "
                     "`cloud.cloudScrollVelocity` times `cloud.cloudScrollSpeed`"),
    "outlineGroups": ("`character` and `fixture` are two independent outline setups "
                      "with the same field names; the character group also drives "
                      "face and body shading, so it is not only an outline"),
    "siteShading": ("some scene shading values are decided by the site rather than by "
                    "the phenomenon and are therefore not in this package; a consumer "
                    "that renders without a site should feed those neutral constants"),
    "overrideState": ("a post-processing parameter with `overrideState: false` is not "
                      "set by the profile at all, and the value inherited from the "
                      "surrounding volume stack stays live"),
    "particles": ("effects are exported as emitter parameters to simulate, not as "
                  "baked frames; a module that is on but not modelled is listed under "
                  "`unsupported`"),
    "effectKinds": ("`sky` is anchored to the sky, `camera` to the camera, `site` to "
                    "one site; `other` means the prefab does not follow those naming "
                    "patterns and its placement is not stated by its name. `site` "
                    "comes from the package variant, never guessed from the name"),
    "musicMatch": ("music is matched on the phenomenon exactly, with no fallback: a "
                   "phenomenon with no row keeps the site's own music"),
    "musicLayers": ("music is two layers: `siteBgms` is the base, keyed by site and "
                    "brightness, and a phenomenon's own `bgms` row replaces it "
                    "whole.  Most phenomena have no row of their own, so the base "
                    "layer is what plays for them; both layers' packages are "
                    "extracted, and a base-layer stream belongs to a site rather "
                    "than to a phenomenon, so it is not listed in a phenomenon's "
                    "`audio`"),
    "ambienceMatch": ("ambience takes the row for this phenomenon and this site first "
                      "and the site's `other` row as the fallback, so "
                      "`siteSoundFallbacks` is what plays when `siteSounds` has no row "
                      "for the site"),
    "audioPackages": ("each music row names its own sound package; every ambience cue "
                      "instead lives in the one shared package named by "
                      "`ambiencePackage`, so an ambience cue is not a package name"),
    "audio": ("a sound package holds one encoded archive, not audio files; the "
              "archive is always extracted, and decoding it needs an external "
              "decoder this repository neither ships nor vendors, so with the "
              "decoder absent `audio.status` is `skipped` and the archives are "
              "still there to decode later"),
    "audioLoops": ("loop points come from the archive's own metadata and are "
                   "written as seconds; the waveforms are exported with the loop "
                   "not unrolled, so a consumer loops over that range itself"),
    "audioCues": ("one cue can have several waveforms and one waveform can answer "
                  "to several cue names, so a cue maps to a list of streams, and "
                  "each stream says which subsong of the archive it came from"),
    "textureArrays": ("a texture array holds several pictures of one size and "
                      "format; it is exported one file per layer, in layer order, "
                      "and is kept apart from the single-image bindings because a "
                      "consumer must sample it with a layer coordinate. "
                      "`sampling.arrayMode` false means the material binds the "
                      "array but the slot samples its single-image companion, so "
                      "the array is not drawn at all"),
    "arraySampling": ("`progressCoord` is a packed `component * 10 + vector` "
                      "selector naming the per-particle value the layer is read "
                      "from (vector 0 selects a constant zero); `sliceCount` is "
                      "authored separately from the real layer count and the two "
                      "do disagree, so the arithmetic uses `sliceCount` and the "
                      "result is then clamped to the layers that exist, exactly as "
                      "`layerFormula` states"),
    "models": ("a model asset is a node tree with meshes on it, exported as one "
               "glTF binary; files are named after their content, so geometry "
               "shared between phenomena is written once and every phenomenon "
               "points at that one file"),
    "meshEmitters": ("an emitter whose render mode is `Mesh` draws copies of a "
                     "mesh instead of camera-facing quads, so its `mesh` field is "
                     "what it draws; a null one means the pointer names geometry "
                     "no supplied package holds"),
    "omitted": ("components read on an effect node and deliberately not exported, "
                "with the reason; these are decisions, not gaps, and are counted "
                "separately from `unsupported`"),
    "timeline": ("one phenomenon is driven by a timeline; each track says which "
                 "value it drives, and each clip carries its own curve or gradient "
                 "on a normalized axis over the clip's duration"),
    "lut": NO_LUT,
}

COUNTERS = ("configs", "profiles", "ramps", "effects", "emitters", "models",
            "omitted", "unsupported")



def variant_key(bundle_name):
    """Short, unique name of a package, used to qualify the images it holds.

    Image names repeat across phenomena, so a flat name would let one package
    overwrite another's picture.
    """
    for prefix in ("mysekai__effect__site__environment__", "mysekai__"):
        if bundle_name.startswith(prefix):
            return bundle_name[len(prefix):].replace("__", "_")
    return bundle_name.replace("__", "_")


class _Images:
    """Images of one phenomenon, written once and referenced by relative path.

    A texture array is written one file per layer and reported with its shape, so
    the caller can keep it apart from the single-image bindings.
    """

    def __init__(self, directory, prefix):
        self.directory = Path(directory)
        self.prefix = prefix                  # path of the directory in the index
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
                reference["files"].append(f"{self.prefix}/{file_name}")
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
        """Write the texture a pointer resolves to and return how to find it."""
        if target is None:
            return None
        record, path_id = target
        key = (record.archive, path_id)
        if key in self.written:
            return dict(self.written[key])
        tree = record.tree(path_id)
        name = str(tree.get("m_Name", "") or "image")
        kind = record.kinds.get(path_id)
        stem = f"{variant_key(record.bundle)}__{name}"
        if kind == "Texture2DArray":
            self.written[key] = self._array(record, path_id, tree, name, stem)
            return dict(self.written[key])
        if kind != "Texture2D":
            self.failed.append({"image": name,
                                "reason": f"{kind} is not a single image"})
            self.written[key] = {"name": name, "file": None}
            return dict(self.written[key])
        reference = {"name": name, "file": f"{self.prefix}/{stem}.png"}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            record.objects[path_id].read().image.save(self.directory / f"{stem}.png")
        except Exception as exc:              # unreadable image format
            self.failed.append({"image": name, "reason": f"{type(exc).__name__}: {exc}"})
            reference = {"name": name, "file": None}
        self.written[key] = reference
        return dict(reference)


def _material(store, record, path_id, images):
    """Material name, shader, queue, texture bindings, and scalar properties.

    Texture arrays are kept in their own map: they are sampled with a layer
    coordinate, so putting them next to single-image bindings would let a consumer
    treat one as the other.  Their sampling parameters are resolved here so the
    consumer does not have to know the encoding.

    Every texture slot also carries a scale/offset pair.  A shader that declares
    ``<name>_ST`` samples the slot through it, so it is recorded for every slot —
    array bindings and slots with no image included — rather than only where an
    image resolved.
    """
    tree = record.tree(path_id)
    properties = tree.get("m_SavedProperties") or {}
    keywords = [str(word) for word in tree.get("m_ValidKeywords") or []]
    scalars = {str(name): value
               for name, value in _pairs(properties.get("m_Floats"))
               if isinstance(value, (int, float))}
    textures, arrays, scale_offset = {}, {}, {}
    for name, value in _pairs(properties.get("m_TexEnvs")):
        entry = value or {}
        scale = entry.get("m_Scale") or {}
        offset = entry.get("m_Offset") or {}
        scale_offset[name] = [round(float(scale.get("x", 1.0)), 6),
                              round(float(scale.get("y", 1.0)), 6),
                              round(float(offset.get("x", 0.0)), 6),
                              round(float(offset.get("y", 0.0)), 6)]
        pointer = entry.get("m_Texture") or {}
        if not pointer.get("m_PathID", 0):
            continue
        reference = images.reference(store.follow(record, pointer))
        if reference is not None and "files" in reference:
            prefix = texarray.slot_prefix(str(name)) or str(name)
            arrays[name] = dict(reference,
                                sampling=texarray.sampling(prefix, scalars, keywords))
            continue
        textures[name] = reference["file"] if reference else None
    shader = None
    shader_target = store.follow(record, tree.get("m_Shader") or {})
    if shader_target is not None:
        shader_record, shader_id = shader_target
        parsed = shader_record.tree(shader_id).get("m_ParsedForm") or {}
        shader = str(parsed.get("m_Name")) if parsed.get("m_Name") else None
    return {"name": tree.get("m_Name"), "shader": shader,
            "renderQueue": tree.get("m_CustomRenderQueue", -1),
            "keywords": keywords,
            "textures": textures,
            "textureArrays": arrays,
            "textureScaleOffset": scale_offset,
            "floats": {name: round(float(value), 6)
                       for name, value in scalars.items()},
            "colors": {n: [v.get(c, 0.0) for c in "rgba"]
                       for n, v in _pairs(properties.get("m_Colors"))
                       if isinstance(v, dict)}}


def _material_resolver(store, record, images):
    """Resolve one material slot of a renderer, keeping unresolved states visible."""
    def resolve(renderer_tree, slot=0):
        materials = renderer_tree.get("m_Materials") or []
        if slot >= len(materials):
            return None
        pointer = materials[slot] or {}
        if not pointer.get("m_PathID", 0):
            return None
        target = store.follow(record, pointer)
        if target is None:
            index = pointer.get("m_FileID", 0) - 1
            archive = (record.externals[index]
                       if 0 <= index < len(record.externals) else None)
            return {"external": True, "fileId": pointer.get("m_FileID", 0),
                    "archive": archive}
        return _material(store, *target, images)
    return resolve


def _effect_kind(asset_name):
    for prefix, kind in EFFECT_KINDS:
        if asset_name.startswith(prefix):
            return kind
    return "other"


def _site_of(variant):
    return variant.split("unique__", 1)[1] if variant.startswith("unique__") else None


def _write_json(path, document):
    return write_json(path, document)


def _master_rows(master, master_cache):
    """Read the phenomena master tables; report which of them are absent."""
    from core.master import Master, MissingTable
    source = Master(master, cache_dir=master_cache)
    rows, absent = {}, []
    for table in MASTER_TABLES:
        try:
            rows[table] = source.table(table)
        except MissingTable:
            absent.append(table)
    try:
        rows["clientConfigs"] = source.client_configs()
    except (MissingTable, ValueError):
        rows["clientConfigs"] = None
    return rows, absent


def _master_entry(row):
    """One phenomena row, under names that do not repeat the table's own prefix."""
    return {"id": row.get("id"), "name": row.get("name"),
            "englishName": row.get("englishName"),
            "description": row.get("description"),
            "timePeriodType": row.get("mysekaiPhenomenaTimePeriodType"),
            "brightnessType": row.get("mysekaiPhenomenaBrightnessType"),
            "backgroundColorId": row.get("mysekaiPhenomenaBackgroundColorId"),
            "iconAssetbundleName": row.get("iconAssetbundleName"),
            "rampTextureAssetbundleName": row.get("rampTextureAssetbundleName")}


class _Phenomenon:
    """Everything one phenomenon's packages produce, written as it is decoded."""

    def __init__(self, name, out, store, geometry):
        self.name = name
        self.out = Path(out)
        self.store = store
        self.geometry = geometry
        self.directory = self.out / name
        self.images = _Images(self.directory / "textures", f"{name}/textures")
        self.effects = {}
        self.unsupported = []
        self.omitted = []
        self.models = []
        self.timeline = None
        self.mesh_ids = set()
        self.entry = {"assetName": name, "id": None, "variants": [], "config": None,
                      "ramp": None, "postprocess": None, "overrides": {},
                      "fx": {"sky": 0, "camera": 0, "site": 0, "other": 0,
                             "emitters": 0, "file": None},
                      "models": [], "timeline": None, "audio": None,
                      "icon": None, "master": None, "bgms": None, "siteSounds": None}
        self.counts = {}

    def _count(self, bundle_name, field, amount=1):
        counter = self.counts.setdefault(bundle_name, {name: 0 for name in COUNTERS})
        counter[field] += amount
        return counter

    def _gap(self, bundle_name, **entry):
        self.unsupported.append(dict(entry, phenomenon=self.name))
        self._count(bundle_name, "unsupported")

    def _texture_ref(self, record):
        return lambda pointer: self.images.reference(self.store.follow(record, pointer))

    def _mesh_resolver(self, record):
        """Resolve an emitter's mesh pointer to a geometry reference, or a gap."""
        def resolve(pointer):
            pointer = pointer or {}
            target = self.store.follow(record, pointer)
            if target is None:
                index = pointer.get("m_FileID", 0) - 1
                archive = (record.externals[index]
                           if 0 <= index < len(record.externals) else None)
                return None, {"reason": meshes_module.pointer_gap(pointer),
                              "mesh": {"fileId": pointer.get("m_FileID"),
                                       "pathId": pointer.get("m_PathID"),
                                       "archive": archive}}
            mesh_record, mesh_id = target
            if mesh_record.kinds.get(mesh_id) != "Mesh":
                return None, {"reason": "emitter mesh pointer is not a mesh"}
            self.mesh_ids.add((mesh_record.archive, mesh_id))
            return self.geometry.mesh(mesh_record, mesh_id), None
        return resolve

    def variant(self, variant, bundle_name):
        """Decode one package of this phenomenon."""
        package = self.store.package(bundle_name)
        if package is None:
            self._gap(bundle_name, variant=variant,
                      reason="package could not be read")
            return
        site = _site_of(variant)
        config = profile = None
        resolvers = {}
        models, standalone = [], []
        for asset_name, record, path_id in package.contents:
            kind = record.kinds.get(path_id)
            if kind == "MonoBehaviour":
                script = record.script_of(path_id)
                if script == CONFIG_SCRIPT:
                    config = self._config(variant, bundle_name, asset_name, record,
                                          path_id)
                elif script == PROFILE_SCRIPT:
                    profile = self._profile(variant, bundle_name, asset_name, record,
                                            path_id)
                elif script == RAMP_SCRIPT:
                    self._ramp(variant, bundle_name, asset_name, record, path_id)
                elif script == TIMELINE_SCRIPT:
                    self._timeline(variant, bundle_name, asset_name, record, path_id)
                else:
                    self._gap(bundle_name, variant=variant, asset=asset_name,
                              script=script or None,
                              reason="asset script not modelled")
            elif kind == "GameObject" and asset_name.endswith(".prefab"):
                if record.archive not in resolvers:
                    resolvers[record.archive] = _material_resolver(
                        self.store, record, self.images)
                self._effect(variant, bundle_name, asset_name, record, path_id, site,
                             resolvers[record.archive])
            elif kind == "GameObject":
                models.append((asset_name, record, path_id))
            elif kind == "Mesh":
                standalone.append((asset_name, record, path_id))
            elif kind == "AnimationClip":
                self._gap(bundle_name, variant=variant, asset=asset_name,
                          clip=str(record.tree(path_id).get("m_Name", "")),
                          reason=UNMODELLED_CLIP_ASSET)
        for asset_name, record, path_id in models:
            self._model(variant, bundle_name, asset_name, record, path_id)
        for asset_name, record, path_id in standalone:
            if (record.archive, path_id) not in self.mesh_ids:
                self._gap(bundle_name, variant=variant, asset=asset_name,
                          mesh=str(record.tree(path_id).get("m_Name", "")),
                          reason=MESH_NOT_EXPORTED)
        self._place(variant, bundle_name, site, config, profile)

    def _model(self, variant, bundle_name, asset_name, record, path_id):
        """Export one model asset: a node tree with meshes on it."""
        def material_name(renderer_tree):
            pointer = (renderer_tree.get("m_Materials") or [{}])[0] or {}
            target = self.store.follow(record, pointer)
            if target is None:
                return None
            material_record, material_id = target
            return str(material_record.tree(material_id).get("m_Name", ""))

        def follow(pointer):
            target = self.store.follow(record, pointer or {})
            if target is not None:
                self.mesh_ids.add((target[0].archive, target[1]))
            return target

        try:
            reference, gaps = self.geometry.model(record, path_id, follow,
                                                 material_name, asset=asset_name)
        except Exception as exc:                    # unreadable geometry
            self._gap(bundle_name, variant=variant, asset=asset_name,
                      reason=f"{type(exc).__name__}: {exc}")
            return
        for gap in gaps:
            self._gap(bundle_name, variant=variant, asset=asset_name, **gap)
        self.models.append(dict(reference, asset=asset_name, variant=variant))
        self._count(bundle_name, "models")

    def _timeline(self, variant, bundle_name, asset_name, record, path_id):
        """Export the timeline that drives this phenomenon, if it has one."""
        document, gaps = decode_timeline(
            path_id, record, lambda pointer: self.store.follow(record, pointer))
        for gap in gaps:
            self._gap(bundle_name, variant=variant, asset=asset_name, **gap)
        document["asset"] = asset_name
        _write_json(self.directory / "timeline.json", document)
        self.timeline = document
        self.entry["timeline"] = {"file": f"{self.name}/timeline.json",
                                 "duration": document["duration"],
                                 **document["summary"]}

    def _config(self, variant, bundle_name, asset_name, record, path_id):
        document, gaps = flatten_config(record.tree(path_id),
                                        texture_ref=self._texture_ref(record))
        for gap in gaps:
            self._gap(bundle_name, variant=variant, asset=asset_name, **gap)
        return {"asset": asset_name, **document}

    def _profile(self, variant, bundle_name, asset_name, record, path_id):
        components = []
        for pointer in record.tree(path_id).get("components") or []:
            target = self.store.follow(record, pointer)
            if target is None:
                self._gap(bundle_name, variant=variant, profile=asset_name,
                          reason="profile component not in this package")
                continue
            component_record, component_id = target
            tree = component_record.tree(component_id)
            components.append((str(tree.get("m_Name", "")),
                               component_record.script_of(component_id), tree))
        document, gaps = volume_profile(components,
                                        texture_ref=self._texture_ref(record))
        for gap in gaps:
            self._gap(bundle_name, variant=variant, asset=asset_name, **gap)
        return {"asset": asset_name, **document}

    def _ramp(self, variant, bundle_name, asset_name, record, path_id):
        if variant != "global":
            self._gap(bundle_name, variant=variant, ramp=asset_name,
                      reason="a site-specific sky gradient is not modelled")
            return
        target = self.store.follow(record, record.tree(path_id).get("_texture") or {})
        if target is None:
            self._gap(bundle_name, variant=variant, ramp=asset_name,
                      reason="gradient image not in this package")
            return
        ramp_record, ramp_id = target
        tree = ramp_record.tree(ramp_id)
        path = self.directory / "ramp.png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            ramp_record.objects[ramp_id].read().image.save(path)
        except Exception as exc:                     # unreadable image format
            self._gap(bundle_name, variant=variant, ramp=asset_name,
                      reason=f"{type(exc).__name__}: {exc}")
            return
        # The colour the runtime hands the shaders as the sky's bottom is the
        # gradient's own end, not the picture's last pixel.  The picture is baked
        # through parameters the gradient reading does not go through, so on one
        # phenomenon here the two differ, and reading the picture instead would
        # be wrong by that much without saying so.
        self.entry["ramp"] = {"file": f"{self.name}/ramp.png",
                             "width": tree.get("m_Width"),
                             "height": tree.get("m_Height"),
                             "skyBottomColor": _gradient_end(
                                 record.tree(path_id).get("_gradient") or {})}
        self._count(bundle_name, "ramps")

    def _effect(self, variant, bundle_name, asset_name, record, path_id, site,
                resolver):
        name = asset_name.rsplit(".", 1)[0]
        kind = _effect_kind(asset_name)
        effect, gaps = decode_effect(path_id, record.kinds, record.trees, resolver,
                                     kind=kind, site=site if kind == "site" else None,
                                     script_of=record.script_of,
                                     resolve_mesh=self._mesh_resolver(record))
        if name in self.effects:
            self._gap(bundle_name, variant=variant, effect=name,
                      reason="two packages hold an effect of the same name")
            return
        effect["variant"] = variant
        self.effects[name] = effect
        for gap in gaps:
            self._gap(bundle_name, variant=variant, effect=name, **gap)
        for entry in effect["omitted"]:
            self.omitted.append(dict(entry, phenomenon=self.name, effect=name))
        self._count(bundle_name, "omitted", len(effect["omitted"]))
        self.entry["fx"][kind] += 1
        self.entry["fx"]["emitters"] += len(effect["particles"])
        self._count(bundle_name, "effects")
        self._count(bundle_name, "emitters", len(effect["particles"]))

    def _place(self, variant, bundle_name, site, config, profile):
        """File a variant's configuration and profile as base values or overrides."""
        for document, stem in ((config, "config"), (profile, "postprocess")):
            if document is None:
                continue
            field = "configs" if stem == "config" else "profiles"
            if variant == "global":
                _write_json(self.directory / f"{stem}.json", document)
                self.entry["config" if stem == "config" else "postprocess"] = (
                    f"{self.name}/{stem}.json")
            elif site is None:
                self._gap(bundle_name, variant=variant, asset=document["asset"],
                          reason="a shared package carries no site to override")
                continue
            else:
                _write_json(self.directory / "overrides" / site / f"{stem}.json",
                            document)
                self.entry["overrides"].setdefault(site, {})[stem] = (
                    f"{self.name}/overrides/{site}/{stem}.json")
            self._count(bundle_name, field)

    def _model_meshes(self):
        """The model assets' mesh nodes, as one flat list for the effect document.

        A phenomenon's geometry arrives two ways.  Emitters carry their own mesh
        reference and are described inside ``effects``.  The rest sits on model
        assets, whose renderers are not components of any effect prefab, so a
        reader that walks ``effects`` alone never learns those meshes exist.
        Nothing in the packages ties a model asset to a particular effect, so the
        association is stated at the phenomenon's own level and no finer: it is
        the level the data actually supports.

        ``materialName`` is the material's name and nothing else.  It is
        deliberately not called ``material``: an emitter's material is a full
        record (shader, render queue, keywords, texture slots, scalars, colours),
        and this one is a bare name, which is not enough to decide whether the
        surface is transparent or what pictures it samples.  ``materialEncoding``
        says so in the document rather than leaving a reader to discover it.
        """
        rows = []
        for model in self.models:
            meshes = model.get("meshes") or []
            materials = model.get("materials") or []
            aligned = len(materials) == len(meshes)
            for index, mesh in enumerate(meshes):
                if aligned:
                    name = materials[index].get("material")
                else:
                    # Node names repeat within a model, so a name lookup can only
                    # be used when the two lists cannot be paired by position.
                    name = next((entry.get("material") for entry in materials
                                 if entry.get("node") == mesh.get("node")), None)
                rows.append({"file": model["file"], "node": mesh.get("node"),
                             "mesh": mesh.get("mesh"), "materialName": name,
                             "materialEncoding": "nameOnly"})
        return rows

    def finish(self):
        """Write the effect document and return this phenomenon's index entry."""
        if self.effects:
            document = {
                "version": 1,
                "phenomenon": self.name,
                "effects": {name: self.effects[name] for name in sorted(self.effects)},
                "modelMeshes": self._model_meshes(),
                "summary": {
                    "effects": len(self.effects),
                    "modelMeshes": len(self._model_meshes()),
                    "emitters": sum(len(effect["particles"])
                                    for effect in self.effects.values()),
                    "meshEmitters": sum(1 for effect in self.effects.values()
                                        for emitter in effect["particles"]
                                        if emitter.get("renderer", {}).get("meshes")),
                    "omitted": self.omitted,
                    "unsupported": [gap for gap in self.unsupported if "effect" in gap],
                },
            }
            _write_json(self.directory / "fx" / "effects.json", document)
            self.entry["fx"]["file"] = f"{self.name}/fx/effects.json"
        self.entry["models"] = self.models
        for gap in self.images.failed:
            self.unsupported.append(dict(gap, phenomenon=self.name))
        if self.entry["config"] is None:
            self.unsupported.append({"phenomenon": self.name,
                                     "reason": "no global package, so this phenomenon "
                                               "has no configuration, gradient, or "
                                               "profile"})
        return self.entry


def extract_phenomena(bundles, out_dir, bundle_root=None, master=None,
                      master_cache=None, vgmstream=None, ffmpeg=None,
                      extra_archives=None):
    """Extract phenomena packages into *out_dir*.

    *bundles* names the packages to extract: the environment packages of any number
    of phenomena, and optionally the shared thumbnail package.  Packages those
    declare as dependencies are read as lookup sources, from the same paths and —
    when given — from *bundle_root*.

    ``index.json`` holds exactly what this run extracted.  With *master* it also
    carries the phenomenon rows, the music and ambience rows, and the refresh
    windows; without it those fields are absent and the reason is recorded rather
    than filled with defaults.

    Music and ambience are decoded with an external decoder: *vgmstream* names it
    (a file or a directory), *ffmpeg* names an optional transcoder for a compressed
    copy, and either may be left out, in which case it is looked up on ``PATH`` and
    the audio entry says what is missing rather than failing the run.

    *extra_archives* names further files to hold as pointer targets, alongside
    *bundles* and *bundle_root*.  A pointer that names an archive not among the
    packages already loaded is otherwise left visibly unresolved; a caller that
    can supply that archive (for example an engine-owned built-in container) adds
    it here so those pointers resolve.  Geometry that comes out of an
    engine-owned container is written like any other mesh and its entry says
    where it came from (``source``), so a reader can tell the engine's own shapes
    from this game's.  The default per-collection behaviour is unchanged — pass
    nothing and nothing extra is loaded.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = PackageStore(list(bundles) + list(extra_archives or []), bundle_root)
    geometry = meshes_module.Store(out / "models", "models")
    groups, thumbnail = {}, None
    for path in bundles:
        name = os.path.basename(str(path))
        matched = PHENOMENA_BUNDLE.match(name)
        if matched:
            groups.setdefault(matched.group("phenomenon"), {})[
                matched.group("variant")] = name
        elif name == PHENOMENA_THUMBNAIL:
            thumbnail = name

    index = {"version": 1, "semantics": SEMANTICS, "phenomena": {}, "icons": [],
             "models": [], "audio": None,
             "refreshTimePeriods": None, "siteBgms": None,
             "siteSoundFallbacks": None,
             "ambiencePackage": AMBIENCE_PACKAGE}
    totals = {"phenomena": 0, "configs": 0, "profiles": 0, "ramps": 0, "overrides": 0,
              "effects": 0, "emitters": 0, "images": 0, "textureArrays": 0,
              "arrayLayers": 0, "models": 0, "meshes": 0, "timelines": 0,
              "audioStreams": 0, "icons": 0, "omitted": 0, "unsupported": 0}
    unsupported, omitted, missing, per_bundle = [], [], {}, {}

    master_rows, absent_tables = {}, []
    if master:
        master_rows, absent_tables = _master_rows(master, master_cache)
        if absent_tables:
            missing["masterTables"] = absent_tables
    else:
        missing["master"] = NO_MASTER
    missing["lut"] = NO_LUT
    by_asset = {row.get("assetbundleName"): row
                for row in master_rows.get("mysekaiPhenomenas") or []}
    client_configs = master_rows.get("clientConfigs") or {}
    bgm_rows = master_rows.get("mysekaiPhenomenaBgms") or []
    site_bgm_rows = master_rows.get("mysekaiSiteBgms") or []
    sound_rows = master_rows.get("mysekaiSiteMysekaiPhenomenaSounds") or []
    if "mysekaiRefreshTimePeriods" in master_rows:
        index["refreshTimePeriods"] = [
            {"id": row.get("id"), "startHour": row.get("startHour"),
             "endHour": row.get("endHour")}
            for row in master_rows["mysekaiRefreshTimePeriods"]]
    if "mysekaiSiteBgms" in master_rows:
        index["siteBgms"] = [
            {"id": row.get("id"), "siteId": row.get("mysekaiSiteId"),
             "brightnessType": row.get("mysekaiPhenomenaBrightnessType"),
             "cue": row.get("cue"),
             "assetbundleName": row.get("assetbundleName"),
             "package": BGM_PACKAGE_PREFIX + str(row.get("assetbundleName"))}
            for row in site_bgm_rows]
    if "mysekaiSiteMysekaiPhenomenaSounds" in master_rows:
        index["siteSoundFallbacks"] = [
            {"id": row.get("id"), "siteId": row.get("mysekaiSiteId"),
             "cue": row.get("cue"), "package": AMBIENCE_PACKAGE}
            for row in sound_rows
            if row.get("mysekaiPhenomenaSoundConditionType") == "other"]

    for name in sorted(groups):
        variants = groups[name]
        store.load_dependencies(variants.values())
        phenomenon = _Phenomenon(name, out, store, geometry)
        phenomenon.entry["variants"] = sorted(variants)
        for variant in sorted(variants):
            phenomenon.variant(variant, variants[variant])
        entry = phenomenon.finish()
        row = by_asset.get(name)
        if row is not None:
            entry["id"] = row.get("id")
            entry["master"] = _master_entry(row)
            entry["bgms"] = [{"id": bgm.get("id"), "cue": bgm.get("cue"),
                              "assetbundleName": bgm.get("assetbundleName"),
                              "package": (BGM_PACKAGE_PREFIX
                                          + str(bgm.get("assetbundleName")))}
                             for bgm in bgm_rows
                             if bgm.get("mysekaiPhenomenaId") == row.get("id")]
            entry["siteSounds"] = [{"id": sound.get("id"),
                                    "siteId": sound.get("mysekaiSiteId"),
                                    "cue": sound.get("cue"),
                                    "package": AMBIENCE_PACKAGE}
                                   for sound in sound_rows
                                   if sound.get("mysekaiPhenomenaId") == row.get("id")]
        elif master and client_configs.get(DELIVERY_ASSET_CONFIG) == name:
            entry["id"] = client_configs.get(DELIVERY_ID_CONFIG)
            entry["note"] = ("the delivery site's phenomenon: named by client config "
                             f"rows {DELIVERY_ID_CONFIG} and {DELIVERY_ASSET_CONFIG}, "
                             "not by a row in the phenomena table")
        elif master:
            entry["note"] = "no row in the phenomena table names this package"
        index["phenomena"][name] = entry
        unsupported.extend(phenomenon.unsupported)
        omitted.extend(phenomenon.omitted)
        for bundle_name, counter in phenomenon.counts.items():
            target = per_bundle.setdefault(bundle_name, {n: 0 for n in COUNTERS})
            for field, value in counter.items():
                target[field] += value
        totals["phenomena"] += 1
        totals["overrides"] += len(entry["overrides"])
        totals["images"] += sum(1 for reference in phenomenon.images.written.values()
                                if reference.get("file"))
        totals["textureArrays"] += sum(1 for reference
                                       in phenomenon.images.written.values()
                                       if reference.get("files"))
        totals["arrayLayers"] += sum(len(reference["files"]) for reference
                                     in phenomenon.images.written.values()
                                     if reference.get("files"))
        for field, value in (("configs", entry["config"] is not None),
                             ("profiles", entry["postprocess"] is not None),
                             ("ramps", entry["ramp"] is not None)):
            totals[field] += int(bool(value))
        totals["configs"] += sum("config" in o for o in entry["overrides"].values())
        totals["profiles"] += sum("postprocess" in o
                                  for o in entry["overrides"].values())
        totals["effects"] += len(phenomenon.effects)
        totals["emitters"] += entry["fx"]["emitters"]
        totals["models"] += len(entry["models"])
        totals["timelines"] += int(entry["timeline"] is not None)
        totals["omitted"] += len(phenomenon.omitted)

    index["models"] = geometry.entries
    totals["meshes"] = len(geometry.entries)

    library = Library(out / "audio", "audio", vgmstream, ffmpeg)
    sound_packages = {}
    wanted_cues = sorted({str(row.get("cue")) for row in sound_rows
                          if row.get("cue")})
    # Both music layers name their own package: the phenomenon override and the
    # site base each hold the whole of a track, so both are extracted.
    for package in sorted({str(row.get("assetbundleName"))
                           for row in [*bgm_rows, *site_bgm_rows]
                           if row.get("assetbundleName")}):
        _audio_package(store, library, BGM_PACKAGE_PREFIX + package, None,
                       unsupported, sound_packages)
    if wanted_cues:
        _audio_package(store, library, AMBIENCE_PACKAGE, wanted_cues, unsupported,
                       sound_packages)
    if not (bgm_rows or site_bgm_rows or sound_rows):
        missing["audio"] = NO_AUDIO_ROWS
    elif not library.packages:
        missing["audio"] = NO_AUDIO_PACKAGES
    index["audio"] = library.finish()
    unsupported.extend(library.unsupported)
    totals["audioStreams"] = sum(1 for entry in library.packages
                                 for stream in entry["streams"]
                                 if stream.get("wav"))
    for entry in index["phenomena"].values():
        if entry.get("bgms") is None and entry.get("siteSounds") is None:
            continue                       # the rows themselves are unknown
        packages = {bgm["package"] for bgm in entry.get("bgms") or []}
        cues = {sound["cue"] for sound in entry.get("siteSounds") or []}
        entry["audio"] = [dict(stream, package=package["package"])
                          for package in library.packages
                          for stream in package["streams"]
                          if package["package"] in packages
                          or (package["package"] == AMBIENCE_PACKAGE
                              and stream.get("cue") in cues)]

    if thumbnail is not None:
        package = store.package(thumbnail)
        icons = out / "icons"
        for asset_name, record, path_id in (package.contents if package else []):
            if record.kinds.get(path_id) != "Texture2D":
                continue
            name = str(record.tree(path_id).get("m_Name", "") or
                       asset_name.rsplit(".", 1)[0])
            try:
                icons.mkdir(parents=True, exist_ok=True)
                record.objects[path_id].read().image.save(icons / f"{name}.png")
            except Exception as exc:                 # unreadable image format
                unsupported.append({"icon": name,
                                    "reason": f"{type(exc).__name__}: {exc}"})
                continue
            index["icons"].append(name)
            totals["icons"] += 1
        index["icons"].sort()
        for entry in index["phenomena"].values():
            wanted = (entry["master"] or {}).get("iconAssetbundleName")
            if wanted and wanted in index["icons"]:
                entry["icon"] = f"icons/{wanted}.png"
    else:
        missing["icons"] = NO_THUMBNAILS

    if store.missing:
        missing["dependencies"] = sorted(store.missing)
    totals["unsupported"] = len(unsupported)
    index["summary"] = dict(totals, missing=missing, omitted=omitted,
                            unsupported=unsupported)
    path = _write_json(out / "index.json", index)
    return dict(totals, path=str(path), perBundle=per_bundle,
                soundPackages=sound_packages)


def _audio_package(store, library, package_name, cues, unsupported, results):
    """Extract one sound package's archive, and decode the cues asked for.

    *results* is keyed by package name and records what became of every package a
    row asked for, so a caller reporting bundle by bundle can tell "no row asked
    for this archive" from "a row did, and it could not be read".
    """
    package = store.package(package_name, record_missing=False)
    if package is None:
        reason = "sound package was not supplied or reachable"
        unsupported.append({"package": package_name, "cues": cues,
                            "reason": reason})
        results[package_name] = {"status": "failed", "streams": 0, "cues": 0,
                                 "archiveBytes": 0, "error": reason}
        return None
    for asset_name, record, path_id in package.contents:
        if record.kinds.get(path_id) != "TextAsset":
            continue
        tree = record.tree(path_id)
        name = str(tree.get("m_Name", "") or asset_name).rsplit(".", 1)[0]
        entry = library.add(package_name, name,
                            archive_bytes(tree.get("m_Script")), cues)
        results[package_name] = {
            "status": "succeeded", "streams": len(entry["streams"]),
            "cues": len({stream["cue"] for stream in entry["streams"]
                         if stream.get("cue")}),
            "archiveBytes": entry["archiveBytes"], "error": ""}
        return entry
    reason = "sound package holds no audio archive"
    unsupported.append({"package": package_name, "reason": reason})
    results[package_name] = {"status": "failed", "streams": 0, "cues": 0,
                             "archiveBytes": 0, "error": reason}
    return None
