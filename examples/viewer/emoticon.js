// emoticon.js — sprite and particle overhead-item runtime
//
// Variable values use explicit modes (constant, twoConstants, curve, or twoCurves).
// Color keys and alpha keys are evaluated on independent timelines. Unsupported
// optional modules are reported and fall back to the documented neutral behavior.

import * as THREE from './three.module.min.js';

export const EMO_BUILD = 'example';       // Public example build identifier
console.info('[emoticon] build', 'example');

const _effVel = new THREE.Vector3();   // update 循环复用
const warned = new Set();
function warnOnce(key, message) {
  if (warned.has(key)) return;
  warned.add(key);
  console.warn(`[emoticon] ${message}`);
}

const num = (v, d = 0) => (Number.isFinite(+v) ? +v : d);
const vec3 = (a, d = [0, 0, 0]) => new THREE.Vector3(num(a?.[0], d[0]), num(a?.[1], d[1]), num(a?.[2], d[2]));
const quat = (a) => new THREE.Quaternion(num(a?.[0]), num(a?.[1]), num(a?.[2]), num(a?.[3], 1));

// ---- 渲染状态 ------------------------------------------------------------
//
// 材质的 floats 里带着上百个属性,但**只有一小部分真的驱动渲染状态** —— 哪些算数由
// 材质用的**着色器**决定,不由属性名决定。这两族的绑定完全不同,所以下面一族一张表,
// **不许跨族外推**:
//
//   Mysekai/Effect/UberUnlit(粒子件)     三个 pass。混合由 `_BlendSrc`/`_BlendDst` 驱动;
//     深度 `_ZTest`/`_ZWrite`;背面 `_Cull`;颜色掩码 14 = 只写 RGB 不写 alpha。
//   Mysekai/Emoticon/Sprite(sprite 件)   一个 pass。混合在 pass 里**写死** One/OneMinusSrcAlpha
//     (预乘 alpha,贴图是直通 alpha,所以是着色器里乘过);背面**写死** Off;
//     颜色掩码 15 = 连 alpha 一起写。只有 `_ZTest`/`_ZWrite` 由材质驱动,示例固定 `_ZWrite`=1。
//
// 两族材质里都留着一大堆**不生效的残留属性**(sprite 材质里甚至有整套 UberUnlit 的 125 个
// 属性,而它的着色器只声明 13 个)。按属性名猜必错,而且不会报错,只会静默画错。
//
// alpha 通道:粒子件的三个 pass 都不写目标 alpha;sprite 件写。three.js 只有整体的
// colorWrite、没有逐通道掩码,渲到不透明底上没差别,渲到透明画布就得自己想清楚。

// Blend-mode values map to the corresponding three.js factors.
const BLEND_FACTOR = {
  0: THREE.ZeroFactor, 1: THREE.OneFactor,
  2: THREE.DstColorFactor, 3: THREE.SrcColorFactor,
  4: THREE.OneMinusDstColorFactor, 5: THREE.SrcAlphaFactor,
  6: THREE.OneMinusSrcColorFactor, 7: THREE.DstAlphaFactor,
  8: THREE.OneMinusDstAlphaFactor, 9: THREE.SrcAlphaSaturateFactor,
  10: THREE.OneMinusSrcAlphaFactor,
};
// Cull-mode values: 0=Off, 1=Front, 2=Back.
const CULL_SIDE = { 0: THREE.DoubleSide, 1: THREE.BackSide, 2: THREE.FrontSide };
const Z_OFFSET_EPSILON = 0.004;          // 两族着色器用的是同一个阈值常量

// 一族一条律。`zOffsetActive` 是**两族唯一不同的那行代码**:UberUnlit 判的是
// `0.004 < abs(_ZOffset)`,sprite 判的是 `0.004 < _ZOffset` —— **没有 abs**。
// sprite 材质里的非零 `_ZOffset` 全是负数,所以那一族的深度偏移实际上一个都不生效。
const SHADER_LAWS = {
  'Mysekai/Effect/UberUnlit': {
    blend: (f) => [num(f._BlendSrc, 5), num(f._BlendDst, 10)],
    cull: (f) => num(f._Cull, 2),
    zWrite: (f) => num(f._ZWrite, 0),
    zOffsetActive: (z) => Math.abs(z) > Z_OFFSET_EPSILON,
  },
  'Mysekai/Emoticon/Sprite': {
    blend: () => [1, 10],                // pass 里写死(预乘 alpha 语义)
    cull: () => 0,                       // pass 里写死 Off
    zWrite: (f) => num(f._ZWrite, 1),
    zOffsetActive: (z) => z > Z_OFFSET_EPSILON,
    // 游戏片元自己做 `SV_Target0.xyz = a * rgb` 的预乘;贴图是直通 alpha,少了这一乘,
    // 透明区的垃圾 RGB 会按 One 因子全部加出来 —— three.js 的 premultipliedAlpha
    // 在片元里做的正是同一行。
    premultiply: true,
  },
};
// 不认识的着色器:不猜。给一份中性状态并且**不加任何深度偏移**。
const NEUTRAL_LAW = {
  blend: () => [5, 10], cull: () => 2, zWrite: () => 0, zOffsetActive: () => false,
  premultiply: false,
};

