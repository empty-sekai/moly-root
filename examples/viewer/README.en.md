# Character viewer example

This is a browser-native ES module example for inspecting character models, motion clips, facial atlases, cloth simulation, and overhead items. It contains code and documentation only. Real character models, textures, extracted JSON, and other game data are not distributed with this repository.

## Start a server

Serve the example through a static HTTP server. Double-clicking `index.html` or using a `file://` URL can cause the browser to block ES modules, import maps, JSON, and glTF requests. The page may then remain empty or report load failures.

From the repository root, run any static server, for example:

```text
python -m http.server 8000
```

Then open:

```text
http://localhost:8000/examples/viewer/index.html
```

Do not commit a local extraction directory or copy real textures and models into this example directory.

## Data placement

Put the extracted output directory (`emoticons/`, `*.glb`, `*.rig.json`, `manifest.json`, `alone-actions.json`, and `facial-tables.json`) in this directory, or point to it with `?base=`.

By default, the page reads these files from its own directory:

- `manifest.json`: optional character index; a built-in unit list is used when it is absent.
- `sd_<unit>.glb`: character glTF model and animation data.
- `sd_<unit>.rig.json`: attachment nodes, facial defaults, and optional cloth contract.
- `facial-tables.json`: eye, mouth, and default facial tables.
- `alone-actions.json`: performance data for motion, facial, and overhead-item channels; the performance control is unavailable when it is absent.
- `motion-library.glb` and `motion-library.index.json`: the shared motion library (`moly extract`'s default layout; clips bind by humanoid bone name, so one library serves every character). Optional when the character glb embeds its own clips.
- `emoticons/emoticons.json`: overhead-item registry; PNG files referenced by its entries are also required below the same directory.

You can keep these outputs elsewhere and set the data root with a URL parameter:

```text
http://localhost:8000/examples/viewer/index.html?base=../../local-data
```

`../../local-data` is exactly the default output directory of `moly extract`, so after a default extraction this URL works as-is. `base` is a directory relative to the page URL or an accessible absolute URL. The viewer appends the filenames above to it, so relative references inside that directory must also be served by the same HTTP server. Encode special characters according to URL rules.

The example does not guess missing real data. Missing `manifest.json`, rig sidecars, performance data, or overhead-item data is reported in the status bar and self-check panel. Facial tables and selected material fields may use only the neutral defaults explicitly marked in the code.

## Performance scenarios and the motion library

The primary entry is the **performance scenario** list (left panel): each scenario is a short script from the performance data — typically one or a few motion steps paired with a series of eye, mouth, and overhead-item steps. Clicking a scenario plays it through once (all three channels in sync) and it stops naturally at the end; it never switches to another scenario on its own.

**Auto perform** (on by default) only covers idle time: when no scenario is playing, the orchestration picks the next scenario by the selected policy. "Original policy" follows the source data's time gates and probabilities (idling most of the time is the original pacing); "cycle" steps through every scenario for inspection. It never interferes with a manually started scenario and resumes after it finishes.

**Restoration scope**: what is restored today is the **solo performance scenarios** — the self-acting part of the performance data. Performances that interact with furniture (whose triggers and assets live in the furniture domain) are **not yet supported**; they belong to the furniture asset pack on the roadmap.

The right panel also offers **eye/mouth pattern selectors** (rows of the facial tables): a selection applies immediately, and the blink and speak machines use the selected rows' open/close cells; like the motion library this is an inspection tool, so selecting disables auto perform.

The **motion library** is a secondary inspection tool (collapsed in the right panel): it plays a single motion family or segment directly. Motion names carry no facial or overhead-item pairing (the pairing exists only in the performance data), so the face stays at its default; starting one disables auto perform (visible on its button), which you can re-enable at any time.

## Query parameters

| Parameter | Effect |
| --- | --- |
| `base=<url>` | Sets the data root, allowing extracted outputs to live outside the example directory. |
| `play=1` | Automatically plays the first available motion family in preview mode after loading (performance suspended). |
| `play=<text>` | Automatically plays a matching motion family in preview mode. |
| `freeze=<seconds>` | Advances the animation to the given time and pauses it for a repeatable view. |
| `perf=0` | Starts in motion-preview mode (performance playback is on by default). |
| `perfmode=faithful` | Selects scenarios using the probabilities and time gates in the performance data. |
| `perfmode=cycle` | Plays scenarios in sequence for coverage of each scenario. |
| `emote=0` | Prevents performance data from driving overhead items automatically; manual controls remain available. |
| `emoteitem=<name>` | Plays the named overhead item after the character loads. |
| `emoteface=0` | Disables camera-facing updates for overhead items and keeps their world orientation. |
| `debug=0..3` | Selects a shading diagnostic mode. |
| `sabotage=<names>` | Injects self-check faults. Available names include `stencil`, `nan`, `gamma`, `shader`, `anim`, and `patch`; separate multiple names with commas. |
| `clothdebug=1` | Prints periodic cloth-chain displacement diagnostics. |

For example, this URL uses external data, starts motion, and freezes at 0.75 seconds:

```text
http://localhost:8000/examples/viewer/index.html?base=../../local-data&play=1&freeze=0.75
```

## JavaScript modules

| Module | Responsibility |
| --- | --- |
| `viewer.js` | Page entry point and orchestration layer. Creates the scene, loads JSON and glTF, assembles materials and controllers, connects the UI, handles query parameters, updates cloth and overhead items, and runs self-checks. |
| `shading.js` | Character toon `ShaderMaterial`, body masks, facial branches, light parameters, eyebrow stencil overlay, and render-state updates. Textures are sampled linearly and the renderer performs no tone mapping. |
| `facial.js` | Facial atlas indexing, UV offsets, default facial state, blink state machine, and speaking-mouth state machine. Indices use one-based, four-column, lower-bound-one behavior. |
| `cloth.js` | 90 Hz Verlet cloth simulation, distance and root-distance constraints, angular limits, sphere/capsule/plane collisions, finite-value guards, teleport resets, and glTF node-index binding. |
| `segments.js` | Groups motion clips by `_S`, `_L`, `_E`, and `_O` suffixes, then implements Start, Loop, End, OneShot, and 0.5-second cross-fades. |
| `performance.js` | Reads `alone-actions.json`, selects mutually exclusive scenarios, handles `randomBranch`, `timeGated`, tail steps, the nominal timeline, and the independent motion, facial, and overhead-item channels. |
| `emoticon.js` | Loads `emoticons.json` and PNG files, plays sprite clips, simulates particle emitters, samples curves and colors, and applies shader-family depth, blend, and orientation rules. |
| `selfcheck.js` | Checks stencil, color space, shader compilation, data sidecars, facial patch drawing, motion advancement, segment transitions, cloth constraints, and facial state. It also implements the `sabotage` parameter. |
| `GLTFLoader.js` | Three.js glTF/glb loader, distributed as a browser module with Three.js r160. |
| `OrbitControls.js` | Three.js orbit camera controller for drag rotation, zoom, and damping. |
| `BufferGeometryUtils.js` | Three.js geometry utility module used by the glTF loader. |
| `three.module.min.js` | Minified Three.js r160 ES module runtime. Its license is recorded in `THIRD_PARTY_NOTICES.md` at the repository root. |

## Contract references

This example is not a second definition of the data format. Use these public documents as the field authority:

- [`docs/data-contract.en.md`](../../docs/data-contract.en.md) describes glTF, rig sidecars, facial tables, performance data, overhead items, particle modes, material fields, and missing-data semantics.
- [`docs/presentation.en.md`](../../docs/presentation.en.md) extends the data contract with presentation rules for coordinate conversion, attachment, sprite facing, particle facing, shader-family render state, depth offset, and draw order.
- Chinese readers can use [`docs/data-contract.md`](../../docs/data-contract.md) and [`docs/presentation.md`](../../docs/presentation.md).

`viewer.js` connects these contracts to a Three.js scene. `shading.js` mainly implements the character render-state rules from the presentation guide. `emoticon.js` mainly implements the overhead-item coordinate, attachment, facing, and particle render-state rules. `facial.js`, `cloth.js`, `segments.js`, and `performance.js` consume the rig, facial, cloth, motion-index, and performance fields respectively. When example code and contract documentation appear ambiguous, use the contract's field definition and let missing runtime data remain visible as a diagnostic state.

## What is not included

This directory does not distribute real character glTF files, PNG files, rig JSON, manifests, performance data, facial tables, or overhead-item data. Users must prepare those files from their own lawful data sources and expose them through a local static server or the `base` parameter. The example code and documentation can be read without data, but a real character cannot be rendered without it.

## Compatibility

Use a modern browser with ES modules, import maps, WebGL2, and stencil-buffer support. The browser console and the right-side self-check panel report load failures, missing sidecars, shader compilation, and runtime checks. A `skip` result means that optional input is missing or the corresponding action has not run; it does not mean that the data passed the check.
