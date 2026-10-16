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

import hashlib
import math
import struct
from pathlib import Path

import UnityPy
from core.unity import configure_fallback_unity_version


configure_fallback_unity_version()

from core.jsonio import write_json
import ui.talk as talk
from ui.window_animation import DECODERS as WINDOW_ANIMATION_DECODERS
from ui.notice_layout import DECODERS as NOTICE_LAYOUT_DECODERS
from ui.field_hud_layout import CN_DECODERS as FIELD_HUD_CN_DECODERS
from ui.harvest_summary_layout import DECODERS as HARVEST_SUMMARY_LAYOUT_DECODERS
from ui.delivery_layout import DECODERS as DELIVERY_LAYOUT_DECODERS
from ui.ui_particle import DECODERS as UI_PARTICLE_DECODERS
from ui.font_face import read_tmp_face_info
from ui.editor_layout import (
    DECODERS as EDITOR_LAYOUT_DECODERS,
    PREFABS as EDITOR_LAYOUT_PREFABS,
    LOADING as EDITOR_LAYOUT_LOADING,
    RUNTIME_VALUES as EDITOR_LAYOUT_RUNTIME_VALUES,
    component_runtime_sprites,
)
from ui.inventory_layout import (
    DECODERS as INVENTORY_LAYOUT_DECODERS,
    PREFABS as INVENTORY_LAYOUT_PREFABS,
    LOADING as INVENTORY_LAYOUT_LOADING,
    RUNTIME_VALUES as INVENTORY_LAYOUT_RUNTIME_VALUES,
)
from ui.bgm_select_layout import (
    DECODERS as BGM_SELECT_LAYOUT_DECODERS,
    CN_DECODERS as BGM_SELECT_CN_DECODERS,
    PREFABS as BGM_SELECT_LAYOUT_PREFABS,
    LOADING as BGM_SELECT_LAYOUT_LOADING,
    RUNTIME_VALUES as BGM_SELECT_LAYOUT_RUNTIME_VALUES,
    RESOURCE_KEYS as BGM_SELECT_LAYOUT_RESOURCE_KEYS,
    PLAYER_PACKAGE as BGM_SELECT_PLAYER_PACKAGE,
    player_package_textures as bgm_select_player_textures,
)
from ui.craft_layout import (
    DECODERS as CRAFT_LAYOUT_DECODERS,
    POINTER_PREFABS as CRAFT_POINTER_PREFABS,
    PREFABS as CRAFT_LAYOUT_PREFABS,
    LOADING as CRAFT_LAYOUT_LOADING,
    RUNTIME_VALUES as CRAFT_LAYOUT_RUNTIME_VALUES,
)
from ui.pointer_fields import collect_pointer_fields
from ui.clip_material import component_clip_material
from ui.player_source import external_file_name
from ui.animator_controller import animator_controller
from ui.wording import text_asset_decoding

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
             "UIPartsDialogFillCover", "LearnPhenomenaSubWindowDialog",
             "HonorRewardSubWindowDialog", "MysekaiRefreshBirthdayPlantSubWindowDialog",
             "CommonRewardSubWindowDialog", "UIPartsThumbnailWithBalloon",
             "UIPartsHonorImage"),
    "hud": ("ScreenLayerMysekaiHUD", "ScreenLayerMysekaiSiteMove",
            "MysekaiWeatherDialog", "MysekaiWeatherCell", "ScreenLayerMysekaiNotice",
            "ScreenLayerMysekaiHarvestSummary"),
    "shell": ("ScreenLayerMysekaiHome", "ScreenLayerMysekaiMyRoom",
              "ScreenLayerMysekaiHarvest", "ScreenLayerMysekaiDelivery"),
    "talk": ("ScreenLayerMysekaiTalk",),
    "editor": EDITOR_LAYOUT_PREFABS,
    "sitemap": ("ScreenLayerMysekaiSiteMap",),
    "bgmselect": BGM_SELECT_LAYOUT_PREFABS,
    "inventory": INVENTORY_LAYOUT_PREFABS,
    "craft": CRAFT_LAYOUT_PREFABS,
}

# Prefabs no screen or dialog loads by a Resources path: the field HUD layer
# instantiates them from serialized prefab references (the tweet display from
# ScreenLayerMysekaiHUD._TweetHeadUpDisplay, the attention displays from its
# two attention controllers' display prefab references).  The Resources index
# of the JP player data still names the one serialized file each lives in, so
# the JP export finds them there by name; the CN export does not select them.
REFERENCED_FAMILIES = {
    "fieldhud": ("MysekaiTweetHeadUpDisplay", "ObjectAttentionHeadUpDisplay",
                 "FixtureAttentionHeadUpDisplay"),
}

# Prefabs in no Resources folder at all: a screen instantiates each from one
# serialized reference and nothing else names it, so the export follows that
# reference.  Prefab -> (the prefab holding the reference, the holding
# component's class, the field).
POINTER_PREFABS = {**CRAFT_POINTER_PREFABS}


def pointer_loading(prefab):
    """How a prefab of :data:`POINTER_PREFABS` is fetched at runtime."""
    holder, holder_class, field = POINTER_PREFABS[prefab]
    return (f"Object.Instantiate of the serialized reference {holder_class}.{field} "
            f"in {holder}; no Resources path names it")

# How each family is fetched at runtime; stated per name because it is the
# reason the layout is in the player data and not in a bundle.
LOADING = {
    "info": 'Resources.Load("Screen/Prefabs/ScreenLayerMysekaiInfo")',
    "menu": 'Resources.Load("Dialog/" + dialogType.ToString())',
    "hud": 'Resources.Load("Screen/Prefabs/…") / "Dialog/…" per name',
    "shell": 'Resources.Load("Screen/Prefabs/" + layerData.name)',
    "talk": 'Resources.Load("Screen/Prefabs/" + layerData.name)',
    "editor": EDITOR_LAYOUT_LOADING,
    "sitemap": 'Resources.Load("Screen/Prefabs/" + layerData.name)',
    "bgmselect": BGM_SELECT_LAYOUT_LOADING,
    "fieldhud": ("Object.Instantiate of a serialized prefab reference (ScreenLayerMysekaiHUD."
                 "_TweetHeadUpDisplay; the attention controllers' display prefabs)"),
    "inventory": INVENTORY_LAYOUT_LOADING,
    "craft": CRAFT_LAYOUT_LOADING,
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
             "UserResource (MysekaiGetResourceSubWindowDialog contents)",
             "the honor and level HonorRewardSubWindowDialog.Setup is given "
             "(its image and balloon text)",
             "the birthday party MysekaiRefreshBirthdayPlantSubWindowDialog.Setup "
             "is given (the icon it loads)",
             "the rewards CommonRewardSubWindowDialog.Setup is given (one "
             "thumbnail with balloon per reward, paged by its start index)",
             "the honor, level and slot UIPartsHonorImage is set up with (its "
             "core texture, frame sprite, rank image and which level view shows)"],
    "hud": ["UserMysekaiPhenomenaSchedules (weather cells)",
            "UserResource (HUD resource bar)",
            "stamina value (HUD MysekaiStaminaView)",
            "MysekaiHarvestPoint (the harvest summary's experience, "
            "event point and badge texts)"],
    "shell": ["MysekaiMenuUIContent.ContentData (site/owner and UI callbacks)",
              "MysekaiMissionHomePanel (current mission state)",
              "site-specific action availability (runtime state)"],
    "talk": ["MySekaiTalkEngine speaker/body from the active selected script",
             "ScenarioPlayerData local auto/hide-UI state"],
    "editor": EDITOR_LAYOUT_RUNTIME_VALUES,
    "fieldhud": ["TweetHUDEvent.Content (the tweet text the tweet display writes "
                 "into its content text)",
                 "the follow target each display projects (the NPC, object or "
                 "fixture its event names)"],
    "sitemap": ["UserMysekaiGamedata.mysekaiRank (which sites are released)",
                "the phenomenon of the day (panel, background colours)",
                "SiteManager.CurrentSiteType (the current-location badge)"],
    "bgmselect": BGM_SELECT_LAYOUT_RUNTIME_VALUES,
    "inventory": INVENTORY_LAYOUT_RUNTIME_VALUES,
    "craft": CRAFT_LAYOUT_RUNTIME_VALUES,
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


def decode_rank_gauge(r: talk.Reader) -> dict:
    """UIPartsMysekaiRankGauge: _rankText, _gaugeExp (the CN class declares
    the same two fields in this order as the JP one)."""
    return {name: r.pptr() for name in ("_rankText", "_gaugeExp")}


def decode_gauge_exp(r: talk.Reader) -> dict:
    """UIPartsGaugeExp: gauge, restText (CustomText), restTextMesh."""
    return {name: r.pptr() for name in ("gauge", "restText", "restTextMesh")}


def decode_gauge(r: talk.Reader) -> dict:
    """UIPartsGauge: fillImage, gaugeImage."""
    return {name: r.pptr() for name in ("fillImage", "gaugeImage")}


def decode_screen_layer_data_list(r: talk.Reader) -> dict:
    """ScreenLayerDataList : ScriptableObject -- one List<ScreenLayerData> Entries."""
    return {"Entries": talk.decode_pptr_list(r)}


EXTRA_DECODERS = {
    **WINDOW_ANIMATION_DECODERS,
    **NOTICE_LAYOUT_DECODERS,
    **HARVEST_SUMMARY_LAYOUT_DECODERS,
    **DELIVERY_LAYOUT_DECODERS,
    **UI_PARTICLE_DECODERS,
    **EDITOR_LAYOUT_DECODERS,
    **INVENTORY_LAYOUT_DECODERS,
    **BGM_SELECT_LAYOUT_DECODERS,
    **BGM_SELECT_CN_DECODERS,
    **CRAFT_LAYOUT_DECODERS,
    **FIELD_HUD_CN_DECODERS,
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
    "Sekai.ScreenLayerDataList": decode_screen_layer_data_list,
    "Sekai.Mysekai.UIPartsMysekaiRankGauge": decode_rank_gauge,
    "Sekai.UIPartsGaugeExp": decode_gauge_exp,
    "Sekai.UIPartsGauge": decode_gauge,
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


def _downscale_multiplier(render_data, label) -> float:
    """The render data's downscaleMultiplier, read from the same entry that
    supplies the texture and its texture rect: the SpriteAtlas render-data
    entry for a packed sprite, the sprite's own m_RD otherwise."""
    value = render_data.get("downscaleMultiplier")
    if value is None:
        raise ValueError(f"{label} render data has no downscaleMultiplier")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} has invalid downscaleMultiplier {value}")
    return value


