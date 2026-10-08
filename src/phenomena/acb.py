"""The sequence structure of a sound archive's cues, read from the archive's own tables.

A sound archive (the audio middleware's cue sheet) names each sound by a **cue**,
and a cue is not a waveform: it references a *sequence*, which lists *tracks*;
every track carries a small event program (start delay, then a note-on that names
a *synth*), and a synth references the waveforms that are actually heard.  How
the tracks are played is the sequence's **type** — all at once, one after the
other, shuffled, at random — and the sequence's own command list carries further
parameters, one of which makes the sequence start again when a round ends.

The decoder that turns waveforms into audio files reports none of that: it knows
which waveforms answer to a cue name, not which one plays when, after what delay,
or whether the cue ever stops.  A consumer that plays a multi-track cue needs the
structure, and the structure is plain data in the archive, so it is read here
from the raw bytes rather than inferred from what got decoded.

The archive's tables are the middleware's own table format (big-endian rows with
typed columns, a string pool and a data pool; a data cell can hold a nested
table).  Every value is read at the width and signedness its column declares,
and a column stored as "zero" reads as its type's zero, which is what the
middleware's reader returns for it.  Command lists are sequences of
``(code: u16, length: u8, bytes)`` records; they are exported in authored order
and byte-exact, with the values the playback rule reads decoded next to them.

Waveforms are mapped to the decoder's stream numbers through the archive's
embedded waveform container: its header lists the waveform ids in file order,
and the decoder numbers streams in that order from 1.  A waveform stored outside
the archive (a streamed one) has no stream number here and says so.
"""
import struct

# Column storage kinds (high nibble of the column flag byte).
STORAGE_ZERO, STORAGE_CONSTANT, STORAGE_ROW, STORAGE_CONSTANT2 = 0x10, 0x30, 0x50, 0x70
# Column value types (low nibble): exact width and signedness.
TYPES = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q",
         8: ">f", 9: ">d"}
TYPE_STRING, TYPE_DATA = 0x0A, 0x0B
TYPE_NAMES = {0: "u8", 1: "s8", 2: "u16", 3: "s16", 4: "u32", 5: "s32", 6: "u64",
              7: "s64", 8: "f32", 9: "f64", TYPE_STRING: "string", TYPE_DATA: "data"}

# Cue reference kinds (CueTable.ReferenceType).
REFERENCE_WAVEFORM, REFERENCE_SYNTH, REFERENCE_SEQUENCE, REFERENCE_BLOCK_SEQUENCE = 1, 2, 3, 8
# Sequence types (SequenceTable.Type), named as the middleware's cue-type enum
# names them.
SEQUENCE_TYPES = {0: "polyphonic", 1: "sequential", 2: "shuffle", 3: "random",
                  4: "randomNoRepeat", 5: "switchGameVariable", 6: "comboSequential",
                  7: "switchSelector", 8: "trackTransitionBySelector"}
# Synth reference item kinds (SynthTable.ReferenceItems pairs).
ITEM_NONE, ITEM_WAVEFORM, ITEM_SYNTH, ITEM_SEQUENCE = 0, 1, 2, 3

# Track event codes.
EVENT_END = 0            # end of the event program
EVENT_NOTE_ON = 2000     # u16 target kind, u16 target index (2 = synth, 3 = sequence)
EVENT_DELAY_MS = 2001    # u32 milliseconds the note-on is held back
EVENT_DELAY_US = 2005    # s16 microseconds added to the millisecond delay
NOTE_ON_SYNTH, NOTE_ON_SEQUENCE = 2, 3
# Sequence command codes the playback rule reads.
COMMAND_REPEAT = 110     # u8: non-zero = the sequence starts again when a round ends
COMMAND_BUS_SEND = 111   # u16 bus index, u16 level x 10000
COMMAND_PARAMETER_69 = 69  # f32 parameter (big-endian); what it controls is not decoded

NOT_A_CUE_SHEET = "the archive is not a cue sheet (no table header), so it has no sequence structure"
STREAMED = "the waveform is stored outside this archive (streamed), so it has no stream number here"


class TableError(ValueError):
    """The bytes do not hold a well-formed table."""


def is_table(data):
    return bytes(data[:4]) == b"@UTF"


