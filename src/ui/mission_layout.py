"""Source-serialized decoders of the MySekai mission screen, its dialogs and the
sub-window base components every sub-window dialog shares.

The mission screen layer is ``Resources.Load("Screen/Prefabs/ScreenLayerMysekaiMission")``
(its ScreenLayerData is ``screen/data/screenlayermysekaimission``); its list
and info dialogs and its detail dialog load from ``Dialog/<name>``, as do the
start, complete and character-reward sub-window dialogs.  Each list names its
cell prefab through a copy on its page rather than a prefab root: the detail
dialog's reward list names a copy under its reward block (:data:`PAGE_PREFABS`),
and the screen's page holds its list's cell instance beside the page's own cell
(:data:`POINTER_PAGE_PREFABS`).  The list and info dialogs' cells are Resources
prefabs of their own (``mysekai/prefabs/mission``).

Every decoder follows the declaration order of the managed type; the common
prefab reader requires each to consume its bytes exactly:

* ``Sekai.ScreenLayerMysekaiMission`` derives from ``ScreenLayer`` (JP: only
  ``layerCamera``), then the page list, the tutorial image and the tap cover.
  A page is a plain serializable object with five references.
* ``Sekai.Mysekai.MysekaiMissionView`` holds the list view and the complete
  sheet's cell; ``Sekai.MissionCellView`` the gauge, the progress and mission
  texts, the complete label, the cell background image and the thumbnail root.
* ``Sekai.Mysekai.MysekaiMissionCell`` derives from ``MissionCellBase``, which
  is a ``ListViewItem``: ``sizeX`` and ``sizeY`` (floats), then the mission
  cell view, then the complete label texture and the other-count label.
* ``Sekai.Mysekai.MysekaiMissionSheetCell`` and
  ``Sekai.Mysekai.MysekaiMissionRewardCell`` are ``ListViewItem`` cells too.
* The list, detail and info dialogs derive from ``Common1ButtonDialog`` or
  ``Common2ButtonDialog``; the start, complete and character-reward dialogs
  derive from ``SubWindowDialog``, whose last declared field is the animation,
  so the dialog's own fields follow it.
* ``Sekai.SubWindowComponent`` serializes the window type (an enum),
  the content and back-panel transforms and the close buttons;
  ``Sekai.SubWindowFadeAnimation`` derives from ``SubWindowAnimationBase``,
  which serializes nothing, so its canvas group is its whole chain.  The
  close area of a sub-window is a node holding a ``CustomButton`` and a
  ``CP.ClickDetector``, not a class of its own.
"""
from ui import talk
from ui.window_animation import (
    decode_common1_button_dialog,
    decode_common2_button_dialog,
    decode_subwindow_dialog,
)
from ui.editor_layout import refs

PREFABS = (
    "ScreenLayerMysekaiMission", "MysekaiMissionListDialog", "MysekaiMissionDetailDialog",
    "MysekaiMissionInfoDialog", "MysekaiMissionStartDialog", "MysekaiMissionCompleteDialog",
    "MysekaiCharaRewardSubWindowDialog", "MysekaiMissionScreenCell",
    "MysekaiMissionRewardCell",
)

# Prefab -> (the prefab holding the reference, the holding component's class, its
# field): the field names the prefab's root.  This family reaches both cells
# through page copies instead (PAGE_PREFABS, POINTER_PAGE_PREFABS).
POINTER_PREFABS = {}

# Prefab -> (the holder, its ListView field, the cell's name): the holder's field
# names a cell instance the list places, and the cell prefab is the Resources
# prefab of that cell's own name (the pages hold no copy of it).
PAGE_PREFABS = {
    "MysekaiMissionRewardCell": (
        "MysekaiMissionDetailDialog", "_rewardListView", "MysekaiMissionRewardCell"),
}

# Prefab -> its Resources key: the cell a list fills is the Resources prefab of
# the cell's own name.
PLACEHOLDER_PREFABS = {
    "MysekaiMissionRewardCell": "mysekai/prefabs/mission/mysekaimissionrewardcell",
}

# The Resources folder the family's cell prefabs sit in.
MISSION_PREFAB_ROOT = ("mysekai/prefabs/mission/",)