class Resolver:
    """Names for the PPtr targets a layout document references.

    Single-file mode (``env`` absent): everything a screen prefab points at
    lives in the same ``resources.assets`` (sprites, sprite atlases, textures,
    font assets), so one object table and per-type typetree reads cover it and
    a nonzero file id is reported as ``external``.

    Cross-file mode (``env`` given): the prefab is one serialized file among
    many, so a PPtr is resolved relative to the file of the object that holds
    it -- file id 0 is that file, file id N is its externals entry N -- and the
    target file must be loaded in ``env``.  The holder is set per component
    with :meth:`holder`.  In both modes an unresolvable reference is reported
    as such -- ``{"state": "unresolved"}`` -- and never as a guessed name.
    """

    def __init__(self, objects: dict, mono_index: dict, image_dir=None, *,
                 env=None, decoders=None):
        self.objects = objects
        self.mono_index = mono_index
        self._sprite_cache = {}
        self._texture_cache = {}
        self._atlas_cache = {}
        self._font_cache = {}
        self._material_cache = {}
        self.image_dir = Path(image_dir) if image_dir is not None else None
        self.env = env
        self.decoders = decoders
        self._owner = None
        # Cross-file mode: target type of every decoded PPtr, and the ones
        # that did not resolve, for the document's pointer census.
        self.pointer_census = {}
        self.unresolved_pointers = []

    @property
    def cross_file(self) -> bool:
        return self.env is not None

    def holder(self, obj):
        """Set the object whose serialized fields the next references belong to."""
        self._owner = obj

    def locate(self, pp):
        """(target object or None, state or None, target file name) for one PPtr.

        State is ``none`` for a null reference, ``external`` for a nonzero
        file id in single-file mode, ``unresolved`` when the target is not
        loaded; ``None`` means the object was found.
        """
        fid, pid = pp
        if not pid:
            return None, "none", None
        if not self.cross_file:
            if fid != 0:
                return None, "external", None
            obj = self.objects.get(pid)
            return obj, (None if obj is not None else "unresolved"), None
        owner = self._owner
        if owner is None:
            raise ValueError("cross-file resolution needs the holding object")
        source = owner.assets_file
        if fid:
            if not 1 <= fid <= len(source.externals):
                return None, "unresolved", None
            name = external_file_name(source.externals[fid - 1].path)
            target = self.env.files.get(name)
            if target is None:
                return None, "unresolved", name
        else:
            target, name = source, source.name
        obj = target.objects.get(pid)
        return obj, (None if obj is not None else "unresolved"), name

    def _image_name(self, kind: str, file_name, pid: int) -> str:
        if not self.cross_file:
            return f"{kind}-{pid}.png"
        # Path ids repeat across serialized files; the file keeps names unique.
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in file_name)
        return f"{kind}-{safe}-{pid}.png"

    def _tree(self, pid: int):
        obj = self.objects.get(pid)
        if obj is None:
            return None
        try:
            return obj.read_typetree()
        except Exception:
            return None

    def sprite(self, pp: tuple) -> dict:
        if self.cross_file:
            return self._sprite_cross_file(tuple(pp))
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
                # Every serialized Sprite carries m_Border; a read without it fails.
                entry["border"] = [float(tree["m_Border"][k]) for k in ("x", "y", "z", "w")]
                rect = tree["m_Rect"]
                entry["rect"] = [float(rect[k]) for k in ("x", "y", "width", "height")]
                entry["rectSize"] = entry["rect"][2:]
                entry["pivot"] = [float(tree["m_Pivot"][k]) for k in ("x", "y")]
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
                        "downscaleMultiplier": getattr(rd, "downscaleMultiplier", None),
                    }
                entry["downscaleMultiplier"] = _downscale_multiplier(
                    render_data, f"Sprite {pid}")
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

    def _sprite_cross_file(self, pp: tuple) -> dict:
        """One Sprite reference resolved across serialized files.

        The render data follows SpriteHelper's atlas selection: a packed
        sprite reads its texture, texture rect and packing settings from the
        SpriteAtlas render-data entry keyed by its m_RenderDataKey, relative
        to the atlas's own file; an unpacked sprite reads them from its m_RD,
        relative to the sprite's file.
        """
        fid, pid = pp
        obj, state, file_name = self.locate(pp)
        if state is not None:
            entry = {"fileId": fid, "pathId": pid, "state": state}
            if file_name:
                entry["serializedFile"] = file_name
            return entry
        key = (file_name, pid)
        if key not in self._sprite_cache:
            self._sprite_cache[key] = self._sprite_body(obj, file_name)
        return {"fileId": fid, "pathId": pid, **self._sprite_cache[key]}

    def _sprite_body(self, obj, file_name) -> dict:
        from UnityPy.classes.PPtr import PPtr
        if obj.type.name != "Sprite":
            return {"state": "unresolved", "serializedFile": file_name,
                    "reason": f"target is a {obj.type.name}, not a Sprite"}
        tree = obj.read_typetree()
        body = {"state": "ok", "serializedFile": file_name, "name": tree.get("m_Name", ""),
                "atlas": (tree.get("m_AtlasTags") or [""])[0]}
        # Every serialized Sprite carries m_Border; a read without it fails.
        body["border"] = [float(tree["m_Border"][k]) for k in ("x", "y", "z", "w")]
        rect = tree["m_Rect"]
        body["rect"] = [float(rect[k]) for k in ("x", "y", "width", "height")]
        body["rectSize"] = body["rect"][2:]
        body["pivot"] = [float(tree["m_Pivot"][k]) for k in ("x", "y")]
        ppu = float(tree["m_PixelsToUnits"])
        if not math.isfinite(ppu) or ppu <= 0:
            raise ValueError(f"Sprite {file_name}:{obj.path_id} has invalid pixels per unit: {ppu}")
        body["pixelsPerUnit"] = ppu
        body["pixelContract"] = {
            "serializedFile": file_name,
            "spriteRect": "rect/rectSize are the original Sprite.rect, in pixels",
            "imageSize": "size is the exported cropped image, not Sprite.rect",
        }
        atlas_ref = tree.get("m_SpriteAtlas") or {"m_FileID": 0, "m_PathID": 0}
        render = tree["m_RD"]
        texture_ptr = PPtr(m_FileID=render["texture"]["m_FileID"],
                           m_PathID=render["texture"]["m_PathID"], assetsfile=obj.assets_file)
        texture_rect, rect_offset, settings = (render["textureRect"],
                                                render["textureRectOffset"], render["settingsRaw"])
        downscale_source = render
        atlas_source = None
        if atlas_ref["m_PathID"]:
            atlas_obj = PPtr(m_FileID=atlas_ref["m_FileID"], m_PathID=atlas_ref["m_PathID"],
                             assetsfile=obj.assets_file).deref()
            atlas_tree = atlas_obj.read_typetree()
            wanted = tree["m_RenderDataKey"]
            matches = [value for key, value in atlas_tree["m_RenderDataMap"] if key == wanted]
            if len(matches) != 1:
                raise ValueError(f"Sprite {file_name}:{obj.path_id} has {len(matches)} "
                                 f"atlas render entries")
            rd = matches[0]
            texture_ptr = PPtr(m_FileID=rd["texture"]["m_FileID"],
                               m_PathID=rd["texture"]["m_PathID"], assetsfile=atlas_obj.assets_file)
            texture_rect, rect_offset, settings = (rd["textureRect"], rd["textureRectOffset"],
                                                    rd["settingsRaw"])
            downscale_source = rd
            atlas_source = {"serializedFile": atlas_obj.assets_file.name,
                            "pathId": atlas_obj.path_id, "name": atlas_tree.get("m_Name", "")}
        elif tree.get("m_AtlasTags"):
            raise ValueError(f"Sprite {file_name}:{obj.path_id} is tagged "
                             f"{tree['m_AtlasTags'][0]!r} but carries no atlas reference")
        body["textureRect"] = [float(texture_rect[k]) for k in ("x", "y", "width", "height")]
        body["textureRectOffset"] = [float(rect_offset[k]) for k in ("x", "y")]
        body["packingSettingsRaw"] = int(settings)
        body["downscaleMultiplier"] = _downscale_multiplier(
            downscale_source, f"Sprite {file_name}:{obj.path_id}")
        body["pixelContract"]["texturePPtr"] = [texture_ptr.m_FileID, texture_ptr.m_PathID]
        if atlas_source is not None:
            body["spriteAtlas"] = atlas_source
        if self.image_dir is not None:
            from UnityPy.export import SpriteHelper
            image = SpriteHelper.get_image_from_sprite(obj.read())
            self.image_dir.mkdir(parents=True, exist_ok=True)
            filename = self._image_name("sprite", file_name, obj.path_id)
            image.save(self.image_dir / filename)
            body["image"] = "textures/" + filename
            body["size"] = list(image.size)
        if not texture_ptr.m_PathID:
            body["texture"] = {"state": "none"}
        else:
            texture = texture_ptr.deref()
            if texture.type.name != "Texture2D":
                raise ValueError(f"Sprite {file_name}:{obj.path_id} texture is a {texture.type.name}")
            texture_tree = texture.read_typetree()
            body["texture"] = {"serializedFile": texture.assets_file.name,
                               "pathId": texture.path_id, "state": "ok",
                               "name": texture_tree["m_Name"],
                               "size": [texture_tree["m_Width"], texture_tree["m_Height"]]}
        return body

    def texture(self, pp) -> dict:
        """A Texture2D reference (RawImage.m_Texture and the like)."""
        if not self.cross_file:
            return self.texture_name(pp[1])
        fid, pid = pp
        obj, state, file_name = self.locate(tuple(pp))
        if state is not None:
            return {"fileId": fid, "pathId": pid, "state": state}
        key = (file_name, pid)
        if key not in self._texture_cache:
            if obj.type.name != "Texture2D":
                body = {"state": "unresolved", "reason": f"target is a {obj.type.name}"}
            else:
                tree = obj.read_typetree()
                body = {"serializedFile": file_name, "state": "ok", "name": tree.get("m_Name", ""),
                        "size": [tree.get("m_Width", 0), tree.get("m_Height", 0)]}
                if self.image_dir is not None:
                    self.image_dir.mkdir(parents=True, exist_ok=True)
                    filename = self._image_name("texture", file_name, pid)
                    obj.read().image.save(self.image_dir / filename)
                    body["image"] = "textures/" + filename
            self._texture_cache[key] = body
        return {"fileId": fid, "pathId": pid, **self._texture_cache[key]}

    def material(self, pp) -> dict:
        """A Material reference: its preset identity, shader and saved properties.

        The saved properties are copied as serialized (floats, ints, colours,
        keyword lists); texture slots keep their target identity.  Nothing is
        defaulted: a property the material does not serialize is absent.
        """
        from UnityPy.classes.PPtr import PPtr
        from shaders.objects import shader_name
        fid, pid = pp
        obj, state, file_name = self.locate(tuple(pp))
        if state is not None:
            return {"fileId": fid, "pathId": pid, "state": state}
        key = (file_name, pid)
        if key not in self._material_cache:
            if obj.type.name != "Material":
                body = {"state": "unresolved", "reason": f"target is a {obj.type.name}"}
            else:
                tree = obj.read_typetree()
                shader_ref = tree["m_Shader"]
                shader = PPtr(m_FileID=shader_ref["m_FileID"], m_PathID=shader_ref["m_PathID"],
                              assetsfile=obj.assets_file).deref()
                saved = tree["m_SavedProperties"]
                textures = {}
                for name, slot in saved.get("m_TexEnvs", []):
                    ref = slot["m_Texture"]
                    entry = {"fileId": ref["m_FileID"], "pathId": ref["m_PathID"],
                             "scale": [slot["m_Scale"]["x"], slot["m_Scale"]["y"]],
                             "offset": [slot["m_Offset"]["x"], slot["m_Offset"]["y"]]}
                    if ref["m_PathID"]:
                        target = PPtr(m_FileID=ref["m_FileID"], m_PathID=ref["m_PathID"],
                                      assetsfile=obj.assets_file).deref()
                        entry.update(state="ok", serializedFile=target.assets_file.name,
                                     name=target.peek_name(), type=target.type.name)
                    else:
                        entry["state"] = "none"
                    textures[name] = entry
                body = {
                    "state": "ok", "serializedFile": file_name or "",
                    "name": tree.get("m_Name", ""),
                    "shader": shader_name(shader.read_typetree()),
                    "shaderSource": {"serializedFile": shader.assets_file.name,
                                     "pathId": shader.path_id},
                    "validKeywords": list(tree.get("m_ValidKeywords", [])),
                    "invalidKeywords": list(tree.get("m_InvalidKeywords", [])),
                    "customRenderQueue": tree.get("m_CustomRenderQueue"),
                    "floats": {name: value for name, value in saved.get("m_Floats", [])},
                    "ints": {name: value for name, value in saved.get("m_Ints", [])},
                    "colors": {name: [value[c] for c in ("r", "g", "b", "a")]
                               for name, value in saved.get("m_Colors", [])},
                    "textures": textures,
                }
            self._material_cache[key] = body
        return {"fileId": fid, "pathId": pid, **self._material_cache[key]}

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
        if self.cross_file:
            fid, pid = pp
            obj, state, file_name = self.locate(tuple(pp))
            if state is not None:
                return {"fileId": fid, "pathId": pid, "state": state}
            if obj.type.name != "SpriteAtlas":
                return {"fileId": fid, "pathId": pid, "state": "unresolved",
                        "reason": f"target is a {obj.type.name}"}
            return {"fileId": fid, "pathId": pid, "state": "ok", "serializedFile": file_name,
                    "name": obj.read_typetree().get("m_Name", "")}
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
        if self.cross_file:
            fid, pid = pp
            obj, state, file_name = self.locate(tuple(pp))
            if state is not None:
                return {"fileId": fid, "pathId": pid, "state": state}
            key = (file_name, pid)
            if key not in self._font_cache:
                if obj.type.name != "MonoBehaviour":
                    body = {"state": "unresolved", "reason": f"target is a {obj.type.name}"}
                else:
                    name = scriptable_object_name(obj.get_raw_data())
                    body = {"state": "ok" if name else "unnamed", "name": name,
                            "serializedFile": file_name,
                            "sourceFace": read_tmp_face_info(obj, self.mono_index)}
                self._font_cache[key] = body
            return {"fileId": fid, "pathId": pid, **self._font_cache[key]}
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


