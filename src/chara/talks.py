"""Extract direct character-talk scripts and their master-table metadata.

Talk assets are small Lua-like call streams. This module parses that stream as
ordered data: comments and quoted strings are skipped by the call scanner,
and every discovered call must become one step. Unknown constants remain as
source tokens instead of being guessed.
"""
import json
import os
import re
from collections import Counter

import UnityPy

from core.master import Master


ALL_CALL_OPS = (
    "change_npc_eye", "change_npc_mouth", "change_animation", "label",
    "text", "wait_click", "look_at_body", "voice", "wait_time",
    "emoticon", "change_fixture_timeline", "change_fixture_character_eye",
    "change_fixture_character_mouth", "fixture_voice", "look_at_fixture",
    "look_at_to_npc", "show_fixture_emoticon", "hide_talk_window",
    "show_talk_window", "wait_time_on_auto_mode", "hide_emoticon",
    "play_animation", "play_fixture_gimmick", "stop_fixture_gimmick",
)
# Public operation vocabulary.  The scanner uses the same set so reports and
# callers cannot silently disagree about which calls are counted.
CALL_OPS = ALL_CALL_OPS
SPEED_ZERO_MEANS = 1.0
_CALL_NAMES = set(CALL_OPS)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_TABLE_HEAD = re.compile(r"(?m)^\s*(?:local\s+)?([A-Za-z_]\w*)\s*=\s*\{")


def _skip_quoted(text, start):
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == quote:
            return i + 1
        else:
            i += 1
    raise ValueError("unterminated quoted string")


def _long_bracket_end(text, start):
    if start >= len(text) or text[start] != "[":
        return None
    match = re.match(r"\[(=*)\[", text[start:])
    if not match:
        return None
    close = "]" + match.group(1) + "]"
    end = text.find(close, start + len(match.group(0)))
    if end < 0:
        raise ValueError("unterminated long-bracket string")
    return end + len(close)


def _skip_comment(text, start):
    if not text.startswith("--", start):
        return None
    long_end = _long_bracket_end(text, start + 2)
    if long_end is not None:
        return long_end
    end = text.find("\n", start + 2)
    return len(text) if end < 0 else end


def _skip_literal(text, start):
    if text[start] in "\"'":
        return _skip_quoted(text, start)
    return _long_bracket_end(text, start)


