"""Unity ``Shader`` objects: their declared form, and the blobs they carry.

:mod:`shaders.blob` reads one *decompressed per-platform blob* -- the index
table and its records.  Getting that blob out of a ``Shader`` asset is this
module's job, and it is not a single field read:

* ``compressedBlob`` is one byte array holding **every** platform's programs
  back to back.  ``platforms`` names the compiler platforms in order, and
  ``offsets`` / ``compressedLengths`` / ``decompressedLengths`` are parallel
  lists that slice it.  Each of the three is a list *of lists* when a platform
  was written as several LZ4 segments, and a bare integer when it was written
  as one; both spellings occur, so both are accepted here and normalised.
* A platform's segments are decompressed individually and then joined.  The
  index table's offsets address the joined buffer.

Two facts about this container cost real time to rediscover, so they are
written down rather than left to the reader:

* **A Shader object's ``m_Name`` is the empty string.**  The name lives in
  ``m_ParsedForm.m_Name``.  Anything that reads ``m_Name`` -- including the
  cheap "peek the name without parsing" helpers -- gets ``""`` for every
  shader, so a survey filtered by name returns *zero* and looks exactly like a
  true negative.  :func:`shader_name` is the only name reader that should be
  used.
* **The container blocks and the blob are compressed independently.**  A raw
  byte search over the package for a uniform or keyword name therefore returns
  nothing whether or not the name is there, which is why the blob has to be
  decoded rather than grepped.

What is verified and what is not:

* The slicing above is exercised against real packages: every shader in the
  world package and every shader in the player build decodes with no error,
  and :func:`shaders.blob.parse_blob` consumes each record exactly (that byte
  account is the verification, not a spot check).
* Every blob seen so far carries a **single** segment per platform.  The
  multi-segment path below is written from the field layout, not from a
  sample, so a multi-segment blob's join order is **unverified**.
  :func:`platform_blobs` reports the segment count it saw so a caller can tell
  which case it is in rather than assuming.
"""
from UnityPy.helpers import CompressionHelper

CLASS_ID_SHADER = 48

#: ``ShaderCompilerPlatform`` values seen in this game's packages.  Platform 9
#: is GLSL ES source as text; 18 is a compiled binary.  The list is what the
#: data contains, not what the enum defines -- an unseen platform is not an
#: error, it simply has no entry here.
PLATFORM_GLSL = 9
PLATFORM_BINARY = 18
PLATFORM_NAMES = {PLATFORM_GLSL: "glsl", PLATFORM_BINARY: "binary"}


