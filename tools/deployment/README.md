# Direct-URL deployment

`prepare_deployment.py` audits exporter-owned membership and stages a publication
without any cloud API calls. `upload_oss.py` is the existing bounded OSS uploader,
now versioned here; its default is dry-run. It keeps its explicit target,
immutable overwrite guard, hash checks and separately authorized mutable updates.
Neither script contains credentials. The former scratch entry points delegate here.

An independent `upload_s3.py` adapter is available for explicitly configured S3
destinations. It does not change the OSS defaults or activate a migration. See
[S3-MIGRATION.md](S3-MIGRATION.md) for the commands, capacity limits and remaining
browser resource-base integration work.

Install the optional deployment dependencies from a clean checkout first:

```powershell
python -m pip install -e ".[deployment]"
```

This installs Brotli and the OSS SDK (locally verified versions: Brotli 1.2.0,
oss2 2.19.1). Without Brotli, `best` visibly warns and falls back to gzip/identity;
without oss2, dry-run still works, while authentication/execute gives an explicit
dependency error. Installing dependencies is not upload authorization.

```powershell
python tools/deployment/prepare_deployment.py --publication PATH_TO_NEW_PUBLICATION --output PATH_TO_FRESH_STAGING --encoding best --stage
```

The default `best` policy generates gzip-6 and Brotli-5 candidates for GLB, RGBA8,
ordinary BIN/WASM and text. It chooses only a representation saving at least 64
bytes and 1%. Client-decoded pack blobs (`.gzz`/`.brz`) and compressed media,
recognised by suffix or by signature (gzip, zip, zstd, PNG, Ogg, JPEG, KTX2), are
not compressed again; an identity pack blob is ordinary content.
Valid existing `.gz`/`.br` sidecars remain candidates. Every candidate must decode
to the original source bytes. `--encoding gzip` avoids Brotli; `identity` disables
HTTP compression; `--existing-encodings-only` retains the old selection-only mode.

`build_store.py --source REGION=PATH --store DIR` builds one region's store from
a release root. Its membership is the same browser membership this script audits
for that root, and it prints the catalogue SHA-256 to pin as `assetCatalog`:

```powershell
python tools/deployment/build_store.py --source cn=PATH_TO_CN_ROOT --store PATH_TO_PUBLICATION/asset-store
```

A snapshot published with `packs` names its pinned `assetCatalog`. Its assets are
not copied below `snapshots/<id>/assets/`: exactly the objects that catalog reaches
(`asset-store/catalogs/`, `asset-store/packages/`, `asset-store/blobs/`) are staged,
and the audited `--source` membership must equal the catalog's logical paths. Mutable
store selectors are never published. A schema 2 catalogue index lists its shared
`catalog-store/<sha256>.json` detail bundles, which are staged with it. Every such
object's decoded SHA-256 must equal its name, and store objects carry `no-transform`.
Store blobs have no media suffix; their Content-Type comes from the PNG, Ogg or
glTF signature, otherwise `application/octet-stream`.

Generated files go under `output/representations/`, never next to source assets.
Staging hardlinks must never be edited in place. Use new release/snapshot identities
for a changed immutable HTTP representation; do not overwrite already-published
objects just to change compression. No `.gz` or `.br` suffix is added to a URL.

The existing routing is unchanged: browser `/moly/<suffix>` maps through ESA to
OSS `moly/alpha/<suffix>`. These are respectively the prepare script's default
`--url-prefix` and `--object-prefix`, matching the current resource-final upload
manifest. The bounded uploader still accepts only its existing bucket/region/
prefix; this change does not alter a production target or configure ESA routing.

The upload manifest records:

- unchanged logical URL and original Content-Type;
- encoded bytes/SHA256 and decoded bytes/SHA256 separately;
- `deliveryMode: fixed`, `requiredAcceptEncoding: br/gzip` (null for identity),
  exact Content-Encoding (omitted for identity), and `no-transform` for pre-encoded responses;
- the deterministic encoder settings and selected local representation.

Content-Length is the encoded byte length. The uploader transmits the listed
headers and decoded accounting metadata and verifies them with HEAD before
accepting a stored object. Each OSS URL stores one fixed representation; it does
not negotiate based on Accept-Encoding. Vary is neither required nor verified
for this mode. A CDN must not add a second compression layer to an already encoded
object. Clients must support the declared requiredAcceptEncoding; use a gzip or
identity policy for a client population that cannot accept Brotli. Negotiated
delivery is not implemented; it would need real variant selection and Vary.

For cross-origin Fetch, Content-Encoding is hidden unless CORS exposes it, while
Content-Length can remain visible. That length is still the encoded wire length,
not the decoded response-body length. Runtime readers must not treat it as a
decoded-size bound merely because the encoding header is inaccessible.

This reduces network bytes only. Browser HTTP decoding restores original hashes;
the existing decoded CacheStorage and live CPU/GPU assets do not become smaller.
The old `moly-deploy` viewer/R2 pipeline uses a different client-side `.glb.br`
contract and is not used or modified by this direct-URL deployment path.
