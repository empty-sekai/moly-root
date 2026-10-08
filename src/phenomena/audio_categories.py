"""The sound configuration's category table, for the runtime's category volumes.

The audio middleware's sound configuration (the project's ``.acf``) ships in
the application package, not in a sound package.  Its category table lists the
categories cues are grouped by; every cue names its categories in its sequence
command list (command 65, a list of 32-bit category indices), and a category's
own command list carries its volume (command 87, a 16-bit value in hundredths).
The player scales every cue by the volume of each category it names, on top of
the player's volume and the cue's own volume commands.  MySekai never changes a
category volume at run time, so the configuration's values are the ones heard.

The runtime reads the table as ``phenomena/audio/categories.json``: one row per
category, in the configuration's index order (the order cues refer to), named
through the configuration's category-name table.  The names are the client's
category enum names; the index order is not that enum's order.
"""
import struct
import zipfile
from pathlib import Path

from core.atomic import PUBLIC_FILE_MODE, write_json
from . import acb

#: Category command: volume in hundredths.
COMMAND_VOLUME = 87
CATEGORY_TABLE_VERSION = 1
#: Where the application package keeps the sound configuration.
PACKAGE_SUFFIX = ".acf"


def read_configuration(path):
    """``(bytes, source)`` of the sound configuration.

    *path* is the ``.acf`` itself or an application package (a zip archive,
    such as the base APK or its asset pack) holding exactly one ``assets/*.acf``.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as package:
            names = [name for name in package.namelist()
                     if name.startswith("assets/") and name.endswith(PACKAGE_SUFFIX)]
            if len(names) != 1:
                raise ValueError(f"{path.name} holds {len(names)} sound configurations, not 1: {names}")
            return package.read(names[0]), f"{path.name}:{names[0]}"
    return path.read_bytes(), path.name


def category_table(data):
    """The category table of a sound configuration as the runtime document."""
    _, _, header = acb.table(data)
    if len(header) != 1:
        raise acb.TableError(f"configuration header has {len(header)} rows, not 1")
    top = header[0]
    rows = acb.table(top["CategoryTable"])[2]
    names = acb.table(top["CategoryNameTable"])[2]
    command_cell = top.get("CommandTable")
    command_rows = acb.table(command_cell)[2] if command_cell else []
    by_index = {}
    for row in names:
        if row["Index"] in by_index:
            raise acb.TableError(f"category index {row['Index']} is named twice")
        by_index[row["Index"]] = row["Name"]
    categories = []
    for index, row in enumerate(rows):
        if index not in by_index:
            raise acb.TableError(f"category {index} has no name")
        command_index = row.get("CommandIndex", 0xFFFF)
        records = (acb.commands(command_rows[command_index]["Command"])
                   if command_index != 0xFFFF else [])
        volume = 1.0
        for code, payload in records:
            if code == COMMAND_VOLUME:
                volume *= struct.unpack(">H", payload)[0] / 100
        categories.append({"index": index, "id": row.get("Id"), "name": by_index[index],
                           "group": row.get("GroupIndex"), "volume": volume,
                           "commands": [{"code": code, "bytes": payload.hex()}
                                        for code, payload in records]})
    return {"version": CATEGORY_TABLE_VERSION, "configurationVersion": top.get("Version"),
            "categories": categories}


def write_category_table(configuration, out_path):
    """Read *configuration* (see :func:`read_configuration`) and write the table to *out_path*."""
    data, source = read_configuration(configuration)
    document = category_table(data)
    document["source"] = source
    write_json(Path(out_path), document, mode=PUBLIC_FILE_MODE)
    return document
