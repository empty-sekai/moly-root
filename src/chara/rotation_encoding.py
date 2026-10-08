"""Bounded glTF core SNORM16 encoding for packed LINEAR rotation keys.

The exporter and the offline candidate tool share these exact operations. This
is lossy storage encoding, not retargeting, resampling or quaternion sign repair.
"""
import math
import struct


DEFAULT_ROTATION_ENCODING = "snorm16"
ROTATION_ENCODINGS = ("snorm16", "float32")
MAX_ANGLE_DEGREES = 0.01
MAX_NORM_SQUARED_ERROR = 2e-4


def _f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def encode_rotation_stream(payload, *, max_angle_degrees=MAX_ANGLE_DEGREES,
                           max_norm_squared_error=MAX_NORM_SQUARED_ERROR):
    """Return SNORM16 bytes and measured key errors; reject malformed inputs.

    Input is already packed float32 VEC4 data, so thresholds are measured
    against exactly what an uncompressed GLB would contain. Neither source
    keys nor decoded keys are normalized before encoding/using their norm.
    Normalization below is only for measuring quaternion direction error.
    """
    if (not 0 < max_angle_degrees <= MAX_ANGLE_DEGREES
            or not 0 < max_norm_squared_error <= MAX_NORM_SQUARED_ERROR):
        raise ValueError("rotation limits may be stricter, never weaker than 0.01 degrees / 2e-4")
    payload = bytes(payload)
    if not payload or len(payload) % 16:
        raise ValueError("rotation stream must contain complete float32 VEC4 keys")
    output = bytearray()
    minimum, maximum = [32767] * 4, [-32767] * 4
    max_angle = max_norm = 0.0
    # gltf 1.4.1 converts i16 by multiplying the float32 reciprocal.
    reciprocal = _f32(1.0 / 32767.0)
    for sample, q in enumerate(struct.iter_unpack("<4f", payload)):
        if not all(math.isfinite(value) for value in q):
            raise ValueError(f"non-finite source quaternion: key {sample}")
        source_norm2 = sum(value * value for value in q)
        if abs(source_norm2 - 1.0) > MAX_NORM_SQUARED_ERROR:
            raise ValueError(f"source quaternion is not normalized: key {sample}")
        integers = [round(max(-1.0, min(1.0, value)) * 32767.0) for value in q]
        decoded = [_f32(value * reciprocal) for value in integers]
        decoded_norm2 = sum(value * value for value in decoded)
        norm_error = abs(decoded_norm2 - 1.0)
        a = [value / math.sqrt(source_norm2) for value in q]
        b = [value / math.sqrt(decoded_norm2) for value in decoded]
        if sum(x * y for x, y in zip(a, b)) <= 0:
            raise ValueError("quantization changed quaternion hemisphere")
        angle = math.degrees(4 * math.atan2(
            math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b))),
            math.sqrt(sum((x + y) ** 2 for x, y in zip(a, b)))))
        if angle > max_angle_degrees or norm_error > max_norm_squared_error:
            raise ValueError(f"rotation error exceeds limit: key {sample}, angle={angle}, norm2={norm_error}")
        max_angle, max_norm = max(max_angle, angle), max(max_norm, norm_error)
        output.extend(struct.pack("<4h", *integers))
        minimum = [min(x, y) for x, y in zip(minimum, integers)]
        maximum = [max(x, y) for x, y in zip(maximum, integers)]
    return bytes(output), {"keys": len(payload) // 16,
        "maxKeyAngleDegrees": max_angle, "maxNormSquaredError": max_norm,
        "min": minimum, "max": maximum}
