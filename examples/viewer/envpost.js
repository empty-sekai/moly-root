// envpost.js — 现象后处理链(自建 composer,零外链)
//
// 数据来自 phenomena/<现象>/postprocess.json:`components[]` 每项是 `{name, class, active,
// parameters}`,每个参数是 `{overrideState, value}`。两条纪律贯穿本文件:
//
//   1) `overrideState` 为 false = **该档案根本不设置这个参数**。丢掉这个标志就把「别管它」
//      变成了「强制成这个值」。本示例没有周围的体积栈可继承,所以未覆盖的参数取
//      DEFAULTS 表里的**组件自身默认值**,并逐个记进 status().inherited。
//   2) `active` 为 false = 该组件整体不生效。交叉淡化期间它按强度插到 0,不是硬切。
//
// 组件顺序是**档案自身的覆盖顺序**,不是渲染顺序。本文件用一条写死的链序(见 PIPELINE)。
//
// ## 颜色空间
//
// 本示例是 gamma 直通管线(renderer.outputColorSpace = LinearSRGBColorSpace,
// toneMapping = NoToneMapping),所以场景缓冲里存的就是屏幕上看到的 sRGB 编码值。
// 而 URP 的调色是**线性域**的算术:曝光是线性倍数,颜色滤镜取 Color.linear,
// 白平衡的 LMS 矩阵吃线性 RGB,饱和度的 Rec.709 亮度权重也只在线性域成立。
// 所以除标准泛光外的六支一律在同一段线性域里按序求值:
//
//     linear = sRGBToLinear(min(scene, 100))
//     linear = 扩散 → 粒子泛光 → 屏幕耀斑 → 白平衡 → 颜色调整 → 分离色调
//     scene' = linearToSRGB(linear)
//
// 六支全关时整段旁路,输出与不接这条链时逐位相同。
// 唯一留在 gamma 域的是分离色调内部的那一次 pow(±2.2):真源的注释写明它就是要在
// gamma 空间上做 soft light,所以那一段自己转过去、算完再转回来。
//
// 近似与未定项一律出现在 status():本文件不假装读懂了没读懂的参数。

import * as THREE from './three.module.min.js';

// 链序(渲染顺序,与档案里的组件顺序无关)
export const PIPELINE = ['MysekaiDiffusionVolume', 'MysekaiParticleBloomVolume', 'Bloom',
  'MysekaiFlareParaVolume', 'WhiteBalance', 'ColorAdjustments', 'SplitToning'];

// 每个组件的参数名单 + **未覆盖时取的组件自身默认值**。
// 对下面三个自定义组件,这里的值就是组件默认值(未覆盖 = 继承默认,不是「中性化」);
// 其余组件本示例仍用「不产生贡献」的中性值占位。
const DEFAULTS = {
  MysekaiDiffusionVolume: {
    intensity: 0, scatter: 0.7, contrast: 1, blendMode: 6, maxIterations: 5, bufferHeight: 540,
  },
  MysekaiParticleBloomVolume: {
    brightEnable: 0, threshold: 0.9, intensity: 0, scatter: 0.7, tint: [1, 1, 1, 1],
    clamp: 65472, dirtTexture: null, dirtIntensity: 0, fixedBufferHeight: 540,
    useAreaOverride: 0, overlayStrength: 0,
  },
  MysekaiFlareParaVolume: {
    isScreenFlareActive: 0, screenFlareIntensity: 1, screenFlareDirection: 45,
    screenFlareColor1: [1, 1, 1, 1], screenFlareColor2: [0, 0, 0, 0],
    screenFlareOffset1: 0, screenFlareOffset2: 0, screenFlareExponent: 1,
    isSunFlareActive: 0, sunFlareIntensity: 1,
    sunFlareColor1: [1, 1, 1, 1], sunFlareColor2: [0, 0, 0, 0],
    sunFlareOffset1: 0, sunFlareOffset2: 0, sunFlareExponent: 1,
  },
  Bloom: {
    skipIterations: 1, threshold: 1, intensity: 0, scatter: 0.7, clamp: 65472,
    tint: [1, 1, 1, 1], highQualityFiltering: 0, downscale: 0, maxIterations: 6,
    dirtTexture: null, dirtIntensity: 0,
  },
  ColorAdjustments: { postExposure: 0, contrast: 0, colorFilter: [1, 1, 1, 1], hueShift: 0, saturation: 0 },
  SplitToning: { shadows: [0.5, 0.5, 0.5, 1], highlights: [0.5, 0.5, 0.5, 1], balance: 0 },
  WhiteBalance: { temperature: 0, tint: 0 },
};

// 枚举型参数:交叉淡化时**不能线性插值**(6 与 18 之间没有「12」这个混合模式,
// 插出来的中间值会落进 default 分支,整支扩散在过渡期里静默失效)。按目标档案取值。
const DISCRETE = new Set(['MysekaiDiffusionVolume.blendMode']);

// 因语义未取证而**不参与像素**的支路。参数照样读进来、照样在 status() 里报告。
export const SUPPRESSED = {
  // 合成式与参数喂法都读出来了(见下面 _pyramid 与 COMPOSITE_FRAG 里的实现,代码留着),
  // 缺的是**输入**:这一支泛光的源不是整幅画面,而是「只画特效那一层」的独立缓冲。
  // 「哪些粒子系统属于那一层」由渲染器侧的层遮罩 + pass 标签决定,而产物里的
  // `effects.json` 逐粒子系统只导出了 `node / system / renderer`,**没有层字段**
  // (560 个粒子系统全数如此),所以本示例无法判断该把谁放进那个缓冲。
  // 退而用「所有环境粒子」当输入是实测过曝的:015_cloud 近白像素占比 0.0880。
  // 产物补上层字段(或等价的「进不进特效缓冲」标记)之后删掉这一行即可。
  MysekaiParticleBloomVolume:
    '合成式已实现,但它的输入是「只画特效层」的独立缓冲,而产物没有导出粒子系统的层归属;'
    + '拿所有环境粒子代替实测把 015_cloud 的近白像素推到 0.0880,所以这一支停掉并计数',
};

