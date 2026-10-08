"""Byte-level texture contract checks, independent of preview PNG encoding."""
from types import SimpleNamespace
import json

from PIL import Image
import pytest
from UnityPy.enums import TextureFormat

from shaders.textures import TextureLibrary, TexturePayloadError, decode_payload, mip_ranges
from shaders.programs import digest


def test_uncompressed_mips_and_array_layers_preserve_every_source_byte():
    # Two layers, each 2x2 + 1x1. Rows and layers have deliberately different
    # bytes so an image flip, mip omission or layer flattening cannot pass.
    payload = bytes(range(40))
    decoded, planes = decode_payload(payload, format_id=int(TextureFormat.RGBA32),
        format_name="RGBA32", width=2, height=2, layers=2, mip_count=2,
        version=(2022, 3, 62, 2), platform=13)
    assert decoded == payload
    assert [(p["layer"], p["mip"], p["width"], p["height"], p["offset"], p["bytes"])
            for p in planes] == [(0, 0, 2, 2, 0, 16), (0, 1, 1, 1, 16, 4),
                                  (1, 0, 2, 2, 20, 16), (1, 1, 1, 1, 36, 4)]
    assert [p["sha256"] for p in planes] == [digest(payload[a:b]) for a, b in
                                             [(0, 16), (16, 20), (20, 36), (36, 40)]]


def test_astc_mip_block_rounding_includes_tiny_levels_and_non_square_images():
    # 13x7 uses 3x2 6x6 blocks; the following mips each still use one block.
    planes = mip_ranges("ASTC_RGBA_6x6", 13, 7, 2, 4, (96 + 16 + 16 + 16) * 2)
    assert [p["encodedBytes"] for p in planes] == [96, 16, 16, 16] * 2
    assert planes[-1]["encodedOffset"] == 272


@pytest.mark.parametrize("args", [
    ("RGBA32", 2, 2, 1, 0, 16), ("RGBA32", 2, 2, 1, 3, 24),
    ("RGBA32", 2, 2, 2, 2, 39), ("RGBA32", 2, 2, 2, 2, 41),
    ("RGBAFloat", 2, 2, 1, 1, 64), ("DXT5Crunched", 2, 2, 1, 1, 16),
])
def test_missing_levels_padding_unknown_formats_and_bad_extents_are_explicit(args):
    with pytest.raises(TexturePayloadError):
        mip_ranges(*args)


def test_decoder_cannot_silently_resize_or_quantize_an_hdr_texture():
    for image in [Image.new("RGBA", (1, 1)), Image.new("F", (2, 2))]:
        with pytest.raises(TexturePayloadError):
            decode_payload(bytes(16), format_id=4, format_name="RGBA32",
                width=2, height=2, layers=1, mip_count=1, version=(2022, 3),
                platform=13, decoder=lambda *args: image)


class Record:
    bundle = "synthetic-texture"
    archive = "synthetic-serialized-file"
    kinds = {7: "Texture2D"}

    def __init__(self, payload):
        self.value = {"m_Name": "not-an-identity", "m_Width": 2, "m_Height": 2,
            "m_MipCount": 2, "m_TextureFormat": 4, "m_ColorSpace": 1,
            "m_ImageCount": 1, "m_CompleteImageSize": 20,
            "m_TextureSettings": {"m_FilterMode": 0, "m_WrapU": 2,
                                  "m_WrapV": 0, "m_WrapW": 1,
                                  "m_Aniso": 1, "m_MipBias": -0.5}}
        texture = SimpleNamespace(m_TextureFormat=4, get_image_data=lambda: payload,
            object_reader=SimpleNamespace(version=(2022, 3, 62, 2), platform=13))
        self.objects = {7: SimpleNamespace(read=lambda: texture)}

    def tree(self, path_id):
        assert path_id == 7
        return self.value


class Store:
    @staticmethod
    def source_receipt(bundle):
        assert bundle == "synthetic-texture"
        return {"sha256": "a" * 64, "bytes": 5}


def test_texture_reference_retains_sampler_source_and_all_encoded_bytes(tmp_path):
    record = Record(bytes(range(20)))
    library = TextureLibrary(Store(), tmp_path / "shader-textures")
    ref = library.reference(record, 7)
    document = json.loads((tmp_path / ref["file"]).read_bytes())
    assert document["status"] == "decoded"
    assert document["sourceTextureSettings"] == record.value["m_TextureSettings"]
    assert document["sourceColorSpace"] == 1
    assert document["source"]["pathId"] == "7"
    assert document["texelOrigin"] == "unity-lower-left"
    assert (tmp_path / document["encoded"]["file"]).read_bytes() == bytes(range(20))
    assert (tmp_path / document["pixels"]["file"]).read_bytes() == bytes(range(20))
    assert digest((tmp_path / ref["file"]).read_bytes()) == ref["sha256"]
    ref["source"]["pathId"] = "changed"
    assert library.reference(record, 7)["source"]["pathId"] == "7"


def test_unreadable_texture_still_has_source_and_encoded_receipt(tmp_path):
    ref = TextureLibrary(Store(), tmp_path / "shader-textures").reference(Record(bytes(17)), 7)
    document = json.loads((tmp_path / ref["file"]).read_bytes())
    assert ref["status"] == "unavailable"
    assert "byte account mismatch" in ref["error"]
    assert (tmp_path / document["encoded"]["file"]).read_bytes() == bytes(17)
    assert "pixels" not in document
