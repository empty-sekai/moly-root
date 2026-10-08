"""The runtime wording dictionary: the built-in CSV, then the master overlay.

The client builds one dictionary in two steps, and a key's value is whatever
the second step leaves:

1. ``WordingManager.ForceInit`` loads the Resources TextAsset
   ``Wording/wording`` and reads it line by line (``StringReader.ReadLine``:
   a line ends at CR, LF or CRLF).  Each line is split on ``,``; only a line
   with exactly two parts and a non-empty key is taken.  The value has
   ``_x_COMMA_x_`` replaced by a comma and then the two-character sequence
   backslash-n replaced by a newline.  A repeated key is logged and skipped,
   so the first line wins.  Every rejected line is logged by its 1-based line
   number and skipped.
2. ``WordingManager.AddMasterWording`` walks the master ``wordings`` rows in
   order: an existing key is overwritten, a new key is added.

``TextAsset.text`` decodes the asset bytes by byte-order mark (UTF-32 BE/LE,
UTF-16 BE/LE, UTF-8 with BOM, in that order) and otherwise as UTF-8 with
U+FFFD for invalid sequences; the same rule is applied here before parsing.

The CN client (a container build: one player data file holding every
Resources asset) builds the same dictionary by rules of its own, kept apart
below:

* ``ForceInit`` loads ``"Wording/" + language + "/wording"``, so the CSV is
  the one of the client's language, not the untagged one the build also
  carries.
* A line is split on ``,``; with two or more parts every part after the first
  is joined back with commas as the value, so a value may hold raw commas.
  A line whose key is empty, or repeats a key already read, is logged by its
  1-based line number and skipped.  A line with a key but no comma makes the
  client throw (it indexes the missing second part).
* ``AddMasterWording``: the CN client replaces ``_x_COMMA_x_`` with a comma
  and the two characters backslash-n with a newline on every master value
  before it stores it, as it does for a CSV value (a null value stays null).
  The JP client stores master values as they are.
"""
from __future__ import annotations

import codecs
import re

COMMA_CODE = "_x_COMMA_x_"
NEWLINE_CODE = "\\n"

_BOMS = (
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF8, "utf-8"),
)

_MASKED_URL = "<external-url>"


def text_asset_decoding(data: bytes):
    """TextAsset.text and the encoding whose byte-order mark it stripped.

    The second value is ``None`` when no mark decoded and the plain UTF-8
    fallback (U+FFFD for invalid sequences) produced the text.
    """
    if not data:
        return "", None
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data[len(bom):].decode(encoding, errors="strict"), encoding
            except UnicodeDecodeError:
                continue
    return data.decode("utf-8", errors="replace"), None


def text_asset_text(data: bytes) -> str:
    """TextAsset.text: byte-order-mark lookup, else UTF-8 with replacement."""
    return text_asset_decoding(data)[0]


def read_lines(text: str):
    """StringReader.ReadLine over the whole text: CR, LF and CRLF end a line."""
    if not text:
        return []
    lines = re.split(r"\r\n|\r|\n", text)
    if text.endswith(("\r", "\n")):
        lines.pop()
    return lines


def parse_builtin_csv(text: str):
    """Entries and rejected lines of the built-in wording CSV, in file order."""
    entries, rejected = {}, []
    for number, line in enumerate(read_lines(text), start=1):
        parts = line.split(",")
        if len(parts) != 2:
            rejected.append({"line": number, "reason": "not two fields", "text": line})
            continue
        key, value = parts
        if key == "":
            rejected.append({"line": number, "reason": "empty key", "text": line})
            continue
        if key in entries:
            rejected.append({"line": number, "reason": "duplicate key", "text": line})
            continue
        entries[key] = value.replace(COMMA_CODE, ",").replace(NEWLINE_CODE, "\n")
    return entries, rejected


