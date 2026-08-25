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

The current content contains 53 items, 74 textures, 85 particle emitters, and 82 `unsupported` entries.

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

The current `unsupported` breakdown is: 63 non-Transform bindings, 18 `CanvasRenderer`, and 1 `Texture2DArray`. All unmodeled content remains explicitly listed.

<a id="particles"></a>

### `particles[]`

One entry per emitting node, `{node, system, renderer}`. `node` is that node's `path`.

`system` holds the emitter parameters: `duration`, `looping`, `prewarm`, `playOnAwake`, `simulationSpeed`, `simulationSpace`, `randomSeed`, `maxParticles`, `start`, plus `emission`, `shape`, `sizeOverLifetime`, `colorOverLifetime`, `rotationOverLifetime`, `velocityOverLifetime`, `limitVelocity`, `textureSheet`, `customData`, `subEmitters`, `noise`, `forceOverLifetime`, `collision`, and `trails` when the corresponding module is enabled. A module that is enabled but not modelled here is listed under `unsupported` with reason `particle module not modelled`.

The last six are described, together with **the four things a consumer has to get right**, under [modules beyond over-lifetime](#modules) below.

`renderer` holds the draw settings: `renderMode`, `sortMode`, `sortingOrder`, `minParticleSize`, `maxParticleSize`, `lengthScale`, `velocityScale`, `cameraVelocityScale`, `pivot`, `alignment`, `material`, and `trailMaterial` when the renderer has a second material slot.

`material` has three distinct states that must not be conflated: `null` means the renderer names no material and draws with the engine default; `{external: true, fileId, archive}` means the material lives in one of the packages listed in `dependencies`; otherwise it is `{name, shader, renderQueue, textures, floats, colors}`. `shader` is `m_Shader.m_ParsedForm.m_Name`, or `null` when it cannot be resolved; `renderQueue` is preserved verbatim, including `-1`; `textures` maps a material property name to a PNG file name, and a `null` value means that image is not in this package.

Consumers select a material render-state family from `shader`; properties from one family must not be interpreted as pass state for the other.

- `Mysekai/Effect/UberUnlit` (particles): the pass uses `_BlendSrc` / `_BlendDst` for active blend factors; `_SrcBlend` / `_DstBlend` are undeclared and do not drive the pass. `_ColorMask=14` writes RGB only and not target alpha; `_ZWriteOverride` is declared but does not drive the pass. The vertex shader applies `_ZOffset` as a linear eye-depth offset in metres when `0.004 < abs(_ZOffset)`; it is not polygon offset.
- `Mysekai/Emoticon/Sprite` (sprites): its single pass hard-codes `One` / `OneMinusSrcAlpha` blending, hard-codes culling `Off`, and uses `colMask=15`, which writes target alpha. Only `_ZTest` / `_ZWrite` are material-driven (observed `_ZWrite=1`); other same-named residual properties are not pass state. Its vertex shader tests `0.004 < _ZOffset` without `abs`, so negative values do not take effect.

**Value encoding**: every animatable particle value is a mode-tagged range, and the mode decides which fields are live —
`{"mode": "constant", "value": v}` uses `v`;
`{"mode": "twoConstants", "min": a, "max": b}` picks uniformly in `[a, b]`;
`{"mode": "curve", "multiplier": m, "keys": [...]}` evaluates the curve over normalised lifetime and multiplies by `m`;
`{"mode": "twoCurves", "multiplier": m, "minKeys": [...], "maxKeys": [...]}` interpolates between the two curves by a per-particle random value, then multiplies by `m`.
Keyframes are `{time, value, inSlope, outSlope}`, with infinite slopes written as `null` — that means a stepped key, and it is not a number JSON can read back.

**Non-finite numbers are written as names.** JSON has no spelling for infinity or not-a-number, and these parameters do contain them: a particle that never expires by age, a burst with no next cycle, a material parameter with no upper bound. So apart from the stepped-key slope above, every non-finite number is written as the string `"Infinity"`, `"-Infinity"`, or `"NaN"` — lossless, accepted by `JSON.parse`, and coerced back to the right value by anything that reads a numeric string (`Number("Infinity")` is infinity in JS). **Do not read it as a missing value**: missing is `null`, and `null` already means other things in this contract. A bare `Infinity` makes a whole document unreadable, so every write in this repository refuses to emit one.

One known instance: an emitter whose `start.lifetime` is `{"mode": "constant", "value": "Infinity"}` (24 in the current content) has particles that never expire by age. 17 of those also write `emission.bursts[].repeatInterval` as `"Infinity"` alongside `cycleCount = 0`, which means **it fires one cycle and never again** — `cycleCount = 0` on its own only says the cycle count is unbounded, and when the next cycle arrives is `repeatInterval`'s business, so read the two together. The current content also has `cycleCount = 0` bursts with finite intervals (13 of them among the emoticon items, intervals 0.05–0.5 s), and those really do repeat forever.

Colours follow the same shape: `{"mode": "color", "color": [r,g,b,a]}`, or `{"mode": "gradient", "gradient": {colorKeys, alphaKeys}}`, and possibly `twoColors`, `twoGradients`, or `randomColor`. **Colour keys and alpha keys have independent time axes and may differ in count** (for example 2 colour keys with 3 alpha keys), so interpolate each separately and combine. Time axes are normalised to `0..1`.

Angular values are **radians per second**; `shape.angle` and `shape.rotation` are in degrees.

<a id="modules"></a>

### Modules beyond over-lifetime

Besides size, colour, rotation and velocity over lifetime, an emitter may carry six further modules. All of them reuse the mode-tagged value encoding above; **no new value primitive is involved**.

| key | present when | content |
|---|---|---|
| `customData` | the custom-data module is enabled | `{custom1, custom2}`, each `{mode, componentCount, components[4], color}`. `mode` is `disabled` / `vector` / `color`. |
| `subEmitters` | the sub-emitter module is enabled | an array of `{emitter, type, properties, inherit, emitProbability}`. |
| `noise` | the noise module is enabled | `{separateAxes, strength/strengthY/strengthZ, frequency, damping, octaves, octaveMultiplier, octaveScale, quality, dimensions, scrollSpeed, remapEnabled, remap/remapY/remapZ, positionAmount/rotationAmount/sizeAmount}`. |
| `forceOverLifetime` | the force module is enabled | `{x, y, z, inWorldSpace, randomizePerFrame}`. The three axes are an **acceleration**: multiply by the time step and add to velocity — not a displacement and not an impulse. |
| `collision` | the collision module is enabled | `{type, mode, dampen, bounce, lifetimeLoss, minKillSpeed, maxKillSpeed, radiusScale, quality, voxelSize, collidesWith, collidesWithDynamic, interiorCollisions, maxCollisionShapes, collisionMessages, colliderForce, multiplyColliderForceBy*, planeSlots, planes}`. `type` is `planes` / `world`; `mode` is `3d` / `2d`; `collidesWith` is a layer-mask bit field; `lifetimeLoss` is the **fraction of total lifetime** removed per collision (`1.0` = dies on contact), not a number of seconds. |
| `trails` | the trail module is enabled | `{mode, ratio, lifetime, minVertexDistance, textureMode, textureScale, ribbonCount, shadowBias, worldSpace, dieWithParticles, sizeAffectsWidth, sizeAffectsLifetime, inheritParticleColor, generateLightingData, splitSubEmitterRibbons, attachRibbonsToTransform, colorOverLifetime, widthOverTrail, colorOverTrail}`. `mode` is `perParticle` / `ribbon`; `textureMode` is `stretch` / `tile` / `distributePerSegment` / `repeatPerSegment` / `static`. |

`noise.quality` is a **dimension count**, not a quality level: the accompanying `dimensions` spells it out — `3` samples a 3D field, `1` and `2` sample a 2D one. `damping` true means strength is scaled by frequency (divided by `frequency`), keeping visual amplitude constant as frequency rises. Noise acts on **velocity**, the same channel force uses.

`subEmitters[].emitter` is the **node path** the child emitter sits on. `null` means the entry names no emitter and therefore emits nothing — it does not mean "emit self". `inherit` is five booleans `{color, size, rotation, lifetime, duration}` taken bit by bit from `properties`; `properties` is also preserved verbatim, because the content contains "inherit everything" written as all bits set rather than the enumeration's 31, and both spellings test the same bitwise. A pointer outside this package leaves `emitter` as `null` and is listed under `unsupported` with reason `sub-emitter is not in this package` — **a null pointer and an unresolvable one are different things**, and the former is not a gap.

#### The four things to get right

1. **Custom data has to be evaluated every frame.** Its components are often curve modes (958 of the 2068 live components in the current content are `curve` or `twoCurves`; of 347 emitters, **305 require per-frame evaluation** and only 42 are fixed at birth). Treating the value as fixed at birth still lands it in a plausible range, so **nothing errors — the animation just stops**: an effect driven by it to pick a texture layer or scroll UVs freezes on frame 0. Each component of each stream also has its **own independent** per-particle random, so x/y/z/w do not correlate, and a given particle's random for a given component **never changes over its life** — do not redraw it per frame. `componentCount` says **how many are evaluated**; all four are always on disk, and evaluating all four uses components the author never touched.
2. **A collision plane slot is empty; do not read it as a plane.** `planeSlots` is how many slots are serialized, and `planes` lists only those that really name a node. In the current content 25 collision modules have `planeSlots` of `1` with `planes` of `[]` (one more has 0 for both), and all 26 are `type: "world"`. Reading that empty slot as "there is one plane" invents an invisible floor at the origin, and rain or snow splashes at `y = 0`.
3. **The trail material is the second slot.** There is no separate trail-material field on disk: `material` is the particles', `trailMaterial` is the trail's, and the latter appears only when the renderer really has a second slot. Of 560 emitters in the current content, 550 have one slot (no `trailMaterial`) and 10 have two (with `trailMaterial`), matching whether the trail module is enabled **exactly, with no exceptions**. Drawing a trail with `material` gets the particle head's material — wrong blending and usually far too bright.
4. **`subEmitters[].type` decides when it fires, and anything but `birth` does not fire from the per-frame list.** `birth` is triggered by the emitter's per-frame update; `death` fires once when a particle dies, and `collision` fires on impact. An implementation that only walks this list for per-frame emission makes the `death` entries fire **never** (42 of them in the current content) and the 7 `collision` entries never; conversely, emitting `death` children every frame turns those 42 entries into continuous streams.

## `phenomena/`

Weather asset packages. The game calls its weather **phenomena**: one phenomenon is a whole set of audiovisual parameters — the sky gradient, the scene light and character shading, the cloud shadow, the wind, the fog and post-processing, and the particle effects such as rain, snow and meteors.

A phenomenon spans several packages: a **global** package (configuration, sky gradient, post-processing profile, sky and camera effects), a **common** package (shared meshes, materials and images the effects draw with), and **one package per site**. A site package carries either that site's own effect prefab, or — for the one indoor site — a configuration and profile that **override** the global ones. All of them are extracted in **one job**, because they write one shared `index.json`.

Directory layout:

```text
phenomena/
  index.json                                  phenomenon list
  icons/<icon name>.png                       every phenomenon icon (one shared thumbnail package)
  models/<asset name>-<digest>.glb            model-asset and mesh-emitter geometry, shared (see below)
  audio/loop.json                             audio status and every loop range
  audio/<archive>/<archive>.acb               the audio archive as shipped
  audio/<archive>/<cue>.wav|.ogg              decoded waveforms (needs the external decoder)
  <phenomenon asset name>/
    config.json                               environment configuration, flattened
    ramp.png                                  sky gradient, 32x1
    postprocess.json                          post-processing profile
    timeline.json                             the timeline driving this phenomenon (only the lightning one)
    textures/<package>__<image name>.png      images the configuration and materials reference
    textures/<package>__<array name>.<n>.png  one file per layer of a texture array
    fx/effects.json                           every particle effect of this phenomenon
    overrides/<site name>/config.json          this site's overriding configuration
    overrides/<site name>/postprocess.json     this site's overriding profile
```

Every file path in `index.json` is **relative to that file itself**. Image filenames carry a package prefix because image names repeat across phenomena; read files through the `file` field rather than constructing names yourself.

The current content holds 15 phenomena, 29 configurations (15 global plus 14 overriding), 29 profiles, 15 gradients, 14 site overrides, 107 particle effects (560 emitters), 283 images, 29 texture-array bindings (124 layer PNGs in all), 10 model assets (20 glTF geometry files after deduplication, about 182 KB in all), one timeline, 31 audio streams across 13 sound packages when the external decoder is present (the sound packages are named by master rows, so that count does not depend on how many phenomena were extracted), 15 icons, 192 `omitted` entries, and 9 `unsupported` entries. All phenomena environment packages together are about 9.9 MB — **phenomena assets are light**, so there is no need to batch or cache them the way a bulky asset domain requires. Of the 15 phenomena, 13 have all six sites, one has only two, and the delivery-site one has no site package at all.

### Two-level lookup and overrides

A site's configuration and profile are found by a **two-level lookup**: that site's own package first, the phenomenon's global package second. The `overrides` map lists only the sites that **really** carry one, so a site's absence means "this site uses the global values", **not** "the lookup failed". In the current content each of 14 phenomena has exactly one override, all for the same indoor site; the delivery-site phenomenon has none.

**The home site gets no override package.** It uses the global configuration and, when `light.homeSiteLightAngle.active` is true, **substitutes** the `angleXZ`/`angleY` held there for the global sun angle — that is what the field exists for; do not read it as redundant duplicate data.

### Cross-fade

Switching phenomena cross-fades the two configurations over **0.25 seconds**: both gradients are live and blended by progress, while light colours and the light direction are interpolated per frame (the direction spherically). A screenshot capture is the one exception: it switches **instantly**, so the shutter cannot catch a half-interpolated state.

### `config.json`

The configuration is flattened into three scalars plus five parameter groups, following the source structure; the leading underscores of source field names are not part of the contract. **Values are given exactly as stored** — not rescaled, not rounded, never defaulted.

| Field | Meaning |
|---|---|
| `asset` | The configuration's asset filename inside the package. |
| `description` | The author's note stored on the asset. |
| `rendererType` | A render-path selector stored with the phenomenon. |
| `gridColorKey` | Name of the ground-grid colour profile (observed values `0_Default` and `1_Dark`). **Not a dead field**: consumers switch the grid colouring by it. |
| `emissionType` | Emission tier, pushed to shaders as an integer global. |
| `light` | 11 entries, see below. |
| `cloud` | 5 entries: the cloud-shadow image and its scroll. |
| `character` | 5 entries: character outline. |
| `fixture` | 5 entries: fixture outline. |
| `wind` | 9 entries: wind and vertex animation. |

The 11 `light` entries are `characterDirectionalLightColor`, `characterShadeSkinColor`, `characterBodyShadeColor`, `phenomenaDirectionalLightColor`, `phenomenaShadeColor`, `angleXZ`, `angleY`, `homeSiteLightAngle`, `dropShadowColor1`, `dropShadowColor2`, and `dropShadowEdgeSmoothness`.

> **This group is not only "scene light".** A phenomenon carries three character-facing colours — a character directional light colour, a skin shade colour, and a body shade colour — **on a separate path from** the scene's own phenomena light and phenomena shade colours. Changing the weather therefore also changes a character's face sphere shadow and body shading, not just the sky. `angleXZ`/`angleY` are the sun azimuth and elevation in degrees; `homeSiteLightAngle` is `{active, angleXZ, angleY}`; the two drop-shadow colours plus an edge smoothness describe the shadow a character casts on the ground.

The 5 `cloud` entries are `cloudShadowTexture`, `cloudShadowOpacity`, `cloudShadowTextureSize`, `cloudScrollVelocity`, and `cloudScrollSpeed`. **Two of them are not used as written**: the cloud shadow is sampled with the **reciprocal** of `cloudShadowTextureSize`, and the actual scroll is `cloudScrollVelocity` times `cloudScrollSpeed`. An opacity of 0 turns the cloud shadow off.

`character` and `fixture` are **two independent** outline setups sharing the same field names (`outlineWidth`, `outlineDepthOffset`, `outlineWidthMaxRate`, `outlineWidthMinRate`, `outlineColor`); the character group also participates in face and body shading, so it is not only an outline.

The 9 `wind` entries are `windSpeed`, `windColor`, `vertexWaveAnimationAmount`, `vertexWaveExponent`, `vertexRandomAnimationAmount`, `vertexRandomAnimationSpeed`, `windWaveDistortionAmount`, `windWaveDistortionFrequency`, and `windNoiseTexture`. A vertex wave amount of 0 turns wind animation off.

Colours are `[r,g,b,a]`; two-dimensional vectors are `[x,y]`. An image field is `{name, file}`; `file` is `null` when the image is not a single bitmap (for instance a texture array), and the gap is then recorded in `summary.unsupported`.

**Shading values decided by the site are not in this package.** A further **29** scene shading globals are decided by the **site** rather than the phenomenon (they come from the site's own view and graphics settings and cover groups such as site extension, roads, trees, drop items, and the object shader); this package does not hold them, so a consumer rendering without a site should feed those neutral constants, or the shaders reading them will drift. **Do not fold them into the phenomenon** — they have nothing to do with the weather and change with the site, not with the weather.

### `postprocess.json`

The post-processing profile. `components[]` is given in the profile's own order, which is the order the volume stack applies them in. Each entry is `{name, class, active, parameters}`: `name` is the component name in the asset, `class` is the script class it instantiates (the two can differ), and `active: false` means the whole component is inert.

Each parameter is `{overrideState, value}`. **`overrideState: false` means the profile does not set that parameter at all**, and the value inherited from the surrounding volume stack stays live; dropping the flag turns "leave this alone" into "force this value".

Every profile in the current content has five components: `MysekaiFogVolume` (9 parameters: enable, density, near/far colour and near/far density, fog start and end distance, fog height), `MysekaiFlarePara` (class `MysekaiFlareParaVolume`, 15 parameters: 8 for the screen flare, 7 for the sun flare), `MysekaiParticleBloomVolume` (11), `MysekaiDiffusionVolume` (6), and `ColorAdjustments` (5). Three further components appear on individual phenomena only: one `Bloom` (11), one `SplitToning` (3), and one `WhiteBalance` (2). **The component set is extracted as it is found**, not assumed from a fixed list; a new component is flattened just the same, and only a parameter whose value shape cannot be read enters `unsupported`.

### `fx/effects.json`

Particle effects. The top level is `version` (currently 1), `phenomenon`, `effects`, and `summary`.

Each `effects[<prefab name>]` is `{kind, site, variant, nodes, particles, effectors, effectiveRotation}`:

| Field | Meaning |
|---|---|
| `kind` | `sky` is anchored to the sky, `camera` to the camera, `site` belongs to one site; `other` means the prefab does not follow those naming patterns and **its placement is not stated by its name**. |
| `site` | The site name when `kind` is `site`, **taken from the package's site variant rather than guessed from the name**; otherwise `null`. |
| `variant` | Which package variant the prefab came from (`global` / `common` / `unique__<site>`). |
| `nodes` | Node tree, parents first. Each entry has `name`, `path`, `parent`, `active`, `position`, `rotation`, and `scale`. |
| `particles` | Emitter array, see below. |
| `effectors` | Lifecycle components, see below; `[]` when the prefab carries none. |
| `effectiveRotation` | The per-frame rotation rule, given only for effects whose `kind` is `camera`; `null` otherwise. |

For `nodes[]`, `path` is relative to the prefab root, whose own path is `""`; `parent` is the parent node's `path`, or `null` for the root. `position` is `[x,y,z]`, `rotation` is quaternion `[x,y,z,w]`, and `scale` is `[x,y,z]`.

`particles[]` has one entry per emitting node, `{node, system, renderer}`, and **uses exactly the same encoding as the overhead-item particles**: `system` holds emitter parameters, `renderer` holds draw settings and the material, and values are mode-tagged ranges. See the [`particles[]` section under `emoticons/`](#particles) above for the full value encoding — mode tags, keyframes, and gradients — which is not repeated here.

`effectors[]` has one entry per node carrying one, `{node, timeUntilDestroy, rotationType}`. It is a **lifecycle** component rather than a visual effect: it plays every emitter beneath it, and on teardown it stops emission and then waits until all child emitters have finished **and** at least `timeUntilDestroy` seconds have passed before destroying the object. All 10 instances in the current content have `timeUntilDestroy` of `2.0` and `rotationType` of `normal`. **Do not destroy the outgoing effect immediately on a change of phenomenon**: the old weather's particles are meant to finish their lifetimes while the new one is already emitting, and that 2-second overlap follows the implementation.

`effectiveRotation` is either `normal` (turns with the camera) or `fix` (each frame the node's own rotation is set to the inverse of its parent's, cancelling the camera's rotation so the effect stays aligned to the world while its position still follows the camera). **This rule is not uniform**: the host looks for the lifecycle component on the prefab root only, uses the component's own serialized `rotationType` when it finds one (4 are `normal` in the current content), and otherwise adds one itself and asks for `fix` (11 in the current content). So of 15 camera effects, 4 turn with the camera and 11 counter-rotate — **applying the counter-rotation to all of them, or to none, is wrong either way**. A component on a child node does not count; the host only looks at the root. How non-camera effects are attached is not established in this repository, so their value is `null` rather than a guess.

In the current content the 107 effects break down as 15 `sky`, 15 `camera`, 75 `site`, and 2 `other`, with 560 emitters in total. 524 materials resolved (shaders `Mysekai/Effect/UberUnlit` 380 and `Particles/Standard Unlit` 144) and 36 are `null`. **Materials and images commonly live in other packages**: extraction loads each package's own declared dependency list alongside it, and loading a global package on its own is what degrades material resolution. A dependency that was not supplied is not guessed, and the pointer stays visible as `{external: true, fileId, archive}`.

### Texture arrays and choosing a layer

Some effect textures are not one picture but a **texture array** — N pictures of the same size and format stacked into one asset. Sampling one needs three coordinates `(u, v, layer)`, and here `layer` is **not a constant**: the material stores a small set of scalars saying which per-particle value the layer is read from and how to turn it into an integer index.

A texture array therefore does **not** go into the one-file-per-slot `textures` map but into the **sibling field** `textureArrays`: a consumer that only followed the single-image map would sample the array's 2D companion slot, which is empty, so sampling returns (1,1,1,1) and it draws a **white quad**.

`_material.textureArrays[<property name>]` is:

| Field | Meaning |
|---|---|
| `name` / `kind` | The array asset's name and type (`Texture2DArray`). |
| `width` / `height` / `layers` | One layer's size, and the layer count (the asset's own depth). |
| `graphicsFormat` | The **raw integer** format code. A texture array's format field is a `GraphicsFormat`, **not** a `TextureFormat`; the two tables have different value ranges and feeding the wrong one silently yields garbage — so the value is kept unmapped. All 5 arrays in the current content are `134`. |
| `colorSpace` / `mipCount` | Colour space and mip count, as stored. |
| `files` | **One file per layer, in layer order**: entry *i* is layer *i*. Filenames read `<package>__<array name>.<layer>.png`. |
| `sampling` | The layer-selection parameters, below. |

`sampling` is:

| Field | Meaning |
|---|---|
| `mode` | The slot's mode scalar. `1.0` is the array mode; anything else samples the single image. |
| `arrayMode` | Whether `mode` is the array mode — that is, **whether this array is sampled at all**. |
| `keyword` / `keywordEnabled` | The shader keyword that corresponds to that mode, and whether it is enabled in the material's keyword set. It is given so a consumer can **cross-check** the mode scalar against the shader variant; the two agree in the current content, with no exceptions. |
| `sliceCount` | The layer count used by the arithmetic. **It is a separate number from `layers` and they do disagree in shipped data** (an 8-layer array with this written as 4) — so the arithmetic uses `sliceCount` and the result is then clamped to the layers that exist. |
| `progress` | A constant offset added to the layer progress. |
| `progressCoord` | A **packed selector**, `component * 10 + vector`: the vector part picks which per-particle custom value carries the progress (`0` selects the zero vector, meaning the slot is not driven by the particle at all), and the component part picks its x/y/z/w. |
| `progressSource` | That selector decoded, as `{vector, component, constant}`. `constant` true means the zero vector was selected. The particle custom value it names is the emitter's [`customData`](#modules): `vector` picks `custom1` / `custom2`, and `component` picks which entry of that stream's `components` — **that entry is often a curve mode and must be evaluated every frame**, otherwise the layer stays on whatever frame 0 computed. |
| `offsetXCoord` / `offsetYCoord` | UV-scroll selectors in the same encoding, carried through as stored. |
| `progressClamp` | The clamp constant in the arithmetic, as authored (`0.999000013`). |
| `layerFormula` | The layer formula, below. It is given so the exported parameters have exactly one meaning. |

The layer formula (`progressSource` being the per-particle value the selector points at):

```text
layer = min(layers - 1, max(0, floor(fract(clamp(progressSource + progress, 0, 0.999000013)) * sliceCount)))
```

**That `floor` must not become a rounding.** The layer coordinate the shader computes carries a `-0.5` precisely to cancel the graphics API's nearest-rounding when sampling an array, turning it into an **exact floor** — the signature of a deliberate integer page index, not blending between layers. Rounding instead moves every boundary by an eighth of the range, putting **a quarter of the particles on the wrong layer** while looking entirely normal on average. Likewise, dropping the `0.999000013` clamp lets a value of 1.0 wrap through `fract` back to **layer 0**.

**There is no frame-by-frame flipbook and no blending across layers**: the mode scalar has only a single-image and an array setting, the vertex streams carry no frame index or blend amount, and each shader subprogram samples the array exactly once.

In the current content the 5 arrays are bound at **80 slots** (`_BaseMap2DArray` 74, `_EmissionMap2DArray` 3, `_AlphaTransitionMap2DArray` 3), of which **only 29 are actually in the array mode**; the other 51 bind an array while the slot stays in the single-image mode — **those bindings are inert**, and drawing them as if they were sampled is wrong. The 29 bindings in `index.json` export 124 layer PNGs in all (an array reached from several phenomena is exported once per phenomenon, into that phenomenon's own `textures/`).

### `models/`: model assets and mesh-emitter geometry

A `common` package also holds **model assets**: a small node tree with meshes on some of its nodes — a sky dome, a cloud ring, a rain ring, a rainbow fan, a milky-way plane. They are exported as **glTF binaries** (`.glb`), **node transforms and all**, so a consumer draws the authored shape rather than an approximation of it.

A second kind of geometry is equally unskippable: an emitter whose `renderMode` is `Mesh` draws each particle as a **copy of one mesh**, and without that mesh there is nothing to draw; an emitter whose `shape.type` is `Mesh` **gives birth to its particles on a mesh's surface**. Both kinds are exported as single-mesh files and referenced as `{file, node}` from `renderer.meshes[]` and `shape.meshes[]` respectively.

**Geometry files are named after their content** (`<asset name>-<first 8 of the content digest>.glb`): packages share meshes with each other and repeat them internally, so identical geometry is **written once** and a second reference is just another pointer, while two different meshes that happen to share a name stay separate files. A shared file's entry carries **nothing that varies per phenomenon**, so one shared file means the same thing to every phenomenon pointing at it.

The top-level `models[]` in `index.json` is the deduplicated geometry file list, each entry `{name, nodes, vertices, triangles, meshes[], materials[], file, sha256, bytes}`, plus `source`, which **appears only when the geometry came out of the engine's own resources** (see "Engine built-in primitives" below). A phenomenon's own `models[]` holds the model assets it references, with the same fields plus `asset` (the name in the container) and `variant` (which package variant it came from). Each entry of `meshes[]` is `{node, mesh, vertices, triangles}`; each entry of `materials[]` is `{node, material}` and gives **the material name only** — a model asset's own material is the import-time default (all 19 mesh renderers in the current content use it), and the material a phenomenon actually draws the sky with is **not in the model asset**: the sky is fed two gradients through a material property block at runtime.

In the current content 10 model assets (`001_sunny`, `006_rain`, `007_rainnight`, `009_meteorshower`, three in `014_sekai`, two in `017_rainbow`, `999_festivalgarden`), 90 mesh emitters and 29 mesh-shaped emitters deduplicate to **29 glTF files**, about 211 KB in all.

#### Engine built-in primitives

Of the 90 mesh emitters, **8** draw something that is not a game asset at all but **one of the engine's own primitives**: container name `unity default resources`, `pathId` 10209 for Plane and 10207 for Sphere (7 of them are `008_thunder`'s lightning, once per site, and 1 is `014_sekai`'s cloud). **No asset package ships that container**, so a default export cannot resolve them: those 8 go into `unsupported` entry by entry with `mesh` carrying `{fileId, pathId, archive}`, and the emitter's `renderer.meshes` is an empty array — **the whole emitter draws not one particle**, which is exactly why the storm showed no lightning.

That container is the **engine's**, not the game's, so this repository does not ship it and **does not fabricate geometry to stand in for it**. A caller who has the matching engine version can hand it over instead: the `phenomena` subcommand's `--builtin-resources <path>` takes **the container file itself, or a directory holding it** (repeatable; a directory contributes the engine's own containers and nothing else). Given it, those pointers resolve like any other mesh pointer, the geometry is written into `models/` under the **same naming and directory convention** as everything else, and `renderer.meshes` / `shape.meshes` carry ordinary `{file, node}` entries. On the current content that empties all 8 `unsupported` entries and adds `Plane` (121 vertices / 200 triangles) and `Sphere` (515 vertices / 768 triangles) to `models/`, for 31 files and about 248 KB.

**The `source` field**: geometry that came out of the engine's container carries `"source": "engineBuiltin"` on its top-level `models[]` entry in `index.json`, and likewise on the `meshes[]` entries inside a model asset. **Nothing else carries the field**, so "no `source`" keeps meaning "shipped by one of this game's packages". It is there because the two shapes land in the same directory under the same naming rule and a consumer draws them the same way — without the mark, nothing in the products would tell the engine's shapes from the authored ones.

**Without `--builtin-resources` the products are byte for byte what they were before.** That is part of the contract: a new input may not move anything an existing consumer is already reading.

Three further emitters whose `shape.type` is `Mesh` keep an empty `shape.meshes` whether or not the container is supplied — their mesh pointer is a **null pointer** (`mesh` is `{fileId: 0, pathId: 0, archive: null}`): no mesh was assigned by the author, rather than one failing to resolve here. They reuse the same `reason` string under `unsupported`; the pointer in `mesh` is what settles which case it is.

Coordinates and winding follow this repository's single convention (X axis reflected once, triangle winding flipped once, UV V flipped once), the same as character `.glb` files; see [Coordinates](#coordinates-and-identity) at the top.

### `timeline.json`: the one phenomenon driven by a timeline

The lightning phenomenon is not a set of constants: the sky flashes, the light flashes with it, and both fall off on authored curves. That schedule lives in a **timeline asset** — tracks, each holding clips, on a common time axis. **Of the 110 packages only that one carries such an asset.**

The document is `{asset, name, duration, durationMode, frameRate, tracks[], summary}`. `duration` is the fixed duration in seconds, `frameRate` is the editing frame rate (60 in the current content), and `summary` is `{tracks, clips}`.

Each entry of `tracks[]`:

| Field | Meaning |
|---|---|
| `name` | The track's editor label — **for a person to read**. |
| `class` | The track's class name. |
| `target` | **Which value this track drives**, taken from an enumeration field on the track rather than guessed from the label: a colour track is `none`/`skyAdditiveColor`/`lightAdditiveColor`, a value track is `none`/`skyAdditiveIntensity`/`lightAdditiveIntensity`. `targetValue` is that enumeration's raw integer. |
| `role` | `null` for a track in the asset's own track list, `markerTrack` for the marker track, `child of <name>` for a subtrack. |
| `scale` | The scale factor a value track carries (present only on tracks that have one). |
| `muted` / `locked` | Track state, as stored. |
| `clips[]` | The clips, sorted by `start`. |

Each entry of `clips[]` is `{start, duration, clipIn, timeScale, label, class, blendInDuration, blendOutDuration, easeInDuration, easeOutDuration, preExtrapolation, postExtrapolation, asset}`. **Time comes in two forms and they are not interchangeable**: `start` and `duration` are **seconds** on the timeline, whereas a clip's own curve runs on a **normalized 0..1 axis** spanning that `duration`, scaled by `timeScale` and offset by `clipIn`.

`asset` is the clip's own payload, in one of three shapes by clip class: a colour clip gives `{gradient}` (the gradient encoding is **exactly the same** as the particle one, see [`emoticons/`'s `particles[]`](#particles)); a value clip gives `{scale, curve}`; a noise clip gives `{intensity, frequency, intensityCurve}`. A curve is `{keys[], preInfinity, postInfinity}` with each key `{time, value, inSlope, outSlope, weightedMode, inWeight, outWeight}`; **an infinite slope means a stepped key and is written as `null`** — it is not a number a JSON reader can take back. A clip class that cannot be read leaves `asset` as `null` and records the class name under `unsupported`, while **the clip's placement on the timeline is kept either way**.

In the current content that timeline is 23.333333 seconds long with 7 tracks (4 main tracks, 2 noise subtracks, 1 marker track) and 25 clips.

### `index.json`

The phenomenon list. The top level is `version` (currently 1), `semantics`, `phenomena`, `icons`, `refreshTimePeriods`, `siteBgms`, `siteSoundFallbacks`, `ambiencePackage`, and `summary`.

Each `phenomena[<phenomenon asset name>]` contains:

| Field | Meaning |
|---|---|
| `assetName` | The phenomenon's asset name, which is its directory name. |
| `id` | Phenomenon id; **present only with master tables**, `null` otherwise. |
| `variants` | The package variants this run extracted. |
| `config` / `ramp` / `postprocess` | Relative paths of the three artifacts; `ramp` is `{file, width, height}`. |
| `overrides` | `{<site name>: {config, postprocess}}`, holding only the sites that really carry one. |
| `fx` | The `{sky, camera, site, other, emitters, file}` counts and the effect document's path. |
| `icon` | Relative path of the icon file. Icon ownership is stated by the master row, so this is **`null` without master tables** (the icons are still exported, they are simply not attached to a phenomenon). |
| `master` | The master row, see below; `null` without master tables or when no row names this package. |
| `models` | The model assets this phenomenon references (the geometry lives in the shared `models/`, above); an empty array when it has none. |
| `timeline` | A summary of this phenomenon's timeline, `{file, duration, tracks, clips}`; `null` when it has none. |
| `audio` | The audio streams **actually decoded** for this phenomenon, each carrying `package` on top of the stream fields of `loop.json`. **Three states**: a non-empty array means there is audio, an **empty array means "the rows are known and this phenomenon has no audio row"** (it keeps the site's own music and ambience), and `null` means **the rows themselves are unknown** (no master tables). Collapsing the last two leaves a consumer unable to tell "none" from "unknown". |
| `bgms` | This phenomenon's music rows (each carrying `package`); `null` without master tables. |
| `siteSounds` | This phenomenon's ambience rows (each carrying `package`); `null` without master tables. |
| `note` | Says why there is no master row (present only when there is something to say). |

**Master tables are supplied by the caller** (`--master <dir>`). A phenomenon's name, its time period, its brightness, its music and its ambience are only written there, so without that input those fields are absent and `summary.missing.master` records the reason — **no defaults are filled in**. The tables read are `mysekaiPhenomenas`, `mysekaiPhenomenaBgms`, `mysekaiSiteBgms`, `mysekaiSiteMysekaiPhenomenaSounds`, `mysekaiRefreshTimePeriods`, and `clientConfigs`; a table that is absent is recorded in `summary.missing.masterTables`.

`master` is `{id, name, englishName, description, timePeriodType, brightnessType, backgroundColorId, iconAssetbundleName, rampTextureAssetbundleName}`. **Time period and brightness are row properties, not clock state**: `timePeriodType` is `daytime`, `evening`, or `night`, and `brightnessType` is `none`, `normal`, `bright`, or `dark`. They change together with which phenomenon row is in effect; no local clock advances the phenomenon.

`refreshTimePeriods` holds the refresh windows, each `{id, startHour, endHour}`. The current content has two: hour 5 to hour 17, and hour 17 to hour **29**. **29 being past 24 is deliberate**: when deciding which window a moment falls in, a time between hour 0 and hour 5 has 24 hours added first, so a day runs from hour 5 to hour 5 of the next day and two windows mean two phenomena per day.

**One phenomenon has no master row**: the delivery site's phenomenon is named by two `clientConfigs` rows (one giving the id, one the asset name), and `note` says so. Its `master` is `null` and its `id` comes from those rows.

**Music and ambience match by opposite rules — do not implement them as one**:

- Music matches the phenomenon **exactly, with no fallback**: a phenomenon with no row keeps the site's own music.
- Ambience takes the row for **this phenomenon and this site first, and falls back to that site's fallback row**; the top-level `siteSoundFallbacks` is exactly that set of fallback rows. In the current content only the four gathering sites have ambience rows.

**Music is also two layers, and `bgms` is only the upper one.** The base layer is keyed by site and brightness and is written as the top-level `siteBgms`, each entry `{id, siteId, brightnessType, cue, assetbundleName, package}`; a phenomenon's own row (`bgms`) **replaces it whole**. In the current content `siteBgms` holds 28 rows over 7 packages while only 6 phenomena have a music row of their own — **every other phenomenon plays the base layer**, so extracting the upper layer alone leaves out the music that actually plays most of the time. Both layers' packages are extracted; a base-layer stream belongs to a site rather than to a phenomenon, so it **does not appear in any phenomenon's `audio`**. With the table absent `siteBgms` is `null` rather than an empty array — the same three-state rule as `siteSoundFallbacks`, because "none" and "unknown" must not share a value.

**They are also addressed differently.** A music row names its own package (`assetbundleName`), whereas an ambience row carries **only a cue name** — every ambience cue lives in **one shared sound package**, so an ambience cue is not a package name. To keep that unambiguous, every entry of `bgms[]` and `siteSounds[]` carries a `package` field naming the package that holds the cue, and the shared package is also stated once as the top-level `ambiencePackage`. **Reading an ambience cue as a package name looks for a package that does not exist and reports audio you already have as missing.**

### Audio

A sound package holds **no audio files**. It holds one **archive** in a middleware container format (`.acb`), and inside it every waveform sits under a **cue** name. Getting that archive out needs nothing external, so it is **always** written to `audio/<archive>/<archive>.acb`; **decoding** it needs an external decoder this repository neither ships nor vendors (`vgmstream-cli`, via `--vgmstream <path or directory>`, or found on `PATH`). An optional `--ffmpeg` is used only to write a compressed copy (`.ogg`) alongside.

Audio absence is therefore **three states, not two**:

| `audio.status` | Meaning |
|---|---|
| `succeeded` | The decoder was found; waveforms and loop ranges are written. |
| `skipped` | **The decoder was not found.** The archive is on disk and `audio.error` names what is missing. This is not a failure and the run completes normally. |
| (`audio` is `null`) | No cue row was available at all (no master tables); `summary.missing.audio` says why. |

`audio/loop.json` is the top-level audio document and holds the same content as the `audio` field of `index.json`: `{status, decoder, decoderPresent, transcoder, transcoderPresent, packages[]}`. `decoder` and `transcoder` name the tools only, never where they were found on the machine that ran the extraction: this document ships next to the audio. Each entry of `packages[]` is `{package, archive, archiveBytes, status, streams[]}`.

Each entry of `streams[]` is one waveform:

| Field | Meaning |
|---|---|
| `cue` | The cue name — what a master row asks for. |
| `subsong` | Which subsong of the archive this waveform is. **One cue can have several waveforms** (the game picks between them), and the filename then carries this number; `null` when the cue has one. |
| `wav` / `ogg` | Relative paths to the waveform; `ogg` exists only when a transcoder was found. |
| `loop` | Whether this stream carries a loop range. |
| `loopStartSeconds` / `loopEndSeconds` | The loop range, in **seconds**. Both are `null` when `loop` is false — **never inferred from the sample count and never faked with the stream length**. |
| `loopStartSamples` / `loopEndSamples` | The same range in raw samples; dividing by `sampleRate` gives the two fields above. |
| `sampleRate` / `channels` / `samples` / `durationSeconds` / `encoding` | The stream's shape, from the archive's metadata. |

**Loop points come from the archive's own metadata, not from detecting them in the samples.** Waveforms are exported with the loop **not unrolled** (the stream is written once), so a consumer loops over `[loopStartSeconds, loopEndSeconds)` itself — which is exactly the shape of WebAudio's `loopStart`/`loopEnd`.

**How many waveforms a cue name maps to is not fixed**, and conversely one waveform can answer to several cue names. Of the four phenomenon-related cues in the current content, one (the meteor one) has **6** waveforms. A cue a master row names but the archive does not hold is recorded entry by entry under `unsupported` with the reason "no waveform in this archive carries this cue name" — **that is a data gap, not a scope boundary**.

Only cues that a master row names are decoded: music from the phenomenon's music rows, ambience from its ambience rows and the fallback rows. The one shared ambience package holds many other cues (site ambience, UI sounds and so on); they do not belong to a phenomenon and are not exported.

`summary` gives the `phenomena`, `configs`, `profiles`, `ramps`, `overrides`, `effects`, `emitters`, `images`, `textureArrays`, `arrayLayers`, `models`, `meshes`, `timelines`, `audioStreams`, `icons`, `omitted`, and `unsupported` counts, plus `missing` (gap to reason), `omitted` (components read and deliberately not exported) and `unsupported` (each entry carrying `phenomenon`). Counts are **files actually on disk**: `images` counts only PNGs that were really written, so an image that could not be written is a gap, not an output; `meshes` counts geometry files after deduplication while `models` counts model-asset references, and the two differing is normal.

The current 9 `unsupported` entries break down as: mesh emitters pointing at the engine's built-in geometry 8; and one animation clip inside a model asset. **Unmodeled content is always listed entry by entry, never dropped silently.**

### `omitted`: read and deliberately not exported

`unsupported` and `omitted` are **two different things**: the first is "should be here and is not", the second is "read, judged, and left out on purpose". Mixing them buries the real gaps — all 192 current `omitted` entries come from effect-prefab nodes and break down as `CanvasRenderer` 122, `MeshFilter` 35, `MeshCollider` 35.

| Component | Why it is not exported |
|---|---|
| `CanvasRenderer` | It draws only what a **graphic** component submits to it, and these prefabs carry **no graphic component at all** (those nodes hold a particle system, or one non-graphic script), so it contributes nothing to draw. |
| `MeshFilter` | The 35 nodes it sits on have **no mesh renderer**, so they are invisible; its only role here is to name the mesh of the collider on the same node. |
| `MeshCollider` | Collision surface, not geometry: invisible for the same reason, and the mesh it names is a **site's navigation surface** living in another package (`mesh.fileId` is non-zero). What interacts with it is a particle collision module. |

Each `omitted` entry carries `{phenomenon, effect, node, component, reason}`, plus `mesh: {fileId, pathId}` when it names a mesh. **The judgement is narrow**: put a mesh renderer on the same node and `MeshFilter` stops being `omitted` and becomes an `unsupported` "prefab component not modelled" — visible geometry is not allowed to fall through this rule.

### No colour-grading lookup texture in the post-processing

Colour grading here is **parametric** (`ColorAdjustments`, `SplitToning`, `WhiteBalance`) and does **not** go through a lookup texture. This was checked rather than assumed: none of the 29 profiles carries a lookup component (the component set is `MysekaiFogVolume`, `MysekaiFlareParaVolume`, `MysekaiParticleBloomVolume`, `MysekaiDiffusionVolume`, `ColorAdjustments`, plus `Bloom` / `SplitToning` / `WhiteBalance` on individual phenomena), the only texture-typed parameter across all 29 is `dirtTexture` and **every one of them is a null pointer**, and the whole corpus these packages come from holds **no 3D texture at all**. `summary.missing.lut` always says so — **an absence has to be written down, or a consumer cannot tell "not there" from "left out"**.

## `site/`

The site asset pack. The nine sites share **one** Unity world coordinate system, one
offset each; beside the eight scene packages, the same path also ships the indoor
kit, the room skins, the field objects, the travel cannon and the world map — **109**
packages in all, and not one of them is left outside this contract.

**What a consumer must know first, because it decides whether the pack is usable at
all**: **every glb keeps its own package's origin. A site's world offset is in the
placement table `sites.json` and in no geometry anywhere.** Three of the nine rows
(1F/2F/3F) use the *same* scene package and are separated only vertically (0 / 500 /
1000), so baking the offset into the meshes collapses the three into one and the
second and third floors are **lost beyond recovery**. Placement is the consumer's
step: `world = sitePosition + grid * tileSize`, with `tileSize = 0.25`.

Directory layout:

```text
site/
  index.json                    domain index: semantics, constants, scenes, indoor
                                assembly, one list per family, summary
  sites.json                    placement: the nine rows + world positions + constants
                                + levels and grid extents + the footstep table
  packages.json                 the census of all 109: class, inventory, per-object
                                accounting, artifact counts
  scenes/<site>/
    <site>.json                 everything in the package: slots, collision,
                                navigation, materials, components, environment presets
    <site>.glb                  its geometry, **one glTF scene per prefab root**; the
                                default scene is the one the game places
    collision/<surface>-<hash>.glb   collision, one file per surface
    navmesh/navmesh.bin         the baked tiles, byte for byte
    navmesh/heightmesh-N.glb    the bake's height mesh (walkable-surface geometry)
    textures/<name>-<id>.png    the package's images
  indoor/kit/                   the indoor kit (wall and floor meshes, materials)
  indoor/modules/lv_NN/         the floor and wall prefabs of one expansion level
  indoor/navigation/            the walkable surface of one expansion level
  skins/<skin>/                 23 room skin sets
  props/<object>/               61 field objects + 1 shared material set
  preview/ travel/ sitemap/ shell/   preview stage, cannon, world map, site shells
```

Every file path in `index.json` is **relative to that file**; paths inside a
package's own document are relative to that document's directory.

What is in it today: 109 packages (8 scenes, 61 objects, 23 skins, 5 expansion
levels, 4 map packages, 2 shells, 2 preview, 1 kit, 1 walkable-surface package, 1
shared material set, 1 cannon), 183 glb files, 1809 glTF meshes, 573304 vertices /
440873 triangles, 87 collision surfaces, 5 shipped navigation bakes (78 tiles, 1
height mesh), 1256 materials, 1383 images, 1596 particle emitters, 161 decoded
animation clips. **Per-object accounting**: 63459 objects = 63042 exported + 417
skipped with a reason + 0 unsupported, and all 109 packages report
`accountedFor: true`.

### The coordinate contract

| Quantity | Value | Where it comes from |
|---|---|---|
| `sitePosition` | the master row's three `int`s converted **plainly** to float, no scaling | read from the disassembly: that `scvtf` is the two-operand vector form and carries no fbits field |
| `tileSize` / `tileScale` | 0.25 | read straight out of the binary's rodata |
| `playerHeight` | 1.0 | the same; and it equals the `agentHeight` of all five shipped bakes — five independent agreements |
| `navmeshDataAreaHeight` | 2.5 | the same; a room layout's height is exactly 10 cells * 0.25 = 2.5 |
| `fixtureTouchSizeY` | 0.125 | the same |
| grid range | -128..127 per axis, 31.75 units half-extent | a grid coordinate is four signed bytes packed into one int32 |
| vertical stride | 500 | **not a storey height**: a room is about 2.5 units tall in this scale, so 500 is the stride that keeps two instances of one package from seeing each other. There are no floors in the geometry |

Every X and Z in the nine rows is a multiple of 200, the closest two sites in the
horizontal plane are 200 apart, and taking away both half-extents leaves 136.5, so
**no two sites can overlap horizontally**.

**Join on the name, not on arithmetic**: each row carries `siteType` (`home_site` /
`first_floor` / … / `festival_garden`) and `controller`. In this snapshot
`id = enum position + 1` holds for every row, but that is **a coincidence and not a
contract** — joining on `id - 1` breaks wholesale the moment a row is inserted.

### The scene binary: one package, one file, several scenes

A scene package holds **more than one prefab root**: beside the site prefab the game
places, the same package carries the model assets it was built from, the per-level
dressing sets (`rank1..rank5` at the home site), and — indoors — a sky of its own.
So a package becomes **one** glb with **one glTF scene per root**, mesh and material
data shared between the scenes, and `defaultScene` is the one the game places.
"One file per site, holding only the site prefab" drops the per-level dressing and
the indoor sky.

| Site | Vertices | Triangles | Prefab roots | Slots | Collision | Materials |
|---|---|---|---|---|---|---|
| `grasslands` | 209917 | 149205 | 79 | 7 | 3 | 87 |
| `memorialplace` | 140348 | 97268 | 65 | 7 | 3 | 72 |
| `beach` | 57672 | 52325 | 82 | 6 | 3 | 61 |
| `festivalgarden` | 46722 | 36733 | 65 | 7 | 2 | 92 |
| `home` | 26762 | 19963 | 13 | 7 | 3 | 38 |
| `flowergarden` | 20320 | 18574 | 90 | 7 | 2 | 51 |
| `first_floor` · `my_room` | 287 | 480 | 2 | 8 / 5 | 1 | 1 |

**There is no fixed seven-slot convention.** Only `navmesh_target`, `env` and
`collider` are in all eight packages, plus "at least one `decoration*`"; `base` does
**not exist** in the two indoor packages, the camera slot is in three packages only,
and `decoration (1)` (Unity's duplicate-name suffix) appears only in `grasslands`,
`flowergarden` and `memorialplace`. Each slot carries `role` and `known`: a false
`known` means this extractor has no semantics for that slot, **not** that it was
filed into some default bucket.

**Hidden nodes are kept and listed.** `inactiveNodes` names the nodes that ship
hidden (the housing-competition camera at the home site, for one), and the glb node
carries `extras.active = false`. The pack is the authored scene, so dropping them
would be untrue; drawing them would show what the game never shows — hence both.

### Collision semantics: one file per surface

A site's walkable ground, its camera blocker, its wall blocker and its
footstep-material surface are **four different things**, and merged into one file a
consumer can no longer tell them apart. So collision surfaces all **leave the visible
scene** and get one glb each, with the role their name states:

| `role` | Name suffix | Across the six outdoor packages |
|---|---|---|
| `walkableGround` | `_nav_ground` | 6/6 |
| `footstepSurface` | `_footse` | 6/6 |
| `cameraBlocker` | `_nav_cam` | `grasslands` and `memorialplace` only |
| `wallBlocker` | `_nav_wall` | `home` only |

A `null` role means the name does not say what it is, **not that it does nothing** —
the sea surface `sea01` at `beach` is exactly that, and it is **both visible geometry
and a collider**, so it is in the scene binary *and* in `collision/`, carrying
`visible: true`. "Collision is never visible" is not an invariant; one counterexample
is enough.

How a footstep cue is chosen: `footsteps` in `sites.json` is an **RGB-to-cue table**
(8 rows, one walk cue and one run cue each). **Which surface carries the colour was
not traced** — `_footse` is the candidate, but this pass did not reach the consumer
that samples it, so the table is given as it stands and nothing is asserted.

The two indoor packages hold **no `MeshCollider` at all** (only a 20-unit box at
`env/test_volume`): their wall and floor collision is in the kit and in the
walkable-surface package.

### Navigation: baked and runtime-built are two different answers

**Five packages ship a bake** (`grasslands` 30 tiles, `beach` 16, `flowergarden` 16,
`memorialplace` 12, `festivalgarden` 4) and **three do not** (`home` and the two
indoor shells). The two states must not stand in for one another: handing a
runtime-built site an empty tile list reads as "baked, and empty", which is the
opposite of what ships. The three without one build their surface at runtime from the
colliders under `navmesh_target`.

There are **two bake settings**: `grasslands` uses the default agent (`agentTypeID`
0) with manual cell and tile sizes and ships a height mesh; the other four share one
custom agent id `-1372625422`, fully automatic parameters and a `m_SourceBounds` of
extent (49.76, 5, 49.76) — **they were not baked in the same pass**. All five have
`agentHeight` 1.0, matching `playerHeight` five times over.

The tiles are **carried across as they are**: `navmesh.bin` is every tile's bytes
concatenated, `tiles.index` gives each one's `offset`, `bytes` and `hash`, and
`tiles.parsed` is always `false` with the reason beside it. Not parsing is a
decision: the format is Unity's own baked Detour data, and guessing at it would be
inventing data. **What can be given as geometry is the height mesh**: the one at
`grasslands` carries 415 vertices / 832 triangles and is exported as
`navmesh/heightmesh-0.glb`, so a consumer can draw or sample the walkable surface
with no navigation runtime. Every bake ships at position zero with an identity
rotation (`siteLocal: true`), so **it too must be offset by `sitePosition`**, exactly
like the geometry.

### The three room sites are a kit, not one glb each

The `first_floor` and `my_room` packages hold 105 and 92 objects: the site-view
shell, the weather presets, one test volume and a sky — **no `base` slot and no wall
or floor geometry at all**. A room is assembled at runtime from three packages, and
`indoor` in `index.json` is that assembly stated:

| Part | What is in it |
|---|---|
| `indoor.kit` | the kit: 18 meshes (floor and wall in large/medium/small, plus entrance and the sound floor), 11 materials, 15 `MeshCollider`s; **collision is the same meshes as the visible walls** |
| `indoor.levels.NN.module` | one `mdl_static_floor` + `mdl_static_wall` pair per level, every mesh referenced out of the kit. Measured: levels 01/02/03 all use `small`, 04 uses `medium`, 05 uses `large` |
| `indoor.levels.NN.walkable` | one collision-only prefab per level. Measured: 03/04/05 are small/medium/large, and **the colliders of 01 and 02 have a null mesh pointer** (`pathId = 0`) — those two levels ship **no** walkable surface, and the pack records that as "a null pointer is an authored state, not a lookup failure" rather than pretending otherwise |

So a product shaped as one glb per site would **silently drop three sites**: their
geometry is not in the site package at all. Room sizes come from
`levels[].layouts[]` in `sites.json`, given in cells and in units at once (room level
1 is 10x10x10 cells = 2.5 units tall; level 5 is 24x10x20).

### Materials: a family name and a property block, not a translation

Every site material's shader lives in a package **this domain does not own**
(`mysekai/shader`), so what can honestly be given is the shader's **family name**,
the tags of its first subshader, and **the whole authored property block** (floats,
colours, texture slots, and the scale/offset pair of each slot). How to read the
families: ground is `Mysekai/Site/Ground`, water `Mysekai/Water`, foliage
`Mysekai/Site/Tree`, every other solid `Mysekai/Site/FieldObject`; at
`festivalgarden` the ground shader is `Mysekai/Site/Ground-Birthday` instead.

The glTF material inside the binary is a **preview approximation**, not a
translation, and every value it uses is authored data: the base colour picture is the
first bound of `_MainTex`/`_BaseMap` (sampled through that slot's scale/offset pair,
via `KHR_texture_transform`), the base colour factor comes from `_Color`/`_BaseColor`,
`_UseAlphaClip` being on gives `alphaMode: MASK` with `_AlphaClip` as the threshold,
otherwise `BLEND` only when the shader's own `QUEUE` tag is `Transparent`, and
`_Cull` at 0 gives a two-sided material. **The record is the property block in the
JSON, not this approximation.** Images are referenced by relative path rather than
embedded, so each picture exists once in the pack.

Texture arrays (`Texture2DArray`) are exported one file per layer and kept in the
material's own `textureArrays` map rather than mixed into `textures`: an array is
sampled with a layer coordinate, and mixing the two invites a consumer to treat one
as the other.

### A timeline socket is empty, not "unsupported"

The two site shells (`site/root` and `site/environment/common`) each carry a
`PlayableDirector` whose **`m_PlayableAsset` is null**. The pack records that as an
empty socket in `timelineSockets` and states **what fills it**: the runtime assigns
one per phenomenon from `EnvironmentLoadData.PlayableAsset` through
`SiteEnvironmentViewController._playableDirector`. Writing "unsupported timeline"
would be the wrong record — it sends a consumer looking for an asset that does not
exist. The other eight directors **are bound** (five at `festivalgarden`, one in each
of three object packages) and carry `bound: true` with the asset's name.

### Furniture is not in a site

Not one of the eight scene packages holds a MonoBehaviour whose class names a
fixture: furniture is instantiated under the site view at runtime and is not baked
into a package. Across the whole site path **exactly one package** needs a furniture
package — the delivery site's birthday cake — and that is an **exception which must
not be generalised**.

Mind the two sources of dependencies: the `declaredDependencies` in the pack is what
the **package's own bundle declares**, which is **not** the shipped manifest's list.
Measured: the delivery site declares 12 inside its bundle where the manifest declares
23, and that furniture package appears **only on the manifest side**; `flowergarden`
is 1 against 5 and `grasslands` 1 against 4. Work out what a download must fetch from the
manifest; `declaredDependencies` answers only "what does this package point at", and
the `dependencySource` field says so inside the artifact.

### Every package was opened, and every object is accounted for

`packages.json` has one entry per package with its inventory, its type histogram, its
script histogram and `objects: {total, exported, skipped, unsupported,
accountedFor}`. The three dispositions **must add up to the total** (all 109 do) —
that is what makes "no package was left unqueried" checkable rather than merely
claimed. A `skipped` object always states why (the package's own manifest object, a
script's identity, an in-package shader variant, an animator state machine).
**A component this extractor has no structured reader for is still exported**: its
serialized fields are written out as they are, with pointers replaced by what they
name. Interpretation and extraction are two things, and declining to interpret is not
a reason to lose data.

One family deserves a note of its own: **the 23 room skins really do carry
`FixtureView` and `NavMeshModifier`**. "No fixture behaviour in a site scene" holds
for **the eight scene packages** only — a door or a window is itself a fixture-class
asset. The skins' door animations are decoded from their compiled curves (161 clips
in all), the node path of each binding is recovered by CRC-32 lookup, and a hash that
matches no node is kept as the hash and said to be unresolved.

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
8. Look up a phenomenon's site override two-level; a site absent from `overrides` uses the global values and is not a failed lookup.
9. Do not apply a post-processing parameter whose `overrideState` is false.
10. Use phenomenon configuration values as stored: sample the cloud shadow with the reciprocal of the image size, and scroll by the velocity times the scalar.
11. Read texture arrays from `textureArrays`, not `textures`; take the layer with `layerFormula` (floor, not rounding), and check `sampling.arrayMode` before sampling at all.
12. An emitter whose `renderMode` is `Mesh` must draw its `mesh`; a null `mesh` means the geometry it points at is in no package.
13. `omitted` is read-and-left-out on purpose; only `unsupported` is a gap. Do not treat them as one list.
14. Audio `skipped` means the external decoder is missing, not the data: the archive is on disk. Loop over `[loopStartSeconds, loopEndSeconds)`; the waveforms are not unrolled.
15. **A site glb is in site-local coordinates**: the world offset is only in
    `sitePosition` in `sites.json`, and a consumer applies it. Three room rows share
    one package, so baking the offset loses the second and third floors for good.
16. Place everything with `world = sitePosition + grid * 0.25`; `positionY` is a
    separation stride, not a storey height.
17. A site scene binary has several scenes: place `defaultScene`. The others are the
    model assets of the same package and its per-level dressing sets.
18. Use a collision surface by its `role`, not by its file name; a surface whose
    `role` is `null` (the sea at `beach`) may also be visible geometry.
19. Navigation has two states, baked and runtime-built: an empty `navmesh` array
    means the second, not a bake that came out empty. The tiles are opaque bytes;
    what you can draw is `heightmesh-N.glb`.
20. Assemble the three room sites from `indoor` in `index.json`: the kit, one module
    per level, one walkable surface per level. Levels 01 and 02 ship no surface.
21. Approximate a site material from its shader family and property block; the
    material inside the binary is a preview approximation, not the game's shading.
22. An unbound entry in `timelineSockets` is a socket the runtime fills, not a
    missing asset.
23. `declaredDependencies` is not the set a download must fetch; the shipped
    manifest states that (12 against 23 for the delivery site).
