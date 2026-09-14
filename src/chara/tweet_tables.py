"""Extract the tweet selection tables: which tweet a situation can pick.

Four master tables chain into one product, joined against the
without-related-talk rows that name the character:

* ``mysekaiCharacterTalkTweetWithoutRelatedTalks`` — the join spine: each row
  assigns one tweet to one character.  Greetings and site entries never name a
  character or a tweet directly; they point here.
* ``mysekaiCharacterTalkTweetGreetings`` — the greeting candidate rows, each
  gated by one condition row.
* ``mysekaiCharacterTalkTweetGreetingConditions`` — the gate: a condition type
  string plus up to two integer values (visit-count interval, phenomena time
  period).
* ``mysekaiCharacterTalkTweetHousingRoomSiteEntries`` — the rows a site entry
  draws from: each names one without-related-talk row.
* ``mysekaiCharacterTalkPreActions`` — the deterministic link a talk consumes:
  one talk id names the tweet shown as its opening pre-action.

Rows are written as arrays in master-table order, not keyed by id: the
source's draws pick candidates *in table order*, so order is part of what the
data says and a keyed dict would invite a consumer to reorder.  Every row key
is the camelCase of the consuming row structure's field name, and every
reference this module writes resolves — a dangling one is an error, not a gap
to fill with a default.
"""
from collections import Counter

from core.jsonio import write_json
from core.master import Master


WRT_TABLE = "mysekaiCharacterTalkTweetWithoutRelatedTalks"
GREETING_TABLE = "mysekaiCharacterTalkTweetGreetings"
GREETING_CONDITION_TABLE = "mysekaiCharacterTalkTweetGreetingConditions"
SITE_ENTRY_TABLE = "mysekaiCharacterTalkTweetHousingRoomSiteEntries"
PRE_ACTION_TABLE = "mysekaiCharacterTalkPreActions"
# Loaded only to check every reference resolves; no tweet body is written here.
TWEETS_TABLE = "mysekaiCharacterTalkTweets"


def _rows(master, name):
    rows = master.table(name)
    if not rows:
        raise ValueError(f"{name}: table is empty; a chain this product joins "
                         "cannot be checked against zero rows")
    return rows


def _unique_ids(name, rows):
    ids = set()
    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            raise ValueError(f"{name}: row without an id")
        if row_id in ids:
            raise ValueError(f"{name}: duplicate id {row_id}")
        ids.add(row_id)
    return ids


def _ref(row, column, target_ids, source_name, target_name):
    value = row.get(column)
    if value not in target_ids:
        raise LookupError(
            f"{source_name} row {row.get('id')}: {column} {value} is not in "
            f"{target_name}")
    return value


