# moly-root Character Export Data Contract

[中文](data-contract.md)

The AssetBundleInfoNew package manifest must be supplied by the user. This repository neither bundles nor distributes any game data.

This is the public consumer contract for files emitted by `moly characters` and `moly facial-tables`. All JSON numbers are JSON numbers; quaternions use `[x, y, z, w]` and vectors use `[x, y, z]`.

## Coordinate System And Identity

Character output is glTF right-handed, Y-up, and in metres. Unity positions become `(x,y,z) -> (-x,y,z)`, rotations become `(x,y,z,w) -> (x,-y,-z,w)`, triangle winding is reversed, and UV V is written as `1-v`. Apply the reflection exactly once; reflecting twice restores source handedness and makes culling and animation directions wrong.

Node array indices are stable identities within one export. Names are labels, not unique keys; duplicate sibling names can occur. Use node indices, and paths where present, for skinning, cloth, and animation targets.

## `.glb`

The binary glTF contains the complete runtime character presentation. `asset.extras.coordinates` declares the reflected convention; `nodes[]` stores the prefab transform tree; `skins[].joints[]` stores renderer bone order; `inverseBindMatrices` stores glTF-space bind poses; mesh primitive material integers and order are authoritative; `TEXCOORD_0..2` already have V flipped; `COLOR_0` is optional; `materials[].extras` stores shader-role inputs and texture indices; `images[]` stores PNG buffers; and `animations[]` stores baked humanoid rotations, auxiliary twist rotations, and Hips translation.

The face renderer uses material order `[eye, mouth]`; the body renderer starts with `body` and may include `accessory`. Mesh primitive order is authoritative.

## `.rig.json`

The sidecar supplies data not modeled by glTF: `name`, `unitId`, `defaultEye`, `defaultMouth`, `eyeAtlas`, `mouthAtlas`, `materials`, and optional `cloth`.

`anchors` gives the attachment points on the character: keys are the field names that declare them in the model, values are the node names (currently `_headRoot` → `Head`, `_headTopRoot` → `HeadRoot`, `_spineRoot` → `Spine`, `_hipsRoot` → `Hips`, `_lightingHeadCenter` → `null`). A field that exists but points at nothing is `null`; a field that does not exist is absent — **"this rig has no such point" and "the exporter did not look" are different facts**.

An overhead item is parented to one of these anchors with a **zero local position** (do not lift it yourself); sprite items face the camera every frame at runtime. The exact correspondence between an overhead item's `view.anchor` (`Face` / `Spine` / `Hips`) and these fields is **not yet established**, so this repository does not assert that mapping.

Eye and mouth atlases contain `texture`, `cell`, `columns:4`, `rows`, `indexBase:1`, `clampMinIndex:1`, and per-cell UV offsets. Eyes are a 4x8 grid (2048x2048, 512x256 cells); mouths are a 4x4 grid (2048x1024, 512x256 cells). Select a cell with:

```text
if index < 2: index = 1
i = index - 1
col = i % 4
row = i >> 2
```

Use offsets only, never scale. `unityOffsetPerCell` has a negative Y component; `gltfOffsetPerCell` has a positive Y component. Keep static defaults distinct from closed-pattern fields; `lip01` through `lip16` map directly to the 16 mouth-atlas cells.

Each material role (`body`, `eye`, `mouth`, optional `accessory`) contains `name`, `floats`, `colors`, `textures`, and `renderQueue`. Preserve source float values without normalization or clamping. A missing texture property means absent, not an implicit replacement.

The cloth object contains coordinate-system and version declarations, components, colliders, statistics, and structural checks. Capsule `length` is a half-length; `startRadius` is the minus-direction end and `endRadius` the plus-direction end. Ignoring baked TRS or treating half-length as full length makes cloth unstable or doubles collider size.

## `characters.json`

The character registry and locomotion personality. **Its input is the caller's own master tables** (given with `--master <directory>`): this repository neither bundles nor distributes any master data. Without that input the file is not produced, and `extraction-report.json` carries a `derived` entry with `status: "skipped"` saying so rather than omitting it silently.

