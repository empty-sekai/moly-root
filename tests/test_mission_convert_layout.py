# tests/test_mission_convert_layout.py -- synthetic tests of the mission,
# sub-window and convert-furniture decoder tables
"""Synthetic bytes only: each chain reads the fields its managed declaration
lists, in declaration order, and stops exactly at the object's end.  The
expected values are the ones written into the synthetic object, so a chain
that consumes the right count in the wrong order fails the value assertions.
"""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ui import convert_layout  # noqa: E402
from ui import jp_decoders  # noqa: E402
from ui import mission_layout  # noqa: E402
from ui import talk  # noqa: E402
from ui import window_animation  # noqa: E402

B = b"\0"  # a zero byte, kept out of the literal text


def i32(value):
    return struct.pack("<i", value)


def f32(value):
    return struct.pack("<f", value)


def b4(value):
    return struct.pack("<I", 1 if value else 0)


def pptr(path_id):
    return struct.pack("<iq", 0, path_id)


def string(text):
    raw = text.encode("utf-8")
    return i32(len(raw)) + raw + (B * 4)[:(-len(raw)) % 4]


def refs(*ids):
    return b"".join(pptr(pid) for pid in ids)


def decode(decoder, raw):
    reader = talk.Reader(raw)
    fields = decoder(reader)
    return fields, reader.remaining()


def dialog_base(header, close, window, open_se):
    return refs(header, close, window) + b4(True) + i32(open_se) + i32(0)


def subwindow(header, close, window, message, component, animation):
    return dialog_base(header, close, window, 2) + refs(message, component, animation)


def click_entries(*targets):
    """The serialized event's persistent-call list, then the object's 4-byte tail."""
    raw = i32(len(targets))
    for target in targets:
        raw += (pptr(target) + string("Close") + i32(1) + i32(0) + f32(0.0)
                + string("") + b4(False) + pptr(0))
    return raw + i32(0)


# -- the mission screen -----------------------------------------------------

def test_mission_screen_reads_the_pages_and_their_references():
    raw = pptr(1) + i32(1) + refs(11, 12, 13, 14, 15) + refs(16, 17)
    fields, remaining = decode(mission_layout.decode_screen_mission, raw)
    assert remaining == 0
    assert fields["layerCamera"] == (0, 1)
    assert fields["_pageDataList"] == [{
        "mysekaiMissionView": (0, 11), "missionListButton": (0, 12),
        "bulkReceiveButton": (0, 13), "rootTransform": (0, 14), "stampRoot": (0, 15),
    }]
    assert fields["_tutorialNaviImage"] == (0, 16)
    assert fields["_tapCoverCollider"] == (0, 17)


def test_mission_view_holds_the_list_and_the_complete_cell():
    assert decode(mission_layout.decode_mission_view, refs(18, 19)) == (
        {"_listView": (0, 18), "_completeCell": (0, 19)}, 0)


def test_mission_cell_is_a_list_view_item_then_the_cell_view():
    fields, remaining = decode(mission_layout.decode_mission_cell,
                               f32(200.0) + f32(96.0) + refs(21, 22, 23))
    assert remaining == 0
    assert fields == {"sizeX": 200.0, "sizeY": 96.0, "missionCellView": (0, 21),
                      "completeLabelTexture": (0, 22), "_otherLabel": (0, 23)}


def test_mission_cell_view_reads_the_gauge_first():
    names = ("progresGauge", "progressText", "missionText", "completeLabelObj",
             "cellBaceImage", "rewardThumbnailRoot")
    fields, remaining = decode(mission_layout.decode_mission_cell_view, refs(*range(24, 30)))
    assert remaining == 0
    assert list(fields) == list(names)
    assert fields["progresGauge"] == (0, 24)
    assert fields["rewardThumbnailRoot"] == (0, 29)


def test_mission_sheet_and_reward_cells_are_list_view_items():
    sheet, remaining = decode(mission_layout.decode_mission_sheet_cell,
                              f32(1.0) + f32(2.0) + refs(*range(31, 38)))
    assert remaining == 0
    assert list(sheet) == ["sizeX", "sizeY", "_titleText", "_titleBg", "_imageRoot",
                           "_getObject", "_infoButton", "_rewardText", "_otherText"]
    reward, remaining = decode(mission_layout.decode_mission_reward_cell,
                               f32(3.0) + f32(4.0) + refs(41, 42))
    assert remaining == 0
    assert reward["_rewardNameText"] == (0, 41) and reward["_imageRoot"] == (0, 42)


# -- the sub-window base and the sub-window dialogs -------------------------

def test_subwindow_component_reads_type_transforms_and_close_buttons():
    raw = i32(0) + refs(51, 52) + i32(2) + refs(53, 54)
    fields, remaining = decode(mission_layout.decode_subwindow_component, raw)
    assert remaining == 0
    assert fields == {"windowType": 0, "contentTransform": (0, 51),
                      "backPanelTransform": (0, 52),
                      "closeButtons": [(0, 53), (0, 54)]}


