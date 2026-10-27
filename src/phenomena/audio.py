"""Music and ambience, decoded through an external decoder when one is present.

A sound package does not hold audio files.  It holds one *archive* in the console
audio middleware's own container format, and inside it every waveform is stored
under a **cue** name — the name the caller's tables use to ask for a sound.  One
archive can hold hundreds of cues, one cue can have several waveforms (the game
picks between them), and one waveform can answer to several cue names.

Decoding that container needs a decoder this repository does not ship and does not
vendor: it is a separate program with its own licence.  So the archive itself is
always extracted — that needs nothing external — and the *decode* is what depends on
the tool.  Without it the audio entry says ``skipped`` and names what is missing,
the same way the character registry does when master tables are absent; it never
fails the run and never silently produces nothing.

Loop points are part of the archive's own metadata, not something to detect from the
samples, so they are read from the decoder's report and written next to the audio as
seconds — which is the form a web audio consumer wants, since it loops on a time
range rather than a sample range.  Waveforms are written with the loop *not* unrolled,
so a consumer loops them itself over exactly that range.

Which waveform a cue plays, when, and whether it ever stops is not in the decoder's
report either: it is the cue's sequence structure, read from the archive's own
tables (see :mod:`phenomena.acb`).  Every package entry of a cue sheet carries it
for the cues the entry lists — ``sequences`` holds a block for every cue that is
not *plain*, and ``sequenceExport`` counts both kinds, so a cue the entry lists
with no block was read and found plain (one waveform, once, from the cue's start),
while an entry without ``sequenceExport`` was never read at all.  From version 2
``cueCommands`` carries, for every listed cue that references a sequence (plain
ones too), the command lists the player reads for its volume -- categories and
volume commands at the sequence, track and synth level -- and ``sequenceExport``
names the cue sheet's format number (``acbVersion``), which decides a branch of
the random-without-repeat type.
"""
import re
import hashlib
import json
import shutil
import struct
import subprocess
from pathlib import Path

from core.jsonio import dumps
from . import acb

DECODER = "vgmstream-cli"
TRANSCODER = "ffmpeg"

NO_DECODER = (f"{DECODER} was not found: it is an optional external program, not "
              f"part of this repository; pass its path or put it on PATH and the "
              f"archives already extracted here will be decoded")
NO_TRANSCODER = (f"{TRANSCODER} was not found, so only uncompressed audio was "
                 f"written")
NO_CUE = "no waveform in this archive carries this cue name"

# Metadata lines the decoder prints, as (line label, exported name, converter).
NUMBERS = {"sample rate": "sampleRate", "channels": "channels",
           "loop start": "loopStart", "loop end": "loopEnd",
           "stream total samples": "samples", "stream count": "streamCount",
           "stream index": "subsong"}
FIRST_NUMBER = re.compile(r"-?\d+")


def tool(name, override=None):
    """Absolute path of an external program, or ``None`` when it is not there."""
    if override:
        path = Path(override)
        if path.is_dir():
            for candidate in (path / name, path / f"{name}.exe"):
                if candidate.exists():
                    return str(candidate)
            return None
        return str(path) if path.exists() else None
    return shutil.which(name)


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def probe(decoder, archive, subsong=None):
    """One stream's metadata, as the decoder reports it."""
    argv = [decoder, "-m"]
    if subsong is not None:
        argv += ["-s", str(subsong)]
    result = _run(argv + [str(archive)])
    if result.returncode != 0:
        return None
    meta = {"streamCount": 1, "cues": []}
    for line in result.stdout.splitlines():
        label, _, value = line.partition(":")
        label, value = label.strip(), value.strip()
        if label in NUMBERS:
            found = FIRST_NUMBER.search(value)
            if found:
                meta[NUMBERS[label]] = int(found.group())
        elif label == "stream name":
            meta["cues"] = [part.strip() for part in value.split(";") if part.strip()]
        elif label == "encoding":
            meta["encoding"] = value
    return meta


def loop_document(meta):
    """Loop range and stream shape, in the units a consumer plays with."""
    rate = meta.get("sampleRate") or 0
    samples = meta.get("samples") or 0
    start, end = meta.get("loopStart"), meta.get("loopEnd")
    looped = start is not None and end is not None
    return {"loop": looped,
            "loopStartSeconds": start / rate if looped and rate else None,
            "loopEndSeconds": end / rate if looped and rate else None,
            "loopStartSamples": start, "loopEndSamples": end,
            "sampleRate": meta.get("sampleRate"), "channels": meta.get("channels"),
            "samples": samples,
            "durationSeconds": samples / rate if rate else None,
            "encoding": meta.get("encoding")}


def archive_bytes(value):
    """Raw bytes of a text asset, whichever way the reader handed it over."""
    if isinstance(value, str):
        return value.encode("utf-8", "surrogateescape")
    return bytes(value or b"")


