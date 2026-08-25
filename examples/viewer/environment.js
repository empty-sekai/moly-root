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
// 站点材质**不再**按「基色贴图 × 现象量」的近似画,改按它自己那一族着色器的律画。
// 这一族八个,各自一份程序:
//
//   Site/FieldObject · Object · Site/Tree · Site/Ground ·
//   DropItem · Fixture/Basic · Room/Floor · Fixture/ShadowMesh
//
// 一、一份材质走哪一个程序,由**产物的材质记录**说了算
//   站点产物给每份材质写了 `shader.name` 与整块作者属性。产物自己写明:glTF 里那份材质
//   是「按属性推出来的预览近似」,**属性块才是记录**。所以程序按 `shader.name` 取、参数按
//   属性块取;glTF 材质只用来接已经解好的贴图与几何绑定。八族之外的材质(粒子那一族的
//   UberUnlit、引擎标准件 URP/Lit、以及本轮清单外的 Water / Ground-Birthday 等)走**明标的
//   回落程序**并逐个计数 —— 它们的律不在这一轮的依据里,不在这里假装照律画。
//
// 二、全族共有的两条
//   (1) 八个程序**都写两个渲染目标**:`SV_Target0` 是颜色,`SV_Target1` 是发光。
//       只有 Object 与 Fixture/Basic 会往第二个写非零值,其余六个恒写 0。第二个目标由
//       `siteEmission` 那一趟单独产出(见 `SiteView.renderEmission`);画到只有一个附着点的
//       目标上时,第二路输出按 GL 的规矩被丢掉 —— 发光**从不加进主目标**。
//   (2) 这一族**不读**球谐环境光、反射探针、光照贴图、主光色与附加光源(依据里逐份点数,
//       542 份全 0)。整个站点的光照就是「现象色 mix + 半兰伯特分段 + 一张主光阴影图」。
//       所以云影四项从站点材质里**撤出来**了:它们在这一族里一次都没出现,先前那份近似
//       把它们乘进了站点颜色,那不是这一族的律。四项照旧被推送,当前站点侧无消费方。
//
// 三、顶点色按平方用
//   Ground 的顶点程序里明写 `in_COLOR0.xyz * in_COLOR0.xyz`;FieldObject / Object / Tree 是
//   两次连乘的等价形式。**全族唯一不平方的是 Room/Floor**(只乘一次,而且没有开关),
//   Fixture/Basic 与 DropItem 完全不用顶点色。另外产物把顶点色写成了 0..255 的浮点,
//   装载时按「最大值超过 1 就整体除 255」还原,还原了几条记进读数(`vertexColorRescaled`)。
//
// 四、照原样实现的九处引擎怪处(不许「修正成合理的样子」)
//   A FieldObject 的主 pass 忽略 `_MainTex_ST`,只有它的投影 pass 用;
//   B 顶点色被乘两次(= 平方);
//   C FieldObject 与 Tree 的主光落影上色**被套两次**(中间那段不改写 atten);
//   D Object 的非道路物件主光衰减**被平方**;
//   E Tree / Ground / DropItem / Fixture 的 alpha 裁剪阈值**硬编码 0.5**,而 `_AlphaClip`
//     这个属性在它们的编译产物里一次都不出现;
//   F Tree 与 Fixture 的抖动屏幕缩放是 0.125(8 像素周期),FieldObject / Object 是 0.25;
//   G Fixture 的立方体反射向量取的是镜像的**相反数**;
//   H Fixture 的反射菲涅耳用**未 clamp** 的 `1 - dot(N,V)`;
//   I 宝箱阴影强度被**硬乘 0.5**,半径 0.96 内全暗、0.96..1.00 过渡。
//   每一处在代码里都标了「照原样」。
//
// 五、依据里明确未取得 / 未编译的,一律不实现并计数(见 `SITE_NOT_OBTAINED`)
//   Object 的水面 / 树 / 门 / 光柱 / 渐变 / 反射 / 视差折射 / 叠加贴图整组、
//   Fixture/Basic 的渐变与视差折射、全族的 `_PositionShading*`、Object 的
//   `_ObjectShaderUsage` 在 0/2/8/11/14 之外各档的差异 —— 这些在已发货的编译产物里
//   一行代码都没有,所以它们的算式无从读出,这里不猜。
//
// 六、本示例侧的三条缺口(照实计数,不假装接上了)
//   * **主光阴影图**:本示例没有阴影 pass,产不出那张图。落影那一格的 `atten` 由示例原有的
//     球体落影近似顶着(角色沿光向投到本片元高度的水平面),**律照原样跑**:FieldObject /
//     Tree 套两次、Object 平方、Ground / Room/Floor 一次。近似只换了 atten 的来源。
//   * **uv2 / uv3**:产物导出的站点网格只带 uv0 与 uv1。Object 的墙面 AO 读 uv3,取不到时
//     整块停用并计数 —— 拿缺失的输入硬算会画出一整片全暗的墙,那不是还原。
//   * **正交相机分支**:视线方向在正交相机下改取视矩阵第三列。本示例相机恒为透视,
//     这一支走不到,计数为「未走到」。
//
// 七、这一族要的全局量,产物里没有的那几个
//   `_GlobalEdgeThreshold` / `_GlobalEdgeSmoothness` / `_ShadowMaskEdge1` / `_ShadowMaskEdge2` /
//   宝箱两项 / 站点边界一组 / 掉落物四项 / 3D 预览两色 —— 现象档案里**一项都没有**,
//   它们是 C# 每帧下发的量。按「服务端决定的做成面板下发」处置:做成一组可写的全局量
//   (`SITE_GLOBALS`),默认值在 `SITE_GLOBAL_META` 里逐项写明出处;凡是本示例自己挑的默认值
//   都标成 `chosen`,不冒充产物给的。

// Unity 侧混合因子枚举 → three 的因子。材质 extras 里 `blendFactors` 就是这两个整数
// (当前内容只出现两对:5/10 = 常规 alpha,5/1 = 叠加),按枚举翻译而不是按 `blendMode`
// 的字符串猜 —— 字符串是给人看的,数对才是律。
const UNITY_BLEND_FACTOR = {
  0: THREE.ZeroFactor, 1: THREE.OneFactor, 2: THREE.DstColorFactor, 3: THREE.SrcColorFactor,
  4: THREE.OneMinusDstColorFactor, 5: THREE.SrcAlphaFactor, 6: THREE.OneMinusSrcColorFactor,
  7: THREE.DstAlphaFactor, 8: THREE.OneMinusDstAlphaFactor, 9: THREE.SrcAlphaSaturateFactor,
  10: THREE.OneMinusSrcAlphaFactor,
};

// `_Cull` 的取值:0=双面 1=剔正面 2=剔背面。Object 的背面色只在**双面**时才生效。
const UNITY_CULL_SIDE = { 0: THREE.DoubleSide, 1: THREE.BackSide, 2: THREE.FrontSide };

/** 依赖名(`a/b/c`)与包名(`a__b__c`)化到同一形状再比。 */
function pkgKey(s) { return String(s || '').replace(/[/_]+/g, '/').toLowerCase(); }

// 落影投射体:所有站点材质**共享同一个 uniform 对象**(与 envglobals 同一条规矩),
// 写一次就等于推给了全部站点材质。
const SITE_SUBJECT = {
  envSubjectPos: { value: new THREE.Vector3(0, 0.6, 0) },
  envSubjectRadius: { value: 0.55 },
};

/**
 * 站点这一族要的全局量里,**现象档案没有给**的那些。语义与出处逐项见 `SITE_GLOBAL_META`。
 * 与 ENV_GLOBALS 同一条规矩:一个对象按引用共享,写一次推给全部站点材质。
 */
const SITE_GLOBALS = {
  // toon 分段(材质的 `_Local*` 在 `_OverrideShadingParameter>0.5` 时顶替它们)
  siteEdgeThreshold: { value: 0.65 },
  siteEdgeSmoothness: { value: 0.04 },
  // 主光阴影:本示例没有阴影图,这一项是近似的强度旋钮(= `_MainLightShadowParams.x` 那一格)
  siteShadowStrength: { value: 1 },
  // 地面/Ground 与 Object 的阴影遮罩边界
  siteShadowMaskEdge1: { value: 0 },
  siteShadowMaskEdge2: { value: 1 },
  // 宝箱阴影:位置两个 + 强度两个(强度 0 = 关)
  siteTreasurePos0: { value: new THREE.Vector3(0, 0, 0) },
  siteTreasurePos1: { value: new THREE.Vector3(0, 0, 0) },
  siteTreasureIntensity: { value: new THREE.Vector2(0, 0) },
  // 站点边界(只有 Object 与 Ground 编译得到,当前内容里没有带这条关键字的材质)
  siteExtensionCenter: { value: new THREE.Vector3(0, 0, 0) },
  siteExtensionRadius: { value: 60 },
  siteExtensionInnerRadius: { value: 40 },
  siteExtensionSmoothness: { value: 1 },
  siteExtensionFadeColor: { value: new THREE.Vector4(1, 1, 1, 0) },
  siteExtensionFadeMinRadius: { value: 0 },
  siteExtensionFadeMaxRadius: { value: 1e9 },
  siteExtensionEdgeColor: { value: new THREE.Vector4(1, 1, 1, 0) },
  // 掉落物自己那一套(它不用 `_GlobalEdge*`)
  siteDropItemPhenomenaLightIntensity: { value: 1 },
  siteDropItemShadingEdgeThreshold: { value: 0.65 },
  siteDropItemShadingEdgeSmoothness: { value: 0.04 },
  siteDropItemNormalShadingIntensity: { value: 1 },
  // Object 的「3D 预览」替身色(材质 `_UseObject3DPreviewLight>0.5` 时顶替现象两色)
  siteObject3DPreviewLightColor: { value: new THREE.Vector4(1, 1, 1, 1) },
  siteObject3DPreviewShadeColor: { value: new THREE.Vector4(0.5, 0.5, 0.5, 1) },
  siteDebugShadowMask: { value: 0 },
  // 引擎内建那一档
  siteScreenParams: { value: new THREE.Vector4(1, 1, 1, 1) },   // `_MysekaiScreenParams`
  siteProjectionParams: { value: new THREE.Vector4(1, 0.1, 1000, 0.001) },
  siteTime: { value: 0 },              // `_Time.y`
  siteTimeParameters: { value: 0 },    // `_TimeParameters.x` —— 与上一项**是两个量**
  siteOrthoCamera: { value: 0 },       // `unity_OrthoParams.w`
  // 站点的世界原点(placement 表的 `sitePosition`)。产物写明它由消费方施加、从不烘进几何。
  siteWorldOrigin: { value: new THREE.Vector3(0, 0, 0) },
};

/** 逐项:对应的引擎量名、谁读、值从哪儿来。`chosen` = 本示例挑的默认值,不是产物给的。 */
export const SITE_GLOBAL_META = {
  siteEdgeThreshold: { unity: '_GlobalEdgeThreshold', from: 'chosen', note: '产物无此全局量;默认取材质 `_LocalEdgeThreshold` 的众数 0.65' },
  siteEdgeSmoothness: { unity: '_GlobalEdgeSmoothness', from: 'chosen', note: '产物无此全局量;默认取材质 `_LocalEdgeSmoothness` 的众数 0.04' },
  siteShadowStrength: { unity: '_MainLightShadowParams.x', from: 'chosen', note: '本示例无阴影图,这是落影近似的强度' },
  siteShadowMaskEdge1: { unity: '_ShadowMaskEdge1', from: 'chosen', note: '产物无此全局量;默认 0(与 edge2=1 合起来是 clamp(NdL) 的恒等重映射)' },
  siteShadowMaskEdge2: { unity: '_ShadowMaskEdge2', from: 'chosen', note: '产物无此全局量;默认 1' },
  siteTreasurePos0: { unity: '_MysekaiTreasurePositionArray[0]', from: 'chosen', note: '本示例无宝箱,强度默认 0 等于关' },
  siteTreasurePos1: { unity: '_MysekaiTreasurePositionArray[1]', from: 'chosen', note: '同上' },
  siteTreasureIntensity: { unity: '_MysekaiTreasureShadowIntensityArray[0..1]', from: 'chosen', note: '默认 (0,0) = 关' },
  siteExtensionCenter: { unity: '_SiteExtensionCenter', from: 'chosen', note: '站点边界那一组在当前内容里没有带关键字的材质' },
  siteExtensionRadius: { unity: '_SiteExtensionRadius', from: 'chosen' },
  siteExtensionInnerRadius: { unity: '_SiteExtensionInnerRadius', from: 'chosen' },
  siteExtensionSmoothness: { unity: '_SiteExtensionSmoothness', from: 'chosen' },
  siteExtensionFadeColor: { unity: '_SiteExtensionFadeColor', from: 'chosen', note: 'a=0 等于关' },
  siteExtensionFadeMinRadius: { unity: '_SiteExtensionFadeMinRadius', from: 'chosen' },
  siteExtensionFadeMaxRadius: { unity: '_SiteExtensionFadeMaxRadius', from: 'chosen' },
  siteExtensionEdgeColor: { unity: '_SiteExtensionEdgeColor', from: 'chosen', note: 'a=0 等于关' },
  siteDropItemPhenomenaLightIntensity: { unity: '_MysekaiDropItemPhenomenaLightIntensity', from: 'chosen' },
  siteDropItemShadingEdgeThreshold: { unity: '_MysekaiDropItemShadingEdgeThreshold', from: 'chosen' },
  siteDropItemShadingEdgeSmoothness: { unity: '_MysekaiDropItemShadingEdgeSmoothness', from: 'chosen' },
  siteDropItemNormalShadingIntensity: { unity: '_MysekaiDropItemNormalShadingIntensity', from: 'chosen' },
  siteObject3DPreviewLightColor: { unity: '_Object3DPreviewLightColor', from: 'chosen', note: '材质开关默认全 0,这一路当前无消费方' },
  siteObject3DPreviewShadeColor: { unity: '_Object3DPreviewShadeColor', from: 'chosen' },
  siteDebugShadowMask: { unity: '_DebugShadowMask', from: 'chosen', note: '调试可视化,默认关' },
  siteScreenParams: { unity: '_MysekaiScreenParams', from: 'renderer', note: '逐帧取绘制缓冲尺寸' },
  siteProjectionParams: { unity: '_ProjectionParams', from: 'camera', note: '逐帧取相机 near/far;雾的顶点斜坡用它' },
  siteTime: { unity: '_Time.y', from: 'viewer', note: 'UV 滚动与叶片旋转读它' },
  siteTimeParameters: { unity: '_TimeParameters.x', from: 'viewer', note: '**树的风摆动读它**,与 `_Time` 是两个量,分开接线' },
  siteOrthoCamera: { unity: 'unity_OrthoParams.w', from: 'camera', note: '本示例相机恒为透视,恒 0' },
  siteWorldOrigin: { unity: '(站点世界位置)', from: 'placement', note: 'placement 表的 `sitePosition`;产物写明由消费方施加、从不烘进几何' },
};

/** 写一个站点全局量(面板与判据都走它)。向量按分量写,标量直接赋。 */
export function siteGlobalSet(name, value) {
  const u = SITE_GLOBALS[name];
  if (!u) return false;
  const cur = u.value;
  if (Array.isArray(value) && cur && typeof cur.fromArray === 'function') cur.fromArray(value);
  else if (cur && cur.isVector3 && value && value.isVector3) cur.copy(value);
  else if (Array.isArray(value)) u.value = value.slice();
  else u.value = num(value);
  return true;
}

/** 面板/判据读的一份快照(向量摊成数组)。 */
export function siteGlobalSnapshot() {
  const out = {};
  for (const [k, u] of Object.entries(SITE_GLOBALS)) {
    const v = u.value;
    out[k] = (v && typeof v.toArray === 'function') ? v.toArray() : v;
  }
  return out;
}

/**
 * 依据里**明确未取得或未编译**的条目。一条都不实现,原样列进读数,让缺口看得见。
 * 每条都带搜索范围,范围是「该着色器在已发货编译产物里的全部带源程序」。
 */
