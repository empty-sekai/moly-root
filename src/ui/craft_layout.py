"""Source-serialized decoders of the MySekai craft screen (screen 615) and its dialogs.

The screen layer is ``Resources.Load("Screen/Prefabs/ScreenLayerMysekaiCraft")``
(its ScreenLayerData is ``screen/data/screenlayermysekaicraft``).  Its content-list
selector instantiates one list prefab per content type from the serialized
``_listSettingDataList`` references, and each list's ListView instantiates its
cell prefab; those list and cell prefabs sit in Resources folders but are reached
only through the references, so they are found by their name in the Resources
index (as the inventory's lists are).  The dialogs load from ``Dialog/<name>``.

Every decoder follows the declaration order of the managed type; the common
prefab reader requires each to consume its bytes exactly.  ``DECODERS`` holds the
CN chains; ``JP_DECODERS`` the classes JP serializes differently
(``ui.jp_decoders`` applies them on JP runs):

* ``Sekai.ScreenLayer.isBootDone`` is public on CN (so serialized after
  ``layerCamera``) and private on JP.
* ``Sekai.Mysekai.Inventory.CraftPreview`` ends at ``_craftTermText`` on CN;
  JP adds ``_layoutCostRoot`` and ``_layoutCostText``.
* ``Sekai.UI.CustomInputFieldTextMesh`` adds ``_rejectIfContainsNgWord`` after
  ``allowEmpty`` on JP.

Two prefabs of the screen are in no Resources folder at all: the colour selector's
cell (``ColorSelector.cellPrefab``) and the 3D preview's content
(``Object3DPreview._previewContentPrefab``, a reference to the content's
``Object3DPreviewContent`` component).  The screen instantiates them from those
references and nothing else names them, so :data:`POINTER_PREFABS` says which
component field of which prefab reaches each one, and the layout run follows that
reference.  The preview content is a 3D prefab (a Transform root holding the
preview camera and the model parent), not a UI one.
"""
from ui import talk
from ui.editor_layout import decode_fixture_content_list, refs
from ui.inventory_layout import decode_content_list_selector
from ui.window_animation import decode_subwindow_dialog

PREFABS = (
    "ScreenLayerMysekaiCraft", "CraftContentListSelector", "CraftFixtureContentList",
    "CraftToolContentList", "LimitedCraftFixtureContentList", "CraftThumbnailViewItem",
    "CraftResultSubWindowDialog", "FixtureBonusSubWindowDialog", "MysekaiRankUpDialog",
    "MysekaiPlayerStatusUpdateDialog", "MysekaiNewHarvestSiteOpenDialog",
    "FixtureColorCell", "3dView",
)

# Prefab -> (the prefab holding the reference, the holding component's class, its field).
POINTER_PREFABS = {
    "FixtureColorCell": ("ScreenLayerMysekaiCraft", "Sekai.FancyScrollView.ColorSelector.ColorSelector",
                         "cellPrefab"),
    "3dView": ("ScreenLayerMysekaiCraft", "Sekai.Mysekai.Object3DPreview", "_previewContentPrefab"),
}

LOADING = ('Resources.Load("Screen/Prefabs/ScreenLayerMysekaiCraft"); the content lists and '
           'their cells are instantiated from their serialized PPtr references; the dialogs '
           'load from Resources Dialog/<name>')

RUNTIME_VALUES = (
    "MasterMysekaiBlueprint and the owned blueprints (the craftable fixture and tool lists)",
    "UserMysekaiMaterial (the material costs' held quantities)",
    "the craft result CraftResultSubWindowDialog.Setup is given (item, craft count, rank exp)",
    "the fixture bonus, rank-up, player-status and new-harvest-site values their dialogs are set up with",
)

CRAFT_PREVIEW = (
    "_previewContent", "_materialCostList", "_blueprint3DPreview", "_colorSelector",
    "_nameText", "_systemFixtureIcon", "_craftCountInputSelector", "_nameObject",
    "_applyButton", "_cantCraftBalloon", "_screenButton", "_screenCanvasGroup",
    "_quantityObject", "_quantityText", "_sizeObject", "_sizeText", "_reactionCountView",
    "_fixtureBonusView", "_craftTermTextRoot", "_craftTermText",
)