// 停掉并计数的支路:读不出来的不实现,也不用近似冒充。
export const SKIPPED = {
  'MysekaiFlareParaVolume.sunFlare':
    '太阳耀斑的 7 个参数与屏幕耀斑走同一条像素式,但它的方向向量来自「平行光方向经相机'
    + '逆变换后的 xy」,而该向量既没有归一化、驱动强度用的又是变换后 z 的单侧钳制 —— '
    + 'z 的符号约定在在手真源里读不出来(符号反了会让耀斑在太阳背对镜头时出现)。'
    + '15 条现象里只有 4 条开这一支;参数照旧读取并在 status() 报告,但不参与像素。',
};

// 语义未定死的参数:照样读进来、照样报告,但**不假装**它在驱动什么。
export const UNRESOLVED = {
  'MysekaiParticleBloomVolume.useAreaOverride': '区域限制未建模(本示例无站点区域)',
  'MysekaiParticleBloomVolume.dirtTexture': '镜头脏污贴图未在产物里出现(档案里恒为 null)',
  'Bloom.highQualityFiltering': '采样质量位:本链的模糊是半分辨率高斯,不分档',
  'Bloom.downscale': '起始降采样档位未建模(缓冲高度由 bufferHeight/fixedBufferHeight 决定)',
  'Bloom.skipIterations': '跳过的起始迭代数按「减少迭代轮数」近似',
};

// 与真源一致但**滤波核**做了替换的地方(不是律的近似,是核形状的近似,如实列出)。
export const APPROXIMATED = {
  'MysekaiParticleBloomVolume.downsample':
    '泛光金字塔的降采样在本链里是 13 taps box(与真源同族);真源的首级把预过滤与降采样'
    + '融进了同一趟,本链拆成两趟。',
};

const num = (v, d = 0) => (Number.isFinite(+v) ? +v : d);
const col = (v, d = [0, 0, 0, 0]) => (Array.isArray(v) ? [num(v[0], d[0]), num(v[1], d[1]), num(v[2], d[2]), num(v[3], d[3])] : d.slice());

/** sRGB 折线的 gamma→linear(逐通道),与引擎侧的同名换算同一条曲线。 */
export function gammaToLinear(v) {
  const x = num(v);
  if (x <= 0.04045) return x / 12.92;
  if (x < 1) return Math.pow((x + 0.055) / 1.055, 2.4);
  return Math.pow(x, 2.2);
}

/** Rec.709 亮度,用于泛光 tint 的亮度归一化。 */
export function luminance(c) {
  return c[0] * 0.2126729 + c[1] * 0.7151522 + c[2] * 0.0721750;
}

/**
 * 泛光 tint:逐通道 gamma→linear,再按自身亮度归一化(亮度 <= 0 时取白)。
 * 归一化保证 tint 只改色相/色度,不改总强度 —— 强度由 intensity 单独给。
 */
export function bloomTint(rgba) {
  const c = [gammaToLinear(rgba[0]), gammaToLinear(rgba[1]), gammaToLinear(rgba[2])];
  const l = luminance(c);
  if (!(l > 0)) return [1, 1, 1];
  return [c[0] / l, c[1] / l, c[2] / l];
}

/**
 * 把一份档案摊成 `{<class>: {active, params:{k:v}, inherited:[k]}}`。
 * 未覆盖的参数取 DEFAULTS,并把参数名记进 inherited(如实报告「档案没设它」)。
 * 带 `{name, file}` 形状的参数值原样保留 —— 这是给贴图类参数(LUT / dirt)留的接口。
 */
export function flattenProfile(doc) {
  const out = {};
  for (const c of (doc && doc.components) || []) {
    const cls = c.class || c.name;
    const defs = DEFAULTS[cls];
    const params = {}; const inherited = []; const textures = {};
    const src = c.parameters || {};
    const names = defs ? Object.keys(defs) : Object.keys(src);
    for (const k of names) {
      const p = src[k];
      const on = !!(p && p.overrideState);
      const v = on ? p.value : (defs ? defs[k] : null);
      if (!on) inherited.push(k);
      if (v && typeof v === 'object' && !Array.isArray(v) && (v.file || v.name)) textures[k] = v;
      params[k] = v;
    }
    // `class` 是索引用的类名;`name` 是资产里写的名字,两者可能不同。
    out[cls] = { class: cls, name: c.name || cls, active: c.active !== false,
                 params, inherited, textures };
  }
  return out;
}

const lerp = (a, b, t) => a + (b - a) * t;
const lerpArr = (a, b, t) => a.map((x, i) => lerp(num(x), num(b[i], num(x)), t));

/**
 * 两份摊平档案按进度 t 混合。**开关量按强度插值**(active/enable 参与,不做硬切);
 * **枚举量不插值**(见 DISCRETE),按目标档案取值。
 */
export function blendProfiles(a, b, t) {
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
  const out = {};
  for (const cls of keys) {
    const fa = (a && a[cls]) || null, fb = (b && b[cls]) || null;
    const base = fb || fa;
    const params = {};
    const names = new Set([...Object.keys((fa && fa.params) || {}), ...Object.keys((fb && fb.params) || {})]);
    for (const k of names) {
      const va = fa ? fa.params[k] : undefined, vb = fb ? fb.params[k] : undefined;
      const pick = vb === undefined ? va : vb;
      if (DISCRETE.has(`${cls}.${k}`)) {
        params[k] = pick;
      } else if (Array.isArray(pick)) {
        params[k] = (Array.isArray(va) && Array.isArray(vb)) ? lerpArr(va, vb, t) : col(pick);
      } else if (typeof pick === 'number') {
        params[k] = (typeof va === 'number' && typeof vb === 'number') ? lerp(va, vb, t) : num(pick);
      } else {
        params[k] = pick;
      }
    }
    // 组件级 active 也做强度插值:两侧只要有一侧开着,权重就在 0..1 之间移动。
    const wa = fa && fa.active ? 1 : 0, wb = fb && fb.active ? 1 : 0;
    out[cls] = {
      // `class` 必须一路带下来:表按类名查,混合时丢了它就等于表失效。
      class: cls, name: base.name, active: (wa || wb) > 0, weight: lerp(wa, wb, t),
      params, inherited: (fb || fa).inherited, textures: (fb || fa).textures,
    };
  }
  return out;
}

// ---- 全屏 pass 的公共部分 -------------------------------------------------

const QUAD_VERT = /* glsl */`
varying vec2 vUv;
void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }
`;

const COPY_FRAG = /* glsl */`
uniform sampler2D tSrc;
varying vec2 vUv;
void main() { gl_FragColor = vec4(texture2D(tSrc, vUv).rgb, 1.0); }
`;