def _strict_fields(env, obj, mono_index, expected_class, decoders=None):
    record = talk.decode_object(env, obj, mono_index, decoders=decoders)
    if (record["class"] != expected_class or not record.get("hand_decoded")
            or record["residual"] != 0):
        raise ValueError(f"invalid {expected_class} source object {obj.path_id}")
    return record


def extract_text_settings(env, mono_index, decoders=None):
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
        record = _strict_fields(env, obj, mono_index, "TMPro.TMP_Settings", decoders)
        fields = record["fields"]
        values = {"useModernHangulLineBreakingRules":
                  fields["m_UseModernHangulLineBreakingRules"]}
        text_sources = {}
        for field, output in (("m_leadingCharacters", "leadingCharacters"),
                              ("m_followingCharacters", "followingCharacters")):
            text = _source_object(env, obj, fields[field], tables)
            if text.type.name != "TextAsset":
                raise ValueError(f"{field} does not reference a TextAsset")
            # TMP_Settings.GetCharacters walks file.text, and TextAsset.text
            # decodes the bytes by their byte-order mark and drops the mark.
            asset_name, data = _text_asset_bytes(text)
            content, preamble = text_asset_decoding(data)
            values[output] = content
            text_sources[output] = {
                "serializedFile": text.assets_file.name, "pathId": text.path_id,
                "name": asset_name, "bytes": len(data), "strippedPreamble": preamble,
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
                       "contentPolicy": ("TextAsset.text: decoded by byte-order mark with the "
                              "mark removed, else UTF-8")}}


def extract_host_canvas(env, mono_index, decoders=None):
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
    scaler_record = _strict_fields(env, scaler, mono_index, "Sekai.ScreenCanvasScaler", decoders)
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
    # A ScreenSpaceCamera root Canvas sizes and places itself from its
    # worldCamera's frustum (orthographic flag and size, normalized viewport
    # rect), so the camera the serialized m_Camera names is a host input too.
    # It must be an object of the same scene file; its fields are exported
    # under their serialized names and the read must consume the object.
    camera_ref = canvas_fields["m_Camera"]
    if camera_ref["m_FileID"] != 0 or camera_ref["m_PathID"] not in objects:
        raise ValueError("Mysekai root Canvas camera is null or outside the scene file")
    camera = objects[camera_ref["m_PathID"]]
    if camera.type.name != "Camera":
        raise ValueError(f"Mysekai root Canvas m_Camera is a {camera.type.name}, not a Camera")
    camera_fields = camera.read_typetree(check_read=True)
    # The camera's pose comes from its own Transform chain; a screen-space-camera
    # Canvas moves itself in front of its camera only when the camera is not
    # one of its descendants. Export the camera's Transform and every parent up
    # to the scene root, and the root Canvas' own Transform, each read exactly.
    camera_go_ref = camera_fields["m_GameObject"]
    if camera_go_ref["m_FileID"] != 0 or camera_go_ref["m_PathID"] not in objects:
        raise ValueError("Mysekai root Canvas camera GameObject is outside the scene file")
    camera_go = objects[camera_go_ref["m_PathID"]].read_typetree()
    camera_transforms = [objects[item["component"]["m_PathID"]] for item in camera_go["m_Component"]
                         if objects[item["component"]["m_PathID"]].type.name in ("Transform", "RectTransform")]
    if len(camera_transforms) != 1:
        raise ValueError("Mysekai root Canvas camera has no unique Transform")
    camera_chain = []
    link = camera_transforms[0]
    while True:
        link_fields = link.read_typetree(check_read=True)
        camera_chain.append({"pathId": link.path_id, "type": link.type.name, "fields": link_fields})
        father = link_fields["m_Father"]
        if father["m_FileID"] != 0:
            raise ValueError("Mysekai root Canvas camera Transform has an external parent")
        if father["m_PathID"] == 0:
            break
        if father["m_PathID"] not in objects or len(camera_chain) > 64:
            raise ValueError("Mysekai root Canvas camera Transform chain is broken")
        link = objects[father["m_PathID"]]
    root_transform_fields = transform.read_typetree(check_read=True)
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
                   "rootCanvasPathId": canvas.path_id, "rootCameraPathId": camera.path_id,
                   "rootCameraGameObjectPathId": camera_fields["m_GameObject"]["m_PathID"],
                   "screenManagerPathId": manager.path_id,
                   "scalerPathId": scaler.path_id, "scalerRawLength": scaler_record["raw_len"],
                   "scalerResidual": scaler_record["residual"]},
        "scalerFields": fields, "rootCanvasFields": canvas_fields,
        "rootCameraFields": camera_fields, "rootCameraTransformChain": camera_chain,
        "rootTransformFields": root_transform_fields, "layers": layers,
        "scope": ("explicit Mysekai CanvasRoot scaler input; no assertion that every "
                  "nested Canvas native getter inherits this value"),
    }


def extract_font_assets(env, mono_index, store, player, container, manager, text_fonts,
                        package_path, decoders=None):
    """The font document: every TMP font asset a written text can draw with.

    *text_fonts* maps (serialized file, path id) of each font asset a written
    document's text names to the number of text components naming it.  The
    chain assets ``FontAssetManager`` attaches come from the Resources index
    and from the decrypted ``font/common`` package at *package_path*; TMP
    Settings contributes its global fallbacks and default font asset.  See
    ``ui.tmp_font`` for the runtime order.
    """
    from ui import tmp_font
    from ui.player_source import container_target

    package_bytes = Path(package_path).read_bytes()
    package_env = UnityPy.load(package_bytes)
    package_index = talk.build_monoscript_index(package_env)
    by_asset_name, node = tmp_font.package_font_assets(
        package_env, lambda obj: talk.resolve_script_class(obj, package_index))
    package_files = {entry["object"].assets_file.name for entry in by_asset_name.values()}
    if len(package_files) != 1:
        raise ValueError(f"{tmp_font.FONT_PACKAGE}: font assets span serialized files")
    package_file = package_files.pop()
    if package_file in env.files:
        raise ValueError(f"{tmp_font.FONT_PACKAGE}: serialized file name {package_file} "
                         "collides with a player file")
    package_sha = hashlib.sha256(package_bytes).hexdigest()

    player_tables, package_tables = {}, {}
    records, by_source, pending = {}, {}, []

    def in_package(obj):
        return obj.assets_file.name == package_file

    def asset_id(obj, file_name):
        index = package_index if in_package(obj) else mono_index
        if (obj.type.name != "MonoBehaviour"
                or talk.resolve_script_class(obj, index) != tmp_font.FONT_ASSET_CLASS):
            raise ValueError(f"{file_name}:{obj.path_id} is not a TMP_FontAsset")
        name = scriptable_object_name(obj.get_raw_data())
        if not name:
            raise ValueError(f"{file_name}:{obj.path_id}: unnamed font asset")
        key = f"{file_name}:{obj.path_id}"
        if key not in by_source:
            if name in by_source.values():
                raise ValueError(f"two font assets are named {name}")
            by_source[key] = name
            pending.append((obj, file_name))
        return by_source[key]

    def resources_asset(load_path):
        entries = container.get(load_path.casefold())
        if not entries:
            raise ValueError(f"Resources index has no entry {load_path!r}")
        # Resources.Load<TMP_FontAsset> returns the object of that class among
        # the entries of the path; the path must name exactly one.
        found = []
        for entry in entries:
            file_name, pid = container_target(env, manager, entry)
            if file_name not in env.files:
                raise ValueError(f"{load_path}: serialized file {file_name} is not loaded")
            obj = env.files[file_name].objects.get(pid)
            if (obj is not None and obj.type.name == "MonoBehaviour"
                    and talk.resolve_script_class(obj, mono_index) == tmp_font.FONT_ASSET_CLASS):
                found.append((obj, file_name))
        if len(found) != 1:
            raise ValueError(f"{load_path}: {len(found)} TMP_FontAsset objects")
        return asset_id(*found[0])

    weights, after_builtin, after_on_demand = {}, {}, {}
    for weight in tmp_font.WEIGHTS:
        names = tmp_font.resources_names(weight)
        chain = {role: resources_asset(path) for role, path in names.items()}
        requested = tmp_font.package_asset_name(weight)
        entry = by_asset_name.get(requested.casefold())
        if entry is None:
            raise ValueError(f"{tmp_font.FONT_PACKAGE} has no {requested}")
        chain["onDemand"] = asset_id(entry["object"], package_file)
        weights[weight] = {"resourcesPaths": names, "packageAsset": requested, **chain}
        after_builtin[chain["base"]] = [chain["builtin"], chain["dynamic"]]
        after_on_demand[chain["base"]] = [chain["onDemand"], chain["dynamic"]]

    references = {}
    for (file_name, pid), count in sorted(text_fonts.items()):
        if file_name not in env.files or pid not in env.files[file_name].objects:
            raise ValueError(f"text font asset {file_name}:{pid} is not loaded")
        name = asset_id(env.files[file_name].objects[pid], file_name)
        references[name] = references.get(name, 0) + count

    # TMP Settings: its fallback list and default font asset follow a text's
    # own chain in TMP_Text.GetTextElement; every indexed copy must agree.
    settings, settings_sources = None, []
    for key in sorted(k for k in container if k.casefold() == "tmp settings"):
        for entry in container[key]:
            file_name, pid = container_target(env, manager, entry)
            obj = env.files[file_name].objects[pid]
            fields = _strict_fields(env, obj, mono_index, "TMPro.TMP_Settings", decoders)["fields"]

            def settings_font(pointer, owner=obj):
                if not pointer[1]:
                    return None
                target = _source_object(env, owner, pointer, player_tables)
                return asset_id(target, target.assets_file.name)

            value = {"m_defaultFontAsset": settings_font(fields["m_defaultFontAsset"]),
                     "m_fallbackFontAssets": [settings_font(p) for p in fields["m_fallbackFontAssets"]],
                     "m_missingGlyphCharacter": fields["m_missingGlyphCharacter"],
                     "m_enableEmojiSupport": fields["m_enableEmojiSupport"],
                     "hasDefaultSpriteAsset": bool(fields["m_defaultSpriteAsset"][1])}
            if settings is not None and value != settings:
                raise ValueError("TMP Settings resource entries disagree on font lookup fields")
            settings = value
            settings_sources.append({"serializedFile": file_name, "pathId": pid})
    if settings is None:
        raise ValueError("TMP Settings resource entry not found")

    containers = {e["object"].path_id: e["containerPath"] for e in by_asset_name.values()}
    while pending:
        obj, file_name = pending.pop(0)
        name = by_source[f"{file_name}:{obj.path_id}"]
        packaged = in_package(obj)
        fields = tmp_font.read_font_asset(obj, node)
        if fields["m_Name"] != name:
            raise ValueError(f"{file_name}:{obj.path_id}: name {fields['m_Name']!r} != {name!r}")

        def locate(pointer, owner=obj, owner_env=package_env if packaged else env,
                   tables=package_tables if packaged else player_tables):
            target = _source_object(owner_env, owner, pointer, tables)
            return target, target.assets_file.name

        record = tmp_font.font_asset_record(
            fields, origin="package" if packaged else "resources", locate=locate,
            asset_id=asset_id, raw_length=len(obj.get_raw_data()))
        if packaged:
            record["source"] = {"package": tmp_font.FONT_PACKAGE, "serializedFile": file_name,
                                "pathId": obj.path_id, "containerPath": containers[obj.path_id],
                                "packageSha256": package_sha}
        else:
            keys = sorted(key for key, entries in container.items() for e in entries
                          if container_target(env, manager, e) == (file_name, obj.path_id))
            record["source"] = {"serializedFile": file_name, "pathId": obj.path_id,
                                "resourcesKeys": keys,
                                "sourceSha256": store.sha256(player.member(file_name))}
        record["textComponents"] = references.get(name, 0)
        records[name] = record

    return {
        "version": 1,
        "fontAssets": dict(sorted(records.items())),
        "bySource": dict(sorted(by_source.items())),
        "runtimeFallbacks": {
            "owner": "Sekai.FontAssetManager",
            "weights": weights,
            "afterSetupBuiltinFontAsset": after_builtin,
            "afterSetupOnDemandFontAsset": after_on_demand,
            "order": ("OnInitialize clears each base asset's serialized fallback list; "
                      "SetupBuiltinFontAsset then sets it to the builtin list; "
                      "SetupOnDemandFontAsset, after the start-up download, clears it again "
                      "and sets the on-demand list"),
        },
        "tmpSettings": {**settings, "sources": settings_sources},
        "source": {"package": {"bundleName": tmp_font.FONT_PACKAGE,
                               "serializedFile": package_file, "bytes": len(package_bytes),
                               "sha256": package_sha},
                   "typeTree": ("the TMP_FontAsset type tree the package serializes; every "
                                "font asset is read with it and consumed exactly")},
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
    resolver.holder(obj)
    record = talk.decode_object(env, obj, resolver.mono_index, decoders=resolver.decoders)
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
            out["texture"] = resolver.texture(texture)
    if cls in ("Sekai.UI.CustomTextMesh", "TMPro.TextMeshProUGUI"):
        font = fields.get("m_fontAsset")
        if font and font[1]:
            out["font"] = resolver.font(font)
        if resolver.cross_file:
            # TMP_Text serializes m_sharedMaterial (a null PPtr when unset); a
            # decoded text component without it fails here.
            out["sharedMaterial"] = resolver.material(fields["m_sharedMaterial"])
    if record["type"] == "Animator":
        controller = animator_controller(obj, _file_lookup(obj, resolver))
        if controller is not None:
            out["animatorController"] = controller
    if resolver.cross_file:
        _pointer_census(out, fields, resolver)
    return out


def _file_lookup(obj, resolver):
    """Serialized files by name, for following a component's pointers."""
    if resolver.cross_file:
        return resolver.env.files.get
    parent = getattr(obj.assets_file, "parent", None)
    return (getattr(parent, "files", None) or {}).get


def _value_at(fields, path):
    """The value one collected pointer path names inside decoded fields."""
    value = fields
    for raw in path.split("/")[1:]:
        key = raw.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, (list, tuple)) else value[key]
    return value


