"""What a package's shaders actually contain, counted rather than assumed.

A shader family is not one program.  A single ``Shader`` asset compiles to one
program per (pass, stage, keyword combination) per platform, and the count is
not small: the heaviest family in this game's world package carries 1,194
program records on one platform alone.  Any statement of the form "this family
is ported" therefore has to say *how many of its programs* are covered, and
that needs a denominator.

This module produces it.  For one package it reports, per shader: the declared
form (name, properties, passes and their light modes), and per platform the
program records with their keyword sets, program type, byte length and content
hash.

Two counts matter and they are not the same:

* **records** -- every *program* record.  A blob's index table also holds
  **parameter** records, the reflection tables several variants share; they are
  counted separately under ``parameterRecords`` and never as programs.  Mixing
  the two inflates a package's denominator by roughly two fifths, which reads
  as progress being harder than it is.
* **unique programs** -- records grouped by content hash.  Variants that
  compile to identical code are one piece of work, not many, and the ratio is
  large in practice: the world package's 2,545 GLSL records are 925 distinct
  programs.  Reviewing by record inflates the work by nearly threefold and
  hides that the two heaviest families dominate it.

Nothing here interprets a program.  Bodies are addressed by
``(shader, platform, record index, content hash)`` and handed back verbatim,
because the moment a survey starts editing what it reports it stops being
usable as a denominator.

A survey filtered by shader *name* is not a denominator either.  Names are an
author's habit, not a classification: this game's world package holds families
whose names carry no product prefix at all, so a name filter drops exactly the
irregularly named part and the loss looks identical to absence.  Pair this with
a name-blind criterion -- which shaders the domain's materials actually point
at -- before calling a denominator complete.
"""
import hashlib

from . import blob as blobs
from . import objects


def _sha256(data):
    return hashlib.sha256(data).hexdigest().upper()


def program_rows(name, platform, parsed, keep_code=False):
    """One row per program record of one platform of one shader.

    *keep_code* attaches the raw body under ``code``.  It is off by default
    because a package's bodies are tens of megabytes and a census is meant to
    be serialisable; a caller that wants bodies asks for them, and
    :func:`without_code` strips them again before serialising.
    """
    rows = []
    for index, record in enumerate(parsed.get("records") or []):
        if record.get("kind") != "program":
            continue
        code = record.get("code") or b""
        rows.append({
            "shader": name,
            "platform": platform,
            "record": index,
            "programType": record.get("programType"),
            "isText": record.get("programType") in blobs.GL_TEXT_TYPES,
            "keywords": sorted(record.get("keywords") or []),
            "localKeywords": sorted(record.get("localKeywords") or []),
            "codeBytes": len(code),
            "codeSha256": _sha256(code),
        })
        if keep_code:
            rows[-1]["code"] = code
    return rows


def shader_entry(tree, keep_code=False):
    """The full record for one Shader object: declared form plus programs.

    A platform whose blob cannot be parsed is kept with its ``error`` rather
    than dropped, so the entry count always equals the shader count and a
    failure cannot masquerade as a family that simply has no programs.
    """
    name = objects.shader_name(tree)
    entry = {
        "shader": name,
        "properties": objects.property_names(tree),
        "passes": [{"subshader": s, "pass": p, "lightMode": mode, "name": pass_name}
                   for s, p, mode, pass_name in objects.passes(tree)],
        "platforms": [],
    }
    for platform, raw, segments in objects.platform_blobs(tree):
        record = {"platform": platform, "segments": segments,
                  "decompressedBytes": len(raw), "rawSha256": _sha256(raw)}
        try:
            parsed = blobs.parse_blob(raw, platform)
        except Exception as exc:                                    # noqa: BLE001
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["programs"] = []
        else:
            record["programs"] = program_rows(name, platform, parsed, keep_code)
            record["parameterRecords"] = sum(
                1 for item in parsed["records"] if item.get("kind") == "parameters")
            record["uniformNames"] = sorted(blobs.uniform_names(parsed))
        entry["platforms"].append(record)
    return entry


def census(environment, keep_code=False):
    """Every Shader in a loaded package, in name order."""
    entries = [shader_entry(tree, keep_code) for _, tree in objects.shaders_in(environment)]
    entries.sort(key=lambda item: item["shader"])
    return entries


def totals(entries, platform=None):
    """Counts a coverage claim is measured against.

    *platform* narrows to one compiler platform; omitting it counts them all,
    which is rarely what a port wants -- a binary platform's records are the
    same variants as the text platform's, so counting both doubles the
    denominator without adding work.
    """
    records = unique = 0
    seen = set()
    errors = 0
    for entry in entries:
        for block in entry["platforms"]:
            if platform is not None and block["platform"] != platform:
                continue
            if block.get("error"):
                errors += 1
            for row in block["programs"]:
                records += 1
                if row["codeSha256"] not in seen:
                    seen.add(row["codeSha256"])
                    unique += 1
    return {"shaders": len(entries), "records": records,
            "uniquePrograms": unique, "platformErrors": errors}


def by_shader(entries, platform=None):
    """``[(shader name, records, unique programs)]``, heaviest first.

    The ordering is the point: a port planned in name order spends its first
    passes on families that are a rounding error in the denominator.
    """
    rows = []
    for entry in entries:
        records = 0
        seen = set()
        for block in entry["platforms"]:
            if platform is not None and block["platform"] != platform:
                continue
            for row in block["programs"]:
                records += 1
                seen.add(row["codeSha256"])
        rows.append((entry["shader"], records, len(seen)))
    rows.sort(key=lambda row: (-row[2], -row[1], row[0]))
    return rows


def without_code(entries):
    """The same entries with program bodies dropped, ready to serialise."""
    return [
        {**entry,
         "platforms": [
             {**block,
              "programs": [{k: v for k, v in row.items() if k != "code"}
                           for row in block["programs"]]}
             for block in entry["platforms"]]}
        for entry in entries
    ]