/** 从材质读出渲染状态。材质缺失、跨包、或着色器不认识时退化成中性状态。 */
function renderState(material) {
  const mat = material && !material.external ? material : null;
  const f = (mat && mat.floats) || {};
  const shader = mat && mat.shader;
  let law = NEUTRAL_LAW;
  if (shader) {
    law = SHADER_LAWS[shader] || NEUTRAL_LAW;
    if (!SHADER_LAWS[shader]) {
      // 静默套一族的律就是在编数据。宁可退化成中性并喊出来。
      warnOnce(`law:${shader}`, `着色器 ${shader} 的渲染状态绑定没查过,退化成中性状态`);
    }
  }
  const [su, du] = law.blend(f);
  // CompareFunction: 0=Disabled 8=Always 都等于不做深度测试。两族都由 `_ZTest` 驱动。
  const zTest = num(f._ZTest, 4);
  const zOffset = num(f._ZOffset, 0);
  return {
    blending: THREE.CustomBlending,
    blendSrc: BLEND_FACTOR[su] ?? THREE.SrcAlphaFactor,
    blendDst: BLEND_FACTOR[du] ?? THREE.OneMinusSrcAlphaFactor,
    depthWrite: law.zWrite(f) > 0.5,
    depthTest: zTest !== 0 && zTest !== 8,
    side: CULL_SIDE[law.cull(f)] ?? THREE.FrontSide,
    premultipliedAlpha: !!law.premultiply,
    // 已经把「这一族认不认这个偏移」判掉了 —— 不生效的一律归零,下游不必再判阈值。
    zOffset: law.zOffsetActive(zOffset) ? zOffset : 0,
  };
}

/**
 * `_ZOffset` 的等价实现:在**线性眼深度(米)**上加偏移,只改 clip z,不动 x/y/w
 * —— 所以件在深度上朝相机浮起而屏幕位置分毫不变。负值 = 更靠近相机。
 *
 * 反解用的是投影矩阵自己的两个系数:clip.z = P[2][2]·(−d) + P[3][2](眼空间 w=1)。
 * 同一个偏移值共享一份编译产物,靠 customProgramCacheKey 区分。
 *
 * 「这一族着色器认不认这个偏移值」已经由 renderState 的 `zOffsetActive` 判掉了
 * (两族的阈值一个带 abs 一个不带),到这里非 0 就是要生效的。
 */
function applyZOffset(material, zOffset) {
  if (!zOffset) return material;
  const literal = zOffset.toFixed(6);
  material.onBeforeCompile = (shader) => {
    const anchor = 'gl_Position = projectionMatrix * mvPosition;';
    if (!shader.vertexShader.includes(anchor)) {
      // 锚点串是 three.js 内置着色器的原文;换了版本就可能改写法。静默失败会让件看着
      // 正常却少了深度偏移,所以宁可喊出来。
      warnOnce('zoffanchor', '着色器里找不到深度改写的锚点,_ZOffset 这次没生效');
      return;
    }
    shader.vertexShader = shader.vertexShader.replace(anchor,
      [anchor,
       '{',
       `  float viewerDepth = max( -mvPosition.z + (${literal}), 0.001 );`,
       '  gl_Position.z = projectionMatrix[2][2] * ( -viewerDepth ) + projectionMatrix[3][2];',
       '  gl_Position.z = clamp( gl_Position.z, -gl_Position.w, gl_Position.w );',
       '}'].join('\n'));
  };
  material.customProgramCacheKey = () => `viewerZOffset${literal}`;
  return material;
}

// ---- 取值编码 ------------------------------------------------------------

function hermite(keys, t) {
  if (!keys || !keys.length) return 0;
  if (t <= keys[0].time) return keys[0].value;
  const last = keys[keys.length - 1];
  if (t >= last.time) return last.value;
  let i = 0;
  while (i < keys.length - 1 && keys[i + 1].time <= t) i++;
  const a = keys[i], b = keys[i + 1];
  const dt = b.time - a.time;
  if (dt <= 0) return b.value;
  const u = (t - a.time) / dt;
  // 斜率为 null 表示序列化里是无穷(阶梯),按 0 处理 —— 这是近似。
  const m0 = num(a.outSlope) * dt, m1 = num(b.inSlope) * dt;
  const u2 = u * u, u3 = u2 * u;
  return (2 * u3 - 3 * u2 + 1) * a.value + (u3 - 2 * u2 + u) * m0
       + (-2 * u3 + 3 * u2) * b.value + (u3 - u2) * m1;
}

