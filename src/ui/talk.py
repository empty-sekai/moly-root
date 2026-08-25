"""对话 UI / 头顶 HUD 序列化字段手解。

手解无 typetree 的 MonoBehaviour。这些类在游戏容器里没有 typetree，UnityPy 通用读法只能读出
32 字节公共前缀，而对象本体是 68 / 140 / 188 / 216 / 228 字节。
解法：按类字段的**声明顺序**与布局逐字段解字节，用「解完剩余 0 字节」当自证判据。
字段顺序与偏移来自托管类型布局；本模块只实现解字节逻辑，不依赖磁盘上任何专有文件。
"""

from __future__ import annotations

import io
import struct
from typing import Callable, Iterable

import UnityPy
import warnings

warnings.filterwarnings("ignore")

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"

# 调用方必须显式传入资产路径：没有默认值，拿不到时明确报错。
DEFAULT_UNITY3D = None

# ---------------------------------------------------------------------------
# MonoScript 索引：**必须用二元键 (assets_file.name, path_id)**。
# 只用单键 path_id 会撞车（实测撞在 path_id 2241），把**所有**类名判错。
# ---------------------------------------------------------------------------


def build_monoscript_index(env):
    """返回 {(assets_file.name, path_id): 全限定类名}。

    全量扫描所有 SerializedFile 里的 MonoScript 对象，用 read_typetree 读其命名空间与类名。
    """
    index = {}
    for obj in env.objects:
        if obj.type.name != "MonoScript":
            continue
        try:
            tt = obj.read_typetree()
        except Exception:
            continue
        cn = tt.get("m_ClassName", "")
        if not cn:
            continue
        ns = tt.get("m_Namespace", "")
        index[(obj.assets_file.name, obj.path_id)] = ".".join(p for p in (ns, cn) if p)
    return index


def resolve_script_class(obj, mono_index: dict) -> str:
    """从 MonoBehaviour 对象解析脚本类名。

    MonoBehaviour 前 32 字节是 m_GameObject(12) + m_Enabled(4) + m_Script(PPtr 12) + m_Name(4 空)。
    m_Script 的 fileID 指向 `externals[fileID - 1]`（F5-3 已确认 off-by-one 陷阱）；
    fileID==0 表示同文件。
    """
    raw = obj.get_raw_data()
    if len(raw) < 28:
        return "?"
    fid, pid = struct.unpack("<iQ", raw[16:28])
    if fid == 0:
        fname = obj.assets_file.name
    else:
        if not obj.assets_file.externals or fid > len(obj.assets_file.externals) or fid < 1:
            return "?"
        fname = obj.assets_file.externals[fid - 1].name
    return mono_index.get((fname, pid), "?")


# ---------------------------------------------------------------------------
# 字节读取原语（全部按序消费；bool = 4 字节，PPtr = 12 字节，string 带 pad-to-4）
# ---------------------------------------------------------------------------


