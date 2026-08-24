// fx/trails.js — the per-particle trail module
//
// A trail is independent strip geometry: for each particle that has a TrailModule
// this module appends a point as the particle moves and connects those points
// into a ribbon. It is not a Sprite and it is not drawn by the drawable registry
// — it is its own geometry, owned by the module and released in onDrop.
//
// The four colour / width inputs are independent and must not be conflated:
//   colorOverLifetime  sampled on the **particle's** lifetime (u = age/life)
//   colorOverTrail     sampled on the **normalized position along the trail**
//   widthOverTrail     same trail-position domain
//   inheritParticleColor  multiplies the trail colour by the particle colour
//
// This implements the per-Particle / ratio-1 / stretch branch, which is every
// trail in the current data (009_meteorshower 9, 014_sekai 1). Ribbon mode,
// world-space trails, and non-stretch texture modes do not appear and are
// refused with a count rather than invented.
//
// **Documented approximations.** Two parts of the trail law were not read out of
// the real draw path, and both are recorded rather than hidden:
//
//   * Ribbon side vector. The direction the width is laid out along is not
//     established. This uses camera-facing width (cross of the tangent with the
//     camera direction) — the standard way to keep a ribbon visible from any
//     view. If the original lays width along a fixed axis instead, this is the
//     line to change and nothing else.
//
//   * Space. The data carries worldSpace=false, which means the trail follows
//     the emitter. The emitter here is effectively static (meteor shower shapes
//     do not move), so storing world-space vertices and parenting the mesh to
//     the world root is indistinguishable in practice. The exact moving-emitter
//     behaviour is left to a follow-up, flagged in the lane findings.

import * as THREE from '../three.module.min.js';

export const NAME = 'trails';

// Engine post-simulation order begins: Collision, Trigger, Lights, Trail, ...
// The point for this frame is recorded after the particle has integrated.
export const ORDER_NOTE = 'post-simulation, after Lights and before Size (canon particle-module-order)';

const MAX_POINTS = 512;
const UP = new THREE.Vector3(0, 1, 0);
const Z = new THREE.Vector3(0, 0, 1);