/** 一个可变量在归一化时间 t 上的值;`r` 是该粒子的随机因子(0..1)。 */
export function sampleValue(spec, t = 0, r = 0.5) {
  if (!spec) return 0;
  switch (spec.mode) {
    case 'constant': return num(spec.value);
    case 'twoConstants': return num(spec.min) + (num(spec.max) - num(spec.min)) * r;
    case 'curve': return hermite(spec.keys, t) * num(spec.multiplier, 1);
    case 'twoCurves': {
      const lo = hermite(spec.minKeys, t), hi = hermite(spec.maxKeys, t);
      return (lo + (hi - lo) * r) * num(spec.multiplier, 1);
    }
    default:
      warnOnce(`curve:${spec.mode}`, `未实现的取值模式 ${spec.mode},按 0 处理`);
      return 0;
  }
}

// 理论量只描述导出参数声明的单周期发射，不改变实际 spawn 调度。
function emissionPlan(system) {
  const emission = system?.emission;
  if (!emission) return { burst: 0, rate: 0, total: 0, duration: 0 };
  const burst = (emission.bursts || []).reduce((sum, item) => {
    const cycles = Math.max(1, Math.round(num(item.cycleCount, 1)));
    return sum + sampleValue(item.count, 0, 0.5) * cycles;
  }, 0);
  const duration = Math.max(0, num(system.duration, 0));
  const rate = sampleValue(emission.rateOverTime, 0, 0.5) * duration;
  return { burst, rate, total: burst + rate, duration };
}

function gradientAt(g, t) {
  const ck = g?.colorKeys || [], ak = g?.alphaKeys || [];
  const pick = (keys, get) => {
    if (!keys.length) return null;
    if (t <= keys[0].time) return get(keys[0]);
    const last = keys[keys.length - 1];
    if (t >= last.time) return get(last);
    let i = 0;
    while (i < keys.length - 1 && keys[i + 1].time <= t) i++;
    const a = keys[i], b = keys[i + 1];
    const span = b.time - a.time;
    const u = span > 0 ? (t - a.time) / span : 1;
    const va = get(a), vb = get(b);
    return Array.isArray(va) ? va.map((x, k) => x + (vb[k] - x) * u) : va + (vb - va) * u;
  };
  const rgb = pick(ck, (k) => k.color) || [1, 1, 1];
  const alpha = pick(ak, (k) => k.alpha);
  return { rgb, alpha: alpha == null ? 1 : alpha };
}

/** 颜色取值 -> {color, alpha}。颜色键与透明键各自插值后合并。 */
export function sampleColor(spec, t = 0, r = 0.5) {
  const out = (rgb, a) => ({ color: new THREE.Color(rgb[0], rgb[1], rgb[2]), alpha: a });
  if (!spec) return out([1, 1, 1], 1);
  switch (spec.mode) {
    case 'color': return out(spec.color, num(spec.color?.[3], 1));
    case 'twoColors': {
      const lo = spec.min || [1, 1, 1, 1], hi = spec.max || [1, 1, 1, 1];
      return out(lo.slice(0, 3).map((x, k) => x + (hi[k] - x) * r),
                 num(lo[3], 1) + (num(hi[3], 1) - num(lo[3], 1)) * r);
    }
    case 'gradient': { const g = gradientAt(spec.gradient, t); return out(g.rgb, g.alpha); }
    case 'twoGradients': {
      const lo = gradientAt(spec.minGradient, t), hi = gradientAt(spec.maxGradient, t);
      return out(lo.rgb.map((x, k) => x + (hi.rgb[k] - x) * r), lo.alpha + (hi.alpha - lo.alpha) * r);
    }
    case 'randomColor': { const g = gradientAt(spec.maxGradient || spec.gradient, r); return out(g.rgb, g.alpha); }
    default:
      warnOnce(`grad:${spec.mode}`, `未实现的颜色模式 ${spec.mode},按白色处理`);
      return out([1, 1, 1], 1);
  }
}

// ---- 发射形状 ------------------------------------------------------------

const DEG = Math.PI / 180;

/** Annulus sampling for radius-based shapes: thickness 1 fills the whole
 *  region, 0 emits from the outer rim only; power 2 = disc, 3 = sphere. */
function sampleShapeRadius(radius, thickness, power) {
  const inner = Math.pow(Math.min(1, Math.max(0, 1 - num(thickness))), power);
  return radius * Math.pow(inner + (1 - inner) * Math.random(), 1 / power);
}

