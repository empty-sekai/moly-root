"""Character registry: who is in the pack, and how each of them moves.

Three master tables describe a character beyond its model: identity (unit and
colours), locomotion personality (which idle / walk / run motion, how fast, how
often it runs, how long it pauses), and the name of its alone-action script.
This module merges them for a given set of characters and says explicitly which
source rows were missing rather than filling a default in.

Four locomotion values are stored a thousand times larger than the unit the
runtime uses: it divides ``pauseMilliSeconds``, ``changeMotionMilliSeconds``,
``walkSpeed`` and ``runSpeed`` by 1000 right after reading the table, so a walk
speed of 400 is 0.4 metres per second.  Both the stored value and the runtime
value are emitted — a consumer that uses the raw number would move characters
1000× too fast.
"""

MILLI = 1000.0
# {stored field: (runtime field, unit)} — the runtime divides each by 1000.
DERIVED = {
    "pauseMilliSeconds": ("pauseSeconds", "seconds"),
    "changeMotionMilliSeconds": ("changeMotionSeconds", "seconds"),
    "walkSpeed": ("walkSpeedMetersPerSecond", "metres per second"),
    "runSpeed": ("runSpeedMetersPerSecond", "metres per second"),
}
IDENTITY_FIELDS = ("gameCharacterId", "unit", "colorCode", "skinColorCode",
                   "skinShadowColorCode1", "skinShadowColorCode2")
LOCOMOTION_FIELDS = ("idleMotion", "walkMotion", "runMotion", "walkSpeed", "runSpeed",
                     "runOccurRate", "pauseMilliSeconds", "changeMotionMilliSeconds")
MOTION_FIELDS = ("idleMotion", "walkMotion", "runMotion")

SEMANTICS = {
    "membership": ("the caller supplies the character set; this module never guesses "
                   "who belongs in a pack"),
    "missingSource": ("a character whose identity / locomotion / soloAction row is "
                      "absent gets `null` there and is listed in summary.missing — "
                      "absent is never filled with a default"),
    "storedVsRuntime": ("the runtime divides pauseMilliSeconds, "
                        "changeMotionMilliSeconds, walkSpeed and runSpeed by 1000; "
                        "both forms are given and the runtime form is the one to use"),
    "runOccurRate": "percentage chance of running rather than walking, as stored",
    "motionNames": ("idle / walk / run name the same shared motion library entries "
                    "the performance data uses; names checked against the library "
                    "index appear under summary.motionsNotInLibrary"),
    "soloAction": ("script name of this character's alone-action performance; the "
                   "scripts themselves live in their own bundle"),
}


def _derive(row):
    """Runtime-unit values next to the stored ones."""
    out = {}
    for stored, (runtime, _unit) in DERIVED.items():
        value = row.get(stored)
        out[runtime] = None if value is None else round(float(value) / MILLI, 6)
    return out


def build_registry(master, unit_ids, motion_library_index=None):
    """Merge identity, locomotion and alone-action data for *unit_ids*.

    *master* is a :class:`core.master.Master`.  *motion_library_index* is the
    shared library's index document; when given, every idle / walk / run name is
    checked against its clips and misses are reported.
    """
    identity = master.character_units()
    locomotion = master.locomotion()
    solo = master.solo_actions()
    library = set((motion_library_index or {}).get("clips") or ())

    characters, missing, not_in_library = {}, {}, {}
    for unit in sorted(unit_ids):
        ident_row = identity.get(unit)
        loco_row = locomotion.get(unit)
        gaps = [kind for kind, row in (("identity", ident_row), ("locomotion", loco_row),
                                       ("soloAction", solo.get(unit))) if row is None]
        if gaps:
            missing[str(unit)] = gaps
        entry = {
            "unitId": unit,
            "identity": ({f: ident_row.get(f) for f in IDENTITY_FIELDS}
                         if ident_row else None),
            "locomotion": None,
            "soloAction": solo.get(unit),
        }
        if loco_row:
            entry["locomotion"] = {f: loco_row.get(f) for f in LOCOMOTION_FIELDS}
            entry["locomotion"].update(_derive(loco_row))
            if library:
                absent = sorted({loco_row.get(f) for f in MOTION_FIELDS
                                 if loco_row.get(f) and loco_row.get(f) not in library})
                if absent:
                    not_in_library[str(unit)] = absent
        characters[str(unit)] = entry

    return {
        "version": 1,
        "semantics": SEMANTICS,
        "units": {"seconds": ["pauseSeconds", "changeMotionSeconds"],
                  "metresPerSecond": ["walkSpeedMetersPerSecond",
                                      "runSpeedMetersPerSecond"]},
        "characters": characters,
        "summary": {
            "requested": len(set(unit_ids)),
            "withIdentity": sum(1 for c in characters.values() if c["identity"]),
            "withLocomotion": sum(1 for c in characters.values() if c["locomotion"]),
            "withSoloAction": sum(1 for c in characters.values() if c["soloAction"]),
            "missing": missing,
            "motionsChecked": bool(library),
            "motionsNotInLibrary": not_in_library,
        },
    }
