"""Manifest-driven AssetBundle download, decryption, and extraction."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAGIC = b"\x10\x00\x00\x00"


class DecryptError(ValueError):
    pass


def decrypt_bundle(data: bytes) -> bytes:
    """Remove the wrapper and invert the first five bytes of each eight-byte block."""
    if data.startswith(b"UnityFS"):
        return data
    if not data.startswith(MAGIC):
        raise DecryptError(f"unknown bundle header: {data[:8].hex()}")
    body = bytearray(data[4:])
    for start in range(0, min(128, len(body)), 8):
        for index in range(start, min(start + 5, len(body))):
            body[index] = (~body[index]) & 0xFF
    if not body.startswith(b"UnityFS"):
        raise DecryptError(f"decrypted payload is not UnityFS: {bytes(body[:7])!r}")
    return bytes(body)


@dataclass(frozen=True)
class BundleEntry:
    bundle_name: str
    download_path: str
    cache_file_name: str
    file_size: int
    crc: int
    dependencies: tuple[str, ...]
    raw: dict

    @classmethod
    def from_dict(cls, value: dict, key: str | None = None) -> "BundleEntry":
        name = str(value.get("bundleName") or value.get("name") or key or "")
        return cls(name, str(value.get("downloadPath") or ""),
                   str(value.get("cacheFileName") or name.rsplit("/", 1)[-1]),
                   int(value.get("fileSize") or 0), int(value.get("crc") or 0),
                   tuple(str(item) for item in (value.get("dependencies") or ())), value)


class Manifest:
    def __init__(self, bundles: dict | list, version: str | None = None):
        self.version = version
        if isinstance(bundles, dict):
            self.entries = {entry.bundle_name: entry for key, value in bundles.items()
                            for entry in [BundleEntry.from_dict(value, key)]}
        else:
            self.entries = {entry.bundle_name: entry for entry in (BundleEntry.from_dict(v) for v in bundles)}

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Manifest":
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
        if isinstance(payload, dict):
            return cls(payload.get("bundles", payload.get("manifest", payload)), payload.get("version"))
        return cls(payload)

    def roots(self) -> list[str]:
        return sorted(name for name in self.entries
                      if name.startswith("mysekai/character/mdl_sd_")
                      or name.startswith("mysekai/effect/emoticon/")
                      or name in {"mysekai/character_motion", "mysekai/character_settings",
                                  "mysekai/character_alone_action",
                                  "mysekai/shader",
                                  "mysekai/talk/scenario/talk"})

    def required_bundles(self, roots: Iterable[str]) -> list[BundleEntry]:
        state: dict[str, int] = {}
        ordered: list[BundleEntry] = []
        stack: list[str] = []

        def visit(name: str) -> None:
            mark = state.get(name, 0)
            if mark == 1:
                raise ValueError("dependency cycle: " + " -> ".join(stack[stack.index(name):] + [name]))
            if mark == 2:
                return
            if name not in self.entries:
                raise KeyError(f"manifest missing dependency: {name}")
            state[name] = 1; stack.append(name)
            for dependency in self.entries[name].dependencies:
                visit(dependency)
            stack.pop(); state[name] = 2; ordered.append(self.entries[name])

        for root in roots:
            visit(root)
        return ordered


def flatten(name: str) -> str:
    return name.replace("/", "__")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_base(asset_base_url: str) -> str:
    """Accept the caller's prefix as opaque; only trailing separators are trimmed."""
    prefix = (asset_base_url or "").strip()
    if not prefix:
        raise ValueError("an asset base URL is required; no endpoint is built in")
    return prefix.rstrip("/")


def build_download_url(asset_base_url: str, entry: BundleEntry) -> str:
    tail = [part.strip("/") for part in (entry.download_path, entry.bundle_name) if part.strip("/")]
    return "/".join([asset_base(asset_base_url), *tail])


def expected_hash(entry: BundleEntry) -> str | None:
    for key in ("hash", "sha256", "sha256Hash"):
        value = entry.raw.get(key)
        if isinstance(value, str) and len(value) == 64:
            return value.lower()
    return None


def download_one(entry: BundleEntry, asset_base_url: str, raw_dir: Path, retries: int = 4) -> dict:
    url = build_download_url(asset_base_url, entry)
    target = raw_dir / flatten(entry.bundle_name)
    part = target.with_name(target.name + ".part")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            offset = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": "UnityPlayer/2022.3.62f3"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=180) as response:
                append = bool(offset and response.status == 206)
                with part.open("ab" if append else "wb") as stream:
                    shutil.copyfileobj(response, stream)
            digest = sha256(part)
            wanted = expected_hash(entry)
            if wanted and digest != wanted:
                raise IOError(f"sha256 mismatch: expected {wanted}, got {digest}")
            os.replace(part, target)
            return {"bundleName": entry.bundle_name, "url": url, "sha256": digest, "bytes": target.stat().st_size}
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(attempt)
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}")


def pull(manifest_path: str, out: str | os.PathLike[str] | None, asset_base_url: str,
         workers: int = 8, retries: int = 4, master: str | None = None,
         master_cache: str | None = None,
         extract_out: str | os.PathLike[str] | None = None) -> dict:
    asset_base_url = asset_base(asset_base_url)
    manifest = Manifest.load(manifest_path)
    output = Path(out) if out else Path.cwd() / "moly-pull-output"
    output.mkdir(parents=True, exist_ok=True)
    entries = manifest.required_bundles(manifest.roots())
    # Extraction artifacts default to local-data so the browser example opens
    # right after a pull; the download/decrypt workspace stays under `output`.
    raw_dir, decrypted_dir = output / "raw", output / "decrypted"
    extracted_dir = Path(extract_out) if extract_out else Path.cwd() / "local-data"
    raw_dir.mkdir(exist_ok=True); decrypted_dir.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(download_one, entry, asset_base_url, raw_dir, retries) for entry in entries]
        downloads = [future.result() for future in as_completed(futures)]
    for entry in entries:
        source = raw_dir / flatten(entry.bundle_name)
        (decrypted_dir / flatten(entry.bundle_name)).write_bytes(decrypt_bundle(source.read_bytes()))
    names = output / "extraction-manifest.txt"
    names.write_text("\n".join(entry.bundle_name for entry in entries) + "\n", encoding="utf-8", newline="\n")
    from .extract import extract_manifest
    report = extract_manifest(names, decrypted_dir, extracted_dir, master=master,
                              master_cache=master_cache)
    (output / "downloads.json").write_text(json.dumps(sorted(downloads, key=lambda item: item["bundleName"]), indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"requiredBundles": len(entries), "downloads": len(downloads), "extraction": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moly pull")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out")
    parser.add_argument("--asset-base-url", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--retries", type=int, default=4)
    args = parser.parse_args(argv)
    print(json.dumps(pull(args.manifest, args.out, args.asset_base_url, args.workers, args.retries), ensure_ascii=False))
    return 0