The top level is `version` (currently 1), `semantics`, `units`, `player`, `characters`, and `summary`.

**The caller decides the membership** — it is derived from the character bundle names in the manifest; this module never guesses who belongs in a pack.

`player` is either an object or `null`. It comes from the caller's `clientConfigs.json`, whose rows have `{id, type, value}`. `type` is `Int`, `Float`, `String`, or `Bool`; `value` is parsed according to that type. The player rows used by this contract are:

| id | Meaning |
|---:|---|
| `77` | Normal ground movement scale. |
| `78` | Harvest-area movement scale. |
| `95` | Dash speed rate. |

When all three rows exist, `player` contains `normalMoveScale`, `harvestMoveScale`, `dashSpeedRate`, `configRows`, and `derived`. `configRows` preserves the parsed values for these rows under string keys (`"77"`, `"78"`, and `"95"`). `derived.walkSpeedMetersPerSecond` equals `normalMoveScale`; `derived.dashSpeedMetersPerSecond` equals `normalMoveScale * dashSpeedRate`.

The player uses the normal scale on ordinary ground and the harvest scale in a harvest area. Dash is an explicit movement state and multiplies the active scale by `dashSpeedRate`. Joystick magnitude is preserved below full input, and a camera state that slows movement applies an additional `0.5` multiplier. These are movement semantics, not character locomotion values.

If `clientConfigs.json` is absent, or any of rows `77`, `78`, and `95` is absent, `player` is `null` and no default is inserted. The gap is recorded as `summary.missing.playerConfig`: a missing table is reported as `clientConfigs` followed by all three required ids; otherwise the missing ids are listed. Identity, locomotion, and alone-action entries are still emitted when their own source rows exist.

`characters[<unitId>]` holds:

| Field | Meaning |
|---|---|
| `unitId` | Character unit id. |
| `identity` | `gameCharacterId`, `unit`, `colorCode`, `skinColorCode`, `skinShadowColorCode1`, `skinShadowColorCode2`; `null` when the source row is absent. |
| `locomotion` | Locomotion personality, see below; `null` when the source row is absent. |
| `soloAction` | Name of this character's alone-action script (the scripts live in their own bundle); `null` when the source row is absent. |

`locomotion` first gives the **stored values**: `idleMotion`, `walkMotion`, `runMotion`, `walkSpeed`, `runSpeed`, `runOccurRate`, `pauseMilliSeconds`, `changeMotionMilliSeconds`; then the **runtime values**: `walkSpeedMetersPerSecond`, `runSpeedMetersPerSecond`, `pauseSeconds`, `changeMotionSeconds`.

> **The stored values are 1000× the runtime unit.** Right after reading the table the runtime divides these four by 1000, so a `walkSpeed` of 400 is **0.4 metres per second** and a `pauseMilliSeconds` of 15000 is **15 seconds**. Use the `*MetersPerSecond` / `*Seconds` fields; using `walkSpeed` directly moves characters 1000× too fast.

`runOccurRate` is the percentage chance of running rather than walking, as stored. All three motion names are keys of the shared motion library index — the same names the performance data uses.

`summary` gives `requested`, `withIdentity`, `withLocomotion`, `withSoloAction`, `missing` (character → which source kinds are absent), `motionsChecked`, and `motionsNotInLibrary` (character → motion names absent from the library index). **Every gap is reported, never defaulted**: in the current content all 31 members have identity and locomotion, one `soloAction` is absent (`unitId` 21), and all 31×3 motion names resolve in the library index.

## `facial-tables.json`

This is a JSON object whose values are arrays of row objects. The three public logical tables are defaults, eyes, and mouths, but keys are asset-defined; identify rows by fields rather than assuming filenames.

`defaults.CharacterUnitId` is an integer from 1 to 55. All 55 rows exist, but only 31 IDs have models; the rest must report an empty model rather than fabricate one. `EyePatternName` and `MouthPatternName` link eye and mouth rows. Muscle channels are not bounded to `[-1,1]`; observed source values are about `-12.06..+9.31`. Do not clamp them.

