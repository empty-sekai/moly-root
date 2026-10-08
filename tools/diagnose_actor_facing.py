#!/usr/bin/env python3
"""Read-only numeric Root/Hips facing report for source-qualified actor clips."""
import json
import math
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.quat import qmul, qconj, qrotv


def load(path):
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20+count])
    offset = 20 + count
    count, kind = struct.unpack_from("<I4s", data, offset)
    assert kind == b"BIN\x00"
    return doc, data[offset+8:offset+8+count]


def accessor(doc, binary, index):
    a = doc["accessors"][index]
    assert a["componentType"] == 5126
    widths = {"SCALAR": 1, "VEC3": 3, "VEC4": 4, "MAT4": 16}
    width = widths[a["type"]]
    v = doc["bufferViews"][a["bufferView"]]
    offset = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = v.get("byteStride", width * 4)
    return [struct.unpack_from(f"<{width}f", binary, offset+i*stride) for i in range(a["count"])]


def inspect(path, filters=()):
    doc, binary = load(path)
    nodes = doc["nodes"]
    parents = {c:i for i,n in enumerate(nodes) for c in n.get("children", [])}
    relevant = [i for i,n in enumerate(nodes) if n.get("name") in ("Root", "Hips", "Spine", "Spine1", "Head")]
    report = {"path": str(path), "binding": doc["asset"].get("extras"),
              "rest": [{"index": i, "name": nodes[i]["name"], "parent": nodes[parents[i]].get("name") if i in parents else None,
                        "translation": nodes[i].get("translation"), "rotation": nodes[i].get("rotation")}
                       for i in relevant], "animations": []}
    for animation in doc.get("animations", []):
        if filters and not any(text in animation.get("name", "") for text in filters):
            continue
        anim = {"name": animation.get("name"), "channels": []}
        for channel in animation["channels"]:
            node = channel["target"]["node"]
            if node not in relevant:
                continue
            sampler = animation["samplers"][channel["sampler"]]
            times = accessor(doc, binary, sampler["input"])
            values = accessor(doc, binary, sampler["output"])
            row = {"node": nodes[node]["name"], "path": channel["target"]["path"],
                   "samples": [[times[i][0], values[i]] for i in (0, len(times)//2, len(times)-1)]}
            if row["path"] == "rotation":
                row["deltaFromRestZ"] = [qrotv(qmul(values[i], qconj(nodes[node].get("rotation", [0,0,0,1]))), (0,0,1))
                                         for i in (0, len(times)//2, len(times)-1)]
            anim["channels"].append(row)
        report["animations"].append(anim)
    return report


if __name__ == "__main__":
    print(json.dumps(inspect(Path(sys.argv[1]), sys.argv[2:]), indent=2))
