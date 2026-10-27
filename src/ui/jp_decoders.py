"""Serialized field chains of the JP clients, one table per supported version.

The decoders in ``ui.talk``, ``ui.editor_layout`` and ``ui.prefab_layout`` were
derived from the CN 6.0.0 declarations and consume those bytes exactly.  The
JP 6.8.1 client changed the declarations below, and every change
shows as a uniform residual or an over-read on the old chain, so the version
is part of the decoder identity, not something a byte count may pick:

* ``Sekai.UI.CustomButton`` appends ``enableHoldRepeat`` (bool) and
  ``holdRepeatInterval`` (float) after its runtime-only hold state; both are
  ``[SerializeField]`` and follow ``pressScale`` on the serialized chain.
* ``Sekai.UI.CustomTextMesh`` appends ``enableWidthScaleFit`` (bool) after
  ``maxValueUpToAutoSize``.
* ``Sekai.UI.CustomScrollbar`` declares two public
  ``UnityEvent<PointerEventData>`` fields, ``OnPointerDownEvent`` then
  ``OnPointerUpEvent``; generic UnityEvents are serialized as their
  persistent-call list after the Scrollbar chain.
* ``Sekai.UI.UIPartsCommonButton`` ends at ``tapEffect``: the trailing tip
  reference of the older chain is no longer declared.
* ``Sekai.UI.CustomIndexToggleGroup`` holds only ``indexToggles`` and
  ``selectedOnAwake`` after ToggleGroup's ``m_AllowSwitchOff``; the two
  caption references of the older chain are no longer declared.
* ``Sekai.Mysekai.MysekaiMenuUIContent`` declares ``_eventBreakTimeController``
  between ``_screenShotButtonOffPosition`` and ``_backButton``.

* ``Sekai.UI.CustomDropdown`` declares ``enableItemClickEvent`` then
  ``centerScrollOnOpen`` (both bool) between ``showsAllItems`` and ``se``.
* ``Sekai.UI.UIPartsLeftTabList`` and ``Sekai.UI.UIPartsLeftTabSubList``
  inherit their references from ``UIPartsLeftTabListBase``, which declares a
  ``scrollRect`` reference after ``bottomScrollableIcon``; the tab list then
  adds its own ``backPanelTransform``.
* ``Sekai.ScreenLayer`` serializes only ``layerCamera``: its ``isBootDone``
  flag is runtime state, not a serialized field, so the screen layers that
  derive from it (``ScreenLayerMysekaiInfo``, ``ScreenLayerSiteEditMode``,
  ``ScreenLayerMysekaiNotice``, ``ScreenLayerMysekaiInventory``) start their
  own fields right after the camera reference.
* ``Sekai.Mysekai.MysekaiInfoOption1Page`` ends at the conversion
  notification toggle group: no review-state tip is declared.
* ``Sekai.Mysekai.SiteEdit.SiteEditView`` holds nine references; no report
  tip button is declared between the rotate button and the UI-disable button.
* ``Sekai.UI.CustomText`` (a UGUI ``Text``) adds only ``useWordingKey`` and
  ``wordingKey``; no font-type field is declared.

* ``Sekai.Mysekai.DeliveryActionButton`` derives from ``CustomButton``, so it
  reads the JP CustomButton chain above before its own ``_iconLoader``.

Classes the JP client serializes that have no older chain at all:

* ``Sekai.OptionLiveSetting`` (twenty references), ``Sekai.OptionNoteSetting``
  (seventeen), ``Sekai.OptionSystemSetting`` (eleven) and
  ``Sekai.OptionCommunicationSetting`` (four) are the option dialog's pages:
  each page's setup reads its numeric selectors, toggle groups and buttons
  through these references, never by path.  ``Sekai.UIPartsNumericSelector``
  serializes its number text, then the decrement buttons and their texts and
  the increment buttons and their texts as four arrays; a selector's
  variation index is the array index.
* ``Sekai.Mysekai.MysekaiInfoRankPage`` serializes the film image, the tape
  images, the photo button, the photo frame preview, the rank text, the rank
  gauge, then the room and the home put-limit cost texts; its setup writes
  the two costs through the last two references.

* ``Sekai.Mysekai.UIPartsMysekaiRankGauge`` serializes ``_rankText`` and
  ``_gaugeExp``; ``Sekai.UIPartsGaugeExp`` serializes ``gauge``, ``restText``
  and ``restTextMesh``.  The rank gauge's setup writes the rank through the
  first reference and the remaining experience through the second one's rest
  text, so the product binds those texts through these references.
  ``Sekai.UIPartsGauge`` serializes ``fillImage`` then ``gaugeImage``; the
  experience gauge's setup writes the fill amount of the first one.

* ``Sekai.Mysekai.ScreenLayerMysekaiHUD``, its two attention controllers, the
  object and fixture attention displays and the three notice cells
  (``MySekaiMissionNoticeCell``, ``MySekaiCommonNoticeCell``,
  ``HarvestCollectItemNoticeCell``) serialize only references; their chains
  are in ``ui.field_hud_layout``.  The layer and the controllers name the
  roots and prefabs its displays are instantiated under and from, and the
  notice screen fills its cells through the cells' references, so the product
  binds them through these references, never by path.

* ``Sekai.EventBreakTimeController`` serializes exactly one field, the
  ``_gauge`` reference to its ``UIPartsEventBreakTimeGauge``; its model,
  flags, timer and display mode are runtime-only.  The menu content hides or
  initializes the gauge through this reference, so the reference is data the
  product needs, not decoration.

One class keeps the older chain on purpose:

* ``Sekai.UIPartsLoadingCircle`` declares exactly ``circleTweens``,
  ``circleImages``, ``colorType`` and ``canvasGroup`` in 6.8.1; its
  ``UpdateView`` is a method, not a serialized flag.  A later CN client
  declares a serialized ``updateView`` bool after ``canvasGroup``; the JP
  6.8.1 declarations do not, so this chain must not read one.

Field order is the declaration order of the 6.8.1 managed types, read from
two independent reconstructions of the same 6.8.1 metadata that agree field
for field; a typetree generated mechanically from the same declarations
reads the same objects to their last byte and agrees value for value.
Base-class chains (Selectable, Button, Scrollbar, ToggleGroup, Text,
TMP_Dropdown, TextMeshProUGUI) are the stock engine ones and are reused
unchanged.

JP 7.0.0 keeps every 6.8.1 chain and changes five classes.  Each change is a
serialized field the 7.0.0 declarations insert; the added bytes show as a
uniform residual on the 6.8.1 chain, and the 6.8.1 objects show an over-read
on the 7.0.0 chain.  Which chain an object takes is decided per object, by the
serialized type hash its file records (``TYPE_LAYOUTS``), because one client's
data mixes both layouts of a class:

* ``Sekai.UI.CustomScrollRect`` adds ``normalizedPositionTolerance`` (float)
  after ``mask``, before the runtime-only ``hasRebuiltLayout`` flag.
* ``Sekai.Mysekai.Inventory.CraftPreview`` adds
  ``_purchaseLimitTextRoot``, ``_noLimitObject``, ``_remainCountObject`` and
  ``_remainCountText`` between ``_craftTermText`` and ``_layoutCostRoot``.
* ``Sekai.Mysekai.MysekaiInfoOption1Page`` adds
  ``_bulkHarvestTargetSettingRoot`` and ``_bulkHarvestTargetSettingButton``
  between ``_voiceDLText`` and the visit-setting toggle group.
* ``Sekai.Mysekai.ContentList.ContentListSelectorCell`` adds
  ``_contentSizeFitter`` after ``_missionIcon``.
* ``Sekai.UIPartsHonorImage`` adds ``wordImage`` after ``coreImage`` and
  ``_medalView`` after ``levelView``.

Field order is the declaration order in the 7.0.0 managed types; each added
field is a reference (12 bytes) except the tolerance, a float (4 bytes).

The mission screen, its dialogs and the sub-window base components are decoded
in ``ui.mission_layout``; the convert-furniture screen and its dialogs in
``ui.convert_layout``.  The sub-window dialogs, the shorten dialog and
``Sekai.UI.ListView`` have one layout in both clients; the layout tables carry
the chain of the layout the probe read to zero residual.  ``Sekai.Mysekai.
UIWaveGaugeAnimation`` stays undecoded: it declares two references but its
objects carry one.
"""
from __future__ import annotations

