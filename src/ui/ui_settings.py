"""Two client-side UI settings assets the runtime reads at boot: the colour palette and the tween defaults.

``PaletteStore`` (uPalette) is a preloaded ScriptableObject; when the player
loads it at startup its OnEnable makes it the store every
``PaletteUtility.GetColor(ColorEntry)`` call reads.  A Graphic whose colour is
bound to a palette entry (a button's press effect, a colour synchronizer)
takes the active theme's value of that entry.  The generated ``ColorEntry``
enum lists the colour palette's entries in the palette's own entry order, so
an entry's enum value is its position in that order; the document keeps the
entries in that order and records the position.

``DOTweenSettings`` is the Resources asset DOTween applies when it
initializes: the default ease, update type, time-scale independence and
auto-kill of every tween whose creator does not set them (a colour fade
created with ``DOColor`` and no ``SetEase`` runs with the default ease).

Both are serialized without a typetree; they are read along the declaration
order of their managed types (uPalette's ``PaletteStore`` / ``Palette<T>`` /
``Entry<T>`` / ``Theme`` / ``ObservableDictionary`` / ``OrderCollection`` /
``ObservableProperty``, and DOTween's ``DOTweenSettings``), and each read must
end exactly on its object.
"""
from __future__ import annotations

from ui import talk

PALETTE_RESOURCE = "color/palettestore"
PALETTE_CLASS = "uPalette.Runtime.Core.PaletteStore"
TWEEN_SETTINGS_RESOURCE = "dotweensettings"
TWEEN_SETTINGS_CLASS = "DG.Tweening.Core.DOTweenSettings"

# DOTweenSettings' serialized fields in declaration order: (name, reader).
TWEEN_SETTINGS_FIELDS = (
    ("useSafeMode", "bool"), ("timeScale", "float"), ("useSmoothDeltaTime", "bool"),
    ("maxSmoothUnscaledTime", "float"), ("showUnityEditorReport", "bool"),
    ("logBehaviour", "int"), ("drawGizmos", "bool"), ("defaultRecyclable", "bool"),
    ("defaultAutoPlay", "int"), ("defaultUpdateType", "int"),
    ("defaultTimeScaleIndependent", "bool"), ("defaultEaseType", "int"),
    ("defaultEaseOvershootOrAmplitude", "float"), ("defaultEasePeriod", "float"),
    ("defaultAutoKill", "bool"), ("defaultLoopType", "int"), ("storeSettingsLocation", "int"),
)


def _array(r: talk.Reader, read):
    count = r.i32()
    if count < 0 or count > r.remaining():
        raise ValueError(f"array length {count} over-reads the object ({r.remaining()} bytes left)")
    return [read() for _ in range(count)]


def _observable_dictionary(r: talk.Reader, read_key, read_value):
    """ObservableDictionary<K, V>: its serialized key array, then its value array."""
    keys = _array(r, read_key)
    values = _array(r, read_value)
    if len(keys) != len(values) or len(set(keys)) != len(keys):
        raise ValueError("palette dictionary keys and values do not pair one to one")
    return dict(zip(keys, values))


def _palette(r: talk.Reader, read_value):
    """Palette<T>: entries, themes, entry order, theme order, active theme id."""
    entries = _observable_dictionary(r, r.string, lambda: {
        "id": r.string(),
        "name": r.string(),                       # ObservableProperty<string>._value
        "values": _observable_dictionary(r, r.string, read_value),  # ObservableProperty<T>._value
    })
    themes = _observable_dictionary(r, r.string, lambda: {"id": r.string(), "name": r.string()})
    entry_order = _array(r, r.string)             # OrderCollection<string>._ids
    theme_order = _array(r, r.string)
    active = r.string()
    return {"entries": entries, "themes": themes, "entryOrder": entry_order,
            "themeOrder": theme_order, "activeThemeId": active}


def _no_value():
    raise ValueError("this palette holds entries, but its value type is not read here")


def decode_palette_store(r: talk.Reader) -> dict:
    """PaletteStore: colour, gradient, character-style and TMP character-style palettes, error level.

    Only the colour palette's value type (Color) is read; the other three
    palettes must hold no entries, or the read refuses.
    """
    return {
        "_colorPalette": _palette(r, r.color),
        "_gradientPalette": _palette(r, _no_value),
        "_characterStylePalette": _palette(r, _no_value),
        "_characterStyleTMPPalette": _palette(r, _no_value),
        "_missingEntryErrorLevel": r.i32(),
    }


def decode_tween_settings(r: talk.Reader) -> dict:
    """DOTweenSettings: seventeen serialized fields (bools are 4-byte aligned)."""
    readers = {"bool": r.bool4, "float": r.f32, "int": r.i32}
    return {name: readers[kind]() for name, kind in TWEEN_SETTINGS_FIELDS}


DECODERS = {PALETTE_CLASS: decode_palette_store, TWEEN_SETTINGS_CLASS: decode_tween_settings}


def palette_document(record, source):
    """The colour palette as the runtime reads it: entries in entry order, one value per theme."""
    palette = record["fields"]["_colorPalette"]
    entries, themes = palette["entries"], palette["themes"]
    if sorted(palette["entryOrder"]) != sorted(entries):
        raise ValueError("palette entry order does not list every entry exactly once")
    if sorted(palette["themeOrder"]) != sorted(themes) or palette["activeThemeId"] not in themes:
        raise ValueError("palette themes, theme order and active theme disagree")
    rows = []
    for position, entry_id in enumerate(palette["entryOrder"]):
        entry = entries[entry_id]
        if entry["id"] != entry_id or sorted(entry["values"]) != sorted(themes):
            raise ValueError(f"palette entry {entry_id} does not carry one value per theme")
        rows.append({"colorEntry": position, "id": entry_id, "name": entry["name"],
                     "values": {theme: entry["values"][theme] for theme in palette["themeOrder"]}})
    return {
        "version": 1,
        "activeThemeId": palette["activeThemeId"],
        "themes": [{"id": theme, "name": themes[theme]["name"]} for theme in palette["themeOrder"]],
        "colorEntries": rows,
        "missingEntryErrorLevel": record["fields"]["_missingEntryErrorLevel"],
        "otherPalettes": {name: len(record["fields"][name]["entries"])
                          for name in ("_gradientPalette", "_characterStylePalette",
                                       "_characterStyleTMPPalette")},
        "source": source,
    }


def tween_settings_document(record, source):
    return {"version": 1, "settings": record["fields"], "source": source}