def _pointer_census(out, fields, resolver):
    """Resolve every decoded PPtr of one component to its target type.

    The census is name-blind: a reference counts as a Sprite because its
    target object is one, not because of its field name.  Sprite targets
    other than the Image's own ``m_Sprite`` (the selectable sprite states and
    any other sprite field) are resolved under ``spritePointers`` so every
    sprite a component can show is exported.  Unresolved targets are listed.
    """
    census = resolver.pointer_census
    sprites = {}
    for path in out["pointerFields"]:
        value = _value_at(fields, path)
        pointer = ((value["m_FileID"], value["m_PathID"]) if isinstance(value, dict)
                   else tuple(value))
        obj, state, file_name = resolver.locate(pointer)
        if state == "none":
            census["null"] = census.get("null", 0) + 1
            continue
        if state is not None:
            resolver.unresolved_pointers.append({
                "component": out["pathId"], "class": out["class"], "field": path,
                "pointer": list(pointer), "state": state, "serializedFile": file_name})
            census["unresolved"] = census.get("unresolved", 0) + 1
            continue
        kind = obj.type.name
        census[kind] = census.get(kind, 0) + 1
        if kind == "Sprite":
            entry = resolver.sprite(pointer)
            if entry.get("state") != "ok":
                resolver.unresolved_pointers.append({
                    "component": out["pathId"], "class": out["class"], "field": path,
                    "pointer": list(pointer), "state": entry.get("state"),
                    "serializedFile": file_name})
            if path != "/m_Sprite":
                sprites[path] = entry
    if sprites:
        out["spritePointers"] = sprites


def _fields_plain(fields):
    """PPtr tuples as lists so the document is plain JSON types."""
    return {key: ([v[0], v[1]] if isinstance(v, tuple) else
                  [ _fields_plain(item) if isinstance(item, dict) else
                    ([p[0], p[1]] if isinstance(p, tuple) else p)
                    for p in v ] if isinstance(v, list) else v)
            for key, v in fields.items()}



def _f32(value):
    """A serialized float32, written as the double that equals it exactly.

    RectTransform fields are float32 in the serialized object; rounding them to
    a fixed number of decimals changes the value the layout solver starts from.
    The exact double is kept (not the shortest float32 decimal) because a JSON
    reader that parses to double and narrows to float32 rounds twice, and only
    an exactly representable value is safe under that.  A value that is not
    exactly a float32 means the reader did not hand over the serialized number,
    so it is refused rather than narrowed.
    """
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"non-finite RectTransform value {value!r}")
    if struct.unpack("<f", struct.pack("<f", value))[0] != value:
        raise ValueError(f"RectTransform value {value!r} is not a float32")
    return value


def extract_prefab(env, objects, root_go_pid, root_transform_pid, resolver,
                   prefab_name, family, source=None):
    """Walk one prefab's RectTransform tree and build its layout document.

    *source* replaces the single-file source block when the prefab was found
    through the Resources index in its own serialized file, or through a
    serialized reference.

    A prefab whose root is a plain Transform is a 3D prefab, not a UI one (a
    preview's camera and model parent): every node of it is placed by its
    local position, rotation and scale, so its nodes carry that ``transform``
    record in place of ``rect``, and the walk goes down through every plain
    Transform.  Under a RectTransform root a plain Transform still ends the
    walk and is listed in ``plainTransformSubtrees``.
    """
    tt_cache = {}

    def tt(pid):
        if pid not in tt_cache:
            tt_cache[pid] = objects[pid].read_typetree()
        return tt_cache[pid]

    nodes = []
    # A plain Transform under a UI root has no rect: its children are placed
    # from its transform origin, not from an anchor, so the rect records below
    # cannot express its subtree.  It is listed by name instead of dropped.
    plain_transforms = []

    spatial = "m_AnchorMin" not in tt(root_transform_pid)

    def walk(transform_pid, path):
        frame = tt(transform_pid)
        go_pid = frame["m_GameObject"]["m_PathID"]
        name = tt(go_pid).get("m_Name", "")
        here = f"{path}/{name}" if path else name
        if "m_AnchorMin" not in frame and not spatial:
            plain_transforms.append({
                "path": here,
                "transformId": transform_pid,
                "active": bool(tt(go_pid).get("m_IsActive", True)),
                "localPosition": [_f32(frame["m_LocalPosition"][k]) for k in ("x", "y", "z")],
                "localRotation": [_f32(frame["m_LocalRotation"][k]) for k in ("x", "y", "z", "w")],
                "localScale": [_f32(frame["m_LocalScale"][k]) for k in ("x", "y", "z")],
                "children": len(frame["m_Children"]),
            })
            return
        components = []
        for entry in tt(go_pid)["m_Component"]:
            cpid = entry["component"]["m_PathID"]
            obj = objects.get(cpid)
            if obj is None or obj.type.name in ("RectTransform", "Transform"):
                continue
            components.append(_component_record(env, obj, resolver))
        if "m_AnchorMin" in frame:
            place_key, place = "rect", {
                "anchorsMin": [_f32(frame["m_AnchorMin"][k]) for k in ("x", "y")],
                "anchorsMax": [_f32(frame["m_AnchorMax"][k]) for k in ("x", "y")],
                "anchoredPosition": [_f32(frame["m_AnchoredPosition"][k]) for k in ("x", "y")],
                "sizeDelta": [_f32(frame["m_SizeDelta"][k]) for k in ("x", "y")],
                "pivot": [_f32(frame["m_Pivot"][k]) for k in ("x", "y")],
                "localScale": [_f32(frame["m_LocalScale"][k]) for k in ("x", "y", "z")],
                "localRotation": [_f32(frame["m_LocalRotation"][k])
                                  for k in ("x", "y", "z", "w")],
            }
        else:
            place_key, place = "transform", {
                "localPosition": [_f32(frame["m_LocalPosition"][k]) for k in ("x", "y", "z")],
                "localRotation": [_f32(frame["m_LocalRotation"][k])
                                  for k in ("x", "y", "z", "w")],
                "localScale": [_f32(frame["m_LocalScale"][k]) for k in ("x", "y", "z")],
            }
        nodes.append({
            "gameObjectId": go_pid,
            "transformId": transform_pid,
            "parentTransformId": (frame.get("m_Father") or {}).get("m_PathID", 0),
            "active": bool(tt(go_pid).get("m_IsActive", True)),
            "path": here,
            "name": name,
            place_key: place,
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
        "source": source if source is not None else {
            "serializedFile": "resources.assets",
            "rootGameObjectPathId": root_go_pid,
            "rootTransformPathId": root_transform_pid,
            "loading": LOADING[family],
        },
        "semantics": ({**SEMANTICS, "transform": SPATIAL_SEMANTICS} if spatial else SEMANTICS),
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
            "plainTransformSubtrees": plain_transforms,
        },
    }
    if spatial:
        document["root"] = "Transform"
    return document


# The node record of a prefab with a plain Transform root (see extract_prefab).
SPATIAL_SEMANTICS = ("a 3D prefab: its root is a plain Transform, and each node is placed "
                     "by its Transform's local position, rotation (quaternion x, y, z, w) "
                     "and scale relative to its parent, read from the typetree; no node "
                     "carries a rect")


def _prefab_components(objects, root_transform_pid):
    """Every component object of the prefab hierarchy under *root_transform_pid*."""
    out, pending = [], [root_transform_pid]
    while pending:
        frame = objects[pending.pop()].read_typetree()
        game_object = objects[frame["m_GameObject"]["m_PathID"]].read_typetree()
        for pid in _component_ids(game_object):
            obj = objects.get(pid)
            if obj is not None:
                out.append(obj)
        pending.extend(kid["m_PathID"] for kid in frame["m_Children"]
                       if kid["m_PathID"] in objects)
    return out