## `alone-actions.json`

Performance data: which motion pairs with which face, and when the face changes.
Motion and facial state are **independent channels** — a motion name implies no facial
state, and the pairing exists only in this data. Without it a consumer can only pin one
static default face, and the character reads as "right motion, wrong expression".

Top level: `version` (currently 2), `semantics`, `constantTables`, `constantScalars`, `units`, and `summary`.

`units` is grouped by the character's numeric identifier; each character carries:

- `scenarios[]` — mutually exclusive performances. **Never concatenate them into one
  timeline.** `trigger.kind` is one of:
  - `timeGated`: `timeLimitSeconds` (minimum spacing since the last time it fired),
    `probability` (0..1 roll), plus `motionSlot` and `slotMemorySeconds` (per-slot
    de-duplication window). Scenarios are evaluated independently of each other.
  - `randomBranch`: `low`/`high`/`weight` from a single 0..99 draw. The branch weights
    of one character sum to 1.
- `tail.steps[]` — the loop tail, which runs **every iteration** and belongs to no scenario.
- `constants` — the script's own threshold and slot constants, verbatim.

`steps[]` is in source order; every step carries `t` and `op`:

| `op` | Fields | Meaning |
|---|---|---|
| `animation` | `motion`, optional `phase`, `speed`, `playbackSpeed`, `playEndMotion`, `blend`, `alias`, `phaseSource` | `motion` is a key of the shared action index's `clips`; when `phase` is `S`/`L`/`E`/`O` play that phase segment, otherwise let the index's available phases decide. `speed` is the script-written playback rate; all 747 animation steps currently write 0. `playbackSpeed` is the rate actually used by the runtime, which reads 0 as 1.0, so play at this value. **Using `speed` directly will freeze the character.** When true, `playEndMotion` plays the motion's End segment after its main segment; it is false when omitted by the script. `blend` is the cross-fade duration in seconds when entering a new motion; every animation step has it, and an omitted script value uses the performance library default, currently 0.5. |
| `eye` | `pattern` | An eye-table `PatternName`; take that row's `OpenEyeIndex` / `CloseEyeIndex` for the atlas cell |
| `mouth` | `pattern` | A mouth-table `Name`; take that row's `OpenLipSyncIndex` / `CloseLipSyncIndex` for the atlas cell |
| `emoticon` / `hideEmoticon` | `name`, optional `showSeconds` | Overhead-item name; its assets live in a separate effect package, not in the character asset pack |
| `wait` | `seconds` | Advances the nominal timeline |

`t` is the **nominal timeline**: the sum of every preceding `wait`, i.e. the authored
moment. The runtime waits in integer milliseconds on a coroutine and does not await
motion calls, so real switch times carry quantization and frame-scheduling error. Treat
`t` as an ordering-plus-nominal-offset contract, not a frame-accurate schedule.

The performance library does not forward the fourth positional argument of its motion call, so `alone-actions.json` does not record it.

`alias` and `phaseSource` record the source constant name and exist only for provenance.
Phase aliases are spelled like the phase letters themselves, so whether a phase was
resolved can only be told from the presence of `phaseSource`.

The performance runs while the character is in its rest state with no cutscene active,
and interruption is cooperative: the loop exits at its own guard.

## `talks.json`

The single-character direct-talk corpus. **Requires the caller's own master tables** (`--master <directory>`): which character a talk belongs to, and the conditions under which it appears, exist only in master. Without that input `extraction-report.json` records the bundle as `unsupported` with the reason, rather than emitting a corpus of unclear scope.

The top level is `version` (currently 1), `semantics`, `units`, and `summary`.

**Selection predicate**: a talk's character group holds exactly one character, **and** its condition group asks nothing about furniture (furniture is not part of a character). In the current content that keeps **1412** of 6180 talks (4354 dropped for furniture conditions, 414 because the character group holds more than one), covering **30 characters** at 46–48 talks each. Both halves of the predicate are counted separately under `summary.filter.dropped`.

