"""Lossless object references at the JSON boundary of a PackageStore graph.

The cached Unity graph keeps integer keys. A resolved object is addressed by its
serialized file and signed 64-bit path ID; a uint32 state hash is not a PPtr.
"""


def path_id(value):
    if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
        raise ValueError(f"object path ID must be a source SInt64, got {value!r}")
    return str(value)


def identity(record, object_id):
    return {"file": record.archive, "pathId": path_id(object_id)}


def pointer_value(pointer):
    """Copy an actual PPtr, preserving its file index and null ID exactly."""
    if not isinstance(pointer, dict) or set(pointer) != {"m_FileID", "m_PathID"}:
        raise ValueError("expected a serialized PPtr pair")
    file_id = pointer["m_FileID"]
    if type(file_id) is not int or not -(1 << 31) <= file_id < (1 << 31):
        raise ValueError("PPtr file index must be a source Int32")
    return {"m_FileID": file_id, "m_PathID": path_id(pointer["m_PathID"])}


def reference(store, record, pointer):
    """The existing file/pathId contract, or authored null/unresolved metadata."""
    if pointer is None:
        return None
    exported = pointer_value(pointer)
    if exported["m_PathID"] == "0":
        return None
    target = store.follow(record, pointer)
    if target is not None:
        return identity(*target)
    return {"unresolved": {
        "ownerFile": record.archive,
        "fileId": exported["m_FileID"],
        "pathId": exported["m_PathID"],
        "targetFile": store.archive_of(record, pointer),
    }}


def json_pointers(value):
    """Copy JSON-bound data; only structural PPtr pairs change representation.

    Do not mutate PackageFile's cached trees, and do not stringify unrelated
    numbers merely because a controller field is also named m_PathID.
    """
    if isinstance(value, dict):
        if set(value) == {"m_FileID", "m_PathID"}:
            return pointer_value(value)
        return {key: json_pointers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_pointers(item) for item in value]
    return value


def object_identity(record, object_id):
    """Object address including the logical package that owns its serialized file."""
    return {"bundle": str(record.bundle), "archive": str(record.archive),
            "pathId": path_id(object_id)}


def source_reference(record, owner_path_id, pointer, follow):
    """Both ends of a source graph edge, including authored null/unresolved PPtrs."""
    pointer = pointer or {}
    copied = pointer_value({"m_FileID": pointer.get("m_FileID", 0),
                            "m_PathID": pointer.get("m_PathID", 0)})
    file_id = copied["m_FileID"]
    externals = getattr(record, "externals", ())
    external = (str(externals[file_id - 1])
                if 0 < file_id <= len(externals) else None)
    target = follow(record, pointer) if copied["m_PathID"] != "0" else None
    return {"owner": object_identity(record, owner_path_id),
            "pointer": {"fileId": file_id, "pathId": copied["m_PathID"]},
            "external": external,
            "target": object_identity(*target) if target else None}
