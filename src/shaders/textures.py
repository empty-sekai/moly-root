"""Source texture payloads for compiled shader consumers.

Preview PNGs are deliberately not the runtime texture contract: they omit mip
levels, sampler settings, texture dimension, transfer metadata and the original
texel origin. This store retains the encoded source payload as well as a fully
byte-accounted, layer-major RGBA8 decoding of every supplied mip. It never
resizes a mip, creates an atlas, generates missing levels or invents a sampler.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.metadata
from pathlib import Path
import re

from .programs import canonical_bytes, digest, object_identity, _publish


class TexturePayloadError(ValueError):
    """Source bytes or a format cannot be represented by this payload decoder."""


def encoded_level_bytes(format_name, width, height):
    """Storage sizes of supported byte-normalized/block-compressed formats.

    The sum is checked against the actual supplied payload, including every
    array layer. HDR, crunch containers and platform-swizzled images are not
    reinterpreted as one of these formats.
    """
    if width < 1 or height < 1:
        raise TexturePayloadError("texture mip dimensions must be positive")
    pixel = {"RGB24": 3, "RGBA32": 4, "ARGB32": 4, "BGRA32": 4}
    if format_name in pixel:
        return width * height * pixel[format_name]
    block_bytes = {"DXT1": 8, "DXT5": 16, "BC7": 16, "ETC_RGB4": 8,
                   "ETC2_RGB": 8, "ETC2_RGBA1": 8, "ETC2_RGBA8": 16}
    if format_name in block_bytes:
        return ((width + 3) // 4) * ((height + 3) // 4) * block_bytes[format_name]
    astc = re.fullmatch(r"ASTC_(?:RGB|RGBA)_(4|5|6|8|10|12)x\1", format_name)
    if astc:
        size = int(astc[1])
        return ((width + size - 1) // size) * ((height + size - 1) // size) * 16
    raise TexturePayloadError(f"source texture format has no loss-qualified decoder: {format_name}")


def mip_ranges(format_name, width, height, layers, mip_count, payload_bytes):
    if min(width, height, layers, mip_count) < 1:
        raise TexturePayloadError("invalid source texture extent or mip count")
    if mip_count > max(width, height).bit_length():
        raise TexturePayloadError("source mip count exceeds the supplied extent")
    planes, offset = [], 0
    for layer in range(layers):
        for mip in range(mip_count):
            w, h = max(1, width >> mip), max(1, height >> mip)
            length = encoded_level_bytes(format_name, w, h)
            planes.append({"layer": layer, "mip": mip, "width": w, "height": h,
                           "encodedOffset": offset, "encodedBytes": length})
            offset += length
    if offset != payload_bytes:
        raise TexturePayloadError(f"texture byte account mismatch: levels require {offset}, source supplies {payload_bytes}")
    return planes


def decode_payload(payload, *, format_id, format_name, width, height, layers,
                   mip_count, version, platform, decoder=None):
    """Decode actual source mip slices without changing their order or origin."""
    planes = mip_ranges(format_name, width, height, layers, mip_count, len(payload))
    if decoder is None:
        from UnityPy.export.Texture2DConverter import parse_image_data
        decoder = parse_image_data
    chunks, cursor = [], 0
    for plane in planes:
        start = plane["encodedOffset"]
        source = payload[start:start + plane["encodedBytes"]]
        image = decoder(source, plane["width"], plane["height"], format_id,
                        version, platform, None, False)
        if image.mode not in ("RGB", "RGBA") or image.size != (plane["width"], plane["height"]):
            raise TexturePayloadError("decoder changed texture extent or requires an unqualified channel conversion")
        decoded = image.convert("RGBA").tobytes()
        expected = plane["width"] * plane["height"] * 4
        if len(decoded) != expected:
            raise TexturePayloadError("decoded RGBA8 byte count mismatch")
        plane.update(offset=cursor, bytes=len(decoded), sha256=digest(decoded))
        chunks.append(decoded)
        cursor += len(decoded)
    return b"".join(chunks), planes


def _array_bytes(texture):
    if texture.image_data is not None:
        return bytes(texture.image_data)
    from UnityPy.helpers.ResourceReader import get_resource_data
    stream = texture.m_StreamData
    return bytes(get_resource_data(stream.path, texture.object_reader.assets_file,
                                   stream.offset, stream.size))


def _array_format(texture):
    # This is the same explicit graphics-format mapping used by UnityPy's
    # Texture2DArray decoder; it is not inferred from an asset's name.
    from UnityPy.classes.legacy_patch.Texture2DArray import GRAPHICS_TO_TEXTURE_MAP
    from UnityPy.enums import GraphicsFormat
    value = GRAPHICS_TO_TEXTURE_MAP.get(GraphicsFormat(texture.m_Format))
    if value is None:
        raise TexturePayloadError(f"unmapped source array graphics format {texture.m_Format}")
    return value


class TextureLibrary:
    """Content-addressed encoded/decoded payloads shared by material instances."""

    def __init__(self, store, directory, prefix="shader-textures"):
        self.store = store
        self.directory = Path(directory)
        self.prefix = prefix
        self._receipts = {}
        self._references = {}

    def reference(self, record, path_id):
        key = record.bundle, record.archive, path_id
        if key in self._references:
            return deepcopy(self._references[key])
        from UnityPy.enums import TextureFormat
        tree = record.tree(path_id)
        if record.bundle not in self._receipts:
            self._receipts[record.bundle] = self.store.source_receipt(record.bundle)
        source = object_identity(record, path_id, self._receipts[record.bundle])
        kind = record.kinds.get(path_id)
        document = {"schemaVersion": 1, "source": source, "name": tree.get("m_Name"),
            "kind": kind, "width": tree.get("m_Width"), "height": tree.get("m_Height"),
            "layers": tree.get("m_Depth") if kind == "Texture2DArray" else 1,
            "mipCount": tree.get("m_MipCount"), "sourceColorSpace": tree.get("m_ColorSpace"),
            "sourceTextureFormat": tree.get("m_TextureFormat"),
            "sourceGraphicsFormat": tree.get("m_Format"),
            "sourceTextureSettings": deepcopy(tree.get("m_TextureSettings")),
            "sourceMipBias": tree.get("m_MipBias"),
            "sourceStreamingMipmaps": tree.get("m_StreamingMipmaps"),
            "sourceCompleteImageSize": tree.get("m_CompleteImageSize"),
            "sourceDataSize": tree.get("m_DataSize"),
            "sourceImageCount": tree.get("m_ImageCount"),
            "sourceTextureDimension": tree.get("m_TextureDimension"),
            "texelOrigin": "unity-lower-left", "dataOrder": "layer-major-mips",
            "decodedFormat": "rgba8unorm", "status": "unavailable"}
        try:
            if kind not in ("Texture2D", "Texture2DArray"):
                raise TexturePayloadError(f"source object is not a supported texture dimension: {kind}")
            texture = record.objects[path_id].read()
            if kind == "Texture2D":
                format_id = TextureFormat(texture.m_TextureFormat)
                payload = bytes(texture.get_image_data())
            else:
                format_id = _array_format(texture)
                payload = _array_bytes(texture)
            document["encoded"] = _publish(self.directory, self.prefix, payload, ".texture.bin")
            if tree.get("m_PlatformBlob"):
                raise TexturePayloadError("platform-specific texture layout requires a source layout adapter")
            if kind == "Texture2D" and tree.get("m_ImageCount", 1) != 1:
                raise TexturePayloadError("a multi-image source is not a single Texture2D")
            extents = [document[key] for key in ("width", "height", "layers", "mipCount")]
            if any(not isinstance(v, int) or isinstance(v, bool) for v in extents):
                raise TexturePayloadError("source texture extent metadata is incomplete")
            decoded, planes = decode_payload(payload, format_id=int(format_id),
                format_name=format_id.name, width=extents[0], height=extents[1],
                layers=extents[2], mip_count=extents[3],
                version=texture.object_reader.version, platform=texture.object_reader.platform)
            document.update(status="decoded", planes=planes,
                pixels=_publish(self.directory, self.prefix, decoded, ".rgba8"),
                decoder={"name": "UnityPy", "version": importlib.metadata.version("UnityPy"),
                         "operation": "decode-each-source-mip-to-rgba8-without-flip",
                         "sourceFormat": format_id.name,
                         "nativeCompressedPayloadRetained": True})
        except (ValueError, TypeError, AttributeError, KeyError, NotImplementedError) as exc:
            document["error"] = f"{type(exc).__name__}: {exc}"
        reference = _publish(self.directory, self.prefix, canonical_bytes(document), ".texture.json")
        self._references[key] = {**reference, "schemaVersion": 1, "source": source,
                                 "status": document["status"]}
        if document.get("error"):
            self._references[key]["error"] = document["error"]
        return deepcopy(self._references[key])