def _lua_string(raw):
    """Decode common Lua escapes without damaging non-ASCII source text."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        raw = raw[1:-1]
    else:
        match = re.match(r"\[(=*)\[(.*)\]\1\]$", raw, re.DOTALL)
        if match:
            raw = match.group(2)
    out, i = [], 0
    simple = {
        "n": "\n", "r": "\r", "t": "\t", "b": "\b",
        "f": "\f", "v": "\v", "a": "\a", "\\": "\\",
        "\"": "\"", "'": "'",
    }
    while i < len(raw):
        if raw[i] != "\\":
            out.append(raw[i])
            i += 1
            continue
        i += 1
        if i >= len(raw):
            out.append("\\")
            break
        char = raw[i]
        if char in simple:
            out.append(simple[char])
            i += 1
        elif char == "z":
            i += 1
            while i < len(raw) and raw[i].isspace():
                i += 1
        elif char == "x" and re.fullmatch(r"[0-9A-Fa-f]{2}", raw[i + 1:i + 3]):
            out.append(chr(int(raw[i + 1:i + 3], 16)))
            i += 3
        elif char.isdigit():
            match = re.match(r"\d{1,3}", raw[i:])
            out.append(chr(int(match.group(0), 10)))
            i += len(match.group(0))
        elif char == "\n":
            out.append("\n")
            i += 1
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _skip_noncode(text, start):
    comment = _skip_comment(text, start)
    if comment is not None:
        return comment
    literal = _skip_literal(text, start)
    return literal


def _split_args(body):
    args, start, stack, i = [], 0, [], 0
    pairs = {")": "(", "]": "[", "}": "{"}
    while i < len(body):
        skipped = _skip_noncode(body, i)
        if skipped is not None:
            i = skipped
            continue
        char = body[i]
        if char in "([{":
            stack.append(char)
        elif char in ")]}" and stack and stack[-1] == pairs[char]:
            stack.pop()
        elif char == "," and not stack:
            args.append(body[start:i].strip())
            start = i + 1
        i += 1
    tail = body[start:].strip()
    if tail:
        args.append(tail)
    return args


def _argument_list(text, open_index):
    """Return raw arguments and the index immediately after their close paren."""
    stack, i, start = ["("], open_index + 1, open_index + 1
    pairs = {")": "(", "]": "[", "}": "{"}
    while i < len(text):
        skipped = _skip_noncode(text, i)
        if skipped is not None:
            i = skipped
            continue
        char = text[i]
        if char in "([{":
            stack.append(char)
        elif char in ")]}" and stack and stack[-1] == pairs[char]:
            stack.pop()
            if not stack:
                return _split_args(text[start:i]), i + 1
        i += 1
    raise ValueError("unterminated call argument list")


def _calls(text, script_name=None):
    """Yield ``(operation, arguments)`` for actual calls in source order."""
    text = text.lstrip("﻿")
    i = 0
    while i < len(text):
        skipped = _skip_noncode(text, i)
        if skipped is not None:
            i = skipped
            continue
        match = _IDENT.match(text, i)
        if not match:
            i += 1
            continue
        name, start, i = match.group(0), i, match.end()
        if name not in _CALL_NAMES:
            continue
        if start and (text[start - 1].isalnum() or text[start - 1] in "_."):
            continue
        whitespace = re.match(r"\s*", text[i:])
        open_index = i + len(whitespace.group(0))
        if open_index >= len(text) or text[open_index] != "(":
            continue
        try:
            args, i = _argument_list(text, open_index)
        except ValueError as err:
            prefix = f"{script_name}: " if script_name else ""
            raise ValueError(prefix + str(err)) from err
        yield name, args


def _strip_comments(text):
    """Remove line comments while retaining strings and line structure."""
    out, i = [], 0
    while i < len(text):
        skipped = _skip_noncode(text, i)
        if skipped is not None:
            if text.startswith("--", i):
                out.append(" " * (skipped - i))
            else:
                out.append(text[i:skipped])
            i = skipped
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _literal(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        return _lua_string(raw)
    if raw.startswith("[") and re.fullmatch(r"\[(=*)\[.*\]\1\]", raw, re.DOTALL):
        return _lua_string(raw)
    if raw == "true":
        return True
    if raw == "false":
        return False
    if _NUMBER.fullmatch(raw):
        return float(raw) if any(c in raw for c in ".eE") else int(raw)
    return raw


def _balanced_block(text, open_index, opening="{", closing="}"):
    stack, i = [opening], open_index + 1
    pairs = {")": "(", "]": "[", "}": "{"}
    while i < len(text):
        skipped = _skip_noncode(text, i)
        if skipped is not None:
            i = skipped
            continue
        char = text[i]
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if stack and stack[-1] == pairs[char]:
                stack.pop()
                if not stack:
                    return i
        i += 1
    raise ValueError("unterminated constant table")


def _assignment_value(item):
    stack, quote, i = [], None, 0
    pairs = {")": "(", "]": "[", "}": "{"}
    while i < len(item):
        if quote:
            if item[i] == "\\":
                i += 2
                continue
            if item[i] == quote:
                quote = None
            i += 1
            continue
        if item[i] in "\"'":
            quote = item[i]
        elif item[i] in "([{":
            stack.append(item[i])
        elif item[i] in ")]}":
            if stack and stack[-1] == pairs[item[i]]:
                stack.pop()
        elif item[i] == "=" and not stack:
            return item[:i].strip(), item[i + 1:].strip()
        i += 1
    return None, None


def parse_constant_tables(text):
    """Parse simple ``Name = { key = value }`` constant tables."""
    text = (text or "").lstrip("﻿")
    tables, cursor = {}, 0
    while True:
        match = _TABLE_HEAD.search(text, cursor)
        if not match:
            return tables
        close = _balanced_block(text, match.end() - 1)
        entries = {}
        for item in _split_args(text[match.end():close]):
            key, value = _assignment_value(item)
            if key and re.fullmatch(r"[A-Za-z_]\w*", key):
                entries[key] = _literal(value)
        tables[match.group(1)] = entries
        cursor = close + 1


def parse_constant_scalars(text, tables):
    """Parse scalar aliases used by call arguments."""
    scalars = {}
    clean = _strip_comments((text or "").lstrip("﻿"))
    pattern = r"(?m)^\s*(?:local\s+)?([A-Za-z_]\w*)\s*=\s*([^\n]+)$"
    for match in re.finditer(pattern, clean):
        name, raw = match.group(1), match.group(2).strip()
        if name in tables or raw.startswith("{"):
            continue
        value = _literal(raw)
        if isinstance(value, str) and "." in value:
            table, key = value.split(".", 1)
            if table in tables and key in tables[table]:
                value = tables[table][key]
        scalars[name] = value
    return scalars


def _resolve(raw, tables, scalars=None):
    token = raw.strip()
    value = _literal(token)
    if not isinstance(value, str):
        return value, None
    dotted = re.fullmatch(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)", value)
    if dotted and dotted.group(1) in tables:
        table, key = dotted.groups()
        if key in tables[table]:
            return tables[table][key], value
    if scalars and value in scalars:
        return scalars[value], value
    return value, None


def _step(op, args, tables, scalars):
    values = [_resolve(arg, tables, scalars) for arg in args]
    step = {"op": op}
    if op == "label":
        step["name"] = values[0][0] if values else None
    elif op == "text":
        step["text"] = values[0][0] if values else ""
    elif op in ("change_npc_eye", "change_fixture_character_eye"):
        key = "who" if op == "change_npc_eye" else "fixture"
        step[key] = values[0][0] if values else None
        step["pattern"] = values[1][0] if len(values) > 1 else None
        if len(values) > 1:
            step["alias"] = args[1].strip()
    elif op in ("change_npc_mouth", "change_fixture_character_mouth"):
        key = "who" if op == "change_npc_mouth" else "fixture"
        step[key] = values[0][0] if values else None
        step["pattern"] = values[1][0] if len(values) > 1 else None
        if len(values) > 1:
            step["alias"] = args[1].strip()
    elif op in ("change_animation", "play_animation"):
        if values:
            step["who"] = values[0][0]
        if len(values) > 1:
            step["motion"] = values[1][0]
            step["alias"] = args[1].strip()
        speed = values[2][0] if len(values) > 2 else None
        step["speed"] = speed
        step["playbackSpeed"] = (
            SPEED_ZERO_MEANS
            if not isinstance(speed, (int, float)) or speed == 0
            else float(speed)
        )
        if len(values) > 4:
            step["playEndMotion"] = bool(values[4][0])
        if len(values) > 5:
            step["blend"] = values[5][0]
        if len(values) > 6:
            step["phase"] = values[6][0]
    elif op == "voice":
        if values:
            step["channel"] = values[0][0]
        if len(values) > 1:
            step["cue"] = values[1][0]
        if len(values) > 2:
            step["who"] = values[2][0]
    elif op == "fixture_voice":
        if values:
            step["cue"] = values[0][0]
        if len(values) > 1:
            step["fixture"] = values[1][0]
    elif op == "look_at_body":
        if values:
            step["who"] = values[0][0]
        if len(values) > 1:
            step["target"] = values[1][0]
        if len(values) > 2:
            step["duration"] = values[2][0]
    elif op == "look_at_to_npc":
        if values:
            step["fixture"] = values[0][0]
        if len(values) > 1:
            step["who"] = values[1][0]
        if len(values) > 2:
            step["duration"] = values[2][0]
    elif op in ("emoticon", "show_fixture_emoticon"):
        key = "who" if op == "emoticon" else "fixture"
        if values:
            step[key] = values[0][0]
        if len(values) > 1:
            step["name"] = values[1][0]
            step["alias"] = args[1].strip()
        if len(values) > 3:
            step["showSeconds"] = values[3][0]
    elif op in ("wait_time", "wait_time_on_auto_mode"):
        if values:
            step["seconds"] = values[0][0]
        if len(values) > 1:
            step["auto"] = values[1][0]
    elif op in ("change_fixture_timeline", "play_fixture_gimmick",
                "stop_fixture_gimmick"):
        if values:
            step["fixture"] = values[0][0]
        if len(values) > 1:
            step["name"] = values[1][0]
        if len(values) > 2:
            step["value"] = values[2][0]
    elif op == "hide_emoticon":
        if values:
            step["who"] = values[0][0]
    elif op == "look_at_fixture":
        if values:
            step["fixture"] = values[0][0]
        if len(values) > 1:
            step["who"] = values[1][0]
    elif op not in ("wait_click", "hide_talk_window", "show_talk_window"):
        step["args"] = [value for value, _ in values]
    return step


def parse_script(text, tables, scalars=None, script_name=None, defaults=None,
                 _drop_ops=None):
    """Parse one direct-talk script and assert complete call accounting."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8-sig")
    else:
        text = (text or "").lstrip("﻿")
    calls = list(_calls(text, script_name))
    scalars = scalars or {}
    drop = set(_drop_ops or ())
    steps, voices = [], []
    for op, args in calls:
        if op in drop:
            continue
        step = _step(op, args, tables or {}, scalars)
        steps.append(step)
        if op in ("voice", "fixture_voice") and "cue" in step:
            voices.append(step["cue"])
    if len(steps) != len(calls):
        prefix = f"{script_name}: " if script_name else ""
        difference = len(calls) - len(steps)
        raise ValueError(f"{prefix}scenario split difference: {difference} calls")
    operations = Counter(step["op"] for step in steps)
    return {
        "steps": steps,
        "voices": voices,
        "callCount": len(calls),
        "operations": dict(operations),
    }


