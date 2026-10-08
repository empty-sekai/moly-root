# Asset architecture validation

Install the validation dependencies and run the synthetic suite from a clean
checkout:

```sh
python -m pip install '.[jp]' pytest build
python -m pytest -q
PACK_FORCE_SCHEMA_LITE=1 python -m pytest tests/test_asset_architecture_v2.py tests/test_pack_integrity.py tests/test_artifact_determinism.py -q
```

On PowerShell, set `PACK_FORCE_SCHEMA_LITE` through `$env:`. Node.js is needed
for the fallback encoder and viewer contract checks. Tests requiring private
inputs are opt-in and report their skips.

`tests/run_validation.py --work-dir <output-directory>` records source
identity, test logs and wheel/source-distribution checks. The output directory
must be outside the checkout. Its `--service-user-check` option also verifies
that a separate POSIX user can read published objects.

For native and browser conformance against a compatible Moly consumer, set
`MOLY_CONSUMER_WORKSPACE` to that checkout:

```sh
cargo test --manifest-path integration/moly-pack-probe/Cargo.toml --lib
cargo build --manifest-path integration/moly-pack-probe/Cargo.toml --target wasm32-unknown-unknown --lib
python tools/generate_asset_v2_fixtures.py --out .validation/public-fixtures --limits
```

Use a `wasm-bindgen` CLI matching the probe's locked dependency to generate
the browser module, then run:

```sh
node integration/browser-pack-conformance.mjs --consumer <consumer> --wasm <probe-web-directory> --fixtures .validation/public-fixtures --output .validation/browser-conformance.json
```

These checks cover package identities, integrity, resource limits, migration,
cache reuse and offline asset reads. They do not establish rendered gameplay
or visual equivalence.

For a private asset collection, `tools/measure_asset_v2.py` and
`tools/validate_real_asset_v2.py` measure decoded contents and written encoded
objects separately. Store their indexes, reports and generated packages in
an ignored directory with the input data. Record the exact inputs and selected
catalogs when comparing releases or regions.
