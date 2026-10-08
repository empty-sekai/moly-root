"""The default exporter and offline candidate use the same bounded key codec."""
import math
import struct

import pytest

from chara.rotation_encoding import DEFAULT_ROTATION_ENCODING, encode_rotation_stream


def test_default_and_exact_signed_normalized_encoding():
    assert DEFAULT_ROTATION_ENCODING == "snorm16"
    raw = struct.pack("<8f", 0, 0, 0, 1, 0, 0, 0, -1)
    encoded, stats = encode_rotation_stream(raw)
    assert encoded == struct.pack("<8h", 0, 0, 0, 32767, 0, 0, 0, -32767)
    assert stats["keys"] == 2
    assert stats["maxKeyAngleDegrees"] == stats["maxNormSquaredError"] == 0


@pytest.mark.parametrize("values", [(0, 0, 0, 0), (0, 0, 0, 2),
                                   (math.inf, 0, 0, 1), (math.nan, 0, 0, 1)])
def test_invalid_quaternions_cannot_bypass_error_gates(values):
    with pytest.raises(ValueError):
        encode_rotation_stream(struct.pack("<4f", *values))


@pytest.mark.parametrize("payload", [b"", b"\0", b"\0" * 15, b"\0" * 17])
def test_partial_or_empty_rotation_stream_is_rejected(payload):
    with pytest.raises(ValueError, match="complete"):
        encode_rotation_stream(payload)


def test_quality_thresholds_cannot_be_relaxed():
    raw = struct.pack("<4f", 0, 0, 0, 1)
    for limits in ({"max_angle_degrees": .02}, {"max_norm_squared_error": .001},
                   {"max_angle_degrees": float("nan")}):
        with pytest.raises(ValueError, match="stricter"):
            encode_rotation_stream(raw, **limits)
