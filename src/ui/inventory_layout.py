"""Source-serialized decoders of the MySekai inventory screen (screen 607).

The screen layer is ``Resources.Load("Screen/Prefabs/ScreenLayerMysekaiInventory")``;
its content-list selector instantiates one list prefab per content type from
the serialized ``_listSettingDataList`` references, and each list's ListView
instantiates its cell prefab.  The list and cell prefabs sit in Resources
folders but are reached only through those references, so they are found by
their name in the Resources index (as the editor's lists are).

Every decoder follows the declaration order of the managed type; the common
prefab reader requires each to consume its bytes exactly.  The ScreenLayer
base is region-specific (the JP chain is in ``ui.jp_decoders``).
"""
from ui import talk
from ui.editor_layout import decode_content_list, decode_fixture_content_list, refs

PREFABS = (
    "ScreenLayerMysekaiInventory", "FixtureContentList", "MaterialContentList",
    "ItemContentList", "ToolContentList", "UIPartsItemThumbnailListViewItem",
    "ContentListSelectorCell",
)

LOADING = ('Resources.Load("Screen/Prefabs/ScreenLayerMysekaiInventory"); the content '
           'lists, their cells and the selector tab cell are instantiated from their '
           'serialized PPtr references')

RUNTIME_VALUES = (
    "UserMysekaiFixture (owned fixtures: id, textureId, quantity)",
    "UserMysekaiMaterial / UserMysekaiItem / UserMysekaiTool (owned resources)",
    "UserMysekaiMaterialPossession and UserMysekaiGamedata possession levels "
    "(the chest limit texts)",
)


def inventory_fields(r):
    """ScreenLayerMysekaiInventory's own six references."""
    return refs(r, ("_contentListSelector", "_chestLimitTextRoot", "_chestLimitText",
                    "_chestLimitNumerText", "_chestLimitDenomText", "_inventoryPreview"))


def decode_screen_layer_inventory(r):
    """ScreenLayerMysekaiInventory : ScreenLayer on the older chain: ScreenLayer's
    ``layerCamera`` and public ``isBootDone``, then the six references."""
    d = {"layerCamera": r.pptr(), "isBootDone": r.bool4()}
    d.update(inventory_fields(r))
    return d


def decode_content_list_selector(r):
    """ContentListSelector's serialized fields."""
    d = refs(r, ("_upperTabGroup", "_tabCellRoot", "_tabCellPrefab", "_contentListBaseRoot"))
    d["_listSettingDataList"] = [{"_listType": r.i32(), "_contentListBasePrefab": r.pptr()}
                                  for _ in range(r.i32())]
    d["_cellSettingDataList"] = [{"_listType": r.i32(), "_sprite": r.pptr(),
                                  "_disableSprite": r.pptr(), "_selectColor": r.color(),
                                  "_unselectColor": r.color()} for _ in range(r.i32())]
    return d


def decode_inventory_selector(r):
    """InventoryContentListSelector : ContentListSelector."""
    d = decode_content_list_selector(r)
    d["_fixtureFilterToggle"] = r.pptr()
    return d


def decode_inventory_preview(r):
    return refs(r, (
        "_previewObject", "_emptyText", "_nameMarqueeObject", "_nameText", "_fixtureObject",
        "_systemFixtureIcon", "_gridSizeText", "_blueprint3DPreview", "_itemObject",
        "_itemPreview", "_itemPreviewTextureLoader", "_descriptionView", "_canNotRecycleInfo",
        "_recycleButton",
    ))


def decode_user_resource_list(r):
    """UserResourceContentList : ContentListBase."""
    d = decode_content_list(r)
    d.update(refs(r, ("_sortDropdown", "_tabListController", "_nothingText")))
    return d


def decode_item_thumbnail(r):
    """UIPartsItemThumbnail : UIPartsThumbnail."""
    d = refs(r, ("thumbnailImage", "textureLoader", "innerText", "thumbnailCanvasGroup",
                 "disableCover"))
    d["preserveAspect"] = r.bool4()
    d.update(refs(r, ("labelImage", "button", "clickDetector", "frameImage", "frameMask",
                     "thumbnailBase", "_zoomIcon")))
    return d


def decode_item_thumbnail_cell(r):
    """UIPartsItemThumbnailListViewItem : ListViewItem."""
    d = {"sizeX": r.f32(), "sizeY": r.f32()}
    d.update(refs(r, ("thumbnail", "selected")))
    return d


DECODERS = {
    "Sekai.Mysekai.ScreenLayerMysekaiInventory": decode_screen_layer_inventory,
    "Sekai.Mysekai.ContentList.InventoryContentListSelector": decode_inventory_selector,
    "Sekai.Mysekai.Inventory.InventoryPreview": decode_inventory_preview,
    "Sekai.Mysekai.Inventory.DescriptionView":
        lambda r: refs(r, ("pageChanger", "leftButton", "rightButton")),
    "Sekai.Mysekai.FixtureInventoryContentList": decode_fixture_content_list,
    "Sekai.Mysekai.ContentList.UserResourceContentList": decode_user_resource_list,
    "Sekai.UIPartsItemThumbnail": decode_item_thumbnail,
    "Sekai.Mysekai.ContentList.UIPartsItemThumbnailListViewItem": decode_item_thumbnail_cell,
}