SEQUENCE_EXPORT_VERSION = 2


def attach_sequences(entry, data):
    """Put the sequence structure of the cues *entry* lists onto *entry*.

    Cues are the ones the entry's streams name (decoded or not).  An archive
    that is not a cue sheet at all (a bare waveform container, say) has no
    sequence structure by construction: the entry says so under
    ``sequenceError`` and ``None`` is returned.  A cue sheet whose tables cannot
    be read returns the error text for the caller to report, and its entry
    says so the same way.  Either way the entry carries no ``sequenceExport``,
    which a consumer reads as "never read".
    """
    for key in ("sequences", "sequenceExport", "sequenceError", "cueCommands"):
        entry.pop(key, None)
    if not acb.is_table(data):
        entry["sequenceError"] = acb.NOT_A_CUE_SHEET
        return None
    cues = {stream.get("cue") for stream in entry["streams"] if stream.get("cue")}
    try:
        found, direct = acb.sequences(data, cues)
        acb_version = acb.header_version(data)
    except (acb.TableError, IndexError, KeyError, struct.error, UnicodeDecodeError) as err:
        entry["sequenceError"] = f"the cue sheet's tables could not be read: {err}"
        return entry["sequenceError"]
    structured = {cue: block for cue, block in found.items() if not acb.is_plain(block)}
    entry["sequences"] = structured
    entry["cueCommands"] = {cue: acb.cue_commands(block) for cue, block in found.items()}
    entry["sequenceExport"] = {
        "version": SEQUENCE_EXPORT_VERSION, "listed": len(cues),
        "sequences": len(found), "plain": len(found) - len(structured),
        "structured": len(structured), "notInArchive": len(cues) - len(found) - len(direct),
        # Cues that reference a waveform or a synth directly, not a sequence.
        "direct": direct, "acbVersion": acb_version}
    return None


