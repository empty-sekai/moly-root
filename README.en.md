# moly-root

[中文](README.md)

`moly-root` is an engine-free extraction toolchain for assets from *Hatsune Miku: Colorful Stage!* (Project SEKAI, "PJSK").

**Current scope: character asset packs, weather (phenomena) asset packs, and the furniture-performance asset packs** — on the character side it emits glTF 2.0 character files, a shared humanoid motion library, facial tables, and overhead-emote data; on the weather side it emits each phenomenon's environment configuration, sky gradient, post-processing profile, site overrides, and particle effects; on the furniture-performance side it emits the fixture interface (character attach points and the grid sets), both timeline families (story and fixture), the camera curves, the beside-the-fixture talks, and the animation clips derived from the timelines, plus the window-tree fields of the dialogue UI and the overhead HUD (whose input is the caller-supplied APK player data, not a downloadable package); all three write one machine-readable extraction report. Public motion outputs bind animation to humanoid bone names so the library can be retargeted onto compatible humanoid rigs. Two browser reference consumers live here: `examples/viewer/` (characters and weather) and `examples/stage/` (performance preview); presentation semantics are documented in `docs/presentation.md`.

**Roadmap**: asset packs for decoration and the like are planned for later releases. There are two browser examples now: `examples/viewer/` restores solo (alone) performance scenarios, and `examples/stage/` previews performances that interact with furniture — both forms (self-initiated and player-participating), the camera work, and the interface slots for the dialogue UI; **the extractor emits data, and an example is a preview, not a finished presentation layer**. Two limits are recorded as they are: the glTF playable rate of the animation clips is not all of them (`cut_scene` 361/371, `fixture_timeline` 859/1401; the difference is 1658 unresolved channel path hashes pointing at hierarchy levels that do not exist in the shipped prefabs — a drift between the game side's authoring state and its shipped state, not a defect here), and the dialogue UI's hand decoding of fields is not fully closed (on real data 290 objects read with 0 leftover bytes and 45 are marked partial). Both are detailed in [manifest extraction](docs/extract.en.md). The weather side **extracts music and ambience too**: the sound packages are named by the caller's master rows and `pull` fetches them along with everything else; decoding them into waveforms needs an optional external program (see [Optional external dependency](#optional-external-dependency-audio-decoding)).

The AssetBundleInfoNew package manifest must be supplied by the user. This repository neither bundles nor distributes any game data.

## Live preview

<https://chara.mizore.art/> is a hosted build of `examples/viewer/`: open it in a browser to look at characters, the shared motion library, facial expressions, overhead emotes and the weather phenomena without running an extraction locally first.

That page runs against **already-extracted output**, prepared by whoever deploys it. **This repository neither bundles nor distributes game data**; the preview is hosted separately from the repository.

`examples/stage/` (the performance preview) **has no hosted instance** — run it locally with the steps below.

## Install

```sh
python -m pip install .
```

## Quick Start

You need Python ≥ 3.11 and a modern browser. Game data (the `AssetBundleInfoNew` package manifest, an asset endpoint, or already-decrypted bundles) is supplied by you; this repository bundles and distributes none of it.

1. Install from the repository root (this provides the `moly` command): `python -m pip install .`
2. Pull and extract. `pull` selects every bundle a character asset pack needs from the user-supplied package manifest (character models, the shared motion library, facial tables, performance data, overhead items, and the material lookup source), resolves dependencies recursively, downloads, decrypts, and extracts:

   ```sh
   moly --unity-version 2022.3.62f3 pull --manifest path/to/AssetBundleInfoNew.json --asset-base-url <your asset endpoint>
   ```

   Extraction artifacts go to `local-data/` (the default, ignored by the repository), together with the `manifest.json` the browser example reads directly; download and decryption intermediates live under `moly-pull-output/`.

   If you already have a directory of decrypted bundles, you can extract without the network; no manifest is required, and the tool recognizes the supported asset-pack bundles in the directory (characters and phenomena):

   ```sh
   moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
   ```

3. View: serve the repository root and open the example page to see the character, motions, and overhead items:

   ```sh
   python -m http.server 8000
   # open http://localhost:8000/examples/viewer/index.html?base=../../local-data
   ```

Master tables are caller-supplied inputs. Use `--master <directory>` to read local `<table>.json` files, or `--master-url [base]` to fetch each table as `<base>/<table>.json`; omitting the URL value selects the public default base. `--master-cache <directory>` caches remote tables. Without master input, `characters.json` is skipped and reported, while model, motion, facial, performance, and overhead-item extraction can still run. Direct-talk extraction needs master to determine single-character membership.

`characters.json` also reads `clientConfigs.json` when master is available. Rows `77`, `78`, and `95` describe normal movement scale, harvest-area movement scale, and dash speed rate. The registry exposes these parsed rows and derived walk/dash speeds. If `clientConfigs.json` or one required row is absent, only `player` is `null` and the gap is recorded in `summary.missing.playerConfig`; other registry sections are still emitted when their own tables are present.

## Commands

Inspect the available command options with:

```sh
moly --help
```

Batch extraction from a directory of decrypted bundles (`--manifest` is optional; when omitted, every recognizable asset-pack bundle in the directory is selected):

```sh
moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
```

When `--out` is omitted, artifacts go to `local-data/` (the default output directory, ignored by `.gitignore`). `--manifest` scopes the set: one logical bundle name per line; slash names and canonical double-underscore names are equivalent; JSON arrays and `{ "bundles": [...] }` documents are also accepted. Missing bundles make the command exit non-zero; domains without a registered extractor remain visible as `unsupported`.

Extract one character, the shared motion library, or facial tables:

```sh
moly --unity-version 2022.3.62f3 characters --bundle path/to/character --out-dir out --name character
moly --unity-version 2022.3.62f3 motion-library --reference-bundle path/to/character --motion-bundle path/to/motion --out-dir out
moly --unity-version 2022.3.62f3 facial-tables --bundle path/to/settings --out tables.json
```

Extract weather (phenomena) asset packages on their own. One phenomenon spans several packages (a global one, a shared one, and one per site); pass them all. `--bundle-root` names the directory the dependency packages (materials, images, and shaders) are read from — without it, materials do not resolve:

```sh
moly --unity-version 2022.3.62f3 phenomena --bundle path/to/decrypted/<phenomenon global package> --bundle path/to/decrypted/<phenomenon site package> --bundle-root path/to/decrypted --out-dir out/phenomena --master path/to/master
```

Add `--vgmstream` to decode a phenomenon's music and ambience (see [Optional external dependency](#optional-external-dependency-audio-decoding) below):

```sh
moly --unity-version 2022.3.62f3 phenomena --bundle path/to/decrypted/<phenomenon global package> --bundle-root path/to/decrypted --out-dir out/phenomena --master path/to/master --vgmstream path/to/vgmstream
```

Pull from an `AssetBundleInfoNew` manifest:

```sh
moly --unity-version 2022.3.62f3 pull --manifest path/to/AssetBundleInfoNew.json --asset-base-url https://assets.example.invalid/base
```

The `AssetBundleInfoNew` manifest is the sole source of bundle metadata. It must be supplied by the user and should provide `bundleName`, `downloadPath`, `cacheFileName`, and `dependencies` for each entry bundle; do not infer dependencies from local directory names or network URLs. `pull` selects a root from the manifest for **every domain the extractor supports** — character models, shared motion, character settings, performance, overhead items, talks, weather (phenomena) environment packages, and the phenomena thumbnail package — plus the material-lookup source, then resolves dependencies recursively with cycle detection. Root selection uses the same router extraction does, so the two cannot drift apart. **Sound packages take one extra step**: no package declares one as a dependency and their names live only in master rows, so with `--master` (or `--master-url`) `pull` reads those rows first and downloads the packages they name; without it none is fetched and the summary says why (see [docs/pull.en.md](docs/pull.en.md)). Extraction artifacts default to `local-data/` (`--extract-out` overrides); the download/decrypt workspace defaults to `moly-pull-output/` (`--out` overrides).

One command for the complete output including audio (`--vgmstream` is optional; without it the archives are still written, just not decoded):

```sh
moly --unity-version 2022.3.62f3 pull --manifest path/to/AssetBundleInfoNew.json --asset-base-url https://assets.example.invalid/base --master path/to/master --vgmstream path/to/vgmstream
```

`--asset-base-url` specifies the asset network endpoint as an opaque prefix; moly-root does not parse its internal structure. Each request URL is `<asset-base-url>/<downloadPath>/<bundleName>`, preserving the manifest `bundleName` including any slashes. The endpoint and package manifest are both supplied by the user; this repository bundles and distributes neither. Downloads retry and resume `.part` files and verify an optional SHA-256 digest. The wrapper is decrypted before extraction.

Discover, download, or inspect an Android APK:

```sh
moly fetch-apk latest
moly fetch-apk download package.apk --url https://example.invalid/package.apk --sha256 sha256:HEX
moly fetch-apk inspect package.apk
```

`fetch-apk` discovers a public Android package, resumes `.part` downloads, verifies an optional SHA-256 digest, or lists embedded asset-container candidates without extracting them.

### `moly site`

Extract the site (place) asset packages: the nine sites' scenes, the indoor kit and
its expansion levels, the room skins, the field objects, the world map and the site
system's own shells.

```sh
moly --unity-version 2022.3.62f3 site \
  --bundle path/to/decrypted/mysekai__site__field__grasslands \
  --bundle path/to/decrypted/mysekai__site__my_room_asset__common \
  --bundle-root path/to/decrypted \
  --out-dir local-data/site \
  --master path/to/master
```

`--bundle` repeats; `--bundle-root` is where dependencies are looked up (both a
material's shader package and the indoor kit come from there). Without master tables
the geometry, the collision surfaces, the navigation data and the census are still
written, and only the placement table stays empty with the reason stated. `moly
extract` already recognizes every package under the site path and runs them as one
site job, so this command is only for running the domain on its own.

## Optional external dependency: audio decoding

A sound package holds no audio files, only an archive in a middleware container format (`.acb`). **Getting that archive out needs nothing external**, so it is always written; **decoding** it needs an external program:

- **[vgmstream](https://vgmstream.org/)**'s command-line build, `vgmstream-cli`. This repository **does not bundle, vendor, or build it**, and it carries its own licence and attribution (see the `COPYING` file shipped with it). Obtain it yourself, then point this tool at it with `--vgmstream <executable or the directory holding it>`, or put it on `PATH` to be found automatically.
- **ffmpeg** (optional, `--ffmpeg`): used only to write a compressed copy (`.ogg`) next to each decoded waveform. Without it you get the uncompressed waveform only.

**Neither being present fails the extraction.** With no decoder the audio entry is recorded as `status: "skipped"` together with what is missing, the archive stays on disk, and a rerun after installing the tool decodes it — the same semantics as `characters.json` being reported `skipped` without master tables: **a missing input is named, never silently turned into a missing artifact and never reported as success**.

Uncompressed waveforms are large (about 150 MB for all of the current content); the compressed copies are roughly a hundredth of that. Both are written only under `local-data/`, which stays out of version control.

## Outputs

Character extraction writes `<name>.glb` and `<name>.rig.json`. Performance extraction writes `alone-actions.json` (the motion-to-facial pairing and its timing). Motion extraction writes a glTF library and `<name>.index.json`. Facial extraction writes table JSON. Direct-talk extraction writes `talks.json` (single-character talks; needs `--master`). Overhead-item extraction writes `emoticons/` (one shared `emoticons.json` plus one PNG per texture). Weather (phenomena) extraction writes `phenomena/`: one shared `index.json` (the phenomenon list; with `--master` it also joins the phenomenon rows, the refresh windows, and the music and ambience cues with the packages holding them), one directory per phenomenon (`config.json`, `ramp.png`, `postprocess.json`, `fx/effects.json`, `textures/`, plus `overrides/<site>/` for a site that carries one), `icons/` for every phenomenon icon, `models/` holding the deduplicated geometry (`.glb` for model assets and mesh emitters), and `audio/` holding each audio archive as shipped along with the decoded waveforms and `loop.json` loop points when an external decoder is given. Site (place) extraction writes `site/`: `index.json` (the domain index: constants, the scene table, how a room is assembled, one list per family), `sites.json` (the nine-row placement table plus world positions, the grid constants, levels and grid extents, and the footstep table), `packages.json` (the census of all 109 packages under the site path, with per-object accounting), and one directory per package: `scenes/<site>/` (geometry as a `.glb` with one glTF scene per prefab root, collision one file per surface, the shipped bake under `navmesh/`, and `textures/`), `indoor/` (the kit plus one module and one walkable surface per expansion level), `skins/`, `props/`, `sitemap/`, `travel/`, `preview/` and `shell/`. **Site geometry always keeps its own package's origin; the world offset is in `sites.json` alone** — baking it in loses the second and third floors, which share one package, for good. With `--master`, `characters.json` is written as well (the character registry and locomotion personality; master tables are supplied by the caller and are not bundled here). Manifest extraction always writes `extraction-report.json`, and rebuilds `manifest.json` (the unit list the browser example reads) from the `sd_<id>.glb` files actually on disk whenever character artifacts exist. `pull` writes its extraction artifacts to `local-data/` just like `extract`; its workspace additionally holds `raw/`, `decrypted/`, `downloads.json`, and `extraction-manifest.txt`.

See [data contract](docs/data-contract.en.md), [manifest extraction](docs/extract.en.md), [pull pipeline](docs/pull.en.md), [retargeting](docs/retarget.en.md), and [shader extraction](docs/shader.en.md).

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party attribution.