Each entry of `units[<unitId>].talks[]`:

| Field | Meaning |
|---|---|
| `talkId` | Talk id. |
| `lua` | Script name. The scripts live in the talk scenario bundle (one bundle for all talks); the asset name inside it carries **one extra `.lua` suffix**. |
| `siteGroupId` / `termId` | Site group and term; their value semantics are not established, so they are passed through as stored. |
| `conditions` | Condition types of this talk's condition group (furniture-gated talks are excluded by the predicate, so only phenomena, visit count, and event-story types appear). |
| `tweet` | `{id, text, motion, eye, mouth}`; `text` is verbatim and contains `\n`. **This is the second source of motion↔facial pairing, alongside the alone-action performance data.** |
| `voices` | Voice cue names referenced by the script. **The voice bytes are not in this bundle**, and this repository does not assert a cue-to-bundle mapping (not established). |
| `steps` | Ordered performance steps parsed from the script, each tagged with an `op`. |

A step's `op` is the script call name. Fourteen appear in the current content: `change_npc_eye` 5037, `change_animation` 3827, `change_npc_mouth` 3724, `label` / `text` / `wait_click` 3571 each, `voice` 3137, `look_at_body` 2823, `wait_time` 1413, `emoticon` 355, `show_talk_window` / `hide_talk_window` 2 each, `hide_emoticon` 1, and `wait_time_on_auto_mode` 1. The parser knows more call names than these fourteen; **every matched call must be accounted for in `steps` exactly once, and a mismatch raises** rather than dropping a step silently.

Motion playback rate follows the same contract as the alone-action data: `speed` is what the script passes and `playbackSpeed` is what the runtime actually uses (it reads 0 as 1.0).

**Constants are not resolved**: the talk scenario bundle carries no constant tables, so named constants such as `Characters.X`, `EyePresets.x`, `LipSyncPresets.x`, and `Motions.x` are **kept verbatim as strings** (an empty `summary.constantTables` says as much). This repository does not guess those mappings; supply your own constant tables if you need resolved values.

## `emoticons/`

Overhead-item (emoticon) effect packages. They are **shared across characters**: a
character asset pack references them by name and does not embed them. The export has one shared
`emoticons.json`; each texture is a separate PNG file. A performance step whose `op` is
`emoticon` names one of these keys.

The current content contains 53 items, 74 textures, 85 particle emitters, and 93 `unsupported` entries.

**Particle counts are genuinely tiny**: across the 32 particle items there are only 155 burst particles plus 12 rate-emitted ones, and the **median item emits 3 particles** (the largest emits 30, the smallest 1); of the 85 systems only 51 have an emission module, and `rateOverTime` is `0` in 47 of them (they emit through `bursts`). Each burst entry is `{time, count, cycleCount, repeatInterval, probability}`; **`cycleCount` of `0` means unlimited cycles** — the burst repeats every `repeatInterval` seconds (13 such bursts across 8 items in the current content), while a non-zero value is a fixed cycle count; the burst totals above are counted **per cycle**. Seeing two or three particles is **correct** — do not treat it as a rendering fault and raise the rate; `0` means `0`. The items comprise 21 `sprite` items and 32 `particle` items: sprite items have an Animator and `start`/`loop`/`end` clips; particle items have neither, because their visuals are particle emitters, exported as **emitter parameters** (shape, emission, lifetime, size and colour over lifetime, and the material they draw with) rather than as baked frames, for the consumer to simulate.

The top level of `emoticons.json` is `version` (currently 2), `semantics`, `items`, and `summary`. `summary` is `{items, textures, unsupported[]}`, and every `unsupported` entry carries an `item` field.

Extractor input has two roles. `mysekai/effect/emoticon/*` (or its equivalent double-underscore name) is an **item target**: each target produces one `items` entry. Explicitly supplied non-emoticon packages are **lookup-only sources**: they build the cross-package material and Shader indexes, produce no `items` entry, and do not count toward `summary.items`. Thus `mysekai/shader` is a lookup source only. An omitted dependency is not guessed and remains an explicit `external` value.

