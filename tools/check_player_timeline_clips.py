"""Check exported player timeline clips against the source AnimationClips.

For each package in ``fixture-timeline/avatar-clips/manifest.json``, every
clip row is read back two ways and compared:

* from the source bundle: the AnimationClip at the row's source file and
  pathId, its compiled curves evaluated here directly from the streamed,
  dense and constant stores (this file's own reader, not the exporter's);
* from the exported glb: the animation of that name, its samplers evaluated
  at the same times.

Per clip it reports

* the transform binding hashes that resolve on the clip file's node tree
  (CRC32 of each node's path below the scene root) and, as the negative
  control, on the source bundle's own transform tree (every node's path
  below every one of its ancestors, the widest reading);
* for every glTF channel, whether its target node's path hashes to a
  transform binding of the clip, and the largest difference between the two
  readings over the sampled times, in the file's coordinate contract
  (x reflected: position (-x, y, z), rotation (x, -y, -z, w)).

Rotation keys are compared component by component, as the source stores
them (no normalisation), and so is linear interpolation between them.

Usage: python tools/check_player_timeline_clips.py ASSET_ROOT BUNDLES [--package P]...
       [--out FILE] [--unity-version V]

The bundles do not state their Unity version; ``--unity-version`` (default
the extractor's default, 2022.3.62f3) is the one assumed for them.
"""
import argparse
import bisect
import json
import struct
import sys
import zlib
from pathlib import Path

import UnityPy
import UnityPy.config

SENTINEL = 1e30
WIDTH = {1: 3, 2: 4, 3: 3, 4: 3}
PROPERTY = {"translation": 1, "rotation": 2, "scale": 3}


def slot_bindings(bindings):
    """Curve slot -> (binding, component), in the stores' order."""
    table = []
    for binding in bindings:
        width = WIDTH.get(int(binding["attribute"]), 1) if int(binding["typeID"]) == 4 else 1
        for component in range(width):
            table.append((binding, component))
    return table


class SourceCurves:
    def __init__(self, tree):
        data = tree["m_MuscleClip"]["m_Clip"]["data"]
        streamed = data["m_StreamedClip"]
        words = streamed.get("data") or []
        raw = struct.pack(f"<{len(words)}I", *[int(w) & 0xFFFFFFFF for w in words])
        self.streamed_count = int(streamed.get("curveCount") or 0)
        self.keys = [[] for _ in range(self.streamed_count)]
        offset = 0
        while offset < len(raw):
            time, count = struct.unpack_from("<fi", raw, offset)
            offset += 8
            for _ in range(count):
                index = struct.unpack_from("<i", raw, offset)[0]
                coeff = struct.unpack_from("<4f", raw, offset + 4)
                offset += 20
                if abs(time) < SENTINEL and 0 <= index < self.streamed_count:
                    self.keys[index].append((time, coeff))
        for keys in self.keys:
            keys.sort(key=lambda key: key[0])
        dense = data["m_DenseClip"]
        self.dense_count = int(dense.get("m_CurveCount") or 0)
        self.dense_frames = int(dense.get("m_FrameCount") or 0)
        self.dense_rate = float(dense.get("m_SampleRate") or 0.0)
        self.dense_begin = float(dense.get("m_BeginTime") or 0.0)
        self.dense = [float(v) for v in dense.get("m_SampleArray") or []]
        self.constant = [float(v) for v in (data["m_ConstantClip"].get("data") or [])]

    def slots(self):
        return self.streamed_count + self.dense_count + len(self.constant)

    def key_times(self, slot):
        if slot < self.streamed_count:
            return [time for time, _ in self.keys[slot]]
        if slot < self.streamed_count + self.dense_count and self.dense_rate > 0:
            return [self.dense_begin + i / self.dense_rate for i in range(self.dense_frames)]
        return []

    def value(self, slot, t):
        if slot < self.streamed_count:
            keys = self.keys[slot]
            if not keys:
                return None
            times = [time for time, _ in keys]
            i = bisect.bisect_right(times, t) - 1
            if i < 0:
                return keys[0][1][3]
            time, c = keys[i]
            x = t - time
            return ((c[0] * x + c[1]) * x + c[2]) * x + c[3]
        slot -= self.streamed_count
        if slot < self.dense_count:
            if not self.dense_frames:
                return None
            f = (t - self.dense_begin) * self.dense_rate
            f = min(max(f, 0.0), self.dense_frames - 1)
            i = min(int(f), self.dense_frames - 1)
            j = min(i + 1, self.dense_frames - 1)
            a = self.dense[i * self.dense_count + slot]
            b = self.dense[j * self.dense_count + slot]
            return a + (b - a) * (f - i)
        slot -= self.dense_count
        return self.constant[slot] if slot < len(self.constant) else None


