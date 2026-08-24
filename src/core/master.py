"""Master tables: the game's own configuration rows, supplied by the caller.

These tables are not bundled here.  The caller points at a directory of them,
the same way the bundle manifest is supplied — this module only says what shape
the rows have and which of them belong to one character.

The load-bearing part is :meth:`Master.solo_talks`.  A talk row addresses a
*group* of characters and a *group* of conditions; a talk belongs to a single
character's asset pack only when its unit group holds exactly one character and
its condition group asks nothing about furniture (furniture is not part of a
character).  Both halves of that predicate are reported, so a consumer can see
what was excluded and why instead of trusting a single number.
"""
import json
import os
import urllib.request
from collections import Counter, defaultdict

# Tables may be read from a directory or fetched one-by-one from a base URL by
# appending "<table>.json".  This public mirror is the default base when the
# caller asks for the remote form without naming one; nothing is fetched unless
# a remote base is chosen explicitly.
DEFAULT_MASTER_URL = ("https://raw.githubusercontent.com/Team-Haruki/"
                      "haruki-sekai-sc-master/main/master")

# A talk gated on furniture depends on something outside the character.
FURNITURE_CONDITIONS = ("mysekai_fixture_id", "mysekai_fixture_tag_id",
                        "after_set_fixture")
UNIT_GROUP_SLOTS = 5                     # gameCharacterUnitId1..5

# Tables that name a sound package.  Music is two layers -- a base keyed by site
# and brightness, and a per-phenomenon row that replaces it -- and a row of
# either layer names the leaf of its own package.  Ambience is addressed the
# other way round: a row names only a cue, and every ambience cue lives in one
# shared package, so those rows say *whether* ambience is wanted, not where.
BGM_TABLES = ("mysekaiPhenomenaBgms", "mysekaiSiteBgms")
AMBIENCE_TABLE = "mysekaiSiteMysekaiPhenomenaSounds"


class MissingTable(LookupError):
    """A required master table is not in the supplied directory."""


