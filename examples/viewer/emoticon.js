// emoticon.js — sprite and particle overhead-item runtime
//
// Variable values use explicit modes (constant, twoConstants, curve, or twoCurves).
// Color keys and alpha keys are evaluated on independent timelines. Unsupported
// optional modules are reported and fall back to the documented neutral behavior.

import * as THREE from './three.module.min.js';
import { GLTFLoader } from './GLTFLoader.js';
import { makeHooks, registeredModules } from './fx/hooks.js';
import { makeDrawable, drawableModes, drawableRejection } from './fx/drawable.js';
// 绘制件的实现文件在这里注册。没被注册的绘制模式一律整条不画并计数。
import * as drawMesh from './fx/draw-mesh.js';
import * as drawHorizontal from './fx/draw-horizontal.js';
import * as trails from './fx/trails.js';
import * as noise from './fx/noise.js';
import * as subEmitters from './fx/sub-emitters.js';
import { registerDrawable } from './fx/drawable.js';
import { registerFxModule } from './fx/hooks.js';
registerDrawable(drawMesh);
registerDrawable(drawHorizontal);
// Noise is pre-simulation slot 6, before InheritVelocity, Force, and ClampVelocity.
registerFxModule(noise, 200);
// Trail runs after Lights and before Size in the post-simulation block (canon
// particle-module-order). The numeric order just needs to be inside that block;
// the per-particle hooks that read position run after integration regardless.
registerFxModule(trails, 420);
// Sub-emitters run in the post-simulation block after Trail: a death-triggered
// record needs the position the particle integrated to this frame.
registerFxModule(subEmitters, 440);

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
// 有些量的「无限」是**有意义的取值**,不是坏数据:一个重发间隔为无穷的 burst 的意思
// 是「不重发」。`num` 会把无穷当成坏数据退回默认值,于是无穷大的间隔变成默认的一
// 百分之一秒 —— 那条 burst 就从发一次变成每秒发一百轮。这里保住无穷。
const spanNum = (v, d = 0) => {
  const n = +v;
  if (Number.isFinite(n)) return n;
  return (n === Infinity || v === 'Infinity') ? Infinity : d;
};
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
// BlendOp values: 0=Add, 1=Subtract, 2=ReverseSubtract, 3=Min, 4=Max.
const BLEND_EQUATION = {
  0: THREE.AddEquation, 1: THREE.SubtractEquation,
  2: THREE.ReverseSubtractEquation, 3: THREE.MinEquation, 4: THREE.MaxEquation,
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
    blendOp: () => 0,
    cull: (f) => num(f._Cull, 2),
    zWrite: (f) => num(f._ZWrite, 0),
    zTest: (f) => num(f._ZTest, 4),
    zOffsetActive: (z) => Math.abs(z) > Z_OFFSET_EPSILON,
  },
  'Mysekai/Emoticon/Sprite': {
    blend: () => [1, 10],                // pass 里写死(预乘 alpha 语义)
    blendOp: () => 0,
    cull: () => 0,                       // pass 里写死 Off
    zWrite: (f) => num(f._ZWrite, 1),
    zTest: (f) => num(f._ZTest, 4),
    zOffsetActive: (z) => z > Z_OFFSET_EPSILON,
    // 游戏片元自己做 `SV_Target0.xyz = a * rgb` 的预乘;贴图是直通 alpha,少了这一乘,
    // 透明区的垃圾 RGB 会按 One 因子全部加出来 —— three.js 的 premultipliedAlpha
    // 在片元里做的正是同一行。
    premultiply: true,
  },
  // 内置 RP 的粒子着色器(默认粒子材质用它)。绑定与 UberUnlit **完全相反**,
  // 跨族外推必错:
  //   * 混合读 `_SrcBlend`/`_DstBlend`/`_BlendOp` —— 这一族的 30 项属性表里
  //     **没有** `_BlendSrc`/`_BlendDst`,照 UberUnlit 那套查一个都查不到;
  //   * 深度测试**写死** LEqual,不由 `_ZTest` 驱动(`_ZWrite` 仍是材质的);
  //   * 没有 `_ZOffset` 这一路,深度不做偏移;
  //   * 颜色掩码写死 14(只写 RGB),队列 3000;
  //   * 可达变体的片元只有 `tex2D(_MainTex, uv) * _Color * vertexColor`:
  //     没有预乘、没有软粒子、没有相机淡出、没有 alpha 裁剪。
  'Particles/Standard Unlit': {
    blend: (f) => [num(f._SrcBlend, 5), num(f._DstBlend, 10)],
    blendOp: (f) => num(f._BlendOp, 0),
    cull: (f) => num(f._Cull, 2),
    zWrite: (f) => num(f._ZWrite, 0),
    zTest: () => 4,                      // pass 里写死 LEqual
    zOffsetActive: () => false,          // 这一族没有深度偏移
    premultiply: false,
  },
};
// 不认识的着色器:不猜。给一份中性状态并且**不加任何深度偏移**。
const NEUTRAL_LAW = {
  blend: () => [5, 10], blendOp: () => 0, cull: () => 2, zWrite: () => 0,
  zTest: (f) => num(f._ZTest, 4), zOffsetActive: () => false,
  premultiply: false,
};

