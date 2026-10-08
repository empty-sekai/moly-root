"""Release selectors for the existing pack store; packages stay immutable."""
from __future__ import annotations
import re
from pathlib import Path
from core.atomic import PUBLIC_FILE_MODE, exclusive_lock, json_bytes, write_bytes
from .hashing import sha256_bytes
from .identity import CATALOG_V2, CHANNEL_V1, canonical_bytes
from .paths import output_path
from .verify import strict_json, verify_catalog_data


def validate_selector(region, channel):
    if not isinstance(region, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,15}", region):
        raise ValueError("region must be an explicit portable region id, such as cn, jp or tw")
    if channel is not None and (not isinstance(channel, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", channel)):
        raise ValueError("invalid channel name")


def commit_catalog(catalog, output, *, channel="stable"):
    """Caller must hold .publish.lock across build/import and this commit.

    Each pointer swap is atomic. A crash between channel/default updates leaves
    two complete releases, never a half-built package graph. History is retained
    before any selector moves. No global all-channel transaction is promised.
    """
    output = Path(output).resolve()
    if catalog.get("schema") == CATALOG_V2:
        validate_selector(catalog.get("region"), channel)
    errors, _ = verify_catalog_data(catalog, output)
    if errors:
        raise ValueError("refusing to publish invalid catalog: " + "; ".join(errors))
    active = output_path(output, "asset-packs.json")
    if active.is_file():
        previous = active.read_bytes()
        prior_errors, _ = verify_catalog_data(strict_json(previous), output)
        if prior_errors:
            raise ValueError("existing active catalog is invalid: " + "; ".join(prior_errors))
        write_bytes(output_path(output, f"catalogs/{sha256_bytes(previous)}.json"), previous, mode=PUBLIC_FILE_MODE)
    data = canonical_bytes(catalog) if catalog["schema"] == CATALOG_V2 else json_bytes(catalog)
    name = f"catalogs/{sha256_bytes(data)}.json"
    write_bytes(output_path(output, name), data, mode=PUBLIC_FILE_MODE)
    if catalog["schema"] == CATALOG_V2 and channel is not None:
        pointer = {"schema": CHANNEL_V1, "region": catalog["region"], "channel": channel, "catalog": name}
        write_bytes(output_path(output, f"channels/{catalog['region']}/{channel}.json"),
                    canonical_bytes(pointer), mode=PUBLIC_FILE_MODE)
    write_bytes(active, data, mode=PUBLIC_FILE_MODE)
    return name


def activate(output, catalog_id, *, channel="stable"):
    """Verified rollback/promotion to an existing immutable catalog."""
    if not isinstance(catalog_id, str) or not re.fullmatch(r"[0-9a-f]{64}", catalog_id):
        raise ValueError("catalog id must be a full SHA-256")
    output = Path(output).resolve()
    with exclusive_lock(output / ".publish.lock"):
        data = output_path(output, f"catalogs/{catalog_id}.json").read_bytes()
        if sha256_bytes(data) != catalog_id:
            raise ValueError("catalog content address mismatch")
        return commit_catalog(strict_json(data), output, channel=channel)
