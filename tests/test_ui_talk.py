# tests/test_ui_talk.py -- UI 手解器的 synthetic 测试
"""全部用合成字节，不碰任何私有资产。

每个「植入违规」测试都自带一份**改坏了的**读法或索引（不是「少做一步」），
并断言正确读法与坏读法给出不同结果——坏读法若被当成生产实现，对应测试立刻转红。
判据本体（c1..c6）在 local-data/ui/verify.py。
"""

import os
import struct
import sys
import warnings

import pytest

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ui.talk as talk  # noqa: E402


# ---------------------------------------------------------------------------
# 合成字节工具
# ---------------------------------------------------------------------------


def i32(v):
    return struct.pack("<i", v)


def u32(v):
    return struct.pack("<I", v)


def f32(v):
    return struct.pack("<f", v)


def b4(v):
    return struct.pack("<I", 1 if v else 0)


def pptr(fid, pid):
    return struct.pack("<iq", fid, pid)


def ustring(s):
    raw = s.encode("utf-8")
    return i32(len(raw)) + raw + b"\x00" * ((-len(raw)) % 4)


def mb_prefix(go_pid=101, script=(1, 2241)):
    return pptr(0, go_pid) + b4(True) + pptr(*script) + i32(0)


class FakeExternal:
    def __init__(self, name):
        self.name = name


class FakeAssetsFile:
    def __init__(self, name, externals=()):
        self.name = name
        self.externals = [FakeExternal(n) for n in externals]


class FakeType:
    def __init__(self, name):
        self.name = name


class FakeObject:
    """够 resolve_script_class / decode_object 用的最小对象壳。"""

    def __init__(self, raw, file_name, externals=(), type_name="MonoBehaviour", path_id=1, typetree=None):
        self._raw = raw
        self.assets_file = FakeAssetsFile(file_name, externals)
        self.type = FakeType(type_name)
        self.path_id = path_id
        self._typetree = typetree

    def get_raw_data(self):
        return self._raw

    def read_typetree(self):
        if self._typetree is None:
            raise ValueError("no typetree")
        return self._typetree


class FakeEnv:
    def __init__(self, objects):
        self.objects = objects


def mono_script(file_name, path_id, namespace, class_name):
    return FakeObject(b"", file_name, type_name="MonoScript", path_id=path_id,
                      typetree={"m_ClassName": class_name, "m_Namespace": namespace})


# ---------------------------------------------------------------------------
# 植入违规 1：单键索引致类名误判
# ---------------------------------------------------------------------------


def test_planted_single_key_index_misjudges_class():
    """两个 SerializedFile 各有一个 path_id 2241 的 MonoScript，类名不同。

    建索引 + 定类走一条链：正确读法（二元键）跟着 m_Script 的 fileID 走；
    索引一旦被改坏成单键，同一个对象会被**确信地**判成另一个文件里那个类。
    坏索引是「把文件名这一维拿掉」造出来的，不是少跑哪一步。
    """
    env = FakeEnv([
        mono_script("globalgamemanagers.assets", 2241, "Sekai", "TweenScale"),
        mono_script("resources.assets", 2241, "RedBlueGames.StyleCopIgnoreUtility",
                    "StyleCopIgnoreUtilityData"),
    ])
    index = talk.build_monoscript_index(env)
    assert len(index) == 2, "二元键必须留下两条；塌成一条就是撞车"

    # fileID=1 -> externals[0] = globalgamemanagers.assets
    obj = FakeObject(mb_prefix(script=(1, 2241)) + f32(0.0),
                     "resources.assets", externals=["globalgamemanagers.assets"])
    assert talk.resolve_script_class(obj, index) == "Sekai.TweenScale"

    # fileID=0 的同文件对象必须落到另一个类
    same_file = FakeObject(mb_prefix(script=(0, 2241)) + f32(0.0), "resources.assets")
    resolved_same_file = talk.resolve_script_class(same_file, index)
    assert resolved_same_file == "RedBlueGames.StyleCopIgnoreUtility.StyleCopIgnoreUtilityData"
    assert resolved_same_file != talk.resolve_script_class(obj, index)

    # 改坏：抹掉文件名这一维，后写的覆盖先写的 —— 两个对象被判成同一个类
    broken = {}
    for (_fname, pid), cls in index.items():
        broken[pid] = cls
    assert len(broken) == 1
    misjudged = [o for o in (obj, same_file)
                 if broken.get(2241) != talk.resolve_script_class(o, index)]
    assert len(misjudged) == 1, "单键必须至少把其中一个对象判错"


