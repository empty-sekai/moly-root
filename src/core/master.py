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
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from collections import Counter, defaultdict

from .atomic import write_json

_INPUT_REPORTS = ContextVar("master_input_reports", default=())


def record_master_inputs(function):
    """Attach the actual tables read by an extraction, including nested passes."""
    @wraps(function)
    def recorded(*args, **kwargs):
        inputs = {}
        token = _INPUT_REPORTS.set((*_INPUT_REPORTS.get(), inputs))
        try:
            report = function(*args, **kwargs)
        finally:
            _INPUT_REPORTS.reset(token)
        report["masterInputs"] = sorted(inputs.values(), key=lambda row: (
            row["sourceId"], row["table"], row["status"], row.get("sha256", "")))
        if report.get("report"):
            write_json(report["report"], report)
        return report
    return recorded


def _source_identity(source):
    parsed = urlsplit(source)
    if parsed.scheme.lower() not in {"http", "https"}:
        return str(Path(source).resolve())
    auth, separator, host = parsed.netloc.rpartition("@")
    netloc = (auth + separator if separator else "") + host.lower()
    if (parsed.scheme.lower() == "http" and netloc.endswith(":80")) \
            or (parsed.scheme.lower() == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), parsed.query, ""))

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

# These payload fields hold one record, not an array.  Keep the complete record
# as one row for table consumers; other objects may instead be wrappers or
# column-oriented compact tables and must not be interpreted as records.
_SINGLE_RECORD_TABLES = frozenset({
    "mysekaiColorfulPass",
    "mysekaiConvertFixtureSlot",
    "mysekaiFixtureGameCharacterPerformanceBonusLimit",
    "mysekaiSiteHousingPreset",
    "mysekaiStaminaRecovery",
})


class MissingTable(LookupError):
    """A required master table is not in the supplied directory."""