# The screen layer repeats its page (Page1 and Page2, each with its own mission
# view), and each page's list names the cell under that page.  The page's content
# is the cell's nodes: the reference points into the first page, and the object
# at the same path under the exported page's content root is the cell prefab
# (NormalMissionRoot/MysekaiMissionCell, beside the page's list instance and its
# copy).  Prefab -> (holder, holder class, field, the candidate path).
POINTER_PAGE_PREFABS = {
    "MysekaiMissionScreenCell": (
        "ScreenLayerMysekaiMission", "Sekai.Mysekai.MysekaiMissionView", "_listView",
        "MysekaiMissionCell"),
}


def decode_screen_mission(r):
    """ScreenLayerMysekaiMission : ScreenLayer (JP: layerCamera only)."""
    return {
        "layerCamera": r.pptr(),
        "_pageDataList": [{
            "mysekaiMissionView": r.pptr(), "missionListButton": r.pptr(),
            "bulkReceiveButton": r.pptr(), "rootTransform": r.pptr(),
            "stampRoot": r.pptr(),
        } for _ in range(r.i32())],
        "_tutorialNaviImage": r.pptr(),
        "_tapCoverCollider": r.pptr(),
    }


def decode_mission_view(r):
    """MysekaiMissionView: the mission list and the complete sheet's cell."""
    return refs(r, ("_listView", "_completeCell"))


def decode_mission_cell_view(r):
    """MissionCellView: the progress gauge and texts, the complete label, the
    cell background image and the thumbnail root its rewards are created under."""
    return refs(r, ("progresGauge", "progressText", "missionText", "completeLabelObj",
                    "cellBaceImage", "rewardThumbnailRoot"))


def decode_mission_cell(r):
    """MysekaiMissionCell : MissionCellBase : ListViewItem."""
    d = {"sizeX": r.f32(), "sizeY": r.f32()}
    d.update(refs(r, ("missionCellView", "completeLabelTexture", "_otherLabel")))
    return d


def decode_mission_sheet_cell(r):
    """MysekaiMissionSheetCell : ListViewItem."""
    d = {"sizeX": r.f32(), "sizeY": r.f32()}
    d.update(refs(r, ("_titleText", "_titleBg", "_imageRoot", "_getObject", "_infoButton",
                      "_rewardText", "_otherText")))
    return d


def decode_mission_reward_cell(r):
    """MysekaiMissionRewardCell : ListViewItem."""
    d = {"sizeX": r.f32(), "sizeY": r.f32()}
    d.update(refs(r, ("_rewardNameText", "_imageRoot")))
    return d


def decode_mission_list_dialog(r):
    """MysekaiMissionListDialog : Common1ButtonDialog, then the sheet list."""
    d = decode_common1_button_dialog(r)
    d.update(refs(r, ("_missionListView",)))
    return d


def decode_mission_info_dialog(r):
    """MysekaiMissionInfoDialog : Common1ButtonDialog, then the mission list, the
    sheet title, the complete reward's image root and text and the get object."""
    d = decode_common1_button_dialog(r)
    d.update(refs(r, ("_missionListView", "_sheetTitleText", "_completeMissionImageRoot",
                      "_completeMissionText", "_getObject")))
    return d


def decode_mission_detail_dialog(r):
    """MysekaiMissionDetailDialog : Common2ButtonDialog, then the reward list,
    the progress gauge and texts, the two contents, the reward label, the clear
    object and the list's transform."""
    d = decode_common2_button_dialog(r)
    d.update(refs(r, ("_rewardListView", "_progresGauge", "_progressText", "_missionText",
                      "_infoMissionText", "_buttonOK", "_buttonOKText", "_contentElement",
                      "_progressContent", "_infomationContent", "_rewardLabelBg",
                      "_rewardLabelText", "_missionClearObj", "_listViewRectTransform")))
    return d


