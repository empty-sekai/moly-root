"""The rest of a world's sound: talk voices, furniture effects, music, part voices.

The phenomena job extracts only the sound packages master rows point at — the
site and phenomenon music, and one shared ambience package.  Everything else a
world ships was never requested by any job, which is a gap in the *asking*, not
in the packages: the manifest lists them, the decrypted tree holds them, and the
archives inside decode like any other.  This job asks for the rest, and it asks
the way the consumers do.

Talk voices are the case that must not be done by rote.  A talk names its voice
cues — ``voice_`` followed by the talk's script name and a line/variant pair —
and each cue lives in the package named after the script name, so the consumer
never asks for a package; it plays cues.  This job therefore walks the talk
corpus, maps every voice cue to its package, and decodes exactly the cues the
corpus names — a package may hold several cues the corpus never uses, and those
stay undecoded rather than blind-extracted.  Cues whose package the manifest
does not list are reported by name: that is a data gap upstream, not a decoding
failure, and naming it beats papering over it.

The other three families are extracted whole.  Furniture effects (``se``), the
music packages the master rows do not name (jukebox tracks, tutorial songs,
variants), and the part-voice packages — both sides of what a gate-passing
speaker loads, the mysekai side and the scenario side, gated to one character
kind at load time so a missing package for the other kind is the truth of the
construction, not an extraction gap — all answer to "everything in the family",
not to a cue list.  A package the manifest marks as part of the player build
is not on the download path at all: asking for it still counts, but the
failure names that instead of "not in the decrypted tree".

Output lands in the same ``audio/`` library the phenomena job writes, in the
same per-package shape, and the loop sidecar is merged: packages already on
disk keep their entries, new ones are appended.  The corpus ledger written
alongside accumulates the same way — counts add up across runs, named gaps
merge by name, and a family a run asked nothing about keeps its previous
summary — so a consumer can tell "never asked" from "asked and missing" over
everything on disk, not just the last run.  A talk corpus arrives in either of
the two shapes the repo's talk extractors write, talks grouped under ``units``
or a flat ``talks`` list (the fixture corpus), and both name voice cues the same way.
A talk document may also carry a ``birthdayParty`` block: the party scripts'
cues are the same form, but they live in the birthday voice bank each script
names, so they are asked of that package rather than of the plain one.
"""
import json
from pathlib import Path

from core.jsonio import dumps
from core.atomic import PUBLIC_FILE_MODE, write_json
from core.assets.router import AMBIENCE_PACKAGE
from .audio import DECODER, Library, NO_CUE, TRANSCODER, archive_bytes

# Package families, in manifest form (slash-separated) and store form (the
# flattened names the decrypted tree uses).
TALK_VOICE = "mysekai/talk/voice/"
# The part-voice family is both sides of a gate-passing speaker's load: the
# mysekai packages under mysekai/talk/part_voice/, and the scenario-side ones
# under sound/scenario/voice/part_voice_*.  An older scenario path
# (sound/scenario/part_voice/) also exists in the manifest, but the talk
# chain never names it, so it is not part of this family.
PART_VOICE = ("mysekai/talk/part_voice/", "sound/scenario/voice/part_voice_")
SE = "mysekai/sound/se/"
BGM = "mysekai/sound/bgm/"

# A talk voice cue is ``voice_<script name>_<line>_<variant>``; the package is
# named after the script name alone.
CUE_PREFIX = "voice_"

NO_PACKAGE = "no package for this cue's talk script in the manifest"
NO_BUNDLE = "package is not in the decrypted tree"
BUILT_IN = ("the manifest marks this package as part of the player build: it "
            "is not on the download path")
NO_ARCHIVE = "package holds no audio archive"

# A run walks a thousand-plus packages; the loop sidecar is rewritten every
# so often so a stopped run resumes from what is already on disk.
CHECKPOINT_EVERY = 50
# Old corpus runs returned after the first TextAsset. A bundle can contain
# several ACBs (e.g. toaster embeds tea_set and toaster), each still source-owned.
ARCHIVE_SET_REVISION = 2

