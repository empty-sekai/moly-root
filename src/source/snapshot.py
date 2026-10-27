"""Portable source receipts: content identity is independent of its locator.

Receipts and plans can contain local paths. They are private build inputs,
not runtime artifacts. A task verifies the bytes it actually reads instead
of trusting file times or a previously computed hash.
"""
from pathlib import Path

from core.content import content_hash, document_hash


def file_input(role, path):
    path = Path(path).resolve(strict=True)
    data = path.read_bytes()
    return {"role": role, "sha256": content_hash(data), "size": len(data),
            "locators": [{"kind": "file", "path": str(path)}]}


def read_inputs(inputs):
    frozen = {}
    for item in inputs:
        failures = []
        for locator in item["locators"]:
            try:
                if locator.get("kind") != "file":
                    raise ValueError("unsupported input locator")
                data = Path(locator["path"]).read_bytes()
                if len(data) != item["size"] or content_hash(data) != item["sha256"]:
                    raise ValueError("input content changed after planning")
                frozen[item["role"]] = data
                break
            except (OSError, ValueError) as exc:
                failures.append(str(exc))
        else:
            raise ValueError(f"cannot read verified input {item['role']}: {'; '.join(failures)}")
    return frozen


def snapshot(inputs, *, region, version):
    if region not in {"jp", "cn", "tw", "en", "kr"} or not version:
        raise ValueError("source requires a known region and an explicit version")
    inputs = sorted(inputs, key=lambda item: item["role"])
    if len({item["role"] for item in inputs}) != len(inputs):
        raise ValueError("duplicate source input role")
    identity = {"schema": "moly-source/1", "region": region, "version": version,
                "inputs": [{k: item[k] for k in ("role", "sha256", "size")} for item in inputs]}
    return {**identity, "revision": document_hash(identity), "inputs": inputs}
