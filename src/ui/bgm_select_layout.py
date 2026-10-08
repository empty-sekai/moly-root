"""Source-serialized decoders of the MySekai BGM select screen (screen 626).

The screen layer is ``Resources.Load("Screen/Prefabs/ScreenLayerMysekaiBGMSelect")``;
its ``MusicSelect`` holds the list and grid views, and each view instantiates
its cell prefab from a serialized reference.  The change-vocal dialog and the
filter dialog are ``Resources.Load("Dialog/" + dialogType.ToString())``.  The
cell prefabs sit in Resources folders but are reached only through those
references, so they are found by their name in the Resources index (as the
editor's lists are).  Two cells share the file name
``mysekaibgmselectgridcell`` (one under ``musicselect/prefabs/``, one under
``mysekai/prefabs/mysekaibgmselect/``), so this family names the Resources
key of every cell it exports.

The player view's images are not in the prefab: ``MysekaiBGMPlayerView``
loads the download package ``mysekai/ui/mysekai_bgm_select`` and assigns
eight named assets from it (``SetupImages``): two sliced Sprites for the
backgrounds and six textures for the record bases, the set buttons, the vocal
change button and the playing icons.  The export writes exactly those eight
as runtime textures (the two Sprites with their authored metrics), keyed by
``PLAYER_TEXTURE_ALIAS`` + the asset name.

Every decoder follows the declaration order of the JP managed type; the
common prefab reader requires each to consume its bytes exactly.  Public
fields of a MonoBehaviour are serialized like ``[SerializeField]`` ones;
delegates, interfaces and dictionaries are not.  ``DECODERS`` holds the JP
chains; ``CN_DECODERS`` the classes CN serializes differently, and
``JP_DECODERS`` the JP chains of those same classes (``ui.jp_decoders``
applies them on JP runs, over the CN ones):

* ``Sekai.ScreenLayer.isBootDone`` is public on CN, so the screen layer
  serializes it after ``layerCamera``.
* ``MusicSelectListCellContentsBase`` has no ``vocalLockObject`` on CN, and
  ``MusicSelectListCellHighLightContents`` no ``vocalLockButton``.
* ``MusicSelect`` serializes ``bonusEvent`` and ``bonusEventInfo`` after
  ``notFilterResultObject`` on CN, and its list and grid views a ``bonusEvent``
  after their ``listCanvasGroup``.
* ``FreeWordFilterContent`` has a public ``MsgType`` on CN.
"""
import hashlib

from ui import talk
from ui.editor_layout import decode_list_view, refs
from ui.window_animation import decode_subwindow_dialog

PREFABS = (
    "ScreenLayerMysekaiBGMSelect", "MysekaiBGMSelectListCell", "MysekaiBGMSelectGridCell",
    "MysekaiBGMSelectGridFancyCell", "MysekaiBGMSelectChangeVocalCell",
    "MysekaiBGMSelectChangeVocalDialog", "BGMFilterDialog",
)

# The Resources key of each referenced prefab (the dialogs and the screen are
# found under their load roots).
RESOURCE_KEYS = {
    "MysekaiBGMSelectListCell": "musicselect/prefabs/musicvideo/mysekaibgmselectlistcell",
    "MysekaiBGMSelectGridCell": "musicselect/prefabs/mysekaibgmselectgridcell",
    "MysekaiBGMSelectGridFancyCell": "mysekai/prefabs/mysekaibgmselect/mysekaibgmselectgridfancycell",
    "MysekaiBGMSelectChangeVocalCell": "mysekai/prefabs/mysekaibgmselect/mysekaibgmselectchangevocalcell",
}

LOADING = ('Resources.Load("Screen/Prefabs/ScreenLayerMysekaiBGMSelect"); the list and grid '
           'cells are instantiated from the MusicSelect views\' serialized PPtr references; '
           'the change-vocal and filter dialogs are Resources.Load("Dialog/" + '
           'dialogType.ToString())')

RUNTIME_VALUES = (
    "UserMysekaiMusicPlayFixtureSetting (the record set for the site)",
    "UserMysekaiMusicRecord (the records the player owns, their obtainedAt)",
    "the record list cells, built from the music record, music, vocal and soundtrack "
    "masters at runtime",
)