Each `items[<item name>]` entry contains:

| Field | Meaning |
|---|---|
| `viewKind` | `"sprite"` or `"particle"`. |
| `view` | `{class, kind, soundLabelType, soundInput}`; particle items also have `anchor` and `keepPosition`. |
| `animator` | `{node, loopEndFlag}`; `null` when there is no Animator. |
| `nodes` | Node tree, with parents first. Each item has `name`, `path`, `parent`, `animationPath`, `active`, `position`, `rotation`, and `scale`; drawable nodes also have `sprite`, `sortingOrder`, `color`, `flipX`, `flipY`, and `rendererEnabled`. |
| `sprites` | `{<sprite name>: {texture, file, rect, pivot, pixelsToUnits}}`. |
| `textures` | `[{name, file, width, height}]`. |
| `clips` | `{start|loop|end: {name, rate, duration, frames, channels}}`; empty for particle items. |
| `particles` | Emitter array, see below; empty array for sprite items. |
| `dependencies` | Other packages this item needs (a variant item may share the material of the item it varies). |
| `unsupported` | Unmodeled content for this item, listed with a reason for each entry. |

For `nodes[]`, `path` is relative to the package root, whose own path is `""`; `parent` is the parent node's `path`, or `null` for the root. `animationPath` is relative to the node containing the Animator; `null` means the node is above the Animator. Drawable nodes also carry `material`: `null` when no material is assigned, `{external: true, fileId, archive}` for an unresolved cross-package pointer, or a resolved material object. `position` is `[x,y,z]`, `rotation` is quaternion `[x,y,z,w]`, and `scale` is `[x,y,z]`.

Animation channels match `animationPath`, **not** `path`: clip binding paths are relative to the Animator node, not the package root. Each channel carries both `pathHash` and the resolved `path`, which is the `animationPath` value; an unresolved hash is entered in `unsupported` with reason `path hash unresolved`.

One item can contain multiple textures, sprites, and drawable nodes (up to 3 sprites and 6 drawable nodes); `sortingOrder` determines stacking order. Texture filenames carry the package prefix in the form `<item name>__<texture name>.png`, because variant items can use the same texture name. Read files through `sprites[..].file` or `textures[..].file`; do not construct filenames yourself.

Clips play as follows: `start` once, then `loop` repeatedly; after the runtime sets the Animator flag named by `animator.loopEndFlag`, the loop exits, `end` plays once, and the item is destroyed one second later. `channels[].values` are resampled at the clip's own `rate`; frame n is `round(t * rate)`.

In `view`, `soundLabelType` is the sound-call type: `PlaySE`, `StopSE`, `FadePlaySE`, `FadeSEVolume`, or `FadeStopSE`. `soundInput` is that call's argument string; for `PlaySE` it is a comma-separated cue list from which the runtime chooses randomly. For particle items, `anchor` is `Face`, `Spine`, or `Hips`, identifying the body attachment point; `keepPosition: true` keeps the item at its spawn position instead of following the anchor. Sprite items have no `anchor` because the caller chooses their position.

The current `unsupported` breakdown is: 63 non-Transform bindings, 18 `CanvasRenderer`, 8 `CustomDataModule`, 2 `NoiseModule`, 1 `SubModule`, and 1 `Texture2DArray`. All unmodeled content remains explicitly listed.

### `particles[]`

One entry per emitting node, `{node, system, renderer}`. `node` is that node's `path`.

`system` holds the emitter parameters: `duration`, `looping`, `prewarm`, `playOnAwake`, `simulationSpeed`, `simulationSpace`, `randomSeed`, `maxParticles`, `start`, plus `emission`, `shape`, `sizeOverLifetime`, `colorOverLifetime`, `rotationOverLifetime`, `velocityOverLifetime`, `limitVelocity`, and `textureSheet` when the corresponding module is enabled. A module that is enabled but not modelled here is listed under `unsupported` with reason `particle module not modelled`.