def decode_screen_layer_craft(r):
    """ScreenLayerMysekaiCraft : ScreenLayer (CN: layerCamera and the public isBootDone)."""
    d = {"layerCamera": r.pptr(), "isBootDone": r.bool4()}
    d.update(refs(r, ("_contentListSelector", "_craftPreview")))
    return d


def decode_screen_layer_craft_jp(r):
    """ScreenLayerMysekaiCraft : ScreenLayer (JP: layerCamera only)."""
    d = {"layerCamera": r.pptr()}
    d.update(refs(r, ("_contentListSelector", "_craftPreview")))
    return d


def decode_craft_preview(r):
    return refs(r, CRAFT_PREVIEW)


def decode_craft_preview_jp(r):
    return refs(r, CRAFT_PREVIEW + ("_layoutCostRoot", "_layoutCostText"))


def decode_craft_thumbnail_cell(r):
    """CraftThumbnailViewItem : ListViewItem."""
    d = {"sizeX": r.f32(), "sizeY": r.f32()}
    d.update(refs(r, ("_thumbnail", "_selected", "_missionLabel")))
    return d


def decode_material_cost_list(r):
    return {"craftNeedItemCells": talk.decode_pptr_list(r), "_additionalMasterialLine": r.pptr()}


def decode_character_icon(r):
    """UIPartsCharacterIcon : ListViewItem."""
    d = {"sizeX": r.f32(), "sizeY": r.f32()}
    d.update(refs(r, ("iconImage", "_assetBundleIconImage")))
    d.update(characterId=r.i32(), iconSize=r.i32())
    d.update(refs(r, ("frameImage", "grayMaskObj")))
    return d


def decode_numeric_input_selector(r):
    return {"_numText": r.pptr(), "_decrementButtons": talk.decode_pptr_list(r),
            "_decrementButtonTexts": talk.decode_pptr_list(r),
            "_incrementButtons": talk.decode_pptr_list(r),
            "_incrementButtonTexts": talk.decode_pptr_list(r)}


def decode_tmp_input_field(r):
    """TMPro.TMP_InputField : Selectable, in declaration order (the delegate
    m_OnValidateInput is not serialized)."""
    d = talk.decode_selectable_base(r)
    d.update(refs(r, ("m_TextViewport", "m_TextComponent", "m_Placeholder",
                     "m_VerticalScrollbar", "m_VerticalScrollbarEventHandler", "m_LayoutGroup")))
    d["m_ScrollSensitivity"] = r.f32()
    d.update(m_ContentType=r.i32(), m_InputType=r.i32(), m_AsteriskChar=r.u32() & 0xFFFF,
             m_KeyboardType=r.i32(), m_LineType=r.i32(), m_HideMobileInput=r.bool4(),
             m_HideSoftKeyboard=r.bool4(), m_CharacterValidation=r.i32(),
             m_RegexValue=r.string(), m_GlobalPointSize=r.f32(), m_CharacterLimit=r.i32())
    for name in ("m_OnEndEdit", "m_OnSubmit", "m_OnSelect", "m_OnDeselect", "m_OnTextSelection",
                 "m_OnEndTextSelection", "m_OnValueChanged", "m_OnTouchScreenKeyboardStatusChanged"):
        d[name] = talk.decode_unity_event(r)
    d.update(m_CaretColor=r.color(), m_CustomCaretColor=r.bool4(), m_SelectionColor=r.color(),
             m_Text=r.string(), m_CaretBlinkRate=r.f32(), m_CaretWidth=r.i32(),
             m_ReadOnly=r.bool4(), m_RichText=r.bool4(), m_GlobalFontAsset=r.pptr(),
             m_OnFocusSelectAll=r.bool4(), m_ResetOnDeActivation=r.bool4(),
             m_RestoreOriginalTextOnEscape=r.bool4(), m_isRichTextEditingAllowed=r.bool4(),
             m_LineLimit=r.i32(), m_InputValidator=r.pptr())
    return d


def decode_custom_input_field(r):
    """CustomInputFieldTextMesh : TMP_InputField (CN)."""
    d = decode_tmp_input_field(r)
    d.update(se=r.i32(), interval=r.i32(), checkEmoji=r.bool4(), allowEmpty=r.bool4())
    return d


