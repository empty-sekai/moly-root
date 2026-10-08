# Asset Architecture v2

Moly uses one content-addressed store owned by `src/pack`. Region and release version select immutable packages; they do not become part of reusable package identity. The application still requests logical paths such as `moly://fixture-models/...`, `moly://sd_104.glb`, `moly://site/...`, `moly://ui/...` and `moly://phenomena/audio/...`. Storage addresses remain below `moly-assets` and the Web asset infrastructure.

The separate Moly consumer changes are delivered through the [verified integration bundle](../integration/README.md). Both Rust and JavaScript packed readers require v2. Loose runtime directories remain a separate development mode. The producer retains v1 verification and an offline migration tool, not a second runtime compatibility path.

## Ownership and identities

| Layer | Owns | Identity or selection |
| --- | --- | --- |
| Acquisition and Unity/domain extraction | Original source facts, graph references, authored hierarchy, bindings and source receipts | Existing extraction contracts; never inferred from a CAS filename |
| Runtime artifact | The bytes actually consumed at a logical path | `content_sha256` of decoded bytes |
| Transfer representation | Encoding of those bytes | `blob_sha256` of stored/served bytes |
| Immutable package | Sorted logical paths, decoded facts, transform semantics and representation entries | `content_id` for semantic content; SHA-256 of manifest bytes for the concrete package representation |
| Release catalog | Region, opaque version, provenance, selected packages, path ownership and dependencies | SHA-256 of the archived catalog bytes |
| Channel/default | Which complete release to select now | Mutable pointer or default catalog |

`moly-asset-content/1` is a hash projection, not another stored object or CAS. It contains `transforms` and sorted entries containing `path`, `bytes`, `content_sha256` and `xf`. Consequently two packages with the same logical decoded contents and transform semantics share a `content_id`, even when their transfer encoding differs. Package manifest hashes also agree when their representations and encoder receipts agree. A content change affects its blob, owning package and selecting release; unrelated packages retain their identities.

`moly-asset-manifest/2` excludes release version, creation time, region, provenance and deployment prefixes. `moly-asset-packs/2` is the release itself; no redundant release wrapper surrounds it. Group IDs, kinds and dependencies live in that catalog. Package manifests can therefore be shared by releases with different labels or dependency selection.

Source provenance is preserved in the release `provenance` object. `pack.groups --provenance` accepts an explicit JSON object; otherwise `source.json`, when present, is captured under `runtime_source`. Existing region/game-version fields embedded in runtime artifacts remain actual content bytes because their consumers use them. Those particular artifacts can legitimately differ between regions. Build/extraction reports are excluded from runtime membership; no arbitrary fields are stripped from authored output to manufacture a better deduplication result.

A full Domain IR or FixtureDescriptor conversion is deferred. Existing Unity/domain artifacts preserve the authoritative facts, and this storage change does not need to change runtime joins or rewrite `moly-game`. Descriptor consolidation can be evaluated independently against specific duplicate knowledge or load-time joins.

## Store and package boundaries

```text
asset-store/
  blobs/<first-two-hash-characters>/<blob-sha256>.<suffix>
  packages/<manifest-sha256>.json
  catalogs/<release-sha256>.json
  channels/<region>/<channel>.json
  asset-packs.json
  .publish.lock
```

Blobs, packages and archived catalogs are immutable. Channels use `moly-asset-channel/1` and name an archived catalog. `asset-packs.json` contains the current default catalog, not a hash-only pointer. Publishing CN and then JP retains both regional channels but makes JP the default. Pin a catalog or select the intended region's channel when a particular region matters.

`groups.runtime_paths` owns runtime membership, including fixture thumbnails and custom fixture models. Packages follow character, furniture, site, weather, common-data and audio-bank families. Oversized families split by successive SHA-256 prefixes of the logical path, with sorted paths in each shard. The target is at most 4,096 entries and an 8 MiB metadata estimate; the final serialized document must still fit the actual reader limit. Dependencies on a family expand to its selected shards. Shard routing hashes are metadata partitioning, not asset identities or a new storage system. Changing a family enough to split a bucket can change its package boundaries; existing logical asset paths remain unchanged.

## Deterministic builds and supported representations

Immutable package/catalog JSON is UTF-8 with sorted object keys, compact separators and one final LF. Package entries sort by logical path; authored array order remains semantic. The cross-language content projection permits nonnegative safe integers, strings, booleans, null, arrays and objects; floating-point transform parameters are rejected. This is the documented serialization contract, not a claim of RFC 8785 compliance.

Shared JSON and GLB emitters also sort object keys while preserving authored arrays and binary geometry. Strict JSON/GLB emitters reject nonfinite numbers; the established domain JSON writer preserves authored infinities/NaN as their explicit string spellings before sorting. Artifact tests vary mapping assembly order and output location. Package tests vary source location, traversal order, metadata and warm/fresh rebuilding. Release builders check source file stamps before publication to reject ordinary concurrent source changes. Build from stable input trees; file stamps are not a general transaction across arbitrary upstream writers.

