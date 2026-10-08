"""Serialized fields of the MySekai field HUD layer, its head-up displays and the notice cells.

``ScreenLayerMysekaiHUD`` serializes the ScreenLayer base, then nineteen
references: the root canvas group its show and hide fades drive, the tweet,
navigate-icon, name and info roots the layer instantiates its displays under,
the chat and stamp roots with their canvas groups, the display prefabs it
instantiates (the tweet display, the player name display, the player info
panel, the stamp, the chat balloon, the navigate icon and the tutorial arrow),
the harvest player display and the two attention controllers.  Its display
dictionaries, cancellation sources and player lists are runtime state.

``MysekaObjectHeadUpDisplayController`` and
``MysekaFixtureHeadUpDisplayController`` each serialize the root their
attention displays are created under and the attention display prefab; the
dictionary of live displays is runtime state.

``HeadUpDisplayBase`` serializes nothing (its cameras, offset, transform and
screen point are set at runtime), so ``ObjectAttentionHeadUpDisplay`` and
``FixtureAttentionHeadUpDisplay`` serialize only their own canvas group; the
follow target and the site type are runtime state.  ``TweetHeadUpDisplay``
keeps the nine references ``ui.talk`` reads.

The notice cells serialize only references: ``MySekaiMissionNoticeCell`` the
mission name and progress texts, the icon image, the clear mark and the
progress gauge; ``MySekaiCommonNoticeCell`` the message text, the icon
texture loader and the icon atlas image; ``HarvestCollectItemNoticeCell`` the
item name and count texts, the new mark, the rare effect, the limit text and
the icon texture loader.  Their tweeners, view data and sound names are
runtime state.

``Sekai.ColorFader`` (the collect-item cell's new mark) serializes its target
object, the blend type, the default colour and colour table, the default
style, delay and duration, then ``playOnAwake``, ``playOnEnable`` and
``includeChildren``; its coroutine, state and fade target are runtime state.

These chains are read from the JP declarations; the ScreenLayer base is
the JP one (``layerCamera`` only).  ``CN_DECODERS`` holds the CN chains,
which differ in two classes: CN declares ``ScreenLayer.isBootDone`` public, so
the HUD layer serializes it after ``layerCamera``, and CN's
``MySekaiCommonNoticeCell`` has no ``iconAtlasImage``.  Every other class here
is declared identically in the two builds.
"""
from __future__ import annotations

from ui import talk

SCREEN_LAYER_HUD_FIELDS = (
    "_rootCanvasGroup", "_tweetRootObj", "_navigateIconRoot", "_nameRoot", "_infoRoot",
    "_chatRoot", "_stampRoot", "_chatCanvasGroup", "_stampCanvasGroup",
    "_TweetHeadUpDisplay", "_harvestPlayerHeadUpDisplay", "_mysekaiPlayerHUDPrefab",
    "_mysekaiPlayerInfoPrefab", "stampPrefab", "chatBalloonPrefab", "mysekaiNavigateIcon",
    "mysekaiTutorialNavigateArrowIcon", "_fixtureHeadUpDisplayController",
    "_objectHeadUpDisplayController",
)

OBJECT_HUD_CONTROLLER_FIELDS = ("_objectAttentionRoot", "_objectAttentionHeadUpDisplayPrefab")
FIXTURE_HUD_CONTROLLER_FIELDS = ("_fixtureAttentionRoot", "_fixtureAttentionHeadUpDisplayPrefab")

MISSION_NOTICE_CELL_FIELDS = (
    "_missionNameText", "_progressText", "_iconImage", "_clearMarkObj", "_progressGauge",
)
COMMON_NOTICE_CELL_FIELDS = ("messageText", "iconLoader", "iconAtlasImage")
COLLECT_ITEM_NOTICE_CELL_FIELDS = (
    "_itemNameText", "_itemGetCountText", "_newObject", "_rareEffect", "_itemLimitText",
    "_iconLoader",
)


