// Head-only export. All textures, toon shading, facial atlas, and stencil passes
// still come from viewer.js; this module only selects geometry and frames it.
import * as THREE from './three.module.min.js';

// Source light direction is `L = (cos(xz)·cos(y), sin(y), sin(xz)·cos(y))`
// (shading.js `lightDirFromAngles`). The portrait camera sits on +z looking
// back at the head, so xz = 90° is the camera-facing (front) azimuth and 0°
// would be a hard side light. Elevation stays small: a high key light puts
// the brow in shadow and reads as an error when 31 heads sit side by side.
export const PORTRAIT_LIGHT = { azimuthDegrees: 90, elevationDegrees: 12 };

// Framing preset: crown-to-chin of the head itself occupies a fixed share of
// the frame, and the head's own centre rides the optical axis, so every
// portrait measures the same regardless of hairstyle. Only the head is
// protected: hair that trails below the chin or spreads past the skull may
// leave the frame, and the capture reports the head's projected rectangle so
// the finalizer can prove the head itself was never cut.
export const PORTRAIT_FRAMING = { fovDegrees: 35, heightFraction: 0.76, nearClearance: 0.01 };

// Final images are 512 x 512; the projected rectangle below is reported in the
// same pixel grid so the validator and the renderer cannot drift apart.
const FRAME_PIXELS = 512;

function isHeadBone(bone) {
  for (let node = bone; node; node = node.parent) {
    if (node.name === 'Head') return true;
  }
  return false;
}

export function frameHeadPortrait({ root, camera, controls }) {
  root.updateMatrixWorld(true);
  const bounds = new THREE.Box3();
  const skullBounds = new THREE.Box3();
  const headPoints = [];
  const skullPoints = [];
  const point = new THREE.Vector3();
  const statistics = [];
  root.traverse(mesh => {
    if (!mesh.isMesh) return;
    // Accessories without a skin are not silently guessed to be a head.
    if (!mesh.isSkinnedMesh) {
      mesh.visible = false;
      statistics.push({ mesh: mesh.name, keptTriangles: 0, reason: 'unskinned' });
      return;
    }
    const geometry = mesh.geometry;
    const skinIndex = geometry.getAttribute('skinIndex');
    const skinWeight = geometry.getAttribute('skinWeight');
    if (!skinIndex || !skinWeight) throw new Error(`Missing skin attribution: ${mesh.name}`);
    const head = mesh.skeleton.bones.map(isHeadBone);
    const count = geometry.getAttribute('position').count;
    const keep = new Uint8Array(count);
    for (let vertex = 0; vertex < count; vertex++) {
      let weight = 0;
      for (let lane = 0; lane < skinIndex.itemSize; lane++) {
        if (head[skinIndex.getComponent(vertex, lane)]) weight += skinWeight.getComponent(vertex, lane);
      }
      // A shoulder, chest, sleeve, and torso cannot enter the image. Boundary
      // triangles with any non-head vertex are omitted rather than stretched.
      keep[vertex] = Number(weight >= 0.999);
    }
    const indices = geometry.getIndex();
    const total = indices ? indices.count : count;
    const selected = [];
    const used = new Set();
    for (let triangle = 0; triangle < total; triangle += 3) {
      const corners = [0, 1, 2].map(lane => indices ? indices.getX(triangle + lane) : triangle + lane);
      if (!corners.every(vertex => keep[vertex])) continue;
      selected.push(...corners);
      for (const vertex of corners) used.add(vertex);
    }
    statistics.push({ mesh: mesh.name, keptTriangles: selected.length / 3, originalTriangles: total / 3 });
    if (!selected.length) { mesh.visible = false; return; }
    mesh.geometry = geometry.clone();
    mesh.geometry.setIndex(selected);
    mesh.geometry.clearGroups();
    mesh.frustumCulled = false;
    for (const vertex of used) {
      mesh.getVertexPosition(vertex, point).applyMatrix4(mesh.matrixWorld);
      bounds.expandByPoint(point);
      headPoints.push(point.clone());
      let directWeight = 0;
      for (let lane = 0; lane < skinIndex.itemSize; lane++) {
        if (mesh.skeleton.bones[skinIndex.getComponent(vertex, lane)]?.name === 'Head')
          directWeight += skinWeight.getComponent(vertex, lane);
      }
      if (directWeight >= 0.999) skullBounds.expandByPoint(point);
      if (directWeight >= 0.999) skullPoints.push(point.clone());
    }
  });
  if (bounds.isEmpty() || skullBounds.isEmpty()) throw new Error('Head geometry is empty');
  // The head's crown-to-chin height fills `heightFraction` of the frame and
  // its centre rides the optical axis, so every portrait carries the same face
  // size at the same place. Hairstyles do not enter the fit at all: a camera
  // pulled back by the widest strand is exactly the "some heads are smaller"
  // complaint this preset exists to remove.
  const { fovDegrees, heightFraction, nearClearance } = PORTRAIT_FRAMING;
  const headHeight = Math.max(1e-4, skullBounds.max.y - skullBounds.min.y);
  const center = skullBounds.getCenter(new THREE.Vector3());
  const tanHalfFov = Math.tan(THREE.MathUtils.degToRad(fovDegrees / 2));
  const distance = headHeight / (2 * tanHalfFov * heightFraction);
  const cameraZ = Math.max(center.z + distance, skullBounds.max.z + nearClearance);
  camera.fov = fovDegrees;
  camera.aspect = 1;
  camera.near = 0.01;
  camera.far = 100;
  camera.position.set(center.x, center.y, cameraZ);
  controls.enableDamping = false;
  controls.target.copy(center);
  camera.lookAt(center);
  controls.update();
  controls.enabled = false;
  camera.updateProjectionMatrix();
  // Projected head rectangle in final-image pixels, from the same pinhole the
  // capture uses. This is the rectangle the finalizer protects; anything else
  // on the border is hair, which is allowed to leave the frame.
  const half = FRAME_PIXELS / 2;
  const projected = { left: FRAME_PIXELS, top: FRAME_PIXELS, right: 0, bottom: 0 };
  for (const vertex of skullPoints) {
    const depth = cameraZ - vertex.z;
    const dx = (vertex.x - center.x) / (depth * tanHalfFov) * half;
    const dy = (vertex.y - center.y) / (depth * tanHalfFov) * half;
    projected.left = Math.min(projected.left, half + dx);
    projected.right = Math.max(projected.right, half + dx);
    projected.top = Math.min(projected.top, half - dy);
    projected.bottom = Math.max(projected.bottom, half - dy);
  }
  projected.left = Math.floor(projected.left);
  projected.top = Math.floor(projected.top);
  projected.right = Math.ceil(projected.right);
  projected.bottom = Math.ceil(projected.bottom);
  return { mode: 'head-only', geometryPolicy: 'all triangle vertices attributed to Head descendants',
    framing: 'crown-to-chin normalized to a fixed frame fraction and centred; hair may leave the frame, the head may not',
    geometryBounds: { min: bounds.min.toArray(), max: bounds.max.toArray() },
    bounds: { min: skullBounds.min.toArray(), max: skullBounds.max.toArray() },
    lighting: PORTRAIT_LIGHT,
    normalization: { fovDegrees, heightFraction, headHeight, headCenter: center.toArray(), distance, framePixels: FRAME_PIXELS, projected },
    camera: { fov: camera.fov, position: camera.position.toArray(), target: center.toArray() }, meshes: statistics };
}
