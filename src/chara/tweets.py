"""Extract head-top tweet texts and the per-character after-edit tweet pools.

Three master tables chain into one product:

* ``mysekaiCharacterTalkTweets`` — every tweet: its id and the text (plus the
  motion / facial pattern the tweet plays). This is the id-to-text table the
  presentation layer resolves a live bubble's line from.
* ``mysekaiCharacterTalkTweetWithoutRelatedTalks`` — tweets that belong to a
  character (``gameCharacterUnitId``) rather than to a talk script.
* ``mysekaiCharacterTalkTweetAfterEditHousingLayouts`` — the rows the source
  walks when a housing-layout edit is responded to; each names one
  without-related-talk row, and that row names the character and the tweet.

The chain is joined here, once, so a consumer never re-derives it: the product
answers two questions and nothing else — *what does tweet N say*, and *which
tweets can character U draw from after an edit*. Every row this module writes
is traceable to a master row; a reference that does not resolve is an error,
not a gap to fill with a default.
"""
import json
import os
from collections import defaultdict

from core.master import Master


TWEETS_TABLE = "mysekaiCharacterTalkTweets"
WITHOUT_RELATED_TABLE = "mysekaiCharacterTalkTweetWithoutRelatedTalks"
AFTER_EDIT_TABLE = "mysekaiCharacterTalkTweetAfterEditHousingLayouts"


def _rows(master, name):
    rows = master.table(name)
    if not rows:
        raise ValueError(f"{name}: table is empty; a chain this product joins "
                         "cannot be checked against zero rows")
    return rows


def _tweet_body(row):
    """The fields a bubble consumer reads, verbatim from the master row."""
    return {
        "text": row.get("text"),
        "motion": row.get("motionName"),
        "eye": row.get("expressionEyeName"),
        "mouth": row.get("expressionMouthName"),
    }


def extract_tweets(master_source, out_path, master_cache=None):
    """Write tweets.json: the id-to-text table plus the per-unit after-edit pools.

    *master_source* is a directory of master tables or a base URL to fetch them
    from; no bundle is read — this chain lives entirely in master tables.
    """
    master = Master(master_source, cache_dir=master_cache)
    tweets = _rows(master, TWEETS_TABLE)
    without_related = _rows(master, WITHOUT_RELATED_TABLE)
    after_edit = _rows(master, AFTER_EDIT_TABLE)

    by_id = {}
    for row in tweets:
        tweet_id = row.get("id")
        if tweet_id is None:
            raise ValueError(f"{TWEETS_TABLE}: row without an id")
        if tweet_id in by_id:
            raise ValueError(f"{TWEETS_TABLE}: duplicate id {tweet_id}")
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{TWEETS_TABLE}: tweet {tweet_id} carries no text")
        by_id[tweet_id] = _tweet_body(row)

    unit_of = {}
    for row in without_related:
        row_id = row.get("id")
        unit = row.get("gameCharacterUnitId")
        tweet_id = row.get("mysekaiCharacterTalkTweetId")
        if row_id is None or unit is None:
            raise ValueError(f"{WITHOUT_RELATED_TABLE}: row without id or unit")
        if tweet_id not in by_id:
            raise LookupError(
                f"{WITHOUT_RELATED_TABLE} row {row_id}: tweet {tweet_id} is not "
                f"in {TWEETS_TABLE}")
        unit_of[row_id] = (unit, tweet_id)

    pools = defaultdict(list)
    for row in after_edit:
        row_id = row.get("id")
        ref = row.get("mysekaiCharacterTalkTweetWithoutRelatedTalkId")
        if ref not in unit_of:
            raise LookupError(
                f"{AFTER_EDIT_TABLE} row {row_id}: without-related-talk row "
                f"{ref} is not in {WITHOUT_RELATED_TABLE}")
        unit, tweet_id = unit_of[ref]
        pools[unit].append(tweet_id)
    pools = {str(unit): sorted(ids) for unit, ids in sorted(pools.items())}

    for unit, ids in pools.items():
        if len(set(ids)) != len(ids):
            raise ValueError(f"unit {unit}: after-edit pool carries a duplicate tweet")
        absent = [tid for tid in ids if tid not in by_id]
        if absent:
            raise LookupError(f"unit {unit}: pool names absent tweets {absent}")

    doc = {
        "version": 1,
        "semantics": {
            "tweets": (
                "keyed by tweet id; text is the master row's own text, "
                "unmodified, newlines included; motion/eye/mouth are the "
                "motion and facial pattern names the master row pairs with it"
            ),
            "afterEditPools": (
                "keyed by gameCharacterUnitId; the tweet ids the source's "
                "after-edit reaction draws from for that character, joined "
                f"{AFTER_EDIT_TABLE} -> {WITHOUT_RELATED_TABLE} -> "
                f"{TWEETS_TABLE}; a unit absent here has no after-edit rows "
                "in the source tables, which is absence, not an empty pool"
            ),
            "chain": [AFTER_EDIT_TABLE, WITHOUT_RELATED_TABLE, TWEETS_TABLE],
        },
        "tweets": {str(tid): body for tid, body in sorted(by_id.items())},
        "afterEditPools": pools,
        "summary": {
            "tweets": len(by_id),
            "withoutRelatedTalkRows": len(unit_of),
            "afterEditRows": len(after_edit),
            "unitsWithAfterEditPool": len(pools),
            "poolSizes": sorted({len(ids) for ids in pools.values()}),
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1, allow_nan=False)
        handle.write("\n")
    return doc["summary"]
