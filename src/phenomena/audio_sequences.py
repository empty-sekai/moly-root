"""Sequence structure for a loop sidecar that was written before the structure was read.

The loop sidecar a runtime ships (``phenomena/audio/loop.json``) lists, per
package archive, the streams the decoder wrote; a sidecar written before the
sequence export existed carries no sequence structure, and the runtime does not
ship the archives themselves.  This step reads every listed archive again from
the decrypted package tree, checks that it is the archive the streams were
decoded from, and writes a copy of the sidecar whose entries carry the
structure exactly as a fresh extraction writes it (``audio.attach_sequences``).

Identity is checked two ways before an entry is annotated: the archive's byte
length equals the entry's ``archiveBytes``, and every cue the entry's streams
name reaches exactly the stream numbers the decoder filed under it (the decoder
names a stream by the cues that reach its waveform, so the two readings must
agree).  An entry that fails either keeps no structure and is named in the
report — a consumer then treats it as never read.  Sample counts are compared
too, for streams that play once, but only reported: the decoder's total for a
compressed waveform can differ from the authored count by a sample or two, and
that says nothing about which waveform a stream is.

Several package trees may be given, in order; each entry is read from the first
tree whose archive passes the byte-length check, and the report says which
tree that was.  A runtime assembled from more than one source tree (shared
weather audio next to a region's own voices) is annotated that way, with the
mix named rather than hidden.
"""
import json
from pathlib import Path

from core.atomic import PUBLIC_FILE_MODE, write_json
from . import acb
from .audio import archive_bytes, attach_sequences

NO_PACKAGE = "package is not in the decrypted tree"
NO_ARCHIVE = "the package holds no archive of this entry's name"
BYTES_DIFFER = "the archive in the decrypted tree is not the one this entry was decoded from (byte length differs)"


def _archives(package):
    """``{name: bytes}`` of a package's text assets, named as the extractors name them."""
    out = {}
    for asset_name, record, path_id in package.contents:
        if record.kinds.get(path_id) != "TextAsset":
            continue
        tree = record.tree(path_id)
        name = str(tree.get("m_Name", "") or asset_name).rsplit(".", 1)[0]
        out.setdefault(name, archive_bytes(tree.get("m_Script")))
    return out


def _cross_check(entry, data):
    """``(problems, sample differences)`` between the decoder's streams and the structure."""
    sheet = acb.CueSheet(data)
    problems, differences = [], []
    by_cue = {}
    for stream in entry["streams"]:
        if stream.get("cue") and not stream.get("error"):
            by_cue.setdefault(stream["cue"], []).append(stream)
    samples = {}
    for row_index, row in enumerate(sheet.rows("WaveformTable")):
        number = sheet.stream_numbers.get(row.get("MemoryAwbId"))
        if number is not None and not row.get("Streaming", 0):
            samples.setdefault(number, set()).add(row.get("NumSamples"))
    for cue, streams in sorted(by_cue.items()):
        index = sheet.indices.get(cue)
        if index is None:
            problems.append(f"{cue}: the decoder named it, the cue sheet has no such cue")
            continue
        structure = sheet.sequence(index)
        if structure is None:
            continue          # a direct cue; the decoder's naming is its own check
        reached = {wave["subsong"] for wave in acb.reached_waveforms(structure)}
        decoded = {stream.get("subsong") for stream in streams}
        if decoded == {None}:
            # the archive holds one stream: the decoder numbers nothing
            if len(sheet.stream_numbers) != 1 or reached != {1}:
                problems.append(f"{cue}: single-stream archive, structure reaches {sorted(reached, key=str)}")
            decoded = {1}
        elif decoded != reached:
            problems.append(f"{cue}: decoder streams {sorted(decoded, key=str)} != structure {sorted(reached, key=str)}")
        for stream in streams:
            number = stream.get("subsong") or 1
            # A looped stream's decoder total is not the authored length (the
            # decoder reports up to its own loop handling), so only a stream
            # that plays once is compared sample for sample.
            if stream.get("loop"):
                continue
            authored = samples.get(number, set())
            if stream.get("samples") is not None and stream["samples"] not in authored:
                differences.append({"cue": cue, "stream": number, "decoder": stream["samples"],
                                    "cueSheet": sorted(authored)})
    return problems, differences