# MysekaiBGMPlayerView.LoadAssetBundle: AssetManager.LoadAssetBundle of this
# package; SetupImages then loads the assets below by name.
PLAYER_PACKAGE = "mysekai__ui__mysekai_bgm_select"
PLAYER_PACKAGE_NAME = "mysekai/ui/mysekai_bgm_select"
PLAYER_TEXTURE_ALIAS = "mysekai-bgm-select/"
# BG_SPRITE_NAME and BG_BOTTOM_SPRITE_NAME: Sprites, drawn Sliced.
PLAYER_SPRITES = ("bg_mysekai_music_player", "bg_mysekai_music_player_bottom")
# RECORD_BASE_TEXTURE_NAME, SET_ON/SET_OFF_BGM_BUTTON_TEXTURE_NAME,
# VOCAL_CHANGE_BUTTON_TEXTURE_NAME, PLAY_OFF/PLAY_ON_TEXTURE_NAME: textures.
PLAYER_TEXTURES = ("bg_base_circle_h240_wh", "btn_bgm_set_on", "btn_bgm_set_off",
                   "btn_mysekai_vocal_change", "icon_mysekai_music_prayer_off",
                   "icon_mysekai_music_prayer_on")


def player_package_textures(package_bytes, out, image_dir):
    """The eight assets SetupImages assigns, from the player view's package.

    Returns ``(textures, sprites, source)`` keyed by alias.  An asset the
    package does not carry by exactly one object of its kind fails the
    export: the view assigns every one of them unconditionally."""
    import UnityPy

    env = UnityPy.load(package_bytes)
    by_kind = {"Sprite": {}, "Texture2D": {}}
    for obj in env.objects:
        if obj.type.name not in by_kind:
            continue
        tree = obj.read_typetree()
        by_kind[obj.type.name].setdefault(tree["m_Name"], []).append((obj, tree))
    textures, sprites = {}, {}
    image_dir.mkdir(parents=True, exist_ok=True)

    def only(kind, name):
        found = by_kind[kind].get(name, [])
        if len(found) != 1:
            raise ValueError(f"{PLAYER_PACKAGE} holds {len(found)} {kind} named {name}")
        return found[0]

    for name in PLAYER_SPRITES:
        obj, tree = only("Sprite", name)
        rect, rd = tree["m_Rect"], tree["m_RD"]
        relative = f"textures/{PLAYER_TEXTURE_ALIAS.rstrip('/')}-{name}.png"
        obj.read().image.save(out / relative)
        alias = PLAYER_TEXTURE_ALIAS + name
        textures[alias] = relative
        sprites[alias] = {
            "name": name, "image": relative, "package": PLAYER_PACKAGE_NAME,
            "pathId": obj.path_id,
            "rect": [rect["x"], rect["y"], rect["width"], rect["height"]],
            "rectSize": [rect["width"], rect["height"]],
            "border": [tree["m_Border"][k] for k in ("x", "y", "z", "w")],
            "pixelsPerUnit": tree["m_PixelsToUnits"],
            "pivot": [tree["m_Pivot"]["x"], tree["m_Pivot"]["y"]],
            "textureRect": [rd["textureRect"][k] for k in ("x", "y", "width", "height")],
            "textureRectOffset": [rd["textureRectOffset"]["x"], rd["textureRectOffset"]["y"]],
            "packingSettingsRaw": rd["settingsRaw"],
            "downscaleMultiplier": rd.get("downscaleMultiplier", 1.0),
        }
    for name in PLAYER_TEXTURES:
        obj, tree = only("Texture2D", name)
        relative = f"textures/{PLAYER_TEXTURE_ALIAS.rstrip('/')}-{name}.png"
        obj.read().image.save(out / relative)
        textures[PLAYER_TEXTURE_ALIAS + name] = relative
    source = {"package": PLAYER_PACKAGE_NAME, "bytes": len(package_bytes),
              "sha256": hashlib.sha256(package_bytes).hexdigest(),
              "sprites": len(PLAYER_SPRITES), "textures": len(PLAYER_TEXTURES)}
    return textures, sprites, source


# ---------------------------------------------------------------------------
# The screen, its view and the player view
# ---------------------------------------------------------------------------

def decode_screen_layer(r):
    """ScreenLayerMysekaiBGMSelect : ScreenLayer (JP: layerCamera only), then _view."""
    return {"layerCamera": r.pptr(), "_view": r.pptr()}


def decode_screen_layer_cn(r):
    """ScreenLayerMysekaiBGMSelect : ScreenLayer (CN: layerCamera and the public isBootDone), then _view."""
    return {"layerCamera": r.pptr(), "isBootDone": r.bool4(), "_view": r.pptr()}


