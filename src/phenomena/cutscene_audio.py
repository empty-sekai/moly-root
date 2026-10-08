"""The sounds a cut-scene's own timeline names: its voices, its music, its effects.

A cut-scene does not borrow its audio from a talk or a site.  Its timeline has
three sound tracks, and every clip on them names what it plays:

* a **music** clip (``Sekai.Mysekai.Timeline.BGMClip``) and an **effect** clip
  (``SEClip``) each serialize the package and the cue: ``_assetBundleName`` and
  ``_cueName``;
* a **voice** clip (``VoiceClip``) serializes only ``_cueName``.  Its package is
  the one the birthday-party presenter downloads before the cut-scene starts:
  ``mysekai/talk/birthday/voice/{0}`` formatted with the cut-scene master row's
  ``timelineAssetbundleName``, the row being the one whose condition type is
  ``birthday_party`` and whose ``externalId`` the party names.  The same format
  is the client's own birthday talk-voice bundle-name helper.  A cut-scene
  reached any other way has no such download, so its voice clips have no
  package this job can name: they are reported, not guessed.

This job reads the cut-scene clip documents the timeline job writes
(``cutscene-timeline/clips/<package>.json``), which carry every clip asset's
serialized fields, and asks for exactly the cues they name -- a music package
may hold more than the cut-scene plays, and those stay undecoded here.  A
package the library already holds is left alone when it already answers every
cue asked of it; when it does not, it is still left alone and the unanswered
cues are named, because that package's entry belongs to the job that wrote it
(an effect sheet decoded whole, say) and a cue subset must never replace it.

Output lands in the shared ``audio/`` library in the corpus job's per-package
shape, the loop sidecar is merged the same way (entries on disk keep their
places, new ones are appended), and the corpus ledger gains a ``cut-scene``
family that accumulates across runs like the others.  Every other family and
field of the ledger is kept as it was.
"""
import json
from pathlib import Path

from core.atomic import PUBLIC_FILE_MODE, write_json
from .audio import Library
from .audio_corpus import (BUILT_IN, NO_BUNDLE, _extract_one, _merge_by_name,
                           _merge_family, _merge_loop)

VOICE_BANK = "mysekai/talk/birthday/voice/{0}"
BIRTHDAY_PARTY = "birthday_party"
CUT_SCENE = "mysekai/cut_scene/{0}"
FAMILY = "cut-scene"

VOICE_CLIP = "VoiceClip"
SOUND_CLIPS = ("BGMClip", "SEClip")

NO_ROW = ("no birthday_party cut-scene row names this cut-scene, so its voice "
          "clips have no voice-bank download to play from")
NOT_IN_MANIFEST = "the package the clip names is not in the manifest"
NOT_ANSWERED = ("the library already holds this package and its entry does not "
                "carry these cues; the entry is left as the job that wrote it "
                "left it")

SEMANTICS = ("per cut-scene clip document: voice clips ask their cues of the "
             "cut-scene's birthday voice bank, music and effect clips ask their "
             "cue of the package they name; `requested` counts packages, "
             "`requestedCues` the cues asked, `decodedCues` those this run "
             "decoded, `answered` the asked cues the library already held, "
             "`unanswered` asked cues a package already in the library does "
             "not carry, `unrouted` voice cues with no voice bank, "
             "`missingPackages` named packages the manifest does not list")


def _clip_fields(document):
    """(class, fields) for every clip asset of a clip document."""
    for asset in document.get("assets") or []:
        yield asset.get("class"), asset.get("fields") or {}


def cutscene_requests(documents, cut_scene_rows):
    """Packages and cues the clip documents ask for.

    *documents* maps a cut-scene package (flattened) to its clip document;
    *cut_scene_rows* is the cut-scene master table.  Returns
    ``(requests, unrouted, per_cut_scene)`` where *requests* maps a package in
    manifest form to its cue set.
    """
    banks = {CUT_SCENE.format(row["timelineAssetbundleName"]).replace("/", "__"):
             VOICE_BANK.format(row["timelineAssetbundleName"])
             for row in cut_scene_rows
             if row.get("mysekaiCutSceneConditionType") == BIRTHDAY_PARTY}
    requests, unrouted, per_cut_scene = {}, [], []
    for flat, document in sorted(documents.items()):
        voices, sounds = [], []
        for cls, fields in _clip_fields(document):
            cue = fields.get("_cueName")
            if not cue:
                continue
            if cls == VOICE_CLIP:
                voices.append(cue)
            elif cls in SOUND_CLIPS and fields.get("_assetBundleName"):
                sounds.append((fields["_assetBundleName"], cue))
        bank = banks.get(flat)
        if voices and bank is None:
            unrouted += [{"cutScene": flat, "cue": cue, "reason": NO_ROW} for cue in sorted(set(voices))]
        elif voices:
            requests.setdefault(bank, set()).update(voices)
        for package, cue in sounds:
            requests.setdefault(package, set()).add(cue)
        per_cut_scene.append({"cutScene": flat, "voiceBank": bank,
                              "voiceCues": sorted(set(voices)),
                              "sounds": sorted({f"{package}:{cue}" for package, cue in sounds})})
    return requests, unrouted, per_cut_scene


