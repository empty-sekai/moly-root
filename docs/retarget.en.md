# Retargeting The Shared Motion Library

[中文](retarget.md)

The AssetBundleInfoNew package manifest must be supplied by the user. This repository neither bundles nor distributes any game data.

This document describes applying the shared motion-library glTF to a humanoid that is not one of the source characters. The consumer needs a glTF loader and an animation system.

## Binding Names

Animation channels target glTF node names, not node indices. The 36 required humanoid bones are:

```text
Hips Spine Spine1 Head
LeftUpLeg RightUpLeg LeftLeg RightLeg LeftFoot RightFoot
LeftArm RightArm LeftForeArm RightForeArm LeftHand RightHand
LeftHandThumb1 LeftHandThumb2 LeftHandIndex1 LeftHandIndex2
LeftHandMiddle1 LeftHandMiddle2 LeftHandRing1 LeftHandRing2
LeftHandPinky1 LeftHandPinky2 RightHandThumb1 RightHandThumb2
RightHandIndex1 RightHandIndex2 RightHandMiddle1 RightHandMiddle2
RightHandRing1 RightHandRing2 RightHandPinky1 RightHandPinky2
```

When present in the source skeleton, clips also contain six optional auxiliary rotation tracks: `LeftArm_twist`, `LeftForeArm_twist`, `LeftHand_twist`, `RightArm_twist`, `RightForeArm_twist`, and `RightHand_twist`. The public hand spelling is `LeftPinky`; do not substitute an engine-specific alias unless the binding table explicitly creates it.

## Channel Semantics

Every required bone channel is a local glTF `rotation` quaternion relative to the node rest transform, in `[x, y, z, w]` order. `Hips` always carries a translation channel. It is a local offset in metres, not root motion or the target model's authored hips height. Do not apply it as an absolute world transform.

Most clips also carry **translation-DoF (TDoF) channels** on a subset of joints: the source data animates the local translation (full local translation, rest included) of `Spine`, `Spine1`, `Head`, `LeftLeg`, `RightLeg`, `LeftFoot`, `RightFoot`, `LeftArm`, `RightArm`, `LeftHand`, and `RightHand` (each clip uses only some of these). They carry the engine's joint translation animation — for example the arm roots shifting forward while the arms cross. Dropping them makes arm-heavy poses interpenetrate and desynchronizes the baked hips rotation, so same-skeleton playback **must apply them as-is**.

The six `_twist` channels are ordinary local rotations on helper nodes. If the target lacks those helpers, skip them; the main limb rotations remain usable. The public asset has no animation `scale` channel, so keep target node scales. The motion library uses right-handed, Y-up glTF coordinates with positions in metres.

## Hips Translation Scaling

Local rotations can be reused directly. Scale Hips translation by the height ratio:

```text
targetHipsOffset = sourceHipsOffset * targetHeight / sourceHeight
```

Prefer measured heights from the lowest planted foot to the top of the head for both rigs. Apply the result to the target `Hips` local translation, not every bone or the model root.

Non-`Hips` translation channels (TDoF) are the joint's **full local translation** (rest value plus animated offset). When retargeting to a differently proportioned rig, do not copy the absolute values: subtract the source rest translation of that bone to get the offset, scale it by the matching segment-length ratio (height ratio as an approximation), and add it onto the target bone's own rest translation:

```text
targetLocalT = targetRest + (channelValue - sourceRest) * targetSegment / sourceSegment
```

This is a proportional correction, not a full retarget solver; large proportion differences require IK or a dedicated retarget pass for exact foot and hand contact.

## Minimal three.js Example

```js
const sourceClip = library.animations.find((clip) => clip.name === "mov_cm_adult_idle_L");
const tracks = sourceClip.tracks.map((track) => {
  const nodeName = track.name.slice(0, track.name.indexOf("."));
  if (!targetNodes.has(nodeName)) return null;
  if (nodeName !== "Hips" || !track.name.endsWith(".position")) return track.clone();
  const values = track.values.slice();
  for (let i = 0; i < values.length; i += 3) values[i + 1] *= targetHeight / sourceHeight;
  return new THREE.VectorKeyframeTrack(track.name, track.times.slice(), values);
}).filter(Boolean);
const targetClip = new THREE.AnimationClip(sourceClip.name, sourceClip.duration, tracks);
```

In production, check for duplicate target node names and reject or mark a clip unusable when a required bone is missing. Do not guess by positional order or silently bind a different node.