def test_external_file_id_is_one_based():
    """m_Script 的 fileID 指向 externals[fileID - 1]；错成 externals[fileID] 会串到别的文件。"""
    index = {
        ("globalgamemanagers.assets", 7): "Sekai.TalkWindow",
        ("sharedassets0.assets", 7): "Sekai.SomethingElse",
    }
    obj = FakeObject(mb_prefix(script=(1, 7)), "resources.assets",
                     externals=["globalgamemanagers.assets", "sharedassets0.assets"])
    assert talk.resolve_script_class(obj, index) == "Sekai.TalkWindow"

    # fileID 越界必须报 "?"，不许回落到第一个 external
    out_of_range = FakeObject(mb_prefix(script=(9, 7)), "resources.assets",
                              externals=["globalgamemanagers.assets"])
    assert talk.resolve_script_class(out_of_range, index) == "?"


# ---------------------------------------------------------------------------
# 植入违规 2：字段按字母序而非声明序
# ---------------------------------------------------------------------------


def _gradient_alpha_bytes():
    return (f32(0.6) + f32(0.0) + f32(1.0) + f32(0.25)
            + f32(-0.5) + f32(0.125) + b4(True))


def _decode_gradient_alpha_alphabetical(r):
    """改坏版：同一批字段按字母序读。宽度全一致，所以残余照样是 0——这正是 c2 挡不住的错法。"""
    d = {}
    for name in ["m_alphaBottom", "m_alphaLeft", "m_alphaRight", "m_alphaTop",
                 "m_gradientOffsetHorizontal", "m_gradientOffsetVertical"]:
        d[name] = r.f32()
    d["m_splitTextGradient"] = r.bool4()
    return d


def test_planted_alphabetical_field_order_passes_residual_but_gives_wrong_values():
    raw = _gradient_alpha_bytes()

    good = talk.Reader(raw)
    got = talk.decode_gradientalpha(good)
    assert good.expect_end() == 0
    assert got["m_alphaTop"] == pytest.approx(0.6)
    assert got["m_alphaBottom"] == pytest.approx(0.0)
    assert got["m_alphaLeft"] == pytest.approx(1.0)
    assert got["m_alphaRight"] == pytest.approx(0.25)
    assert got["m_gradientOffsetVertical"] == pytest.approx(-0.5)
    assert got["m_gradientOffsetHorizontal"] == pytest.approx(0.125)

    bad = talk.Reader(raw)
    wrong = _decode_gradient_alpha_alphabetical(bad)
    # 关键：坏读法的残余也是 0，「解完剩 0」根本没报警
    assert bad.expect_end() == 0
    # 但每一个值都落错了字段
    assert wrong["m_alphaBottom"] == pytest.approx(0.6)
    assert wrong["m_alphaTop"] == pytest.approx(0.25)
    assert wrong != got


# ---------------------------------------------------------------------------
# 植入违规 3：某字段宽度写错致残余非 0
# ---------------------------------------------------------------------------


def _vertical_layout_group_bytes():
    return (f32(0) * 1 + f32(0) + f32(0) + f32(0)            # m_Padding
            + i32(7)                                          # m_ChildAlignment
            + f32(0.0)                                        # m_Spacing
            + b4(False) + b4(False) + b4(True) + b4(True)
            + b4(False) + b4(False) + b4(False))              # 7 个 bool，各占 4 字节


def _decode_vlg_bool_one_byte(r):
    """改坏版：把 bool 当 1 字节读（不补对齐）。7 个 bool 少吃 21 字节 → 残余非 0。"""
    d = {}
    d["m_Padding"] = r.vec4()
    d["m_ChildAlignment"] = r.i32()
    d["m_Spacing"] = r.f32()
    for name in ["m_ChildForceExpandWidth", "m_ChildForceExpandHeight",
                 "m_ChildControlWidth", "m_ChildControlHeight",
                 "m_ChildScaleWidth", "m_ChildScaleHeight", "m_ReverseArrangement"]:
        d[name] = r.u8() != 0
    return d