def test_subwindow_fade_animation_reads_its_canvas_group():
    fields, remaining = decode(mission_layout.decode_subwindow_fade_animation, pptr(61))
    assert remaining == 0
    assert fields == {"canvasGroup": (0, 61)}


@pytest.mark.parametrize("decoder", [mission_layout.decode_mission_start_dialog,
                                     mission_layout.decode_mission_complete_dialog])
def test_start_and_complete_dialogs_follow_the_animation_with_the_click_event(decoder):
    chain = subwindow(71, 72, 73, 74, 75, 76) + refs(77, 78) + click_entries(79)
    fields, remaining = decode(decoder, chain)
    assert remaining == 0
    assert fields["animation"] == (0, 76)
    assert fields["_fxRootCanvasGroup"] == (0, 77) and fields["_skipButton"] == (0, 78)
    calls = fields["clickEvent"]["m_Calls"]
    assert [call["m_Target"] for call in calls] == [(0, 79)]
    assert calls[0]["m_MethodName"] == "Close"


def test_chara_reward_dialog_reads_the_reward_subwindow_fields():
    chain = subwindow(81, 82, 83, 84, 85, 86) + refs(87, 88, 89)
    fields, remaining = decode(mission_layout.decode_chara_reward_dialog, chain)
    assert remaining == 0
    assert fields["messageBodyText"] == (0, 84)
    assert (fields["thumbnailRoot"], fields["messageObj"],
            fields["thumbnailWithBalloonPrefab"]) == ((0, 87), (0, 88), (0, 89))


def test_click_entries_read_the_object_end_padding_and_refuse_more():
    assert mission_layout.dialog_click_entries(
        talk.Reader(i32(0) + i32(0))) == {"m_Calls": []}
    with pytest.raises(ValueError):
        mission_layout.dialog_click_entries(talk.Reader(i32(0) + i32(0) + pptr(1)))


# -- the convert-furniture family -------------------------------------------

def test_convert_screen_reads_layer_camera_then_presenter():
    assert decode(convert_layout.decode_screen_convert, pptr(1) + pptr(2)) == (
        {"layerCamera": (0, 1), "_presenter": (0, 2)}, 0)


def test_slot_cell_gauge_view_reads_the_curve_after_the_two_floats():
    names = ("_slotView", "_emptySlotButton", "_progressText", "_lockedContent",
             "_readyContent", "_settingContent", "_progressContent", "_completeContent",
             "_gaugeAnimation", "_waitEffectRoot", "_progressHighlightEffectRoot",
             "_completeHighlightEffectRoot")
    raw = (refs(*range(91, 103)) + f32(0.5) + f32(0.25)
           + animation_curve((0.0, 0.0, 1.0, 1.0, 0, 0.0, 0.0), pre=2, post=4,
                             rotation=3))
    fields, remaining = decode(convert_layout.decode_slot_cell_gauge_view, raw)
    assert remaining == 0
    assert [fields[name] for name in names] == [(0, pid) for pid in range(91, 103)]
    assert fields["_settingAnimationSeconds"] == 0.5
    assert fields["_receiveAnimationSeconds"] == 0.25
    assert fields["_receiveAnimationCurve"] == {
        "keys": [{"time": 0.0, "value": 0.0, "inTangent": 1.0, "outTangent": 1.0,
                  "weightedMode": 0, "inWeight": 0.0, "outWeight": 0.0}],
        "preWrapMode": 2, "postWrapMode": 4, "rotationOrder": 3}
    # The object ends at the curve; a word left over is not absorbed.
    fields, remaining = decode(convert_layout.decode_slot_cell_gauge_view,
                               raw + i32(0))
    assert remaining == 4


def test_slot_view_ends_after_the_cells_and_the_balloon():
    """The slot cell array follows the four plain references, then the unlock
    balloon; the declaration ends there, so a following reference stays
    unread."""
    raw = refs(131, 132, 133, 134) + i32(2) + refs(136, 137) + refs(135)
    fields, remaining = decode(convert_layout.decode_slot_view, raw)
    assert remaining == 0
    assert [fields[name] for name in ("_fadeCanvasGroup", "_systemFixtureIcon", "_blahImage",
                                      "_animationRoot")] == [(0, pid) for pid in (131, 132, 133, 134)]
    assert fields["_slotCellViews"] == [(0, 136), (0, 137)]
    assert fields["_unlockSlotBalloon"] == (0, 135)
    fields, remaining = decode(convert_layout.decode_slot_view, raw + pptr(138))
    assert remaining == 12
    assert list(fields) == ["_fadeCanvasGroup", "_systemFixtureIcon", "_blahImage",
                            "_animationRoot", "_slotCellViews", "_unlockSlotBalloon"]