def _answered(out_dir, rows):
    """Cues an existing package's entries carry with a waveform on disk."""
    cues = set()
    for row in rows:
        for stream in row.get("streams") or []:
            if not stream.get("error") and any(
                    stream.get(kind) and (Path(out_dir) / stream[kind]).is_file()
                    for kind in ("wav", "ogg")):
                cues.add(stream.get("cue"))
    return cues


def extract_cutscene_audio(clip_paths, cut_scenes_path, manifest_path, bundle_root,
                           out_dir, decoder=None, transcoder=None):
    """Decode the cues the cut-scene clip documents name into ``out_dir/audio``."""
    from core.assets.packages import PackageStore
    from core.fetch import Manifest

    documents = {}
    for path in clip_paths:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        documents[document["package"]] = document
    rows = json.loads(Path(cut_scenes_path).read_text(encoding="utf-8"))
    manifest = Manifest.load(manifest_path)
    requests, unrouted, per_cut_scene = cutscene_requests(documents, rows)

    audio_root = Path(out_dir) / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    loop_path = audio_root / "loop.json"
    existing = {}
    if loop_path.exists():
        for row in json.loads(loop_path.read_text(encoding="utf-8")).get("packages") or []:
            existing.setdefault(row.get("package"), []).append(row)

    plan, missing, unanswered, answered = [], [], [], 0
    for package, cues in sorted(requests.items()):
        flat = package.replace("/", "__")
        if flat in existing:
            have = _answered(out_dir, existing[flat])
            answered += len(cues & have)
            unanswered += [{"package": flat, "cue": cue, "reason": NOT_ANSWERED}
                           for cue in sorted(cues - have)]
            continue
        entry = manifest.entries.get(package)
        if entry is None:
            missing.append({"package": flat, "cues": sorted(cues), "reason": NOT_IN_MANIFEST})
            continue
        plan.append((flat, sorted(cues), BUILT_IN if entry.in_player_build else NO_BUNDLE))

    library = Library(audio_root, "audio", decoder, transcoder)
    store = PackageStore([], bundle_root)
    failures, done = [], 0
    for flat, cues, absent in plan:
        if _extract_one(store, library, flat, cues, failures, absent_reason=absent):
            done += 1
    loop = _merge_loop(audio_root, library)

    asked = {cue for cues in requests.values() for cue in cues}
    decoded = {stream.get("cue") for entry in library.packages
               for stream in entry["streams"] if stream.get("wav")}
    family = {"requested": len(plan) + len(missing), "succeeded": done, "failed": failures,
              "requestedCues": len(asked), "decodedCues": len(decoded & asked),
              "answered": answered, "unanswered": unanswered, "unrouted": unrouted,
              "missingPackages": missing, "cutScenes": per_cut_scene}

    path = audio_root / "corpus.json"
    document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "families": {}}
    families = dict(document.get("families") or {})
    prior = families.get(FAMILY)
    if prior is not None:
        merged = _merge_family(prior, family)
        for field, key in (("unanswered", "cue"), ("unrouted", "cue"), ("cutScenes", "cutScene")):
            merged[field] = _merge_by_name(prior.get(field) or [], family[field], key)
        family = merged
    families[FAMILY] = family
    document["families"] = families
    document["semantics"] = {**(document.get("semantics") or {}), "cutScene": SEMANTICS}
    document["loop"] = {"file": "audio/loop.json", "packages": len(loop["packages"])}
    write_json(path, document, mode=PUBLIC_FILE_MODE)
    return {"family": family, "loop": {"packages": len(loop["packages"])},
            "newPackages": [entry["package"] for entry in library.packages],
            "unsupported": library.unsupported, "path": str(path)}
