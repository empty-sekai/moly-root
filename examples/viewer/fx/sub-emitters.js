// fx/sub-emitters.js — one system firing another
//
// A sub-emitter record names a second particle system in the same package and a
// moment to start it: when a particle is born, when it dies, or when it hits
// something. The target does not run on its own — it is played by whatever
// triggers it — so the emitter marks its targets and stops their own emission,
// otherwise a target's burst fires once at mount and again on every trigger.
//
// Birth and the rest are two separate paths, not one path with two owners.
//
// Birth runs the target: the rate over time and every burst on its schedule, on
// the parent particle's own clock. It is gated every frame, and all three gates
// must hold -- the parent is still alive, the parent's normalized age less the
// target's own start delay lies in [0,1), and the elapsed time is below a limit
// that is the target's duration when it does not loop and unbounded when it
// does. That last gate is what stops a short target from emitting for as long as
// a long-lived parent survives. The clock never wraps here: it comes from a
// particle's monotonic age, so the swept window can never run backwards.
//
// Death -- and collision, and trigger -- is not a run. It takes the target's
// first burst, emits that many particles where the particle died, and is over.
// It reads no rate, no burst time, no cycle count and no repeat interval, so a
// burst authored at a later time still fires at once; and it never asks whether
// the target loops. A target that declares no burst emits nothing this way.
//
// The particles land at the triggering particle's position: the engine moves the
// target system there and emits normally, so the target's shape offsets still
// apply, rotated by the target's own node.
//
// **Inheritance is refused, not approximated.** A record can ask the spawned
// particles to inherit the parent's colour, size, rotation, lifetime or
// duration. The composition rule for that is not established from the real
// source, and a guessed one would be invisible: the particles would appear, at
// the wrong size or colour, and nothing would say so. Records asking for any
// inheritance are skipped and counted instead. Every record in the current data
// that names a real target asks for none of it, so the refusal costs nothing
// here — but it is what stops a future record from being drawn wrong in silence.
//
// One part of this is still not established: which fields of that first burst
// the death path reads. The count is certain -- it has to emit something -- and
// the time, cycle count and repeat interval are ruled out. Whether the burst's
// own probability is among them is not settled, and it is applied here; the
// difference shows only on records whose probability is below one.
//
// "Following" means the emission point follows: the target is moved to the
// parent particle's current position every frame. The spawned particles are not
// attached to that parent and hold no reference back to it, so they carry on
// under their own motion once emitted.
//
// The random stream is a known deviation, stated rather than hidden: the engine
// derives a child seed from the parent particle's own seed, so a sub-system's
// randomness is tied to the particle that started it without repeating it. This
// draws from the shared generator instead. Nothing here depends on the sequence,
// but a criterion that expects the same particle to produce the same splash twice
// would be measuring the wrong thing.
//
// Collision-triggered records are counted separately and never fire, because
// collision itself is a no-op here: the data's colliders are all of the kind
// this scene has no geometry for, so no particle ever reports a hit. That is a
// consequence of the collision law, not a gap in this module.

// **The origin handed to the target is the parent particle's world position.**
// A particle's own `pos` lives in its system's simulation space: local-space
// systems keep it node-relative, so it must be run through the parent node's
// world matrix once, here, before it is given to the target. Doing it later
// would be too late -- the target interprets the origin as a world point
// (see `spawn(origin)` in the emitter), and a World-space target renders it as
// is, so a node-relative value lands it tens of meters short of the parent.
// World-space parents pass their `pos` unchanged -- it is world already.

export const NAME = 'subEmitters';

const BIRTH = 'birth';
const DEATH = 'death';
const COLLISION = 'collision';

export function make(system, renderer, ctx) {
  const specs = system.subEmitters;
  if (!Array.isArray(specs) || !specs.length) return null;
  const num = ctx.num;
  const _subP = new ctx.THREE.Vector3();

  const records = [];
  const skipped = { noTarget: 0, inheritUnread: 0, collision: 0, unresolved: 0 };

  for (const spec of specs) {
    if (!spec || !spec.emitter) { skipped.noTarget += 1; continue; }
    const inherit = spec.inherit || {};
    if (Object.keys(inherit).some((key) => inherit[key])) {
      skipped.inheritUnread += 1;
      continue;
    }
    if (spec.type === COLLISION) { skipped.collision += 1; continue; }
    records.push({
      path: spec.emitter,
      type: spec.type || BIRTH,
      probability: num(spec.emitProbability, 1),
      target: undefined,          // resolved lazily: the target may be built later
    });
  }
  if (!records.length && !skipped.inheritUnread && !skipped.collision) return null;

  const fired = { birth: 0, death: 0, plays: 0, emitted: 0 };

  function targetOf(record) {
    if (record.target === undefined) {
      record.target = ctx.resolveEmitter(record.path) || null;
      if (!record.target) skipped.unresolved += 1;
    }
    return record.target;
  }

  // The parent particle's position, in world space. `p.pos` is world only when
  // the parent system is World-space; a Local-space parent keeps it
  // node-relative, so it must pass through the parent node's world matrix
  // first -- the target interprets the origin as a world point (see
  // `spawn(origin)` in the emitter), and a World-space target renders it as
  // is, so a node-relative value lands it a node-length short of the parent.
  // A birth play keeps following the particle afterwards (see the follow
  // refresh in the emitter's update); the death path fires at the spot where
  // the particle died and stops there.
  function parentWorld(p) {
    const em = ctx.emitter;
    if (!em || em.worldSpace || !em.node) return p.pos;
    em.node.updateWorldMatrix(true, false);
    return _subP.copy(p.pos).applyMatrix4(em.node.matrixWorld);
  }

  function fire(type, p) {
    for (const record of records) {
      if (record.type !== type) continue;
      const target = targetOf(record);
      if (!target) continue;
      if (record.probability < 1 && Math.random() > record.probability) continue;
      // The two trigger kinds are two different things, not one thing with a
      // different owner. Birth starts the target running: rate and every burst,
      // on the parent particle's clock, for as long as the gates hold. Death is
      // not a run at all -- it takes the target's first burst, emits that many
      // where the particle died, and is over. It reads no rate, no burst time,
      // no cycle count, no repeat interval, and never asks whether the target
      // loops; a target with no burst at all emits nothing when a particle dies.
      if (type === BIRTH) {
        const play = target.playSub(parentWorld(p), p, ctx.emitter);
        (p.subPlays || (p.subPlays = [])).push({ target, play });
        fired.plays += 1;
      } else {
        fired.emitted += target.emitBurstZero(parentWorld(p));
      }
      fired[type] = (fired[type] || 0) + 1;
    }
  }

  function stopAll(p) {
    if (!p.subPlays) return;
    for (const held of p.subPlays) held.target.stopSub(held.play);
    p.subPlays = null;
  }

  return {
    onSpawn(p) { fire(BIRTH, p); },
    // Death fires before the particle is recycled, which is the only moment its
    // position still says where it died.
    onDeath(p) { fire(DEATH, p); },
    // Every recycle path, so a whole-emitter reset cannot leave a play running
    // that emits at a position no particle occupies any more.
    onDrop(p) { stopAll(p); },
    report() {
      return {
        records: records.length,
        firedBirth: fired.birth,
        firedDeath: fired.death,
        startedPlays: fired.plays,
        deathEmitted: fired.emitted,
        skippedNoTarget: skipped.noTarget,
        skippedInheritUnread: skipped.inheritUnread,
        skippedCollision: skipped.collision,
        skippedUnresolved: skipped.unresolved,
      };
    },
  };
}