Gzip uses an explicit zlib profile with fixed timestamp and platform header bytes. Manifests record encoder implementation/version and parameters. Warm reuse requires a matching profile and verified stored bytes. Different encoder versions may produce different representation hashes; decoded content identity remains separate. Offline migration records unknown historical encoders as `legacy-v1` / `unrecorded` rather than fabricating reproducibility metadata. Optional Vorbis export retains quality 4 and uses FFmpeg bitexact muxer/codec flags without inherited container tags. Actual repeat/relocation tests produce identical Ogg bytes and preserve the previous decoded PCM samples; this holds for the same FFmpeg/libvorbis build, not arbitrary encoder upgrades.

The runtime group profile uses gzip for JSON, GLB and BIN, and identity encoding otherwise. Rust/Web packed readers support these two codecs, identity HTTP encoding and no asset transform. Generic pack tooling can describe additional codecs/transforms for analysis; those are not a promise that the runtime supports them. A migrated store must use the runtime profile to load directly; rebuild unsupported legacy representations from the runtime source when necessary.

## Build, verify and publish

Commands below run from the moly-root checkout. Example directories are operator inputs, not included game assets. Install the project or set `PYTHONPATH=src`; PowerShell uses `$env:PYTHONPATH = 'src'`.

```text
python -m pack.groups --src runtime-cn --out asset-store --region cn --version 6.0.0 --channel stable
python -m pack.groups --src runtime-jp --out asset-store --region jp --version 6.8.1 --channel stable
python -m pack.verify --out asset-store
```

Both builds write into the same CAS. Choose version labels from the actual source facts: acquisition-folder names need not equal the master/runtime version. An explicit provenance file is supplied with `--provenance provenance-cn.json`. Region selectors use lowercase portable IDs; the Web stage currently supports CN, JP, TW, EN and KR. Catalog versions are opaque, while existing Web source descriptors still use their established game-version format.

Read `channels/cn/stable.json` or `channels/jp/stable.json` to obtain the pinned `catalogs/<sha256>.json` path. To verify a particular archived selection while retaining all active/history checks:

```text
python -m pack.verify --out asset-store --catalog asset-store/catalogs/<sha256>.json
```

Import a release into another shared store:

```text
python -m pack.publish --src asset-store --out publication/asset-store --catalog <sha256> --channel stable
```

The publisher verifies the source graph, checks each copied or reused object, verifies the destination release and only then moves selectors. Its JSON result counts copied/reused package and blob objects and bytes; catalog selector bytes are not included in those counters. It copies files instead of hardlinking mutable source storage. Cooperating builders, publishers and GC use `.publish.lock`. Each selector replacement is atomic; a crash between regional/default updates can leave different complete selections, not a partially published package graph. There is no all-channel transaction.

For an existing v1 store:

```text
python -m pack.migrate --out legacy-store --region cn --channel stable
python -m pack.verify --out legacy-store
```

Optional migration flags are `--version`, `--provenance` and `--catalog` (a path relative to the store). Migration first verifies the legacy catalog, keeps its exact archive and package history, creates v2 manifests/catalogs and re-encodes zero blobs. It does not make unsupported codecs or transforms acceptable to the runtime. V1 reader rejection and offline migration are tested separately.

## Native and Web selection

Native selection uses the same store for every region:

```powershell
$env:MOLY_ASSET_ROOT = (Resolve-Path asset-store).Path
$env:MOLY_ASSET_CATALOG = '<64-character-lowercase-catalog-sha256>'
```

Start the consumer normally with those variables. With no `MOLY_ASSET_CATALOG`, an `asset-packs.json` under the root selects packed mode and its current default release; a root without that file selects loose mode. A pinned hash is checked against actual archived catalog bytes. Missing/corrupt catalogs, manifests or blobs are errors, with no fallback to another region or loose source.

Web uses a canonical same-origin directory URL plus the pinned catalog:

```text
?assets=/moly/asset-store/&packs=1&asset_catalog=<sha256>
```

The embed options carry the same selection as `assets`, `packs: true` and `assetCatalog`. The snapshot menu uses `assetBase` and `assetCatalog`; two regions sharing a base URL remain distinct selections. `moly://` paths never include those hashes. Browser prewarming and DOM images resolve through verified decoded bytes, while Bevy/WASM loads through the Rust reader.

The Moly development server reads `MOLY_ASSET_PACK_ROOT` for the shared store and still requires `MOLY_ASSET_ROOT` for its loose development mount. `node web/serve.mjs 8000` exposes the store at `/moly/asset-store/` and discovers its regional `stable` channels.

For a stage publication, first import selected catalogs with `pack.publish` into the publication directory's `asset-store/`. Build the consumer's Web engines with its normal `node web/build-wasm.mjs` workflow. Then run `node web/release-artifact.mjs --config publication.json` in the consumer repository. The configuration uses these real fields:

```json
{
  "workspace": "consumer-checkout",
  "output": "publication",
  "sources": [
    {
      "region": "cn",
      "assets": "runtime-cn",
      "catalog": "browser-catalog-cn.json",
      "assetCatalog": "<64-character-lowercase-catalog-sha256>"
    }
  ]
}
```

