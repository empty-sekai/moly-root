"""Waveform facts of the packages a music record plays.

A music record plays one package's cue as the housing site's music.  A
record of track type ``music_sound_track`` names a ``musicSoundTracks`` row
whose ``assetbundleName`` and ``assetbundleFileName`` are the package and the
cue; a record of type ``music`` plays one vocal of its song (the one the
site's setting names), and the vocal's ``assetbundleName`` is the cue, the
package being ``music/long/<assetbundleName>``.

The audio itself is not extracted here: the product fetches it at runtime
from a public storage mirror of the same assets.  The mirror's files carry
no loop points, while the player that plays a record forces a loop -- a
waveform with loop points loops between them, one without loops as a whole
-- so what the product needs from the archives is read here: per package,
the cue's waveform length and loop region (or the fact that it has none) and
the cue's command lists (categories and volume), plus the bundle version the
package was read from.  Every package a record can play gets a row, so a
package missing from the document always means "not read", never "read and
found without a loop".  A package whose bundle is not in the given trees is
listed under ``absent`` with its reason instead.

Another region's document can be carried from one already read: a package
whose bundle in that region's package list has the same crc and file size as
the bundle read gets the same facts, with their origin named on the row.
"""
import hashlib
import json
import tempfile
import time
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.atomic import PUBLIC_FILE_MODE, write_json
from core.assets.packages import PackageStore
from core.fetch import Manifest, decrypt_bundle, download_one, flatten
from core.master import Master
from .audio import (DECODER, NO_DECODER, archive_bytes, attach_sequences,
                    loop_document, probe, tool)

VERSION = 1
SONG_PACKAGE_PREFIX = "music/long/"
NO_BUNDLE = "the package's bundle is not in the given trees"
NO_ARCHIVE = "the package holds no archive with this cue"
TRACK_MUSIC = "music"
TRACK_SOUNDTRACK = "music_sound_track"


def record_packages(master):
    """``[(package, cue, kind)]`` every music record can play, in record order."""
    soundtracks = {row["id"]: row for row in master.table("musicSoundTracks")}
    vocals = {}
    for row in master.table("musicVocals"):
        vocals.setdefault(row["musicId"], []).append(row)
    out, seen = [], set()
    for record in master.table("mysekaiMusicRecords"):
        kind = record["mysekaiMusicTrackType"]
        if kind == TRACK_SOUNDTRACK:
            row = soundtracks.get(record["externalId"])
            wanted = [] if row is None else [
                (row["assetbundleName"], row["assetbundleFileName"])]
        elif kind == TRACK_MUSIC:
            wanted = [(SONG_PACKAGE_PREFIX + vocal["assetbundleName"],
                       vocal["assetbundleName"])
                      for vocal in vocals.get(record["externalId"], [])]
        else:
            raise ValueError(f"record {record['id']}: unknown track type {kind!r}")
        for package, cue in wanted:
            if package not in seen:
                seen.add(package)
                out.append((package, cue, kind))
    return out


def _archives(package):
    """Every text asset of a loaded package as ``(name, bytes)``."""
    out = []
    for asset_name, record, path_id in package.contents:
        if record.kinds.get(path_id) != "TextAsset":
            continue
        tree = record.tree(path_id)
        name = str(tree.get("m_Name", "") or asset_name).rsplit(".", 1)[0]
        out.append((name, archive_bytes(tree.get("m_Script"))))
    return out


def _stream_of(decoder, archive, cue):
    """``(subsong, meta)`` of the archive's stream that answers to *cue*."""
    head = probe(decoder, archive) or {}
    count = head.get("streamCount", 1)
    for subsong in (range(1, count + 1) if count > 1 else (None,)):
        meta = head if subsong is None else (probe(decoder, archive, subsong) or {})
        if cue in (meta.get("cues") or []):
            return subsong, meta
    return None, None


