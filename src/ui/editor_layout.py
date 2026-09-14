"""Source-serialized editor UI decoders; no layout hand authoring.

The common prefab reader requires registered decoders to consume all bytes.
"""
from ui import talk

PREFABS = (
    "ScreenLayerSiteEditMode", "MysekaiEditSaveConfirmationDialog",
    "SiteEditListSelectorCell", "SiteEditOutDoorContentList", "SiteEditFloorContentList",
    "SiteEditWallContentList", "SiteEnvironmentContentList", "RoadFixtureContentList",
    "FixtureSelectCell", "UIPartsLeftTabListMysekaiContentCell",
    "ScreenLayerHeader",
)

LOADING = ('Resources.Load("Screen/Prefabs/ScreenLayerSiteEditMode") / '
           '"Dialog/MysekaiEditSaveConfirmationDialog"; list and cell prefabs '
           'are instantiated from their serialized PPtr references')

RUNTIME_VALUES = (
    "UserMysekaiSiteHousingLayoutData (per-site saved layout / editable draft)",
    "UserMysekaiFixture and custom ornament inventory (owned counts)",
    "MasterMysekaiFixtureTabView plus runtime site availability / selection",
)


def refs(r, names):
    return {name: r.pptr() for name in names}


def decode_site_edit_mode(r):
    d = {"layerCamera": r.pptr(), "isBootDone": r.bool4()}
    d.update(refs(r, (
        "_contentListSelector", "_siteEditView", "_fixtureEditHeadUpDisplay",
        "_placedCountHeadUpDisplay", "_sequentialFixtureStartMarker",
        "_sequentialFixtureEndMarker", "_groundEditTargetIcon",
        "_fixtureLayoutCountInfo", "_siteEnvironmentSelectContent",
        "_siteEnvironmentText", "_rightBottomRoot", "_rectangle",
        "_showFixtureToggle", "_switchEditModeToggle", "_longTouchGauge",
    )))
    return d


def decode_site_edit_view(r):
    return refs(r, (
        "_saveButton", "_removeAllButton", "_infoButton", "_presetSaveButton",
        "_changeLookButton", "_rotateButton", "_reportTipButton",
        "_uiDisableButton", "_uiEnableButton", "_uiEnableCanvasGroup",
    ))


def decode_fixture_edit_hud(r):
    d = refs(r, ("deleteButton", "cancelButton", "rotateButton", "decideButton",
                 "_layoutGroup", "_contentSizeFitter"))
    d["_screenOffsetScale"] = r.f32()
    return d


def decode_expansion_selector(r):
    d = refs(r, ("_upperTabGroup", "_tabCellRoot", "_tabCellPrefab", "_contentListBaseRoot"))
    d["_listSettingDataList"] = [{"_listType": r.i32(), "_contentListBasePrefab": r.pptr()}
                                  for _ in range(r.i32())]
    d["_cellSettingDataList"] = [{"_listType": r.i32(), "_sprite": r.pptr(),
                                  "_disableSprite": r.pptr(), "_selectColor": r.color(),
                                  "_unselectColor": r.color()} for _ in range(r.i32())]
    d.update(refs(r, ("baseContentRectTransform", "eventTrigger", "handleObject", "handleTapEffect")))
    d.update({name: r.f32() for name in ("viewBaseHide", "viewBaseWidth", "viewMaxWidth")})
    return d


def decode_tab_group(r):
    d = {"selectorCells": talk.decode_pptr_list(r)}
    d.update(refs(r, ("cellPrefab", "cellRoot", "coverObj")))
    return d


def decode_fixture_thumbnail(r):
    d = refs(r, ("thumbnailImage", "textureLoader", "innerText", "thumbnailCanvasGroup", "disableCover"))
    d["preserveAspect"] = r.bool4()
    d.update(refs(r, ("labelImage", "button", "clickDetector", "frameImage", "frameMask", "thumbnailBase", "_zoomIcon", "_subThumbnailImage")))
    return d


def decode_ui_parts_toggle(r):
    d = {"_seToggleOn": r.i32(), "_seToggleOff": r.i32()}
    d.update(refs(r, ("desableGroup", "offGroup", "onGroup")))
    return d


