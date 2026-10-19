"""Source-serialized decoders of the MySekai convert-furniture screen and its dialogs.

The screen layer is ``Resources.Load("Screen/Prefabs/ScreenLayerMysekaiConvertFurniture")``
(its ScreenLayerData is ``screen/data/screenlayermysekaiconvertfurniture``); its
dialogs load from ``Dialog/<name>`` and carry the skeleton unlock balloon
``UIPartsReleaseConditionsBalloon``, which opens the expiry-slot dialog.  Every
decoder follows the declaration order of the managed type; the common prefab
reader requires each to consume its bytes exactly:

* ``Sekai.ScreenLayerMysekaiConvertFurniture`` derives from ``ScreenLayer``
  (JP: only ``layerCamera``), then the presenter.
* The presenter, the two page views (slot page, item-select page), the slot
  cell and fixture slot views, the button and timer views, the item select list
  and detail views, the random-box category groups and
  ``UIPartsMysekaiConvertItem`` serialize references (some as arrays), except
  the slot cell gauge view, which also holds two animation seconds (floats) and
  a receive animation curve after its effect roots.
* ``Sekai.Mysekai.MysekaiConvertFixtureSlotView`` derives from MonoBehaviour
  only; it holds one image, then three reference arrays (the blurred, highlight
  and mask images), then the frame image, the slot number text and the convert
  item.
* ``Sekai.Mysekai.MysekaiConvertFurnitureSlotView`` holds four references, the
  slot cell array and the unlock balloon, and ends there.
* The gauge view's ``AnimationCurve`` is the engine type: a key count, each
  key's seven words, then the two wrap modes and the rotation order.
* ``Sekai.Mysekai.UIWaveGaugeAnimation`` stays undecoded: its declaration holds
  a canvas group and a mask, but its objects carry a single reference after the
  maskable fields.
* The item detail, start-confirm and shorten dialogs derive from
  ``Common1ButtonDialog`` or ``Common2ButtonDialog``.  The shorten dialog's own
  click event follows its references, and its crystal price parts serialize a
  number text and a paid-charge label.  The two lottery dialogs derive from
  ``MysekaiConvertLotterySubWindowDialogBase``, which derives from
  ``SubWindowDialog`` and adds one animation root before the subclass's own
  fields.
"""
from ui import craft_layout
from ui import mission_layout
from ui import talk
from ui.window_animation import (
    decode_common1_button_dialog,
    decode_common2_button_dialog,
    decode_subwindow_dialog,
)
from ui.editor_layout import refs
from ui.mission_layout import dialog_click_entries

PREFABS = (
    "ScreenLayerMysekaiConvertFurniture", "MysekaiConvertItemDetailDialog",
    "MysekaiConvertStartConfirmDialog", "MysekaiConvertFurnitureShortenDialog",
    "MysekaiConvertBlueprintLotterySubWindowDialog",
    "MysekaiConvertRecordLotterySubWindowDialog",
)

# No prefab of this family is reached only through a serialized reference: the
# cell prefabs the item-select detail view and the category groups name live in
# Resources folders of their own.
POINTER_PREFABS = {}


def decode_animation_curve(r):
    """UnityEngine.AnimationCurve: its key count, each key, then its wrapping.

    A key carries its time, value, in and out tangents, its weighted-mode flag
    and its in and out weights (seven words); the two wrap modes and the
    rotation order follow the keys as three integers.  The engine type has no
    managed serialized fields, so the third word is named after the engine
    field the extractor's other curve readers use.
    """
    keys = []
    for _ in range(r.i32()):
        keys.append({"time": r.f32(), "value": r.f32(), "inTangent": r.f32(),
                     "outTangent": r.f32(), "weightedMode": r.i32(),
                     "inWeight": r.f32(), "outWeight": r.f32()})
    return {"keys": keys, "preWrapMode": r.i32(), "postWrapMode": r.i32(),
            "rotationOrder": r.i32()}


def decode_screen_convert(r):
    """ScreenLayerMysekaiConvertFurniture : ScreenLayer (JP: layerCamera only),
    then the presenter."""
    return {"layerCamera": r.pptr(), "_presenter": r.pptr()}


