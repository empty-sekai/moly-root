"""Serialized subwindow presentation and its concrete menu binding.

Only serialized fields are decoded. Tween durations belong to the runtime
animation classes, rather than to a second set of prefab defaults.
"""
from __future__ import annotations

from ui import talk


def decode_dialog_base(r: talk.Reader) -> dict:
    fields = {name: r.pptr() for name in ("header", "closeButton", "windowObject")}
    fields.update(needBackgroundCover=r.bool4(), openSE=r.i32(), closeBehavior=r.i32())
    return fields


def decode_subwindow_dialog(r: talk.Reader) -> dict:
    fields = decode_dialog_base(r)
    fields.update({name: r.pptr() for name in ("messageBodyText", "subWindowComponent", "animation")})
    return fields


def decode_mysekai_menu_dialog(r: talk.Reader) -> dict:
    fields = decode_subwindow_dialog(r)
    fields.update({name: r.pptr() for name in (
        "_staminaView", "_recoverStaminaButton", "_recoverStaminaLabel", "_mysekaiRankGauge",
        "_chestButton", "_mysekaiInfoButton", "_mysekaiAvtarChangeButton", "_transitionToTitleButton",
        "_exitMysekaiButton", "_photoShotButton", "_photoAlbumButton", "_crystalShopButton",
        "_whiteBlueprintSketchButton", "_exchangeButton", "_mysekaiAvtarChangeButtonImage",
        "_photoAlbumButtonImage", "_exitMysekaiButtonImage", "_chestButtonImage", "_crystalShopButtonImage",
        "_whiteBlueprintSketchButtonImage", "_photoShotButtonImage", "_mysekaiInfoButtonImage", "_exchangeButtonImage",
    )})
    return fields


def decode_learn_phenomena_dialog(r: talk.Reader) -> dict:
    """LearnPhenomenaSubWindowDialog: the subwindow chain, then the thumbnail
    image, the name balloon and the button that opens it (``_balloonText`` is
    runtime state)."""
    fields = decode_subwindow_dialog(r)
    fields.update({name: r.pptr() for name in ("_itemImage", "_balloon", "_button")})
    return fields


def decode_honor_reward_dialog(r: talk.Reader) -> dict:
    """HonorRewardSubWindowDialog: the subwindow chain, then the honor image
    root, the name balloon and the button that opens it (the honor it shows is
    runtime state, passed to its setup)."""
    fields = decode_subwindow_dialog(r)
    fields.update({name: r.pptr() for name in ("imageRoot", "balloon", "button")})
    return fields


def decode_common_reward_dialog(r: talk.Reader) -> dict:
    """CommonRewardSubWindowDialog: the subwindow chain, then the thumbnail
    root, the message object and the thumbnail-with-balloon prefab it
    instantiates once per reward (the rewards and their paging indices are
    runtime state, passed to its setup).  JP and CN serialize the same three
    fields."""
    fields = decode_subwindow_dialog(r)
    fields.update({name: r.pptr() for name in ("thumbnailRoot", "messageObj", "thumbnailWithBalloonPrefab")})
    return fields


def decode_thumbnail_with_balloon(r: talk.Reader) -> dict:
    """UIPartsThumbnailWithBalloon, the cell CommonRewardSubWindowDialog
    instantiates per reward: the name balloon, the thumbnail root and the
    button that opens the balloon.  The thumbnail itself is created under
    the root by its setup from the reward's resource, so it is runtime
    state.  JP and CN serialize the same three fields."""
    return {name: r.pptr() for name in ("balloon", "thumbnailRoot", "button")}


def decode_honor_image(r: talk.Reader) -> dict:
    """UIPartsHonorImage, the honor HonorUtility.CreateHonorImage instantiates
    under a dialog's image root (HonorRewardSubWindowDialog.imageRoot): the
    core image the honor's own texture goes into, the frame sprite, the rank
    image, the three level views (plain pips, unique-honor pips, live-master
    level) and the two positions the live-master level is placed at.  Which
    honor, level and slot it shows is runtime state passed to its setup.
    JP and CN serialize the same eight fields."""
    return {name: r.pptr() for name in (
        "coreImage", "frameSprite", "rankImage", "levelView", "_uniqueLevelView",
        "_liveMasterLevel", "_liveMasterLevelMainPosition", "_liveMasterLevelSubPosition")}