def decode_custom_input_field_jp(r):
    """CustomInputFieldTextMesh : TMP_InputField (JP: plus _rejectIfContainsNgWord)."""
    d = decode_custom_input_field(r)
    d["_rejectIfContainsNgWord"] = r.bool4()
    return d


def decode_scroller(r):
    """FancyScrollView.Scroller."""
    d = {"viewport": r.pptr(), "scrollDirection": r.i32(), "movementType": r.i32(),
         "elasticity": r.f32(), "scrollSensitivity": r.f32(), "inertia": r.bool4(),
         "decelerationRate": r.f32()}
    d["snap"] = {"Enable": r.bool4(), "VelocityThreshold": r.f32(), "Duration": r.f32(),
                 "Easing": r.i32()}
    d.update(draggable=r.bool4(), scrollbar=r.pptr())
    return d


def decode_color_selector(r):
    """ColorSelector : CustomFancyScrollView<FixtureColorData, Context> :
    FancyScrollView<FixtureColorData, Context>."""
    d = {"cellInterval": r.f32(), "scrollOffset": r.f32(), "loop": r.bool4(),
         "cellContainer": r.pptr()}
    d.update(refs(r, ("scroller", "_rightArrowButton", "_leftArrowButton", "cellPrefab")))
    return d


def decode_object_3d_preview(r):
    d = refs(r, ("_targetImage", "_loadingCircleObject", "_previewContentPrefab"))
    d["__previewRotation"] = r.vec4()
    d.update(_eventSystemGestureListener=r.pptr(), _enablePinchInOut=r.bool4())
    return d


def decode_color_selector_cell(r):
    """ColorSelectorCell : FancyCell<FixtureColorData> (the base serializes nothing)."""
    return refs(r, ("animator", "button", "colorImage", "_coverImage"))


def decode_object_3d_preview_content(r):
    d = refs(r, ("_targetParent", "_fixtureViewCamera"))
    d["_cameraDistance"] = r.f32()
    return d


def decode_universal_additional_camera_data(r):
    """URP UniversalAdditionalCameraData, in declaration order.  Its TemporalAA.Settings
    serializes six fields: resetHistoryFrames and jitterFrameCountOffset are not
    serialized (the object's bytes end after contrastAdaptiveSharpening)."""
    d = {"m_RenderShadows": r.bool4(), "m_RequiresDepthTextureOption": r.i32(),
         "m_RequiresOpaqueTextureOption": r.i32(), "m_CameraType": r.i32()}
    d["m_Cameras"] = [r.pptr() for _ in range(r.i32())]
    d.update(m_RendererIndex=r.i32(), m_VolumeLayerMask=r.u32(), m_VolumeTrigger=r.pptr(),
             m_VolumeFrameworkUpdateModeOption=r.i32(), m_RenderPostProcessing=r.bool4(),
             m_Antialiasing=r.i32(), m_AntialiasingQuality=r.i32())
    for name in ("m_StopNaN", "m_Dithering", "m_ClearDepth", "m_AllowXRRendering",
                 "m_AllowHDROutput", "m_UseScreenCoordOverride"):
        d[name] = r.bool4()
    d.update(m_ScreenSizeOverride=r.vec4(), m_ScreenCoordScaleBias=r.vec4(),
             m_RequiresDepthTexture=r.bool4(), m_RequiresColorTexture=r.bool4(), m_Version=r.f32())
    d["m_TaaSettings"] = {"quality": r.i32(), **{name: r.f32() for name in (
        "frameInfluence", "jitterScale", "mipBias", "varianceClampScale", "contrastAdaptiveSharpening")}}
    return d


def decode_gradient_color(r):
    """UiEffect.GradientColor : BaseMeshEffect (which serializes nothing)."""
    d = {name: r.color() for name in ("m_colorTop", "m_colorBottom", "m_colorLeft", "m_colorRight")}
    d.update(m_gradientOffsetVertical=r.f32(), m_gradientOffsetHorizontal=r.f32(),
             m_splitTextGradient=r.bool4())
    return d