Paths resolve from the publisher process's working directory; choose an output outside the consumer source tree. `catalog` above is the existing browsing projection, distinct from the CAS release catalog selected by `assetCatalog`. The runtime directory must contain the prepared browser descriptors and match the pinned store's decoded artifacts exactly. The publisher checks every selected object and source artifact, required browser-base paths and stage build receipts before changing discovery. Published snapshots share `/moly/asset-store/`; they do not copy binary payloads into individual snapshot directories. Existing pinned remote loose-source provisioning remains an acquisition mechanism, not another CAS interpretation.

Serve blobs, packages and pinned catalogs with immutable caching and no transformations. Mutable discovery, `asset-packs.json` and channel documents must use `no-store`. The service worker recognizes the shared immutable URLs across snapshots and verifies their addressed bytes before retention. Storage denial is handled as a cache failure, not a reason to bypass asset integrity. Offline reads are guaranteed only for objects actually retained within the bounded cache.

## Retention, retirement and rollback

GC roots are the active default catalog, every valid regional channel target and every retained `catalogs/*.json` archive. Verification of any invalid/missing root fails the GC operation. Reachability includes package manifests and blobs. A retained historical catalog keeps its objects even when no channel currently points to it.

```text
python -m pack.gc --out asset-store
python -m pack.gc --out asset-store --apply
```

The first command reports only; the second removes recognized unreachable blobs and package manifests under the publish lock. Neither implicitly retires catalog history. To deliberately remove a historical root:

```text
python -m pack.gc --out asset-store --retire <historical-sha256>
```

**`--retire` deletes that archive immediately even without `--apply`.** It refuses the active default and channel targets. Inspect the returned reachability report before a subsequent collection. Keep all release archives still referenced by deployed Web snapshots or external pinned clients: GC does not discover consumer deployment manifests or remote browser pins. Retiring and collecting such a release intentionally ends its availability.

Rollback/promotion uses the verified Python API; `pack.release` has no separate CLI:

```python
from pack.release import activate
activate("asset-store", "<64-character-catalog-sha256>", channel="stable")
```

This revalidates the retained graph and moves that release's regional channel and the default. Roll back before retiring the desired catalog; a collected release must be republished from a verified copy.

## Limits and validation scope

| Bound | Value |
| --- | --- |
| Catalog or package JSON bytes | 16 MiB |
| Encoded blob bytes | 128 MiB |
| Decoded artifact bytes | 256 MiB |
| Rust application-owned in-flight transfer/decoded buffers | 512 MiB, 8 admission slots |
| JavaScript prewarm read reservations | 512 MiB |
| Optional service-worker retention | 512 MiB total; 128 MiB per object |

Readers enforce declared encoded and decoded lengths, caps and SHA-256 values. There is **no compression-ratio rejection rule**: a small valid compressed artifact can expand up to its declared capped size. The Rust scratch buffer lives on the heap, and budget arithmetic must remain valid on wasm32. Transfer budgets do not cap total application memory: parsed metadata, Bevy-loaded assets, GPU resources, browser internals and caller-retained decoded results have separate lifetimes. The JavaScript and Rust budgets are separate admissions, not one combined browser-process limit.

Run producer and integration-delivery tests from moly-root:

```text
python -m pytest -q
```

The suites cover release/provenance-only identity stability, changed-content propagation, fresh/warm/reordered builds, strict verification and corruption, historical/channel GC, rollback, migration, artifact determinism and exact-preimage consumer delivery. Tests requiring private extraction inputs remain explicitly conditional; synthetic fixtures are not a claim of a complete independent game extraction.

After preparing or installing the consumer described in [integration/README.md](../integration/README.md), set `MOLY_CONSUMER_WORKSPACE` to that tested checkout and reproduce native/WASM conformance:

```text
cargo test --manifest-path integration/moly-pack-probe/Cargo.toml
cargo build --manifest-path integration/moly-pack-probe/Cargo.toml --target wasm32-unknown-unknown --lib
python tools/generate_asset_v2_fixtures.py --out .validation/public-fixtures --limits
```

Use `wasm-bindgen` matching the probe's resolved dependency to generate its Web module. Run the consumer's complete `npm --prefix <consumer>/web test`, and `cargo check --manifest-path <consumer>/Cargo.toml --target wasm32-unknown-unknown -p moly-assets -p moly-app`. Browser execution uses:

```text
node integration/browser-pack-conformance.mjs --consumer <consumer> --wasm <generated-probe-web-directory> --fixtures .validation/public-fixtures --output .validation/browser-conformance.json
```

Chromium/Playwright comes from the consumer Web dependencies. For real corpus probes add `--real-report <real-pack-report.json> --store <measured-store>`. Asset IO conformance exercises actual Rust/WASM reads, JavaScript verification, DOM image decoding, corruption/missing-object failures and shared-cache offline reads. It does not claim a full rendered gameplay acceptance test; publisher unit fixtures also use a minimal stage ABI fixture to isolate publication behavior.

The [validation guide](asset-architecture-v2-validation.md) describes the reproducible checks. Measure storage savings against the inputs and output objects of each build; synthetic conformance tests establish protocol behavior, not expected savings for a particular asset collection. Keep input-derived reports with the private input data.