class Reader:
    def __init__(self, raw: bytes):
        self._b = io.BytesIO(raw)
        self._raw = raw

    def u8(self) -> int:
        return struct.unpack("<B", self._b.read(1))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self._b.read(4))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self._b.read(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self._b.read(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self._b.read(4))[0]

    def bool4(self) -> bool:
        return self.u32() != 0

    def pptr(self):
        return (self.i32(), self.i64())

    def string(self) -> str:
        n = self.i32()
        data = self._b.read(n)
        self._align4()
        return data.decode("utf-8", errors="replace")

    def color(self):
        return [self.f32() for _ in range(4)]

    def vec2(self):
        return [self.f32() for _ in range(2)]

    def vec3(self):
        return [self.f32() for _ in range(3)]

    def vec4(self):
        return [self.f32() for _ in range(4)]

    def _align4(self):
        pad = (-self._b.tell()) % 4
        if pad:
            self._b.read(pad)

    def remaining(self) -> int:
        return len(self._raw) - self._b.tell()

    def tell(self) -> int:
        return self._b.tell()

    def expect_end(self) -> int:
        r = self.remaining()
        if r != 0:
            raise ValueError(f"残余 {r} 字节未解完")
        return 0


# ---------------------------------------------------------------------------
# 资产加载
# ---------------------------------------------------------------------------


def load_env(path=None):
    """按显式路径加载资产；path 为空时明确报错，不静默回落。"""
    if path is None:
        raise ValueError("load_env 需要显式资产路径（无默认值）")
    return UnityPy.load(path)


def objects_by_file(env, fname: str):
    return {o.path_id: o for o in env.objects if o.assets_file.name == fname}


# ---------------------------------------------------------------------------
# 手解骨架：声明「当前类」的序列化字段链（按顺序、只列序列化字段）。
# 每个描述符是 (字段名, reader, 值提取器)。
# ---------------------------------------------------------------------------


def _rd_pptr(name):
    return lambda r, s: (name, s.setdefault(name, r.pptr()))


def unpack_mb_prefix(r: Reader):
    """消费 MonoBehaviour 前缀：m_GameObject(12) + m_Enabled(4) + m_Script(12) + m_Name(string)。

    这些对象 m_Name 恒为空字符串（4 字节即 len=0，无数据无 pad），故前缀恰 32 字节。
    返回 prefix 信息。
    """
    go_fid, go_pid = r.pptr()
    enabled = r.bool4()
    scr_fid, scr_pid = r.pptr()
    name = r.string()
    return {"m_GameObject": (go_fid, go_pid), "m_Enabled": enabled, "m_Script": (scr_fid, scr_pid), "m_Name": name}


def decode_tweenbase(r: Reader):
    """TweenBase 序列化字段（F5-3 §3）。全部 int/float 4B；curve 特殊，onFinished 为空 UnityEvent → 4B。"""
    result = {}
    result["behaviour"] = r.i32()
    # curve: count(int32) + n*Keyframe(7 words) + preWrap(int) + postWrap(int) + 第三个 int
    count = r.i32()
    keys = []
    for _ in range(count):
        keys.append(
            {
                "time": r.f32(),
                "value": r.f32(),
                "inTangent": r.f32(),
                "outTangent": r.f32(),
                "weightedMode": r.i32(),
                "inWeight": r.f32(),
                "outWeight": r.f32(),
            }
        )
    preWrap = r.i32()
    postWrap = r.i32()
    third = r.i32()
    result["curve"] = {"count": count, "keys": keys, "preWrap": preWrap, "postWrap": postWrap, "third": third}
    result["duration"] = r.f32()
    result["delay"] = r.f32()
    result["loopType"] = r.i32()
    result["loopCount"] = r.i32()
    result["componentType"] = r.i32()
    result["direction"] = r.i32()
    result["startTiming"] = r.i32()
    result["reference"] = r.pptr()
    result["dontKillIfDisable"] = r.bool4()
    result["onFinished"] = r.i32()  # 空 UnityEvent 序列化 = m_PersistentCalls 的 count
    result["isReflesh"] = r.bool4()
    result["syncGameTime"] = r.bool4()
    return result


def decode_tweenscale(r: Reader):
    d = decode_tweenbase(r)
    d["from"] = r.vec3()
    d["to"] = r.vec3()
    return d


def decode_tweenalpha(r: Reader):
    d = decode_tweenbase(r)
    d["from"] = r.f32()
    d["to"] = r.f32()
    return d


def decode_tweenposition(r: Reader):
    d = decode_tweenbase(r)
    d["from"] = r.vec3()
    d["to"] = r.vec3()
    return d


def decode_mysekaichatballoon(r: Reader):
    """MysekaiChatBalloon 序列化字段：3 个 PPtr（_chatText/_balloonRect/_tweener）。"""
    d = {}
    d["_chatText"] = r.pptr()
    d["_balloonRect"] = r.pptr()
    d["_tweener"] = r.pptr()
    return d


def decode_tweetheadupdisplay(r: Reader):
    """TweetHeadUpDisplay 序列化字段：9 个 PPtr（F5-3 §1 表）。"""
    d = {}
    d["_canvasGroup"] = r.pptr()
    d["_contentText"] = r.pptr()
    d["_animationRectTransform"] = r.pptr()
    d["_layoutGroup"] = r.pptr()
    d["_customText"] = r.pptr()
    d["_overlayImage1"] = r.pptr()
    d["_overlayImage2"] = r.pptr()
    d["_button"] = r.pptr()
    d["_animationRoot"] = r.pptr()
    return d


def decode_verticallayoutgroup(r: Reader):
    """VerticalLayoutGroup 序列化字段（F5-3 §4）：Padding Vector4 + ChildAlignment int + Spacing float + 7 bool。"""
    d = {}
    d["m_Padding"] = r.vec4()
    d["m_ChildAlignment"] = r.i32()
    d["m_Spacing"] = r.f32()
    d["m_ChildForceExpandWidth"] = r.bool4()
    d["m_ChildForceExpandHeight"] = r.bool4()
    d["m_ChildControlWidth"] = r.bool4()
    d["m_ChildControlHeight"] = r.bool4()
    d["m_ChildScaleWidth"] = r.bool4()
    d["m_ChildScaleHeight"] = r.bool4()
    d["m_ReverseArrangement"] = r.bool4()
    return d


def decode_contentsizefitter(r: Reader):
    """ContentSizeFitter 序列化字段：m_HorizontalFit + m_VerticalFit。"""
    d = {}
    d["m_HorizontalFit"] = r.i32()
    d["m_VerticalFit"] = r.i32()
    return d


def decode_layoutgroup(r: Reader):
    """通用 LayoutGroup 基类（HOVLG 之前的部分）：m_Padding + m_ChildAlignment。"""
    d = {}
    d["m_Padding"] = r.vec4()
    d["m_ChildAlignment"] = r.i32()
    return d


def decode_horizontallayoutgroup(r: Reader):
    """HorizontalLayoutGroup 基类 = LayoutGroup + HOVLG（Spacing + 7 bool）。"""
    d = decode_layoutgroup(r)
    d["m_Spacing"] = r.f32()
    d["m_ChildForceExpandWidth"] = r.bool4()
    d["m_ChildForceExpandHeight"] = r.bool4()
    d["m_ChildControlWidth"] = r.bool4()
    d["m_ChildControlHeight"] = r.bool4()
    d["m_ChildScaleWidth"] = r.bool4()
    d["m_ChildScaleHeight"] = r.bool4()
    d["m_ReverseArrangement"] = r.bool4()
    return d


def decode_clickdetector(r: Reader):
    """CP.ClickDetector : Graphic，无自身序列化字段，仅继承 Graphic 基类链（48 字节）。"""
    return decode_graphic_base(r)


def decode_unity_event(r: Reader):
    """UnityEventBase 序列化 = m_PersistentCalls { m_Calls: List<PersistentCall> }。

    空事件 = 4 字节 count=0。PersistentCall 的字段序（声明序）：
    m_Target(PPtr) + m_TargetAssemblyTypeName(string) + m_MethodName(string) + m_Mode(i32)
    + m_Arguments{ m_ObjectArgument(PPtr) + m_ObjectArgumentAssemblyTypeName(string)
      + m_IntArgument(i32) + m_FloatArgument(f32) + m_StringArgument(string) + m_BoolArgument(bool4) }
    + m_CallState(i32)。
    两个程序集限定名字符串各自紧跟它限定的那一项，**不是**并排在前面。
    """
    d = {}
    n = r.i32()
    d["m_CallsCount"] = n
    d["m_Calls"] = []
    for _ in range(n):
        c = {}
        c["m_Target"] = r.pptr()
        c["m_TargetAssemblyTypeName"] = r.string()
        c["m_MethodName"] = r.string()
        c["m_Mode"] = r.i32()
        c["m_ObjectArgument"] = r.pptr()
        c["m_ObjectArgumentAssemblyTypeName"] = r.string()
        c["m_IntArgument"] = r.i32()
        c["m_FloatArgument"] = r.f32()
        c["m_StringArgument"] = r.string()
        c["m_BoolArgument"] = r.bool4()
        c["m_CallState"] = r.i32()
        d["m_Calls"].append(c)
    return d


def decode_selectable_base(r: Reader):
    """UnityEngine.UI.Selectable 序列化字段（SerializedChain 实测 280 字节前段）。
    m_Navigation(56) + m_Transition(4) + m_Colors ColorBlock(88) + m_SpriteState(48) + m_AnimationTriggers(5 strings) + m_Interactable(4) + m_TargetGraphic(12)。"""
    d = {}
    # Navigation
    d["m_Mode"] = r.i32()
    d["m_WrapAround"] = r.bool4()
    d["m_SelectOnUp"] = r.pptr()
    d["m_SelectOnDown"] = r.pptr()
    d["m_SelectOnLeft"] = r.pptr()
    d["m_SelectOnRight"] = r.pptr()
    d["m_Transition"] = r.i32()
    # ColorBlock (5 Colors + 2 floats)
    for nm in ["m_NormalColor", "m_HighlightedColor", "m_PressedColor", "m_SelectedColor", "m_DisabledColor"]:
        d[nm] = r.color()
    d["m_ColorMultiplier"] = r.f32()
    d["m_FadeDuration"] = r.f32()
    # SpriteState (4 PPtr)
    for nm in ["m_HighlightedSprite", "m_PressedSprite", "m_SelectedSprite", "m_DisabledSprite"]:
        d[nm] = r.pptr()
    # AnimationTriggers (5 strings)
    for nm in ["m_NormalTrigger", "m_HighlightedTrigger", "m_PressedTrigger", "m_SelectedTrigger", "m_DisabledTrigger"]:
        d[nm] = r.string()
    d["m_Interactable"] = r.bool4()
    d["m_TargetGraphic"] = r.pptr()
    return d


def decode_pptr_list(r: Reader):
    """List<T>/T[] 的引用元素：int 计数 + 计数个 PPtr。"""
    n = r.i32()
    return [r.pptr() for _ in range(n)]


def decode_button(r: Reader):
    """UnityEngine.UI.Button : Selectable。自身只有 m_OnClick。"""
    d = decode_selectable_base(r)
    d["m_OnClick"] = decode_unity_event(r)
    return d


def decode_custombutton(r: Reader):
    """Sekai.UI.CustomButton : Button : Selectable。

    序列化链 = Selectable 基类 + Button.m_OnClick(ButtonClickedEvent) + 本类 14 个字段（声明序）。
    m_OnClick 不是恒空：带持久调用的按钮要按 UnityEvent 全解，否则残余非 0。
    """
    d = decode_selectable_base(r)
    d["m_OnClick"] = decode_unity_event(r)
    d["se"] = r.i32()
    d["otherSeName"] = r.string()
    d["interval"] = r.i32()
    d["absolutelyPress"] = r.bool4()
    d["enableLongPress"] = r.bool4()
    d["buttonViewInteraction"] = r.pptr()
    d["shape"] = r.i32()
    d["shapeButtonImage"] = r.pptr()
    d["shapeRectDotImage"] = r.pptr()
    d["shapeRectText"] = r.pptr()
    d["disableActionType"] = r.i32()
    d["coverImage"] = r.pptr()
    d["optionalCoverImages"] = decode_pptr_list(r)
    d["pressScale"] = r.f32()
    return d


def decode_talkwindow(r: Reader):
    """Sekai.TalkWindow 序列化字段（stub 0x20-0x94：8 PPtr + bool + float + 5 PPtr + bool + float）。
    characterId / words 无 [SerializeField]，不在序列化链。实测 172 字节 = 96+8+60+8。"""
    d = {}
    for nm in ["windowRectTransform", "nameLabel", "nameOutlineLabel", "wordsLabel", "wordsOutlineLabel", "autoSpriteObject", "endIconObj", "clickCollider"]:
        d[nm] = r.pptr()
    d["isAutoTextMode"] = r.bool4()
    d["wordInterval"] = r.f32()
    d["rootCanvasGroup"] = r.pptr()
    d["autoSignalText"] = r.pptr()
    d["skipSignalText"] = r.pptr()
    d["autoSignalIcon1"] = r.pptr()
    d["autoSignalIcon2"] = r.pptr()
    d["isAutoTextModeEndSign"] = r.bool4()
    d["autoTextNextPageDelay"] = r.f32()
    return d


def decode_graphic_base(r: Reader):
    """Graphic 基类序列化字段（与 CanvasRenderer/Image 共享）：48 字节。
    m_Material(PPtr) + m_Color(Color 16) + m_RaycastTarget(bool 4) + m_RaycastPadding(Vector4 16)。"""
    d = {}
    d["m_Material"] = r.pptr()
    d["m_Color"] = r.color()
    d["m_RaycastTarget"] = r.bool4()
    d["m_RaycastPadding"] = r.vec4()
    return d


def decode_maskable_graphic_base(r: Reader):
    """MaskableGraphic 序列化字段：m_Maskable(bool 4) + m_OnCullStateChanged(空 UnityEvent → count 4)。共 8 字节。"""
    d = {}
    d["m_Maskable"] = r.bool4()
    d["m_OnCullStateChanged"] = r.i32()  # 空 UnityEvent 序列化 = m_PersistentCalls.count
    return d


def decode_image_base(r: Reader):
    """UnityEngine.UI.Image 序列化字段（本 player build 实测 48 字节，无 m_FillOrigin/m_UseSpriteMesh/m_PixelsPerUnit）：
    m_Sprite + m_OverrideSprite + m_Type + m_PreserveAspect + m_FillCenter + m_FillMethod + m_FillAmount + m_FillClockwise。"""
    d = {}
    d["m_Sprite"] = r.pptr()
    d["m_OverrideSprite"] = r.pptr()
    d["m_Type"] = r.i32()
    d["m_PreserveAspect"] = r.bool4()
    d["m_FillCenter"] = r.bool4()
    d["m_FillMethod"] = r.i32()
    d["m_FillAmount"] = r.f32()
    d["m_FillClockwise"] = r.bool4()
    return d


def decode_atlasimage(r: Reader):
    """Sekai.AtlasImage : Image + atlas(PPtr) + spriteName(string) + useSharedSprite(bool 4)。"""
    d = {}
    d.update(decode_graphic_base(r))
    d.update(decode_maskable_graphic_base(r))
    d.update(decode_image_base(r))
    d["atlas"] = r.pptr()
    d["spriteName"] = r.string()
    d["useSharedSprite"] = r.bool4()
    return d


def decode_customimage(r: Reader):
    """Sekai.UI.CustomImage : AtlasImage 无自身序列化字段。"""
    return decode_atlasimage(r)


def decode_image(r: Reader):
    """UnityEngine.UI.Image 基类链：Graphic + MaskableGraphic + Image。"""
    d = {}
    d.update(decode_graphic_base(r))
    d.update(decode_maskable_graphic_base(r))
    d.update(decode_image_base(r))
    return d


def decode_layout_element(r: Reader):
    """UnityEngine.UI.LayoutElement 序列化字段：8 字段。
    m_IgnoreLayout(bool) + m_MinWidth + m_MinHeight + m_PreferredWidth + m_PreferredHeight + m_FlexibleWidth + m_FlexibleHeight + m_LayoutPriority。全 4 字节。"""
    d = {}
    d["m_IgnoreLayout"] = r.bool4()
    d["m_MinWidth"] = r.f32()
    d["m_MinHeight"] = r.f32()
    d["m_PreferredWidth"] = r.f32()
    d["m_PreferredHeight"] = r.f32()
    d["m_FlexibleWidth"] = r.f32()
    d["m_FlexibleHeight"] = r.f32()
    d["m_LayoutPriority"] = r.i32()
    return d


def decode_fontdata(r: Reader):
    """UnityEngine.UI.FontData（Text 的内嵌 [Serializable] 结构）：56 字节 = 1 PPtr + 11 words。"""
    d = {}
    d["m_Font"] = r.pptr()
    d["m_FontSize"] = r.i32()
    d["m_FontStyle"] = r.i32()
    d["m_BestFit"] = r.bool4()
    d["m_MinSize"] = r.i32()
    d["m_MaxSize"] = r.i32()
    d["m_Alignment"] = r.i32()
    d["m_AlignByGeometry"] = r.bool4()
    d["m_RichText"] = r.bool4()
    d["m_HorizontalOverflow"] = r.i32()
    d["m_VerticalOverflow"] = r.i32()
    d["m_LineSpacing"] = r.f32()
    return d


def decode_text_base(r: Reader):
    """UnityEngine.UI.Text : MaskableGraphic : Graphic。链 = 48 + 8 + FontData(56) + m_Text(string)。"""
    d = {}
    d.update(decode_graphic_base(r))
    d.update(decode_maskable_graphic_base(r))
    d["m_FontData"] = decode_fontdata(r)
    d["m_Text"] = r.string()
    return d


def decode_customtext(r: Reader):
    """Sekai.UI.CustomText : Text。

    自身序列化字段（声明序）：useWordingKey(bool) · wordingKey(string) · FontType(enum int)。
    otherFont / formatArgs / isReadConfig 无 [SerializeField]，不入序列化链。
    """
    d = decode_text_base(r)
    d["useWordingKey"] = r.bool4()
    d["wordingKey"] = r.string()
    d["FontType"] = r.i32()
    return d


def decode_tmp_text(r: Reader):
    """TMPro.TMP_Text : MaskableGraphic 的自身序列化字段（声明序，59 个声明里 58 个入链）。

    m_TextPreprocessor 是接口字段，Unity 不序列化接口引用，故不在链上（跳过它才能 0 残余）。
    Color32 序列化为 1 个 uint（4 字节）；VertexGradient = 4 个 Color（64 字节）。
    """
    d = {}
    d["m_text"] = r.string()
    d["m_isRightToLeft"] = r.bool4()
    d["m_fontAsset"] = r.pptr()
    d["m_sharedMaterial"] = r.pptr()
    d["m_fontSharedMaterials"] = decode_pptr_list(r)
    d["m_fontMaterial"] = r.pptr()
    d["m_fontMaterials"] = decode_pptr_list(r)
    d["m_fontColor32"] = r.u32()
    d["m_fontColor"] = r.color()
    d["m_enableVertexGradient"] = r.bool4()
    d["m_colorMode"] = r.i32()
    d["m_fontColorGradient"] = [r.color() for _ in range(4)]
    d["m_fontColorGradientPreset"] = r.pptr()
    d["m_spriteAsset"] = r.pptr()
    d["m_tintAllSprites"] = r.bool4()
    d["m_StyleSheet"] = r.pptr()
    d["m_TextStyleHashCode"] = r.i32()
    d["m_overrideHtmlColors"] = r.bool4()
    d["m_faceColor"] = r.u32()
    d["m_fontSize"] = r.f32()
    d["m_fontSizeBase"] = r.f32()
    d["m_fontWeight"] = r.i32()
    d["m_enableAutoSizing"] = r.bool4()
    d["m_fontSizeMin"] = r.f32()
    d["m_fontSizeMax"] = r.f32()
    d["m_fontStyle"] = r.i32()
    d["m_HorizontalAlignment"] = r.i32()
    d["m_VerticalAlignment"] = r.i32()
    d["m_textAlignment"] = r.i32()
    d["m_characterSpacing"] = r.f32()
    d["m_wordSpacing"] = r.f32()
    d["m_lineSpacing"] = r.f32()
    d["m_lineSpacingMax"] = r.f32()
    d["m_paragraphSpacing"] = r.f32()
    d["m_charWidthMaxAdj"] = r.f32()
    d["m_enableWordWrapping"] = r.bool4()
    d["m_wordWrappingRatios"] = r.f32()
    d["m_overflowMode"] = r.i32()
    d["m_linkedTextComponent"] = r.pptr()
    d["parentLinkedComponent"] = r.pptr()
    d["m_enableKerning"] = r.bool4()
    d["m_enableExtraPadding"] = r.bool4()
    d["checkPaddingRequired"] = r.bool4()
    d["m_isRichText"] = r.bool4()
    d["m_parseCtrlCharacters"] = r.bool4()
    d["m_isOrthographic"] = r.bool4()
    d["m_isCullingEnabled"] = r.bool4()
    d["m_horizontalMapping"] = r.i32()
    d["m_verticalMapping"] = r.i32()
    d["m_uvLineOffset"] = r.f32()
    d["m_geometrySortingOrder"] = r.i32()
    d["m_IsTextObjectScaleStatic"] = r.bool4()
    d["m_VertexBufferAutoSizeReduction"] = r.bool4()
    d["m_useMaxVisibleDescender"] = r.bool4()
    d["m_pageToDisplay"] = r.i32()
    d["m_margin"] = r.vec4()
    d["m_isUsingLegacyAnimationComponent"] = r.bool4()
    d["m_isVolumetricText"] = r.bool4()
    return d


def decode_textmeshprougui(r: Reader):
    """TMPro.TextMeshProUGUI : TMP_Text。自身 3 个字段 = 32 字节。"""
    d = {}
    d.update(decode_graphic_base(r))
    d.update(decode_maskable_graphic_base(r))
    d.update(decode_tmp_text(r))
    d["m_hasFontAssetChanged"] = r.bool4()
    d["m_baseMaterial"] = r.pptr()
    d["m_maskOffset"] = r.vec4()
    return d


def decode_customtextmesh(r: Reader):
    """Sekai.UI.CustomTextMesh : TextMeshProUGUI。

    自身序列化字段（声明序）：useWordingKey(bool) · wordingKey(string) · maxValueUpToAutoSize(uint)。
    lastText / formatArgs / otherFont / isReadConfig 无 [SerializeField]。
    """
    d = decode_textmeshprougui(r)
    d["useWordingKey"] = r.bool4()
    d["wordingKey"] = r.string()
    d["maxValueUpToAutoSize"] = r.u32()
    return d


def decode_gradientalpha(r: Reader):
    """UiEffect.GradientAlpha : BaseMeshEffect。

    BaseMeshEffect.m_Graphic 是 [NonSerialized]，故链上只有本类 6 float + 1 bool = 28 字节。
    """
    d = {}
    d["m_alphaTop"] = r.f32()
    d["m_alphaBottom"] = r.f32()
    d["m_alphaLeft"] = r.f32()
    d["m_alphaRight"] = r.f32()
    d["m_gradientOffsetVertical"] = r.f32()
    d["m_gradientOffsetHorizontal"] = r.f32()
    d["m_splitTextGradient"] = r.bool4()
    return d


def decode_graphic_color_synchronizer(r: Reader):
    """调色板颜色同步器：ColorSynchronizer._entryId(内嵌 string) + ColorSynchronizer<T>._component(PPtr)。

    _entryId 是 [Serializable] 类，唯一序列化字段是 string _value（实测为 36 字符的 GUID 文本）。
    """
    d = {}
    d["_entryId"] = r.string()
    d["_component"] = r.pptr()
    return d


def decode_stringalias(r: Reader, n: int):
    return [r.string() for _ in range(n)]



# 手解器注册表：类名 -> [序列化字段描述符]（按声明序）
_DECODERS = {
    "Sekai.Mysekai.MysekaiChatBalloon": decode_mysekaichatballoon,
    "Sekai.Mysekai.TweetHeadUpDisplay": decode_tweetheadupdisplay,
    "Sekai.TweenScale": decode_tweenscale,
    "Sekai.TweenAlpha": decode_tweenalpha,
    "Sekai.TweenPosition": decode_tweenposition,
    "UnityEngine.UI.VerticalLayoutGroup": decode_verticallayoutgroup,
    "UnityEngine.UI.HorizontalLayoutGroup": decode_horizontallayoutgroup,
    "UnityEngine.UI.ContentSizeFitter": decode_contentsizefitter,
    "UnityEngine.UI.Image": decode_image,
    "Sekai.AtlasImage": decode_atlasimage,
    "Sekai.UI.CustomImage": decode_customimage,
    "UnityEngine.UI.LayoutElement": decode_layout_element,
    "CP.ClickDetector": decode_clickdetector,
    "Sekai.UI.CustomButton": decode_custombutton,
    "UnityEngine.UI.Button": decode_button,
    "UnityEngine.UI.Selectable": decode_selectable_base,
    "Sekai.TalkWindow": decode_talkwindow,
    "UnityEngine.UI.Text": decode_text_base,
    "Sekai.UI.CustomText": decode_customtext,
    "TMPro.TextMeshProUGUI": decode_textmeshprougui,
    "Sekai.UI.CustomTextMesh": decode_customtextmesh,
    "UiEffect.GradientAlpha": decode_gradientalpha,
    "uPalette.Runtime.Core.Synchronizer.Color.GraphicColorSynchronizer": decode_graphic_color_synchronizer,
}


def rect_go_and_children(o):
    """从一个 RectTransform 对象解出 (m_GameObject, 子 RectTransform 的 PPtr 列表)。"""
    r = Reader(o.get_raw_data())
    go = r.pptr()
    r.vec4()  # m_LocalRotation
    r.vec3()  # m_LocalPosition
    r.vec3()  # m_LocalScale
    nch = r.i32()
    ch = [r.pptr() for _ in range(nch)]
    return go, ch


def build_go_components(env, file="resources.assets"):
    """返回 {go_path_id: [组件对象]}（仅 RectTransform / MonoBehaviour，且 m_GameObject.fileID==0）。"""
    gocomp = {}
    for o in env.objects:
        if o.assets_file.name != file:
            continue
        if o.type.name not in ("RectTransform", "MonoBehaviour"):
            continue
        raw = o.get_raw_data()
        if len(raw) < 12:
            continue
        fid, gopid = struct.unpack("<iQ", raw[0:12])
        if fid != 0:
            continue
        gocomp.setdefault(gopid, []).append(o)
    return gocomp


def game_object_components(obs, go_pid, mono_index):
    """从 GameObject 自己的 m_Component 表取全部组件（原生类型也在内，不只 RectTransform/MonoBehaviour）。

    这一步不能只扫 m_GameObject 反查：Canvas / CanvasGroup / CanvasRenderer / Animator 都是原生类型，
    漏了它们「链上无 Canvas」这类判据就变成空判。
    """
    go = obs.get(go_pid)
    if go is None or go.type.name != "GameObject":
        return []
    tt = go.read_typetree()
    comps = []
    for entry in tt.get("m_Component", []):
        ptr = entry.get("component", entry)
        pid = ptr.get("m_PathID")
        c = obs.get(pid)
        if c is None:
            comps.append({"path_id": pid, "type": "?", "class": "?"})
            continue
        cls = resolve_script_class(c, mono_index) if c.type.name == "MonoBehaviour" else "UnityEngine." + c.type.name
        comps.append({"path_id": pid, "type": c.type.name, "class": cls})
    return comps


def _root_rect_of(obs, pid):
    """把「根 GO 的 path_id」归一成「该 GO 上 RectTransform 的 path_id」。"""
    o = obs.get(pid)
    if o is None:
        return None
    if o.type.name == "RectTransform":
        return pid
    if o.type.name != "GameObject":
        return None
    for entry in o.read_typetree().get("m_Component", []):
        ptr = entry.get("component", entry)
        c = obs.get(ptr.get("m_PathID"))
        if c is not None and c.type.name == "RectTransform":
            return c.path_id
    return None


def collect_tree(env, root_rect_pid, mono_index, file="resources.assets"):
    """从一棵窗树根出发，沿 m_Children 收集全部节点。

    root_rect_pid 可以是根 GO 的 path_id，也可以是该 GO 上 RectTransform 的 path_id。
    每个节点含：本节点所属 GO、该 GO 上全部组件（含类名）。返回按拓扑序（先父后子）的节点表。
    """
    obs = {o.path_id: o for o in env.objects if o.assets_file.name == file}
    root_rect_pid = _root_rect_of(obs, root_rect_pid)
    if root_rect_pid is None:
        return []
    nodes = []

    def walk_rect(rect_pid, depth, parent_go):
        o = obs.get(rect_pid)
        if o is None or o.type.name != "RectTransform":
            return
        go, ch = rect_go_and_children(o)
        go_pid = go[1]
        nodes.append(
            {
                "rect_pid": rect_pid,
                "go_pid": go_pid,
                "name": go_name(obs, go_pid),
                "depth": depth,
                "parent_go": parent_go,
                "components": game_object_components(obs, go_pid, mono_index),
            }
        )
        for fid, cpid in ch:
            if fid == 0:
                walk_rect(cpid, depth + 1, go_pid)

    walk_rect(root_rect_pid, 0, None)
    return nodes


def go_name(obs, go_pid):
    o = obs.get(go_pid)
    if o is None or o.type.name != "GameObject":
        return "?"
    return o.read_typetree().get("m_Name", "?")


def rect_father(o):
    """RectTransform 的 m_Father（跳过 m_Children 之后的那个 PPtr）。"""
    r = Reader(o.get_raw_data())
    r.pptr()
    r.vec4()
    r.vec3()
    r.vec3()
    n = r.i32()
    for _ in range(n):
        r.pptr()
    return r.pptr()


def collect_ancestors(env, root_pid, mono_index, file="resources.assets"):
    """从一棵树的根沿 m_Father 向上收集祖先节点（含各自的全部组件），根在前。"""
    obs = {o.path_id: o for o in env.objects if o.assets_file.name == file}
    rect_pid = _root_rect_of(obs, root_pid)
    out = []
    seen = set()
    while rect_pid is not None and rect_pid not in seen:
        seen.add(rect_pid)
        o = obs.get(rect_pid)
        if o is None or o.type.name != "RectTransform":
            break
        go_pid = rect_go_and_children(o)[0][1]
        out.append(
            {
                "rect_pid": rect_pid,
                "go_pid": go_pid,
                "name": go_name(obs, go_pid),
                "components": game_object_components(obs, go_pid, mono_index),
            }
        )
        fid, fpid = rect_father(o)
        rect_pid = fpid if fid == 0 and fpid else None
    return out[1:]


def flatten_typetree(value, prefix=""):
    """把通用读法的嵌套结果压平成 {点分路径: 标量}，用于逐字段比对。

    PPtr 与 Vector/Color 之类的结构一律压到叶子标量，避免「名字不同但值相同」被算作不等。
    """
    out = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(flatten_typetree(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            out.update(flatten_typetree(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out


def compare_hand_vs_typetree(obj, hand_fields):
    """c5 阳性对照：同一手解读法 vs UnityPy 通用读法，逐字段（按压平后的取值序列）比对。

    返回 (是否逐字段相等, 手解叶子值序列, 通用读法叶子值序列)。
    结构名在两条路径下写法不同（手解用列表，通用读法用 x/y/z/w 键），所以比对的是
    **压平后的标量序列**——顺序与取值都必须一致，才算相等。
    """
    generic = flatten_typetree(obj.read_typetree())
    hand = flatten_typetree(hand_fields)
    gv, hv = list(generic.values()), list(hand.values())

    def norm(seq):
        return [round(x, 6) if isinstance(x, float) else x for x in seq]

    return norm(hv) == norm(gv), hv, gv


# ---------------------------------------------------------------------------
# 代码侧常量：有些值（时长、上下限）根本不在序列化字节里，只存在于原生代码的立即数中。
# 下面两个原语把它们从二进制字节解出来，而不是在 Python 里抄一个字面量。
# 调用方负责提供二进制内容与方法入口偏移；本模块不知道任何磁盘路径。
# ---------------------------------------------------------------------------

def a64_fmov_scalar_immediate(word: int):
    """解 A64 `FMOV Sd, #imm`（单精度）的立即数；不是该指令则返回 None。

    单精度立即数展开：sign : NOT(b6) : b6×5 : b5 : b4 : mant(4) : 0×19。
    """
    if (word & 0xFF201FE0) != 0x1E201000 or (word >> 22) & 0x3 != 0:
        return None
    imm8 = (word >> 13) & 0xFF
    sign = (imm8 >> 7) & 1
    b6, b5, b4 = (imm8 >> 6) & 1, (imm8 >> 5) & 1, (imm8 >> 4) & 1
    exp = ((1 - b6) << 7) | (b6 * 0x7C) | (b5 << 1) | b4
    bits = (sign << 31) | (exp << 23) | ((imm8 & 0xF) << 19)
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def a64_adrp_ldr_float(blob: bytes, off: int, va_of_off=lambda x: x, off_of_va=lambda x: x):
    """解 `ADRP xN, page` + `LDR Sd, [xN, #imm]` 组合，读回它加载的 4 字节 float。

    两个映射函数把「文件偏移」与「虚拟地址」互相换算；缺省是恒等（段内 vaddr == offset）。
    不是该组合则返回 None。
    """
    w1, w2 = struct.unpack_from("<I", blob, off)[0], struct.unpack_from("<I", blob, off + 4)[0]
    if (w1 & 0x9F000000) != 0x90000000:  # ADRP
        return None
    if (w2 & 0xFFC00000) != 0xBD400000:  # LDR (imm, 32-bit FP)
        return None
    rd, rn = w1 & 0x1F, (w2 >> 5) & 0x1F
    if rd != rn:
        return None
    imm = (((w1 >> 5) & 0x7FFFF) << 2) | ((w1 >> 29) & 0x3)
    if imm & (1 << 20):
        imm -= 1 << 21
    addr = (va_of_off(off) & ~0xFFF) + (imm << 12) + ((w2 >> 10) & 0xFFF) * 4
    return struct.unpack_from("<f", blob, off_of_va(addr))[0]


def a64_float_constants(blob: bytes, entry_off: int, max_words: int):
    """扫一个方法体，把它加载的单精度常量按出现顺序列出。

    覆盖三条路径：`FMOV Sd,#imm` 立即数、`MOVZ/MOVK` 拼出的 32 位模式、`ADRP+LDR` 字面池。
    """
    out = []
    pend = {}
    for i in range(max_words):
        off = entry_off + i * 4
        w = struct.unpack_from("<I", blob, off)[0]
        v = a64_fmov_scalar_immediate(w)
        if v is not None:
            out.append(("fmov", i * 4, v))
        if (w & 0x7F800000) == 0x52800000:  # MOVZ (32-bit)
            pend[w & 0x1F] = ((w >> 5) & 0xFFFF) << (((w >> 21) & 0x3) * 16)
        elif (w & 0x7F800000) == 0x72800000:  # MOVK (32-bit)
            rd = w & 0x1F
            if rd in pend:
                bits = pend.pop(rd) | (((w >> 5) & 0xFFFF) << (((w >> 21) & 0x3) * 16))
                out.append(("movk", i * 4, struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]))
        try:
            lit = a64_adrp_ldr_float(blob, off)
        except (struct.error, IndexError):
            lit = None
        if lit is not None:
            out.append(("literal", i * 4, lit))
    return out


def decode_object(env, obj, mono_index, allow_partial=False):
    """解一个对象：返回 dict + 语义信息 + 自证判据。"""
    otype = obj.type.name
    raw = obj.get_raw_data()

    # —— c5 阳性对照：原生 typetree 类型，与 UnityPy 通用读法逐字段一致 ——
    if otype == "RectTransform":
        r = Reader(raw)
        t = {}
        t["m_GameObject"] = r.pptr()
        t["m_LocalRotation"] = r.vec4()
        t["m_LocalPosition"] = r.vec3()
        t["m_LocalScale"] = r.vec3()
        nch = r.i32()
        t["m_Children"] = [r.pptr() for _ in range(nch)]
        t["m_Father"] = r.pptr()
        t["m_AnchorMin"] = r.vec2()
        t["m_AnchorMax"] = r.vec2()
        t["m_AnchoredPosition"] = r.vec2()
        t["m_SizeDelta"] = r.vec2()
        t["m_Pivot"] = r.vec2()
        r.expect_end()
        return {
            "type": otype,
            "class": "UnityEngine.RectTransform",
            "raw_len": len(raw),
            "tail_len": len(raw),
            "fields": t,
            "residual": 0,
            "consumed": len(raw),
            "has_typetree": True,
            "hand_decoded": True,
        }

    if otype != "MonoBehaviour":
        # 原生类型：通用读法本来就读得通，不属于手解目标，也不许拿它的字节数充残余账。
        try:
            fields = obj.read_typetree()
            note = ""
        except Exception as exc:  # noqa: BLE001 - 只记不猜
            fields, note = {}, f"通用读法失败：{type(exc).__name__}"
        return {
            "type": otype,
            "class": "UnityEngine." + otype,
            "raw_len": len(raw),
            "tail_len": len(raw),
            "fields": fields,
            "residual": 0,
            "consumed": len(raw),
            "has_typetree": True,
            "hand_decoded": False,
            "note": note or "原生类型，走通用读法",
        }

    cls = resolve_script_class(obj, mono_index)
    dec = _DECODERS.get(cls)
    if dec is None:
        # 未知类形：报告未解（partial），不假装解了。前缀按 m_Name 长度可变。
        r = Reader(raw)
        meta = unpack_mb_prefix(r)
        tail_len = len(raw) - r.tell()
        return {
            "type": "MonoBehaviour",
            "class": cls,
            "raw_len": len(raw),
            "tail_len": tail_len,
            "fields": {},
            "residual": tail_len,
            "consumed": 0,
            "has_typetree": False,
            "hand_decoded": False,
            "meta": meta,
            "note": "无手解器（partial）",
        }

    r = Reader(raw)
    prefix = unpack_mb_prefix(r)
    tail_start = r.tell()
    fields = dec(r)
    tail_consumed = r.tell() - tail_start
    tail_total = len(raw) - tail_start
    # 剩余 0 字节自证
    residual = r.remaining()
    return {
        "type": "MonoBehaviour",
        "class": cls,
        "raw_len": len(raw),
        "tail_len": tail_total,
        "fields": fields,
        "residual": residual,
        "consumed": tail_consumed,
        "has_typetree": False,
        "hand_decoded": True,
        "meta": prefix,
    }
