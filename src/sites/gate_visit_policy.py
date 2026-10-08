"""Extract the gate visit tables as policy data for a server mock.

Who visits a site, and how often, is decided by the server: the client only
receives the resulting visitor list and spawns what it is given.  No client
code reads these tables to choose visitors, so they are not inputs of the
client's character model.  They are the readable part of the server's rule
(gate rosters by gate level, the visitor-count rates of each gate level, the
refresh windows and the configuration keys that name the rule's intervals),
and a mock that hands out visitor lists validates its states against them.

The document says so in its ``role`` field, so a reader cannot take it for a
client master slice.  Rows are kept verbatim and in source order, keyed by
``id``; a missing, empty or key-inconsistent table is an error, never an empty
product.  Configuration rows are selected by one stated rule (the key prefix
below) and kept verbatim, including their string-typed values.
"""
from core.jsonio import write_json
from core.master import Master


ROLE = "mock-policy"

#: Each table with the join a policy reads it through.
TABLES = {
    "mysekaiGates": "the gates; id is the mysekaiGateId every other table names",
    "mysekaiGateLevels": ("one row per (mysekaiGateId, level); names the "
                          "mysekaiGateCharacterVisitCountRateId of that level"),
    "mysekaiGateCharacterLotteries": ("gate roster: gameCharacterUnitId per "
                                      "mysekaiGateId, visitable from "
                                      "visitableMysekaiGateLevel on"),
    "mysekaiGateCharacterVisitCountRates": ("visitor-count rates in percent "
                                            "(characterCount1..5SpawnRate) per rate id"),
    "mysekaiGateRefreshSchedules": ("refresh windows (fromHour:fromMinute to "
                                    "toHour:toMinute; hours past 24 run into "
                                    "the next day)"),
}

CONFIG_TABLE = "configs"
CONFIG_KEY_PREFIX = "mysekai_gate_"


def _keyed(table, rows):
    if not rows:
        raise ValueError(f"{table}: table is empty; a policy table with no "
                         "rows cannot be told apart from one never read")
    entries, order = {}, []
    for row in rows:
        key = row.get("id")
        if key is None:
            raise ValueError(f"{table}: a row has no id")
        if str(key) in entries:
            raise ValueError(f"{table}: duplicate id {key!r}")
        entries[str(key)] = row
        order.append(key)
    return {"keyField": "id", "rowOrder": order, "entries": entries}


def _check_joins(tables):
    """Every id one table names must exist in the table it names."""
    gates = set(tables["mysekaiGates"]["rowOrder"])
    rates = set(tables["mysekaiGateCharacterVisitCountRates"]["rowOrder"])
    for table, field, targets in (
            ("mysekaiGateLevels", "mysekaiGateId", gates),
            ("mysekaiGateLevels", "mysekaiGateCharacterVisitCountRateId", rates),
            ("mysekaiGateCharacterLotteries", "mysekaiGateId", gates)):
        for row in tables[table]["entries"].values():
            if row.get(field) not in targets:
                raise ValueError(f"{table} row {row.get('id')}: {field}="
                                 f"{row.get(field)!r} names no row")


def extract_gate_visit_policy(master_source, out_path, master_cache=None):
    """Write the gate visit policy document and return its row counts.

    *master_source* is a directory of master tables or a base URL; no bundle
    is read.
    """
    master = Master(master_source, cache_dir=master_cache)
    tables = {name: _keyed(name, master.table(name)) for name in TABLES}
    _check_joins(tables)
    configs = [row for row in master.table(CONFIG_TABLE)
               if str(row.get("configKey", "")).startswith(CONFIG_KEY_PREFIX)]
    if not configs:
        raise ValueError(f"{CONFIG_TABLE}: no key starts with "
                         f"{CONFIG_KEY_PREFIX!r}")
    keys = [row["configKey"] for row in configs]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{CONFIG_TABLE}: duplicate gate configuration key")
    summary = {"rows": {name: len(doc["rowOrder"]) for name, doc in tables.items()},
               "configKeys": len(configs)}
    doc = {
        "version": 1,
        "role": ROLE,
        "semantics": {
            "role": ("policy data for a server mock that hands out visitor "
                     "lists; no client code reads these tables to choose "
                     "visitors, so they are not client character inputs"),
            "tables": TABLES,
            "configs": (f"every {CONFIG_TABLE} row whose configKey starts with "
                        f"{CONFIG_KEY_PREFIX!r}, verbatim; values stay strings "
                        "as the source stores them"),
            "rowOrder": "source row order of each table",
        },
        "tables": tables,
        "configs": {row["configKey"]: row for row in configs},
        "summary": summary,
    }
    write_json(out_path, doc)
    return summary