def _as_list(value):
    """One platform's slice fields, whether written as a list or a scalar."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def is_shader(obj):
    """Whether an object reader points at a ``Shader``."""
    return int(obj.type) == CLASS_ID_SHADER


def parsed_form(tree):
    """The ``m_ParsedForm`` block, or an empty mapping when absent."""
    return tree.get("m_ParsedForm") or {}


def shader_name(tree):
    """The shader's real name.

    Falls back to ``m_Name`` only so that a malformed object yields something
    rather than raising; on well-formed data that fallback is the empty string
    and never wins.
    """
    return parsed_form(tree).get("m_Name") or tree.get("m_Name") or ""


def property_names(tree):
    """Material property names the shader declares, in declaration order."""
    info = parsed_form(tree).get("m_PropInfo") or {}
    return [str((prop or {}).get("m_Name")) for prop in (info.get("m_Props") or [])]


def passes(tree):
    """``[(subshader index, pass index, LIGHTMODE tag, pass name)]``.

    The tag list is a pass's own ``m_Tags``; ``LIGHTMODE`` is the one the
    engine dispatches on, and a pass that does not set it yields ``""`` rather
    than being dropped, because "this pass declares no light mode" is a fact a
    caller needs to see.
    """
    out = []
    for sub_index, subshader in enumerate(parsed_form(tree).get("m_SubShaders") or []):
        for pass_index, entry in enumerate((subshader or {}).get("m_Passes") or []):
            tags = (entry or {}).get("m_Tags") or {}
            table = tags.get("tags") if isinstance(tags, dict) else None
            if isinstance(table, dict):
                lookup = {str(k).upper(): str(v) for k, v in table.items()}
            elif isinstance(table, (list, tuple)):
                lookup = {str(k).upper(): str(v) for k, v in table}
            else:
                lookup = {str(k).upper(): str(v) for k, v in (tags or {}).items()
                          if isinstance(k, str)}
            out.append((sub_index, pass_index, lookup.get("LIGHTMODE", ""),
                        str((entry or {}).get("m_Name") or "")))
    return out


def platform_blobs(tree):
    """``[(platform, decompressed bytes, segment count)]`` for one Shader.

    The segment count is reported rather than hidden: everything observed so
    far is 1, and a value above 1 means the caller has reached the join order
    this module documents as unverified.
    """
    blob = tree.get("compressedBlob")
    if isinstance(blob, (list, tuple)):
        blob = bytes(blob)
    if not blob:
        return []
    out = []
    for index, platform in enumerate(tree.get("platforms") or []):
        offsets = _as_list(tree["offsets"][index])
        packed = _as_list(tree["compressedLengths"][index])
        unpacked = _as_list(tree["decompressedLengths"][index])
        segments = [
            bytes(CompressionHelper.decompress_lz4(
                blob[offsets[part]:offsets[part] + packed[part]], unpacked[part]))
            for part in range(len(packed))
        ]
        out.append((int(platform), b"".join(segments), len(segments)))
    return out


def shaders_in(environment):
    """``[(object reader, typetree)]`` for every Shader in a loaded package.

    Reading the typetree is what costs; doing it once here keeps callers from
    reading the same object twice to get first its name and then its blobs.
    """
    out = []
    for obj in environment.objects:
        if is_shader(obj):
            out.append((obj, obj.read_typetree()))
    return out


#: ``m_GpuProgramType`` values and the compiler platform each was observed to
#: address.  Established by cross-check rather than assumed: for every one of
#: the 5,096 subprogram entries in the world package, the record at
#: ``m_BlobIndex`` in that platform's blob carries exactly this program type
#: (0 disagreements, 0 out-of-range).  Two GL types occur, which is why the
#: mapping is not one-to-one and cannot be inverted by taking the first hit.
PROGRAM_TYPE_PLATFORM = {3: PLATFORM_GLSL, 4: PLATFORM_GLSL, 25: PLATFORM_BINARY}

#: The program-block field names a pass can carry, in Unity's own order.
PASS_PROGRAM_FIELDS = ("progVertex", "progFragment", "progGeometry",
                       "progHull", "progDomain", "progRayTracing")


def subprograms(tree):
    """Which pass, stage and keyword set each blob record belongs to.

    Returns ``[{platform, record, subshader, pass, programBlock, lightMode,
    gpuProgramType, keywordIndices, parameterRecord}]``.

    ``programBlock`` is the field a subprogram was **declared under**, not the
    stage of the code it points at.  On this content every entry is declared
    under ``progVertex`` and those entries account for every program record in
    both blobs -- because one GL record holds the whole program, vertex and
    fragment together, under ``#ifdef VERTEX`` / ``#ifdef FRAGMENT``.  Naming
    the field ``stage`` would say each record is vertex-only, which is the
    opposite of true and would send a consumer looking for fragment records
    that do not exist as separate entries.

    A blob record on its own says only "here is a compiled program"; the pass it
    serves, the stage it is, and the keyword combination that selected it live
    in the *declared* form, under
    ``m_SubShaders[].m_Passes[].prog<Stage>.m_PlayerSubPrograms``.  Without this
    link a survey can group records by content hash but cannot say which pass's
    render state applies to one, and render state is per pass -- so a value
    comparison that ignores the link is comparing numbers whose meaning it does
    not know.

    The outer list of ``m_PlayerSubPrograms`` is **not** indexed by the shader's
    ``platforms`` field: the world package's shaders declare two platforms and
    that list has four slots with only one populated.  The platform comes from
    ``m_GpuProgramType`` (see :data:`PROGRAM_TYPE_PLATFORM`), and
    ``m_BlobIndex`` then indexes *that platform's own* blob -- both platforms
    reuse the same index range, so reading the index without first resolving the
    platform silently attributes half the records to the wrong blob.
    """
    out = []
    for sub_index, subshader in enumerate(parsed_form(tree).get("m_SubShaders") or []):
        for pass_index, entry in enumerate((subshader or {}).get("m_Passes") or []):
            light_mode = ""
            for s, p, mode, _ in passes(tree):
                if (s, p) == (sub_index, pass_index):
                    light_mode = mode
                    break
            for field in PASS_PROGRAM_FIELDS:
                block = (entry or {}).get(field) or {}
                slots = block.get("m_PlayerSubPrograms") or []
                parameter_slots = block.get("m_ParameterBlobIndices") or []
                for slot_index, slot in enumerate(slots):
                    parameters = (parameter_slots[slot_index]
                                  if slot_index < len(parameter_slots) else []) or []
                    for item_index, item in enumerate(slot or []):
                        program_type = (item or {}).get("m_GpuProgramType")
                        out.append({
                            "platform": PROGRAM_TYPE_PLATFORM.get(program_type),
                            "record": (item or {}).get("m_BlobIndex"),
                            "subshader": sub_index,
                            "pass": pass_index,
                            "programBlock": field,
                            "lightMode": light_mode,
                            "gpuProgramType": program_type,
                            "keywordIndices": list((item or {}).get("m_KeywordIndices") or []),
                            "parameterRecord": (parameters[item_index]
                                                if item_index < len(parameters) else None),
                        })
    return out


def attribution_check(tree, parsed_by_platform):
    """Whether every subprogram's record really carries the type it claims.

    ``parsed_by_platform`` maps a platform to a
    :func:`shaders.blob.parse_blob` result.  Returns
    ``{"checked", "agree", "typeMismatch", "outOfRange"}``.  The check is the
    reason :func:`subprograms` can be trusted at all: the platform is inferred
    from ``m_GpuProgramType``, and if that inference were wrong the record it
    points at would hold a different program type.  A caller that reports an
    attribution without running this is reporting an assumption.
    """
    counts = {"checked": 0, "agree": 0, "typeMismatch": 0, "outOfRange": 0}
    for item in subprograms(tree):
        counts["checked"] += 1
        parsed = parsed_by_platform.get(item["platform"])
        records = (parsed or {}).get("records") or []
        index = item["record"]
        if not isinstance(index, int) or not 0 <= index < len(records):
            counts["outOfRange"] += 1
        elif records[index].get("programType") != item["gpuProgramType"]:
            counts["typeMismatch"] += 1
        else:
            counts["agree"] += 1
    return counts


def attribution_coverage(tree, parsed_by_platform):
    """Whether the attribution partitions the program records, or merely touches them.

    Returns ``{"programRecords", "claimedOnce", "claimedTwiceOrMore",
    "unclaimed"}`` per the whole object.  This is a different question from
    :func:`attribution_check` and both are needed: that one asks whether the
    records an attribution names are the right *kind*, this one asks whether
    every record is named and named once.  A consistent attribution that covers
    half the records reads as working, because everything it does say is true.
    """
    counts = {"programRecords": 0, "claimedOnce": 0,
              "claimedTwiceOrMore": 0, "unclaimed": 0}
    rows = subprograms(tree)
    for platform, parsed in parsed_by_platform.items():
        program_indexes = [index for index, record in enumerate(parsed.get("records") or [])
                           if record.get("kind") == "program"]
        claims = {}
        for item in rows:
            if item["platform"] == platform:
                claims[item["record"]] = claims.get(item["record"], 0) + 1
        counts["programRecords"] += len(program_indexes)
        for index in program_indexes:
            times = claims.get(index, 0)
            if times == 0:
                counts["unclaimed"] += 1
            elif times == 1:
                counts["claimedOnce"] += 1
            else:
                counts["claimedTwiceOrMore"] += 1
    return counts
