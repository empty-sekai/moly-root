"""Extract registered mysekai master tables whose rows are looked up by key.

``mysekaiBlueprints``, ``mysekaiItems`` and ``mysekaiMusicRecords`` are each
keyed by an integer ``id``; ``wordings`` is keyed by the string
``wordingKey``.  These four remain the default; additional gameplay tables
are exported only when explicitly selected.  A consumer asks the table for
one entry by that key, so the product keeps every row under its key rather
than as an array a reader would have to search.  No rows are filtered from a
selected table: a key column that is absent or duplicated is an error rather
than a silent overwrite, and a table with no rows is an error rather than an
empty product — both states a consumer must be told, not left to infer.

The one exception to "verbatim": string values that are external http(s)
links are replaced by ``<external-url>`` (see :func:`_mask_external_urls`).
"""
from core.jsonio import write_json
from core.master import Master


TABLES = {
    "mysekaiBlueprints": ("id", "mysekai-blueprints.json"),
    "mysekaiItems": ("id", "mysekai-items.json"),
    "mysekaiMusicRecords": ("id", "mysekai-music-records.json"),
    "wordings": ("wordingKey", "wordings.json"),
}

OPTIONAL_TABLES = {
    "mysekaiCharacterTalkNoTalkMysekaiFixtureActions": ("id", "mysekai-character-talk-no-talk-fixture-actions.json"),
    "mysekaiCharacterTalks": ("id", "mysekai-character-talks.json"),
    "mysekaiFixturePlayerTimelines": ("id", "mysekai-fixture-player-timelines.json"),
    "mysekaiCharacterTalkFixtureTimelines": ("id", "mysekai-character-talk-fixture-timelines.json"),
    "mysekaiCharacterTalkActionPoints": ("id", "mysekai-character-talk-action-points.json"),
    "mysekaiCharacterTalkConditions": ("id", "mysekai-character-talk-conditions.json"),
    "mysekaiCharacterTalkConditionGroups": ("id", "mysekai-character-talk-condition-groups.json"),
    "mysekaiGameCharacterUnitGroups": ("id", "mysekai-game-character-unit-groups.json"),
    "mysekaiTools": ("id", "mysekai-tools.json"),
    "mysekaiStaminas": ("id", "mysekai-staminas.json"),
    "mysekaiStaminaRecovery": ("id", "mysekai-stamina-recovery.json"),
    "mysekaiMaterials": ("id", "mysekai-materials.json"),
    "mysekaiFixturePossessions": ("id", "mysekai-fixture-possessions.json"),
    "mysekaiMaterialPossessions": ("id", "mysekai-material-possessions.json"),
    "mysekaiSystemFixtures": ("id", "mysekai-system-fixtures.json"),
    "mysekaiBlueprintMysekaiMaterialCosts": ("id", "mysekai-blueprint-material-costs.json"),
    "mysekaiBlueprintTerms": ("id", "mysekai-blueprint-terms.json"),
}

REGISTERED_TABLES = {**TABLES, **OPTIONAL_TABLES}

_MASKED_URL = "<external-url>"


def _mask_external_urls(table, rows):
    """Replace external http(s) link values with a placeholder, in place.

    A handful of wording rows carry links to the live game's operational
    web pages (policy notices, galleries).  The product never opens them —
    there is no networking — and the published tree must not name vendor
    endpoints, so the value is masked and the count reported in the summary;
    the key and every other field stay verbatim.
    """
    masked = 0
    for row in rows:
        for field, value in row.items():
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                row[field] = _MASKED_URL
                masked += 1
    return masked


def _keyed_rows(table, key_field, rows):
    keyed = {}
    for row in rows:
        key = row.get(key_field)
        if key is None:
            raise ValueError(f"{table} row {row.get('id')}: key field "
                             f"{key_field} is absent or null")
        stored = str(key)
        if stored in keyed:
            raise ValueError(f"{table}: duplicate key {key_field}={key!r}")
        keyed[stored] = row
    return keyed


def extract_master_tables(master_source, out_dir, master_cache=None, *, tables=None):
    """Write one keyed document per selected table into *out_dir*.

    *master_source* is a directory of master tables or a base URL to fetch
    them from; no bundle is read — these tables live entirely in master.
    With *tables* omitted, only the original four :data:`TABLES` are written.
    Otherwise, names must be in :data:`REGISTERED_TABLES`; repeated names are
    written once, in first-selection order.  Validate all names before reading
    or writing a table, so an unknown selection cannot leave partial output.
    """
    selected = tuple(TABLES) if tables is None else tuple(dict.fromkeys(tables))
    for table in selected:
        if table not in REGISTERED_TABLES:
            raise ValueError(f"unregistered master table: {table}")
    master = Master(master_source, cache_dir=master_cache)
    summary = {}
    for table in selected:
        key_field, filename = REGISTERED_TABLES[table]
        rows = master.table(table)
        if not rows:
            raise ValueError(f"{table}: table is empty; a table with no rows "
                             f"cannot be told apart from a table that was "
                             f"never read")
        masked = _mask_external_urls(table, rows)
        keyed = _keyed_rows(table, key_field, rows)
        doc = {
            "version": 1,
            "rowOrder": [row[key_field] for row in rows],
            "semantics": {
                "table": table,
                "keyField": key_field,
                "rowOrder": "source row order, retained explicitly for ordered selection consumers",
                "entries": (
                    "every row of the table, keyed by the field a consumer "
                    "looks it up by; the source carries no ordering the "
                    "consumer reads, so the map implies none"
                ) if table in TABLES else (
                    "every row of the table, keyed by the field a consumer "
                    "looks it up by; record fields, including any sequence "
                    "values, are retained verbatim; map order implies none"
                ),
                "externalUrls": (
                    "string values that are external http(s) links are "
                    "replaced by the literal <external-url>; the product has "
                    "no networking and the published tree does not name "
                    "vendor endpoints"
                ) if masked else None,
            },
            "entries": keyed,
            "summary": {"rows": len(rows), "externalUrlsMasked": masked},
        }
        write_json(f"{out_dir}/{filename}", doc)
        summary[table] = {"rows": len(rows), "file": filename,
                          "externalUrlsMasked": masked}
    return summary