class Master:
    """Read-only view over master tables held in a directory or behind a URL.

    *source* is either a directory of ``<table>.json`` files or a base URL that
    ``<table>.json`` is appended to.  With a URL, each table is fetched once and
    written into *cache_dir* (when given) so a second run reads from disk.
    """

    def __init__(self, source, cache_dir=None, timeout=30.0):
        self.source = str(source)
        self.remote = self.source.startswith(("http://", "https://"))
        self.cache_dir = str(cache_dir) if cache_dir else None
        self.timeout = timeout
        self.fetched = []
        self._cache = {}

    def _read_local(self, path, name):
        if not os.path.isfile(path):
            raise MissingTable(f"master table not found: {name}")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _read_remote(self, name):
        cached = os.path.join(self.cache_dir, f"{name}.json") if self.cache_dir else None
        if cached and os.path.isfile(cached):
            return self._read_local(cached, name)
        url = f"{self.source.rstrip('/')}/{name}.json"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                payload = response.read()
        except Exception as exc:                       # 404, DNS, timeout...
            raise MissingTable(f"master table not retrievable: {name} ({exc})") from exc
        rows = json.loads(payload.decode("utf-8"))
        self.fetched.append(name)
        if cached:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(cached, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(rows, handle, ensure_ascii=False, allow_nan=False)
        return rows

    def table(self, name):
        """Rows of one table; raises when it is absent rather than returning []."""
        if name not in self._cache:
            rows = (self._read_remote(name) if self.remote
                    else self._read_local(os.path.join(self.source, f"{name}.json"), name))
            if isinstance(rows, dict):                 # some dumps wrap the array
                rows = rows.get("data") or next(iter(rows.values()), [])
            self._cache[name] = rows
        return self._cache[name]

    # -- sound packages -----------------------------------------------------

    def sound_packages(self):
        """Sound packages these tables name, and which of the tables were absent.

        Returns ``(names, report)``: *names* are logical package names, and
        *report* says how many packages each table named and which tables are
        not in this source.  Several rows naming one package are one package —
        the site layer has one row per brightness over the same music.
        """
        from .assets.router import AMBIENCE_PACKAGE, BGM_PACKAGE_PREFIX
        packages, counted, absent = set(), {}, []
        for table in BGM_TABLES:
            try:
                rows = self.table(table)
            except MissingTable:
                absent.append(table)
                continue
            named = {BGM_PACKAGE_PREFIX + str(row["assetbundleName"])
                     for row in rows if row.get("assetbundleName")}
            counted[table] = len(named)
            packages |= named
        try:
            rows = self.table(AMBIENCE_TABLE)
        except MissingTable:
            absent.append(AMBIENCE_TABLE)
        else:
            # The rows carry cues, so they answer "is the shared package wanted".
            wanted = any(row.get("cue") for row in rows)
            counted[AMBIENCE_TABLE] = int(wanted)
            if wanted:
                packages.add(AMBIENCE_PACKAGE)
        return sorted(packages), {"tables": counted, "absentTables": sorted(absent)}

    # -- identity -----------------------------------------------------------

    def character_units(self):
        """{unitId: identity row} — colours and unit name of each character."""
        return {row["id"]: row for row in self.table("gameCharacterUnits")}

    # -- locomotion -------------------------------------------------------

    def locomotion(self):
        """{unitId: idle/walk/run personality} — one row per character."""
        return {row["gameCharacterUnitId"]: row
                for row in self.table("mysekaiCharacterTalkMotions")}

    def solo_actions(self):
        """{unitId: alone-action script name}."""
        return {row["gameCharacterUnitId"]: row["lua"]
                for row in self.table("mysekaiCharacterTalkSoloActions")}

    def client_configs(self):
        """{id: value} with each clientConfigs value parsed by its declared type."""
        parsed = {}
        for row in self.table("clientConfigs"):
            value = row.get("value")
            kind = row.get("type")
            if kind == "Int":
                value = int(value)
            elif kind == "Float":
                value = float(value)
            elif kind == "Bool":
                value = str(value).strip().lower() in {"1", "true", "yes"}
            elif kind == "String":
                value = str(value)
            else:
                raise ValueError(f"unsupported client config type: {kind}")
            parsed[row["id"]] = value
        return parsed

    # -- talk membership ---------------------------------------------------

    def solo_unit_groups(self):
        """{groupId: unitId} for groups that hold exactly one character."""
        out = {}
        for row in self.table("mysekaiGameCharacterUnitGroups"):
            members = [row.get(f"gameCharacterUnitId{i}")
                       for i in range(1, UNIT_GROUP_SLOTS + 1)]
            members = [m for m in members if m]
            if len(members) == 1:
                out[row["id"]] = members[0]
        return out

    def condition_types(self):
        """{conditionGroupId: [condition type, ...]}.

        The group table is a mapping table: one row per (group, condition) pair,
        keyed by ``groupId`` — not by its own ``id``.
        """
        types = {row["id"]: row["mysekaiCharacterTalkConditionType"]
                 for row in self.table("mysekaiCharacterTalkConditions")}
        out = defaultdict(list)
        for row in self.table("mysekaiCharacterTalkConditionGroups"):
            out[row["groupId"]].append(types.get(row["mysekaiCharacterTalkConditionId"]))
        return dict(out)

    def tweets(self):
        """{tweetId: row} — each carries text plus a motion and facial pattern."""
        return {row["id"]: row for row in self.table("mysekaiCharacterTalkTweets")}

    def talk_tweet_ids(self):
        """{talkId: tweetId} from the pre-action table."""
        return {row["mysekaiCharacterTalkId"]: row["mysekaiCharacterTalkTweetId"]
                for row in self.table("mysekaiCharacterTalkPreActions")}

    def solo_talks(self):
        """Talks that belong to one character, with why the rest were excluded.

        Returns ``(kept, report)``: *kept* is a list of ``{talk, unitId, tweetId}``
        and *report* counts the talks dropped by each half of the predicate.
        """
        solo = self.solo_unit_groups()
        conditions = self.condition_types()
        tweet_of = self.talk_tweet_ids()
        kept, dropped = [], Counter()
        for talk in self.table("mysekaiCharacterTalks"):
            unit = solo.get(talk.get("mysekaiGameCharacterUnitGroupId"))
            if unit is None:
                dropped["unit group holds more than one character"] += 1
                continue
            types = conditions.get(talk.get("mysekaiCharacterTalkConditionGroupId"), [])
            if any(t in FURNITURE_CONDITIONS for t in types):
                dropped["gated on furniture"] += 1
                continue
            kept.append({"talk": talk, "unitId": unit,
                         "tweetId": tweet_of.get(talk["id"])})
        report = {
            "talksTotal": len(self.table("mysekaiCharacterTalks")),
            "kept": len(kept),
            "dropped": dict(dropped),
            "soloUnitGroups": len(solo),
            "charactersCovered": len({row["unitId"] for row in kept}),
            "withoutTweet": sum(1 for row in kept if row["tweetId"] is None),
        }
        return kept, report
