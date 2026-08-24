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
"""
import re
import shutil
import subprocess
from pathlib import Path

from core.jsonio import dumps

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
            "loopStartSeconds": round(start / rate, 6) if looped and rate else None,
            "loopEndSeconds": round(end / rate, 6) if looped and rate else None,
            "loopStartSamples": start, "loopEndSamples": end,
            "sampleRate": meta.get("sampleRate"), "channels": meta.get("channels"),
            "samples": samples,
            "durationSeconds": round(samples / rate, 6) if rate else None,
            "encoding": meta.get("encoding")}


def archive_bytes(value):
    """Raw bytes of a text asset, whichever way the reader handed it over."""
    if isinstance(value, str):
        return value.encode("utf-8", "surrogateescape")
    return bytes(value or b"")


class Library:
    """Audio of one run: archives always, waveforms when the decoder is present."""

    def __init__(self, directory, prefix, decoder=None, transcoder=None):
        self.root = Path(directory)
        self.prefix = prefix
        self.decoder = tool(DECODER, decoder)
        self.transcoder = tool(TRANSCODER, transcoder)
        self.packages = []
        self.unsupported = []

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
            done = _run([self.transcoder, "-y", "-loglevel", "error", "-i", str(wav),
                         "-c:a", "libvorbis", "-q:a", "4", str(ogg)])
            if done.returncode == 0 and ogg.exists():
                files["ogg"] = f"{prefix}/{ogg.name}"
        return files, None

    def add(self, package, name, data, cues=None):
        """Extract one archive and decode the streams that are asked for.

        *cues* names the cue set to decode; ``None`` means the archive holds one
        sound and the whole of it is wanted.  Returns the package entry.
        """
        directory = self.root / name
        prefix = f"{self.prefix}/{name}"
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / f"{name}.acb"
        archive.write_bytes(data)
        entry = {"package": package, "archive": f"{prefix}/{archive.name}",
                 "archiveBytes": len(data), "status": self.status, "streams": []}
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
        for cue in sorted((wanted or set()) - set(found)):
            self.unsupported.append({"package": package, "cue": cue,
                                     "reason": NO_CUE})
            entry["streams"].append({"cue": cue, "error": NO_CUE})
        self.packages.append(entry)
        return entry

    def finish(self):
        """Write the loop sidecar and return the index-level audio document."""
        # Only tool names, never the paths they were found at: this document ships
        # next to the audio and would otherwise carry one machine's directory layout.
        document = {"status": self.status, "decoder": DECODER,
                    "decoderPresent": bool(self.decoder),
                    "transcoder": TRANSCODER,
                    "transcoderPresent": bool(self.transcoder),
                    "packages": self.packages}
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
