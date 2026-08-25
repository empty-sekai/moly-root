// environment.js — 现象(天气)环境层:光照 / 天空 / 雾 / 云影 / 粒子 / 后处理
//
// 数据来自 `phenomena/`:`index.json` 是清单,每个现象带一份 `config.json`(摊平的环境配置)、
// 一张 32x1 的 `ramp.png`(天空渐变)、一份 `postprocess.json`(后处理档案)、一份
// `fx/effects.json`(粒子效果),以及**只有真的带覆盖的站点**才有的 `overrides/<站点>/`。
//
// 两条通道:
//
//   通道一 · 全局着色量 —— 逐帧写进 envglobals.js 的那组共享 uniform。消费方按名字声明,
//     没有声明的量照样被推送(风的九项在本示例里就没有消费方)。
//   通道二 · 光照九项 —— 现象自己的方向光向量、角色/现象方向光色、皮肤/身体/现象暗部色、
//     两个落影色与落影边缘柔和度。其中能接到本示例既有 toon 着色的那几项直接喂进去,
//     接不上的**如实记进 `status().unwired`**,不硬造消费方。
//
// 换现象 = 0.25 秒交叉淡化:九项逐项插值、光向按球面插值、渐变两张同时在位按进度混合、
// 雾与后处理参数逐项插值。这个时长写在 `CROSS_FADE_SECONDS`,截图式瞬切用 `setPhenomenon(id, 0)`。
//
// 站点覆盖按**两级查找**:选了带覆盖的站点就用 `overrides/<站点>/`,否则回落全局。
// `overrides` 里没有某站点意味着「该站点用全局值」,不是查找失败。

import * as THREE from './three.module.min.js';
import * as Shading from './shading.js';
import { createParticleEffect, makeSharedTextureLoader, makeSharedMeshLoader, shapeSupport } from './emoticon.js';
import {
  ENV_GLOBALS, ENV_GLOBAL_META, ENV_FOG_CHUNK, ENV_WHITE_TEX, ENV_BLACK_TEX,
  withEnvGlobals, envSet, envConsumers,
} from './envglobals.js';
import { PostChain, flattenProfile, blendProfiles } from './envpost.js';
import { GLTFLoader } from './GLTFLoader.js';

export const CROSS_FADE_SECONDS = 0.25;   // 交叉淡化时长(截图式瞬切传 0)
export const RAMP_WIDTH = 32;             // 渐变纹理宽度(契约:32x1)
export const EFFECT_RETIRE_SECONDS = 2;   // 换天气时旧特效的最短存活期(与新特效交叠淡出)
// 一帧最多推进多少秒淡化。浏览器会在标签页不可见时节流动画帧,恢复后的第一帧携带的
// 增量可能长达数百毫秒 —— 不设上限,那一帧会**整口吞掉** 0.25 秒的交叉淡化,看上去是瞬切。
// 引擎侧同样有「单帧增量上限」这个概念,所以这不是我们额外发明的行为。
export const MAX_FADE_STEP_SECONDS = 0.05;

const _envQ = new THREE.Quaternion();     // 相机跟随律复用

const num = (v, d = 0) => (Number.isFinite(+v) ? +v : d);
const col4 = (v, d = [0, 0, 0, 1]) => (Array.isArray(v)
  ? [num(v[0], d[0]), num(v[1], d[1]), num(v[2], d[2]), num(v[3], d[3])] : d.slice());
const lerp = (a, b, t) => a + (b - a) * t;
const lerp4 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t), lerp(a[3], b[3], t)];

/** 方位角/仰角(度)→ 单位向量,与角色着色用的同一条公式。 */
export function dirFromAngles(angleXZDeg, angleYDeg) {
  const a = num(angleXZDeg) * Math.PI / 180, b = num(angleYDeg) * Math.PI / 180;
  return new THREE.Vector3(Math.cos(a) * Math.cos(b), Math.sin(b), Math.sin(a) * Math.cos(b));
}

/**
 * 光照九项从一份 config 摊出来。**光向是第九项之外的那一路**:它由角度算出,换现象时按球面
 * 插值(不是逐分量线性插值),所以单独放在 `dir` 上。
 *
 * `homeSiteLightAngle.active` 为真时,家园站用它替换全局太阳角 —— 这就是那个字段存在的理由,
 * 所以它由调用方按当前站点决定要不要用,不在这里偷偷替换。
 */
export function lightStateOf(config, { homeSite = false } = {}) {
  const L = (config && config.light) || {};
  const hs = L.homeSiteLightAngle || {};
  const useHome = homeSite && !!hs.active;
  return {
    dir: dirFromAngles(useHome ? hs.angleXZ : L.angleXZ, useHome ? hs.angleY : L.angleY),
    angleXZ: num(useHome ? hs.angleXZ : L.angleXZ),
    angleY: num(useHome ? hs.angleY : L.angleY),
    homeAngleUsed: useHome,
    charLightColor: col4(L.characterDirectionalLightColor, [1, 1, 1, 1]),
    charSkinShade: col4(L.characterShadeSkinColor, [0.5, 0.5, 0.5, 1]),
    charBodyShade: col4(L.characterBodyShadeColor, [0.5, 0.5, 0.5, 1]),
    phenLightColor: col4(L.phenomenaDirectionalLightColor, [1, 1, 1, 1]),
    phenShadeColor: col4(L.phenomenaShadeColor, [0.5, 0.5, 0.5, 1]),
    dropShadowColor1: col4(L.dropShadowColor1, [0, 0, 0, 0]),
    dropShadowColor2: col4(L.dropShadowColor2, [0, 0, 0, 0]),
    dropShadowEdgeSmoothness: num(L.dropShadowEdgeSmoothness, 1),
  };
}

/** 光照九项按进度混合;光向走 Slerp,其余逐分量线性。 */
export function blendLight(a, b, t) {
  const qa = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), a.dir.clone().normalize());
  const qb = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.dir.clone().normalize());
  const dir = new THREE.Vector3(0, 1, 0).applyQuaternion(qa.slerp(qb, t));
  return {
    dir,
    angleXZ: lerp(a.angleXZ, b.angleXZ, t),
    angleY: lerp(a.angleY, b.angleY, t),
    homeAngleUsed: t < 0.5 ? a.homeAngleUsed : b.homeAngleUsed,
    charLightColor: lerp4(a.charLightColor, b.charLightColor, t),
    charSkinShade: lerp4(a.charSkinShade, b.charSkinShade, t),
    charBodyShade: lerp4(a.charBodyShade, b.charBodyShade, t),
    phenLightColor: lerp4(a.phenLightColor, b.phenLightColor, t),
    phenShadeColor: lerp4(a.phenShadeColor, b.phenShadeColor, t),
    dropShadowColor1: lerp4(a.dropShadowColor1, b.dropShadowColor1, t),
    dropShadowColor2: lerp4(a.dropShadowColor2, b.dropShadowColor2, t),
    dropShadowEdgeSmoothness: lerp(a.dropShadowEdgeSmoothness, b.dropShadowEdgeSmoothness, t),
  };
}

// 九项在本示例里各自接到哪里。`wired` 为假的项**没有消费方**,面板与 status() 照实列出。
// 这张表是接线的唯一出处:改了接法就改这里,不要在两处各写一份。
export const LIGHT_WIRING = [
  { key: 'dir', label: '方向光向量', wired: true, to: 'toon 着色 lightDir + 天空/站点法线直射/落影' },
  { key: 'charLightColor', label: '角色方向光色', wired: true, to: 'toon 着色 lightColor' },
  { key: 'charSkinShade', label: '角色皮肤影色', wired: true, to: 'toon 着色 skinShade' },
  { key: 'charBodyShade', label: '角色身体影色', wired: true, to: 'toon 着色 bodyShade' },
  { key: 'phenLightColor', label: '现象方向光色', wired: true, to: '站点材质的直射项' },
  { key: 'phenShadeColor', label: '现象影色', wired: true, to: '站点材质的暗部项' },
  { key: 'dropShadowColor1', label: '落影色 1', wired: true, to: '站点表面的落影(角色投影盘)' },
  { key: 'dropShadowColor2', label: '落影色 2', wired: true, to: '站点表面的落影(角色投影盘)' },
  { key: 'dropShadowEdgeSmoothness', label: '落影边缘平滑', wired: true, to: '站点表面落影的边缘过渡' },
];

// ---- 时间轴 ---------------------------------------------------------------
//
// 有一个现象不是恒定状态,它由一份时间轴资产驱动 —— 清单 `semantics.timeline` 的原话:
// "one phenomenon is driven by a timeline; each track says which value it drives, and each clip
// carries its own curve or gradient on a normalized axis over the clip's duration"。
// **打雷天那一下亮就出自这里**:天空与光的「附加色 / 附加强度」四项是四条轨的目标,
// 现象配置里根本没有这四项,时间轴就是它们唯一的来源。
//
// 一、接哪四根线。按轨记录里的 `target` 字段接,**不按轨名猜** —— 轨名是编辑器里给人看的
//    标签,`target` 才是从轨上的枚举读出来的目标名(`targetValue` 是它的原始整数,一并报出
//    来便于对账)。当前内容里四条轨的对应关系是:
//
//      skyAdditiveColor       (1) → 天空材质的附加色    → SkyDome 的 additiveColor
//      skyAdditiveIntensity   (1) → 天空材质的附加强度  → SkyDome 的 additiveIntensity
//      lightAdditiveColor     (2) → 方向光的附加色      ↘ 两项一起叠到**现象方向光**上
//      lightAdditiveIntensity (2) → 方向光的附加强度    ↗ (哪一盏灯的取舍见 NOT_RESTORED)
//
// 二、一拍怎么算。
//    * clip 在 `[start, start + duration)` 上活;它自己的曲线/渐变跑在**归一化的 0..1 轴**上,
//      轴的位置 = `(clipIn + (t - start) * timeScale) / duration`(契约原话:normalized axis
//      over the clip's duration, scaled by `timeScale` and offset by `clipIn`)。
//    * 数值轨的值 = 曲线值 × clip 自带的 `scale` × **轨自带的 `scale`**。轨那一档不是 1:
//      当前内容里天空那条是 0.5、光那条是 2.56,漏掉它闪光的强弱就是错的。
//    * 混入混出(`blendIn/OutDuration`)与缓入缓出(`easeIn/OutDuration`)按记录里的秒数取线性
//      权重。**当前内容里这四项全是 0**,所以权重恒为 1 —— 是 0 就按 0 处理,不额外加平滑。
//    * 同一条轨上若有多个 clip 同时活,按权重加权平均(当前内容里没有一处重叠)。
//    * 一条轨上**没有** clip 活着 = 这一拍它不出力:颜色回到材质自带的那一份,强度回到 0。
//      时间轴混合器在权重为零时把绑定值还原回默认值,这里照同一条处置。
//
// 三、没做的照实计数(见 `notModelled`):两条 `ValueNoiseTrack` 是两条强度轨的**子轨**,
//    记录里带噪声强度、频率与强度曲线,但「噪声怎么并进父轨的值」产物一个字没说 —— 不猜,
//    整条不做并计一笔;空的 `MarkerTrack` 同理照实列出来。

/** 四条附加量:轨的 `target` 名 → 这条轨是颜色轨还是数值轨。名单之外的目标一律计进未建模。 */
export const TIMELINE_TARGETS = {
  skyAdditiveColor: 'color',
  skyAdditiveIntensity: 'value',
  lightAdditiveColor: 'color',
  lightAdditiveIntensity: 'value',
};

// 加权切线的位:1 = 入端用键自带的权重,2 = 出端用,3 = 两端。没有那一位就取 1/3 ——
// 那正好让下面的贝塞尔段退化成普通的 Hermite 段,两条路走同一份代码。
const CURVE_WEIGHT_IN = 1;
const CURVE_WEIGHT_OUT = 2;
const CURVE_DEFAULT_WEIGHT = 1 / 3;

/** 三次贝塞尔在参数 s 处的一维取值。 */
function bezier1(p0, p1, p2, p3, s) {
  const m = 1 - s;
  return m * m * m * p0 + 3 * m * m * s * p1 + 3 * m * s * s * p2 + s * s * s * p3;
}

/**
 * 已知横坐标反解参数 s。**加权切线下 x(s) 不是线性的**,拿 `(x - x0) / dt` 当 s 会取错值;
 * 未加权时两个控制点正好落在三等分处,x(s) 退化成线性,二分给出的还是同一个答案。
 */
function bezierParamAt(x0, x1, x2, x3, x) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 32; i += 1) {
    const mid = (lo + hi) * 0.5;
    if (bezier1(x0, x1, x2, x3, mid) < x) lo = mid; else hi = mid;
  }
  return (lo + hi) * 0.5;
}

/**
 * 一条动画曲线在 `u` 处的值。键之间是三次段:未加权 = Hermite,加权 = 按键自带权重摆控制点。
 * 斜率为 `null` 的键是作者侧的**阶梯键**(原始值是无穷,JSON 存不下,提取侧记成 null),
 * 整段保持左键的值。轴外按端点值钳住。
 */
export function evalCurve(curve, u) {
  const keys = (curve && curve.keys) || [];
  if (!keys.length) return 0;
  if (u <= num(keys[0].time)) return num(keys[0].value);
  const last = keys[keys.length - 1];
  if (u >= num(last.time)) return num(last.value);
  let i = 0;
  while (i < keys.length - 2 && num(keys[i + 1].time) <= u) i += 1;
  const a = keys[i], b = keys[i + 1];
  const dt = num(b.time) - num(a.time);
  if (dt <= 0) return num(b.value);
  if (a.outSlope === null || b.inSlope === null) return num(a.value);
  const w0 = ((a.weightedMode | 0) & CURVE_WEIGHT_OUT)
    ? num(a.outWeight, CURVE_DEFAULT_WEIGHT) : CURVE_DEFAULT_WEIGHT;
  const w1 = ((b.weightedMode | 0) & CURVE_WEIGHT_IN)
    ? num(b.inWeight, CURVE_DEFAULT_WEIGHT) : CURVE_DEFAULT_WEIGHT;
  const x0 = num(a.time), x3 = num(b.time);
  const y0 = num(a.value), y3 = num(b.value);
  const x1 = x0 + w0 * dt, x2 = x3 - w1 * dt;
  const y1 = y0 + num(a.outSlope) * w0 * dt, y2 = y3 - num(b.inSlope) * w1 * dt;
  return bezier1(y0, y1, y2, y3, bezierParamAt(x0, x1, x2, x3, u));
}

/**
 * 一份渐变在 `u` 处的 RGBA。颜色键与透明键是**两串独立的键**,各自线性插值 —— 产物里存的
 * 就是这两串,没有第三个字段说它是别的插值方式,所以不去发明一种。
 */
export function evalGradient(gradient, u) {
  const pick = (keys, field, dflt) => {
    if (!keys || !keys.length) return dflt;
    if (u <= num(keys[0].time)) return keys[0][field];
    const last = keys[keys.length - 1];
    if (u >= num(last.time)) return last[field];
    for (let i = 0; i < keys.length - 1; i += 1) {
      const a = keys[i], b = keys[i + 1];
      if (u <= num(b.time)) {
        const span = num(b.time) - num(a.time);
        const k = span > 0 ? (u - num(a.time)) / span : 0;
        if (Array.isArray(a[field])) {
          return [lerp(num(a[field][0]), num(b[field][0]), k),
            lerp(num(a[field][1]), num(b[field][1]), k),
            lerp(num(a[field][2]), num(b[field][2]), k)];
        }
        return lerp(num(a[field]), num(b[field]), k);
      }
    }
    return last[field];
  };
  const rgb = pick((gradient || {}).colorKeys, 'color', [0, 0, 0]);
  const alpha = pick((gradient || {}).alphaKeys, 'alpha', 1);
  return [num(rgb[0]), num(rgb[1]), num(rgb[2]), num(alpha, 1)];
}

/**
 * 一个 clip 在其局部秒数处的权重。混入/缓入吃前段、混出/缓出吃后段,各自线性。
 * **当前内容里这四个时长全是 0**,所以这里恒返回 1 —— 数据是 0 就按 0 处理。
 */
