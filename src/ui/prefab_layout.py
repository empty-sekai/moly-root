"""UI prefab layout: the RectTransform truth of the screens and dialogs the
game builds from the APK player data.

Where the layout lives is not a guess.  Screen layers are fetched with
``Resources.Load("Screen/Prefabs/" + layerData.name)`` and dialogs with
``Resources.Load("Dialog/" + dialogType.ToString())``, so the whole hierarchy
of every screen and dialog -- anchors, anchored positions, sizes, pivots,
scales, sprite references, static text -- is serialized in the player data's
``resources.assets`` and in no downloadable bundle.  The per-screen download
packages (``mysekai/ui/*`` and neighbours) carry textures, atlases and effect
prefabs instead; the census in this module reports them as the second source
so a consumer knows which half of a screen comes from where.

MonoBehaviours in the player data ship without typetrees, so every game
component is hand-decoded along the declaration order the managed types fix
(the rule ``ui.talk`` established; the shared decoders live there and the
screen-specific decoders live here).  A hand decode is
only trusted when it consumes its object exactly -- a nonzero residual fails
the extraction rather than writing a half-truth.

Server-decided values (stamina, rank, jewel balance, weather schedules, reward
contents) are **not** in a prefab: the serialized text and image slots they
fill are placeholders.  Each document names them under ``runtimeValues`` so a
consumer can tell layout truth (real data) from runtime state (a named mock,
per the scope rule), instead of mistaking the placeholder for content.
"""
from __future__ import annotations

import struct
import math
import hashlib
from pathlib import Path

import UnityPy
from core.unity import configure_fallback_unity_version


configure_fallback_unity_version()

from core.jsonio import write_json
import ui.talk as talk
from ui.window_animation import DECODERS as WINDOW_ANIMATION_DECODERS
from ui.font_face import read_tmp_face_info
from ui.editor_layout import (
    DECODERS as EDITOR_LAYOUT_DECODERS,
    PREFABS as EDITOR_LAYOUT_PREFABS,
    LOADING as EDITOR_LAYOUT_LOADING,
    RUNTIME_VALUES as EDITOR_LAYOUT_RUNTIME_VALUES,
    component_runtime_sprites,
)
from ui.pointer_fields import collect_pointer_fields
from ui.clip_material import component_clip_material

# ---------------------------------------------------------------------------
# Which prefabs this extractor exports, by screen family.
# ---------------------------------------------------------------------------

# The families the order names.  The settings family holds two prefabs: the
# info screen (quality and the other-options page, rank page) and OptionDialog
# -- the volume screen, whose LiveVolume/SystemVolume sliders are the audio
# settings the owner named.  The menu family carries the menu dialog with the
# shared button bases its dialogs derive from, and the fill cover DialogBase
# instantiates as its first sibling; the HUD family is the site-switch,
# weather and resource-bar layer.
SCREENS = {
    "info": ("ScreenLayerMysekaiInfo", "OptionDialog"),
    "menu": ("MysekaiMenuDialog", "MysekaiGetResourceSubWindowDialog",
             "SubWindowDialog", "Common1ButtonDialog", "Common2ButtonDialog",
             "UIPartsDialogFillCover"),
    "hud": ("ScreenLayerMysekaiHUD", "ScreenLayerMysekaiSiteMove",
            "MysekaiWeatherDialog"),
    "shell": ("ScreenLayerMysekaiHome", "ScreenLayerMysekaiMyRoom",
              "ScreenLayerMysekaiHarvest", "ScreenLayerMysekaiDelivery"),
    "talk": ("ScreenLayerMysekaiTalk",),
    "editor": EDITOR_LAYOUT_PREFABS,
}

# How each family is fetched at runtime; stated per name because it is the
# reason the layout is in the player data and not in a bundle.
LOADING = {
    "info": 'Resources.Load("Screen/Prefabs/ScreenLayerMysekaiInfo")',
    "menu": 'Resources.Load("Dialog/" + dialogType.ToString())',
    "hud": 'Resources.Load("Screen/Prefabs/…") / "Dialog/…" per name',
    "shell": 'Resources.Load("Screen/Prefabs/" + layerData.name)',
    "talk": 'Resources.Load("Screen/Prefabs/" + layerData.name)',
    "editor": EDITOR_LAYOUT_LOADING,
}

SEMANTICS = {
    "rect": ("RectTransform is engine-typed and read from its typetree; "
             "anchoredPosition is relative to the anchor point, y up"),
    "sprites": ("an Image's sprite is resolved to its name, its atlas (the "
                "sprite's own m_AtlasTags entry, or the atlas the AtlasImage "
                "points at), and its texture with native size"),
    "text": ("CustomTextMesh is TMPro: content, font asset name, font size "
             "and alignment come from the hand-decoded TMP field chain"),
    "residual": ("every hand-decoded component must consume its bytes exactly; "
                 "one leftover byte fails the prefab instead of half-decoding"),
    "runtimeValues": ("server-decided slots are named here; the serialized "
                      "text and images they hold are placeholders, and the "
                      "values themselves are a named mock per the scope rule"),
}

# Server-decided values each family's placeholders stand for.  The field names
# are the ones the dialog census reads off the runtime code; they name the
# mock a consumer of this layout pairs with the placeholder slot.  The volume
# sliders are neither server state nor static content: their values are the
# player's own local settings (ApplicationLocalSettings persists them on
# device), so the layout ships the sliders and their serialized defaults and
# the product reads/writes them as local state.
RUNTIME_VALUES = {
    "info": ["UserMysekaiGamedata (rank / refreshedAt, per the rank page)",
             "volume slider values (local ApplicationLocalSettings, not "
             "server state: LiveVolume / SystemVolume persist on device)"],
    "menu": ["stamina value (MysekaiStaminaView)",
             "mysekai rank gauge (UIPartsMysekaiRankGauge)",
             "jewel balance (menu cells)",
             "UserResource (MysekaiGetResourceSubWindowDialog contents)"],
    "hud": ["UserMysekaiPhenomenaSchedules (weather cells)",
            "UserResource (HUD resource bar)",
            "stamina value (HUD MysekaiStaminaView)"],
    "shell": ["MysekaiMenuUIContent.ContentData (site/owner and UI callbacks)",
              "MysekaiMissionHomePanel (current mission state)",
              "site-specific action availability (runtime state)"],
    "talk": ["MySekaiTalkEngine speaker/body from the active selected script",
             "ScenarioPlayerData local auto/hide-UI state"],
    "editor": EDITOR_LAYOUT_RUNTIME_VALUES,
}


