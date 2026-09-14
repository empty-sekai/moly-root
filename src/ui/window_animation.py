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
    "Sekai.SubWindowComponent": decode_subwindow_component,
    "Sekai.SubWindowSlideAnimation": decode_subwindow_slide_animation,
    "Sekai.SubWindowFadeAnimation": decode_subwindow_fade_animation,
}
