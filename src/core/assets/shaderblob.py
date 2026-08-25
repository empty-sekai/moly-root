"""Shader program blobs: the compiled-variant table a Shader carries per platform.

A ``Shader`` asset keeps its compiled programs in ``compressedBlob``, one LZ4 segment
per ``ShaderCompilerPlatform``.  Decompressing a platform's segments and joining them
yields a **blob**: a little-endian index table followed by variable-length records.
Reading it is what turns "which uniforms does this shader consume" from a guess into a
count -- the names live nowhere else, because the container blocks and the blob are
compressed independently, so a raw byte grep over the package returns ``0`` regardless
of the truth.

Since Unity 2021.2 (record version ``202012090``) a blob holds **two kinds** of
record, and they do not share a layout:

* a **program** record -- keywords plus the compiled code for one variant;
* a **parameter** record -- the reflection table (constant buffers, their members,
  texture/sampler/buffer bindings) shared by several variants.

Parsing every record as a program is the failure this module exists to avoid: the
parameter table's leading string length is read as a keyword count, its member names
as keyword strings, and one member name lands halfway inside the "code" byte array.
The result is a *truncated* uniform name -- worse than no name at all, because it does
not raise, so "the full name is nowhere in this shader" looks like negative evidence.

The layouts below are transcribed from the engine's own reader, the Android player
binary of editor 2022.3.62f2 (the game ships f3 -- same minor, and the gates in these
functions are record-version gates, not build gates):

* ``libunity.so:LoadVariantFromData`` -- program record;
* ``libunity.so:LoadParametersFromData`` -- parameter record;
* ``libunity.so:GetGpuProgramTypeFromData`` -- why a program record carries its type
  at a fixed offset, and where the oldest readable record version comes from;
* ``libunity.so:ReadString`` -- every string: ``int32`` length, then that many bytes,
  then the cursor advances to the next 4-byte boundary;
* ``libunity.so:TryLoadVariantFromBlob`` -- hands the reader ``entry_start +
  entry_length`` as its end pointer, which is why a correct parse consumes a record
  *exactly*.  That property is the whole verification story: a mis-sized field cannot
  leave the byte account balanced, so :func:`parse_blob` never has to guess.

What has actually been exercised, and what has not:

* Every blob observed so far carries a **single** LZ4 segment per platform.  The
  ``segment`` field is therefore recorded but never used for addressing, and whether a
  multi-segment blob's table offsets are absolute within the joined buffer is **not
  verified** -- there was no sample to verify it against.
* Only record version ``202012090`` has been run against real data.  The older branches
  follow the same engine reader, but they are **unexercised**; treat a parse of an older
  blob as unverified until a sample balances its byte account.
"""
import re
import struct

# Record-version gates, spelled as the engine spells them (YYYYMMDD plus one digit).
# Each is a literal comparison in the reader, so they are copied, not inferred.
VERSION_MIN = 201609010            # LoadVariantFromData rejects anything older
VERSION_STRUCT_PARAMS = 201703280  # constant buffers gained a struct-member section
VERSION_TEXTURE_EXTRA = 201708220  # texture bindings gained a fourth int
VERSION_TEXTURE_PACKED = 201802150  # ... which from here packs dim and multisampled
VERSION_LOCAL_KEYWORDS = 201806140  # a per-variant keyword list appears ...
VERSION_LOCAL_KEYWORDS_END = 202011577  # ... and is gone again by 2021.2

# Index-table stride: (offset, length), plus a segment index from 2019.3 on.
ENTRY_STRIDE_SEGMENTED = 12
ENTRY_STRIDE_PLAIN = 8

# ShaderGpuProgramType values whose program code is GLSL *text*; everything else is a
# binary container (DXBC, SPIR-V, Metal) that must not be mined for identifiers.
GL_TEXT_TYPES = frozenset(range(1, 9))