// 泛光预过滤:软膝阈值 + 上限钳制,结果以 sqrt 编码存进金字塔
// (缓冲是 gamma 空间的,取回时平方即为线性值)。
const BLOOM_PRE_FRAG = /* glsl */`
uniform sampler2D tSrc;
uniform float uThreshold;
uniform float uKnee;
uniform float uClampMax;
varying vec2 vUv;
void main() {
  vec3 c = min(texture2D(tSrc, vUv).rgb, vec3(uClampMax));
  float br = max(c.r, max(c.g, c.b));
  float soft = clamp(br - uThreshold + uKnee, 0.0, 2.0 * uKnee);
  soft = soft * soft / (4.0 * uKnee + 1e-4);
  float w = max(soft, br - uThreshold) / max(br, 1e-4);
  gl_FragColor = vec4(sqrt(max(c * w, vec3(0.0))), 1.0);
}
`;

// 降采样:4 taps box(±1 纹素)。扩散金字塔用这一档。
const DOWN4_FRAG = /* glsl */`
uniform sampler2D tSrc;
uniform vec2 uTexel;
varying vec2 vUv;
void main() {
  vec3 s = texture2D(tSrc, vUv + uTexel * vec2(-1.0, -1.0)).rgb
         + texture2D(tSrc, vUv + uTexel * vec2( 1.0, -1.0)).rgb
         + texture2D(tSrc, vUv + uTexel * vec2(-1.0,  1.0)).rgb
         + texture2D(tSrc, vUv + uTexel * vec2( 1.0,  1.0)).rgb;
  gl_FragColor = vec4(s * 0.25, 1.0);
}
`;

// 降采样:13 taps box(内 4 taps 各 0.125,外 3x3 分四组 2x2 各 0.03125)。泛光金字塔用这一档。
const DOWN13_FRAG = /* glsl */`
uniform sampler2D tSrc;
uniform vec2 uTexel;
varying vec2 vUv;
vec3 T(vec2 o) { return texture2D(tSrc, vUv + uTexel * o).rgb; }
void main() {
  vec3 inner = T(vec2(-0.5, -0.5)) + T(vec2(0.5, -0.5)) + T(vec2(-0.5, 0.5)) + T(vec2(0.5, 0.5));
  vec3 c = T(vec2(0.0, 0.0));
  vec3 tl = T(vec2(-1.0, -1.0)), tc = T(vec2(0.0, -1.0)), tr = T(vec2(1.0, -1.0));
  vec3 ml = T(vec2(-1.0, 0.0)), mr = T(vec2(1.0, 0.0));
  vec3 bl = T(vec2(-1.0, 1.0)), bc = T(vec2(0.0, 1.0)), br = T(vec2(1.0, 1.0));
  vec3 outer = (tl + tc + ml + c) + (tc + tr + c + mr) + (ml + c + bl + bc) + (c + mr + bc + br);
  gl_FragColor = vec4(inner * 0.125 + outer * 0.03125, 1.0);
}
`;

// 上采样:低一级做 4 taps box,与本级按 uScatter 线性混合。
// uEncoded>0.5 时先平方解码、混完再开方回编码(泛光金字塔);扩散金字塔直接混。
const UP_FRAG = /* glsl */`
uniform sampler2D tHi;
uniform sampler2D tLow;
uniform vec2 uLowTexel;
uniform float uScatter;
uniform float uEncoded;
varying vec2 vUv;
void main() {
  vec3 lo = texture2D(tLow, vUv + uLowTexel * vec2(-1.0, -1.0)).rgb;
  vec3 l2 = texture2D(tLow, vUv + uLowTexel * vec2( 1.0, -1.0)).rgb;
  vec3 l3 = texture2D(tLow, vUv + uLowTexel * vec2(-1.0,  1.0)).rgb;
  vec3 l4 = texture2D(tLow, vUv + uLowTexel * vec2( 1.0,  1.0)).rgb;
  vec3 hi = texture2D(tHi, vUv).rgb;
  if (uEncoded > 0.5) {
    vec3 box = (lo * lo + l2 * l2 + l3 * l3 + l4 * l4) * 0.25;
    vec3 h = hi * hi;
    gl_FragColor = vec4(sqrt(max(mix(h, box, uScatter), vec3(0.0))), 1.0);
  } else {
    vec3 box = (lo + l2 + l3 + l4) * 0.25;
    gl_FragColor = vec4(mix(hi, box, uScatter), 1.0);
  }
}
`;

