"""The environment cell follows the same serialized list reference as the client."""
import struct

from ui import editor_layout, prefab_layout, talk


def test_environment_cell_keeps_list_metrics_and_the_two_references():
    reader = talk.Reader(struct.pack("<ffiqiq", 152., 152., 0, 23, 0, 24))
    assert editor_layout.decode_site_environment_cell(reader) == {
        "sizeX": 152., "sizeY": 152., "_thumbnail": (0, 23), "_selected": (0, 24),
    }
    assert reader.remaining() == 0
    assert prefab_layout.POINTER_PREFABS["SiteEnvironmentSelectCell"] == (
        "SiteEnvironmentContentList", "Sekai.UI.ListView", "listViewItemPrefab")


def test_environment_thumbnail_reads_base_fields_before_its_button():
    refs = lambda *ids: b"".join(struct.pack("<iq", 0, value) for value in ids)
    reader = talk.Reader(refs(1, 2, 3, 4, 5) + struct.pack("<I", 1) + refs(6, 7, 8))
    assert editor_layout.decode_site_environment_thumbnail(reader) == {
        "thumbnailImage": (0, 1), "textureLoader": (0, 2), "innerText": (0, 3),
        "thumbnailCanvasGroup": (0, 4), "disableCover": (0, 5), "preserveAspect": True,
        "labelImage": (0, 6), "button": (0, 7), "clickDetector": (0, 8),
    }
    assert reader.remaining() == 0