// 基础贴图的属性名也由着色器族决定,不由属性名的常见程度决定。实测每族只有一个基础贴图
// 属性、且两族不重名,所以这张表是**查得到的**,不是猜的:
//   Mysekai/Effect/UberUnlit → `_BaseMap`(数组形 `_BaseMap2DArray` 本 demo 解不了)
//   Particles/Standard Unlit → `_MainTex`
// 不在表里的着色器仍按 `_BaseMap` 试探(与本文件此前的行为一致)。
const BASE_MAP_KEY = {
  'Mysekai/Effect/UberUnlit': '_BaseMap',
  'Particles/Standard Unlit': '_MainTex',
};
const DEFAULT_BASE_MAP_KEY = '_BaseMap';
const TINT_AREA_ALL = 1;
// 打包的逐粒子选择器:**个位**选哪一个向量(0 是零流、1 是 custom1、2 是 custom2),
// **十位**选哪一个分量。反过来读会整条绑错。
const PROGRESS_VECTORS = { 1: 'custom1', 2: 'custom2' };
function progressCoordSource(coord) {
  const value = Math.round(num(coord, 0));
  const vector = PROGRESS_VECTORS[value % 10];
  if (!vector) return null;
  return { vector, component: 'xyzw'[Math.floor(value / 10)] || 'x' };
}

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
  // CompareFunction: 0=Disabled 8=Always 都等于不做深度测试。哪个字段驱动它由**族**决定:
  // UberUnlit / sprite 读 `_ZTest`,Particles/Standard Unlit 在 pass 里写死 LEqual。
  const zTest = law.zTest(f);
  const zOffset = num(f._ZOffset, 0);
  return {
    blending: THREE.CustomBlending,
    blendSrc: BLEND_FACTOR[su] ?? THREE.SrcAlphaFactor,
    blendDst: BLEND_FACTOR[du] ?? THREE.OneMinusSrcAlphaFactor,
    blendEquation: BLEND_EQUATION[law.blendOp(f)] ?? THREE.AddEquation,
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
  const duration = Math.max(0, num(system.duration, 0));
  const burst = (emission.bursts || []).reduce((sum, item) => {
    // cycleCount 0 是**无限轮**,不是「一轮」,也不是「不发」:它按 repeatInterval
    // 一直重发。夹成 1 会让这个数小得不动声色 —— 一个看起来合理的小数字,而任何
    // 按它判「发够了没有」的判据都会跟着变弱。单周期里的诚实值是能塞进 duration
    // 的重发次数。
    const declared = Math.round(num(item.cycleCount, 1));
    const interval = Math.max(spanNum(item.repeatInterval, 0.01), 0.01);
    const cycles = declared === 0
      ? Math.max(1, Math.floor((duration - num(item.time)) / interval) + 1)
      : Math.max(1, declared);
    return sum + sampleValue(item.count, 0, 0.5) * cycles;
  }, 0);
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

/**
 * 已实现的发射形状,与下面 `emitFrom` 的 switch 一一对应(改一处就要改另一处)。
 * 导出它是为了让消费方在**装载时**数出未建模形状的发射器数,而不是等运行时的控制台警告。
 */
export const EMIT_SHAPES = new Set(['Sphere', 'Circle', 'Cone', 'SingleSidedEdge', 'BoxEdge',
  'Hemisphere', 'ConeVolume', 'Donut', 'Mesh']);

/**
 * 形状模块的三态。**「没有形状模块」与「形状没建模」是两件不同的事**:
 *
 *   'none'          —— 数据里没有 `shape`(子发射器与小件多是这样)。运行时的语义就是从发射
 *                      节点原点发射一个点,所以点发射在这里是**正确行为**,不是退化,也不喊。
 *   'ok'            —— 形状在 EMIT_SHAPES 里,按 `emitFrom` 的公式采样。
 *   'unimplemented' —— 数据声明了形状,但本引擎没有它的发射公式。这时**整个发射器不发射**:
 *                      退化成点发射会把本该铺开几十米的粒子全堆在节点原点上 —— 实测一份雨天
 *                      现象的天空效果里,Hemisphere 半径 25 的发射器 28 个粒子的水平散布是
 *                      1e-16 米(全在世界原点,也就是角色脚下),画面上看就是「粒子堆在角色身上」。
 *                      宁可不画,也不要画在错的地方;计数由 `Emitter.suppressed` 逐个报出来。
 */
export function shapeSupport(shape) {
  const type = shape && shape.type;
  if (!type) return 'none';
  if (!EMIT_SHAPES.has(type)) return 'unimplemented';
  // Mesh 是有条件的:形状在表里,但数据可能声明了这条律覆盖不到的用法(见 meshShapeGap)。
  return (type === 'Mesh' && meshShapeGap(shape)) ? 'unimplemented' : 'ok';
}

/**
 * 「从网格表面发射」这条律**覆盖不到**这个形状的理由,覆盖得到时返回 null。
 * 三种都对应数据里明写的东西,不是保险丝:
 *
 *   'noMeshRef'     —— 声明了从网格发射,但一个网格都没解出来(产物里 29 个里有 3 个是
 *                      这样)。不拿别的网格顶替,整条停发并计数。
 *   'placement'     —— 放置模式不是三角。三种模式是顶点/边/三角(依次记 0/1/2),只有
 *                      三角这一种在数据里出现,也只有它建了模。字段缺席不当成三角。
 *   'materialIndex' —— 声明了只从某一个材质槽的三角发射。产物里一份网格是一整块几何,
 *                      没有分槽信息,做不到就不做(数据里没有一个用它)。
 */
export function meshShapeGap(shape) {
  const list = shape && shape.meshes;
  if (!Array.isArray(list) || !list.length || !list[0] || !list[0].file) return 'noMeshRef';
  if (Math.round(num(shape.meshPlacement, -1)) !== 2) return 'placement';
  if (shape.meshMaterialIndex != null) return 'materialIndex';
  return null;
}

/**
 * 故障注入开关(`?sabotage=` 专用,默认全假)。每一项都**原样还原一种已知错法**,
 * 用来让位置判据在错法下必须转红 —— 判据自己也要能被证伪。
 *   pointEmit          —— 未建模形状退化成点发射(散布判据必须红)
 *   dropNodeTransform  —— 出生点不过发射节点的世界变换(高度判据必须红)
 */
export const EMIT_FAULT = { pointEmit: false, dropNodeTransform: false };

// 资产里可能出现的可选模块名(用来把「声明了」与「跑了」分开数)。
export const OPTIONAL_MODULES = ['emission', 'shape', 'sizeOverLifetime', 'colorOverLifetime',
  'velocityOverLifetime', 'rotationOverLifetime', 'limitVelocity', 'forceOverLifetime',
  'noise', 'collision', 'trails', 'subEmitters', 'textureSheet', 'customData'];

/** Annulus sampling for radius-based shapes: thickness 1 fills the whole
 *  region, 0 emits from the outer rim only; power 2 = disc, 3 = sphere. */
function sampleShapeRadius(radius, thickness, power) {
  const inner = Math.pow(Math.min(1, Math.max(0, 1 - num(thickness))), power);
  return radius * Math.pow(inner + (1 - inner) * Math.random(), 1 / power);
}

// ---- 从网格表面发射 ------------------------------------------------------
//
// 三角放置模式(数据里 29 个发射器全是它)的三条律:
//
// 1) 选哪个三角 —— **按面积加权**,不是按三角序号均匀取。抽一个 [0,1) 的数,乘以整张
//    网格的三角面积总和,再在三角面积的前缀和上取反函数:第一个使累计面积达到这个目标
//    的三角就是它。引擎为这一步另建了一张按面积总和均分的桶表(每档记下当时的累计面积
//    与三角序号)当起点、再前后线性走到位,那只是加速 —— 结果与直接在前缀和上二分完全
//    相同,所以这里直接二分。
//    两种做法在三角大小不均的网格上分布明显不同(地面导航网格恰恰不均:同一张网格里
//    最大与最小的三角能差上百倍),按序号均匀取会让小三角那一片密得多。
//
// 2) 三角内取哪一点 —— 两个独立的 [0,1) 随机数 u、v,若 u+v>1 就折成 (1-u, 1-v),
//    出生点 = u·P0 + v·P1 + (1-u-v)·P2。折叠这一步是必需的:不折就是在平行四边形里
//    取点,一半的点会落到三角外面去。
//
// 3) 发射方向 —— **用同一组重心权重插值三个顶点的法线**,不是三角的面法线。
//    `meshNormalOffset` 沿这条插值出来的法线把出生点推开(数据里 29 个全是 0)。
//
// 形状自身的 position/rotation/scale 照常在后面那道公共尾巴上作用,与别的形状一样。

const _meshSamplers = new WeakMap();

/**
 * 把一个网格节点整理成可按面积采样的三角表(只建一次,按节点缓存)。
 *
 * 产物里的网格顶点已经做过一次左右手转换(x 取反、绕序翻转),而出生点在 `spawn` 里
 * 还要再过一次同样的转换 —— 所以这里先把 x 转回去,免得转两次。转两次不会报错,
 * 表现是整片粒子沿 x 轴镜像:落在地面网格的镜像位置上,看着「铺开了」却铺错了地方。
 * 绕序翻转不必还原:重心权重在三个角上是可交换的,换两个角的次序不改变分布。
 */
function buildMeshSampler(root) {
  const geoms = [];
  const walk = (o, mat) => {
    o.updateMatrix();
    const m = (o === root) ? new THREE.Matrix4() : mat.clone().multiply(o.matrix);
    if (o.isMesh && o.geometry) geoms.push([o.geometry, m]);
    for (const c of o.children) walk(c, m);
  };
  walk(root, new THREE.Matrix4());
  const P = [], N = [], A = [];
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const e1 = new THREE.Vector3(), e2 = new THREE.Vector3(), cr = new THREE.Vector3();
  const n0 = new THREE.Vector3(), nm = new THREE.Matrix3();
  for (const [geo, mat] of geoms) {
    const pos = geo.getAttribute && geo.getAttribute('position');
    if (!pos) continue;
    const nor = geo.getAttribute('normal') || null;
    const idx = geo.index;
    const count = idx ? idx.count : pos.count;
    nm.getNormalMatrix(mat);
    for (let t = 0; t + 2 < count; t += 3) {
      const corners = [idx ? idx.getX(t) : t, idx ? idx.getX(t + 1) : t + 1,
                       idx ? idx.getX(t + 2) : t + 2];
      const p3 = [a, b, c];
      for (let k = 0; k < 3; k++) {
        p3[k].fromBufferAttribute(pos, corners[k]).applyMatrix4(mat);
        p3[k].x = -p3[k].x;
      }
      e1.subVectors(b, a); e2.subVectors(c, a);
      const area = cr.crossVectors(e1, e2).length() * 0.5;
      for (let k = 0; k < 3; k++) P.push(p3[k].x, p3[k].y, p3[k].z);
      if (nor) {
        for (let k = 0; k < 3; k++) {
          n0.fromBufferAttribute(nor, corners[k]).applyMatrix3(nm);
          n0.x = -n0.x;
          if (n0.lengthSq() > 1e-30) n0.normalize();
          N.push(n0.x, n0.y, n0.z);
        }
      } else {
        // 网格没带法线时只剩面法线可用。产物里的网格都带法线,这一支没有被数据走到。
        cr.normalize();
        for (let k = 0; k < 3; k++) N.push(cr.x, cr.y, cr.z);
      }
      A.push(area);
    }
  }
  if (!A.length) return null;
  const cum = new Float64Array(A.length + 1);
  for (let i = 0; i < A.length; i++) cum[i + 1] = cum[i] + A[i];
  const total = cum[A.length];
  if (!(total > 0)) return null;           // 整张网格面积为零:按面积加权采不出东西
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < P.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      min[k] = Math.min(min[k], P[i + k]);
      max[k] = Math.max(max[k], P[i + k]);
    }
  }
  return { count: A.length, P: Float32Array.from(P), N: Float32Array.from(N),
           cum, total, min, max };
}

/** 按节点缓存的三角表。整张网格只整理一次,多个发射器共用同一张。 */
export function meshSamplerFor(root) {
  if (!root) return null;
  if (_meshSamplers.has(root)) return _meshSamplers.get(root);
  let s = null;
  try { s = buildMeshSampler(root); } catch (e) { s = null; }
  _meshSamplers.set(root, s);
  return s;
}

/** 在三角表上采一点与一条法线(律见本节开头的三条)。 */
function sampleMeshSurface(s, pos, dir) {
  // 面积加权:目标是「累计面积」轴上的一个均匀点,取第一个够到它的三角。
  const target = Math.random() * s.total;
  let lo = 0, hi = s.count - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (s.cum[mid + 1] >= target) hi = mid; else lo = mid + 1;
  }
  let u = Math.random(), v = Math.random();
  if (u + v > 1) { u = 1 - u; v = 1 - v; }
  const w = 1 - u - v, k = lo * 9;
  const P = s.P, N = s.N;
  pos.set(u * P[k] + v * P[k + 3] + w * P[k + 6],
          u * P[k + 1] + v * P[k + 4] + w * P[k + 7],
          u * P[k + 2] + v * P[k + 5] + w * P[k + 8]);
  dir.set(u * N[k] + v * N[k + 3] + w * N[k + 6],
          u * N[k + 1] + v * N[k + 4] + w * N[k + 7],
          u * N[k + 2] + v * N[k + 5] + w * N[k + 8]);
}

/** 形状自身的变换:pos' = R·(S·p) + t、dir' = R·(S·n)。缩放先于旋转,Euler 合成序 Z-X-Y。 */
function applyShapeTransform(shape, pos, dir) {
  if (shape?.scale) {
    const s = vec3(shape.scale);
    pos.multiply(s); if (dir) dir.multiply(s);
  }
  if (shape?.rotation) {
    const e = new THREE.Euler(num(shape.rotation[0]) * DEG, num(shape.rotation[1]) * DEG,
                              num(shape.rotation[2]) * DEG, 'YXZ');
    pos.applyEuler(e); if (dir) dir.applyEuler(e);
  }
  if (shape?.position) pos.add(vec3(shape.position));
}

/**
 * Sample a position and direction in shape-local space, then apply the
 * shape's own transform:
 *   pos' = R * (S * p) + t     dir' = normalize(R * (S * n))
 * Scale applies before rotation; Euler composition order is Z-X-Y
 * ('YXZ' in three.js terms). See docs/presentation.md for the per-shape
 * conventions.
 *
 * 返回 null = 这个形状没有发射公式(见 `shapeSupport`),调用方**不要**代它编一个点。
 *
 * `mesh` 是「从网格表面发射」用的三角表,由发射器在装配时从**已经预载好的**网格备好
 * (见 `meshSamplerFor`)。发射是同步的而网格是异步读的,所以这里只用现成的,不去取;
 * 该有而没有就整条不发(返回 null),由调用方计数。
 */
