"""Serialized fields of the MySekai notice screen layer and its weather banner view.

``ScreenLayerMysekaiNotice`` serializes the ScreenLayer base, then seventeen
references: the collect-item, message and mission notice roots with their
cell prefabs, and the site-environment banner (``_phenomenaRoot``, the
``SiteMapPhenomenaView`` it shows, the ``_topNoticeBack`` shade) and the
other-site name banner.  Its pools and slide controllers are runtime state.
The ScreenLayer base is region-specific (the JP chain is in ``ui.jp_decoders``).

``SiteMapPhenomenaView`` serializes five references: the icon and background
images, the Japanese and English name texts and the horizontal layout group
that is rebuilt after the names are written; its bundle loader is runtime
state.
"""
from __future__ import annotations

from ui import talk

NOTICE_FIELDS = (
    "collectItemRoot", "collectItemCellPrefab", "missionCellPrefab",
    "rightUpperMessageRoot", "rightUpperMessageCellPrefab",
    "rightUnderMessageRoot", "rightUnderMessageCellPrefab",
    "homeLeftUpperMessageRoot", "LeftUpperMessageRoot", "leftUpperMessageCellPrefab",
    "homeLeftUpperMessageCellCountObject", "homeLeftUpperMessageCellCountText",
    "_phenomenaRoot", "_phenomenaView", "_topNoticeBack",
    "_otherMysekaiNameNoticeRoot", "_otherMysekaiNameNoticeText",
)

PHENOMENA_VIEW_FIELDS = (
    "_siteMapPhenomenaIcon", "_siteMapPhenomenaBackground",
    "_siteMapPhenomenaJPName", "_siteMapPhenomenaENName", "_layoutGroup",
)


def notice_fields(r: talk.Reader) -> dict:
    return {name: r.pptr() for name in NOTICE_FIELDS}


def decode_screen_layer_mysekai_notice(r: talk.Reader) -> dict:
    """ScreenLayerMysekaiNotice on the older chain: ScreenLayer's
    ``layerCamera`` and public ``isBootDone``, then the seventeen references."""
    fields = {"layerCamera": r.pptr(), "isBootDone": r.bool4()}
    fields.update(notice_fields(r))
    return fields


def decode_site_map_phenomena_view(r: talk.Reader) -> dict:
    """SiteMapPhenomenaView: five serialized references."""
    return {name: r.pptr() for name in PHENOMENA_VIEW_FIELDS}


DECODERS = {
    "Sekai.Mysekai.ScreenLayerMysekaiNotice": decode_screen_layer_mysekai_notice,
    "Sekai.Mysekai.SiteMapPhenomenaView": decode_site_map_phenomena_view,
}