/**
 * Sample a position and direction in shape-local space, then apply the
 * shape's own transform:
 *   pos' = R * (S * p) + t     dir' = normalize(R * (S * n))
 * Scale applies before rotation; Euler composition order is Z-X-Y
 * ('YXZ' in three.js terms). See docs/presentation.md for the per-shape
 * conventions.
 */
export function emitFrom(shape) {
  const pos = new THREE.Vector3(), dir = new THREE.Vector3(0, 0, 1);
  const radius = num(shape?.radius), arc = num(shape?.arc, 360) * DEG;
  switch (shape?.type) {
    case 'Sphere': {
      // Isotropic in the volume; direction is the radial unit vector of the
      // position — there is no preferred axis.
      const u = Math.random() * Math.PI * 2, v = Math.acos(2 * Math.random() - 1);
      dir.set(Math.sin(v) * Math.cos(u), Math.sin(v) * Math.sin(u), Math.cos(v));
      pos.copy(dir).multiplyScalar(sampleShapeRadius(radius, shape.radiusThickness, 3));
      break;
    }
    case 'Circle': {
      // Position in the local XY plane; direction is radially outward within
      // that plane — not the +Z normal.
      const a = Math.random() * (arc || Math.PI * 2);
      dir.set(Math.cos(a), Math.sin(a), 0);
      pos.copy(dir).multiplyScalar(sampleShapeRadius(radius, shape.radiusThickness, 2));
      break;
    }
    case 'Cone': {
      // Base circle in the local XY plane (z = 0); the main axis is +Z, and
      // with angle = 0 the direction is exactly (0, 0, 1).
      const a = Math.random() * (arc || Math.PI * 2);
      const rr = sampleShapeRadius(radius, shape.radiusThickness, 2);
      pos.set(Math.cos(a) * rr, Math.sin(a) * rr, 0);
      const spread = num(shape.angle) * DEG;
      dir.set(Math.cos(a) * Math.sin(spread), Math.sin(a) * Math.sin(spread), Math.cos(spread));
      break;
    }
    case 'SingleSidedEdge':
      // Position along the local X axis in [-radius, +radius] (radius is the
      // half-length); direction is always local +Y.
      pos.set((Math.random() * 2 - 1) * radius, 0, 0);
      dir.set(0, 1, 0);
      break;
    case 'BoxEdge': {
      // The 12 edges of the unit cube [-0.5, 0.5]^3: pick one axis to vary
      // continuously, snap the other two to the edge ends. Actual size comes
      // from shape.scale. Direction is always local +Z regardless of edge.
      const axis = Math.floor(Math.random() * 3);
      const ends = [Math.random() < 0.5 ? -0.5 : 0.5, Math.random() < 0.5 ? -0.5 : 0.5];
      ends.splice(axis, 0, Math.random() - 0.5);
      pos.set(ends[0], ends[1], ends[2]);
      break;
    }
    default:
      warnOnce(`shape:${shape?.type}`, `发射形状 ${shape?.type ?? '(无)'} 未实现,退化成点发射`);
  }
  if (shape?.scale) {
    const s = vec3(shape.scale);
    pos.multiply(s); dir.multiply(s);
  }
  if (shape?.rotation) {
    const e = new THREE.Euler(num(shape.rotation[0]) * DEG, num(shape.rotation[1]) * DEG,
                              num(shape.rotation[2]) * DEG, 'YXZ');
    pos.applyEuler(e); dir.applyEuler(e);
  }
  if (shape?.position) pos.add(vec3(shape.position));
  // A zero scale axis can collapse the direction to a zero vector; keep it
  // zero (no initial velocity) instead of inventing a direction.
  if (dir.lengthSq() > 1e-30) dir.normalize(); else dir.set(0, 0, 0);
  return { pos, dir };
}

// ---- 粒子发射器 ----------------------------------------------------------

const GRAVITY = -9.81;

