"""Extract the birthday-party delivery tables the delivery site reads.

Three server-delivered master tables drive the client side of a delivery:

- ``birthdayPartyDeliveryRewards``: per party, the reward rows; the client
  divides its delivery points by the requirement of the party's last row to
  count the reward drops (a party without rows divides by 1).
- ``birthdayPartyDeliveryPointBonuses``: per party, the card ids whose owners
  earn a bonus rate on every delivery point.
- ``birthdayPartyDeliveryTotalRewards``: per party, the total-reward rows
  (requirement, resource box); the server grants them and the delivery reply
  carries the granted resources.
- ``resourceBoxDetails``, only the rows of purpose
  ``birthday_party_delivery_total_reward`` whose box a total-reward row
  names (``totalRewardBoxes``): what each granted box holds; the client
  splits a reply's resources into the honor and the rest.  A master that
  has no such table (the JP one) carries the same rows nested in
  ``resourceBoxes``, each box's ``details`` list; they are read from there,
  box by box in master order.

The rows ride through verbatim and in master-table order: the client reads
them in that order (the last reward row's requirement, the bonus sum in row
order), so reordering would change a value.  A table with no rows is an
error rather than an empty product: it cannot be told apart from a table
that was never read.
"""
from core.jsonio import write_json
from core.master import Master, MissingTable


TABLES = {
    "rewards": ("birthdayPartyDeliveryRewards",
                ("id", "birthdayPartyId", "requirement")),
    "pointBonuses": ("birthdayPartyDeliveryPointBonuses",
                     ("id", "birthdayPartyId", "cardId", "rate")),
    "totalRewards": ("birthdayPartyDeliveryTotalRewards",
                     ("id", "birthdayPartyId", "requirement", "resourceBoxId")),
}
BOX_TABLE = "resourceBoxDetails"
BOXES_TABLE = "resourceBoxes"
BOX_PURPOSE = "birthday_party_delivery_total_reward"


def _box_details(master):
    """``(source, rows)``: the resource box detail rows in master order.

    A master either carries them as their own table, or nests each box's rows
    in that box's ``details`` list in ``resourceBoxes``; a master with neither
    raises the missing table.
    """
    try:
        return BOX_TABLE, master.table(BOX_TABLE)
    except MissingTable:
        try:
            boxes = master.table(BOXES_TABLE)
        except MissingTable:
            raise MissingTable(f"master table not found: {BOX_TABLE} "
                               f"(nor {BOXES_TABLE} with nested details)") from None
    rows = []
    for box in boxes:
        nested = box.get("details")
        if nested is None:
            continue
        if not isinstance(nested, list):
            raise ValueError(f"{BOXES_TABLE} box {box.get('id')}: details is not a list")
        for row in nested:
            if row.get("resourceBoxId") != box.get("id") or                     row.get("resourceBoxPurpose") != box.get("resourceBoxPurpose"):
                raise ValueError(f"{BOXES_TABLE} box {box.get('id')}: a nested detail "
                                 f"names another box")
            rows.append(row)
    return f"{BOXES_TABLE}[].details", rows


def extract_birthday_party_delivery(master_source, out_path, master_cache=None):
    """Write birthday-party-delivery.json: the three delivery tables.

    *master_source* is a directory of master tables or a base URL to fetch
    them from; no bundle is read.
    """
    master = Master(master_source, cache_dir=master_cache)
    doc = {
        "version": 1,
        "semantics": {
            "rewards": (
                "birthdayPartyDeliveryRewards verbatim in master order; the "
                "client's reward loop requirement is the last row of a party"),
            "pointBonuses": (
                "birthdayPartyDeliveryPointBonuses verbatim in master order; "
                "an owned card adds rate / 100 to the member bonus"),
            "totalRewards": (
                "birthdayPartyDeliveryTotalRewards verbatim in master order; "
                "granted by the server, carried back in the delivery reply"),
        },
    }
    summary = {}
    for key, (table, required) in TABLES.items():
        rows = master.table(table)
        if not rows:
            raise ValueError(f"{table}: table is empty; a delivery table with "
                             f"no rows cannot be told apart from a table that "
                             f"was never read")
        for row in rows:
            for field in required:
                if not isinstance(row.get(field), int):
                    raise ValueError(f"{table} row {row.get('id')}: incomplete "
                                     f"row: {field}={row.get(field)!r}")
        doc[key] = rows
        summary[key] = len(rows)
    summary["parties"] = len({row["birthdayPartyId"] for key in TABLES
                              for row in doc[key]})
    boxes = sorted({row["resourceBoxId"] for row in doc["totalRewards"]})
    wanted = set(boxes)
    source_table, rows = _box_details(master)
    details = [row for row in rows
               if row.get("resourceBoxPurpose") == BOX_PURPOSE
               and row.get("resourceBoxId") in wanted]
    for row in details:
        for field in ("resourceBoxId", "resourceId", "resourceQuantity"):
            if not isinstance(row.get(field), int):
                raise ValueError(f"{BOX_TABLE} box {row.get('resourceBoxId')}: "
                                 f"incomplete row: {field}={row.get(field)!r}")
        if not isinstance(row.get("resourceType"), str):
            raise ValueError(f"{BOX_TABLE} box {row.get('resourceBoxId')}: "
                             f"incomplete row: resourceType")
    missing = sorted(wanted - {row["resourceBoxId"] for row in details})
    if missing:
        raise ValueError(f"{BOX_TABLE}: total reward boxes without a "
                         f"{BOX_PURPOSE} row: {missing}")
    doc["semantics"]["totalRewardBoxes"] = (
        f"{source_table} rows of purpose {BOX_PURPOSE} whose box a total reward "
        f"row names, verbatim in master order")
    doc["totalRewardBoxes"] = details
    summary["totalRewardBoxes"] = len(details)
    summary["boxes"] = len(boxes)
    doc["summary"] = summary
    write_json(out_path, doc)
    return summary
