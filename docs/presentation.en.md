# Presentation Consumer Guide

[中文](presentation.md)

This document extends the [data contract](data-contract.en.md) with presentation-layer rules for browser consumers of character `.glb`, `*.rig.json`, and `emoticons.json` data. The data contract remains authoritative for field shapes; this document defines coordinates, attachment, facing, render state, depth, and draw order.

Examples use three.js. In the examples, `itemRoot` is the overhead item's root object, `item.nodes` is the node data from JSON, and `character` is an already-loaded character rig.

## 1. Coordinate Conversion

The character `.glb` is right-handed glTF, Y-up, and in metres; its world x coordinate is negated relative to the source data. Local transforms in overhead-item JSON remain in the source coordinate system, so the character glb transform rule must not be applied to every overhead item in the same way.

When consuming local data for a `particle` item, apply one conjugation to every local position and local quaternion:

```text
position([x, y, z]) = [-x, y, z]
rotation([qx, qy, qz, qw]) = [qx, -qy, -qz, qw]
emissionSample.position.x = -emissionSample.position.x
emissionSample.direction.x = -emissionSample.direction.x
```

Apply this to the particle item's local node data and emission samples; scale is unchanged. A `sprite` item is camera-frame content and does not use this conjugation; consume its item data directly.

Minimal three.js implementation:

```js
function transformParticleLocal(nodeData, object3d) {
  const [x, y, z] = nodeData.position;
  const [qx, qy, qz, qw] = nodeData.rotation;
  object3d.position.set(-x, y, z);
  object3d.quaternion.set(qx, -qy, -qz, qw);
}

function transformParticleSample(sample)
  return {
    ...sample,
    position: [-sample.position[0], sample.position[1], sample.position[2]],
    direction: [-sample.direction[0], sample.direction[1], sample.direction[2]],
  };
}

function applyItemTransform(item, nodeData, object3d) {
  if (item.viewKind === "particle") transformParticleLocal(nodeData, object3d);
  else {
    object3d.position.fromArray(nodeData.position);
    object3d.quaternion.fromArray(nodeData.rotation);
  }
}
```

Do not reflect the same particle data again. Do not reflect a sprite's camera-frame data.

## 2. Attachment

The four attachment nodes in the character rig use the same names in all 31 characters: `HeadRoot`, `Head`, `Spine`, and `Hips`.

Use this dispatch table:

```text
sprite                                      -> HeadRoot, clear local position
particle + view.anchor == Face             -> Head
particle + view.anchor == Spine            -> Spine
particle + any other or missing anchor     -> Hips
```

Retain the local position of a `particle` item. These local positions are all zero in the current data, but a consumer should still read the field rather than lift the item itself. Attachment dispatch is independent of `keepPosition`; `keepPosition` gates rotation only, as described in the next section.

three.js example:

```js
const particleAnchor = {
  Face: "Head",
  Spine: "Spine",
};

function attachItem(item, itemRoot, character) {
  const anchorName = item.viewKind === "sprite"
    ? "HeadRoot"
    : (particleAnchor[item.view?.anchor] ?? "Hips");
  const anchor = character.getObjectByName(anchorName);
  if (!anchor) throw new Error(`Missing attachment node: ${anchorName}`);

  anchor.add(itemRoot);
  if (item.viewKind === "sprite") itemRoot.position.set(0, 0, 0);
  // Particle local position is read and retained; keepPosition does not clear it.
  return anchor;
}
```

For particle nodes, first apply the coordinate conversion from the preceding section, then use the result as the attachment's local position. For a sprite root, always write zero local position.

## 3. Facing

### Sprite

Every frame, write the sprite's **world** rotation as the equivalent of making the item face the camera. The three.js object-forward convention is opposite to this contract, so use this direction for facing:

```text
forward = normalize(camera.position - itemWorldPosition)
```

The equivalent quaternion rotates the item's convention-facing axis to `forward`. If the mesh's facing axis is `+Z`:

```text
spriteWorldRotation = quatFromUnitVectors(+Z, forward)
```

three.js example (when a parent exists, convert the world quaternion back to local):

```js
const worldPosition = new THREE.Vector3();
const parentWorldQuaternion = new THREE.Quaternion();
const forward = new THREE.Vector3();
const itemWorldQuaternion = new THREE.Quaternion();

function faceCamera(spriteRoot, camera) {
  spriteRoot.getWorldPosition(worldPosition);
  forward.copy(camera.position).sub(worldPosition).normalize();
  itemWorldQuaternion.setFromUnitVectors(
    new THREE.Vector3(0, 0, 1),
    forward,
  );
  spriteRoot.parent.getWorldQuaternion(parentWorldQuaternion);
  spriteRoot.quaternion.copy(
    parentWorldQuaternion.invert().multiply(itemWorldQuaternion),
  );
}
```

### Particle

Only a particle item with `view.keepPosition == true` receives a per-frame rotation write. First compute the signed horizontal `xz` angle from the Hips attachment's forward direction to the camera direction:

```text
toCamera = normalizeXZ(camera.position - HipsAnchor.worldPosition)
hipsForward = normalizeXZ(HipsAnchor.forward)
thetaSource = signedAngleXZ(hipsForward, toCamera)
theta = -thetaSource
particle.localRotation = Euler(theta, 0, 0)
```

The negative sign is the post-conversion sign reversal. When `keepPosition == false`, write **nothing**: preserve the prefab rotation and continue inheriting the rig rotation. `keepPosition` gates rotation, not position.

The following `signedAngleXZ` uses `atan2(crossY, dot)` with one fixed horizontal-plane sign convention:

```js
function normalizeXZ(vector) {
  vector.y = 0;
  return vector.normalize();
}

function signedAngleXZ(from, to) {
  const crossY = from.z * to.x - from.x * to.z;
  const dot = from.x * to.x + from.z * to.z;
  return Math.atan2(crossY, dot);
}

const hipsWorldPosition = new THREE.Vector3();
const hipsForward = new THREE.Vector3();
const toCamera = new THREE.Vector3();

function updateParticleRotation(item, particleRoot, hipsAnchor, camera) {
  if (item.view?.keepPosition !== true) return;

  hipsAnchor.getWorldPosition(hipsWorldPosition);
  hipsAnchor.getWorldDirection(hipsForward);
  toCamera.copy(camera.position).sub(hipsWorldPosition);
  normalizeXZ(hipsForward);
  normalizeXZ(toCamera);
  const theta = -signedAngleXZ(hipsForward, toCamera);
  particleRoot.rotation.set(theta, 0, 0);
}
```

## 4. Apply Render State By Shader Family

Materials carry a `shader` field. Select a family by the complete shader name first, then interpret that family's fields. Do not treat a property from one family as pass state for another family.

### Unity BlendMode To WebGL Factors

For `Mysekai/Effect/UberUnlit`, blend factors come from `_BlendSrc` and `_BlendDst`. Use this mapping:

| Unity BlendMode | Value | WebGL factor |
|---|---:|---|
| Zero | 0 | `gl.ZERO` |
| One | 1 | `gl.ONE` |
| DstColor | 2 | `gl.DST_COLOR` |
| SrcColor | 3 | `gl.SRC_COLOR` |
| OneMinusDstColor | 4 | `gl.ONE_MINUS_DST_COLOR` |
| SrcAlpha | 5 | `gl.SRC_ALPHA` |
| OneMinusSrcAlpha | 6 | `gl.ONE_MINUS_SRC_ALPHA` |
| DstAlpha | 7 | `gl.DST_ALPHA` |
| OneMinusDstAlpha | 8 | `gl.ONE_MINUS_DST_ALPHA` |
| SrcAlphaSaturate | 9 | `gl.SRC_ALPHA_SATURATE` |
| OneMinusSrcColor | 10 | `gl.ONE_MINUS_SRC_COLOR` |