def annotate_loop(loop_path, bundle_roots, out_path, progress=None):
    """Write a copy of the sidecar at *loop_path* with sequence structure attached.

    *bundle_roots* is one decrypted package tree or a list of them, tried in
    order.  Returns the report (also written next to *out_path* as
    ``sequence-report.json``).
    """
    from core.assets.packages import PackageStore

    loop_path, out_path = Path(loop_path), Path(out_path)
    if not loop_path.is_file():
        raise FileNotFoundError(loop_path)
    roots = [bundle_roots] if isinstance(bundle_roots, (str, Path)) else list(bundle_roots)
    for root in roots:
        if not Path(root).is_dir():
            raise NotADirectoryError(root)
    document = json.loads(loop_path.read_text(encoding="utf-8"))
    stores = [PackageStore([], root) for root in roots]
    grouped = {}
    for entry in document.get("packages") or []:
        grouped.setdefault(entry.get("package"), []).append(entry)
    failures, disagreements, sample_differences, answered_by = [], [], [], {}
    counts = {"entries": 0, "annotated": 0, "listed": 0, "sequences": 0, "plain": 0,
              "structured": 0, "direct": 0, "notInArchive": 0, "streamsChecked": 0}
    types, structured_names = {}, []
    # Every cue of every annotated archive, listed or not: by what it references,
    # and (for sequences) by sequence type.
    archive_references, archive_sequence_types = {}, {}
    for done, (package, entries) in enumerate(sorted(grouped.items(), key=lambda kv: str(kv[0]))):
        if progress is not None:
            progress({"package": package, "completed": done, "packages": len(grouped)})
        counts["entries"] += len(entries)
        per_root = []
        for store in stores:
            loaded = store.package(package, record_missing=False)
            try:
                per_root.append(None if loaded is None else _archives(loaded))
            finally:
                store.forget(package)
        if all(archives is None for archives in per_root):
            failures.extend({"package": package, "archive": entry.get("archive"),
                             "reason": NO_PACKAGE} for entry in entries)
            continue
        for entry in entries:
            name = Path(str(entry.get("archive"))).name.rsplit(".", 1)[0]
            candidates = [(position, archives[name]) for position, archives in enumerate(per_root)
                          if archives is not None and name in archives]
            if not candidates:
                failures.append({"package": package, "archive": entry.get("archive"),
                                 "reason": NO_ARCHIVE})
                continue
            matching = [(position, data) for position, data in candidates
                        if len(data) == entry.get("archiveBytes")]
            if not matching:
                failures.append({"package": package, "archive": entry.get("archive"),
                                 "reason": BYTES_DIFFER, "archiveBytes": entry.get("archiveBytes"),
                                 "treeBytes": {str(position): len(data)
                                               for position, data in candidates}})
                continue
            position, data = matching[0]
            error = attach_sequences(entry, data)
            if error is not None or "sequenceExport" not in entry:
                failures.append({"package": package, "archive": entry.get("archive"),
                                 "reason": error or entry.get("sequenceError")})
                continue
            problems, differences = _cross_check(entry, data)
            counts["streamsChecked"] += sum(1 for stream in entry["streams"]
                                            if stream.get("cue") and not stream.get("error"))
            if differences:
                sample_differences.append({"package": package, "archive": entry.get("archive"),
                                           "streams": differences})
            if problems:
                for key in ("sequences", "sequenceExport"):
                    entry.pop(key, None)
                disagreements.append({"package": package, "archive": entry.get("archive"),
                                      "problems": problems})
                continue
            counts["annotated"] += 1
            answered_by.setdefault(str(position), []).append(package)
            sheet = acb.CueSheet(data)
            sequence_rows = sheet.rows("SequenceTable")
            for cue_row in sheet.rows("CueTable"):
                kind = str(cue_row.get("ReferenceType"))
                archive_references[kind] = archive_references.get(kind, 0) + 1
                if cue_row.get("ReferenceType") == acb.REFERENCE_SEQUENCE:
                    sequence_type = str(sequence_rows[cue_row["ReferenceIndex"]].get("Type"))
                    archive_sequence_types[sequence_type] = archive_sequence_types.get(sequence_type, 0) + 1
            summary = entry["sequenceExport"]
            for field in ("listed", "sequences", "plain", "structured", "notInArchive"):
                counts[field] += summary[field]
            counts["direct"] += len(summary["direct"])
            for cue, block in entry["sequences"].items():
                key = block["typeName"] or str(block["type"])
                types[key] = types.get(key, 0) + 1
                structured_names.append({"package": package, "cue": cue, "type": block["type"],
                                         "tracks": len(block["tracks"]), "repeat": block["repeat"]})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, document, mode=PUBLIC_FILE_MODE)
    report = {"version": 1, "source": str(loop_path.name), "counts": counts,
              "trees": len(roots),
              # tree position (0 = first given) -> packages whose entries it answered
              "answeredByTree": {position: sorted(set(names))
                                 for position, names in sorted(answered_by.items())},
              "structuredByType": dict(sorted(types.items())),
              "archiveCuesByReferenceType": dict(sorted(archive_references.items())),
              "archiveSequencesByType": dict(sorted(archive_sequence_types.items())),
              "structured": structured_names, "failures": failures,
              "disagreements": disagreements, "sampleDifferences": sample_differences}
    write_json(out_path.parent / "sequence-report.json", report, mode=PUBLIC_FILE_MODE)
    return report
