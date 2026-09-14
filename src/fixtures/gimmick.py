"""Source controller, clip events and property curves for furniture gimmicks.

This is metadata extraction, not an Animator emulator. State and clip names do
not imply an on/off value. Motion indices, transition conditions, event payloads
and non-Transform bindings retain their authored identities. The caller selects
packages using the existing manifest flow and supplies its PackageStore; this
module neither searches for packages nor rewrites furniture geometry.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from core.jsonio import write_json
from core.animator import (_identity, _reference, _json_identity, _node_tree,
                           _controller, _clip)
from fixtures.house_views import read as read_house_views


def extract_package(store, name):
    """Extract only the explicitly selected package; never infer game state."""
    package = store.package(name)
    if package is None:
        raise FileNotFoundError(f"selected furniture package is missing: {name}")
    animators, fixture_views, controllers, clips, materials = [], [], {}, [], []
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind == "MonoBehaviour" and record.script_of(path_id) == "FixtureView":
                tree = record.tree(path_id)
                fixture_views.append({"source": _identity(record, path_id),
                                      "gameObject": _reference(store, record, tree["m_GameObject"]),
                                      "animator": _reference(store, record, tree.get("_animator")),
                                      "fields": _json_identity(tree)})
            if kind != "Animator":
                continue
            tree = record.tree(path_id)
            owner = store.follow(record, tree["m_GameObject"])
            if owner is None:
                raise ValueError("Animator has no resolvable owner GameObject")
            controller_ref = _reference(store, record, tree["m_Controller"])
            animator = {"source": _identity(record, path_id),
                        "gameObject": _identity(*owner), "fields": _json_identity(tree),
                        "controller": controller_ref, "nodes": _node_tree(store, *owner)}
            animators.append(animator)
            controller = store.follow(record, tree["m_Controller"])
            if controller is not None:
                key = (controller[0].archive, controller[1])
                controllers[key] = _controller(store, *controller)
    for record in package.files:
        for path_id, kind in record.kinds.items():
            if kind == "AnimationClip":
                clips.append(_clip(record, path_id, animators))
            elif kind == "Material":
                tree = record.tree(path_id)
                materials.append({"source": _identity(record, path_id),
                                  "shader": _reference(store, record, tree["m_Shader"]),
                                  "savedProperties": _json_identity(tree.get("m_SavedProperties")),
                                  "keywords": tree.get("m_ShaderKeywords"),
                                  "validKeywords": tree.get("m_ValidKeywords")})
    return {"name": name, "fixtureViews": fixture_views,
            "houseViews": read_house_views(store, package), "animators": animators,
            "controllers": list(controllers.values()), "clips": clips, "materials": materials,
            "coverage": {"controllerCount": len(controllers), "animatorCount": len(animators),
                         "clipCount": len(clips),
                         "eventCount": sum(len(clip["events"]) for clip in clips),
                         "decodedCurveSlots": sum(len(clip["curves"]) for clip in clips),
                         "unresolvedCurveSlots": sum(len(clip["accounting"]["unresolved"])
                                                     for clip in clips)}}


def extract(store, names, output):
    """Write selected-package metadata to a caller-owned candidate path.

    The normal extraction orchestrator owns package selection and registration.
    This entry point is intentionally usable without another independent CLI.
    """
    names = list(names)
    if len(names) != len(set(names)):
        raise ValueError("duplicate selected furniture package")
    packages = [extract_package(store, name) for name in names]
    sources = []
    for name in names:
        path = store.paths.get(name)
        if path is not None:
            sources.append({"package": name,
                            "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()})
    document = {"version": 1, "kind": "fixture-gimmick-metadata",
                "semantics": {"stateSelection": "controller conditions and motion indices, not names",
                              "events": "authored clip event time and payload; no on/off coercion",
                              "curvePoints": "cubic polynomial coefficients, linear samples, or constants",
                              "objectIdentity": "serialized-file identity and exact signed pathId"},
                "sources": sources, "packages": packages}
    write_json(output, document, indent=None)
    return document