class Master:
    """Read-only view over master tables held in a directory or behind a URL.

    *source* is either a directory of ``<table>.json`` files or a base URL that
    ``<table>.json`` is appended to.  With a URL, each table is fetched once and
    written into a source/snapshot namespace in *cache_dir*. The default freezes
    cached tables. Choose another *snapshot* (or a versioned URL) for another
    input version; *refresh=True* explicitly fetches new bytes for this namespace.
    """

    def __init__(self, source, cache_dir=None, timeout=30.0, *, snapshot=None, refresh=False):
        self.source = str(source)
        self.source_identity = _source_identity(self.source)
        self.remote = self.source_identity.startswith(("http://", "https://"))
        self.snapshot = snapshot
        self.refresh = refresh
        self.source_id = hashlib.sha256(json.dumps(
            [self.source_identity, snapshot], ensure_ascii=False).encode("utf-8")).hexdigest()
        self.cache_dir = str(cache_dir) if cache_dir else None
        self.timeout = timeout
        self.fetched = []
        self._cache = {}
        self.provenance = {}

    def _record(self, name, status, payload=None, error=None):
        row = {"table": name, "sourceId": self.source_id,
               "source": self.source_identity, "snapshot": self.snapshot, "status": status}
        if payload is not None:
            row["sha256"] = hashlib.sha256(payload).hexdigest()
        if error:
            row["error"] = error
        self.provenance[name] = row
        for report in _INPUT_REPORTS.get():
            report[(self.source_id, name, status, row.get("sha256"))] = row

    def _read_local(self, path, name):
        if not os.path.isfile(path):
            self._record(name, "missing")
            raise MissingTable(f"master table not found: {name}")
        payload = Path(path).read_bytes()
        try:
            rows = json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            self._record(name, "invalid", payload, str(exc))
            raise
        self._record(name, "local", payload)
        return rows

    def _read_remote(self, name):
        cached = Path(self.cache_dir) / self.source_id / f"{name}.json" if self.cache_dir else None
        if cached and cached.is_file() and not self.refresh:
            try:
                envelope = json.loads(cached.read_bytes())
                payload = envelope["payload"].encode("utf-8")
                if (envelope.get("schema") != "moly-master-cache/1"
                        or envelope.get("source") != self.source_identity
                        or envelope.get("snapshot") != self.snapshot
                        or envelope.get("table") != name
                        or envelope.get("sha256") != hashlib.sha256(payload).hexdigest()):
                    raise ValueError("cache identity or checksum mismatch")
                rows = json.loads(payload)
            except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
                self._record(name, "invalid", error=str(exc))
                raise ValueError(f"invalid master cache for {name}; use refresh=True to fetch again") from exc
            self._record(name, "cached", payload)
            return rows
        parsed = urlsplit(self.source_identity)
        url = urlunsplit(parsed._replace(path=f"{parsed.path}/{name}.json"))
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                payload = response.read()
        except Exception as exc:                       # 404, DNS, timeout...
            self._record(name, "fetch_failed", error=str(exc))
            raise MissingTable(f"master table not retrievable: {name} ({exc})") from exc
        try:
            text = payload.decode("utf-8")
            rows = json.loads(text)
        except (ValueError, UnicodeError) as exc:
            self._record(name, "invalid", payload, str(exc))
            raise
        self.fetched.append(name)
        if cached:
            write_json(cached, {"schema": "moly-master-cache/1", "source": self.source_identity,
                                "snapshot": self.snapshot, "table": name,
                                "sha256": hashlib.sha256(payload).hexdigest(), "payload": text})
        self._record(name, "fetched", payload)
        return rows

    def table(self, name):
        """Object rows in source order, including an explicitly empty table.

        A missing table raises.  Arrays and row-array wrappers retain their
        rows; known single-record payloads become one-element arrays.  Compact
        column objects and other unsupported shapes are not guessed at.
        """
        if not name or not all(c.isalnum() or c in "_-" for c in name):
            raise ValueError(f"invalid master table name: {name!r}")
        if name not in self._cache:
            rows = (self._read_remote(name) if self.remote
                    else self._read_local(os.path.join(self.source, f"{name}.json"), name))
            if isinstance(rows, dict):
                if not rows:
                    rows = []
                elif "data" in rows:
                    rows = rows["data"]
                elif name in _SINGLE_RECORD_TABLES:
                    rows = [rows]
                elif len(rows) == 1:
                    rows = next(iter(rows.values()))
                else:
                    raise ValueError(f"{name}: unsupported table object; expected "
                                     "a row-array wrapper or a known single record")
            if not isinstance(rows, list):
                raise ValueError(f"{name}: expected an array of object rows, "
                                 f"got {type(rows).__name__}")
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError(f"{name}: row {index} is "
                                     f"{type(row).__name__}, expected an object")
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

    def game_characters(self):
        """{gameCharacterId: character row} — display names live here, not in
        the unit rows above; one character row feeds many unit variants."""
        return {row["id"]: row for row in self.table("gameCharacters")}

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
        types = {row["id"]: row.get("mysekaiCharacterTalkConditionType")
                 for row in self.table("mysekaiCharacterTalkConditions")}
        out = defaultdict(list)
        for row in self.table("mysekaiCharacterTalkConditionGroups"):
            out[row["groupId"]].append(types.get(row.get("mysekaiCharacterTalkConditionId")))
        return dict(out)

    def condition_entries(self):
        """{conditionGroupId: [{conditionType, conditionTypeValue}, ...]}.

        The value payload beside :meth:`condition_types`: the same group
        mapping in the same order, each entry carrying the condition's type
        name and the value the conditions table holds for it on
        ``mysekaiCharacterTalkConditionTypeValue`` — the field the phenomena
        and visit-count gates compare against.  The conditions table is the
        only place the value lives, so a condition id that table does not
        have resolves to null type and null value: the same absence rule the
        bare type list applies, kept so the two arrays cannot disagree about
        one group row.
        """
        entries = {row["id"]: row
                   for row in self.table("mysekaiCharacterTalkConditions")}
        out = defaultdict(list)
        for row in self.table("mysekaiCharacterTalkConditionGroups"):
            entry = entries.get(row.get("mysekaiCharacterTalkConditionId")) or {}
            out[row["groupId"]].append({
                "conditionType": entry.get("mysekaiCharacterTalkConditionType"),
                "conditionTypeValue": entry.get(
                    "mysekaiCharacterTalkConditionTypeValue"),
            })
        return dict(out)

    def site_groups(self):
        """{siteGroupId: [siteId, ...]} from the ``mysekaiSiteGroups`` table.

        A pure membership table: rows of (groupId, siteId), nothing else.
        The environment-site talk gate reads it as a mapping — the talk row
        names a ``mysekaiSiteGroupId`` and the gate asks whether the site
        the player stands in is a member.  Site ids keep table order.
        """
        out = defaultdict(list)
        for row in self.table("mysekaiSiteGroups"):
            out[row["groupId"]].append(row["mysekaiSiteId"])
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
        unit_groups = {row["id"] for row in self.table("mysekaiGameCharacterUnitGroups")}
        conditions = self.condition_types()
        condition_links = defaultdict(list)
        for row in self.table("mysekaiCharacterTalkConditionGroups"):
            condition_links[row["groupId"]].append({
                "groupRowId": row.get("id"),
                "conditionId": row.get("mysekaiCharacterTalkConditionId")})
        tweet_of = self.talk_tweet_ids()
        kept, dropped, unresolved = [], Counter(), []
        for talk in self.table("mysekaiCharacterTalks"):
            unit_group = talk.get("mysekaiGameCharacterUnitGroupId")
            condition_group = talk.get("mysekaiCharacterTalkConditionGroupId")
            reason = None
            if unit_group not in unit_groups:
                reason = "missing unit group"
            elif condition_group not in conditions:
                reason = "missing condition group"
            elif any(not isinstance(t, str) or not t for t in conditions[condition_group]):
                reason = "unresolved condition"
            if reason:
                unresolved.append({"talkId": talk["id"], "reason": reason,
                                   "unitGroupId": unit_group, "conditionGroupId": condition_group,
                                   "conditions": condition_links.get(condition_group, [])})
                continue
            unit = solo.get(unit_group)
            if unit is None:
                dropped["unit group holds more than one character"] += 1
                continue
            types = conditions[condition_group]
            if any(t in FURNITURE_CONDITIONS for t in types):
                dropped["gated on furniture"] += 1
                continue
            kept.append({"talk": talk, "unitId": unit,
                         "tweetId": tweet_of.get(talk["id"])})
        report = {
            "talksTotal": len(self.table("mysekaiCharacterTalks")),
            "kept": len(kept),
            "dropped": dict(dropped),
            "excluded": sum(dropped.values()),
            "unresolved": dict(Counter(row["reason"] for row in unresolved)),
            "unresolvedCount": len(unresolved),
            "unresolvedTalks": unresolved,
            "soloUnitGroups": len(solo),
            "charactersCovered": len({row["unitId"] for row in kept}),
            "withoutTweet": sum(1 for row in kept if row["tweetId"] is None),
        }
        return kept, report
