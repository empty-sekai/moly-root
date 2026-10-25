# Reproducible extraction tasks

The extraction pipeline follows the same separation as
[nnnotes](https://github.com/MetaSekaiLab/nnnotes): versioned sources, reusable
tasks, immutable results, and product layouts. The existing Moly pack v2
contracts and logical runtime paths remain the publication boundary.

`cli` parses options and calls library code. `source` records and checks input
bytes. `pipeline` composes the existing domain readers, text identity registry,
official-text matcher and display products. `pack` owns the shared blob store
and release retention. Legacy domain exports remain available; they have not
all been converted into cached tasks.

## Sources and plans

```sh
moly source record --region jp --version VERSION \
  --input talks=inputs/talk.bundle --input constants=inputs/lib.bundle \
  --out source.json
moly source verify source.json

moly export talks --master inputs/master --bundle inputs/talk.bundle \
  --lib inputs/lib.bundle --depot asset-store --out extracted \
  --plan --emit-task talks-task.json
moly depot run --depot asset-store --task talks-task.json
```

The source receipt and task descriptions contain local input locators. Keep
these in build storage, outside runtime publication. Copying them to another
machine requires replacing their locators with paths to the same bytes; this
does not change the task key. Source receipts identify a caller-supplied region
and version and do not authenticate a publisher or discover new game files.

`--family fixture` selects the existing furniture dialogue reader. The default
includes solo, birthday and ordinary multi-character conversations. Both use
the existing script and constant readers. The Unity fallback version is an
explicit parameter, not evidence of the actual bundle's engine version.

`moly export communication-actions --master inputs/master --bundle inputs/communication.bundle
--depot asset-store --out extracted` exports the separate per-character
communication motion loops. It retains participant conditions and random
branches; these scripts contain presentation actions rather than spoken text.
The manifest-driven extractor also routes this family automatically.

A task key hashes its stage/version, subject, normalized parameters, actual
input hashes and sizes, used implementation/dependency identifiers, and
consumed context. Locators and output directories do not affect it. Source
module hashes distinguish unreleased edits as well as installed versions.
`depot plan --previous old-task.json` explains which key components changed.

`depot run` rejects incompatible installed stages, changed input bytes, corrupt
cached outputs and forged task keys. It verifies and freezes inputs before
calling readers. `--force` recomputes a result and refuses different output for
the same key. Failures leave no committed result. This first implementation
runs one task at a time under the store's existing publication lock.

## Text products

```sh
moly export text-catalog --corpus extracted/talks.json \
  --corpus extracted/fixture-talks/talks.json --region jp \
  --source-revision SOURCE_REVISION --previous prior/text-source.json \
  --depot asset-store --out text-jp
moly export official-text --source text-jp/text-source.json \
  --donor text-cn/text-source.json --locale zh-CN \
  --depot asset-store --out official
moly export text-runtime --source text-jp/text-source.json \
  --overlay official/official-zh-CN.json --depot asset-store --out display
```

Omit `--previous` only for the first catalogue. The previous source catalogue
is the durable identity registry used for future updates; preserve it and its
retired history. Text identities and source-change handling are described in
[text localization](text-localization.md).

`text/index.json` is a `moly-text-runtime/1` root. Hashing a script key as UTF-8
SHA-256 and taking the first two hex digits selects one of at most 256 routing
buckets. Each `moly-text-routes/1` bucket maps script keys to a display source.
Every reference carries `path`, `sha256` and
`bytes`. Objects live at `text/objects/<sha256>.json`; discovery is written last.

Display sources carry only original text, identities, source hashes and current
step bindings. A script's linked tweets are in the same display source. The
complete registry, editor context and retired history are not downloaded for
each playback. Shards have a 2 MiB bound. Unchanged scripts retain identical
bytes when another source script or translation changes. The source root never
contains a translation revision. Publish only `text/` with the resource snapshot.

Official overlays are a separate tree at `translations/<region>/<locale>/`.
Its `latest.json` (`moly-text-publication/1`) and archived
`revisions/<revision>.json` use the same 256-bucket routing. Translation buckets
(`moly-text-translation-routes/1`) map script keys directly to overlay references;
all references are relative to that locale directory. Publish this tree
independently. Editing translations changes neither the source index nor the
resource snapshot. Sessions pin a publication, immutable objects are reusable,
and source ID/hash compatibility is checked per line, not by requiring the
whole source registry revision to match. Retain historical publication roots
and their objects for playback pins and rollback.

Catalogue browsing uses optional `searchBuckets`: 16 shards selected by the
first hash digit. Source rows are `[id, sourceHash, kind, text]`; translation
rows are `[id, sourceHash, text, source]`, grouped by script. These compact
indexes let hosts translate titles and search all dialogue without fetching
each playback document. Source and translation indexes remain in their
respective publications. A translation edit changes its leaf, route and search
shard; unrelated shards and all resource snapshot bytes remain identical.

Prepare official text for the existing S3 uploader independently:

```sh
python tools/deployment/prepare_translations.py \
  --publication display/translations/jp/zh-CN --output translation-staging \
  --object-prefix sekai-extra-assets/ --url-prefix /moly/
```

Use the resulting `upload-manifest.json` with `upload_s3.py` and the existing
destination options. `--scope stable` selects immutable objects and the archived
revision root. After these have been uploaded and verified, `--scope mutable
--mutable-path translations/jp/zh-CN/latest.json --allow-mutable-update` selects
only the current pointer. Replacing an existing pointer also requires
`--expect-previous-sha256` for the exact previous bytes. Dry-run remains the
default; `--execute --exclusive-writer` explicitly enables writes. Retain old
objects and roots for rollback; ordinary resource deployment excludes this tree.
This preparation path handles text-only publications. Fonts use the independent
catalogue described below rather than being copied into translation revisions.

These exports prepare inputs for the editorial and playback systems. They do
not publish translations or grant approval to drafts.

## Independent font catalogue

An operator prepares the approved open font once. Translators continue to enter
text; they do not upload font files. Coverage is read from each supplied font's
character map, and the base font is identified by the exact UI font hash:

```sh
pip install 'moly-root[fonts]'
moly export text-fonts --base-font UI.subset.ttf --font OPEN-FONT.ttf \
  --license OFL-1.1.txt --out-dir font-publication/fonts
python tools/deployment/prepare_fonts.py --publication font-publication/fonts \
  --output font-staging --object-prefix sekai-extra-assets/ --url-prefix /moly/
```

`fonts/latest.json` and `fonts/revisions/<revision>.json` contain the
`moly-text-fonts/1` catalogue, base-font coverage, and immutable font and license
references under `fonts/objects/`. Upload stable objects first, then use the
existing uploader's explicit mutable scope for `fonts/latest.json`, checking
the previous pointer hash when replacing it. No font object changes when a
translator changes text. Retain historical catalogues and referenced objects.
Set NextTrans's `MYSEKAI_FONT_CATALOG_URL` to the public catalogue so its coverage
check and the stage use the same approved fonts. This command only prepares
files and does not deploy or configure the translation service.

## One store, two kinds of roots

```text
asset-store/
  blobs/ab/<sha256>.bin       extraction bytes (existing identity codec)
  tasks/<task-key>.json      committed moly-result/1, with verified artifacts
  packages/                 existing release packages
  catalogs/                 existing retained releases
  channels/                 existing release selectors
```

Task results contain key parts and artifact records, never local input paths
or timings. Both extraction results and retained release catalogs protect
their referenced blobs. `moly depot verify --depot asset-store` verifies all
task results; `python -m pack.verify --out asset-store` verifies release roots
and task roots together when a release exists. `moly depot gc` uses the same
collector as `pack.gc`; it reports only unless `--apply` is passed. It refuses
collection on corrupt roots or an incomplete release layout. Task history is
retained; this interface does not implicitly retire prior source registries.

`depot layout --key KEY --out DIRECTORY` copies verified artifacts into an
editable output directory, separate from the store. It never hard-links that
directory to shared immutable blobs. Use the existing pack/group publication
workflow for runtime releases. Content/representation identity, JSON number
semantics, existing domain schemas and release channels are unchanged.