export const SITE_NOT_OBTAINED = [
  { shader: 'Mysekai/Object', module: '水面(泡沫/焦散/翻页/扰动/水雾)', why: '整组属性在该着色器的全部带源程序里各 0 次,没有编译进任何已发货变体' },
  { shader: 'Mysekai/Object', module: '树(叶遮罩/叶旋转/摆动)', why: '同上;树的摆动只有 Site/Tree 有' },
  { shader: 'Mysekai/Object', module: '门与光柱', why: '同上' },
  { shader: 'Mysekai/Object', module: '程序化渐变(两色 + 锐度 + 法线混合)', why: '同上' },
  { shader: 'Mysekai/Object', module: '立方体反射与视差折射', why: '同上;切线被顶点段转发但片元段没有任何消费者' },
  { shader: 'Mysekai/Object', module: '叠加贴图(第一层与第二层)', why: '同上' },
  { shader: 'Mysekai/Object', module: '`_ObjectShaderUsage` 在 0/2/8/11/14 之外各档的差异', why: '代码里只判了这几档,其余一律落 default,差异无从读出' },
  { shader: 'Mysekai/Fixture/Basic', module: '程序化渐变', why: '整组属性 0 次' },
  { shader: 'Mysekai/Fixture/Basic', module: '视差折射', why: '整组属性 0 次' },
  { shader: '(全族)', module: '`_PositionShading*` 三项', why: '八个着色器的全部带源程序里各 0 次,等价于未实现' },
  { shader: 'Mysekai/Site/Tree', module: '`_AdditiveColor`', why: '属性存在但 Tree 的代码从不读它(Object 里同名属性是读的)' },
  { shader: '(全族)', module: '法线贴图', why: '这一族一个都没有:属性表里含 bump/normalmap 的属性 0 个,程序里 0 次' },
];

// ---- GLSL:全族共用的一段 ---------------------------------------------------

// 4x4 Bayer 表。依据里那四个点乘展开就是它,按 `[x][y]` 取值(x 为行)。
const SITE_BAYER = /* glsl */`
const float SITE_BAYER_TABLE[16] = float[16](
   0.0, 12.0,  3.0, 15.0,
   8.0,  4.0, 11.0,  7.0,
   2.0, 14.0,  1.0, 13.0,
  10.0,  6.0,  9.0,  5.0);
`;

// 顶点色:三没有 COLOR_0 的网格由 three 喂默认值(1,1,1,1),于是这一格自动成恒等。
const SITE_VCOLOR_MACRO = /* glsl */`
#if defined(USE_COLOR_ALPHA)
  #define SITE_VC color
#else
  #define SITE_VC vec4(color, 1.0)
#endif
`;

const SITE_UNIFORMS = /* glsl */`
uniform vec3 envLightDir;
uniform vec4 envPhenLightColor;
uniform vec4 envPhenShadeColor;
uniform vec4 envDropShadowColor1;
uniform float envDropShadowEdgeSmoothness;
uniform float envEmissionType;
uniform vec4 envSkyBottomColor;
uniform vec3 envSubjectPos;
uniform float envSubjectRadius;

uniform float siteEdgeThreshold;
uniform float siteEdgeSmoothness;
uniform float siteShadowStrength;
uniform float siteShadowMaskEdge1;
uniform float siteShadowMaskEdge2;
uniform vec3 siteTreasurePos0;
uniform vec3 siteTreasurePos1;
uniform vec2 siteTreasureIntensity;
uniform vec3 siteExtensionCenter;
uniform float siteExtensionRadius;
uniform float siteExtensionInnerRadius;
uniform float siteExtensionSmoothness;
uniform vec4 siteExtensionFadeColor;
uniform float siteExtensionFadeMinRadius;
uniform float siteExtensionFadeMaxRadius;
uniform vec4 siteExtensionEdgeColor;
uniform float siteDropItemPhenomenaLightIntensity;
uniform float siteDropItemShadingEdgeThreshold;
uniform float siteDropItemShadingEdgeSmoothness;
uniform float siteDropItemNormalShadingIntensity;
uniform vec4 siteObject3DPreviewLightColor;
uniform vec4 siteObject3DPreviewShadeColor;
uniform float siteDebugShadowMask;
uniform vec4 siteScreenParams;
uniform vec4 siteProjectionParams;
uniform float siteTime;
uniform float siteTimeParameters;
uniform float siteOrthoCamera;
uniform vec3 siteWorldOrigin;
`;

// 顶点侧共用:世界法线、雾斜坡、屏幕坐标。
const SITE_VERT_COMMON = /* glsl */`
${SITE_UNIFORMS}
${SITE_VCOLOR_MACRO}
${ENV_FOG_CHUNK}

// 世界法线。产物那一步是「法线乘世界→物体矩阵」(即逆转置)再归一化,归一化**带
// max(dot, 1.17549435e-38) 保护**,不是裸 inversesqrt —— 保护照抄。
// 这里的 mat3(modelMatrix) 在非均匀缩放下不是严格的法线矩阵(GLSL ES 没有 inverse()),
// 站点节点的缩放基本均匀,按近似记着,不假装它精确。
vec3 siteWorldNormal(vec3 n) {
  vec3 w = mat3(modelMatrix) * n;
  return w * inversesqrt(max(dot(w, w), 1.17549435e-38));
}

// 雾的能见度斜坡。产物用的是**未做透视除法的 clip.z**,不是视深度,照实照抄:
//   d   = max( (clip.z + near) / (near + far) * far , 0 )
//   fog = clamp( d * P.x + P.y , 0, 1 )
float siteFogRamp(vec4 clipPos) {
  float nearZ = siteProjectionParams.y;
  float farZ = siteProjectionParams.z;
  float d = max((clipPos.z + nearZ) / (nearZ + farZ) * farZ, 0.0);
  return clamp(d * envFogParams.x + envFogParams.y, 0.0, 1.0);
}

// 屏幕坐标:.xy 是 0.5*clip.xy(+_ProjectionParams.x 定 y 向)+0.5*clip.w,.zw = clip.zw。
vec4 siteScreenPos(vec4 clipPos) {
  return vec4(clipPos.x * 0.5 + clipPos.w * 0.5,
              clipPos.y * 0.5 * siteProjectionParams.x + clipPos.w * 0.5,
              clipPos.z, clipPos.w);
}
`;

// 片元侧共用。
const SITE_FRAG_COMMON = /* glsl */`
${SITE_UNIFORMS}
${SITE_BAYER}
${ENV_FOG_CHUNK}

layout(location = 0) out vec4 SV_Target0;
layout(location = 1) out vec4 SV_Target1;

// 视线方向。正交相机改取视矩阵第三列 —— 本示例相机恒为透视,那一支走不到,计数为未走到。
vec3 siteViewDir(vec3 posWS) {
  return normalize(cameraPosition + siteWorldOrigin - posWS);
}

// 抖动剪除。'scale' 是屏幕缩放:FieldObject / Object 是 0.25(4 像素格),
// Tree / Fixture 是 0.125(8 像素格)—— 同族不一致,照原样。
bool siteDitherDiscard(vec4 screenPos, float scale, float ditherAlpha) {
  vec2 sp = screenPos.xy / screenPos.w;
  vec2 c = fract(abs(sp * siteScreenParams.xy * scale)) * 4.0;
  int ix = int(clamp(c.x, 0.0, 3.999));
  int iy = int(clamp(c.y, 0.0, 3.999));
  float thr = SITE_BAYER_TABLE[ix * 4 + iy] * 0.0618750006 + 0.00999999978;
  return ditherAlpha < thr;
}

// toon 分段:**线性斜坡,没有 smoothstep**。h 是半兰伯特值。
float siteToonT(float h, float thr, float smo) {
  float hi = thr + smo;
  float lo = thr - smo;
  return clamp((h - hi) / (lo - hi), 0.0, 1.0);
}

// 现象方向光:乘进去再按 a 插值。
vec3 sitePhenomenaLight(vec3 c, vec4 lightColor) {
  return mix(c, c * lightColor.rgb, lightColor.a);
}

// 宝箱阴影两个。半径 0.96 内全暗、0.96..1.00 过渡,强度**再硬乘 0.5** —— 照原样。
vec3 siteTreasureShadow(vec3 c, vec3 posWS) {
  float d0 = length(posWS.xz - siteTreasurePos0.xz);
  float m0 = clamp((d0 - 0.959999979) * 24.9999866, 0.0, 1.0);
  c = mix(c, c * m0, siteTreasureIntensity.x * 0.5);
  float d1 = length(posWS.xz - siteTreasurePos1.xz);
  float m1 = clamp((d1 - 0.959999979) * 24.9999866, 0.0, 1.0);
  c = mix(c, c * m1, siteTreasureIntensity.y * 0.5);
  return c;
}

// 主光落影上色:先按落影色乘一遍,再按 atten 插回原色。
vec3 siteDropShadow(vec3 c, float atten) {
  vec3 d = mix(c, c * envDropShadowColor1.rgb, envDropShadowColor1.a);
  return mix(d, c, atten);
}

// 菲涅耳:**相加**,不是插值。
vec3 siteFresnel(vec3 c, vec3 N, vec3 V, float power, vec4 fresnelColor) {
  float f = pow(clamp(1.0 - dot(N, V), 0.0, 1.0), power);
  return c + f * fresnelColor.rgb * fresnelColor.a;
}

// 主光阴影衰减。**本示例没有阴影 pass,产不出那张阴影图**;这一格由示例原有的球体落影
// 近似顶着:角色沿光向投到本片元所在高度的水平面,朝上分量与「只投在角色下方」两个权重
// 是本示例的近似。落影的**律**(套一次/两次/平方)照原样跑,近似只换了 atten 的来源。
float siteAtten(vec3 posWS, vec3 N) {
  vec3 posLocal = posWS - siteWorldOrigin;
  float dy = envSubjectPos.y - posLocal.y;
  vec2 off = envLightDir.y > 1e-3
    ? envSubjectPos.xz - envLightDir.xz * (dy / max(envLightDir.y, 1e-3))
    : envSubjectPos.xz;
  float d = length(posLocal.xz - off) / max(envSubjectRadius, 1e-3);
  float edge = clamp(envDropShadowEdgeSmoothness, 0.02, 4.0);
  float core = 1.0 - smoothstep(1.0 - edge, 1.0, clamp(d, 0.0, 1.0));
  return clamp(1.0 - core * clamp(N.y, 0.0, 1.0) * step(0.0, dy) * siteShadowStrength, 0.0, 1.0);
}

// 发光门控:全局整型现象发光类型 × 材质声明的两个 int。0/1 门,不是强度。
float siteEmissionGate(float bright, float dark, float manual, float debugOn, float overrideMode) {
  float gate = 0.0;
  if (envEmissionType > 1.5 && envEmissionType < 2.5) gate = (dark > 0.5) ? 1.0 : 0.0;
  else if (envEmissionType > 0.5 && envEmissionType < 1.5) gate = (bright > 0.5) ? 1.0 : 0.0;
  if (manual > 0.5 || debugOn > 0.5) gate = 1.0;
  if (overrideMode > 0.5 && overrideMode < 1.5) gate = 1.0;
  if (overrideMode > 1.5) gate = 0.0;
  return gate;
}
`;

// 关键字与运行期分支的分界,写明一次:
//   * 材质记录里**真的带着**的关键字(`_USE_ALPHA_CLIP` / `_DISABLE_DITHER` /
//     `_USE_OVERLAY_TEXTURE` / `_USE_OVERLAY_TEXTURE_2ND` / `_ENABLE_MODULE_FRESNEL` /
//     `_ENABLE_MODULE_REFLECTION` / `_USE_TREE_ANIMATION` / `_USE_HEIGHT_FADE` /
//     `_USE_RARE` / `_RECEIVE_SHADOWS_OFF`)照原样做成 `#define`,变体之间真的换程序。
//   * 由管线或站点控制器在运行时下发、**不会出现在材质记录里**的那几个
//     (`_MAIN_LIGHT_SHADOWS` / `_USE_MYSEKAI_FOG` / `_USE_MYSEKAI_SITE_EXTENSION` /
//     `_SKIP_PHENOMENA_LIGHT`)在这里做成运行期分支,由对应的全局量或材质属性驱动:
//     开关两态的画面与关键字版本相同,差别只在编译期。这一条如实记在读数里。

const SITE_PROGRAMS = {};

// ---- 1. Mysekai/Site/FieldObject(站点材质第一名)---------------------------
SITE_PROGRAMS.fieldObject = {
  shader: 'Mysekai/Site/FieldObject',
  vert: /* glsl */`
${SITE_VERT_COMMON}
uniform vec4 mOverlayST;
uniform vec2 mOverlayScroll;
uniform float mOverlayUvSet;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vVC;
varying float vFogRamp;
varying vec4 vScreen;
varying vec2 vOverlayUv;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz + siteWorldOrigin;
  vWorldNormal = siteWorldNormal(normal);
  vUv0 = uv;                       // 怪处 A:主 pass **不套** _MainTex_ST(只有投影 pass 用)
  vVC = SITE_VC;
  vec4 clip = projectionMatrix * viewMatrix * wp;
  vFogRamp = siteFogRamp(clip);
  vScreen = siteScreenPos(clip);
  vec2 ov = uv;
#ifdef USE_UV1
  if (mOverlayUvSet > 0.5) ov = uv1;
#endif
  vOverlayUv = ov * mOverlayST.xy + mOverlayST.zw - siteTime * mOverlayScroll;
  gl_Position = clip;
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform sampler2D mMainTex;
uniform sampler2D mOverlayTex;
uniform float mAlphaClip;
uniform float mDitherAlpha;
uniform float mBaseOpacity;
uniform float mUseVertexColorBlend;
uniform float mUseOverlayVertexAlpha;
uniform float mOverrideShading;
uniform float mLocalEdgeThreshold;
uniform float mLocalEdgeSmoothness;
uniform float mLocalShadingIntensity;
uniform float mUseFresnel;
uniform float mFresnelPower;
uniform vec4 mFresnelColor;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vVC;
varying float vFogRamp;
varying vec4 vScreen;
varying vec2 vOverlayUv;
void main() {
  vec4 B = texture2D(mMainTex, vUv0);                       // 1 基础采样(原始 uv0)
#ifdef SITE_ALPHA_CLIP
  if (B.a < mAlphaClip) discard;                            // 2 阈值是材质的 _AlphaClip
#endif
#ifndef SITE_DISABLE_DITHER
  if (siteDitherDiscard(vScreen, 0.25, mDitherAlpha)) discard;   // 3 抖动,屏幕缩放 0.25
#endif
  vec3 N = normalize(vWorldNormal);
  float S = 1.0;                                            // 4 主光阴影衰减
#ifndef SITE_RECEIVE_SHADOWS_OFF
  S = siteAtten(vWorldPos, N);
#endif
  vec3 C = B.rgb;
#ifdef SITE_OVERLAY_1ST
  float va = (mUseOverlayVertexAlpha > 0.5) ? vVC.a : 1.0;  // 5 叠加贴图:覆盖式插值,只改 RGB
  vec4 O = texture2D(mOverlayTex, vOverlayUv);
  C = mix(C, O.rgb, O.a * va);
#endif
  C = mix(C, C * vVC.rgb * vVC.rgb, mUseVertexColorBlend);  // 6 顶点色:**平方**(怪处 B)
  float outA = B.a * mBaseOpacity;                          // 7 顶点色 alpha 不进输出 alpha
  C = sitePhenomenaLight(C, envPhenLightColor);             // 8 现象方向光
  float ov = step(0.5, mOverrideShading);                   // 9 toon:h 用**未 clamp** 的 dot
  float thr = mix(siteEdgeThreshold, mLocalEdgeThreshold, ov);
  float smo = mix(siteEdgeSmoothness, mLocalEdgeSmoothness, ov);
  float inten = mix(1.0, mLocalShadingIntensity, ov);
  float h = dot(envLightDir, N) * 0.5 + 0.5;
  C = mix(C, C * envPhenShadeColor.rgb, inten * siteToonT(h, thr, smo));
  C = siteDropShadow(C, S);                                 // 10 落影上色(第一次)
  C = siteTreasureShadow(C, vWorldPos);                     // 11 宝箱阴影 ×2
  if (mUseFresnel > 0.5) {                                  // 12 菲涅耳:**相加**
    C = siteFresnel(C, N, siteViewDir(vWorldPos), mFresnelPower, mFresnelColor);
  }
  C = siteDropShadow(C, S);                                 // 13 怪处 C:落影再套一次,S 未改写
  SV_Target0 = vec4(envApplyFogRamp(C, vFogRamp, vWorldPos.y), outA);   // 14 雾,clamp 到 0..1
  SV_Target1 = vec4(0.0);                                   // 15 本着色器恒写 0
}
`,
};