def extract_record_audio(master_source, bundle_roots, out, *, bundle_version,
                         decoder=None, master_cache=None, only=None):
    """Write the waveform facts of every record package found in *bundle_roots*.

    *bundle_version* names the asset version the bundles were downloaded at;
    it is written on every row.  *only* restricts the run to one kind
    (``music`` or ``music_sound_track``); packages of the other kind are
    neither read nor listed.
    """
    decoder_path = tool(DECODER, decoder)
    if decoder_path is None:
        raise RuntimeError(NO_DECODER)
    master = Master(master_source, cache_dir=master_cache)
    wanted = [row for row in record_packages(master) if only is None or row[2] == only]
    stores = [PackageStore([], root) for root in bundle_roots]
    packages, absent = {}, []
    with tempfile.TemporaryDirectory(prefix="record-audio-") as scratch:
        for package_name, cue, kind in wanted:
            flat = flatten(package_name)
            package = None
            for store in stores:
                package = store.package(flat, record_missing=False)
                if package is not None:
                    break
            if package is None:
                absent.append({"package": package_name, "cue": cue, "kind": kind,
                               "reason": NO_BUNDLE})
                continue
            row = None
            for name, data in _archives(package):
                archive = Path(scratch) / f"{flat}.acb"
                archive.write_bytes(data)
                subsong, meta = _stream_of(decoder_path, archive, cue)
                if meta is None:
                    continue
                entry = {"package": package_name, "streams": [{"cue": cue}]}
                error = attach_sequences(entry, data)
                row = {"package": package_name, "cue": cue, "kind": kind,
                       "bundleVersion": bundle_version,
                       "stream": dict(loop_document(meta), cue=cue, subsong=subsong),
                       "sequenceExport": entry.get("sequenceExport"),
                       "sequences": entry.get("sequences"),
                       "cueCommands": entry.get("cueCommands")}
                if error is not None:
                    row["sequenceError"] = error
                break
            for store in stores:
                store.forget(flat)
            if row is None:
                absent.append({"package": package_name, "cue": cue, "kind": kind,
                               "reason": NO_ARCHIVE})
            else:
                packages[package_name] = row
    looped = sum(1 for row in packages.values() if row["stream"]["loop"])
    document = {
        "version": VERSION,
        "semantics": {
            "packages": ("every package a music record can play whose bundle was "
                         "read, keyed by its manifest name; stream.loop false means "
                         "the waveform has no loop chunk (the player then loops the "
                         "whole waveform), true gives the loop region in seconds"),
            "absent": "packages a record can play that were not read, with the reason",
            "audio": ("not carried: the product fetches the audio from the public "
                      "storage mirror, whose files have no loop points"),
        },
        "packages": packages,
        "absent": absent,
        "summary": {"wanted": len(wanted), "read": len(packages), "looped": looped,
                    "unlooped": len(packages) - looped, "absent": len(absent),
                    "bundleVersion": bundle_version, "only": only},
    }
    write_json(out, document, mode=PUBLIC_FILE_MODE)
    return document


def merge_record_audio(paths, out):
    """Combine documents written by separate runs over disjoint packages
    (of one region, when the documents name theirs)."""
    packages, absent, versions, regions = {}, {}, set(), set()
    for path in paths:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if doc.get("version") != VERSION:
            raise ValueError(f"{path}: unsupported record audio version")
        if doc.get("region"):
            regions.add(doc["region"])
        for name, row in doc["packages"].items():
            if name in packages and packages[name] != row:
                raise ValueError(f"{name}: read twice with different facts")
            packages[name] = row
            versions.add(row["bundleVersion"])
        for row in doc["absent"]:
            absent.setdefault(row["package"], row)
    if len(regions) > 1:
        raise ValueError(f"documents of different regions: {sorted(regions)}")
    absent = [row for name, row in absent.items() if name not in packages]
    looped = sum(1 for row in packages.values() if row["stream"]["loop"])
    semantics = {}
    for path in paths:
        semantics.update(json.loads(Path(path).read_text(encoding="utf-8"))["semantics"])
    document = {
        "version": VERSION,
        **({"region": regions.pop()} if regions else {}),
        "semantics": semantics,
        "packages": dict(sorted(packages.items())),
        "absent": sorted(absent, key=lambda row: row["package"]),
        "summary": {"read": len(packages), "looped": looped,
                    "unlooped": len(packages) - looped, "absent": len(absent),
                    "bundleVersions": sorted(versions)},
    }
    write_json(out, document, mode=PUBLIC_FILE_MODE)
    return document


CARRY_DIFFERS = ("the package's bundle in the target package list differs from the "
                 "bundle the source document was read from (crc or file size)")
CARRY_NOT_LISTED = "the package is not in the target package list"
CARRY_NOT_READ = "the source document has no row with a verified storage file for the package"
CARRY_CUE = "the target master names another cue for the package than the source row"


def _bundle_identity(entry):
    """``(crc, fileSize)`` of a package list entry, or ``None`` when either is unknown."""
    if entry is None or not entry.crc or not entry.file_size:
        return None
    return entry.crc, entry.file_size


