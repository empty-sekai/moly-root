"""Controller identity must not widen a character's animation repertoire."""
from types import SimpleNamespace

import pytest

from chara.characters import read_animation_controller


def ref(file_id, path_id):
    return {"m_FileID": file_id, "m_PathID": path_id}


class Object:
    def __init__(self, kind, path_id, tree, *, file="CAB-character", externals=()):
        self.type = SimpleNamespace(name=kind)
        self.path_id = path_id
        self.assets_file = SimpleNamespace(name=file, externals=[SimpleNamespace(path=p) for p in externals])
        self.tree = tree

    def read_typetree(self):
        return self.tree


def assets(clips, *, auxiliary=True):
    controller = Object("AnimatorController", -123, {"m_AnimationClips": clips},
                        externals=["archive:/CAB-motion/CAB-motion", "archive:\\CAB-other\\CAB-other"])
    animator = Object("Animator", 10, {"m_Controller": ref(0, -123)})
    objects = [animator, controller]
    if auxiliary:
        objects.insert(0, Object("Animator", 11, {"m_Controller": ref(0, 0)}))
    return SimpleNamespace(objects=objects, deref=lambda owner, pointer: controller)


def test_actor_controller_keeps_source_membership_order_and_signed_ids():
    source = assets([ref(2, -(1 << 62)), ref(0, 41), ref(1, 41)])
    # A separately listed, unrelated controller must never supply the clips.
    source.objects.append(Object("AnimatorController", 99, {"m_AnimationClips": [ref(0, 42)]}))
    result = read_animation_controller(source)
    assert result["animator"] == {"file": "CAB-character", "pathId": "10"}
    assert result["controller"] == {"file": "CAB-character", "pathId": "-123"}
    assert result["clips"] == [
        {"file": "CAB-other", "pathId": str(-(1 << 62))},
        {"file": "CAB-character", "pathId": "41"},
        {"file": "CAB-motion", "pathId": "41"},
    ]
    # Same numeric path ID in another serialized file is a different clip.
    index = {("CAB-character", "41"): "female_S", ("CAB-motion", "41"): "male_S",
             ("CAB-other", str(-(1 << 62))): "idle_L"}
    assert [index[(r["file"], r["pathId"])] for r in result["clips"]] == ["idle_L", "female_S", "male_S"]


def test_missing_controller_stays_null_but_empty_real_controller_stays_present():
    assert read_animation_controller(SimpleNamespace(objects=[])) is None
    null = Object("Animator", 1, {"m_Controller": ref(0, 0)})
    assert read_animation_controller(SimpleNamespace(objects=[null])) is None
    assert read_animation_controller(assets([]))["clips"] == []


def test_ambiguous_animator_or_changed_controller_identity_is_refused():
    source = assets([])
    source.objects.append(Object("Animator", 12, {"m_Controller": ref(0, -123)}))
    with pytest.raises(ValueError, match="one controller-bearing Animator"):
        read_animation_controller(source)
    source.objects.pop()
    source.objects[-1].path_id = -124
    with pytest.raises(ValueError, match="identity mismatch"):
        read_animation_controller(source)


@pytest.mark.parametrize("clips", [
    [ref(0, 0)], [ref(3, 1)], [ref(-1, 1)], [ref(True, 1)], [ref(1, True)],
    [ref(0, 1 << 63)], [ref(1, 4), ref(1, 4)], [None],
])
def test_invalid_or_duplicate_clip_references_do_not_produce_partial_maps(clips):
    with pytest.raises(ValueError):
        read_animation_controller(assets(clips))
