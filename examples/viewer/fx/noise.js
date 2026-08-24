// fx/noise.js - analytic curl noise for particle velocity

export const NAME = 'noise';

// The pre-simulation block places Noise sixth, after velocity-over-lifetime and
// before inherit-velocity, force, and velocity clamping. Post-simulation slots
// use a separate range; Trail is registered at 420.
export const ORDER = 200;
export const ORDER_NOTE = 'pre-simulation slot 6: after Velocity and before InheritVelocity, Force, and ClampVelocity';

// Ken Perlin's doubled table is equivalent to this 256-entry table when every
// lookup masks its index. Keeping the source table at 256 entries makes its
// permutation property directly testable.
export const PERM = Object.freeze([
  151, 160, 137, 91, 90, 15, 131, 13, 201, 95, 96, 53, 194, 233, 7, 225,
  140, 36, 103, 30, 69, 142, 8, 99, 37, 240, 21, 10, 23, 190, 6, 148,
  247, 120, 234, 75, 0, 26, 197, 62, 94, 252, 219, 203, 117, 35, 11, 32,
  57, 177, 33, 88, 237, 149, 56, 87, 174, 20, 125, 136, 171, 168, 68, 175,
  74, 165, 71, 134, 139, 48, 27, 166, 77, 146, 158, 231, 83, 111, 229, 122,
  60, 211, 133, 230, 220, 105, 92, 41, 55, 46, 245, 40, 244, 102, 143, 54,
  65, 25, 63, 161, 1, 216, 80, 73, 209, 76, 132, 187, 208, 89, 18, 169,
  200, 196, 135, 130, 116, 188, 159, 86, 164, 100, 109, 198, 173, 186, 3, 64,
  52, 217, 226, 250, 124, 123, 5, 202, 38, 147, 118, 126, 255, 82, 85, 212,
  207, 206, 59, 227, 47, 16, 58, 17, 182, 189, 28, 42, 223, 183, 170, 213,
  119, 248, 152, 2, 44, 154, 163, 70, 221, 153, 101, 155, 167, 43, 172, 9,
  129, 22, 39, 253, 19, 98, 108, 110, 79, 113, 224, 232, 178, 185, 112, 104,
  218, 246, 97, 228, 251, 34, 242, 193, 238, 210, 144, 12, 191, 179, 162, 241,
  81, 51, 145, 235, 249, 14, 239, 107, 49, 192, 214, 31, 181, 199, 106, 157,
  184, 84, 204, 176, 115, 121, 50, 45, 127, 4, 150, 254, 138, 236, 205, 93,
  222, 114, 67, 29, 24, 72, 243, 141, 128, 195, 78, 66, 215, 61, 156, 180,
]);

// Lower-case aliases make the primitive table part of the small, useful test
// surface without duplicating mutable data.
export const perm = PERM;

export const G3 = Object.freeze([
  Object.freeze([1, 1, 0]), Object.freeze([-1, 1, 0]),
  Object.freeze([1, -1, 0]), Object.freeze([-1, -1, 0]),
  Object.freeze([1, 0, 1]), Object.freeze([-1, 0, 1]),
  Object.freeze([1, 0, -1]), Object.freeze([-1, 0, -1]),
  Object.freeze([0, 1, 1]), Object.freeze([0, -1, 1]),
  Object.freeze([0, 1, -1]), Object.freeze([0, -1, -1]),
  Object.freeze([1, 1, 0]), Object.freeze([0, -1, 1]),
  Object.freeze([-1, 1, 0]), Object.freeze([0, -1, -1]),
]);
export const gradients3D = G3;

export function fade(t) {
  return t * t * t * (t * (t * 6 - 15) + 10);
}

export function dfade(t) {
  return 30 * t * t * (t - 1) * (t - 1);
}

function table(i) {
  return PERM[i & 255];
}

function trilerp(a, u, v, w) {
  const uv = u * v, uw = u * w, vw = v * w, uvw = uv * w;
  return a[0] + (a[1] - a[0]) * u + (a[2] - a[0]) * v + (a[4] - a[0]) * w
    + (a[3] - a[2] - a[1] + a[0]) * uv
    + (a[5] - a[4] - a[1] + a[0]) * uw
    + (a[6] - a[4] - a[2] + a[0]) * vw
    + (a[7] - a[6] - a[5] + a[3] - a[4] + a[2] + a[1] - a[0]) * uvw;
}

function dTrilerpDu(a, v, w) {
  return (a[1] - a[0])
    + (a[3] - a[2] - a[1] + a[0]) * v
    + (a[5] - a[4] - a[1] + a[0]) * w
    + (a[7] - a[6] - a[5] + a[3] - a[4] + a[2] + a[1] - a[0]) * v * w;
}

function dTrilerpDv(a, u, w) {
  return (a[2] - a[0])
    + (a[3] - a[2] - a[1] + a[0]) * u
    + (a[6] - a[4] - a[2] + a[0]) * w
    + (a[7] - a[6] - a[5] + a[3] - a[4] + a[2] + a[1] - a[0]) * u * w;
}