def test_planted_wrong_bool_width_leaves_nonzero_residual():
    raw = _vertical_layout_group_bytes()
    assert len(raw) == 52

    good = talk.Reader(raw)
    got = talk.decode_verticallayoutgroup(good)
    assert good.remaining() == 0
    assert good.expect_end() == 0
    assert got["m_ChildAlignment"] == 7
    assert got["m_ChildControlWidth"] is True
    assert got["m_ChildScaleWidth"] is False

    bad = talk.Reader(raw)
    _decode_vlg_bool_one_byte(bad)
    assert bad.remaining() == 21
    with pytest.raises(ValueError, match="21"):
        bad.expect_end()


# ---------------------------------------------------------------------------
# 其余 synthetic 测试
# ---------------------------------------------------------------------------


def test_string_is_padded_to_four_bytes():
    r = talk.Reader(ustring("WORD_A") + i32(0x5A5A))
    assert r.string() == "WORD_A"
    assert r.tell() == 12  # 4 + 6 + 2 pad
    assert r.i32() == 0x5A5A
    assert r.expect_end() == 0


def test_persistent_call_needs_both_assembly_qualified_names():
    """m_TargetAssemblyTypeName 与 m_ObjectArgumentAssemblyTypeName 各自紧跟被限定项。

    改坏版拿掉前者（旧写法），同一段字节就解不完 / 解错位。
    """
    call = (pptr(0, 511081)
            + ustring("Sekai.TalkWindow, Assembly-CSharp")
            + ustring("OnClick")
            + i32(1)
            + pptr(0, 0)
            + ustring("UnityEngine.Object, UnityEngine")
            + i32(0) + f32(0.0) + ustring("") + b4(False) + i32(2))
    raw = i32(1) + call

    r = talk.Reader(raw)
    ev = talk.decode_unity_event(r)
    assert r.expect_end() == 0
    assert ev["m_CallsCount"] == 1
    one = ev["m_Calls"][0]
    assert one["m_Target"] == (0, 511081)
    assert one["m_TargetAssemblyTypeName"] == "Sekai.TalkWindow, Assembly-CSharp"
    assert one["m_MethodName"] == "OnClick"
    assert one["m_CallState"] == 2

    def broken(r):
        n = r.i32()
        out = []
        for _ in range(n):
            c = {"m_Target": r.pptr(), "m_MethodName": r.string(),
                 "m_ObjectArgumentAssemblyTypeName": r.string(), "m_Mode": r.i32(),
                 "m_ObjectArgument": r.pptr(), "m_IntArgument": r.i32(),
                 "m_FloatArgument": r.f32(), "m_StringArgument": r.string(),
                 "m_BoolArgument": r.bool4(), "m_CallState": r.i32()}
            out.append(c)
        return out

    bad = talk.Reader(raw)
    with pytest.raises((struct.error, ValueError, UnicodeDecodeError)):
        broken(bad)
        bad.expect_end()


def test_custom_text_trailing_enum_is_load_bearing():
    """CustomText 尾部那个 4 字节枚举拿掉就解不平——它不是可选的填充。"""
    body = (pptr(0, 0) + f32(0.33) * 1 + f32(0.33) + f32(0.47) + f32(1.0)   # m_Material + m_Color
            + b4(False) + f32(0) + f32(0) + f32(0) + f32(0)                  # RaycastTarget + Padding
            + b4(True) + i32(0)                                             # Maskable + OnCullStateChanged
            + pptr(0, 0) + i32(32) + i32(0) + b4(False) + i32(10) + i32(40)
            + i32(4) + b4(False) + b4(True) + i32(0) + i32(1) + f32(0.6)    # FontData
            + ustring(""))                                                  # m_Text
    tail_ok = body + b4(False) + ustring("WORD_X") + i32(1)
    r = talk.Reader(tail_ok)
    got = talk.decode_customtext(r)
    assert r.expect_end() == 0
    assert got["m_FontData"]["m_FontSize"] == 32
    assert got["m_FontData"]["m_Alignment"] == 4
    assert got["wordingKey"] == "WORD_X"
    assert got["FontType"] == 1

    # 改坏数据：把尾部枚举那 4 字节拿掉
    truncated = tail_ok[:-4]
    r2 = talk.Reader(truncated)
    with pytest.raises(struct.error):
        talk.decode_customtext(r2)