def decode_honor_level(r: talk.Reader) -> dict:
    """UIPartsHonorLevel: the level pip images and the sprites a pip takes."""
    return {"images": talk.decode_pptr_list(r), "sprites": talk.decode_pptr_list(r)}


def decode_unique_honor_level(r: talk.Reader) -> dict:
    """UIPartsUniqueHonorLevel: the unique-honor level pip images."""
    return {"_images": talk.decode_pptr_list(r)}


def decode_live_master_honor_level(r: talk.Reader) -> dict:
    """UIPartsLiveMasterHonorLevel: the scroll image, the clear-count number
    view and the lit and unlit star images."""
    return {"_scrollImage": r.pptr(), "_clearCountNumberView": r.pptr(),
            "_enableStarImages": talk.decode_pptr_list(r),
            "_disableStarImages": talk.decode_pptr_list(r)}


def decode_rank_live_number_view(r: talk.Reader) -> dict:
    """Sekai.RankLive.NumberView, the live-master level's clear count: the
    number, the sprite-name prefix and postfix, the digit images and what
    it shows for a number with more digits than images.  JP and CN serialize
    the same five fields."""
    return {"num": r.i32(), "prefix": r.string(), "postfix": r.string(),
            "digits": talk.decode_pptr_list(r), "outOfDigitViewMode": r.i32()}


def decode_refresh_birthday_plant_dialog(r: talk.Reader) -> dict:
    """MysekaiRefreshBirthdayPlantSubWindowDialog: the subwindow chain, then the
    icon loader (the icon it loads is runtime state)."""
    fields = decode_subwindow_dialog(r)
    fields["_iconLoader"] = r.pptr()
    return fields


def decode_subwindow_component(r: talk.Reader) -> dict:
    return dict(windowType=r.i32(), contentTransform=r.pptr(), backPanelTransform=r.pptr(),
                closeButtons=talk.decode_pptr_list(r))


def decode_subwindow_slide_animation(r: talk.Reader) -> dict:
    return dict(subWindowComponent=r.pptr(), slideOffset=r.vec2(), slideType=r.i32())


def decode_subwindow_fade_animation(r: talk.Reader) -> dict:
    return {"canvasGroup": r.pptr()}


DECODERS = {
    "Sekai.Mysekai.MysekaiMenuDialog": decode_mysekai_menu_dialog,
    "Sekai.SubWindowDialog": decode_subwindow_dialog,
    "Sekai.Mysekai.LearnPhenomenaSubWindowDialog": decode_learn_phenomena_dialog,
    "Sekai.HonorRewardSubWindowDialog": decode_honor_reward_dialog,
    "Sekai.CommonRewardSubWindowDialog": decode_common_reward_dialog,
    "Sekai.UI.UIPartsThumbnailWithBalloon": decode_thumbnail_with_balloon,
    "Sekai.UIPartsHonorImage": decode_honor_image,
    "Sekai.UIPartsHonorLevel": decode_honor_level,
    "Sekai.UIPartsUniqueHonorLevel": decode_unique_honor_level,
    "Sekai.Honor.UIPartsLiveMasterHonorLevel": decode_live_master_honor_level,
    "Sekai.RankLive.NumberView": decode_rank_live_number_view,
    "Sekai.Mysekai.MysekaiRefreshBirthdayPlantSubWindowDialog": decode_refresh_birthday_plant_dialog,
    "Sekai.SubWindowComponent": decode_subwindow_component,
    "Sekai.SubWindowSlideAnimation": decode_subwindow_slide_animation,
    "Sekai.SubWindowFadeAnimation": decode_subwindow_fade_animation,
}
