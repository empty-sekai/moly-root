"""Reproducible source coverage census, distinct from runtime acceptance.

Counts are obligations, never a claim that a renderer consumes them. Run with
python -m phenomena.coverage ROOT --out REPORT.json after formal extraction.
"""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import argparse
import json


def emitter_inventory(effect):
    """Keep every source renderer, including disabled and indirectly driven ones.

    Names identify source nodes; they do not infer behavior or acceptance.
    Hierarchy state is independent of renderer.enabled and runtime support.
    """
    nodes = {node["path"]: node for node in effect.get("nodes", [])}
    rows = []
    for ordinal, particle in enumerate(effect.get("particles", [])):
        path = particle["node"]
        current, visited, chain = path, set(), []
        hierarchy = "active"
        while current is not None:
            if current in visited:
                raise ValueError(f"cyclic effect hierarchy at {path!r}")
            visited.add(current)
            node = nodes.get(current)
            if node is None:
                hierarchy = "unresolved"
                break
            chain.append(current)
            if node.get("active") is False:
                hierarchy = "inactive"
            elif node.get("active") is not True and hierarchy != "inactive":
                hierarchy = "unresolved"
            current = node.get("parent")
        renderer = particle.get("renderer") or {}
        material = renderer.get("material") or {}
        system = particle.get("system") or {}
        rows.append({
            "ordinal": ordinal, "node": path, "ancestorChain": chain,
            "systemPathId": particle.get("systemPathId"),
            "rendererPathId": particle.get("rendererPathId"),
            "hierarchyState": hierarchy, "rendererEnabled": renderer.get("enabled"),
            "renderMode": renderer.get("renderMode"), "alignment": renderer.get("alignment"),
            "material": material.get("name"), "shader": material.get("shader"),
            "program": material.get("shaderProgram"),
            "passes": material.get("shaderPasses"),
            "streams": renderer.get("vertexStreams"),
            "meshes": renderer.get("meshes"),
            "trailMaterial": renderer.get("trailMaterial"),
            "systemFields": sorted(system),
            "emission": system.get("emission"),
            "subEmitters": system.get("subEmitters"),
            "inheritVelocity": system.get("inheritVelocity"),
            "trails": system.get("trails"),
            "shape": system.get("shape"),
            "sourceSystemState": {key: system.get(key) for key in
                                  ("playOnAwake", "looping", "prewarm", "simulationSpace",
                                   "simulationSpeed", "useUnscaledTime", "cullingMode", "stopAction",
                                   "randomSeed", "autoRandomSeed", "ringBufferMode", "ringBufferLoopRange")},
            "sourceRendererState": {key: renderer.get(key) for key in
                                    ("sortMode", "sortingFudge", "sortingOrder", "flip",
                                     "applyActiveColorSpace", "allowRoll")},
            "runtimeAcceptance": "not_verified",
        })
    return rows