export function emitFrom(shape, mesh = null) {
  if (shapeSupport(shape) === 'unimplemented' && !EMIT_FAULT.pointEmit) {
    warnOnce(`shape:${shape.type}`,
      `发射形状 ${shape.type} 没有发射公式,该发射器停发(不退化成点发射:那会把粒子堆在节点原点)`);
    return null;
  }
  if (shape?.type === 'Mesh' && !mesh && !EMIT_FAULT.pointEmit) return null;
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
    case 'Hemisphere': {
      // Sphere with z restricted to the upper half: z = u, not 2u - 1. Everything
      // else — the polar ring, the cube-root radial, the direction being the
      // position radial — is the Sphere code unchanged.
      const z = Math.random();
      const a = Math.random() * (arc || Math.PI * 2);
      const ring = Math.sqrt(Math.max(0, 1 - z * z));
      dir.set(Math.cos(a) * ring, Math.sin(a) * ring, z);
      pos.copy(dir).multiplyScalar(sampleShapeRadius(radius, shape.radiusThickness, 3));
      break;
    }
    case 'ConeVolume': {
      // A filled base disc pushed along a **per-particle tilted** axis. Two things
      // separate it from Cone:
      //   * the radial fraction is area-uniform (sqrt), with **no** cube root, and
      //     it is the same `rho` that scales the direction's lateral part — an
      //     axis-born particle therefore flies straight up whatever the angle is.
      //     Reusing the Cone direction here makes every particle fly at the full
      //     cone angle.
      //   * `length` is travelled **along that tilted axis**, so it is a distance,
      //     not a height: pos.z < length whenever angle > 0.
      const A = num(shape.angle) * DEG;
      const k = Math.min(1, Math.max(0, 1 - num(shape.radiusThickness)));
      const rho = Math.sqrt(k + (1 - k) * Math.random());
      const a = Math.random() * (arc || Math.PI * 2);
      const h = Math.random() * num(shape.length);
      // The direction is stored un-normalized and normalized once for the push;
      // both consumers see the same vector, so normalize it here and keep going.
      dir.set(Math.sin(A) * rho * Math.cos(a), Math.sin(A) * rho * Math.sin(a), Math.cos(A));
      const n = dir.lengthSq() > 1e-30
        ? dir.clone().normalize() : new THREE.Vector3(0, 0, 1);
      pos.set(radius * rho * Math.cos(a) + n.x * h,
              radius * rho * Math.sin(a) + n.y * h,
              n.z * h);
      break;
    }
    case 'Donut': {
      // Torus: `radius` is the major radius, `donutRadius` the tube radius. The
      // tube radial fraction is a **plain linear** lerp — not sqrt, not cube root.
      // The direction is the outward tube-surface normal, which is neither +Z nor
      // normalize(pos), and its z spans [-1, 1] so some particles fly downward.
      const th = Math.min(1, Math.max(0, num(shape.radiusThickness)));
      const f = (1 - th) + th * Math.random();
      const r = num(shape.donutRadius) * f;
      const a = Math.random() * (arc || Math.PI * 2);
      const phi = Math.random() * Math.PI * 2;
      const cphi = Math.cos(phi), sphi = Math.sin(phi);
      const w = radius + r * cphi;
      pos.set(Math.cos(a) * w, Math.sin(a) * w, r * sphi);
      dir.set(cphi * Math.cos(a), cphi * Math.sin(a), sphi);
      break;
    }
    case 'SingleSidedEdge':
      // Position along the local X axis in [-radius, +radius] (radius is the
      // half-length); direction is always local +Y.
      pos.set((Math.random() * 2 - 1) * radius, 0, 0);
      dir.set(0, 1, 0);
      break;
    case 'Mesh': {
      // 面积加权选三角 → 三角内均匀取点 → 重心插值顶点法线当方向 → 沿法线推开
      // `meshNormalOffset`。三条律见本节开头。
      sampleMeshSurface(mesh, pos, dir);
      const off = num(shape.meshNormalOffset);
      if (off) pos.addScaledVector(dir, off);
      break;
    }
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
      // 到这里只有两种情形:没有形状模块(点发射就是它的语义),或者故障注入把未建模形状
      // 按点发射放了进来。两者都不喊 —— 前者不是错,后者是判据自己要红的那一路。
      break;
  }
  applyShapeTransform(shape, pos, dir);
  // A zero scale axis can collapse the direction to a zero vector. The engine's
  // normalize has one fallback for that case and it is local +Z, not "no
  // velocity" — reached by real data (shapes whose scale.z is 0).
  if (dir.lengthSq() > 1e-30) dir.normalize(); else dir.set(0, 0, 1);
  return { pos, dir };
}

// ---- 粒子发射器 ----------------------------------------------------------

const GRAVITY = -9.81;
const MIN_PARTICLE_SCALE = 0.0001;     // 零缩放的 sprite 会被剔掉,给一个下限
const TMP_V3 = new THREE.Vector3();    // 屏幕占比截断算深度用的临时向量(每帧每粒子,别新建)
const _subQ = new THREE.Quaternion();  // 子发射:只取发射节点的世界旋转
const _birthP = new THREE.Vector3();   // 出生点统计复用
const _centerP = new THREE.Vector3();

class Emitter {
  /**
   * @param spec `particles[]` 的一项
   * @param localParent 跟随锚点的父节点(Local 空间)
   * @param worldParent 不跟随锚点的父节点(World 空间 / keepPosition)
   */
  /**
   * 贴图数组的选层。返回该粒子要用的那一层贴图(没有数组时返回 null)。
   *
   * 采层律(现象域已钉死):
   *   layer = min(layers-1, max(0, floor(fract(clamp(source + progress, 0, 0.999000013)) * sliceCount)))
   * `source` 由 `progressCoord` 的打包选择器指出:`component*10 + vector`,
   * 例如 11 = custom1.x。选择器为常量(或指不到)时,全体粒子同层。
   * **层在出生时定死、终生不翻页** —— 所以这里只在出生时调一次。
   */
  /**
   * 自定义数据里某个向量某个分量在归一化年龄 `u` 处的取值。
   *
   * 模块声明成向量时,四个分量各是一条独立的取值曲线;声明成颜色或整个关掉时,
   * 这里没有可取的分量 —— 取不到就是取不到,返回 0 并数出来,不拿别的量顶替。
   */
  /**
   * 这个发射器的材质染色乘数,或者 null(没开、或者这一种区域模式还没律)。
   */
  _tintMultiplier() {
    const material = this.renderer.material;
    if (!material || material.external) return null;
    const keywords = new Set(material.keywords || []);
    if (!keywords.has('_TINT_COLOR_ENABLED')) return null;
    const floats = material.floats || {};
    const area = Math.round(num(floats._TintAreaMode, 0));
    if (area !== TINT_AREA_ALL) { this.tintRefused = (this.tintRefused || 0) + 1; return null; }
    if (keywords.has('_TINT_MAP_ENABLED')) {
      // 染色贴图逐像素给出乘数,拿它的常量部分冒充等于画错。
      this.tintRefused = (this.tintRefused || 0) + 1;
      return null;
    }
    const colour = (material.colors || {})._TintColor;
    if (!Array.isArray(colour)) { this.tintRefused = (this.tintRefused || 0) + 1; return null; }
    const source = progressCoordSource(floats._TintBlendRateCoord);
    const vertex = source
      ? this._customValue(source.vector, source.component, 0, 0.5) : 0;
    const rate = Math.min(1, Math.max(0, vertex + num(floats._TintBlendRate, 0)));
    const mul = [0, 1, 2].map((i) => 1 + rate * (num(colour[i], 1) - 1));
    // 有些染色是高动态的:乘数可以到几十甚至几百,原版靠色调映射与泛光把它收回可显示
    // 范围,亮而不糊。这个示例是刻意的 gamma 直通管线,**没有那一步**,乘上去的结果只会
    // 在帧缓冲里削平成纯白 —— 流星就是这样从一道光变成一块白方片的。
    //
    // 所以这一档**不画染色**并数出来。理由不是「算术不对」,算术是对的;是这条管线表示
    // 不了它的结果,硬乘出来的画面比不乘更远离原版,而且看着像有东西。缺的是色调映射
    // 那条律,记在它头上;真要还原这一档,得先有那条律,不是在这里塞一个补偿系数。
    if (mul.some((v) => v > 1)) {
      this.tintHdrUnrepresented = (this.tintHdrUnrepresented || 0) + 1;
      return null;
    }
    this.tintApplied = (this.tintApplied || 0) + 1;
    return new THREE.Color(mul[0], mul[1], mul[2]);
  }

  _customValue(vector, component, u, rand) {
    const data = (this.system.customData || {})[vector];
    const index = 'xyzw'.indexOf(String(component));
    const list = data && data.mode === 'vector' ? (data.components || []) : null;
    const spec = list && index >= 0 ? list[index] : null;
    if (!spec) { this.customMisses = (this.customMisses || 0) + 1; return 0; }
    this.customReads = (this.customReads || 0) + 1;
    return num(sampleValue(spec, u, rand), 0);
  }

  _arrayLayer(rand) {
    if (!this.arraySlices) return null;
    const s = this.arraySampling || {};
    const layers = this.arraySlices.length;
    const slices = num(s.sliceCount, layers) || layers;
    const progress = num(s.progress, 0);
    const clampTo = num(s.progressClamp, 0.999000013);
    let source = 0;
    const src = s.progressSource || {};
    if (!src.constant && src.vector != null && src.component != null) {
      // 选择器指向 custom1/custom2 的某个分量,而那个分量是自定义数据模块按曲线
      // 定出来的,不是一个通用随机数 —— 拿粒子的随机因子顶替它,只有在那个分量
      // 恰好声明成 0 到 1 的均匀随机时才碰巧相同,别的声明(常量、曲线、两条曲线
      // 之间取随机)一律取错层。层在出生时定死,所以按出生那一刻求值。
      source = this._customValue(src.vector, src.component, 0, rand);
    }
    let v = source + progress;
    v = Math.min(Math.max(v, 0), clampTo);
    v = v - Math.floor(v);                       // fract
    const layer = Math.min(layers - 1, Math.max(0, Math.floor(v * slices)));
    return this.arraySlices[layer] || this.arraySlices[0] || null;
  }

  /**
   * 屏幕占比截断的比例因子。返回 1 表示不改尺寸。
   *
   * 真源语义:`maxParticleSize` / `minParticleSize` 是**视口占比**,
   * 换算基准是**视口在该深度处的世界宽度**(横向):
   *
   *   W(d) = 2 · d · aspect · tan(fovV / 2)
   *
   * 比较的是粒子的**全尺寸**(两轴取大者)对 `W(d)`,两轴再同乘同一个比例 ——
   * 各轴独立裁会把作者写的长宽比毁掉。
   *
   * 深度用**眼空间前向距离**。相机在背后或深度非有限时不截断(返回 1):
   * 那些粒子本来就不该出现在画面上,拿一个负数去算宽度只会得出镜像或 NaN。
   */
  _screenClampRatio(p, size) {
    const cam = this.camera;
    const r = this.renderer || {};
    const maxFrac = num(r.maxParticleSize, 0);
    const minFrac = num(r.minParticleSize, 0);
    if (!cam || !cam.isPerspectiveCamera || (maxFrac <= 0 && minFrac <= 0)) return 1;
    if (!(size > 1e-6)) return 1;
    p.sprite.getWorldPosition(TMP_V3);
    // 眼空间 z 在相机前方是负的,取负得前向距离。
    const depth = -TMP_V3.applyMatrix4(cam.matrixWorldInverse).z;
    if (!Number.isFinite(depth) || depth <= 1e-4) return 1;
    const width = 2 * depth * cam.aspect * Math.tan((cam.fov * Math.PI / 180) / 2);
    if (!Number.isFinite(width) || width <= 1e-6) return 1;
    let target = size;
    if (minFrac > 0) target = Math.max(target, minFrac * width);
    if (maxFrac > 0) target = Math.min(target, maxFrac * width);
    const ratio = target / size;
    return Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
  }