def carry_record_audio(master_source, manifest, source, source_manifest, out, *,
                       region, master_cache=None):
    """Write the facts of another region's record packages, carried from a
    document already read, where the bundles are the same.

    *master_source* and *manifest* are the target region's master tables and
    package list (``AssetBundleInfoNew``); *source* is a record-audio document
    with storage attached, read from the bundles *source_manifest* lists.  A
    package is carried when its target entry has the crc and file size of the
    bundle the source row was read from (the same bundle content), the target
    master names the same cue, and the source row's storage file was verified.
    Each carried row keeps the source's facts and names where they come from
    under ``carriedFrom`` (region, bundle version, crc, file size and the sha256
    of the audio file the facts describe); ``attach_storage`` then refuses a
    target file whose bytes differ from that audio.  Every other package is
    listed under ``absent`` with its reason.
    """
    master = Master(master_source, cache_dir=master_cache)
    wanted = record_packages(master)
    target = Manifest.load(manifest).entries
    origin = Manifest.load(source_manifest).entries
    doc = json.loads(Path(source).read_text(encoding="utf-8"))
    if doc.get("version") != VERSION:
        raise ValueError(f"{source}: unsupported record audio version")
    packages, absent = {}, []
    for package_name, cue, kind in wanted:
        row = doc["packages"].get(package_name)
        storage = (row or {}).get("storage") or {}
        reason = None
        if row is None or not storage.get("sha256") or "error" in storage or "listed" in storage:
            reason = CARRY_NOT_READ
        elif package_name not in target:
            reason = CARRY_NOT_LISTED
        elif (_bundle_identity(target[package_name]) is None
              or _bundle_identity(target[package_name]) != _bundle_identity(origin.get(package_name))):
            reason = CARRY_DIFFERS
        elif row["cue"] != cue:
            reason = CARRY_CUE
        if reason is not None:
            entry = target.get(package_name)
            absent.append({"package": package_name, "cue": cue, "kind": kind, "reason": reason,
                           "targetCrc": entry.crc if entry else None,
                           "targetFileSize": entry.file_size if entry else None})
            continue
        crc, file_size = _bundle_identity(target[package_name])
        carried = {key: value for key, value in row.items() if key != "storage"}
        carried["kind"] = kind
        carried["carriedFrom"] = {"region": storage.get("server"), "bundleVersion": row["bundleVersion"],
                                  "crc": crc, "fileSize": file_size,
                                  "storageSha256": storage["sha256"]}
        packages[package_name] = carried
    looped = sum(1 for row in packages.values() if row["stream"]["loop"])
    reasons = {}
    for row in absent:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    semantics = dict(doc["semantics"])
    semantics.pop("storage", None)
    semantics["carriedFrom"] = (
        "the facts were read from another region's bundle with this crc and file "
        "size, which the target package list gives for the package too; "
        "storageSha256 is the audio file those facts describe")
    document = {
        "version": VERSION,
        "region": region,
        "semantics": semantics,
        "packages": dict(sorted(packages.items())),
        "absent": sorted(absent, key=lambda row: row["package"]),
        "summary": {"wanted": len(wanted), "read": len(packages), "looped": looped,
                    "unlooped": len(packages) - looped, "absent": len(absent),
                    "absentReasons": reasons,
                    "carriedFrom": sorted({row["carriedFrom"]["region"] for row in packages.values()}),
                    "bundleVersions": sorted({row["bundleVersion"] for row in packages.values()})},
    }
    write_json(out, document, mode=PUBLIC_FILE_MODE)
    return document


FETCH_BUILTIN = ("the package is part of the player build (isBuiltin), so it is "
                 "not on the download path")


def uncompressed_crc(data):
    """CRC-32 of a decoded bundle's uncompressed content, its nodes in
    directory order: the value a package list carries as ``crc``.  A parsed
    node (a serialized file) holds its reader; a node UnityPy leaves unparsed
    (a texture's resource stream, say) is the reader itself."""
    import UnityPy
    crc = 0
    for bundle in UnityPy.load(data).files.values():
        for node in bundle.files.values():
            reader = getattr(node, "reader", node)
            position = reader.Position
            reader.Position = 0
            crc = zlib.crc32(reader.read_bytes(reader.Length), crc)
            reader.Position = position
    return crc