# Binding kinds in the record that follows the constant buffers.  The engine
# dispatches on this int through a five-entry jump table, and only a texture carries a
# fourth int, so the kind decides the record length and cannot be skipped over.
BIND_TEXTURE = 0
BIND_CONSTANT_BUFFER = 1
BIND_BUFFER = 2
BIND_UAV = 3
BIND_SAMPLER = 4
BIND_KINDS = {BIND_TEXTURE: "texture", BIND_CONSTANT_BUFFER: "constantBuffer",
              BIND_BUFFER: "buffer", BIND_UAV: "uav", BIND_SAMPLER: "sampler"}

# Identifiers inside GLSL text.  HLSLcc renames a constant-buffer member that the
# variant never reads to ``Xhlslcc_UnusedX<name>``, so a scan can tell "read" from
# "declared only" without guessing.
_IDENTIFIER = re.compile(r"(Xhlslcc_UnusedX)?(_[A-Za-z][A-Za-z0-9_]*)")


class BlobError(ValueError):
    """A blob, or one record in it, does not parse as the engine would read it."""


class _Cursor:
    """Bounds-checked little-endian reader over one record of the blob.

    Alignment is computed on blob-absolute positions because that is what the engine
    does -- it rounds a pointer, not an offset -- and record starts are themselves
    4-aligned, so the two can only differ if a blob is malformed.
    """

    def __init__(self, blob, start, end):
        self.blob = blob
        self.pos = start
        self.end = end

    def remaining(self):
        return self.end - self.pos

    def int32(self):
        if self.remaining() < 4:
            raise BlobError(f"want 4 bytes at {self.pos}, {self.remaining()} left")
        value, = struct.unpack_from("<i", self.blob, self.pos)
        self.pos += 4
        return value

    def ints(self, count):
        return [self.int32() for _ in range(count)]

    def string(self):
        length = self.int32()
        if length < 0 or (length + 3) & ~3 > self.remaining():
            raise BlobError(f"string of {length} bytes does not fit at {self.pos}")
        raw = self.blob[self.pos:self.pos + length]
        self.pos = (self.pos + length + 3) & ~3
        return raw.decode("utf-8", "replace")

    def byte_array(self, length):
        if length < 0 or length > self.remaining():
            raise BlobError(f"byte array of {length} does not fit at {self.pos}")
        raw = self.blob[self.pos:self.pos + length]
        self.pos += length
        return raw


def _parse_program(cursor, version):
    """One program record, after its version int: keywords, code, bind channels."""
    program_type = cursor.int32()
    # Four ints the engine reads and throws away (ALU/TEX/flow/temp-register stats).
    stats = cursor.ints(4)
    keywords = [cursor.string() for _ in range(cursor.int32())]
    local_keywords = None
    if VERSION_LOCAL_KEYWORDS <= version <= VERSION_LOCAL_KEYWORDS_END:
        local_keywords = [cursor.string() for _ in range(cursor.int32())]
    code = cursor.byte_array(cursor.int32())
    cursor.pos = (cursor.pos + 3) & ~3
    # Vertex-channel bindings: a mask the engine ORs into the subprogram's bound
    # channels, then a count, then one (source, target) pair each.  The record ends
    # here -- the engine reads nothing after the last pair.
    channel_source_map = cursor.int32()
    channels = [tuple(cursor.ints(2)) for _ in range(cursor.int32())]
    return {"kind": "program", "programType": program_type, "stats": stats,
            "keywords": keywords, "localKeywords": local_keywords, "code": code,
            "channelSourceMap": channel_source_map, "channels": channels}


def _parse_value_param(cursor):
    """A constant-buffer member: a name, then six ints.

    The engine passes four of them on (index, array size, type, and either the row
    count of a matrix or the dimension of a vector) and branches on the fourth to pick
    matrix versus vector, so that one is the only int whose meaning this module relies
    on.  All six are kept verbatim.
    """
    name = cursor.string()
    raw = cursor.ints(6)
    return {"name": name, "raw": raw, "isMatrix": raw[3] != 0, "type": raw[0],
            "dim": raw[2], "arraySize": raw[4], "index": raw[5]}


