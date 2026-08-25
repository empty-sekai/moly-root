// fx/sub-emitters.js — one system firing another
//
// A sub-emitter record names a second particle system in the same package and a
// moment to start it: when a particle is born, when it dies, or when it hits
// something. The target does not run on its own — it is played by whatever
// triggers it — so the emitter marks its targets and stops their own emission,
// otherwise a target's burst fires once at mount and again on every trigger.
//
// "Start it" means the target runs its whole emission from its own time zero:
// the rate over time and every burst on its schedule, not just a burst at zero.
// Seven of the targets here emit by rate alone and declare no burst at zero, so
// firing only a zero-time burst would leave them silent. Both paths share one
// emission routine, which is what keeps the triggered law the same as the
// autonomous one.
//
// The particles land at the triggering particle's position: the engine moves the
// target system there and emits normally, so the target's shape offsets still
// apply, rotated by the target's own node. A birth-triggered system keeps
// following that particle and stops when it does; a death-triggered one has no
// owner left and plays once.
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
// Collision-triggered records are counted separately and never fire, because
// collision itself is a no-op here: the data's colliders are all of the kind
// this scene has no geometry for, so no particle ever reports a hit. That is a
// consequence of the collision law, not a gap in this module.

export const NAME = 'subEmitters';

const BIRTH = 'birth';
const DEATH = 'death';
const COLLISION = 'collision';

export function make(system, renderer, ctx) {
  const specs = system.subEmitters;
  if (!Array.isArray(specs) || !specs.length) return null;
  const num = ctx.num;

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

  const fired = { birth: 0, death: 0, plays: 0 };

  function targetOf(record) {
    if (record.target === undefined) {
      record.target = ctx.resolveEmitter(record.path) || null;
      if (!record.target) skipped.unresolved += 1;
    }
    return record.target;
  }

  function fire(type, p) {
    for (const record of records) {
      if (record.type !== type) continue;
      const target = targetOf(record);
      if (!target) continue;
      if (record.probability < 1 && Math.random() > record.probability) continue;
      // A birth-triggered system belongs to the particle that started it: it
      // rides along and stops when that particle does. A death-triggered one has
      // no owner left, so it plays once where the particle died.
      const play = target.playSub(p.pos, type === BIRTH ? p : null);
      if (type === BIRTH) (p.subPlays || (p.subPlays = [])).push({ target, play });
      fired[type] = (fired[type] || 0) + 1;
      fired.plays += 1;
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
        skippedNoTarget: skipped.noTarget,
        skippedInheritUnread: skipped.inheritUnread,
        skippedCollision: skipped.collision,
        skippedUnresolved: skipped.unresolved,
      };
    },
  };
}
