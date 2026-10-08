"""Serialized field chains of the JP 6.8.1 client, where they differ from the CN 6.0.0 chains.

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


REGION = "jp"
VERSION = "6.8.1"


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
    """MysekaiInfoOption1Page: six serialized references."""
    return editor_layout.refs(r, (
        "_voiceDLButton", "_voiceDLText", "_mysekaiVisitSettingToggleGroup",
        "_mysekaiImageQualityToggleGroup", "_mysekaiFPSSettingToggleGroup",
        "_mysekaiConvertFixtureNotificationSettingToggleGroup",
    ))


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
}