def curve_key(time, value, in_tangent, out_tangent, weighted_mode, in_weight, out_weight):
    """One AnimationCurve key's serialized words: four floats, the mode, two weights."""
    return (f32(time) + f32(value) + f32(in_tangent) + f32(out_tangent)
            + i32(weighted_mode) + f32(in_weight) + f32(out_weight))


def animation_curve(*keys, pre=0, post=0, rotation=0):
    """An AnimationCurve: key count, each key's words, the wrap modes, rotation order."""
    return (i32(len(keys)) + b"".join(curve_key(*key) for key in keys)
            + i32(pre) + i32(post) + i32(rotation))


def test_animation_curve_reads_every_key_word_then_both_wrap_modes_and_rotation():
    raw = animation_curve((0.0, 1.0, 0.0, 0.0, 0, 0.0, 0.0),
                          (1.0, 2.0, 3.0, 4.0, 1, 0.5, 0.75), pre=2, post=4,
                          rotation=6)
    fields, remaining = decode(convert_layout.decode_animation_curve, raw)
    assert remaining == 0
    assert fields["keys"][1] == {"time": 1.0, "value": 2.0, "inTangent": 3.0,
                                 "outTangent": 4.0, "weightedMode": 1,
                                 "inWeight": 0.5, "outWeight": 0.75}
    assert (fields["preWrapMode"], fields["postWrapMode"],
            fields["rotationOrder"]) == (2, 4, 6)
    # An empty curve is the count alone, then the three curve-level words.
    assert decode(convert_layout.decode_animation_curve, animation_curve(pre=1)) == (
        {"keys": [], "preWrapMode": 1, "postWrapMode": 0, "rotationOrder": 0}, 0)


def test_fixture_slot_view_reads_the_declared_image_arrays():
    raw = (pptr(105) + i32(2) + refs(110, 111) + i32(1) + pptr(112)
           + i32(3) + refs(113, 114, 115) + refs(116, 117, 118))
    fields, remaining = decode(convert_layout.decode_fixture_slot_view, raw)
    assert remaining == 0
    assert fields["_baseImage"] == (0, 105)
    assert fields["_blahImages"] == [(0, 110), (0, 111)]
    assert fields["_highlightImages"] == [(0, 112)]
    assert fields["_maskImages"] == [(0, 113), (0, 114), (0, 115)]
    assert (fields["_frameImage"], fields["_slotNumberText"], fields["_convertItem"]) == (
        (0, 116), (0, 117), (0, 118))
    # An array count longer than the object is an over-read, not a short read.
    with pytest.raises(struct.error):
        decode(convert_layout.decode_fixture_slot_view, pptr(105) + i32(2) + pptr(110))
    # An empty array is the count alone; the declaration has no size prefix.
    fields, remaining = decode(convert_layout.decode_fixture_slot_view,
                               pptr(105) + i32(0) + i32(0) + i32(0) + refs(116, 117, 118))
    assert remaining == 0
    assert fields["_blahImages"] == [] and fields["_highlightImages"] == [] \
        and fields["_maskImages"] == []


def test_detail_view_reads_the_remaining_count_row_between_references():
    raw = refs(*range(121, 137)) + refs(141, 142) + refs(*range(143, 149))
    fields, remaining = decode(convert_layout.decode_item_select_detail_view, raw)
    assert remaining == 0
    assert fields["_convertStartButton"] == (0, 136)
    assert fields["_remainingCountInfo"] == {"_remainingCountBaseImage": (0, 141),
                                             "_remainingCountText": (0, 142)}
    assert fields["_lockedBalloon"] == (0, 148)


@pytest.mark.parametrize("decoder", [convert_layout.decode_lottery_dialog,
                                     convert_layout.decode_cell_prefab_reference
                                     if hasattr(convert_layout, "decode_cell_prefab_reference")
                                     else convert_layout.decode_lottery_dialog])
def test_lottery_dialogs_add_the_animation_root_to_the_subwindow_chain(decoder):
    chain = subwindow(151, 152, 153, 154, 155, 156) + refs(157)
    fields, remaining = decode(decoder, chain)
    assert remaining == 0
    assert fields["_animationRoot"] == (0, 157)
    assert fields["animation"] == (0, 156)


def test_shorten_dialog_reads_its_click_event_after_the_chain():
    # The common dialog chain first (three references, a flag and two enums, then
    # eight references), then the shorten dialog's own eight references.
    raw = (dialog_base(161, 162, 163, 1) + refs(*range(164, 172)) + refs(*range(173, 181))
           + click_entries(181))
    fields, remaining = decode(convert_layout.decode_convert_shorten_dialog, raw)
    assert remaining == 0
    assert fields["_confirmShortenText"] == (0, 173)
    assert fields["_specifiedCommercialTransactionLawArticleButton"] == (0, 180)
    assert fields["clickEvent"]["m_Calls"][0]["m_Target"] == (0, 181)