def decode_select_view(r):
    """MysekaiBGMSelectView: eight public references."""
    return refs(r, ("bgmPlayerView", "musicSelect", "categoryTabList", "sortDropDown",
                    "freeWordFilterContent", "filterButton", "filterOnImage", "filterOffImage"))


def decode_player_view(r):
    """MysekaiBGMPlayerView: references, the playing and stopped object arrays,
    the record base image array and the six images SetupImages assigns."""
    d = refs(r, ("_jacketThumbnailImage", "_jacketShadow", "_blurJacket", "_collaborationObj",
                 "_collaborationText", "_musicInfoContent"))
    d["_playingObjects"] = talk.decode_pptr_list(r)
    d["_stoppedObjects"] = talk.decode_pptr_list(r)
    d.update(refs(r, ("_switchPlayStatusButton", "_changeVocalButton", "_recordAnimationTransform",
                     "_systemFixtureIcon")))
    d["_recordBaseImageArray"] = talk.decode_pptr_list(r)
    d.update(refs(r, ("_bgImage", "_bgBottomImage", "_setOnBgmButtonImage", "_setOffBgmButtonImage",
                     "_changeVocalButtonImage", "_playOffImage", "_playOnImage")))
    return d


def decode_music_info_content(r):
    """MusicInfoContent: the title, vocal and artist texts with their marquees."""
    return refs(r, ("musicTitle", "musicTitleMarquee", "musicVocalName", "musicVocalNameMarquee",
                    "_artistName", "_artistNameMarquee"))


def decode_marquee(r):
    """UIPartsMarquee: the two masks, the target, speed and waits, four flags."""
    d = refs(r, ("mask2D", "softMask", "marqueeTarget"))
    d.update(spd=r.f32(), wait_sec=r.f32(), enableMarquee=r.bool4(),
             unconditionalMarquee=r.bool4(), waitBasePosition=r.bool4(),
             defaultFadeIn=r.bool4(), waitBeforeSpd=r.f32())
    return d


def decode_marquee_group(r):
    """UIPartsMarqueeGroup: the marquees and the enable flag."""
    return {"marquees": talk.decode_pptr_list(r), "enableMarquee": r.bool4()}


def decode_thumbnail(r):
    """UIPartsThumbnail."""
    d = refs(r, ("thumbnailImage", "textureLoader", "innerText", "thumbnailCanvasGroup",
                 "disableCover"))
    d["preserveAspect"] = r.bool4()
    d["labelImage"] = r.pptr()
    return d


def decode_blur_jacket(r):
    """UIPartsBlurMusicJacket: the thumbnail and the blur shader."""
    return refs(r, ("thumbnail", "shader"))


def decode_free_word_filter(r):
    """FreeWordFilterContent: the input field, the search icon and the clear
    button (OnFiltered is a delegate, not serialized)."""
    return refs(r, ("inputField", "_searchIconObject", "_clearTextButton"))


def decode_free_word_filter_cn(r):
    """The CN FreeWordFilterContent: its public MsgType follows the three references."""
    d = decode_free_word_filter(r)
    d["MsgType"] = r.i32()
    return d


def decode_cover_tap_effect(r):
    """CoverButtonTapEffect : GraphicButtonTapEffect, then _baseGraphic."""
    d = refs(r, ("_effectGraphic", "_effectGraphicIcon"))
    d.update(_defaultColorPalette=r.i32(), _effectColorPalette=r.i32(),
             _useCustomAlpha=r.bool4(), _customDefaultAlpha=r.f32(),
             _customEffectAlpha=r.f32(), _refreshColorWhenAwake=r.bool4())
    d["_baseGraphic"] = r.pptr()
    return d


def decode_gradient_color(r):
    """UiEffect.GradientColor : BaseMeshEffect (no serialized base fields)."""
    return {"m_colorTop": r.color(), "m_colorBottom": r.color(), "m_colorLeft": r.color(),
            "m_colorRight": r.color(), "m_gradientOffsetVertical": r.f32(),
            "m_gradientOffsetHorizontal": r.f32(), "m_splitTextGradient": r.bool4()}


# ---------------------------------------------------------------------------
# MusicSelect and its views
# ---------------------------------------------------------------------------

MUSIC_SELECT_FIELDS = ("gridView", "listView", "_changeViewButton", "listViewBackGround",
                       "notFilterResultObject")


def decode_music_select(r):
    """MusicSelect: the grid and list views, the view change button, the list
    background, the no-result object, then the two public fields."""
    d = refs(r, MUSIC_SELECT_FIELDS)
    d.update(IsFilterCheckedCell=r.bool4(), ListViewMode=r.i32())
    return d


