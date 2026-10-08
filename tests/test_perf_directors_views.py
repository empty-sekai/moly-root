"""Synthetic contract for the cut-scene view's GameObject fields.

A view field that names a GameObject of the prefab is listed with that
object's hierarchy and transform chain; a null field, a field naming an object
outside the prefab and engine bookkeeping are not.  Synthetic fixtures test
the code only; game semantics are never inferred from them.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from perf.directors import _view_game_objects


class _Store:
    def follow(self, record, pointer):
        path_id = pointer.get("m_PathID")
        return (record, path_id) if path_id in record.kinds else None


def test_view_fields_naming_prefab_objects_carry_their_chain():
    record = SimpleNamespace(archive="CAB-synth", kinds={10: "GameObject", 20: "GameObject"})
    chain = [{"name": "root"}, {"name": "CharacterRoot", "position": {"x": 1.0}}]
    placed = {("CAB-synth", 10): ((record, 10), ["root", "CharacterRoot"], chain)}
    tree = {
        "m_GameObject": {"m_FileID": 0, "m_PathID": 10},
        "_characterRoot": {"m_FileID": 0, "m_PathID": 10},
        "_outside": {"m_FileID": 0, "m_PathID": 20},
        "_missing": {"m_FileID": 0, "m_PathID": 0},
        "_hideFixtureDistance": 5,
    }
    rows = _view_game_objects(_Store(), record, tree, placed)
    assert set(rows) == {"_characterRoot"}
    assert rows["_characterRoot"] == {
        "gameObject": {"file": "CAB-synth", "pathId": "10"},
        "hierarchy": ["root", "CharacterRoot"], "transforms": chain}