// ---- 2. Mysekai/Object(全族唯一往第二目标写非零的两个之一)-----------------
SITE_PROGRAMS.object = {
  shader: 'Mysekai/Object',
  vert: /* glsl */`
${SITE_VERT_COMMON}
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec4 vUv01;
varying vec4 vUv23;
varying vec4 vVC;
varying float vFogRamp;
varying vec4 vScreen;
varying vec3 vObjectOrigin;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz + siteWorldOrigin;
  vWorldNormal = siteWorldNormal(normal);
  vec2 u1 = vec2(0.0);
  vec2 u2 = vec2(0.0);
  vec2 u3 = vec2(0.0);
#ifdef USE_UV1
  u1 = uv1;
#endif
#ifdef USE_UV2
  u2 = uv2;
#endif
#ifdef USE_UV3
  u3 = uv3;
#endif
  vUv01 = vec4(uv, u1);
  vUv23 = vec4(u2, u3);
  vVC = SITE_VC;
  // 道路阴影按「相对物体原点的水平偏移」算,原点就是世界矩阵的第四列。
  vObjectOrigin = (modelMatrix[3].xyz) + siteWorldOrigin;
  vec4 clip = projectionMatrix * viewMatrix * wp;
  vFogRamp = siteFogRamp(clip);
  vScreen = siteScreenPos(clip);
  gl_Position = clip;
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform sampler2D mMainTex;
uniform sampler2D mEmissionMaskTex;
uniform float mBaseTextureMappingMode;
uniform float mMainTextureLocalMapping;
uniform float mObjectShaderUsage;
uniform float mRoadTextureScale;
uniform vec2 mUvScroll;
uniform vec4 mAlphaTilingOffset;
uniform float mAlphaClip;
uniform float mDitherAlpha;
uniform float mBaseOpacity;
uniform float mUseVertexColorBlend;
uniform float mUseVertexAlphaOpacity;
uniform float mUseFresnel;
uniform float mFresnelPower;
uniform vec4 mFresnelColor;
uniform vec4 mBackFaceColor;
uniform float mCullOff;
uniform float mUsePhenomenaLighting;
uniform float mUseObject3DPreviewLight;
uniform float mOverrideShading;
uniform float mLocalEdgeThreshold;
uniform float mLocalEdgeSmoothness;
uniform float mLocalShadingIntensity;
uniform float mWallAOIntensity;
uniform float mWallAOExponent;
uniform vec2 mWallAOScale;
uniform vec4 mAdditiveColor;
uniform float mBrightEmission;
uniform float mDarkEmission;
uniform float mManualEmission;
uniform float mDebugEmission;
uniform float mUseHeightFade;
uniform float mHeightFadePosition;
uniform float mHeightFadeLength;
uniform float mHeightFadeExponent;
uniform float mSiteExtensionOn;
uniform float mHasUv3;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec4 vUv01;
varying vec4 vUv23;
varying vec4 vVC;
varying float vFogRamp;
varying vec4 vScreen;
varying vec3 vObjectOrigin;
void main() {
#ifndef SITE_DISABLE_DITHER
  if (siteDitherDiscard(vScreen, 0.25, mDitherAlpha)) discard;   // 1 抖动,与 FieldObject 逐字相同
#endif
  vec3 N = normalize(vWorldNormal);                              // 2
  vec3 V = siteViewDir(vWorldPos);
  int mode = int(mBaseTextureMappingMode + 0.5);                 // 3 基础 UV 选择
  vec2 uvB = vUv01.xy;
  if (mode == 1) uvB = vWorldPos.xz;
  else if (mode == 2) uvB = vUv01.zw;
  else if (mode == 3) uvB = vUv23.xy;
  if (mMainTextureLocalMapping > 0.5) uvB = vUv01.xy;            //   强制回 uv0
  bool isRoad = abs(mObjectShaderUsage - 11.0) < 0.5;
  bool isField = abs(mObjectShaderUsage - 8.0) < 0.5;
  if (isRoad) uvB *= mRoadTextureScale;
  uvB = uvB - siteTime * mUvScroll;                              //   滚动是减号
  vec4 B = texture2D(mMainTex, uvB);                             // 4 采样与裁剪
  if (isRoad) {
    // 道路用**同一张图的另一套 UV**单独取 alpha 做裁剪,颜色仍用上面那条。
    float a = texture2D(mMainTex, vUv01.xy * mAlphaTilingOffset.xy + mAlphaTilingOffset.zw).a;
    if (a < mAlphaClip) discard;
  } else {
#ifdef SITE_ALPHA_CLIP
    if (B.a < mAlphaClip) discard;
#endif
  }
  vec3 C = mix(B.rgb, B.rgb * vVC.rgb, mUseVertexColorBlend);    // 5 顶点色(第一次)
  C = siteTreasureShadow(C, vWorldPos);                          // 6 宝箱阴影 ×2
  if (mUseFresnel > 0.5) C = siteFresnel(C, N, V, mFresnelPower, mFresnelColor);   // 7
  if (mCullOff > 0.5 && !gl_FrontFacing) {                       // 8 背面色:只在双面时
    C = mix(C, mBackFaceColor.rgb, mBackFaceColor.a);
  }
  if (mUsePhenomenaLighting > 0.5) {                             // 9 整块被材质开关包住
    bool prev = mUseObject3DPreviewLight > 0.5;
    vec4 L = prev ? siteObject3DPreviewLightColor : envPhenLightColor;
    vec4 shadeCol = prev ? siteObject3DPreviewShadeColor : envPhenShadeColor;
    C = sitePhenomenaLight(C, L);                                // 9a
    float ndl = clamp(dot(envLightDir, N), 0.0, 1.0);            // 9b 阴影遮罩
    float mask = clamp((ndl - siteShadowMaskEdge1)
                       / (siteShadowMaskEdge2 - siteShadowMaskEdge1), 0.0, 1.0);
    if (abs(mObjectShaderUsage) > 0.5) mask = 1.0;               //    只有 usage==0 用遮罩
    if (siteDebugShadowMask > 0.5) {                             // 9c 调试可视化
      SV_Target0 = vec4(vec3(mask), 1.0);
      SV_Target1 = vec4(0.0);
      return;
    }
    float atten = 1.0;                                           // 9d 主光阴影
#ifndef SITE_RECEIVE_SHADOWS_OFF
    atten = siteAtten(vWorldPos, N);
#endif
    float ov = step(0.5, mOverrideShading);                      // 9e toon:按 usage 分档
    float inten = mix(1.0, mLocalShadingIntensity, ov);
    int usage = int(mObjectShaderUsage + 0.5);
    if (usage == 2) {
      // case 2(Wall):不做半兰伯特,改用「顶点色 R + 墙面 AO」。
      // 墙面 AO 读的是 **uv3**,与其它模块都不同;产物导出的站点网格只带 uv0/uv1,
      // 取不到 uv3 时整块停用并计数 —— 拿缺失的输入硬算会画出一整片全暗的墙。
      float a0 = 1.0 - inten * vVC.r;
      float k = 1.0;
      if (mHasUv3 > 0.5) {
        vec2 p = vUv23.zw * 2.0 - 1.0;
        vec2 q = pow(abs(p), mWallAOScale * mWallAOExponent);
        float ao = 1.0 - dot(q, q);
        k = mix(1.0, ao, mWallAOIntensity);
      }
      C = mix(C * shadeCol.rgb, C, k * a0);
    } else if (usage == 14) {
      // case 14(Floor):**什么都不做** —— 不上 toon、不上暗部色。
    } else {
      float thr = mix(siteEdgeThreshold, mLocalEdgeThreshold, ov);
      float smo = mix(siteEdgeSmoothness, mLocalEdgeSmoothness, ov);
      vec3 C2 = mix(C, C * vVC.rgb, mUseVertexColorBlend);       //    顶点色**第二次**(怪处 A)
      float h = ndl * 0.5 + 0.5;                                 //    h 用 **clamp 后**的 dot
      float t = siteToonT(h, thr, smo) * shadeCol.a * inten;     //    Object 独有:乘 shade 的 a
      C = mix(C2, C2 * shadeCol.rgb, t);
    }
    float roadAtten = atten;                                     // 9f 道路阴影(只有 usage==11)
    if (isRoad) roadAtten = 1.0;                                 //    路网连接数据不在产物里
    // 9g 怪处 B:usage != Road 时 atten 被乘了两遍(= atten 的平方)。
    float S = mix(1.0, atten * roadAtten, mask);
    C = siteDropShadow(C, S);                                    // 9h 落影上色**只做一次**
    C = envApplyFogRamp(C, vFogRamp, vWorldPos.y);               // 9i 雾
  }
  if (mSiteExtensionOn > 0.5) {                                  // 10 站点边界淡出环
    float dist = length(vWorldPos.xz - siteExtensionCenter.xz);
    bool inMin = dist >= siteExtensionFadeMinRadius;
    bool inMax = siteExtensionFadeMaxRadius >= dist;
    float tt = clamp((dist - siteExtensionInnerRadius)
                     / (siteExtensionRadius - siteExtensionInnerRadius), 0.0, 1.0);
    if (inMin && inMax && tt <= 0.999000013) {
      C = mix(C, siteExtensionFadeColor.rgb, tt * siteExtensionFadeColor.a);
    }
    if (isField) {                                               // 11 边缘 + 剪除,只有 usage==8
      float e = clamp(clamp(dist - siteExtensionRadius, 0.0, 1.0) / siteExtensionSmoothness, 0.0, 1.0);
      float sm = e * e * (3.0 - 2.0 * e);
      if (sm < 0.001 && dist < siteExtensionFadeMaxRadius) discard;
      if (sm <= 0.999000013 && inMax) {
        C = mix(C, siteExtensionEdgeColor.rgb, (1.0 - sm) * siteExtensionEdgeColor.a);
      }
    }
  }
  float a = (mUseVertexAlphaOpacity > 0.5) ? B.a * vVC.a : B.a;  // 12 顶点 alpha 可选进输出
  SV_Target0.w = a * mBaseOpacity;
  C = C + mAdditiveColor.rgb * mAdditiveColor.a;                 // 13 附加色(相加)
  // 14 发光 → **第二个渲染目标**。0/1 门控,不加进主目标。
  float gate = siteEmissionGate(mBrightEmission, mDarkEmission, mManualEmission, mDebugEmission, 0.0);
  SV_Target1 = vec4(clamp(texture2D(mEmissionMaskTex, uvB).rgb * gate, 0.0, 1.0), 0.0);
  if (mUseHeightFade > 0.5) {                                    // 15 高度淡出(最后一步)
    float lo = mHeightFadePosition - mHeightFadeLength;
    float t = clamp((vWorldPos.y - lo) / max(mHeightFadePosition - lo, 1e-6), 0.0, 1.0);
    t = min(pow(t, mHeightFadeExponent), 1.0);
    C = mix(envSkyBottomColor.rgb, C, t);
  }
  SV_Target0.xyz = C;                                            //    雾之后**不再 clamp**
}
`,
};

// ---- 3. Mysekai/Site/Tree(全族唯一有顶点动画的)-----------------------------
SITE_PROGRAMS.tree = {
  shader: 'Mysekai/Site/Tree',
  vert: /* glsl */`
${SITE_VERT_COMMON}
uniform sampler2D mLeafMaskTex;
uniform float mTurbulence;
uniform float mStrength;
uniform float mLeafRotationSpeed;
uniform float mLeafRotationRange;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vVC;
varying float vFogRamp;
varying vec4 vScreen;
varying vec2 vScreenRaw;
void main() {
  vec3 p = position;
#ifdef SITE_TREE_ANIMATION
  // 风的摆动。**读的是 '_TimeParameters.x',不是 '_Time'** —— 两个是不同的量,接错频率就不对。
  // 全部在物体空间,原点即物体轴心;最后一步把位移后的点**投回原来的球面半径**,
  // 所以整棵树是绕物体原点「摆」,不会被拉长。摆动没有相位随机,同材质的树同相。
  float s = sin(siteTimeParameters * mTurbulence);
  float w = (0.1 * s) * (0.1 * s - 0.1) + 0.1;          // 取值 0.08..0.12,恒为正
  vec2 sway = vec2(0.660000026, 1.0) * w;               // x 方向幅度是 z 方向的 0.66
  float g = position.y * mStrength * 0.0110000018 + 1.0;
  float hw = g * g * g * g - g * g;                     // 高度权重:y 越高摆得越多
  vec3 q = vec3(position.x + sway.x * hw, position.y, position.z + sway.y * hw);
  float len = length(position);
  float qq = dot(q, q);
  p = (qq > 0.0) ? normalize(q) * len : position;
#endif
  vec2 uv0 = uv;
#ifdef SITE_TREE_ANIMATION
  // 叶片 UV 旋转:另一套动画,读 '_Time.y'。绕 UV 点 (0.75, 0.25) 转,
  // 比例常数是「度转弧度的一半」,被 '_LeafMaskTex' 在**顶点**上按阈值 0.5 选中的才转。
  float ang = sin(siteTime * mLeafRotationSpeed * 3.14159274) * mLeafRotationRange * 0.00872664712;
  float cs = cos(ang);
  float sn = sin(ang);
  float dx = uv.x - 0.75;
  float dy = uv.y - 0.25;
  vec2 rotated = vec2(0.75 + (cs * dx - sn * dy), 0.25 + (sn * dx + cs * dy));
  float mask = textureLod(mLeafMaskTex, uv, 0.0).x;
  uv0 = (mask > 0.5) ? rotated : uv;
#endif
  vec4 wp = modelMatrix * vec4(p, 1.0);
  vWorldPos = wp.xyz + siteWorldOrigin;
  vWorldNormal = siteWorldNormal(normal);
  vUv0 = uv0;
  vVC = SITE_VC;
  vec4 clip = projectionMatrix * viewMatrix * wp;
  vFogRamp = siteFogRamp(clip);
  vScreen = siteScreenPos(clip);
  vScreenRaw = vScreen.xy;          // 稀有覆盖 case 1 用的是**未除 w** 的屏幕坐标
  gl_Position = clip;
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform sampler2D mMainTex;
uniform sampler2D mRareOverlayTexture;
uniform float mDitherAlpha;
uniform float mUseVertexColorBlend;
uniform float mUsePhenomenaLighting;
uniform float mOverrideShading;
uniform float mLocalEdgeThreshold;
uniform float mLocalEdgeSmoothness;
uniform float mLocalShadingIntensity;
uniform float mFresnelPower;
uniform vec4 mFresnelColor;
uniform float mHeightFadeRcpLength;
uniform float mHeightFadeStartTimeRcpLength;
uniform float mHeightFadeExponent;
uniform vec4 mHeightGradientColor0;
uniform vec4 mHeightGradientColor1;
uniform vec4 mHeightGradientColor2;
uniform float mHeightGradientPos01;
uniform float mHeightGradientPos12;
uniform float mTreeRarityType;
uniform vec4 mRareST;
uniform vec2 mRareScroll;
uniform vec4 mRareBaseColor;
uniform vec3 mTreeRareCenter;
uniform float mRareSphereNormalBlend;
uniform float mRareFresnelPower;
uniform float mRareFresnelEdge;
uniform float mRareFresnelEdge2;
uniform float mRareFresnelIntensity;
uniform float mFlowerRareIntensity;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vVC;
varying float vFogRamp;
varying vec4 vScreen;
varying vec2 vScreenRaw;
void main() {
  vec4 B = texture2D(mMainTex, vUv0);                       // 1 基础采样(没有 _MainTex_ST)
  vec3 N = normalize(vWorldNormal);                         // 2
  vec2 screenUV = vScreen.xy / vScreen.w;
  vec3 V = siteViewDir(vWorldPos);
#ifdef SITE_ALPHA_CLIP
  if (B.a < 0.5) discard;                                   // 3 阈值**硬编码 0.5**,不是 _AlphaClip
#endif
#ifndef SITE_DISABLE_DITHER
  if (siteDitherDiscard(vScreen, 0.125, mDitherAlpha)) discard;  // 4 屏幕缩放 **0.125**,同族不一致
#endif
  bool vcOn = mUseVertexColorBlend >= 0.5;                   // 5 顶点色是硬开关,不是插值
  vec3 C = vcOn ? B.rgb * vVC.rgb : B.rgb;
  if (mUsePhenomenaLighting > 0.5) {                         // 6 整块被开关包住
#ifdef SITE_MODULE_FRESNEL
    C = siteFresnel(C, N, V, mFresnelPower, mFresnelColor);  // 6a 菲涅耳**排在方向光之前**
#endif
    C = sitePhenomenaLight(C, envPhenLightColor);            // 6b
    float ov = step(0.5, mOverrideShading);                  // 6c toon 系数,h 未 clamp
    float thr = mix(siteEdgeThreshold, mLocalEdgeThreshold, ov);
    float smo = mix(siteEdgeSmoothness, mLocalEdgeSmoothness, ov);
    float inten = mix(1.0, mLocalShadingIntensity, ov);
    float k = inten * siteToonT(dot(envLightDir, N) * 0.5 + 0.5, thr, smo);
    float atten = 1.0;                                       // 6d 主光阴影
#ifndef SITE_RECEIVE_SHADOWS_OFF
    atten = siteAtten(vWorldPos, N);
#endif
    C = mix(C, C * envPhenShadeColor.rgb, k);                // 6e 暗部色,**不乘 shade 的 a**
    C = siteDropShadow(C, atten);                            // 6f 落影(第一次)
    C = siteTreasureShadow(C, vWorldPos);                    // 6g 宝箱阴影 ×2
    C = vcOn ? C * vVC.rgb : C;                              // 6h 顶点色**又乘一遍**(= 平方)
    C = siteDropShadow(C, atten);                            // 6i 落影(第二次,atten 未改写)
    C = envApplyFogRamp(C, vFogRamp, vWorldPos.y);           // 6j 雾
#ifdef SITE_HEIGHT_FADE
    float u = clamp(vWorldPos.y * mHeightFadeRcpLength - mHeightFadeStartTimeRcpLength, 0.0, 1.0);
    float e0 = min(pow(clamp(u / max(mHeightGradientPos01, 1e-6), 0.0, 1.0), mHeightFadeExponent), 1.0);
    vec3 c01 = mix(mHeightGradientColor0.rgb, mHeightGradientColor1.rgb, e0);
    float e1 = min(pow(clamp((u - mHeightGradientPos01)
                             / max(mHeightGradientPos12 - mHeightGradientPos01, 1e-6), 0.0, 1.0),
                       mHeightFadeExponent), 1.0);
    vec3 c12 = mix(mHeightGradientColor1.rgb, mHeightGradientColor2.rgb, e1);
    c12 = (mHeightGradientPos12 >= u) ? c12 : mHeightGradientColor2.rgb;
    vec3 g = (mHeightGradientPos01 >= u) ? c01 : c12;
    C = mix(C, g, u);                                        // 6k 三色高度渐变
#endif
  }
#ifdef SITE_RARE
  int rarity = int(mTreeRarityType + 0.5);                   // 7 稀有覆盖(在 if 之外)
  if (rarity == 1) {
    vec2 uvR = vScreenRaw * mRareST.xy - siteTime * mRareScroll;   // 未除 w 的屏幕坐标,照原样
    vec3 base = mix(C, C * mRareBaseColor.rgb, mRareBaseColor.a);
    vec3 O = texture2D(mRareOverlayTexture, uvR).rgb;
    vec3 sphN = normalize(vWorldPos - mTreeRareCenter);
    vec3 n2 = normalize(N + mRareSphereNormalBlend * (sphN - N));
    float x = pow(fract(-dot(n2, V)), mRareFresnelPower);    // **fract**,不是 saturate/abs
    float t = clamp((x - mRareFresnelEdge)
                    / max(mRareFresnelEdge2 - mRareFresnelEdge, 1e-6), 0.0, 1.0);
    t = t * t * (3.0 - 2.0 * t);
    t = clamp(t * mRareFresnelIntensity, 0.0, 1.0);
    C = clamp(mix(base, O, t), 0.0, 1.0);
  } else if (rarity == 2) {
    vec2 uvR = screenUV * mRareST.xy - siteTime * mRareScroll;     // 这一支**除过 w**
    C = clamp(mix(C, texture2D(mRareOverlayTexture, uvR).rgb, mFlowerRareIntensity), 0.0, 1.0);
  }
#endif
  SV_Target0 = vec4(C, 1.0);                                 // 8 alpha **恒为 1**
  SV_Target1 = vec4(0.0);
}
`,
};