def decode_music_select_cn(r):
    """The CN MusicSelect: the bonus event group and its text after the no-result object."""
    d = refs(r, MUSIC_SELECT_FIELDS + ("bonusEvent", "bonusEventInfo"))
    d.update(IsFilterCheckedCell=r.bool4(), ListViewMode=r.i32())
    return d


def decode_music_select_list_view(r):
    """MusicSelectListView : RepeatListVew : ListView."""
    d = decode_list_view(r)
    d.update(createViewItemFirstPositionType=r.i32(), dragCellGuideLine=r.pptr())
    d.update(snapSelectedCellSpd=r.f32(), cellFadeAlphaMaterial=r.pptr(),
             cellCoverFadeAlphaMaterial=r.pptr(), cellFadeAlphaHeight=r.f32(),
             cellCoverFadeAlphaHeight=r.f32(), listCanvasGroup=r.pptr())
    return d


def decode_music_select_list_view_cn(r):
    """The CN MusicSelectListView: a bonus event group after the list canvas group."""
    d = decode_music_select_list_view(r)
    d["bonusEvent"] = r.pptr()
    return d


def decode_music_select_grid_view(r):
    """MusicSelectGridView: the cell prefab, the content and the canvas group."""
    return refs(r, ("cell", "content", "listCanvasGroup"))


def decode_music_select_grid_view_cn(r):
    """The CN MusicSelectGridView: a bonus event group after the list canvas group."""
    return refs(r, ("cell", "content", "listCanvasGroup", "bonusEvent"))


def decode_loop_vertical_scroll_rect(r):
    """LoopVerticalScrollRect : LoopScrollRect : LoopScrollRectBase.

    The public ``totalCount`` and ``reverseDirection`` come first (the data,
    prefab and size sources are interfaces, not serialized), then the
    ScrollRect-shaped serialized chain."""
    d = {"totalCount": r.i32(), "reverseDirection": r.bool4(), "m_Content": r.pptr(),
         "m_Horizontal": r.bool4(), "m_Vertical": r.bool4(), "m_MovementType": r.i32(),
         "m_Elasticity": r.f32(), "m_Inertia": r.bool4(), "m_DecelerationRate": r.f32(),
         "m_ScrollSensitivity": r.f32()}
    d.update(refs(r, ("m_Viewport", "m_HorizontalScrollbar", "m_VerticalScrollbar")))
    d.update(m_HorizontalScrollbarVisibility=r.i32(), m_VerticalScrollbarVisibility=r.i32(),
             m_HorizontalScrollbarSpacing=r.f32(), m_VerticalScrollbarSpacing=r.f32(),
             m_OnValueChanged=talk.decode_unity_event(r))
    return d


def decode_change_view_button(r):
    """MusicSelectChangeViewButton: the mode button and the two mode images."""
    return refs(r, ("_modeChangeButton", "_listModeContents", "_gridModeContents"))


def decode_unit_tab_list(r):
    """MusicSelectUnitTabList: its ListView."""
    return {"listView": r.pptr()}


def decode_list_view_item(r):
    """ListViewItem: sizeX, sizeY."""
    return {"sizeX": r.f32(), "sizeY": r.f32()}


def decode_music_select_list_cell(r):
    """MusicSelectListCell : ListViewItem (the two actions are delegates)."""
    d = decode_list_view_item(r)
    d.update(refs(r, ("selfButton", "selfButtonClickDetector", "underLineObject",
                     "highlightContents", "neutralContents")))
    d.update(highlightSizeX=r.f32(), highlightSizeY=r.f32())
    return d


CONTENTS_BASE_FIELDS = (
    "canvasGroup", "contentRectTransform", "title", "musicDifficultyPlayLevel",
    "uiPartsGroupMusicDifficultyClearStatus", "mv3dObj", "mv2dObj", "originalMvObj",
    "fullRangeMusic", "lockObject", "vocalLockObject", "_preliminaryTournamentBadgeObj",
    "checkBox",
)


# CN's contents base declares no vocalLockObject.
CN_CONTENTS_BASE_FIELDS = tuple(name for name in CONTENTS_BASE_FIELDS if name != "vocalLockObject")
HIGHLIGHT_FIELDS = ("jacketThumbnail", "artist", "_vocalNameRectTransform", "vocal", "vocalMarquee",
                    "transitionBalloon", "lockButton", "vocalLockButton")