class Glb:
    def __init__(self, path):
        data = Path(path).read_bytes()
        length = struct.unpack_from("<I", data, 12)[0]
        self.doc = json.loads(data[20:20 + length])
        start = 20 + length
        self.bin = data[start + 8:start + 8 + struct.unpack_from("<I", data, start)[0]]

    def accessor(self, index):
        accessor = self.doc["accessors"][index]
        view = self.doc["bufferViews"][accessor["bufferView"]]
        assert accessor["componentType"] == 5126 and "byteStride" not in view
        width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[accessor["type"]]
        offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        flat = struct.unpack_from(f"<{accessor['count'] * width}f", self.bin, offset)
        if width == 1:
            return list(flat)
        return [flat[i:i + width] for i in range(0, len(flat), width)]

    def node_paths(self):
        """Node index -> path below its scene root ('' for the root)."""
        paths = {}
        nodes = self.doc["nodes"]

        def walk(index, path):
            paths[index] = path
            for child in nodes[index].get("children", []):
                name = nodes[child]["name"]
                walk(child, f"{path}/{name}" if path else name)
        for root in self.doc["scenes"][self.doc.get("scene", 0)]["nodes"]:
            walk(root, "")
        return paths


def evaluate(times, values, interpolation, t):
    if interpolation == "CUBICSPLINE":
        points = values[1::3]
        in_tan, out_tan = values[0::3], values[2::3]
    else:
        points = values
    if t <= times[0]:
        return list(points[0])
    if t >= times[-1]:
        return list(points[-1])
    i = bisect.bisect_right(times, t) - 1
    t0, t1 = times[i], times[i + 1]
    s = (t - t0) / (t1 - t0)
    p0, p1 = points[i], points[i + 1]
    if interpolation == "STEP":
        return list(p0)
    if interpolation == "LINEAR":
        return [a + (b - a) * s for a, b in zip(p0, p1)]
    d = t1 - t0
    m0, m1 = out_tan[i], in_tan[i + 1]
    h00, h10 = 2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s
    h01, h11 = -2 * s**3 + 3 * s**2, s**3 - s**2
    return [h00 * a + h10 * d * ma + h01 * b + h11 * d * mb
            for a, b, ma, mb in zip(p0, p1, m0, m1)]


def to_file_space(prop, value):
    if prop == 1:
        return [-value[0], value[1], value[2]]
    if prop == 2:
        return [value[0], -value[1], -value[2], value[3]]
    return list(value)


def source_tree_hashes(env):
    """CRC32 of every transform's path below every one of its ancestors."""
    names, parent, owner = {}, {}, {}
    for obj in env.objects:
        kind = obj.type.name
        if kind == "GameObject":
            names[obj.path_id] = obj.read_typetree().get("m_Name", "")
        elif kind in ("Transform", "RectTransform"):
            tree = obj.read_typetree()
            owner[obj.path_id] = tree["m_GameObject"]["m_PathID"]
            parent[obj.path_id] = tree["m_Father"]["m_PathID"]
    hashes = set()
    for node in owner:
        chain = [node]
        while parent.get(chain[-1]) in owner:
            chain.append(parent[chain[-1]])
        labels = [names.get(owner[n], "") for n in reversed(chain)]
        for start in range(len(labels)):
            hashes.add(zlib.crc32("/".join(labels[start + 1:]).encode()) & 0xFFFFFFFF)
    return hashes