// 合成。六支(扩散 / 粒子泛光 / 屏幕耀斑 / 白平衡 / 颜色调整 / 分离色调)在同一段
// 线性空间里按序求值;只有标准泛光保持本示例原有的 gamma 域加算。
const COMPOSITE_FRAG = /* glsl */`
uniform sampler2D tScene;
uniform sampler2D tBloom;
uniform sampler2D tPBloom;
uniform sampler2D tDiff;

uniform float uBloomOn;      uniform float uBloomIntensity;  uniform vec3 uBloomTint;
uniform float uLinearOn;     // 三支全关时整段旁路
uniform float uPBloomOn;     uniform float uPBloomIntensity; uniform vec3 uPBloomTint;
uniform float uPBloomOverlay;
uniform float uDiffOn;       uniform float uDiffIntensity;   uniform float uDiffContrast;
uniform float uDiffBlendMode;

uniform float uFlareScreenOn; uniform float uFlareScreenI;
uniform vec2  uFlareScreenAxis;
uniform vec4 uFlareScreenC1;  uniform vec4 uFlareScreenC2;
uniform float uFlareScreenO1; uniform float uFlareScreenO2; uniform float uFlareScreenExp;

uniform float uWbOn;         uniform vec3 uWbCoeffs;
uniform float uCaOn;         uniform float uExposure;  uniform float uContrast;
uniform vec3 uColorFilter;   uniform float uHueShift;  uniform float uSaturation;
uniform float uStOn;         uniform vec3 uStShadows;  uniform vec3 uStHighlights;
uniform float uStBalance;
varying vec2 vUv;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const float MIDGRAY = 0.4135884;    // ACEScc 中灰:URP 的对比度枢轴,在**对数域**上取
const float DIFF_PIVOT = 0.217637643;   // pow(0.5, 2.2):扩散对比度的枢轴

vec3 sRGBToLinear(vec3 c) {
  vec3 hi = pow(max(c + 0.055, vec3(0.0)) * (1.0 / 1.055), vec3(2.4));
  return mix(hi, c * (1.0 / 12.92), step(c, vec3(0.04045)));
}
vec3 linearToSRGB(vec3 c) {
  vec3 hi = pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) * 1.055 - 0.055;
  return mix(hi, c * 12.92, step(c, vec3(0.0031308)));
}
// s <= 0.5 -> 2*b*s ; s > 0.5 -> 1 - 2*(1-b)*(1-s)
vec3 hardLight(vec3 b, vec3 s) {
  return mix(2.0 * b * s, 1.0 - 2.0 * (1.0 - b) * (1.0 - s), step(0.5, s));
}
// 与 hardLight 同式,判别位换成 base
vec3 overlay(vec3 b, vec3 s) {
  return mix(2.0 * b * s, 1.0 - 2.0 * (1.0 - b) * (1.0 - s), step(0.5, b));
}
vec3 softLightBlend(vec3 b, vec3 s) {
  return mix(2.0 * b * s + b * b * (1.0 - 2.0 * s),
             2.0 * b * (1.0 - s) + sqrt(max(b, vec3(0.0))) * (2.0 * s - 1.0),
             step(0.5, s));
}
float powSafe(float x, float e) { return x <= 0.0 ? 0.0 : pow(x, e); }

vec3 rgb2hsv(vec3 c) {
  vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
  vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
  vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
  float d = q.x - min(q.w, q.y);
  return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + 1e-10)), d / (q.x + 1e-10), q.x);
}
vec3 hsv2rgb(vec3 c) {
  vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}
vec3 pow3(vec3 v, float e) { return pow(max(v, vec3(0.0)), vec3(e)); }

// LogC(EI800)对数曲线。URP 的对比度不在线性域也不在 gamma 域上做:它先把线性值
// 送进这条对数曲线,绕 ACEScc 中灰(MIDGRAY)缩放,再折回线性。未接色调映射组件时
// 走的就是这一支(接了 ACES 的那一支是 ACEScc,本示例的档案里没有该组件)。
const float LOGC_A = 5.555556, LOGC_B = 0.047996, LOGC_C = 0.244161, LOGC_D = 0.386036;
const float LOG10 = 0.4342944819;   // 1 / ln(10):GLSL 只有自然对数
vec3 linearToLogC(vec3 x) {
  return LOGC_C * (log(LOGC_A * max(x, vec3(0.0)) + LOGC_B) * LOG10) + LOGC_D;
}
vec3 logCToLinear(vec3 x) {
  return (pow(vec3(10.0), (x - LOGC_D) / LOGC_C) - LOGC_B) / LOGC_A;
}

void main() {
  vec3 c = texture2D(tScene, vUv).rgb;

  // 标准泛光:保持本示例原有的 gamma 域加算
  c += texture2D(tBloom, vUv).rgb * uBloomTint * uBloomIntensity * uBloomOn;

  // 六支共用同一段线性域:调色的算术(线性倍数 / LMS 矩阵 / 亮度权重)只在这里成立。
  // 全关时整段旁路,输出与不接这条链时逐位相同。
  if (max(max(uLinearOn, uWbOn), max(uCaOn, uStOn)) > 0.5) {
    vec3 b = sRGBToLinear(min(c, vec3(100.0)));

    if (uLinearOn > 0.5) {
      // 1) 扩散:整幅模糊层提对比后按混合模式并入,强度即混合权重
      if (uDiffOn > 0.5) {
        vec3 d = texture2D(tDiff, vUv).rgb;
        d = clamp((d - DIFF_PIVOT) * uDiffContrast + DIFF_PIVOT, 0.0, 1.0);
        vec3 s = b;
        if (uDiffBlendMode < 8.0)       s = hardLight(b, d);        // 6
        else if (uDiffBlendMode < 12.0) s = b + d;                  // 10 线性减淡
        else if (uDiffBlendMode < 16.0) s = overlay(b, d);          // 15
        else if (uDiffBlendMode < 20.0) s = softLightBlend(b, d);   // 18
        b = mix(b, s, uDiffIntensity);
      }

      // 2) 粒子泛光:金字塔来自「只有粒子」的缓冲,平方解码后按 tint/强度加算;
      //    叠加层是同一份泛光先加进去再做 overlay,权重为 overlayStrength。
      if (uPBloomOn > 0.5) {
        vec3 t = texture2D(tPBloom, vUv).rgb;
        vec3 tinted = (t * t) * uPBloomIntensity * uPBloomTint;
        vec3 ov = overlay(b, b + tinted);
        b = max(b + uPBloomOverlay * (ov - b), vec3(0.0));
        b = b + tinted;
      }

      // 3) 屏幕耀斑:沿 uFlareScreenAxis 的两条反向线性渐变,各自 hardLight 混一层色
      if (uFlareScreenOn > 0.5) {
        float t = dot(vUv - 0.5, uFlareScreenAxis);
        float e = uFlareScreenExp;
        float w1 = powSafe(clamp(t - uFlareScreenO1, 0.0, 1.0), e) * uFlareScreenC1.a * uFlareScreenI;
        float w2 = powSafe(clamp((1.0 - t) - uFlareScreenO2, 0.0, 1.0), e) * uFlareScreenC2.a * uFlareScreenI;
        b = mix(b, hardLight(b, uFlareScreenC1.rgb), w1);
        b = mix(b, hardLight(b, uFlareScreenC2.rgb), w2);
      }
    }

    // 4) 白平衡:LMS 空间逐通道缩放(系数在 JS 侧按公开的 URP 公式算好)。
    //    那两个矩阵的入口是**线性** RGB。
    if (uWbOn > 0.5) {
      mat3 toLms = mat3(3.90405e-1, 7.08416e-2, 2.31082e-2,
                        5.49941e-1, 9.63172e-1, 1.28021e-1,
                        8.92632e-3, 1.35775e-3, 9.36245e-1);
      mat3 fromLms = mat3(2.85847e+0, -2.10182e-1, -4.18120e-2,
                          -1.62879e+0, 1.15820e+0, -1.18169e-1,
                          -2.48910e-2, 3.24281e-4, 1.06867e+0);
      b = fromLms * ((toLms * b) * uWbCoeffs);
    }
    // 5) 颜色调整:曝光 → 对比度(对数域,绕中灰)→ 颜色滤镜 → 色相 → 饱和度。
    //    曝光是 2^EV 的**线性**倍数,颜色滤镜是 Color.linear,两者都在这里相乘;
    //    对比度在 LogC 上绕 MIDGRAY 缩放,不是在线性值上减中灰。
    if (uCaOn > 0.5) {
      b *= uExposure;
      if (abs(uContrast - 1.0) > 1e-6) {
        vec3 lg = linearToLogC(b);
        lg = (lg - MIDGRAY) * uContrast + MIDGRAY;
        b = logCToLinear(lg);
      }
      b *= uColorFilter;
      b = max(b, vec3(0.0));       // 后面几步不接受负值
      if (abs(uHueShift) > 1e-6) {
        vec3 hsv = rgb2hsv(b);
        hsv.x = fract(hsv.x + uHueShift);
        b = hsv2rgb(hsv);
      }
      float l = dot(max(b, vec3(0.0)), LUMA);
      b = l + (b - l) * uSaturation;
    }
    // 6) 分离色调:亮部/暗部各自 soft light,balance 移动分界。
    //    这一支**故意**转到 gamma 域再算(真源如此),算完转回线性。
    if (uStOn > 0.5) {
      vec3 g = pow3(max(b, vec3(0.0)), 1.0 / 2.2);
      float l = clamp(dot(clamp(g, 0.0, 1.0), LUMA) + uStBalance, 0.0, 1.0);
      g = softLightBlend(g, mix(vec3(0.5), uStShadows, 1.0 - l));
      g = softLightBlend(g, mix(vec3(0.5), uStHighlights, l));
      b = pow3(g, 2.2);
    }

    c = linearToSRGB(max(b, vec3(0.0)));
  }

  gl_FragColor = vec4(max(c, vec3(0.0)), 1.0);
}
`;