def decode_common_multi_dialog(r):
    d = refs(r, ("header", "closeButton", "windowObject"))
    d.update(needBackgroundCover=r.bool4(), openSE=r.i32(), closeBehavior=r.i32())
    d["dialogButtonObjectList"] = [{"key": r.string(), "dialogButton": r.pptr(),
                                    "dialogButtonLabelMesh": r.pptr()}
                                   for _ in range(r.i32())]
    d["messageBodyTextMesh"] = r.pptr()
    return d


def decode_content_list(r):
    return {"contentListType": r.i32(), "listView": r.pptr()}


def decode_site_edit_content_list(r):
    d = decode_content_list(r)
    d.update(refs(r, ("_tabListController", "_fixtureContentListView", "_collectionContentList",
                     "_penlightContentList", "_honorContentList", "_recordContentListView",
                     "_photoContentListView")))
    return d


def decode_fixture_content_list(r):
    d = decode_content_list(r)
    d.update(refs(r, ("_searchButton", "_hashTagFilteredBalloon", "_sortDropdown",
                     "_tabListController", "_nothingText")))
    return d


def decode_site_environment_list(r):
    d = decode_content_list(r)
    d.update(refs(r, ("_sortDropdown", "_tabListController", "_nothingText")))
    return d


def decode_site_edit_fixture_view(r):
    return refs(r, ("_listView", "_sortDropdown", "_nothingText", "_searchButton",
                    "_hashTagFilteredBalloon"))


def decode_selector_cell(r):
    d = refs(r, ("numberText", "button", "_tapEffect", "_icon", "selectedObj", "lineObj",
                 "badgeObj", "bannerCanvas", "selectBannerObj", "selectBannerText",
                 "selectBannerCover"))
    d.update(disabledTextColor=r.color(), enableTextColor=r.color())
    d.update(refs(r, ("_iconImage", "_missionIcon")))
    return d


def decode_list_view(r):
    d = {"scrollRect": r.pptr(), "horizontalSpacing": r.f32(), "verticalSpacing": r.f32(),
         "padding": [r.i32() for _ in range(4)], "rowCount": r.i32(), "columnCount": r.i32(),
         "snap": r.bool4(), "snapSpeed": r.f32(), "offSnapTypeCalc": r.bool4(),
         "listViewItemPrefab": r.pptr()}
    return d


def decode_custom_scroll_rect(r):
    d = {"m_Content": r.pptr(), "m_Horizontal": r.bool4(), "m_Vertical": r.bool4(),
         "m_MovementType": r.i32(), "m_Elasticity": r.f32(), "m_Inertia": r.bool4(),
         "m_DecelerationRate": r.f32(), "m_ScrollSensitivity": r.f32()}
    d.update(refs(r, ("m_Viewport", "m_HorizontalScrollbar", "m_VerticalScrollbar")))
    d.update(m_HorizontalScrollbarVisibility=r.i32(), m_VerticalScrollbarVisibility=r.i32(),
             m_HorizontalScrollbarSpacing=r.f32(), m_VerticalScrollbarSpacing=r.f32(),
             m_OnValueChanged=talk.decode_unity_event(r), absolutelyControl=r.bool4(),
             disableVerticalScrollByAuto=r.bool4(), disableHorizontalScrollByAuto=r.bool4(),
             mask=r.pptr())
    return d


def decode_common_button(r):
    d = refs(r, ("rectTransform", "baseImage", "coverImage", "customButton"))
    d.update(adjustButtonSetting=r.bool4(), adjustTextSize=r.bool4(), changeSprite=r.bool4(),
             customTextMesh=r.pptr(), buttonType=r.i32(), displayType=r.i32(), size=r.i32(),
             tapEffect=r.pptr(), tip=r.pptr())
    return d


def decode_texture_loader(r):
    d = refs(r, ("targetGraphic", "loadingObject", "notFoundObject"))
    d.update(doCache=r.bool4(), showIndicator=r.bool4(), onDisableUnload=r.bool4())
    return d


def decode_left_tab_controller(r):
    d = refs(r, ("tabList", "subTabList"))
    d.update(subTabListOffset=r.f32(), subTabListCloseArea=r.pptr(),
             enableTapWhenCloseSubTab=r.bool4(), _touchController=r.pptr())
    return d


def decode_fixture_cell(r):
    d = {"sizeX": r.f32(), "sizeY": r.f32()}
    d.update(refs(r, ("thumbnail", "selected", "_inPlacedLabel", "_missionLabel")))
    return d