function lattice(x, y, z) {
  const ix = Math.floor(x) & 255;
  const iy = Math.floor(y) & 255;
  const iz = Math.floor(z) & 255;
  const A = table(ix), B = table(ix + 1);
  const AA = table(A + iy), AB = table(A + iy + 1);
  const BA = table(B + iy), BB = table(B + iy + 1);
  return [
    table(AA + iz) & 15, table(BA + iz) & 15,
    table(AB + iz) & 15, table(BB + iz) & 15,
    table(AA + iz + 1) & 15, table(BA + iz + 1) & 15,
    table(AB + iz + 1) & 15, table(BB + iz + 1) & 15,
  ];
}

function cornerData(x, y, z, scale) {
  const xs = x * scale, ys = y * scale, zs = z * scale;
  const X0 = Math.floor(xs), Y0 = Math.floor(ys), Z0 = Math.floor(zs);
  const fx = xs - X0, fy = ys - Y0, fz = zs - Z0;
  const h = lattice(xs, ys, zs);
  const n = new Array(8), gx = new Array(8), gy = new Array(8);
  const dx = [fx, fx - 1, fx, fx - 1, fx, fx - 1, fx, fx - 1];
  const dy = [fy, fy, fy - 1, fy - 1, fy, fy, fy - 1, fy - 1];
  const dz = [fz, fz, fz, fz, fz - 1, fz - 1, fz - 1, fz - 1];
  for (let c = 0; c < 8; c++) {
    const g = G3[h[c]];
    n[c] = g[0] * dx[c] + g[1] * dy[c] + g[2] * dz[c];
    gx[c] = g[0];
    gy[c] = g[1];
  }
  return { n, gx, gy, fx, fy, fz };
}

/** Return the scalar improved-noise field value at a point. */
export function perlin3DValue(x, y, z, scale = 1) {
  const d = cornerData(x, y, z, scale);
  return trilerp(d.n, fade(d.fx), fade(d.fy), fade(d.fz));
}
export const noiseValue = perlin3DValue;

/**
 * Return [dn/dx, dn/dy]. The native 3D primitive intentionally has no z
 * derivative; the curl construction arranges its coordinates around that fact.
 */
export function perlin3D(x, y, z, scale = 1) {
  const d = cornerData(x, y, z, scale);
  const u = fade(d.fx), v = fade(d.fy), w = fade(d.fz);
  return [
    (dfade(d.fx) * dTrilerpDu(d.n, v, w) + trilerp(d.gx, u, v, w)) * scale,
    (dfade(d.fy) * dTrilerpDv(d.n, u, w) + trilerp(d.gy, u, v, w)) * scale,
  ];
}

function number(ctx, value, fallback = 0) {
  if (ctx && ctx.num) return ctx.num(value, fallback);
  return Number.isFinite(+value) ? +value : fallback;
}

function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}

function sample(ctx, spec, t, r) {
  if (ctx && ctx.sampleValue) return ctx.sampleValue(spec, t, r);
  if (!spec) return 0;
  switch (spec.mode) {
    case 'constant': return number(ctx, spec.value);
    case 'twoConstants': return number(ctx, spec.min) + (number(ctx, spec.max) - number(ctx, spec.min)) * r;
    default: return 0;
  }
}

function nextSeed(state) {
  return (Math.imul(state, 0x6c078965) + 1) >>> 0;
}

function seededFloat(state) {
  let x = state >>> 0;
  x ^= x >>> 11;
  x ^= x << 8;
  x ^= x >>> 19;
  return (x & 0x007fffff) * 1.1920928955078125e-7;
}

function fieldOffset(seed) {
  let state = seed >>> 0;
  const out = [];
  for (let i = 0; i < 3; i++) {
    state = nextSeed(state);
    out.push(seededFloat(state));
  }
  return out;
}

function curveTime(p, u) {
  return Math.max(0, Number.isFinite(+u) ? +u : (p.age / Math.max(p.life, 1e-6)));
}

function rotationGate(spec) {
  if (!spec) return 0;
  if (spec.mode === 'constant') return number(null, spec.value);
  if (spec.mode === 'twoConstants') return number(null, spec.min) || number(null, spec.max);
  return 1;
}

function remapValue(ctx, value, curve, frequency) {
  const t = clamp01(value * (0.5 / frequency) * 0.5 + 0.5);
  return sample(ctx, curve, t, 1) * (2 * frequency);
}

function cloneFrame() {
  return {
    particles: 0,
    perlinCalls: 0,
    nonZeroField: 0,
    changedParticles: 0,
    velocityDelta: 0,
    fieldMagnitude: 0,
  };
}

