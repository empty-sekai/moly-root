

def test_anchor_fields_report_unset_points_as_null():
    """A rig must distinguish "no such attachment point" from "not looked at".

    Overhead items are parented to one of these nodes with a zero local
    position, so a consumer placing one needs the node name — and needs to know
    when the rig simply does not have that point.
    """
    from chara.characters import read_anchors

    class _Obj:
        def __init__(self, kind, pid, tree):
            self.type = type("T", (), {"name": kind})()
            self.path_id = pid
            self._tree = tree

        def read_typetree(self):
            return self._tree

    class _Assets:
        objects = [
            _Obj("MonoScript", 1, {"m_ClassName": "SomeAvatarView"}),
            _Obj("MonoBehaviour", 2, {"_headRoot": {"m_PathID": 10},
                                      "_headTopRoot": {"m_PathID": 11},
                                      "_lightingHeadCenter": {"m_PathID": 0}}),
        ]

    nodes = [{"name": "Root"}, {"name": "Head"}, {"name": "HeadRoot"}]
    anchors = read_anchors(_Assets(), {10: 1, 11: 2}, nodes)
    assert anchors["_headRoot"] == "Head"
    assert anchors["_headTopRoot"] == "HeadRoot"
    assert anchors["_lightingHeadCenter"] is None      # 字段有、但没设
    assert "_spineRoot" not in anchors                 # 字段不存在就不编
