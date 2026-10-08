"""Synthetic contracts for the birthday-party delivery tables."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sites.birthday_party_delivery import extract_birthday_party_delivery


BOXES = [
    {"resourceBoxId": 250601, "resourceBoxPurpose": "birthday_party_delivery_total_reward",
     "resourceType": "honor", "resourceId": 6830, "resourceQuantity": 1, "resourceLevel": 1},
    {"resourceBoxId": 100, "resourceBoxPurpose": "birthday_party_delivery_total_reward",
     "resourceType": "material", "resourceId": 15, "resourceQuantity": 50, "resourceLevel": None},
    {"resourceBoxId": 100, "resourceBoxPurpose": "billing_shop_item",
     "resourceType": "jewel", "resourceId": 1, "resourceQuantity": 9, "resourceLevel": None},
    {"resourceBoxId": 7, "resourceBoxPurpose": "birthday_party_delivery_total_reward",
     "resourceType": "coin", "resourceId": 1, "resourceQuantity": 9, "resourceLevel": None},
]


def _write(directory, rewards, bonuses, totals, boxes=BOXES):
    for name, rows in (("birthdayPartyDeliveryRewards", rewards),
                       ("birthdayPartyDeliveryPointBonuses", bonuses),
                       ("birthdayPartyDeliveryTotalRewards", totals),
                       ("resourceBoxDetails", boxes)):
        (directory / f"{name}.json").write_text(
            json.dumps(rows), encoding="utf-8", newline="\n")


REWARDS = [{"birthdayPartyId": 1, "id": 1, "requirement": 10000, "seq": 1},
           {"birthdayPartyId": 2, "id": 2, "requirement": 10000, "seq": 1}]
BONUSES = [{"birthdayPartyId": 1, "cardId": 1260, "id": 1, "rate": 50},
           {"birthdayPartyId": 1, "cardId": 1010, "id": 2, "rate": 15}]
TOTALS = [{"birthdayPartyId": 1, "id": 1, "requirement": 2, "resourceBoxId": 250601},
          {"birthdayPartyId": 1, "id": 2, "requirement": 3, "resourceBoxId": 100}]


def test_rows_ride_through_verbatim_in_master_order(tmp_path):
    _write(tmp_path, REWARDS, BONUSES, TOTALS)
    out = tmp_path / "birthday-party-delivery.json"
    summary = extract_birthday_party_delivery(str(tmp_path), out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert doc["rewards"] == REWARDS
    assert doc["pointBonuses"] == BONUSES
    assert [row["id"] for row in doc["totalRewards"]] == [1, 2]
    assert summary == {"rewards": 2, "pointBonuses": 2, "totalRewards": 2,
                       "parties": 2, "totalRewardBoxes": 2, "boxes": 2}
    # Only the purpose rows of the named boxes: box 100's shop row and box 7
    # (named by no total reward row) stay out.
    assert doc["totalRewardBoxes"] == [BOXES[0], BOXES[1]]


def test_a_named_box_without_rows_is_an_error(tmp_path):
    _write(tmp_path, REWARDS, BONUSES, TOTALS, boxes=[BOXES[0]])
    with pytest.raises(ValueError, match=r"\[100\]"):
        extract_birthday_party_delivery(str(tmp_path), tmp_path / "out.json")


@pytest.mark.parametrize("which", [0, 1, 2])
def test_an_empty_table_is_an_error_not_an_empty_product(tmp_path, which):
    tables = [REWARDS, BONUSES, TOTALS]
    tables[which] = []
    _write(tmp_path, *tables)
    with pytest.raises(ValueError, match="table is empty"):
        extract_birthday_party_delivery(str(tmp_path), tmp_path / "out.json")


def test_a_missing_table_is_an_error(tmp_path):
    _write(tmp_path, REWARDS, BONUSES, TOTALS)
    (tmp_path / "birthdayPartyDeliveryTotalRewards.json").unlink()
    with pytest.raises(Exception):
        extract_birthday_party_delivery(str(tmp_path), tmp_path / "out.json")


@pytest.mark.parametrize("field", ["birthdayPartyId", "requirement"])
def test_a_reward_row_missing_what_the_client_reads_is_an_error(tmp_path, field):
    row = dict(REWARDS[0])
    row.pop(field)
    _write(tmp_path, [row], BONUSES, TOTALS)
    with pytest.raises(ValueError, match=field):
        extract_birthday_party_delivery(str(tmp_path), tmp_path / "out.json")
