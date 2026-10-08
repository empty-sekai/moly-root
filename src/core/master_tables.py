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
    "mysekaiSiteFootsteps": ("id", "mysekai-site-footsteps.json"),
    "mysekaiFixtureFootsteps": ("id", "mysekai-fixture-footsteps.json"),
    "mysekaiSiteHarvestUnavailableSpots": ("id", "mysekai-site-harvest-unavailable-spots.json"),
    # The common furniture reaction of a character: one row per (unit, polarity)
    # naming a furniture group and a tweet group; the two group tables hold the
    # members in source row order, which ordered pickers read.
    "mysekaiCharacterTalkFixtureCommons": ("id", "mysekai-character-talk-fixture-commons.json"),
    "mysekaiCharacterTalkFixtureCommonMysekaiFixtureGroups": (
        "id", "mysekai-character-talk-fixture-common-fixture-groups.json"),
    "mysekaiCharacterTalkFixtureCommonTweetGroups": (
        "id", "mysekai-character-talk-fixture-common-tweet-groups.json"),
    # Talk content terms: the start time a talk row's term id points at.
    "mysekaiCharacterTalkTerms": ("id", "mysekai-character-talk-terms.json"),
    # The action-point row a talk pre-action's together communication names:
    # the NPC fixture talk that waits with a communication reads its members'
    # action points from it.
    "mysekaiCharacterTalkFixtureTogetherCommunications": (
        "id", "mysekai-character-talk-fixture-together-communications.json"),
    "mysekaiRanks": ("id", "mysekai-ranks.json"),
    # The info rank page's put-cost limits: a fixture put-limit release names
    # a level row by its externalId, and the highest level row per site
    # category among the releases at or below the rank is the limit.
    "mysekaiRankReleases": ("id", "mysekai-rank-releases.json"),
    "mysekaiFixturePutLimitLevels": ("id", "mysekai-fixture-put-limit-levels.json"),
    # The cue a decided furniture placement plays: a fixture row's
    # ``mysekaiFixturePutSoundId`` names a row here, whose ``assetbundleName``
    # is the cue name.
    "mysekaiFixturePutSounds": ("id", "mysekai-fixture-put-sounds.json"),
    # The talk rows a multiple-character fixture talk is played from as a
    # some-character timeline, each naming the talk and its main character
    # unit; the forced read-talk of an NPC's random fixture action chooses
    # its talk-data builder by whether a row names the talk and that unit.
    "mysekaiCharacterTalkSomeCharacterTalks": (
        "id", "mysekai-character-talk-some-character-talks.json"),
    # The zones of a site layout where the layout editor refuses a floor
    # fixture: each row names its site layout (``mysekaiSiteLayoutId``) and
    # a box from ``startX/startY/startZ`` over ``width/height/depth`` tiles.
    "mysekaiSiteHousingLayoutUnavailableZones": (
        "id", "mysekai-site-housing-layout-unavailable-zones.json"),
    # The game's general materials (not the MySekai ones): each row's name,
    # flavour text and material type; the birthday-party delivery cells show
    # the name of the material they list.
    "materials": ("id", "materials.json"),
    # The honors a reward dialog shows: each row's name, rarity, group and
    # image bundle (``assetbundleName``) with its levels; the honor image a
    # reward dialog builds also reads the row's group (background bundle and
    # honor type).
    "honors": ("id", "honors.json"),
    "honorGroups": ("id", "honor-groups.json"),
    # The master configs (``MasterDataManager.GetMasterConfigToInt`` and its
    # siblings): one string value per ``configKey``; the birthday-party
    # delivery reads its base point and reward drop upper limit here.
    "configs": ("configKey", "configs.json"),
    # The avatar wear masters ``UserAvatar``'s four ids name: the costume and
    # accessory bundles, the skin colour codes, and the coordinates (a full
    # set naming its own costume, accessory and skin colour).
    "avatarCostumes": ("id", "avatar-costumes.json"),
    "avatarAccessories": ("id", "avatar-accessories.json"),
    "avatarSkinColors": ("id", "avatar-skin-colors.json"),
    "avatarCoordinates": ("id", "avatar-coordinates.json"),
}

# Tables the game's suite master ships on their own, but which the master dump
# keeps only as a copy embedded in every row of another table. The export
# projects each row of that table onto the listed fields, verbatim, keyed by
# the row's own key; a consumer rebuilds the standalone table from the
# embedded copies. Two rows embedding the same group id with different
# contents are an error, since the standalone table could not hold both.
PROJECTED_TABLES = {
    # A furniture piece's tag group: the group id and up to five tag ids.
    # The fixture-tag talk condition compares the tag ids of the condition's
    # furniture group with those of every placed piece.
    "mysekaiFixtureTagGroups": (
        "mysekaiFixtures", ("id", "mysekaiFixtureTagGroup"),
        "mysekai-fixture-tag-groups.json"),
}

REGISTERED_TABLES = {
    **TABLES, **OPTIONAL_TABLES,
    **{name: ("id", filename) for name, (_, _, filename) in PROJECTED_TABLES.items()},
}

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


def _projected_rows(table, master):
    source, fields, _ = PROJECTED_TABLES[table]
    rows = []
    groups = {}
    for row in master.table(source):
        missing = [field for field in fields if field not in row]
        if missing:
            raise ValueError(f"{table}: {source} row {row.get('id')} lacks "
                             f"{', '.join(missing)}")
        projected = {field: row[field] for field in fields}
        embedded = projected[fields[-1]]
        if isinstance(embedded, dict) and "id" in embedded:
            kept = groups.setdefault(embedded["id"], embedded)
            if kept != embedded:
                raise ValueError(f"{table}: group {embedded['id']} is embedded "
                                 f"with different contents")
        rows.append(projected)
    return rows


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
        if table in PROJECTED_TABLES:
            rows = _projected_rows(table, master)
        else:
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
        if table in PROJECTED_TABLES:
            source, fields, _ = PROJECTED_TABLES[table]
            doc["semantics"]["projection"] = {
                "source": source,
                "fields": list(fields),
                "meaning": (
                    "each source row reduced to these fields, verbatim; the "
                    "last field is the embedded copy of the standalone table's "
                    "row, and every copy of one group id is identical"
                ),
            }
        write_json(f"{out_dir}/{filename}", doc)
        summary[table] = {"rows": len(rows), "file": filename,
                          "externalUrlsMasked": masked}
    return summary