  constructor(spec, localParent, worldParent, textureFor, camera = null, resolveEmitter = null,
              meshFor = null) {
    this.nodePath = spec.node ?? null;             // 发射节点的相对路径(判据与面板按它认发射器)
    this.system = spec.system || {};
    this.renderer = spec.renderer || {};
    // World 空间的粒子生成后不跟随发射器,所以挂在世界父节点下 —— 但发射位置与方向
    // 仍以**发射节点当时的世界变换**为基准。少了这步变换,粒子就会从世界原点(地面)
    // 冒出来,而不是从头顶的发射节点冒出来。
    this.camera = camera;                          // 屏幕占比截断要读它的 fov/aspect
    this.textureFor = textureFor;                  // 出生时绘制件还要用它取贴图
    // 网格取用器,同步。glb 是异步加载的而绘制件是同步建的,所以这里只查已经预载好的
    // 缓存;查不到返回 null,绘制件据此整条不画并计数,**绝不用 billboard 冒充**。
    this.meshFor = meshFor || (() => null);
    // 被屏幕占比截断改过尺寸的粒子数(累计),给面板与判据看。
    this.clampedCount = 0;
    this.worldSpace = this.system.simulationSpace === 'World';
    this.node = localParent;                       // 发射节点(局部空间基准)
    this.parent = (this.worldSpace ? worldParent : localParent) || localParent;
    this.particles = [];
    this.age = 0;
    this.pending = 0;
    this.burstCursor = [];
    // 被触发的播放实例(子发射目标用),与自主播放共用 `_runEmission`。
    this.plays = [];
    this._auto = { age: 0, pending: 0, cursors: [], origin: null, follow: null };
    this.peak = 0;
    // 起始延迟进的是出生触发那道归一化闸,取常量那一支就够:全部系统里只有一个
    // 声明了非零延迟。
    this.startDelaySeconds = num(sampleValue(this.system.startDelay, 0, 0.5), 0);
    // 环形复用:系统不再让粒子活到寿命尽头就退场,而是拿新生的那颗**顶掉**环里的一位。
    // 随之改变的是发射能不能被「满了」挡住:声明的容量不再是发射的闸,缓冲翻倍后的
    // 大小才是。没有这一条,一个容量 1、循环、只靠一发 burst 的系统在我们这里发出第
    // 一颗之后就再也发不出第二颗,而原版是每轮都发、每轮顶掉一位。
    //
    // 顶替是**轮转**选的,不是挑最老的那颗;搬运是逐字段拷贝,新粒子用自己的年龄、
    // 位置与随机流,**不是接着上一颗往下跑**。
    //
    // 两处是推断,标出来:环的模数按未翻倍的容量取,游标每顶替一颗加一。这两个是彼此
    // 独立的未知,真源里都还没定死,别拿其中一条去推另一条。
    // 材质染色:采样出来的基础色要乘上一个乘数,乘数是 1 加上混合率乘以(染色 - 1),
    // 混合率是逐粒子流值加材质常量再夹到 [0,1]。整张图统一乘同一个数,所以这里在
    // 逐粒子材质的颜色上乘一次即可,与逐像素乘等价。
    //
    // 只做**全域**那一种。边缘染色要按边缘权重逐像素给,第三种区域模式的律还没取得
    // —— 两者都不画成全域,那会把「只在边上淡淡一层」画成整片变色,看着像有效果而
    // 其实是错的。不做就数出来。
    this.tint = this._tintMultiplier();
    this.ringMode = Math.round(num(this.system.ringBufferMode, 0));
    this.ringSize = Math.max(1, Math.round(num(this.system.maxParticles, 1)));
    this.ring = [];
    this.ringCursor = 0;
    this.ringEvicted = 0;
    this.ringRetired = 0;
    this.theoretical = emissionPlan(this.system);
    this.shapeType = (this.system.shape && this.system.shape.type) || null;
    this.shapeSupport = shapeSupport(this.system.shape);
    this.shapeGap = this.shapeType === 'Mesh' ? meshShapeGap(this.system.shape) : null;
    // 「从网格表面发射」要的三角表。**同步**从已经预载好的网格建 —— glb 是异步读的,
    // 而发射器是同步装配的,所以预载必须在挂载之前做完(见环境层的 loadPhenomenon)。
    // 建不出来就整条停发并计数:不拿别的网格顶替,也不退化成点发射。
    this.meshShape = null;
    if (this.shapeSupport === 'ok' && this.shapeType === 'Mesh') {
      const ref = this.system.shape.meshes[0];
      const src = this.meshFor(ref.file, ref.node);
      this.meshShape = meshSamplerFor(src);
      if (!this.meshShape) this.shapeGap = src ? 'meshEmpty' : 'meshMissing';
    }
    // 形状没建模 = 这一条**整个不发射**(理由见 shapeSupport)。不是静默的:
    // `placement()` 与消费方的面板/自检逐个把它数出来。
    this.suppressed = (this.shapeSupport === 'unimplemented'
      || (this.shapeType === 'Mesh' && !this.meshShape)) && !EMIT_FAULT.pointEmit;
    if (this.suppressed) {
      // 停发的发射器不会再走到 spawn,所以警告在这里喊(每种形状一次);计数在 placement()。
      warnOnce(`shape:${this.shapeType}:${this.shapeGap || ''}`,
        `发射形状 ${this.shapeType}${this.shapeGap ? `(${this.shapeGap})` : ''} 没有发射公式,`
        + '用它的发射器停发(不退化成点发射:那会把粒子堆在节点原点)');
    }
    // 形状自身的原点在**节点局部空间**里的位置(= shape.position,再按 spawn 的同一条
    // x 换手镜一次)。判据的参照点是它,不是节点原点:`shape.position` 是数据明写的偏移
    // (实测有一个发射器把边发射器整体下移 2 米),拿节点原点当参照会把这个合法偏移判成错。
    this.shapeCenter = new THREE.Vector3();
    if (this.system.shape && this.system.shape.position) {
      const t = vec3(this.system.shape.position);
      this.shapeCenter.set(-t.x, t.y, t.z);
    }
    this.birth = this._emptyBirth();

    const material = this.renderer.material;
    let file = null;
    let key = DEFAULT_BASE_MAP_KEY;
    // 贴图数组:层在**粒子出生时定死**、终生不翻页(见现象域的采层律),而每个粒子本来
    // 就各有一份材质 —— 所以直接把那一层的 PNG 当这颗粒子的贴图用,等价于采数组的那一层,
    // 不需要真的建一张数组纹理。数组住在 `material.textureArrays`,**不是** `textures`
    // （早先按 `textures[key + '2DArray']` 查,永远查不到 ⇒ 无贴图 ⇒ 白方块）。
    this.arraySlices = null;
    this.arraySampling = null;
    if (material && !material.external) {
      key = BASE_MAP_KEY[material.shader] || DEFAULT_BASE_MAP_KEY;
      file = material.textures?.[key] ?? null;
      const arr = material.textureArrays?.[`${key}2DArray`]
        || material.textureArrays?.[key] || null;
      // **一个绑定着的数组不代表这个材质在用数组。** 材质自己说了用哪一种,而两种
      // 槽位是**同时绑着**的:采样参数里 `arrayMode` 为假时,该采的是单图那一槽,
      // 数组只是躺在那里没被用。只看「有没有数组」就会逐粒子从 4 层里挑一层去画,
      // 而原版画的是同一张 —— 云会变成四种云随机混着出现,看着「有变化」而其实是错的。
      // 字段缺席不算表态,所以只在**显式为假**时才让开。
      const arrayMode = arr && (arr.sampling || {}).arrayMode;
      if (arr && arrayMode === false) {
        this.arrayModeOff = (this.arrayModeOff || 0) + 1;
      } else if (arr && Array.isArray(arr.files) && arr.files.length) {
        this.arraySlices = arr.files.map((f) => textureFor(f));
        this.arraySampling = arr.sampling || null;
      }
      // 只有基础贴图那一槽走上面这条路。材质上别的槽(过渡图、自发光图……)同样绑着
      // 数组,而这个 demo 一张都没读 —— 数出来,不然「基础槽处理对了」会被当成
      // 「数组都处理了」。未读就是未读,得有个数。
      this.baseMapKey = key;
      this.arrayUnread = Object.keys(material.textureArrays || {})
        .filter((k) => k !== `${key}2DArray` && k !== key).length;
    } else if (material?.external) {
      warnOnce('matext', '有粒子材质在别的包里(变体件复用主件材质),本 demo 未加载,退化成无贴图');
    }
    this.map = file ? textureFor(file) : null;
    // 既没有单张贴图、也没有数组 ⇒ 这个发射器画不出东西。**不画**并让上层数出来,
    // 而不是拿一个 `map: null` 的精灵去画 —— 那画出来就是白方块(默认贴图 RGB 全白、
    // 形状全在 alpha),看起来「有东西」而其实是缺失。
    this.hasTexture = !!(this.map || this.arraySlices);
    this.state = renderState(material);
    // 基础贴图的 `<名>_ST`:顶点侧算的是 `uv * ST.xy + ST.zw`。取值来自贴图槽自己的
    // 缩放/偏移对,不是 floats —— 缺了它,非 1 的平铺会整片取错贴图区域。
    // 缩放偏移要跟着**实际在采的那一槽**走。数组与单图两槽同时绑着,各自带一份
    // `_ST`,而它们不一定相同 —— 采数组却拿单图那份,就会把一个本不存在的平铺按到
    // 图上。语料里有两个发射器正是这样:远景闪电的单图槽声明了 8 倍横向平铺,而它
    // 实际采的是数组、数组那份是恒等。
    const stKey = this.arraySlices && material && !material.external
      && material.textureScaleOffset?.[`${key}2DArray`] ? `${key}2DArray` : key;
    const st = (material && !material.external && material.textureScaleOffset?.[stKey]) || null;
    this.uvScaleOffset = st
      ? [num(st[0], 1), num(st[1], 1), num(st[2], 0), num(st[3], 0)] : [1, 1, 0, 0];
    this.uvSlot = stKey;
    const sheet = this.system.textureSheet;
    this.tiles = sheet && (num(sheet.tilesX, 1) > 1 || num(sheet.tilesY, 1) > 1)
      ? { x: num(sheet.tilesX, 1), y: num(sheet.tilesY, 1), spec: sheet } : null;

    // 可选模块按注册表接进来。声明了而没有实现的模块**不会**被默默当成「没这一项」——
    // `moduleFaults` 与 `declaredModules` 把「资产里有」和「我们跑了」分开数,
    // 两个数不等就是缺口,面板上看得见。
    this.moduleFaults = [];
    // `resolveEmitter(path)` 按节点路径找同一包里的另一个发射器。必须是**惰性**的:
    // 发射器是逐个构造的,构造第一个时后面的还不存在,所以查表要推迟到调用时。
    // 有模块(子发射)要按序列化里的目标路径去触发**别的**发射器,没有它就只能
    // 在自己身上发,那是错的 —— 宁可解析不到并计数,不要发在错的发射器上。
    this.resolveEmitter = resolveEmitter || (() => null);
    this.hooks = makeHooks(this.system, this.renderer, {
      THREE, sampleValue, sampleColor, num, vec3,
      emitter: this, camera: this.camera, textureFor,
      resolveEmitter: (path) => this.resolveEmitter(path),
      onModuleFault: (name, why) => {
        this.moduleFaults.push(`${name}: ${why}`);
        warnOnce(`mod:${name}`, `可选模块 ${name} 构造失败,这条发射器按没有它跑:${why}`);
      },
    });
    this.hookNames = this.hooks.map((h) => h.__name);
  }