def _highlight_contents(r, base, fields):
    d = refs(r, base)
    d.update(refs(r, fields))
    d["isSelectOnly"] = r.bool4()
    d.update(refs(r, ("mysekaiBGMSettingObj", "spectrum")))
    return d


def _neutral_contents(r, base):
    d = refs(r, base)
    d["jacketThumbnail"] = r.pptr()
    d["lockObjects"] = talk.decode_pptr_list(r)
    d["mysekaiBGMSettingObj"] = r.pptr()
    return d


def decode_highlight_contents(r):
    """MusicSelectListCellHighLightContents : MusicSelectListCellContentsBase."""
    return _highlight_contents(r, CONTENTS_BASE_FIELDS, HIGHLIGHT_FIELDS)


def decode_highlight_contents_cn(r):
    """The CN MusicSelectListCellHighLightContents: no vocalLockObject, no vocalLockButton."""
    return _highlight_contents(r, CN_CONTENTS_BASE_FIELDS,
                               tuple(name for name in HIGHLIGHT_FIELDS if name != "vocalLockButton"))


def decode_neutral_contents(r):
    """MusicSelectListCellNeutralContents : MusicSelectListCellContentsBase."""
    return _neutral_contents(r, CONTENTS_BASE_FIELDS)


def decode_neutral_contents_cn(r):
    """The CN MusicSelectListCellNeutralContents: its base has no vocalLockObject."""
    return _neutral_contents(r, CN_CONTENTS_BASE_FIELDS)


def decode_music_select_grid_cell(r):
    """MusicSelectGridCell : MusicSelectCellBase (button, isSelectOnly)."""
    d = {"button": r.pptr(), "isSelectOnly": r.bool4()}
    d.update(refs(r, ("selectedFrame", "jacketThumbnail", "uiPartsMusicDifficulty",
                     "uiPartsGroupMusicDifficultyClearStatus", "mv3dObj", "mv2dObj",
                     "originalObj", "fullRangeObj", "lockObject", "vocalLockObject",
                     "_preliminaryTournamentBadgeObj", "contentsCanvasGroup", "_checkBox",
                     "_transitionBalloon", "mysekaiBGMSettingObj", "mysekaiBGMSettingIconObj",
                     "_titleText")))
    return d


# ---------------------------------------------------------------------------
# The change-vocal dialog and the record-list cells of the Mysekai namespace
# ---------------------------------------------------------------------------

def decode_change_vocal_dialog(r):
    """MysekaiBGMSelectChangeVocalDialog : SubWindowDialog, then _listView."""
    d = decode_subwindow_dialog(r)
    d["_listView"] = r.pptr()
    return d


def decode_change_vocal_list_view(r):
    """MysekaiBGMSelectChangeVocalListView : FancyScrollRect<T> : FancyScrollView<T>."""
    return {"cellInterval": r.f32(), "scrollOffset": r.f32(), "loop": r.bool4(),
            "cellContainer": r.pptr(), "reuseCellMarginCount": r.f32(), "paddingHead": r.f32(),
            "paddingTail": r.f32(), "spacing": r.f32(), "_cellPrefab": r.pptr()}


def decode_change_vocal_list_cell(r):
    """MysekaiBGMSelectChangeVocalListCell : FancyScrollRectCell<T> (no
    serialized base fields)."""
    return refs(r, ("_captionText", "_vocalText", "_lockedObject", "_lockButton", "_balloon",
                    "_selectedObject", "_selectedbutton"))


def decode_fancy_scroller(r):
    """FancyScrollView.Scroller : UIBehaviour; ``snap`` is a serializable
    class read inline (Enable, VelocityThreshold, Duration, Easing)."""
    return {"viewport": r.pptr(), "scrollDirection": r.i32(), "movementType": r.i32(),
            "elasticity": r.f32(), "scrollSensitivity": r.f32(), "inertia": r.bool4(),
            "decelerationRate": r.f32(),
            "snap": {"Enable": r.bool4(), "VelocityThreshold": r.f32(), "Duration": r.f32(),
                     "Easing": r.i32()},
            "draggable": r.bool4(), "scrollbar": r.pptr()}


def decode_record_list_cell(r):
    """Sekai.Mysekai.MysekaiBGMSelectGridCell : MysekaiBGMSelectListCellBase<T>."""
    d = refs(r, ("_titleText", "_jacketThumbnailImage", "_playingObject", "_selectedObject",
                 "_selectedButton"))
    d["_emptyJacketImage"] = r.pptr()
    return d