def _parse_parameters(cursor, version):
    """One parameter record, after its version int: constant buffers, then bindings."""
    buffers = []
    for _ in range(cursor.int32()):
        name = cursor.string()
        size = cursor.int32()
        params = [_parse_value_param(cursor) for _ in range(cursor.int32())]
        structs = []
        if version >= VERSION_STRUCT_PARAMS:
            for _ in range(cursor.int32()):
                struct_name = cursor.string()
                struct_raw = cursor.ints(3)
                fields = [_parse_value_param(cursor) for _ in range(cursor.int32())]
                structs.append({"name": struct_name, "raw": struct_raw,
                                "fields": fields})
        buffers.append({"name": name, "size": size, "params": params,
                        "structs": structs})
    bindings = []
    for _ in range(cursor.int32()):
        name = cursor.string()
        raw = cursor.ints(3)
        kind = raw[0]
        binding = {"name": name, "kind": kind,
                   "kindName": BIND_KINDS.get(kind, str(kind)), "raw": raw}
        if kind == BIND_TEXTURE and version >= VERSION_TEXTURE_EXTRA:
            packed = cursor.int32()
            binding["packed"] = packed
            if version >= VERSION_TEXTURE_PACKED:
                binding["dim"] = (packed >> 1) & 0x7F
                binding["multisampled"] = bool(packed & 1)
            else:
                binding["dim"] = packed >> 8
                binding["multisampled"] = bool(packed & 0xFF)
        bindings.append(binding)
    return {"kind": "parameters", "constantBuffers": buffers, "bindings": bindings}


def parse_record(blob, start, length):
    """Parse one record both ways and keep the reading that consumes it exactly.

    A program and a parameter record both open with the version int, and nothing in
    the record says which it is -- the shader's parsed form does, through
    ``m_ParameterBlobIndices``.  Rather than depend on that cross-reference, both
    layouts are tried and the byte account decides: the engine's reader is handed
    ``start + length`` as its end pointer, so the right layout lands exactly on it
    while a wrong one overshoots or stops short.
    """
    end = start + length
    if length < 4:
        raise BlobError(f"record at {start} is {length} bytes, too short for a version")
    version, = struct.unpack_from("<i", blob, start)
    if version < VERSION_MIN:
        raise BlobError(f"record version {version} predates {VERSION_MIN}")
    accepted = []
    for parse in (_parse_program, _parse_parameters):
        cursor = _Cursor(blob, start + 4, end)
        try:
            parsed = parse(cursor, version)
        except BlobError:
            continue
        if cursor.pos == end:
            accepted.append(parsed)
    if not accepted:
        raise BlobError(f"record at {start} (+{length}) parses as neither a program "
                        f"nor a parameter table")
    if len(accepted) > 1:
        raise BlobError(f"record at {start} (+{length}) parses as both a program and "
                        f"a parameter table; the byte account cannot decide")
    parsed = accepted[0]
    parsed.update({"offset": start, "length": length, "version": version})
    return parsed


def entry_table(blob, stride=None):
    """The index table: ``[(offset, length, segment)]``, one tuple per record.

    ``stride`` is 12 from Unity 2019.3 (the third int is a segment index) and 8
    before.  Left at ``None`` it is inferred from the table itself: the first record
    starts immediately after the table, so only one stride puts the header size and
    the first offset in agreement.
    """
    if len(blob) < 4:
        raise BlobError(f"blob of {len(blob)} bytes holds no index table")
    count, = struct.unpack_from("<i", blob, 0)
    if count < 0:
        raise BlobError(f"index table claims {count} records")
    for candidate in ([stride] if stride else
                      [ENTRY_STRIDE_SEGMENTED, ENTRY_STRIDE_PLAIN]):
        header = 4 + count * candidate
        if header > len(blob):
            continue
        if count and struct.unpack_from("<i", blob, 4)[0] != header:
            continue
        entries = []
        for index in range(count):
            layout = "<3i" if candidate == ENTRY_STRIDE_SEGMENTED else "<2i"
            fields = struct.unpack_from(layout, blob, 4 + index * candidate)
            entries.append((fields[0], fields[1],
                            fields[2] if len(fields) > 2 else 0))
        return entries, candidate
    raise BlobError(f"no index-table stride explains {count} records in "
                    f"{len(blob)} bytes")


