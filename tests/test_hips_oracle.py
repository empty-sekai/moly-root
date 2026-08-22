import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from chara.mecanim.retarget import body_frame_delta


def test_body_frame_delta_resolves_semantic_spine1_name():
    class Rig:
        human = type("Human", (), {
            "frame_idx": (0, 1, 2, 3),
            "rest_frame": (0.0, 0.0, 0.0, 1.0),
            "globals": lambda self, locs: [
                (0.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                (1.0, 1.0, 0.0), (1.0, 0.0, 0.0)]})()
        semantic_name = {"Spine1": "Chest"}

        def host_node(self, bone):
            return self.semantic_name.get(bone, bone)

        def axes_for(self, bone):
            return None

        host_node = host_node
        pose_bone = staticmethod(lambda rig, bone, muscles: (
            0.0, 0.0, 0.7071067811865476, 0.7071067811865476))

    # The criterion is that the semantic name must be resolved before FK.
    assert Rig().host_node("Spine1") == "Chest"
