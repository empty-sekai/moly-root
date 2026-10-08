"""Extract the project's TimeManager settings from a player build.

``Time.deltaTime`` is the real frame delta clamped to the project's Maximum
Allowed Timestep, and the fixed step, the global time scale and the particle
step come from the same object.  Game code never states these values; they are
project settings serialized into the player's ``globalgamemanagers``.

The source is an APK (a ZIP) or a ``globalgamemanagers`` file.  In an APK the
file is either a plain member under ``assets/bin/Data/`` or a member of the
``data.unity3d`` container there; both layouts occur in shipped builds.

Player builds strip type trees from ``globalgamemanagers``, so field names come
from UnityPy's class database for the file's Unity version.  The object is then
decoded a second time from its raw bytes (four little-endian float32 in the
serialized field order) and the two readings must agree bit for bit; field
names stay the serialized names, with their spaces.
"""
import hashlib
import io
import math
import struct
import zipfile
from pathlib import Path

from core.jsonio import write_json

DATA_DIR = "assets/bin/Data/"
MANAGERS = "globalgamemanagers"
CONTAINER = "data.unity3d"
#: Serialized field order of TimeManager (Unity 2022.3): four float32.
FIELDS = ("Fixed Timestep", "Maximum Allowed Timestep", "m_TimeScale",
          "Maximum Particle Timestep")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_source(source):
    """Return (UnityPy environment, provenance, serialized file name) for *source*.

    The name is the one the build gives the file: the archive member's base
    name for a plain member, the container's entry name inside
    ``data.unity3d``, or the file's own name on disk.
    """
    import UnityPy
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"TimeManager source not found: {source}")
    provenance = {"file": path.name, "sha256": _file_sha256(path)}
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            for member in (DATA_DIR + MANAGERS, DATA_DIR + CONTAINER):
                if member in names:
                    data = archive.read(member)
                    provenance.update(member=member, memberSha256=_sha256(data))
                    env = UnityPy.load(io.BytesIO(data))
                    return env, provenance, (MANAGERS if member.endswith(MANAGERS) else None)
        raise ValueError(f"{path.name}: neither {DATA_DIR + MANAGERS} nor "
                         f"{DATA_DIR + CONTAINER} is in the archive")
    return UnityPy.load(str(path)), provenance, path.name


def extract_time_manager(source, out_path=None):
    """Read the one TimeManager of *source*; write it to *out_path* if given."""
    from core.unity import configure_fallback_unity_version
    configure_fallback_unity_version()
    env, provenance, loaded_name = _load_source(source)
    found = [obj for obj in env.objects if obj.type.name == "TimeManager"]
    if len(found) != 1:
        raise ValueError(f"expected exactly one TimeManager, found {len(found)}")
    obj = found[0]
    # A plain member or a file on disk is one serialized file named by the
    # build; inside the container the entry carries the name itself.
    serialized_name = loaded_name or obj.assets_file.name
    if serialized_name != MANAGERS:
        raise ValueError(f"TimeManager sits in {serialized_name!r}, "
                         f"not in {MANAGERS}")
    tree = obj.read_typetree()
    if tuple(tree) != FIELDS:
        raise ValueError(f"TimeManager fields {tuple(tree)} differ from {FIELDS}")
    raw = obj.get_raw_data()
    if len(raw) != 4 * len(FIELDS):
        raise ValueError(f"TimeManager holds {len(raw)} bytes, expected "
                         f"{4 * len(FIELDS)}")
    decoded = struct.unpack("<4f", raw)
    for field, value in zip(FIELDS, decoded):
        if struct.pack("<f", tree[field]) != struct.pack("<f", value):
            raise ValueError(f"{field}: class-database reading {tree[field]!r} "
                             f"differs from the raw float32 {value!r}")
        if not math.isfinite(value):
            raise ValueError(f"{field} is not finite: {value!r}")
    serialized = obj.assets_file
    doc = {
        "version": 1,
        "semantics": {
            "settings": ("TimeManager fields under their serialized names, "
                         "float32 values widened to JSON numbers"),
            "Maximum Allowed Timestep": ("seconds; the engine clamps each "
                                         "frame's delta time to this value"),
            "typeTree": ("embedded when the file carries type trees, else the "
                         "class database of the file's Unity version; the raw "
                         "float32 decoding agreed either way"),
        },
        "source": {
            **provenance,
            "serializedFile": serialized_name,
            "unityVersion": str(getattr(serialized, "unity_version", "")),
            "pathId": str(obj.path_id),
            "classId": obj.class_id,
            "rawHex": raw.hex(),
            "typeTree": ("embedded" if getattr(serialized, "_enable_type_tree", False)
                         else "class-database"),
            # JSON object order is not kept by the writer; the serialized
            # order of the four float32 is stated here.
            "fieldOrder": list(FIELDS),
        },
        "timeManager": {field: tree[field] for field in FIELDS},
    }
    if out_path is not None:
        write_json(out_path, doc)
    return doc