// ---- 4. Mysekai/Site/Ground(顶点色的平方在顶点段明写)------------------------
SITE_PROGRAMS.ground = {
  shader: 'Mysekai/Site/Ground',
  vert: /* glsl */`
${SITE_VERT_COMMON}
uniform vec2 mUvScroll;
uniform vec4 mOverlayST;
uniform vec2 mOverlayScroll;
uniform float mOverlayUvSet;
uniform vec4 mOverlay2ndST;
uniform vec2 mOverlay2ndScroll;
uniform float mOverlay2ndUvSet;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUvBase;
varying vec3 vViewDir;
varying vec4 vVCsq;
varying vec2 vOverlayUv;
varying vec2 vOverlay2ndUv;
varying float vFogRamp;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz + siteWorldOrigin;
  vWorldNormal = siteWorldNormal(normal);
  vUvBase = uv - siteTime * mUvScroll;
  vViewDir = normalize(cameraPosition + siteWorldOrigin - vWorldPos);
  // **顶点色的平方在这里是明写的**:整族的顶点色都按平方用,这一条最直接。
  vec4 vc = SITE_VC;
  vVCsq = vec4(vc.rgb * vc.rgb, vc.a);
  vec2 o1 = vUvBase;                    // 叠加 UV 的 case 0 取的是**已经滚过**的基础 UV
  vec2 o2 = vUvBase;
#ifdef USE_UV1
  if (mOverlayUvSet > 0.5) o1 = uv1;
  if (mOverlay2ndUvSet > 0.5) o2 = uv1;
#endif
  vOverlayUv = o1 * mOverlayST.xy + mOverlayST.zw - siteTime * mOverlayScroll;
  vOverlay2ndUv = o2 * mOverlay2ndST.xy + mOverlay2ndST.zw - siteTime * mOverlay2ndScroll;
  vec4 clip = projectionMatrix * viewMatrix * wp;
  vFogRamp = siteFogRamp(clip);
  gl_Position = clip;
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform sampler2D mMainTex;
uniform sampler2D mOverlayTex;
uniform sampler2D mOverlay2ndTex;
uniform float mUseVertexAlphaOpacity;
uniform float mBaseOpacity;
uniform float mUseVertexColorBlend;
uniform float mUseOverlayVertexAlpha;
uniform float mUseOverlayVertexAlpha2nd;
uniform float mOverrideShading;
uniform float mLocalEdgeThreshold;
uniform float mLocalEdgeSmoothness;
uniform float mLocalShadingIntensity;
uniform float mFresnelPower;
uniform vec4 mFresnelColor;
uniform float mSkipPhenomenaLighting;
uniform float mSiteExtensionOn;
uniform float mUseHeightFade;
uniform float mHeightFadeRcpLength;
uniform float mHeightFadeStartTimeRcpLength;
uniform float mHeightFadeExponent;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUvBase;
varying vec3 vViewDir;
varying vec4 vVCsq;
varying vec2 vOverlayUv;
varying vec2 vOverlay2ndUv;
varying float vFogRamp;
void main() {
  vec4 B = texture2D(mMainTex, vUvBase);
  // 1 alpha 与裁剪:裁的是**已乘顶点 alpha 的**那一个,阈值**硬编码 0.5**。
  float a = (mUseVertexAlphaOpacity > 0.5) ? B.a * vVCsq.a : B.a;
  float outA = a * mBaseOpacity;
#ifdef SITE_ALPHA_CLIP
  if (a < 0.5) discard;
#endif
  if (mSkipPhenomenaLighting > 0.5) {
    // 这一支整条光照链被切掉:只有「基础贴图 × 平方顶点色」。
    vec3 only = (mUseVertexColorBlend > 0.5) ? B.rgb * vVCsq.rgb : B.rgb;
    SV_Target0 = vec4(only, outA);
    SV_Target1 = vec4(0.0);
    return;
  }
  vec3 N = normalize(vWorldNormal);
  float atten = 1.0;                                          // 2 主光阴影
#ifndef SITE_RECEIVE_SHADOWS_OFF
  atten = siteAtten(vWorldPos, N);
#endif
  float ndl = clamp(dot(envLightDir, N), 0.0, 1.0);           // 3 遮罩合成
  float mask = clamp((ndl - siteShadowMaskEdge1)
                     / (siteShadowMaskEdge2 - siteShadowMaskEdge1), 0.0, 1.0);
  float S = mix(1.0, atten, mask);                            //   这里**没有** Object 那个平方
  float h = dot(envLightDir, N) * 0.5 + 0.5;                  //   h 另存,用的是未 clamp 的 dot
  vec3 C = B.rgb;
#ifdef SITE_OVERLAY_1ST
  {
    float va = (mUseOverlayVertexAlpha > 0.5) ? vVCsq.a : 1.0;   // 4 叠加贴图
    vec4 O = texture2D(mOverlayTex, vOverlayUv);
    C = mix(B.rgb, O.rgb, va * O.a);
  }
#endif
#ifdef SITE_OVERLAY_2ND
  {
    float va = (mUseOverlayVertexAlpha2nd > 0.5) ? vVCsq.a : 1.0;
    vec4 O = texture2D(mOverlay2ndTex, vOverlay2ndUv);
    C = mix(B.rgb, O.rgb, va * O.a);
  }
#endif
  if (mUseVertexColorBlend > 0.5) C = C * vVCsq.rgb;           // 5 顶点色(顶点段已平方)
  C = sitePhenomenaLight(C, envPhenLightColor);                // 6 现象方向光
  float ov = step(0.5, mOverrideShading);                      // 7 toon
  float thr = mix(siteEdgeThreshold, mLocalEdgeThreshold, ov);
  float smo = mix(siteEdgeSmoothness, mLocalEdgeSmoothness, ov);
  float inten = mix(1.0, mLocalShadingIntensity, ov);
  C = mix(C, C * envPhenShadeColor.rgb, inten * siteToonT(h, thr, smo));
  C = siteDropShadow(C, S);                                    // 8 落影**只做一次**
  C = siteTreasureShadow(C, vWorldPos);                        // 9 宝箱阴影 ×2
#ifdef SITE_MODULE_FRESNEL
  C = siteFresnel(C, N, normalize(vViewDir), mFresnelPower, mFresnelColor);   // 10 视线来自顶点段
#endif
  C = envApplyFogRamp(C, vFogRamp, vWorldPos.y);               // 11 雾
  if (mSiteExtensionOn > 0.5) {                                // 12 只有淡出环,没有边缘色与剪除
    float dist = length(vWorldPos.xz - siteExtensionCenter.xz);
    float tt = clamp((dist - siteExtensionInnerRadius)
                     / (siteExtensionRadius - siteExtensionInnerRadius), 0.0, 1.0);
    if (tt <= 0.999000013 && dist <= siteExtensionFadeMaxRadius && dist >= siteExtensionFadeMinRadius) {
      C = mix(C, siteExtensionFadeColor.rgb, tt * siteExtensionFadeColor.a);
    }
  }
#ifdef SITE_HEIGHT_FADE
  if (mUseHeightFade > 0.5) {                                  // 13 高度淡出:融进天空底色
    float t = min(pow(clamp(vWorldPos.y * mHeightFadeRcpLength - mHeightFadeStartTimeRcpLength,
                            0.0, 1.0), mHeightFadeExponent), 1.0);
    C = mix(envSkyBottomColor.rgb, C, t);
  }
#endif
  SV_Target0 = vec4(C, outA);
  SV_Target1 = vec4(0.0);                                      // 14 地面不发光
}
`,
};

// ---- 5. Mysekai/DropItem(全局参数自成一套)---------------------------------
SITE_PROGRAMS.dropItem = {
  shader: 'Mysekai/DropItem',
  vert: /* glsl */`
${SITE_VERT_COMMON}
uniform float mUvSelection;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz + siteWorldOrigin;
  // **片元里不重新归一化**,直接用插值出来的法线 —— 照原样,所以这里归一化一次即可。
  vWorldNormal = siteWorldNormal(normal);
  vec2 sel = uv;
#ifdef USE_UV1
  if (mUvSelection > 0.5) sel = uv1;
#endif
  vUv0 = sel;
  gl_Position = projectionMatrix * viewMatrix * wp;
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform sampler2D mMainTex;
uniform vec2 mMainTexUVScroll;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
void main() {
  vec2 uvS = fract(vUv0 - siteTime * mMainTexUVScroll);      // 有 fract
  vec4 B = texture2D(mMainTex, uvS);
#ifdef SITE_ALPHA_CLIP
  if (B.a < 0.5) discard;                                    // 阈值硬编码 0.5
#endif
  // 掉落物**不用** _GlobalEdge*,自己一套全局参数;没有雾、阴影、抖动、宝箱、边界、菲涅耳、顶点色。
  float h = dot(envLightDir, vWorldNormal) * 0.5 + 0.5;
  float t = siteToonT(h, siteDropItemShadingEdgeThreshold, siteDropItemShadingEdgeSmoothness);
  vec3 C = mix(B.rgb, B.rgb * envPhenLightColor.rgb,
               envPhenLightColor.a * siteDropItemPhenomenaLightIntensity);
  C = mix(C, C * envPhenShadeColor.rgb, t * siteDropItemNormalShadingIntensity);
  SV_Target0 = vec4(C, B.a);
  SV_Target1 = vec4(0.0);
}
`,
};

// ---- 6. Mysekai/Fixture/Basic(第二个往第二目标写非零的)----------------------
SITE_PROGRAMS.fixtureBasic = {
  shader: 'Mysekai/Fixture/Basic',
  vert: /* glsl */`
${SITE_VERT_COMMON}
uniform vec2 mMainTexOffset;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vScreen;
varying float vFogRamp;
varying vec3 vViewDir;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz + siteWorldOrigin;
  vWorldNormal = siteWorldNormal(normal);
  vUv0 = uv + mMainTexOffset;              // **只有偏移,没有缩放**
  vViewDir = normalize(cameraPosition + siteWorldOrigin - vWorldPos);
  vec4 clip = projectionMatrix * viewMatrix * wp;
  vFogRamp = siteFogRamp(clip);
  vScreen = siteScreenPos(clip);
  gl_Position = clip;
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform sampler2D mMainTex;
uniform sampler2D mEmissionMaskTex;
uniform samplerCube mReflectionCubeMap;
uniform float mDitherAlpha;
uniform float mUsePhenomenaLighting;
uniform float mOverrideShading;
uniform float mLocalEdgeThreshold;
uniform float mLocalEdgeSmoothness;
uniform float mLocalShadingIntensity;
uniform float mFresnelPower;
uniform vec4 mFresnelColor;
uniform float mReflectionFresnelPower;
uniform float mReflectionIntensity;
uniform float mBrightEmission;
uniform float mDarkEmission;
uniform float mEmissionOverrideMode;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vScreen;
varying float vFogRamp;
varying vec3 vViewDir;
void main() {
  vec4 B = texture2D(mMainTex, vUv0);                        // 1
  vec3 E = texture2D(mEmissionMaskTex, vUv0).rgb;
#ifdef SITE_ALPHA_CLIP
  if (B.a < 0.5) discard;                                    // 2 阈值硬编码 0.5
#endif
#ifndef SITE_DISABLE_DITHER
  if (siteDitherDiscard(vScreen, 0.125, mDitherAlpha)) discard;   // 3 屏幕缩放 0.125,同 Tree
#endif
  vec3 N = normalize(vWorldNormal);
  vec3 V = normalize(vViewDir);
  float ov = step(0.5, mOverrideShading);                     // 4 光照
  float thr = mix(siteEdgeThreshold, mLocalEdgeThreshold, ov);
  float smo = mix(siteEdgeSmoothness, mLocalEdgeSmoothness, ov);
  float inten = mix(1.0, mLocalShadingIntensity, ov);
  vec3 L = sitePhenomenaLight(B.rgb, envPhenLightColor);
  float h = dot(envLightDir, N) * 0.5 + 0.5;
  vec3 C = mix(L, L * envPhenShadeColor.rgb, inten * siteToonT(h, thr, smo));
  C = (mUsePhenomenaLighting > 0.5) ? C : B.rgb;              //   整条光照可被材质关掉
  C = siteTreasureShadow(C, vWorldPos);                       // 5 宝箱阴影 ×2
  //   家具**永远不接收主光投影阴影**:这一族里它一份阴影采样器都没有。
#ifdef SITE_MODULE_FRESNEL
  C = siteFresnel(C, N, V, mFresnelPower, mFresnelColor);     // 6
#endif
#ifdef SITE_MODULE_REFLECTION
  {
    // 怪处 G:算的是 V - 2*dot(N,V)*N,**是镜像的相反数**;
    // 怪处 H:反射菲涅耳用的是**未 clamp** 的 1 - dot(N,V),照原样。
    float ndv = dot(N, V);
    vec3 R = V - 2.0 * ndv * N;
    float rf = min(pow(1.0 - ndv, mReflectionFresnelPower), 1.0);
    C = C + textureCube(mReflectionCubeMap, R).rgb * rf * mReflectionIntensity;
  }
#endif
  C = envApplyFogRamp(C, vFogRamp, vWorldPos.y);              // 7 雾,随后 clamp
  SV_Target0 = vec4(clamp(C, 0.0, 1.0), B.a);                 //   没有 _BaseOpacity
  // 8 发光 → 第二目标。这一支多一个覆盖档,而且**不 clamp**、alpha 写的是 B.a(不是 0)。
  float gate = siteEmissionGate(mBrightEmission, mDarkEmission, 0.0, 0.0, mEmissionOverrideMode);
  SV_Target1 = vec4(E * gate, B.a);
}
`,
};

