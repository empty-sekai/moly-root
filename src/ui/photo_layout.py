"""Serialized photo view references, separate from runtime photo state."""
from ui import editor_layout, talk
from pathlib import Path
import hashlib
import UnityPy

PACKAGE = "mysekai__ui__mysekai_photo_shot"
IMAGES = (
    "overlay_photoshot_screen", "frame_PhotoShot", "line_cornerguide_photoshot",
    "btn_photoshot_gr_h180", "icon_callout_wh", "icon_camera_wh_h80", "btn_circle120_lgr",
    "icon_cameraEffect_h56", "icon_tripod_wh_h44", "icon_photoalbum_wh_h42",
    "txt_photoshot_blur", "txt_photoshot_filmCount",
)


def package_images(data, out):
    """The view loads images by these authored names when it opens."""
    env = UnityPy.load(data)
    sprites = {}
    for obj in env.objects:
        if obj.type.name != 'Sprite':
            continue
        tree = obj.read_typetree()
        # A bundle also includes common-atlas sprite aliases whose atlas data
        # is supplied elsewhere. The photo view's own standalone sprite is
        # the one with a concrete unpacked texture and no atlas binding.
        if tree.get('m_SpriteAtlas', {}).get('m_PathID') or tree['m_RD']['settingsRaw'] & 1:
            continue
        name = tree['m_Name']
        if name in sprites:
            raise ValueError(f'duplicate unpacked photo sprite {name}')
        sprites[name] = obj
    textures = {obj.read_typetree()["m_Name"]: obj for obj in env.objects if obj.type.name == "Texture2D"}
    paths, metadata = {}, {}
    output = Path(out)
    (output / 'textures').mkdir(parents=True, exist_ok=True)
    for name in IMAGES:
        sprite = sprites.get(name)
        obj = sprite or textures.get(name)
        if obj is None:
            raise ValueError(f'photo package is missing authored image {name}')
        relative = f'textures/photo-{name}.png'
        image = obj.read()
        if sprite is not None and not image.m_SpriteAtlas:
            # These unpacked sprites retain a CommonAtlas tag even though the
            # package's atlas is empty. Their own render-data texture is the
            # authored source; do not resolve a tag to an unrelated atlas.
            if image.m_RD.settingsRaw & 1 or not image.m_RD.texture:
                raise ValueError(f'photo sprite has no unpacked texture: {name}')
            image.m_AtlasTags = []
        image.image.save(output / relative)
        paths[name] = relative
        if sprite is not None:
            tree = sprite.read_typetree()
            rect, rd = tree['m_Rect'], tree['m_RD']
            metadata[name] = {
                'name': name, 'image': relative, 'package': PACKAGE, 'pathId': obj.path_id,
                'rect': [rect[k] for k in ('x', 'y', 'width', 'height')],
                'rectSize': [rect['width'], rect['height']],
                'border': [tree['m_Border'][k] for k in ('x', 'y', 'z', 'w')],
                'pixelsPerUnit': tree['m_PixelsToUnits'],
                'pivot': [tree['m_Pivot']['x'], tree['m_Pivot']['y']],
                'textureRect': [rd['textureRect'][k] for k in ('x', 'y', 'width', 'height')],
                'textureRectOffset': [rd['textureRectOffset']['x'], rd['textureRectOffset']['y']],
                'packingSettingsRaw': rd['settingsRaw'],
                'downscaleMultiplier': rd.get('downscaleMultiplier', 1.0),
            }
    return paths, metadata, {'package': PACKAGE, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                             'images': len(paths), 'sprites': len(metadata)}


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