def decode_record_grid_fancy_cell(r):
    """MysekaiBGMSelectGridFancyCell : FancyCell<T, TContext> (no serialized
    base fields)."""
    return {"_gridCells": talk.decode_pptr_list(r)}


def decode_release_conditions_balloon(r):
    """UIPartsReleaseConditionsBalloon : UIPartsCommonBalloon."""
    d = {"isAllowedClose": r.bool4()}
    d.update(refs(r, ("canvasGroup", "mainText", "touchController")))
    d["adjustSize"] = r.bool4()
    d["baseCenterRt"] = r.pptr()
    d["baseSideRts"] = talk.decode_pptr_list(r)
    d["padding"] = [r.i32() for _ in range(4)]
    d["maxSize"] = r.vec2()
    d.update(refs(r, ("textSizeFitter", "textLayoutElement", "transitionButton",
                     "_transitionButtonRt")))
    d["_transitionButtonOffset"] = r.i32()
    return d


def decode_system_fixture_icon(r):
    """SystemFixtureIcon: its texture loader."""
    return {"textureLoader": r.pptr()}


DECODERS = {
    "Sekai.Mysekai.ScreenLayerMysekaiBGMSelect": decode_screen_layer,
    "Sekai.Mysekai.MysekaiBGMSelectView": decode_select_view,
    "Sekai.Mysekai.MysekaiBGMPlayerView": decode_player_view,
    "Sekai.MusicInfoContent": decode_music_info_content,
    "Sekai.UI.UIPartsMarquee": decode_marquee,
    "Sekai.UI.UIPartsMarqueeGroup": decode_marquee_group,
    "Sekai.UIPartsThumbnail": decode_thumbnail,
    "Sekai.UIPartsBlurMusicJacket": decode_blur_jacket,
    "Sekai.FreeWordFilterContent": decode_free_word_filter,
    "Sekai.UI.CoverButtonTapEffect": decode_cover_tap_effect,
    "UiEffect.GradientColor": decode_gradient_color,
    "Sekai.MusicSelect": decode_music_select,
    "Sekai.MusicSelectListView": decode_music_select_list_view,
    "Sekai.UI.MusicSelectGridView": decode_music_select_grid_view,
    "UnityEngine.UI.LoopVerticalScrollRect": decode_loop_vertical_scroll_rect,
    "Sekai.UI.MusicSelectChangeViewButton": decode_change_view_button,
    "Sekai.MusicSelectUnitTabList": decode_unit_tab_list,
    "Sekai.MusicSelectListCell": decode_music_select_list_cell,
    "Sekai.UI.MusicSelectListCellHighLightContents": decode_highlight_contents,
    "Sekai.UI.MusicSelectListCellNeutralContents": decode_neutral_contents,
    "Sekai.UI.MusicSelectGridCell": decode_music_select_grid_cell,
    "Sekai.Mysekai.MysekaiBGMSelectChangeVocalDialog": decode_change_vocal_dialog,
    "Sekai.Mysekai.MysekaiBGMSelectChangeVocalListView": decode_change_vocal_list_view,
    "Sekai.Mysekai.MysekaiBGMSelectChangeVocalListCell": decode_change_vocal_list_cell,
    "FancyScrollView.Scroller": decode_fancy_scroller,
    "Sekai.Mysekai.MysekaiBGMSelectGridCell": decode_record_list_cell,
    "Sekai.Mysekai.MysekaiBGMSelectGridFancyCell": decode_record_grid_fancy_cell,
    "Sekai.UI.UIPartsReleaseConditionsBalloon": decode_release_conditions_balloon,
    "Sekai.Mysekai.SystemFixtureIcon": decode_system_fixture_icon,
}

# The classes CN serializes differently (applied over DECODERS on every run) and
# their JP chains (ui.jp_decoders applies these back on JP runs).
CN_DECODERS = {
    "Sekai.Mysekai.ScreenLayerMysekaiBGMSelect": decode_screen_layer_cn,
    "Sekai.MusicSelect": decode_music_select_cn,
    "Sekai.MusicSelectListView": decode_music_select_list_view_cn,
    "Sekai.UI.MusicSelectGridView": decode_music_select_grid_view_cn,
    "Sekai.FreeWordFilterContent": decode_free_word_filter_cn,
    "Sekai.UI.MusicSelectListCellHighLightContents": decode_highlight_contents_cn,
    "Sekai.UI.MusicSelectListCellNeutralContents": decode_neutral_contents_cn,
}
JP_DECODERS = {name: DECODERS[name] for name in CN_DECODERS}