def decode_presenter(r):
    """MysekaiConvertFurniturePresenter: the slot page and the item-select page."""
    return refs(r, ("_slotView", "_itemSelectView"))


def decode_slot_view(r):
    """MysekaiConvertFurnitureSlotView: the fade group, the system fixture icon,
    the blurred image, the animation root, the slot cells and the unlock
    balloon; the declaration ends there."""
    d = refs(r, ("_fadeCanvasGroup", "_systemFixtureIcon", "_blahImage", "_animationRoot"))
    d["_slotCellViews"] = talk.decode_pptr_list(r)
    d.update(refs(r, ("_unlockSlotBalloon",)))
    return d


def decode_slot_cell_view(r):
    """MysekaiConvertFurnitureSlotCellView: the fade group and the three views of
    one slot (gauge, timer, button)."""
    return refs(r, ("_fadeCanvasGroup", "_gaugeView", "_timerView", "_buttonView"))


def decode_slot_cell_gauge_view(r):
    """MysekaiConvertFixtureSlotCellGaugeView: the slot view, the empty-slot
    button, the progress text, the five state contents, the wave gauge, the
    three effect roots, the two animation seconds and the receive curve; the
    declaration ends there."""
    d = refs(r, ("_slotView", "_emptySlotButton", "_progressText", "_lockedContent",
                 "_readyContent", "_settingContent", "_progressContent", "_completeContent",
                 "_gaugeAnimation", "_waitEffectRoot", "_progressHighlightEffectRoot",
                 "_completeHighlightEffectRoot"))
    d.update(_settingAnimationSeconds=r.f32(), _receiveAnimationSeconds=r.f32(),
             _receiveAnimationCurve=decode_animation_curve(r))
    return d


def decode_slot_cell_button_view(r):
    """MysekaiConvertFixtureSlotCellButtonView: the selected button, the five raw
    images and the two receive-state contents."""
    return refs(r, ("_selectedButton", "_baseImage", "_offImage", "_onImage", "_effectImage",
                    "_interactImage", "_unReceiveableContent", "_receiveableContent"))


def decode_slot_cell_timer_view(r):
    """MysekaiConvertFixtureSlotCellTimerView: the base image, the timer text and
    the two shorten buttons with the locked balloon."""
    return refs(r, ("_baseImage", "_timerText", "_shortenSlotButton", "_shortenSlotLockedButton",
                    "_shortenSlotLockedBalloon"))


def decode_fixture_slot_view(r):
    """MysekaiConvertFixtureSlotView : MonoBehaviour: the base image, the
    blurred, highlight and mask image arrays, the frame image, the slot number
    text and the convert item; the declaration ends there."""
    d = refs(r, ("_baseImage",))
    for name in ("_blahImages", "_highlightImages", "_maskImages"):
        d[name] = talk.decode_pptr_list(r)
    d.update(refs(r, ("_frameImage", "_slotNumberText", "_convertItem")))
    return d


def decode_convert_item(r):
    """UIPartsMysekaiConvertItem: the canvas group, the base and blurred images
    and the item thumbnail."""
    return refs(r, ("_canvasGroup", "_baseImage", "_blahImage", "_convertItemThumbnail"))


def decode_item_select_view(r):
    """MysekaiConvertFurnitureItemSelectView: the fade group, the tab list, the
    list and the detail view of the item-select page."""
    return refs(r, ("_fadeCanvasGroup", "_tabList", "_itemSelectListView",
                    "_itemSelectDetailView"))


def decode_item_select_list_view(r):
    """MysekaiConvertFurnitureItemSelectListView: the reuse scroller and its
    empty text."""
    return refs(r, ("_scroller", "_emptyText"))


def decode_remaining_count_info(r):
    """The remaining-count row of the detail view: its base image and text."""
    return refs(r, ("_remainingCountBaseImage", "_remainingCountText"))