function fullscreen(uniforms, fragmentShader) {
  return new THREE.ShaderMaterial({
    uniforms, vertexShader: QUAD_VERT, fragmentShader,
    depthTest: false, depthWrite: false, blending: THREE.NoBlending,
  });
}

/** 白平衡系数:公开的 URP 白平衡公式(色温/色调 → LMS 逐通道缩放)。 */
export function whiteBalanceCoeffs(temperature, tint) {
  const t1 = num(temperature) / 65, t2 = num(tint) / 65;
  const x = 0.31271 - t1 * (t1 < 0 ? 0.1 : 0.05);
  const y = 2.87 * x - 3 * x * x - 0.27509507 + t2 * 0.05;
  const toLms = (X, Y, Z) => [
    0.390405 * X + 0.549941 * Y + 0.00892632 * Z,
    0.0708416 * X + 0.963172 * Y + 0.00135775 * Z,
    0.0231082 * X + 0.128021 * Y + 0.936245 * Z,
  ];
  const w1 = toLms(0.949237, 1.03542, 1.08728);   // D65 白点(LMS)
  const Y = 1, X = Y * x / y, Z = Y * (1 - x - y) / y;
  const w2 = toLms(X, Y, Z);
  return [w1[0] / w2[0], w1[1] / w2[1], w1[2] / w2[2]];
}

// ---- 金字塔 --------------------------------------------------------------

/**
 * 双向金字塔:`down[0..n-1]` 逐级减半,`up[0..n-2]` 回上采样。
 * 与自定义扩散/泛光两支的缓冲布局一致(结果落在 up[0],单级时落在 down[0])。
 */
class Pyramid {
  constructor() { this.down = []; this.up = []; this.w = 0; this.h = 0; this.n = 0; }

  ensure(w, h, n) {
    if (this.w === w && this.h === h && this.n === n) return;
    this.dispose();
    this.down = []; this.up = [];
    const opts = { type: THREE.HalfFloatType, depthBuffer: false, stencilBuffer: false };
    let tw = w, th = h;
    for (let i = 0; i < n; i++) {
      const a = new THREE.WebGLRenderTarget(tw, th, opts);
      a.texture.colorSpace = THREE.NoColorSpace;
      this.down.push({ rt: a, w: tw, h: th });
      if (i < n - 1) {
        const b = new THREE.WebGLRenderTarget(tw, th, opts);
        b.texture.colorSpace = THREE.NoColorSpace;
        this.up.push({ rt: b, w: tw, h: th });
      }
      tw = Math.max(1, tw >> 1); th = Math.max(1, th >> 1);
    }
    this.w = w; this.h = h; this.n = n;
  }

  dispose() {
    for (const l of this.down) l.rt.dispose();
    for (const l of this.up) l.rt.dispose();
    this.down = []; this.up = []; this.w = 0; this.h = 0; this.n = 0;
  }
}

// ---- 后处理链 ------------------------------------------------------------

