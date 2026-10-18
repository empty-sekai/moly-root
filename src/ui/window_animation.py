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


def decode_common1_button_dialog(r: talk.Reader) -> dict:
    """Common1ButtonDialog : DialogBase: the dialog chain, then the one button
    and the two message slots it shows the message in (one of the two carries
    it depending on the layout)."""
    fields = decode_dialog_base(r)
    fields.update({name: r.pptr() for name in (
        "positiveButton", "positiveButtonLabel", "positiveButtonLabelText",
        "messageBodyText", "messageBodyTextMesh")})
    return fields


def decode_common2_button_dialog(r: talk.Reader) -> dict:
    """Common2ButtonDialog : DialogBase: the dialog chain, then the positive
    button and its label (a CustomTextMesh slot and a CustomText slot, whichever
    of the two the layout uses), the negative button and its two label slots,
    and the message body's two slots.  The click callbacks and the message
    interface are runtime state, serialized after the references."""
    fields = decode_dialog_base(r)
    fields.update({name: r.pptr() for name in (
        "positiveButton", "positiveButtonLabelMesh", "positiveButtonLabel",
        "negativeButton", "negativeButtonLabelMesh", "negativeButtonLabel",
        "messageBodyTextMesh", "messageBodyText")})
    return fields


def decode_ui_parts_dialog_tab(r: talk.Reader) -> dict:
    """UIPartsDialogTab: the background image, the text, the badge object and
    the line object, then whether the tab is on.  The toggle and the
    value-changed callback the tab binds at runtime are not serialized."""
    fields = {name: r.pptr() for name in ("backgroundImage", "text", "badge", "lineObj")}
    fields["isOn"] = r.bool4()
    return fields


def decode_mysekai_recover_boost_stamina_dialog(r: talk.Reader) -> dict:
    """MysekaiRecoverBoostStaminaDialog : Common2ButtonDialog: the dialog
    chain, then the use-or-recover tab group, the recovery-method tab group,
    the item and crystal recovery contents, the live-bonus conversion view and
    the object the method contents hang under.  The player HUD, the finish
    callback and the last chosen tab (local settings) are runtime state."""
    fields = decode_common2_button_dialog(r)
    fields.update({name: r.pptr() for name in (
        "_liveBoostConsumptionOrRecoverTabGroup", "_recoverContentSelectTabGroup",
        "_boostRecoveryMethodItemContent", "_boostRecoveryMethodJewelContent",
        "convertLiveBonusToBoostStaminaView", "_boostRecoveryContentParentObject")})
    return fields


def decode_boost_stamina_recover_confirm_dialog(r: talk.Reader) -> dict:
    """BoostStaminaRecoverConfirmDialog : Common2ButtonDialog: the dialog
    chain, then the live-boost number changer, the four stamina views (the
    normal and boost pools before and after), and the warning messages of the
    same two."""
    fields = decode_common2_button_dialog(r)
    fields.update({name: r.pptr() for name in (
        "liveBoostNumberChanger", "_beforeNormalStaminaView", "_beforeBoostStaminaView",
        "_afterNormalStaminaView", "_afterBoostStaminaView", "_beforeWarningMessage",
        "_afterWarningMessage")})
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


def decode_mysekai_weather_dialog(r: talk.Reader) -> dict:
    """MysekaiWeatherDialog : Common1ButtonDialog: the dialog chain, then the
    two weather cell lists (today, tomorrow), the label the last row's date
    goes into, the time period labels and the highlight node moved onto the
    current one.  The schedules the cells are set up with and which period is
    current are runtime state."""
    fields = decode_common1_button_dialog(r)
    fields.update({"_weatherCellList": talk.decode_pptr_list(r), "_tomorrowLabel": r.pptr(),
                   "_timeLabel": talk.decode_pptr_list(r), "_timeHighlight": r.pptr()})
    return fields


def decode_mysekai_weather_cell_list(r: talk.Reader) -> dict:
    """MysekaiWeatherCellList: the content transform its cells are
    instantiated under and the cell prefab they are instantiated from."""
    return {name: r.pptr() for name in ("_content", "_weatherCellPrefab")}


def decode_mysekai_weather_cell(r: talk.Reader) -> dict:
    """MysekaiWeatherCell: the phenomena thumbnail image, the cover its setup
    opens for a period that has passed and the weather name label."""
    return {name: r.pptr() for name in ("_weatherIcon", "_cover", "_weatherLabel")}


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
    "Sekai.Mysekai.MysekaiWeatherDialog": decode_mysekai_weather_dialog,
    "Sekai.Mysekai.MysekaiWeatherCellList": decode_mysekai_weather_cell_list,
    "Sekai.Mysekai.MysekaiWeatherCell": decode_mysekai_weather_cell,
    "Sekai.Common2ButtonDialog": decode_common2_button_dialog,
    "Sekai.UI.UIPartsDialogTab": decode_ui_parts_dialog_tab,
    "Sekai.Mysekai.MysekaiRecoverBoostStaminaDialog": decode_mysekai_recover_boost_stamina_dialog,
    "Sekai.Mysekai.BoostStaminaRecoverConfirmDialog": decode_boost_stamina_recover_confirm_dialog,
    "Sekai.SubWindowComponent": decode_subwindow_component,
    "Sekai.SubWindowSlideAnimation": decode_subwindow_slide_animation,
    "Sekai.SubWindowFadeAnimation": decode_subwindow_fade_animation,
}
