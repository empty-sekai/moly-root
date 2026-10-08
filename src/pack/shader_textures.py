"""Check the concrete runtime payload closure of exported shader textures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .paths import asset_path
from .shader_programs import SOURCE_SHADER_ROOTS


def validate_shader_texture_payloads(source, members):
    """Fail publication if a descriptor names absent, changed or omitted bytes.

    TextureLibrary emits paths relative to its phenomena or fixture-particles-v2
    root, not each descriptor. Unavailable source decodes remain valid diagnostics, while
    any retained encoded payload and every successful decode must be complete.
    """
    source = Path(source).resolve()
    descriptors = references = 0
    checked = {}
    prefixes = tuple(root + "/shader-textures/" for root in SOURCE_SHADER_ROOTS)
    for name in members:
        if not name.startswith(prefixes) or not name.endswith(".texture.json"):
            continue
        asset_root = name.split("/", 1)[0]
        descriptors += 1
        document = json.loads(asset_path(source, name).read_bytes())
        if document.get("schemaVersion") != 1 or document.get("status") not in {"decoded", "unavailable"}:
            raise ValueError(f"invalid shader texture descriptor: {name}")
        for field in ("encoded", "pixels"):
            reference = document.get(field)
            if reference is None and document["status"] == "unavailable":
                continue
            if not isinstance(reference, dict):
                raise ValueError(f"missing shader texture {field} payload: {name}")
            relative = reference.get("file")
            suffix = ".rgba8" if field == "pixels" else ".texture.bin"
            if (not isinstance(relative, str) or
                    not re.fullmatch(r"shader-textures/[a-f0-9]{64}" + re.escape(suffix), relative)):
                raise ValueError(f"invalid shader texture {field} path: {name}")
            logical = asset_root + "/" + relative
            payload = asset_path(source, logical)
            # Resolving a symlink must not silently change an immutable URL.
            if payload.relative_to(source).as_posix() != logical or logical not in members:
                raise ValueError(f"shader texture payload omitted from runtime: {logical}")
            expected_bytes, expected_hash = reference.get("bytes"), reference.get("sha256")
            if (type(expected_bytes) is not int or expected_bytes <= 0 or
                    not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash) or
                    PurePosixPath(relative).name != expected_hash + suffix):
                raise ValueError(f"invalid shader texture {field} identity: {name}")
            if logical not in checked:
                raw = payload.read_bytes()
                checked[logical] = len(raw), hashlib.sha256(raw).hexdigest()
            if checked[logical] != (expected_bytes, expected_hash):
                raise ValueError(f"shader texture payload hash/size mismatch: {logical}")
            references += 1
    return {"descriptors": descriptors, "references": references, "payloads": len(checked)}