def dialog_click_entries(r):
    """The serialized entries of a dialog's own click event (a
    ``ButtonClickedEvent``): the entry count, then each entry's target, method
    name, call state and arguments.  The serialized event is its persistent-call
    list only; the object's end padding follows it."""
    calls = []
    for _ in range(r.i32()):
        calls.append({
            "m_Target": r.pptr(), "m_MethodName": r.string(), "m_Mode": r.i32(),
            "m_IntArgument": r.i32(), "m_FloatArgument": r.f32(),
            "m_StringArgument": r.string(), "m_BoolArgument": r.bool4(),
            "m_ObjectArgument": r.pptr(),
        })
    padding = r.remaining()
    if not 0 <= padding <= 4 or any(r.u8() for _ in range(padding)):
        raise ValueError(f"{padding} bytes after the click event")
    return {"m_Calls": calls}


def subwindow_dialog_tail(r, names):
    """A SubWindowDialog's serialized object (7.0.0): the dialog chain, then the
    subclass's own references, then the subclass's click event (the end of the
    object)."""
    d = decode_subwindow_dialog(r)
    d.update(refs(r, names))
    d["clickEvent"] = dialog_click_entries(r)
    return d


def decode_mission_animation_dialog_681(r):
    """MysekaiMissionStartDialog / MysekaiMissionCompleteDialog : SubWindowDialog
    (6.8.1): the dialog chain, then the animation root's canvas group and the
    skip button, which end the 6.8.1 object; 7.0.0 appends the dialog's click
    event."""
    d = decode_subwindow_dialog(r)
    d.update(refs(r, ("_fxRootCanvasGroup", "_skipButton")))
    return d


def decode_mission_start_dialog(r):
    """MysekaiMissionStartDialog : SubWindowDialog, then the animation root's
    canvas group and the skip button, then the dialog's click event."""
    return subwindow_dialog_tail(r, ("_fxRootCanvasGroup", "_skipButton"))


def decode_mission_complete_dialog(r):
    """MysekaiMissionCompleteDialog : SubWindowDialog, then the animation root's
    canvas group and the skip button, then the dialog's click event."""
    return subwindow_dialog_tail(r, ("_fxRootCanvasGroup", "_skipButton"))


def decode_chara_reward_dialog(r):
    """MysekaiCharaRewardSubWindowDialog : CommonRewardSubWindowDialog, then the
    thumbnail root, the message object and the thumbnail-with-balloon prefab."""
    d = decode_subwindow_dialog(r)
    d.update(refs(r, ("thumbnailRoot", "messageObj", "thumbnailWithBalloonPrefab")))
    return d


def decode_subwindow_component(r):
    """SubWindowComponent: the window type, the content and back-panel
    transforms and the close buttons whose click closes the sub-window."""
    return {
        "windowType": r.i32(),
        "contentTransform": r.pptr(),
        "backPanelTransform": r.pptr(),
        "closeButtons": talk.decode_pptr_list(r),
    }


def decode_subwindow_fade_animation(r):
    """SubWindowFadeAnimation : SubWindowAnimationBase (which serializes
    nothing): the canvas group it fades."""
    return {"canvasGroup": r.pptr()}


DECODERS = {
    "Sekai.ScreenLayerMysekaiMission": decode_screen_mission,
    "Sekai.Mysekai.MysekaiMissionView": decode_mission_view,
    "Sekai.MissionCellView": decode_mission_cell_view,
    "Sekai.Mysekai.MysekaiMissionCell": decode_mission_cell,
    "Sekai.Mysekai.MysekaiMissionSheetCell": decode_mission_sheet_cell,
    "Sekai.Mysekai.MysekaiMissionRewardCell": decode_mission_reward_cell,
    "Sekai.Mysekai.MysekaiMissionListDialog": decode_mission_list_dialog,
    "Sekai.Mysekai.MysekaiMissionInfoDialog": decode_mission_info_dialog,
    "Sekai.Mysekai.MysekaiMissionDetailDialog": decode_mission_detail_dialog,
    "Sekai.Mysekai.MysekaiMissionStartDialog": decode_mission_start_dialog,
    "Sekai.Mysekai.MysekaiMissionCompleteDialog": decode_mission_complete_dialog,
    "Sekai.Mysekai.MysekaiCharaRewardSubWindowDialog": decode_chara_reward_dialog,
    "Sekai.SubWindowComponent": decode_subwindow_component,
    "Sekai.SubWindowFadeAnimation": decode_subwindow_fade_animation,
}
