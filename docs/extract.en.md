# Manifest Extraction

[中文](extract.md)

The AssetBundleInfoNew package manifest must be supplied by the user. This repository neither bundles nor distributes any game data.

`moly extract` is the consumer-facing batch entry point:

```sh
moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
```

When `--out` is omitted, output goes to `local-data/` — the tool's default output
directory, which the repository's `.gitignore` keeps out of version control.

Verified output for a missing bundle:

```json
{"version": 1, "bundles": [{"bundle": "mysekai__character__mdl_sd_999_001", "status": "failed", "artifacts": [], "counts": {}, "error": "FileNotFoundError: bundle not found: mysekai__character__mdl_sd_999_001"}], "summary": {"requested": 1, "succeeded": 0, "failed": 1, "unsupported": 0}, "report": "out/extraction-report.json"}
```

`--master <directory>` reads caller-supplied master tables from `<table>.json`. `--master-url [base]` fetches the same tables from `<base>/<table>.json`; when the value is omitted, the public default base is used. `--master-cache <directory>` caches fetched tables. These options are also available on `pull` and `registry`.

When master tables are supplied, the derived `characters.json` artifact merges identity, locomotion, alone-action, and player movement configuration. The player section reads `clientConfigs.json` rows `77`, `78`, and `95`; it exposes the parsed rows and derived normal walk and dash speeds. If that table or one of those rows is missing, `characters.json` is still written with `player: null` and `summary.missing.playerConfig`; identity, locomotion, alone-action, and all unrelated extraction artifacts remain independent. Other missing master tables still fail the registry artifact and are reported as such.

The router recognizes character model bundles, the shared character motion bundle, character settings, the performance orchestration bundle, overhead-item (emoticon) bundles, the talk bundle (which needs caller-supplied master tables), the weather (phenomena) environment bundles together with the shared phenomena thumbnail bundle and the sound bundles (`mysekai/sound/**`), every package under the site path (`mysekai/site/**`), the fixture-interface packages (`mysekai__fixture__**`), the story cut-scene timeline packages (`mysekai__cut_scene__**`) and the fixture-timeline packages (`mysekai__fixture_timeline__**`), and the dialogue-camera package (`mysekai__camera`, by exact name rather than by prefix). Each requested item receives a report entry with `status`, `artifacts`, `counts`, and an error string. Missing bundles are `failed` and make the command exit non-zero. Domains without a registered extractor remain `unsupported`, providing an extension point for future asset domains.

**Overhead items and phenomena each get exactly one job.** Both write a shared index, so within one run all of their bundles are extracted together once rather than one bundle at a time: a phenomenon spans several packages (a global one, a shared one, and one per site), and running them apart resolves neither the site overrides nor the cross-package materials. Per-bundle `counts` still report what each bundle contributed, and one `derived` entry carries the `phenomena/index.json` totals. **Phenomena extraction additionally reads the dependency packages each package declares** (materials, images, and shaders), looking for them in the same directory as `--bundles`; a dependency that cannot be found is recorded in `summary.missing.dependencies` of `phenomena/index.json`, and its pointers stay in an unresolved state rather than being read as "no material".

