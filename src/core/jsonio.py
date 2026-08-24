"""Writing documents out as JSON, with the two cases JSON itself cannot express.

JSON has no spelling for infinity or for not-a-number, and Python's encoder
papers over that by emitting the bare words ``Infinity`` and ``NaN`` — which is
not JSON, and which every strict parser (a browser's ``JSON.parse`` included)
rejects.  Authored data does contain infinities: a particle lifetime that never
expires, a burst whose next cycle never arrives, a shader parameter whose fade
distance is unbounded.  Dropping them, or clamping them to a large number, would
change what the asset says.

So every non-finite number is written as its **name in a string** —
``"Infinity"``, ``"-Infinity"``, ``"NaN"`` — which is lossless, survives
``JSON.parse``, and coerces back to the right value in any language that reads a
numeric string (``Number("Infinity")`` is infinity).  A reader cannot mistake it
for a missing value the way ``null`` would be mistaken, and ``null`` already
means other things in these documents.

``allow_nan=False`` is then kept on every dump as the backstop: if a non-finite
number ever reaches the encoder by a path that skipped normalisation, it raises
instead of writing something no parser will read back.
"""
import json
import math
from pathlib import Path

INFINITY = "Infinity"
NEGATIVE_INFINITY = "-Infinity"
NOT_A_NUMBER = "NaN"


def spell(value):
    """The string a non-finite number is written as."""
    if math.isnan(value):
        return NOT_A_NUMBER
    return INFINITY if value > 0 else NEGATIVE_INFINITY


def normalize(document):
    """*document* with every non-finite number replaced by its name.

    Booleans are left alone — in Python they are integers, and a ``True`` that
    came back as ``1`` would quietly change a document's shape.
    """
    if isinstance(document, dict):
        return {key: normalize(value) for key, value in document.items()}
    if isinstance(document, (list, tuple)):
        return [normalize(value) for value in document]
    if isinstance(document, float) and not math.isfinite(document):
        return spell(document)
    return document


def dumps(document, indent=1):
    """*document* as JSON text, non-finite numbers spelled out."""
    return json.dumps(normalize(document), ensure_ascii=False, indent=indent,
                      allow_nan=False)


def write_json(path, document, indent=1):
    """Write one document, newline-terminated, as UTF-8 with LF endings."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(document, indent=indent) + "\n", encoding="utf-8",
                    newline="\n")
    return path
