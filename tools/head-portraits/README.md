# Head-only chara portrait exporter

This tool exports the actual `examples/viewer` toon/facial/stencil renderer as
transparent 512 × 512 RGBA portraits. It selects only triangles whose three
vertices have at least 0.999 total skin weight on `Head` or its descendants.
Shoulders, torso, sleeves and unskinned meshes are removed before rendering.
The camera is derived from the retained geometry, with a 35° field of view and
crown-to-chin framing. Long trailing hair may leave the lower edge; the crown
and both side edges must remain clear. There are no character-specific camera
overrides and no cropping of a half-body image.

## Install

Requires Node.js 20+, Python 3.11+, and a working WebGL2 browser. Install the
pinned export dependencies from the repository root:

```sh
cd tools/head-portraits
npm ci
npx playwright install chromium
python -m pip install -r requirements.txt
cd ../..
```

`--channel chrome` may instead use installed Google Chrome. Omitting it uses
Playwright's bundled Chromium. The manifest records the actual browser version
and WebGL renderer. `--python /path/to/python` selects a Python environment.

## Export and verify

Provide a runtime extraction containing `manifest.json`, `mysekai-fixtures.json`,
`facial-tables.json`, the listed GLB/rig files and the viewer's pose dependencies
(normally `motion-library.glb` and its index). Embedded/external model textures
must resolve inside this source directory. Region and game version come from
`mysekai-fixtures.json`; `--region` is a required cross-check and `--version` is
an optional additional assertion. Missing rig/facial or pose data fails export.

```sh
node tools/head-portraits/export.mjs --source local-data --out ../portraits-cn --region cn --version 6.0.0
node tools/head-portraits/export.mjs --source /path/to/jp/runtime --out ../portraits-jp --region jp --version 6.8.1
python tools/head-portraits/finalize.py --source local-data --out ../portraits-cn --verify
```

The source manifest supplies the entire roster; no fixed count or list of
characters is embedded in the tool. `--units 1,17` exports an explicitly selected
subset, retaining `expected` and `rosterCount` to distinguish it from complete
coverage. Unit IDs come from each rig, falling back to the viewer's existing
`sd_<unit+100>` convention when the rig ID is null. Variants stay separate.

The CLI starts and closes its own loopback HTTP server on an available port,
mounts the source and viewer directories, captures a fresh page for each unit,
and runs final validation. Output must be new or empty: existing publications
are never overwritten. On failure, the directory retains diagnostics and any
completed captures but no valid final manifest; use a new output directory to
retry after correcting the cause.

Output contains:

- `unit-<id>.source.png`: untouched 1024 × 1024 WebGL RGBA capture at 2× sampling.
- `unit-<id>.png`: Pillow LANCZOS downsample to 512 × 512, without cropping.
- `capture.json`: frozen camera, selected mesh triangle counts, facial state,
  capture hashes, source hashes and render environment.
- `manifest.json`: generator `moly-root-chara-head-v1`, source provenance,
  model/rig and image SHA-256, `bodyGeometry: false`, coverage and alpha metrics.
- `qa-light.png` / `qa-dark.png`: contact sheets for visual review of every head.

All served source files and viewer JS/HTML/CSS files are hashed. The server
rejects changes to already observed inputs, and the inventories are checked
again after capture. Tool code, dependency lock and Pillow version are recorded
too. Finalization verifies all source/model/rig/capture hashes and identities,
rejects empty images, checks RGBA dimensions and requires zero nontransparent
pixels at the top/left/right boundary. `--verify` is read-only and additionally
checks final PNG bytes and the full manifest against recomputed values. Optional
viewer resource 404 paths remain visible in `optionalMissingResources`; they
are not silently discarded. Required portrait input failures abort the batch.

No timestamps, absolute machine paths, temporary ports or run IDs enter the
manifest. With the same browser/GPU, source, renderer, tool code and Pillow
versions, repeated runs can be compared byte-for-byte. Different GPUs/drivers
or browser builds can produce small rasterization differences; compare the
recorded environment before treating a PNG hash difference as source drift.
Input hashing proves provenance, not visual correctness: review both sheets.

## Tests

```sh
node --test tools/head-portraits/export.test.mjs
python -m pytest tests/test_head_portrait_export.py -q
```

The tests cover geometry/body separation, trailing-hair framing, roster identity,
region/version mismatches, invalid source paths, source hash drift, alpha and
clipping checks. Real browser export remains the final acceptance check.

Generated images and extracted game data are not committed, in line with the
viewer data policy. Keep outputs outside the repository; ship them through the
consumer's existing asset publication process.