// ---- 7. Mysekai/Room/Floor(全族唯一顶点色不平方的)--------------------------
SITE_PROGRAMS.roomFloor = {
  shader: 'Mysekai/Room/Floor',
  vert: /* glsl */`
${SITE_VERT_COMMON}
uniform float mUvSelection;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vVC;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz + siteWorldOrigin;
  vWorldNormal = siteWorldNormal(normal);
  vec2 sel = uv;
#ifdef USE_UV1
  if (mUvSelection > 0.5 && mUvSelection < 1.5) sel = uv1;
#endif
#ifdef USE_UV2
  if (mUvSelection > 1.5) sel = uv2;
#endif
  vUv0 = sel;
  vVC = SITE_VC;
  gl_Position = projectionMatrix * viewMatrix * wp;
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform sampler2D mMainTex;
uniform float mUsePhenomenaLighting;
uniform float mUseFresnel;
uniform float mFresnelPower;
uniform vec4 mFresnelColor;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vVC;
void main() {
  vec4 B = texture2D(mMainTex, vUv0);
  // **全族唯一一个顶点色不平方的**:只乘一次,而且没有 _UseVertexColorBlend 开关。
  vec3 C = B.rgb * vVC.rgb;
  vec3 N = normalize(vWorldNormal);
  if (mUseFresnel > 0.5) {
    C = siteFresnel(C, N, siteViewDir(vWorldPos), mFresnelPower, mFresnelColor);
  }
  if (mUsePhenomenaLighting > 0.5) {
    // 没有 toon 分段、不读暗部色与光向:**暗部完全由投影阴影决定**。也没有雾。
    float atten = siteAtten(vWorldPos, N);
    C = sitePhenomenaLight(C, envPhenLightColor);
    C = siteDropShadow(C, atten);
  }
  SV_Target0 = vec4(C, B.a);
  SV_Target1 = vec4(0.0);
}
`,
};

// ---- 8. Mysekai/Fixture/ShadowMesh(把顶点色原样画出来)----------------------
SITE_PROGRAMS.shadowMesh = {
  shader: 'Mysekai/Fixture/ShadowMesh',
  vert: /* glsl */`
${SITE_VERT_COMMON}
varying vec4 vVC;
void main() {
  vVC = SITE_VC;
  gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform float mShow;
varying vec4 vVC;
void main() {
  if (mShow < 0.5) discard;
  // 顶点色直接就是输出(RGB 与 alpha 都是):**没有平方、没有贴图、没有光照**。
  // 家具脚下那块接地阴影不是实时算的,是美术给的一片带顶点色的网格。
  SV_Target0 = vVC;
  SV_Target1 = vec4(0.0);
}
`,
};

// ---- 回落:八族之外的材质 ---------------------------------------------------
//
// 这一族之外的着色器(粒子那一族的 UberUnlit、引擎标准件 URP/Lit、以及本轮清单外的
// Water / Ground-Birthday / TreasureBox 等)**律不在这一轮的依据里**。这里给它们一份
// 明标的回落程序:基色贴图 × 现象方向光 × 半兰伯特分段 + 雾 —— 与站点族形状相近,
// 但**它是近似,不是律**,逐个材质计数进 `status().site.families.fallback`。
SITE_PROGRAMS.fallback = {
  shader: '(八族之外:近似)',
  vert: /* glsl */`
${SITE_VERT_COMMON}
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vVC;
varying float vFogRamp;
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz + siteWorldOrigin;
  vWorldNormal = siteWorldNormal(normal);
  vUv0 = uv;
  vVC = SITE_VC;
  vec4 clip = projectionMatrix * viewMatrix * wp;
  vFogRamp = siteFogRamp(clip);
  gl_Position = clip;
}
`,
  frag: /* glsl */`
${SITE_FRAG_COMMON}
uniform sampler2D mMainTex;
uniform vec4 mBaseColor;
uniform float mAlphaClip;
varying vec3 vWorldPos;
varying vec3 vWorldNormal;
varying vec2 vUv0;
varying vec4 vVC;
varying float vFogRamp;
void main() {
  vec4 B = texture2D(mMainTex, vUv0) * mBaseColor;
  if (mAlphaClip > 0.0 && B.a < mAlphaClip) discard;
  vec3 N = normalize(vWorldNormal);
  vec3 C = sitePhenomenaLight(B.rgb, envPhenLightColor);
  float h = dot(envLightDir, N) * 0.5 + 0.5;
  C = mix(C, C * envPhenShadeColor.rgb, siteToonT(h, siteEdgeThreshold, siteEdgeSmoothness));
  SV_Target0 = vec4(envApplyFogRamp(C, vFogRamp, vWorldPos.y), B.a);
  SV_Target1 = vec4(0.0);
}
`,
};

/** 着色器名 → 程序键。名字不在表里的走回落。 */
export const SITE_FAMILY_BY_SHADER = {};
for (const [key, prog] of Object.entries(SITE_PROGRAMS)) {
  if (key !== 'fallback') SITE_FAMILY_BY_SHADER[prog.shader] = key;
}

/** 八个程序 + 回落的全部源码,供全局量消费方普查用(名字出现即算声明)。 */
const SITE_ALL_SOURCE = Object.values(SITE_PROGRAMS)
  .map((p) => `${p.vert}\n${p.frag}`).join('\n');

/** 往第二渲染目标写非零值的两个 —— 其余六个恒写 0。 */
const SITE_EMISSIVE_FAMILIES = ['object', 'fixtureBasic'];

// ---- 材质:按记录建,不按 glTF 的预览近似建 --------------------------------

const _siteF = (rec, name, dflt = 0) => num(((rec || {}).floats || {})[name], dflt);
const _siteC = (rec, name, dflt = [0, 0, 0, 0]) => col4(((rec || {}).colors || {})[name], dflt);
const _siteST = (rec, name) => {
  const v = ((rec || {}).textureScaleOffset || {})[name];
  return Array.isArray(v) && v.length >= 4 ? v.map(Number) : [1, 1, 0, 0];
};
const _siteKw = (rec, name) => ((rec || {}).keywords || []).indexOf(name) >= 0;

const V4 = (a) => new THREE.Vector4(a[0], a[1], a[2], a[3]);

/**
 * 一份材质记录 → 该程序要的 uniforms。**只从记录取**,glTF 那份预览近似只在记录缺席时兜底。
 * 每个程序取自己那一份;取不到的槽用产物给的默认贴图语义(`_MainTex` 白、其余黑)。
 */
function siteUniformsFor(family, rec, ctx) {
  const white = ENV_WHITE_TEX;
  const black = ENV_BLACK_TEX;
  const tex = (slot, dflt) => ctx.texture(rec, slot) || dflt;
  const st = (slot) => V4(_siteST(rec, slot));
  const common = {
    mMainTex: { value: tex('_MainTex', ctx.gltfMap || white) },
    mAlphaClip: { value: _siteF(rec, '_AlphaClip', 0.5) },
    mDitherAlpha: { value: _siteF(rec, '_DitherAlpha', 1) },
    mBaseOpacity: { value: _siteF(rec, '_BaseOpacity', 1) },
    mUseVertexColorBlend: { value: _siteF(rec, '_UseVertexColorBlend', 0) },
    mUsePhenomenaLighting: { value: _siteF(rec, '_UsePhenomenaLighting', 1) },
    mOverrideShading: { value: _siteF(rec, '_OverrideShadingParameter', 0) },
    mLocalEdgeThreshold: { value: _siteF(rec, '_LocalEdgeThreshold', 0.65) },
    mLocalEdgeSmoothness: { value: _siteF(rec, '_LocalEdgeSmoothness', 0.04) },
    mLocalShadingIntensity: { value: _siteF(rec, '_LocalShadingIntensity', 1) },
    mUseFresnel: { value: _siteF(rec, '_UseFresnel', 0) },
    mFresnelPower: { value: _siteF(rec, '_FresnelPower', 5) },
    mFresnelColor: { value: V4(_siteC(rec, '_FresnelColor', [1, 1, 1, 0])) },
  };
  if (family === 'fieldObject') {
    return {
      ...common,
      mOverlayTex: { value: tex('_OverlayColorMap', black) },
      mOverlayST: { value: st('_OverlayColorMap') },
      mOverlayScroll: {
        value: new THREE.Vector2(_siteF(rec, '_UVScrollX_Overlay1st'), _siteF(rec, '_UVScrollY_Overlay1st')),
      },
      mOverlayUvSet: { value: _siteF(rec, '_TextureCoord_Overlay1st') },
      mUseOverlayVertexAlpha: { value: _siteF(rec, '_UseOverlayTextureVertexAlpha') },
    };
  }
  if (family === 'object') {
    return {
      ...common,
      mEmissionMaskTex: { value: tex('_EmissionMaskTex', black) },
      mBaseTextureMappingMode: { value: _siteF(rec, '_BaseTextureMappingMode') },
      mMainTextureLocalMapping: { value: _siteF(rec, '_MainTextureLocalMapping') },
      mObjectShaderUsage: { value: _siteF(rec, '_ObjectShaderUsage') },
      mRoadTextureScale: { value: _siteF(rec, '_RoadTextureScale', 1) },
      mUvScroll: { value: new THREE.Vector2(_siteF(rec, '_UVScrollX'), _siteF(rec, '_UVScrollY')) },
      mAlphaTilingOffset: { value: V4(_siteC(rec, '_AlphaTilingOffset', [1, 1, 0, 0])) },
      mUseVertexAlphaOpacity: { value: _siteF(rec, '_UseVertexAlphaOpacity') },
      mBackFaceColor: { value: V4(_siteC(rec, '_BackFaceColor', [0, 0, 0, 0])) },
      mCullOff: { value: _siteF(rec, '_Cull', 2) < 0.5 ? 1 : 0 },
      mUseObject3DPreviewLight: { value: _siteF(rec, '_UseObject3DPreviewLight') },
      mWallAOIntensity: { value: _siteF(rec, '_WallAOIntensity') },
      mWallAOExponent: { value: _siteF(rec, '_WallAOExponent', 1) },
      mWallAOScale: {
        value: new THREE.Vector2(_siteF(rec, '_WallAOScaleX', 1), _siteF(rec, '_WallAOScaleY', 1)),
      },
      mAdditiveColor: { value: V4(_siteC(rec, '_AdditiveColor', [0, 0, 0, 0])) },
      mBrightEmission: { value: _siteF(rec, '_BrightPhenomenaEmission') },
      mDarkEmission: { value: _siteF(rec, '_DarkPhenomenaEmission') },
      mManualEmission: { value: _siteF(rec, '_EnableManualEmission') },
      mDebugEmission: { value: _siteF(rec, '_DebugEmission') },
      mUseHeightFade: { value: _siteF(rec, '_UseHeightFade') },
      mHeightFadePosition: { value: _siteF(rec, '_HeightFadePosition') },
      mHeightFadeLength: { value: _siteF(rec, '_HeightFadeLength', 1) },
      mHeightFadeExponent: { value: _siteF(rec, '_HeightFadeExponent', 1) },
      mSiteExtensionOn: { value: 0 },
      mHasUv3: { value: 0 },
    };
  }
  if (family === 'tree') {
    return {
      ...common,
      mLeafMaskTex: { value: tex('_LeafMaskTex', black) },
      mTurbulence: { value: _siteF(rec, '_turbulenceValue', 2) },
      mStrength: { value: _siteF(rec, '_strengthValue', 13) },
      mLeafRotationSpeed: { value: _siteF(rec, '_LeafRotationSpeed', 3) },
      mLeafRotationRange: { value: _siteF(rec, '_LeafRotationRange', 0.83) },
      mHeightFadeRcpLength: { value: _siteF(rec, '_HeightFadeRcpLength', 1) },
      mHeightFadeStartTimeRcpLength: { value: _siteF(rec, '_HeightFadeStartTimeRcpLength') },
      mHeightFadeExponent: { value: _siteF(rec, '_HeightFadeExponent', 1) },
      mHeightGradientColor0: { value: V4(_siteC(rec, '_HeightGradientColor0', [1, 1, 1, 1])) },
      mHeightGradientColor1: { value: V4(_siteC(rec, '_HeightGradientColor1', [1, 1, 1, 1])) },
      mHeightGradientColor2: { value: V4(_siteC(rec, '_HeightGradientColor2', [1, 1, 1, 1])) },
      mHeightGradientPos01: { value: _siteF(rec, '_HeightGradientPos01', 0.3) },
      mHeightGradientPos12: { value: _siteF(rec, '_HeightGradientPos12', 0.7) },
      mTreeRarityType: { value: _siteF(rec, '_TreeRarityType') },
      mRareOverlayTexture: { value: tex('_RareOverlayTexture', black) },
      mRareST: { value: st('_RareOverlayTexture') },
      mRareScroll: { value: new THREE.Vector2(_siteF(rec, '_RareScrollX'), _siteF(rec, '_RareScrollY')) },
      mRareBaseColor: { value: V4(_siteC(rec, '_RareBaseColor', [1, 1, 1, 0])) },
      mTreeRareCenter: { value: new THREE.Vector3(0, 0, 0) },
      mRareSphereNormalBlend: { value: _siteF(rec, '_RareSphereNormalBlend') },
      mRareFresnelPower: { value: _siteF(rec, '_RareFresnelPower', 1) },
      mRareFresnelEdge: { value: _siteF(rec, '_RareFresnelEdge') },
      mRareFresnelEdge2: { value: _siteF(rec, '_RareFresnelEdge2', 1) },
      mRareFresnelIntensity: { value: _siteF(rec, '_RareFresnelIntensity', 1) },
      mFlowerRareIntensity: { value: _siteF(rec, '_FlowerRareIntensity', 1) },
    };
  }
  if (family === 'ground') {
    return {
      ...common,
      mUvScroll: { value: new THREE.Vector2(_siteF(rec, '_UVScrollX'), _siteF(rec, '_UVScrollY')) },
      mOverlayTex: { value: tex('_OverlayColorMap', black) },
      mOverlayST: { value: st('_OverlayColorMap') },
      mOverlayScroll: {
        value: new THREE.Vector2(_siteF(rec, '_UVScrollX_Overlay1st'), _siteF(rec, '_UVScrollY_Overlay1st')),
      },
      mOverlayUvSet: { value: _siteF(rec, '_TextureCoord_Overlay1st') },
      mUseOverlayVertexAlpha: { value: _siteF(rec, '_UseOverlayTextureVertexAlpha') },
      mOverlay2ndTex: { value: tex('_OverlayColorMap2nd', black) },
      mOverlay2ndST: { value: st('_OverlayColorMap2nd') },
      mOverlay2ndScroll: {
        value: new THREE.Vector2(_siteF(rec, '_UVScrollX_Overlay2nd'), _siteF(rec, '_UVScrollY_Overlay2nd')),
      },
      mOverlay2ndUvSet: { value: _siteF(rec, '_TextureCoord_Overlay2nd') },
      mUseOverlayVertexAlpha2nd: { value: _siteF(rec, '_UseOverlayTextureVertexAlpha2nd') },
      mUseVertexAlphaOpacity: { value: _siteF(rec, '_UseVertexAlphaOpacity') },
      mSkipPhenomenaLighting: { value: _siteF(rec, '_SkipPhenomenaLighting') },
      mSiteExtensionOn: { value: 0 },
      mUseHeightFade: { value: _siteF(rec, '_UseHeightFade') },
      mHeightFadeRcpLength: { value: _siteF(rec, '_HeightFadeRcpLength', 1) },
      mHeightFadeStartTimeRcpLength: { value: _siteF(rec, '_HeightFadeStartTimeRcpLength') },
      mHeightFadeExponent: { value: _siteF(rec, '_HeightFadeExponent', 1) },
    };
  }
  if (family === 'dropItem') {
    return {
      mMainTex: common.mMainTex,
      mUvSelection: { value: _siteF(rec, '_UVSelection') },
      mMainTexUVScroll: {
        value: new THREE.Vector2(_siteF(rec, '_MainTex_UVScrollX'), _siteF(rec, '_MainTex_UVScrollY')),
      },
    };
  }
  if (family === 'fixtureBasic') {
    return {
      ...common,
      mEmissionMaskTex: { value: tex('_EmissionMaskTex', black) },
      mReflectionCubeMap: { value: ctx.blackCube },
      mMainTexOffset: {
        value: new THREE.Vector2(_siteF(rec, '_MainTexOffsetX'), _siteF(rec, '_MainTexOffsetY')),
      },
      mReflectionFresnelPower: { value: _siteF(rec, '_ReflectionFresnelPower', 5) },
      mReflectionIntensity: { value: _siteF(rec, '_ReflectionIntensity') },
      mBrightEmission: { value: _siteF(rec, '_BrightPhenomenaEmission') },
      mDarkEmission: { value: _siteF(rec, '_DarkPhenomenaEmission') },
      mEmissionOverrideMode: { value: _siteF(rec, '_PhenomenaEmissionOverrideMode') },
    };
  }
  if (family === 'roomFloor') {
    return {
      mMainTex: common.mMainTex,
      mUvSelection: { value: _siteF(rec, '_UVSelection') },
      mUsePhenomenaLighting: common.mUsePhenomenaLighting,
      mUseFresnel: common.mUseFresnel,
      mFresnelPower: common.mFresnelPower,
      mFresnelColor: common.mFresnelColor,
    };
  }
  if (family === 'shadowMesh') {
    return { mShow: { value: _siteF(rec, '_Show', 1) } };
  }
  // 回落
  return {
    mMainTex: common.mMainTex,
    mBaseColor: { value: V4(ctx.gltfBaseColor || [1, 1, 1, 1]) },
    mAlphaClip: { value: num(ctx.gltfAlphaTest, 0) },
  };
}