def decode_fixed_scroller(r):
    d = refs(r, ("scrollRect", "prefab"))
    d.update(baseDirectionType=r.i32(), updateItemTimingOffsetCount=r.i32(),
             layoutInfo={"splitCount": r.i32(), "itemSpacing": r.vec2()},
             padding=[r.i32() for _ in range(4)],
             onScrollItem=talk.decode_unity_event(r), onUpdateItem=talk.decode_unity_event(r),
             onDisableItem=talk.decode_unity_event(r))
    return d


def decode_common_tap_effect(r):
    d = refs(r, ("_buttonImage", "_glowButtonImage", "_buttonTextMesh"))
    d.update(_additionalTextList=talk.decode_pptr_list(r), _autoSetEffectImage=r.bool4())
    return d


def decode_graphic_tap_effect(r):
    d = refs(r, ("_effectGraphic", "_effectGraphicIcon"))
    d.update(_defaultColorPalette=r.i32(), _effectColorPalette=r.i32(),
             _useCustomAlpha=r.bool4(), _customDefaultAlpha=r.f32(),
             _customEffectAlpha=r.f32(), _refreshColorWhenAwake=r.bool4())
    return d


def decode_text_tap_effect(r):
    d = decode_graphic_tap_effect(r)
    d["_effectText"] = r.pptr()
    return d


def decode_event_trigger(r):
    return {"m_Delegates": [{"eventID": r.i32(), "callback": talk.decode_unity_event(r)}
                            for _ in range(r.i32())]}


def decode_dialog_setting(r):
    d = refs(r, ("sizeFitter", "tabGroup", "buttonGroup"))
    d.update(dialogSize=r.i32(), tabCount=r.i32(), buttonCount=r.i32())
    return d


def decode_scrollbar(r):
    d = talk.decode_selectable_base(r)
    d.update(m_HandleRect=r.pptr(), m_Direction=r.i32(), m_Value=r.f32(), m_Size=r.f32(),
             m_NumberOfSteps=r.i32(), m_OnValueChanged=talk.decode_unity_event(r))
    return d


def decode_dropdown(r):
    d = talk.decode_selectable_base(r)
    d.update(refs(r, ("m_Template", "m_CaptionText", "m_CaptionImage", "m_Placeholder",
                     "m_ItemText", "m_ItemImage")))
    d["m_Value"] = r.i32()
    d["m_Options"] = [{"m_Text": r.string(), "m_Image": r.pptr()} for _ in range(r.i32())]
    d.update(m_OnValueChanged=talk.decode_unity_event(r), m_AlphaFadeSpeed=r.f32(),
             baseObject=r.pptr(), arrowObject=r.pptr(), showsAllItems=r.bool4(), se=r.i32())
    return d


def decode_mysekai_left_cell(r):
    d = {"base": refs(r, ("button", "cellView"))}
    d.update(refs(r, ("_icon", "textureLoader", "deselectedBack", "selectedBack", "button", "nameText")))
    d["subTabArrowObjects"] = talk.decode_pptr_list(r)
    d.update(refs(r, ("_lockedObject", "_lockedBalloon", "_missionIcon", "_iconTapEffect",
                     "_nameTextTapEffect", "_tapEffects")))
    return d


def decode_hashtag_balloon(r):
    d = refs(r, ("_hashTag", "_multipleTagsText", "_canvasGroup"))
    d.update({name: r.f32() for name in ("_timeToFadeIn", "_timeToStartFadeOut", "_timeToFadeOut")})
    return d


def decode_layer_data(r):
    return {
        "ScreenType": r.i32(), "DisplayLayer": r.i32(), "StartAnimationType": r.i32(),
        "MaskInTexture": r.pptr(), "MaskInUV": r.vec4(), "MaskInHaveDirection": r.bool4(),
        "ExitAnimationType": r.i32(), "MaskOutTexture": r.pptr(), "MaskOutUV": r.vec4(),
        "MaskOutHaveDirection": r.bool4(), "DisplayHeader": r.i32(), "DisplayCategory": r.i32(),
        "DisplayPlayerInfo": r.i32(), "DisplayBackUIScreen": r.i32(), "EnableBackUIScreen": r.bool4(),
        "DisplayScreenName": r.i32(), "ScreenName": r.string(), "ScreenSubName": r.string(),
        "EnableTapScreenAnimation": r.bool4(), "IncludeChildCanvasForAlphaTransiton": r.bool4(),
        "hasLayerCamera": r.bool4(), "layerCameraPriority": r.i32(), "bgmType": r.i32(),
        "BackgroundType": r.i32(),
    }