class Library:
    """Audio of one run: archives always, waveforms when the decoder is present."""

    def __init__(self, directory, prefix, decoder=None, transcoder=None, *, preserve_existing=False):
        self.root = Path(directory)
        self.prefix = prefix
        self.decoder = tool(DECODER, decoder)
        self.transcoder = tool(TRANSCODER, transcoder)
        self.packages = []
        self.unsupported = []
        self.previous = None
        if preserve_existing and (self.root / "loop.json").is_file():
            self.previous = json.loads((self.root / "loop.json").read_bytes())
            # Capture identity before add() can write an archive at its old path.
            for entry in self.previous.get("packages", []):
                if not entry.get("sourceSha256"):
                    archive = self._existing_file(entry.get("archive"))
                    if archive is not None:
                        entry["sourceSha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()

    def _existing_file(self, logical):
        if not isinstance(logical, str) or not logical.startswith(self.prefix + "/"):
            return None
        target = self.root / logical[len(self.prefix) + 1:]
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("existing audio path escaped output root")
        return target if target.is_file() else None

    def _preserved_packages(self):
        if self.previous is None:
            return self.packages
        current = {(row["package"], Path(row["archive"]).name): row for row in self.packages}
        combined = []
        for prior in self.previous.get("packages", []):
            key = (prior["package"], Path(prior["archive"]).name)
            fresh = current.pop(key, None)
            if fresh is None:
                combined.append(prior)
                continue
            if not prior.get("sourceSha256") or prior["sourceSha256"] != fresh["sourceSha256"]:
                raise ValueError("audio archive identity changed; use a fresh output root")
            # A weather subset cannot erase the whole bank the corpus decoded.
            keys = {(row.get("cue"), row.get("subsong")) for row in fresh["streams"]}
            retained = [row for row in prior.get("streams", [])
                        if (row.get("cue"), row.get("subsong")) not in keys]
            if any(not any(self._existing_file(row.get(kind)) is not None
                           for kind in ("wav", "ogg")) for row in retained):
                raise ValueError("existing audio stream is missing; repair the corpus before extraction")
            merged = {**fresh, "streams": [*retained, *fresh["streams"]]}
            for name in ("archiveSetRevision", "archiveSetCount", "archiveSetIndex"):
                if name in prior:
                    merged[name] = prior[name]
            archive = self._existing_file(fresh["archive"])
            attach_sequences(merged, archive.read_bytes())
            combined.append(merged)
        combined.extend(current.values())
        return combined

    @property
    def status(self):
        return "succeeded" if self.decoder else "skipped"

    def _decode(self, archive, directory, prefix, stem, subsong, meta):
        """Write one waveform, plus a compressed copy when a transcoder is there."""
        wav = directory / f"{stem}.wav"
        argv = [self.decoder, "-i", "-o", str(wav)]
        if subsong is not None:
            argv += ["-s", str(subsong)]
        result = _run(argv + [str(archive)])
        if result.returncode != 0 or not wav.exists():
            return None, (result.stderr or result.stdout or "").strip().splitlines()[-1:]
        files = {"wav": f"{prefix}/{wav.name}"}
        if self.transcoder:
            ogg = directory / f"{stem}.ogg"
            # Ogg normally chooses a random stream serial. Pin muxer/codec
            # behavior and omit inherited tags so the same waveform and encoder
            # build produce the same bytes, independently of output location.
            # Keep the existing Vorbis quality; loop points live in loop.json.
            done = _run([self.transcoder, "-y", "-loglevel", "error", "-i", str(wav),
                         "-c:a", "libvorbis", "-q:a", "4", "-fflags", "+bitexact",
                         "-flags:a", "+bitexact", "-map_metadata", "-1", str(ogg)])
            if done.returncode == 0 and ogg.exists():
                files["ogg"] = f"{prefix}/{ogg.name}"
        return files, None

    def add(self, package, name, data, cues=None, *, namespace=None, report_missing=True):
        """Extract one archive and decode the streams that are asked for.

        *cues* names the cue set to decode; ``None`` means the archive holds one
        sound and the whole of it is wanted.  Returns the package entry.
        """
        relative = Path(namespace) / name if namespace is not None else Path(name)
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            raise ValueError("audio storage must remain within its output directory")
        directory = self.root / relative
        prefix = f"{self.prefix}/{relative.as_posix()}"
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / f"{name}.acb"
        source_sha = hashlib.sha256(data).hexdigest()
        for prior in (self.previous or {}).get("packages", []):
            if prior["package"] == package and Path(prior["archive"]).name == archive.name:
                if prior.get("sourceSha256") != source_sha:
                    raise ValueError("audio archive identity changed; use a fresh output root")
        archive.write_bytes(data)
        entry = {"package": package, "archive": f"{prefix}/{archive.name}",
                 "archiveBytes": len(data), "sourceSha256": source_sha,
                 "status": self.status, "streams": []}
        if not self.decoder:
            entry["error"] = NO_DECODER
            self.packages.append(entry)
            return entry
        head = probe(self.decoder, archive) or {}
        count = head.get("streamCount", 1)
        wanted = None if cues is None else set(cues)
        found = {}
        for subsong in (range(1, count + 1) if count > 1 else (None,)):
            meta = probe(self.decoder, archive, subsong) or {}
            names = meta.get("cues") or []
            if wanted is not None and not (wanted & set(names)):
                continue
            for cue in (sorted(wanted & set(names)) if wanted is not None
                        else names or [name]):
                found.setdefault(cue, []).append((subsong, meta))
        for cue, streams in sorted(found.items()):
            for subsong, meta in streams:
                stem = cue if len(streams) == 1 else f"{cue}.{subsong}"
                files, error = self._decode(archive, directory, prefix, stem,
                                            subsong, meta)
                stream = dict(loop_document(meta), cue=cue, subsong=subsong)
                if files is None:
                    stream["error"] = "; ".join(error or ["decode failed"])
                    self.unsupported.append({"package": package, "cue": cue,
                                             "reason": stream["error"]})
                stream.update(files or {})
                entry["streams"].append(stream)
        for cue in sorted((wanted or set()) - set(found)) if report_missing else ():
            self.unsupported.append({"package": package, "cue": cue,
                                     "reason": NO_CUE})
            entry["streams"].append({"cue": cue, "error": NO_CUE})
        error = attach_sequences(entry, data)
        if error is not None:
            self.unsupported.append({"package": package, "archive": entry["archive"],
                                     "reason": error})
        self.packages.append(entry)
        return entry

    def finish(self):
        """Write the loop sidecar and return the index-level audio document.

        By default the index becomes exactly this run's package list. Weather
        callers opt into preserve_existing so re-exporting its cue subset does
        not erase the rest of the same-source sound corpus. Overlapping banks
        must retain their exact archive hash; changing releases needs a new root.
        """
        # Only tool names, never the paths they were found at: this document ships
        # next to the audio and would otherwise carry one machine's directory layout.
        document = {"status": self.status, "decoder": DECODER,
                    "decoderPresent": bool(self.decoder),
                    "transcoder": TRANSCODER,
                    "transcoderPresent": bool(self.transcoder),
                    "packages": self._preserved_packages()}
        if not self.decoder:
            document["error"] = NO_DECODER
        elif not self.transcoder:
            document["note"] = NO_TRANSCODER
        if self.packages:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / "loop.json"
            path.write_text(dumps(document) + "\n", encoding="utf-8",
                            newline="\n")
            document["file"] = f"{self.prefix}/loop.json"
        return document