/** 材质记录里**真的带着**的关键字 → 程序的 `#define`。管线下发的那几个不在这里。 */
function siteDefinesFor(family, rec) {
  const d = {};
  if (_siteKw(rec, '_USE_ALPHA_CLIP')) d.SITE_ALPHA_CLIP = '';
  if (_siteKw(rec, '_DISABLE_DITHER')) d.SITE_DISABLE_DITHER = '';
  if (_siteKw(rec, '_RECEIVE_SHADOWS_OFF')) d.SITE_RECEIVE_SHADOWS_OFF = '';
  if (_siteKw(rec, '_USE_OVERLAY_TEXTURE')) d.SITE_OVERLAY_1ST = '';
  if (_siteKw(rec, '_USE_OVERLAY_TEXTURE_2ND')) d.SITE_OVERLAY_2ND = '';
  if (_siteKw(rec, '_ENABLE_MODULE_FRESNEL')) d.SITE_MODULE_FRESNEL = '';
  if (_siteKw(rec, '_ENABLE_MODULE_REFLECTION')) d.SITE_MODULE_REFLECTION = '';
  if (_siteKw(rec, '_USE_TREE_ANIMATION')) d.SITE_TREE_ANIMATION = '';
  if (_siteKw(rec, '_USE_HEIGHT_FADE')) d.SITE_HEIGHT_FADE = '';
  if (_siteKw(rec, '_USE_RARE')) d.SITE_RARE = '';
  // 抖动:DropItem / Ground / Room/Floor / ShadowMesh 本来就没有这一步。
  if (family === 'dropItem' || family === 'ground' || family === 'roomFloor'
      || family === 'shadowMesh' || family === 'fallback') d.SITE_DISABLE_DITHER = '';
  return d;
}

/**
 * 站点几何 + 站点这一族的着色。
 *
 * 一、名单怎么建 —— **一行一个站点,不是一行一个场景包**
 *   placement 表(`index.json` 的 `placement.file` 指到那份)一行一个站点,带着它的
 *   `sitePosition` 与开放的等级。**一个场景包可以承载好几行**:室内那一个包承载
 *   1/2/3 楼三行,三行只差 `sitePosition.y`。产物自己写明「三行共用一个场景包,
 *   把偏移烘进几何会丢掉其中两行」。行与场景包按 `siteType` 名字接 —— 产物写明
 *   游戏 switch 的就是这个名字,id 与顺序的巧合**不可依赖**。
 *
 * 二、装什么
 *   `site/index.json` 里 `scenes.<键>.geometry` 是场景包的 glb(清单里所有路径都相对清单自己)。
 *   一个 glb 里一个 prefab 一个 glTF scene,**默认场景才是游戏摆出来的那个**,
 *   所以只取 glTF 自己的默认场景,不遍历全部 scene —— 遍历会把同一份网格摆好几遍。
 *   场景文档的 `inactiveNodes` / `disabledRenderers` 是**原版根本不画**的节点,照它隐藏:
 *   产物说列出来就是为了让消费方「not draw what the game never shows」。
 *   隐藏≠删除,它们照样在几何计数里。
 *
 * 三、室内/室外怎么判(不按名字猜)
 *   主判据 —— 场景文档里 `env` 槽自带的角色原话:「environment volume anchor; empty in
 *   every outdoor package and holding the room volume indoors」。槽里有节点或碰撞体 = 室内。
 *   清单级判据 —— 建面板下拉框时用,不必读逐场景文档(最大的一份 6 MB):
 *   `families.kit` 只有一个包,那就是室内套件;场景记录的 `declaredDependencies` 点名它的
 *   就是房间站点。依赖名是路径式样、包名是下划线式样,规范化到同一形状再比。
 *   不一致时**以主判据为准**,分歧记进 `status().site.indoorDisagree`,不静默取一条。
 *
 * 四、室内怎么拼
 *   房间站点的场景包里**没有墙也没有地板**。`indoor.assembly` 一句话给了完整编排:
 *   「a room is the kit's meshes placed by one expansion module per level plus that level's
 *   walkable surface; the site package of a room site holds no wall or floor geometry at all」。
 *   照这句话拼,三件:
 *
 *     场景包    —— 照常取它的默认场景。房间站点的默认场景里只有那一件「房间体积」
 *                  (`env` 槽里的天穹网格),墙与地一片都没有。
 *     扩张模块  —— `indoor.levels.<级>.module`,按 `module.prefabs[].root` **点名**取那几个
 *                  glTF scene(不是把 glb 里的 scene 全挂上)。模块包自己**一个网格资产都没有**,
 *                  它的绘制件指向套件包里的网格;产物把这层跨包引用解开后把几何写进了模块的
 *                  glb,所以取模块的 glb 就是完整的一份 —— 套件包名一并记进读数。
 *     可走面    —— `indoor.levels.<级>.walkable`,一级一份,文件相对可走面包自己的目录。
 *                  它是碰撞面,**原版不画**,所以这里挂上但不画,读数里单列 `drawn: false`。
 *                  有几级的可走面在盘上就没有几何,记录自己写明那是作者状态而不是查找失败 ——
 *                  照抄那句话,不当成错误。
 *
 *   房间是第几级由服务端决定。placement 表给了每一站**开放到第几级**(2 楼与 3 楼只开放
 *   5 级),所以等级名单 = 产物里的扩张模块等级 ∩ 这一站开放的等级;当前是哪一级
 *   按「服务端决定的做成面板下发」处置,默认取本站可用的最高一级。
 *
 * 五、站点的世界位置
 *   产物写明 `sitePosition` 由消费方施加、**从不烘进几何**。本示例把它施加在**着色读的
 *   世界坐标**上(`siteWorldOrigin`),几何仍留在包自己的原点。这个数唯一起作用的地方就是
 *   着色读的绝对世界坐标(雾的高度衰减、高度淡出、宝箱与站点边界的水平距离),
 *   施加在那里 = 完整地施加了。一次只看一个站点:把几何平移过去再把相机平移同样的量,
 *   画面一模一样;但本示例把站点与一个摆在场景原点的角色合成在一起,而那个角色同时是
 *   站点着色器读的落影投射体 —— 平移几何会把这两件拆开。所以落影比较仍在**站点局部
 *   坐标**里做,与「把几何和相机一起平移」逐像素等价。
 *装载的 glTF 材质在这里按**材质记录**换成对应的程序;
 * 透明度照原样带过来:混合模式来自材质 extras 的 `blendFactors`(Unity 枚举),
 * 剔面来自记录的 `_Cull`,遮罩阈值由记录的关键字与属性决定(不是 glTF 的 alphaCutoff)。
 * 贴图按本示例的 gamma 直通规矩置 `NoColorSpace`(与角色贴图同一条),不走 sRGB 解码。
 */
class SiteView {
  /** @param opts `{base}` —— `base` 是 `site/` 的父目录 */
  constructor(opts) {
    this.base = String(opts.base || '.').replace(/\/+$/, '');
    this.root = `${this.base}/site`;
    this.index = null;
    this.placement = null;        // placement 表(`sites.json`):站点世界位置与逐站等级
    this.loader = new GLTFLoader();
    this.texLoader = new THREE.TextureLoader();
    this.texCache = new Map();
    this.texFailed = new Map();
    this.group = new THREE.Group();
    this.group.name = 'env_site';
    this.key = null;              // 当前挂着的**站点键**(不是场景键:三层室内共用一个场景包)
    this.level = null;            // 面板下发的室内等级;null = 取本站可用的最高一级
    this.mounted = null;
    this.mounting = null;
    this.materials = [];
    this.errors = [];
    this.loadNotes = [];          // 装载期的那几条(比如 placement 表读不到),跨挂载留着
    this.notes = [];              // 挂载期的,每次重来
    this.families = {};           // 逐族材质数
    this.fallbackShaders = {};    // 回落材质:按着色器名计数
    this.vertexColorRescaled = 0; // 顶点色按 0..255 还原过的网格数
    this.emissiveMaterials = 0;   // 真的会往第二目标写非零的材质数
    this.ambiguousMaterials = 0;  // 同名多条且贴图也分不开的材质数
    this.emission = 'buffer';     // off | buffer | view
    this.emissionRT = null;
    this.emissionFrames = 0;
    this.blendCounts = {};
    // 立方体反射的默认图:产物里这一槽全是空的,给一张全黑的六面图(零贡献)。
    this.blackCube = new THREE.CubeTexture([...Array(6)].map(() => ENV_BLACK_TEX.image));
    this.blackCube.colorSpace = THREE.NoColorSpace;
    this.blackCube.needsUpdate = true;
  }

  async load(fetchJson) {
    const idx = await fetchJson(`${this.root}/index.json`);
    if (!idx || !idx.scenes) { this.errors.push('site/index.json 读不到或没有 scenes 段'); return false; }
    this.index = idx;
    // placement 表:站点世界位置(`sitePosition`)与逐站等级都在它里面。读不到不当致命 ——
    // 没有它就只有场景键这一层,三层室内会塌成一层,那件事要说出来而不是静默。
    const file = ((idx.placement || {}).file) || 'sites.json';
    this.placement = await fetchJson(`${this.root}/${file}`);
    if (!this.placement || !Array.isArray(this.placement.sites) || !this.placement.sites.length) {
      this.placement = null;
      this.loadNotes.push('placement 表读不到:站点世界位置与逐站等级取默认值,室内三层塌成一层');
    }
    return true;
  }

  /** 室内套件的包名(`families.kit`)。产物里只有一个,没有就是判据缺了输入。 */
  get kitPackage() {
    const kit = ((this.index || {}).families || {}).kit || [];
    return kit.length ? kit[0].package : null;
  }

  /**
   * 面板下拉框的名单 —— **一行一个站点,不是一行一个场景包**。
   *
   * 这一条是这一轮补上的:placement 表里 `first_floor` 这一个场景包**承载三个站点**
   * (1 楼 / 2 楼 / 3 楼),三行只差 `sitePosition.y`(0 / 500 / 1000)。产物自己写明:
   * 「三行共用一个场景包,只差 `sitePosition.y`,把偏移烘进几何会丢掉其中两行」。
   * 所以名单按 placement 的行来建,行与场景包按 `siteType` 名字接(产物写明游戏就是
   * switch 这个名字,id 与顺序的巧合**不可依赖**)。
   *
   * 键的取法:一个场景包只有一行时用**场景键**(与既有面板、既有判据的取值一致),
   * 一个场景包多行时用各自的 `siteType`。placement 里没有行的场景(当前内容里有一个)
   * 照样列出来,标明它没有 placement 行。
   */
  siteRows() {
    const scenes = (this.index || {}).scenes || {};
    const rows = ((this.placement || {}).sites) || [];
    const perScene = {};
    for (const r of rows) {
      const s = r.scene || r.assetbundleName;
      if (!s) continue;
      (perScene[s] = perScene[s] || []).push(r);
    }
    const out = [];
    for (const sceneKey of Object.keys(scenes).sort()) {
      const list = perScene[sceneKey] || [];
      if (!list.length) {
        out.push({
          key: sceneKey, scene: sceneKey, siteType: null, name: null,
          position: [0, 0, 0], levels: null, placement: false,
          why: 'placement 表里没有这一场景的行:世界位置取原点、等级取产物里最高的一级',
        });
        continue;
      }
      for (const r of list) {
        const p = r.sitePosition || {};
        out.push({
          key: list.length > 1 ? String(r.siteType) : sceneKey,
          scene: sceneKey,
          siteType: r.siteType || null,
          name: r.name || null,
          category: r.category || null,
          position: [num(p.x), num(p.y), num(p.z)],
          levels: (r.levels || []).map((l) => num(l.level)).filter((x) => x > 0),
          placement: true,
          why: list.length > 1
            ? `一个场景包承载 ${list.length} 个站点,按 siteType 分行(只差 sitePosition)`
            : 'placement 表里这一场景只有一行,键沿用场景键',
        });
      }
    }
    return out;
  }

  rowFor(key) { return this.siteRows().find((r) => r.key === key) || null; }

  /** 站点键 → 场景键。取不到就当它自己是场景键(面板与判据的旧取值照样能用)。 */
  sceneKeyOf(key) {
    const row = this.rowFor(key);
    return row ? row.scene : key;
  }

  /** 站点的世界位置。产物写明它由消费方施加、从不烘进几何。 */
  worldOriginOf(key) {
    const row = this.rowFor(key);
    return row ? row.position : [0, 0, 0];
  }

  /**
   * 清单级的室内判据:场景的 `declaredDependencies` 点名了室内套件包。
   * 返回 `{indoor, by}`;套件包读不到时 `by` 说明输入缺失,而不是默认判成室外。
   */
  indoorFromIndex(key) {
    const sceneKey = this.sceneKeyOf(key);
    const rec = ((this.index || {}).scenes || {})[sceneKey];
    const kit = this.kitPackage;
    if (!rec || !kit) return { indoor: false, by: '室内套件包或场景记录读不到:判据无输入' };
    const want = pkgKey(kit);
    const hit = (rec.declaredDependencies || []).some((d) => pkgKey(d) === want);
    // 佐证:产物说带 `_footse` 的碰撞面属于每一个室外站点,
    // 所以没有 footstepSurface 这一角色的场景不是室外站点。
    const noFootstep = !(rec.collision || []).some((c) => c && c.role === 'footstepSurface');
    return {
      indoor: hit,
      footstepAgrees: noFootstep === hit,
      by: hit ? `declaredDependencies 点名室内套件 ${kit}` : '未点名室内套件',
    };
  }