def decode_item_select_detail_view(r):
    """MysekaiConvertFurnitureItemSelectDetailView: the slot view, the item name,
    the obtained item and its possession row, the two random-box category
    groups, the consumed-resource row and cell prefab, the convert time, the
    count selector, the three state objects, the start button, the remaining
    count row, the reset and end texts, the two balloons, the locked button and
    balloon."""
    d = refs(r, ("_slotView", "_itemNameRoot", "_itemNameText", "_obtainedConvertItem",
                 "_obtainedResourcePossessionRoot", "_obtainedResourcePossessionCount",
                 "_blueprintRandomBoxCategoryGroup", "_recordRandomBoxCategoryGroup",
                 "_consumedResourceRoot", "_consumedResourceCellPrefab", "_convertTimeText",
                 "_selectConvertCount", "_notEnoughConsumedResourceObj", "_lockedObj",
                 "_soldOutObj", "_convertStartButton"))
    d["_remainingCountInfo"] = decode_remaining_count_info(r)
    d.update(refs(r, ("_resetAtText", "_endAtText", "_shortenByJoinedPassBalloon",
                      "_notEnoughConsumedResourceBalloon", "_lockedButton", "_lockedBalloon")))
    return d


def decode_blueprint_category_group(r):
    """MysekaiBlueprintRandomBoxCategoryGroup: the layout groups and the cell prefab."""
    return {"_layoutGroups": talk.decode_pptr_list(r), "_categoryCellPrefab": r.pptr()}


def decode_record_category_group(r):
    """MysekaiRecordRandomBoxCategoryGroup: the layout groups and the cell prefab."""
    return {"_layoutGroups": talk.decode_pptr_list(r), "_categoryCellPrefab": r.pptr()}


def decode_numeric_input_field(r):
    """UIPartsNumericInputField : TMP_InputField, on the same JP chain as the
    craft screen's input field."""
    return craft_layout.decode_numeric_input_field_jp(r)


def decode_shorten_dialog_681(r):
    """MysekaiConvertFurnitureShortenDialog : Common2ButtonDialog (6.8.1), then
    the confirm and remaining-time texts, the three crystal quantity texts, the
    not-enough text, the jewel-shop button and the commercial-transaction-law
    button.  The 6.8.1 dialog's object ends there; 7.0.0 adds its click event,
    which the unverified 6.8.1 chain would misread."""
    d = decode_common2_button_dialog(r)
    d.update(refs(r, ("_confirmShortenText", "_remainingTimeToCompleteConvertText",
                      "_consumedJewelQuantityText", "_currentTotalJewelQuantityText",
                      "_currentPaidJewelQuantityText", "_notEnoughJewelText",
                      "_toJewelShopButton", "_specifiedCommercialTransactionLawArticleButton")))
    return d


def decode_convert_item_detail_dialog(r):
    """MysekaiConvertItemDetailDialog : Common1ButtonDialog, then the item
    thumbnail, the name and the description."""
    d = decode_common1_button_dialog(r)
    d.update(refs(r, ("_itemThumbnail", "_nameText", "_descriptionText")))
    return d


def decode_convert_start_confirm_dialog(r):
    """MysekaiConvertStartConfirmDialog : Common2ButtonDialog, then the obtained
    item, the item name, the convert time, the pass balloon and the pass
    expiration warning."""
    d = decode_common2_button_dialog(r)
    d.update(refs(r, ("_obtainedConvertItem", "_itemNameText", "_convertTimeText",
                      "_shortenByJoinedPassBalloon", "_passExpirationWarningText")))
    return d


def decode_convert_shorten_dialog(r):
    """MysekaiConvertFurnitureShortenDialog : Common2ButtonDialog, then the
    confirm and remaining-time texts, the three crystal quantity texts, the
    not-enough text, the jewel-shop button and the commercial-transaction-law
    button, then the dialog's click event."""
    d = decode_common2_button_dialog(r)
    d.update(refs(r, ("_confirmShortenText", "_remainingTimeToCompleteConvertText",
                      "_consumedJewelQuantityText", "_currentTotalJewelQuantityText",
                      "_currentPaidJewelQuantityText", "_notEnoughJewelText",
                      "_toJewelShopButton", "_specifiedCommercialTransactionLawArticleButton")))
    d["clickEvent"] = dialog_click_entries(r)
    return d


