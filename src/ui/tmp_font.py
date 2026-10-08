"""TMP font asset tables for the UI documents: faces, glyphs and characters.

A text component names one ``TMP_FontAsset``; which glyph a character draws
with is decided at runtime by that asset and the fallback list attached to it.
The game's ``FontAssetManager`` owns those lists for the two shared base
assets (weights EB and DB):

* ``OnInitialize`` loads ``Font/FOT-RodinNTLGPro-<weight> SDF_Base`` and
  ``... SDF_Dynamic`` from Resources and clears each base asset's
  ``m_FallbackFontAssetTable`` (so the serialized list is never used);
* ``SetupBuiltinFontAsset`` (called while the singleton managers initialize)
  loads ``Font/FOT-RodinNTLGPro-<weight>-Builtin``, clears the base list and
  adds the builtin asset, then the dynamic asset;
* ``SetupOnDemandFontAsset`` (called by ``OutGameController`` once the
  start-up download is processed) loads the asset bundle ``font/common``,
  takes ``FOT-RodinNTLGPro-<weight>-OnDemand.asset`` from it, clears the base
  list again and adds the on-demand asset, then the dynamic asset.

This module exports every font asset on those chains, every asset a written
document's text names, and every asset their own fallback tables name, with
the serialized fields TMP reads to find and advance a character.  The player
data carries no type trees; the ``font/common`` package does, and its
``TMP_FontAsset`` MonoScript has the same serialized-properties hash as the
player data's, so the package's tree is the build's own layout for the
class.  Every object is read with that tree and must be consumed to its last
byte.  Atlas pixels are not exported.
"""
from __future__ import annotations

FONT_ASSET_CLASS = "TMPro.TMP_FontAsset"
# The asset bundle FontAssetManager.SetupOnDemandFontAsset loads.
FONT_PACKAGE = "font/common"
WEIGHTS = ("EB", "DB")


def resources_names(weight: str) -> dict:
    """The Resources load paths FontAssetManager uses for one weight."""
    return {"base": f"Font/FOT-RodinNTLGPro-{weight} SDF_Base",
            "dynamic": f"Font/FOT-RodinNTLGPro-{weight} SDF_Dynamic",
            "builtin": f"Font/FOT-RodinNTLGPro-{weight}-Builtin"}


def package_asset_name(weight: str) -> str:
    """The asset name FontAssetManager requests from ``font/common``."""
    return f"FOT-RodinNTLGPro-{weight}-OnDemand.asset"


# Fields of a TMP 1.1.0 font asset that the lookup and layout read, exported
# under their serialized names.  m_Version "1.1.0" means ReadFontAssetDefinition
# performs no legacy upgrade; the legacy tables must then be empty (below).
_EXPORTED = ("m_Name", "m_Version", "m_SourceFontFileGUID", "m_AtlasPopulationMode",
             "m_FaceInfo", "m_GlyphTable", "m_CharacterTable", "m_AtlasTextureIndex",
             "m_IsMultiAtlasTexturesEnabled", "m_ClearDynamicDataOnBuild", "m_AtlasWidth",
             "m_AtlasHeight", "m_AtlasPadding", "m_AtlasRenderMode", "m_FontFeatureTable",
             "normalStyle", "normalSpacingOffset", "boldStyle", "boldSpacing", "italicStyle",
             "tabSize")
_LEGACY_EMPTY_LISTS = ("m_glyphInfoList", "fallbackFontAssets", "fontWeights")
SUPPORTED_VERSION = "1.1.0"


def package_font_assets(package_env, resolve_class):
    """The package's AssetBundle container and its TMP_FontAsset type tree.

    Returns ``(by_asset_name, node)``: container entries of class
    TMP_FontAsset keyed by the casefolded last path segment (the name the
    loader is asked for), and the one serialized type tree they share.
    """
    bundles = [obj for obj in package_env.objects if obj.type.name == "AssetBundle"]
    if len(bundles) != 1:
        raise ValueError(f"{FONT_PACKAGE}: expected one AssetBundle object, found {len(bundles)}")
    manifest = bundles[0].read_typetree()
    if manifest["m_Name"] != FONT_PACKAGE:
        raise ValueError(f"package is {manifest['m_Name']!r}, not {FONT_PACKAGE}")
    objects = {obj.path_id: obj for obj in package_env.objects}
    by_name, nodes = {}, []
    for path, info in manifest["m_Container"]:
        pointer = info["asset"]
        if pointer["m_FileID"] != 0 or pointer["m_PathID"] not in objects:
            raise ValueError(f"{FONT_PACKAGE}: container entry {path} leaves the package")
        obj = objects[pointer["m_PathID"]]
        if obj.type.name != "MonoBehaviour" or resolve_class(obj) != FONT_ASSET_CLASS:
            continue
        key = path.rsplit("/", 1)[-1].casefold()
        if key in by_name:
            raise ValueError(f"{FONT_PACKAGE}: two font assets answer to {key}")
        by_name[key] = {"containerPath": path, "object": obj}
        nodes.append(obj.serialized_type.node)
    if not nodes:
        raise ValueError(f"{FONT_PACKAGE} holds no TMP_FontAsset")
    shapes = {_node_shape(node) for node in nodes}
    if len(shapes) != 1:
        raise ValueError(f"{FONT_PACKAGE}: font assets disagree on their serialized layout")
    return by_name, nodes[0]