def follow_prefab_reference(env, objects, holder_transform_pid, holder_class, field,
                            resolver, file_objects):
    """The prefab one component field of a holder prefab references.

    The holder prefab is the hierarchy under *holder_transform_pid* in
    *objects*; exactly one component of *holder_class* in it must hold the
    reference, read by its hand decode with nothing left over.  The reference
    is resolved the way the layout resolves every pointer (*resolver*); it may
    name the prefab's root GameObject or a component on it (a field typed as a
    component class), and either must be the root of its prefab: its
    transform has no father.  *file_objects* maps a serialized file name to its
    objects (not consulted in single-file mode).  Returns the reference record
    and ``(root GameObject path id, root transform path id, objects)``.
    """
    holders = [obj for obj in _prefab_components(objects, holder_transform_pid)
               if obj.type.name == "MonoBehaviour"
               and talk.resolve_script_class(obj, resolver.mono_index) == holder_class]
    if len(holders) != 1:
        raise ValueError(f"{holder_class}: {len(holders)} components in the holder prefab")
    holder = holders[0]
    record = talk.decode_object(env, holder, resolver.mono_index, decoders=resolver.decoders)
    if not record.get("hand_decoded") or record["residual"]:
        raise ValueError(f"{holder_class} is not read exactly (hand decoded "
                         f"{record.get('hand_decoded')}, residual {record['residual']})")
    pointer = record["fields"].get(field)
    if not isinstance(pointer, tuple):
        raise ValueError(f"{holder_class} has no reference field {field}")
    resolver.holder(holder)
    target, state, file_name = resolver.locate(pointer)
    if state is not None:
        raise ValueError(f"{holder_class}.{field} {list(pointer)} is {state}")
    target_objects = objects if file_name is None else file_objects(file_name)
    if target.type.name == "GameObject":
        go_pid, target_class = target.path_id, "UnityEngine.GameObject"
    else:
        target_record = talk.decode_object(env, target, resolver.mono_index,
                                           decoders=resolver.decoders)
        owner = (target_record["meta"]["m_GameObject"] if target_record["type"] == "MonoBehaviour"
                 else _pointer_tuple(target_record["fields"].get("m_GameObject")))
        if owner is None or owner[0] != 0:
            raise ValueError(f"{holder_class}.{field}: the referenced {target_record['class']} "
                             "has no GameObject in its own file")
        go_pid, target_class = owner[1], target_record["class"]
    game_object = target_objects.get(go_pid)
    if game_object is None or game_object.type.name != "GameObject":
        raise ValueError(f"{holder_class}.{field}: GameObject {go_pid} is not in its file")
    transforms = [pid for pid in _component_ids(game_object.read_typetree())
                  if pid in target_objects
                  and target_objects[pid].type.name in ("Transform", "RectTransform")]
    if len(transforms) != 1:
        raise ValueError(f"{holder_class}.{field}: GameObject {go_pid} has "
                         f"{len(transforms)} transforms")
    if target_objects[transforms[0]].read_typetree()["m_Father"]["m_PathID"]:
        raise ValueError(f"{holder_class}.{field}: GameObject {go_pid} is not a prefab root")
    reference = {
        "holderClass": holder_class, "field": field,
        "holderSerializedFile": holder.assets_file.name, "holderComponentPathId": holder.path_id,
        "pointer": [pointer[0], pointer[1]],
        **({"targetSerializedFile": file_name} if file_name is not None else {}),
        "targetClass": target_class, "targetPathId": target.path_id,
        "rootTransformType": target_objects[transforms[0]].type.name,
    }
    return reference, (go_pid, transforms[0], target_objects)


def _pointer_tuple(value):
    if isinstance(value, tuple):
        return value
    if isinstance(value, dict) and {"m_FileID", "m_PathID"} <= set(value):
        return (value["m_FileID"], value["m_PathID"])
    return None


def _component_ids(game_object):
    out = []
    for entry in game_object.get("m_Component") or []:
        pointer = entry.get("component", entry) if isinstance(entry, dict) else entry
        if isinstance(pointer, dict) and pointer.get("m_PathID", 0):
            out.append(pointer["m_PathID"])
    return out


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
                   bundle_manifest=None, *, prefabs=None, client_version=None) -> dict:
    """Write one layout document per target prefab plus the census.

    With *client_version* the run also writes the colour palette and the tween
    defaults (see :func:`extract_player_settings`); each names that version and
    the region in its source block, which is why they need it.

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
    settings = []
    if client_version is not None:
        settings = extract_player_settings(env, mono_index, pd, out, client_version)
    resolver = Resolver(objects, mono_index, out / "textures")
    runtime_textures = {}
    runtime_sprites = {}
    if bundles_root and (selected is None or "MysekaiMenuDialog" in selected):
        package = Path(bundles_root) / "mysekai__ui__mysekai_menu"
        if not package.is_file():
            raise FileNotFoundError(f"UI texture bundle missing: {package.name}")
        texture_env = UnityPy.load(str(package))
        (out / "textures").mkdir(parents=True, exist_ok=True)
        for obj in texture_env.objects:
            if obj.type.name != "Texture2D":
                continue
            texture = obj.read()
            relative = f"textures/menu-{obj.path_id}.png"
            texture.image.save(out / relative)
            runtime_textures[texture.m_Name] = relative
    if bundles_root and (selected is None or "ScreenLayerMysekaiNotice" in selected):
        sitemap = Path(bundles_root) / SITEMAP_TEXTURE_PACKAGE
        if not sitemap.is_file():
            raise FileNotFoundError(f"UI texture bundle missing: {sitemap.name}")
        package_bytes = sitemap.read_bytes()
        # No master here: every icon Sprite of the package is exported (the
        # view takes "icon_" + iconAssetbundleName of the day's row).
        textures, sprites, missing = _site_map_phenomena_sprites(
            package_bytes, _package_icon_names(package_bytes), out, out / "textures")
        for alias in textures:
            if alias in runtime_textures:
                raise ValueError(f"UI runtime texture alias collision: {alias}")
        runtime_textures.update(textures)
        runtime_sprites.update(sprites)
    written, failures = [], []
    for family, prefabs in SCREENS.items():
        for prefab in prefabs:
            if selected is not None and prefab not in selected:
                continue
            source = None
            if prefab in POINTER_PREFABS:
                holder, holder_class, field = POINTER_PREFABS[prefab]
                holder_roots = find_prefab_root(objects, holder)
                if len(holder_roots) != 1:
                    failures.append({"prefab": prefab, "family": family, "reason":
                                     f"holder {holder}: {len(holder_roots)} candidate roots"})
                    continue
                reference, (go_pid, transform_pid, _) = follow_prefab_reference(
                    env, objects, holder_roots[0][1], holder_class, field, resolver, None)
                name = objects[go_pid].read_typetree().get("m_Name")
                if name != prefab:
                    raise ValueError(f"{prefab}: {holder_class}.{field} names {name!r}")
                source = {
                    "serializedFile": "resources.assets",
                    "rootGameObjectPathId": go_pid,
                    "rootTransformPathId": transform_pid,
                    "loading": pointer_loading(prefab),
                    "reference": {"holderPrefab": holder,
                                  "holderRootGameObjectPathId": holder_roots[0][0], **reference},
                }
            else:
                roots = find_prefab_root(objects, prefab)
                if len(roots) != 1:
                    failures.append({"prefab": prefab, "family": family,
                                     "reason": f"{len(roots)} candidate roots"})
                    continue
                go_pid, transform_pid = roots[0]
            document = extract_prefab(env, objects, go_pid, transform_pid,
                                      resolver, prefab, family, source=source)
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
            "census": census, "censusPath": str(census_path), "settings": settings}


# The region of the builds this container-build path reads.
CONTAINER_REGION = "cn"
# The CN client's WordingManager.ForceInit loads "Wording/" + the current
# language + "/wording".  Its GameConfig static constructor sets the language
# to zh_cn, and EnvironmentConfig.GetCurrentLanguage returns that default, so
# the other wording CSVs the build carries (the untagged one, korean, zh_tw)
# are never read.
CONTAINER_WORDING_RESOURCE = "wording/zh_cn/wording"


def _container_resources(env):
    """The build's Resources index: its ResourceManager and every load name's targets.

    Load names are case-folded (Resources.Load matches them without case);
    each maps to the (file id, path id) pointers the index stores for it.
    """
    managers = [obj for obj in talk.objects_by_file(env, "globalgamemanagers").values()
                if obj.type.name == "ResourceManager"]
    if len(managers) != 1:
        raise ValueError(f"expected one ResourceManager, found {len(managers)}")
    manager = managers[0]
    container = {}
    for name, pointer in manager.read_typetree()["m_Container"]:
        container.setdefault(str(name).casefold(), []).append(
            (pointer["m_FileID"], pointer["m_PathID"]))
    return manager, container


def extract_player_settings(env, mono_index, player_data, out, client_version):
    """The colour palette and the tween defaults of a container build.

    Both are Resources assets, found through the build's own Resources index
    (the ResourceManager in globalgamemanagers) by the load names the runtime
    uses, and read with the same decoders and documents as a region root's
    (:mod:`ui.ui_settings`).  Each document's source block names the region
    and *client_version*, so a reader of a root shared between regions can
    refuse a document of another region.  Returns one summary row per document.
    """
    from ui import ui_settings

    decoders = {**talk._DECODERS, **ui_settings.DECODERS}
    manager, container = _container_resources(env)
    player_sha = hashlib.sha256(Path(player_data).read_bytes()).hexdigest()
    tables, rows = {}, []
    for key, cls, build, document, kind in (
            (ui_settings.PALETTE_RESOURCE, ui_settings.PALETTE_CLASS,
             ui_settings.palette_document, "palette.json", "palette"),
            (ui_settings.TWEEN_SETTINGS_RESOURCE, ui_settings.TWEEN_SETTINGS_CLASS,
             ui_settings.tween_settings_document, "tween-settings.json", "tween-settings")):
        targets = sorted(set(container.get(key, ())))
        if len(targets) != 1:
            raise ValueError(f"Resources index has {len(targets)} targets for {key!r}")
        obj = _source_object(env, manager, targets[0], tables)
        record = _strict_fields(env, obj, mono_index, cls, decoders)
        path = write_json(out / document, build(record, {
            "region": CONTAINER_REGION, "clientVersion": client_version,
            "resourceKey": key, "serializedFile": obj.assets_file.name, "pathId": obj.path_id,
            "class": cls, "rawLength": record["raw_len"], "residual": record["residual"],
            "playerDataSha256": player_sha}))
        rows.append({"document": document, "kind": kind, "serializedFile": obj.assets_file.name,
                     "pathId": obj.path_id, "path": str(path)})
    return rows


def extract_container_wordings(player_data, master_dir, out_path, client_version) -> dict:
    """The runtime wording dictionary of a container build, as one document.

    The CN client builds it in two steps, by its own rules
    (:mod:`ui.wording`): the built-in CSV of its language, then every master
    ``wordings`` row of *master_dir* applied over it.  The CN client replaces
    ``_x_COMMA_x_`` with a comma and backslash-n with a newline on every
    master value, and so does this document.  A shared root reads this
    document as its ``wordings.json`` in place of the master table alone, so
    a key only the CSV defines resolves there too and a master value reads as
    the client shows it.  The source block names the region,
    *client_version*, the CSV asset and the master table (with the identity
    of ``master-source.json`` when the directory has one).  Returns the
    document's summary and path.
    """
    from ui import wording as wording_rules
    from core.master import Master

    pd = Path(player_data)
    if not pd.is_file():
        raise FileNotFoundError(f"player data not found: {player_data}")
    master_path = Path(master_dir)
    if not (master_path / "wordings.json").is_file():
        raise FileNotFoundError(f"master wordings.json not found under {master_dir}")
    if not client_version:
        raise ValueError("the client version is required: the document's source block names it")
    env = UnityPy.load(str(pd))
    manager, container = _container_resources(env)
    key = CONTAINER_WORDING_RESOURCE
    targets = sorted(set(container.get(key, ())))
    if len(targets) != 1:
        raise ValueError(f"Resources index has {len(targets)} targets for {key!r}")
    obj = _source_object(env, manager, targets[0], {})
    if obj.type.name != "TextAsset":
        raise ValueError(f"{key} is a {obj.type.name}, not a TextAsset")
    asset_name, data = _text_asset_bytes(obj)
    builtin, rejected = wording_rules.parse_builtin_csv_container(wording_rules.text_asset_text(data))
    master_rows = Master(str(master_path)).table("wordings")
    master_identity = None
    marker = master_path / "master-source.json"
    if marker.is_file():
        import json
        master_identity = json.loads(marker.read_text(encoding="utf-8")).get("identity")
    document = wording_rules.wording_document(builtin, rejected, master_rows, {
        "region": CONTAINER_REGION, "clientVersion": client_version,
        "unityVersion": str(manager.assets_file.unity_version),
        "builtin": {"resourceKey": key, "serializedFile": obj.assets_file.name,
                    "pathId": obj.path_id, "name": asset_name, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "playerDataSha256": hashlib.sha256(pd.read_bytes()).hexdigest()},
        "master": {"table": "wordings", "rows": len(master_rows), "identity": master_identity,
                   "sha256": hashlib.sha256((master_path / "wordings.json").read_bytes()).hexdigest()},
    }, master_value=wording_rules.container_value)
    path = write_json(Path(out_path), document)
    return {"path": str(path), "serializedFile": obj.assets_file.name, "pathId": obj.path_id,
            "documentSha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            **document["summary"]}


# ---------------------------------------------------------------------------
# Entry point for player data without resources.assets: the Resources index
# points at one serialized file per asset (see ui.player_source).
# ---------------------------------------------------------------------------

# Resources.Load roots screens and dialogs are fetched by, spelled the way the
# Resources index stores its keys (lower case).
RESOURCE_ROOTS = ("screen/prefabs/", "dialog/")
# The editor's list and cell prefabs sit in Resources folders too, but no code
# path loads them by a Resources path: the screen that shows them holds
# serialized references to them.  The Resources index still names the one
# serialized file each lives in, so they are found there by their name.
REFERENCED_PREFAB_ROOTS = ("mysekai/prefabs/inventory/", "ui/uiparts/")
# The Resources folders each referenced family's prefabs sit in.
REFERENCED_FAMILY_ROOTS = {
    "editor": REFERENCED_PREFAB_ROOTS,
    "inventory": REFERENCED_PREFAB_ROOTS,
    "craft": REFERENCED_PREFAB_ROOTS,
    "fieldhud": ("mysekai/prefabs/",),
}
# Referenced prefabs whose file name another Resources folder repeats: the
# exact Resources key of each, by the prefab name the family exports.
REFERENCED_PREFAB_KEYS = {**BGM_SELECT_LAYOUT_RESOURCE_KEYS}
# ScreenManager's registry of screen layers:
# Resources.Load<ScreenLayerDataList>("Screen/SceneEntry/EntryScreenLayers").
# ScreenLayerData.LoadScreenLayerPrefab loads "Screen/Prefabs/" + its own name.
ENTRY_SCREEN_LAYERS = "screen/sceneentry/entryscreenlayers"
SCREEN_DATA_ROOT = "screen/data/"
SCREEN_PREFAB_LOAD_ROOT = "Screen/Prefabs/"
# WordingManager.ForceInit: Resources.Load<TextAsset>("Wording/wording").
WORDING_RESOURCE = "wording/wording"
# TMP_Settings loads itself by the Resources name "TMP Settings".
TMP_SETTINGS_RESOURCE = "tmp settings"


def _plain(value):
    """Decoded fields as plain JSON types: PPtr tuples become [fileId, pathId]."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _file_objects(env, name):
    return dict(env.files[name].objects)


