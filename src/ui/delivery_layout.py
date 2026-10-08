"""Serialized fields of the MySekai delivery screen and the parts it is built from.

The delivery view serializes six references: the menu content, the gauge
contents, the character cell group, the delivery action button, the
information button and the dash button.  The gauge contents serialize four
references (remaining count text, the gauge, the added point text and the
member bonus balloon) and two floats, ``_timeRateToFadeStart`` and
``_timeRataToFadeDuration`` (spelled as declared).  The cell group serializes
its cell list and the item name text; a cell serializes its button, selected
image, texture loader and count text.  The information and dash buttons derive
from the action button base (type, internal button, icon loader, joystick
reset flag), as does the room's floor selector (three floor buttons, the
Animator that shows it and a fade time); the internal button is a Button with its own sound, interval,
long-press, interaction, cover and press-scale fields.  The delivery action
button is a CustomButton with an icon loader after it, and its press
interaction serializes the pressed image and the particle parent (the
interaction base class serializes nothing).  UIPartsBalloon serializes its
hide-on-awake flag, canvas group, message text and minimum size.

Their runtime state (selected index, view data, tweens, callbacks, tap state,
icon load cancellation) is not serialized.  CustomButton's chain is
region-specific; the JP delivery action button is in ``ui.jp_decoders``.
"""
from __future__ import annotations

from ui import talk


def refs(r: talk.Reader, names) -> dict:
    return {name: r.pptr() for name in names}


def decode_delivery_view(r: talk.Reader) -> dict:
    return refs(r, ("_menuUIContent", "_deliveryGaugeContents", "_characterCellGroup",
                    "_deliveryActionButton", "_informationButton", "_dashButton"))


def decode_delivery_action_button(r: talk.Reader) -> dict:
    """DeliveryActionButton on the older CustomButton chain, then ``_iconLoader``."""
    d = talk.decode_custombutton(r)
    d["_iconLoader"] = r.pptr()
    return d


def decode_delivery_action_button_interaction(r: talk.Reader) -> dict:
    return refs(r, ("_onPressImage", "_particleParent"))


def decode_delivery_gauge_contents(r: talk.Reader) -> dict:
    d = refs(r, ("_remainingCountText", "_deliveryGauge", "_addedDeliveryPoint",
                 "_memberBonusBalloon"))
    d.update(_timeRateToFadeStart=r.f32(), _timeRataToFadeDuration=r.f32())
    return d


def decode_character_cell_group(r: talk.Reader) -> dict:
    return {"_cellList": talk.decode_pptr_list(r), "_itemNameText": r.pptr()}


def decode_character_cell(r: talk.Reader) -> dict:
    return refs(r, ("_cellButton", "_selectedImage", "_textureLoader", "_materialCount"))


def action_button_base_fields(r: talk.Reader) -> dict:
    return {"_type": r.i32(), "_button": r.pptr(), "_iconLoader": r.pptr(),
            "_isResetJoyStick": r.bool4()}


def decode_action_button_base(r: talk.Reader) -> dict:
    return action_button_base_fields(r)


def decode_dash_action_button(r: talk.Reader) -> dict:
    d = action_button_base_fields(r)
    d["_iconImages"] = talk.decode_pptr_list(r)
    d.update(refs(r, ("_canvasGroup", "_activeEffectRoot", "_activeEffectRing",
                      "_iconEffectImage1", "_iconEffectImage2")))
    return d


def decode_my_room_site_selector(r: talk.Reader) -> dict:
    """MyRoomSiteSelector: the action button base, then the three floor buttons,
    the Animator that shows the selector and ``_fadeTime``."""
    d = action_button_base_fields(r)
    d.update(refs(r, ("_goFirstFloorButton", "_goSecondFloorButton", "_goThirdFloorButton",
                      "_showSelectorAnimator")))
    d["_fadeTime"] = r.f32()
    return d


def decode_action_internal_button(r: talk.Reader) -> dict:
    d = talk.decode_button(r)
    d.update(se=r.i32(), otherSeName=r.string(), interval=r.i32(),
             enableLongPress=r.bool4(), buttonViewInteraction=r.pptr(),
             disableActionType=r.i32(), coverImage=r.pptr(),
             optionalCoverImages=talk.decode_pptr_list(r), pressScale=r.f32())
    return d


def decode_parts_balloon(r: talk.Reader) -> dict:
    return {"enableHideOnAwake": r.bool4(), "canvasGroup": r.pptr(), "message": r.pptr(),
            "minSize": r.vec2()}


DECODERS = {
    "Sekai.Mysekai.ScreenLayerMysekaiDeliveryView": decode_delivery_view,
    "Sekai.Mysekai.DeliveryActionButton": decode_delivery_action_button,
    "Sekai.Mysekai.DeliveryActionButtonInteraction": decode_delivery_action_button_interaction,
    "Sekai.Mysekai.MysekaiDeliveryGaugeContents": decode_delivery_gauge_contents,
    "Sekai.Mysekai.MysekaiDeliveryCharacterCellGroup": decode_character_cell_group,
    "Sekai.Mysekai.MysekaiDeliveryCharacterCell": decode_character_cell,
    "Sekai.Mysekai.ActionButton.MysekaiActionButtonBase": decode_action_button_base,
    "Sekai.Mysekai.ActionButton.MysekaiDashActionButton": decode_dash_action_button,
    "Sekai.Mysekai.MyRoomSiteSelector": decode_my_room_site_selector,
    "Sekai.Mysekai.ActionButton.MysekaiActionInternalButton": decode_action_internal_button,
    "Sekai.UIPartsBalloon": decode_parts_balloon,
}