  /** 产物里有哪几级室内扩张模块,以及每一级带不带可走面几何。 */
  levels(key) {
    const all = ((this.index || {}).indoor || {}).levels || {};
    const row = key ? this.rowFor(key) : null;
    // placement 行给了这一站开放到第几级(2 楼与 3 楼只出 5 级),按它过滤产物里的名单。
    // 只对**室内**站点过滤:室外站点的等级行是站点自己的扩张阶段,
    // 与室内套件的扩张模块等级不是同一件事。
    const indoorHere = row ? this.indoorFromIndex(row.key).indoor : false;
    const allowed = indoorHere && row && row.levels && row.levels.length
      ? new Set(row.levels.map((n) => String(n).padStart(2, '0'))) : null;
    return Object.keys(all).sort().filter((k) => !allowed || allowed.has(k)).map((k) => {
      const lv = all[k] || {};
      const surfaces = (lv.walkable || {}).surfaces || [];
      return {
        key: k,
        module: (lv.module || {}).package || null,
        prefabs: ((lv.module || {}).prefabs || []).map((x) => x.root).filter(Boolean),
        colliders: ((lv.module || {}).colliders || []).length,
        walkablePackage: (lv.walkable || {}).package || null,
        walkableFiles: surfaces.filter((x) => x && x.file).length,
        walkableNotes: surfaces.filter((x) => x && !x.file).map((x) => x.reason || '记录里没有几何'),
        allowedBy: allowed ? 'placement 表里这一站的等级行' : '产物里的全部等级(该站无 placement 行)',
      };
    });
  }

  /** 本站可用的最高一级。 */
  topLevelOf(key) {
    const list = this.levels(key);
    return list.length ? list[list.length - 1].key : null;
  }

  /** 旧名:面板与既有判据按它读「产物里最高的一级」。 */
  get topLevel() {
    return Object.keys(((this.index || {}).indoor || {}).levels || {}).sort().pop() || null;
  }

  get activeLevel() {
    const list = this.levels(this.key || undefined).map((x) => x.key);
    return (this.level && list.includes(this.level)) ? this.level : (list.length ? list[list.length - 1] : null);
  }

  setLevel(level) {
    const list = this.levels(this.key || undefined).map((x) => x.key);
    this.level = (level && list.includes(String(level))) ? String(level) : null;
    return this.activeLevel;
  }

  /** 面板下拉框的名单:站点键、室内与否、世界位置、可选等级,全部从产物读出来。 */
  scenes() {
    return this.siteRows().map((row) => {
      const f = this.indoorFromIndex(row.key);
      const rec = ((this.index || {}).scenes || {})[row.scene] || {};
      return {
        key: row.key, scene: row.scene, siteType: row.siteType, name: row.name,
        indoor: f.indoor, indoorBy: f.by, footstepAgrees: f.footstepAgrees,
        position: row.position, levels: row.levels, placement: row.placement, why: row.why,
        package: rec.package, declaredTriangles: rec.triangles,
      };
    });
  }

  // ---- 贴图 ---------------------------------------------------------------

  /** 材质记录里的贴图路径相对该记录所在的文档目录。gamma 直通,不做 sRGB 解码。 */
  _textureFor(rec, slot) {
    const rel = ((rec || {}).textures || {})[slot];
    if (!rel) return null;
    const dir = (rec && rec.__dir) || '';
    const path = (dir && !String(rel).startsWith(`${dir}/`)) ? `${dir}/${rel}` : String(rel);
    if (this.texCache.has(path)) return this.texCache.get(path);
    const url = `${this.root}/${path}`;
    const tex = this.texLoader.load(url, undefined, undefined, () => {
      this.texFailed.set(path, '贴图读不到');
    });
    tex.colorSpace = THREE.NoColorSpace;
    tex.wrapS = THREE.RepeatWrapping;
    tex.wrapT = THREE.RepeatWrapping;
    this.texCache.set(path, tex);
    return tex;
  }

  // ---- 装载编排 -----------------------------------------------------------