def _mono_accounting(env, objects, mono_index, decoders):
    """Every MonoBehaviour of one serialized file against the registry.

    Returns per-class counts: hand-decoded with zero residual, hand-decoded
    with a residual (by residual size), over-read (the chain ran past the
    object), and raw (no decoder; raw length kept).  Nothing is dropped.
    """
    ok, residual, overread, raw, unresolved = {}, {}, {}, {}, 0
    total = 0
    for obj in objects.values():
        if obj.type.name != "MonoBehaviour":
            continue
        total += 1
        cls = talk.resolve_script_class(obj, mono_index)
        if cls == "?":
            unresolved += 1
        try:
            record = talk.decode_object(env, obj, mono_index, decoders=decoders)
        except (ValueError, struct.error) as exc:
            overread.setdefault(cls, {"count": 0, "errors": []})
            overread[cls]["count"] += 1
            overread[cls]["errors"].append(f"{obj.path_id}: {type(exc).__name__}: {exc}")
            continue
        if not record.get("hand_decoded"):
            raw[cls] = raw.get(cls, 0) + 1
        elif record["residual"]:
            sizes = residual.setdefault(cls, {})
            sizes[str(record["residual"])] = sizes.get(str(record["residual"]), 0) + 1
        else:
            ok[cls] = ok.get(cls, 0) + 1
    return {
        "monoBehaviours": total,
        "unresolvedScriptClass": unresolved,
        "handDecodedExact": dict(sorted(ok.items())),
        "handDecodedResidual": dict(sorted(residual.items())),
        "overRead": dict(sorted(overread.items())),
        "raw": dict(sorted(raw.items())),
        "counts": {
            "handDecodedExact": sum(ok.values()),
            "handDecodedResidual": sum(sum(v.values()) for v in residual.values()),
            "overRead": sum(v["count"] for v in overread.values()),
            "raw": sum(raw.values()),
        },
    }


def _closure_of(env, root):
    """File names reachable from *root* through loaded externals tables."""
    seen, queue = set(), [root]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        file = env.files.get(name)
        if file is None:
            continue
        queue.extend(external_file_name(e.path) for e in file.externals)
    return seen


def _document_counts(document):
    decoded, raw, monos = {}, {}, 0
    for node in document["nodes"]:
        for comp in node["components"]:
            if comp["type"] != "MonoBehaviour":
                continue
            monos += 1
            bucket = raw if comp.get("partial") else decoded
            bucket[comp["class"]] = bucket.get(comp["class"], 0) + 1
    return monos, dict(sorted(decoded.items())), dict(sorted(raw.items()))


def _wording_key_counts(document, entries):
    """Wording keys the document's texts resolve when they start.

    CustomTextMesh and CustomText resolve ``wordingKey`` through the wording
    dictionary when ``useWordingKey`` is set; each distinct key is looked up
    in the merged dictionary this run writes.
    """
    keys, empty = {}, 0
    for node in document["nodes"]:
        for comp in node["components"]:
            fields = comp.get("fields") or {}
            if comp.get("partial") or fields.get("useWordingKey") is not True:
                continue
            key = fields.get("wordingKey")
            if not key:
                empty += 1
                continue
            keys.setdefault(key, set()).add(comp["class"])
    missing = sorted(key for key in keys if key not in entries)
    return {"distinct": len(keys), "resolved": len(keys) - len(missing), "missing": missing,
            "useWordingKeyWithoutKey": empty,
            "byClass": {cls: sum(1 for owners in keys.values() if cls in owners)
                        for cls in sorted({c for owners in keys.values() for c in owners})}}


def _text_asset_bytes(obj):
    """TextAsset serialized as m_Name then m_Script (length-prefixed bytes)."""
    reader = talk.Reader(obj.get_raw_data())
    name = reader.string()
    length = reader.i32()
    if length < 0 or length > reader.remaining():
        raise ValueError(f"TextAsset {obj.path_id} script length {length} over-reads")
    data = reader._b.read(length)
    reader._align4()
    reader.expect_end()
    return name, data


# The menu dialog's cells take their button art by texture name from this
# download package (decrypted file name), not from the player data.
MENU_TEXTURE_PACKAGE = "mysekai__ui__mysekai_menu"
# SiteMapPhenomenaView (the notice layer's weather banner and the site map's
# weather panel) loads its sprites at runtime from this download package:
# Setup takes "bg_site_info", UpdatePhenomena takes "icon_" plus the
# phenomenon master row's iconAssetbundleName.  They are exported as runtime
# sprites under this alias prefix, with the Sprite metrics the Image reads.
SITEMAP_TEXTURE_PACKAGE = "mysekai__site__sitemap__texture"
SITE_MAP_PHENOMENA_ALIAS = "site-map-phenomena/"
SITE_MAP_PHENOMENA_BACKGROUND = "bg_site_info"


def _package_icon_names(package_bytes):
    """The phenomenon icon keys the sitemap texture package carries: its
    "icon_" Sprites without the prefix, sorted."""
    env = UnityPy.load(package_bytes)
    names = {obj.read_typetree()["m_Name"] for obj in env.objects if obj.type.name == "Sprite"}
    return sorted(name[len("icon_"):] for name in names if name.startswith("icon_"))


def _site_map_phenomena_sprites(package_bytes, icon_names, out, image_dir):
    """Export the SiteMapPhenomenaView sprites of the sitemap texture package.

    Returns ``(textures, sprites, missing)`` keyed by alias; the background
    must exist, an icon the package lacks is listed (the view then logs and
    assigns no sprite)."""
    env = UnityPy.load(package_bytes)
    by_name = {}
    for obj in env.objects:
        if obj.type.name != "Sprite":
            continue
        tree = obj.read_typetree()
        if tree["m_Name"] in by_name:
            raise ValueError(f"{SITEMAP_TEXTURE_PACKAGE} names Sprite {tree['m_Name']} twice")
        by_name[tree["m_Name"]] = (obj, tree)
    wanted = [SITE_MAP_PHENOMENA_BACKGROUND] + [f"icon_{name}" for name in icon_names]
    if SITE_MAP_PHENOMENA_BACKGROUND not in by_name:
        raise ValueError(f"{SITEMAP_TEXTURE_PACKAGE} holds no {SITE_MAP_PHENOMENA_BACKGROUND} Sprite")
    textures, sprites, missing = {}, {}, []
    image_dir.mkdir(parents=True, exist_ok=True)
    for name in dict.fromkeys(wanted):
        if name not in by_name:
            missing.append(name)
            continue
        obj, tree = by_name[name]
        rect, rd = tree["m_Rect"], tree["m_RD"]
        relative = f"textures/site-map-phenomena-{name}.png"
        obj.read().image.save(out / relative)
        alias = SITE_MAP_PHENOMENA_ALIAS + name
        textures[alias] = relative
        sprites[alias] = {
            "name": name, "image": relative, "package": SITEMAP_TEXTURE_PACKAGE,
            "pathId": obj.path_id,
            "rect": [rect["x"], rect["y"], rect["width"], rect["height"]],
            "rectSize": [rect["width"], rect["height"]],
            "border": [tree["m_Border"][k] for k in ("x", "y", "z", "w")],
            "pixelsPerUnit": tree["m_PixelsToUnits"],
            "pivot": [tree["m_Pivot"]["x"], tree["m_Pivot"]["y"]],
            "textureRect": [rd["textureRect"][k] for k in ("x", "y", "width", "height")],
            "textureRectOffset": [rd["textureRectOffset"]["x"], rd["textureRectOffset"]["y"]],
            "packingSettingsRaw": rd["settingsRaw"],
            "downscaleMultiplier": rd.get("downscaleMultiplier", 1.0),
        }
    return textures, sprites, missing


