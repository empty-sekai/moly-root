"""Versioned display-text catalogues, independent of executable scripts.

Text IDs survive source revisions through unique voice/context anchors or
unchanged text. Ambiguous repetitions are never silently paired after an
edit. Bindings locate a display operation in a specific script revision;
they are not the translation identity.
"""
from collections import defaultdict
import hashlib
import json

from core.jsonio import write_json

SCHEMA = "moly-text-source/1"
REGIONS = {"jp", "cn", "tw", "en", "kr"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def corpus_rows(document):
    for unit, group in (document.get("units") or {}).items():
        for row in group.get("talks", []):
            yield "general", row, [int(unit)]
    for row in document.get("talks", []):
        yield "fixture", row, row["unitIds"]
    for row in (document.get("commonSomeCharacter") or {}).get("talks", []):
        yield "common", row, row["unitIds"]
    for lua, row in (document.get("birthdayParty") or {}).get("talks", {}).items():
        yield "birthday", {**row, "lua": lua, "talkId": lua}, []


def _display_rows(row):
    """Original label tokens remain untouched; their displays get bindings."""
    rows = []
    speaker, unit, pending = "", None, []
    for index, step in enumerate(row["steps"]):
        op = step["op"]
        if op == "label":
            speaker = step["name"]
            unit, pending = None, []
            rows.append({"kind": "speaker", "step": index, "text": speaker,
                         "speaker": speaker, "speakerUnit": None, "cues": []})
        elif op in ("voice", "fixture_voice"):
            unit = step.get("who", step.get("fixture"))
            pending.append(step["cue"])
            # Labels conventionally precede voice; attach its identity to
            # that label's display without changing the label/control token.
            for display in reversed(rows):
                if display["kind"] == "speaker":
                    display["speakerUnit"] = unit
                    display["cues"] = list(pending)
                    break
                if display["kind"] == "text":
                    break
        elif op == "text":
            rows.append({"kind": "text", "step": index, "text": step["text"],
                         "speaker": speaker, "speakerUnit": unit, "cues": list(pending)})
            pending = []
    for i, display in enumerate(rows):
        if not isinstance(display["text"], str):
            raise ValueError(f"{row['lua']}: display text is not a string")
        display["position"] = i
        display["sourceHash"] = digest([display["kind"], display["text"], display["speaker"],
                                        display["speakerUnit"], display["cues"]])
        display["anchor"] = digest([display["kind"], display["speakerUnit"], display["cues"],
                                    display.get("tweetId")]) if display["cues"] or display["kind"] == "tweet" else None
    return rows


def _assign_ids(region, script, current, previous):
    old = previous.get("lines", []) if previous else []
    retired_before = previous.get("retiredLines", []) if previous else []
    used, matched = set(), {}
    # Unchanged complete display sequence permits exact occurrence mapping.
    # A motion insertion changes bindings but not this sequence.
    if [r["sourceHash"] for r in old] == [r["sourceHash"] for r in current]:
        matched = {i: r for i, r in enumerate(old)}
        used = {r["id"] for r in old}
    else:
        for field in ("anchor", "sourceHash"):
            old_by, new_by = defaultdict(list), defaultdict(list)
            for r in old:
                if r["id"] not in used and r.get(field):
                    old_by[r[field]].append(r)
            for i, r in enumerate(current):
                if i not in matched and r.get(field):
                    new_by[r[field]].append(i)
            for key, indices in new_by.items():
                if len(indices) == 1 and len(old_by.get(key, [])) == 1:
                    match = old_by[key][0]
                    matched[indices[0]] = match
                    used.add(match["id"])
    old_ids = {r["id"] for r in old + retired_before}
    allocated = set(old_ids)
    for i, row in enumerate(current):
        if i in matched:
            prior = matched[i]
            row["id"] = prior["id"]
            row["change"] = "unchanged" if row["sourceHash"] == prior["sourceHash"] else "changed"
        else:
            salt = 0
            while True:
                identity = "t_" + digest([region, script, row["kind"], row["anchor"],
                                           row["sourceHash"], i, salt])[:32]
                if identity not in allocated:
                    break
                salt += 1
            row["id"] = identity
            row["change"] = "new"
            allocated.add(identity)
    return retired_before + [r for r in old if r["id"] not in used]


def build_catalog(documents, *, region, source_revision, previous=None):
    if region not in REGIONS or not source_revision:
        raise ValueError("a supported source region and explicit source revision are required")
    if previous and (previous.get("schema") != SCHEMA or previous.get("region") != region):
        raise ValueError("previous text catalogue belongs to another schema or region")
    old = {r["key"]: r for r in (previous or {}).get("retiredScripts", [])}
    old.update({r["key"]: r for r in (previous or {}).get("scripts", [])})
    scripts, tweet_refs = {}, {}
    for document in documents:
        for kind, row, members in corpus_rows(document):
            key = row["lua"].removesuffix(".lua")
            lines = _display_rows(row)
            signature = digest(row["steps"])
            content = {"kind": kind, "id": row["talkId"], "unitIds": members}
            tweet = row.get("tweet")
            if tweet and isinstance(tweet.get("text"), str):
                tweet_key = "@tweet/" + str(tweet["id"])
                if tweet_key in tweet_refs and tweet_refs[tweet_key]["tweet"] != tweet:
                    raise ValueError(f"conflicting source bodies for tweet {tweet['id']}")
                ref = tweet_refs.setdefault(tweet_key, {"tweet": tweet, "contents": []})
                tweet_content = {**content, "scriptKey": key}
                if tweet_content not in ref["contents"]:
                    ref["contents"].append(tweet_content)
                content = {**content, "tweetKey": tweet_key}
            if key in scripts:
                existing = scripts[key]
                if existing["scriptHash"] != signature:
                    raise ValueError(f"conflicting source bodies for script {key}")
                if content not in existing["contents"]:
                    existing["contents"].append(content)
                continue
            retired = _assign_ids(region, key, lines, old.get(key))
            scripts[key] = {"key": key, "scriptHash": signature, "contents": [content],
                            "lines": lines, "retiredLines": retired,
                            "displaySequence": [s["op"] for s in row["steps"] if s["op"] in
                                                ("label", "text", "voice", "fixture_voice", "wait_click")],
                            "speakerIdentity": sorted({str(s.get("who", s.get("fixture")))
                                                       for s in row["steps"]
                                                       if s["op"] in ("voice", "fixture_voice")})}
    # A script may be referenced by several talk rows with different tweets.
    # Tweets have their own master identities, not a script-body identity.
    for key, ref in tweet_refs.items():
        tweet = ref["tweet"]
        lines = [{"kind": "tweet", "step": None, "tweetId": tweet["id"],
                  "text": tweet["text"], "speaker": "", "speakerUnit": None,
                  "cues": [], "position": 0,
                  "sourceHash": digest(["tweet", tweet]),
                  "anchor": digest(["tweet", tweet["id"]])}]
        retired = _assign_ids(region, key, lines, old.get(key))
        scripts[key] = {"key": key, "scriptHash": digest(tweet),
                        "contents": ref["contents"], "lines": lines, "retiredLines": retired,
                        "tweetContext": {k: v for k, v in tweet.items() if k != "text"}}
    result = {"schema": SCHEMA, "region": region, "sourceRevision": source_revision,
              "scripts": list(scripts.values()),
              "retiredScripts": [r for key, r in old.items() if key not in scripts]}
    # Import diagnostics (new/changed/unchanged and retired history) are not
    # published source content. Reimporting identical bytes keeps a revision.
    result["revision"] = digest({
        "schema": SCHEMA, "region": region, "sourceRevision": source_revision,
        "scripts": [{k: ([{lk: lv for lk, lv in line.items() if lk != "change"}
                          for line in v] if k == "lines" else v)
                     for k, v in script.items() if k != "retiredLines"}
                    for script in result["scripts"]],
    })
    return result


def match_official(source, donor, *, locale):
    """Pair complete display sequences only when spoken context agrees.

    Same script name is a candidate, never sufficient proof. Require equal
    participating units, line kinds and per-line voice/speaker identity.
    Lines without a voice anchor require an explicit reviewed mapping.
    """
    expected = {"zh-CN": "cn", "zh-TW": "tw", "en-US": "en", "ko-KR": "kr"}
    if source.get("schema") != SCHEMA or donor.get("schema") != SCHEMA:
        raise ValueError("unsupported source catalogue schema")
    if expected.get(locale) != donor["region"]:
        raise ValueError("official text locale does not match donor region")
    donors = {r["key"]: r for r in donor["scripts"]}
    entries, issues = [], []
    for script in source["scripts"]:
        other = donors.get(script["key"])
        reason = None
        if other is None:
            reason = "missing_script"
        else:
            def members(value):
                return sorted({tuple(c["unitIds"]) for c in value["contents"]})
            def spoken(value):
                return [(r["kind"], r["speakerUnit"], r["cues"]) for r in value["lines"] if r["kind"] != "tweet"]
            if members(script) != members(other):
                reason = "participants_differ"
            elif script.get("displaySequence") != other.get("displaySequence"):
                reason = "display_control_sequence_differs"
            elif script.get("speakerIdentity") != other.get("speakerIdentity"):
                reason = "speaker_identities_differ"
            elif script.get("tweetContext") != other.get("tweetContext"):
                reason = "tweet_context_differs"
            elif script.get("tweetContext") is not None and sorted(
                {c["scriptKey"] for c in script["contents"]}
            ) != sorted({c["scriptKey"] for c in other["contents"]}):
                reason = "tweet_script_links_differ"
            elif spoken(script) != spoken(other):
                reason = "display_sequence_differs"
        if reason:
            issues.append({"script": script["key"], "reason": reason})
            continue
        source_lines = script["lines"]
        donor_lines = other["lines"]
        anchors = defaultdict(int)
        for line in source_lines:
            anchors[line.get("anchor")] += 1
        for line, official in zip(source_lines, donor_lines):
            if line.get("anchor") and anchors[line["anchor"]] > 1:
                issues.append({"script": script["key"], "id": line["id"], "reason": "ambiguous_voice_anchor"})
                continue
            if line["kind"] != "tweet" and (not line["cues"] or line["speakerUnit"] is None):
                issues.append({"script": script["key"], "id": line["id"], "reason": "unanchored_line"})
                continue
            if not official["text"].strip() or official["text"] == line["text"]:
                issues.append({"script": script["key"], "id": line["id"], "reason": "empty_or_unchanged_text"})
                continue
            entries.append({"id": line["id"], "sourceHash": line["sourceHash"],
                            "text": official["text"], "source": "official_" + donor["region"],
                            "donor": {"region": donor["region"], "revision": donor["revision"],
                                      "script": other["key"], "id": official["id"],
                                      "sourceHash": official["sourceHash"]}})
    result = {"schema": "moly-text-overlay/1", "sourceRegion": source["region"],
              "sourceRevision": source["revision"], "locale": locale,
              "entries": entries, "issues": issues}
    result["revision"] = digest(result)
    return result


def export_catalog(paths, out, *, region, source_revision, previous=None):
    def read(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    result = build_catalog([read(p) for p in paths], region=region,
                           source_revision=source_revision, previous=read(previous) if previous else None)
    write_json(out, result)
    return {"scripts": len(result["scripts"]),
            "lines": sum(len(r["lines"]) for r in result["scripts"]), "revision": result["revision"]}