DECODERS = {
    "Sekai.Mysekai.ScreenLayerSiteEditMode": decode_site_edit_mode,
    "Sekai.Mysekai.SiteEdit.SiteEditView": decode_site_edit_view,
    "Sekai.Mysekai.ContentList.SiteEditFixtureLayoutCountInfo":
        lambda r: refs(r, ("_layoutInfoText", "_layoutCountText")),
    "Sekai.Mysekai.EditTargetIcon": lambda r: refs(r, ("_fixtureThumbnail",)),
    "Sekai.Mysekai.LongTouchGauge": lambda r: refs(r, ("_canvasGroup", "_gauge")),
    "Sekai.Mysekai.FixtureEditHeadUpDisplay": decode_fixture_edit_hud,
    "Sekai.Mysekai.PlanningConfirmHeadUpDisplay": lambda r: refs(r, ("_okButton", "_cancelButton")),
    "Sekai.Mysekai.PlacedCountHeadUpDisplay": lambda r: refs(r, ("_text",)),
    "Sekai.Mysekai.SequentialFixtureMarker": lambda r: {},
    "Sekai.Mysekai.ExpansionContentListSelector": decode_expansion_selector,
    "Sekai.UI.UIPartsTabGroup": decode_tab_group,
    "Sekai.Mysekai.UIPartsFixtureThumbnail": decode_fixture_thumbnail,
    "Sekai.UIPartsToggle": decode_ui_parts_toggle,
    "UnityEngine.UI.Mask": lambda r: {"m_ShowMaskGraphic": r.bool4()},
    "Sekai.Mysekai.MysekaiEditSaveConfirmationDialog": decode_common_multi_dialog,
    "Sekai.Mysekai.ContentList.SiteEditContentList": decode_site_edit_content_list,
    "Sekai.Mysekai.ContentList.SiteEditFixtureContentListView": decode_site_edit_fixture_view,
    "Sekai.Mysekai.ContentList.FixtureContentList": decode_fixture_content_list,
    "Sekai.Mysekai.ContentList.SiteEnvironmentContentList": decode_site_environment_list,
    "Sekai.Mysekai.ContentList.ContentListSelectorCell": decode_selector_cell,
    "Sekai.Mysekai.ContentList.ContentListSortDropDown": lambda r: refs(r, ("_sortOrderButton", "_sortDropdown")),
    "Sekai.UI.ListView": decode_list_view,
    "Sekai.UI.CustomScrollRect": decode_custom_scroll_rect,
    "UnityEngine.UI.RectMask2D": lambda r: {"m_Padding": r.vec4(), "m_Softness": [r.i32(), r.i32()]},
    "Sekai.UI.UIPartsCommonButton": decode_common_button,
    "Sekai.UI.UITextureLoader": decode_texture_loader,
    "Sekai.UI.UIPartsLeftTabListController": decode_left_tab_controller,
    "Sekai.UI.FixtureSelectCell": decode_fixture_cell,
    "Sekai.UI.UIPartsIconInfoView": lambda r: refs(r, ("infoTextRoot", "infoText", "lockedObject")),
    "Sekai.UI.UIPartsLeftTabList": lambda r: refs(r, ("scroller", "topScrollableIcon", "bottomScrollableIcon", "backPanelTransform")),
    "Sekai.UI.UIPartsLeftTabSubList": lambda r: refs(r, ("scroller", "topScrollableIcon", "bottomScrollableIcon")),
    "Sekai.FixedSizeReuseScroller": decode_fixed_scroller,
    "Sekai.UI.CommonButtonTapEffect": decode_common_tap_effect,
    "Sekai.UI.GraphicButtonTapEffect": decode_graphic_tap_effect,
    "Sekai.UI.TextButtonTapEffect": decode_text_tap_effect,
    "Sekai.UI.MultiButtonTapEffect": lambda r: {"_tapEffectList": talk.decode_pptr_list(r)},
    "SafeArea.SafeAreaAdjuster": lambda r: {k: r.bool4() for k in ("isAutoScale", "isStretchX", "isSafeNochArea", "isIgnoreDefaultAdjustmentRate", "isStretchY")},
    "Sekai.UIPartsLoadingCircle": lambda r: {"circleTweens": talk.decode_pptr_list(r), "circleImages": talk.decode_pptr_list(r), "colorType": r.i32(), "canvasGroup": r.pptr()},
    "UnityEngine.EventSystems.EventTrigger": decode_event_trigger,
    "Sekai.DialogSetting": decode_dialog_setting,
    "Sekai.DialogSizeFitter": lambda r: refs(r, ("windowRect", "paddingLayoutElement", "content")),
    "Sekai.UIPartsDialogTabGroup": lambda r: refs(r, ("toggleGroup", "tabPrefab")),
    "Sekai.UIPartsDialogButtonGroup": lambda r: refs(r, ("layoutGroup", "buttonPrefab")),
    "Sekai.UIPartsDialogTab": lambda r: refs(r, ("backgroundImage", "text", "badge", "lineObj")),
    "Sekai.UI.ScrollMask": lambda r: {"mask": r.pptr(), "maskThreshold": r.f32()},
    "Sekai.UIPartsFilterButton": lambda r: refs(r, ("_button", "offImage", "onImage")),
    "Sekai.UIPartsSortOrder": lambda r: refs(r, ("_button", "ascImage", "descImage")),
    "Sekai.UI.CustomScrollbar": decode_scrollbar,
    "UnityEngine.UI.Scrollbar": decode_scrollbar,
    "Sekai.UI.CustomDropdown": decode_dropdown,
    "Sekai.Mysekai.ContentList.UIPartsLeftTabListMysekaiContentCell": decode_mysekai_left_cell,
    "Sekai.UI.UIPartsHashTagFilteredBalloon": decode_hashtag_balloon,
    "Sekai.UI.UIPartsHashTag": lambda r: refs(r, ("_contentSizeFitter", "_layoutGroup", "_tagText", "_baseImage", "_button")),
    "Sekai.Mysekai.ContentList.SiteEditCustomFixtureOrnamentCollectionList": lambda r: refs(r, ("_listView", "_sortDropdown", "_nothingText", "_filterButton")),
    "Sekai.Mysekai.ContentList.SiteEditCustomFixtureOrnamentPenlightList": lambda r: refs(r, ("_listView", "_sortDropdown", "_nothingText", "_filterButton")),
    "Sekai.Mysekai.ContentList.SiteEditCustomFixtureOrnamentHonorList": lambda r: refs(r, ("_listView", "_sortDropdown", "_nothingText", "_filterButton")),
    "Sekai.Mysekai.ContentList.SiteEditCustomFixtureOrnamentRecordList": lambda r: refs(r, ("_listView", "_sortDropdown", "_nothingText", "_filterButton")),
    "Sekai.Mysekai.ContentList.SiteEditCustomFixtureOrnamentPhotoList": lambda r: refs(r, ("_listView", "_sortDropdown", "_nothingText")),
    "Sekai.ScreenLayerData": decode_layer_data,
    "Sekai.TouchController": lambda r: {},
    "Sekai.UI.UIPartsCommonBalloon": lambda r: {
        "isAllowedClose": r.bool4(), "canvasGroup": r.pptr(), "mainText": r.pptr(),
        "touchController": r.pptr(), "adjustSize": r.bool4(), "baseCenterRt": r.pptr(),
        "baseSideRts": talk.decode_pptr_list(r), "padding": [r.i32() for _ in range(4)],
        "maxSize": r.vec2(), "textSizeFitter": r.pptr(), "textLayoutElement": r.pptr(),
    },
}


def component_runtime_sprites(class_name, fields, resolver):
    """Actual selector Sprite refs used only when runtime tab cells are built."""
    if class_name != "Sekai.Mysekai.ExpansionContentListSelector":
        return {}
    resolved = {}
    for row in fields["_cellSettingDataList"]:
        for field, suffix in (("_sprite", "normal"), ("_disableSprite", "disabled")):
            alias = f"editor-tab-{row['_listType']}-{suffix}"
            sprite = resolver.sprite(row[field])
            if sprite.get("state") != "ok":
                raise ValueError(f"editor selector runtime Sprite missing: {alias}: {sprite}")
            resolved[alias] = sprite
    return resolved