def check_package(root, bundles, entry, sources):
    folder = root / "fixture-timeline" / "avatar-clips"
    index = json.loads((folder / entry["index"]).read_text(encoding="utf-8"))
    glb = Glb(folder / entry["glb"])
    paths = glb.node_paths()
    tree_hashes = {zlib.crc32(p.encode()) & 0xFFFFFFFF: n for n, p in paths.items()}
    animations = {a["name"]: a for a in glb.doc.get("animations", [])}
    results = []
    for row in index["clips"]:
        package = row["sourcePackage"]
        if package not in sources:
            env = UnityPy.load(str(bundles / package))
            clips = {(o.assets_file.name, str(o.path_id)): o for o in env.objects
                     if o.type.name == "AnimationClip"}
            sources[package] = (clips, source_tree_hashes(env))
        clips, own_hashes = sources[package]
        tree = clips[(row["sourceClip"]["file"], str(row["sourceClip"]["pathId"]))].read_typetree()
        bindings = tree["m_ClipBindingConstant"]["genericBindings"]
        transform = sorted({int(b["path"]) & 0xFFFFFFFF for b in bindings if int(b["typeID"]) == 4})
        curves = SourceCurves(tree)
        table = slot_bindings(bindings)
        result = {"clip": row["name"], "sourcePackage": package,
                  "transformHashes": len(transform),
                  "resolvedOnClipFileTree": sum(1 for h in transform if h in tree_hashes),
                  "resolvedOnSourceTree": sum(1 for h in transform if h in own_hashes),
                  "slots": {"bindings": len(table), "stores": curves.slots()},
                  "channels": 0, "channelsOnBinding": 0, "samples": 0, "maxError": {},
                  "maxErrorUnreflected": {}, "valueSpread": {}}
        animation = animations.get(row["name"])
        if animation is None:
            result["missing"] = True
            results.append(result)
            continue
        stop = float(row["sourceStopTime"])
        for channel in animation["channels"]:
            result["channels"] += 1
            node = channel["target"]["node"]
            prop = PROPERTY[channel["target"]["path"]]
            path_hash = zlib.crc32(paths[node].encode()) & 0xFFFFFFFF if node in paths else None
            slots = [i for i, (b, _) in enumerate(table)
                     if int(b["typeID"]) == 4 and int(b["attribute"]) == prop
                     and int(b["path"]) & 0xFFFFFFFF == path_hash]
            if not slots:
                continue
            result["channelsOnBinding"] += 1
            sampler = animation["samplers"][channel["sampler"]]
            times = glb.accessor(sampler["input"])
            values = glb.accessor(sampler["output"])
            key_times = sorted({t for s in slots for t in curves.key_times(s) if 0.0 <= t <= stop})
            probe = sorted(set([stop * k / 16 for k in range(17)] + key_times[:12]
                               + [(a + b) / 2 for a, b in zip(key_times[:12], key_times[1:13])]))
            name = channel["target"]["path"]
            low, high = None, None
            for t in probe:
                raw = [curves.value(s, t) for s in slots]
                source = to_file_space(prop, raw)
                exported = evaluate(times, values, sampler["interpolation"], t)
                error = max(abs(a - b) for a, b in zip(source, exported))
                result["samples"] += 1
                result["maxError"][name] = max(result["maxError"].get(name, 0.0), error)
                # Negative arm: the source values without the file's x reflection.
                unconverted = max(abs(a - b) for a, b in zip(raw, exported))
                result["maxErrorUnreflected"][name] = max(
                    result["maxErrorUnreflected"].get(name, 0.0), unconverted)
                low = source if low is None else [min(a, b) for a, b in zip(low, source)]
                high = source if high is None else [max(a, b) for a, b in zip(high, source)]
            # Signal: how far the sampled source values move over the clip.
            spread = max(b - a for a, b in zip(low, high))
            result["valueSpread"][name] = max(result["valueSpread"].get(name, 0.0), spread)
        results.append(result)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("bundles")
    parser.add_argument("--package", action="append")
    parser.add_argument("--out")
    parser.add_argument("--unity-version", default="2022.3.62f3")
    args = parser.parse_args(argv)
    UnityPy.config.FALLBACK_UNITY_VERSION = args.unity_version
    root, bundles = Path(args.root), Path(args.bundles)
    assert root.is_dir() and bundles.is_dir()
    manifest = json.loads((root / "fixture-timeline/avatar-clips/manifest.json").read_text(encoding="utf-8"))
    sources, report = {}, {}
    for package, entry in sorted(manifest["packages"].items()):
        if args.package and package not in args.package:
            continue
        report[package] = check_package(root, bundles, entry, sources)
    rows = [r for rs in report.values() for r in rs]
    summary = {
        "unityVersion": args.unity_version,
        "packages": len(report), "clips": len(rows),
        "missing": sum(1 for r in rows if r.get("missing")),
        "transformHashes": sum(r["transformHashes"] for r in rows),
        "resolvedOnClipFileTree": sum(r["resolvedOnClipFileTree"] for r in rows),
        "resolvedOnSourceTree": sum(r["resolvedOnSourceTree"] for r in rows),
        "slotCountMismatches": sum(1 for r in rows if r["slots"]["bindings"] != r["slots"]["stores"]),
        "channels": sum(r["channels"] for r in rows),
        "channelsOnBinding": sum(r["channelsOnBinding"] for r in rows),
        "samples": sum(r["samples"] for r in rows),
        "maxError": {k: max((r["maxError"].get(k, 0.0) for r in rows), default=0.0)
                     for k in ("translation", "rotation", "scale")},
        "maxErrorUnreflected": {k: max((r["maxErrorUnreflected"].get(k, 0.0) for r in rows),
                                       default=0.0) for k in ("translation", "rotation", "scale")},
        "valueSpread": {k: max((r["valueSpread"].get(k, 0.0) for r in rows), default=0.0)
                        for k in ("translation", "rotation", "scale")},
        "clipsWithMovingRotation": sum(1 for r in rows if r["valueSpread"].get("rotation", 0.0) > 1e-3),
    }
    document = {"summary": summary, "packages": report}
    if args.out:
        Path(args.out).write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
