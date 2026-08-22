"""Character registry: merged identity and locomotion, with gaps kept visible.

Two properties are load-bearing.  A character whose source row is absent must
stay absent — filling a default would hand a consumer an invented walk speed.
And the stored locomotion numbers are a thousand times the runtime unit, so both
forms must be present: a consumer that used the stored one would move characters
1000x too fast.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from chara.registry import DERIVED, build_registry


class _Master:
    """Stand-in for core.master.Master with the tables in play."""

    def __init__(self, identity, locomotion, solo, client_configs=None):
        self._identity, self._locomotion = identity, locomotion
        self._solo, self._client_configs = solo, client_configs

    def character_units(self):
        return self._identity

    def locomotion(self):
        return self._locomotion

    def solo_actions(self):
        return self._solo

    def client_configs(self):
        if self._client_configs is None:
            from core.master import MissingTable
            raise MissingTable("master table not found: clientConfigs")
        return self._client_configs


LOCO_ROW = {"idleMotion": "mov_idle", "walkMotion": "mov_walk", "runMotion": "mov_run",
            "walkSpeed": 400.0, "runSpeed": 1000.0, "runOccurRate": 50.0,
            "pauseMilliSeconds": 15000, "changeMotionMilliSeconds": 500}


def _master(**over):
    identity = {12: {"gameCharacterId": 12, "unit": "light_sound", "colorCode": "#33aaee",
                     "skinColorCode": "#fff5e8", "skinShadowColorCode1": "#f4b6cd",
                     "skinShadowColorCode2": "#e982a5"}}
    return _Master(over.get("identity", identity),
                   over.get("locomotion", {12: dict(LOCO_ROW)}),
                   over.get("solo", {12: "character_alone_action_12"}),
                   over.get("client_configs", {77: 2.5, 78: 2.5, 95: 1.75}))


def test_player_exports_declared_configs_and_rows():
    doc = build_registry(_master(), [12])
    assert doc["player"]["normalMoveScale"] == 2.5
    assert doc["player"]["harvestMoveScale"] == 2.5
    assert doc["player"]["dashSpeedRate"] == 1.75
    assert doc["player"]["configRows"] == {"77": 2.5, "78": 2.5, "95": 1.75}


def test_player_derived_dash_speed_multiplies_normal_speed():
    doc = build_registry(_master(client_configs={77: 3.0, 78: 2.0, 95: 1.75}), [12])
    assert doc["player"]["derived"] == {
        "walkSpeedMetersPerSecond": 3.0,
        "dashSpeedMetersPerSecond": 5.25,
    }


def test_missing_client_configs_keeps_registry_and_reports_player_gap():
    doc = build_registry(_master(client_configs=None), [12])
    assert doc["player"] is None
    assert doc["characters"]["12"]["identity"] is not None
    assert doc["summary"]["missing"]["playerConfig"] == ["clientConfigs", 77, 78, 95]


def test_missing_player_config_id_keeps_player_null_and_reports_id():
    doc = build_registry(_master(client_configs={77: 2.5, 78: 2.5}), [12])
    assert doc["player"] is None
    assert doc["summary"]["missing"]["playerConfig"] == [95]


def test_stored_values_keep_their_runtime_counterparts():
    doc = build_registry(_master(), [12])
    loco = doc["characters"]["12"]["locomotion"]
    assert loco["walkSpeed"] == 400.0 and loco["walkSpeedMetersPerSecond"] == 0.4
    assert loco["runSpeed"] == 1000.0 and loco["runSpeedMetersPerSecond"] == 1.0
    assert loco["pauseMilliSeconds"] == 15000 and loco["pauseSeconds"] == 15.0
    assert loco["changeMotionMilliSeconds"] == 500 and loco["changeMotionSeconds"] == 0.5
    assert doc["units"]["metresPerSecond"] == ["walkSpeedMetersPerSecond",
                                              "runSpeedMetersPerSecond",
                                              "dashSpeedMetersPerSecond"]


def test_every_stored_field_has_a_declared_runtime_field():
    # 少一条映射就意味着某个值会以存储单位交给消费者。
    assert set(DERIVED) == {"pauseMilliSeconds", "changeMotionMilliSeconds",
                            "walkSpeed", "runSpeed"}


def test_a_missing_source_row_is_reported_not_defaulted():
    doc = build_registry(_master(solo={}), [12])
    assert doc["characters"]["12"]["soloAction"] is None
    assert doc["summary"]["missing"] == {"12": ["soloAction"]}
    assert doc["summary"]["withSoloAction"] == 0
    assert doc["summary"]["withLocomotion"] == 1


def test_a_character_absent_from_every_table_still_appears():
    doc = build_registry(_master(), [12, 21])
    assert doc["characters"]["21"] == {"unitId": 21, "identity": None,
                                       "locomotion": None, "soloAction": None}
    assert doc["summary"]["missing"]["21"] == ["identity", "locomotion", "soloAction"]
    assert doc["summary"]["requested"] == 2


def test_motion_names_are_checked_against_the_library_when_given():
    index = {"clips": {"mov_idle": {}, "mov_walk": {}}}        # run 缺
    doc = build_registry(_master(), [12], motion_library_index=index)
    assert doc["summary"]["motionsChecked"] is True
    assert doc["summary"]["motionsNotInLibrary"] == {"12": ["mov_run"]}


def test_without_an_index_no_motion_claim_is_made():
    doc = build_registry(_master(), [12])
    assert doc["summary"]["motionsChecked"] is False
    assert doc["summary"]["motionsNotInLibrary"] == {}


def test_membership_comes_from_the_caller():
    # 成员集合不由本模块猜:只给一个角色就只出一条。
    doc = build_registry(_master(), [12])
    assert list(doc["characters"]) == ["12"]
