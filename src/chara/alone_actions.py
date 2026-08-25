"""Alone-action performance data: which motion pairs with which facial pattern, and when.

Motion and facial state are independent channels — no motion name implies a facial
state.  The pairing lives in per-character performance scripts shipped as Lua text
assets, which drive three channels explicitly: motion by name, eye pattern by name,
mouth pattern by name, interleaved with waits.

Two script shapes appear in shipped content and both are parsed:

* time-gated — a loop whose scenarios each guard on an elapsed-time limit, a
  probability, and an optional motion slot used for de-duplication;
* random-branch — a loop that draws a number and picks one mutually exclusive
  branch, followed by a shared tail that always runs.

Timestamps are the authored (nominal) timeline: the cumulative sum of wait
durations.  The runtime waits with integer-millisecond delays on a coroutine and
starts motions without awaiting them, so real switch times carry quantization and
frame-scheduling error.  Treat ``t`` as an ordering-plus-nominal-offset contract,
not as a frame-accurate schedule.
"""
import json
import os
import re

import UnityPy

CALL_OPS = ("change_animation", "change_npc_eye", "change_npc_mouth",
            "alone_wait_time", "emoticon", "hide_emoticon")
CALL_RE = re.compile(r"\b(" + "|".join(CALL_OPS) + r")\s*\(")
TABLE_RE = re.compile(r"(?m)^\s*(\w+)\s*=\s*\{")
ENTRY_RE = re.compile(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|-?[\d.]+)")
BRANCH_RE = re.compile(r"(?m)^\s*(?:if|elseif)\s*\(?\s*(\w+)\s*<\s*(\d+)")
GATE_RE = re.compile(
    r"(?m)^\s*if\s+hasTimeElapsed\((\w+)\)\s*and\s+shouldExecuteWithProbability\((\w+)\)"
    r"(?:\s*and\s+canSelectMotion\((\w+)\s*,)?")
LOCAL_RE = re.compile(r"(?m)^\s*local\s+(\w+)\s*=\s*(-?[\d.]+)")
SCALAR_RE = re.compile(r"(?m)^(\w+)\s*=\s*(-?[\d.]+|\w+\.\w+)\s*(?:--.*)?$")
# Motion-phase suffix letters, keyed by the numeric phase constants the scripts use.
PHASE_SUFFIX = {0: "S", 1: "L", 2: "E", 3: "O"}
UNIT_RE = re.compile(r"^(?:.*[/\\])?character_alone_action_(\d+)(?:\.lua)?$")
DEFINES_NAME = "defines.lua"
LIBRARY_NAME = "lib.lua"
# The runtime substitutes this playback speed whenever a script passes zero.
SPEED_ZERO_MEANS = 1.0
# Crossfade used when a script omits the argument; read from the shipped library
# when it is present, since the library is what applies the default.
DEFAULT_BLEND_SECONDS = 0.5
BLOCK_TOKENS = re.compile(r"\b(if|for|while|function|do|then|end|elseif|else)\b")


def _strip_comments(text):
    """Remove line comments without touching string literals."""
    out = []
    for line in text.split("\n"):
        res, i, quote = [], 0, None
        while i < len(line):
            c = line[i]
            if quote:
                res.append(c)
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
                res.append(c)
            elif c == "-" and line[i + 1:i + 2] == "-":
                break
            else:
                res.append(c)
            i += 1
        out.append("".join(res))
    return "\n".join(out)


def parse_constant_scalars(text, tables):
    """Top-level ``name = value`` bindings, including aliases of table members.

    The scripts pass phase constants as bare globals (an alias of a table member),
    so resolving only tables would silently drop the motion phase.
    """
    text = _strip_comments(text)
    scalars = {}
    for name, raw in SCALAR_RE.findall(text):
        if name in tables:
            continue
        if re.fullmatch(r"-?[\d.]+", raw):
            scalars[name] = float(raw) if "." in raw else int(raw)
            continue
        table, _, key = raw.partition(".")
        if table in tables and key in tables[table]:
            scalars[name] = tables[table][key]
    return scalars