three.js factor and depth comparison example:

```js
const unityBlendToThree = {
  0: THREE.ZeroFactor,
  1: THREE.OneFactor,
  2: THREE.DstColorFactor,
  3: THREE.SrcColorFactor,
  4: THREE.OneMinusDstColorFactor,
  5: THREE.SrcAlphaFactor,
  6: THREE.OneMinusSrcAlphaFactor,
  7: THREE.DstAlphaFactor,
  8: THREE.OneMinusDstAlphaFactor,
  9: THREE.SrcAlphaSaturateFactor,
  10: THREE.OneMinusSrcColorFactor,
};

const unityZTestToThree = {
  1: THREE.NeverDepth,
  2: THREE.LessDepth,
  3: THREE.EqualDepth,
  4: THREE.LessEqualDepth,
  5: THREE.GreaterDepth,
  6: THREE.NotEqualDepth,
  7: THREE.GreaterEqualDepth,
};

function applyZTest(material, zTest) {
  if (zTest === 0 || zTest === 8) {
    material.depthTest = false;
    return;
  }
  material.depthTest = true;
  material.depthFunc = unityZTestToThree[zTest] ?? THREE.LessEqualDepth;
}
```

### `Mysekai/Effect/UberUnlit` (particle)

- Blending uses `_BlendSrc` / `_BlendDst` through the mapping above; `_SrcBlend` / `_DstBlend` are residual fields and do not take effect.
- Depth testing is disabled when `_ZTest` is `0` or `8`; other known comparison values use the mapping above.
- Depth writing uses `_ZWrite`; its current value is always `0`, so particles do not write depth.
- Back-face behavior: `_Cull == 0` means double-sided; `_Cull == 2` means back-face culling, which is `FrontSide` in three.js.
- The target color mask writes RGB but not target alpha. A custom pass must use `(true, true, true, false)` for the color mask; particles must not modify target alpha.

```js
function configureUberUnlit(material, floats) {
  material.transparent = true;
  material.blending = THREE.CustomBlending;
  material.blendSrc = unityBlendToThree[floats._BlendSrc] ?? THREE.OneFactor;
  material.blendDst = unityBlendToThree[floats._BlendDst] ?? THREE.ZeroFactor;
  material.blendEquation = THREE.AddEquation;
  material.depthWrite = Boolean(floats._ZWrite);
  material.side = floats._Cull === 0
    ? THREE.DoubleSide
    : THREE.FrontSide; // _Cull == 2: cull back faces
  applyZTest(material, floats._ZTest);
  // A custom WebGL pass must use gl.colorMask(true, true, true, false).
}
```

### `Mysekai/Emoticon/Sprite` (sprite)

- Blending is fixed at `One` / `OneMinusSrcAlpha`.
- The fragment color is premultiplied with `rgb *= a`; in three.js, enable `premultipliedAlpha: true` on the material only (the renderer option of the same name is a canvas-compositing setting — leave it at its default).
- Back-face behavior is fixed to double-sided.
- `_ZWrite` is always `1` in the current data, so sprites write depth.
- Handle `_ZTest` using the rules above.

```js
function configureSprite(material, floats) {
  material.transparent = true;
  material.premultipliedAlpha = true;
  material.blending = THREE.CustomBlending;
  material.blendSrc = THREE.OneFactor;
  material.blendDst = THREE.OneMinusSrcAlphaFactor;
  material.blendEquation = THREE.AddEquation;
  material.side = THREE.DoubleSide;
  material.depthWrite = true;
  applyZTest(material, floats._ZTest);
}

const renderer = new THREE.WebGLRenderer({
  alpha: true,
  premultipliedAlpha: true,
});
```

Residual fields with names such as `_ZWrite` or `_ZTest` that do not drive the pass must not be guessed into use; family rules take precedence over property names.

