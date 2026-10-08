"""Export-local sharing and checked rotation encoding for actor accessors.

The caller still writes every authored sampler/channel. No key reduction,
quaternion sign canonicalization or persistent GLB cache is performed.
"""
import json

from .rotation_encoding import DEFAULT_ROTATION_ENCODING, ROTATION_ENCODINGS, encode_rotation_stream


MOTION_STORAGE_REVISION = 2


def validate_rotation_encoding(value):
    if value not in ROTATION_ENCODINGS:
        raise ValueError(f"rotation_encoding must be one of {ROTATION_ENCODINGS}")
    return value


class MotionStorage:
    """Intern the complete layout accepted by GLB.acc and its immutable bytes.

    One instance belongs to one export/GLB. Bounds use their exact JSON spelling
    so even signed zero or different numeric metadata cannot be merged. Bytes
    keys also compare their contents after hashing; hash collisions cannot alias
    two streams. Release this instance before serializing the finished GLB.
    """

    def __init__(self, glb):
        self._glb = glb
        self._accessors = {}

    def acc(self, data, ctype, atype, count, target=None, minmax=None, normalized=None):
        if normalized is not None and not isinstance(normalized, bool):
            raise ValueError("normalized must be a boolean when present")
        data = bytes(data)
        bounds = json.dumps(minmax, separators=(",", ":"), allow_nan=False)
        key = (ctype, atype, count, target, bounds, normalized, data)
        previous = self._accessors.get(key)
        if previous is not None:
            return previous
        accessor = self._glb.acc(data, ctype, atype, count, target=target, minmax=minmax)
        if normalized is not None:
            self._glb.g["accessors"][accessor]["normalized"] = normalized
        self._accessors[key] = accessor
        return accessor

    def rotation(self, data, count, *, rotation_encoding=DEFAULT_ROTATION_ENCODING):
        """Store packed LINEAR quaternion keys, leaving signs and key times alone."""
        validate_rotation_encoding(rotation_encoding)
        data = bytes(data)
        if len(data) != count * 16:
            raise ValueError("rotation stream must contain count packed float32 VEC4 keys")
        if rotation_encoding == "float32":
            return self.acc(data, 5126, "VEC4", count)
        encoded, _ = encode_rotation_stream(data)
        return self.acc(encoded, 5122, "VEC4", count, normalized=True)