def decode_price_parts(r):
    """Sekai.CrystalShop.ShopConfirmDialogPriceParts: the number text and the
    paid-charge label."""
    return refs(r, ("num", "paidChargeLabel"))


def decode_lottery_dialog(r):
    """MysekaiConvertLotterySubWindowDialogBase : SubWindowDialog, then the
    animation root the box is instantiated under."""
    d = decode_subwindow_dialog(r)
    d.update(refs(r, ("_animationRoot",)))
    return d


DECODERS = {
    "Sekai.ScreenLayerMysekaiConvertFurniture": decode_screen_convert,
    "Sekai.Mysekai.MysekaiConvertFurniturePresenter": decode_presenter,
    "Sekai.Mysekai.MysekaiConvertFurnitureSlotView": decode_slot_view,
    "Sekai.Mysekai.MysekaiConvertFurnitureSlotCellView": decode_slot_cell_view,
    "Sekai.Mysekai.MysekaiConvertFixtureSlotCellGaugeView": decode_slot_cell_gauge_view,
    "Sekai.Mysekai.MysekaiConvertFixtureSlotCellButtonView": decode_slot_cell_button_view,
    "Sekai.Mysekai.MysekaiConvertFixtureSlotCellTimerView": decode_slot_cell_timer_view,
    "Sekai.Mysekai.MysekaiConvertFixtureSlotView": decode_fixture_slot_view,
    "Sekai.Mysekai.UIPartsMysekaiConvertItem": decode_convert_item,
    "Sekai.Mysekai.MysekaiConvertFurnitureItemSelectView": decode_item_select_view,
    "Sekai.Mysekai.MysekaiConvertFurnitureItemSelectListView": decode_item_select_list_view,
    "Sekai.Mysekai.MysekaiConvertFurnitureItemSelectDetailView": decode_item_select_detail_view,
    "Sekai.Mysekai.MysekaiBlueprintRandomBoxCategoryGroup": decode_blueprint_category_group,
    "Sekai.Mysekai.MysekaiRecordRandomBoxCategoryGroup": decode_record_category_group,
    "Sekai.UI.UIPartsNumericInputField": decode_numeric_input_field,
    "Sekai.Mysekai.MysekaiConvertItemDetailDialog": decode_convert_item_detail_dialog,
    "Sekai.Mysekai.MysekaiConvertStartConfirmDialog": decode_convert_start_confirm_dialog,
    "Sekai.Mysekai.MysekaiConvertFurnitureShortenDialog": decode_shorten_dialog_681,
}


# The shorten dialog's 6.8.1 file keeps atomic scene objects beside its prefab:
# components whose four-byte entries its typetree does not describe.  The
# dialog's own document leaves those out and names them.
ATOMIC_OBJECTS_681 = {
    "MysekaiConvertFurnitureShortenDialog": (
        "UnityEngine.UI.RectMask2D",
        "uPalette.Runtime.Core.Synchronizer.Color.GraphicColorSynchronizer",
        "Sekai.UI.CustomImage",
        "Sekai.UI.CustomRawImage",
        "Sekai.UIPartsDialogTab",
    ),
}


# The 7.0.0 chains: the same dialogs with the click event the version adds.
# The pause overlay points the client's own list at the sheet it opens, so its
# chain stays in this version's table.
DECODERS_700 = {
    "Sekai.Mysekai.MysekaiConvertItemDetailDialog": decode_convert_item_detail_dialog,
    "Sekai.Mysekai.MysekaiConvertStartConfirmDialog": decode_convert_start_confirm_dialog,
    "Sekai.Mysekai.MysekaiConvertFurnitureShortenDialog": decode_convert_shorten_dialog,
    "Sekai.CrystalShop.ShopConfirmDialogPriceParts": decode_price_parts,
    "Sekai.Mysekai.MysekaiConvertBlueprintLotterySubWindowDialog": decode_lottery_dialog,
    "Sekai.Mysekai.MysekaiConvertRecordLotterySubWindowDialog": decode_lottery_dialog,
}
