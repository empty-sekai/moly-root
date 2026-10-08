# Consumer integration tools

The tools in this directory validate compatible Moly readers and support
explicit, reversible consumer updates. A consumer checkout is supplied by the
caller; no captured checkout or prebuilt upgrade payload is distributed here.

## Generate an update

Provide a baseline checkout, an updated checkout and a JSON mapping from every
baseline source path to its SHA-256. The builder checks that mapping and permits
changes only inside its documented consumer boundary:

```sh
python tools/build_moly_asset_v2_bundle.py --baseline <baseline> --updated <updated> --baseline-hashes <hashes.json> --base-head <baseline-commit> --output .validation/consumer-upgrade.json --patch .validation/consumer-upgrade.patch
```

The bundle preserves exact file bytes, including line endings and empty files.
The patch normalizes line endings for review. Keep generated payloads outside
the tracked source tree and review their contents before sharing.

## Check, apply and reverse

```sh
python tools/apply_moly_asset_v2.py --target <consumer> --bundle .validation/consumer-upgrade.json --check
python tools/apply_moly_asset_v2.py --target <consumer> --bundle .validation/consumer-upgrade.json --apply
python tools/apply_moly_asset_v2.py --target <consumer> --bundle .validation/consumer-upgrade.json --reverse --check
```

Preflight checks every file before writing. Each file must match the supplied
preimage or postimage; unrelated local changes are preserved. The installer
rejects path aliases, traversal, overlapping paths and symlinks or junctions.
Replacements are atomic per file, with rollback on ordinary failures and an
explicit error if a concurrent edit prevents safe rollback. Repeating a
completed update changes no files; interrupted updates can resume.

The conformance probe and browser driver use caller-supplied Moly source.
Their consumer-derived code retains AGPL-3.0-only licensing as described in
[MOLY-LICENSE](MOLY-LICENSE). The producer's license is unchanged. See the
[validation guide](../docs/asset-architecture-v2-validation.md) for commands.