def parse_constant_tables(text):
    """``Name = { key = value, ... }`` blocks -> {table name: {key: value}}."""
    text = _strip_comments(text)
    tables, i = {}, 0
    while True:
        head = TABLE_RE.search(text, i)
        if not head:
            return tables
        depth, j = 1, head.end()
        while j < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        entries = {}
        for key, raw in ENTRY_RE.findall(text[head.end():j - 1]):
            entries[key] = (raw[1:-1] if raw[0] in "\"'"
                            else (float(raw) if "." in raw else int(raw)))
        tables[head.group(1)] = entries
        i = j


def _args(text, open_paren):
    """Balanced argument list starting at ``text[open_paren] == '('``."""
    depth, i, cur, args = 0, open_paren, [], []
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif c == ")":
            depth -= 1
            if depth == 0:
                args.append("".join(cur).strip())
                return [a for a in args if a], i + 1
        elif c == "," and depth == 1:
            args.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    raise ValueError("unterminated call argument list")


def _resolve(token, tables, scalars=None):
    """``Table.key`` / bare alias -> (concrete value, source name); literals -> (value, None)."""
    dotted = re.fullmatch(r"(\w+)\.(\w+)", token.strip())
    if dotted and dotted.group(1) in tables:
        table, key = dotted.group(1), dotted.group(2)
        if key not in tables[table]:
            raise KeyError(f"{table}.{key} is not defined in the constant tables")
        return tables[table][key], f"{table}.{key}"
    tok = token.strip()
    if re.fullmatch(r"-?[\d.]+", tok):
        return (float(tok) if "." in tok else int(tok)), None
    if tok in ("true", "false"):
        return tok == "true", None
    if len(tok) >= 2 and tok[0] in "\"'":
        return tok[1:-1], None
    if scalars and tok in scalars:
        return scalars[tok], tok
    return tok, None                 # an unresolved local; kept verbatim


def parse_library_defaults(text):
    """Defaults the performance library applies to omitted arguments."""
    blend = dict(LOCAL_RE.findall(text or "")).get("animation_blend_time")
    return {"blend": float(blend) if blend is not None else DEFAULT_BLEND_SECONDS}


def _steps(body, tables, scalars=None, defaults=None):
    """Ordered channel writes with a nominal cumulative timestamp."""
    defaults = defaults or {"blend": DEFAULT_BLEND_SECONDS}
    steps, t, i, count = [], 0.0, 0, 0
    while True:
        call = CALL_RE.search(body, i)
        if not call:
            return steps, count
        op = call.group(1)
        args, i = _args(body, call.end() - 1)
        count += 1
        values = [_resolve(a, tables, scalars) for a in args]
        if op == "alone_wait_time":
            seconds = values[1][0] if len(values) > 1 else 0.0
            steps.append({"t": round(t, 4), "op": "wait", "seconds": seconds})
            if isinstance(seconds, (int, float)):
                t += float(seconds)
            continue
        step = {"t": round(t, 4)}
        if op == "change_animation":
            step["op"] = "animation"
            step["motion"], step["alias"] = values[1]
            speed = values[2][0] if len(values) > 2 else None
            step["speed"] = speed
            # A consumer that plays `speed` literally would freeze the character:
            # every shipped call passes zero, which the runtime reads as 1.0.
            step["playbackSpeed"] = (SPEED_ZERO_MEANS
                                     if not isinstance(speed, (int, float)) or speed == 0
                                     else float(speed))
            step["playEndMotion"] = bool(values[4][0]) if len(values) > 4 else False
            step["blend"] = values[5][0] if len(values) > 5 else defaults["blend"]
            if len(values) > 6:
                phase, source = values[6]
                step["phase"] = PHASE_SUFFIX.get(phase, phase)
                step["phaseSource"] = source
        elif op in ("change_npc_eye", "change_npc_mouth"):
            step["op"] = "eye" if op.endswith("eye") else "mouth"
            step["pattern"], step["alias"] = values[1]
        elif op == "emoticon":
            step["op"] = "emoticon"
            step["name"] = values[1][0]
            if len(values) > 3:
                step["showSeconds"] = values[3][0]
        else:
            step["op"] = "hideEmoticon"
        steps.append(step)