# ---------------------------------------------------------------------------
# The screen-specific decoders these prefabs need beyond ui.talk's registry.
# Declaration order fixed by the managed types; a decode must end exactly.
# ---------------------------------------------------------------------------

def decode_raw_image_base(r: talk.Reader) -> dict:
    """UnityEngine.UI.RawImage : MaskableGraphic.

    Serialized fields in declaration order: m_Texture(PPtr) + m_UVRect(Rect,
    four floats).  Sekai.UI.CustomRawImage adds none of its own (the managed
    declaration holds only a ctor), so this chain is its whole tail.
    """
    d = {}
    d.update(talk.decode_graphic_base(r))
    d.update(talk.decode_maskable_graphic_base(r))
    d["m_Texture"] = r.pptr()
    d["m_UVRect"] = r.vec4()
    return d


def decode_unity_toggle(r: talk.Reader) -> dict:
    """UnityEngine.UI.Toggle : Selectable.

    Declaration order: toggleTransition(enum) + graphic(PPtr) + m_Group(PPtr)
    + onValueChanged(ToggleEvent, a UnityEvent) + m_IsOn(bool).
    """
    d = talk.decode_selectable_base(r)
    d["toggleTransition"] = r.i32()
    d["graphic"] = r.pptr()
    d["m_Group"] = r.pptr()
    d["onValueChanged"] = talk.decode_unity_event(r)
    d["m_IsOn"] = r.bool4()
    return d


def decode_custom_toggle(r: talk.Reader) -> dict:
    """Sekai.UI.CustomToggle : Toggle, then its own [SerializeField] chain.

    se(enum) + interval(enum) + disableActionType(enum) + coverImage(PPtr)
    + optionalCoverImages(List<Graphic>) + interaction(PPtr)
    + _captionText(PPtr).  onPointerClickAction and the readonly Lazy are not
    serialized and stay off the chain.
    """
    d = decode_unity_toggle(r)
    d["se"] = r.i32()
    d["interval"] = r.i32()
    d["disableActionType"] = r.i32()
    d["coverImage"] = r.pptr()
    d["optionalCoverImages"] = talk.decode_pptr_list(r)
    d["interaction"] = r.pptr()
    d["_captionText"] = r.pptr()
    return d


def decode_custom_index_toggle_group(r: talk.Reader) -> dict:
    """ToggleGroup and the serialized CustomIndexToggleGroup field chain.

    The two optional caption references belong to the source client's group;
    runtime selection and callbacks are not serialized. The ordered PPtr list
    defines option indices, including when inactive template siblings exist.
    """
    return {
        "m_AllowSwitchOff": r.bool4(),
        "indexToggles": talk.decode_pptr_list(r),
        "selectedOnAwake": r.bool4(),
        "allText": r.pptr(),
        "otherText": r.pptr(),
    }


def decode_menu_dialog_cell(r: talk.Reader) -> dict:
    """Sekai.MenuDialogCell : MonoBehaviour -- badge(PPtr) + button(PPtr).

    menuSetting and the transition Action are not [SerializeField].
    """
    d = {}
    d["badge"] = r.pptr()
    d["button"] = r.pptr()
    return d