class Emitter {
  /**
   * @param spec `particles[]` 的一项
   * @param localParent 跟随锚点的父节点(Local 空间)
   * @param worldParent 不跟随锚点的父节点(World 空间 / keepPosition)
   */
  constructor(spec, localParent, worldParent, textureFor) {
    this.system = spec.system || {};
    this.renderer = spec.renderer || {};
    // World 空间的粒子生成后不跟随发射器,所以挂在世界父节点下 —— 但发射位置与方向
    // 仍以**发射节点当时的世界变换**为基准。少了这步变换,粒子就会从世界原点(地面)
    // 冒出来,而不是从头顶的发射节点冒出来。
    this.worldSpace = this.system.simulationSpace === 'World';
    this.node = localParent;                       // 发射节点(局部空间基准)
    this.parent = (this.worldSpace ? worldParent : localParent) || localParent;
    this.particles = [];
    this.age = 0;
    this.pending = 0;
    this.burstCursor = [];
    this.peak = 0;
    this.theoretical = emissionPlan(this.system);

    const material = this.renderer.material;
    let file = null;
    if (material && !material.external) {
      file = material.textures?._BaseMap ?? null;
      if (!file && material.textures?._BaseMap2DArray !== undefined) {
        warnOnce('tex2darray', '有粒子材质用的是贴图数组,本 demo 解不了,退化成无贴图');
      }
    } else if (material?.external) {
      warnOnce('matext', '有粒子材质在别的包里(变体件复用主件材质),本 demo 未加载,退化成无贴图');
    }
    this.map = file ? textureFor(file) : null;
    this.state = renderState(material);
    const sheet = this.system.textureSheet;
    this.tiles = sheet && (num(sheet.tilesX, 1) > 1 || num(sheet.tilesY, 1) > 1)
      ? { x: num(sheet.tilesX, 1), y: num(sheet.tilesY, 1), spec: sheet } : null;
  }

  spawn() {
    const s = this.system, start = s.start || {};
    const r = Math.random();
    const { pos, dir } = emitFrom(s.shape);
    pos.x = -pos.x; dir.x = -dir.x;      // 同一节点内容的 M 换手(节点链共轭之外的另一半)
    if (this.worldSpace && this.node) {
      // 局部点/方向 → 世界。父链的旋转也一起吃掉(挂点在绑定姿态下带 90° 旋转,
      // 不转换的话件的 y 偏移会跑成世界 x 偏移)。
      this.node.updateWorldMatrix(true, false);
      pos.applyMatrix4(this.node.matrixWorld);
      dir.transformDirection(this.node.matrixWorld).normalize();
    }
    const life = Math.max(0.01, sampleValue(start.lifetime, 0, r));
    const size = sampleValue(start.size, 0, r);
    const first = sampleColor(start.color, 0, r);
    // 每粒子独立材质:颜色、透明度、旋转、贴图帧都是逐粒子量。
    const map = this.tiles && this.map ? this.map.clone() : this.map;
    if (map && this.tiles) {
      map.needsUpdate = true;
      map.repeat.set(1 / this.tiles.x, 1 / this.tiles.y);
    }
    const st = this.state;
    const sprite = new THREE.Sprite(applyZOffset(new THREE.SpriteMaterial({
      map: map || null, color: first.color, opacity: first.alpha, transparent: true,
      blending: st.blending, blendSrc: st.blendSrc, blendDst: st.blendDst,
      premultipliedAlpha: st.premultipliedAlpha,
      depthWrite: st.depthWrite, depthTest: st.depthTest,
    }), st.zOffset));
    sprite.position.copy(pos);
    sprite.material.rotation = sampleValue(start.rotation, 0, r);
    sprite.renderOrder = num(this.renderer.sortingOrder);
    this.parent.add(sprite);
    this.particles.push({
      sprite, map, r, life, age: 0, size,
      pos: pos.clone(),
      vel: dir.multiplyScalar(sampleValue(start.speed, 0, r)),
      spin: sampleValue(start.rotation, 0, r),
      gravity: sampleValue(start.gravityModifier, 0, r),
    });
  }

