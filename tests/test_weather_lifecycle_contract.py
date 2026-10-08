"""Host lifecycle bindings, separate from per-particle simulation and sky fading."""
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from phenomena.effects import _effective_rotation, effective_lifecycle


def test_added_component_has_source_constructor_grace_time():
    for kind in ("sky", "camera", "site"):
        assert effective_lifecycle(kind, []) == {
            "stopBehavior": "stopEmitting", "timeUntilDestroy": 2.0,
            "delaySource": "addedComponentConstructor"}


@pytest.mark.parametrize("delay", [0.0, 2.0, 3.75])
def test_serialized_root_delay_is_not_replaced_by_a_default(delay):
    result = effective_lifecycle("camera", [{"node": "", "timeUntilDestroy": delay}])
    assert result["timeUntilDestroy"] == delay
    assert result["delaySource"] == "serializedRoot"


@pytest.mark.parametrize("delay", [None, -1, float("nan"), float("inf"), "2"])
def test_unresolved_authored_delay_is_not_guessed(delay):
    assert effective_lifecycle("sky", [{"node": "", "timeUntilDestroy": delay}]) is None


def test_child_component_is_not_the_root_component_queried_by_emit_effect():
    child = [{"node": "child", "timeUntilDestroy": 7.0, "rotationType": "normal"}]
    assert effective_lifecycle("camera", child)["timeUntilDestroy"] == 2.0
    assert _effective_rotation("camera", child) == "fix"


def test_existing_component_setup_normal_overrides_serialized_rotation():
    assert _effective_rotation("camera", [{"node": "", "rotationType": "fix"}]) == "normal"
    assert _effective_rotation("camera", []) == "fix"
    for kind in ("sky", "site"):
        assert _effective_rotation(kind, []) == "normal"
        assert _effective_rotation(kind, [{"node": "", "rotationType": "fix"}]) == "normal"


def test_unclassified_prefabs_do_not_acquire_a_factory_lifecycle():
    assert effective_lifecycle("other", []) is None
    assert _effective_rotation("other", []) is None
    assert effective_lifecycle("sky", [{"node":"", "timeUntilDestroy":2.0}]*2) is None