def test_positive_control_catches_what_residual_zero_cannot():
    """阳性对照：残余 0 但字段错位的读法，必须被逐字段比对抓住。"""
    raw = (pptr(0, 66509) + f32(0) + f32(0) + f32(0) + f32(1)
           + f32(1) + f32(2) + f32(3)
           + f32(1) + f32(1) + f32(1)
           + i32(0)
           + pptr(0, 377892)
           + f32(0) + f32(0) + f32(1) + f32(1)
           + f32(4) + f32(5) + f32(6) + f32(7)
           + f32(0.5) + f32(0.5))
    typetree = {
        "m_GameObject": {"m_FileID": 0, "m_PathID": 66509},
        "m_LocalRotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        "m_LocalPosition": {"x": 1.0, "y": 2.0, "z": 3.0},
        "m_LocalScale": {"x": 1.0, "y": 1.0, "z": 1.0},
        "m_Children": [],
        "m_Father": {"m_FileID": 0, "m_PathID": 377892},
        "m_AnchorMin": {"x": 0.0, "y": 0.0},
        "m_AnchorMax": {"x": 1.0, "y": 1.0},
        "m_AnchoredPosition": {"x": 4.0, "y": 5.0},
        "m_SizeDelta": {"x": 6.0, "y": 7.0},
        "m_Pivot": {"x": 0.5, "y": 0.5},
    }
    obj = FakeObject(raw, "resources.assets", type_name="RectTransform", path_id=377278, typetree=typetree)
    dec = talk.decode_object(None, obj, {})
    assert dec["residual"] == 0
    same, hand, generic = talk.compare_hand_vs_typetree(obj, dec["fields"])
    assert same and len(hand) == len(generic) == 24

    # 改坏：把 m_AnchoredPosition 与 m_SizeDelta 互换（宽度相同，残余还是 0）
    swapped = dict(dec["fields"])
    swapped["m_AnchoredPosition"], swapped["m_SizeDelta"] = (
        swapped["m_SizeDelta"], swapped["m_AnchoredPosition"])
    still_same, _, _ = talk.compare_hand_vs_typetree(obj, swapped)
    assert still_same is False


def test_a64_float_constants_are_read_out_of_instruction_bytes():
    """两个时长常量不在序列化字节里，只能从指令字节解。合成三条指令验证解码器本身。"""
    # FMOV S1, #3.0  /  FMOV S3, #8.0
    fmov_3 = struct.pack("<I", 0x1E211001)
    fmov_8 = struct.pack("<I", 0x1E241003)
    assert talk.a64_fmov_scalar_immediate(0x1E211001) == pytest.approx(3.0)
    assert talk.a64_fmov_scalar_immediate(0x1E241003) == pytest.approx(8.0)
    # 非 FMOV-imm 指令必须返回 None，不许瞎解
    assert talk.a64_fmov_scalar_immediate(0xD65F03C0) is None

    # MOVZ w8,#0xcccd ; MOVK w8,#0x3e4c,lsl 16 -> 0x3E4CCCCD = 0.2f
    movz = struct.pack("<I", 0x529999A8)
    movk = struct.pack("<I", 0x72A7C988)
    found = talk.a64_float_constants(movz + movk + fmov_3 + fmov_8, 0, 4)
    vals = [round(v, 7) for _kind, _at, v in found]
    assert 0.2 in vals and 3.0 in vals and 8.0 in vals

    # 改坏数据：把 MOVK 那条指令抹掉，0.2 就拼不出来（不许从别处补一个字面量）
    broken = movz + struct.pack("<I", 0xD503201F) + fmov_3 + fmov_8
    vals2 = [round(v, 7) for _kind, _at, v in talk.a64_float_constants(broken, 0, 4)]
    assert 0.2 not in vals2
    assert 3.0 in vals2 and 8.0 in vals2


def test_load_env_refuses_to_guess_a_path():
    with pytest.raises(ValueError):
        talk.load_env(None)
    assert talk.DEFAULT_UNITY3D is None
