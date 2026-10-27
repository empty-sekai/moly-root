"""Per-character communication performances, distinct from spoken dialogue.

The script's lifetime loop and participant-gated random prefix are retained.
Channel steps use the existing performance reader and its source library's
argument order. No dialogue, voice or translated text is invented here.
"""
from collections import Counter
import re

from core.jsonio import write_json
from core.master import Master
from chara import alone_actions
from chara.talks import _asset_named, _calls, _resolve, _strip_comments, _text_assets

PACKAGE = "mysekai__some_character_communication"
TABLE = "mysekaiCharacterTalkTogetherCommunications"


def parse_program(script, tables, scalars, defaults):
    text = _strip_comments(script).strip()
    loop = re.fullmatch(r"while\s+enable_communication\(\)\s+do\s*(.*?)\s*end", text, re.S)
    if not loop:
        raise ValueError("unsupported communication lifetime loop")
    body = loop[1]
    branches, random = [], None
    prefix = re.match(r"\s*rand\s*=\s*math\.random\(0,\s*99\)\s*", body)
    if prefix:
        random = {"min": 0, "max": 99}
        body = body[prefix.end():]
        branch = re.match(r"if\s+is_communicating\((\w+\.\w+)\)\s+and\s+rand\s*<\s*(\d+)\s+then(.*?)"
                          r"elseif\s*\(\s*rand\s*<\s*(\d+)\s*\)\s*then(.*?)\bend\b", body, re.S)
        if not branch:
            raise ValueError("unsupported communication random branch")
        participant, _ = _resolve(branch[1], tables, scalars)
        if not isinstance(participant, int):
            raise ValueError("unresolved communication participant")
        branches = [{"condition": {"participant": participant, "randomBelow": int(branch[2])}, "body": branch[3]},
                    {"condition": {"participant": None, "randomBelow": int(branch[4])}, "body": branch[5]}]
        body = body[branch.end():]

    def steps(fragment):
        if re.search(r"\b(?:if|elseif|else|end|while|for|repeat|function|return)\b", fragment):
            raise ValueError("unhandled communication control flow")
        allowed = set(alone_actions.CALL_OPS) | {"wait_time"}
        calls = list(_calls(fragment))
        # _calls deliberately scans the shared talk vocabulary. Also account
        # for every lexical function name so a future command cannot vanish.
        names = re.findall(r"\b([A-Za-z_]\w*)\s*\(", fragment)
        if any(name not in allowed for name in names) or len(names) != len(calls):
            raise ValueError("unsupported communication operation")
        result, elapsed = [], 0.0
        for op, args in calls:
            call = ("alone_wait_time(nil, " + ",".join(args) + ")" if op == "wait_time"
                    else op + "(" + ",".join(args) + ")")
            emitted, count = alone_actions._steps(call, tables, scalars, defaults)
            if count != 1:
                raise ValueError("communication call accounting mismatch")
            if op != "wait_time":
                who, _ = _resolve(args[0], tables, scalars)
                if not isinstance(who, int):
                    raise ValueError("unresolved communication actor")
                for step in emitted:
                    step["who"] = who
            for step in emitted:
                step["t"] = elapsed
                if step["op"] == "animation" and not isinstance(step["motion"], str):
                    raise ValueError("unresolved communication motion")
                if step["op"] == "wait":
                    if not isinstance(step["seconds"], (int, float)) or step["seconds"] < 0:
                        raise ValueError("invalid communication wait duration")
                    elapsed += step["seconds"]
                result.append(step)
        return result

    for branch in branches:
        branch["steps"] = steps(branch.pop("body"))
    tail = steps(body)
    if not any(step["op"] == "wait" and isinstance(step["seconds"], (int, float)) and step["seconds"] > 0
               for step in tail):
        raise ValueError("communication lifetime loop has no positive wait")
    return {"kind": "whileCommunication", "random": random, "branches": branches, "steps": tail}


def extract_communication_actions(master_source, bundle, out_path, master_cache=None):
    assets = _text_assets(bundle)
    defines = _asset_named(assets, "defines.lua")
    library = _asset_named(assets, "lib.lua")
    if defines is None or library is None:
        raise ValueError("communication package is missing its libraries")
    tables = alone_actions.parse_constant_tables(defines)
    scalars = alone_actions.parse_constant_scalars(defines, tables)
    defaults = alone_actions.parse_library_defaults(library)
    units, operations = {}, Counter()
    for row in Master(master_source, cache_dir=master_cache).table(TABLE):
        unit = str(row["gameCharacterUnitId"])
        if unit in units:
            raise ValueError("duplicate communication character")
        script = _asset_named(assets, row["lua"] + ".lua")
        if script is None:
            raise ValueError("missing communication script: " + row["lua"])
        program = parse_program(script, tables, scalars, defaults)
        units[unit] = {"id": row["id"], "lua": row["lua"], "program": program}
        for steps in [program["steps"], *(b["steps"] for b in program["branches"])]:
            operations.update(step["op"] for step in steps)
    summary = {"units": len(units), "steps": sum(operations.values()), "operations": dict(operations)}
    write_json(out_path, {"version": 1, "units": units, "summary": summary})
    return {**summary, "path": str(out_path)}