  update(dt) {
    // 单帧 dt 钳制:切件/加载卡顿会给出数百毫秒的帧间隔,新生粒子会被一帧推出老远,
    // 肉眼看就是「出生位置错了」。0.1 s 对应 10 fps 下限,正常帧率不受影响。
    dt = Math.min(dt, 0.1);
    const s = this.system, em = s.emission;
    this.age += dt * num(s.simulationSpeed, 1);
    const cap = num(s.maxParticles, 1000);
    if (em) {
      this.pending += sampleValue(em.rateOverTime, 0, Math.random()) * dt;
      while (this.pending >= 1) {
        this.pending -= 1;
        if (this.particles.length < cap) this.spawn();
      }
      for (const [bi, burst] of (em.bursts || []).entries()) {
        // cycleCount 0 = 无限循环(每 repeatInterval 重发一轮),非 0 = 固定轮数。
        const declared = num(burst.cycleCount, 1);
        const cycles = declared === 0 ? Infinity : Math.max(1, declared);
        const interval = Math.max(num(burst.repeatInterval, 0.01), 0.01);
        let c = this.burstCursor[bi] || 0;
        while (c < cycles && this.age >= num(burst.time) + c * interval) {
          c += 1;
          if (Math.random() > num(burst.probability, 1)) continue;
          const count = Math.round(sampleValue(burst.count, 0, Math.random()));
          for (let k = 0; k < count && this.particles.length < cap; k++) this.spawn();
        }
        this.burstCursor[bi] = c;
      }
    }
    const alive = [];
    for (const p of this.particles) {
      p.age += dt * num(s.simulationSpeed, 1);
      if (p.age >= p.life) { this._drop(p); continue; }
      const u = p.age / p.life;
      if (p.gravity) p.vel.y += GRAVITY * p.gravity * dt;
      const limit = s.limitVelocity;
      if (limit) {
        const max = sampleValue(limit.magnitude, u, p.r);
        const speed = p.vel.length();
        if (max > 0 && speed > max) {
          const damped = speed + (max - speed) * Math.min(1, num(limit.dampen));
          p.vel.setLength(num(limit.dampen) ? damped : max);
        }
      }
      // 位移用的有效速度 = 有状态速度(出生速度,经阻尼/重力累积)
      //                  + velocityOverLifetime 的**叠加值**(逐帧取值,不积分、不入状态)
      //                  再整体乘 speedModifier。阻尼作用于状态速度,叠加值不吃阻尼。
      const vol = s.velocityOverLifetime;
      _effVel.copy(p.vel);
      if (vol) {
        _effVel.x += sampleValue(vol.x, u, p.r);
        _effVel.y += sampleValue(vol.y, u, p.r);
        _effVel.z += sampleValue(vol.z, u, p.r);
        const mod = sampleValue(vol.speedModifier, u, p.r);
        if (mod) _effVel.multiplyScalar(mod);
      }
      p.pos.addScaledVector(_effVel, dt);
      p.sprite.position.copy(p.pos);

      const scale = p.size * (s.sizeOverLifetime ? sampleValue(s.sizeOverLifetime.curve, u, p.r) : 1);
      const clamped = Math.min(Math.max(Math.abs(scale), 0.0001), num(this.renderer.maxParticleSize, 1e3) || 1e3);
      p.sprite.scale.setScalar(clamped);

      if (s.colorOverLifetime) {
        const c = sampleColor(s.colorOverLifetime, u, p.r);
        p.sprite.material.color.copy(c.color);
        p.sprite.material.opacity = c.alpha;
      }
      if (s.rotationOverLifetime) {                    // 弧度每秒
        p.spin += sampleValue(s.rotationOverLifetime.curve, u, p.r) * dt;
        p.sprite.material.rotation = p.spin;
      }
      if (this.tiles && p.map) {
        const sheet = this.tiles.spec;
        const total = this.tiles.x * this.tiles.y;
        const frame = Math.min(total - 1, Math.max(0,
          Math.floor(sampleValue(sheet.frameOverTime, u, p.r) * total)));
        p.map.offset.set((frame % this.tiles.x) / this.tiles.x,
                         1 - 1 / this.tiles.y - Math.floor(frame / this.tiles.x) / this.tiles.y);
      }
      alive.push(p);
    }
    this.particles = alive;
    this.peak = Math.max(this.peak, alive.length);
  }

  _drop(p) {
    p.sprite.removeFromParent();
    p.sprite.material.dispose();
    if (p.map && p.map !== this.map) p.map.dispose();
  }

  reset() {
    for (const p of this.particles) this._drop(p);
    this.particles = [];
    this.age = 0;
    this.pending = 0;
    this.burstCursor = [];
  }

  dispose() { this.reset(); }
}

// ---- 件 -----------------------------------------------------------------

function planeFor(node, sprites, textureFor) {
  const spec = sprites?.[node.sprite];
  if (!spec) return null;
  const ppu = num(spec.pixelsToUnits, 100) || 100;
  const w = num(spec.rect?.[2], 1) / ppu, h = num(spec.rect?.[3], 1) / ppu;
  const geometry = new THREE.PlaneGeometry(w, h);
  // pivot 是 0..1 的归一化锚点;平面默认以中心为原点,按 pivot 偏移。
  geometry.translate((0.5 - num(spec.pivot?.[0], 0.5)) * w, (0.5 - num(spec.pivot?.[1], 0.5)) * h, 0);
  const map = spec.file ? textureFor(spec.file) : null;
  // `rect` is the sprite sub-rectangle in the full texture. The coordinate origin
  // is converted to the normalized UV convention used by the loaded texture.
  if (map) {
    const applyUv = () => {
      const img = map.image;
      const W = img && (img.width || img.videoWidth), H = img && (img.height || img.videoHeight);
      if (!W || !H) return;
      const x0 = num(spec.rect?.[0], 0) / W, y0 = num(spec.rect?.[1], 0) / H;
      const x1 = (num(spec.rect?.[0], 0) + num(spec.rect?.[2], W)) / W;
      const y1 = (num(spec.rect?.[1], 0) + num(spec.rect?.[3], H)) / H;
      if (x0 === 0 && y0 === 0 && Math.abs(x1 - 1) < 1e-6 && Math.abs(y1 - 1) < 1e-6) return;
      // PlaneGeometry 的 uv 顺序是 [左上, 右上, 左下, 右下]
      geometry.setAttribute('uv', new THREE.Float32BufferAttribute(
        [x0, y1, x1, y1, x0, y0, x1, y0], 2));
    };
    if (map.image && (map.image.width || map.image.videoWidth)) applyUv();
    else { const prev = map.onUpdate; map.onUpdate = () => { applyUv(); if (prev) prev(); }; }
  }
  const c = node.color || [1, 1, 1, 1];
  const st = renderState(node.material);
  // 节点带 material 就照它的着色器族画;不带的时候退化成双面 —— flipX/flipY 用负缩放
  // 实现,会翻绕序,单面会整片消失。
  const mesh = new THREE.Mesh(geometry, applyZOffset(new THREE.MeshBasicMaterial({
    map, transparent: true,
    blending: st.blending, blendSrc: st.blendSrc, blendDst: st.blendDst,
    premultipliedAlpha: st.premultipliedAlpha,
    depthWrite: st.depthWrite, depthTest: st.depthTest,
    side: node.material ? st.side : THREE.DoubleSide,
    color: new THREE.Color(num(c[0], 1), num(c[1], 1), num(c[2], 1)), opacity: num(c[3], 1),
  }), st.zOffset));
  if (node.flipX) mesh.scale.x = -1;
  if (node.flipY) mesh.scale.y = -1;
  mesh.renderOrder = num(node.sortingOrder);
  mesh.visible = node.rendererEnabled !== false;
  return mesh;
}