export function clipWeight(clip, local, duration) {
  let w = 1;
  for (const len of [num(clip.easeInDuration), num(clip.blendInDuration)]) {
    if (len > 0 && local < len) w *= local / len;
  }
  for (const len of [num(clip.easeOutDuration), num(clip.blendOutDuration)]) {
    if (len > 0 && local > duration - len) w *= (duration - local) / len;
  }
  return Math.max(0, Math.min(1, w));
}

/**
 * 一份现象时间轴。构造时按 `target` 分拣轨道:认得的四个目标进 `tracks`,其余(目标是
 * `none`、轨被静音、或轨类本示例没建模)一条不落地进 `notModelled`。
 */
export class PhenomenonTimeline {
  constructor(doc) {
    const d = doc || {};
    this.name = String(d.name || '');
    this.duration = num(d.duration);
    // 帧率是资产自带的编辑器帧率:面板按它报「第几帧」、按 1/帧率 步进;
    // **不拿它去量化取样** —— 那是另一条律,产物没说运行时按帧对齐。
    this.frameRate = num(d.frameRate, 60) || 60;
    this.durationMode = d.durationMode;
    this.tracks = [];
    this.notModelled = [];
    for (const t of d.tracks || []) {
      const target = t ? t.target : null;
      if (t && !t.muted && target && TIMELINE_TARGETS[target]) {
        this.tracks.push({
          name: String(t.name || ''), class: t.class || null, target,
          targetValue: t.targetValue === undefined ? null : t.targetValue,
          kind: TIMELINE_TARGETS[target], scale: num(t.scale, 1), clips: t.clips || [],
        });
        continue;
      }
      this.notModelled.push({
        track: t ? String(t.name || '(未命名)') : '(未命名)',
        class: t ? t.class : null,
        role: t ? t.role || null : null,
        clips: t && t.clips ? t.clips.length : 0,
        why: (t && t.muted) ? '轨被静音'
          : (t && target && !TIMELINE_TARGETS[target]) ? `目标 ${target} 不在四条附加量之内`
            : `轨类 ${t ? t.class : '?'} 未建模:产物没说它怎么并进父轨`,
      });
    }
  }

  get clipCount() { return this.tracks.reduce((n, t) => n + t.clips.length, 0); }

  get notModelledClips() { return this.notModelled.reduce((n, t) => n + t.clips, 0); }

  /** 面板与 status() 读的轨道摘要(不含 clip 本体)。 */
  trackSummary() {
    return this.tracks.map((t) => ({
      name: t.name, class: t.class, target: t.target, targetValue: t.targetValue,
      scale: t.scale, clips: t.clips.length,
    }));
  }

  /**
   * 时间轴在 `time` 秒处的一拍。返回的对象上,四个目标名各挂一项:有 clip 活着就是算出来的
   * 值,**没有就是 `null`**(不是 0 也不是黑色)—— 「这一拍这条轨不出力」与「它算出来正好是零」
   * 是两件事,消费方据此回落到自己的默认值。
   */
  sample(time) {
    const d = this.duration;
    const t = d > 0 ? ((num(time) % d) + d) % d : 0;
    const out = {
      time: t, frame: Math.floor(t * this.frameRate), duration: d, tracks: [],
      skyAdditiveColor: null, skyAdditiveIntensity: null,
      lightAdditiveColor: null, lightAdditiveIntensity: null,
    };
    for (const track of this.tracks) {
      let weight = 0, live = 0;
      let acc = track.kind === 'color' ? [0, 0, 0, 0] : 0;
      for (const clip of track.clips) {
        const start = num(clip.start), dur = num(clip.duration);
        if (!(dur > 0) || t < start || t >= start + dur) continue;
        const w = clipWeight(clip, t - start, dur);
        if (!(w > 0)) continue;
        live += 1;
        const u = Math.max(0, Math.min(1,
          (num(clip.clipIn) + (t - start) * num(clip.timeScale, 1)) / dur));
        const asset = clip.asset || {};
        if (track.kind === 'color') {
          const c = evalGradient(asset.gradient, u);
          acc = [acc[0] + c[0] * w, acc[1] + c[1] * w, acc[2] + c[2] * w, acc[3] + c[3] * w];
        } else {
          acc += evalCurve(asset.curve, u) * num(asset.scale, 1) * w;
        }
        weight += w;
      }
      let value = null;
      if (weight > 0) {
        value = track.kind === 'color'
          ? [acc[0] / weight, acc[1] / weight, acc[2] / weight, acc[3] / weight]
          : (acc / weight) * track.scale;
      }
      out.tracks.push({
        name: track.name, target: track.target, targetValue: track.targetValue,
        scale: track.scale, live, weight: +weight.toFixed(6), value,
      });
      out[track.target] = value;
    }
    return out;
  }
}

/**
 * 把一份附加色叠到一个颜色上。与天空着色器里那一段**同一形状**:两边各走一趟 gamma→线性,
 * 附加项乘自己的 alpha 与强度之后相加,再回 gamma。alpha 不动(它不是这条律的量)。
 */
export function addAdditiveColor(base, add, intensity) {
  const out = [num(base[0]), num(base[1]), num(base[2]), num(base[3], 1)];
  if (!add || !(num(intensity) > 0)) return out;
  const lin = (v) => Math.pow(Math.max(v, 0), 2.2);
  const gam = (v) => Math.pow(Math.max(v, 0), 1 / 2.2);
  const k = num(add[3], 1) * num(intensity);
  for (let i = 0; i < 3; i += 1) out[i] = gam(lin(out[i]) + lin(num(add[i])) * k);
  return out;
}

// ---- 天空 -----------------------------------------------------------------
//
// 天空是一个网格 + 一份材质属性块,不是天空盒。网格与材质都在站点系统自己那套共享包里,
// 15 个天气**共用同一份网格与同一份材质**,天气之间只差各自那张 32x1 的渐变贴图;
// 换天气 = **两张渐变同时在位** + 插值进度。本示例照这个形状实现:
//
//   * 几何**从产物装载**(两条来源见 `Environment._loadSky`)。`SkyDome` 只认拿到的网格:
//     装不上就**不画天空**,不自造一个穹顶顶替 —— 自造形状会连带把 UV 也造出来,
//     而 UV 正是这条渐变律的输入,造出来的 UV 就是造出来的天空。
//   * 渐变按**网格 UV0 的纵坐标**采样,窗口两端从材质的 `_GradientMinY` / `_GradientMaxY` 读,
//     不写死:写死之后材质一改,画面就按旧窗口继续算,而且是悄悄地算。
//   * 渐变采样单独抽成 `SKY_RAMP_SAMPLE` 一段 GLSL:换实现只改这一段,不动其余管线。
//
// 天空网格每帧钉到相机水平位置(与运行时把它钉到玩家位置同义:天空永远不被走出去)。

// 天空材质认**属性**不认名字:这条律要的输入就是窗口两端加两个渐变槽,谁带齐谁就是它。
// 按名字挑等于把包内的命名抄进示例,包一改名示例就静默挑不到。
export const SKY_GRADIENT_MIN = '_GradientMinY';
export const SKY_GRADIENT_MAX = '_GradientMaxY';
export const SKY_RAMP_SLOTS = ['_RampTex1', '_RampTex2'];
// 附加色在材质里的属性名。材质自带的那一份是**没有 clip 活着时的底值**(当前这份是全零),
// 时间轴一活就由 skyAdditiveColor 轨接管。
export const SKY_ADDITIVE_COLOR = '_AdditiveColor';
// 天空视图脚本的类名:站点系统有两个 shell 包都挂着它,带网格的那个才是天空所在。
const SKY_VIEW_SCRIPT = 'EnvironmentSkyView';

/**
 * 一份材质记录里的渐变窗口。两端缺一、读不成数、或不成一个正区间就返回 `null` ——
 * 调用方据此**停画天空**,不许在这里补默认值。
 */
export function skyGradientWindow(material) {
  const f = material && material.floats;
  if (!f) return null;
  const min = +f[SKY_GRADIENT_MIN], max = +f[SKY_GRADIENT_MAX];
  if (!Number.isFinite(min) || !Number.isFinite(max) || !(max > min)) return null;
  return { min, max };
}

/** 一份包文档里的天空材质:带齐窗口两端与两个渐变槽的那一份。 */
export function skyMaterialOf(pkg) {
  const list = (pkg && pkg.materials) || [];
  return list.find((m) => m && skyGradientWindow(m)
    && m.textures && SKY_RAMP_SLOTS.every((slot) => slot in m.textures)) || null;
}

/** 一个装载好的场景里、用天空材质绘制的那个网格。 */
function pickSkyMesh(scene, materialName) {
  let hit = null;
  scene.traverse((o) => {
    if (hit || !o.isMesh || !o.geometry) return;
    if (!materialName) { hit = o; return; }
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    if (mats.some((m) => m && m.name === materialName)) hit = o;
  });
  return hit;
}

export const SKY_RAMP_SAMPLE = /* glsl */`
// 渐变采样(真律)。v 是网格 UV0 的纵坐标(作者侧取向:天顶端≈1,底端≈0),
// 窗口两端 minY / maxY 由材质给出:
//     t = saturate((v - minY) / (maxY - minY));   u = 1 - t
// 天顶端 → u≈0 → 纹素 0;窗口下沿及其以下 → t=0 → u=1 → 纹素 31。
// **不做半纹素内缩**:运行时拿 u 直接采样,32x1 的纹理两端 clamp;内缩会把整条取样位置挪动
// 半个纹素,那是另一条律。窗口退化(两端相等)时给分母一个下限,免得除出 NaN 把天空涂黑 ——
// 数据里不存在这种窗口,这只是不让一处坏数据变成满屏黑。
vec2 envRampUv(float v, float minY, float maxY) {
  float t = clamp((v - minY) / max(maxY - minY, 1e-6), 0.0, 1.0);
  return vec2(1.0 - t, 0.5);
}
`;

const SKY_VERT = /* glsl */`
varying float vGradientV;
void main() {
  // 导出的几何按 glTF 的纵向纹理原点存 UV,与作者侧上下相反,所以这条律的 v 是 1 - uv.y。
  // 三个环可核:天顶环 0.008729 → 0.991271,地平环 0.5 → 0.5,底环 0.991271 → 0.008729。
  vGradientV = 1.0 - uv.y;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const SKY_FRAG = /* glsl */`