def _block_end(text, start):
    """End of the branch body that starts at ``start`` (after its ``then``)."""
    depth, i = 1, start
    while i < len(text):
        tok = BLOCK_TOKENS.search(text, i)
        if not tok:
            return len(text)
        word = tok.group(1)
        if word in ("if", "for", "while", "function"):
            depth += 1
        elif word == "end":
            depth -= 1
            if depth == 0:
                return tok.start()
        elif word in ("elseif", "else") and depth == 1:
            return tok.start()
        i = tok.end()
    return len(text)


def parse_script(text, tables, scalars=None, defaults=None):
    """One performance script -> scenarios, shared tail, and its call count.

    Raises when the scenario split would lose calls: every parsed call must be
    accounted for exactly once, otherwise mutually exclusive branches could be
    silently concatenated into one bogus timeline.
    """
    text = _strip_comments(text)
    expected = len(CALL_RE.findall(text))
    consts = {k: (int(v) if re.fullmatch(r"-?\d+", v) else float(v))
              for k, v in LOCAL_RE.findall(text)}
    scenarios, seen, low = [], 0, 0
    spans = []
    for match in BRANCH_RE.finditer(text):
        then = text.find("then", match.end())
        end = _block_end(text, then + 4)
        spans.append((then + 4, end))
        steps, count = _steps(text[then + 4:end], tables, scalars, defaults)
        high = int(match.group(2))
        scenarios.append({
            "id": f"{match.group(1)}<{high}", "kind": "randomBranch",
            "trigger": {"kind": "randomBranch", "low": low, "high": high,
                        "weight": round((high - low) / 100.0, 4)},
            "steps": steps})
        low, seen = high, seen + count
    for match in GATE_RE.finditer(text):
        then = text.find("then", match.end())
        end = _block_end(text, then + 4)
        spans.append((then + 4, end))
        steps, count = _steps(text[then + 4:end], tables, scalars, defaults)
        limit, prob, slot = match.group(1), match.group(2), match.group(3)
        scenarios.append({
            "id": slot or f"gate@{match.start()}", "kind": "timeGated",
            "trigger": {"kind": "timeGated", "timeLimitName": limit,
                        "timeLimitSeconds": consts.get(limit),
                        "probabilityName": prob, "probability": consts.get(prob),
                        "motionSlot": slot,
                        "slotMemorySeconds": consts.get("memoryDuration")},
            "steps": steps})
        seen += count
    masked = list(text)
    for start, end in spans:
        masked[start:end] = " " * (end - start)
    tail, tail_count = _steps("".join(masked), tables, scalars, defaults)
    seen += tail_count
    if seen != expected:
        raise ValueError(f"scenario split accounted for {seen} of {expected} calls")
    return {"scenarios": scenarios, "tail": {"steps": tail},
            "constants": consts, "callCount": expected}


def _text_assets(bundle):
    """{asset name: text} for every TextAsset in the bundle."""
    out = {}
    for obj in UnityPy.load(bundle).objects:
        if obj.type.name != "TextAsset":
            continue
        tree = obj.read_typetree()
        script = tree.get("m_Script", "")
        if isinstance(script, (bytes, bytearray)):
            script = script.decode("utf-8", "replace")
        out[str(tree.get("m_Name", ""))] = script
    return out


