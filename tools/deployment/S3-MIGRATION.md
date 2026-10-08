# S3-compatible resource publication

This guide describes the repository's S3 adapter and the consumer contract.
Endpoint, bucket, region, credentials and capacity are deployment configuration;
the examples below name no live destination or published snapshot.

## Resource URLs and object keys

Keep a single, normalized HTTPS resource base, including its bucket path or
object prefix when the provider uses one.

| Meaning | Example |
| --- | --- |
| Public resource base | `https://objects.example.invalid/resource-bucket/` |
| Logical resource | `/moly/snapshots/<snapshot-id>/assets/<path>` |
| Public URL | `<public-base>snapshots/<snapshot-id>/assets/<path>` |
| Bucket | `resource-bucket` |
| Object key with an empty prefix | `snapshots/<snapshot-id>/assets/<path>` |

The bucket segment is not part of the object key. With a nonempty object prefix,
include it in both the uploader configuration and the public base. Strip the
logical `/moly/` prefix before joining its suffix to that base: resolving an
absolute `/moly/...` URL directly would discard the base's path.

The host, runtime loader, coordinate preflight, pack client and cache worker
must use the same mapping. Preserve same-origin SDK, iframe and service-worker
entry routes, and admit remote resources only beneath the configured base.
For hosts that embed public configuration at build time, rebuild the host after
changing that configuration.

## Portable source metadata

`source.json` is a runtime input: coordinate preflight and master-table region
selection read it. It must remain in the package, but local extraction paths
and journals must not be published. Release provenance keeps only explicit
source identities, source hashes, coordinate markers and relative UI receipts.

New JP extraction writes a descriptor SHA-256 instead of local output paths.
For an older extraction, first copy the resource tree to a fresh staging root,
then generate its portable source document without altering the original:

```sh
cp -R SOURCE_ROOT CLEAN_ROOT
python -m pack.groups --sanitize-source SOURCE_ROOT/source.json --source-copy CLEAN_ROOT/source.json
```

The conversion prints original and public SHA-256 values. Unknown journal fields
are omitted. Invalid location-bearing identity fields or absolute resource paths
are refused rather than guessed; correct those identities in the separate copy.
Use the clean root for package creation and a new immutable snapshot identity.

## Adapter preparation and credentials

`prepare_deployment.py --provider s3` emits `moly-object-upload/1`; the S3
network adapter is `upload_s3.py`. OSS authentication, metadata names and
overwrite headers are not interchangeable with S3 equivalents.

Confirm the authenticated API endpoint, signing region and addressing style
against the destination service. A public download URL does not establish
those settings. Use the standard AWS SDK credential chain or `--profile`.
Keep credentials out of commands, source files, manifests and logs.
Cloud modes require `boto3>=1.42,<2`; local dry-run does not import boto3,
instantiate a client, read credentials or contact the destination.

```sh
python tools/deployment/prepare_deployment.py --publication NEW_PUBLICATION --output FRESH_STAGING --provider s3 --object-prefix= --url-prefix /resource-bucket/ --stage
python tools/deployment/upload_s3.py --manifest FRESH_STAGING/upload-manifest.json --endpoint https://objects.example.invalid --bucket resource-bucket --region CONFIRMED_REGION --object-prefix= --scope stable
```

The second command is a dry-run. `--inspect` performs signed read-only planning.
Writes require explicit `--execute --exclusive-writer`. Release scope also
requires `--release-id`; mutable scope requires one `--mutable-path`,
`--allow-mutable-update`, and the expected previous encoded SHA-256 when
replacing an object. Scope selection must explicitly cover the publication's
packed objects; do not assume snapshot directories enumerate an `asset-store`.

## Writes, capacity and receipts

The planner counts encoded objects across the whole bucket against the
adapter's enforced capacity limit. Its report states that limit; it is not a
measurement of the provider's quota. Unknown versioning or multipart status,
enabled or suspended versioning, existing multipart uploads and plans exceeding
the limit block execution. A replacement conservatively counts its entire
incoming body as additional space. Planning does not reserve capacity or delete
objects.

Uploads use bounded worker concurrency and single `PutObject` requests. Create
uses `IfNoneMatch="*"`; mutable replacement uses the observed ETag in `IfMatch`
after checking the expected SHA. Missing SDK support or rejected conditions
cause failure, without unconditional-write or multipart fallback. A 409/412
is accepted only after a fresh read confirms the entire requested representation.
Receipts are scoped by provider, endpoint, bucket, region, prefix and manifest
digest; resuming rechecks remote objects instead of trusting a prior receipt.

Preserve original Content-Type, encoded Content-Length, the declared
Content-Encoding and Cache-Control. Store `moly-sha256`,
`moly-decoded-sha256` and `moly-decoded-bytes` as S3 metadata. ETag does not
replace SHA-256. Do not add `.br`/`.gz` to logical URLs or recompress an already
encoded representation at an intermediary.

## Provider and browser checks

Some S3-compatible proxies vary HEAD metadata with Accept-Encoding or reject
a signed HEAD after rewriting its method. The adapter requests the stored
representation and can use a signed one-byte ranged GET after a HEAD 403;
Content-Range supplies the total stored length. Verify this behavior against
the intended provider. Also check that SDK uploads do not leave
`Content-Encoding: aws-chunked`, which browsers cannot decode.

CORS must permit the actual website origin and expose Content-Encoding,
Content-Length, ETag and the three `x-amz-meta-moly-*` headers. In particular,
the worker needs decoded-byte metadata for retention accounting. Configure
allowed request headers for the client actually being deployed; legacy clients
may send `X-Moly-Required`, while current clients announce required objects by
service-worker message.

Before switching traffic, verify immutable and conditional writes, encoded and
decoded hashes, SDK/WASM loading, snapshot paths, thumbnails, and a real
play/stop/replay flow using the configured base. Check cold and warm retention,
offline reads after completed retention, and scoped clearing. HEAD or CORS
success alone does not establish these behaviors. Keep validation receipts
outside the public source repository.