def _dialog(names):
    def decode(r):
        d = decode_subwindow_dialog(r)
        d.update(refs(r, names))
        return d
    return decode


DECODERS = {
    "Sekai.Mysekai.ScreenLayerMysekaiCraft": decode_screen_layer_craft,
    "Sekai.Mysekai.ContentList.CraftContentListSelector": decode_content_list_selector,
    "Sekai.Mysekai.ContentList.CraftContentList": decode_fixture_content_list,
    "Sekai.Mysekai.ContentList.CraftThumbnailViewItem": decode_craft_thumbnail_cell,
    "Sekai.Mysekai.Inventory.CraftPreview": decode_craft_preview,
    "Sekai.Mysekai.Inventory.MaterialCostList": decode_material_cost_list,
    "Sekai.Mysekai.Inventory.MaterialCostCell":
        lambda r: refs(r, ("thumbnail", "needQuantityText", "hasQuantityText")),
    "Sekai.CraftFixtureBonusView": lambda r: refs(r, (
        "_fixtureBonusTargetCharacterIcon", "_emptyCharacterIcon", "_fixtureBonusRateText",
        "_fixtureBonusNotUpText", "_notCreatedIcon", "_createdIcon")),
    "Sekai.Mysekai.MysekaiCharacterReactionCountView":
        lambda r: refs(r, ("_infoButtonParent", "_infoButton", "_countText")),
    "Sekai.Mysekai.SystemFixtureIcon": lambda r: {"textureLoader": r.pptr()},
    "Sekai.UIPartsCharacterIcon": decode_character_icon,
    "Sekai.UIPartsNumericInputSelector": decode_numeric_input_selector,
    "Sekai.UI.CustomInputFieldTextMesh": decode_custom_input_field,
    "FancyScrollView.Scroller": decode_scroller,
    "Sekai.FancyScrollView.ColorSelector.ColorSelector": decode_color_selector,
    "Sekai.Mysekai.MysekaiBlueprint3DPreview":
        lambda r: {"_previewScale": r.f32(), "_object3DPreview": r.pptr()},
    "Sekai.Mysekai.Object3DPreview": decode_object_3d_preview,
    "Sekai.Mysekai.Inventory.ColorSelectorCell": decode_color_selector_cell,
    "Sekai.Mysekai.Object3DPreviewContent": decode_object_3d_preview_content,
    "UnityEngine.Rendering.Universal.UniversalAdditionalCameraData": decode_universal_additional_camera_data,
    # Declares no serialized field.
    "UnityEngine.Rendering.Universal.SekaiAdditionalCameraData": lambda r: {},
    "Sekai.RankUp.RankUpCommonTitle": lambda r: {"animator": r.pptr(), "playOnAwake": r.bool4()},
    "UiEffect.GradientColor": decode_gradient_color,
    "Sekai.Mysekai.CraftResultSubWindowDialog": _dialog((
        "itemImage", "_rankGaugeViewRoot", "balloon", "button", "craftCountText",
        "mysekaiRankExpText", "firstCraftBonusBalloon", "_mysekaiRankGauge")),
    "Sekai.FixtureBonusSubWindowDialog": _dialog(("_icon", "_beforeBonusText", "_afterBonusText")),
    "Sekai.Mysekai.MysekaiRankUpDialog": _dialog((
        "_fxRootCanvasGroup", "_rankUpAnimator", "_titleAnimator", "_titleRawImage",
        "_beforeRankText", "_afterRankText", "_rankUpParticleRoot")),
    "Sekai.Mysekai.MysekaiPlayerStatusUpdateDialog": _dialog((
        "_thumbnailLoader", "_description", "_beforeValueText", "_afterValueText",
        "_contentLayoutGroup", "_changesLayout")),
    "Sekai.Mysekai.MysekaiNewHarvestSiteOpenDialog": _dialog((
        "_thumbnailLoader", "_description", "_contentLayoutGroup")),
}

JP_DECODERS = {
    "Sekai.Mysekai.ScreenLayerMysekaiCraft": decode_screen_layer_craft_jp,
    "Sekai.Mysekai.Inventory.CraftPreview": decode_craft_preview_jp,
    "Sekai.UI.CustomInputFieldTextMesh": decode_custom_input_field_jp,
}