  /**
   * 贴图坐标的合成。片表动画先把四边形的 uv 映进某一格,`_ST` 再作用在映过的 uv 上:
   *   最终 = (uv / 格数 + 格偏移) * ST.xy + ST.zw
   * three.js 的 `repeat`/`offset` 算的是 `uv * repeat + offset`,所以
   *   repeat = ST.xy / 格数     offset = 格偏移 * ST.xy + ST.zw
   * 两者写成一处,免得片表推进时把 `_ST` 覆盖掉。
   */
  _applyUv(map, tileOffsetX = 0, tileOffsetY = 0) {
    if (!map) return;
    const [sx, sy, ox, oy] = this.uvScaleOffset;
    const tx = this.tiles ? this.tiles.x : 1, ty = this.tiles ? this.tiles.y : 1;
    map.repeat.set(sx / tx, sy / ty);
    map.offset.set(tileOffsetX * sx + ox, tileOffsetY * sy + oy);
  }

  _emptyBirth() {
    return { n: 0, radialMax: 0, horizMax: 0, radialSum: 0, yMin: Infinity, yMax: -Infinity, ySum: 0,
             xMin: Infinity, xMax: -Infinity, zMin: Infinity, zMax: -Infinity };
  }

  /**
   * 出生点的世界坐标统计(位置判据读它)。World 空间的 `pos` 已是世界坐标,Local 的还要
   * 过一次节点世界变换才能与形状原点的世界位置比。只统计**出生点**:出生后的运动(重力、
   * velocityOverLifetime)会把粒子带走几十米,拿在场粒子的散布去判形状会把两件事混起来。
   * 参照点是**形状原点**(节点世界变换作用在 shape.position 上),不是节点原点。
   */
  _recordBirth(pos) {
    const node = this.node;
    if (!node) return;
    _birthP.copy(pos);
    if (!this.worldSpace) _birthP.applyMatrix4(node.matrixWorld);
    _centerP.copy(this.shapeCenter).applyMatrix4(node.matrixWorld);
    const dx = _birthP.x - _centerP.x, dy = _birthP.y - _centerP.y, dz = _birthP.z - _centerP.z;
    const b = this.birth;
    b.n += 1;
    const radial = Math.sqrt(dx * dx + dy * dy + dz * dz);
    b.radialMax = Math.max(b.radialMax, radial);
    b.radialSum += radial;
    b.horizMax = Math.max(b.horizMax, Math.hypot(dx, dz));
    // 水平两轴各自的**区间**(相对形状原点)。只有最大半径分不开「铺满一片」与
    // 「绕着边缘一圈」,更分不开「铺对了地方」与「沿 x 镜像铺在对面」—— 区间能。
    b.xMin = Math.min(b.xMin, dx); b.xMax = Math.max(b.xMax, dx);
    b.zMin = Math.min(b.zMin, dz); b.zMax = Math.max(b.zMax, dz);
    b.yMin = Math.min(b.yMin, _birthP.y);
    b.yMax = Math.max(b.yMax, _birthP.y);
    b.ySum += _birthP.y;
  }

  /**
   * 一个发射器的位置判据读数。`births` 是累计出生点数,`radialMax` 是出生点到发射节点的
   * 最大距离(米,世界尺度) —— 散布判据比它与声明半径;`birthY` 与 `nodeY` 供高度判据。
   */
  placement() {
    const sh = this.system.shape || null;
    const b = this.birth;
    const em = this.system.emission;
    // 「会不会发射」按取值的**上界**判(r=1):判据要区分「零活粒子因为停发」与
    // 「零活粒子因为该发的没发」,不能把 rateOverDistance-only 这类本来就不发的算进去。
    const rateTop = em ? sampleValue(em.rateOverTime, 0, 1) : 0;
    const burstTop = em ? (em.bursts || []).reduce((n, x) => n + sampleValue(x.count, 0, 1), 0) : 0;
    let nodeY = null, centerY = null;
    if (this.node) {
      this.node.updateWorldMatrix(true, false);
      nodeY = +this.node.matrixWorld.elements[13].toFixed(4);
      centerY = +_centerP.copy(this.shapeCenter).applyMatrix4(this.node.matrixWorld).y.toFixed(4);
    }
    return {
      node: this.nodePath,
      space: this.worldSpace ? 'World' : 'Local',
      shape: this.shapeType,
      support: this.shapeSupport,
      suppressed: this.suppressed,
      emits: rateTop > 0 || burstTop > 0,
      radius: sh ? num(sh.radius) : null,
      // Donut 的外缘是 radius + donutRadius,ConeVolume 的粒子沿轴再走最多 length ——
      // 散布判据要拿得到这两项才能算出正确的期望范围,只给 radius 会把对的实现判成错。
      donutRadius: sh ? num(sh.donutRadius) : null,
      length: sh ? num(sh.length) : null,
      angle: sh ? num(sh.angle) : null,
      radiusThickness: sh ? num(sh.radiusThickness) : null,
      shapeScale: sh && sh.scale ? [num(sh.scale[0], 1), num(sh.scale[1], 1), num(sh.scale[2], 1)] : [1, 1, 1],
      live: this.particles.length,
      peak: this.peak,
      spawnedTotal: this.spawnedTotal || 0,
      births: b.n,
      radialMax: +b.radialMax.toFixed(4),
      radialMean: b.n ? +(b.radialSum / b.n).toFixed(4) : 0,
      horizMax: +b.horizMax.toFixed(4),
      birthY: b.n ? {
        min: +b.yMin.toFixed(4), max: +b.yMax.toFixed(4), mean: +(b.ySum / b.n).toFixed(4),
      } : null,
      nodeY,
      centerY,          // 形状原点的世界高度(= 节点世界变换作用在 shape.position 上)
      shapeGap: this.shapeGap || null,
      // 出生点水平两轴的区间(米,相对形状原点)。判据拿它与 `meshSpan` 比。
      birthSpan: b.n ? {
        x: [+b.xMin.toFixed(4), +b.xMax.toFixed(4)],
        z: [+b.zMin.toFixed(4), +b.zMax.toFixed(4)],
      } : null,
      // 从网格表面发射时,这张网格自己的水平包围盒 —— 同一条变换链算出来,同一个参照点。
      meshSpan: this._meshSpan(),
    };
  }

  /**
   * 「从网格表面发射」那张网格的包围盒,**走与出生点同一条变换链**(形状自身变换 →
   * x 换手 → 节点世界变换),再减掉形状原点的世界位置 —— 与 `_recordBirth` 的 dx/dz
   * 同一个参照。改 `spawn` 里那条链就要一起改这里。
   * 出生点铺没铺在网格上,比的就是这两个区间。
   */
  _meshSpan() {
    const s = this.meshShape;
    if (!s || !this.node) return null;
    this.node.updateWorldMatrix(true, false);
    const c = new THREE.Vector3().copy(this.shapeCenter).applyMatrix4(this.node.matrixWorld);
    const p = new THREE.Vector3();
    let xMin = Infinity, xMax = -Infinity, zMin = Infinity, zMax = -Infinity;
    for (let i = 0; i < 8; i++) {
      p.set(i & 1 ? s.max[0] : s.min[0], i & 2 ? s.max[1] : s.min[1], i & 4 ? s.max[2] : s.min[2]);
      applyShapeTransform(this.system.shape, p, null);
      p.x = -p.x;
      p.applyMatrix4(this.node.matrixWorld);
      xMin = Math.min(xMin, p.x - c.x); xMax = Math.max(xMax, p.x - c.x);
      zMin = Math.min(zMin, p.z - c.z); zMax = Math.max(zMax, p.z - c.z);
    }
    return {
      x: [+xMin.toFixed(4), +xMax.toFixed(4)], z: [+zMin.toFixed(4), +zMax.toFixed(4)],
      triangles: s.count, area: +s.total.toFixed(3),
    };
  }