def table(data):
    """One table: ``(name, columns, rows)``.

    *columns* is ``[(name, storage, type name)]`` in declared order; each row is
    a ``{column: value}`` mapping.  Data cells come back as ``bytes`` (a nested
    table stays bytes until :func:`table` is called on it).
    """
    data = bytes(data)
    if not is_table(data):
        raise TableError("no table header")
    size = struct.unpack(">I", data[4:8])[0]
    body = data[8:8 + size]
    if len(body) != size:
        raise TableError("table is shorter than its header says")
    (_version, rows_offset, strings_offset, data_offset, name_offset, count,
     row_width, row_count) = struct.unpack(">HHIIIHHI", body[0:24])

    def string(offset):
        start = strings_offset + offset
        end = body.index(b"\x00", start)
        return body[start:end].decode("utf-8")

    def value(kind, position):
        if kind in TYPES:
            width = struct.calcsize(TYPES[kind])
            return struct.unpack(TYPES[kind], body[position:position + width])[0], width
        if kind == TYPE_STRING:
            return string(struct.unpack(">I", body[position:position + 4])[0]), 4
        if kind == TYPE_DATA:
            offset, length = struct.unpack(">II", body[position:position + 8])
            return body[data_offset + offset:data_offset + offset + length], 8
        raise TableError(f"unknown column type {kind:#x}")

    def zero(kind):
        if kind in (8, 9):
            return 0.0
        return "" if kind == TYPE_STRING else b"" if kind == TYPE_DATA else 0

    columns, specs, position = [], [], 24
    for _ in range(count):
        flags = body[position]
        name = string(struct.unpack(">I", body[position + 1:position + 5])[0])
        position += 5
        storage, kind = flags & 0xF0, flags & 0x0F
        if kind not in TYPE_NAMES:
            raise TableError(f"column {name} has unknown type {kind:#x}")
        constant = None
        if storage in (STORAGE_CONSTANT, STORAGE_CONSTANT2):
            constant, width = value(kind, position)
            position += width
        elif storage not in (STORAGE_ZERO, STORAGE_ROW):
            raise TableError(f"column {name} has unknown storage {storage:#x}")
        columns.append((name, storage, TYPE_NAMES[kind]))
        specs.append((name, storage, kind, constant))
    rows = []
    for index in range(row_count):
        cursor, row = rows_offset + index * row_width, {}
        for name, storage, kind, constant in specs:
            if storage == STORAGE_ROW:
                row[name], width = value(kind, cursor)
                cursor += width
            elif storage == STORAGE_ZERO:
                row[name] = zero(kind)
            else:
                row[name] = constant
        rows.append(row)
    return string(name_offset), columns, rows


def commands(data):
    """A command list as ``[(code, bytes)]`` in authored order."""
    data, out, position = bytes(data), [], 0
    while position < len(data):
        if position + 3 > len(data):
            raise TableError("command list ends inside a record header")
        code, length = struct.unpack(">HB", data[position:position + 3])
        start = position + 3
        if start + length > len(data):
            raise TableError(f"command {code} runs past the end of its list")
        out.append((code, data[start:start + length]))
        position = start + length
    return out


def _command_document(records):
    return [{"code": code, "bytes": payload.hex()} for code, payload in records]


def waveform_container_ids(data):
    """Waveform ids of an embedded waveform container, in file order."""
    data = bytes(data)
    if data[:4] != b"AFS2":
        raise TableError("embedded waveform container has no header")
    id_width = struct.unpack("<H", data[6:8])[0]
    count = struct.unpack("<I", data[8:12])[0]
    if id_width not in (2, 4):
        raise TableError(f"waveform container id width {id_width} is not 2 or 4")
    fmt = "<H" if id_width == 2 else "<I"
    return [struct.unpack(fmt, data[16 + i * id_width:16 + (i + 1) * id_width])[0]
            for i in range(count)]