SEMANTICS = {
    "families": ("per family: `requested` packages against the manifest "
                 "(talk voices instead against talks.json), `succeeded` "
                 "decoded ones, `failed` the named remainder with reasons"),
    "talkVoices": ("one entry per package the talk corpus names, carrying the "
                   "cues asked for; only those cues are decoded, so a package "
                   "holding more cues than the corpus names stays partially "
                   "undecoded on purpose"),
    "uncovered": ("cues whose talk script has no package in the manifest: a "
                  "data gap to fix upstream, named rather than swallowed"),
    "partVoiceCues": ("the talk corpus also names part-voice cues; this is "
                      "whether the part-voice packages on disk — both sides "
                      "of the family — answer them, as a check only: the "
                      "packages themselves are extracted whole, the "
                      "unanswered names accumulate across runs, and a "
                      "package on disk is the only thing that clears one"),
    "loop": ("the loop sidecar is shared with the phenomena job: existing "
             "entries keep their places, this job's are appended"),
    "corpus": ("the corpus ledger accumulates across runs the way the loop "
               "sidecar does: a family this run asked nothing about keeps its "
               "previous summary, a family it did ask for adds this run's "
               "counts to the previous ones and merges its named entries by "
               "name with this run's reading winning, and the part-voice "
               "check keeps every unanswered name any run has found until a "
               "package on disk answers it — the ledger describes the corpus "
               "on disk, not the last run"),
    "inPackage": ("counts taken from the archives' own reports; a stream "
                  "without a `wav` path failed to decode and says why"),
}


def voice_stem(cue):
    """The talk script name a voice cue belongs to: drop ``voice_`` and the
    trailing line/variant pair."""
    body = cue[len(CUE_PREFIX):] if cue.startswith(CUE_PREFIX) else cue
    return body.rsplit("_", 2)[0]


def _corpus_talks(document):
    """Every talk entry of a corpus, whichever of the two shapes the repo's
    talk extractors write: talks grouped under ``units`` by character, or a
    flat ``talks`` list (the fixture corpus)."""
    for unit in (document.get("units") or {}).values():
        yield from unit.get("talks") or []
    yield from document.get("talks") or []


def voice_requests(document):
    """Voice cues of a talk corpus, grouped into package requests.

    Returns ``(requests, cues)``: a mapping from flattened package name to the
    sorted cues asked of it, and the sorted set of cues themselves.
    """
    cues = set()
    requests = {}
    for talk in _corpus_talks(document):
        for cue in talk.get("voices") or []:
            if cue.startswith(CUE_PREFIX):
                cues.add(cue)
                script = talk.get("lua")
                # Vocal variants can end in e.g. _023_street or _002_b.
                # The authored script identity separates it from the suffix;
                # stripping a fixed number of underscore fields does not.
                stem = script if isinstance(script, str) and cue.startswith(
                    CUE_PREFIX + script + "_") else voice_stem(cue)
                requests.setdefault(TALK_VOICE + stem, set()).add(cue)
    # A party script's cues live in the bank the talk engine loads under the
    # Birthday context, not in the plain one, and the talk document names that
    # bank per script; it is read from there rather than assumed from a prefix.
    party = (document.get("birthdayParty") or {}).get("talks") or {}
    for script, talk in party.items():
        bank = talk.get("voiceBank")
        for cue in talk.get("voices") or []:
            if not cue.startswith(CUE_PREFIX):
                continue
            if not isinstance(bank, str) or not bank:
                raise ValueError(f"party script {script} names voice cues but "
                                 f"no voice bank")
            cues.add(cue)
            requests.setdefault(bank, set()).add(cue)
    requests = {name: sorted(values) for name, values in requests.items()}
    return requests, sorted(cues)


def prefix_packages(names, prefix, flat_prefix, exclude=()):
    """Family members of a manifest, as flattened store names."""
    skipped = {name.replace("/", "__") for name in exclude}
    return sorted(name.replace("/", "__") for name in names
                  if name.startswith(prefix)
                  and name.replace("/", "__") not in skipped)


def _existing_packages(audio_root):
    """Package names the loop sidecar already carries, in file order."""
    path = Path(audio_root) / "loop.json"
    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    return [entry.get("package") for entry in document.get("packages") or []
            if entry.get("package")]