export function make(system, renderer, ctx) {
  const spec = system.trails;
  if (!spec) return null;
  const num = ctx.num;

  const skipped = {};
  if (spec.mode && spec.mode !== 'perParticle') skipped.ribbon = (skipped.ribbon || 0) + 1;
  if (spec.textureMode && spec.textureMode !== 'stretch') skipped.textureMode = (skipped.textureMode || 0) + 1;
  if (spec.worldSpace) skipped.worldSpace = (skipped.worldSpace || 0) + 1;

  // The trail material lives on the renderer record, not inside system.trails,
  // and it is a different blending state from the particle material. Using the
  // particle material for the ribbon blends wrong and blows out.
  const trailMaterial = renderer && renderer.trailMaterial;
  const tex = trailMaterial && trailMaterial.textures && trailMaterial.textures._BaseMap;
  const map = tex ? ctx.textureFor(tex) : null;

  const lifetime = num(spec.lifetime && spec.lifetime.value, 0.5);
  const minVertexDistance = num(spec.minVertexDistance, 0);
  const widthSpec = spec.widthOverTrail, colorSpec = spec.colorOverTrail;
  const lifeColorSpec = spec.colorOverLifetime;
  const inherit = !!spec.inheritParticleColor;

  // Per-frame accumulators. One counter shared by every particle would only ever
  // hold the last particle rebuilt this frame, which reads as a plausible small
  // number and quietly makes any criterion built on it far weaker than it looks.
  // onFrame zeroes them before the particle loop; each rebuild adds to them.
  const frame = { vertices: 0, trails: 0, points: 0 };

  // scratch, module-level to avoid per-particle allocation
  const tangent = new THREE.Vector3();
  const side = new THREE.Vector3();
  const cam = new THREE.Vector3();

  function appendPoint(tr, worldPos) {
    const tgt = tr.points;
    if (tr.lastPos && worldPos.distanceTo(tr.lastPos) < minVertexDistance) return;
    tgt.push({ pos: worldPos.clone(), t: tr.ageNow });
    if (tgt.length > MAX_POINTS) tgt.shift();
    tr.lastPos = worldPos.clone();
    const cutoff = tr.ageNow - lifetime;
    while (tgt.length && tgt[0].t < cutoff) tgt.shift();
    return tgt.length >= 2;
  }

  function rebuild(emitter, tr, p, u) {
    const tgt = tr.points, n = tgt.length;
    if (n < 2) { tr.mesh.visible = false; return; }

    const camDir = emitter.camera ? cam.copy(emitter.camera.position).sub(tr.parentPos).normalize() : cam.copy(Z);
    const lifeC = inherit ? ctx.sampleColor(lifeColorSpec, u, p.r) : null;

    const posAttr = tr.posAttr, colAttr = tr.colAttr;
    const idx = tr.index;

    // The point stored most recently is the head. Traverse newest to oldest so
    // the gradient's t=0 (white, per the data) lies at the head.
    let ii = 0;
    for (let k = 0; k < n; k++) {
      const i = n - 1 - k;
      const pt = tgt[i];
      const tNorm = k / Math.max(1, n - 1);

      tangent.copy(tgt[Math.max(0, i - 1)].pos).sub(tgt[Math.min(n - 1, i + 1)].pos);
      if (tangent.lengthSq() < 1e-10) tangent.copy(UP);
      side.copy(tangent).cross(camDir);
      if (side.lengthSq() < 1e-10) side.copy(UP);
      side.normalize();

      const w = 0.5 * ctx.sampleValue(widthSpec, tNorm, p.r);
      const tc = ctx.sampleColor(colorSpec, tNorm, p.r);
      let cr = tc.color.r, cg = tc.color.g, cb = tc.color.b, ca = tc.alpha;
      if (lifeC) { cr *= lifeC.color.r; cg *= lifeC.color.g; cb *= lifeC.color.b; ca *= lifeC.alpha; }

      const vi = i * 2;                       // two vertices per point
      posAttr.array[vi * 3] = pt.pos.x - side.x * w;
      posAttr.array[vi * 3 + 1] = pt.pos.y - side.y * w;
      posAttr.array[vi * 3 + 2] = pt.pos.z - side.z * w;
      posAttr.array[vi * 3 + 3] = pt.pos.x + side.x * w;
      posAttr.array[vi * 3 + 4] = pt.pos.y + side.y * w;
      posAttr.array[vi * 3 + 5] = pt.pos.z + side.z * w;
      for (let v = 0; v < 2; v++) {
        colAttr.array[(vi + v) * 4] = cr;
        colAttr.array[(vi + v) * 4 + 1] = cg;
        colAttr.array[(vi + v) * 4 + 2] = cb;
        colAttr.array[(vi + v) * 4 + 3] = ca;
      }
      if (k < n - 1) {
        const a = vi, b = vi + 1, c = vi + 2, d = vi + 3;
        idx[ii++] = a; idx[ii++] = b; idx[ii++] = c;
        idx[ii++] = b; idx[ii++] = d; idx[ii++] = c;
      }
    }

    posAttr.needsUpdate = true;
    colAttr.needsUpdate = true;
    tr.geo.drawRange.count = (n - 1) * 6;
    tr.mesh.visible = true;
    frame.vertices += n * 2;
    frame.points += n;
    frame.trails += 1;
  }

  return {
    onSpawn(p) {
      if (skipped.ribbon || skipped.textureMode || skipped.worldSpace) return;
      const geo = new THREE.BufferGeometry();
      const posAttr = new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 2 * 3), 3);
      const colAttr = new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 2 * 4), 4);
      geo.setAttribute('position', posAttr);
      geo.setAttribute('color', colAttr);
      const index = new Uint16Array(MAX_POINTS * 6);
      geo.setIndex(new THREE.BufferAttribute(index, 1));
      geo.drawRange.start = 0;
      geo.drawRange.count = 0;

      const mat = new THREE.MeshBasicMaterial({
        map, transparent: true, vertexColors: true,
        side: THREE.DoubleSide, depthWrite: false, depthTest: true,
        blending: THREE.AdditiveBlending,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.renderOrder = ctx.num(renderer && renderer.sortingOrder, 0);
      mesh.frustumCulled = false;
      mesh.visible = false;
      // `this` is the hook object, not the emitter. `ctx.emitter` is.
      // The ribbon mesh hangs under the same parent as the particle sprites;
      // with an emitter-local trail both live and move together.
      ctx.emitter.parent.add(mesh);

      p.trails = { mesh, geo, mat, posAttr, colAttr, index, points: [], lastPos: null, ageNow: 0, parentPos: new THREE.Vector3() };
    },

    onUpdate(p, u, dt) {
      const tr = p.trails;
      if (!tr) return;
      tr.ageNow = ctx.emitter.age;
      if (ctx.emitter.parent && ctx.emitter.parent.getWorldPosition) ctx.emitter.parent.getWorldPosition(tr.parentPos);
      const ready = appendPoint(tr, p.pos);   // p.pos is already world space
      // Rebuild regardless of whether a new point arrived: points may have aged
      // out this frame even if none were added.
      if (ready || tr.points.length >= 2) rebuild(ctx.emitter, tr, p, u);
    },

    onFrame() {
      frame.vertices = 0;
      frame.trails = 0;
      frame.points = 0;
    },

    report() {
      return {
        activeVertices: frame.vertices,
        activePoints: frame.points,
        activeTrails: frame.trails,
        skippedRibbon: skipped.ribbon || 0,
        skippedTextureMode: skipped.textureMode || 0,
        skippedWorldSpace: skipped.worldSpace || 0,
      };
    },

    onDrop(p) {
      const tr = p.trails;
      if (!tr) return;
      tr.mesh.removeFromParent();
      tr.geo.dispose();
      tr.mat.dispose();
      p.trails = null;
    },
  };
}