export function make(system, renderer, ctx) {
  const spec = system && system.noise;
  if (!spec) return null;

  const frequency = Math.max(number(ctx, spec.frequency, 1), 1e-6);
  const dampingScale = spec.damping ? 1 / frequency : 1;
  const octaveCount = Math.max(1, Math.floor(number(ctx, spec.octaves ?? spec.octaveCount, 1)));
  const octaveMultiplier = number(ctx, spec.octaveMultiplier, 0.5);
  const octaveScale = number(ctx, spec.octaveScale, 2);
  const separateAxes = !!spec.separateAxes;
  const unsupportedDimensions = number(ctx, spec.dimensions, 3) !== 3;
  const offset = fieldOffset(number(ctx, system.randomSeed, 0) >>> 0);
  const duration = Math.max(number(ctx, system.duration, 1), 1e-6);
  let scrollTotal = 0;
  let frame = cloneFrame();

  function octaveField(x, y, z) {
    let freq = frequency;
    let amp = 1;
    let sumAmp = 1;
    let dx = 0, dy = 0;
    for (let octave = 0; octave < octaveCount; octave++) {
      const d = perlin3D(x, y, z, freq);
      frame.perlinCalls += 1;
      dx += d[0] * amp;
      dy += d[1] * amp;
      if (octave + 1 < octaveCount) {
        freq *= octaveScale;
        amp *= octaveMultiplier;
        sumAmp += amp;
      }
    }
    return [dx / sumAmp, dy / sumAmp];
  }

  function curlAt(p) {
    const x = p.pos.x + offset[0] * 100;
    const y = p.pos.y + offset[1] * 100;
    const z = p.pos.z + offset[2] * 100;
    const d1 = octaveField(z, y, x + scrollTotal);
    const d2 = octaveField(x + 100, z, y + scrollTotal);
    const d3 = octaveField(y, x + 100, z + scrollTotal);
    return [d3[0] - d2[1], d1[0] - d3[1], d2[0] - d1[1]];
  }

  function applyRemap(raw) {
    if (!spec.remapEnabled) return raw;
    const xCurve = spec.remap;
    const yCurve = separateAxes ? (spec.remapY || xCurve) : xCurve;
    const zCurve = separateAxes ? (spec.remapZ || xCurve) : xCurve;
    return [
      remapValue(ctx, raw[0], xCurve, frequency),
      remapValue(ctx, raw[1], yCurve, frequency),
      remapValue(ctx, raw[2], zCurve, frequency),
    ];
  }

  function applyStrength(raw, p, u) {
    const r = Number.isFinite(+p.r) ? +p.r : 0.5;
    const sx = sample(ctx, spec.strength, u, r) * dampingScale;
    const sy = separateAxes ? sample(ctx, spec.strengthY || spec.strength, u, r) * dampingScale : sx;
    const sz = separateAxes ? sample(ctx, spec.strengthZ || spec.strength, u, r) * dampingScale : sx;
    return [raw[0] * sx, raw[1] * sy, raw[2] * sz];
  }

  function applyRotation(raw, p, u, dt) {
    const rotation = spec.rotationAmount;
    if (!rotation || rotationGate(rotation) === 0) return;
    const r = Number.isFinite(+p.r) ? +p.r : 0.5;
    const amount = sample(ctx, rotation, u, r) * dt * (Math.PI / 360);
    if (spec.rotation3D || spec.rotation3d) {
      p.rotationVelocity = p.rotationVelocity || { x: 0, y: 0, z: 0 };
      p.rotationVelocity.x += raw[0] * amount;
      p.rotationVelocity.y += raw[1] * amount;
      p.rotationVelocity.z += raw[2] * amount;
    } else {
      p.rotationVelocityZ = number(ctx, p.rotationVelocityZ, 0) + raw[2] * amount;
    }
  }

  return {
    onFrame(dt) {
      frame = cloneFrame();
      scrollTotal += sample(ctx, spec.scrollSpeed, dt / duration, 1) * dt;
    },

    onPreIntegrate(p, u, dt, vel) {
      if (unsupportedDimensions || !p || !p.pos || !vel) return;
      const time = curveTime(p, u);
      const raw = applyStrength(applyRemap(curlAt(p)), p, time);
      const pAmt = sample(ctx, spec.positionAmount, time, p.r);
      const nx = raw[0] * pAmt, ny = raw[1] * pAmt, nz = raw[2] * pAmt;
      const beforeX = vel.x, beforeY = vel.y, beforeZ = vel.z;
      vel.x += nx;
      vel.y += ny;
      vel.z += nz;
      applyRotation(raw, p, time, dt);

      const dx = vel.x - beforeX, dy = vel.y - beforeY, dz = vel.z - beforeZ;
      const delta = Math.hypot(dx, dy, dz);
      const magnitude = Math.hypot(nx, ny, nz);
      frame.particles += 1;
      frame.nonZeroField += magnitude > 1e-12 ? 1 : 0;
      frame.changedParticles += delta > 1e-12 ? 1 : 0;
      frame.velocityDelta += delta;
      frame.fieldMagnitude += magnitude;
    },

    report() {
      return {
        particles: frame.particles,
        perlinCalls: frame.perlinCalls,
        nonZeroField: frame.nonZeroField,
        changedParticles: frame.changedParticles,
        velocityDelta: frame.velocityDelta,
        fieldMagnitude: frame.fieldMagnitude,
        scrollTotal,
        skippedDimensions: unsupportedDimensions ? 1 : 0,
        remapEnabled: spec.remapEnabled ? 1 : 0,
        sizeAmountIgnored: spec.sizeAmount ? 1 : 0,
      };
    },
  };
}