def extract_tweet_tables(master_source, out_path, master_cache=None):
    """Write tweet-tables.json: the selection tables the four chains consume.

    *master_source* is a directory of master tables or a base URL to fetch them
    from; no bundle is read — this chain lives entirely in master tables.
    """
    master = Master(master_source, cache_dir=master_cache)
    tweets = _rows(master, TWEETS_TABLE)
    wrt = _rows(master, WRT_TABLE)
    greetings = _rows(master, GREETING_TABLE)
    conditions = _rows(master, GREETING_CONDITION_TABLE)
    site_entries = _rows(master, SITE_ENTRY_TABLE)
    pre_actions = _rows(master, PRE_ACTION_TABLE)

    tweet_ids = _unique_ids(TWEETS_TABLE, tweets)

    _unique_ids(WRT_TABLE, wrt)
    _unique_ids(GREETING_TABLE, greetings)
    _unique_ids(GREETING_CONDITION_TABLE, conditions)
    _unique_ids(SITE_ENTRY_TABLE, site_entries)
    _unique_ids(PRE_ACTION_TABLE, pre_actions)

    wrt_rows = []
    wrt_ids = set()
    for row in wrt:
        unit = row.get("gameCharacterUnitId")
        if unit is None:
            raise ValueError(
                f"{WRT_TABLE} row {row.get('id')}: no gameCharacterUnitId")
        tweet_id = _ref(row, "mysekaiCharacterTalkTweetId", tweet_ids,
                        WRT_TABLE, TWEETS_TABLE)
        wrt_ids.add(row["id"])
        wrt_rows.append({"id": row["id"], "gameCharacterUnitId": unit,
                         "tweetId": tweet_id})

    condition_ids = {row["id"] for row in conditions}
    greeting_rows = [
        {"id": row["id"],
         "withoutRelatedTalkId": _ref(
             row, "mysekaiCharacterTalkTweetWithoutRelatedTalkId", wrt_ids,
             GREETING_TABLE, WRT_TABLE),
         "greetingConditionId": _ref(
             row, "mysekaiCharacterTalkTweetGreetingConditionId",
             condition_ids, GREETING_TABLE, GREETING_CONDITION_TABLE)}
        for row in greetings]

    condition_rows = [
        {"id": row["id"],
         "conditionType": row.get("mysekaiCharacterTalkTweetGreetingConditionType"),
         "value1": row.get("mysekaiCharacterTalkTweetGreetingConditionValue1"),
         "value2": row.get("mysekaiCharacterTalkTweetGreetingConditionValue2")}
        for row in conditions]

    site_entry_rows = [
        {"id": row["id"],
         "withoutRelatedTalkId": _ref(
             row, "mysekaiCharacterTalkTweetWithoutRelatedTalkId", wrt_ids,
             SITE_ENTRY_TABLE, WRT_TABLE)}
        for row in site_entries]

    pre_action_rows = [
        {"id": row["id"],
         "talkId": row.get("mysekaiCharacterTalkId"),
         "tweetId": _ref(row, "mysekaiCharacterTalkTweetId", tweet_ids,
                         PRE_ACTION_TABLE, TWEETS_TABLE),
         "timelineGroupId": row.get("mysekaiCharacterTalkFixtureTimelineGroupId"),
         "fixtureTogetherCommunicationId": row.get("mysekaiCharacterTalkFixtureTogetherCommunicationId")}
        for row in pre_actions]
    absent_talks = [row["id"] for row in pre_action_rows
                    if row["talkId"] is None]
    if absent_talks:
        raise ValueError(f"{PRE_ACTION_TABLE}: rows without a talk id: "
                         f"{absent_talks[:5]}")

    unit_of_wrt = {row["id"]: row["gameCharacterUnitId"] for row in wrt_rows}
    greeting_units = {unit_of_wrt[row["withoutRelatedTalkId"]]
                      for row in greeting_rows}
    entry_units = {unit_of_wrt[row["withoutRelatedTalkId"]]
                   for row in site_entry_rows}

    doc = {
        "version": 1,
        "semantics": {
            "withoutRelatedTalks": (
                "the join spine, in master-table order; greetings and site "
                "entries point here for both the character and the tweet; "
                "order matters because the source's site-entry draw picks "
                "candidates in this table's order"
            ),
            "greetings": (
                "the greeting candidate rows, in master-table order (the "
                "source's greeting draw picks candidates in this order); each "
                "is gated by one greetingConditions row"
            ),
            "greetingConditions": (
                "conditionType is the server-issued gate name with value1 and "
                "value2 its parameters; a type the consumer does not know "
                "never matches, which is data, not an extraction error"
            ),
            "siteEntries": (
                "the rows a site entry draws from, in master-table order; "
                "each names one withoutRelatedTalks row"
            ),
            "talkPreActions": (
                "the deterministic link: one talk id names the tweet shown as "
                "that talk's opening pre-action; no draw is involved. talkId "
                "is opaque here — the talk rows live in the talk corpus "
                "product, not in this chain. timelineGroupId retains the "
                "authored fixture-timeline group reference, or null when "
                "that field is absent; it is not inferred from the tweet"
            ),
            "chain": [GREETING_TABLE, GREETING_CONDITION_TABLE, WRT_TABLE,
                      SITE_ENTRY_TABLE, PRE_ACTION_TABLE],
        },
        "withoutRelatedTalks": wrt_rows,
        "greetings": greeting_rows,
        "greetingConditions": condition_rows,
        "siteEntries": site_entry_rows,
        "talkPreActions": pre_action_rows,
        "summary": {
            "withoutRelatedTalkRows": len(wrt_rows),
            "greetingRows": len(greeting_rows),
            "greetingConditionRows": len(condition_rows),
            "greetingConditionTypes": dict(sorted(Counter(
                row["conditionType"] for row in condition_rows).items())),
            "siteEntryRows": len(site_entry_rows),
            "talkPreActionRows": len(pre_action_rows),
            "unitsWithGreeting": len(greeting_units),
            "unitsWithSiteEntry": len(entry_units),
        },
    }
    write_json(out_path, doc)
    return doc["summary"]
