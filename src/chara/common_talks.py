"""Ordinary conversations between multiple characters, without a fixture.

Membership follows the source's IsCommonSomeCharacterTalk predicate: there
are two to five nonzero group members, no fixture-id condition, and a row in
the some-character association table. Association rows are preserved as
metadata; the runtime's initiating character leads the resulting plan.
Scripts share the ordinary talk parser and voice-bank rule.
"""
from collections import Counter, defaultdict


def select_common_talks(master):
    groups = {}
    for row in master.table("mysekaiGameCharacterUnitGroups"):
        key = row["id"]
        if key in groups:
            raise ValueError(f"duplicate character group {key}")
        groups[key] = [row[f"gameCharacterUnitId{i}"] for i in range(1, 6)
                       if row.get(f"gameCharacterUnitId{i}")]
    conditions = master.condition_types()
    candidates = []
    for talk in master.table("mysekaiCharacterTalks"):
        members = groups.get(talk["mysekaiGameCharacterUnitGroupId"])
        if members is None:
            raise ValueError(f"talk {talk['id']}: missing character group")
        if not 2 <= len(members) <= 5:
            continue
        kinds = conditions.get(talk["mysekaiCharacterTalkConditionGroupId"])
        if kinds is None or any(not isinstance(kind, str) for kind in kinds):
            raise ValueError(f"talk {talk['id']}: unresolved conditions")
        # IsFixtureActionTalk tests this exact condition, not a string prefix
        # and not the wider furniture predicate used by the solo selector.
        if "mysekai_fixture_id" not in kinds:
            candidates.append((talk, members))
    if not candidates:
        return [], {"candidates": 0, "selected": 0, "withoutAssociation": []}
    associations = defaultdict(list)
    for row in master.table("mysekaiCharacterTalkSomeCharacterTalks"):
        associations[row["mysekaiCharacterTalkId"]].append(row)
    selected, absent = [], []
    for talk, members in candidates:
        links = associations.get(talk["id"])
        if not links:
            absent.append(talk["id"])
            continue
        selected.append({"talk": talk, "unitIds": members, "associations": links})
    return selected, {"candidates": len(candidates), "selected": len(selected),
                      "withoutAssociation": absent}


def common_talks(master, assets, tables, scalars):
    """Build the commonSomeCharacter block of the ordinary talk document."""
    from chara.talks import (_asset_named, _tweet, constant_census, parse_script,
                             TALK_VOICE_BANK)
    from perf.fixture_talks import classify_ops

    selected, selection = select_common_talks(master)
    rows, scripts, operations = [], [], Counter()
    conditions = master.condition_types()
    payloads = master.condition_entries()
    tweets = master.tweets()
    pre_actions = {}
    for row in master.table("mysekaiCharacterTalkPreActions"):
        pre_actions.setdefault(row["mysekaiCharacterTalkId"], row)
    for item in selected:
        source = item["talk"]
        lua = source["lua"]
        name = lua if lua.endswith(".lua") else lua + ".lua"
        script = _asset_named(assets, name)
        if script is None:
            raise LookupError(f"talk {source['id']}: script asset not found: {name}")
        _, unknown = classify_ops(script, name)
        if unknown:
            raise ValueError(f"talk {source['id']}: unknown script operations: {dict(unknown)}")
        parsed = parse_script(script, tables, scalars, script_name=name)
        pre_action = pre_actions.get(source["id"])
        tweet_id = (pre_action or {}).get("mysekaiCharacterTalkTweetId")
        if tweet_id and tweet_id not in tweets:
            raise LookupError(f"talk {source['id']}: tweet not found: {tweet_id}")
        group = source["mysekaiCharacterTalkConditionGroupId"]
        rows.append({
            "talkId": source["id"], "lua": lua,
            "unitGroupId": source["mysekaiGameCharacterUnitGroupId"],
            "unitIds": item["unitIds"],
            "someCharacterTalks": item["associations"],
            "siteGroupId": source["mysekaiSiteGroupId"],
            "termId": source["mysekaiCharacterTalkTermId"],
            "conditionGroupId": group, "conditions": conditions[group],
            "conditionValues": payloads[group],
            "preAction": pre_action, "tweet": _tweet(tweets.get(tweet_id)),
            "voiceBank": TALK_VOICE_BANK.format(lua.removesuffix(".lua")),
            "voices": parsed["voices"], "steps": parsed["steps"],
        })
        scripts.append(script)
        operations.update(parsed["operations"])
    constants = constant_census(scripts, tables)
    summary = {
        "talks": len(rows), "steps": sum(len(row["steps"]) for row in rows),
        "voiceCues": sum(len(row["voices"]) for row in rows),
        "uniqueVoiceCues": len({cue for row in rows for cue in row["voices"]}),
        "operations": dict(operations), "selection": selection,
        "constants": constants,
    }
    return {
        "version": 1,
        "semantics": {
            "membership": "Non-fixture talks with two to five group members and a some-character association.",
            "mainCharacter": "Chosen by the runtime: the initiating character leads the plan; association metadata does not fix its leader.",
            "unitIds": "Nonzero character group slots, in source slot order.",
            "preAction": "The source pre-action row, or null; its tweet is resolved separately.",
            "fixture": "This family has no fixture anchor; it requires common multi-character runtime support.",
        },
        "talks": rows, "summary": summary,
    }, summary