def test_convert_dialogs_read_their_inherited_chain_first():
    detail = (dialog_base(171, 172, 173, 0) + refs(174, 175, 176, 177, 178)
              + refs(179, 180, 181))
    fields, remaining = decode(convert_layout.decode_convert_item_detail_dialog, detail)
    assert remaining == 0
    assert fields["closeButton"] == (0, 172)
    assert fields["_descriptionText"] == (0, 181)


# -- registration -----------------------------------------------------------

_CONVERT_DIALOGS = (
    "Sekai.Mysekai.MysekaiConvertItemDetailDialog",
    "Sekai.Mysekai.MysekaiConvertStartConfirmDialog",
    "Sekai.Mysekai.MysekaiConvertFurnitureShortenDialog",
    "Sekai.CrystalShop.ShopConfirmDialogPriceParts",
    "Sekai.Mysekai.MysekaiConvertBlueprintLotterySubWindowDialog",
    "Sekai.Mysekai.MysekaiConvertRecordLotterySubWindowDialog",
)


def test_tables_are_registered_for_both_client_versions():
    for version in jp_decoders.VERSIONS:
        table = jp_decoders.decoders_for(version)
        for name, decoder in mission_layout.DECODERS.items():
            if "Dialog" in name and ("MissionStartDialog" in name
                                     or "MissionCompleteDialog" in name):
                continue  # registered per version below
            assert table[name] is decoder
        for name, decoder in convert_layout.DECODERS.items():
            if name in _CONVERT_DIALOGS:
                continue  # registered per version below
            assert table[name] is decoder


def test_convert_dialogs_are_registered_per_version():
    """The convert dialogs end at their click event only in 7.0.0 files; the
    6.8.1 chain stops after the last serialized field."""
    for name in _CONVERT_DIALOGS:
        assert name not in jp_decoders.decoders_for("6.8.1") or \
            jp_decoders.decoders_for("6.8.1")[name] in (
                convert_layout.decode_shorten_dialog_681,
                convert_layout.decode_convert_item_detail_dialog,
                convert_layout.decode_convert_start_confirm_dialog,
                convert_layout.decode_lottery_dialog,
                convert_layout.decode_price_parts)
        assert jp_decoders.decoders_for("7.0.0")[name] in (
            convert_layout.decode_convert_shorten_dialog,
            convert_layout.decode_convert_item_detail_dialog,
            convert_layout.decode_convert_start_confirm_dialog,
            convert_layout.decode_lottery_dialog,
            convert_layout.decode_price_parts)


def test_sub_window_dialogs_follow_each_version_end():
    """6.8.1 ends the start/complete dialogs at the FX root and the skip button;
    7.0.0 appends the click event.  The shorten dialog is the other way round."""
    tail = subwindow(1, 2, 3, 4, 5, 6) + refs(7, 8)
    for name in ("Sekai.Mysekai.MysekaiMissionStartDialog",
                 "Sekai.Mysekai.MysekaiMissionCompleteDialog"):
        fields, remaining = decode(jp_decoders.decoders_for("6.8.1")[name], tail)
        assert remaining == 0
        assert fields["_skipButton"] == (0, 8)
        fields, remaining = decode(jp_decoders.decoders_for("7.0.0")[name],
                                   tail + click_entries(9))
        assert remaining == 0
        assert fields["clickEvent"]["m_Calls"][0]["m_Target"] == (0, 9)
    shorten = dialog_base(1, 2, 3, 0) + refs(*range(4, 12)) + refs(20, 21, 22, 23, 24, 25, 26, 27)
    name = "Sekai.Mysekai.MysekaiConvertFurnitureShortenDialog"
    fields, remaining = decode(jp_decoders.decoders_for("6.8.1")[name], shorten)
    assert remaining == 0
    assert fields["_toJewelShopButton"] == (0, 26)
    fields, remaining = decode(jp_decoders.decoders_for("7.0.0")[name],
                               shorten + click_entries(28))
    assert remaining == 0
    assert fields["clickEvent"]["m_Calls"][0]["m_Target"] == (0, 28)


def test_undecoded_convert_classes_stay_out_of_the_table():
    table = jp_decoders.decoders_for("7.0.0")
    assert "Sekai.Mysekai.UIWaveGaugeAnimation" not in table


def test_base_dialog_chain_is_the_one_the_mission_dialogs_reuse():
    fields, remaining = decode(window_animation.decode_subwindow_dialog,
                               subwindow(1, 2, 3, 4, 5, 6))
    assert remaining == 0
    assert fields["animation"] == (0, 6)
