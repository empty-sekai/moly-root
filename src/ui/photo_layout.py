"""Serialized photo view references, separate from runtime photo state."""
from ui import editor_layout, talk


VIEW_FIELDS = (
    "_canvasGroup", "_overlayImage", "_frameImage", "_lineCornerImageList",
    "_playerHeadUpDisplay", "_switchEditModeToggle", "_avatarMotionSelector",
    "_photoShotBaseImage", "_photoShotIconImage", "_photoShotButton",
    "_photoShotCoverBaseImage", "_photoShotIconCoverImage", "_photoShotCoverButton",
    "_callCharacterButton", "_callCharacterButtonCover", "_callCharacterButtonIcon",
    "_callCharacterButtonCoverIcon", "_callCharacterButtonGraphics", "_callCharacterButtonTapEffect",
    "_callCharacterWaveParticleRoot", "_iconBaseImageList", "_photoEffectIconImage",
    "_openAvatarMotionSelectorButton", "_showsSelectPhotoEffectDialogButton", "_openPhotoAlbumButton",
    "_dofAdjustSlider", "_tripodIconList", "_photoAlbumIcon", "_remainFilmCount",
    "_remainFilmCountBg", "_dofTextImage", "_remainCountTextImage", "_callCharacterTutorialGuidePrefab",
)
VIEW_ARRAYS = {"_lineCornerImageList", "_callCharacterButtonGraphics", "_iconBaseImageList", "_tripodIconList"}
ANIMATION_FIELDS = (
    "_fxPhotoShotRoot", "_photoShotAnimator", "_photoFramePreview", "_photoCameraImages",
    "_photoFramePartsImage", "_photoFilmImage", "_photoFrameShadowCanvasGroup", "_showPhotoFramePreviewEvent",
)


def references(reader, fields, arrays=()):
    result = {}
    for name in fields:
        if name not in arrays:
            result[name] = reader.pptr()
            continue
        count = reader.i32()
        if count < 0 or count > reader.remaining() // 12:
            raise ValueError('photo reference array over-reads object')
        result[name] = [reader.pptr() for _ in range(count)]
    return result


def decode_view(reader: talk.Reader):
    return references(reader, VIEW_FIELDS, VIEW_ARRAYS)


def decode_animation(reader: talk.Reader):
    return references(reader, ANIMATION_FIELDS, {"_photoCameraImages"})


def decode_presenter(reader: talk.Reader):
    return editor_layout.refs(reader, ("_photoShotView", "_selectFrameView", "_photoSaveAnimation"))


DECODERS = {
    "Sekai.Mysekai.MysekaiPhotoShotView": decode_view,
    "Sekai.Mysekai.MysekaiPhotoShotAnimation": decode_animation,
    "Sekai.Mysekai.ScreenLayerMysekaiPhotoShotPresenter": decode_presenter,
}
