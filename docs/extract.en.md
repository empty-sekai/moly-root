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

The router recognizes character model bundles, the shared character motion bundle, character settings, the performance orchestration bundle, overhead-item (emoticon) bundles, and the talk bundle (which needs caller-supplied master tables). Each requested item receives a report entry with `status`, `artifacts`, `counts`, and an error string. Missing bundles are `failed` and make the command exit non-zero. Domains without a registered extractor remain `unsupported`, providing an extension point for future asset domains.

Character artifacts are named after the character (`sd_112.glb`, `sd_112.rig.json`), matching the browser example's consumption convention. Whenever character artifacts exist, `manifest.json` (the unit list the browser example reads) is rebuilt from the `sd_<unit>.glb` files actually on disk, so repeated or partial runs always reflect the current directory. By default stdout carries a short summary plus a viewing hint; `--json` prints the full report instead. `extraction-report.json` is always written. Character entries use a motion bundle and settings bundle listed in the same manifest when present. The first character model supplies the motion library reference skeleton. Batch outputs and `extraction-report.json` are written below the output directory.
