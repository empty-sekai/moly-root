# Pull Pipeline

[中文](pull.md)

The AssetBundleInfoNew package manifest must be supplied by the user. This repository neither bundles nor distributes any game data.

`moly --unity-version 2022.3.62f3 pull --manifest manifest.json --asset-base-url https://assets.example.invalid/base` accepts an `AssetBundleInfoNew` JSON object or array. Character model roots, shared character motion, and character settings are selected from `bundleName`; transitive `dependencies` are resolved with cycle detection.

The `AssetBundleInfoNew` manifest is the sole source of bundle metadata and must be supplied by the user. Each entry should provide `bundleName`, `downloadPath`, and `dependencies`; `cacheFileName` is cache metadata and is not used in network URLs. `--asset-base-url` is a user-supplied opaque asset endpoint; moly-root does not parse its internal structure. Each request URL is assembled as:

```text
<asset-base-url>/<downloadPath>/<bundleName>
```

The original `bundleName`, including any slashes, is preserved. The network endpoint and package manifest are both supplied by the user; this repository bundles and distributes neither. Downloads retry and resume partial files and verify an optional SHA-256 digest. The wrapper is decrypted before UnityPy reads it, then the existing extraction orchestrator writes character asset packs and `extraction-report.json`. Game asset bundles carry no readable engine version, so the global `--unity-version` flag is required for `pull` — without it the extraction stage reports `UnityVersionFallbackError` per bundle and exits non-zero.

Extraction artifacts default to `local-data/` (`--extract-out` overrides); the workspace directory (default `moly-pull-output/`, `--out` overrides) contains `raw/`, `decrypted/`, `downloads.json`, and `extraction-manifest.txt`. Character glbs carry **no embedded animations**: all motion lives in the shared motion-library glTF, bound by humanoid bone name (see [retarget.en.md](retarget.en.md)); facial tables are a separate JSON. Overhead-item outputs use one shared `emoticons.json` plus one PNG per texture; PNG filenames carry the package prefix in the form `<item name>__<texture name>.png`. Within one run, the overhead-item packages **merge** into that single index; an index left by a previous run is removed when this run starts, so one run's output holds exactly the items that run extracted.
Master tables are the caller's **own** input: the character registry, locomotion personality, and talk membership live only there, and this repository neither bundles nor distributes master data. Two ways to supply them:

- `--master <directory>` — read `<table>.json` from a local directory;
- `--master-url [base]` — fetch each table as `<base>/<table>.json`; **with no value it uses the public mirror's default base** (`Team-Haruki/haruki-sekai-sc-master` on `raw.githubusercontent.com`). Add `--master-cache <directory>` to keep fetched tables on disk so a second run reads locally instead of over the network.

With neither given, **nothing is fetched and nothing is read**: `characters.json` is not produced and `extraction-report.json` carries a `derived` entry with `status: "skipped"` explaining why; the talk scenario bundle is recorded as `unsupported` (its corpus cannot be scoped without master), rather than quietly producing one artifact fewer.

**Each manifest entry carries its own `downloadPath` segment** (entries may differ), so `--asset-base-url` must be the prefix *before* that segment; folding one entry's `downloadPath` into the base yields 404s.