export class EmoticonView {
  /**
   * @param item `items[name]`,额外带一个 `name` 字段
   * @param opts `{anchor, worldParent, textureFor, textureBase}` —— 给了 anchor 就直接挂上去;
   *             没有 textureFor 时用 textureBase 自己加载贴图
   */
  constructor(item, opts = {}) {
    this.name = item?.name || '(unnamed)';
    this.item = item;
    const name = this.name;
    this.root = new THREE.Group();
    this.root.name = `emoticon:${name}`;
    this.root.visible = false;
    this.byPath = new Map();
    this.byAnimPath = new Map();
    this.disposed = false;
    const textureFor = opts.textureFor || makeTextureLoader(opts.textureBase || '');
    const mirror = item?.viewKind === 'particle';

    for (const node of item.nodes || []) {
      const group = new THREE.Group();
      group.name = node.name || '';
      // Particle-local transforms use the same reflected right-handed frame as the
      // glTF scene; sprite items remain in the camera-facing item frame.
      if (mirror) {
        const q0 = quat(node.rotation);
        group.position.set(-num(node.position?.[0]), num(node.position?.[1]), num(node.position?.[2]));
        group.quaternion.set(q0.x, -q0.y, -q0.z, q0.w);
      } else {
        group.position.copy(vec3(node.position));
        group.quaternion.copy(quat(node.rotation));
      }
      group.scale.copy(vec3(node.scale, [1, 1, 1]));
      group.visible = node.active !== false;
      const parent = node.parent == null ? this.root : (this.byPath.get(node.parent) || this.root);
      parent.add(group);
      this.byPath.set(node.path, group);
      // 片段通道按 animationPath 匹配,不是 path。
      if (node.animationPath != null) this.byAnimPath.set(node.animationPath, group);
      if (node.sprite) {
        const mesh = planeFor(node, item.sprites, textureFor);
        if (mesh) group.add(mesh);
        else console.warn(`[emoticon] ${name}: 节点 ${node.path} 要画的 sprite ${node.sprite} 不在本包里`);
      }
    }
    this.emitters = (item.particles || [])
      .filter((p) => p.system)
      .map((p) => {
        const local = this.byPath.get(p.node) || this.root;
        // 世界/局部父节点由 Emitter 按 simulationSpace 选择。keepPosition 只门控
        // billboard 旋转律,与挂父无关 —— World 空间的出生点已是世界坐标,
        // 挂回锚定树会被再变换一次,出生位置整体飞离角色。
        return new Emitter(p, local, opts.worldParent || this.root, textureFor);
      });
    if (opts.anchor) opts.anchor.add(this.root);
    this.reset();
  }

  /** 兼容访问器:节点表(按相对包根的 path)。片段通道要用 byAnimPath,不是这个。 */
  get nodeMap() { return this.byPath; }

  /** 已经收完(可以回收)。 */
  get hidden() { return this.phase === 'idle' && !this.root.visible; }

  /** 已进入 end 段(hide() 或 showSeconds 到点触发)。 */
  get endRequested() { return this.phase === 'end' || this.phase === 'closing'; }

  get spriteCount() {
    let n = 0;
    this.root.traverse((o) => { if (o.isMesh) n++; });
    return n;
  }