def _text_assets(bundle):
    """Return ``{TextAsset name: decoded script text}`` from a Unity bundle."""
    out = {}
    for obj in UnityPy.load(bundle).objects:
        if obj.type.name != "TextAsset":
            continue
        tree = obj.read_typetree()
        script = tree.get("m_Script", "")
        if isinstance(script, (bytes, bytearray)):
            script = bytes(script).decode("utf-8-sig", "replace")
        out[str(tree.get("m_Name", ""))] = script
    return out


def _asset_named(assets, name):
    if name in assets:
        return assets[name]
    suffixes = ("/" + name, "\\" + name)
    for asset, text in assets.items():
        if asset.endswith(suffixes):
            return text
    return None


def _tweet(row):
    if row is None:
        return None
    return {
        "id": row.get("id"),
        "text": row.get("text"),
        "motion": row.get("motionName"),
        "eye": row.get("expressionEyeName"),
        "mouth": row.get("expressionMouthName"),
    }


def _semantics(constant_assets, resolved):
    return {
        "selection": (
            "Only talks whose unit group has exactly one character and whose "
            "condition group contains no furniture condition are included."
        ),
        "steps": "steps are the source-order call stream; no runtime timestamp is inferred",
        "text": "text is preserved as decoded source text, including newline characters",
        "voiceCues": "voice cue names occur in scripts; audio bytes are not in the talk bundle",
        "tweet": "tweet is the separate text, motion, eye, and mouth pairing from the master tables",
        "constants": {
            "resolved": resolved,
            "sourceAssets": list(constant_assets),
            "unresolvedTokensPreserved": True,
        },
        "playbackSpeed": (
            "speed is the scripted value; playbackSpeed uses "
            f"{SPEED_ZERO_MEANS} when speed is zero"
        ),
    }


