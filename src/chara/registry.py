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
PLAYER_CONFIG_IDS = (77, 78, 95)
_CONFIG_UNSET = object()

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
        out[runtime] = None if value is None else float(value) / MILLI
    return out


def build_registry(master, unit_ids, motion_library_index=None, client_configs=_CONFIG_UNSET):
    """Merge character rows and optional player movement configuration.

    *master* supplies identity, locomotion, and alone-action rows.  If
    *client_configs* is omitted, it is read from the master; callers that need
    local fault isolation may pass ``None`` when that table is unavailable.
    """
    identity = master.character_units()
    locomotion = master.locomotion()
    solo = master.solo_actions()
    if client_configs is _CONFIG_UNSET:
        from core.master import MissingTable
        try:
            client_configs = master.client_configs()
        except MissingTable:
            client_configs = None
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

    player = None
    player_gap = None
    if client_configs is None:
        player_gap = ["clientConfigs", *PLAYER_CONFIG_IDS]
    else:
        absent = [config_id for config_id in PLAYER_CONFIG_IDS
                  if config_id not in client_configs]
        if absent:
            player_gap = absent
        else:
            normal = client_configs[77]
            harvest = client_configs[78]
            dash_rate = client_configs[95]
            player = {
                "normalMoveScale": normal,
                "harvestMoveScale": harvest,
                "dashSpeedRate": dash_rate,
                "configRows": {str(config_id): client_configs[config_id]
                               for config_id in PLAYER_CONFIG_IDS},
                "derived": {
                    "walkSpeedMetersPerSecond": normal,
                    "dashSpeedMetersPerSecond": normal * dash_rate,
                },
            }
    if player_gap is not None:
        missing["playerConfig"] = player_gap

    return {
        "version": 1,
        "semantics": SEMANTICS,
        "units": {"seconds": ["pauseSeconds", "changeMotionSeconds"],
                  "metresPerSecond": ["walkSpeedMetersPerSecond",
                                      "runSpeedMetersPerSecond",
                                      "dashSpeedMetersPerSecond"]},
        "player": player,
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