  reset() {
    this.phase = 'idle';
    this.clock = 0;
    this.endClock = 0;
    this.hideAt = null;
    for (const e of this.emitters) e.reset();
  }

  play(showSeconds = null) {
    if (this.disposed) return;
    this.reset();
    this.root.visible = true;
    this.phase = this.item.clips?.start ? 'start' : (this.item.clips?.loop ? 'loop' : 'live');
    this.hideAt = showSeconds == null ? null : Math.max(0, showSeconds);
    this._applyClip(this.item.clips?.[this.phase], 0);
  }

  /** 收件:有 end 片段就播一次,播完再等 1 秒才真正消失。 */
  hide() {
    if (this.disposed || this.phase === 'idle' || this.phase === 'end') return;
    this.phase = this.item.clips?.end ? 'end' : 'closing';
    this.endClock = 0;
  }

  _applyClip(clip, t) {
    for (const channel of clip?.channels || []) {
      const node = this.byAnimPath.get(channel.path);
      if (!node || !channel.values?.length) continue;
      const index = Math.min(channel.values.length - 1,
                             Math.max(0, Math.round(t * num(clip.rate, 60))));
      const v = channel.values[index];
      if (channel.property === 'position') node.position.fromArray(v);
      else if (channel.property === 'scale') node.scale.fromArray(v);
      else if (channel.property === 'rotation') node.quaternion.fromArray(v);
      else if (channel.property === 'eulerAngles') {
        node.quaternion.setFromEuler(new THREE.Euler(v[0] * DEG, v[1] * DEG, v[2] * DEG));
      }
    }
  }

  update(dt) {
    if (this.disposed || this.phase === 'idle') return;
    this.clock += dt;
    if (this.hideAt != null && this.clock >= this.hideAt) { this.hideAt = null; this.hide(); }
    for (const e of this.emitters) e.update(dt);

    const clips = this.item.clips || {};
    if (this.phase === 'start') {
      const clip = clips.start;
      if (this.clock >= num(clip?.duration)) { this.phase = clips.loop ? 'loop' : 'live'; }
      else return this._applyClip(clip, this.clock);
    }
    if (this.phase === 'loop' && clips.loop) {
      const span = Math.max(0.001, num(clips.loop.duration));
      const base = num(clips.start?.duration);
      return this._applyClip(clips.loop, (this.clock - base) % span);
    }
    if (this.phase === 'end' || this.phase === 'closing') {
      this.endClock += dt;
      const clip = clips.end;
      if (clip) this._applyClip(clip, Math.min(this.endClock, num(clip.duration)));
      // 运行时在 end 之后还留 1 秒才销毁。
      if (this.endClock >= num(clip?.duration) + 1) this.stop();
    }
  }

  /** 收完:从画面移除但保留对象,可再次 play()。 */
  stop() {
    this.reset();
    this.root.visible = false;
  }

  stats() {
    return {
      name: this.name, kind: this.item.viewKind, phase: this.phase,
      nodes: this.byPath.size, sprites: this.spriteCount,
      emitters: this.emitters.length,
      live: this.emitters.reduce((n, e) => n + e.particles.length, 0),
      peak: this.emitters.reduce((n, e) => Math.max(n, e.peak), 0),
      theoreticalBurst: this.emitters.reduce((n, e) => n + e.theoretical.burst, 0),
      theoreticalRate: this.emitters.reduce((n, e) => n + e.theoretical.rate, 0),
      theoreticalTotal: this.emitters.reduce((n, e) => n + e.theoretical.total, 0),
    };
  }

  dispose() {
    for (const e of this.emitters) e.dispose();
    this.root.traverse((o) => {
      if (o.isMesh || o.isSprite) {
        o.geometry?.dispose?.();
        o.material?.dispose?.();
      }
    });
    this.root.removeFromParent();
    this.disposed = true;
  }
}

function makeTextureLoader(base) {
  const loader = new THREE.TextureLoader();
  const cache = new Map();
  const prefix = base && !base.endsWith('/') ? `${base}/` : base;
  return (file) => {
    if (!cache.has(file)) {
      const tex = loader.load(prefix + file);
      tex.colorSpace = THREE.SRGBColorSpace;
      cache.set(file, tex);
    }
    return cache.get(file);
  };
}

export async function loadEmoticons(url) {
  const response = await fetch(url);
  if (!response.ok) return null;
  const doc = await response.json();
  const textureFor = makeTextureLoader(url.replace(/[^/]*$/, ''));
  return {
    doc,
    items: doc.items || {},
    names: Object.keys(doc.items || {}).sort(),
    kindOf: (name) => doc.items?.[name]?.viewKind || null,
    create(name, opts = {}) {
      const item = doc.items?.[name];
      if (!item) return null;
      return new EmoticonView({ ...item, name }, { ...opts, textureFor });
    },
  };
}