def container_value(value: str) -> str:
    """A value as the CN client stores it: the comma code, then backslash-n, replaced."""
    return value.replace(COMMA_CODE, ",").replace(NEWLINE_CODE, "\n")


def parse_builtin_csv_container(text: str):
    """Entries and rejected lines of the CN client's built-in CSV, in file order.

    A key with no comma is refused, not skipped: the client throws on it, so
    no dictionary it could build is known.
    """
    entries, rejected = {}, []
    for number, line in enumerate(read_lines(text), start=1):
        parts = line.split(",")
        key = parts[0]
        if key == "":
            rejected.append({"line": number, "reason": "empty key", "text": line})
            continue
        if key in entries:
            rejected.append({"line": number, "reason": "duplicate key", "text": line})
            continue
        if len(parts) < 2:
            raise ValueError(f"built-in wording line {number} has the key {key!r} and no comma; "
                             "the client throws on it")
        entries[key] = container_value(",".join(parts[1:]))
    return entries, rejected


def merge_master(builtin: dict, master_rows, master_value=None):
    """Apply the master rows over the built-in entries, master winning.

    *master_value*, when given, is applied to each non-null master value
    before it is stored (the CN client's ``AddMasterWording``).
    """
    merged = dict(builtin)
    origin = {key: "builtin" for key in builtin}
    overwritten = []
    rewritten = 0
    for row in master_rows:
        key = row.get("wordingKey")
        if key is None or "value" not in row:
            raise ValueError(f"master wording row lacks wordingKey/value: {row}")
        if key in merged:
            if origin[key] == "builtin":
                overwritten.append(key)
            origin[key] = "master"
        else:
            origin[key] = "master"
        value = row["value"]
        if master_value is not None and value is not None:
            stored = master_value(value)
            rewritten += stored != value
            value = stored
        merged[key] = value
    if master_value is None:
        return merged, origin, overwritten
    return merged, origin, overwritten, rewritten


def wording_document(builtin: dict, rejected, master_rows, source: dict, *, master_value=None) -> dict:
    """The merged dictionary in the keyed-table shape the product reads.

    *master_value* is the CN client's master value rule (see
    :func:`merge_master`); the document then says so and counts the values
    it changed.
    """
    rewritten = None
    if master_value is None:
        merged, origin, overwritten = merge_master(builtin, master_rows)
    else:
        merged, origin, overwritten, rewritten = merge_master(builtin, master_rows, master_value)
    masked = 0
    entries = {}
    for key, value in merged.items():
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            value = _MASKED_URL
            masked += 1
        entries[key] = {"wordingKey": key, "value": value, "origin": origin[key]}
    document = {
        "version": 1,
        "rowOrder": list(merged),
        "semantics": {
            "table": "wordings",
            "keyField": "wordingKey",
            "entries": ("the runtime dictionary: the built-in wording CSV, then "
                        "every master wordings row applied over it (a master row "
                        "overwrites a built-in key and adds a new one)"),
            "origin": ("builtin = only the CSV defines the key; master = the "
                       "master row is the surviving value"),
            "rowOrder": ("CSV keys in file order, then master-only keys in master "
                         "row order: the insertion order of the client dictionary"),
            "externalUrls": ("string values that are external http(s) links are "
                             "replaced by the literal <external-url>") if masked else None,
        },
        "entries": entries,
        "summary": {
            "rows": len(entries),
            "builtinRows": len(builtin),
            "builtinRejectedLines": len(rejected),
            "masterRows": len(master_rows),
            "masterOverwroteBuiltin": len(overwritten),
            "externalUrlsMasked": masked,
        },
        "builtinRejected": rejected,
        "masterOverwroteBuiltin": overwritten,
        "source": source,
    }
    if master_value is not None:
        document["semantics"]["masterValues"] = (
            "the client replaces _x_COMMA_x_ with a comma and the two characters "
            "backslash-n with a newline on every master value before it stores it")
        document["summary"]["masterValuesRewritten"] = rewritten
    return document