  /**
   * 发一颗。`origin` 非空时是**世界空间的发射原点**,用于子发射:目标系统被父粒子
   * 驱动时,它是被搬到父粒子所在处再发射的,所以形状的局部偏移仍按本节点的**旋转**
   * 摆放,但平移来自父粒子而不是本节点。继承父粒子的颜色/尺寸等是另一回事,本方法
   * 不做 —— 那需要一条尚未从真源读出的合成律,见 `fx/sub-emitters.js`。
   */
  spawn(origin = null) {
    const s = this.system, start = s.start || {};
    const r = Math.random();
    const sampled = emitFrom(s.shape, this.meshShape);
    if (!sampled) return;                // 形状没建模:这一发不发(计数见 this.suppressed)
    const { pos, dir } = sampled;
    pos.x = -pos.x; dir.x = -dir.x;      // 同一节点内容的 M 换手(节点链共轭之外的另一半)
    if (this.node) {
      this.node.updateWorldMatrix(true, false);
      if (origin) {
        // 只取旋转:平移由父粒子给。用整条 matrixWorld 会把本节点的世界位置也加
        // 进去,水环就会在雨滴落点与发射节点之间的某处冒出来。
        this.node.getWorldQuaternion(_subQ);
        pos.applyQuaternion(_subQ).add(origin);
        dir.applyQuaternion(_subQ).normalize();
      } else if (this.worldSpace && !EMIT_FAULT.dropNodeTransform) {
        // 局部点/方向 → 世界。父链的旋转也一起吃掉(挂点在绑定姿态下带 90° 旋转,
        // 不转换的话件的 y 偏移会跑成世界 x 偏移)。
        pos.applyMatrix4(this.node.matrixWorld);
        dir.transformDirection(this.node.matrixWorld).normalize();
      }
      this._recordBirth(pos);
    }
    const life = Math.max(0.01, sampleValue(start.lifetime, 0, r));
    // 出生尺寸是**逐轴量**:`size3D` 为真时 `start.size` 只是 X 轴,Y 轴另有 `sizeY`
    // (雨滴就是这样:X 0.01~0.02 米、Y 0.1~0.8 米的一道细长条;当成各向同性的标量
    // 就会画成 1~2 厘米的小方块,远看等于没有雨)。
    // `sizeZ` 只对 Mesh 绘制模式有意义(billboard 是二维片,没有第三轴可缩),但它
    // **必须带进粒子记录**:26 个 Mesh 发射器导出了它,绘制件读不到就只能拿两轴去缩
    // 一个三维网格 —— 那是错的,而且错得看不出来。
    const sizeX = sampleValue(start.size, 0, r);
    const sizeY = start.size3D ? sampleValue(start.sizeY, 0, r) : sizeX;
    const sizeZ = start.size3D && start.sizeZ ? sampleValue(start.sizeZ, 0, r) : sizeX;
    const first = sampleColor(start.color, 0, r);
    if (this.tint) first.color.multiply(this.tint);
    // 每粒子独立材质:颜色、透明度、旋转、贴图帧都是逐粒子量。
    // 贴图对象要在两种情形下各自复制一份:片表动画逐粒子推进帧;`_ST` 非恒等时
    // 平铺/偏移是**这个材质**的,共享贴图对象会把它按到同一张图的所有使用者头上。
    const identityUv = this.uvScaleOffset[0] === 1 && this.uvScaleOffset[1] === 1
      && this.uvScaleOffset[2] === 0 && this.uvScaleOffset[3] === 0;
    // 数组贴图:出生时选层。层由 custom data 定,而粒子对象要到下面才建 —— 所以
    // 这里把随机因子直接传进去,**不要引用还不存在的粒子对象**。
    const base = this.arraySlices ? this._arrayLayer(r) : this.map;
    const map = base && (this.tiles || !identityUv) ? base.clone() : base;
    // **这张贴图是不是这颗粒子自己的**,下面建粒子时记进 `ownsMap`。原先靠
    // `p.map !== this.map` 反推,而数组发射器的 `this.map` 是 null ⇒ 共享的那一层
    // 会被当成克隆体 dispose 掉,下一颗粒子再用它就炸在渲染循环里(UI 能动、画面卡死)。
    const ownsMap = !!(map && map !== base);
    if (map && map !== base) {
      map.needsUpdate = true;
      this._applyUv(map);
    }
    const st = this.state;
    const spin0 = sampleValue(start.rotation, 0, r);
    // 绘制件按渲染器的绘制模式分派(朝相机的 billboard 是内建的那一支)。
    // 没有对应实现的绘制模式在挂载时就被挡掉了,所以这里拿到 null 只可能是
    // 材质/贴图这一侧出了问题 —— 那就不发这一颗,并数出来。
    const draw = makeDrawable(this.renderer, {
      THREE, num, textureFor: this.textureFor, camera: this.camera, emitter: this,
      material: this.renderer.material, state: st, applyZOffset,
      map: map || null, color: first.color, alpha: first.alpha, rotation: spin0,
      sortingOrder: num(this.renderer.sortingOrder),
      // 这颗粒子出生时抽的随机因子。Mesh 模式最多挂 4 个网格、**逐粒子随机选一个**,
      // 选择必须用这个因子 —— 每帧重抽会让同一颗粒子每帧换一个网格。
      r,
      // 网格取用器。**同步**返回已经预载好的 glTF 节点(取不到就是 null)——
      // 绘制件是在 spawn 里同步建的,而 glb 加载是异步的,所以加载必须在挂载之前
      // 就做完。谁负责预载见 environment.js 的 loadPhenomenon。
      meshFor: this.meshFor,
    });
    if (!draw) { this.drawFaults = (this.drawFaults || 0) + 1; return; }
    const sprite = draw.object;
    sprite.position.copy(pos);
    this.parent.add(sprite);
    const p = {
      sprite, draw, map, ownsMap, r, life, age: 0, sizeX, sizeY, sizeZ,
      pos: pos.clone(),
      vel: dir.multiplyScalar(sampleValue(start.speed, 0, r)),
      spin: spin0,
      gravity: sampleValue(start.gravityModifier, 0, r),
    };
    this.particles.push(p);
    // 累计发射数。存活数量说明不了「还在不在发」:一个容量 1 的系统,发一次就停和
    // 每轮顶替一次,存活数**都是** 1,差别全在这个累计量上。
    this.spawnedTotal = (this.spawnedTotal || 0) + 1;
    if (this.ringMode) this._ringPlace(p);
    for (const h of this.hooks) h.onSpawn?.(p);
  }

  /**
   * 把新生的这颗放进环里的下一位,并处置被它顶掉的那一颗。
   *
   * 模式 1:被顶掉的那颗**真的死了**,走的是和寿命到点一样的死亡路径,所以它的死亡
   * 子发射器会照放。模式 2:被顶掉的那颗**不死**,只是被换到环外,之后按自己的寿命
   * 正常老死,通常比被顶那一刻晚一帧以上。
   */
  _ringPlace(p) {
    const lane = this.ringCursor % this.ringSize;
    this.ringCursor += 1;
    const victim = this.ring[lane];
    this.ring[lane] = p;
    if (!victim || victim === p || victim.retired) return;
    if (this.ringMode === 1) {
      victim.retired = true;
      this.ringEvicted += 1;
      for (const h of this.hooks) h.onDeath?.(victim);
      this._drop(victim);
      const at = this.particles.indexOf(victim);
      if (at >= 0) this.particles.splice(at, 1);
    } else {
      // 换到环外:不动它的年龄、位置与随机流,它继续跑自己的一生。
      this.ringRetired += 1;
    }
  }

  /**
   * 某个模块还归内建那段代码管吗?
   *
   * 有几个模块内建了**部分**实现(只做了完整律的一小块)。等专门的模块文件接上来,
   * 内建那段就必须整段让位 —— 两边同时跑会叠两次(尺寸表算两遍 UV、速度钳两次),
   * 症状是画面「差一点」而不报错,最难查。反过来,模块还没接上时内建那段要继续跑,
   * 否则等于把能跑的换成不能跑的。所以以「有没有模块认领这个名字」为准。
   */
  _inlineOwns(name) { return !this.hookNames.includes(name); }

