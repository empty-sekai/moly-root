"""Resuming a staged runtime cannot silently ignore its animation policy."""
import pytest

from core.runtime_pack import _step_cached, prepare_runtime


def test_actor_stage_needs_matching_revision_and_encoding_not_only_completion():
    label = "source-qualified-actor-libraries-v2"
    snorm = {"motionStorageRevision": 2, "rotationEncoding": "snorm16"}
    state = {"steps": {label: {"files": 2}}}
    assert not _step_cached(state, label, snorm)
    state["stepIdentities"] = {label: snorm}
    assert _step_cached(state, label, snorm)
    float32 = {**snorm, "rotationEncoding": "float32"}
    assert not _step_cached(state, label, float32)
    state["stepIdentities"][label] = float32
    assert not _step_cached(state, label, snorm)
    assert not _step_cached(state, label, {**float32, "motionStorageRevision": 3})
    assert _step_cached(state, label)  # Existing unversioned stages are unchanged.


def test_unknown_encoding_is_rejected_before_filesystem_access():
    with pytest.raises(ValueError, match="rotation_encoding"):
        prepare_runtime("missing", "missing", "missing", rotation_encoding="invalid")