### Unknown Shaders

An unknown shader falls back to a neutral material and emits a warning. Do not guess blend, depth, or culling state from property names:

```js
function neutralMaterial(shaderName) {
  console.warn(`Unknown shader family: ${shaderName}`);
  return new THREE.MeshBasicMaterial({
    transparent: true,
    blending: THREE.NormalBlending,
    depthTest: true,
    depthWrite: false,
    side: THREE.FrontSide,
  });
}
```

## 5. Depth Offset `_ZOffset`

`_ZOffset` adds to linear eye depth in metres; it is not polygon offset. It changes only clip-space `z`, leaving `x`, `y`, and `w` unchanged:

```text
viewPos = modelViewMatrix * vec4(position, 1)
d = -viewPos.z + _ZOffset
clip.z = P[2][2] * (-d) + P[3][2]
```

The activation threshold is family-specific:

```text
UberUnlit: abs(_ZOffset) > 0.004
Sprite:    _ZOffset > 0.004
```

The Sprite test has no `abs`; all non-zero Sprite values in the current data are negative, so none of those values takes effect. A negative value pulls depth toward the camera and lets an overhead item pass in front of the character; this is the only mechanism that floats an item in front of the character.

Example custom vertex stage in three.js:

```glsl
vec4 viewPos = modelViewMatrix * vec4(position, 1.0);
vec4 clip = projectionMatrix * viewPos;

bool active = isUberUnlit
  ? abs(uZOffset) > 0.004
  : uZOffset > 0.004;
if (active) {
  float d = -viewPos.z + uZOffset;
  clip.z = projectionMatrix[2][2] * (-d) + projectionMatrix[3][2];
}
gl_Position = clip;
```

In JavaScript, let the family determine only the threshold test; do not replace this with `polygonOffset` or change `clip.x`, `clip.y`, or `clip.w`:

```js
function zOffsetActive(shaderFamily, zOffset) {
  return shaderFamily === "uberUnlit"
    ? Math.abs(zOffset) > 0.004
    : zOffset > 0.004;
}
```

## 6. Draw Order

The draw order is:

```text
opaque character draw (writes depth)
    -> all transparent overhead-item draws
       -> overhead items sorted from farthest to nearest
```

Sort overhead items by eye depth. For world position `p`, transform it into camera space:

```text
eyeDepth(p) = -(camera.matrixWorldInverse * vec4(p, 1)).z
```

An item with larger eye depth is farther away and is drawn first. Pseudocode:

```text
items.sort((a, b) => eyeDepth(b) - eyeDepth(a))
```

three.js example:

```js
const worldPosition = new THREE.Vector3();
const viewPosition = new THREE.Vector3();

function eyeDepth(object3d, camera) {
  object3d.getWorldPosition(worldPosition);
  return -viewPosition.copy(worldPosition)
    .applyMatrix4(camera.matrixWorldInverse).z;
}

function orderOverheadItems(items, camera) {
  return items.slice().sort(
    (a, b) => eyeDepth(b.root, camera) - eyeDepth(a.root, camera),
  );
}

function assignRenderOrder(characterRoot, orderedItems) {
  characterRoot.renderOrder = 0;
  for (let i = 0; i < orderedItems.length; i += 1) {
    orderedItems[i].root.renderOrder = 1000 + i;
  }
}
```

Character materials must write depth and draw first. Sprite materials write depth, so after drawing they occlude farther particle items; particle items test against that depth according to their material family. Do not substitute any per-item sorting field for this order: current `sortingOrder` values are all `0`, and only a few items use `0/1` internally for two layers. Those values are not a global draw-order protocol.

## 7. Particle Value Encoding

