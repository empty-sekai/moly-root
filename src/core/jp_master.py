"""Pin and verify the public JP master snapshot required by MYSEKAI.

The Git tree's immutable blob identities are checked before publication. A
snapshot is region/version scoped and never updates a CN master directory.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

from .atomic import PUBLIC_FILE_MODE, exclusive_lock, write_bytes, write_json
from .jp import JPError, load_descriptor

REPOSITORY = "Team-Haruki/haruki-sekai-master"
PROVIDERS = {
    "haruki": (REPOSITORY, "master/", "versions/current_version.json"),
    "sekai-world": ("Sekai-World/sekai-master-db-diff", "", "versions.json"),
}


def master_paths(tree, prefix, *, all_tables=False):
    """Map provider paths to flat runtime master names without accepting traversal."""
    files = []
    for row in tree:
        source = row.get("path", "")
        if row.get("type") != "blob" or not source.startswith(prefix):
            continue
        name = source[len(prefix):]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*\.json", name):
            continue
        if name == "versions.json":
            continue
        if all_tables or name.startswith("mysekai") or name in EXTRA_TABLES:
            if not re.fullmatch(r"[a-f0-9]{40}", row.get("sha", "")):
                raise JPError("master tree contains an invalid blob identity")
            files.append({**row, "sourcePath": source, "path": name})
    return files
EXTRA_TABLES = {"gameCharacters.json", "gameCharacterUnits.json", "unitProfiles.json",
                "characterProfiles.json", "wordings.json", "clientConfigs.json", "birthdayParties.json", "character2ds.json",
                "birthdayPartyDeliveryRewards.json", "birthdayPartyDeliveryPointBonuses.json",
                "birthdayPartyDeliveryTotalRewards.json", "resourceBoxDetails.json"}


def _read(url, limit=256 * 1024 * 1024):
    with urlopen(Request(url, headers={"User-Agent": "moly-root/jp-master"}), timeout=45) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise JPError("master response exceeds the bounded read limit")
        return body


def download_master_snapshot(descriptor_path, out, *, revision="main", workers=6, provider="haruki", all_tables=False):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", revision) or not 1 <= workers <= 12:
        raise ValueError("invalid master revision or worker count")
    if provider not in PROVIDERS:
        raise ValueError("unknown JP master provider")
    repository, prefix, version_path = PROVIDERS[provider]
    descriptor = load_descriptor(descriptor_path)
    source = descriptor["source"]
    commit = json.loads(_read(f"https://api.github.com/repos/{repository}/commits/{revision}"))
    sha = commit["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise JPError("master service did not return an immutable commit identity")
    base = f"https://raw.githubusercontent.com/{repository}/{sha}"
    versions_raw = _read(f"{base}/{version_path}")
    versions = json.loads(versions_raw)
    for key in ("assetVersion", "assetHash"):
        if versions.get(key) != source.get(key):
            raise JPError(f"master {key} differs from bundle descriptor; refresh the descriptor or supply a matching --revision")
    tree = json.loads(_read(f"https://api.github.com/repos/{repository}/git/trees/{sha}?recursive=1"))
    if tree.get("truncated"):
        raise JPError("master file tree was truncated; snapshot was not published")
    files = master_paths(tree["tree"], prefix, all_tables=all_tables)
    if not any(row["path"] == "mysekaiFixtures.json" for row in files):
        raise JPError("master snapshot lacks MYSEKAI furniture definitions")
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    identity = {"region": "jp", "commit": sha, "assetVersion": source["assetVersion"],
                "assetHash": source["assetHash"], "dataVersion": versions.get("dataVersion")}
    with exclusive_lock(out / ".master.lock"):
        marker = out / "master-source.json"
        if marker.exists() and (json.loads(marker.read_text(encoding="utf8"))["identity"] != identity
                                or json.loads(marker.read_text(encoding="utf8")).get("repository") != repository):
            raise JPError("master directory belongs to another snapshot; choose a new --out")
        if not marker.exists() and any(path.suffix == ".json" for path in out.iterdir()):
            raise JPError("nonempty master directory lacks source identity; choose a new --out")
        write_json(marker, {"version": 1, "status": "downloading", "identity": identity,
                            "repository": repository, "provider": provider}, mode=PUBLIC_FILE_MODE)
        def fetch(row):
            target = out / row["path"]
            body = target.read_bytes() if target.is_file() else b""
            def blob_hash(value):
                return hashlib.sha1(f"blob {len(value)}\0".encode() + value).hexdigest()
            if blob_hash(body) != row["sha"]:
                body = _read(f"{base}/{row['sourcePath']}")
                if blob_hash(body) != row["sha"]:
                    raise JPError(f"master blob integrity mismatch: {row['path']}")
                json.loads(body)
                write_bytes(target, body, mode=PUBLIC_FILE_MODE)
            return {"table": row["path"], "sourcePath": row["sourcePath"], "gitBlob": row["sha"],
                    "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="moly-master") as pool:
            rows = list(pool.map(fetch, files))
        write_bytes(out / "versions.json", versions_raw, mode=PUBLIC_FILE_MODE)
        report = {"version": 1, "status": "complete", "identity": identity, "repository": repository, "provider": provider,
                  "tables": sorted(rows, key=lambda row: row["table"]), "tableCount": len(rows), "allTables": all_tables}
        write_json(marker, report, mode=PUBLIC_FILE_MODE)
        return report
