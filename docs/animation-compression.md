# Actor animation compression

The production motion exporter uses checked SNORM16 rotation encoding and
exact stream sharing by default. Neither changes the coordinate contract,
clip names/source identities, target nodes or source looping metadata. This
default applies to newly generated assets, not previously published snapshots.

## Default: SNORM16 rotations plus exact stream sharing

`chara.motion_library` uses one `MotionStorage` per exported GLB. Accessors with
identical layout (including normalized state), min/max and encoded bytes share
storage across tracks/clips. All authored animations/channels remain. LINEAR
rotation keys use glTF core `VEC4 / SHORT (5122) / normalized=true`; time and
translation keys remain float32. There is no key reduction, quaternion sign
rewriting or resampling, and no glTF extension is needed.

The independent `motionStorageRevision=2` and `rotationEncoding` are recorded
in GLB extras, companion motion indexes and source-qualified actor plans and
receipts. Both are included in actor export cache identities, so old float32
outputs are not incorrectly treated as current SNORM16 exports. This is not
a pose-binding revision.

To disable lossy encoding, pass `rotation_encoding="float32"` to
`export_motion_library`, `export_fixture_actor_libraries`, `build_plan` or
`extract_manifest`. The `motion-library`, `fixture-actors`, `extract` and
`jp prepare` commands expose the same choice as `--rotation-encoding float32`.
Omitting the option selects `snorm16`. Unknown encodings fail before writing.
Float32 keeps the original quaternion bytes and exact stream sharing.

Every source quaternion must satisfy the unit-length tolerance. Each decoded
key must have direction error at most 0.01 degrees and squared-norm error at
most 0.0002. Non-finite or out-of-budget streams fail rather than silently
falling back to a different encoding. Time, translation and clip metadata are
not quantized. The exporter and candidate tool use the same checked encoder.

## Offline conversion of an existing GLB

```text
python tools/encode_actor_animation_candidate.py --input ORIGINAL_ACTOR.glb --output NEW_CANDIDATE.glb --lossy-rotation-snorm16
```

This conversion is **lossy** and explicit. Both the
output and its `.encoding.json` report must be new files. Source files and
companion indexes are never overwritten. Use fresh snapshot identities for
any eventual publication; generating a candidate does not publish it.

Only exclusive, packed float32 `LINEAR` rotation outputs are converted.
Time, translation and other protected accessor bytes are unchanged. Cubic
rotation tangents and shared/mixed-use outputs are not converted. The tool
does not perform additional accessor pooling.

CLI limits can only be made stricter. Reports contain actual raw
sizes/hashes, changed/protected accessor counts and measured key errors.

These checks do not establish a continuous-time, blending or full-skeleton
error bound. Representative playback and interpolated-pose checks remain
necessary for each release; an unrelated blocked scene is not a playback pass. Bevy
0.18 reads SNORM16 rotations but expands them to float32 quaternions; it does
not retain compressed curve storage. HTTP Brotli can additionally compress
either representation, and its wire saving must be measured separately.

Draco is a mesh codec, not this animation encoding. The current Bevy loader
does not support `EXT_meshopt_compression`, so a meshopt animation path would
require a separate decoder integration.
