"""Built-in stages call the same domain readers as direct exports.

Only inputs consumed by the selected stage enter its key. Source-module
fingerprints also separate unreleased edits; versions remain the declared
semantic contract and installed dependency versions identify byte readers.
"""
from importlib.metadata import version
import json
from pathlib import Path

from core.content import canonical_bytes, content_hash, document_hash
from source.snapshot import file_input
from .contract import describe

VERSIONS = {"talks.extract": 1, "communication.extract": 1, "text.catalog": 1, "text.official": 1, "text.runtime": 1}
MODULES = {
    "communication.extract": ("chara/communication_actions.py", "chara/alone_actions.py", "chara/talks.py", "core/master.py", "core/jsonio.py", "core/unity.py"),
    "talks.extract": ("chara/talks.py", "chara/common_talks.py", "perf/fixture_talks.py",
                      "core/master.py", "core/assets/router.py", "core/unity.py"),
    "text.catalog": ("chara/text_catalog.py", "core/jsonio.py"),
    "text.official": ("chara/text_catalog.py",),
    "text.runtime": ("pipeline/text_product.py",),
}
TALK_TABLES = (
    "mysekaiCharacterTalks", "mysekaiGameCharacterUnitGroups",
    "mysekaiCharacterTalkConditions", "mysekaiCharacterTalkConditionGroups",
    "mysekaiCharacterTalkPreActions", "mysekaiCharacterTalkTweets",
)


def atoms(stage):
    root = Path(__file__).resolve().parents[1]
    modules = (*MODULES[stage], "pipeline/stages.py", "core/content.py")
    result = {"implementation": document_hash({name: content_hash((root / name).read_bytes())
                                                for name in modules})}
    if stage in {"talks.extract", "communication.extract"}:
        result["unitypy"] = version("UnityPy")
    return result


def task(stage, subject, inputs, **params):
    return describe(stage, VERSIONS[stage], subject, inputs, params=params, atoms=atoms(stage))


def talk_task(master, bundle, lib, *, family="general", unity_version="2022.3.62f3"):
    if family not in {"general", "fixture"}:
        raise ValueError("unknown conversation family")
    inputs = [file_input("bundle", bundle), file_input("lib", lib)]
    tables = list(TALK_TABLES)
    if family == "fixture":
        tables += ["mysekaiCharacterTalkNoTalkMysekaiFixtureActions", "mysekaiFixtures"]
    else:
        # Optional tables are part of the input identity when present. Their
        # absence is represented by the table list, not a made-up empty file.
        tables += [name for name in ("birthdayPartyScenarios", "mysekaiCharacterTalkSomeCharacterTalks", "mysekaiTutorialTalks")
                   if (Path(master) / (name + ".json")).is_file()]
    inputs += [file_input("master/" + name, Path(master) / (name + ".json")) for name in tables]
    return task("talks.extract", family, inputs, family=family, unityVersion=unity_version)


def catalog_task(corpora, *, region, source_revision, previous=None):
    inputs = [file_input(f"corpus/{i:04d}", path) for i, path in enumerate(corpora)]
    if previous:
        inputs.append(file_input("previous", previous))
    return task("text.catalog", region, inputs, region=region, sourceRevision=source_revision)


def communication_task(master, bundle, *, unity_version="2022.3.62f3"):
    return task("communication.extract", "characters", [file_input("bundle", bundle),
        file_input("master/mysekaiCharacterTalkTogetherCommunications", Path(master) / "mysekaiCharacterTalkTogetherCommunications.json")],
        unityVersion=unity_version)


def official_task(source, donor, *, locale):
    return task("text.official", locale,
                [file_input("source", source), file_input("donor", donor)], locale=locale)


def runtime_task(source, overlays=()):
    inputs = [file_input("source", source)]
    inputs += [file_input(f"overlay/{i:04d}", path) for i, path in enumerate(overlays)]
    return task("text.runtime", "display", inputs)