def inventory(root):
    root = Path(root).resolve()
    receipts = {}

    def read(file):
        path = (root / file).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"artifact path escapes source root: {file}")
        data = path.read_bytes()
        receipts[file] = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
        return json.loads(data)

    index = read("index.json")
    if index.get("version") != 1 or not index.get("phenomena"):
        raise ValueError("unsupported or empty phenomenon index")
    summary = index.get("summary") or {}
    gaps = summary.get("unsupported") or []
    if not isinstance(gaps, list):
        raise ValueError("source coverage requires individual unsupported receipts")
    rows = []
    post_classes = Counter()
    effect_kinds = Counter()
    for name, entry in sorted(index["phenomena"].items(), key=lambda pair: (pair[1].get("id", -1), pair[0])):
        configs, posts = {}, {}
        for site, variant in [("global", entry), *sorted(entry.get("overrides", {}).items())]:
            if variant.get("config"):
                config = read(variant["config"])
                configs[site] = {"file": variant["config"], "rendererType": config.get("rendererType"),
                                 "sections": sorted(config),
                                 "fields": {k: sorted(v) for k, v in config.items() if isinstance(v, dict)}}
            if variant.get("postprocess"):
                post = read(variant["postprocess"])
                components = []
                for component in post["components"]:
                    post_classes[component["class"]] += 1
                    components.append({"class": component["class"], "active": component["active"],
                                       "overrides": sorted(k for k, v in component.get("parameters", {}).items()
                                                           if isinstance(v, dict) and v.get("overrideState"))})
                posts[site] = {"file": variant["postprocess"], "components": components}
        fx = read(entry["fx"]["file"])
        effects = []
        for key, effect in sorted(fx["effects"].items()):
            effect_kinds[effect["kind"]] += 1
            particles = effect.get("particles") or []
            effects.append({"key": key, "kind": effect["kind"], "site": effect.get("site"),
                            "variant": effect.get("variant"), "nodes": len(effect.get("nodes") or []),
                            "emitters": len(particles),
                            "emitterInventory": emitter_inventory(effect),
                            "sourceNodes": effect.get("nodes") or [],
                            "source": effect.get("source"),
                            "collisionSchemaVersion": effect.get("collisionSchemaVersion"),
                            "colliders": effect.get("colliders"),
                            "animationSchemaVersion": effect.get("animationSchemaVersion"),
                            "animationComponents": effect.get("animationComponents"),
                            "omittedComponents": effect.get("omitted") or [],
                            "rendererModes": dict(sorted(Counter(str(p.get("renderer", {}).get("renderMode")) for p in particles).items())),
                            "systemFields": sorted({k for p in particles for k in p.get("system", {})}),
                            "lifecycle": effect.get("lifecycle"), "effectors": effect.get("effectors") or []})
        timeline = None
        animations = None
        if entry.get("animations"):
            document = read(entry["animations"]["file"])
            animations = {"file": entry["animations"]["file"],
                          "schemaVersion": document.get("schemaVersion"),
                          "summary": document.get("summary"),
                          "runtimeAcceptance": "not_verified"}
        if entry.get("timeline"):
            document = read(entry["timeline"]["file"])
            timeline = {"file": entry["timeline"]["file"], "schemaVersion": document.get("schemaVersion"),
                        "source": document.get("source"), "duration": document["duration"],
                        "tracks": [{"id": t.get("id"), "parentId": t.get("parentId"), "class": t["class"],
                                    "target": t.get("target"), "clips": len(t["clips"]), "source": t.get("source")}
                                   for t in document["tracks"]], "unsupported": document.get("unsupported")}
        rows.append({"id": entry.get("id"), "assetKey": name, "master": entry.get("master"),
                     "config": configs, "post": posts, "ramp": entry.get("ramp"),
                     "effects": effects, "models": entry.get("models") or [], "timeline": timeline,
                     "animations": animations,
                     "audio": {"bgms": entry.get("bgms"), "siteSounds": entry.get("siteSounds")},
                     "ui": {"icon": entry.get("icon"), "sourceDisplayMetadata": entry.get("master") is not None},
                     "unsupported": [gap for gap in gaps if gap.get("phenomenon") == name],
                     "runtimeAcceptance": {"state": "not_recorded", "backends": []}})
    return {"schemaVersion": 1, "scope": "source_artifact_inventory_not_runtime_acceptance",
            "counts": {"phenomena": len(rows), "effects": sum(len(r["effects"]) for r in rows),
                       "emitters": sum(e["emitters"] for r in rows for e in r["effects"]),
                       "timelines": sum(r["timeline"] is not None for r in rows),
                       "sourceIcons": len(index.get("icons") or []), "unsupported": len(gaps)},
            "environmentController": index.get("environmentController"),
            "postClasses": dict(sorted(post_classes.items())), "effectKinds": dict(sorted(effect_kinds.items())),
            "phenomena": rows, "unassignedGaps": [g for g in gaps if not g.get("phenomenon")],
            "missing": summary.get("missing"), "sourceArtifacts": dict(sorted(receipts.items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = inventory(args.root)
    args.out.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
