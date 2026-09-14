"""Read the serialized face metrics prefix, without decoding font tables."""
from __future__ import annotations

import math

from ui import talk


def read_tmp_face_info(obj, mono_index: dict) -> dict:
    """Return layout metrics for a supported TMP_FontAsset serialized schema.

    This is a prefix reader, not a complete MonoBehaviour decoder. Glyphs,
    fallback fonts, atlas textures and all trailing tables remain unread.
    """
    if obj.type.name != "MonoBehaviour":
        raise ValueError("face metrics require a TMP_FontAsset MonoBehaviour")
    if talk.resolve_script_class(obj, mono_index) != "TMPro.TMP_FontAsset":
        raise ValueError("face metrics require the TMP_FontAsset class")

    reader = talk.Reader(obj.get_raw_data())
    talk.unpack_mb_prefix(reader)
    reader.i32()  # TMP_Asset.hashCode
    reader.pptr()  # TMP_Asset.material
    reader.i32()  # TMP_Asset.materialHashCode
    version = reader.string()
    if version != "1.1.0":
        raise ValueError(f"unsupported TMP_FontAsset serialized version: {version!r}")
    reader.string()  # m_SourceFontFileGUID
    reader.pptr()  # m_SourceFontFile
    reader.i32()  # m_AtlasPopulationMode
    reader.i32()  # FaceInfo.m_FaceIndex
    reader.string()  # FaceInfo.m_FamilyName
    reader.string()  # FaceInfo.m_StyleName
    point_size = reader.i32()
    scale = reader.f32()
    reader.i32()  # FaceInfo.m_UnitsPerEM
    line_height = reader.f32()
    ascent_line = reader.f32()
    reader.f32()  # FaceInfo.m_CapLine
    reader.f32()  # FaceInfo.m_MeanLine
    reader.f32()  # FaceInfo.m_Baseline
    descent_line = reader.f32()
    if point_size <= 0 or not all(math.isfinite(value) for value in (
        scale, line_height, ascent_line, descent_line,
    )):
        raise ValueError("invalid TMP_FontAsset face metrics")
    return {
        "pointSize": point_size,
        "scale": scale,
        "ascentLine": ascent_line,
        "descentLine": descent_line,
        "lineHeight": line_height,
        "source": {"file": obj.assets_file.name, "pathId": obj.path_id},
    }