from ui import talk
from ui import editor_layout
from ui import notice_layout
from ui import harvest_summary_layout
from ui import inventory_layout
from ui import field_hud_layout
from ui import craft_layout
from ui import bgm_select_layout
from ui import mission_layout
from ui import convert_layout
from ui import photo_layout


REGION = "jp"


def decode_custom_button(r: talk.Reader) -> dict:
    """CustomButton: the older chain, then enableHoldRepeat + holdRepeatInterval."""
    d = talk.decode_custombutton(r)
    d["enableHoldRepeat"] = r.bool4()
    d["holdRepeatInterval"] = r.f32()
    return d


def decode_custom_text_mesh(r: talk.Reader) -> dict:
    """CustomTextMesh: the older chain, then enableWidthScaleFit."""
    d = talk.decode_customtextmesh(r)
    d["enableWidthScaleFit"] = r.bool4()
    return d


def decode_custom_scrollbar(r: talk.Reader) -> dict:
    """CustomScrollbar: Scrollbar, then the pointer-down and pointer-up events."""
    d = editor_layout.decode_scrollbar(r)
    d["OnPointerDownEvent"] = talk.decode_unity_event(r)
    d["OnPointerUpEvent"] = talk.decode_unity_event(r)
    return d


def decode_common_button(r: talk.Reader) -> dict:
    """UIPartsCommonButton: twelve serialized fields ending at tapEffect."""
    d = editor_layout.refs(r, ("rectTransform", "baseImage", "coverImage", "customButton"))
    d.update(adjustButtonSetting=r.bool4(), adjustTextSize=r.bool4(), changeSprite=r.bool4(),
             customTextMesh=r.pptr(), buttonType=r.i32(), displayType=r.i32(), size=r.i32(),
             tapEffect=r.pptr())
    return d


def decode_custom_index_toggle_group(r: talk.Reader) -> dict:
    """ToggleGroup.m_AllowSwitchOff, then indexToggles and selectedOnAwake."""
    return {
        "m_AllowSwitchOff": r.bool4(),
        "indexToggles": talk.decode_pptr_list(r),
        "selectedOnAwake": r.bool4(),
    }


MENU_UI_CONTENT_REFERENCES = (
    "_siteMapButton", "_leaveMysekaiButton", "_homeAreaInfo",
    "_siteNameIconImage", "_screenShotButton", "_screenShotButtonCanvasGroup",
    "_screenShotUx", "_cameraResetButton", "_menuButton", "_uiDisableButton",
    "_uiDisableButtonCanvasGroup", "_sekaiMissionHomePanel",
    "_uiDisableButtonOnPosition", "_uiDisableButtonOffPosition",
    "_screenShotButtonOnPosition", "_screenShotButtonOffPosition",
    "_eventBreakTimeController", "_backButton",
)


def decode_mysekai_menu_ui_content(r: talk.Reader) -> dict:
    """MysekaiMenuUIContent: eighteen serialized references, runtime state excluded."""
    return {name: r.pptr() for name in MENU_UI_CONTENT_REFERENCES}


def decode_event_break_time_controller(r: talk.Reader) -> dict:
    """EventBreakTimeController: the single serialized ``_gauge`` reference."""
    return {"_gauge": r.pptr()}


def decode_custom_dropdown(r: talk.Reader) -> dict:
    """CustomDropdown: TMP_Dropdown, then the dropdown's own seven fields."""
    d = talk.decode_selectable_base(r)
    d.update(editor_layout.refs(r, ("m_Template", "m_CaptionText", "m_CaptionImage",
                                    "m_Placeholder", "m_ItemText", "m_ItemImage")))
    d["m_Value"] = r.i32()
    d["m_Options"] = [{"m_Text": r.string(), "m_Image": r.pptr()} for _ in range(r.i32())]
    d.update(m_OnValueChanged=talk.decode_unity_event(r), m_AlphaFadeSpeed=r.f32(),
             baseObject=r.pptr(), arrowObject=r.pptr(), showsAllItems=r.bool4(),
             enableItemClickEvent=r.bool4(), centerScrollOnOpen=r.bool4(), se=r.i32())
    return d


LEFT_TAB_LIST_BASE = ("scroller", "topScrollableIcon", "bottomScrollableIcon", "scrollRect")


def decode_left_tab_list(r: talk.Reader) -> dict:
    """UIPartsLeftTabList: the list base references, then backPanelTransform."""
    return editor_layout.refs(r, LEFT_TAB_LIST_BASE + ("backPanelTransform",))