def _extract_one(store, library, flat, cues, failures,
                 absent_reason=NO_BUNDLE):
    """One package into the library; ``False`` when nothing could be read."""
    package = store.package(flat, record_missing=False)
    if package is None:
        failures.append({"package": flat, "reason": absent_reason})
        return False
    try:
        archives, seen, names = [], set(), set()
        for asset_name, record, path_id in package.contents:
            identity = (id(record), path_id)
            if record.kinds.get(path_id) != "TextAsset" or identity in seen:
                continue
            seen.add(identity)
            tree = record.tree(path_id)
            name = str(tree.get("m_Name", "") or asset_name).rsplit(".", 1)[0]
            if name in names:
                raise ValueError(f"distinct audio archives share an output name in {flat}: {name}")
            names.add(name)
            archives.append((name, archive_bytes(tree.get("m_Script"))))
        found, entries = set(), []
        for index, (name, payload) in enumerate(archives):
            # An embedded archive may share its name with another package's
            # different ACB. Never overwrite that package's bytes or waveforms.
            entry = library.add(flat, name, payload, cues, namespace=flat, report_missing=False)
            entry.update(archiveSetRevision=ARCHIVE_SET_REVISION,
                         archiveSetCount=len(archives), archiveSetIndex=index)
            entries.append(entry)
            found.update(stream.get("cue") for stream in entry["streams"])
        # A requested voice may live in the second ACB. Absence is a package
        # result, not one diagnostic for every sibling archive without that cue.
        if entries and library.decoder:
            for cue in sorted(set(cues or ()) - found):
                library.unsupported.append({"package": flat, "cue": cue, "reason": NO_CUE})
                entries[-1]["streams"].append({"cue": cue, "error": NO_CUE})
        if not archives:
            failures.append({"package": flat, "reason": NO_ARCHIVE})
        return bool(archives)
    finally:
        store.forget(flat)


def _family_summary(requested, succeeded, failures, extra=None):
    summary = {"requested": requested, "succeeded": succeeded,
               "failed": failures}
    if extra:
        summary.update(extra)
    return summary


def _merge_loop(audio_root, library):
    """Append this run's packages to the shared loop sidecar, keeping every
    entry already on disk in its place."""
    document = {"status": library.status, "decoder": DECODER,
                "decoderPresent": bool(library.decoder),
                "transcoder": TRANSCODER,
                "transcoderPresent": bool(library.transcoder),
                "packages": library.packages}
    path = Path(audio_root) / "loop.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        known = {entry.get("package") for entry in document["packages"]}
        kept = [entry for entry in existing.get("packages") or []
                if entry.get("package") not in known]
        document["packages"] = kept + document["packages"]
    if document["packages"]:
        write_json(path, document, mode=PUBLIC_FILE_MODE)
        document["file"] = "audio/loop.json"
    return document


# Count fields of a family summary that accumulate across runs.  `requested`
# and `requestedCues` re-count a corpus that is run again; `succeeded` and
# `decodedCues` do not, because a package already on disk is never re-decoded.
COUNTS = ("requested", "succeeded", "requestedCues", "decodedCues")


def _merge_by_name(prior, current, key):
    """Two named-entry lists as one sorted list; this run's reading of a
    name wins."""
    merged = {entry.get(key): entry for entry in prior}
    merged.update({entry.get(key): entry for entry in current})
    return [merged[name] for name in sorted(merged)]


def _family_asked(family):
    """Whether the run asked anything of this family."""
    return bool(family.get("requested") or family.get("requestedCues")
                or family.get("failed") or family.get("missingPackages"))


def _merge_family(prior, current):
    """A family summary as an accumulating account: the count fields add up
    across runs, the named remainders merge by name."""
    merged = dict(current)
    for field in COUNTS:
        if isinstance(prior.get(field), int) and isinstance(current.get(field), int):
            merged[field] = prior[field] + current[field]
    for field, name in (("failed", "package"), ("missingPackages", "package"),
                        ("uncovered", "cue")):
        if field in prior or field in current:
            merged[field] = _merge_by_name(prior.get(field) or [],
                                           current.get(field) or [], name)
    return merged


def _merge_part_voice(prior, current, answered_on_disk):
    """The part-voice check as an accumulating account: the counts describe
    the latest corpus, a name stays on the books from the run that found it
    unanswered, and a package on disk is the only thing that clears one."""
    if not current.get("requested") and not current.get("missing"):
        merged = dict(prior)          # this corpus names no part-voice cues
    else:
        merged = dict(current)
        merged["missing"] = sorted(
            set(prior.get("missing") or []) | set(current.get("missing") or []))
    # The ledger describes the corpus on disk, not the runs that found gaps:
    # a cue a package on disk answers is no longer missing, whichever run
    # booked it.
    merged["missing"] = [cue for cue in merged.get("missing") or []
                         if cue not in answered_on_disk]
    return merged