  update(dt) {
    // 单帧 dt 钳制:切件/加载卡顿会给出数百毫秒的帧间隔,新生粒子会被一帧推出老远,
    // 肉眼看就是「出生位置错了」。0.1 s 对应 10 fps 下限,正常帧率不受影响。
    dt = Math.min(dt, 0.1);
    const s = this.system, em = this.suppressed ? null : s.emission;
    this.age += dt * num(s.simulationSpeed, 1);
    // 环形模式下容量翻倍。翻倍是发射侧的上限,不是环的模数 —— 模式 2 顶出来的那些
    // 粒子不死,要有地方待到自己老死,翻出来的那一半就是给它们的。
    const cap = num(s.maxParticles, 1000) * (this.ringMode ? 2 : 1);
    // 子发射的目标系统**不自行播放**:它的发射完全由父粒子触发,而「触发」的意思是
    // 把这个系统搬到触发点、从它自己的 0 时刻**跑一遍它整套发射**(速率 + 全部 burst),
    // 不是只放 time=0 的那一发 —— 靠速率发射的目标一发都不会有 burst@0。
    // 少了这道闸,目标的 burst 会在挂载时自己放一次,与触发的那次叠成两份。
    if (em) {
      if (this.subEmitterDriven) {
        for (const play of this.plays) {
          if (play.done) continue;
          play.age += dt * num(s.simulationSpeed, 1);
          if (play.follow) play.origin.copy(play.follow.pos);
          // 出生触发每帧过三道闸,全部通过才发。父粒子还活着是第一道(它死时由
          // onDeath/onDrop 收场);第二道是父粒子的归一化年龄减去本系统自己的起始
          // 延迟要落在 [0,1);第三道是播放时间要小于上限,而上限对循环目标是无穷、
          // 对非循环目标是它自己的时长 —— 少了第三道,非循环目标会一直跟着父粒子发。
          const follow = play.follow;
          if (follow) {
            const life = num(follow.life, 0);
            const u = life > 0 ? (num(follow.age, 0) - this.startDelaySeconds) / life : 0;
            if (u < 0 || u >= 1) continue;
          }
          const limit = s.looping ? Infinity : num(s.duration, 1);
          if (play.age >= limit) { play.done = true; continue; }
          this._runEmission(em, cap, play, dt);
        }
        if (this.plays.length) this.plays = this.plays.filter((play) => !play.done);
      } else {
        this._auto.age = this.age;
        this._runEmission(em, cap, this._auto, dt);
        this.pending = this._auto.pending;
        this.burstCursor = this._auto.cursors;
      }
    }
    for (const h of this.hooks) h.onFrame?.(dt);
    const alive = [];
    for (const p of this.particles) {
      p.age += dt * num(s.simulationSpeed, 1);
      if (p.age >= p.life) {
        // 死亡事件必须在回收**之前**送出:回收之后粒子的位置与速度已经不可读,
        // 而按死亡触发的子发射要在死亡那一点的位置上发射。
        for (const h of this.hooks) h.onDeath?.(p);
        this._drop(p);
        continue;
      }
      const u = p.age / p.life;
      if (p.gravity) p.vel.y += GRAVITY * p.gravity * dt;
      // 内建的这一段只是**兜底**:模块在位时由模块负责,两边同时跑会钳两次。
      // 而且求值序上它站错了位置——引擎把速度钳制放在 velocityOverLifetime
      // **之后**(钳的是「状态速度 + 叠加值」的合成量),这里却在之前。
      // 模块要按真源把它放对,所以模块在位就整段让位。
      const limit = this._inlineOwns('limitVelocity') ? s.limitVelocity : null;
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
      // 积分**之前**的模块槽位 —— 引擎的 pre-simulation 块(噪声、力、速度钳制、
      // customData 都在这里)。它们改的是「这一帧用来位移的有效速度」,所以
      // _effVel 作为可变参数交出去,模块就地改它;要改的是有状态速度就改 p.vel。
      for (const h of this.hooks) h.onPreIntegrate?.(p, u, dt, _effVel);
      p.pos.addScaledVector(_effVel, dt);
      // 积分**之后**的模块槽位 —— 引擎的 post-simulation 块(碰撞、尾迹、子发射)。
      // 逐个模块该插在哪一步由引擎自己的求值序决定(见 fx/hooks.js 的说明)。
      for (const h of this.hooks) h.onUpdate?.(p, u, dt);
      p.sprite.position.copy(p.pos);

      // 尺寸也是逐轴量:sizeOverLifetime 的 `curve` 是 X 轴,`separateAxes` 为真时
      // Y 轴另有一条 `y` 曲线(否则两轴共用 X 的那条)。
      const sol = s.sizeOverLifetime;
      const kx = sol ? sampleValue(sol.curve, u, p.r) : 1;
      const ky = sol && sol.separateAxes && sol.y ? sampleValue(sol.y, u, p.r) : kx;
      // 屏幕占比截断。`maxParticleSize` / `minParticleSize` 是**视口占比**不是米
      // (1 = 整个视口宽,0.5 = 一半;0.5 正是运行时默认值)。两条容易写错的地方:
      //   * 比的是**全尺寸**对**视口全宽**(横向宽度,不是高、不是对角);
      //   * 两轴同乘一个比例(保持作者写的长宽比),不是各轴独立裁。
      // 求值序:此处已经过 startSize 与 sizeOverLifetime,截断落在半展与 pivot **之前**。
      // 豁免两条:Mesh 绘制模式完全不截断(它根本不走这条路,见挂载处);
      // 拉伸型 billboard 的拉伸在截断**之后**施加,所以拉伸轴可以超限。
      let sx = Math.max(Math.abs(p.sizeX * kx), MIN_PARTICLE_SCALE);
      let sy = Math.max(Math.abs(p.sizeY * ky), MIN_PARTICLE_SCALE);
      // Mesh 绘制模式豁免屏幕占比截断(它不走 billboard 那条路)。
      const ratio = p.draw.clampExempt ? 1 : this._screenClampRatio(p, Math.max(sx, sy));
      if (ratio !== 1) { sx *= ratio; sy *= ratio; this.clampedCount += 1; }
      // 第三轴只有 Mesh 绘制件用得上(billboard 是二维片)。`sizeOverLifetime` 在本域
      // 没有独立的 z 曲线,所以 Z 跟 X 那条走 —— 这是**当前数据下**的取法,
      // 出现独立 z 曲线时要按真源改,别默认它永远跟 X。
      p.draw.setScale(sx, sy, Math.max(Math.abs(p.sizeZ * kx), MIN_PARTICLE_SCALE));
      p.draw.orient(this.camera, _effVel);

      if (s.colorOverLifetime) {
        const c = sampleColor(s.colorOverLifetime, u, p.r);
        p.draw.material.color.copy(c.color);
        if (this.tint) p.draw.material.color.multiply(this.tint);
        p.draw.material.opacity = c.alpha;
      }
      if (s.rotationOverLifetime) {                    // 弧度每秒
        p.spin += sampleValue(s.rotationOverLifetime.curve, u, p.r) * dt;
        p.draw.setRotation(p.spin);
      }
      if (this.tiles && p.map && this._inlineOwns('textureSheet')) {
        const sheet = this.tiles.spec;
        const total = this.tiles.x * this.tiles.y;
        const frame = Math.min(total - 1, Math.max(0,
          Math.floor(sampleValue(sheet.frameOverTime, u, p.r) * total)));
        this._applyUv(p.map, (frame % this.tiles.x) / this.tiles.x,
                      1 - 1 / this.tiles.y - Math.floor(frame / this.tiles.x) / this.tiles.y);
      }
      alive.push(p);
    }
    this.particles = alive;
    this.peak = Math.max(this.peak, alive.length);
  }

  _drop(p) {
    // 模块先释放自己的东西,再拆粒子本体。模块可能挂了自建几何(尾迹的条带就是),
    // 那些不在 draw 里,父级不知道怎么收 —— 只能模块自己收。
    // 注意这里是**回收**钩子,和 onDeath 不是一回事:onDeath 只在寿命到点时发,
    // 是个可以触发子发射的事件;_drop 走的是所有回收路径(含 reset() 整批清场),
    // 所以释放资源必须挂在这里,挂在 onDeath 上会在 reset 时漏掉。
    for (const h of this.hooks) h.onDrop?.(p);
    p.sprite.removeFromParent();
    p.draw.dispose();                          // 材质由绘制件自己销毁(Mesh 件还有几何)
    if (p.map && p.ownsMap) p.map.dispose();   // 只销毁自己克隆的那份,共享贴图不动
  }

  /**
   * 一次发射循环。**自主播放与被触发播放共用这一段**,所以两者的律必然一致 ——
   * 各自的时钟、速率余数、burst 游标、发射原点都装在 `state` 里。
   */
  _runEmission(em, cap, state, dt) {
    // 循环系统的发射时钟在 duration 处回绕,burst 游标随之复位 —— 少了这一步,
    // 只靠 burst 发射的循环系统会在第一轮把游标推过 cycleCount,然后**永远沉默**:
    // 画面上是「加载时闪一下就没了」,而每个发射器都挂载成功、没有任何计数报错。
    const system = this.system;
    const duration = Math.max(0.0001, num(system.duration, 1));
    let age = state.age;
    if (system.looping && !state.follow) {
      const loop = Math.floor(age / duration);
      if (loop !== state.loop) { state.loop = loop; state.cursors = []; }
      age -= loop * duration;
    }
    state.pending += sampleValue(em.rateOverTime, 0, Math.random()) * dt;
    while (state.pending >= 1) {
      state.pending -= 1;
      if (this.particles.length < cap) this.spawn(state.origin);
    }
    for (const [bi, burst] of (em.bursts || []).entries()) {
      // cycleCount 0 = 无限循环(每 repeatInterval 重发一轮),非 0 = 固定轮数。
      const declared = num(burst.cycleCount, 1);
      const cycles = declared === 0 ? Infinity : Math.max(1, declared);
      const interval = Math.max(spanNum(burst.repeatInterval, 0.01), 0.01);
      let c = state.cursors[bi] || 0;
      while (c < cycles && age >= num(burst.time) + c * interval) {
        c += 1;
        if (Math.random() > num(burst.probability, 1)) continue;
        const count = Math.round(sampleValue(burst.count, 0, Math.random()));
        for (let k = 0; k < count && this.particles.length < cap; k++) this.spawn(state.origin);
      }
      state.cursors[bi] = c;
    }
  }

  /**
   * 播放一次这个系统,原点在 `origin`。`follow` 非空时原点每帧跟着那颗粒子走,
   * 直到调用方停掉它 —— 出生触发在 Unity 里就是挂在父粒子上活一辈子的。
   */
  playSub(origin, follow = null) {
    const play = { age: 0, pending: 0, cursors: [], done: false,
                   origin: origin.clone(), follow };
    this.plays.push(play);
    return play;
  }

  /** 停掉一次播放(父粒子死了,或整条被回收)。 */
  stopSub(play) { if (play) play.done = true; }

  /**
   * 死亡、碰撞、触发器这三种触发**不是播放**:它们只对目标的第一发 burst 记一次数,
   * 就地发那么多颗,然后结束。这条路径不读速率、不读 burst 的时刻/轮数/重发间隔、
   * 也不读目标循不循环 —— 所以一个 time 大于 0 的 burst 在死亡触发时立刻就发,
   * 而没有任何 burst 的目标在死亡触发下**一颗都不发**。
   *
   * 这条路径上读到的 burst 字段共四个,其中「发多少颗」是确定的,概率是否在内尚未
   * 定死;这里按读了处理,数目上的差别只在概率小于 1 的记录上。
   */
  emitBurstZero(origin) {
    const em = this.system.emission;
    const bursts = em && em.bursts;
    if (!bursts || !bursts.length) return 0;
    const burst = bursts[0];
    if (Math.random() > num(burst.probability, 1)) return 0;
    const cap = num(this.system.maxParticles, 1000);
    const count = Math.round(sampleValue(burst.count, 0, Math.random()));
    let made = 0;
    for (let k = 0; k < count && this.particles.length < cap; k++) {
      this.spawn(origin); made += 1;
    }
    return made;
  }