uniform sampler2D rampTex1;
uniform sampler2D rampTex2;
uniform float fadeProgress;
uniform float gradientMinY;
uniform float gradientMaxY;
uniform vec4 additiveColor;
uniform float additiveIntensity;
varying float vGradientV;
${SKY_RAMP_SAMPLE}
void main() {
  vec2 uv = envRampUv(vGradientV, gradientMinY, gradientMaxY);
  vec3 a = texture2D(rampTex1, uv).rgb;
  vec3 b = texture2D(rampTex2, uv).rgb;
  vec3 c = mix(a, b, clamp(fadeProgress, 0.0, 1.0));
  // 附加色在运行时走一趟 gamma→linear→缩放→gamma 的往返;这里按同一形状实现。
  // 缩放因子(AdditiveIntensity)由现象时间轴的 skyAdditiveIntensity 轨写进来(见「时间轴」段);
  // 没有时间轴、或这一拍没有 clip 活着时它是 0,附加项因而整项不出力。
  vec3 lin = pow(max(c, vec3(0.0)), vec3(2.2)) + pow(max(additiveColor.rgb, vec3(0.0)), vec3(2.2))
           * additiveColor.a * additiveIntensity;
  gl_FragColor = vec4(pow(max(lin, vec3(0.0)), vec3(1.0 / 2.2)), 1.0);
}
`;

class SkyDome {
  constructor() {
    this.uniforms = {
      rampTex1: { value: ENV_WHITE_TEX },
      rampTex2: { value: ENV_WHITE_TEX },
      fadeProgress: { value: 0 },
      // 窗口两端的**占位值**:材质没读到就不画天空(见 `ready`),这两个不会被用到。
      gradientMinY: { value: 0 },
      gradientMaxY: { value: 0 },
      additiveColor: { value: new THREE.Vector4(0, 0, 0, 0) },
      // 底值 0:没有时间轴写它的时候附加项一分不出力。**不是 1** —— 1 会让任何一份非零的
      // 材质附加色在没人驱动时也悄悄加进画面。
      additiveIntensity: { value: 0 },
    };
    this.material = new THREE.ShaderMaterial({
      name: 'env-sky',
      uniforms: this.uniforms,
      vertexShader: SKY_VERT,
      fragmentShader: SKY_FRAG,
      // 网格法线朝内、三角绕序与法线一致 —— 从里面看见的是正面,与材质那条背面剔除同一结果。
      side: THREE.FrontSide,
      depthWrite: false,
      depthTest: true,
    });
    this.additive = { color: [0, 0, 0, 0], intensity: 0 };  // 时间轴写进来的那一拍(status 读它)
    this.geometrySource = null;    // 装上网格之前没有来源可报
    this.gradient = null;          // { min, max, source };读不到就是 null,不填默认值
    this.meshInfo = null;
    this.wantVisible = true;
    this.mesh = new THREE.Mesh(this._geometryFor(null), this.material);
    this.mesh.name = 'env_sky';
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = -1000;         // 先画天空,再画其余一切
    this.mesh.visible = false;             // 网格与窗口都到位之前不画
  }

  /**
   * 几何来源。给了网格就用它并记下来源;**没给就没有天空** —— 返回一个空几何(那是「还没有
   * 网格」,不是一个形状),`ready` 因此为假,天空不画。本示例不自造穹顶顶替产物网格。
   */
  _geometryFor(loadedGeometry, source) {
    if (loadedGeometry) {
      this.geometrySource = source || 'unnamed';
      return loadedGeometry;
    }
    this.geometrySource = null;
    return new THREE.BufferGeometry();
  }

  useGeometry(geometry, source, name) {
    if (!geometry) return false;
    const old = this.mesh.geometry;
    this.mesh.geometry = this._geometryFor(geometry, source);
    if (old && old !== this.mesh.geometry) old.dispose();
    const g = this.mesh.geometry;
    const pos = g.getAttribute('position');
    const index = g.getIndex();
    this.meshInfo = {
      name: name || g.name || null,
      vertices: pos ? pos.count : 0,
      triangles: index ? index.count / 3 : (pos ? Math.floor(pos.count / 3) : 0),
      // UV0 是这条渐变律**唯一**的输入:没有它这个网格画不成天空。
      uv: !!g.getAttribute('uv'),
    };
    this._syncVisible();
    return true;
  }

  /** 渐变窗口。读不到(`win` 为 null)就**清空**并停画:既不留上一次的值,也不填默认值。 */
  setGradientWindow(win, source) {
    if (!win || !Number.isFinite(win.min) || !Number.isFinite(win.max) || !(win.max > win.min)) {
      this.gradient = null;
      this._syncVisible();
      return false;
    }
    this.uniforms.gradientMinY.value = win.min;
    this.uniforms.gradientMaxY.value = win.max;
    this.gradient = { min: win.min, max: win.max, source: source || 'unnamed' };
    this._syncVisible();
    return true;
  }

  /** 网格(含 UV0)与窗口都到位才画得成天空。 */
  get ready() {
    return !!(this.geometrySource && this.gradient && this.meshInfo && this.meshInfo.uv);
  }

  setVisible(on) {
    this.wantVisible = !!on;
    this._syncVisible();
    return this.mesh.visible;
  }

  _syncVisible() { this.mesh.visible = this.wantVisible && this.ready; }

  /** 附加色与附加强度(时间轴驱动)。两项一起写,免得只更新一半。 */
  setAdditive(color, intensity) {
    const c = col4(color, [0, 0, 0, 0]);
    const i = num(intensity);
    this.uniforms.additiveColor.value.set(c[0], c[1], c[2], c[3]);
    this.uniforms.additiveIntensity.value = i;
    this.additive = { color: c, intensity: i };
    return this.additive;
  }

  setRamps(a, b, progress) {
    this.uniforms.rampTex1.value = a || ENV_WHITE_TEX;
    this.uniforms.rampTex2.value = b || a || ENV_WHITE_TEX;
    this.uniforms.fadeProgress.value = num(progress);
  }

  follow(camera) {
    this.mesh.position.set(camera.position.x, 0, camera.position.z);
  }

  dispose() {
    this.mesh.geometry.dispose();
    this.material.dispose();
    this.mesh.removeFromParent();
  }
}

// ---- 站点 -----------------------------------------------------------------
//
// 承影面是**真站点**:几何从站点产物装载(`site/index.json` → 场景包的 glb),本示例不再自造
// 平面。四组现象量因此落在真地形上,逐项对号见 `SITE_FRAG`;没有对号的量不在这里假装被用到。
//
// 一、装什么
//   `site/index.json` 里 `scenes.<键>.geometry` 是场景包的 glb(清单里所有路径都相对清单自己)。
//   一个 glb 里一个 prefab 一个 glTF scene,**`defaultScene` 才是游戏摆出来的那个**
//   (`semantics.geometry` 原话),所以只取 glTF 自己的默认场景,不遍历全部 scene ——
//   遍历会把同一份网格摆好几遍。场景文档的 `inactiveNodes` / `disabledRenderers` 是**原版
//   根本不画**的节点,照它隐藏:`semantics.inactiveNodes` 说列出来就是为了让消费方
//   「not draw what the game never shows」。隐藏≠删除,它们照样在几何计数里。
//
// 二、室内/室外怎么判(不按名字猜)
//   主判据 —— 场景文档里 `env` 槽自带的角色原话:"environment volume anchor; empty in every
//   outdoor package and holding the room volume indoors"。槽里有节点或碰撞体 = 室内。
//   清单级判据 —— 建面板下拉框时用,不必读逐场景文档(最大的一份 6 MB):`families.kit` 只有
//   一个包,那就是室内套件;场景记录的 `declaredDependencies` 点名它的就是房间站点。产物自己
//   的话见 `semantics.indoor`("the three room sites have no scene geometry: their rooms are
//   assembled at runtime from the kit")与 `indoor.assembly`。依赖名是路径式样、包名是下划线
//   式样,规范化到同一形状再比。
//   两条在当前内容里同解(室内 = first_floor / my_room,其余六个室外)。不一致时**以主判据
//   为准**,分歧记进 `status().site.indoorDisagree`,不静默取一条。
//
// 三、室内怎么拼
//   房间站点的场景包里**没有墙也没有地板**。`indoor.assembly` 一句话给了完整编排:
//   "a room is the kit's meshes placed by one expansion module per level plus that level's
//   walkable surface; the site package of a room site holds no wall or floor geometry at all"。
//   照这句话拼,三件:
//
//     场景包    —— 照常取它的默认场景。房间站点的默认场景里只有那一件「房间体积」
//                  (`env` 槽里的天穹网格),墙与地一片都没有 —— 这正是上面那句话的后半句。
//     扩张模块  —— `indoor.levels.<级>.module`,按 `module.prefabs[].root` **点名**取那几个
//                  glTF scene(不是「把 glb 里的 scene 全挂上」)。模块包自己**一个网格资产
//                  都没有**(它的清单里 Mesh 一项为零),它的绘制件指向套件包里的网格;
//                  产物把这层跨包引用解开后把几何写进了模块的 glb,所以「套件的网格由模块
//                  摆出来」这一步取模块的 glb 就是完整的一份 —— 套件包名一并记进读数,
//                  让拼上去的每一件都追得到出处。
//     可走面    —— `indoor.levels.<级>.walkable`,一级一份,文件相对可走面包自己的目录。
//                  它是 MeshCollider,**原版不画**,所以这里挂上但不画(与「原版不画的节点
//                  照样在几何计数里」同一条处置),读数里单列 `drawn: false`。有几级的
//                  可走面在盘上就没有几何,记录自己写明那是作者状态而不是查找失败 ——
//                  照抄那句话,不当成错误。
//
//   房间是第几级由 master 表决定,本仓没有 master(`sites.json` 的 `missing` 写明
//   「no master directory supplied」)。所以等级按「服务端决定的做成面板下发」处置:
//   面板上一个旋钮,默认取产物里最高的一级(`semantics.levels`:房间站点到 5 级封顶),
//   读数见 `mounted.level` 与 `mounted.levelSource`。
//
// 四、站点世界位置
//   `sitePosition` 同样只在 master 表里(`placement` 指到的 `sites.json` 里 `sites` 是空数组),
//   所以本示例把当前站点摆在原点。一次只看一个站点,这不改变站点内部的相对关系。

// Unity 侧混合因子枚举 → three 的因子。材质 extras 里 `blendFactors` 就是这两个整数
// (当前内容只出现两对:5/10 = 常规 alpha,5/1 = 叠加),按枚举翻译而不是按 `blendMode`
// 的字符串猜 —— 字符串是给人看的,数对才是律。
const UNITY_BLEND_FACTOR = {
  0: THREE.ZeroFactor, 1: THREE.OneFactor, 2: THREE.DstColorFactor, 3: THREE.SrcColorFactor,
  4: THREE.OneMinusDstColorFactor, 5: THREE.SrcAlphaFactor, 6: THREE.OneMinusSrcColorFactor,
  7: THREE.DstAlphaFactor, 8: THREE.OneMinusDstAlphaFactor, 9: THREE.SrcAlphaSaturateFactor,
  10: THREE.OneMinusSrcAlphaFactor,
};

/** 依赖名(`a/b/c`)与包名(`a__b__c`)化到同一形状再比。 */
function pkgKey(s) { return String(s || '').replace(/[/_]+/g, '/').toLowerCase(); }

// 落影投射体:所有站点材质**共享同一个 uniform 对象**(与 envglobals 同一条规矩),
// 写一次就等于推给了全部站点材质。
const SITE_SUBJECT = {
  envSubjectPos: { value: new THREE.Vector3(0, 0.6, 0) },
  envSubjectRadius: { value: 0.55 },
};

const SITE_VERT = /* glsl */`
uniform mat3 siteUvTransform;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vSiteUv;
varying float vFogRamp;
${ENV_FOG_CHUNK}
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz;
  // 世界法线。非均匀缩放下这不是严格的法线矩阵(GLSL ES 1.0 没有 inverse()),
  // 站点节点的缩放基本是均匀的,所以这条按近似记着,不假装它精确。
  vWorldNormal = mat3(modelMatrix) * normal;
  // KHR_texture_transform 的 UV 变换在自定义着色器里不会被自动施加,手动乘。
  vSiteUv = (siteUvTransform * vec3(uv, 1.0)).xy;
  // 与角色着色器同一条:雾的能见度斜坡逐顶点算,片元只吃插值结果。
  vFogRamp = envFogRamp(-(viewMatrix * wp).z);
  gl_Position = projectionMatrix * viewMatrix * wp;
}
`;

const SITE_FRAG = /* glsl */`
uniform vec3 envLightDir;
uniform vec4 envPhenLightColor;
uniform vec4 envPhenShadeColor;
uniform sampler2D envCloudShadowTex;
uniform sampler2D envCloudShadowTexB;
uniform float envCloudFade;
uniform float envCloudShadowScale;
uniform float envCloudShadowOpacity;
uniform vec2 envCloudScrollSpeed;
uniform vec4 envDropShadowColor1;
uniform vec4 envDropShadowColor2;
uniform float envDropShadowEdgeSmoothness;
uniform float envTime;
uniform vec3 envSubjectPos;      // 角色位置(本示例的落影投射体)
uniform float envSubjectRadius;
uniform sampler2D siteMap;       // 站点材质的基色贴图(glTF baseColorTexture)
uniform float siteHasMap;
uniform vec4 siteBaseColor;      // glTF baseColorFactor
uniform float siteAlphaCutoff;   // glTF MASK 的阈值;BLEND/OPAQUE 是 0
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vSiteUv;
varying float vFogRamp;
${ENV_FOG_CHUNK}
void main() {
  vec4 base = siteBaseColor;
  if (siteHasMap > 0.5) base *= texture2D(siteMap, vSiteUv);
  // MASK:自定义着色器没有内建的 alphaTest,阈值在这里自己丢弃。
  if (siteAlphaCutoff > 0.0 && base.a < siteAlphaCutoff) discard;

  // 直射项:自制平面时法线恒为 +Y,所以那里只取光向的高度分量;真站点有真法线,
  // 这里就是法线与光向的夹角。
  vec3 N = normalize(vWorldNormal);
  float ndl = clamp(dot(N, normalize(envLightDir)), 0.0, 1.0);
  vec3 lit = base.rgb * mix(envPhenShadeColor.rgb, envPhenLightColor.rgb, ndl);

  // 云影:两张贴图同时在位(交叉淡化),采样用**尺寸的倒数**,滚动是速度向量乘标量。
  vec2 uv = vWorldPos.xz * envCloudShadowScale + envCloudScrollSpeed * envTime;
  float ca = texture2D(envCloudShadowTex, uv).r;
  float cb = texture2D(envCloudShadowTexB, uv).r;
  float cloud = mix(ca, cb, clamp(envCloudFade, 0.0, 1.0));
  lit *= mix(1.0, cloud, clamp(envCloudShadowOpacity, 0.0, 1.0));

  // 落影:两色 + 边缘平滑。角色沿光向投到**本片元所在高度**的水平面(自制平面时那是 y=0)。
  // 两个权重是本示例的近似,不是产物里的律:朝上分量(不加它,墙面与树叶上也会糊一块影子)
  // 与「只投在角色下方」。
  float dy = envSubjectPos.y - vWorldPos.y;
  vec2 off = envLightDir.y > 1e-3
    ? envSubjectPos.xz - envLightDir.xz * (dy / max(envLightDir.y, 1e-3))
    : envSubjectPos.xz;
  float d = length(vWorldPos.xz - off) / max(envSubjectRadius, 1e-3);
  float edge = clamp(envDropShadowEdgeSmoothness, 0.02, 4.0);
  float core = 1.0 - smoothstep(1.0 - edge, 1.0, clamp(d, 0.0, 1.0));
  vec3 shadowCol = mix(envDropShadowColor1.rgb, envDropShadowColor2.rgb, clamp(d, 0.0, 1.0));
  float shadowA = mix(envDropShadowColor1.a, envDropShadowColor2.a, clamp(d, 0.0, 1.0))
                * core * clamp(N.y, 0.0, 1.0) * step(0.0, dy);
  lit = mix(lit, shadowCol, clamp(shadowA, 0.0, 1.0));

  gl_FragColor = vec4(envApplyFogRamp(lit, vFogRamp, vWorldPos.y), base.a);
}
`;

/**
 * 站点几何 + 现象着色。装载的 glTF 材质在这里换成上面那份着色器,**透明度照原样带过来**:
 * 混合模式来自材质 extras 的 `blendFactors`(Unity 枚举),遮罩阈值来自 glTF 的 alphaCutoff,
 * 双面来自 glTF 的 doubleSided。贴图按本示例的 gamma 直通规矩置 `NoColorSpace`
 * (与角色贴图同一条),不走 sRGB 解码。
 */
class SiteView {
  /** @param opts `{base}` —— `base` 是 `site/` 的父目录 */
  constructor(opts) {
    this.base = String(opts.base || '.').replace(/\/+$/, '');
    this.root = `${this.base}/site`;
    this.index = null;
    this.loader = new GLTFLoader();
    this.group = new THREE.Group();
    this.group.name = 'env_site';
    this.key = null;              // 当前挂着的场景键
    this.level = null;            // 面板下发的室内等级;null = 取产物里最高的一级
    this.mounted = null;          // 当前挂载的读数(status 直接报它)
    this.mounting = null;         // 正在进行的挂载 Promise(判据可以等它)
    this.materials = [];
    this.errors = [];
  }

  async load(fetchJson) {
    const idx = await fetchJson(`${this.root}/index.json`);
    if (!idx || !idx.scenes) { this.errors.push('site/index.json 读不到或没有 scenes 段'); return false; }
    this.index = idx;
    return true;
  }

  /** 室内套件的包名(`families.kit`)。产物里只有一个,没有就是判据缺了输入。 */
  get kitPackage() {
    const kit = ((this.index || {}).families || {}).kit || [];
    return kit.length ? kit[0].package : null;
  }

  /**
   * 清单级的室内判据:场景的 `declaredDependencies` 点名了室内套件包。
   * 返回 `{indoor, by}`;套件包读不到时 `by` 说明输入缺失,而不是默认判成室外。
   */
  indoorFromIndex(key) {
    const rec = ((this.index || {}).scenes || {})[key];
    const kit = this.kitPackage;
    if (!rec || !kit) return { indoor: false, by: '室内套件包或场景记录读不到:判据无输入' };
    const want = pkgKey(kit);
    const hit = (rec.declaredDependencies || []).some((d) => pkgKey(d) === want);
    // 佐证:`semantics.footsteps` 说带 `_footse` 的碰撞面属于 "each outdoor site",
    // 所以没有 footstepSurface 这一角色的场景不是室外站点。
    const noFootstep = !(rec.collision || []).some((c) => c && c.role === 'footstepSurface');
    return {
      indoor: hit,
      footstepAgrees: noFootstep === hit,
      by: hit ? `declaredDependencies 点名室内套件 ${kit}` : '未点名室内套件',
    };
  }

  /** 产物里有哪几级室内扩张模块,以及每一级带不带可走面几何。面板下拉框直接用。 */
  levels() {
    const levels = ((this.index || {}).indoor || {}).levels || {};
    return Object.keys(levels).sort().map((key) => {
      const lv = levels[key] || {};
      const surfaces = (lv.walkable || {}).surfaces || [];
      return {
        key,
        module: (lv.module || {}).package || null,
        prefabs: ((lv.module || {}).prefabs || []).map((x) => x.root).filter(Boolean),
        colliders: ((lv.module || {}).colliders || []).length,
        walkablePackage: (lv.walkable || {}).package || null,
        walkableFiles: surfaces.filter((x) => x && x.file).length,
        // 盘上没有几何的那几级:记录自己写明是作者状态,原话带出来给面板显示。
        walkableNotes: surfaces.filter((x) => x && !x.file).map((x) => x.reason || '记录里没有几何'),
      };
    });
  }

  /** 产物里最高的一级(房间站点到 5 级封顶)。 */
  get topLevel() {
    return Object.keys(((this.index || {}).indoor || {}).levels || {}).sort().pop() || null;
  }

  /** 这一次拼哪一级:面板下发的那一级,没下发就是最高的一级。 */
  get activeLevel() {
    const all = Object.keys(((this.index || {}).indoor || {}).levels || {});
    return (this.level && all.includes(this.level)) ? this.level : this.topLevel;
  }

  /** 面板下发等级。传空 = 回到「取最高的一级」。 */
  setLevel(level) {
    const all = Object.keys(((this.index || {}).indoor || {}).levels || {});
    this.level = (level && all.includes(String(level))) ? String(level) : null;
    return this.activeLevel;
  }

  /** 面板下拉框的名单:键、包、室内与否,全部从清单读出来。 */
  scenes() {
    const all = (this.index || {}).scenes || {};
    return Object.keys(all).sort().map((key) => {
      const f = this.indoorFromIndex(key);
      return {
        key, indoor: f.indoor, indoorBy: f.by, footstepAgrees: f.footstepAgrees,
        package: all[key].package, declaredTriangles: all[key].triangles,
      };
    });
  }

  /**
   * 一个场景要装哪几份 glb、每份取哪几个 glTF scene。`scenes` 为 null = 用 glTF 自己的
   * 默认场景(那是游戏摆出来的那个);给了名单就按名字点名取。
   */
  _plan(key, indoor) {
    const idx = this.index || {};
    const rec = (idx.scenes || {})[key];
    const out = [];
    if (!rec) return out;
    if (rec.geometry) {
      out.push({
        part: 'scene', file: rec.geometry, scenes: null, drawn: true,
        package: rec.package || null, from: `scenes.${key}.geometry`,
        why: '场景包的默认场景',
      });
    }
    if (!indoor) return out;
    const levels = (idx.indoor || {}).levels || {};
    const lv = this.activeLevel;
    const level = lv ? levels[lv] : null;
    if (!level) { this.errors.push(`${key}: indoor.levels 里没有等级 ${lv || '?'}`); return out; }
    const fam = idx.families || {};

    // 一、这一级的扩张模块 —— 它把套件的网格摆成一间房。
    const mod = level.module || {};
    const modFam = (fam.roomModule || []).find((f) => f.package === mod.package) || null;
    if (modFam && modFam.geometry) {
      out.push({
        part: 'module', file: modFam.geometry, drawn: true, level: lv,
        scenes: (mod.prefabs || []).map((x) => x.root).filter(Boolean),
        package: mod.package, kit: this.kitPackage, from: `indoor.levels.${lv}.module`,
        why: `扩张模块 lv_${lv} · 网格出自套件 ${this.kitPackage || '(套件包读不到)'}`,
      });
    } else {
      this.errors.push(`${key}: 室内扩张模块的几何取不到(级 ${lv},包 ${mod.package || '?'})`);
    }

    // 二、这一级的可走面 —— `indoor.assembly` 把它算进「一个房间」里,所以照挂;
    //     它是碰撞面,原版不画,这里挂上不画。
    const walk = level.walkable || {};
    const walkFam = (fam.roomNavModule || []).find((f) => f.package === walk.package) || null;
    for (const surface of walk.surfaces || []) {
      if (!surface) continue;
      if (!surface.file || !walkFam) {
        out.push({
          part: 'walkable', skipped: true, level: lv, package: walk.package || null,
          node: surface.node || null, from: `indoor.levels.${lv}.walkable`,
          why: surface.reason || (walkFam ? '记录里没有可走面几何' : '可走面包不在 families 里'),
        });
        continue;
      }
      // 记录里的路径相对可走面包自己的目录;已经带上目录的就照用,不重复拼一层。
      const rel = String(surface.file).startsWith(`${walkFam.directory}/`)
        ? String(surface.file) : `${walkFam.directory}/${surface.file}`;
      out.push({
        part: 'walkable', file: rel, scenes: null, drawn: false, level: lv,
        package: walk.package || null, node: surface.node || null,
        from: `indoor.levels.${lv}.walkable`,
        why: `可走面 ${surface.mesh || surface.node || '(未命名)'}(碰撞面,原版不画)`,
      });
    }
    return out;
  }

  /** 场景文档(逐场景一份,最大的一份 6 MB,所以只在挂载当前场景时读)。 */
  async _document(key, fetchJson) {
    const rec = ((this.index || {}).scenes || {})[key];
    if (!rec || !rec.document) return null;
    return fetchJson(`${this.root}/${rec.document}`);
  }

  /** 挂一个场景。同一个键重复挂不做事;换键先卸再挂。 */
  async mount(key, fetchJson) {
    if (this.key === key && this.mounted) return this.mounted;
    this.mounting = this._mount(key, fetchJson);
    try { return await this.mounting; } finally { this.mounting = null; }
  }

  async _mount(key, fetchJson) {
    this.unmount();
    const rec = ((this.index || {}).scenes || {})[key];
    if (!rec) { this.errors.push(`site/index.json 里没有场景 ${key}`); return null; }
    const fromIndex = this.indoorFromIndex(key);
    const doc = await this._document(key, fetchJson);
    // 主判据:场景文档里 `env` 槽的角色原话说「室外空、室内装着房间体积」。
    const envSlot = doc ? (doc.slots || []).find((s) => s && s.name === 'env') : null;
    const fromSlot = envSlot ? (num(envSlot.nodes) > 1 || num(envSlot.colliders) > 0 || num(envSlot.renderers) > 0) : null;
    const indoor = fromSlot === null ? fromIndex.indoor : fromSlot;
    const primary = doc ? (doc.roots || []).find((r) => r && r.primary) : null;

    const plan = this._plan(key, indoor);
    const inactive = doc ? doc.inactiveNodes || [] : [];
    const disabled = doc ? doc.disabledRenderers || [] : [];
    const files = [];
    let meshes = 0, triangles = 0, drawnTriangles = 0;
    let hiddenMeshes = 0, hiddenTriangles = 0, unresolvedHides = 0;
    for (const step of plan) {
      // 编排里点了名、但盘上没有几何的那一件。记录自己写明这是作者状态而不是查找失败,
      // 所以照抄那句话记一笔,**不当成错误**、也不拿别的东西顶替。
      if (step.skipped) {
        files.push({
          part: step.part, from: step.from, package: step.package, node: step.node || null,
          file: null, why: step.why, ok: false, skipped: true, meshes: 0, triangles: 0,
        });
        continue;
      }
      let gltf = null;
      try {
        gltf = await this.loader.loadAsync(`${this.root}/${step.file}`);
      } catch (e) {
        this.errors.push(`${step.file} → ${String(e).slice(0, 90)}`);
        files.push({
          part: step.part, from: step.from, package: step.package,
          file: step.file, why: step.why, ok: false, meshes: 0, triangles: 0,
        });
        continue;
      }
      const picked = step.scenes
        ? step.scenes.map((n) => (gltf.scenes || []).find((s) => s.name === n)).filter(Boolean)
        : [gltf.scene].filter(Boolean);
      if (step.scenes && picked.length !== step.scenes.length) {
        this.errors.push(`${step.file}: 点名的 ${step.scenes.length} 个 scene 只取到 ${picked.length} 个`);
      }
      let m = 0, t = 0, h = 0, ht = 0;
      for (const root of picked) {
        // `inactiveNodes` / `disabledRenderers` 是**场景文档**里的路径,只对场景包那一件有意义;
        // 拿它去套模块或可走面会指不到任何节点,白记一堆「指不到」。
        if (step.part === 'scene' && !step.scenes) {
          unresolvedHides += this._hide(root, inactive, disabled);
        }
        // 原版不画的那一件:挂上,但不画。可见性一关,下面的计数自己会把它算进「隐藏」。
        if (step.drawn === false) root.visible = false;
        const c = this._convert(root);
        m += c.meshes; t += c.triangles; h += c.hidden; ht += c.hiddenTriangles;
        this.group.add(root);
      }
      meshes += m; triangles += t; hiddenMeshes += h; hiddenTriangles += ht;
      drawnTriangles += t - ht;
      files.push({
        part: step.part, from: step.from, package: step.package, kit: step.kit || null,
        level: step.level || null, node: step.node || null,
        file: step.file, why: step.why, ok: true, drawn: step.drawn !== false,
        scenes: picked.map((x) => x.name), meshes: m, triangles: t, drawnTriangles: t - ht,
      });
    }

    this.key = key;
    this.mounted = {
      key,
      indoor,
      indoorBy: fromSlot === null
        ? `场景文档读不到,落到清单级判据(${fromIndex.by})`
        : `场景文档 env 槽${fromSlot ? '有' : '空'}内容 —— 槽角色原话:室外空、室内装房间体积`,
      indoorFromIndex: fromIndex.indoor,
      indoorDisagree: fromSlot !== null && fromSlot !== fromIndex.indoor,
      footstepAgrees: fromIndex.footstepAgrees,
      // 室内拼装:哪一级、这一级从哪儿来的、拼上去的每一件各自的出处与三角数。
      level: indoor ? this.activeLevel : null,
      levelSource: !indoor ? null
        : (this.level
          ? '面板下发(等级是 master 表的字段,本仓没有 master,所以做成旋钮)'
          : '产物里最高的一级(等级是 master 表的字段,本仓没有 master)'),
      levelsAvailable: indoor ? this.levels().map((x) => x.key) : [],
      // 场景包之外的那几件 —— 就是 `indoor.assembly` 点名的那份编排。
      assembly: files.filter((f) => f.part !== 'scene'),
      files,
      meshes,
      triangles,
      // 真的画出来的三角数(总数里扣掉原版不画的那些:隐藏的节点、以及可走面这种碰撞面)。
      drawnTriangles,
      hiddenMeshes,
      hiddenTriangles,
      // 文档说不画、但路径指不到节点的条数。这一项不为零 = 画面上多画了这么多处。
      unresolvedHides,
      declaredHides: inactive.length + disabled.length,
      materials: this.materials.length,
      blend: this.blendCounts,
      declared: primary
        ? { root: primary.name, renderers: primary.renderers, vertices: primary.vertices, triangles: primary.triangles }
        : null,
      documentRead: !!doc,
    };
    return this.mounted;
  }

  /**
   * 原版不画的东西照文档关掉。两张表**语义不同**,分开处理:
   *   `inactiveNodes`     —— 节点自己关着,整条支路都不画;
   *   `disabledRenderers` —— 只有那个节点上的绘制件关着,子节点照画。three 的 `visible`
   *                          是沿层级传的,所以只有「本身是网格且没有子节点」的那种关得干净;
   *                          带子节点的那种关不掉**只关自己**,如实计一笔而不是连子树一起关。
   * 空路径(当前内容里出现过一条)在文档里指不到任何节点:**不猜**,记一笔跳过 ——
   * 把它当成主根节点会把整个站点一起关掉。
   */
  _hide(root, inactive, disabled) {
    // glTF scene 的孩子才是那一个主根节点,文档里的路径从主根节点的孩子算起。
    const base = root.children.length === 1 ? root.children[0] : root;
    // 装载器把节点名**规范化**过(空白换下划线、几个保留字符删掉),文档里的路径是原名。
    // 不按同一条规范化去找,带空格的那一层就永远指不到 —— 当前内容里「decoration (1)」
    // 这一支就是这样,不处理就有一整支该隐藏的节点照画。
    const norm = (s) => THREE.PropertyBinding.sanitizeNodeName(String(s));
    const find = (path) => {
      let node = base;
      // 路径分隔符是 `/`,而规范化会把 `/` 删掉,所以**先切段再逐段规范化**。
      for (const seg of String(path).split('/')) {
        const want = norm(seg);
        const kids = node.children || [];
        let hit = kids.filter((c) => c.name === seg || c.name === want);
        if (!hit.length) {
          // 装载器还要保证节点名在整份文件里唯一,重名的第二个起被加上 `_N` 后缀。
          // 先按原名找,找不到才认这个后缀式样 —— 反过来会把真叫 `x_2` 的节点当成 `x`。
          const re = new RegExp(`^${want.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}_\\d+$`);
          hit = kids.filter((c) => re.test(c.name));
        }
        // 同名兄弟节点是有的(装载器给它们加了后缀,原名一样)。取**第一个** —— 与运行时
        // 按路径查子节点的取法一致:路径本身在这种情况下也只指得到第一个。
        if (!hit.length) return null;
        node = hit[0];
      }
      return node;
    };
    let skipped = 0;
    for (const path of inactive || []) {
      if (path === '') { skipped += 1; continue; }
      const node = find(path);
      if (node) node.visible = false;
      else skipped += 1;
    }
    for (const path of disabled || []) {
      if (path === '') { skipped += 1; continue; }
      const node = find(path);
      if (node && node.isMesh && !(node.children || []).length) node.visible = false;
      else skipped += 1;
    }
    if (skipped) {
      this.errors.push(`${root.name || '场景'}: 原版不画的路径有 ${skipped} 条指不到节点,照实跳过`);
    }
    return skipped;
  }

  /** 逐网格换材质并计数。隐藏的网格照样计数 —— 它在场景里,只是不画。 */
  _convert(root) {
    let meshes = 0, triangles = 0, hidden = 0, hiddenTriangles = 0;
    const cache = new Map();
    root.updateMatrixWorld(true);
    root.traverse((o) => {
      if (!o.isMesh || !o.geometry) return;
      meshes += 1;
      const idx = o.geometry.getIndex();
      const pos = o.geometry.getAttribute('position');
      const tris = idx ? idx.count / 3 : (pos ? Math.floor(pos.count / 3) : 0);
      triangles += tris;
      // 可见性沿祖先链算:整条支路被关掉时,这一格也是不画的。
      let vis = true;
      for (let p = o; p; p = p.parent) if (!p.visible) { vis = false; break; }
      if (!vis) { hidden += 1; hiddenTriangles += tris; }
      if (!o.geometry.getAttribute('normal')) o.geometry.computeVertexNormals();
      const src = Array.isArray(o.material) ? o.material[0] : o.material;
      if (!cache.has(src)) cache.set(src, this._materialFor(src));
      o.material = cache.get(src);
    });
    return { meshes, triangles, hidden, hiddenTriangles };
  }

  _materialFor(src) {
    const map = src && src.map ? src.map : null;
    if (map) {
      // gamma 直通:与角色贴图同一条规矩,采样不做 sRGB 解码。
      map.colorSpace = THREE.NoColorSpace;
      map.updateMatrix();
    }
    const c = src && src.color ? src.color : { r: 1, g: 1, b: 1 };
    const ud = (src && src.userData) || {};
    const bf = ud.blendFactors || null;
    const mat = new THREE.ShaderMaterial({
      name: `env-site:${(src && src.name) || 'unnamed'}`,
      uniforms: withEnvGlobals({
        ...SITE_SUBJECT,
        siteMap: { value: map || ENV_WHITE_TEX },
        siteHasMap: { value: map ? 1 : 0 },
        siteBaseColor: { value: new THREE.Vector4(c.r, c.g, c.b, src ? src.opacity : 1) },
        siteAlphaCutoff: { value: src ? num(src.alphaTest) : 0 },
        siteUvTransform: { value: map ? map.matrix : new THREE.Matrix3() },
      }),
      vertexShader: SITE_VERT,
      fragmentShader: SITE_FRAG,
      side: src ? src.side : THREE.FrontSide,
      transparent: !!(src && src.transparent),
      depthWrite: src ? src.depthWrite : true,
    });
    if (bf && UNITY_BLEND_FACTOR[bf.src] !== undefined && UNITY_BLEND_FACTOR[bf.dst] !== undefined) {
      // 混合因子照 extras 给的那一对写,不按 `blendMode` 的字符串猜。
      mat.blending = THREE.CustomBlending;
      mat.blendSrc = UNITY_BLEND_FACTOR[bf.src];
      mat.blendDst = UNITY_BLEND_FACTOR[bf.dst];
      mat.transparent = true;
      // 叠加(dst = One)不写深度,否则后面的东西被它挡掉。
      if (bf.dst === 1) mat.depthWrite = false;
      this._countBlend(ud.blendMode || `${bf.src}/${bf.dst}`);
    } else if (bf) {
      this._countBlend('未知因子对');
      this.errors.push(`材质 ${(src && src.name) || '?'}: blendFactors ${JSON.stringify(bf)} 不在枚举里`);
    } else {
      this._countBlend(mat.transparent ? '混合(无 extras)' : '不透明');
    }
    this.materials.push(mat);
    return mat;
  }

  _countBlend(kind) {
    if (!this.blendCounts) this.blendCounts = {};
    this.blendCounts[kind] = (this.blendCounts[kind] || 0) + 1;
  }

  unmount() {
    for (const child of [...this.group.children]) {
      child.traverse((o) => { if (o.isMesh && o.geometry) o.geometry.dispose(); });
      this.group.remove(child);
    }
    for (const m of this.materials) m.dispose();
    this.materials = [];
    this.blendCounts = {};
    this.key = null;
    this.mounted = null;
  }

  setSubject(v3, radius) {
    SITE_SUBJECT.envSubjectPos.value.copy(v3);
    if (radius) SITE_SUBJECT.envSubjectRadius.value = radius;
  }

  dispose() {
    this.unmount();
    this.group.removeFromParent();
  }
}

// ---- 粒子 -----------------------------------------------------------------
//
// 现象粒子与头顶件是**同一套编码**,所以走 emoticon.js 的同一个发射器引擎(`createParticleEffect`),
// 不复制代码。三族按语义挂:
//
//   `sky`    → 挂世界(天空粒子不跟相机、也不跟角色)
//   `camera` → 挂相机(跟着视点走)
//   `site`   → 挂世界(属于某个站点;当前站点选谁就只挂谁的那一份)
//   `other`  → 名字不符合上面三族的命名式样,**它的挂法不由名字说明**;本示例按站点粒子挂世界,
//              并在 status().effects 里标出来,不假装它有确定归属。
//
// 未建模的发射形状(Box 等,以及网格没解出来的那几个「从网格表面发射」)在发射器引擎里**整条停发**
// (不退化成点发射:那会把粒子全堆在发射节点原点上,画面上就是「粒子堆在角色身上」)。
// 本模块两处都数:装载时按文档数出来(`particles.unmodelledShapes`),挂上之后按活发射器数出来
// (`particles.suppressed` / `suppressedShapes`,只算当前站点真的挂着的那些)。

const FX_KINDS = ['sky', 'camera', 'site', 'other'];

// 相机族的跟随律**不统一**,一刀切会让多数效果的方向错。两种律:
//
//   'rotate' —— 随相机一起转(效果自带那个环境效应器组件、走零参装配)。
//   'fixed'  —— 位置跟随,**旋转反向抵消**:本地旋转 = 父节点世界旋转的逆。
//
// 当前内容里 15 个相机族效果只有 4 个是 'rotate',其余 11 个是 'fixed'。判据就在产物里:
// 带那个效应器组件的效果会以 `prefab component not modelled` 出现在 `summary.unsupported`,
// 所以**不必按现象名写死名单**,按数据判。派生字段(提取侧将给出的 `cameraFollow` 之类)
// 一旦出现就优先用它 —— 见 `cameraFollowOf`。
export const CAMERA_FOLLOW = { ROTATE: 'rotate', FIXED: 'fixed' };
const EFFECTOR_COMPONENT = 'SiteEnvironmentEffector';

/**
 * 一个相机族效果的跟随律。三级取值,越靠前越可信:
 *   1) 效果自带的派生字段(契约字段名以最终产物为准,这里认几个同义写法);
 *   2) 该效果在 `summary.unsupported` 里带那个效应器组件 → 'rotate';
 *   3) 都没有 → 'fixed'(多数律)。
 * 返回 `{law, source}`,`source` 进 status(),便于看出这一条是读出来的还是落到了默认。
 */
export function cameraFollowOf(name, effect, unsupported) {
  const declared = effect && (effect.cameraFollow || effect.effectiveRotation || effect.rotationMode);
  if (typeof declared === 'string') {
    const v = declared.toLowerCase();
    if (v.includes('rotate') || v.includes('follow')) return { law: CAMERA_FOLLOW.ROTATE, source: 'contract-field' };
    if (v.includes('fix') || v.includes('inverse') || v.includes('cancel')) {
      return { law: CAMERA_FOLLOW.FIXED, source: 'contract-field' };
    }
  }
  const hit = (unsupported || []).some((u) => u && u.effect === name && u.component === EFFECTOR_COMPONENT);
  if (hit) return { law: CAMERA_FOLLOW.ROTATE, source: 'effector-component' };
  return { law: CAMERA_FOLLOW.FIXED, source: 'default-majority' };
}

class EffectSet {
  constructor(doc, textureFor) {
    this.doc = doc || { effects: {} };
    this.textureFor = textureFor;
    this.unsupported = ((doc || {}).summary || {}).unsupported || [];
  }

  /** 该现象在当前站点下要挂的效果名单(两级:global/common 的通用件 + 当前站点的件)。 */
  plan(site, { includeSite = true } = {}) {
    const out = [];
    for (const [name, e] of Object.entries(this.doc.effects || {})) {
      const kind = FX_KINDS.includes(e.kind) ? e.kind : 'other';
      if (kind === 'site') {
        // `site` 为 null 的站点件来自 common/global 包 —— 它对所有站点通用(雨滴、地面效果这类)。
        if (!includeSite) continue;
        if (e.site && e.site !== site) continue;
      }
      const follow = kind === 'camera' ? cameraFollowOf(name, e, this.unsupported) : null;
      out.push({ name, kind, site: e.site || null, variant: e.variant || null, effect: e, follow });
    }
    return out;
  }
}

/**
 * 一份效果文档里用到网格的两处引用到的 glb 文件名(去重)。**两处都要收**:
 *   - 渲染器上的 `meshes`:Mesh 绘制模式最多挂 4 个网格,逐粒子随机选一个;
 *   - 形状上的 `meshes`:从网格表面发射,发射公式要读它的三角。
 * 漏掉后一处的表现是那些发射器整条停发(取不到网格),而不是报错。
 */
function meshFilesOf(doc) {
  const out = new Set();
  for (const e of Object.values((doc && doc.effects) || {})) {
    for (const p of e.particles || []) {
      const lists = [((p.renderer || {}).meshes) || [],
                     (((p.system || {}).shape || {}).meshes) || []];
      for (const list of lists) {
        for (const m of list) if (m && m.file) out.add(m.file);
      }
    }
  }
  return [...out];
}

/**
 * 数一份效果文档里用到但发射器引擎没建模的形状。判定走发射器引擎的 `shapeSupport`,
 * **不是**只看形状名在不在表里:有的形状是有条件的(从网格表面发射,网格没解出来的
 * 那几个照样停发),两处若用不同的判据,装载时数出来的与挂上之后数出来的就对不上。
 */
export function unmodelledShapes(doc) {
  const counts = {};
  let noShape = 0, total = 0;
  for (const e of Object.values((doc && doc.effects) || {})) {
    for (const p of e.particles || []) {
      total++;
      const shape = (p.system || {}).shape;
      const t = (shape || {}).type;
      if (!t) { noShape++; continue; }
      if (shapeSupport(shape) === 'unimplemented') counts[t] = (counts[t] || 0) + 1;
    }
  }
  return { counts, noShape, total };
}

// ---- 环境层 ---------------------------------------------------------------

export class Environment {
  /**
   * @param opts `{scene, camera, renderer, base}` —— `base` 是 `phenomena/` 的父目录
   */
  constructor(opts) {
    this.scene = opts.scene;
    this.camera = opts.camera;
    this.renderer = opts.renderer;
    this.base = String(opts.base || '.').replace(/\/+$/, '');
    this.root = `${this.base}/phenomena`;
    this.textureFor = makeSharedTextureLoader(`${this.root}/`);
    // Mesh 绘制模式要的网格。加载是异步的、绘制件是同步建的,所以每换一个现象都要在
    // 挂载**之前**把它引用到的 glb 全部读进来(见 loadPhenomenon 末尾那次 preload)。
    this.meshLoader = makeSharedMeshLoader(`${this.root}/`);
    this.meshFor = (file, node) => this.meshLoader.get(file, node);
    // 天空网格的来源可能落在 `phenomena/` 之外(站点系统的共享包),所以这一份按**完整相对
    // 路径**取,不预设根目录。
    this.skyMeshLoader = makeSharedMeshLoader('');

    this.index = null;
    this.names = [];            // 现象资产名(目录名),升序
    this.loaded = new Map();    // 资产名 → { config, ramp, profile, fx, overrides }
    this.enabled = false;
    this.particlesOn = true;
    this.indoor = false;        // 「室内覆盖」= 用带覆盖的那个站点(现象两级查找的第一级)
    this.site = 'grasslands';   // 当前站点(几何挂哪一站 + 站点粒子挂哪一站)
    this.overrideSite = null;   // 带覆盖的站点名(从 index 读出,不写死)
    // 当前站点**是不是室内**。这一项由站点产物判出来(见 SiteView 顶上的注释),
    // 与上面那个手动开关是两回事:开关选的是「用谁的现象配置」,这一项是「站在哪里」。
    this.siteIndoor = false;

    this.sky = new SkyDome();
    this.siteView = new SiteView({ base: this.base });
    this.skyVisible = true;
    this.siteVisible = true;
    this.group = new THREE.Group();
    this.group.name = 'env_root';
    this.fxWorld = new THREE.Group();
    this.fxWorld.name = 'env_fx_world';
    this.fxCamera = new THREE.Group();
    this.fxCamera.name = 'env_fx_camera';
    this.group.add(this.sky.mesh, this.siteView.group, this.fxWorld);
    this.camera.add(this.fxCamera);

    this.post = new PostChain(this.renderer, { blackTexture: ENV_BLACK_TEX });
    this.postOn = true;

    this.from = null;           // 淡出侧 { name, light, profile, ramp, config }
    this.to = null;             // 淡入侧
    this.fade = 1;              // 0..1 进度(1 = 已完成)
    this.fadeDuration = CROSS_FADE_SECONDS;
    this.fadeElapsed = 0;
    this.fadeRuns = [];         // 实测淡化时长(自检读它)
    this.time = 0;
    this.effects = [];          // 当前挂着的效果 view
    this.retiring = [];         // 换天气后正在淡出的旧效果(存活 >= EFFECT_RETIRE_SECONDS)
    this.effectNotes = [];
    this.subject = new THREE.Vector3(0, 0.6, 0);
    this.characterMaterials = [];
    this.errors = [];
    this.fxDataError = null;     // 清单声明了发射器却读不出效果文档时的如实记录
    this.skyMeshNote = '天空尚未装载';
    this.skyMaterial = null;     // 天空材质记录(渐变窗口的**唯一**出处,不另存两个数字)
    this.skyGradientSource = null;
    // 附加色的**底值**:没有 clip 活着的那些拍就用它。来源是天空材质自带的那一项;
    // 附加强度没有任何材质属性对应,底值只能是 0(不出力),这一条如实记在 notRestored 里。
    this.skyAdditiveDefault = [0, 0, 0, 0];
    this.skyAdditiveDefaultSource = null;
    // 时间轴时钟。秒,按当前现象自己的 `duration` 循环;换现象时归零 —— 产物里那个空的
    // 时间轴插槽写着 `initialTime: 0.0`,现象是运行时才被塞进去的,所以塞进去就是从头。
    this.timelineTime = 0;
    this.timelinePlaying = true;
    this.timelineSample = null;    // 这一拍四条轨各算出什么(status 读它)
    this.timelineApplied = null;   // 这一拍真的写出去的四项 + 叠加后的现象方向光色
    this.timelineDataError = null; // 清单声明了时间轴却读不出文档时的如实记录
    this.timelineNames = [];       // 清单里带时间轴的现象名(判据据此知道这是不是唯一一个)
  }

  // ---- 装载 ----

  async load() {
    const idx = await this._json(`${this.root}/index.json`);
    if (!idx) { this.errors.push('phenomena/index.json 读不到'); return false; }
    this.index = idx;
    this.names = Object.keys(idx.phenomena || {}).sort();
    // 带覆盖的站点名从数据读出(当前内容里 14 个现象各有一处,全是同一个室内站点)。
    const sites = new Set();
    for (const p of Object.values(idx.phenomena || {})) {
      for (const s of Object.keys(p.overrides || {})) sites.add(s);
    }
    this.overrideSite = [...sites].sort()[0] || null;
    // 带时间轴的现象名。**从清单读**,不写死「只有打雷天有」——- 数据一变名单跟着变。
    this.timelineNames = Object.keys(idx.phenomena || {})
      .filter((n) => (idx.phenomena[n] || {}).timeline).sort();
    this.overrideSites = [...sites].sort();
    // 站点清单:面板的站点名单与室内判据都从这里来(几何要到 attach / 换站点时才装)。
    await this.siteView.load((url) => this._json(url));
    const keys = this.siteView.scenes().map((s) => s.key);
    // 默认站点必须在产物名单里 —— 写死的名字一旦不在名单里,面板会显示一个装不上的站点。
    if (keys.length && !keys.includes(this.site)) this.site = keys[0];
    this.siteIndoor = this.siteView.indoorFromIndex(this.site).indoor;
    await this._loadSky(idx);
    return true;
  }

  /** 站点名单(面板下拉框直接用):键 + 室内与否 + 判据出处。 */
  siteScenes() { return this.siteView.scenes(); }

  /** 室内等级名单(面板下发用)。等级本身是 master 表的字段,本仓没有 master。 */
  siteLevels() { return this.siteView.levels(); }

  /** 当前拼的是哪一级(室外时这一项没有意义,照样返回名单里的默认级)。 */
  get siteLevel() { return this.siteView.activeLevel; }

  /**
   * 下发室内等级。等级由服务端的 master 表决定、本仓拿不到,所以按「服务端决定的做成面板
   * 下发」处置:改了就重拼一遍(计划变了,得让 mount 真的再跑一次)。
   */
  async setSiteLevel(level) {
    if (!this.siteView.index) return null;
    const before = this.siteView.activeLevel;
    const after = this.siteView.setLevel(level);
    if (after === before && this.siteView.mounted) return this.siteView.mounted;
    this.siteView.key = null;
    const rec = await this.siteView.mount(this.site, (url) => this._json(url));
    if (rec) this.siteIndoor = !!rec.indoor;
    this.refreshSite();
    return rec;
  }

  /**
   * 把当前站点的几何挂上。**站点没装好之前不挂粒子也不画天空** —— 室内/室外要按产物判,
   * 判据的输入就在场景文档里,抢在它到位之前挂等于按上一站的答案挂。
   */
  async ensureSite() {
    if (!this.siteView.index) return null;
    if (this.siteView.key === this.site && this.siteView.mounted) return this.siteView.mounted;
    const rec = await this.siteView.mount(this.site, (url) => this._json(url));
    if (rec) this.siteIndoor = !!rec.indoor;
    this.refreshSite();
    return rec;
  }

  /**
   * 天空的网格与材质。两条来源,先后有序:
   *
   *   1) `phenomena/index.json` 的 `sky`:`mesh` 是相对本清单的 glb 路径,`material` 是材质名,
   *      `gradient.minY` / `gradient.maxY` 是渐变窗口。清单一旦给出这一条就优先。
   *   2) 站点包清单:那个**自带网格**的 shell 包 —— 天空视图脚本与天空材质都在它里面。
   *      同类的另一个 shell 包只挂着同一个脚本却没有几何,所以按「带网格」筛,不按名字筛。
   *      包文档的文件名是几何文件换后缀;文档里带齐渐变属性的那份材质就是天空材质。
   *
   * 两条都不成:**天空不画**,原因进 `errors` 与 `status()`。窗口尤其不拿默认值顶替 ——
   * 顶替之后材质一改,画面照旧按旧窗口算,谁都看不出来。
   */
  async _loadSky(idx) {
    const plan = this._skyPlanFromIndex(idx) || await this._skyPlanFromSite();
    if (!plan) {
      this.skyMeshNote = '天空网格与材质都没解析到:天空不画';
      return false;
    }
    this.skyMaterial = plan.material;
    this.skyGradientSource = plan.gradientSource;
    // 材质自带的附加色 = 没有 clip 驱动它时的底值。当前这份材质里它是全零,所以画面上
    // 看不出差别 —— 但读出来与不读是两件事,这里读。
    const authored = (plan.material && plan.material.colors)
      ? plan.material.colors[SKY_ADDITIVE_COLOR] : null;
    this.skyAdditiveDefault = authored ? col4(authored, [0, 0, 0, 0]) : [0, 0, 0, 0];
    this.skyAdditiveDefaultSource = authored
      ? `材质 ${plan.materialName || '(未命名)'} 的 ${SKY_ADDITIVE_COLOR}`
      : `${plan.gradientSource} 里没有 ${SKY_ADDITIVE_COLOR}:底值取零`;
    const win = skyGradientWindow(plan.material);
    if (!win) this.errors.push(`${plan.gradientSource}: 渐变窗口读不出`);
    await this.skyMeshLoader.preload([plan.url]);
    const scene = this.skyMeshLoader.get(plan.url);
    const picked = scene ? pickSkyMesh(scene, plan.materialName) : null;
    if (!picked) {
      this.errors.push(`${plan.url} → 天空网格取不到`
        + `${plan.materialName ? `(按材质 ${plan.materialName} 找)` : ''}`);
      this.skyMeshNote = `天空网格 ${plan.meshSource} 取不到:天空不画`;
      return false;
    }
    // 网格在包里挂在几层节点之下,把那几层的变换烘进几何 —— 之后这个网格自己跟相机走。
    picked.updateWorldMatrix(true, false);
    this.sky.useGeometry(picked.geometry.clone().applyMatrix4(picked.matrixWorld),
      plan.meshSource, picked.name || null);
    this.sky.setGradientWindow(win, plan.gradientSource);
    const info = this.sky.meshInfo || {};
    this.skyMeshNote = this.sky.ready
      ? `天空网格 ${plan.meshSource}(${info.vertices} 顶点 / ${info.triangles} 三角)`
        + ` · 渐变窗口 [${win.min}, ${win.max}] 读自 ${plan.gradientSource}`
      : `天空装载不全(网格 ${info.uv ? '有' : '缺'} UV0 · 窗口 ${win ? '有' : '缺'}):天空不画`;
    return this.sky.ready;
  }

  /** 来源一:现象清单自己给出天空。给了就照它走,连窗口一起。 */
  _skyPlanFromIndex(idx) {
    const s = (idx && idx.sky) || null;
    if (!s || !s.mesh) return null;
    const g = s.gradient || {};
    return {
      url: `${this.root}/${s.mesh}`,
      materialName: s.material || null,
      meshSource: `phenomena/index.json sky.mesh(${s.mesh})`,
      // 摆成与材质记录同一个形状,下游只认一种取法。
      material: { floats: { [SKY_GRADIENT_MIN]: +g.minY, [SKY_GRADIENT_MAX]: +g.maxY } },
      gradientSource: 'phenomena/index.json sky.gradient',
    };
  }

  /** 来源二:站点包清单里那个自带网格的 shell 包。 */
  async _skyPlanFromSite() {
    const doc = await this._json(`${this.base}/site/packages.json`);
    const list = doc && doc.packages ? Object.values(doc.packages) : null;
    if (!list) return null;
    const hit = list.find((p) => p && p.kind === 'shell'
      && ((p.inventory || {}).scripts || {})[SKY_VIEW_SCRIPT]
      && num(((p.inventory || {}).types || {}).Mesh) > 0
      && (p.artifacts || {}).geometry);
    if (!hit) { this.errors.push('site/packages.json 里没有自带网格的天空 shell 包'); return null; }
    const dir = `${this.base}/site/${hit.directory}`;
    const geo = String(hit.artifacts.geometry);
    const pkg = await this._json(`${dir}/${geo.replace(/\.[^./]+$/, '')}.json`);
    if (!pkg) return null;
    const mat = skyMaterialOf(pkg);
    if (!mat) { this.errors.push(`${hit.key}: 包里没有带齐渐变属性的天空材质`); return null; }
    const file = (pkg.geometry || {}).file || geo;
    return {
      url: `${dir}/${file}`,
      materialName: mat.name || null,
      meshSource: `site/${hit.directory}/${file}`,
      material: mat,
      gradientSource: `材质 ${mat.name || '(未命名)'}(site/${hit.directory})`,
    };
  }

  /** 一个现象的四份产物 + 覆盖。已装载的直接返回(现象资产很轻,全量常驻没有压力)。 */
  async loadPhenomenon(name) {
    if (this.loaded.has(name)) return this.loaded.get(name);
    const entry = this.index && this.index.phenomena && this.index.phenomena[name];
    if (!entry) { this.errors.push(`index.json 里没有 ${name}`); return null; }
    const rel = (p) => `${this.root}/${p}`;
    const declaredEmitters = entry.fx ? num(entry.fx.emitters) : 0;
    const [config, profileDoc, fx, timelineDoc] = await Promise.all([
      this._json(rel(entry.config)),
      entry.postprocess ? this._json(rel(entry.postprocess)) : null,
      entry.fx && entry.fx.file ? this._json(rel(entry.fx.file)) : null,
      entry.timeline && entry.timeline.file ? this._json(rel(entry.timeline.file)) : null,
    ]);
    // 清单声明了时间轴却读不出文档 = **数据缺口**,不是「这个现象没有时间轴」。
    if (entry.timeline && entry.timeline.file && !timelineDoc) {
      this.timelineDataError = `${name}: 清单声明了时间轴 ${entry.timeline.file},但读不出来`;
    }
    // 清单声明了发射器却读不出效果文档 = **数据缺口**,不是「这个现象没粒子」。
    // 如实记下来(面板与自检照实显示),不容错解析、不猜。
    if (declaredEmitters > 0 && !fx) {
      this.fxDataError = `${name}: 清单声明 ${declaredEmitters} 个发射器,但 fx/effects.json 读不出`
        + `(${(this.errors[this.errors.length - 1] || '').slice(0, 120)})`;
    }
    const ramp = entry.ramp && entry.ramp.file ? this._ramp(rel(entry.ramp.file)) : null;
    const overrides = {};
    for (const [site, o] of Object.entries(entry.overrides || {})) {
      const [oc, op] = await Promise.all([
        o.config ? this._json(rel(o.config)) : null,
        o.postprocess ? this._json(rel(o.postprocess)) : null,
      ]);
      overrides[site] = { config: oc, profile: op ? flattenProfile(op) : null };
    }
    const rec = {
      name, entry, config, ramp, overrides,
      timeline: timelineDoc ? new PhenomenonTimeline(timelineDoc) : null,
      timelineDeclared: entry.timeline || null,
      profile: profileDoc ? flattenProfile(profileDoc) : null,
      fx: fx ? new EffectSet(fx, this.textureFor) : null,
      shapes: fx ? unmodelledShapes(fx) : null,
      declaredEmitters,
      rampSize: entry.ramp ? { width: num(entry.ramp.width), height: num(entry.ramp.height) } : null,
    };
    // Mesh 绘制模式的网格要在挂载**之前**读完 —— 绘制件是同步建的,glb 加载是异步的。
    // 这一步失败不阻塞现象加载:取不到的绘制件整条不画并计数,那是如实的缺失。
    rec.meshes = fx ? await this.meshLoader.preload(meshFilesOf(fx)) : null;
    this.loaded.set(name, rec);
    return rec;
  }

  async _json(url) {
    try {
      const r = await fetch(url);
      if (!r.ok) { this.errors.push(`${url} → HTTP ${r.status}`); return null; }
      return await r.json();
    } catch (e) { this.errors.push(`${url} → ${String(e).slice(0, 80)}`); return null; }
  }

  /** 渐变纹理:32x1,**最近邻不适用**(要平滑过渡),但两端必须 clamp,不能重复。 */
  _ramp(url) {
    const t = new THREE.TextureLoader().load(url);
    t.colorSpace = THREE.NoColorSpace;      // 与角色贴图同一条 gamma 直通规矩
    t.wrapS = THREE.ClampToEdgeWrapping;
    t.wrapT = THREE.ClampToEdgeWrapping;
    t.minFilter = THREE.LinearFilter;
    t.magFilter = THREE.LinearFilter;
    t.generateMipmaps = false;
    return t;
  }

  /** 云影/风噪贴图:平铺采样。 */
  _tileTex(file) {
    if (!file) return null;
    const t = this.textureFor(file);
    t.colorSpace = THREE.NoColorSpace;
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.RepeatWrapping;
    return t;
  }

  // ---- 切换 ----

  /**
   * 切到一个现象。`seconds` 缺省用契约里的 0.25 秒;传 0 = 瞬切(截图语义)。
   * 首次切换没有淡出侧,所以直接落到目标值(t=1)。
   */
  async setPhenomenon(name, seconds = CROSS_FADE_SECONDS) {
    const rec = await this.loadPhenomenon(name);
    if (!rec) return false;
    const prev = this.to;
    this.from = prev ? { name: prev.name, rec: prev.rec, state: this._stateOf(prev.rec) } : null;
    this.to = { name, rec, state: this._stateOf(rec) };
    this.fadeDuration = Math.max(0, num(seconds, CROSS_FADE_SECONDS));
    this.fadeElapsed = 0;
    // 换现象 = 时间轴从头起。产物里那个空插槽写着 `initialTime: 0.0`,现象的时间轴是运行时
    // 被赋给导演的,所以赋上去就是从 0 开始 —— 不是接着上一个现象的时钟往下跑。
    if (!prev || prev.name !== name) this.timelineTime = 0;
    this.fade = (this.from && this.fadeDuration > 0) ? 0 : 1;
    this._fadeStart = (this.from && this.fadeDuration > 0) ? performance.now() : null;
    this._mountEffects();
    this._applyBlend();
    // 瞬切(时长 0 或首次落地)时 fade 已经是 1,而清理淡出侧的代码只在 update() 的
    // `fade < 1` 分支里 —— 不在这里丢掉它,`from` 会永远挂着:状态会谎报「仍在从 X 淡入」,
    // 旧现象的退场特效也等不到收口。
    if (this.fade >= 1) this.from = null;
    return true;
  }

  /** 当前站点下该现象的**有效**配置与档案(两级查找:站点覆盖 → 全局)。 */
  _stateOf(rec) {
    const site = this.indoor ? this.overrideSite : this.site;
    const ov = (site && rec.overrides[site]) || null;
    const config = (ov && ov.config) || rec.config;
    const profile = (ov && ov.profile) || rec.profile;
    const cloud = (config || {}).cloud || {};
    const wind = (config || {}).wind || {};
    return {
      site,
      usedOverride: !!ov,
      config,
      profile,
      light: lightStateOf(config, { homeSite: site === 'home' }),
      ramp: rec.ramp,
      cloudTex: this._tileTex(cloud.cloudShadowTexture ? cloud.cloudShadowTexture.file : null),
      windTex: this._tileTex(wind.windNoiseTexture ? wind.windNoiseTexture.file : null),
    };
  }

  /** 站点/室内开关变了 → 重取两级查找的结果并重挂粒子(不走淡化:这是视角切换,不是换天气)。 */
  refreshSite() {
    if (this.to) this.to.state = this._stateOf(this.to.rec);
    if (this.from) this.from.state = this._stateOf(this.from.rec);
    this._mountEffects();
    // 天空的室内闸门与粒子同一处生效:换站点时立刻重算,不等下一次点开关。
    this.sky.setVisible(this.skyVisible && !this.siteIndoor);
    this._applyBlend();
  }

  // ---- 逐帧写出 ----

  /** 把混合结果写进两条通道 + 天空 + 后处理档案。 */
  _applyBlend() {
    if (!this.to) return;
    const t = this.from ? this.fade : 1;
    const a = this.from ? this.from.state : this.to.state;
    const b = this.to.state;
    const light = this.from ? blendLight(a.light, b.light, t) : b.light;
    this.light = light;

    // 通道二:光照九项
    envSet('envLightDir', light.dir);
    envSet('envCharLightColor', light.charLightColor);
    envSet('envCharSkinShade', light.charSkinShade);
    envSet('envCharBodyShade', light.charBodyShade);
    envSet('envPhenLightColor', light.phenLightColor);
    envSet('envPhenShadeColor', light.phenShadeColor);
    envSet('envDropShadowColor1', light.dropShadowColor1);
    envSet('envDropShadowColor2', light.dropShadowColor2);
    envSet('envDropShadowEdgeSmoothness', light.dropShadowEdgeSmoothness);
    // 角色组三项直接喂进既有 toon 着色(它们是那套着色器真的声明了的 uniform)。
    if (this.characterMaterials.length) {
      Shading.setLightDir(this.characterMaterials, [light.dir.x, light.dir.y, light.dir.z]);
      Shading.setLightColor(this.characterMaterials, light.charLightColor);
      Shading.setShadeColors(this.characterMaterials, light.charSkinShade, light.charBodyShade);
    }

    // 通道一:现象驱动的全局量。云影两张同时在位、按进度混合(与渐变同一条淡化规矩)。
    const ca = (a.config || {}).cloud || {}, cb = (b.config || {}).cloud || {};
    envSet('envCloudShadowTex', a.cloudTex || ENV_WHITE_TEX);
    envSet('envCloudShadowTexB', b.cloudTex || a.cloudTex || ENV_WHITE_TEX);
    envSet('envCloudFade', t);
    const sizeA = num(ca.cloudShadowTextureSize, 1) || 1, sizeB = num(cb.cloudShadowTextureSize, 1) || 1;
    // 采样尺度是**存储尺寸的倒数**(契约明写),混合在取倒数之后做。
    envSet('envCloudShadowScale', lerp(1 / sizeA, 1 / sizeB, t));
    envSet('envCloudShadowOpacity', lerp(num(ca.cloudShadowOpacity), num(cb.cloudShadowOpacity), t));
    const vA = Array.isArray(ca.cloudScrollVelocity) ? ca.cloudScrollVelocity : [0, 0];
    const vB = Array.isArray(cb.cloudScrollVelocity) ? cb.cloudScrollVelocity : [0, 0];
    const spA = num(ca.cloudScrollSpeed), spB = num(cb.cloudScrollSpeed);
    // 实际滚动速度 = 速度向量 × 标量(契约明写,这两处都不能直接照用存储值)。
    envSet('envCloudScrollSpeed', [
      lerp(num(vA[0]) * spA, num(vB[0]) * spB, t),
      lerp(num(vA[1]) * spA, num(vB[1]) * spB, t),
    ]);

    // 风九项:**真的推出去**,本示例没有消费方(无植被/家具),将来任何声明它们的材质即刻生效。
    const wa = (a.config || {}).wind || {}, wb = (b.config || {}).wind || {};
    envSet('envWindSpeed', lerp(num(wa.windSpeed), num(wb.windSpeed), t));
    envSet('envWindColor', lerp4(col4(wa.windColor, [1, 1, 1, 0]), col4(wb.windColor, [1, 1, 1, 0]), t));
    envSet('envVertexWaveAmount', lerp(num(wa.vertexWaveAnimationAmount), num(wb.vertexWaveAnimationAmount), t));
    envSet('envVertexWaveExponent', lerp(num(wa.vertexWaveExponent, 1), num(wb.vertexWaveExponent, 1), t));
    envSet('envVertexRandomAmount', lerp(num(wa.vertexRandomAnimationAmount), num(wb.vertexRandomAnimationAmount), t));
    envSet('envVertexRandomSpeed', lerp(num(wa.vertexRandomAnimationSpeed), num(wb.vertexRandomAnimationSpeed), t));
    envSet('envWindWaveDistortionAmount', lerp(num(wa.windWaveDistortionAmount), num(wb.windWaveDistortionAmount), t));
    envSet('envWindWaveDistortionFrequency',
      lerp(num(wa.windWaveDistortionFrequency), num(wb.windWaveDistortionFrequency), t));
    envSet('envWindNoiseTex', b.windTex || a.windTex || ENV_BLACK_TEX);

    // 两组描边(角色/家具)也照样推:本示例没有描边 pass。
    for (const [group, prefix] of [['character', 'envCharOutline'], ['fixture', 'envFixtureOutline']]) {
      const ga = (a.config || {})[group] || {}, gb = (b.config || {})[group] || {};
      envSet(prefix + 'Width', lerp(num(ga.outlineWidth), num(gb.outlineWidth), t));
      envSet(prefix + 'DepthOffset', lerp(num(ga.outlineDepthOffset), num(gb.outlineDepthOffset), t));
      envSet(prefix + 'WidthMaxRate', lerp(num(ga.outlineWidthMaxRate, 1), num(gb.outlineWidthMaxRate, 1), t));
      envSet(prefix + 'WidthMinRate', lerp(num(ga.outlineWidthMinRate, 1), num(gb.outlineWidthMinRate, 1), t));
      envSet(prefix + 'Color', lerp4(col4(ga.outlineColor), col4(gb.outlineColor), t));
    }
    // 自发光档位是整型全局量:插值没有意义,过半即换。
    envSet('envEmissionType', t < 0.5 ? num((a.config || {}).emissionType) : num((b.config || {}).emissionType));

    // 雾:九参出自后处理档案。**组件 active 与 enabled 都要看**,两者任一为假就没有雾。
    const prof = this.from ? blendProfiles(a.profile, b.profile, t) : (b.profile || {});
    this.profile = prof;
    const fog = prof.MysekaiFogVolume;
    const fogWeight = fog ? (fog.weight === undefined ? 1 : fog.weight) : 0;
    const fogOn = !!(fog && fog.active && num(fog.params.enabled) > 0 && fogWeight > 1e-4);
    envSet('envFogEnabled', fogOn ? 1 : 0);
    if (fogOn) {
      const p = fog.params;
      // 九个字段在这里折成三个全局量 —— 与原版运行时同一个折叠位置,
      // 着色器里不再重算(见 envglobals.js 的 ENV_FOG_CHUNK 注释)。
      // 组件级权重同时缩放总密度:淡入一个有雾的现象 = 雾从无到有,不是硬切。
      const density = num(p.density, 1) * fogWeight;
      const start = num(p.fogStartDistance);
      const end = num(p.fogEndDistance, 1);
      const span = Math.max(end - start, 1e-4);   // 防 end==start 时除零
      const height = num(p.fogHeight);
      const near = col4(p.nearColor), far = col4(p.farColor);
      // 资产里雾色自带的 alpha 被丢弃:alpha 槽装的是密度。
      envSet('envFogNearColor', [near[0], near[1], near[2], num(p.nearDensity) * density]);
      envSet('envFogFarColor', [far[0], far[1], far[2], num(p.farDensity) * density]);
      envSet('envFogParams', [-1 / span, end / span, height > 0 ? 1 / height : 0, 0]);
    }
    this.post.setProfile(prof);

    // 天空:两张渐变同时在位 + 进度。渐变窗口每次都**从材质记录现取** —— 不缓存成两个数字,
    // 缓存下来的那份会在材质改动之后继续算旧窗口,而且看不出来。
    this.sky.setGradientWindow(skyGradientWindow(this.skyMaterial), this.skyGradientSource);
    this.sky.setRamps(a.ramp, b.ramp, t);

    // 时间轴写在最后:它叠在**现象方向光色**上,顺序反了就会被上面那次 envSet 覆盖掉。
    this._applyTimeline();
  }

  /**
   * 时间轴这一拍写出去。四条轨各自的目标见「时间轴」段的注释。
   *
   * 换现象时两侧**各采一次样、按同一个淡化进度混合** —— 与光照九项、渐变、雾、后处理用的是
   * 同一条淡化规矩,不给时间轴另开一条。没有时间轴的那一侧按「不出力」参与混合:附加色取
   * 材质底值、附加强度取 0。
   */
  _applyTimeline() {
    const t = this.from ? this.fade : 1;
    const sTo = this._sampleTimeline(this.to);
    const sFrom = this.from ? this._sampleTimeline(this.from) : null;
    const pick = (side, key, dflt) => {
      const v = side ? side[key] : null;
      return (v === null || v === undefined) ? dflt : v;
    };
    const mix = (key, dflt) => {
      const b = pick(sTo, key, dflt);
      if (!sFrom) return b;
      const a = pick(sFrom, key, dflt);
      return Array.isArray(b) ? lerp4(a, b, t) : lerp(a, b, t);
    };
    const skyColor = mix('skyAdditiveColor', this.skyAdditiveDefault);
    const skyIntensity = mix('skyAdditiveIntensity', 0);
    const lightColor = mix('lightAdditiveColor', [0, 0, 0, 0]);
    const lightIntensity = mix('lightAdditiveIntensity', 0);
    this.sky.setAdditive(skyColor, skyIntensity);
    // 光那一侧:叠在现象方向光色上再推出去。底色是 `_applyBlend` 算好的那一份(status 里
    // `light.phenLightColor` 报的仍是**底色**,叠加后的值单独在 timeline.applied 里报)。
    const base = this.light ? this.light.phenLightColor : null;
    const lit = base ? addAdditiveColor(base, lightColor, lightIntensity) : null;
    if (lit) envSet('envPhenLightColor', lit);
    this.timelineSample = sTo;
    this.timelineApplied = {
      skyAdditiveColor: skyColor, skyAdditiveIntensity: skyIntensity,
      lightAdditiveColor: lightColor, lightAdditiveIntensity: lightIntensity,
      phenLightColorBase: base ? base.slice() : null,
      phenLightColor: lit,
    };
  }

  /** 一侧(淡入/淡出)的时间轴在当前时钟处的一拍;那一侧没有时间轴就是 null。 */
  _sampleTimeline(side) {
    const tl = side && side.rec ? side.rec.timeline : null;
    return tl ? tl.sample(this.timelineTime) : null;
  }

  /** 当前现象的时间轴(没有就是 null)。 */
  get timeline() { return this.to && this.to.rec ? this.to.rec.timeline : null; }

  /** 时间轴:播 / 暂停。暂停之后时钟不走,画面停在这一拍(判据靠它定点观测)。 */
  setTimelinePlaying(on) {
    this.timelinePlaying = !!on;
    this._applyTimeline();
    return this.timelinePlaying;
  }

  /** 时间轴:定位到第几秒(按当前现象自己的 `duration` 取模)。 */
  setTimelineTime(seconds) {
    const tl = this.timeline;
    const d = tl ? tl.duration : 0;
    const v = num(seconds);
    this.timelineTime = d > 0 ? ((v % d) + d) % d : 0;
    this._applyTimeline();
    return this.timelineTime;
  }

  /** 时间轴:复位到 0 秒。 */
  resetTimeline() { return this.setTimelineTime(0); }

  /** 挂粒子:三族按语义挂,`site` 只挂当前站点(或对所有站点通用的那些)。 */
  _mountEffects() {
    this._retireEffects();
    if (!this.particlesOn || !this.to || !this.to.rec.fx) return;
    // 室内不挂现象粒子。这是**保守处置,不是从产物读出来的律**:产物里室内站点照样带着
    // 逐现象的 `SiteEnvironmentConfig` 与 `VolumeProfile`(光照与后处理确实分室内一档),
    // 但现象包给室内站点的覆盖**只有 config 与 postprocess 两份,从来没有一份效果清单**,
    // 也没有任何字段说室内该挂哪些粒子。既然没有依据,就一件都不挂,并在这里标明是保守处置。
    if (this.siteIndoor) {
      this.effectNotes = [`${this.site}:室内站点,现象粒子与天空都不挂(保守处置,产物未给室内的粒子律)`];
      return;
    }
    const site = this.indoor ? this.overrideSite : this.site;
    const plan = this.to.rec.fx.plan(site);
    for (const item of plan) {
      // camera 族挂在相机的子节点上(跟视点);其余挂世界。
      const parent = item.kind === 'camera' ? this.fxCamera : this.fxWorld;
      let view = null;
      try {
        view = createParticleEffect(
          { name: item.name, nodes: item.effect.nodes, particles: item.effect.particles },
          { anchor: parent, worldParent: parent, textureFor: this.textureFor,
            meshFor: this.meshFor,      // Mesh 绘制模式取网格(已在 loadPhenomenon 里预载)
            camera: this.camera });   // 屏幕占比截断要按当前相机的 fov/aspect 算
      } catch (e) {
        this.errors.push(`${item.name}: ${String(e).slice(0, 90)}`);
        continue;
      }
      if (!view) continue;
      view.play(null);        // 环境粒子不自动收场(没有 showSeconds 语义)
      this.effects.push({
        name: item.name, kind: item.kind, site: item.site, variant: item.variant,
        follow: item.follow, view, parent,
      });
    }
    this.effectNotes = plan.filter((p) => p.kind === 'other')
      .map((p) => `${p.name}: 命名式样不符三族,本示例按世界挂载(挂法不由名字说明)`);
  }

  /**
   * 相机族的跟随律逐帧施加。'fixed' 的那一支要把父节点的世界旋转抵消掉 ——
   * 位置照样跟随相机,朝向不跟着转。'rotate' 的那一支什么都不做(默认就是跟着父节点转)。
   */
  _applyCameraFollow() {
    if (!this.effects.length) return;
    for (const e of this.effects) {
      if (e.kind !== 'camera' || !e.follow || e.follow.law !== CAMERA_FOLLOW.FIXED) continue;
      const top = e.view.root;
      const p = top.parent;
      if (!p) continue;
      p.getWorldQuaternion(_envQ);
      top.quaternion.copy(_envQ).invert();
    }
  }

  /**
   * 换天气时旧特效不立即销毁:它们**存活 ≥ 2 秒**并在这段时间里淡出(与新特效交叠)。
   * 「淡出」在本示例里 = 停止继续发射 + 让在场粒子自然走完寿命,所以只需把发射率清零。
   */
  _retireEffects() {
    for (const e of this.effects) {
      for (const em of e.view.emitters) {
        // 清掉发射模块 = 不再新生;已在场的粒子照常按自己的寿命消失。
        if (em.system) em.system = { ...em.system, emission: null };
      }
      this.retiring.push({ name: e.name, view: e.view, clock: 0 });
    }
    this.effects = [];
  }

  /**
   * 退场特效的推进与回收。**到点就销毁,不等粒子走完**——延时销毁那条路是「停发 + 等
   * `_timeUntilDestroy` 秒 + 销毁」,秒数一到 GameObject 就没了,还在场的粒子跟着一起消失。
   * 早先这里多加了一个「粒子清空」条件,寿命长于 2 秒的粒子(云片就是)于是永远等不到回收:
   * 每换一次天气多留一套云在天上,连切几次就叠成一片。**多一个条件不是更保守,是另一条律。**
   */
  _updateRetiring(dt) {
    if (!this.retiring.length) return;
    const keep = [];
    for (const r of this.retiring) {
      r.clock += dt;
      r.view.update(dt);
      if (r.clock >= EFFECT_RETIRE_SECONDS) {
        try { r.view.dispose(); } catch (e) { this.errors.push(`retire ${r.name}: ${String(e).slice(0, 60)}`); }
      } else keep.push(r);
    }
    this.retiring = keep;
  }

  _clearEffects() {
    for (const e of this.effects) {
      try { e.view.dispose(); } catch (err) { this.errors.push(`dispose ${e.name}: ${String(err).slice(0, 60)}`); }
    }
    this.effects = [];
    for (const r of this.retiring) {
      try { r.view.dispose(); } catch (err) { this.errors.push(`dispose ${r.name}: ${String(err).slice(0, 60)}`); }
    }
    this.retiring = [];
  }

  setParticles(on) {
    this.particlesOn = !!on;
    if (this.particlesOn) this._mountEffects(); else this._clearEffects();
  }

  setIndoor(on) {
    this.indoor = !!on;
    this.refreshSite();
  }

  /** 换站点:几何重挂 + 两级查找重取 + 室内与否重判。返回挂载读数(判据可以等它)。 */
  async setSite(site) {
    this.site = site;
    // 先按清单级判据落一个答案,几何装好之后由场景文档的主判据覆盖它。
    this.siteIndoor = this.siteView.indoorFromIndex(site).indoor;
    this.refreshSite();
    return this.ensureSite();
  }

  setPost(on) { this.postOn = !!on; this.post.setEnabled(!!on); }

  /**
   * 返回**真的画不画**:网格或窗口没到位时它是假,与开关的意愿分开报。
   * 室内再多一道:室内不画天空(与不挂现象粒子同一条保守处置,见 `_mountEffects`)。
   */
  setSkyVisible(on) {
    this.skyVisible = !!on;
    return this.sky.setVisible(this.skyVisible && !this.siteIndoor);
  }

  /** 站点几何的可见性(面板上那个开关原先管的是自制承影面)。 */
  setSiteVisible(on) {
    this.siteVisible = !!on;
    this.siteView.group.visible = this.siteVisible;
    return this.siteVisible;
  }

  /** 旧名:面板与自检都按这个名字调,保留成别名,不在两处各写一份。 */
  setGroundVisible(on) { return this.setSiteVisible(on); }

  /** 角色材质换了(换角色)就重新接线。 */
  setCharacterMaterials(mats) {
    this.characterMaterials = mats || [];
    this._applyBlend();
  }

  setSubject(v3, radius) {
    this.subject.copy(v3);
    this.siteView.setSubject(v3, radius);
  }

  // ---- 帧循环 ----

  attach() {
    if (!this.group.parent) this.scene.add(this.group);
    this.enabled = true;
    // 站点几何按需装:环境层没开过的会话不必为此拉一份十几兆的 glb。
    this.ensureSite();
  }

  detach() {
    this._clearEffects();
    this.group.removeFromParent();
    this.enabled = false;
  }

  update(dt) {
    if (!this.enabled) return;
    this.time += dt;
    envSet('envTime', this.time);
    if (this.from && this.fade < 1) {
      this.fadeElapsed += Math.min(dt, MAX_FADE_STEP_SECONDS);
      this.fade = this.fadeDuration > 0 ? Math.min(1, this.fadeElapsed / this.fadeDuration) : 1;
      this._applyBlend();
      if (this.fade >= 1) {
        // 淡化收口:记一次实测时长(自检读它),再把淡出侧丢掉。
        if (this._fadeStart) {
          // 两个口径都记:`simulated` 是累加的帧增量(**判据看它**——它与刷新率无关),
          // `seconds` 是墙钟(仅参考:帧被节流时它会长于声明值,单帧上限见 MAX_FADE_STEP_SECONDS)。
          this.fadeRuns.push({
            from: this.from.name, to: this.to.name,
            simulated: +this.fadeElapsed.toFixed(4),
            seconds: +((performance.now() - this._fadeStart) / 1000).toFixed(4),
            declared: this.fadeDuration,
          });
          if (this.fadeRuns.length > 8) this.fadeRuns.shift();
          this._fadeStart = null;
        }
        this.from = null;
      }
    }
    // 时间轴按当前现象自己的 `duration` 循环。**闪光那一下就在这里**:四条轨这一拍算出来的
    // 附加色/附加强度由 `_applyTimeline` 写给天空与现象方向光。
    const tl = this.timeline;
    if (tl && this.timelinePlaying && tl.duration > 0) {
      this.timelineTime = (this.timelineTime + dt) % tl.duration;
    }
    this._applyTimeline();
    this.sky.follow(this.camera);
    this._applyCameraFollow();
    for (const e of this.effects) e.view.update(dt);
    this._updateRetiring(dt);
  }

  /** 后处理:环境层开着且档案在位时接管最终绘制;返回 false = 调用方照常自己 render。 */
  render() {
    if (!this.enabled || !this.postOn) return false;
    return this.post.render(this.scene, this.camera, {
      sunScreen: this._sunScreen(),
      particleCount: this.liveParticles(),
      hideForParticlePass: (hide) => this._isolateParticles(hide),
    });
  }

  /** 粒子泛光缓冲要「只有环境粒子」的画面:其余一切临时隐藏,再原样恢复。 */
  _isolateParticles(hide) {
    const keep = new Set([this.fxWorld, this.fxCamera]);
    const walk = (obj) => {
      if (keep.has(obj)) return;
      if (hide) { obj.userData._envPrevVisible = obj.visible; obj.visible = false; }
      else if (obj.userData._envPrevVisible !== undefined) {
        obj.visible = obj.userData._envPrevVisible;
        delete obj.userData._envPrevVisible;
      }
    };
    for (const child of this.scene.children) {
      if (child === this.group) { for (const g of child.children) walk(g); continue; }
      walk(child);
    }
  }

  /** 太阳的屏幕位置(太阳耀斑要用)。光向朝天空,所以太阳在 +光向 上。 */
  _sunScreen() {
    if (!this.light) return null;
    const p = this.light.dir.clone().multiplyScalar(500).add(this.camera.position);
    p.project(this.camera);
    return { x: p.x * 0.5 + 0.5, y: p.y * 0.5 + 0.5, visible: p.z < 1 };
  }

  liveParticles() {
    let n = 0;
    for (const e of this.effects) for (const em of e.view.emitters) n += em.particles.length;
    return n;
  }

  emitterCount() {
    return this.effects.reduce((n, e) => n + e.view.emitters.length, 0);
  }

  /**
   * 装载时就摘掉、一发都不会发的渲染器计数。两道门分开数:渲染器自己的 `enabled`,
   * 与发射节点(含祖先)的 `active`。这些不是「停发」——停发是形状没建模,发射器还在;
   * 这些是原版根本不画的东西,发射器压根没建。
   */
  skippedRenderers() {
    let disabled = 0, inactive = 0, unsupported = 0;
    const unsupportedModes = {};
    for (const e of this.effects) {
      const s = e.view.skipped || {};
      disabled += num(s.disabledRenderer);
      inactive += num(s.inactiveNode);
      unsupported += num(s.unsupportedRenderer);
      for (const [mode, count] of Object.entries(s.unsupportedRenderModes || {})) {
        unsupportedModes[mode] = (unsupportedModes[mode] || 0) + num(count);
      }
    }
    return {
      disabled, inactive, unsupported,
      unsupportedModes,
      total: disabled + inactive + unsupported,
    };
  }

  /** 当前挂着的发射器里,形状没建模因而**整条停发**的那些(逐形状计数)。 */
  suppressedEmitters() {
    const counts = {};
    let total = 0;
    for (const e of this.effects) {
      for (const em of e.view.emitters) {
        if (!em.suppressed) continue;
        total += 1;
        counts[em.shapeType] = (counts[em.shapeType] || 0) + 1;
      }
    }
    return { total, counts };
  }

  /**
   * 逐发射器的位置判据读数(散布/高度判据读它)。带上效果名与族,便于一眼看出
   * 是哪个效果的哪个节点在发射 —— 判据报红时要能直接指到发射器。
   */
  placement() {
    const rows = [];
    for (const e of this.effects) {
      for (const row of e.view.placement()) rows.push({ effect: e.name, kind: e.kind, ...row });
    }
    return rows;
  }

  // ---- 自检与探针读的状态 ----

  /**
   * 一份可被外部探针读的状态快照。**近似与未接线一律出现在这里**:
   * 面板与 README 都从这份数据取,不在别处另写一份名单。
   */
  status() {
    const rec = this.to ? this.to.rec : null;
    const st = this.to ? this.to.state : null;
    const consumers = envConsumers({
      character: Shading.CHARACTER_FRAG,
      site: SITE_FRAG,
      sky: SKY_FRAG,
    });
    const pushedNoConsumer = Object.keys(ENV_GLOBALS).filter((k) => !consumers[k].length);
    const shapes = rec && rec.shapes ? rec.shapes : null;
    const suppressed = this.suppressedEmitters();
    return {
      enabled: this.enabled,
      phenomenon: this.to ? this.to.name : null,
      fadingFrom: this.from ? this.from.name : null,
      fade: +this.fade.toFixed(4),
      fadeDuration: this.fadeDuration,
      crossFadeSeconds: CROSS_FADE_SECONDS,
      fadeRuns: this.fadeRuns.slice(),
      indexLoaded: !!this.index,
      phenomenaCount: this.names.length,
      loadedCount: this.loaded.size,
      site: st ? st.site : null,
      indoor: this.indoor,
      usedOverride: st ? st.usedOverride : false,
      overrideSites: this.overrideSites || [],
      homeAngleUsed: !!(st && st.light.homeAngleUsed),
      ramp: rec ? rec.rampSize : null,
      rampReady: !!(st && st.ramp && st.ramp.image),
      light: this.light ? {
        dir: [+this.light.dir.x.toFixed(6), +this.light.dir.y.toFixed(6), +this.light.dir.z.toFixed(6)],
        angleXZ: +this.light.angleXZ.toFixed(4), angleY: +this.light.angleY.toFixed(4),
        charLightColor: this.light.charLightColor, charSkinShade: this.light.charSkinShade,
        charBodyShade: this.light.charBodyShade, phenLightColor: this.light.phenLightColor,
        phenShadeColor: this.light.phenShadeColor,
        dropShadowColor1: this.light.dropShadowColor1, dropShadowColor2: this.light.dropShadowColor2,
        dropShadowEdgeSmoothness: this.light.dropShadowEdgeSmoothness,
      } : null,
      wiring: LIGHT_WIRING.map((w) => ({ key: w.key, label: w.label, wired: w.wired, to: w.to })),
      unwired: LIGHT_WIRING.filter((w) => !w.wired).map((w) => w.label),
      globals: {
        total: Object.keys(ENV_GLOBALS).length,
        consumers,
        pushedNoConsumer,
        groups: Object.fromEntries(Object.entries(ENV_GLOBAL_META)
          .reduce((m, [k, v]) => { m.set(v.group, (m.get(v.group) || 0) + 1); return m; }, new Map())),
      },
      fog: (() => {
        const P = ENV_GLOBALS.envFogParams.value;
        const nc = ENV_GLOBALS.envFogNearColor.value, fc = ENV_GLOBALS.envFogFarColor.value;
        // 报告折叠后的真值,同时把它们反解回可读的距离/高度(便于对着档案核)。
        const span = P.x !== 0 ? -1 / P.x : 0;
        return {
          enabled: ENV_GLOBALS.envFogEnabled.value > 0.5,
          params: [P.x, P.y, P.z, P.w],
          nearAlpha: nc.w, farAlpha: fc.w,
          derived: { start: span ? P.y * span - span : 0, end: span ? P.y * span : 0,
                     height: P.z ? 1 / P.z : 0 },
        };
      })(),
      cloud: {
        scale: ENV_GLOBALS.envCloudShadowScale.value,
        opacity: ENV_GLOBALS.envCloudShadowOpacity.value,
        scroll: [ENV_GLOBALS.envCloudScrollSpeed.value.x, ENV_GLOBALS.envCloudScrollSpeed.value.y],
      },
      particles: {
        on: this.particlesOn,
        effects: this.effects.length,
        emitters: this.emitterCount(),
        live: this.liveParticles(),
        // 原版不画的渲染器:装载时就摘掉了,一发不发(与「停发」是两件事,见 skippedRenderers)
        skipped: this.skippedRenderers(),
        retiring: this.retiring.length,
        retireSeconds: EFFECT_RETIRE_SECONDS,
        // Mesh 绘制模式的网格预载情况。报出来才能被判据读到 —— 不报的东西
        // 无法证明它跑通了(requested / loaded 不等就是有 glb 没读进来)。
        meshes: this.meshLoader.stats(),
        byKind: this.effects.reduce((m, e) => { m[e.kind] = (m[e.kind] || 0) + 1; return m; }, {}),
        // 相机族的跟随律逐个报出来(读出来的还是落到默认,一眼可辨)。
        cameraFollow: this.effects.filter((e) => e.kind === 'camera')
          .map((e) => ({ name: e.name, law: e.follow ? e.follow.law : null, source: e.follow ? e.follow.source : null })),
        notes: this.effectNotes.slice(),
        unmodelledShapes: shapes ? shapes.counts : {},
        emittersWithoutShape: shapes ? shapes.noShape : 0,
        emittersInDoc: shapes ? shapes.total : 0,
        // 挂着但停发的发射器(形状没建模)。`unmodelledShapes` 数的是整份文档(含别的站点),
        // 这两项数的是**当前真的挂着**的那些 —— 面板要报的是后者。
        suppressed: suppressed.total,
        suppressedShapes: suppressed.counts,
        placement: this.placement(),
        dataError: this.fxDataError || null,
      },
      sky: {
        // `wanted` 是开关的意愿,`visible` 是这一帧真的画不画 —— 网格或窗口缺一就是假。
        wanted: this.skyVisible,
        visible: this.sky.mesh.visible,
        ready: this.sky.ready,
        geometrySource: this.sky.geometrySource,
        mesh: this.sky.meshInfo,
        // 窗口连同它的出处一起报:一眼看出是读出来的,还是根本没读到。
        gradient: this.sky.gradient,
        meshLoad: this.skyMeshLoader.stats(),
        note: this.skyMeshNote,
        // 这一拍真的写进天空材质的附加色与附加强度(时间轴驱动)。
        additive: this.sky.additive,
        approximations: [
          '附加色与附加强度由现象时间轴驱动;没有 clip 活着时附加色回到材质自带的那一份、强度回到 0',
        ],
      },
      // 站点:真站点几何的读数。`meshes` / `triangles` 是**场景里真的挂着**的数量
      // (含原版不画因而隐藏的那些 —— 它们在场景里,只是不画),`declared` 是场景文档里
      // 主根节点声明的数量,两者对得上才说明挂进去的是产物本身。
      site: {
        visible: this.siteVisible,
        key: this.site,
        indoor: this.siteIndoor,
        // 几何挂哪一站(`key`)与现象配置查哪一站(`configSite`)是两件事:
        // 「室内覆盖」开着时后者是那个带覆盖的站点,前者不动。
        configSite: st ? st.site : null,
        usedOverride: st ? st.usedOverride : false,
        // 室内不挂现象粒子、不画天空;这两项照实报出来,判据读它们。
        indoorSuppressesWeather: this.siteIndoor,
        mounting: !!this.siteView.mounting,
        mounted: this.siteView.mounted,
        available: this.siteView.scenes().map((x) => ({ key: x.key, indoor: x.indoor })),
        errors: this.siteView.errors.slice(0, 8),
      },
      // 时间轴:声明与实到分开报(清单说有、文档读不出 = 数据缺口,不是「没有时间轴」),
      // 这一拍四条轨算出什么、真的写出去什么、以及**没做的那几条轨**都在这里。
      timeline: (() => {
        const tl = rec ? rec.timeline : null;
        return {
          declared: rec ? rec.timelineDeclared : null,
          has: !!tl,
          name: tl ? tl.name : null,
          duration: tl ? tl.duration : null,
          frameRate: tl ? tl.frameRate : null,
          frames: tl ? Math.round(tl.duration * tl.frameRate) : null,
          playing: this.timelinePlaying,
          time: +this.timelineTime.toFixed(4),
          frame: tl ? Math.floor(this.timelineTime * tl.frameRate) : null,
          trackCount: tl ? tl.tracks.length : 0,
          clipCount: tl ? tl.clipCount : 0,
          tracks: tl ? tl.trackSummary() : [],
          sample: this.timelineSample,
          applied: this.timelineApplied,
          notModelled: tl ? tl.notModelled : [],
          notModelledClips: tl ? tl.notModelledClips : 0,
          skyAdditiveDefault: this.skyAdditiveDefault,
          skyAdditiveDefaultSource: this.skyAdditiveDefaultSource,
          dataError: this.timelineDataError,
          phenomenaWithTimeline: this.timelineNames.slice(),
        };
      })(),
      post: this.post.status(),
      notRestored: NOT_RESTORED,
      errors: this.errors.slice(0, 8),
    };
  }

  dispose() {
    this._clearEffects();
    this.sky.dispose();
    this.siteView.dispose();
    this.post.dispose();
    this.fxCamera.removeFromParent();
    this.group.removeFromParent();
  }
}

/**
 * 未还原的部分。**这张表是判据的一部分**:面板与 README 从这里取,少写一条就是在装作做到了。
 * 「原始数据不含」与「本示例未实现」是两件不同的事,分开写 —— 前者没人在等,后者是欠账。
 */
export const NOT_RESTORED = [
  '时间轴的两条 `ValueNoiseTrack`(共 5 个 clip):它们是两条强度轨的**子轨**,记录里带噪声强度、频率与强度曲线,但「噪声怎么并进父轨的值」产物一个字没说 —— **整条不做并计数**,不猜一条律出来。读数见 timeline.notModelled',
  '时间轴的 `MarkerTrack`:标记轨在产物里是空的(0 个 clip),没有东西可播;它照实列在 timeline.notModelled 里,不假装它被消费了',
  '光那一侧的附加色落在哪一盏灯:轨的目标名只说 `lightAdditiveColor` / `lightAdditiveIntensity`,产物没说它指的是现象方向光还是角色方向光。本示例叠在**现象方向光**上(那是站点材质的直射项,也是这套 `SiteEnvironment*Track` 所属的那一层),角色方向光不动 —— 这是取舍,不是读出来的',
  '光那一侧附加色的合成律:天空那一侧着色器里写明是 gamma→线性→按 alpha 与强度缩放→回 gamma 的往返,光那一侧产物没有单独说,本示例照天空**同一形状**实现',
  '附加强度的底值:天空材质里没有与 `AdditiveIntensity` 对应的属性,所以「没有 clip 活着」时的强度只能取 0(不出力);附加**色**的底值是读出来的(材质的 `_AdditiveColor`,当前是全零)',
  'clip 的 `preExtrapolation` / `postExtrapolation` 与曲线的 `preInfinity` / `postInfinity`:当前内容里每条曲线的键都铺满 0..1、取样点也从不越界,这几项一次都没被走到,本示例照实不实现',
  '粒子模块:**提取侧已全部建模**(自定义数据/子发射器/碰撞/受力/噪声/拖尾都在产物里),但本示例的发射器引擎只实现了出生与基本运动 —— 曲线型自定义数据、子发射器、碰撞与拖尾在画面上还未生效;未实现项与未建模的发射形状逐项计数见 particles.unmodelledShapes',
  '发射形状 Box 等没有发射公式:这些形状的发射器**整条停发**,不退化成点发射(点发射会把本该铺开的粒子全堆在发射节点原点上)。「从网格表面发射」已按三角放置建模(面积加权选三角、三角内均匀取点、重心插值顶点法线当方向),但形状没解出网格的那几个照样停发。当前站点真的挂着几个、分别是什么形状,见 particles.suppressed / particles.suppressedShapes。renderMode=Mesh 是另一道渲染器能力缺口,单独见 particles.skipped.unsupported / unsupportedModes',
  '`renderer.maxParticleSize` / `minParticleSize` 是**视口比例**(默认 0.5 = 半个视口高),不是世界尺寸:本示例不按它截尺寸(拿视口比例去截米是单位错误,会把声明 100x60 米的云静默截成 0.5 米),而按视口比例截断的那条律本示例没有建模 —— 大件在近处会比运行时更大',
  '站点决定的那批全局着色量不在本包里,本示例按中性常量处理(它们随站点变而不随天气变)',
  '站点的世界位置:`sitePosition` 只在 master 表里,产物里那张放置表是空的并写明「no master directory supplied」,所以本示例把当前站点摆在原点 —— 一次只看一个站点,这不改变站点内部的相对关系',
  '房间等级:等级同样是 master 表的字段,产物给不出「这间房现在是第几级」。所以它按「服务端决定的做成面板下发」处置 —— 面板上一个旋钮,默认取产物里最高的一级,读数见 site.mounted.level / levelSource,**不再是代码里写死的一个假定**',
  '室内套件里没有被扩张模块摆出来的那几件(入口件 `mdl_site_entrance_common`、别的等级的地板与墙件、编辑器预览件):`indoor` 只说了「一级一个扩张模块 + 该级的可走面」,没有任何字段说入口摆在哪一格 —— 摆放要 master 表,本仓没有',
  '站点自己的着色器在另一个包里(场景包只带着材质名与属性块),所以本示例的站点材质是**近似**:基色贴图 + 现象量(直射/暗部/云影/落影/雾)。透明度按产物走(混合因子读材质 extras 的 Unity 枚举、遮罩阈值读 glTF 的 alphaCutoff、双面读 doubleSided),但法线贴图、顶点色、发光与风的顶点动画都没接',
  '落影仍是一个投影盘(不是阴影贴图):它现在落在真站点的表面上,按片元所在高度沿光向反投。两个权重是本示例加的近似 —— 朝上分量与「只投在角色下方」,产物里没有这两条',
  '站点的碰撞面、导航网格与足音颜色表照实装在产物里,本示例都不消费(它没有行走与足音)。**室内那一份是例外**:`indoor.assembly` 把「该级的可走面」算进「一个房间」里,所以它按编排挂上,但照原版**不画**(它是 MeshCollider),读数见 site.mounted.assembly',
  '家具、房间皮肤与散布道具包不挂:场景包里没有家具(`semantics.fixtures`),这几族要 master 表说明摆哪些,本仓没有 master',
];

/** 原始数据里就没有的东西。**不是欠账**,写在这里免得被当成未实现。 */
export const ABSENT_FROM_DATA = [
  'LUT 颜色分级:后处理档案里没有任何查表类组件,资产里也没有对应的三维贴图 —— 原始数据不含,不是未实现。代码留了贴图类参数的接入位,但没有东西在等它',
  '天空底色:它由装载参数给出,不在现象配置里,所以本示例不编造一个值',
];