def _merge_corpus(path, document, answered_on_disk):
    """Fold this run's account into the corpus ledger already on disk.

    A family this run asked nothing about keeps its previous summary; a
    family it did ask for adds this run's counts and merges its named
    entries by name.  The part-voice check keeps every unanswered name any
    run has found, minus the ones packages on disk now answer.
    """
    prior = json.loads(path.read_text(encoding="utf-8"))
    merged = dict(document)
    families = dict(document["families"])
    for name, family in families.items():
        old = (prior.get("families") or {}).get(name)
        if old is None:
            continue
        families[name] = (old if not _family_asked(family)
                          else _merge_family(old, family))
    merged["families"] = families
    old_parts = prior.get("partVoiceCues")
    if old_parts is not None:
        merged["partVoiceCues"] = _merge_part_voice(old_parts,
                                                    document["partVoiceCues"],
                                                    answered_on_disk)
    return merged


def extract_audio_corpus(talks_path, manifest_path, bundle_root, out_dir,
                         decoder=None, transcoder=None, skip_existing=True, progress=None):
    """Extract the sound families the phenomena job does not ask for.

    *talks_path* names the talk corpus whose voice cues are the talk-voice
    denominator; *manifest_path* names the bundle manifest that is every other
    family's denominator; *bundle_root* is the decrypted tree the packages are
    read from; *out_dir* is the phenomena output directory whose ``audio/``
    receives the products.
    """
    from core.assets.packages import PackageStore

    talks = json.loads(Path(talks_path).read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    bundles = manifest.get("bundles") or {}
    names = list(bundles)
    manifest_set = set(names)
    # Entries the manifest marks as part of the player build are not on the
    # download path, so their absence from the decrypted tree is a named
    # fact about them rather than a download somebody forgot.
    builtin_flats = {name.replace("/", "__") for name in names
                     if (bundles.get(name) or {}).get("isBuiltin")}

    audio_root = Path(out_dir) / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    existing = _existing_packages(audio_root) if skip_existing else []
    existing_set = set(existing)
    existing_voices = {}
    if existing:
        old_loop = json.loads((audio_root / "loop.json").read_text(encoding="utf-8"))
        rows_by_package = {}
        for entry in old_loop.get("packages", []):
            rows_by_package.setdefault(entry["package"], []).append(entry)
            existing_voices.setdefault(entry["package"], set()).update(
                stream.get("cue") for stream in entry.get("streams", [])
                if stream.get("wav") and (Path(out_dir) / stream["wav"]).is_file())
        # Repair the affected fixture-SE family once. Legacy ambience/BGM/voice
        # jobs retain their separate cache policy; no whole voice-corpus redo.
        def complete_fixture(package):
            rows = rows_by_package.get(package, [])
            return bool(rows) and all(row.get("archiveSetRevision") == ARCHIVE_SET_REVISION
                and row.get("archiveSetCount") == len(rows)
                and row.get("status") == "succeeded" and bool(row.get("streams"))
                and all(not stream.get("error") and any(stream.get(kind)
                        and (Path(out_dir) / stream[kind]).is_file() for kind in ("wav", "ogg"))
                        for stream in row["streams"]) for row in rows) and {
                    row.get("archiveSetIndex") for row in rows} == set(range(len(rows)))
        existing = [package for package in existing if not package.startswith("mysekai__sound__se__fixture__")
                    or complete_fixture(package)]
        # The phenomena job decodes the shared ambience package only as the
        # cue subset its master rows name (the weather ambience); the se
        # family wants every cue of that archive, the UI button cues among
        # them.  Only an entry this job wrote (whole) counts as done.
        existing = [package for package in existing if package != AMBIENCE_PACKAGE
                    or all(row.get("archiveSetRevision") == ARCHIVE_SET_REVISION
                           for row in rows_by_package.get(package, []))]
        existing_set = set(existing)

    requests, voice_cues = voice_requests(talks)

    missing = []
    plan = {"talk-voice": [], "se": [], "bgm": [], "part-voice": []}
    for package, cues in sorted(requests.items()):
        flat = package.replace("/", "__")
        if flat in existing_set and set(cues) <= existing_voices.get(flat, set()):
            continue                       # already in the library
        if package not in manifest_set:
            missing.append({"package": flat, "cues": cues,
                            "reason": NO_PACKAGE})
            continue
        plan["talk-voice"].append((flat, cues))
    for family, prefixes in (("se", (SE,)), ("bgm", (BGM,)),
                             ("part-voice", PART_VOICE)):
        for prefix in prefixes:
            for flat in prefix_packages(names, prefix,
                                        prefix.replace("/", "__"),
                                        exclude=existing):
                plan[family].append((flat, None))

    library = Library(audio_root, "audio", decoder, transcoder)
    store = PackageStore([], bundle_root)
    done = {"talk-voice": 0, "se": 0, "bgm": 0, "part-voice": 0}
    failures = {family: [] for family in done}
    walked = 0
    total = sum(len(rows) for rows in plan.values())
    for family, wanted in plan.items():
        for flat, cues in wanted:
            if progress is not None:
                progress({"family": family, "package": flat, "completed": walked,
                          "requested": total, "cached": len(existing_set)})
            # A package the manifest marks as player-build names its own
            # reason when it is absent; anything else is simply not there.
            absent = BUILT_IN if flat in builtin_flats else NO_BUNDLE
            if _extract_one(store, library, flat, cues, failures[family],
                            absent_reason=absent):
                done[family] += 1
            walked += 1
            if walked % CHECKPOINT_EVERY == 0 or progress is not None:
                # progress so far is durable: a run stopped here resumes
                # without redoing the packages already on disk
                _merge_loop(audio_root, library)

    families = {}
    for family in ("talk-voice", "se", "bgm", "part-voice"):
        families[family] = _family_summary(
            len(plan[family]) + (len(missing) if family == "talk-voice" else 0),
            done[family], failures[family])
    families["talk-voice"]["requestedCues"] = len(voice_cues)
    decoded = {stream.get("cue") for entry in library.packages
               for stream in entry["streams"] if stream.get("wav")}
    families["talk-voice"]["decodedCues"] = len(decoded & set(voice_cues))
    families["talk-voice"]["missingPackages"] = missing
    families["talk-voice"]["uncovered"] = [
        {"cue": cue, "reason": NO_PACKAGE} for row in missing for cue in row["cues"]]

    loop = _merge_loop(audio_root, library)

    # The corpus also names part-voice cues; whether the packages answer them
    # is a consumer-side check, not part of any denominator.  It reads the
    # merged library — both sides of the family — so streams a previous run
    # decoded count as answered.
    part_cues = sorted(
        {cue for talk in _corpus_talks(talks)
         for cue in talk.get("voices") or []
         if cue.startswith("partvoice_")})
    part_prefixes = tuple(prefix.replace("/", "__") for prefix in PART_VOICE)
    answered_on_disk = {stream.get("cue") for entry in loop["packages"]
                        if any(entry["package"].startswith(prefix)
                               for prefix in part_prefixes)
                        for stream in entry["streams"] if stream.get("wav")}
    part_voice_cues = {"requested": len(part_cues),
                       "answered": len(answered_on_disk & set(part_cues)),
                       "missing": [cue for cue in part_cues
                                   if cue not in answered_on_disk]}

    document = {"version": 1, "semantics": SEMANTICS, "families": families,
                "partVoiceCues": part_voice_cues,
                "loop": {"file": "audio/loop.json",
                         "packages": len(loop["packages"])}}
    path = audio_root / "corpus.json"
    if path.exists():
        document = _merge_corpus(path, document, answered_on_disk)
    # A retry can repair a previous missing package or an older cue route.
    # Remove those stale failures only when the decoded library answers them.
    successful = {entry["package"] for entry in loop["packages"]
                  if any(stream.get("wav") for stream in entry.get("streams", []))}
    decoded_cues = {stream.get("cue") for entry in loop["packages"]
                   for stream in entry.get("streams", []) if stream.get("wav")}
    for family in document["families"].values():
        for field in ("failed", "missingPackages"):
            if field in family:
                family[field] = [entry for entry in family[field]
                                 if entry["package"] not in successful
                                 and not (entry.get("cues") and set(entry["cues"]) <= decoded_cues)]
        if "uncovered" in family:
            family["uncovered"] = [entry for entry in family["uncovered"]
                                   if entry["cue"] not in decoded_cues]
    write_json(path, document, mode=PUBLIC_FILE_MODE)
    document["path"] = str(path)
    document["audio"] = loop
    return document
