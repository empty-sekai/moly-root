// fx/draw-mesh.js — the Mesh draw mode
//
// A mesh particle is not a quad. It carries authored geometry, scales on three
// axes, and orients in a basis chosen by the renderer's alignment field rather
// than always facing the camera. The geometry itself is resolved synchronously:
// the files a phenomenon references are loaded before it mounts, so `ctx.meshFor`
// is a cache lookup, never a fetch.
//
// Alignment 0 uses the camera's world rotation as its basis. Alignment 1 keeps
// the authored mesh orientation in world axes. Alignment 2 is intentionally
// refused until its emitter-local basis is established.
export const MODES = ['Mesh'];

const ALIGNMENT_VIEW = 0;
const ALIGNMENT_WORLD = 1;
const ALIGNMENT_LOCAL = 2;

/**
 * Why this renderer cannot be drawn, or null when it can. The caller uses this
 * at mount time so an unsupported emitter is skipped once with a reason, rather
 * than failing once per particle for the lifetime of the effect.
 */
export function rejects(renderer, num) {
  const n = (v, d) => (num ? num(v, d) : (Number.isFinite(+v) ? +v : d));
  const meshes = renderer && renderer.meshes;
  if (!Array.isArray(meshes) || !meshes.length) return 'noMeshRef';
  const alignment = n(renderer.alignment, 0);
  if (alignment === ALIGNMENT_VIEW || alignment === ALIGNMENT_WORLD) return null;
  if (alignment === ALIGNMENT_LOCAL) return 'alignmentLocalUnread';
  return 'alignmentUnread';
}

export function makeDrawable(renderer, ctx) {
  const THREE = ctx.THREE;
  const viewWorldQ = new THREE.Quaternion();
  const viewParentQ = new THREE.Quaternion();
  const viewLocalQ = new THREE.Quaternion();
  const viewSpinQ = new THREE.Quaternion();
  const viewBasisWorldQ = new THREE.Quaternion();
  const viewZ = new THREE.Vector3(0, 0, 1);
  if (rejects(renderer, ctx.num)) return null;

  // Unity holds up to four meshes per renderer and picks one per particle. The
  // pick must come from the birth-time random factor: drawn fresh each frame, a
  // particle would swap geometry every frame.
  const entries = renderer.meshes;
  const idx = Math.min(entries.length - 1, Math.floor(ctx.num(ctx.r, 0) * entries.length));
  const chosen = entries[idx];
  const source = ctx.meshFor(chosen.file, chosen.node);
  if (!source) return null;                 // not preloaded, or the node is absent

  const st = ctx.state;
  // Unlit: these particles carry their colour in the material and the vertex
  // stream, and the scene's lights are not part of that.
  const material = ctx.applyZOffset(new THREE.MeshBasicMaterial({
    map: ctx.map || null,
    color: ctx.color,
    opacity: ctx.alpha,
    transparent: true,
    blending: st.blending,
    blendSrc: st.blendSrc,
    blendDst: st.blendDst,
    blendEquation: st.blendEquation,
    premultipliedAlpha: st.premultipliedAlpha,
    depthWrite: st.depthWrite,
    depthTest: st.depthTest,
    side: THREE.DoubleSide,
  }), st.zOffset);

  // One instance per particle. The cached node is the shared original and must
  // never be added to the scene itself - it is the source every particle clones.
  const object = source.clone(true);
  const owned = [];
  object.traverse((o) => {
    if (!o.isMesh) return;
    o.material = material;                  // the per-particle material, shared within this instance
    o.renderOrder = ctx.num(renderer.sortingOrder, 0);
    o.frustumCulled = false;                // particles move; culling by the source bounds drops them
    owned.push(o);
  });
  if (!owned.length) { material.dispose(); return null; }   // the node carried no geometry

  let spin = ctx.num(ctx.rotation, 0);
  const authoredQ = object.quaternion.clone();
  const alignment = ctx.num(renderer.alignment, ALIGNMENT_WORLD);
  const applyView = () => {
    viewSpinQ.setFromAxisAngle(viewZ, spin);
    object.quaternion.copy(viewLocalQ).multiply(authoredQ).multiply(viewSpinQ);
  };
  if (spin && alignment !== ALIGNMENT_VIEW) object.rotation.z = spin;

  return {
    object,
    material,
    // Diagnostic readout of the basis actually applied to this View particle.
    // It is kept in world space so it can be compared with the camera rotation.
    viewBasisWorldQ,
    // Three axes, unlike a billboard. `sz` is absent for callers that still pass
    // two, in which case the depth axis follows x rather than collapsing to zero.
    setScale(sx, sy, sz) { object.scale.set(sx, sy, sz === undefined ? sx : sz); },
    // The particle spin is local to the mesh. For View alignment the basis is
    // applied first, then this quaternion is multiplied on the right.
    setRotation(rad) {
      spin = rad;
      if (alignment === ALIGNMENT_VIEW) applyView();
      else object.rotation.z = rad;
    },
    // View alignment uses the camera's world basis, converted to this object's
    // parent space. The authored mesh orientation and particle spin follow it.
    // World alignment remains a no-op; Local alignment never reaches here.
    orient(camera) {
      if (alignment !== ALIGNMENT_VIEW || !camera) return;
      camera.getWorldQuaternion(viewWorldQ);
      if (object.parent) object.parent.getWorldQuaternion(viewParentQ);
      else viewParentQ.identity();
      viewLocalQ.copy(viewParentQ).invert().multiply(viewWorldQ);
      viewBasisWorldQ.copy(viewParentQ).multiply(viewLocalQ);
      applyView();
    },
    // The screen-size clamp is a billboard rule: it scales a quad by its share of
    // the viewport. A mesh has real extent in the world and is not clamped.
    clampExempt: true,
    dispose() {
      material.dispose();
      // Geometry is cloned per particle by `clone(true)` only when the source
      // node owns it; three.js shares geometry across clones, so disposing it
      // here would destroy the cached original every other particle still needs.
      // The material is ours alone and is the only thing to release.
    },
  };
}
