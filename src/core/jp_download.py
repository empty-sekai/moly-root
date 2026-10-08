"""Restartable, source-isolated JP original-bundle download stage.

This stage deliberately does not publish a runtime release. Extraction and
release validation are separate, so an interrupted extraction never forces a
redownload and a raw cache can never masquerade as a playable asset root.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
from typing import Callable

from .atomic import PUBLIC_FILE_MODE, exclusive_lock, write_bytes, write_json
from .fetch import Manifest, decrypt_bundle, download_one, flatten, sha256, unityfs_declared_size
from .jp import Client, JPError, load_descriptor, plan


def download_descriptor(path, *, out=None, workers=8, retries=4, include=(), families=(),
                        progress: Callable[[dict], None] | None = None):
    """Download the dependency closure; preserve validated successes on failure.

    Network cookies are ephemeral, only the public source descriptor is stored.
    The workspace lock prevents simultaneous releases from overwriting a cache.
    ``download-progress.json`` is atomically checkpointed throughout the run.
    """
    if not 1 <= workers <= 16 or not 1 <= retries <= 10:
        raise ValueError("workers must be 1..16 and retries must be 1..10")
    descriptor = load_descriptor(path)
    source = descriptor["source"]
    identity = {key: source.get(key) for key in
                ("region", "platform", "assetVersion", "assetHash", "assetbundleHostHash")}
    name = f"{source['assetVersion']}-{source['assetHash'][:12]}"
    workspace = Path(out) if out else Path.cwd() / "moly-pull-output" / "jp" / name
    selection = plan(path, include=include, families=families)
    manifest = Manifest(descriptor["bundles"], descriptor.get("version"))
    entries = [manifest.entries[name] for name in selection["bundles"]]
    if not entries:
        raise JPError("selection contains no downloadable bundles")
    workspace.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(workspace / ".download.lock"):
        marker = workspace / "cache-source.json"
        if marker.exists():
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if previous.get("identity") != identity:
                raise JPError("workspace belongs to another region or release; choose a separate --out")
        elif any((workspace / part).exists() and any((workspace / part).iterdir())
                 for part in ("raw", "decrypted")):
            raise JPError("nonempty cache has no source identity; choose a new source-scoped workspace")
        write_json(marker, {"version": 1, "identity": identity, "source": source}, mode=PUBLIC_FILE_MODE)
        write_json(workspace / "download-plan.json", selection, mode=PUBLIC_FILE_MODE)
        raw, decoded = workspace / "raw", workspace / "decrypted"
        raw.mkdir(exist_ok=True); decoded.mkdir(exist_ok=True)
        client = Client(source["appVersion"], source["appHash"],
                        platform=source.get("platform", "android"), retries=min(retries, 3))
        client.issue_signature()
        receipts, failures = [], []
        start = time.monotonic()

        def one(entry):
            receipt = download_one(entry, descriptor["assetBaseUrl"], raw, retries,
                                   {"Cookie": client.cookie}, verify_bundle=True)
            content = decrypt_bundle((raw / flatten(entry.bundle_name)).read_bytes())
            unityfs_declared_size(content)
            target = decoded / flatten(entry.bundle_name)
            expected = hashlib.sha256(content).hexdigest()
            if not target.is_file() or sha256(target) != expected:
                write_bytes(target, content, mode=PUBLIC_FILE_MODE)
            return {**receipt, "decodedSha256": expected, "decodedBytes": len(content)}

        def checkpoint(status):
            report = {"version": 1, "status": status, "source": source,
                      "requested": len(entries), "completed": len(receipts),
                      "failed": len(failures), "failures": failures,
                      "wireBytes": sum(row["bytes"] for row in receipts),
                      "cacheHits": sum(bool(row.get("cached")) for row in receipts),
                      "elapsedSeconds": round(time.monotonic() - start, 2)}
            write_json(workspace / "download-progress.json", report, mode=PUBLIC_FILE_MODE)
            if progress is not None:
                progress(report)
            return report

        checkpoint("downloading")
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="moly-jp") as pool:
            futures = {pool.submit(one, entry): entry.bundle_name for entry in entries}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    receipts.append(future.result())
                except Exception as exc:
                    # Neither cookies nor token-bearing request objects enter this report.
                    failures.append({"bundle": name, "errorType": type(exc).__name__,
                                     "reason": str(exc).split("Cookie", 1)[0][:600]})
                if (len(receipts) + len(failures)) % 50 == 0:
                    checkpoint("downloading")
        report = checkpoint("failed" if failures else "complete")
        ledger = workspace / "downloads.json"
        prior = json.loads(ledger.read_text(encoding="utf-8")) if ledger.is_file() else []
        accumulated = {row["bundleName"]: row for row in prior}
        accumulated.update({row["bundleName"]: row for row in receipts})
        write_json(ledger, sorted(accumulated.values(), key=lambda row: row["bundleName"]),
                   mode=PUBLIC_FILE_MODE)
        if failures:
            raise JPError(f"{len(failures)} of {len(entries)} bundles failed; inspect {workspace / 'download-progress.json'} and rerun to reuse verified successes")
        write_bytes(workspace / "extraction-manifest.txt",
                    ("\n".join(selection["bundles"]) + "\n").encode("utf-8"), mode=PUBLIC_FILE_MODE)
        return {**report, "workspace": str(workspace), "manifest": str(workspace / "extraction-manifest.txt"),
                "decrypted": str(decoded)}
