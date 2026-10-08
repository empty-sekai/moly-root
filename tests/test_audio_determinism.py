"""Actual optional Vorbis encoding must not randomize immutable artifacts."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import wave

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from phenomena import audio


META = {"sampleRate": 44100, "channels": 1, "samples": 44100,
        "loopStart": 4410, "loopEnd": 39690, "encoding": "synthetic PCM"}


def waveform(comment=""):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(index * math.tau * 440 / 44100)))
                                   for index in range(44100)))
    data = bytearray(buffer.getvalue())
    if comment:
        tag = comment.encode("utf-8") + b"\0"
        info = b"INFO" + b"ICMT" + struct.pack("<I", len(tag)) + tag + b"\0" * (len(tag) % 2)
        data += b"LIST" + struct.pack("<I", len(info)) + info
        struct.pack_into("<I", data, 4, len(data) - 8)
    return bytes(data)


def library(tmp_path, transcoder):
    result = audio.Library(tmp_path, "phenomena/audio")
    result.decoder = "synthetic-decoder"
    result.transcoder = transcoder
    return result


def decode(lib, directory):
    directory.mkdir(parents=True, exist_ok=True)
    return lib._decode(directory / "source.acb", directory, "phenomena/audio/bank", "cue", 2, META)


def test_audio_command_pins_muxer_and_codec_without_changing_quality_or_loop_semantics(tmp_path, monkeypatch):
    calls = []

    def fake_run(argv):
        calls.append(argv)
        if argv[0] == "synthetic-decoder":
            Path(argv[argv.index("-o") + 1]).write_bytes(waveform())
        else:
            Path(argv[-1]).write_bytes(b"synthetic encoded audio")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(audio, "_run", fake_run)
    lib = library(tmp_path, "synthetic-ffmpeg")
    files, error = decode(lib, tmp_path / "bank")
    assert error is None and files == {"wav": "phenomena/audio/bank/cue.wav", "ogg": "phenomena/audio/bank/cue.ogg"}
    assert calls[0][1:3] == ["-i", "-o"]  # vgmstream ignores authored loop playback.
    assert calls[0][calls[0].index("-s") + 1] == "2"
    command = calls[1]
    for option, expected in [("-c:a", "libvorbis"), ("-q:a", "4"), ("-fflags", "+bitexact"),
                             ("-flags:a", "+bitexact"), ("-map_metadata", "-1")]:
        assert command[command.index(option) + 1] == expected
        assert command.index(option) > command.index("-i")
    assert audio.loop_document(META) == {"loop": True, "loopStartSeconds": 0.1, "loopEndSeconds": 0.9,
                                       "loopStartSamples": 4410, "loopEndSamples": 39690, "sampleRate": 44100,
                                       "channels": 1, "samples": 44100, "durationSeconds": 1.0, "encoding": "synthetic PCM"}


def test_real_vorbis_is_byte_identical_across_repeats_relocation_and_source_tags(tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("optional ffmpeg executable is unavailable")
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True)
    if "libvorbis" not in encoders.stdout:
        pytest.skip("optional ffmpeg build does not provide libvorbis")
    real_run, comment = audio._run, "first machine source tag"

    def decode_waveform(argv):
        if argv[0] == "synthetic-decoder":
            Path(argv[argv.index("-o") + 1]).write_bytes(waveform(comment))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return real_run(argv)

    monkeypatch.setattr(audio, "_run", decode_waveform)
    lib = library(tmp_path, ffmpeg)
    first, moved = tmp_path / "first", tmp_path / "relocated/other machine"
    files, error = decode(lib, first)
    assert error is None and "ogg" in files
    original = (first / "cue.ogg").read_bytes()
    decode(lib, first)
    assert (first / "cue.ogg").read_bytes() == original
    comment = "different machine source tag"
    files, error = decode(lib, moved)
    assert error is None and "ogg" in files
    assert (moved / "cue.ogg").read_bytes() == original
    assert original.startswith(b"OggS") and struct.unpack_from("<I", original, 14)[0] == 0
    assert b"first machine" not in original and b"different machine" not in original

    # Existing and deterministic profiles encode identical audio samples; only
    # container metadata changes. Quality remains libvorbis q4.
    legacy = tmp_path / "legacy.ogg"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(first / "cue.wav"),
                    "-c:a", "libvorbis", "-q:a", "4", str(legacy)], check=True, capture_output=True)
    def pcm(path):
        return subprocess.run([ffmpeg, "-loglevel", "error", "-i", str(path), "-map_metadata", "-1",
                               "-f", "s16le", "-c:a", "pcm_s16le", "-"], check=True, capture_output=True).stdout
    assert pcm(legacy) == pcm(first / "cue.ogg")

    lib.packages = [{"package": "synthetic", "streams": [audio.loop_document(META)]}]
    lib.finish()
    sidecar = json.loads((tmp_path / "loop.json").read_bytes())
    assert sidecar["packages"][0]["streams"][0] == audio.loop_document(META)