def decode_mysekai_info_page(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiInfoPage: the two serialized CanvasGroup references."""
    return {name: r.pptr() for name in ("_leftCanvasGroups", "_rightCanvasGroups")}


def decode_mysekai_info_option_page(r: talk.Reader) -> dict:
    """Option page component references, including the review-state tip."""
    return {name: r.pptr() for name in (
        "_voiceDLButton", "_voiceDLText", "_mysekaiVisitSettingToggleGroup",
        "_mysekaiImageQualityToggleGroup", "_mysekaiFPSSettingToggleGroup",
        "_mysekaiConvertFixtureNotificationSettingToggleGroup", "_reviewTip",
    )}


def decode_mysekai_info_tab(r: talk.Reader) -> dict:
    """Tab setup reads its toggle and changes the two referenced images."""
    return {name: r.pptr() for name in ("_toggle", "_offImage", "_onImage")}


def decode_mysekai_info(r: talk.Reader) -> dict:
    """ScreenLayer serialized base, then the information screen's bindings."""
    d = {"layerCamera": r.pptr(), "isBootDone": r.bool4()}
    d.update({name: r.pptr() for name in ("_noteImage", "_noteTabGroup")})
    d["_mysekaiInfoPages"] = talk.decode_pptr_list(r)
    d.update({name: r.pptr() for name in (
        "_particleRoot", "_pageFlickGestureListener", "_rankInfoButton",
        "_leftArrowButton", "_rightArrowButton",
    )})
    d["_infoTabs"] = talk.decode_pptr_list(r)
    d.update({name: r.pptr() for name in ("_rankPage", "_option1Page")})
    return d


def decode_mysekai_stamina_view(r: talk.Reader) -> dict:
    """MysekaiStaminaView: eight serialized component references, in order."""
    return {name: r.pptr() for name in (
        "_staminaGageImageOnStock", "_staminaGageImageOnNoStock",
        "_staminaGageImage", "_staminaDecreaseGageImage", "_staminaIcon",
        "_staminaGradiantImage", "_boostStaminaCount", "_canvasGroup",
    )}


def decode_mysekai_menu_ui_content(r: talk.Reader) -> dict:
    """MysekaiMenuUIContent: serialized references, excluding runtime state."""
    return {name: r.pptr() for name in (
        "_siteMapButton", "_leaveMysekaiButton", "_homeAreaInfo",
        "_siteNameIconImage", "_screenShotButton", "_screenShotButtonCanvasGroup",
        "_screenShotUx", "_cameraResetButton", "_menuButton", "_uiDisableButton",
        "_uiDisableButtonCanvasGroup", "_sekaiMissionHomePanel",
        "_uiDisableButtonOnPosition", "_uiDisableButtonOffPosition",
        "_screenShotButtonOnPosition", "_screenShotButtonOffPosition", "_backButton",
    )}


def decode_mysekai_custom_button(r: talk.Reader) -> dict:
    """MysekaiCustomButton wraps one serialized CustomButton reference."""
    return {"_button": r.pptr()}


def decode_soft_mask(r: talk.Reader) -> dict:
    """SoftMask: Mask's serialized flag, then the seven SoftMask fields."""
    return {
        "m_ShowMaskGraphic": r.bool4(),
        "m_DownSamplingRate": r.i32(),
        "m_Softness": r.f32(),
        "m_Alpha": r.f32(),
        "m_IgnoreParent": r.bool4(),
        "m_PartOfParent": r.bool4(),
        "m_IgnoreSelfGraphic": r.bool4(),
        "m_IgnoreSelfStencil": r.bool4(),
    }


def decode_canvas_scaler(r: talk.Reader) -> dict:
    """CanvasScaler's serialized chain; ScreenCanvasScaler adds no fields."""
    return {
        "m_UiScaleMode": r.i32(),
        "m_ReferencePixelsPerUnit": r.f32(),
        "m_ScaleFactor": r.f32(),
        "m_ReferenceResolution": r.vec2(),
        "m_ScreenMatchMode": r.i32(),
        "m_MatchWidthOrHeight": r.f32(),
        "m_PhysicalUnit": r.i32(),
        "m_FallbackScreenDPI": r.f32(),
        "m_DefaultSpriteDPI": r.f32(),
        "m_DynamicPixelsPerUnit": r.f32(),
        "m_PresetInfoIsWorld": r.bool4(),
    }


def decode_tmp_settings(r: talk.Reader) -> dict:
    """TMP_Settings in declaration order, including the two TextAsset refs.

    LineBreakingTable contains dictionaries, which Unity does not serialize;
    it contributes no bytes between the TextAsset refs and the Hangul flag.
    """
    d = {name: r.bool4() for name in (
        "m_enableWordWrapping", "m_enableKerning", "m_enableExtraPadding",
        "m_enableTintAllSprites", "m_enableParseEscapeCharacters",
        "m_EnableRaycastTarget", "m_GetFontFeaturesAtRuntime",
    )}
    d["m_missingGlyphCharacter"] = r.i32()
    d["m_warningsDisabled"] = r.bool4()
    d["m_defaultFontAsset"] = r.pptr()
    d["m_defaultFontAssetPath"] = r.string()
    for name in ("m_defaultFontSize", "m_defaultAutoSizeMinRatio",
                 "m_defaultAutoSizeMaxRatio"):
        d[name] = r.f32()
    d["m_defaultTextMeshProTextContainerSize"] = r.vec2()
    d["m_defaultTextMeshProUITextContainerSize"] = r.vec2()
    d["m_autoSizeTextContainer"] = r.bool4()
    d["m_IsTextObjectScaleStatic"] = r.bool4()
    d["m_fallbackFontAssets"] = talk.decode_pptr_list(r)
    d["m_matchMaterialPreset"] = r.bool4()
    d["m_defaultSpriteAsset"] = r.pptr()
    d["m_defaultSpriteAssetPath"] = r.string()
    d["m_enableEmojiSupport"] = r.bool4()
    d["m_MissingCharacterSpriteUnicode"] = r.u32()
    d["m_defaultColorGradientPresetsPath"] = r.string()
    d["m_defaultStyleSheet"] = r.pptr()
    d["m_StyleSheetsResourcePath"] = r.string()
    d["m_leadingCharacters"] = r.pptr()
    d["m_followingCharacters"] = r.pptr()
    d["m_UseModernHangulLineBreakingRules"] = r.bool4()
    return d


EXTRA_DECODERS = {
    **WINDOW_ANIMATION_DECODERS,
    **EDITOR_LAYOUT_DECODERS,
    "UnityEngine.UI.Toggle": decode_unity_toggle,
    "Sekai.UI.CustomRawImage": decode_raw_image_base,
    "Sekai.UI.CustomToggle": decode_custom_toggle,
    "Sekai.UI.CustomIndexToggleGroup": decode_custom_index_toggle_group,
    "Sekai.MenuDialogCell": decode_menu_dialog_cell,
    "Sekai.Mysekai.MysekaiStaminaView": decode_mysekai_stamina_view,
    "Sekai.Mysekai.ScreenLayerMysekaiInfoPage": decode_mysekai_info_page,
    "Sekai.Mysekai.MysekaiInfoOption1Page": decode_mysekai_info_option_page,
    "Sekai.Mysekai.ScreenLayerMysekaiInfoTab": decode_mysekai_info_tab,
    "Sekai.Mysekai.ScreenLayerMysekaiInfo": decode_mysekai_info,
    "Sekai.Mysekai.MysekaiMenuUIContent": decode_mysekai_menu_ui_content,
    "Sekai.Mysekai.MysekaiCustomButton": decode_mysekai_custom_button,
    "Coffee.UISoftMask.SoftMask": decode_soft_mask,
    "UnityEngine.UI.CanvasScaler": decode_canvas_scaler,
    "UnityEngine.UI.GridLayoutGroup": talk.decode_gridlayoutgroup,
    "Sekai.ScreenCanvasScaler": decode_canvas_scaler,
    "TMPro.TMP_Settings": decode_tmp_settings,
}


# ---------------------------------------------------------------------------
# Asset resolution: sprite / atlas / texture / font names for the references
# the decoded components carry.
# ---------------------------------------------------------------------------

def scriptable_object_name(raw: bytes) -> str:
    """The m_Name of a ScriptableObject such as TMP_FontAsset.

    The serialized header is m_GameObject(PPtr) + m_Enabled(bool) +
    m_Script(PPtr) -- 28 bytes -- and m_Name follows as a length-prefixed
    string.  Verified on both font assets the target screens reference.
    """
    if len(raw) < 32:
        return ""
    length = struct.unpack_from("<i", raw, 28)[0]
    if not 0 < length < 128 or 32 + length > len(raw):
        return ""
    return raw[32:32 + length].decode("utf-8", "replace")


class Resolver:
    """Names for the PPtr targets a layout document references.

    Everything a screen prefab points at lives in the same ``resources.assets``
    (sprites, sprite atlases, textures, font assets), so one object table and
    per-type typetree reads cover it.  An unresolvable reference is reported
    as such -- ``{"unresolved": …}`` -- and never as a guessed name.
    """

    def __init__(self, objects: dict, mono_index: dict, image_dir=None):
        self.objects = objects
        self.mono_index = mono_index
        self._sprite_cache = {}
        self._texture_cache = {}
        self._atlas_cache = {}
        self._font_cache = {}
        self.image_dir = Path(image_dir) if image_dir is not None else None

    def _tree(self, pid: int):
        obj = self.objects.get(pid)
        if obj is None:
            return None
        try:
            return obj.read_typetree()
        except Exception:
            return None

    def sprite(self, pp: tuple) -> dict:
        key = tuple(pp)
        if key in self._sprite_cache:
            return self._sprite_cache[key]
        fid, pid = pp
        entry = {"fileId": fid, "pathId": pid}
        if not pid:
            entry["state"] = "none"
        elif fid != 0:
            entry["state"] = "external"
        else:
            tree = self._tree(pid)
            obj = self.objects.get(pid)
            if tree is None or obj is None or obj.type.name != "Sprite":
                entry["state"] = "unresolved"
            else:
                entry.update(state="ok", name=tree.get("m_Name", ""),
                             atlas=(tree.get("m_AtlasTags") or [""])[0])
                entry["border"] = [float(tree.get("m_Border", {}).get(k, 0))
                                   for k in ("x", "y", "z", "w")]
                rect = tree["m_Rect"]
                entry["rect"] = [float(rect[k]) for k in ("x", "y", "width", "height")]
                entry["rectSize"] = entry["rect"][2:]
                ppu = float(tree["m_PixelsToUnits"])
                if not math.isfinite(ppu) or ppu <= 0:
                    raise ValueError(f"Sprite {pid} has invalid pixels per unit: {ppu}")
                entry["pixelsPerUnit"] = ppu
                entry["pixelContract"] = {
                    "serializedFile": obj.assets_file.name,
                    "spriteRect": "rect/rectSize are the original Sprite.rect, in pixels",
                    "imageSize": "size is the exported cropped image, not Sprite.rect",
                }
                # Match SpriteHelper's atlas selection, preserving the actual
                # render-data texture/trim contract instead of m_RD's empty ref.
                sprite_data = obj.read()
                render_data = tree["m_RD"]
                atlas_ptr = sprite_data.m_SpriteAtlas
                atlas_data = None
                if atlas_ptr:
                    atlas_data = atlas_ptr.deref_parse_as_object()
                elif sprite_data.m_AtlasTags:
                    atlas_objects = (o for o in self.objects.values()
                                     if o.type.name == "SpriteAtlas"
                                     and o.peek_name() == sprite_data.m_AtlasTags[0])
                    atlas_obj = next(atlas_objects, None)
                    if atlas_obj is not None:
                        atlas_data = atlas_obj.read()
                if atlas_data is not None:
                    matches = [data for key, data in atlas_data.m_RenderDataMap
                               if key == sprite_data.m_RenderDataKey]
                    if len(matches) != 1:
                        raise ValueError(f"Sprite {pid} has {len(matches)} atlas render entries")
                    rd = matches[0]
                    render_data = {
                        "texture": {"m_FileID": rd.texture.file_id,
                                    "m_PathID": rd.texture.path_id},
                        "textureRect": {k: getattr(rd.textureRect, k)
                                        for k in ("x", "y", "width", "height")},
                        "textureRectOffset": {k: getattr(rd.textureRectOffset, k)
                                              for k in ("x", "y")},
                        "settingsRaw": rd.settingsRaw,
                    }
                entry["textureRect"] = [float(render_data["textureRect"][k])
                                        for k in ("x", "y", "width", "height")]
                entry["textureRectOffset"] = [float(render_data["textureRectOffset"][k])
                                              for k in ("x", "y")]
                entry["packingSettingsRaw"] = int(render_data["settingsRaw"])
                texture_ref = render_data["texture"]
                entry["pixelContract"]["texturePPtr"] = [texture_ref["m_FileID"],
                                                          texture_ref["m_PathID"]]
                if self.image_dir is not None:
                    from UnityPy.export import SpriteHelper
                    image = SpriteHelper.get_image_from_sprite(sprite_data)
                    self.image_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"sprite-{pid}.png"
                    image.save(self.image_dir / filename)
                    entry["image"] = "textures/" + filename
                    entry["size"] = list(image.size)
                if texture_ref["m_FileID"] != 0:
                    entry["texture"] = {"fileId": texture_ref["m_FileID"],
                                        "pathId": texture_ref["m_PathID"], "state": "external"}
                else:
                    # A cropped sprite already supplies its image.  Keep the
                    # original texture identity without exporting an extra atlas.
                    entry["texture"] = self.texture_metadata(texture_ref["m_PathID"])
        self._sprite_cache[key] = entry
        return entry

    def texture_metadata(self, pid: int) -> dict:
        tree = self._tree(pid)
        obj = self.objects.get(pid)
        if tree is None or obj is None or obj.type.name != "Texture2D":
            return {"pathId": pid, "state": "unresolved"}
        return {"pathId": pid, "state": "ok", "name": tree["m_Name"],
                "size": [tree["m_Width"], tree["m_Height"]]}

    def texture_name(self, pid: int) -> dict:
        if pid in self._texture_cache:
            return self._texture_cache[pid]
        tree = self._tree(pid)
        obj = self.objects.get(pid)
        if tree is None or obj is None or obj.type.name != "Texture2D":
            entry = {"pathId": pid, "state": "unresolved"}
        else:
            entry = {"pathId": pid, "state": "ok", "name": tree.get("m_Name", ""),
                     "size": [tree.get("m_Width", 0), tree.get("m_Height", 0)]}
            if self.image_dir is not None:
                self.image_dir.mkdir(parents=True, exist_ok=True)
                filename = f"texture-{pid}.png"
                obj.read().image.save(self.image_dir / filename)
                entry["image"] = "textures/" + filename
        self._texture_cache[pid] = entry
        return entry

    def atlas_name(self, pp: tuple) -> dict:
        key = tuple(pp)
        if key in self._atlas_cache:
            return self._atlas_cache[key]
        fid, pid = pp
        entry = {"fileId": fid, "pathId": pid}
        if not pid:
            entry["state"] = "none"
        elif fid != 0:
            entry["state"] = "external"
        else:
            tree = self._tree(pid)
            obj = self.objects.get(pid)
            if tree is None or obj is None or obj.type.name != "SpriteAtlas":
                entry["state"] = "unresolved"
            else:
                entry.update(state="ok", name=tree.get("m_Name", ""))
        self._atlas_cache[key] = entry
        return entry

    def font(self, pp: tuple) -> dict:
        key = tuple(pp)
        if key in self._font_cache:
            return self._font_cache[key]
        fid, pid = pp
        entry = {"fileId": fid, "pathId": pid}
        if not pid:
            entry["state"] = "none"
        elif fid != 0:
            entry["state"] = "external"
        else:
            obj = self.objects.get(pid)
            if obj is None or obj.type.name != "MonoBehaviour":
                entry["state"] = "unresolved"
            else:
                name = scriptable_object_name(obj.get_raw_data())
                entry.update(state="ok" if name else "unnamed", name=name)
                entry["sourceFace"] = read_tmp_face_info(obj, self.mono_index)
        self._font_cache[key] = entry
        return entry


# ---------------------------------------------------------------------------
# Layout extraction
# ---------------------------------------------------------------------------

def _source_object(env, owner, pointer, tables):
    """Resolve one serialized PPtr using its owner's external-file table."""
    if isinstance(pointer, dict):
        fid, pid = pointer["m_FileID"], pointer["m_PathID"]
    else:
        fid, pid = pointer
    if not pid:
        raise ValueError("required source reference is null")
    name = owner.assets_file.name
    if fid:
        if not 1 <= fid <= len(owner.assets_file.externals):
            raise ValueError(f"invalid external file id {fid} in {name}")
        name = owner.assets_file.externals[fid - 1].name
    if name not in tables:
        tables[name] = talk.objects_by_file(env, name)
    if pid not in tables[name]:
        raise ValueError(f"source object not found: {name}:{pid}")
    return tables[name][pid]


def _strict_fields(env, obj, mono_index, expected_class):
    record = talk.decode_object(env, obj, mono_index)
    if (record["class"] != expected_class or not record.get("hand_decoded")
            or record["residual"] != 0):
        raise ValueError(f"invalid {expected_class} source object {obj.path_id}")
    return record


def extract_text_settings(env, mono_index):
    """Follow the actual Resources index to TMP Settings and its TextAssets.

    Duplicate load-name entries must agree on the requested line-breaking
    subset. Different rule data is an ambiguity, never an arbitrary choice.
    """
    tables = {}
    managers = (obj for obj in talk.objects_by_file(env, "globalgamemanagers").values()
                if obj.type.name == "ResourceManager")
    settings_objects = {}
    resource_sources = []
    for manager in managers:
        for name, pointer in manager.read_typetree()["m_Container"]:
            if name.casefold() != "tmp settings":
                continue
            obj = _source_object(env, manager, pointer, tables)
            settings_objects[(obj.assets_file.name, obj.path_id)] = obj
            resource_sources.append({"serializedFile": manager.assets_file.name,
                                     "pathId": manager.path_id, "key": name,
                                     "target": pointer})
    if not settings_objects:
        raise ValueError("TMP Settings resource entry not found")
    result = None
    sources = []
    for obj in settings_objects.values():
        record = _strict_fields(env, obj, mono_index, "TMPro.TMP_Settings")
        fields = record["fields"]
        values = {"useModernHangulLineBreakingRules":
                  fields["m_UseModernHangulLineBreakingRules"]}
        text_sources = {}
        for field, output in (("m_leadingCharacters", "leadingCharacters"),
                              ("m_followingCharacters", "followingCharacters")):
            text = _source_object(env, obj, fields[field], tables)
            if text.type.name != "TextAsset":
                raise ValueError(f"{field} does not reference a TextAsset")
            tree = text.read_typetree()
            content = tree["m_Script"]
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            if not isinstance(content, str):
                raise ValueError(f"{field} has non-text content")
            values[output] = content
            text_sources[output] = {
                "serializedFile": text.assets_file.name, "pathId": text.path_id,
                "name": tree["m_Name"],
                "rawSha256": hashlib.sha256(text.get_raw_data()).hexdigest(),
            }
        if result is not None and values != result:
            raise ValueError("TMP Settings resource entries disagree on line-breaking rules")
        result = values
        sources.append({"serializedFile": obj.assets_file.name,
                        "pathId": obj.path_id, "rawLength": record["raw_len"],
                        "residual": record["residual"], "textAssets": text_sources})
    return {"version": 1, **result,
            "source": {"resourceEntries": resource_sources, "settings": sources,
                       "contentPolicy": "verbatim TextAsset strings, including BOM"}}


def extract_host_canvas(env, mono_index):
    """The Mysekai scene's actual ScreenManager/CanvasRoot scaler contract.

    This is an explicit host input, not a fabricated per-prefab ancestor.
    Nested Canvas native getter inheritance is not inferred from serialization.
    """
    global_objects = talk.objects_by_file(env, "globalgamemanagers")
    settings = [obj for obj in global_objects.values() if obj.type.name == "BuildSettings"]
    if len(settings) != 1:
        raise ValueError("expected one BuildSettings for the scene index")
    scenes = settings[0].read_typetree()["scenes"]
    matches = [(index, path) for index, path in enumerate(scenes)
               if path.replace("\\", "/").rsplit("/", 1)[-1] == "Mysekai.unity"]
    if len(matches) != 1:
        raise ValueError("Mysekai scene is absent or ambiguous in BuildSettings")
    scene_index, scene_path = matches[0]
    scene_file = f"level{scene_index}"
    objects = talk.objects_by_file(env, scene_file)
    managers = [obj for obj in objects.values()
                if obj.type.name == "MonoBehaviour"
                and talk.resolve_script_class(obj, mono_index) == "Sekai.ScreenManager"]
    if len(managers) != 1:
        raise ValueError(f"expected one ScreenManager in {scene_file}")
    manager = managers[0]
    reader = talk.Reader(manager.get_raw_data())
    prefix = talk.unpack_mb_prefix(reader)
    go_ref = prefix["m_GameObject"]
    if go_ref[0] != 0:
        raise ValueError("ScreenManager GameObject is external")
    go = objects[go_ref[1]].read_typetree()
    components = [objects[item["component"]["m_PathID"]] for item in go["m_Component"]]
    canvases = [obj for obj in components if obj.type.name == "Canvas"]
    transforms = [obj for obj in components if obj.type.name == "RectTransform"]
    scalers = [obj for obj in components if obj.type.name == "MonoBehaviour"
               and talk.resolve_script_class(obj, mono_index) == "Sekai.ScreenCanvasScaler"]
    if len(canvases) != 1 or len(transforms) != 1 or len(scalers) != 1:
        raise ValueError("ScreenManager lacks a unique Canvas/RectTransform/ScreenCanvasScaler")
    canvas, transform, scaler = canvases[0], transforms[0], scalers[0]
    scaler_record = _strict_fields(env, scaler, mono_index, "Sekai.ScreenCanvasScaler")
    fields = scaler_record["fields"]
    frame = transform.read_typetree()
    canvas_fields = canvas.read_typetree()
    if (frame["m_Father"]["m_PathID"] != 0 or not prefix["m_Enabled"]
            or not scaler_record["meta"]["m_Enabled"] or not go["m_IsActive"]
            or not canvas_fields["m_Enabled"]):
        raise ValueError("Mysekai host is not an enabled root Canvas")
    if fields["m_UiScaleMode"] != 1 or canvas_fields["m_RenderMode"] == 2:
        raise ValueError("Mysekai host reference PPU requires a different scaler mode")
    ppu = fields["m_ReferencePixelsPerUnit"]
    if not math.isfinite(ppu) or ppu <= 0:
        raise ValueError("Mysekai host reference PPU is invalid")
    layers = []
    for pointer in frame["m_Children"]:
        child = objects[pointer["m_PathID"]]
        child_frame = child.read_typetree()
        child_go_id = child_frame["m_GameObject"]["m_PathID"]
        child_go = objects[child_go_id].read_typetree()
        if child_go["m_Name"] not in ("Layer_UI", "Layer_Dialog"):
            continue
        child_canvases = [objects[item["component"]["m_PathID"]]
                          for item in child_go["m_Component"]
                          if objects[item["component"]["m_PathID"]].type.name == "Canvas"]
        if len(child_canvases) != 1:
            raise ValueError("display layer has no unique Canvas")
        layers.append({"name": child_go["m_Name"], "gameObjectPathId": child_go_id,
                       "transformPathId": child.path_id,
                       "canvasPathId": child_canvases[0].path_id,
                       "canvasFields": child_canvases[0].read_typetree()})
    if {layer["name"] for layer in layers} != {"Layer_UI", "Layer_Dialog"}:
        raise ValueError("Mysekai display-layer hosts are missing")
    return {
        "version": 1, "referencePixelsPerUnit": ppu,
        "source": {"scene": scene_path, "serializedFile": scene_file,
                   "buildSettingsPathId": settings[0].path_id,
                   "rootGameObjectPathId": go_ref[1], "rootTransformPathId": transform.path_id,
                   "rootCanvasPathId": canvas.path_id, "screenManagerPathId": manager.path_id,
                   "scalerPathId": scaler.path_id, "scalerRawLength": scaler_record["raw_len"],
                   "scalerResidual": scaler_record["residual"]},
        "scalerFields": fields, "rootCanvasFields": canvas_fields, "layers": layers,
        "scope": ("explicit Mysekai CanvasRoot scaler input; no assertion that every "
                  "nested Canvas native getter inherits this value"),
    }


def _pptr(value):
    """A decoded PPtr tuple as JSON-able [fileId, pathId]."""
    return [value[0], value[1]] if isinstance(value, tuple) else value


def _component_record(env, obj, resolver):
    """One component as a document record, with its references resolved.

    ``talk._DECODERS`` carries the combined registry (``EXTRA_DECODERS`` is
    merged into it by :func:`extract_layout`), which is what
    ``talk.decode_object`` consults.
    """
    record = talk.decode_object(env, obj, resolver.mono_index)
    cls = record["class"]
    if (record["type"] == "MonoBehaviour" and record.get("hand_decoded")
            and record["residual"] != 0):
        # decode_object itself never raises on residual; a nonzero residual on
        # a hand decode means the chain was wrong.  Fail the prefab, loudly.
        raise ValueError(f"{cls} left {record['residual']} bytes undecoded")
    out = {
        "pathId": obj.path_id,
        "enabled": record.get("meta", {}).get("m_Enabled", True),
        "class": cls,
        "type": record["type"],
        "handDecoded": record.get("hand_decoded", False),
        "partial": (record["type"] == "MonoBehaviour"
                    and not record.get("hand_decoded")),
    }
    fields = record.get("fields") or {}
    if out["partial"]:
        out["state"] = "no decoder (partial; raw length kept)"
        out["rawLength"] = record.get("raw_len")
        return out
    out["fields"] = fields
    # Reader.pptr tuples are still typed here; never infer PPtrs from the
    # serialized [fileId,pathId] shape (a Vector2Int has the same JSON shape).
    out["pointerFields"] = collect_pointer_fields(
        fields, decoded_pptr_tuples=record.get("hand_decoded", False))
    clip_material = component_clip_material(obj, cls, fields, resolver)
    if clip_material is not None:
        out["clipMaterial"] = clip_material
    runtime_sprites = component_runtime_sprites(cls, fields, resolver)
    if runtime_sprites:
        out["runtimeSprites"] = runtime_sprites

    # Reference resolution per component family.
    if cls in ("Sekai.UI.CustomImage", "Sekai.AtlasImage", "UnityEngine.UI.Image"):
        sprite = fields.get("m_Sprite")
        if sprite is not None:
            out["sprite"] = resolver.sprite(sprite)
        if fields.get("atlas") and fields["atlas"][1]:
            ref = resolver.atlas_name(fields["atlas"])
            ref["spriteName"] = fields.get("spriteName", "")
            out["atlasImage"] = ref
    if cls in ("Sekai.UI.CustomRawImage", "UnityEngine.UI.RawImage"):
        texture = fields.get("m_Texture")
        if texture and texture[1]:
            out["texture"] = resolver.texture_name(texture[1])
    if cls in ("Sekai.UI.CustomTextMesh", "TMPro.TextMeshProUGUI"):
        font = fields.get("m_fontAsset")
        if font and font[1]:
            out["font"] = resolver.font(font)
    return out


def _fields_plain(fields):
    """PPtr tuples as lists so the document is plain JSON types."""
    return {key: ([v[0], v[1]] if isinstance(v, tuple) else
                  [ _fields_plain(item) if isinstance(item, dict) else
                    ([p[0], p[1]] if isinstance(p, tuple) else p)
                    for p in v ] if isinstance(v, list) else v)
            for key, v in fields.items()}


def extract_prefab(env, objects, root_go_pid, root_transform_pid, resolver,
                   prefab_name, family):
    """Walk one prefab's RectTransform tree and build its layout document."""
    tt_cache = {}

    def tt(pid):
        if pid not in tt_cache:
            tt_cache[pid] = objects[pid].read_typetree()
        return tt_cache[pid]

    nodes = []

    def walk(transform_pid, path):
        frame = tt(transform_pid)
        go_pid = frame["m_GameObject"]["m_PathID"]
        name = tt(go_pid).get("m_Name", "")
        here = f"{path}/{name}" if path else name
        components = []
        for entry in tt(go_pid)["m_Component"]:
            cpid = entry["component"]["m_PathID"]
            obj = objects.get(cpid)
            if obj is None or obj.type.name == "RectTransform":
                continue
            components.append(_component_record(env, obj, resolver))
        nodes.append({
            "gameObjectId": go_pid,
            "transformId": transform_pid,
            "parentTransformId": (frame.get("m_Father") or {}).get("m_PathID", 0),
            "active": bool(tt(go_pid).get("m_IsActive", True)),
            "path": here,
            "name": name,
            "rect": {
                "anchorsMin": [round(frame["m_AnchorMin"]["x"], 4),
                               round(frame["m_AnchorMin"]["y"], 4)],
                "anchorsMax": [round(frame["m_AnchorMax"]["x"], 4),
                               round(frame["m_AnchorMax"]["y"], 4)],
                "anchoredPosition": [round(frame["m_AnchoredPosition"]["x"], 4),
                                     round(frame["m_AnchoredPosition"]["y"], 4)],
                "sizeDelta": [round(frame["m_SizeDelta"]["x"], 4),
                              round(frame["m_SizeDelta"]["y"], 4)],
                "pivot": [round(frame["m_Pivot"]["x"], 4),
                          round(frame["m_Pivot"]["y"], 4)],
                "localScale": [round(frame["m_LocalScale"][k], 4)
                               for k in ("x", "y", "z")],
                "localRotation": [round(frame["m_LocalRotation"][k], 4)
                                  for k in ("x", "y", "z", "w")],
            },
            "components": components,
        })
        for kid in frame["m_Children"]:
            kpid = kid["m_PathID"]
            if kpid in objects:
                walk(kpid, here)

    walk(root_transform_pid, "")

    component_counts, partial_classes = {}, []
    sprites, texts = 0, 0
    for node in nodes:
        for comp in node["components"]:
            component_counts[comp["class"]] = component_counts.get(comp["class"], 0) + 1
            if comp.get("partial"):
                partial_classes.append(f"{node['path']} :: {comp['class']}")
            if "sprite" in comp or "atlasImage" in comp:
                sprites += 1
            if comp["class"] in ("Sekai.UI.CustomTextMesh",
                                 "TMPro.TextMeshProUGUI"):
                texts += 1
    document = {
        "version": 2,
        "prefab": prefab_name,
        "family": family,
        "source": {
            "serializedFile": "resources.assets",
            "rootGameObjectPathId": root_go_pid,
            "rootTransformPathId": root_transform_pid,
            "loading": LOADING[family],
        },
        "semantics": SEMANTICS,
        "runtimeValues": {
            "note": ("the serialized text and image slots these name are "
                     "placeholders; the values are server state and enter the "
                     "product as a named mock, never as these bytes"),
            "named": RUNTIME_VALUES[family],
        },
        "nodes": nodes,
        "summary": {
            "nodes": len(nodes),
            "components": component_counts,
            "spriteReferences": sprites,
            "textComponents": texts,
            "partialComponents": partial_classes,
        },
    }
    return document


def find_prefab_root(objects, name: str):
    """The prefab root for *name*: a RectTransform with no father whose
    GameObject carries exactly this name.

    Player-data resources hold one prefab per screen/dialog name; a same-named
    GameObject under another root is a nested instance (MysekaiMenuDialog has
    one), which the father check keeps out.
    """
    roots = []
    for pid, obj in objects.items():
        if obj.type.name != "RectTransform":
            continue
        try:
            frame = obj.read_typetree()
        except Exception:
            continue
        if frame["m_Father"]["m_PathID"]:
            continue
        go_pid = frame["m_GameObject"]["m_PathID"]
        go = objects.get(go_pid)
        if go is None or go.type.name != "GameObject":
            continue
        if go.read_typetree().get("m_Name") == name:
            roots.append((go_pid, pid))
    return roots


# ---------------------------------------------------------------------------
# Census
# ---------------------------------------------------------------------------

# Download-package families that dress the screens the player data builds.
# A package under these prefixes is opened and counted, never silently
# dropped; a name matching nothing here lands in "unclassified" instead.
BUNDLE_PREFIXES = (
    "mysekai__ui__",
    "mysekai__ui_anim__",
    "mysekai__site__sitemap__",
    "mysekai__effect__ui_anim__",
)

# Player-data name families: the Resources.Load path each is fetched by.
# Dialog prefabs are named after their DialogType member (Mysekai*Dialog,
# Common1ButtonDialog), so that family matches on a suffix; screen layers and
# UI parts match on their prefixes.
PD_NAME_FAMILIES = (
    ("screenLayer", ("ScreenLayer",), None),
    ("dialog", None, "Dialog"),
    ("uiParts", ("UIParts",), None),
)


def census_player_data(objects) -> dict:
    """Count prefab-name families in the player data's resources.assets.

    GameObjects are bucketed by name prefix; the families are the
    Resources.Load roots the runtime reads (Screen/Prefabs/, Dialog/, UI/).
    Counts are of distinct names, with per-family totals.
    """
    names = {}
    for obj in objects.values():
        if obj.type.name != "GameObject":
            continue
        try:
            name = obj.read_typetree().get("m_Name", "")
        except Exception:
            continue
        names[name] = names.get(name, 0) + 1
    families = {}
    for family, prefixes, suffix in PD_NAME_FAMILIES:
        def _match(n, prefixes=prefixes, suffix=suffix):
            if prefixes and any(n.startswith(p) for p in prefixes):
                return True
            return bool(suffix) and n.endswith(suffix) and len(n) > len(suffix)
        matched = {n: c for n, c in names.items() if _match(n)}
        mysekai = {n: c for n, c in matched.items() if "Mysekai" in n}
        families[family] = {
            "distinctNames": len(matched),
            "gameObjects": sum(matched.values()),
            "mysekaiNames": len(mysekai),
            "names": sorted(matched),
        }
    return {"gameObjects": len(names), "distinctNames": len(names),
            "families": families}


def census_bundles(bundles_root, bundle_manifest=None) -> dict:
    """What the per-screen download packages contain.

    *bundles_root* is the decrypted package directory.  *bundle_manifest*,
    when given, is the game's own bundle manifest (AssetBundleInfoNew.json):
    it supplies the isBuiltin split (built-in packages ship in the APK player
    data, not on the download path) and the declared file sizes.
    """
    root = Path(bundles_root)
    if not root.is_dir():
        return {"error": f"bundles root not found: {bundles_root}"}

    manifest = {}
    if bundle_manifest:
        mpath = Path(bundle_manifest)
        if not mpath.is_file():
            return {"error": f"bundle manifest not found: {bundle_manifest}"}
        import json
        data = json.loads(mpath.read_text(encoding="utf-8"))
        entries = data.get("bundles", data) if isinstance(data, dict) else {}
        for name, row in entries.items():
            manifest[str(name).replace("/", "__")] = row

    packages = []
    for path in sorted(root.iterdir()):
        name = path.name
        if not any(name.startswith(p) for p in BUNDLE_PREFIXES):
            continue
        from collections import Counter
        row = {"package": name, "declared": name in manifest}
        if name in manifest:
            entry = manifest[name]
            row["isBuiltin"] = bool(entry.get("isBuiltin"))
            row["fileSize"] = entry.get("fileSize")
        try:
            env = UnityPy.load(str(path))
            kinds = Counter()
            for obj in env.objects:
                kinds[obj.type.name] += 1
            row["objects"] = sum(kinds.values())
            row["kinds"] = dict(sorted(kinds.items()))
            row["state"] = "ok"
        except Exception as exc:  # noqa: BLE001 - a census reports, never guesses
            row["state"] = f"unreadable: {type(exc).__name__}"
        packages.append(row)

    classified = {p["package"]: p for p in packages}
    missing = sorted(name for name in manifest
                     if any(name.startswith(p) for p in BUNDLE_PREFIXES)
                     and name not in classified)
    return {
        "packages": packages,
        "packageCount": len(packages),
        "declaredButNotOnDisk": missing,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def extract_layout(player_data: str, out_dir, bundles_root=None,
                   bundle_manifest=None, *, prefabs=None) -> dict:
    """Write one layout document per target prefab plus the census.

    ``prefabs`` optionally selects exact names from SCREENS; unknown names
    fail before any output is written.  A targeted run omits the broad census.
    Returns the counts the caller reports.  Every registered hand decode must
    end exactly; unknown components remain explicitly partial in the document.
    """
    if not player_data:
        raise ValueError("player data path is required (the layout lives in "
                         "the APK player data, in no download bundle)")
    pd = Path(player_data)
    if not pd.is_file():
        raise FileNotFoundError(f"player data not found: {player_data}")
    selected = None if prefabs is None else set(prefabs)
    known = {name for names in SCREENS.values() for name in names}
    if selected is not None and (not selected or selected - known):
        raise ValueError(f"invalid prefab selection: {sorted(selected - known)}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    env = UnityPy.load(str(pd))
    objects = talk.objects_by_file(env, "resources.assets")
    if not objects:
        raise ValueError("resources.assets not found in the player data")
    mono_index = talk.build_monoscript_index(env)

    talk._DECODERS.update(EXTRA_DECODERS)
    text_settings = extract_text_settings(env, mono_index)
    host_canvas = extract_host_canvas(env, mono_index)
    write_json(out / "text-settings.json", text_settings)
    write_json(out / "host-canvas.json", host_canvas)
    resolver = Resolver(objects, mono_index, out / "textures")
    runtime_textures = {}
    runtime_sprites = {}
    if bundles_root and (selected is None or "MysekaiMenuDialog" in selected):
        package = Path(bundles_root) / "mysekai__ui__mysekai_menu"
        if not package.is_file():
            raise FileNotFoundError(f"UI texture bundle missing: {package.name}")
        texture_env = UnityPy.load(str(package))
        for obj in texture_env.objects:
            if obj.type.name != "Texture2D":
                continue
            texture = obj.read()
            relative = f"textures/menu-{obj.path_id}.png"
            texture.image.save(out / relative)
            runtime_textures[texture.m_Name] = relative
    written, failures = [], []
    for family, prefabs in SCREENS.items():
        for prefab in prefabs:
            if selected is not None and prefab not in selected:
                continue
            roots = find_prefab_root(objects, prefab)
            if len(roots) != 1:
                failures.append({"prefab": prefab, "family": family,
                                 "reason": f"{len(roots)} candidate roots"})
                continue
            go_pid, transform_pid = roots[0]
            document = extract_prefab(env, objects, go_pid, transform_pid,
                                      resolver, prefab, family)
            for node in document["nodes"]:
                for component in node["components"]:
                    for alias, sprite in component.get("runtimeSprites", {}).items():
                        image = sprite["image"]
                        if alias in runtime_textures and runtime_textures[alias] != image:
                            raise ValueError(f"UI runtime texture alias collision: {alias}")
                        runtime_textures[alias] = image
                        runtime_sprites[alias] = sprite
            path = write_json(out / family / f"{prefab}.json", document)
            written.append({"prefab": prefab, "family": family,
                            "path": str(path),
                            "summary": document["summary"]})

    if selected is None or runtime_textures:
        write_json(out / "textures.json", runtime_textures)
    if runtime_sprites:
        write_json(out / "runtime-sprites.json", runtime_sprites)

    census = {}
    if selected is None:
        census["playerData"] = census_player_data(objects)
    if bundles_root and selected is None:
        census["bundles"] = census_bundles(bundles_root, bundle_manifest)
    census["targets"] = [{"prefab": w["prefab"], "family": w["family"]}
                         for w in written]
    # A selected subset must not replace an existing full census.
    census_path = write_json(out / ("census.json" if selected is None
                                   else "selection.json"), census)

    return {"written": written, "failures": failures,
            "census": census, "censusPath": str(census_path)}