export class PostChain {
  /**
   * @param renderer three.js renderer
   * @param opts `{blackTexture}` —— 未启用的分支采样它(全黑 ⇒ 零贡献)
   */
  constructor(renderer, opts = {}) {
    this.renderer = renderer;
    this.quadScene = new THREE.Scene();
    this.quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), null);
    this.quad.frustumCulled = false;
    this.quadScene.add(this.quad);

    this.black = opts.blackTexture || (() => {
      const t = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1, THREE.RGBAFormat);
      t.colorSpace = THREE.NoColorSpace; t.needsUpdate = true;
      return t;
    })();

    this.uCopy = { tSrc: { value: null } };
    this.uPre = {
      tSrc: { value: null }, uThreshold: { value: 0 }, uKnee: { value: 0 },
      uClampMax: { value: 65472 },
    };
    this.uDown = { tSrc: { value: null }, uTexel: { value: new THREE.Vector2() } };
    this.uUp = {
      tHi: { value: null }, tLow: { value: null }, uLowTexel: { value: new THREE.Vector2() },
      uScatter: { value: 0.5 }, uEncoded: { value: 0 },
    };
    this.mCopy = fullscreen(this.uCopy, COPY_FRAG);
    this.mPre = fullscreen(this.uPre, BLOOM_PRE_FRAG);
    this.mDown4 = fullscreen(this.uDown, DOWN4_FRAG);
    this.mDown13 = fullscreen(this.uDown, DOWN13_FRAG);
    this.mUp = fullscreen(this.uUp, UP_FRAG);

    this.uComp = {
      tScene: { value: null }, tBloom: { value: this.black }, tPBloom: { value: this.black },
      tDiff: { value: this.black },
      uBloomOn: { value: 0 }, uBloomIntensity: { value: 0 }, uBloomTint: { value: new THREE.Vector3(1, 1, 1) },
      uLinearOn: { value: 0 },
      uPBloomOn: { value: 0 }, uPBloomIntensity: { value: 0 }, uPBloomTint: { value: new THREE.Vector3(1, 1, 1) },
      uPBloomOverlay: { value: 0 },
      uDiffOn: { value: 0 }, uDiffIntensity: { value: 0 }, uDiffContrast: { value: 1 },
      uDiffBlendMode: { value: 6 },
      uFlareScreenOn: { value: 0 }, uFlareScreenI: { value: 0 },
      uFlareScreenAxis: { value: new THREE.Vector2(0, 1) },
      uFlareScreenC1: { value: new THREE.Vector4() }, uFlareScreenC2: { value: new THREE.Vector4() },
      uFlareScreenO1: { value: 0 }, uFlareScreenO2: { value: 0 }, uFlareScreenExp: { value: 1 },
      uWbOn: { value: 0 }, uWbCoeffs: { value: new THREE.Vector3(1, 1, 1) },
      uCaOn: { value: 0 }, uExposure: { value: 1 }, uContrast: { value: 1 },
      uColorFilter: { value: new THREE.Vector3(1, 1, 1) }, uHueShift: { value: 0 }, uSaturation: { value: 1 },
      uStOn: { value: 0 }, uStShadows: { value: new THREE.Vector3(0.5, 0.5, 0.5) },
      uStHighlights: { value: new THREE.Vector3(0.5, 0.5, 0.5) }, uStBalance: { value: 0 },
    };
    this.mComp = fullscreen(this.uComp, COMPOSITE_FRAG);

    this.rtScene = null; this.rtParticles = null;
    this.pyBloom = new Pyramid();
    this.pyPBloom = new Pyramid();
    this.pyDiff = new Pyramid();
    this.size = new THREE.Vector2(1, 1);
    this.profile = null;
    this.enabled = true;
    this.lut = null;                 // LUT 接口:产物出现带 file 的贴图参数时接上
    this.lutNote = 'no LUT component in the profiles';
    this.stats = { passes: 0, particlePass: false, lastPasses: [], skipped: {} };
  }

  setEnabled(on) { this.enabled = !!on; }

  /** 摊平并混合好的档案(blendProfiles 的产物)。 */
  setProfile(profile) {
    // 没经过混合的档案(第一次选中一个现象、或直达某个现象)**没有 `weight` 字段** ——
    // `flattenProfile` 不产它,只有 `blendProfiles` 产。而下面的 `on()` 判的是
    // `weight > 1e-4`,`undefined > 1e-4` 为假,于是整条链会静默地一个组件都不生效。
    // 「没有权重」= 全强度,与雾那一路同一个约定。
    if (!profile) { this.profile = profile; return; }
    const out = {};
    for (const key of Object.keys(profile)) {
      const c = profile[key];
      out[key] = (c && c.weight === undefined) ? { ...c, weight: 1 } : c;
    }
    this.profile = out;
  }

  _rt(w, h) {
    const t = new THREE.WebGLRenderTarget(Math.max(1, w | 0), Math.max(1, h | 0), {
      type: THREE.HalfFloatType, depthBuffer: true, stencilBuffer: true,
    });
    t.texture.colorSpace = THREE.NoColorSpace;
    return t;
  }

  setSize(w, h) {
    if (this.size.x === w && this.size.y === h) return;
    this.size.set(w, h);
    if (this.rtScene) this.rtScene.dispose();
    this.rtScene = this._rt(w, h);
    if (this.rtParticles) { this.rtParticles.dispose(); this.rtParticles = null; }
  }

  _blit(material, target) {
    this.quad.material = material;
    this.renderer.setRenderTarget(target || null);
    this.renderer.render(this.quadScene, this.quadCam);
  }

  /** 金字塔的层级布局:高度给定,宽度按画面宽高比,级数由高度的对数与上限共同决定。 */
  _levels(height, maxIterations) {
    const aspect = this.size.x / Math.max(this.size.y, 1);
    const th = Math.max(16, Math.min(height | 0 || 540, this.size.y | 0));
    const tw = Math.max(16, Math.round(th * aspect));
    // 层数 = clamp(maxIterations, 1, floor(log2(height) - 1))
    const cap = Math.trunc(Math.log2(th) - 1);
    let n = Math.min(maxIterations | 0 || 1, cap);
    if (cap < 1) n = 1;
    return { tw, th, n: Math.max(1, Math.min(8, n)) };
  }

  /**
   * 走一遍金字塔:预过滤 → 逐级降采样 → 逐级上采样(与低一级按 scatter 混合)。
   * `mode` 为 `'bloom'` 时首级做软膝阈值并以 sqrt 编码,降采样用 13 taps;
   * 为 `'diffusion'` 时首级是直拷(真源的预过滤就是直拷,没有阈值),降采样用 4 taps。
   */
  _pyramid(py, source, { height, maxIterations, scatter, mode, threshold = 0, clampMax = 65472 }) {
    const { tw, th, n } = this._levels(height, maxIterations);
    py.ensure(tw, th, n);
    const bloom = mode === 'bloom';

    if (bloom) {
      this.uPre.tSrc.value = source;
      this.uPre.uThreshold.value = threshold;
      this.uPre.uKnee.value = threshold * 0.5;
      this.uPre.uClampMax.value = clampMax;
      this._blit(this.mPre, py.down[0].rt);
    } else {
      this.uCopy.tSrc.value = source;
      this._blit(this.mCopy, py.down[0].rt);
    }

    const mDown = bloom ? this.mDown13 : this.mDown4;
    for (let i = 1; i < n; i++) {
      const src = py.down[i - 1];
      this.uDown.tSrc.value = src.rt.texture;
      this.uDown.uTexel.value.set(1 / src.w, 1 / src.h);
      this._blit(mDown, py.down[i].rt);
    }
    if (n === 1) return py.down[0].rt.texture;

    // scatter 的实际权重:lerp(0.05, 0.95, scatter)(负值取下界)
    const s = scatter < 0 ? 0.05 : Math.min(1, scatter) * 0.9 + 0.05;
    this.uUp.uScatter.value = s;
    this.uUp.uEncoded.value = bloom ? 1 : 0;
    for (let i = n - 2; i >= 0; i--) {
      const low = (i === n - 2) ? py.down[i + 1] : py.up[i + 1];
      this.uUp.tHi.value = py.down[i].rt.texture;
      this.uUp.tLow.value = low.rt.texture;
      this.uUp.uLowTexel.value.set(1 / low.w, 1 / low.h);
      this._blit(this.mUp, py.up[i].rt);
    }
    return py.up[0].rt.texture;
  }

  /**
   * 渲染一帧。`hideForParticlePass` 是一个 `(hide:boolean) => void` 回调:
   * 为真时只留环境粒子可见(粒子泛光缓冲要的是「只有粒子」的画面),为假时复原。
   */
  render(scene, camera, { hideForParticlePass = null, particleCount = 0 } = {}) {
    if (!this.enabled || !this.profile) return false;
    const r = this.renderer;
    const db = r.getDrawingBufferSize(new THREE.Vector2());
    this.setSize(db.x, db.y);
    const P = this.profile;
    const passes = [];
    const skipped = {};

    const pb = P.MysekaiParticleBloomVolume;
    const bl = P.Bloom;
    const df = P.MysekaiDiffusionVolume;
    const fl = P.MysekaiFlareParaVolume;
    const wb = P.WhiteBalance;
    const ca = P.ColorAdjustments;
    const st = P.SplitToning;

    const on = (c) => !!(c && c.active && c.weight > 1e-4 && !SUPPRESSED[c.class || c.name]);
    // 自定义扩散的启用条件是 intensity>0 **且** scatter>0(两项皆非零才生效)
    const dfOn = on(df) && num(df.params.intensity) > 0 && num(df.params.scatter) > 0;
    // 粒子泛光:brightEnable 且 intensity>0.001;本示例还要求真有粒子可渲
    const pbWant = num(pb && pb.params.brightEnable) > 0 && num(pb && pb.params.intensity) > 0.001;
    const pbOn = on(pb) && pbWant && particleCount > 0 && !!hideForParticlePass;
    if (pbWant && !pbOn) {
      skipped[SUPPRESSED.MysekaiParticleBloomVolume
        ? 'particleBloom.noEffectLayerField' : 'particleBloom.noParticleBuffer'] = 1;
    }
    const blOn = on(bl) && num(bl.params.intensity) > 0;

    // 1) 场景
    r.setRenderTarget(this.rtScene);
    r.clear();
    r.render(scene, camera);
    passes.push('scene');

    // 2) 环境粒子缓冲 + 粒子泛光金字塔(真源里泛光的输入是「只有特效」的缓冲,不是整幅画面)
    let pbTex = this.black;
    if (pbOn) {
      const h = Math.max(16, Math.min(num(pb.params.fixedBufferHeight, 540) | 0, db.y | 0));
      const w = Math.max(16, Math.round(h * (db.x / Math.max(db.y, 1))));
      if (!this.rtParticles || this.rtParticles.width !== w || this.rtParticles.height !== h) {
        if (this.rtParticles) this.rtParticles.dispose();
        this.rtParticles = this._rt(w, h);
      }
      hideForParticlePass(true);
      const prevBg = scene.background;
      scene.background = null;
      const prevClear = new THREE.Color();
      r.getClearColor(prevClear);
      const prevAlpha = r.getClearAlpha();
      r.setClearColor(0x000000, 0);
      r.setRenderTarget(this.rtParticles);
      r.clear();
      r.render(scene, camera);
      r.setClearColor(prevClear, prevAlpha);
      scene.background = prevBg;
      hideForParticlePass(false);
      passes.push('particle-buffer');
      pbTex = this._pyramid(this.pyPBloom, this.rtParticles.texture, {
        height: num(pb.params.fixedBufferHeight, 540),
        maxIterations: 6,                       // 本作里泛光的迭代上限是定值
        scatter: num(pb.params.scatter, 0.7), mode: 'bloom',
        threshold: gammaToLinear(num(pb.params.threshold)),
        clampMax: num(pb.params.clamp, 65472),
      });
      passes.push('particle-bloom');
    }

    // 3) 标准泛光(本示例原有支路)
    let blTex = this.black;
    if (blOn) {
      const iters = Math.max(1, num(bl.params.maxIterations, 6) - num(bl.params.skipIterations, 0));
      blTex = this._pyramid(this.pyBloom, this.rtScene.texture, {
        height: 540, maxIterations: iters, scatter: num(bl.params.scatter, 0.7), mode: 'bloom',
        threshold: gammaToLinear(num(bl.params.threshold, 1)),
        clampMax: num(bl.params.clamp, 65472),
      });
      passes.push('bloom');
    }

    // 4) 扩散金字塔(预过滤是直拷:没有阈值,整幅画面都进模糊)
    let dfTex = this.black;
    if (dfOn) {
      dfTex = this._pyramid(this.pyDiff, this.rtScene.texture, {
        height: num(df.params.bufferHeight, 540),
        maxIterations: num(df.params.maxIterations, 5),
        scatter: num(df.params.scatter, 0.7), mode: 'diffusion',
      });
      passes.push('diffusion');
    }

    // 5) 合成
    const u = this.uComp;
    u.tScene.value = this.rtScene.texture;
    u.tBloom.value = blTex; u.tPBloom.value = pbTex; u.tDiff.value = dfTex;

    u.uBloomOn.value = blOn ? bl.weight : 0;
    u.uBloomIntensity.value = blOn ? num(bl.params.intensity) : 0;
    if (blOn) u.uBloomTint.value.fromArray(col(bl.params.tint, [1, 1, 1, 1]));

    u.uDiffOn.value = dfOn ? 1 : 0;
    // 权重在混合期折进强度:强度就是混合式里的 lerp 系数
    u.uDiffIntensity.value = dfOn ? num(df.params.intensity) * df.weight : 0;
    u.uDiffContrast.value = dfOn ? num(df.params.contrast, 1) : 1;
    u.uDiffBlendMode.value = dfOn ? num(df.params.blendMode, 6) : 6;

    u.uPBloomOn.value = pbOn ? 1 : 0;
    u.uPBloomIntensity.value = pbOn ? num(pb.params.intensity) * pb.weight : 0;
    u.uPBloomOverlay.value = pbOn ? num(pb.params.overlayStrength) : 0;
    if (pbOn) u.uPBloomTint.value.fromArray(bloomTint(col(pb.params.tint, [1, 1, 1, 1])));

    const flOn = on(fl);
    const sOn = flOn && num(fl.params.isScreenFlareActive) > 0
      && num(fl.params.screenFlareIntensity) > 0.001;
    u.uFlareScreenOn.value = sOn ? 1 : 0;
    if (sOn) {
      const a = num(fl.params.screenFlareDirection, 45) * Math.PI / 180;
      u.uFlareScreenAxis.value.set(Math.cos(a), Math.sin(a));
      u.uFlareScreenI.value = num(fl.params.screenFlareIntensity) * fl.weight;
      u.uFlareScreenC1.value.fromArray(col(fl.params.screenFlareColor1, [1, 1, 1, 1]));
      u.uFlareScreenC2.value.fromArray(col(fl.params.screenFlareColor2));
      u.uFlareScreenO1.value = num(fl.params.screenFlareOffset1);
      u.uFlareScreenO2.value = num(fl.params.screenFlareOffset2);
      u.uFlareScreenExp.value = Math.max(1e-3, num(fl.params.screenFlareExponent, 1));
    }
    // 太阳耀斑:停掉并计数(见 SKIPPED),不用近似冒充
    if (flOn && num(fl.params.isSunFlareActive) > 0
        && num(fl.params.sunFlareIntensity) > 0.001) {
      skipped['sunFlare.viewSpaceSignUnread'] = 1;
    }

    u.uLinearOn.value = (dfOn || pbOn || sOn) ? 1 : 0;

    const wbOn = on(wb) && (Math.abs(num(wb.params.temperature)) > 1e-6 || Math.abs(num(wb.params.tint)) > 1e-6);
    u.uWbOn.value = wbOn ? 1 : 0;
    if (wbOn) {
      const k = whiteBalanceCoeffs(num(wb.params.temperature), num(wb.params.tint));
      const w = wb.weight;
      u.uWbCoeffs.value.set(1 + (k[0] - 1) * w, 1 + (k[1] - 1) * w, 1 + (k[2] - 1) * w);
    }
    const caOn = on(ca);
    u.uCaOn.value = caOn ? 1 : 0;
    if (caOn) {
      const w = ca.weight;
      // 档案里的取值域与 URP 一致:对比度/饱和度是百分数,色相是度数。
      // postExposure 是 EV,2^EV 是**线性域**的倍数(着色器里那一段因此必须是线性域)。
      u.uExposure.value = 1 + (Math.pow(2, num(ca.params.postExposure)) - 1) * w;
      u.uContrast.value = 1 + (num(ca.params.contrast) / 100) * w;
      u.uHueShift.value = (num(ca.params.hueShift) / 360) * w;
      u.uSaturation.value = 1 + (num(ca.params.saturation) / 100) * w;
      // colorFilter 在档案里是一个 **Color**,而 URP 喂给着色器的是它的 `.linear`。
      // 按权重与中性白混合是 Color 之间的事,所以先混合、再逐通道 gamma→linear。
      // 这条换算对 x >= 1 恰好是 x^2.2 —— 档案里那些大于 1 的取值不是「已经线性」,
      // 是 HDR Color 的原始通道值,少了这一步曝光与滤镜就不在同一个域里相乘。
      const f = col(ca.params.colorFilter, [1, 1, 1, 1]);
      u.uColorFilter.value.set(
        gammaToLinear(1 + (f[0] - 1) * w),
        gammaToLinear(1 + (f[1] - 1) * w),
        gammaToLinear(1 + (f[2] - 1) * w));
    }
    const stOn = on(st);
    u.uStOn.value = stOn ? 1 : 0;
    if (stOn) {
      const w = st.weight;
      const sh = col(st.params.shadows, [0.5, 0.5, 0.5, 1]), hi = col(st.params.highlights, [0.5, 0.5, 0.5, 1]);
      u.uStShadows.value.set(0.5 + (sh[0] - 0.5) * w, 0.5 + (sh[1] - 0.5) * w, 0.5 + (sh[2] - 0.5) * w);
      u.uStHighlights.value.set(0.5 + (hi[0] - 0.5) * w, 0.5 + (hi[1] - 0.5) * w, 0.5 + (hi[2] - 0.5) * w);
      u.uStBalance.value = (num(st.params.balance) / 100) * w;
    }

    this._blit(this.mComp, null);
    passes.push('composite');
    this.stats = { passes: passes.length, particlePass: pbOn, lastPasses: passes, skipped };
    return true;
  }

  /** 逐组件报告:哪些在生效、哪些参数是档案没设的、哪些语义未定、哪些支路停掉了。 */
  status() {
    const P = this.profile || {};
    const components = PIPELINE.filter((k) => P[k]).map((k) => ({
      class: k, name: P[k].name, active: P[k].active, weight: +(P[k].weight ?? 1).toFixed(3),
      params: Object.keys(P[k].params).length, inherited: P[k].inherited.slice(),
      suppressed: SUPPRESSED[k] || null,
    }));
    const extra = Object.keys(P).filter((k) => !PIPELINE.includes(k));
    return {
      enabled: this.enabled, passes: this.stats.lastPasses.slice(), particlePass: this.stats.particlePass,
      components, notInPipeline: extra,
      unresolved: Object.entries(UNRESOLVED).map(([k, v]) => `${k}: ${v}`),
      approximated: Object.entries(APPROXIMATED).map(([k, v]) => `${k}: ${v}`),
      suppressed: Object.entries(SUPPRESSED)
        .filter(([k]) => P[k]).map(([k, v]) => `${k}: ${v}`),
      skippedLaws: Object.entries(SKIPPED).map(([k, v]) => `${k}: ${v}`),
      skipped: { ...this.stats.skipped },
      lut: { present: !!this.lut, note: this.lutNote },
    };
  }

  dispose() {
    for (const t of [this.rtScene, this.rtParticles]) if (t) t.dispose();
    for (const p of [this.pyBloom, this.pyPBloom, this.pyDiff]) p.dispose();
    for (const m of [this.mCopy, this.mPre, this.mDown4, this.mDown13, this.mUp, this.mComp]) m.dispose();
    this.quad.geometry.dispose();
  }
}
