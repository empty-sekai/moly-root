# moly-root

[中文](README.md)

`moly-root` is an engine-free extraction toolchain for assets from *Hatsune Miku: Colorful Stage!* (Project SEKAI, "PJSK").

**Current scope: character asset packs only** — it emits glTF 2.0 character files, a shared humanoid motion library, facial tables, overhead-emote data, and a machine-readable extraction report. Public motion outputs bind animation to humanoid bone names so the library can be retargeted onto compatible humanoid rigs. A browser reference consumer lives in `examples/viewer/`; presentation semantics are documented in `docs/presentation.md`.

**Roadmap**: asset packs for weather (phenomena), site scenes, and furniture/decoration are planned for later releases. The browser example currently restores solo performance scenarios; performances that interact with furniture belong to the furniture domain and are not yet supported.

The AssetBundleInfoNew package manifest must be supplied by the user. This repository neither bundles nor distributes any game data.

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

   If you already have a directory of decrypted bundles, you can extract without the network; no manifest is required, and the tool recognizes the character-asset-pack bundles in the directory:

   ```sh
   moly --unity-version 2022.3.62f3 extract --bundles path/to/decrypted
   ```

3. View: serve the repository root and open the example page to see the character, motions, and overhead items:

   ```sh
   python -m http.server 8000
   # open http://localhost:8000/examples/viewer/index.html?base=../../local-data
   ```

## Commands

Inspect the available command options with:

```sh
moly --help
```

Batch extraction from a directory of decrypted bundles (`--manifest` is optional; when omitted, every recognizable character-asset-pack bundle in the directory is selected):

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

Pull from an `AssetBundleInfoNew` manifest:

```sh
moly --unity-version 2022.3.62f3 pull --manifest path/to/AssetBundleInfoNew.json --asset-base-url https://assets.example.invalid/base
```

The `AssetBundleInfoNew` manifest is the sole source of bundle metadata. It must be supplied by the user and should provide `bundleName`, `downloadPath`, `cacheFileName`, and `dependencies` for each entry bundle; do not infer dependencies from local directory names or network URLs. `pull` selects character model, shared motion, character settings, performance, overhead-item, and material-lookup roots from the manifest, then resolves dependencies recursively with cycle detection. Extraction artifacts default to `local-data/` (`--extract-out` overrides); the download/decrypt workspace defaults to `moly-pull-output/` (`--out` overrides).

`--asset-base-url` specifies the asset network endpoint as an opaque prefix; moly-root does not parse its internal structure. Each request URL is `<asset-base-url>/<downloadPath>/<bundleName>`, preserving the manifest `bundleName` including any slashes. The endpoint and package manifest are both supplied by the user; this repository bundles and distributes neither. Downloads retry and resume `.part` files and verify an optional SHA-256 digest. The wrapper is decrypted before extraction.

Discover, download, or inspect an Android APK:

```sh
moly fetch-apk latest
moly fetch-apk download package.apk --url https://example.invalid/package.apk --sha256 sha256:HEX
moly fetch-apk inspect package.apk
```

`fetch-apk` discovers a public Android package, resumes `.part` downloads, verifies an optional SHA-256 digest, or lists embedded asset-container candidates without extracting them.

## Outputs

Character extraction writes `<name>.glb` and `<name>.rig.json`. Performance extraction writes `alone-actions.json` (the motion-to-facial pairing and its timing). Motion extraction writes a glTF library and `<name>.index.json`. Facial extraction writes table JSON. Direct-talk extraction writes `talks.json` (single-character talks; needs `--master`). Overhead-item extraction writes `emoticons/` (one shared `emoticons.json` plus one PNG per texture). With `--master`, `characters.json` is written as well (the character registry and locomotion personality; master tables are supplied by the caller and are not bundled here). Manifest extraction always writes `extraction-report.json`, and rebuilds `manifest.json` (the unit list the browser example reads) from the `sd_<id>.glb` files actually on disk whenever character artifacts exist. `pull` writes its extraction artifacts to `local-data/` just like `extract`; its workspace additionally holds `raw/`, `decrypted/`, `downloads.json`, and `extraction-manifest.txt`.

See [data contract](docs/data-contract.en.md), [manifest extraction](docs/extract.en.md), [pull pipeline](docs/pull.en.md), and [retargeting](docs/retarget.en.md).

## License

MIT. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party attribution.