class CueSheet:
    """The tables of one cue sheet, and the sequence structure of its cues."""

    def __init__(self, data):
        _, _, header = table(data)
        if len(header) != 1:
            raise TableError(f"cue sheet header has {len(header)} rows, not 1")
        self.header = header[0]
        self.tables = {}
        for name, cell in self.header.items():
            if isinstance(cell, bytes) and is_table(cell):
                self.tables[name] = table(cell)[2]
        # Older cue sheets keep the track event programs under another name.
        self.event_table = ("TrackEventTable" if "TrackEventTable" in self.tables
                            else "CommandTable")
        awb = self.header.get("AwbFile")
        self.stream_numbers = ({awb_id: position + 1 for position, awb_id
                                in enumerate(waveform_container_ids(awb))}
                               if isinstance(awb, bytes) and awb else {})
        # Cue name -> cue index.  A cue may answer to several names; a name
        # listed twice keeps its first row.
        self.indices = {}
        for row in self.tables.get("CueNameTable", []):
            self.indices.setdefault(row["CueName"], row["CueIndex"])

    def rows(self, name):
        return self.tables.get(name, [])

    def waveform(self, index):
        row = self.rows("WaveformTable")[index]
        streamed = row.get("Streaming", 0) != 0
        entry = {"waveformIndex": index, "memoryAwbId": row.get("MemoryAwbId"),
                 "streaming": row.get("Streaming", 0), "loopFlag": row.get("LoopFlag"),
                 "encodeType": row.get("EncodeType"), "channels": row.get("NumChannels"),
                 "samplingRate": row.get("SamplingRate"), "samples": row.get("NumSamples")}
        if streamed:
            entry["streamAwbId"] = row.get("StreamAwbId")
            entry["subsong"] = None
            entry["subsongError"] = STREAMED
        else:
            number = self.stream_numbers.get(row.get("MemoryAwbId"))
            entry["subsong"] = number
            if number is None:
                entry["subsongError"] = ("the waveform's id is not in the archive's "
                                         "embedded waveform container")
        return entry

    def synth(self, index, depth=0):
        row = self.rows("SynthTable")[index]
        items = bytes(row.get("ReferenceItems") or b"")
        if len(items) % 4:
            raise TableError(f"synth {index} reference items are not whole pairs")
        references = []
        for position in range(0, len(items), 4):
            kind, target = struct.unpack(">HH", items[position:position + 4])
            item = {"kind": kind, "index": target}
            if kind == ITEM_WAVEFORM:
                item["waveform"] = self.waveform(target)
            elif kind == ITEM_SYNTH and depth < 8:
                item["synth"] = self.synth(target, depth + 1)
            references.append(item)
        command_index = row.get("CommandIndex", 0xFFFF)
        synth_commands = (commands(self.rows("SynthCommandTable")[command_index]["Command"])
                          if command_index != 0xFFFF and self.rows("SynthCommandTable")
                          else [])
        return {"synthIndex": index, "type": row.get("Type"),
                "references": references, "commands": _command_document(synth_commands)}

    def track(self, index):
        row = self.rows("TrackTable")[index]
        event_index = row.get("EventIndex", 0xFFFF)
        events = (commands(self.rows(self.event_table)[event_index]["Command"])
                  if event_index != 0xFFFF else [])
        delay_ms = delay_us = 0
        note_ons, unknown = [], []
        for code, payload in events:
            if code == EVENT_DELAY_MS:
                delay_ms += struct.unpack(">I", payload)[0]
            elif code == EVENT_DELAY_US:
                delay_us += struct.unpack(">h", payload)[0]
            elif code == EVENT_NOTE_ON:
                kind, target = struct.unpack(">HH", payload[:4])
                note = {"kind": kind, "index": target}
                if kind == NOTE_ON_SYNTH:
                    note["synth"] = self.synth(target)
                note_ons.append(note)
            elif code != EVENT_END:
                unknown.append(code)
        command_index = row.get("CommandIndex", 0xFFFF)
        track_commands = (commands(self.rows("TrackCommandTable")[command_index]["Command"])
                          if command_index != 0xFFFF and self.rows("TrackCommandTable")
                          else [])
        return {"trackIndex": index, "events": _command_document(events),
                # Both delay records are subtracted from the track clock before
                # the note-on is reached: milliseconds scaled to microseconds,
                # the microsecond record as it is.
                "startDelayMicroseconds": delay_ms * 1000 + delay_us,
                "delayMilliseconds": delay_ms, "delayMicroseconds": delay_us,
                "noteOns": note_ons, "eventCodesNotDecoded": sorted(set(unknown)),
                "commands": _command_document(track_commands)}

    def sequence(self, cue_index):
        """The structure of one cue, or ``None`` when it references no sequence."""
        cue = self.rows("CueTable")[cue_index]
        if cue.get("ReferenceType") != REFERENCE_SEQUENCE:
            return None
        sequence_index = cue.get("ReferenceIndex")
        row = self.rows("SequenceTable")[sequence_index]
        count = row.get("NumTracks", 0)
        track_list = bytes(row.get("TrackIndex") or b"")
        if len(track_list) < 2 * count:
            raise TableError(f"sequence {sequence_index} lists fewer tracks than it counts")
        tracks = [self.track(struct.unpack(">H", track_list[2 * i:2 * i + 2])[0])
                  for i in range(count)]
        command_index = row.get("CommandIndex", 0xFFFF)
        records = (commands(self.rows("SeqCommandTable")[command_index]["Command"])
                   if command_index != 0xFFFF else [])
        repeat = None
        bus_sends, parameter_69, not_decoded = [], None, []
        for code, payload in records:
            if code == COMMAND_REPEAT:
                repeat = payload[0]
            elif code == COMMAND_BUS_SEND:
                bus, level = struct.unpack(">HH", payload)
                bus_sends.append({"bus": bus, "levelTimes10000": level})
            elif code == COMMAND_PARAMETER_69:
                parameter_69 = struct.unpack(">f", payload)[0]
            else:
                not_decoded.append(code)
        kind = row.get("Type")
        return {"cueIndex": cue_index, "cueId": cue.get("CueId"),
                "length": cue.get("Length"),
                "sequenceIndex": sequence_index, "type": kind,
                "typeName": SEQUENCE_TYPES.get(kind),
                "controlWorkArea": [row.get("ControlWorkArea1"), row.get("ControlWorkArea2")],
                "playbackRatio": row.get("PlaybackRatio"),
                "trackValues": bytes(row.get("TrackValues") or b"").hex(),
                "actionTracks": {"start": row.get("ActionTrackStartIndex"),
                                 "count": row.get("NumActionTracks", 0)},
                "instrumentPluginTracks": {"start": row.get("InstPluginTrackStartIndex"),
                                           "count": row.get("NumInstPluginTracks", 0)},
                "aisac": {"local": bytes(row.get("LocalAisacs") or b"").hex(),
                          "globalStart": row.get("GlobalAisacStartIndex"),
                          "globalCount": row.get("GlobalAisacNumRefs", 0)},
                "commands": _command_document(records),
                "repeat": repeat, "busSends": bus_sends, "parameter69": parameter_69,
                "commandCodesNotDecoded": sorted(set(not_decoded)),
                "tracks": tracks}

    def reference(self, cue_index):
        cue = self.rows("CueTable")[cue_index]
        return {"cueIndex": cue_index, "cueId": cue.get("CueId"),
                "referenceType": cue.get("ReferenceType"),
                "referenceIndex": cue.get("ReferenceIndex"), "length": cue.get("Length")}


