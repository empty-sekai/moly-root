"""Export character locomotion data as engine-independent JSON."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


_FRAME_FIELDS = ("t", "muscles", "body_q", "body_p", "transform_rotations")


def _finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _vector(value: Any, size: int) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return None
    if not all(_finite(item) for item in value):
        return None
    return [float(item) for item in value]


def _frame_dict(frame: Any) -> dict[str, Any]:
    if isinstance(frame, Mapping):
        result = dict(frame)
        aliases = {"time": "t", "bodyQ": "body_q", "bodyP": "body_p",
                   "transformRotations": "transform_rotations"}
        for old, new in aliases.items():
            if old in result and new not in result:
                result[new] = result.pop(old)
        return result
    if isinstance(frame, (list, tuple)) and len(frame) == 5:
        t, muscles, body_q, body_p, transforms = frame
        return {"t": t, "muscles": muscles, "body_q": body_q,
                "body_p": body_p, "transform_rotations": transforms}
    raise TypeError("a frame must be a mapping or a five-item sampled frame")


def normalize_record(record: Any, *, name: str | None = None,
                     rate: float | None = None,
                     frames: Iterable[Any] | None = None) -> dict[str, Any]:
    """Normalize a sampled clip into the canonical record shape."""
    if (isinstance(record, (list, tuple)) and len(record) == 2
            and isinstance(record[1], Mapping)):
        name = record[0] if name is None else name
        record = record[1]
    if not isinstance(record, Mapping):
        raise TypeError("record must be a mapping or a (name, mapping) pair")
    record_name = record.get("name", name)
    record_rate = record.get("rate", rate)
    record_frames = record.get("frames", frames)
    if record_name is None or record_rate is None or record_frames is None:
        raise ValueError("record requires name, rate, and frames")
    normalized_frames = []
    for raw in record_frames:
        frame = _frame_dict(raw)
        normalized_frames.append({key: frame.get(key) for key in _FRAME_FIELDS})
    return {"name": str(record_name), "rate": float(record_rate),
            "frames": normalized_frames}


def _check(record: str, criterion: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"record": record, "criterion": criterion, "ok": bool(ok),
            "detail": detail}


def validate_record(record: Mapping[str, Any], *, criteria: Any = None) -> list[dict[str, Any]]:
    """Return structured checks without raising for malformed record data."""
    name = str(record.get("name", "<unnamed>"))
    checks = [_check(name, "record.name.non_empty",
                     isinstance(record.get("name"), str)
                     and bool(record["name"].strip()), None)]
    rate = record.get("rate")
    checks.append(_check(name, "record.rate.positive_finite",
                         _finite(rate) and float(rate) > 0,
                         {"observed": rate}))
    raw_frames = record.get("frames")
    frames = list(raw_frames) if isinstance(raw_frames, (list, tuple)) else []
    checks.append(_check(name, "record.frames.non_empty", bool(frames),
                         {"observed": len(frames)}))
    previous_t = None
    for index, raw in enumerate(frames):
        try:
            frame = _frame_dict(raw)
        except (TypeError, ValueError) as exc:
            checks.append(_check(name, f"frame[{index}].shape", False, str(exc)))
            continue
        t = frame.get("t")
        monotonic = (_finite(t) and
                     (previous_t is None or float(t) >= previous_t))
        checks.append(_check(name, f"frame[{index}].time.monotonic", monotonic,
                             {"observed": t}))
        if _finite(t):
            previous_t = float(t)

        muscles = frame.get("muscles")
        muscle_ok = isinstance(muscles, Mapping)
        detail: Any = {"observed": "mapping" if muscle_ok else type(muscles).__name__}
        if muscle_ok:
            for key, value in muscles.items():
                try:
                    muscle_index = int(key)
                    key_ok = str(muscle_index) == str(key)
                except (TypeError, ValueError):
                    key_ok, muscle_index = False, -1
                if not key_ok or not 0 <= muscle_index <= 94:
                    muscle_ok = False
                    detail = {"index": key, "expected": "0..94"}
                    break
                if not _finite(value):
                    muscle_ok = False
                    detail = {"index": key, "value": value, "expected": "finite"}
                    break
        checks.append(_check(name, f"frame[{index}].muscles.valid", muscle_ok, detail))

        for field, size in (("body_q", 4), ("body_p", 3)):
            vector = _vector(frame.get(field), size)
            ok = vector is not None
            if field == "body_q" and ok:
                ok = any(abs(item) > 0 for item in vector)
            checks.append(_check(name, f"frame[{index}].{field}.valid", ok,
                                 {"expectedLength": size, "observed": frame.get(field)}))

        transforms = frame.get("transform_rotations")
        transform_ok = isinstance(transforms, Mapping)
        if transform_ok:
            transform_ok = all(_vector(value, 4) is not None
                               for value in transforms.values())
        checks.append(_check(name, f"frame[{index}].transform_rotations.valid",
                             transform_ok, {"observed": len(transforms)
                                            if isinstance(transforms, Mapping) else None}))
    if criteria is not None:
        result = criteria(record) if callable(criteria) else criteria
        if isinstance(result, Mapping):
            checks.append(dict(result))
        elif isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
            checks.extend(dict(item) for item in result)
        else:
            checks.append(_check(name, "consumer.criteria", bool(result), None))
    return checks


def build_document(records: Iterable[Any], *, parameters: Mapping[str, Any] | None = None,
                   bindings: Iterable[Mapping[str, Any]] | None = None,
                   ik: Mapping[str, Any] | None = None, criteria: Any = None,
                   metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a JSON-serializable locomotion document."""
    normalized = [normalize_record(record) for record in records]
    checks: list[dict[str, Any]] = []
    names: set[str] = set()
    per_record: dict[str, bool] = {}
    for record in normalized:
        record_checks = validate_record(record, criteria=criteria)
        checks.extend(record_checks)
        duplicate = record["name"] in names
        checks.append(_check(record["name"], "record.name.unique", not duplicate,
                             {"name": record["name"]}))
        names.add(record["name"])
        per_record[record["name"]] = (not duplicate and
            all(item.get("ok", False) for item in record_checks))
    valid = sum(per_record.values())
    document: dict[str, Any] = {
        "version": 1,
        "kind": "locomotion",
        "binding": "mecanim-sampled-v1",
        "records": normalized,
        "parameters": dict(parameters) if parameters is not None else {},
        "bindings": list(bindings) if bindings is not None else [],
        "ik": dict(ik) if ik is not None else None,
        "counts": {"records": len(normalized), "valid": valid,
                   "invalid": len(normalized) - valid},
        "checks": checks,
    }
    if metadata is not None:
        document["metadata"] = dict(metadata)
    return document


def export_json(records: Iterable[Any], out: str | os.PathLike[str], *,
                parameters: Mapping[str, Any] | None = None,
                bindings: Iterable[Mapping[str, Any]] | None = None,
                ik: Mapping[str, Any] | None = None, criteria: Any = None,
                metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write a locomotion document and return a runtime-only report."""
    document = build_document(records, parameters=parameters, bindings=bindings,
                              ik=ik, criteria=criteria, metadata=metadata)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1, allow_nan=False)
        handle.write("\n")
    return {"out": str(path), **document["counts"],
            "checks": len(document["checks"])}