`renderer` holds the draw settings: `renderMode`, `sortMode`, `sortingOrder`, `minParticleSize`, `maxParticleSize`, `lengthScale`, `velocityScale`, `cameraVelocityScale`, `pivot`, `alignment`, and `material`.

`material` has three distinct states that must not be conflated: `null` means the renderer names no material and draws with the engine default; `{external: true, fileId, archive}` means the material lives in one of the packages listed in `dependencies`; otherwise it is `{name, shader, renderQueue, textures, floats, colors}`. `shader` is `m_Shader.m_ParsedForm.m_Name`, or `null` when it cannot be resolved; `renderQueue` is preserved verbatim, including `-1`; `textures` maps a material property name to a PNG file name, and a `null` value means that image is not in this package.

Consumers select a material render-state family from `shader`; properties from one family must not be interpreted as pass state for the other.

- `Mysekai/Effect/UberUnlit` (particles): the pass uses `_BlendSrc` / `_BlendDst` for active blend factors; `_SrcBlend` / `_DstBlend` are undeclared and do not drive the pass. `_ColorMask=14` writes RGB only and not target alpha; `_ZWriteOverride` is declared but does not drive the pass. The vertex shader applies `_ZOffset` as a linear eye-depth offset in metres when `0.004 < abs(_ZOffset)`; it is not polygon offset.
- `Mysekai/Emoticon/Sprite` (sprites): its single pass hard-codes `One` / `OneMinusSrcAlpha` blending, hard-codes culling `Off`, and uses `colMask=15`, which writes target alpha. Only `_ZTest` / `_ZWrite` are material-driven (observed `_ZWrite=1`); other same-named residual properties are not pass state. Its vertex shader tests `0.004 < _ZOffset` without `abs`, so negative values do not take effect.

**Value encoding**: every animatable particle value is a mode-tagged range, and the mode decides which fields are live —
`{"mode": "constant", "value": v}` uses `v`;
`{"mode": "twoConstants", "min": a, "max": b}` picks uniformly in `[a, b]`;
`{"mode": "curve", "multiplier": m, "keys": [...]}` evaluates the curve over normalised lifetime and multiplies by `m`;
`{"mode": "twoCurves", "multiplier": m, "minKeys": [...], "maxKeys": [...]}` interpolates between the two curves by a per-particle random value, then multiplies by `m`.
Keyframes are `{time, value, inSlope, outSlope}`, with infinite slopes written as `null`.

Colours follow the same shape: `{"mode": "color", "color": [r,g,b,a]}`, or `{"mode": "gradient", "gradient": {colorKeys, alphaKeys}}`, and possibly `twoColors`, `twoGradients`, or `randomColor`. **Colour keys and alpha keys have independent time axes and may differ in count** (for example 2 colour keys with 3 alpha keys), so interpolate each separately and combine. Time axes are normalised to `0..1`.

Angular values are **radians per second**; `shape.angle` and `shape.rotation` are in degrees.

## Shared Action Library And Index

The action library is character-independent and can be downloaded once for members sharing the humanoid skeleton contract. Action index records contain an exact `name`, positive `rate`, and `frames`; frames contain `t`, `muscles`, `body_q`, `body_p`, and `transform_rotations`. Muscle keys are integer indices 0..94 and values must not be clamped; `body_q` is normalized xyzw. Runtime Lua names and clip filenames are different namespaces.

For applying this library to another humanoid, see the [retargeting contract](retarget.en.md).

## Consumer Checklist

1. Use glTF indices, not names, for skinning, cloth, and animation binding.
2. Apply coordinate reflection and triangle-winding conversion exactly once.
3. Sample facial atlases with the lower-bound and four-column formula, using offsets only.
4. Keep default eye/mouth fields distinct from closed-pattern fields.
5. Drive facial state separately from the shared action clip; take the pairing from `alone-actions.json` rather than guessing a face from a motion name.
6. Performance scenarios are mutually exclusive — play one at a time; the loop tail runs every iteration.
7. Treat missing clips, models, and partial left/right metadata as explicit absence, never as a guessed fallback.