The four min-max curve modes and the two-axis gradient rules are fully specified in the [particle value encoding section of the data contract](data-contract.en.md#particles). They are not repeated here; implement the mode tags, keyframe timelines, and independent color/alpha timelines exactly as specified there.

The three.js consumer only needs to connect one evaluator to the particle update loop:

```js
function updateParticle(particle, life01, random01) {
  particle.size = evaluateParticleValue(
    particle.definition.system.start.size,
    life01,
    random01,
  );
  particle.color = evaluateParticleColor(
    particle.definition.system.colorOverLifetime,
    life01,
    random01,
  );
}
```

`evaluateParticleValue` and `evaluateParticleColor` should implement the data-contract encoding directly; do not treat a missing field as another mode.

## 8. Known Approximations And Undetermined Semantics

Emission-shape local directions and birth distributions are **settled**, implemented per shape after the engine's native semantics:

- `Sphere`: isotropic in the volume; the direction is the radial unit vector of the position — no preferred axis.
- `Circle`: position and direction both lie in the shape-local XY plane; the direction is radially outward — **not the +Z normal**; `arc` limits the angular domain.
- `Cone`: base circle in the local XY plane (z = 0); the main axis is +Z and the direction tilts by `angle` (`angle = 0` gives exactly `(0,0,1)`).
- `SingleSidedEdge`: position uniform along the local X axis within `±radius` (`radius` is the half-length); the direction is always local +Y.
- `BoxEdge`: position on the 12 edges of the unit cube `[-0.5, 0.5]^3` (actual size comes from `scale`); the direction is always local +Z regardless of edge.

The shape's own transform then applies: `pos' = R * (S * p) + t` and `dir' = normalize(R * (S * n))` — **scale before rotation**, with `rotation` composed in Z-X-Y Euler order (`'YXZ'` in three.js terms). `radiusThickness` restricts sampling to an annulus (1 = whole region, 0 = outer rim only). A zero scale axis can collapse the direction to a zero vector: keep zero initial velocity in that case instead of inventing a direction. See `emitFrom()` in `examples/viewer/emoticon.js` for a full reference implementation.

The following two items remain explicit consumer boundaries:

1. Tight sprite meshes are not exported. Use a plane covering the full rectangle; do not assume tight-packed vertices exist.
2. The sign mapping for particle spin direction is undetermined. Do not assign a particular rotation direction to positive or negative angular speed without an explicit mapping.

three.js example:

```js
function makeSpriteGeometry() {
  // The exported presentation uses a full rectangle, not a tight mesh.
  return new THREE.PlaneGeometry(1, 1);
}

function applyParticleSpin(baseAngle, spinSign) {
  if (spinSign !== 1 && spinSign !== -1) return baseAngle;
  return baseAngle * spinSign;
}
```

When no explicit spin-sign configuration is available, retain a neutral result and record a warning rather than presenting an approximation as settled semantics.

## 9. Consumer Self-Check

Before handing the presentation result to the browser renderer, check at least the following:

1. Is coordinate conversion applied only to `particle` items, with no conjugation on `sprite` items?
2. Is fragment premultiplication enabled for sprites, together with three.js `premultipliedAlpha: true`?
3. Are attachments dispatched as `sprite -> HeadRoot`, `Face -> Head`, `Spine -> Spine`, and other/missing -> `Hips`?
4. Does `keepPosition` control only particle rotation, while local position is still written from the item data?
5. Does the character draw first and write depth, with all transparent overhead items drawn afterward and sorted far to near?

```js
function checkPresentation(item, state) {
  console.assert(state.coordinateConversion === (item.viewKind === "particle"));
  console.assert(state.premultipliedAlpha === (item.viewKind === "sprite"));
  console.assert(state.anchorAssigned === true);
  console.assert(state.positionWritten === true);
  console.assert(state.keepPositionControlsRotationOnly === true);
  console.assert(state.characterDepthPassBeforeItems === true);
  console.assert(state.transparentItemsAfterCharacter === true);
}
```

When a check fails, correct the consumer rule before changing emission rates, material colors, or camera parameters.