def extract_talks(master_source, talk_bundle, out_path, master_cache=None):
    """Write talks.json for single-character, non-furniture talks.

    *master_source* is a directory of master tables or a base URL to fetch them
    from; *master_cache* is where fetched tables are kept.
    """
    master = Master(master_source, cache_dir=master_cache)
    selected, filter_report = master.solo_talks()
    tweets = master.tweets()
    conditions = master.condition_types()
    assets = _text_assets(talk_bundle)
    constant_assets = [
        name for name in assets
        if name in ("defines.lua", "lib.lua")
        or name.endswith(("/defines.lua", "\\defines.lua", "/lib.lua", "\\lib.lua"))
    ]
    constant_text = "\n".join(assets[name] for name in constant_assets)
    tables = parse_constant_tables(constant_text)
    scalars = parse_constant_scalars(constant_text, tables)
    units, operations, all_voices = {}, Counter(), []
    total_steps = 0
    for item in selected:
        row, unit = item["talk"], item["unitId"]
        lua = row.get("lua", "")
        asset_name = lua if lua.endswith(".lua") else lua + ".lua"
        script = _asset_named(assets, asset_name)
        if script is None:
            raise LookupError(
                f"talk {row.get('id')}: script asset not found: {asset_name}"
            )
        parsed = parse_script(script, tables, scalars, script_name=asset_name)
        tweet_id = item.get("tweetId")
        if tweet_id is not None and tweet_id not in tweets:
            raise LookupError(f"talk {row.get('id')}: tweet not found: {tweet_id}")
        talk = {
            "talkId": row.get("id"),
            "lua": lua,
            "siteGroupId": row.get("mysekaiSiteGroupId"),
            "termId": row.get("mysekaiCharacterTalkTermId"),
            "conditions": list(conditions.get(
                row.get("mysekaiCharacterTalkConditionGroupId"), []
            )),
            "tweet": _tweet(tweets.get(tweet_id)) if tweet_id is not None else None,
            "voices": parsed["voices"],
            "steps": parsed["steps"],
        }
        units.setdefault(str(unit), {"talks": []})["talks"].append(talk)
        total_steps += len(parsed["steps"])
        operations.update(parsed["operations"])
        all_voices.extend(parsed["voices"])
    summary = {
        "talks": len(selected),
        "units": len(units),
        "steps": total_steps,
        "voiceCues": len(all_voices),
        "uniqueVoiceCues": len(set(all_voices)),
        "operations": dict(operations),
    }
    doc = {
        "version": 1,
        "semantics": _semantics(constant_assets, bool(constant_assets)),
        "units": units,
        "summary": summary,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    return {
        **summary,
        "filter": filter_report,
        "constantAssets": constant_assets,
        "constantTables": {key: len(value) for key, value in tables.items()},
    }