def decode_left_tab_sub_list(r: talk.Reader) -> dict:
    """UIPartsLeftTabSubList: the list base references only."""
    return editor_layout.refs(r, LEFT_TAB_LIST_BASE)


def decode_mysekai_info(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiInfo: ScreenLayer.layerCamera, then the screen's bindings."""
    d = {"layerCamera": r.pptr()}
    d.update(editor_layout.refs(r, ("_noteImage", "_noteTabGroup")))
    d["_mysekaiInfoPages"] = talk.decode_pptr_list(r)
    d.update(editor_layout.refs(r, ("_particleRoot", "_pageFlickGestureListener",
                                    "_rankInfoButton", "_leftArrowButton", "_rightArrowButton")))
    d["_infoTabs"] = talk.decode_pptr_list(r)
    d.update(editor_layout.refs(r, ("_rankPage", "_option1Page")))
    return d


def decode_mysekai_info_option_page(r: talk.Reader) -> dict:
    """MysekaiInfoOption1Page (6.8.1): six serialized references."""
    return editor_layout.refs(r, (
        "_voiceDLButton", "_voiceDLText", "_mysekaiVisitSettingToggleGroup",
        "_mysekaiImageQualityToggleGroup", "_mysekaiFPSSettingToggleGroup",
        "_mysekaiConvertFixtureNotificationSettingToggleGroup",
    ))


MYSEKAI_INFO_OPTION_PAGE_700 = (
    "_voiceDLButton", "_voiceDLText", "_bulkHarvestTargetSettingRoot",
    "_bulkHarvestTargetSettingButton", "_mysekaiVisitSettingToggleGroup",
    "_mysekaiImageQualityToggleGroup", "_mysekaiFPSSettingToggleGroup",
    "_mysekaiConvertFixtureNotificationSettingToggleGroup",
)


def decode_mysekai_info_option_page_700(r: talk.Reader) -> dict:
    """MysekaiInfoOption1Page (7.0.0): the bulk-harvest target setting row
    (its root object and button) between the voice download pair and the
    visit-setting toggle group."""
    return editor_layout.refs(r, MYSEKAI_INFO_OPTION_PAGE_700)


def decode_site_edit_mode(r: talk.Reader) -> dict:
    """ScreenLayerSiteEditMode: ScreenLayer.layerCamera, then fifteen references."""
    d = {"layerCamera": r.pptr()}
    d.update(editor_layout.refs(r, (
        "_contentListSelector", "_siteEditView", "_fixtureEditHeadUpDisplay",
        "_placedCountHeadUpDisplay", "_sequentialFixtureStartMarker",
        "_sequentialFixtureEndMarker", "_groundEditTargetIcon",
        "_fixtureLayoutCountInfo", "_siteEnvironmentSelectContent",
        "_siteEnvironmentText", "_rightBottomRoot", "_rectangle",
        "_showFixtureToggle", "_switchEditModeToggle", "_longTouchGauge",
    )))
    return d


def decode_screen_layer_mysekai_notice(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiNotice: ScreenLayer.layerCamera, then seventeen references."""
    d = {"layerCamera": r.pptr()}
    d.update(notice_layout.notice_fields(r))
    return d


def decode_screen_layer_mysekai_harvest_summary(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiHarvestSummary: ScreenLayer.layerCamera, then eight references."""
    d = {"layerCamera": r.pptr()}
    d.update(harvest_summary_layout.harvest_summary_fields(r))
    return d


def decode_screen_layer_mysekai_inventory(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiInventory: ScreenLayer.layerCamera, then six references."""
    d = {"layerCamera": r.pptr()}
    d.update(inventory_layout.inventory_fields(r))
    return d


def decode_delivery_action_button(r: talk.Reader) -> dict:
    """DeliveryActionButton: the JP CustomButton chain, then ``_iconLoader``."""
    d = decode_custom_button(r)
    d["_iconLoader"] = r.pptr()
    return d


def decode_site_edit_view(r: talk.Reader) -> dict:
    """SiteEditView: nine serialized references."""
    return editor_layout.refs(r, (
        "_saveButton", "_removeAllButton", "_infoButton", "_presetSaveButton",
        "_changeLookButton", "_rotateButton", "_uiDisableButton", "_uiEnableButton",
        "_uiEnableCanvasGroup",
    ))


def decode_custom_text(r: talk.Reader) -> dict:
    """CustomText: UGUI Text, then useWordingKey and wordingKey."""
    d = talk.decode_text_base(r)
    d["useWordingKey"] = r.bool4()
    d["wordingKey"] = r.string()
    return d


def decode_rank_gauge(r: talk.Reader) -> dict:
    """UIPartsMysekaiRankGauge: the rank text and the experience gauge references."""
    return editor_layout.refs(r, ("_rankText", "_gaugeExp"))


def decode_gauge_exp(r: talk.Reader) -> dict:
    """UIPartsGaugeExp: the gauge, then the rest text as a CustomText or a CustomTextMesh."""
    return editor_layout.refs(r, ("gauge", "restText", "restTextMesh"))


def decode_gauge(r: talk.Reader) -> dict:
    """UIPartsGauge: the image whose fill amount the gauge sets, then the gauge image."""
    return editor_layout.refs(r, ("fillImage", "gaugeImage"))


OPTION_LIVE_SETTING_REFERENCES = (
    "_scrollRect", "_noteSpeed", "_timingAdjust", "_timingAdjustTap", "_brightness",
    "_laneAlpha", "_cutin", "_skillAndPraise", "_noteEffect", "_feverEffect",
    "_simultaneousPushingLine", "_vibrationRoot", "_vibration", "_allPerfectEffect",
    "_fastLateFlick", "_mirrorToggle", "_use120FpsToggle", "_noteSetting",
    "_modeSetting", "_qualitySetting",
)

OPTION_NOTE_SETTING_REFERENCES = (
    "_skinChangeLeft", "_skinChangeRight", "_notesSkinPreview", "_longNoteAlpha",
    "_longNotePreviewBackGround", "_longNotePreview", "_guideAlpha",
    "_guidePreviewBackGround", "_guidePreview", "_tapSeText", "_tapSeChangeLeft",
    "_tapSeChangeRight", "_testTapSeButton", "_testFlickSeButton", "_testLongSeButton",
    "_testTraceSeButton", "_notesPreviewImage",
)

OPTION_SYSTEM_SETTING_REFERENCES = (
    "scrollRect", "defaultMusic", "screenFixed", "showNoteSpeed", "showLoginStatus",
    "voiceBulkDownload", "mvBulkDownload", "musicBulkDownload", "_mvSaveNumSettingButton",
    "_musicSaveNumSettingButton", "notifyLiveBonusMax",
)

OPTION_COMMUNICATION_SETTING_REFERENCES = (
    "scrollRect", "friendRequestToggle", "displayStoryFavoriteToggle", "blockListButton",
)


def decode_option_live_setting(r: talk.Reader) -> dict:
    """OptionLiveSetting: twenty serialized references; the settings object is runtime."""
    return editor_layout.refs(r, OPTION_LIVE_SETTING_REFERENCES)


def decode_option_note_setting(r: talk.Reader) -> dict:
    """OptionNoteSetting: seventeen serialized references; indices and play ids are runtime."""
    return editor_layout.refs(r, OPTION_NOTE_SETTING_REFERENCES)


def decode_option_system_setting(r: talk.Reader) -> dict:
    """OptionSystemSetting: eleven serialized references; lists, model and flags are runtime."""
    return editor_layout.refs(r, OPTION_SYSTEM_SETTING_REFERENCES)


def decode_option_communication_setting(r: talk.Reader) -> dict:
    """OptionCommunicationSetting: four serialized references; the previous status is runtime."""
    return editor_layout.refs(r, OPTION_COMMUNICATION_SETTING_REFERENCES)


def decode_numeric_selector(r: talk.Reader) -> dict:
    """UIPartsNumericSelector: the number text, then four reference arrays."""
    d = {"_numText": r.pptr()}
    for name in ("_decrementButtons", "_decrementButtonTexts",
                 "_incrementButtons", "_incrementButtonTexts"):
        d[name] = talk.decode_pptr_list(r)
    return d


def decode_mysekai_info_rank_page(r: talk.Reader) -> dict:
    """MysekaiInfoRankPage: the film image, the tape images, then six references."""
    d = {"_filmRawImage": r.pptr(), "_tapeImages": talk.decode_pptr_list(r)}
    d.update(editor_layout.refs(r, (
        "_selectedPhotoButton", "_photoFramePreview", "_mysekaiRankText",
        "_mysekaiRankGauge", "_fixtureMyRoomPutLimitCostText", "_fixtureHomePutLimitCostText",
    )))
    return d


DECODERS = {
    "Sekai.UI.CustomButton": decode_custom_button,
    "Sekai.UI.CustomTextMesh": decode_custom_text_mesh,
    "Sekai.UI.CustomScrollbar": decode_custom_scrollbar,
    "Sekai.UI.UIPartsCommonButton": decode_common_button,
    "Sekai.UI.CustomIndexToggleGroup": decode_custom_index_toggle_group,
    "Sekai.Mysekai.MysekaiMenuUIContent": decode_mysekai_menu_ui_content,
    "Sekai.EventBreakTimeController": decode_event_break_time_controller,
    "Sekai.UI.CustomDropdown": decode_custom_dropdown,
    "Sekai.UI.UIPartsLeftTabList": decode_left_tab_list,
    "Sekai.UI.UIPartsLeftTabSubList": decode_left_tab_sub_list,
    "Sekai.Mysekai.ScreenLayerMysekaiInfo": decode_mysekai_info,
    "Sekai.Mysekai.MysekaiInfoOption1Page": decode_mysekai_info_option_page,
    "Sekai.Mysekai.ScreenLayerSiteEditMode": decode_site_edit_mode,
    "Sekai.Mysekai.ScreenLayerMysekaiNotice": decode_screen_layer_mysekai_notice,
    "Sekai.ScreenLayerMysekaiHarvestSummary": decode_screen_layer_mysekai_harvest_summary,
    "Sekai.Mysekai.ScreenLayerMysekaiInventory": decode_screen_layer_mysekai_inventory,
    "Sekai.Mysekai.DeliveryActionButton": decode_delivery_action_button,
    "Sekai.Mysekai.SiteEdit.SiteEditView": decode_site_edit_view,
    "Sekai.UI.CustomText": decode_custom_text,
    "Sekai.Mysekai.UIPartsMysekaiRankGauge": decode_rank_gauge,
    "Sekai.UIPartsGaugeExp": decode_gauge_exp,
    "Sekai.UIPartsGauge": decode_gauge,
    "Sekai.OptionLiveSetting": decode_option_live_setting,
    "Sekai.OptionNoteSetting": decode_option_note_setting,
    "Sekai.OptionSystemSetting": decode_option_system_setting,
    "Sekai.OptionCommunicationSetting": decode_option_communication_setting,
    "Sekai.UIPartsNumericSelector": decode_numeric_selector,
    "Sekai.Mysekai.MysekaiInfoRankPage": decode_mysekai_info_rank_page,
    **field_hud_layout.DECODERS,
    **craft_layout.JP_DECODERS,
    **bgm_select_layout.JP_DECODERS,
    **mission_layout.DECODERS,
    **convert_layout.DECODERS,
}

# ---------------------------------------------------------------------------
# JP 7.0.0: the classes whose serialized layout the 7.0.0 client changed.
# Every other class reads the same bytes in both clients.
# ---------------------------------------------------------------------------

def decode_custom_scroll_rect_700(r: talk.Reader) -> dict:
    """CustomScrollRect : ScrollRect (7.0.0).

    The 7.0.0 declaration adds one field after the inherited ScrollRect chain:
    ``private float normalizedPositionTolerance;`` ([SerializeField], [Min],
    header about the inertia scroll tolerance in px).  ``hasRebuiltLayout`` and
    the drag callbacks after it are runtime state and are not serialized.
    """
    d = editor_layout.decode_custom_scroll_rect(r)
    d["normalizedPositionTolerance"] = r.f32()
    return d


CRAFT_PREVIEW_700 = (
    "_previewContent", "_materialCostList", "_blueprint3DPreview", "_colorSelector",
    "_nameText", "_systemFixtureIcon", "_craftCountInputSelector", "_nameObject",
    "_applyButton", "_cantCraftBalloon", "_screenButton", "_screenCanvasGroup",
    "_quantityObject", "_quantityText", "_sizeObject", "_sizeText", "_reactionCountView",
    "_fixtureBonusView", "_craftTermTextRoot", "_craftTermText",
    "_purchaseLimitTextRoot", "_noLimitObject", "_remainCountObject", "_remainCountText",
    "_layoutCostRoot", "_layoutCostText",
)


def decode_craft_preview_700(r: talk.Reader) -> dict:
    """CraftPreview (7.0.0): the purchase-limit row (its root, the no-limit
    object, the remaining-count object and text) before the layout cost."""
    return editor_layout.refs(r, CRAFT_PREVIEW_700)


def decode_content_list_selector_cell_700(r: talk.Reader) -> dict:
    """ContentListSelectorCell (7.0.0): the selector cell fields, then
    ``_contentSizeFitter``."""
    d = editor_layout.decode_selector_cell(r)
    d["_contentSizeFitter"] = r.pptr()
    return d


HONOR_IMAGE_700 = (
    "coreImage", "wordImage", "frameSprite", "rankImage", "levelView", "_medalView",
    "_uniqueLevelView", "_liveMasterLevel", "_liveMasterLevelMainPosition",
    "_liveMasterLevelSubPosition",
)


def decode_honor_image_700(r: talk.Reader) -> dict:
    """UIPartsHonorImage (7.0.0): the word image after the core image and the
    medal view after the level view."""
    return editor_layout.refs(r, HONOR_IMAGE_700)


def decode_list_view_700(r: talk.Reader) -> dict:
    """Sekai.UI.ListView: the fields it serializes, then the cell a page fills
    after ``listViewItemPrefab`` (both clients' list objects carry it)."""
    d = editor_layout.decode_list_view(r)
    d["filledCell"] = r.pptr()
    return d

# The chains that read the 6.8.1 and the 7.0.0 serialized layouts.  The
# object's own serialized type hash chooses one of the two (see
# :data:`TYPE_LAYOUTS` below), never the client version of the run.
SHARED = dict(DECODERS)
# The 6.8.1 layout of the classes whose 7.0.0 declaration changed: the convert
# family has no click events after its chains, the start and complete dialogs
# end at their two trailing references, and the shorten dialog stops after its
# own references.
ONLY_681 = {
    "Sekai.Mysekai.MysekaiMissionStartDialog":
        mission_layout.decode_mission_animation_dialog_681,
    "Sekai.Mysekai.MysekaiMissionCompleteDialog":
        mission_layout.decode_mission_animation_dialog_681,
    "Sekai.Mysekai.MysekaiConvertFurnitureShortenDialog":
        convert_layout.decode_shorten_dialog_681,
    "Sekai.CrystalShop.ShopConfirmDialogPriceParts": convert_layout.decode_price_parts,
    "Sekai.Mysekai.MysekaiConvertItemDetailDialog": convert_layout.decode_convert_item_detail_dialog,
    "Sekai.Mysekai.MysekaiConvertStartConfirmDialog": convert_layout.decode_convert_start_confirm_dialog,
    "Sekai.Mysekai.MysekaiConvertBlueprintLotterySubWindowDialog": convert_layout.decode_lottery_dialog,
    "Sekai.Mysekai.MysekaiConvertRecordLotterySubWindowDialog": convert_layout.decode_lottery_dialog,
}
DECODERS_681 = {**SHARED, **ONLY_681}
# The 7.0.0 layout: the four dialogs end at their click event, and the five
# classes the 7.0.0 declaration changed read their added fields.


def decode_bulk_checkbox_control(r):
    return {"checkObj": r.pptr(), "check": r.bool4(), "toggle": r.pptr()}


def decode_harvest_player_hud_700(r):
    return editor_layout.refs(r, ("_staminaView", "_bulkHarvestChargeGaugeView", "_canvasGroup"))


def decode_stock_scroll_rect(r):
    fields = {"m_Content": r.pptr(), "m_Horizontal": r.bool4(), "m_Vertical": r.bool4(),
        "m_MovementType": r.i32(), "m_Elasticity": r.f32(), "m_Inertia": r.bool4(),
        "m_DecelerationRate": r.f32(), "m_ScrollSensitivity": r.f32()}
    fields.update(editor_layout.refs(r, ("m_Viewport", "m_HorizontalScrollbar", "m_VerticalScrollbar")))
    fields.update(m_HorizontalScrollbarVisibility=r.i32(), m_VerticalScrollbarVisibility=r.i32(),
        m_HorizontalScrollbarSpacing=r.f32(), m_VerticalScrollbarSpacing=r.f32(),
        m_OnValueChanged=talk.decode_unity_event(r))
    return fields

def decode_bulk_harvest_button(r):
    fields = decode_custom_button(r)
    fields.update(editor_layout.refs(r, ("_cannotButton", "_cannotBalloon", "_iconLoader")))
    return fields


def decode_bulk_harvest_gauge(r):
    return editor_layout.refs(r, ("_gaugeImage", "_canvasGroup", "_maxEffectRoot"))


def decode_bulk_harvest_settings(r):
    from ui.window_animation import decode_common2_button_dialog
    fields = decode_common2_button_dialog(r)
    fields.update(editor_layout.refs(r, ("_siteTabGroup", "_checkBoxListViewPrefab", "_checkBoxListViewParent")))
    return fields


def decode_bulk_harvest_list(r):
    return editor_layout.refs(r, ("_allCheckBox", "_groupViewRoot", "_groupViewBase"))


def decode_bulk_harvest_group(r):
    return editor_layout.refs(r, ("_groupNameText", "_allCheckBox", "_checkBoxRoot", "_checkBoxBase"))


def decode_bulk_harvest_checkbox(r):
    return editor_layout.refs(r, ("_checkBox", "_text"))


def decode_bulk_harvest_reward(r):
    from ui.window_animation import decode_common1_button_dialog
    fields = decode_common1_button_dialog(r)
    fields.update(editor_layout.refs(r, ("_listView", "_shortageTextRoot", "_staminaShortageText", "_toolShortageText")))
    return fields


def decode_harvest_layer_700(r):
    fields = {"layerCamera": r.pptr()}
    fields.update(editor_layout.refs(r, ("_menuUIContent", "_selectedToolView", "_toolSelectorViewController",
        "_harvestActionButton", "_toolNotHaveBalloon", "_harvestActionCannotButton", "_dashButton", "_bulkHarvestActionButton")))
    return fields

ONLY_700 = {
    "Sekai.UIPartsThumbnailRewardListCell": lambda r: {"sizeX": r.f32(), "sizeY": r.f32(), "thumbnailRoots": talk.decode_pptr_list(r), "nameText": r.pptr()},
    "Sekai.UIPartsCheckBox": decode_bulk_checkbox_control,
    "Sekai.Mysekai.HarvestPlayerHeadUpDisplay": decode_harvest_player_hud_700,
    "UnityEngine.UI.ScrollRect": decode_stock_scroll_rect,
    "Sekai.Mysekai.BulkHarvestActionButton": decode_bulk_harvest_button,
    "Sekai.Mysekai.BulkHarvestChargeGaugeView": decode_bulk_harvest_gauge,
    "Sekai.Mysekai.BulkHarvestCheckBoxListView": decode_bulk_harvest_list,
    "Sekai.Mysekai.BulkHarvestGroupCheckBoxView": decode_bulk_harvest_group,
    "Sekai.Mysekai.BulkHarvestTargetSettingCheckBox": decode_bulk_harvest_checkbox,
    "Sekai.Mysekai.BulkHarvestTargetSettingDialog": decode_bulk_harvest_settings,
    "Sekai.Mysekai.MysekaiBulkHarvestRewardDialog": decode_bulk_harvest_reward,
    "Sekai.Mysekai.ScreenLayerMysekaiHarvest": decode_harvest_layer_700,

    **photo_layout.DECODERS,
    "Sekai.UI.CustomScrollRect": decode_custom_scroll_rect_700,
    "Sekai.Mysekai.MysekaiMissionStartDialog": mission_layout.decode_mission_start_dialog,
    "Sekai.Mysekai.MysekaiMissionCompleteDialog": mission_layout.decode_mission_complete_dialog,
    "Sekai.Mysekai.MysekaiConvertFurnitureShortenDialog":
        convert_layout.decode_convert_shorten_dialog,
    "Sekai.CrystalShop.ShopConfirmDialogPriceParts": convert_layout.decode_price_parts,
    "Sekai.Mysekai.MysekaiConvertItemDetailDialog": convert_layout.decode_convert_item_detail_dialog,
    "Sekai.Mysekai.MysekaiConvertStartConfirmDialog": convert_layout.decode_convert_start_confirm_dialog,
    "Sekai.Mysekai.MysekaiConvertBlueprintLotterySubWindowDialog": convert_layout.decode_lottery_dialog,
    "Sekai.Mysekai.MysekaiConvertRecordLotterySubWindowDialog": convert_layout.decode_lottery_dialog,
    "Sekai.Mysekai.Inventory.CraftPreview": decode_craft_preview_700,
    "Sekai.Mysekai.MysekaiInfoOption1Page": decode_mysekai_info_option_page_700,
    "Sekai.Mysekai.ContentList.ContentListSelectorCell":
        decode_content_list_selector_cell_700,
    "Sekai.UIPartsHonorImage": decode_honor_image_700,
}
DECODERS_700 = {**SHARED, **ONLY_700}
LAYOUT_CHAINS = {
    "6.8.1": DECODERS_681,
    "7.0.0": DECODERS_700,
}
# The layout versions an export knows, oldest first.
VERSIONS = tuple(LAYOUT_CHAINS)

# (class name, serialized type hash hex) -> the layout the bytes carry.
TYPE_LAYOUTS = {
    ("Sekai.UIPartsThumbnailRewardListCell", "d2817fbfe87a2ef02b700815f2918477"): "7.0.0",
    ("Sekai.Mysekai.HarvestPlayerHeadUpDisplay", "b0aefa4373b8001001f4e51227165a4e"): "7.0.0",
    ("Sekai.UIPartsCheckBox", "beb6fd08a47ed26ebf63cc74f518fb08"): "7.0.0",
    ("UnityEngine.UI.ScrollRect", "d5cd7f207e91ca8336c65a577a6fb2a0"): "7.0.0",

    ("Sekai.Mysekai.BulkHarvestActionButton", "0502d10b158ddd2b9dd787bb9dd58fb4"): "7.0.0",
    ("Sekai.Mysekai.BulkHarvestChargeGaugeView", "2167f64c1ddff3c0aece99da045ee93c"): "7.0.0",
    ("Sekai.Mysekai.BulkHarvestCheckBoxListView", "d2835e1e2684fe21d4e3bddcd2841cf3"): "7.0.0",
    ("Sekai.Mysekai.BulkHarvestGroupCheckBoxView", "b3511010ceb423f8f82c90a70d17ce65"): "7.0.0",
    ("Sekai.Mysekai.BulkHarvestTargetSettingCheckBox", "5ad4d5bf1f146309431caa4cfd96f525"): "7.0.0",
    ("Sekai.Mysekai.BulkHarvestTargetSettingDialog", "1faddd3b4959d3e99ca1afa0d2c7ac43"): "7.0.0",
    ("Sekai.Mysekai.MysekaiBulkHarvestRewardDialog", "7231bc0e697f6ce50d2b7ce254736217"): "7.0.0",
    ("Sekai.Mysekai.ScreenLayerMysekaiHarvest", "4dda12598a43e512cb119ab96403b0a9"): "7.0.0",

    ("Sekai.UIPartsPhotoFrameThumbnail", "701a1b9241173c653af93358697fbf4a"): "7.0.0",
    ("Sekai.Mysekai.MysekaiSelectFrameView", "56a5828cb66f421adde139868f381dc6"): "7.0.0",
    ("Sekai.UIPartsPhotoFramePreview", "8d1a4f81285d407da7fc2608b914a06e"): "7.0.0",
    ("Sekai.Mysekai.MysekaiPhotoSaveAnimation", "a7952c6374ff1805f356704076fb3a8a"): "7.0.0",
    ("Sekai.AnimationClipEvent", "431f36454113f7d83bc74347ac5d008f"): "7.0.0",
    ("Sekai.Mysekai.MysekaiPhotoShotView", "9c249c4e2d9276be8c161151ab811ebf"): "7.0.0",
    ("Sekai.Mysekai.MysekaiPhotoShotAnimation", "f1b1ff8667c818bf70d56dd9b284b432"): "7.0.0",
    ("Sekai.Mysekai.ScreenLayerMysekaiPhotoShotPresenter", "a4805d90303631a00c5a8e37e1ff5a00"): "7.0.0",
    ("Sekai.ColorFader", "2a6b976ee5ac7d8120ec45875b46424f"): "6.8.1",
    ("Sekai.CrystalShop.ShopConfirmDialogPriceParts", "2a424898f46b3aeb4201e3430b854139"): "6.8.1",
    ("Sekai.EventBreakTimeController", "273defc7d306842f06a7362af5082289"): "6.8.1",
    ("Sekai.FreeWordFilterContent", "ec8ac42a1ac35e0fff58bbf777bd1913"): "6.8.1",
    ("Sekai.MissionCellView", "4528ff5bf7a4ad0f3ff6234e505a3390"): "6.8.1",
    ("Sekai.MusicSelect", "5eb6db0de11175cd078d521f3dbbe837"): "6.8.1",
    ("Sekai.MusicSelectListView", "acdd2264ac9ad37ba8e797d013f49289"): "6.8.1",
    ("Sekai.Mysekai.ContentList.ContentListSelectorCell", "a2c862aac38ddb0d86677af405a5b440"): "7.0.0",
    ("Sekai.Mysekai.ContentList.ContentListSelectorCell", "c1cd8e01aa2d79ed7d2a3454cc7e5627"): "6.8.1",
    ("Sekai.Mysekai.DeliveryActionButton", "2a79f1aa6a1d01a3c5a4463d78148ca1"): "6.8.1",
    ("Sekai.Mysekai.FixtureAttentionHeadUpDisplay", "f5f144cfc3a127fe8433020d8691597f"): "6.8.1",
    ("Sekai.Mysekai.HarvestCollectItemNoticeCell", "fc749ec245a4a5b1b509fd722f86bab5"): "6.8.1",
    ("Sekai.Mysekai.Inventory.CraftPreview", "7d2a01edecbaccc53499cd089c7cc645"): "6.8.1",
    ("Sekai.Mysekai.Inventory.CraftPreview", "dc8a5d454296fd711231cf2553f6322a"): "7.0.0",
    ("Sekai.Mysekai.MySekaiCommonNoticeCell", "0ec9ac35681fcf0dff95650d2e867e70"): "6.8.1",
    ("Sekai.Mysekai.MySekaiMissionNoticeCell", "df46f71d0a692ab152ac7cd070ead138"): "6.8.1",
    ("Sekai.Mysekai.MysekaFixtureHeadUpDisplayController", "30eca0d31977a42bf3a5b3ee1fb8fb60"): "6.8.1",
    ("Sekai.Mysekai.MysekaObjectHeadUpDisplayController", "4de0e3bd2bae199b07c4078c8da59c18"): "6.8.1",
    ("Sekai.Mysekai.MysekaiBlueprintRandomBoxCategoryGroup", "688fe826487e6ef9ffec251b7bd62c10"): "6.8.1",
    ("Sekai.Mysekai.MysekaiCharaRewardSubWindowDialog", "102b9be015b61f1dfcde35d12063bf3e"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertBlueprintLotterySubWindowDialog", "64268edf9e6dcac8da20d9354e609f5b"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFixtureSlotCellButtonView", "1e670b5ed5812cd4f73c1ce6dd65ccd1"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFixtureSlotCellGaugeView", "55a7c09d1b02db7290639c307f14c6fa"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFixtureSlotCellTimerView", "3b38c3437f71963e047cd4924bb0858b"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFixtureSlotView", "e41d2d48e753121ef27962b3bd4abd25"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFurnitureItemSelectDetailView", "4d1b497813a46e578922aeea115ec4e9"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFurnitureItemSelectListView", "19ee8fadba780da115d8a4ce83fa4b57"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFurnitureItemSelectView", "a9af89ccb9824a8612aef787a24ea91f"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFurniturePresenter", "ebd90cac65eaf9c31d9f8e4cd6923709"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFurnitureShortenDialog", "039dab4c3e4a76389d2181077d337081"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFurnitureSlotCellView", "d104b39416c155118e1491590533f647"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertFurnitureSlotView", "6ae7fa1f21be895cf6352ea4c9797c6c"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertItemDetailDialog", "d37a053e352c857bd73513c1bf90a861"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertRecordLotterySubWindowDialog", "eb2e3ee399c923846ee095b9096f2a5d"): "6.8.1",
    ("Sekai.Mysekai.MysekaiConvertStartConfirmDialog", "5fe7a0e916a65f662aec7a683cf9ad39"): "6.8.1",
    ("Sekai.Mysekai.MysekaiInfoOption1Page", "07953da62e9d4b0fa7626199c506edd3"): "7.0.0",
    ("Sekai.Mysekai.MysekaiInfoOption1Page", "4ba0531b6d14da8607273f8b5830fb5a"): "6.8.1",
    ("Sekai.Mysekai.MysekaiInfoRankPage", "53025f4dc36a601625da7de5075d5831"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMenuUIContent", "ff6762695583aac4822ff3c2462e8af3"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionCell", "8d02604548a409d6a757d31f8b2ad723"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionCompleteDialog", "91fb0c20331bf3cf80373d6cb2300317"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionDetailDialog", "3c47f55901f7177b5e7c2c0451f69096"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionInfoDialog", "adc213e009f3821dfc1fc3d180cde13b"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionListDialog", "94413a501e37aa2770cb3ab9df08ac62"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionRewardCell", "9826b83cb3b1676a3d94e28c82bab9b8"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionSheetCell", "74e6cf4de10e6b07d6003bd1300409b3"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionStartDialog", "e5238fd557f1bd48f01ff9f1c3487a11"): "6.8.1",
    ("Sekai.Mysekai.MysekaiMissionView", "9fc656a8638e90190d7fb884bac3c96f"): "6.8.1",
    ("Sekai.Mysekai.MysekaiRecordRandomBoxCategoryGroup", "67262baf1ebbe9a10743815b0ca594b7"): "6.8.1",
    ("Sekai.Mysekai.ObjectAttentionHeadUpDisplay", "82568351f2cc147d927f9a85c5627acb"): "6.8.1",
    ("Sekai.Mysekai.ScreenLayerMysekaiBGMSelect", "751580a12d737aef7e0495dc694df994"): "6.8.1",
    ("Sekai.Mysekai.ScreenLayerMysekaiCraft", "45ce5fb56f25251fe43e8190e33a1005"): "6.8.1",
    ("Sekai.Mysekai.ScreenLayerMysekaiHUD", "47e09b1c3e1a9f6870ab475159ff3e7b"): "6.8.1",
    ("Sekai.Mysekai.ScreenLayerMysekaiInfo", "614bb1d0094edb9b0d37d02558966595"): "6.8.1",
    ("Sekai.Mysekai.ScreenLayerMysekaiInventory", "4d98231acd6ce820d33175ccb5a5cb1e"): "6.8.1",
    ("Sekai.Mysekai.ScreenLayerMysekaiNotice", "2e803bdb13290549256936e8888ce9f0"): "6.8.1",
    ("Sekai.Mysekai.ScreenLayerSiteEditMode", "9b4d415dabeecd2dc7c32171490c4cad"): "6.8.1",
    ("Sekai.Mysekai.SiteEdit.SiteEditView", "1775757ebba02683070c597055d3904f"): "6.8.1",
    ("Sekai.Mysekai.UIPartsMysekaiConvertItem", "926f7eab39c137349ae5ee50a62db445"): "6.8.1",
    ("Sekai.Mysekai.UIPartsMysekaiRankGauge", "57fef83fb4ad355297def0c3ad105fa8"): "6.8.1",
    ("Sekai.OptionCommunicationSetting", "b966bbb51dd635c30f04faa8dbe8a2c9"): "6.8.1",
    ("Sekai.OptionLiveSetting", "fbadfe4e7ddfbb71887833f9cc0dace9"): "6.8.1",
    ("Sekai.OptionNoteSetting", "042b1858d7061803a29a4d3666af64b2"): "6.8.1",
    ("Sekai.OptionSystemSetting", "8eb091635909eeed632af78d7eba6c5b"): "6.8.1",
    ("Sekai.ScreenLayerMysekaiConvertFurniture", "11faa9b650f7533e23c7c2444068c8a9"): "6.8.1",
    ("Sekai.ScreenLayerMysekaiHarvestSummary", "cb07a4d24583623367da0e2cadf66a6a"): "6.8.1",
    ("Sekai.ScreenLayerMysekaiMission", "8c7b84d4b5dd711b82972d124f4be26a"): "6.8.1",
    ("Sekai.SubWindowComponent", "5a3c0aa51e533e6e8a8ccb8bb64f6a45"): "6.8.1",
    ("Sekai.SubWindowFadeAnimation", "0498b1dbe382c4cf590214f215e272f5"): "6.8.1",
    ("Sekai.UI.CustomButton", "3dea6fd66e2c86118ef6ca034453ef43"): "6.8.1",
    ("Sekai.UI.CustomDropdown", "657ccb128e92c6c4c858bbacf275f9a7"): "6.8.1",
    ("Sekai.UI.CustomIndexToggleGroup", "8b8d72bd4cb01cf08fbfcd0121d2dccf"): "6.8.1",
    ("Sekai.UI.CustomInputFieldTextMesh", "2e2620e5c8ef4eda79e04f8eb3c2b02b"): "6.8.1",
    ("Sekai.UI.CustomScrollRect", "9b7a704c00b99a994bc10fafb1778380"): "6.8.1",
    ("Sekai.UI.CustomScrollRect", "a60a7b83f16be5ac8a89d787d1cb36dc"): "7.0.0",
    ("Sekai.UI.CustomScrollbar", "630a1a5fe101e645301770df23eca0d4"): "6.8.1",
    ("Sekai.UI.CustomText", "b9945521985eca7173865bd35ece19eb"): "6.8.1",
    ("Sekai.UI.CustomTextMesh", "ec7822e3edc04e34d9218f31544b0c51"): "6.8.1",
    ("Sekai.UI.ListView", "76d116f59060ca8b9741bfb9c377d703"): "6.8.1",
    ("Sekai.UI.MusicSelectGridView", "92a04c949b2da6f3db4dd362a17d8927"): "6.8.1",
    ("Sekai.UI.MusicSelectListCellHighLightContents", "309ae776e86689840b08156070540f76"): "6.8.1",
    ("Sekai.UI.MusicSelectListCellNeutralContents", "a39e8b6fe463e51089c72577617305bc"): "6.8.1",
    ("Sekai.UI.UIPartsCommonButton", "4bc0848cc6bb920374f0708604166894"): "6.8.1",
    ("Sekai.UI.UIPartsLeftTabList", "ee3d93fb3211e02734cbd458ec4dc35e"): "6.8.1",
    ("Sekai.UI.UIPartsLeftTabSubList", "51b6bd3314080742f47c1a285b03d534"): "6.8.1",
    ("Sekai.UI.UIPartsNumericInputField", "febf02b46ce5c847600b483925635ef2"): "6.8.1",
    ("Sekai.UIPartsGauge", "45cf43fce33b71a481cba8f2042dcd5f"): "6.8.1",
    ("Sekai.UIPartsGaugeExp", "616474623b2462935926d7b636e7f8f2"): "6.8.1",
    ("Sekai.UIPartsHonorImage", "3dbf1be936144360c486a68d14a13d8b"): "7.0.0",
    ("Sekai.UIPartsHonorImage", "6a8b9a5026f60c1066daa17aaed40864"): "6.8.1",
    ("Sekai.UIPartsNumericSelector", "3ddd014e82984d92a9de00ff728f121b"): "6.8.1",
}


LAYOUT_CLASSES = frozenset(cls for cls, _ in TYPE_LAYOUTS)


class UnknownLayout(ValueError):
    """A serialized type hash that names no registered JP layout."""


def check_version(version: str) -> None:
    """Refuse a client version whose runs this module has no layouts for."""
    if version not in LAYOUT_CHAINS:
        raise ValueError(f"no decoder chains registered for JP {version}; "
                         f"supported versions: {', '.join(sorted(LAYOUT_CHAINS))}")


def decoders_for(version: str) -> dict:
    """One layout's chain table, the layout versions are named after.

    The export itself picks a chain per object (see :func:`chain_for`); this is
    the table of one layout version, for callers that read one layout's bytes.
    """
    check_version(version)
    return LAYOUT_CHAINS[version]


def chain_for(cls: str, type_hash, file_name: str, base):
    """The chain that reads one object, by its class and serialized type hash.

    *base* holds the chains every layout shares.  A class outside
    :data:`LAYOUT_CLASSES` has no JP chain and is read from *base*.  A class
    the JP tables know must name a registered (class, hash) row: an object of
    another layout is refused with its class, hash and file, never read with a
    fallback chain.
    """
    layout = TYPE_LAYOUTS.get((cls, type_hash))
    if layout is None:
        if cls in LAYOUT_CLASSES:
            raise UnknownLayout(
                f"{cls}: serialized type hash {type_hash or 'absent'} in {file_name} "
                f"names no registered JP layout")
        return base.get(cls)
    # A class with one layout in both clients lists that one row, and one
    # layout table may not carry its chain: the shared table does.
    return LAYOUT_CHAINS[layout].get(cls) or base.get(cls)


class TypeHashChains:
    """The decoder table of one run, with each JP object's chain chosen by its
    serialized type hash rather than by the run's client version."""

    def __init__(self, base):
        self.base = base

    def get(self, cls):
        return self.base.get(cls)

    def chain_for(self, cls, obj):
        return chain_for(cls, talk.serialized_type_hash(obj),
                         obj.assets_file.name, self.base)