def extract_alone_actions(bundle):
    """Performance data for every character in an alone-action bundle.

    Returns a document with the resolved constant-table sizes, per-character
    scenarios, and a motion-to-facial co-occurrence summary.  Character keys are
    the numeric identifiers carried by the script asset names.
    """
    assets = _text_assets(bundle)
    defines = next((v for k, v in assets.items()
                    if k == DEFINES_NAME or k.endswith("/" + DEFINES_NAME)), None)
    if defines is None:
        raise LookupError(f"{DEFINES_NAME} is missing from the bundle")
    tables = parse_constant_tables(defines)
    scalars = parse_constant_scalars(defines, tables)
    library = next((v for k, v in assets.items()
                    if k == LIBRARY_NAME or k.endswith("/" + LIBRARY_NAME)), None)
    defaults = parse_library_defaults(library)
    names = {v: k for k, v in tables.get("Characters", {}).items()}
    units, eye_pairs, mouth_pairs = {}, {}, {}
    totals = {"scenarios": 0, "steps": 0, "calls": 0}
    for name in sorted(assets):
        matched = UNIT_RE.match(name)
        if not matched:
            continue
        unit = int(matched.group(1))
        parsed = parse_script(assets[name], tables, scalars, defaults)
        parsed["unitId"] = unit
        parsed["characterKey"] = names.get(unit)
        parsed["asset"] = name
        units[str(unit)] = parsed
        totals["scenarios"] += len(parsed["scenarios"])
        totals["calls"] += parsed["callCount"]
        for block in parsed["scenarios"] + [parsed["tail"]]:
            totals["steps"] += len(block["steps"])
            eye = mouth = None
            for step in block["steps"]:
                if step["op"] == "eye":
                    eye = step["pattern"]
                elif step["op"] == "mouth":
                    mouth = step["pattern"]
                elif step["op"] == "animation":
                    for table, key in ((eye_pairs, eye), (mouth_pairs, mouth)):
                        if key is None:
                            continue
                        bucket = table.setdefault(step["motion"], {})
                        bucket[key] = bucket.get(key, 0) + 1
    if not units:
        raise LookupError("the bundle holds no per-character performance scripts")
    return {
        "version": 2,
        "semantics": {
            "timeAxis": "nominal",
            "timeAxisNote": ("t is the cumulative sum of wait durations, i.e. the authored "
                             "timeline; the runtime waits in integer milliseconds on a "
                             "coroutine and starts motions without awaiting them"),
            "eyePatternField": "eye table PatternName",
            "mouthPatternField": "mouth table Name",
            "atlasIndexSource": ("eye OpenEyeIndex / CloseEyeIndex and mouth "
                                 "OpenLipSyncIndex / CloseLipSyncIndex of the named row"),
            "channelIndependence": ("motion and facial writes are separate calls; no motion "
                                    "name implies a facial state"),
            "motionName": ("the key of a shared motion-library index entry; play the segment "
                           "named by `phase` when present, otherwise the entry's own phases"),
            "playbackSpeed": (f"effective playback rate: `speed` is what the script passes and "
                              f"the runtime reads 0 as {SPEED_ZERO_MEANS}, so play at "
                              f"`playbackSpeed`"),
            "blend": ("crossfade seconds into the new motion; recorded for every animation "
                      "step, taken from the performance library's default when the script "
                      "omits it"),
            "playEndMotion": ("when true the runtime follows the motion with its End segment; "
                              "false unless the script asks for it"),
            "droppedArgument": ("the library never forwards the 4th positional argument of its "
                                "motion call, so it is not recorded here"),
        },
        "constantTables": {k: len(v) for k, v in tables.items()},
        "constantScalars": scalars,
        "units": units,
        "summary": {**totals, "units": len(units),
                    "motionEyePairs": eye_pairs, "motionMouthPairs": mouth_pairs},
    }


def write_alone_actions(bundle, out_path):
    """Extract and write ``alone-actions.json``; returns a report dict."""
    doc = extract_alone_actions(bundle)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1, allow_nan=False)
    return {"path": out_path, "units": doc["summary"]["units"],
            "scenarios": doc["summary"]["scenarios"], "steps": doc["summary"]["steps"],
            "constantTables": doc["constantTables"]}
