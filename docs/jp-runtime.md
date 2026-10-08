# Japanese runtime resources

The built-in JP commands follow the public asset-discovery protocol documented
by `sssekai`. Install the optional protocol dependencies with
`pip install -e '.[jp]'`. Discovery and downloads happen only when requested.

```sh
moly jp discover
moly jp manifest --out jp-manifest.json
moly jp plan --manifest jp-manifest.json
moly jp download --manifest jp-manifest.json --out jp-workspace --workers 1
moly jp master --manifest jp-manifest.json --out jp-workspace/master-haruki \
  --provider haruki --all-tables --workers 1
```

The default source is `Team-Haruki/haruki-sekai-master`. The master exporter pins
an immutable Git commit, verifies Git blob hashes, and requires its asset version
and hash to match the bundle descriptor. A different release needs a different
workspace. Download receipts accumulate when extra package roots are requested;
verified original and decrypted bundles are retained for resuming a run.

`jp pull` combines download and extraction. An existing source-qualified
extraction can be assembled into a separate runtime directory:

```sh
moly jp pull --manifest jp-manifest.json --out jp-workspace \
  --extract-out jp-workspace/extracted --master jp-workspace/master-haruki --workers 1
moly jp prepare --manifest jp-manifest.json --workspace jp-workspace \
  --out jp-runtime --master-from jp-workspace/master-haruki \
  --ui-from cn-runtime --actor-workers 1
moly validate-runtime --asset-root jp-runtime --verify-actor-hashes
```

The UI donor supplies shared interface pixels only. Its files, region and
version are recorded; JP models, furniture, characters, scripts, master tables
and voices remain JP source data. `runtime-build.json` records completed stages
and a failed stage's reason. Re-running the command resumes those stages.

Furniture coverage distinguishes models from room surfaces and custom display
furniture. `fixture-presentation` resolves the latter through the master and the
original texture naming configuration, including colors and UV sets. Custom
frames can be exported without fabricating the user's chosen artwork.

```sh
moly fixture-presentation --asset-root runtime --master master --bundles decrypted
moly fixture-animations --asset-root runtime --bundles decrypted --plan
moly fixture-animations --asset-root runtime --bundles decrypted
moly fixture-coverage --data-root runtime --json
moly timeline-capabilities --asset-root runtime
```

The animation refresh selects models with constant poses or external fixture
clips. It follows the Timeline's `Fixture1` track and exact serialized file and
path ID, preserving clips moved from model packages into Timeline packages.
Constant values remain constant over the authored clip duration, including the
endpoint needed by engines that reject a zero-duration glTF animation.

`runtime-validation.json` checks referenced files, optional actor receipt hashes,
and the exact voice cues requested by both script corpora. Missing original cues
are reported as `ready-with-source-gaps`; missing output files are `failed`.
Resource coverage describes available source presentations, not a guarantee that
every game-specific shader or custom artwork feature is implemented by a viewer.
The compact Timeline capabilities index records only nonempty payload classes
for each authored prefab. The viewer can explain unavailable actions before
loading their assets; support policy stays in the runtime, not in the extractor.
