"""Discover and download a public Android APK, with resumable verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, build_opener

DEFAULT_ENDPOINT = "https://pjsk.nvsgames.cn/"
USER_AGENT = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
CHUNK_SIZE = 1024 * 1024


class ApkError(RuntimeError):
    """An actionable discovery, download, or verification failure."""


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
             timeout: float = 30.0):
    request = Request(url, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        return build_opener().open(request, timeout=timeout)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ApkError(f"network request failed for {url}: {exc}. Check connectivity or use --endpoint") from exc


def _redirect(url: str, *, timeout: float) -> str:
    try:
        with _request(url, method="HEAD", timeout=timeout) as response:
            return response.geturl()
    except ApkError:
        with _request(url, timeout=timeout) as response:
            return response.geturl()


def discover_latest(endpoint: str = DEFAULT_ENDPOINT, *, timeout: float = 30.0,
                    retries: int = 3) -> dict[str, str]:
    """Return final APK URL and version metadata found on the endpoint."""
    if retries < 1:
        raise ValueError("retries must be at least 1")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with _request(endpoint, timeout=timeout) as response:
                html = response.read().decode("utf-8", "replace")
                page_url = response.geturl()
            match = re.search(r"GameTool\.redirect\(['\"](https://pjskyy\.ugurl\.cn/[^'\"]+)", html)
            if not match:
                raise ApkError("could not find the public download link on endpoint")
            first = _redirect(match.group(1), timeout=timeout)
            final_url = urljoin(page_url, _redirect(first, timeout=timeout))
            version_match = (re.search(r"/\d+_(\d{3,6})(?:/|_)", final_url)
                             or re.search(r"_v\d+_(\d{3,6})_", final_url)
                             or re.search(r"[?&]versionCode=(\d{3,6})(?:&|$)", final_url))
            result = {"download_url": final_url}
            if version_match:
                result["version_code"] = version_match.group(1)
            channel = re.search(r"pjsk_([A-Za-z0-9]+)_v", final_url)
            if channel:
                result["channel"] = channel.group(1)
            return result
        except (ApkError, UnicodeError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(attempt + 1)
    raise ApkError(f"version discovery failed after {retries} attempt(s): {last}") from last


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hash(path: Path, expected: str, algorithm: str = "sha256") -> bool:
    """Verify a file against a hex digest; expected may be ``sha256:<hex>``."""
    if ":" in expected:
        algorithm, expected = expected.split(":", 1)
    try:
        return _digest(path, algorithm).lower() == expected.strip().lower()
    except (OSError, ValueError):
        return False


def download(url: str, destination: Path, *, expected_hash: str | None = None,
             timeout: float = 1800.0, retries: int = 3) -> Path:
    """Download an APK, resuming ``.part`` when the server supports ranges."""
    if retries < 1:
        raise ValueError("retries must be at least 1")
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    last: Exception | None = None
    for attempt in range(retries):
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with _request(url, headers=headers, timeout=timeout) as response:
                resumed = bool(offset and response.status == 206)
                if offset and not resumed:
                    offset = 0
                    part.unlink()
                mode = "ab" if resumed else "wb"
                with part.open(mode) as stream:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        stream.write(chunk)
            if expected_hash and not verify_hash(part, expected_hash):
                raise ApkError("download completed but hash verification failed")
            part.replace(destination)
            return destination
        except (ApkError, OSError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(attempt + 1)
    raise ApkError(f"download failed after {retries} attempt(s): {last}") from last


def inspect_assets_container(path: Path) -> dict[str, object]:
    """Report likely embedded container files without extracting assets."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ApkError(f"APK is not a readable ZIP archive: {exc}") from exc
    candidates = [name for name in names if name.startswith("assets/") and (
        name.endswith(".unity3d") or "StreamingAssets" in name or "bundl" in name.lower())]
    return {"archive": str(path), "container_candidates": candidates,
            "note": "Candidates are paths inside the APK; extraction is a separate step."}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover, download, and verify an Android APK")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("latest")
    get = sub.add_parser("download")
    get.add_argument("destination", type=Path)
    get.add_argument("--url")
    get.add_argument("--sha256")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("apk", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "latest":
            print(json.dumps(discover_latest(args.endpoint, timeout=args.timeout, retries=args.retries), indent=2))
        elif args.command == "download":
            info = {"download_url": args.url} if args.url else discover_latest(args.endpoint, timeout=args.timeout, retries=args.retries)
            print(download(info["download_url"], args.destination, expected_hash=args.sha256,
                           timeout=max(args.timeout, 30.0), retries=args.retries))
        else:
            print(json.dumps(inspect_assets_container(args.apk), indent=2))
        return 0
    except ApkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
