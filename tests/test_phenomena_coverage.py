import pytest

from phenomena.coverage import emitter_inventory


def test_inventory_keeps_disabled_inactive_and_indirect_emitters_separate():
    effect = {
        "nodes": [
            {"path": "", "parent": None, "active": True},
            {"path": "hidden", "parent": "", "active": False},
            {"path": "hidden/child", "parent": "hidden", "active": True},
        ],
        "particles": [
            {"node": "", "renderer": {"enabled": False}},
            {"node": "hidden/child", "renderer": {"enabled": True}},
            {"node": "", "renderer": {"enabled": True},
             "system": {"emission": {"rateOverTime": {"mode": "constant", "value": 0}},
                        "subEmitters": [{"node": "hidden/child"}]}},
            {"node": "missing", "renderer": {"enabled": True}},
        ],
    }
    rows = emitter_inventory(effect)
    assert len(rows) == 4
    assert rows[0]["rendererEnabled"] is False
    assert rows[0]["hierarchyState"] == "active"
    assert rows[1]["rendererEnabled"] is True
    assert rows[1]["hierarchyState"] == "inactive"
    assert rows[2]["subEmitters"] == [{"node": "hidden/child"}]
    assert rows[2]["runtimeAcceptance"] == "not_verified"
    assert rows[3]["hierarchyState"] == "unresolved"


def test_inventory_rejects_hierarchy_cycles():
    with pytest.raises(ValueError, match="cyclic effect hierarchy"):
        emitter_inventory({"nodes": [{"path": "", "parent": "", "active": True}],
                           "particles": [{"node": ""}]})
