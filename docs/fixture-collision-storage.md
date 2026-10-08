# Fixture collision storage v4

Fixture index version **4** stores each GLB's collision document once. This is
a storage change, not a coordinate or collision-geometry change. The coordinate
contract remains `moly-rh-y-up-reflect-x-v1`, the units remain
`source-unity-unit`, and the collision document's `schemaVersion` remains `1`.

## Detached document and scene references

Append one node at index `N` after all original nodes:

```json
{
  "name": "__moly_fixture_collision",
  "extras": {
    "fixtureCollision": {
      "schemaVersion": 1,
      "coordinateContract": "moly-rh-y-up-reflect-x-v1",
      "units": "source-unity-unit",
      "geometry": [],
      "gaps": []
    }
  }
}
```

The example uses empty arrays; actual exports keep the complete original
collision document unchanged. The top-level GLB `extras` and the `extras` of
**every scene-root node** replace their inline `fixtureCollision` with the same
reference (shown here for an appended node at index `42`):

```json
{ "fixtureCollisionRef": { "schemaVersion": 1, "node": 42 } }
```

The reference has exactly these two fields. The metadata node has only `name`
and `extras`; its extras contain only `fixtureCollision`. It must not occur in
any scene's roots, any node's children, a skin's skeleton or joints, or an
animation channel's target. It has no mesh, skin, transform, or children.
Inline documents and references must not coexist on a referring object, and
all scene-root references must agree with the top-level reference.

Existing node, scene, animation, and accessor indices stay unchanged. The
metadata node is not an original source node and must not enter source
`nodeNames` or scene statistics. Per-node `sourceCollision`, source identities,
activity flags, and geometry values are unchanged.

Bevy loads detached `GltfNode` assets even when they are not instantiated in a
scene. This lets the runtime resolve the document through the active fixture's
source GLB without retaining the entire source JSON/BIN. The decoded document
is shared within one collision bake; its temporary cache does not retain asset
handles or become a permanent geometry cache.

## Compatibility and publication

Updated readers accept fixture indexes v3 and v4. V3 inline documents remain
supported; v4 exported GLBs require detached references. Missing, conflicting,
or invalid references are errors, not grounds to silently omit collision.

Publish converted assets as a **new snapshot** with fresh publication evidence;
do not replace files inside an existing immutable snapshot. Pair it with the
updated runtime supporting v4. That runtime must perform coordinate preflight
before importing or initializing WASM. The unchanged coordinate-contract name
alone does not make an older runtime capable of reading v4 storage.

## Creating a lossless overlay

Run from the extractor repository, with a canonical source root containing
`cn/fixture-models` and/or `jp/fixture-models`:

```text
python tools/deduplicate_fixture_collision.py --source SOURCE_ROOT --output NEW_OVERLAY_ROOT --regions cn jp
```

The output directory must not exist and must be separate from the source tree.
The tool does not modify source files or upload anything. It copies each GLB
with byte-identical binary chunks, verifies unchanged collision and other JSON
values, and writes each region's original `fixture-models/index.json` with only
its version changed to 4. Integer identities and floating-point precision are
preserved. `report.json` and per-region receipts record sizes and hashes; raw
GLB savings do not establish gzip/Brotli transfer savings.

This is a **GLB/index overlay, not a standalone deployment**. Assemble the new
snapshot from the same canonical source, replacing only the overlaid files.
Textures, shaders, animations, `source.json`, and
`fixture-attach/attach-points.json` remain dependencies supplied by that source;
the conversion does not copy them into the overlay.

## Fixture view collision references

`fixture-attach/attach-points.json` records each serialized view's `collider`:
`{pointer: {fileId, pathId}, status, source}`. `pathId` is a signed decimal string.
An authored null has `pathId: "0"`, `status: "null"`, and `source: null`.
A resolved reference has `status: "resolved"` and
`source: {file, pathId, kind: "MeshCollider"}`. Missing fields, unresolved non-null
references, and references to another component type fail extraction.

Action-button bounds read this exact reference. The presence of another collider
in the prefab, or the referenced collider's enabled flag, does not replace it.
Serialized `FixtureView` prefabs keep their authored collider and modifier graph;
the exporter does not add renderer-derived fallback colliders or modifiers.
`FixtureController.Initialize` only calls `AttachComponents` when it creates a
missing view, a separate runtime construction path.
