// fx/hooks.js — per-particle module registry
//
// A particle system declares a dozen optional modules. Each one is a separate
// file under this directory so that a module can be added, replaced, or removed
// without touching the emitter itself. A module that a given emitter does not
// declare costs nothing: its factory returns null and it never enters the list.
//
// Contract, implemented by every file registered below:
//
//   export const NAME = '<module>';
//   export function make(system, renderer, ctx) -> null | {
//     onSpawn?(p),                    // at birth; own state goes in p.<namespace> = {…}
//     onPreIntegrate?(p, u, dt, vel), // before the position integrates
//     onUpdate?(p, u, dt),            // after it integrates
//     onDeath?(p),                    // the frame the particle expires, before recycling
//     onDrop?(p),                     // every recycle path, including a whole-emitter reset
//     onFrame?(dt),                   // once per frame, emitter level
//     report?(),                      // plain numbers and strings, for the panel
//   }
//
// `ctx` carries { THREE, sampleValue, sampleColor, num, vec3, emitter, camera,
// textureFor, resolveEmitter }. The particle object is
// { sprite, map, ownsMap, r, life, age, sizeX, sizeY, pos, vel, spin, gravity };
// `p.r` is the random factor drawn at birth, which every curve lookup for that
// particle must reuse so one particle reads one curve.
//
// **Which of the two per-particle slots to use.** The engine evaluates its
// modules in two blocks, one before the position integrates and one after, and
// a module in the wrong block is not an error — it is off by one frame, or it
// silently defeats a later clamp. Velocity-shaping modules (noise, force,
// velocity clamping, custom data) belong in `onPreIntegrate`, which receives
// `vel`, the mutable effective velocity this frame is about to move by: change
// it in place, or change `p.vel` to affect the stored velocity. Modules that
// react to where the particle ended up (collision, trails, sub-emitters) belong
// in `onUpdate`.
//
// `onDeath` fires on the frame a particle expires, **before** it is recycled,
// because after recycling its position and velocity are gone and death-triggered
// emission has to happen at the point where the particle died.
//
// `onDrop` is the release hook, and it is not the same thing. It runs on **every**
// recycle path, including a whole-emitter reset, so anything a module allocated
// itself — geometry a module built, a texture it cloned — must be freed here. The
// parent only knows how to dispose the drawable; a module's own objects are
// invisible to it. Freeing in `onDeath` instead leaks on reset.
//
// `report()` is read back through the emitter view's `stats().moduleReports`,
// which sums numeric fields across every emitter that declared the module. That
// is what a criterion reads: a module with no `report()` cannot be shown to work.
// Counters that describe the whole frame must be **accumulated per frame** and
// zeroed in `onFrame` — a single counter written by each particle in turn ends up
// holding only the last one, which reads as a plausible small number and makes
// any criterion built on it quietly weaker than it looks.
//
// **`this` inside a hook is the hook object, not the emitter.** Hooks are invoked
// as `h.onSpawn?.(p)`, so reach the emitter through `ctx.emitter` — and note that
// a throw inside a hook does not reach `status().errors`, so getting this wrong
// shows up only as particles quietly failing to spawn.
//
// `ctx.resolveEmitter(path)` maps a node path to another emitter in the same
// package, for modules whose serialized target is a different particle system.
// It resolves lazily and returns null when the path is not found — a module that
// cannot resolve its target must stop that branch and count it, never fall back
// to emitting on itself.
//
// **Known gap, deliberately not papered over:** there is no API yet for a
// sub-emitter to hand inherited parent state (color, size, rotation, lifetime,
// duration) to the particles it spawns. The composition rule for that
// inheritance is not established from the real source, and inventing a shape for
// it would bake in a guess. Whoever establishes the rule adds the API with it.
//
// **Order matters.** The modules run in the order of the list below, and that
// order is the engine's own evaluation order: what runs first writes velocity,
// what runs later reads the value already changed.

const MODULES = [];

/**
 * Register a module. `order` is its slot in the evaluation order; lower runs
 * first. Slots are spaced so a module can be inserted between two others
 * without renumbering.
 */
export function registerFxModule(mod, order) {
  MODULES.push({ mod, order: Number.isFinite(+order) ? +order : 500 });
  MODULES.sort((a, b) => a.order - b.order);
}

/** Instantiate every module this emitter declares, in evaluation order. */
export function makeHooks(system, renderer, ctx) {
  const out = [];
  for (const entry of MODULES) {
    let h = null;
    try {
      h = entry.mod.make ? entry.mod.make(system, renderer, ctx) : null;
    } catch (e) {
      // A module that throws at construction is skipped and counted, never
      // allowed to take the emitter down with it.
      ctx.onModuleFault?.(entry.mod.NAME || '?', String(e).slice(0, 120));
      h = null;
    }
    if (!h) continue;
    h.__name = entry.mod.NAME || '?';
    out.push(h);
  }
  return out;
}

/** Which modules are registered at all (for the panel: declared vs consumed). */
export function registeredModules() {
  return MODULES.map((e) => e.mod.NAME || '?');
}
