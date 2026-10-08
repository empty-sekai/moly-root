import json

import pytest

from fixtures.reactions import PACKAGE, SCHEMA, parse_fixture_reactions


def _tree():
    return {
        "m_Name": "fixture_reaction_data",
        "FixturerRactions": [
            {
                "FixtureId": 534,
                "ReactionCharacter": [
                    {"CharacterUnitIds": [1, 4]},
                    {"CharacterUnitIds": [27, 42]},
                    {"CharacterUnitIds": [7, 8]},
                    {"CharacterUnitIds": [28, 33]},
                    {"CharacterUnitIds": [11, 12]},
                    {"CharacterUnitIds": [29, 49]},
                    {"CharacterUnitIds": [13, 55]},
                    {"CharacterUnitIds": [14, 15]},
                    {"CharacterUnitIds": [18, 20]},
                ],
            },
            {
                "FixtureId": 90001,
                "ReactionCharacter": [
                    {"CharacterUnitIds": [1]},
                    {"CharacterUnitIds": [1, 2, 3, 4]},
                ],
            },
        ],
    }


def test_source_order_and_pingpong_groups_are_preserved():
    document = parse_fixture_reactions(_tree())
    assert document["schema"] == SCHEMA
    assert document["source"]["package"] == PACKAGE
    assert document["fixtures"][0] == {
        "fixtureId": 534,
        "groups": [
            [1, 4], [27, 42], [7, 8], [28, 33], [11, 12],
            [29, 49], [13, 55], [14, 15], [18, 20],
        ],
    }
    assert document["fixtures"][1]["groups"] == [[1], [1, 2, 3, 4]]
    assert document["summary"] == {
        "fixtures": 2,
        "groups": 11,
        "characterUnits": 20,
        "maxParticipants": 4,
    }


def test_duplicate_source_rows_fail_instead_of_merging():
    tree = _tree()
    tree["FixturerRactions"].append(tree["FixturerRactions"][0].copy())
    with pytest.raises(ValueError, match="duplicate fixture reaction row"):
        parse_fixture_reactions(tree)


def test_invalid_or_duplicate_character_units_fail_closed():
    tree = _tree()
    tree["FixturerRactions"][0]["ReactionCharacter"][0]["CharacterUnitIds"] = [14, 14]
    with pytest.raises(ValueError, match="duplicate character unit"):
        parse_fixture_reactions(tree)

    tree = _tree()
    tree["FixturerRactions"][0]["ReactionCharacter"][0]["CharacterUnitIds"] = []
    with pytest.raises(ValueError, match="must be non-empty"):
        parse_fixture_reactions(tree)


def test_fixture_reactions_are_runtime_root_documents(tmp_path):
    from pack.groups import runtime_paths

    (tmp_path / "fixture-reactions.json").write_text("{}\n", encoding="utf-8")
    paths = runtime_paths(tmp_path)
    assert "fixture-reactions.json" in paths

def test_fixture_reaction_route_is_exact_and_does_not_claim_system_neighbours():
    from core.assets.router import FIXTURE_REACTION_PACKAGE, route

    routed = route(FIXTURE_REACTION_PACKAGE)
    assert routed is not None
    assert routed.domain == "fixture-reaction"
    assert routed.extractor == "fixture-reactions"
    for neighbour in (
        "mysekai__system__fixture_reaction_data_extra",
        "mysekai__system__fixture_data",
        "mysekai__system__anything_else",
    ):
        assert route(neighbour) is None