def validate_installed(value):
    name = value["stage"]["name"]
    if name not in VERSIONS or value["stage"]["version"] != VERSIONS[name]:
        raise ValueError("task stage/version is not installed")
    if value["atoms"] != atoms(name):
        raise ValueError("task implementation differs from this installation; regenerate the plan")
    expected = {"talks.extract": {"family", "unityVersion"}, "communication.extract": {"unityVersion"},
                "text.catalog": {"region", "sourceRevision"},
                "text.official": {"locale"}, "text.runtime": set()}
    if set(value["params"]) != expected[name]:
        raise ValueError("task parameters differ from the stage contract")
    roles = {item["role"] for item in value["inputs"]}
    if name == "communication.extract":
        if roles != {"bundle", "master/mysekaiCharacterTalkTogetherCommunications"}:
            raise ValueError("communication requires bundle and source master")
    elif name == "talks.extract":
        if value["params"]["family"] not in {"general", "fixture"}:
            raise ValueError("unknown conversation family")
        permitted = set(TALK_TABLES) | {"birthdayPartyScenarios", "mysekaiCharacterTalkSomeCharacterTalks", "mysekaiTutorialTalks",
                                      "mysekaiFixtures", "mysekaiCharacterTalkNoTalkMysekaiFixtureActions"}
        if not {"bundle", "lib"} <= roles or any(
                role not in {"bundle", "lib"} and role not in {"master/" + t for t in permitted}
                for role in roles):
            raise ValueError("unknown or missing talk input roles")
    elif name == "text.official" and roles != {"source", "donor"}:
        raise ValueError("official text requires source and donor")
    elif name in {"text.catalog", "text.runtime"}:
        prefix = "corpus/" if name == "text.catalog" else "overlay/"
        singles = {"previous"} if name == "text.catalog" else {"source"}
        if any(role not in singles and not (role.startswith(prefix) and role[len(prefix):].isdigit())
               for role in roles):
            raise ValueError("unknown stage input role")
        if (name == "text.runtime" and "source" not in roles
                or name == "text.catalog" and not any(role.startswith(prefix) for role in roles)):
            raise ValueError("stage has no source inputs")


def execute(value, inputs, workspace):
    name, params = value["stage"]["name"], value["params"]
    if name == "communication.extract":
        from chara.communication_actions import extract_communication_actions, TABLE
        from core.unity import configure_fallback_unity_version
        configure_fallback_unity_version(params["unityVersion"])
        master = workspace / "master"
        master.mkdir()
        (master / (TABLE + ".json")).write_bytes(inputs["master/" + TABLE])
        bundle, out = workspace / "bundle", workspace / "communication-actions.json"
        bundle.write_bytes(inputs["bundle"])
        extract_communication_actions(master, bundle, out)
        return {"communication-actions.json": out.read_bytes()}
    if name == "talks.extract":
        from core.unity import configure_fallback_unity_version
        from chara.talks import extract_talks
        from perf.fixture_talks import extract_fixture_talks
        configure_fallback_unity_version(params["unityVersion"])
        master = workspace / "master"
        master.mkdir()
        for role, data in inputs.items():
            if role.startswith("master/"):
                (master / (role.removeprefix("master/") + ".json")).write_bytes(data)
        bundle, lib, out = workspace / "bundle", workspace / "lib", workspace / "talks.json"
        bundle.write_bytes(inputs["bundle"])
        lib.write_bytes(inputs["lib"])
        reader = extract_talks if params["family"] == "general" else extract_fixture_talks
        reader(str(master), str(bundle), str(out), lib_bundle=str(lib))
        doc = json.loads(out.read_bytes())
        if params["family"] == "fixture":
            summary = doc["summary"]
            if summary.get("missingScripts") or summary.get("unknownOperations"):
                raise ValueError("fixture conversation extraction is incomplete")
        return {("talks.json" if params["family"] == "general" else "fixture-talks/talks.json"):
                out.read_bytes()}
    if name == "text.catalog":
        from chara.text_catalog import build_catalog
        doc = build_catalog([json.loads(data) for role, data in sorted(inputs.items()) if role.startswith("corpus/")],
                            region=params["region"], source_revision=params["sourceRevision"],
                            previous=json.loads(inputs["previous"]) if "previous" in inputs else None)
        return {"text-source.json": canonical_bytes(doc)}
    if name == "text.official":
        from chara.text_catalog import match_official
        doc = match_official(json.loads(inputs["source"]), json.loads(inputs["donor"]), locale=params["locale"])
        return {f"official-{params['locale']}.json": canonical_bytes(doc)}
    if name == "text.runtime":
        from .text_product import build_product
        return build_product(json.loads(inputs["source"]),
                             [json.loads(data) for role, data in sorted(inputs.items()) if role.startswith("overlay/")])
    raise ValueError("unknown stage")