def _node_shape(node):
    return (node.m_Type, node.m_Name, tuple(_node_shape(child) for child in node.m_Children))


def read_font_asset(obj, node) -> dict:
    """The font asset's fields; the read must consume the object exactly."""
    fields = obj.read_typetree(nodes=node, check_read=True)
    if fields.get("m_Version") != SUPPORTED_VERSION:
        raise ValueError(f"{fields.get('m_Name')!r}: TMP font asset version "
                         f"{fields.get('m_Version')!r} would be upgraded at load")
    for name in _LEGACY_EMPTY_LISTS:
        if fields[name]:
            raise ValueError(f"{fields['m_Name']!r}: legacy table {name} is not empty")
    if fields["m_KerningTable"]["kerningPairs"]:
        raise ValueError(f"{fields['m_Name']!r}: a legacy kerning table would be upgraded at load")
    return fields


def font_asset_record(fields, *, origin, locate, asset_id, raw_length) -> dict:
    """One exported font asset.

    *locate* maps a PPtr dict of this asset to ``(object, file name)`` or
    raises; *asset_id* maps a located font asset object to its exported id.
    """
    record = {name: fields[name] for name in _EXPORTED}
    record["origin"] = origin
    record["rawLength"] = raw_length

    def target(pointer):
        if not pointer["m_PathID"]:
            return None
        obj, file_name = locate(pointer)
        return obj, file_name

    source_font = target(fields["m_SourceFontFile"])
    record["sourceFontFile"] = (None if source_font is None else {
        "serializedFile": source_font[1], "pathId": source_font[0].path_id,
        "class": source_font[0].type.name, "name": source_font[0].peek_name()})
    material = target(fields["material"])
    record["material"] = (None if material is None else {
        "serializedFile": material[1], "pathId": material[0].path_id,
        "name": material[0].peek_name()})
    atlases = []
    for pointer in fields["m_AtlasTextures"]:
        located = target(pointer)
        if located is None:
            atlases.append(None)
            continue
        texture = located[0].read_typetree()
        atlases.append({"serializedFile": located[1], "pathId": located[0].path_id,
                        "m_Name": texture["m_Name"], "m_Width": texture["m_Width"],
                        "m_Height": texture["m_Height"],
                        "m_TextureFormat": texture["m_TextureFormat"]})
    record["atlasTextures"] = atlases
    fallbacks = []
    for pointer in fields["m_FallbackFontAssetTable"]:
        located = target(pointer)
        fallbacks.append(None if located is None else asset_id(located[0], located[1]))
    record["m_FallbackFontAssetTable"] = fallbacks
    weights = []
    for pair in fields["m_FontWeightTable"]:
        entry = {}
        for side in ("regularTypeface", "italicTypeface"):
            located = target(pair[side])
            entry[side] = None if located is None else asset_id(located[0], located[1])
        weights.append(entry)
    record["m_FontWeightTable"] = weights
    record["counts"] = {"glyphs": len(fields["m_GlyphTable"]),
                        "characters": len(fields["m_CharacterTable"]),
                        "usedGlyphRects": len(fields["m_UsedGlyphRects"]),
                        "freeGlyphRects": len(fields["m_FreeGlyphRects"]),
                        "glyphPairAdjustmentRecords":
                            len(fields["m_FontFeatureTable"]["m_GlyphPairAdjustmentRecords"])}
    return record


def fallback_targets(fields):
    """PPtrs of the font assets a font asset's own tables name."""
    pointers = [p for p in fields["m_FallbackFontAssetTable"] if p["m_PathID"]]
    for pair in fields["m_FontWeightTable"]:
        pointers += [pair[side] for side in ("regularTypeface", "italicTypeface")
                     if pair[side]["m_PathID"]]
    return pointers