def _refs(r: talk.Reader, names) -> dict:
    return {name: r.pptr() for name in names}


def decode_screen_layer_mysekai_hud(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiHUD: ScreenLayer.layerCamera, then nineteen references."""
    d = {"layerCamera": r.pptr()}
    d.update(_refs(r, SCREEN_LAYER_HUD_FIELDS))
    return d


def decode_screen_layer_mysekai_hud_cn(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiHUD (CN): ScreenLayer.layerCamera and the public
    isBootDone, then the nineteen references."""
    d = {"layerCamera": r.pptr(), "isBootDone": r.bool4()}
    d.update(_refs(r, SCREEN_LAYER_HUD_FIELDS))
    return d


def decode_object_hud_controller(r: talk.Reader) -> dict:
    """MysekaObjectHeadUpDisplayController: the attention root and the display prefab."""
    return _refs(r, OBJECT_HUD_CONTROLLER_FIELDS)


def decode_fixture_hud_controller(r: talk.Reader) -> dict:
    """MysekaFixtureHeadUpDisplayController: the attention root and the display prefab."""
    return _refs(r, FIXTURE_HUD_CONTROLLER_FIELDS)


def decode_attention_head_up_display(r: talk.Reader) -> dict:
    """Object and fixture attention displays: the canvas group only."""
    return {"_canvasGroup": r.pptr()}


def decode_mission_notice_cell(r: talk.Reader) -> dict:
    """MySekaiMissionNoticeCell: five serialized references."""
    return _refs(r, MISSION_NOTICE_CELL_FIELDS)


def decode_common_notice_cell(r: talk.Reader) -> dict:
    """MySekaiCommonNoticeCell: three serialized references."""
    return _refs(r, COMMON_NOTICE_CELL_FIELDS)


def decode_common_notice_cell_cn(r: talk.Reader) -> dict:
    """MySekaiCommonNoticeCell (CN): the message text and the icon loader."""
    return _refs(r, COMMON_NOTICE_CELL_FIELDS[:2])


def decode_collect_item_notice_cell(r: talk.Reader) -> dict:
    """HarvestCollectItemNoticeCell: six serialized references."""
    return _refs(r, COLLECT_ITEM_NOTICE_CELL_FIELDS)


def decode_color_fader(r: talk.Reader) -> dict:
    """ColorFader: the fade's serialized defaults."""
    d = {"targetGameObject": r.pptr(), "blendType": r.i32(), "defaultColor": r.color()}
    d["defaultColorTable"] = [r.color() for _ in range(r.i32())]
    d.update(defaultStyle=r.i32(), defaultDelay=r.f32(), defaultDuration=r.f32(),
             playOnAwake=r.bool4(), playOnEnable=r.bool4(), includeChildren=r.bool4())
    return d


DECODERS = {
    "Sekai.Mysekai.ScreenLayerMysekaiHUD": decode_screen_layer_mysekai_hud,
    "Sekai.Mysekai.MysekaObjectHeadUpDisplayController": decode_object_hud_controller,
    "Sekai.Mysekai.MysekaFixtureHeadUpDisplayController": decode_fixture_hud_controller,
    "Sekai.Mysekai.ObjectAttentionHeadUpDisplay": decode_attention_head_up_display,
    "Sekai.Mysekai.FixtureAttentionHeadUpDisplay": decode_attention_head_up_display,
    "Sekai.Mysekai.MySekaiMissionNoticeCell": decode_mission_notice_cell,
    "Sekai.Mysekai.MySekaiCommonNoticeCell": decode_common_notice_cell,
    "Sekai.Mysekai.HarvestCollectItemNoticeCell": decode_collect_item_notice_cell,
    "Sekai.ColorFader": decode_color_fader,
}

CN_DECODERS = {
    **DECODERS,
    "Sekai.Mysekai.ScreenLayerMysekaiHUD": decode_screen_layer_mysekai_hud_cn,
    "Sekai.Mysekai.MySekaiCommonNoticeCell": decode_common_notice_cell_cn,
}