  _plan(key, indoor) {
    const idx = this.index || {};
    const sceneKey = this.sceneKeyOf(key);
    const rec = (idx.scenes || {})[sceneKey];
    const out = [];
    if (!rec) return out;
    if (rec.geometry) {
      out.push({
        part: 'scene', file: rec.geometry, scenes: null, drawn: true,
        package: rec.package || null, from: `scenes.${sceneKey}.geometry`,
        document: rec.document || null,
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
        document: modFam.document || null,
        why: `扩张模块 lv_${lv} · 网格出自套件 ${this.kitPackage || '(套件包读不到)'}`,
      });
    } else {
      this.errors.push(`${key}: 室内扩张模块的几何取不到(级 ${lv},包 ${mod.package || '?'})`);
    }

    // 二、这一级的可走面 —— 碰撞面,挂上不画。
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
      const rel = String(surface.file).startsWith(`${walkFam.directory}/`)
        ? String(surface.file) : `${walkFam.directory}/${surface.file}`;
      out.push({
        part: 'walkable', file: rel, scenes: null, drawn: false, level: lv,
        package: walk.package || null, node: surface.node || null,
        from: `indoor.levels.${lv}.walkable`,
        document: walkFam.document || null,
        why: `可走面 ${surface.mesh || surface.node || '(未命名)'}(碰撞面,原版不画)`,
      });
    }
    return out;
  }

  /** 场景文档(逐场景一份,最大的一份 6 MB,所以只在挂载当前场景时读)。 */
  async _document(key, fetchJson) {
    const sceneKey = this.sceneKeyOf(key);
    const rec = ((this.index || {}).scenes || {})[sceneKey];
    if (!rec || !rec.document) return null;
    return fetchJson(`${this.root}/${rec.document}`);
  }

  /**
   * 一份文档里的材质记录。**材质名在这些包里不唯一** —— 庆典庭院那一包里
   * 四份材质都叫 `mat_base`,各自的着色器与贴图全不一样。所以这里按名字存**一串**,
   * 同名多条时再拿基色贴图的文件名分开(glTF 里那张图的路径与记录里 `_MainTex`
   * 的路径是同一个)。两步都分不开的记一笔歧义并取第一条 —— 不静默。
   */
  _recordsFrom(doc, path) {
    const out = new Map();
    const dir = String(path || '').split('/').slice(0, -1).join('/');
    for (const m of ((doc || {}).materials) || []) {
      if (!m || !m.name) continue;
      const rec = { ...m, __dir: dir };
      if (!out.has(m.name)) out.set(m.name, [rec]);
      else out.get(m.name).push(rec);
    }
    return out;
  }

  /** 一份 glTF 材质 → 它的记录。同名多条时拿基色贴图的文件名分。 */
  _recordFor(records, src) {
    if (!records || !src || !src.name) return null;
    const list = records.get(src.name);
    if (!list || !list.length) return null;
    if (list.length === 1) return list[0];
    const img = src.map && src.map.image ? String(src.map.image.src || '') : '';
    const leaf = img.split('/').pop().split('?')[0];
    if (leaf) {
      const hit = list.filter((r) => {
        const t = ((r.textures || {})._MainTex) || '';
        return t && String(t).split('/').pop() === leaf;
      });
      if (hit.length === 1) return hit[0];
    }
    this._note(`材质名 ${src.name} 在文档里有 ${list.length} 条记录且基色贴图分不开,取第一条并计数`);
    this.ambiguousMaterials += 1;
    return list[0];
  }

  async mount(key, fetchJson) {
    if (this.key === key && this.mounted) return this.mounted;
    this.mounting = this._mount(key, fetchJson);
    try { return await this.mounting; } finally { this.mounting = null; }
  }

  async _mount(key, fetchJson) {
    this.unmount();
    // 键在**开始装**的那一刻就占上。这样一次没装出东西的挂载读起来是
    // 「试过这一站、什么都没挂上」,而不是永远的「还在装」。
    this.key = key;
    const sceneKey = this.sceneKeyOf(key);
    const rec = ((this.index || {}).scenes || {})[sceneKey];
    if (!rec) { this.errors.push(`site/index.json 里没有场景 ${sceneKey}`); return null; }
    // 这一站的世界位置先落到全局量上:站点这一族读的是**绝对世界坐标**(雾的高度衰减、
    // 高度淡出、宝箱与站点边界的水平距离都读它),三层室内正是靠这一个数分开的。
    const origin = this.worldOriginOf(key);
    SITE_GLOBALS.siteWorldOrigin.value.set(origin[0], origin[1], origin[2]);

    const fromIndex = this.indoorFromIndex(key);
    const doc = await this._document(key, fetchJson);
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
    let matchedMaterials = 0, unmatchedMaterials = 0;
    for (const step of plan) {
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
      // 这一件的材质记录:场景包用刚读到的那份文档,模块/可走面各读自己的一份。
      let records = new Map();
      if (step.part === 'scene' && doc) records = this._recordsFrom(doc, rec.document);
      else if (step.document) {
        const d = await fetchJson(`${this.root}/${step.document}`);
        records = this._recordsFrom(d, step.document);
      }
      const picked = step.scenes
        ? step.scenes.map((n) => (gltf.scenes || []).find((s) => s.name === n)).filter(Boolean)
        : [gltf.scene].filter(Boolean);
      if (step.scenes && picked.length !== step.scenes.length) {
        this.errors.push(`${step.file}: 点名的 ${step.scenes.length} 个 scene 只取到 ${picked.length} 个`);
      }
      let m = 0, t = 0, h = 0, ht = 0;
      for (const root of picked) {
        if (step.part === 'scene' && !step.scenes) {
          unresolvedHides += this._hide(root, inactive, disabled);
        }
        if (step.drawn === false) root.visible = false;
        const c = this._convert(root, records);
        m += c.meshes; t += c.triangles; h += c.hidden; ht += c.hiddenTriangles;
        matchedMaterials += c.matched; unmatchedMaterials += c.unmatched;
        this.group.add(root);
      }
      meshes += m; triangles += t; hiddenMeshes += h; hiddenTriangles += ht;
      drawnTriangles += t - ht;
      files.push({
        part: step.part, from: step.from, package: step.package, kit: step.kit || null,
        level: step.level || null, node: step.node || null,
        file: step.file, why: step.why, ok: true, drawn: step.drawn !== false,
        scenes: picked.map((x) => x.name), meshes: m, triangles: t, drawnTriangles: t - ht,
        materialRecords: records.size,
      });
    }

    const row = this.rowFor(key);
    this.mounted = {
      key,
      scene: sceneKey,
      siteType: row ? row.siteType : null,
      siteName: row ? row.name : null,
      // 站点世界位置。产物写明由消费方施加、从不烘进几何 —— 本示例把它施加在**着色的世界
      // 坐标**上(`siteWorldOrigin`),几何仍留在包自己的原点。理由见下面 `worldOriginNote`。
      worldOrigin: origin,
      worldOriginApplied: 'shading',
      worldOriginNote: row && row.placement
        ? '施加在着色读的世界坐标上:一次只看一个站点,把几何平移过去等于把相机也平移过去,'
          + '画面不变却会把站点与本示例摆在原点的角色(它同时是落影投射体)拆开;'
          + '这个数唯一起作用的地方是着色读的绝对世界坐标(雾的高度衰减、高度淡出、'
          + '宝箱与站点边界的水平距离),所以就施加在那里。三层室内正是靠它分开。'
        : 'placement 表里没有这一站的行,世界位置取原点',
      indoor,
      indoorBy: fromSlot === null
        ? `场景文档读不到,落到清单级判据(${fromIndex.by})`
        : `场景文档 env 槽${fromSlot ? '有' : '空'}内容 —— 槽角色原话:室外空、室内装房间体积`,
      indoorFromIndex: fromIndex.indoor,
      indoorDisagree: fromSlot !== null && fromSlot !== fromIndex.indoor,
      footstepAgrees: fromIndex.footstepAgrees,
      level: indoor ? this.activeLevel : null,
      levelSource: !indoor ? null
        : (this.level
          ? '面板下发(等级是 placement 表的字段,当前哪一级由服务端决定,所以做成旋钮)'
          : (row && row.levels && row.levels.length > 1
            ? '本站可用的最高一级(placement 表给了这一站的等级行)'
            : (row && row.levels && row.levels.length === 1
              ? `placement 表里这一站只开放 ${row.levels.join('/')} 级`
              : '产物里最高的一级(该站无 placement 行)'))),
      levelsAvailable: indoor ? this.levels(key).map((x) => x.key) : [],
      assembly: files.filter((f) => f.part !== 'scene'),
      files,
      meshes,
      triangles,
      drawnTriangles,
      hiddenMeshes,
      hiddenTriangles,
      unresolvedHides,
      declaredHides: inactive.length + disabled.length,
      materials: this.materials.length,
      // 逐族材质数:每一份材质走的是哪一个程序。回落的那些逐个着色器名计数。
      families: { ...this.families },
      fallbackShaders: { ...this.fallbackShaders },
      matchedMaterials,
      unmatchedMaterials,
      vertexColorRescaled: this.vertexColorRescaled,
      emissiveMaterials: this.emissiveMaterials,
      ambiguousMaterials: this.ambiguousMaterials,
      blend: this.blendCounts,
      declared: primary
        ? { root: primary.name, renderers: primary.renderers, vertices: primary.vertices, triangles: primary.triangles }
        : null,
      documentRead: !!doc,
      notes: this.notes.slice(),
    };
    return this.mounted;
  }

  /**
   * 原版不画的东西照文档关掉。两张表**语义不同**,分开处理:
   *   `inactiveNodes`     —— 节点自己关着,整条支路都不画;
   *   `disabledRenderers` —— 只有那个节点上的绘制件关着,子节点照画。
   * 空路径在文档里指不到任何节点:**不猜**,记一笔跳过。
   */
  _hide(root, inactive, disabled) {
    const base = root.children.length === 1 ? root.children[0] : root;
    const norm = (s) => THREE.PropertyBinding.sanitizeNodeName(String(s));
    const find = (path) => {
      let node = base;
      for (const seg of String(path).split('/')) {
        const want = norm(seg);
        const kids = node.children || [];
        let hit = kids.filter((c) => c.name === seg || c.name === want);
        if (!hit.length) {
          const re = new RegExp(`^${want.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}_\\d+$`);
          hit = kids.filter((c) => re.test(c.name));
        }
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

  /**
   * 顶点色的量纲。产物把 `COLOR_0` 写成了 **0..255 的浮点**(引擎侧那份是 32 位色的
   * 字节值),而着色器读的是 0..1。所以装载时按「最大值超过 1 就整体除 255」还原一次,
   * 还原过几件记进读数。这是**产物的写法**,不是着色器的律 —— 两者分开记。
   */
  _rescaleVertexColor(geometry) {
    const attr = geometry.getAttribute('color');
    if (!attr || geometry.userData.__siteVcScaled) return;
    geometry.userData.__siteVcScaled = true;
    const a = attr.array;
    let max = 0;
    for (let i = 0; i < a.length; i += 1) if (a[i] > max) max = a[i];
    if (max > 1.5) {
      for (let i = 0; i < a.length; i += 1) a[i] /= 255;
      attr.needsUpdate = true;
      this.vertexColorRescaled += 1;
    }
  }

  /** 逐网格换材质并计数。隐藏的网格照样计数 —— 它在场景里,只是不画。 */
  _convert(root, records) {
    let meshes = 0, triangles = 0, hidden = 0, hiddenTriangles = 0, matched = 0, unmatched = 0;
    const cache = new Map();
    root.updateMatrixWorld(true);
    root.traverse((o) => {
      if (!o.isMesh || !o.geometry) return;
      meshes += 1;
      const idx = o.geometry.getIndex();
      const pos = o.geometry.getAttribute('position');
      const tris = idx ? idx.count / 3 : (pos ? Math.floor(pos.count / 3) : 0);
      triangles += tris;
      let vis = true;
      for (let p = o; p; p = p.parent) if (!p.visible) { vis = false; break; }
      if (!vis) { hidden += 1; hiddenTriangles += tris; }
      if (!o.geometry.getAttribute('normal')) o.geometry.computeVertexNormals();
      this._rescaleVertexColor(o.geometry);
      const src = Array.isArray(o.material) ? o.material[0] : o.material;
      // 同一份材质用在属性不同的网格上会编译出不同的程序(three 按几何属性下 define),
      // 所以缓存键要把属性签名带上,不能只按源材质。
      const sig = `${o.geometry.getAttribute('uv3') ? 'u3' : ''}`;
      const ck = `${(src && src.uuid) || 'none'}|${sig}`;
      if (!cache.has(ck)) {
        const rec = this._recordFor(records, src);
        if (rec) matched += 1; else unmatched += 1;
        cache.set(ck, this._materialFor(src, rec, o.geometry));
      }
      o.material = cache.get(ck);
    });
    return { meshes, triangles, hidden, hiddenTriangles, matched, unmatched };
  }

  _materialFor(src, rec, geometry) {
    const shaderName = ((rec || {}).shader || {}).name || null;
    const family = SITE_FAMILY_BY_SHADER[shaderName] || 'fallback';
    const prog = SITE_PROGRAMS[family];
    this.families[family] = (this.families[family] || 0) + 1;
    if (family === 'fallback') {
      const k = shaderName || (rec ? '(记录里没有着色器名)' : '(没有材质记录)');
      this.fallbackShaders[k] = (this.fallbackShaders[k] || 0) + 1;
    }
    const map = src && src.map ? src.map : null;
    if (map) { map.colorSpace = THREE.NoColorSpace; map.updateMatrix(); }
    const c = src && src.color ? src.color : { r: 1, g: 1, b: 1 };
    const ud = (src && src.userData) || {};
    const bf = ud.blendFactors || null;
    const ctx = {
      texture: (r, slot) => this._textureFor(r, slot),
      gltfMap: map,
      gltfBaseColor: [c.r, c.g, c.b, src ? src.opacity : 1],
      gltfAlphaTest: src ? num(src.alphaTest) : 0,
      blackCube: this.blackCube,
    };
    const uniforms = siteUniformsFor(family, rec, ctx);
    if (family === 'object') {
      // 墙面 AO 读 uv3。产物导出的站点网格只带 uv0 与 uv1,取不到就**整块停用并计数**:
      // 拿缺失的输入硬算会画出一整片全暗的墙,那不是还原。
      uniforms.mHasUv3.value = geometry && geometry.getAttribute('uv3') ? 1 : 0;
      if (!uniforms.mHasUv3.value && Math.abs(num(_siteF(rec, '_ObjectShaderUsage')) - 2) < 0.5
          && _siteF(rec, '_WallAOIntensity') > 0) {
        this._note('墙面 AO 读 uv3,产物导出的网格只带 uv0/uv1:这一块停用并计数');
      }
    }
    const mat = new THREE.ShaderMaterial({
      name: `env-site:${family}:${(src && src.name) || 'unnamed'}`,
      uniforms: withEnvGlobals({ ...SITE_SUBJECT, ...SITE_GLOBALS, ...uniforms }),
      defines: siteDefinesFor(family, rec),
      vertexShader: prog.vert,
      fragmentShader: prog.frag,
      glslVersion: THREE.GLSL3,     // 两个渲染目标要 GLSL3 的具名输出
      vertexColors: true,           // 没有 COLOR_0 的网格由 three 喂 (1,1,1,1),这一格自动成恒等
      // 剔面照**记录**的 `_Cull` 走(0=双面 1=剔正面 2=剔背面),记录里没有才落回 glTF 那份预览近似。
      // 注意 `THREE.FrontSide` 就是 0,不能拿 `||` 当「取不到」判 —— 那会把最常见的那一档整批漏掉。
      side: (UNITY_CULL_SIDE[num(_siteF(rec, '_Cull', -1))] !== undefined)
        ? UNITY_CULL_SIDE[num(_siteF(rec, '_Cull', -1))]
        : (src ? src.side : THREE.FrontSide),
      transparent: !!(src && src.transparent),
      depthWrite: src ? src.depthWrite : true,
    });
    mat.userData.siteFamily = family;
    mat.userData.siteShader = shaderName;
    mat.userData.siteMaterial = (src && src.name) || null;
    // 真的会往第二目标写非零的:两个发光族 + 记录里那一槽真的有贴图。
    if (SITE_EMISSIVE_FAMILIES.indexOf(family) >= 0 && ((rec || {}).textures || {})._EmissionMaskTex) {
      this.emissiveMaterials += 1;
      mat.userData.siteEmissive = true;
    }
    if (bf && UNITY_BLEND_FACTOR[bf.src] !== undefined && UNITY_BLEND_FACTOR[bf.dst] !== undefined) {
      mat.blending = THREE.CustomBlending;
      mat.blendSrc = UNITY_BLEND_FACTOR[bf.src];
      mat.blendDst = UNITY_BLEND_FACTOR[bf.dst];
      mat.transparent = true;
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

  _note(text) { if (this.notes.indexOf(text) < 0) this.notes.push(text); }

  _countBlend(kind) {
    if (!this.blendCounts) this.blendCounts = {};
    this.blendCounts[kind] = (this.blendCounts[kind] || 0) + 1;
  }

  // ---- 第二渲染目标 -------------------------------------------------------
  //
  // 八个程序都写两个输出。画到只有一个附着点的目标上时第二路被丢掉,所以要真的拿到
  // 那一张图,得画进一个**两个附着点**的目标里 —— 就是下面这一趟。它只产出缓冲:
  // 辉光合成在后处理链那一侧,那条车道不在本轮的改动范围里,所以这里**不合成**,
  // 只把缓冲产出来、报出来、可以直显给人看。

  setEmission(mode) {
    this.emission = (mode === 'off' || mode === 'view') ? mode : 'buffer';
    return this.emission;
  }

  _ensureEmissionTarget(renderer) {
    const s = renderer.getDrawingBufferSize(new THREE.Vector2());
    const w = Math.max(1, s.x | 0);
    const h = Math.max(1, s.y | 0);
    if (this.emissionRT && this.emissionRT.width === w && this.emissionRT.height === h) return;
    if (this.emissionRT) this.emissionRT.dispose();
    this.emissionRT = new THREE.WebGLMultipleRenderTargets(w, h, 2, { type: THREE.HalfFloatType });
    for (const t of this.emissionRT.texture) t.colorSpace = THREE.NoColorSpace;
  }

  /** 站点这一趟画进两个附着点的目标;`texture[1]` 就是第二渲染目标的内容。 */
  renderEmission(renderer, camera) {
    if (this.emission === 'off' || !this.materials.length) return false;
    this._ensureEmissionTarget(renderer);
    const prevTarget = renderer.getRenderTarget();
    const prevClear = new THREE.Color();
    renderer.getClearColor(prevClear);
    const prevAlpha = renderer.getClearAlpha();
    renderer.setClearColor(0x000000, 0);
    renderer.setRenderTarget(this.emissionRT);
    renderer.clear();
    renderer.render(this.group, camera);
    renderer.setRenderTarget(prevTarget);
    renderer.setClearColor(prevClear, prevAlpha);
    this.emissionFrames += 1;
    return true;
  }

  /** 直显:把第二渲染目标画到屏幕上。**只为看得见,不参与合成。** */
  drawEmission(renderer) {
    if (!this.emissionRT) return false;
    if (!this._quad) {
      this._quadScene = new THREE.Scene();
      this._quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
      this._quadMat = new THREE.ShaderMaterial({
        uniforms: { tEmission: { value: null } },
        vertexShader: 'varying vec2 vUv;\nvoid main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
        fragmentShader: 'uniform sampler2D tEmission;\nvarying vec2 vUv;\n'
          + 'void main(){ gl_FragColor = vec4(texture2D(tEmission, vUv).rgb, 1.0); }',
        depthTest: false, depthWrite: false,
      });
      this._quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this._quadMat);
      this._quad.frustumCulled = false;
      this._quadScene.add(this._quad);
    }
    this._quadMat.uniforms.tEmission.value = this.emissionRT.texture[1];
    renderer.setRenderTarget(null);
    renderer.clear();
    renderer.render(this._quadScene, this._quadCam);
    return true;
  }

  emissionStatus() {
    return {
      mode: this.emission,
      // 两个渲染目标真的在位:一趟画进两个附着点,`texture[1]` 是第二个。
      supported: !!this.emissionRT,
      frames: this.emissionFrames,
      // 会往第二目标写非零值的两个族,以及当前挂着的材质里真的带发光遮罩图的份数。
      emissiveFamilies: SITE_EMISSIVE_FAMILIES.slice(),
      emissiveMaterials: this.emissiveMaterials,
      // 现象下发的发光类型(0=无 1=亮 2=暗),门开不开由它与材质的两个声明一起定。
      emissionType: ENV_GLOBALS.envEmissionType.value,
      composited: false,
      compositeNote: '发光缓冲**不加进主目标**;辉光合成在后处理链那一侧,本轮不改那条车道的文件',
    };
  }

  unmount() {
    for (const child of [...this.group.children]) {
      child.traverse((o) => { if (o.isMesh && o.geometry) o.geometry.dispose(); });
      this.group.remove(child);
    }
    for (const m of this.materials) m.dispose();
    this.materials = [];
    this.blendCounts = {};
    this.families = {};
    this.fallbackShaders = {};
    this.vertexColorRescaled = 0;
    this.emissiveMaterials = 0;
    this.ambiguousMaterials = 0;
    this.notes = [...(this.loadNotes || [])];
    this.key = null;
    this.mounted = null;
  }

  setSubject(v3, radius) {
    SITE_SUBJECT.envSubjectPos.value.copy(v3);
    if (radius) SITE_SUBJECT.envSubjectRadius.value = radius;
  }

  /** 逐帧的引擎内建量:屏幕尺寸(抖动用)、相机 near/far(雾的顶点斜坡用)、两个时间。 */
  syncFrame(renderer, camera, seconds) {
    const s = renderer.getDrawingBufferSize(new THREE.Vector2());
    SITE_GLOBALS.siteScreenParams.value.set(s.x, s.y, 1 + 1 / Math.max(s.x, 1), 1 + 1 / Math.max(s.y, 1));
    SITE_GLOBALS.siteProjectionParams.value.set(1, camera.near, camera.far, 1 / Math.max(camera.far, 1e-6));
    SITE_GLOBALS.siteOrthoCamera.value = camera.isOrthographicCamera ? 1 : 0;
    // `_Time.y` 与 `_TimeParameters.x` 是**两个量**:UV 滚动与叶片旋转读前者,
    // 树的风摆动读后者。这里分开推,判据可以只冻结其中一个看谁在驱动摆动。
    SITE_GLOBALS.siteTime.value = seconds;
    SITE_GLOBALS.siteTimeParameters.value = seconds;
  }

  dispose() {
    this.unmount();
    if (this.emissionRT) this.emissionRT.dispose();
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

  /**
   * 当前站点对应的**场景包键**。站点键与场景键是两件事:`first_floor` 这一个场景包承载
   * 三个站点(1/2/3 楼),几何按站点键挂、现象的两级查找与站点粒子按场景键查 ——
   * 粒子与覆盖的名单是按场景包命名的,拿站点键去查会查不到 2 楼与 3 楼的那一份。
   */
  get sceneKey() { return this.siteView.sceneKeyOf(this.site); }

  /** 室内等级名单(面板下发用)。等级本身是 master 表的字段,本仓没有 master。 */
  siteLevels() { return this.siteView.levels(this.site); }

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
    const site = this.indoor ? this.overrideSite : this.sceneKey;
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
    const site = this.indoor ? this.overrideSite : this.sceneKey;
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

  /**
   * 站点的**第二渲染目标**(发光缓冲)。三态:
   *   'off'    —— 不产;
   *   'buffer' —— 每帧把站点画进一个两个附着点的目标,`texture[1]` 就是那张图(默认);
   *   'view'   —— 在上一档的基础上把那张图直接画到屏幕上,给人眼看。
   * 三态都**不把发光加进主目标**:合成在后处理链那一侧,本轮不改那条车道的文件。
   */
  setSiteEmission(mode) { return this.siteView.setEmission(mode); }

  /**
   * 写一个站点这一族的全局量。这一组量是 C# 每帧下发的,现象档案里一项都没有,
   * 所以按「服务端决定的做成面板下发」处置。逐项出处见 `SITE_GLOBAL_META`。
   */
  setSiteGlobal(name, value) { return siteGlobalSet(name, value); }

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
    // 站点这一族要的引擎内建量:屏幕尺寸(抖动)、相机 near/far(雾的顶点斜坡)、
    // 以及 `_Time.y` 与 `_TimeParameters.x` 这**两个**时间量。
    this.siteView.syncFrame(this.renderer, this.camera, this.time);
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

  /**
   * 后处理:环境层开着且档案在位时接管最终绘制;返回 false = 调用方照常自己 render。
   *
   * 站点这一族**每个程序都写两个渲染目标**。画进只有一个附着点的目标时第二路被 GL 丢掉,
   * 所以要真的拿到那一张发光图,得先把站点画进一个两个附着点的目标 —— 就是下面这一趟。
   * 它**只产出缓冲**:辉光合成在后处理链那一侧,那条车道不在本轮改动范围里。
   */
  render() {
    if (!this.enabled) return false;
    if (this.siteVisible) this.siteView.renderEmission(this.renderer, this.camera);
    // 直显:把第二渲染目标画到屏幕上给人看。它不参与合成,也不加进主目标。
    if (this.siteView.emission === 'view') return this.siteView.drawEmission(this.renderer);
    if (!this.postOn) return false;
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
      site: SITE_ALL_SOURCE,
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
        // 换站点是异步的:从面板写下新站点到新几何真的挂上去,中间有一段。
        // 那一段里 **不能把上一站的读数抬到这一站的键下面报** —— 报了,
        // 读它的人(面板与判据)会把别人的三角数当成这一站的。
        // 所以:读数自己带的 `key` 对不上当前站点时,当作「还在装」。
        mounting: !!this.siteView.mounting || this.siteView.key !== this.site,
        mounted: (this.siteView.mounted && this.siteView.mounted.key === this.site)
          ? this.siteView.mounted : null,
        available: this.siteView.scenes().map((x) => ({
          key: x.key, scene: x.scene, siteType: x.siteType, indoor: x.indoor,
          position: x.position, levels: x.levels, placement: x.placement,
        })),
        // 站点键与场景键是两件事:三层室内共用一个场景包,只差世界位置里的 y。
        sceneKey: this.sceneKey,
        worldOrigin: this.siteView.worldOriginOf(this.site),
        // 这一份材质走的是哪一个程序:八族逐族计数,八族之外的按着色器名逐个计数。
        families: this.siteView.mounted ? this.siteView.mounted.families : {},
        fallbackShaders: this.siteView.mounted ? this.siteView.mounted.fallbackShaders : {},
        // 第二渲染目标(发光):产得出来没有、当前有几份材质会往它写非零、有没有被合成。
        emission: this.siteView.emissionStatus(),
        // 这一族要的全局量里现象档案没有给的那些,以及每一项的出处(`chosen` = 本示例挑的默认)。
        globals: siteGlobalSnapshot(),
        globalsMeta: SITE_GLOBAL_META,
        // 依据里明确未取得 / 未编译的条目:一条都没实现,原样列在这里。
        notObtained: SITE_NOT_OBTAINED,
        // 本示例侧的缺口,照实计数:阴影图、uv2/uv3、正交分支、云影的去向。
        approximations: [
          '主光阴影图:本示例没有阴影 pass,`atten` 由示例原有的球体落影近似顶着;'
            + '落影的律(FieldObject/Tree 套两次、Object 平方、Ground/Room-Floor 一次)照原样跑',
          'uv2 / uv3:产物导出的站点网格只带 uv0 与 uv1,读 uv3 的墙面 AO 整块停用并计数',
          '正交相机分支:视线方向在正交相机下改取视矩阵第三列;本示例相机恒为透视,这一支未走到',
          '云影四项:站点这一族里一次都没出现,已从站点材质撤出;四项照旧推送,当前站点侧无消费方',
          '关键字与运行期分支:由管线/站点控制器下发、不出现在材质记录里的那几个'
            + '(主光阴影、雾、站点边界、跳过现象光)做成运行期分支,两态画面与关键字版本相同',
        ],
        notes: this.siteView.notes.slice(),
        textureFailures: Object.fromEntries(this.siteView.texFailed),
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
