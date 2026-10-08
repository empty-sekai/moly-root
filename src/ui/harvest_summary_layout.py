"""Serialized fields of the MySekai harvest point summary screen layer.

``ScreenLayerMysekaiHarvestSummary`` serializes the ScreenLayer base, then
eight references: the background image, the rank experience, event point and
event badge texts, the badge texture loader, the canvas group the layer's
sequence moves and fades, and the event point and event badge contents.  Its
hide position (set by the constructor) and its sequence are not serialized.
The ScreenLayer base is region-specific (the JP chain is in ``ui.jp_decoders``).
"""
from __future__ import annotations

from ui import talk

HARVEST_SUMMARY_FIELDS = (
    "_bgImage", "_rankExpText", "_eventPointText", "_eventBadgeText",
    "_eventBadgeLoader", "_animationCanvasGroup", "_eventPointContent",
    "_eventBadgeContent",
)


def harvest_summary_fields(r: talk.Reader) -> dict:
    return {name: r.pptr() for name in HARVEST_SUMMARY_FIELDS}


def decode_screen_layer_mysekai_harvest_summary(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiHarvestSummary on the older chain: ScreenLayer's
    ``layerCamera`` and public ``isBootDone``, then the eight references."""
    fields = {"layerCamera": r.pptr(), "isBootDone": r.bool4()}
    fields.update(harvest_summary_fields(r))
    return fields


DECODERS = {
    "Sekai.ScreenLayerMysekaiHarvestSummary": decode_screen_layer_mysekai_harvest_summary,
}
