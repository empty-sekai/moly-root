# Pull Pipeline

[中文](pull.md)

The AssetBundleInfoNew package manifest must be supplied by the user. This repository neither bundles nor distributes any game data.

`moly --unity-version 2022.3.62f3 pull --manifest manifest.json --asset-base-url https://assets.example.invalid/base` accepts an `AssetBundleInfoNew` JSON object or array. Roots are selected from `bundleName` — **one set for every domain the extractor supports**, covering character models, shared character motion, character settings, performance, overhead items, talks, weather (phenomena) environment packages, the phenomena thumbnail package, and every package under the site path (`mysekai/site/**`), plus the material-lookup source; transitive `dependencies` are then resolved with cycle detection. Root selection uses the same router extraction does.

**Sound packages are the one kind no name shape can select, and master rows are what name them.** No package declares a sound package as a dependency and no manifest field says which of them hold a world's audio: master rows do (each music layer names its own package, and every ambience cue lives in one shared package, so an ambience row only decides whether that shared package is wanted). So with `--master` (or `--master-url`), `pull` reads those tables first, then adds the ones this manifest actually carries to the root set; **without master tables, no sound package is downloaded at all**, and the default summary says why:

```text
audio: 13 sound packages named by master tables
audio: no sound package pulled (no master directory or base URL was supplied, and only master rows name the sound packages: ...)
```

Which packages those are is decided **entirely by the caller's master tables**; no package name is built into this repository. A package a row names but this manifest does not carry is listed under `audio.notInManifest` (and printed in the default summary) instead of aborting the whole download as a missing dependency. In the full report (`--json`) the `audio` section is `{status, roots, error, tables, absentTables, notInManifest}`: `tables` says how many packages each table named and `absentTables` which tables were not there. That section holds package and table names only — **never a path from the running machine**.

The `AssetBundleInfoNew` manifest is the sole source of bundle metadata and must be supplied by the user. Each entry should provide `bundleName`, `downloadPath`, and `dependencies`; `cacheFileName` is cache metadata and is not used in network URLs. `--asset-base-url` is a user-supplied opaque asset endpoint; moly-root does not parse its internal structure. Each request URL is assembled as:

```text
<asset-base-url>/<downloadPath>/<bundleName>
```

The original `bundleName`, including any slashes, is preserved. The network endpoint and package manifest are both supplied by the user; this repository bundles and distributes neither. Downloads retry and resume partial files and verify an optional SHA-256 digest. The wrapper is decrypted before UnityPy reads it, then the existing extraction orchestrator writes character asset packs and `extraction-report.json`. Game asset bundles carry no readable engine version, so the global `--unity-version` flag is required for `pull` — without it the extraction stage reports `UnityVersionFallbackError` per bundle and exits non-zero.

Extraction artifacts default to `local-data/` (`--extract-out` overrides); the workspace directory (default `moly-pull-output/`, `--out` overrides) contains `raw/`, `decrypted/`, `downloads.json`, and `extraction-manifest.txt`. Character glbs carry **no embedded animations**: all motion lives in the shared motion-library glTF, bound by humanoid bone name (see [retarget.en.md](retarget.en.md)); facial tables are a separate JSON. Overhead-item outputs use one shared `emoticons.json` plus one PNG per texture; PNG filenames carry the package prefix in the form `<item name>__<texture name>.png`. Within one run, the overhead-item packages **merge** into that single index; an index left by a previous run is removed when this run starts, so one run's output holds exactly the items that run extracted. Weather (phenomena) artifacts go to `phenomena/`: one shared `index.json` plus one directory per phenomenon, described in the [data contract](data-contract.en.md). A phenomenon's particle materials and images live in that phenomenon's shared package, and `pull` fetches those dependencies along with the roots, so materials resolve when going through `pull`. **The sound packages come along too**: they are named by master rows rather than declared as a dependency by any package (see above), so with master tables they are part of the root set and `local-data/phenomena/audio/` holds the archives, `loop.json`, and — with a decoder installed — the decoded waveforms. Without master tables none is fetched, audio is recorded as "sound package was not supplied or reachable", and the reason is printed in the summary.
Master tables are the caller's **own** input: the character registry, locomotion personality, and talk membership live only there, and this repository neither bundles nor distributes master data. Two ways to supply them:

- `--master <directory>` — read `<table>.json` from a local directory;
- `--master-url [base]` — fetch each table as `<base>/<table>.json`; **with no value it uses the public mirror's default base** (`Team-Haruki/haruki-sekai-sc-master` on `raw.githubusercontent.com`). Add `--master-cache <directory>` to keep fetched tables on disk so a second run reads locally instead of over the network.

Master tables are caller-supplied inputs for the character registry, locomotion personality, talk membership, and player movement configuration. Use `--master <directory>` for local `<table>.json` files, or `--master-url [base]` to fetch `<base>/<table>.json`; an omitted URL value uses the public default base. `--master-cache <directory>` stores fetched tables for later local reads.

When master is available, `characters.json` includes `player` from `clientConfigs.json`: rows `77`, `78`, and `95` are normal movement scale, harvest-area movement scale, and dash speed rate. Its derived walk speed is the normal scale, and its derived dash speed is normal scale multiplied by dash rate. If the table or a required row is absent, the registry still succeeds with `player: null` and records `summary.missing.playerConfig`; identity, locomotion, alone-action, and other extraction artifacts are not blocked. Without any master source, the registry artifact is skipped and the talk bundle is unsupported because its membership cannot be scoped.

**Each manifest entry carries its own `downloadPath` segment** (entries may differ), so `--asset-base-url` must be the prefix *before* that segment; folding one entry's `downloadPath` into the base yields 404s.