def account(blob, entries, header_size):
    """Byte account over the blob: what the table covers, and what it leaves out.

    Returns ``{"total", "header", "records", "gaps", "gapBytes", "overlaps",
    "balanced"}``.  ``balanced`` is the decisive check on the table: header plus
    record bytes plus gaps must equal the blob, with no record overlapping another and
    none running past the end.  A mis-read record length shows up here and nowhere
    else, which is why it is computed even when the caller only wants names.
    """
    total = len(blob)
    spans = sorted((offset, offset + length) for offset, length, _ in entries)
    overlaps, gaps, covered = [], [], 0
    reach = header_size
    for start, stop in spans:
        if start < reach:
            overlaps.append((start, reach))
        elif start > reach:
            gaps.append((reach, start))
        covered += max(0, stop - start)
        reach = max(reach, stop)
    if reach < total:
        gaps.append((reach, total))
    gap_bytes = sum(stop - start for start, stop in gaps)
    balanced = (not overlaps and reach <= total
                and header_size + covered + gap_bytes == total)
    return {"total": total, "header": header_size, "records": covered,
            "gaps": gaps, "gapBytes": gap_bytes, "overlaps": overlaps,
            "balanced": balanced}


def record_names(record):
    """Every uniform-ish name one record spells out, as a set.

    For a parameter record these are exact: constant-buffer names, their members,
    struct members, and binding names, each read as a length-prefixed string, so a
    name is either complete or the record fails to parse.  For a program record whose
    code is GLSL text the names come from scanning that text; for a binary program
    type there are none to report, and inventing some by scanning bytes would be
    noise, not evidence.
    """
    if record["kind"] == "parameters":
        names = set()
        for buffer in record["constantBuffers"]:
            names.add(buffer["name"])
            names.update(param["name"] for param in buffer["params"])
            for entry in buffer["structs"]:
                names.add(entry["name"])
                names.update(field["name"] for field in entry["fields"])
        names.update(binding["name"] for binding in record["bindings"])
        return {name for name in names if name}
    if record["programType"] in GL_TEXT_TYPES:
        text = record["code"].decode("utf-8", "replace")
        return {name for _, name in _IDENTIFIER.findall(text)}
    return set()


def parse_blob(blob, platform, stride=None):
    """Parse one decompressed per-platform blob.

    ``blob`` is the concatenation of that platform's decompressed LZ4 segments, and
    ``platform`` is its ``ShaderCompilerPlatform``, carried through for reporting --
    the layout does not depend on it, the record version does.

    Returns ``{"platform", "stride", "records", "account"}``.  Raises
    :class:`BlobError` rather than returning a partial reading: a blob that does not
    balance means a parser bug or an unknown version, and quietly returning half of it
    is how a truncated name gets mistaken for evidence.
    """
    entries, stride = entry_table(blob, stride)
    header_size = 4 + len(entries) * stride
    balance = account(blob, entries, header_size)
    if not balance["balanced"]:
        raise BlobError(f"index table does not balance: {balance}")
    records = []
    for index, (offset, length, segment) in enumerate(entries):
        record = parse_record(blob, offset, length)
        record.update({"index": index, "segment": segment})
        records.append(record)
    return {"platform": int(platform), "stride": stride, "records": records,
            "account": balance}


def uniform_names(parsed):
    """Union of :func:`record_names` over every record of a parsed blob."""
    names = set()
    for record in parsed["records"]:
        names |= record_names(record)
    return names
