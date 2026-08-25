// fx/draw-horizontal.js — the HorizontalBillboard draw mode
//
// A horizontal billboard is a quad that lies flat in the world: its normal is
// world +Y and stays there wherever the camera goes. That is the whole of the
// mode, and it is why it cannot borrow the camera-facing path — a sheet meant
// to hug the ground would come up as a wall standing in front of the viewer,
// which reads as "something is there" while being in the wrong plane entirely.
//
// Settled about the orientation, and relied on here:
//
//   * the quad normal is world +Y, always;
//   * the camera's world rotation takes no part. The camera-facing billboard
//     and the vertical billboard both read it; this mode never does;
//   * the renderer's alignment field takes no part either. The mode does not
//     offer that choice, and every renderer in this data leaves the field on
//     its default;
//   * the emitter node's own rotation takes no part. The quad lies flat in the
//     world, not in whatever frame it happens to be parented under, so that
//     frame is undone here the same way a world-aligned mesh undoes it.
//
// NOT settled, and not guessed:
//
//   * WHICH world axes the two in-plane quad axes are, and with which signs.
//     The normal pins the plane; it does not label the directions inside it.
//     (+X, -Z) and (+Z, +X) both describe a flat quad whose normal is +Y.
//     `BASIS_AXIS` / `BASIS_ANGLE` below pick one so the mode can be drawn at
//     all, and that pick is a placeholder, not a reading.
//     What it can change: how the texture lands on the ground — a yaw by some
//     multiple of a quarter turn, possibly mirrored. What it cannot change:
//     that the quad is flat, nor its position, size, colour or sort order.
//     When the real pairing is read, those two constants are the only lines
//     that change; nothing else in this file depends on the choice.
//   * the positive direction of the particle spin. That is the same gap seen
//     from the other side: the spin turns about the normal, and which way
//     counts as positive is decided by that same in-plane labelling.
//
// Size, texture, colour, blend state and sort order are the billboard rules
// unchanged. This file differs from the camera-facing billboard in the
// orientation and in nothing else.
export const MODES = ['HorizontalBillboard'];

// Local +Z is the quad's normal; a quarter turn about local +X carries it onto
// world +Y. The two in-plane axes then land on world +X and world -Z.
//
// *** PLACEHOLDER — this pair-and-sign choice is the unread part described
// above. Change these two constants, and only these two, when it is read. ***
const BASIS_AXIS = [1, 0, 0];
const BASIS_ANGLE = -Math.PI / 2;

// One unit quad, shared by every particle of every horizontal emitter. It holds
// no per-particle state — size is a scale on the object, colour and texture
// live on the material — so a copy per particle would buy nothing and cost
// memory. Being shared, it must never be disposed along with a particle.
let unitQuad = null;
function quadGeometry(THREE) {
  if (!unitQuad) unitQuad = new THREE.PlaneGeometry(1, 1);
  return unitQuad;
}

/**
 * Why this renderer cannot be drawn, or null when it can. Asked once per
 * renderer at mount time, so an unsupported emitter is skipped a single time
 * with a reason instead of failing once per particle for the life of the effect.
 *
 * The gate here is the pivot. A pivot offsets the quad within its own frame,
 * and two of its three components are expressed in exactly the in-plane axes
 * this file has not read: with the labelling unknown, a sheet asked to sit half
 * a width off-centre cannot be told which way off-centre that is. The third
 * component runs along the normal, which is read — but the quad path does not
 * carry a pivot at all, so honouring one component while dropping the other two
 * would put the sheet somewhere the data did not ask for and say nothing about
 * it. A declared pivot is therefore refused whole.
 *
 * Every renderer in this data leaves the pivot at zero, so nothing is refused
 * today; the gate exists so that a record which would expose the unread
 * labelling is stopped rather than drawn on a guess.
 */
export function rejects(renderer, num) {
  const n = (v, d) => (num ? num(v, d) : (Number.isFinite(+v) ? +v : d));
  const pivot = renderer && renderer.pivot;
  if (Array.isArray(pivot)) {
    for (let i = 0; i < 3; i++) if (n(pivot[i], 0) !== 0) return 'pivotUnplaced';
  }
  return null;
}

export function makeDrawable(renderer, ctx) {
  const THREE = ctx.THREE;
  if (rejects(renderer, ctx.num)) return null;

  const st = ctx.state;
  // The camera-facing billboard is a sprite, and a sprite is oriented by the
  // renderer rather than by its own transform — there is no world orientation
  // to give it. A quad that must hold one is a plane mesh instead, with the
  // unlit material; every other field below is the billboard's, unchanged.
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
    // A ground sheet is seen from above, and from below wherever the viewer
    // gets under it. Culling one of the two faces makes it disappear there.
    side: THREE.DoubleSide,
  }), st.zOffset);

  const object = new THREE.Mesh(quadGeometry(THREE), material);
  object.renderOrder = ctx.num(renderer && renderer.sortingOrder, 0);
  object.frustumCulled = false;      // particles move; the unit quad's own bounds do not

  const basisQ = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(BASIS_AXIS[0], BASIS_AXIS[1], BASIS_AXIS[2]), BASIS_ANGLE);
  const parentQ = new THREE.Quaternion();
  const spinQ = new THREE.Quaternion();
  const appliedWorldQ = new THREE.Quaternion();
  const normalAxis = new THREE.Vector3(0, 0, 1);   // the local normal, and the spin axis
  let spin = ctx.num(ctx.rotation, 0);

  // The object's quaternion lives in its parent's space, so the parent's world
  // rotation has to be undone before the world basis is written in. Skipping
  // that would let the quad inherit whatever the emitter node is turned to —
  // and for a camera-mounted effect, whatever the camera is turned to — which
  // is precisely the thing this mode does not do. The spin then multiplies on
  // the right, about the local normal, which the basis has just put on world +Y.
  const apply = () => {
    if (object.parent) object.parent.getWorldQuaternion(parentQ);
    else parentQ.identity();
    spinQ.setFromAxisAngle(normalAxis, spin);
    appliedWorldQ.copy(basisQ).multiply(spinQ);
    object.quaternion.copy(parentQ).invert().multiply(appliedWorldQ);
  };
  apply();

  return {
    object,
    material,
    // Diagnostic readout of the orientation actually applied, in world space:
    // the fixed basis with this particle's spin on it. Kept in world space so a
    // probe can compare it against the camera and against world +Y.
    quadBasisWorldQ: appliedWorldQ,
    // A quad has two axes. The third argument exists for the mesh mode and is
    // ignored here rather than collapsed into a depth this shape does not have.
    setScale(sx, sy) { object.scale.set(sx, sy, 1); },
    // The spin turns about the quad's normal, so unlike the camera-facing
    // billboard — where it is a rotation of the material in screen space — it
    // is part of the orientation and goes through the same assembly.
    setRotation(rad) { spin = rad; apply(); },
    // No argument is read: the camera does not enter this basis. The parent
    // frame can still move between frames, so the basis is rebuilt each time
    // rather than written once at birth.
    orient() { apply(); },
    // The screen-size clamp is a billboard rule and this is a billboard: its
    // extent is a share of the viewport exactly as on the camera-facing path,
    // so it is not exempt.
    clampExempt: false,
    dispose() {
      material.dispose();
      // The geometry is the shared unit quad. Disposing it here would take it
      // away from every other particle still drawing with it.
    },
  };
}