def fetch_record_bundles(manifest, document, asset_base_url, out, *, workers=4):
    """Download the bundles of the packages *document* lists as absent.

    *manifest* is the package list the bundles are fetched by and
    *asset_base_url* the prefix before each entry's ``downloadPath``.  A
    fetched bundle is decoded, and kept under ``<out>/decrypted`` only when its
    length is the list's ``fileSize`` and the CRC-32 of its uncompressed
    content is the list's ``crc``; the raw download is removed either way.  A
    package in the player build is not fetched and is named under
    ``skipped``; a package the list does not carry is not tried.
    """
    entries = Manifest.load(manifest).entries
    doc = json.loads(Path(document).read_text(encoding="utf-8"))
    out = Path(out)
    raw_dir, decrypted_dir = out / "raw", out / "decrypted"
    raw_dir.mkdir(parents=True, exist_ok=True)
    decrypted_dir.mkdir(parents=True, exist_ok=True)
    wanted, skipped = [], []
    for row in doc["absent"]:
        entry = entries.get(row["package"])
        if entry is None:
            continue
        if entry.in_player_build:
            skipped.append({"package": row["package"], "reason": FETCH_BUILTIN})
        else:
            wanted.append(entry)

    def one(entry):
        raw = raw_dir / flatten(entry.bundle_name)
        try:
            receipt = download_one(entry, asset_base_url, raw_dir, verify_bundle=True)
            data = decrypt_bundle(raw.read_bytes())
            if len(data) != entry.file_size:
                raise ValueError(f"decoded {len(data)} bytes, the list says {entry.file_size}")
            crc = uncompressed_crc(data)
            if crc != entry.crc:
                raise ValueError(f"uncompressed crc {crc}, the list says {entry.crc}")
            (decrypted_dir / flatten(entry.bundle_name)).write_bytes(data)
            return {"package": entry.bundle_name, "url": receipt["url"], "bytes": len(data),
                    "crc": crc}
        except Exception as error:  # network or content: named per package
            return {"package": entry.bundle_name, "error": f"{type(error).__name__}: {error}"}
        finally:
            for leftover in (raw, raw.with_name(raw.name + ".verified.json")):
                leftover.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, wanted))
    receipt = {"fetched": [r for r in results if "error" not in r],
               "failed": [r for r in results if "error" in r], "skipped": skipped}
    receipt["summary"] = {key: len(value) for key, value in receipt.items()}
    write_json(out / "fetch.json", receipt, mode=PUBLIC_FILE_MODE)
    return receipt


STORAGE_API = "https://storage.pjsk.moe"
STORAGE_TIMEOUT = 60
STORAGE_TRIES = 3


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "moly-root record-audio"})
    return urllib.request.urlopen(request, timeout=STORAGE_TIMEOUT)


def _storage_row(api, server, row):
    """The storage mirror's listing of the row's audio file, and its bytes' hash.

    A carried row's file must be the audio its facts describe: when the bytes
    hash to anything else, the result carries no ``sha256`` and names the
    difference under ``error``."""
    file_name = f"{row['cue']}.mp3"
    query = urllib.parse.urlencode({"server": server, "path": row["package"], "limit": 200})
    last = None
    for attempt in range(STORAGE_TRIES):
        try:
            with _get(f"{api}/api/assets/bundle-files?{query}") as response:
                listing = json.loads(response.read().decode("utf-8"))
            item = next((entry for entry in listing.get("items") or []
                         if entry.get("name") == file_name), None)
            if item is None:
                return {"file": file_name, "error": "the storage listing has no such file"}
            digest, size = hashlib.sha256(), 0
            with _get(api + item["url"]) as response:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
            got = digest.hexdigest()
            storage = {"server": server, "url": item["url"], "size": size,
                       "sha256": got, "version": item.get("version")}
            if got != item.get("sha256") or size != item.get("size"):
                storage["listed"] = {"sha256": item.get("sha256"), "size": item.get("size")}
            described = (row.get("carriedFrom") or {}).get("storageSha256")
            if described is not None and got != described:
                storage.pop("sha256")
                storage["sha256Downloaded"] = got
                storage["error"] = ("the storage file differs from the audio the carried "
                                    f"facts describe (sha256 {described})")
            return storage
        except Exception as error:  # network: retried, then named on the row
            last = f"{type(error).__name__}: {error}"
            time.sleep(2 * (attempt + 1))
    return {"file": file_name, "error": last}


def attach_storage(path, out, *, server, api=STORAGE_API, workers=4):
    """Add, per row, the storage mirror's audio file: the sha256 and size of
    the bytes actually downloaded and the listing's version.  A listing whose
    sha256 or size differs from the downloaded bytes keeps its values under
    ``listed``; a file that could not be read keeps the reason under ``error``,
    and so does a carried row's file that is not the audio its facts describe
    (with the downloaded bytes' hash under ``sha256Downloaded``).
    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = list(document["packages"].values())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda row: _storage_row(api, server, row), rows))
    for row, storage in zip(rows, results):
        row["storage"] = storage
    document["semantics"]["storage"] = (
        "the public storage mirror's audio file of the row: sha256 and size of the "
        "bytes downloaded, the listing's version; listed holds the listing's own "
        "values when they differ; error names a file that could not be read, or a "
        "carried row's file that is not the audio its facts describe")
    document["summary"]["storage"] = {
        "server": server,
        "verified": sum(1 for r in results if "error" not in r and "listed" not in r),
        "listingDiffers": sum(1 for r in results if "listed" in r),
        "unread": sum(1 for r in results if "error" in r and "sha256Downloaded" not in r),
        "carriedDiffers": sum(1 for r in results if "sha256Downloaded" in r),
        "bytes": sum(r.get("size", 0) for r in results if "error" not in r)}
    write_json(out, document, mode=PUBLIC_FILE_MODE)
    return document