**The site domain is one job too.** Every package under the site path (scenes, the
indoor kit, the expansion levels, the room skins, the field objects, the world map,
the shells) is extracted together in one run: an expansion level's meshes live in the
kit package, a material's shader lives in the shared package, and the placement table
spans every site, so extracting one package at a time resolves none of the three. The
per-package `counts` give what that package contributed in geometry, collision
surfaces, navigation assets, materials and images, and one `derived` entry records the
totals of `site/index.json`. The artifacts go under `site/` in the output directory;
their shape is in the [data contract](data-contract.en.md#site).

The site domain also runs on its own:

```sh
moly --unity-version 2022.3.62f3 site \
  --bundle path/to/decrypted/mysekai__site__field__grasslands \
  --bundle path/to/decrypted/mysekai__site__my_room_asset__common \
  --bundle-root path/to/decrypted \
  --out-dir local-data/site \
  --master path/to/master
```

`--bundle-root` is where dependencies are looked up (both the shader package and the
kit come from there). Without master tables the geometry, collision, navigation and
census are still written; only the placement table `sites.json` stays empty, with the
reason in `summary.missing.master`. **Site geometry always keeps its own package's
origin** — the world offset is in `sites.json` alone, which is a consumer-facing rule
and is argued in the contract.

**Music and ambience are a third kind of input, and they need two things present at once.** The sound packages of a phenomenon are named by master rows (each music layer names its own package; every ambience cue in one shared package), and extraction looks those names up **in the same directory as `--bundles`** — they are not declared as a dependency by any package, so `extract` cannot discover them on its own: going through `extract`, place them in that directory by hand (**going through `pull` you do not have to** — `pull` downloads them from the same master rows, see [the pull pipeline](pull.en.md)). A package that cannot be found is recorded entry by entry under `unsupported` with the reason "sound package was not supplied or reachable". Getting the audio archive out needs no external program; **decoding** it does (`--vgmstream`, see the optional external dependency section of the README), and with the decoder absent the audio is reported `skipped` rather than failed.

**A sound package is a domain of its own.** The router routes `mysekai/sound/**` to the `sound` domain, so a sound package named in a manifest — or discovered under `--bundles` — gets its own report entry, with `counts` of `{streams, cues, archiveBytes}`. **A sound package no master row names is reported `unsupported`** with the reason that no row names it: "no row asked for this archive" and "a row did, and it could not be read" are different answers and must not look alike.

**The fixture interface is one job too.** Every package under the fixture path (999 of them in the current content) is extracted together in one run: the character attach-point index and the per-fixture grid sets are each written once as a shared artifact, and one package at a time writes neither index. The artifacts go under `fixture-interface/` in the output directory: `attach-points.json` holds the attach-point pairs (441 pairs from 195 packages in the current content), and `areas.json` holds the grid sets of each package's `FixtureBundleMeta` (858 meta objects in the current content, of which the non-empty ones are 184 `motionArea`, 92 `stackEnables`, 22 `AddUsingGrid` and 2 `cutsceneArea`). **The domain is `fixture-interface`, never `fixture`**: only those two things are read out of the family, and calling it `fixture` would promise the whole family.

**The two timeline families each get one job.** The story cut-scene player (`mysekai__cut_scene__**`, 92 packages and 92 timelines in the current content) and the furniture fixture-timeline views (`mysekai__fixture_timeline__**`, 87 packages and 682 timelines) are two domains, never one "performance" domain — the products are split by host, not merged. Each family's job performs the same three reads — the track trees, the clips' timing fields and the clip targets — writing them under that family's `tracks/`, `clips/` and `clip-targets/`; a package's pointers reach other packages in the same family, so a per-package run would resolve none of the three.

Character artifacts are named after the character (`sd_112.glb`, `sd_112.rig.json`), matching the browser example's consumption convention. Whenever character artifacts exist, `manifest.json` (the unit list the browser example reads) is rebuilt from the `sd_<unit>.glb` files actually on disk, so repeated or partial runs always reflect the current directory. By default stdout carries a short summary plus a viewing hint; `--json` prints the full report instead. `extraction-report.json` is always written. Character entries use a motion bundle and settings bundle listed in the same manifest when present. The first character model supplies the motion library reference skeleton. Batch outputs and `extraction-report.json` are written below the output directory.
**The dialogue camera is a domain claimed by an exact package name.** The router routes the single package `mysekai__camera` to the `camera` domain, by exact name rather than by prefix: a prefix would also swallow any future `mysekai__camera_*` neighbour into the camera job, and the contents of those packages have not been read by anyone. Such a neighbour therefore stays visible in the report as `unsupported`. `counts` gives the package's `CameraParam` / `CameraSetting` instance counts, how many curves were found empty, and how many field-completeness issues were seen; the per-package product lands under `camera/` in the output directory.

**The talk package feeds two extractors.** The one package `mysekai__talk__scenario__talk` holds two families of talk: the single-character talks that happen away from any fixture, and the ones beside a fixture. Both read that same package and write different artifacts — the first `talks.json`, the second `fixture-talks/talks.json` — so the package is extracted twice rather than routed twice: one package cannot belong to two domains. The beside-the-fixture family gets its own `derived` entry for its own outcome, so a failure there is not read as a failure of the first, and its success does not vouch for the first either. In the current content the beside-the-fixture family holds 4768 talks (258 self-initiated and 4510 player-participating).

**The animation-clip export is a derived pass, not a domain.** Which clips are exported is decided by the clip-target documents the two timeline jobs have just written, never by a package name, so it has no route: giving it one would promise that some bundle name selects it, and none does. It runs after both timeline jobs, over their output, writes below `perf-animations/` in the output directory, and records `targets` plus the exported, skipped and remaining counts in `derived`; a package that failed to export is named in that entry's `error` rather than folded into the count of what did export. In the current content the `cut_scene` side exports 371/371 targets and the `fixture_timeline` side 1401/1403. **Exported is not the same as playable**: the glTF-playable counts are 361/371 and 859/1401, and the difference comes from 1658 unresolved channel path hashes — **classified, with no catch-all class**, each of the four classes stating the evidence that put the hash there: the clip's other transform bindings resolved to a character rig, to fixture-model nodes, to this package's own nodes, or to nothing at all. **The class says the hash resolved in none of the hierarchies this run searched, not that it does not exist in the shipped prefabs** — the cross-package search space is the model files this tool has exported, which is a subset of what ships, so "does not exist" is not established and only "was not found here" is. The two are recorded apart; an unresolved binding stays in the product as it is and is counted separately, never dropped.

**The dialogue UI's input is not a downloadable package.** It is built into the APK's player data, no package name can point at it, and no route claims it: `extract` takes the path to that file from the caller (`player_data`), reads it when given, and reports under the report's `playerData` section how many windows, nodes and components `ui/talk.json` holds, together with the leftover-bytes self-check counts. **Without the path the section is still there, with status `skipped` and the reason stated** — an absent entry would read as "this domain does not exist" when the fact is "nobody supplied its input", and the two must not look alike. What is read is the whole window tree under each root behaviour; a component with no hand decoder stays in the product carrying the reader's own partial mark and is counted separately, never dropped. **The hand decoding of fields is not fully closed**: on real data 290 objects read with 0 leftover bytes and 45 more are marked partial.

**Keyframes in the phenomena domain deliberately carry no weighting fields; the values are known to be zero.** Every keyframe `core.particles` exports carries `weightedMode` / `inWeight` / `outWeight` — Unity's weighted mode changes the cubic evaluation, so a consumer that cannot see it evaluates weighted keyframes wrongly and silently; the three fields are therefore always exported rather than omitted. **The phenomena product does not have them, and that is deliberate**: reading the raw typetrees of the domain's 111 bundles directly, all 18,668 keyframes across 8,289 curves have `weightedMode` 0, and the curves a product exports are a subset of the raw curves, so zero in the superset forces zero in the subset — what re-running would add is the zero itself. **The same scan measured 457 weighted keyframes in the site domain and 16 in the overhead-item domain**, so that zero is a measured zero, not a blind instrument. The other half of the reason is cost: this domain also takes `--vgmstream` / `--ffmpeg` audio decoder paths, and no run record retains them; re-running without them would drop the `.ogg` files already produced, and trading serving audio for a known-zero addition is not a trade. The bundle list itself needs no archaeology: the router claims 111 packages in the decrypted root for the `phenomena` domain.
