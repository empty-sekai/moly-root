// fx/draw-mesh.js — the Mesh draw mode
//
// A mesh particle is not a quad. It carries authored geometry, scales on three
// axes, and orients in a basis chosen by the renderer's alignment field rather
// than always facing the camera. The geometry itself is resolved synchronously:
// the files a phenomenon references are loaded before it mounts, so `ctx.meshFor`
// is a cache lookup, never a fetch.
//
// The alignment field picks the basis, and all three of its values appear in
// the data:
//
//   0  the camera's world rotation, so the mesh keeps one face to the viewer;
//   1  the world axes themselves, so the mesh ignores whatever rotation its
//      emitter's frame carries;
//   2  the emitter's own frame, so the mesh turns with the node it sits on.
//
// 1 and 2 differ only when the emitter's frame is rotated, which is exactly why
// leaving 1 alone was wrong: with no basis applied a mesh inherits its parent's
// rotation, which is what 2 means, not what 1 means.
//
// Every basis here is a quaternion, so a scaled emitter transform contributes
// none of its scale to the orientation. The node graph applies that scale once,
// the same way it does for every other node, and the basis cannot double it.
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
  if (alignment === ALIGNMENT_VIEW || alignment === ALIGNMENT_WORLD
      || alignment === ALIGNMENT_LOCAL) return null;
  // Facing and velocity alignment do not appear in this data and have no basis
  // read out of the engine, so they are refused rather than approximated.
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

  // Unlit: these particles carry their colour in the material and the vertex
  // stream, and the scene's lights are not part of that. The shading is the
  // emitter's shared one — the same fragment chain the two billboard modes run.
  // A mesh is the one draw mode with authored normals, so it is also the one
  // where the rim terms on that chain see a real surface.
  const shading = ctx.shading;
  if (!shading) return null;
  // 没有可画的基础贴图就**不建绘制件**。取用器永远不返回 null(加载失败留下的是一个
  // 空白占位),而数组选层也可能取空 —— 拿这两种去画得到的都是一块白方片,
  // 看着「有东西」而其实是缺失。调用方拿到 null 就不发这一颗并计数。
  if (!ctx.map) return null;
  const material = shading.material;
  const state = shading.makeState();
  state.color.copy(ctx.color);
  state.opacity = ctx.num(ctx.alpha, 1);
  state.rotation = ctx.num(ctx.rotation, 0);
  state.map = ctx.map || null;

  // One instance per particle. The cached node is the shared original and must
  // never be added to the scene itself - it is the source every particle clones.
  const object = source.clone(true);
  const owned = [];
  object.traverse((o) => {
    if (!o.isMesh) return;
    o.material = material;                  // the emitter's shared material
    o.renderOrder = ctx.num(renderer.sortingOrder, 0);
    o.frustumCulled = false;                // particles move; culling by the source bounds drops them
    // The clone's root is a group, and a group is never drawn — so the push has
    // to hang off each drawn mesh inside it, not off the root.
    shading.bind(o, state);
    owned.push(o);
  });
  if (!owned.length) return null;           // the node carried no geometry

  let spin = ctx.num(ctx.rotation, 0);
  const authoredQ = object.quaternion.clone();
  const alignment = ctx.num(renderer.alignment, ALIGNMENT_WORLD);
  // `viewLocalQ` holds the chosen basis expressed in this object's parent space,
  // which is the space its quaternion lives in. Emitter-local alignment needs no
  // correction there — sitting still in the parent space *is* the emitter's
  // frame — so it stays identity and only the world basis has to undo anything.
  const apply = () => {
    viewSpinQ.setFromAxisAngle(viewZ, spin);
    object.quaternion.copy(viewLocalQ).multiply(authoredQ).multiply(viewSpinQ);
  };
  apply();

  return {
    object,
    material,
    state,
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
      apply();
    },
    // The basis is chosen here and written into the object's parent space; the
    // authored mesh orientation and then the particle spin multiply onto it.
    orient(camera) {
      if (object.parent) object.parent.getWorldQuaternion(viewParentQ);
      else viewParentQ.identity();
      if (alignment === ALIGNMENT_VIEW) {
        if (!camera) return;
        camera.getWorldQuaternion(viewWorldQ);
        viewLocalQ.copy(viewParentQ).invert().multiply(viewWorldQ);
      } else if (alignment === ALIGNMENT_WORLD) {
        // Undo the emitter frame so the basis lands on the world axes.
        viewLocalQ.copy(viewParentQ).invert();
      } else {
        // Emitter-local: the parent space already is the frame.
        viewLocalQ.identity();
      }
      viewBasisWorldQ.copy(viewParentQ).multiply(viewLocalQ);
      apply();
    },
    // The screen-size clamp is a billboard rule: it scales a quad by its share of
    // the viewport. A mesh has real extent in the world and is not clamped.
    clampExempt: true,
    dispose() {
      // Nothing here is this particle's to release. three.js shares geometry
      // across clones, so disposing it would destroy the cached original every
      // other particle still needs; the material belongs to the emitter and
      // outlives every particle drawn with it.
    },
  };
}