  reset() {
    for (const p of this.particles) this._drop(p);
    this.particles = [];
    this.age = 0;
    this.pending = 0;
    this.burstCursor = [];
    this.plays = [];
    this._auto = { age: 0, pending: 0, cursors: [], origin: null, follow: null };
    this.birth = this._emptyBirth();     // 判据读的是这一轮的出生点,不是上一轮的
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
    blendEquation: st.blendEquation,
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
    // **不画的发射器在这里就摘掉,不是靠 `visible` 兜。** 门只有两道,两道都是数据明写的:
    // 渲染器自己的 `enabled`,与发射节点(含祖先)的 `active`。ParticleSystem 组件
    // 没有 enabled 字段,所以再没有第三道。
    // 靠 `visible` 兜不住:World 空间的粒子挂在**世界父节点**下,发射节点关着也照样可见,
    // 于是原版从不显示的东西会被画出来(默认粒子贴图整张 RGB 全白、形状全在 alpha 里,
    // 画出来就是一片白方块)。
    this.skipped = {
      disabledRenderer: 0,
      inactiveNode: 0,
      unsupportedRenderer: 0,
      unsupportedRenderModes: {},
      // 第三类「不画」:材质压根没给出可用的基础贴图(既没有单张也没有数组)。
      // 这不是数据里的门,是**我们这侧解析不出来** —— 所以它必须单独计数,
      // 不能混进上面两道数据门,否则「原版本来不显示」和「我们没读出来」就分不清了。
      missingTexture: 0,
    };
    const chainActive = (obj) => {
      for (let p = obj; p && p !== this.root; p = p.parent) if (!p.visible) return false;
      return true;
    };
    this.emitters = [];
    // 节点路径 -> 发射器。子发射把目标写成同一包里的节点路径(与 particles[i].node
    // 同一套写法),要按它找到那个发射器实例才能在正确的发射器上发射。
    this.emitterByNode = new Map();
    for (const p of item.particles || []) {
      if (!p.system) continue;
      if (p.renderer && p.renderer.enabled === false) { this.skipped.disabledRenderer++; continue; }
      // 绘制模式有实现才挂。**没有实现的绘制模式整条不画**:拿朝相机的 billboard
      // 去画一个本该贴地的片或一个网格,会得到一块立着的方形布 —— 看着「有东西」
      // 而其实是错的,比缺失更难发现。哪些模式有实现由 fx/drawable.js 的注册表决定。
      // 而「认领了这个模式」不等于「这一条画得了」:实现可能只读出了一种朝向基,
      // 或者这一条缺它要的引用。所以在挂载时**逐条**问一次,拿到理由就整条摘掉并按
      // 理由计数 —— 否则每颗粒子都失败一次,原因被埋进 drawFaults 里看不出来。
      const renderMode = (p.renderer && p.renderer.renderMode) || 'Billboard';
      const why = drawableRejection(p.renderer, num);
      if (!drawableModes().has(renderMode) || why) {
        this.skipped.unsupportedRenderer++;
        const tag = drawableModes().has(renderMode) ? `${renderMode}.${why}` : renderMode;
        this.skipped.unsupportedRenderModes[tag] =
          (this.skipped.unsupportedRenderModes[tag] || 0) + 1;
        continue;
      }
      const local = this.byPath.get(p.node) || this.root;
      if (!chainActive(local)) { this.skipped.inactiveNode++; continue; }
      // 世界/局部父节点由 Emitter 按 simulationSpace 选择。keepPosition 只门控
      // billboard 旋转律,与挂父无关 —— World 空间的出生点已是世界坐标,
      // 挂回锚定树会被再变换一次,出生位置整体飞离角色。
      const emitter = new Emitter(p, local, opts.worldParent || this.root,
                                  textureFor, opts.camera || null,
                                  // 惰性:构造时后面的发射器还没建好,查表推迟到调用时。
                                  (path) => this.emitterByNode.get(path) || null,
                                  opts.meshFor || null);
      // 没有可用贴图就不画。拿 `map: null` 的精灵去画会得到纯白方块
      // (默认粒子贴图 RGB 全白、形状全在 alpha),看着像「有东西」而其实是缺失 ——
      // 宁可不画并数出来,也不要伪装。
      if (!emitter.hasTexture) { this.skipped.missingTexture++; continue; }
      this.emitters.push(emitter);
      if (emitter.nodePath) this.emitterByNode.set(emitter.nodePath, emitter);
    }
    // 标记子发射目标。要等全部发射器建完才做得了:目标可能排在引用者后面。
    for (const emitter of this.emitters) {
      for (const record of (emitter.system.subEmitters || [])) {
        const target = record.emitter && this.emitterByNode.get(record.emitter);
        if (target) target.subEmitterDriven = true;
      }
    }
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
      // 摘掉的发射器逐类数出来:渲染器 enabled=false 与发射节点 active=false 是两件不同的
      // 事,合成一个数就看不出是哪一道门关的。
      skippedRenderers: this.skipped.disabledRenderer,
      skippedInactive: this.skipped.inactiveNode,
      skippedUnsupportedRenderers: this.skipped.unsupportedRenderer,
      skippedMissingTexture: this.skipped.missingTexture,
      unsupportedRenderModes: { ...this.skipped.unsupportedRenderModes },
      // 屏幕占比截断改过尺寸的粒子累计数,以及有多少发射器压根没相机可用(那时不截断)。
      screenClamped: this.emitters.reduce((n, e) => n + (e.clampedCount || 0), 0),
      clampBlind: this.emitters.filter((e) => !e.camera).length,
      live: this.emitters.reduce((n, e) => n + e.particles.length, 0),
      peak: this.emitters.reduce((n, e) => Math.max(n, e.peak), 0),
      // 停发的发射器逐个数出来:形状没建模的那些**不画**,但绝不静默(见 shapeSupport)。
      suppressed: this.emitters.filter((e) => e.suppressed).length,
      suppressedShapes: this.emitters.reduce((m, e) => {
        if (e.suppressed) m[e.shapeType] = (m[e.shapeType] || 0) + 1;
        return m;
      }, {}),
      theoreticalBurst: this.emitters.reduce((n, e) => n + e.theoretical.burst, 0),
      theoreticalRate: this.emitters.reduce((n, e) => n + e.theoretical.rate, 0),
      theoreticalTotal: this.emitters.reduce((n, e) => n + e.theoretical.total, 0),
      // 「资产里声明了哪些可选模块」对「我们真的跑了哪些」。两个数不等就是缺口,
      // 不许合成一个数 —— 合起来就看不出是没实现还是没声明。
      declaredModules: this.emitters.reduce((m, e) => {
        for (const k of OPTIONAL_MODULES) if (e.system[k]) m[k] = (m[k] || 0) + 1;
        return m;
      }, {}),
      consumedModules: this.emitters.reduce((m, e) => {
        for (const n of e.hookNames || []) m[n] = (m[n] || 0) + 1;
        return m;
      }, {}),
      moduleFaults: this.emitters.flatMap((e) => e.moduleFaults || []),
      // 材质绑着数组、但材质自己说不用数组的次数。这一项不是错误,是**被正确让开的
      // 绑定** —— 数出来才看得见「我们没有把它当数组画」这件事真的发生了。
      arrayModeOff: this.emitters.reduce((n, e) => n + (e.arrayModeOff || 0), 0),
      arraySampled: this.emitters.reduce((n, e) => n + (e.arraySlices ? 1 : 0), 0),
      arrayUnread: this.emitters.reduce((n, e) => n + (e.arrayUnread || 0), 0),
      ringEmitters: this.emitters.filter((e) => e.ringMode).length,
      ringEvicted: this.emitters.reduce((n, e) => n + (e.ringEvicted || 0), 0),
      ringRetired: this.emitters.reduce((n, e) => n + (e.ringRetired || 0), 0),
      customReads: this.emitters.reduce((n, e) => n + (e.customReads || 0), 0),
      customMisses: this.emitters.reduce((n, e) => n + (e.customMisses || 0), 0),
      tintApplied: this.emitters.reduce((n, e) => n + (e.tintApplied || 0), 0),
      tintRefused: this.emitters.reduce((n, e) => n + (e.tintRefused || 0), 0),
      tintHdrUnrepresented: this.emitters.reduce(
        (n, e) => n + (e.tintHdrUnrepresented || 0), 0),
      drawFaults: this.emitters.reduce((n, e) => n + (e.drawFaults || 0), 0),
      // 模块自己报的数,按模块名归并到一处。判据要读的就是这里 —— 没有它,
      // 模块跑没跑、跑出什么数,外面一个字也看不到,「绿」就无从谈起。
      // 数值字段跨发射器相加(一条天气里同一个模块有几十个发射器,单个的数没意义);
      // 非数值原样留最后一个,只为看一眼形状。
      moduleReports: this.emitters.reduce((acc, e) => {
        for (const h of e.hooks || []) {
          if (!h.report) continue;
          let r = null;
          try { r = h.report(); } catch (err) { continue; }   // 报数出错不许拖垮面板
          if (!r || typeof r !== 'object') continue;
          const into = acc[h.__name] || (acc[h.__name] = {});
          for (const [k, v] of Object.entries(r)) {
            if (typeof v === 'number') into[k] = (into[k] || 0) + v;
            else into[k] = v;
          }
        }
        return acc;
      }, {}),
      registeredModules: registeredModules(),
      drawModes: [...drawableModes()],
    };
  }

  /** 逐发射器的位置判据读数(散布/高度判据与面板都读它)。 */
  placement() { return this.emitters.map((e) => e.placement()); }

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

/**
 * 复用入口:任何**与头顶件同一套编码**的粒子 prefab 都走这里,不要另抄一份发射器。
 * 现象环境的 `fx/effects.json` 就是同一套编码(`nodes[]` + `particles[]`,取值都带模式标签),
 * 只是没有 `clips` / `sprites` —— 没有片段时 view 起播即进入 `live`,由发射器自己推进。
 *
 * @param effect `{name, nodes, particles}`
 * @param opts   同 EmoticonView(`{anchor, worldParent, textureFor, textureBase}`)
 */
export function createParticleEffect(effect, opts = {}) {
  if (!effect || !effect.nodes) return null;
  return new EmoticonView({
    name: effect.name || '(effect)',
    viewKind: 'particle',              // 走与粒子件相同的换手与挂父规则
    nodes: effect.nodes,
    particles: effect.particles || [],
  }, opts);
}

export function makeSharedTextureLoader(base) { return makeTextureLoader(base); }

/**
 * Shared mesh loader for the Mesh draw mode: an async preload half and a
 * synchronous lookup half.
 *
 * The split is forced by the runtime, not chosen for convenience. A drawable is
 * built synchronously inside `spawn()`, while reading a glTF file is inherently
 * asynchronous. Without a preload there are only two other options: make
 * emission async, which rewrites the whole emitter chain, or draw a placeholder
 * and swap it later, which means deliberately drawing the wrong thing first.
 * Loading everything a phenomenon references before it mounts is the only choice
 * that never shows something untrue.
 *
 * `get` returns the named glTF node, or null when the file was never preloaded or
 * failed to load. A caller that gets null must skip and count; a mesh emitter has
 * no honest fallback, and a camera-facing quad standing in for a mesh reads as
 * "something is there" while being wrong.
 */
export function makeSharedMeshLoader(base) {
  const loader = new GLTFLoader();
  const cache = new Map();          // file -> glTF scene root, or null when it failed
  const failed = new Map();         // file -> reason, surfaced to the panel

  return {
    /** Load every file named, skipping ones already cached. Deduplicates. */
    async preload(files) {
      const want = [...new Set((files || []).filter(Boolean))].filter((f) => !cache.has(f));
      await Promise.all(want.map(async (file) => {
        try {
          const gltf = await loader.loadAsync(`${base}${file}`);
          cache.set(file, gltf.scene || null);
          if (!gltf.scene) failed.set(file, 'glTF carried no scene');
        } catch (e) {
          // A file that will not load is cached as a failure so it is attempted
          // once, not once per particle.
          cache.set(file, null);
          failed.set(file, String(e).slice(0, 120));
        }
      }));
      return this.stats();
    },

    /** Synchronous. null means "not available", never a substitute. */
    get(file, node) {
      const scene = file ? cache.get(file) : null;
      if (!scene) return null;
      return node ? (scene.getObjectByName(node) || null) : scene;
    },

    stats() {
      let ok = 0;
      for (const v of cache.values()) if (v) ok += 1;
      return { requested: cache.size, loaded: ok, failed: Object.fromEntries(failed) };
    },
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