def is_plain(structure):
    """Whether a cue's sequence plays exactly one waveform once, from the cue's start.

    Plain = polyphonic, one track, no repeat command, no action or plug-in
    tracks, a track program that is a single note-on of a synth and nothing
    else (no delay), and a synth that references exactly one waveform.  Such a
    cue is heard as its one waveform, looping or not by that waveform's own
    loop flag; everything a plain cue's command lists carry (levels, bus sends,
    parameters) is a mixer parameter, not part of which waveform plays when.
    """
    tracks = structure["tracks"]
    if (structure["type"] != 0 or len(tracks) != 1 or structure["repeat"] is not None
            or structure["actionTracks"]["count"]
            or structure["instrumentPluginTracks"]["count"]):
        return False
    track = tracks[0]
    codes = [event["code"] for event in track["events"]]
    if codes not in ([EVENT_NOTE_ON, EVENT_END], [EVENT_NOTE_ON]):
        return False
    notes = track["noteOns"]
    if len(notes) != 1 or notes[0]["kind"] != NOTE_ON_SYNTH:
        return False
    references = notes[0]["synth"]["references"]
    return len(references) == 1 and references[0]["kind"] == ITEM_WAVEFORM


def reached_waveforms(structure):
    """Every waveform a cue's tracks can sound, as ``[waveform entry]``."""
    out = []

    def walk_synth(synth):
        for item in synth["references"]:
            if "waveform" in item:
                out.append(item["waveform"])
            elif "synth" in item:
                walk_synth(item["synth"])

    for track in structure["tracks"]:
        for note in track["noteOns"]:
            if "synth" in note:
                walk_synth(note["synth"])
    return out


def header_version(data):
    """The cue sheet header's format number (``Version``)."""
    _, _, header = table(data)
    if len(header) != 1:
        raise TableError(f"cue sheet header has {len(header)} rows, not 1")
    return header[0].get("Version")


def cue_commands(structure):
    """The command lists a player reads for a cue's volume, in the block shape.

    The sequence's own list (categories are command 65, volume command 87 and
    89), and per track in authored order the track's list and each note-on's
    synth list.  Plain and structured cues alike.
    """
    return {"commands": structure["commands"],
            "tracks": [{"commands": track["commands"],
                        "noteOns": [{"synth": {"commands": note["synth"]["commands"]}}
                                    for note in track["noteOns"] if "synth" in note]}
                       for track in structure["tracks"]]}


def sequences(data, cues):
    """Sequence structure of the named *cues* of one archive.

    Returns ``(sequences, plain)``: *sequences* maps each cue that references a
    sequence to its structure; *plain* lists the cues that reference something
    else (a waveform or a synth directly), with what they reference.  A cue name
    the archive does not hold is in neither.
    """
    sheet = CueSheet(data)
    indices = sheet.indices
    found, plain = {}, []
    for cue in sorted(set(cues)):
        index = indices.get(cue)
        if index is None:
            continue
        structure = sheet.sequence(index)
        if structure is None:
            plain.append(dict(sheet.reference(index), cue=cue))
        else:
            found[cue] = structure
    return found, plain