def extract_layout_jp(apks, out_dir, *, client_version, master_dir, prefabs,
                      bundles_root=None, font_bundles_root=None) -> dict:
    """Write layout documents for *prefabs* from APK player data.

    *apks* are the base APK and the install-time asset pack; *client_version*
    selects the decoder chains (only the versions ``ui.jp_decoders`` names are
    accepted).  Each prefab is found through the Resources index and read from
    its own serialized file with that file's externals closure loaded.  Next
    to the documents the run writes the screen-layer data of every selected
    screen layer, the merged wording dictionary (built-in CSV + master
    ``wordings`` from *master_dir*), the TMP settings, the host canvas and a
    manifest with one row per written document.  A hand decode with a
    residual or an over-read anywhere in a prefab's file fails that prefab
    and is reported per class; nothing is written for it.  When the menu
    dialog is selected, its button textures are read from the decrypted menu
    package under *bundles_root*, which is then required.  The font document
    needs the decrypted ``font/common`` package, looked up under
    *font_bundles_root* (else *bundles_root*); it is always required, because
    the base font assets every text names carry only the space character and
    take their glyphs from that package's assets at runtime.
    """
    from ui import jp_decoders
    from ui.player_source import (ApkPlayerData, container_target, resource_container,
                                  unity_version)
    from ui import wording as wording_rules
    from ui import ui_settings
    from core.master import Master

    if client_version != jp_decoders.VERSION:
        raise ValueError(f"no decoder chains registered for JP {client_version}")
    selected = list(dict.fromkeys(prefabs or ()))
    family_of = {name: family for family, names in {**SCREENS, **REFERENCED_FAMILIES}.items()
                 for name in names}
    unknown = [name for name in selected if name not in family_of]
    if not selected or unknown:
        raise ValueError(f"invalid prefab selection: {unknown or 'empty'}")
    master_path = Path(master_dir)
    if not master_path.is_dir():
        raise FileNotFoundError(f"master directory not found: {master_dir}")
    from core.fetch import flatten
    from ui import tmp_font
    font_root = font_bundles_root or bundles_root
    if font_root is None:
        raise ValueError(f"the font document needs the download package {tmp_font.FONT_PACKAGE}: "
                         "supply its decrypted package directory (jp download --root "
                         f"{tmp_font.FONT_PACKAGE})")
    font_package = Path(font_root) / flatten(tmp_font.FONT_PACKAGE)
    if not font_package.is_file():
        raise FileNotFoundError(f"font package missing: {font_package.name} "
                                f"(jp download --root {tmp_font.FONT_PACKAGE})")
    menu_package = None
    if "MysekaiMenuDialog" in selected:
        if bundles_root is None:
            raise ValueError("MysekaiMenuDialog's button textures are in the download package "
                             f"{MENU_TEXTURE_PACKAGE}: supply the decrypted package directory")
        menu_package = Path(bundles_root) / MENU_TEXTURE_PACKAGE
        if not menu_package.is_file():
            raise FileNotFoundError(f"UI texture package missing: {menu_package.name}")
    sitemap_package = None
    if "ScreenLayerMysekaiNotice" in selected:
        if bundles_root is None:
            raise ValueError("ScreenLayerMysekaiNotice's weather banner sprites are in the download package "
                             f"{SITEMAP_TEXTURE_PACKAGE}: supply the decrypted package directory")
        sitemap_package = Path(bundles_root) / SITEMAP_TEXTURE_PACKAGE
        if not sitemap_package.is_file():
            raise FileNotFoundError(f"UI texture package missing: {sitemap_package.name}")
    bgm_player_package = None
    if "ScreenLayerMysekaiBGMSelect" in selected:
        if bundles_root is None:
            raise ValueError("ScreenLayerMysekaiBGMSelect's player images are in the download package "
                             f"{BGM_SELECT_PLAYER_PACKAGE}: supply the decrypted package directory")
        bgm_player_package = Path(bundles_root) / BGM_SELECT_PLAYER_PACKAGE
        if not bgm_player_package.is_file():
            raise FileNotFoundError(f"UI texture package missing: {bgm_player_package.name}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    store = ApkPlayerData(apks)
    player = store.environment()
    player.add(["globalgamemanagers"], follow=False)
    env = player.env
    manager, container = resource_container(env)

    def resource(key):
        entries = container.get(key)
        if not entries:
            raise ValueError(f"Resources index has no entry {key!r}")
        targets = sorted({container_target(env, manager, entry) for entry in entries})
        if len(targets) != 1:
            raise ValueError(f"Resources entry {key!r} has {len(targets)} targets")
        return entries[0], targets[0]

    def resource_key(prefab):
        key_roots = RESOURCE_ROOTS + REFERENCED_FAMILY_ROOTS.get(family_of[prefab], ())
        keys = [key for key in container if key.startswith(key_roots)
                and key.rsplit("/", 1)[-1] == prefab.lower()]
        if prefab in REFERENCED_PREFAB_KEYS:
            keys = [key for key in container if key == REFERENCED_PREFAB_KEYS[prefab]]
        if not keys and family_of[prefab] == "menu":
            # A cell a menu dialog instantiates from a serialized prefab field
            # (CommonRewardSubWindowDialog's thumbnail with balloon) is indexed
            # under the referenced-prefab roots, not under Dialog/.
            keys = [key for key in container if key.startswith(REFERENCED_PREFAB_ROOTS)
                    and key.rsplit("/", 1)[-1] == prefab.lower()]
        if len(keys) != 1:
            raise ValueError(f"{prefab}: {len(keys)} Resources keys match")
        return keys[0]

    plan = []
    for prefab in selected:
        if prefab in POINTER_PREFABS:
            # No Resources key names it: its holder's key does, and the holder's
            # reference is followed once the files are loaded (below).
            holder = POINTER_PREFABS[prefab][0]
            holder_key = resource_key(holder)
            holder_entry, holder_target = resource(holder_key)
            plan.append({"prefab": prefab, "family": family_of[prefab], "key": None,
                         "entry": None, "target": None, "via": POINTER_PREFABS[prefab],
                         "holder": {"prefab": holder, "resourceKey": holder_key,
                                    "resourceEntry": {"fileId": holder_entry[0],
                                                      "pathId": holder_entry[1]},
                                    "target": holder_target}})
            continue
        key = resource_key(prefab)
        entry, target = resource(key)
        plan.append({"prefab": prefab, "family": family_of[prefab], "key": key,
                     "entry": entry, "target": target})

    tmp_keys = [key for key in container if key.casefold() == TMP_SETTINGS_RESOURCE]
    roots = [(item["target"] if item["target"] is not None else item["holder"]["target"])[0]
             for item in plan]
    # The colour palette and the tween defaults the runtime applies at boot.
    roots += [resource(key)[1][0] for key in (ui_settings.PALETTE_RESOURCE,
                                              ui_settings.TWEEN_SETTINGS_RESOURCE)]
    roots += [resource(key)[1][0] for key in (ENTRY_SCREEN_LAYERS, WORDING_RESOURCE)]
    # TMP Settings may be indexed more than once; extract_text_settings
    # requires every copy to agree, so every copy is loaded.
    roots += [container_target(env, manager, entry)[0]
              for key in tmp_keys for entry in container[key]]
    for item in plan:
        if item["key"] is not None and item["key"].startswith("screen/prefabs/"):
            data_key = SCREEN_DATA_ROOT + item["prefab"].lower()
            if data_key in container:
                roots.append(resource(data_key)[1][0])
    # The Resources font assets FontAssetManager loads, with every file they
    # reference (MonoScripts, atlas textures, the dynamic assets' font files).
    for weight in tmp_font.WEIGHTS:
        for load_path in tmp_font.resources_names(weight).values():
            entries = container.get(load_path.casefold())
            if not entries:
                raise ValueError(f"Resources index has no entry {load_path!r}")
            roots += sorted({container_target(env, manager, entry)[0] for entry in entries})
    # Bring globalgamemanagers' own table forward only through the requested
    # roots; its full externals closure is the whole Resources set.
    player.add(roots)
    build = [obj for obj in env.files["globalgamemanagers"].objects.values()
             if obj.type.name == "BuildSettings"]
    scenes = build[0].read_typetree()["scenes"] if len(build) == 1 else []
    scene_index = [i for i, path in enumerate(scenes)
                   if path.replace("\\", "/").rsplit("/", 1)[-1] == "Mysekai.unity"]
    if len(scene_index) == 1:
        # The host canvas reads objects of the scene file only.
        player.add([f"level{scene_index[0]}"], follow=False)
    if player.missing:
        raise FileNotFoundError(f"externals no APK member supplies: {player.missing}")

    mono_index = talk.build_monoscript_index(env)
    decoders = {**talk._DECODERS, **EXTRA_DECODERS, **jp_decoders.DECODERS, **ui_settings.DECODERS}
    for item in plan:
        if item.get("via") is None:
            continue
        holder_file, holder_go = item["holder"]["target"]
        holder_objects = _file_objects(env, holder_file)
        holder_roots = find_prefab_root(holder_objects, item["holder"]["prefab"])
        if len(holder_roots) != 1 or holder_roots[0][0] != holder_go:
            raise ValueError(f"{item['prefab']}: the holder's Resources target is not its "
                             f"file's unique root of that name: {holder_roots}")
        reference, (go_pid, transform_pid, _) = follow_prefab_reference(
            env, holder_objects, holder_roots[0][1], item["via"][1], item["via"][2],
            Resolver({}, mono_index, None, env=env, decoders=decoders),
            lambda name: _file_objects(env, name))
        item["target"] = (reference["targetSerializedFile"], go_pid)
        item["root"] = (go_pid, transform_pid)
        holder = item["holder"]
        item["reference"] = {"holderPrefab": holder["prefab"], "holderResourceKey": holder["resourceKey"],
                             "holderResourceEntry": holder["resourceEntry"],
                             "holderRootGameObjectPathId": holder_go, **reference}
    unity = unity_version(env)
    identity = {"region": jp_decoders.REGION, "clientVersion": client_version,
                "unityVersion": unity}

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    rows = []
    text_settings = extract_text_settings(env, mono_index, decoders)
    text_path = write_json(out / "text-settings.json", text_settings)
    rows.append({"document": "text-settings.json", "kind": "tmp-settings", **identity,
                 "sources": [{"guid": row["serializedFile"], "pathId": row["pathId"],
                              "residual": row["residual"],
                              "sourceSha256": store.sha256(player.member(row["serializedFile"]))}
                             for row in text_settings["source"]["settings"]],
                 "documentSha256": digest(text_path)})
    host_canvas = extract_host_canvas(env, mono_index, decoders)
    host_path = write_json(out / "host-canvas.json", host_canvas)
    rows.append({"document": "host-canvas.json", "kind": "host-canvas", **identity,
                 "guid": host_canvas["source"]["serializedFile"],
                 "sourceSha256": store.sha256(player.member(host_canvas["source"]["serializedFile"])),
                 "scalerResidual": host_canvas["source"]["scalerResidual"],
                 "documentSha256": digest(host_path)})
    for key, cls, build, document, kind in (
            (ui_settings.PALETTE_RESOURCE, ui_settings.PALETTE_CLASS,
             ui_settings.palette_document, "palette.json", "palette"),
            (ui_settings.TWEEN_SETTINGS_RESOURCE, ui_settings.TWEEN_SETTINGS_CLASS,
             ui_settings.tween_settings_document, "tween-settings.json", "tween-settings")):
        _, (s_file, s_pid) = resource(key)
        record = _strict_fields(env, env.files[s_file].objects[s_pid], mono_index, cls, decoders)
        s_sha = store.sha256(player.member(s_file))
        settings_path = write_json(out / document, build(record, {
            "resourceKey": key, "serializedFile": s_file, "pathId": s_pid, "class": cls,
            "rawLength": record["raw_len"], "residual": record["residual"], "sourceSha256": s_sha}))
        rows.append({"document": document, "kind": kind, **identity, "guid": s_file,
                     "pathId": s_pid, "residual": record["residual"], "sourceSha256": s_sha,
                     "documentSha256": digest(settings_path)})

    # Screen-layer registry: every entry decoded, keyed by its asset name.
    _, (list_file, list_pid) = resource(ENTRY_SCREEN_LAYERS)
    list_obj = env.files[list_file].objects[list_pid]
    list_record = _strict_fields(env, list_obj, mono_index, "Sekai.ScreenLayerDataList", decoders)
    registry_resolver = Resolver({}, mono_index, None, env=env, decoders=decoders)
    registry = {}
    for index, pointer in enumerate(list_record["fields"]["Entries"]):
        registry_resolver.holder(list_obj)
        obj, state, file_name = registry_resolver.locate(pointer)
        if state is not None:
            raise ValueError(f"screen layer registry entry {index} is {state}")
        record = _strict_fields(env, obj, mono_index, "Sekai.ScreenLayerData", decoders)
        name = record["meta"]["m_Name"]
        if name in registry:
            raise ValueError(f"screen layer registry names {name} twice")
        registry[name] = {"index": index, "object": obj, "record": record}

    # The runtime wording dictionary.
    _, (w_file, w_pid) = resource(WORDING_RESOURCE)
    w_obj = env.files[w_file].objects[w_pid]
    if w_obj.type.name != "TextAsset":
        raise ValueError(f"{WORDING_RESOURCE} is a {w_obj.type.name}, not a TextAsset")
    asset_name, data = _text_asset_bytes(w_obj)
    builtin, rejected = wording_rules.parse_builtin_csv(wording_rules.text_asset_text(data))
    master_rows = Master(str(master_path)).table("wordings")
    master_identity = None
    marker = master_path / "master-source.json"
    if marker.is_file():
        import json
        master_identity = json.loads(marker.read_text(encoding="utf-8")).get("identity")
    wording_doc = wording_rules.wording_document(builtin, rejected, master_rows, {
        **identity,
        "builtin": {"resourceKey": WORDING_RESOURCE, "serializedFile": w_file, "pathId": w_pid,
                    "name": asset_name, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "fileSha256": store.sha256(player.member(w_file))},
        "master": {"table": "wordings", "rows": len(master_rows), "identity": master_identity,
                   "sha256": hashlib.sha256((master_path / "wordings.json").read_bytes()).hexdigest()},
    })

    written, failures = [], []
    text_fonts = {}
    image_dir = out / "textures"
    runtime_textures, runtime_sprites = {}, {}
    texture_sources = []
    if menu_package is not None:
        package_bytes = menu_package.read_bytes()
        texture_env = UnityPy.load(package_bytes)
        exported = 0
        image_dir.mkdir(parents=True, exist_ok=True)
        for obj in texture_env.objects:
            if obj.type.name != "Texture2D":
                continue
            texture = obj.read()
            relative = f"textures/menu-{obj.path_id}.png"
            if texture.m_Name in runtime_textures:
                raise ValueError(f"{MENU_TEXTURE_PACKAGE} names texture {texture.m_Name} twice")
            texture.image.save(out / relative)
            runtime_textures[texture.m_Name] = relative
            exported += 1
        if not exported:
            raise ValueError(f"{MENU_TEXTURE_PACKAGE} holds no Texture2D")
        texture_sources.append({"package": MENU_TEXTURE_PACKAGE, "bytes": len(package_bytes),
                                "sha256": hashlib.sha256(package_bytes).hexdigest(),
                                "textures": exported})
    if sitemap_package is not None:
        package_bytes = sitemap_package.read_bytes()
        icon_rows = sorted(Master(str(master_path)).table("mysekaiPhenomenas"), key=lambda row: row["id"])
        icons = [row["iconAssetbundleName"] for row in icon_rows]
        textures, sprites, missing = _site_map_phenomena_sprites(package_bytes, icons, out, image_dir)
        for alias in textures:
            if alias in runtime_textures:
                raise ValueError(f"UI runtime texture alias collision: {alias}")
        runtime_textures.update(textures)
        runtime_sprites.update(sprites)
        texture_sources.append({"package": SITEMAP_TEXTURE_PACKAGE, "bytes": len(package_bytes),
                                "sha256": hashlib.sha256(package_bytes).hexdigest(),
                                "sprites": len(sprites), "masterIconRows": len(icons),
                                "missingSprites": missing})
    if bgm_player_package is not None:
        textures, sprites, source = bgm_select_player_textures(
            bgm_player_package.read_bytes(), out, image_dir)
        for alias in textures:
            if alias in runtime_textures:
                raise ValueError(f"UI runtime texture alias collision: {alias}")
        runtime_textures.update(textures)
        runtime_sprites.update(sprites)
        texture_sources.append(source)
    for item in plan:
        prefab, family = item["prefab"], item["family"]
        file_name, root_pid = item["target"]
        objects = _file_objects(env, file_name)
        accounting = _mono_accounting(env, objects, mono_index, decoders)
        counts = accounting["counts"]
        if counts["handDecodedResidual"] or counts["overRead"]:
            failures.append({"prefab": prefab, "family": family,
                             "reason": "hand decodes do not consume the file exactly",
                             "accounting": accounting})
            continue
        root = objects.get(root_pid)
        if root is None or root.type.name != "GameObject":
            raise ValueError(f"{prefab}: Resources target is not a GameObject")
        if item.get("via") is not None:
            # Reached through a reference: the root follow_prefab_reference proved.
            name = root.read_typetree().get("m_Name")
            if name != prefab:
                raise ValueError(f"{prefab}: {item['via'][1]}.{item['via'][2]} names {name!r}")
            go_pid, transform_pid = item["root"]
        else:
            roots_found = find_prefab_root(objects, prefab)
            if len(roots_found) != 1 or roots_found[0][0] != root_pid:
                raise ValueError(f"{prefab}: Resources target is not the file's unique "
                                 f"root of that name: {roots_found}")
            go_pid, transform_pid = roots_found[0]
        resolver = Resolver({}, mono_index, image_dir, env=env, decoders=decoders)
        source = {
            **identity,
            "serializedFile": file_name,
            "resourceKey": item["key"],
            **({"resourceEntry": {"fileId": item["entry"][0], "pathId": item["entry"][1]}}
               if item["entry"] is not None else {"reference": item["reference"]}),
            "rootGameObjectPathId": go_pid,
            "rootTransformPathId": transform_pid,
            "loading": pointer_loading(prefab) if item.get("via") is not None else LOADING[family],
            "sha256": store.sha256(player.member(file_name)),
            "members": store.members_of(player.member(file_name)),
        }
        document = extract_prefab(env, objects, go_pid, transform_pid, resolver,
                                  prefab, family, source=source)
        document["summary"]["pointerTargets"] = dict(sorted(resolver.pointer_census.items()))
        document["summary"]["unresolvedPointers"] = resolver.unresolved_pointers
        for node in document["nodes"]:
            for component in node["components"]:
                font = component.get("font")
                if font is not None:
                    if font.get("state") != "ok":
                        raise ValueError(f"{prefab}: text component {component['pathId']} "
                                         f"font is {font.get('state')}")
                    key = (font["serializedFile"], font["pathId"])
                    text_fonts[key] = text_fonts.get(key, 0) + 1
                for alias, sprite in component.get("runtimeSprites", {}).items():
                    if alias in runtime_textures and runtime_textures[alias] != sprite["image"]:
                        raise ValueError(f"UI runtime texture alias collision: {alias}")
                    runtime_textures[alias] = sprite["image"]
                    runtime_sprites[alias] = sprite
        path = write_json(out / family / f"{prefab}.json", document)
        monos, decoded, raw = _document_counts(document)
        closure = _closure_of(env, file_name)
        row = {
            "document": f"{family}/{prefab}.json", "kind": "prefab", **identity,
            "prefab": prefab, "family": family, "resourceKey": item["key"],
            **({"fileId": item["entry"][0], "pathId": item["entry"][1]}
               if item["entry"] is not None else {"reference": item["reference"]}),
            "guid": file_name, "sourceSha256": source["sha256"],
            "documentSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "nodes": document["summary"]["nodes"],
            "monoBehaviours": {"inFile": accounting["monoBehaviours"], "inDocument": monos},
            "decodedByClass": decoded, "rawByClass": raw,
            "fileAccounting": accounting,
            "externalsClosure": {"files": len(closure),
                                 "missing": sorted(n for n in closure if n not in env.files)},
            "pointerTargets": document["summary"]["pointerTargets"],
            "unresolvedPointers": len(resolver.unresolved_pointers),
            "wordingKeys": _wording_key_counts(document, wording_doc["entries"]),
        }
        # The screen-layer data that routes a MenuScreenType to this prefab.
        if item["key"] is not None and item["key"].startswith("screen/prefabs/"):
            entry = registry.get(prefab)
            if entry is None:
                raise ValueError(f"{prefab}: no screen layer registry entry names it")
            data_obj, record = entry["object"], entry["record"]
            data_key = SCREEN_DATA_ROOT + prefab.lower()
            cross = None
            if data_key in container:
                _, target = resource(data_key)
                cross = {"resourceKey": data_key, "serializedFile": target[0], "pathId": target[1],
                         "sameObject": target == (data_obj.assets_file.name, data_obj.path_id)}
                if not cross["sameObject"]:
                    raise ValueError(f"{prefab}: registry and {data_key} disagree")
            registry_resolver.holder(data_obj)
            fields = record["fields"]
            screen_doc = {
                "version": 1, **identity,
                "screenLayerData": record["meta"]["m_Name"],
                "screenType": fields["ScreenType"],
                "fields": _plain(fields),
                "masks": {name: registry_resolver.texture(fields[name])
                          for name in ("MaskInTexture", "MaskOutTexture")},
                "prefabLink": {
                    "loadPath": SCREEN_PREFAB_LOAD_ROOT + record["meta"]["m_Name"],
                    "resourceKey": item["key"], "serializedFile": file_name,
                    "pathId": root_pid, "document": f"{family}/{prefab}.json",
                },
                "source": {
                    "serializedFile": data_obj.assets_file.name, "pathId": data_obj.path_id,
                    "sha256": store.sha256(player.member(data_obj.assets_file.name)),
                    "rawLength": record["raw_len"], "residual": record["residual"],
                    "registry": {"resourceKey": ENTRY_SCREEN_LAYERS, "serializedFile": list_file,
                                 "pathId": list_pid, "entryIndex": entry["index"],
                                 "entries": len(registry)},
                    "resourceIndex": cross,
                },
            }
            screen_path = write_json(out / "screen-data" / f"{prefab}.json", screen_doc)
            rows.append({"document": f"screen-data/{prefab}.json", "kind": "screen-layer-data",
                         **identity, "screenType": fields["ScreenType"],
                         "guid": data_obj.assets_file.name, "pathId": data_obj.path_id,
                         "sourceSha256": screen_doc["source"]["sha256"],
                         "documentSha256": hashlib.sha256(screen_path.read_bytes()).hexdigest(),
                         "residual": record["residual"]})
            row["screenLayerData"] = f"screen-data/{prefab}.json"
        rows.append(row)
        written.append({"prefab": prefab, "family": family, "path": str(path),
                        "summary": document["summary"]})

    font_doc = extract_font_assets(env, mono_index, store, player, container, manager,
                                   text_fonts, font_package, decoders)
    download_marker = font_package.parent.parent / "cache-source.json"
    font_doc = {"version": font_doc.pop("version"), **identity, **font_doc}
    if download_marker.is_file():
        import json
        font_doc["source"]["package"]["downloadIdentity"] = json.loads(
            download_marker.read_text(encoding="utf-8")).get("identity")
    font_path = write_json(out / "fonts" / "tmp-font-assets.json", font_doc)
    font_records = font_doc["fontAssets"].values()
    rows.append({"document": "fonts/tmp-font-assets.json", "kind": "tmp-font-assets", **identity,
                 "fontAssets": len(font_doc["fontAssets"]),
                 "byOrigin": {origin: sum(1 for r in font_records if r["origin"] == origin)
                              for origin in ("resources", "package")},
                 "package": font_doc["source"]["package"],
                 "sources": [{"guid": r["source"]["serializedFile"], "pathId": r["source"]["pathId"],
                              "sourceSha256": r["source"]["sourceSha256"]}
                             for r in font_records if r["origin"] == "resources"],
                 "textComponents": sum(text_fonts.values()),
                 "documentSha256": digest(font_path)})

    # The runtime texture inventory of the written documents (a consumer root
    # reads it unconditionally; it is empty when no written document names a
    # runtime-assigned sprite).
    textures_path = write_json(out / "textures.json", runtime_textures)
    rows.append({"document": "textures.json", "kind": "runtime-textures", **identity,
                 "entries": len(runtime_textures), "packages": texture_sources,
                 "documentSha256": digest(textures_path)})
    if runtime_sprites:
        sprites_path = write_json(out / "runtime-sprites.json", runtime_sprites)
        rows.append({"document": "runtime-sprites.json", "kind": "runtime-sprites", **identity,
                     "entries": len(runtime_sprites), "documentSha256": digest(sprites_path)})

    wording_path = write_json(out / "wordings.json", wording_doc)
    rows.append({"document": "wordings.json", "kind": "wordings", **identity,
                 "guid": w_file, "pathId": w_pid, "sourceSha256": wording_doc["source"]["builtin"]["fileSha256"],
                 "documentSha256": hashlib.sha256(wording_path.read_bytes()).hexdigest(),
                 **wording_doc["summary"]})

    manifest = {
        "version": 1, **identity,
        "inputs": {"apks": [{"name": Path(p).name, "bytes": Path(p).stat().st_size} for p in apks],
                   "resourceIndexKeys": len(container),
                   "resourceIndexEntries": sum(len(v) for v in container.values()),
                   "globalgamemanagersExternals": len(manager.assets_file.externals),
                   "loadedFiles": len(player.loaded), "shallowFiles": list(player.shallow),
                   "missingExternals": list(player.missing)},
        "decoderChains": {"region": jp_decoders.REGION, "version": jp_decoders.VERSION,
                          "overridden": sorted(jp_decoders.DECODERS)},
        "documents": rows,
        "failures": failures,
    }
    manifest_path = write_json(out / "ui-manifest.json", manifest)
    return {"written": written, "failures": failures, "manifestPath": str(manifest_path),
            "manifest": manifest}
