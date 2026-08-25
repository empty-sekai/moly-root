// fx/drawable.js — what one particle is actually drawn with
//
// The renderer picks a draw mode per emitter. Camera-facing billboards are the
// common case and are built here; every other mode is a provider file in this
// directory that registers itself. A mode with no provider is **not drawn** and
// is counted, because drawing it with the billboard path would put a flat
// square where a ground-hugging sheet or a mesh belongs — visible, wrong, and
// easy to mistake for "something is there".
//
// Contract, implemented by every provider:
//
//   export const MODES = ['Mesh'];        // which renderMode values it owns
//   export function makeDrawable(renderer, ctx) -> null | Drawable
//
// Drawable:
//   object        THREE.Object3D added to the parent
//   material      the emitter's shared shader material — NOT one per particle
//   state         this particle's own values (colour, opacity, rotation, map,
//                 sheet frame, the two per-particle vectors); pushed into the
//                 shared material just before this object is drawn
//   setScale(sx, sy)
//   setRotation(rad)          // billboard: about the view axis; mesh: own axis
//   orient(camera, vel)       // per-frame orientation law; no-op when unneeded
//   clampExempt   boolean     // whether the screen-size clamp skips this mode
//   dispose()
//
// One material per emitter, not per particle. Everything on the fragment chain
// except colour, alpha, spin, sheet frame and the two per-particle vectors is a
// material constant, so a material per particle bought nothing and cost a
// material object for every grain of rain. `ctx.shading` owns that one shared
// material; the drawable takes a per-particle state record from it and binds
// that record to the object it builds.
//
// `ctx` carries { THREE, num, textureFor, camera, emitter, material, state,
// shading, applyZOffset, map, color, alpha, rotation, sortingOrder }, where
// `material` is the decoded material record and `state` the blend/depth state
// already resolved from the shader family.

const PROVIDERS = [];

export function registerDrawable(mod) {
  if (mod && Array.isArray(mod.MODES) && typeof mod.makeDrawable === 'function') {
    PROVIDERS.push(mod);
  }
}

/** Draw modes that can be drawn at all: the built-in billboard plus providers. */
export function drawableModes() {
  const out = new Set(['Billboard']);
  for (const p of PROVIDERS) for (const m of p.MODES) out.add(m);
  return out;
}

/**
 * Why this particular renderer cannot be drawn, or null when it can.
 *
 * Owning a draw mode is not the same as being able to draw every renderer that
 * uses it: a provider may know one alignment and not another, or the reference
 * it needs may be absent from the data. Asking once at mount, per renderer,
 * keeps that distinction — the emitter is skipped a single time with a reason,
 * instead of every particle failing for the life of the effect and burying the
 * cause in a fault counter.
 */
export function drawableRejection(renderer, num) {
  const mode = (renderer && renderer.renderMode) || 'Billboard';
  if (mode === 'Billboard') return null;
  for (const p of PROVIDERS) {
    if (!p.MODES.includes(mode)) continue;
    return p.rejects ? (p.rejects(renderer, num) || null) : null;
  }
  return 'noProvider';
}

/**
 * A camera-facing billboard: one screen-aligned quad, scaled on two axes,
 * spun about the view axis. This is the baseline mode and needs no provider.
 */
function billboard(renderer, ctx) {
  const THREE = ctx.THREE;
  const shading = ctx.shading;
  if (!shading) return null;
  const material = shading.material;
  // A sprite is still the right object for this mode — it is the one three.js
  // shape whose quad is assembled in view space — but the shader on it is the
  // emitter's, not the built-in sprite one: the built-in draws "one texture
  // times one colour", and that is not what this shader family does.
  const sprite = new THREE.Sprite(material);
  sprite.renderOrder = ctx.num(renderer && renderer.sortingOrder, 0);
  const state = shading.makeState();
  state.color.copy(ctx.color);
  state.opacity = ctx.num(ctx.alpha, 1);
  state.rotation = ctx.num(ctx.rotation, 0);
  state.map = ctx.map || null;
  shading.bind(sprite, state);
  return {
    object: sprite,
    material,
    state,
    setScale(sx, sy) { sprite.scale.set(sx, sy, 1); },
    setRotation(rad) { state.rotation = rad; },
    orient() {},                    // a sprite already faces the camera
    clampExempt: false,
    // The material belongs to the emitter and outlives this particle; the
    // sprite geometry is three.js's own shared quad. Neither is ours to release.
    dispose() {},
  };
}

/**
 * Build the drawable for one particle. Returns null when this emitter's draw
 * mode has no provider — the caller must then skip the emitter and count it.
 */
export function makeDrawable(renderer, ctx) {
  const mode = (renderer && renderer.renderMode) || 'Billboard';
  for (const p of PROVIDERS) {
    if (!p.MODES.includes(mode)) continue;
    const d = p.makeDrawable(renderer, ctx);
    if (d) return d;
  }
  if (mode === 'Billboard') return billboard(renderer, ctx);
  return null;
}
