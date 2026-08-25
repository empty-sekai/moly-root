"""Export the shared motion naming contract for external consumers."""

from __future__ import annotations

import json
import re
from pathlib import Path

import UnityPy
import UnityPy.config

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"


_BASE = re.compile(r"^mov_(?P<family>[^_]+)_(?P<action>.+?)(?P<num>\d{3})(?P<side>[lr]?)$")
_ALIAS = re.compile(r"^\s*(w_[A-Za-z0-9_]+)\s*=\s*[\"']([^\"']+)[\"']", re.M)


def _clips(bundle):
    env = UnityPy.load(bundle)
    out = {}
    for obj in env.objects:
        if obj.type.name != "AnimationClip":
            continue
        tt = obj.read_typetree()
        name = str(tt.get("m_Name", ""))
        if not name:
            continue
        rate = float(tt.get("m_SampleRate", 0.0) or 0.0)
        duration = float(tt.get("m_StopTime", 0.0) or 0.0)
        muscle = tt.get("m_MuscleClip")
        if isinstance(muscle, dict):
            duration = float(muscle.get("m_StopTime", duration) or duration)
        out[name] = {
            "frames": max(1, int(round(duration * rate)) + 1) if rate else 0,
            "duration": duration,
            "loop": name.endswith("_L"),
        }
    return out


def _controller(bundle, motion_bundle, clip_names):
    env = UnityPy.load(bundle, motion_bundle)
    controller = next(o for o in env.objects if o.type.name == "AnimatorController")
    tt = controller.read_typetree()
    tos = {int(path): str(name) for path, name in tt.get("m_TOS", [])}
    refs = tt.get("m_AnimationClips", [])
    by_path = {int(o.path_id): o for o in env.objects if o.type.name == "AnimationClip"}
    clip_refs = []
    for ref in refs:
        obj = by_path.get(int(ref.get("m_PathID", 0)))
        clip_refs.append(str(obj.read_typetree().get("m_Name", "")) if obj else "")
    states = {}
    for machine in tt["m_Controller"]["m_StateMachineArray"]:
        for state in machine["data"]["m_StateConstantArray"]:
            s = state["data"]
            path_id = int(s["m_PathID"])
            trees = s.get("m_BlendTreeConstantArray", [])
            if not trees or not trees[0].get("data", {}).get("m_NodeArray"):
                states[tos.get(path_id, str(path_id))] = {"clip": None, "exists": False}
                continue
            node = trees[0]["data"]["m_NodeArray"][0]["data"]
            clip_id = int(node["m_ClipID"])
            clip = clip_refs[clip_id] if 0 <= clip_id < len(clip_refs) else ""
            states[tos.get(path_id, str(path_id))] = {"clip": clip, "exists": clip in clip_names}
    return states


def _aliases(lua_dir):
    root = Path(lua_dir)
    aliases = {}
    for path in sorted(root.glob("*.lua")):
        for alias, base in _ALIAS.findall(path.read_text(encoding="utf-8")):
            aliases[alias] = base
    return aliases


def build_motion_index(controller_bundle, motion_bundle, lua_dir):
    """Build the JSON-compatible three-namespace motion index."""
    clips = _clips(motion_bundle)
    states = _controller(controller_bundle, motion_bundle, set(clips))
    aliases = _aliases(lua_dir)
    bases = {}
    prefixes, actions = set(), set()
    for name, meta in sorted(clips.items()):
        if not name.startswith("mov_") or "_" not in name:
            continue
        stem, suffix = name.rsplit("_", 1)
        if suffix not in {"E", "L", "S", "O"}:
            continue
        base = stem
        match = _BASE.match(stem)
        if match:
            prefixes.add(match.group("family"))
            actions.add(match.group("action"))
        bases.setdefault(base, {})[suffix] = {"clip": name, **meta}
    unmatched = sorted(alias for alias, base in aliases.items() if base not in bases)
    return {
        "schema": 1,
        "bases": bases,
        "aliases": {a: (b if b in bases else None) for a, b in sorted(aliases.items())},
        "states": states,
        "enums": {"prefixes": sorted(prefixes), "actions": sorted(actions)},
        "match": {"total": len(aliases), "matched": len(aliases) - len(unmatched),
                  "unmatched": unmatched},
    }


def write_motion_index(controller_bundle, motion_bundle, lua_dir, output):
    doc = build_motion_index(controller_bundle, motion_bundle, lua_dir)
    Path(output).write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    return doc
