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
import { createParticleEffect, makeSharedTextureLoader, makeSharedMeshLoader, EMIT_SHAPES } from './emoticon.js';
import {
  ENV_GLOBALS, ENV_GLOBAL_META, ENV_FOG_CHUNK, ENV_WHITE_TEX, ENV_BLACK_TEX,
  withEnvGlobals, envSet, envConsumers,
} from './envglobals.js';
import { PostChain, flattenProfile, blendProfiles } from './envpost.js';

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
  { key: 'dir', label: '方向光向量', wired: true, to: 'toon 着色 lightDir + 天空/地面/落影' },
  { key: 'charLightColor', label: '角色方向光色', wired: true, to: 'toon 着色 lightColor' },
  { key: 'charSkinShade', label: '角色皮肤影色', wired: true, to: 'toon 着色 skinShade' },
  { key: 'charBodyShade', label: '角色身体影色', wired: true, to: 'toon 着色 bodyShade' },
  { key: 'phenLightColor', label: '现象方向光色', wired: true, to: '地面着色的直射项' },
  { key: 'phenShadeColor', label: '现象影色', wired: true, to: '地面着色的暗部项' },
  { key: 'dropShadowColor1', label: '落影色 1', wired: true, to: '地面落影(角色投影盘)' },
  { key: 'dropShadowColor2', label: '落影色 2', wired: true, to: '地面落影(角色投影盘)' },
  { key: 'dropShadowEdgeSmoothness', label: '落影边缘平滑', wired: true, to: '地面落影的边缘过渡' },
];

// ---- 天空 -----------------------------------------------------------------
//
// 真运行时的天空是一个网格 + 一份材质属性块,自带四个属性:混合进度、两张渐变、一个附加色;
// 换天气 = **两张渐变同时在位** + 插值进度。本示例照这个形状实现,但网格是**近似**:
//
//   * 几何用半球穹顶,不是原始天空网格(那个网格在 common 包里,提取侧尚未产出);
//     `index.json` 里一旦出现 `sky.mesh`,`SkyDome` 会走那个分支(见 `_geometryFor`)。
//   * **穹顶 UV 是近似**:渐变按世界方向的高度分量采样(天顶 → 第 0 个纹素,地平线 → 第 31 个),
//     真网格的 UV 布置未知,所以这条映射的方向本身也是近似。
//   * 渐变采样单独抽成 `SKY_RAMP_SAMPLE` 一段 GLSL:换实现只改这一段,不动其余管线。
//
// 天空网格每帧钉到相机水平位置(与运行时把它钉到玩家位置同义:穹顶永远不被走出去)。

export const SKY_RAMP_SAMPLE = /* glsl */`
// 渐变采样(**近似**):v = 世界方向的高度分量,天顶 v=1 取纹素 0,地平线 v=0 取纹素 31。
// 32x1 的纹理边缘半纹素内缩,免得线性过滤在两端把边缘纹素与自己混出台阶。
vec2 envRampUv(float height) {
  float u = clamp(1.0 - clamp(height, 0.0, 1.0), 0.0, 1.0);
  float halfTexel = 0.5 / ${RAMP_WIDTH}.0;   // 不用 half 当变量名:那是 GLSL 保留字
  return vec2(mix(halfTexel, 1.0 - halfTexel, u), 0.5);
}
`;

const SKY_VERT = /* glsl */`
varying vec3 vDir;
void main() {
  vDir = normalize(position);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const SKY_FRAG = /* glsl */`
uniform sampler2D rampTex1;
uniform sampler2D rampTex2;
uniform float fadeProgress;
uniform vec4 additiveColor;
uniform float additiveIntensity;
varying vec3 vDir;
${SKY_RAMP_SAMPLE}
void main() {
  vec2 uv = envRampUv(vDir.y);
  vec3 a = texture2D(rampTex1, uv).rgb;
  vec3 b = texture2D(rampTex2, uv).rgb;
  vec3 c = mix(a, b, clamp(fadeProgress, 0.0, 1.0));
  // 附加色在运行时走一趟 gamma→linear→缩放→gamma 的往返;这里按同一形状实现,
  // 缩放因子(AdditiveIntensity)的来源未知,默认 1。
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
      additiveColor: { value: new THREE.Vector4(0, 0, 0, 0) },
      additiveIntensity: { value: 1 },
    };
    this.material = new THREE.ShaderMaterial({
      name: 'env-sky',
      uniforms: this.uniforms,
      vertexShader: SKY_VERT,
      fragmentShader: SKY_FRAG,
      side: THREE.BackSide,
      depthWrite: false,
      depthTest: true,
    });
    this.geometrySource = 'hemisphere-approximation';
    this.mesh = new THREE.Mesh(this._geometryFor(null), this.material);
    this.mesh.name = 'env_sky_dome';
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = -1000;         // 先画天空,再画其余一切
  }

  /**
   * 几何来源。`index.json` 里出现 `sky.mesh`(一个 glTF/glb 相对路径)时用它,否则用半球近似。
   * 本示例只做**分支**与来源标注,不猜网格形状。
   */
  _geometryFor(loadedGeometry) {
    if (loadedGeometry) {
      this.geometrySource = 'index.json sky.mesh';
      return loadedGeometry;
    }
    // 半球:上半球 + 一点点裙边(地平线以下 6 度),免得相机略微俯视时看到穹顶开口。
    const g = new THREE.SphereGeometry(60, 48, 24, 0, Math.PI * 2, 0, Math.PI * 0.5 + 0.1);
    return g;
  }

  useGeometry(geometry) {
    if (!geometry) return false;
    const old = this.mesh.geometry;
    this.mesh.geometry = this._geometryFor(geometry);
    if (old && old !== this.mesh.geometry) old.dispose();
    return true;
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

// ---- 地面 -----------------------------------------------------------------
//
// 地面是本示例自己加的一块承影面(真站点地形不在本包里)。它的存在是为了让四组现象量**真的有
// 消费方**:现象方向光色与现象影色、云影四件套、落影三项、以及雾。写着色器时逐项对号,
// 没有对号的量不在这里假装被用到。

const GROUND_VERT = /* glsl */`
varying vec3 vWorldPos;
varying float vFogRamp;
${ENV_FOG_CHUNK}
void main() {
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vWorldPos = wp.xyz;
  // 与角色着色器同一条:雾的能见度斜坡逐顶点算,片元只吃插值结果。
  vFogRamp = envFogRamp(-(viewMatrix * wp).z);
  gl_Position = projectionMatrix * viewMatrix * wp;
}
`;

const GROUND_FRAG = /* glsl */`
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
uniform vec3 envBaseColor;
varying vec3 vWorldPos;
varying float vFogRamp;
${ENV_FOG_CHUNK}
void main() {
  // 直射项:平面法线恒为 +Y,所以直射强度就是光向的高度分量。
  float ndl = clamp(envLightDir.y, 0.0, 1.0);
  vec3 lit = envBaseColor * mix(envPhenShadeColor.rgb, envPhenLightColor.rgb, ndl);

  // 云影:两张贴图同时在位(交叉淡化),采样用**尺寸的倒数**,滚动是速度向量乘标量。
  vec2 uv = vWorldPos.xz * envCloudShadowScale + envCloudScrollSpeed * envTime;
  float ca = texture2D(envCloudShadowTex, uv).r;
  float cb = texture2D(envCloudShadowTexB, uv).r;
  float cloud = mix(ca, cb, clamp(envCloudFade, 0.0, 1.0));
  lit *= mix(1.0, cloud, clamp(envCloudShadowOpacity, 0.0, 1.0));

  // 落影:两色 + 边缘平滑。角色沿光向投到地面,近似成一个椭圆盘。
  vec2 off = envLightDir.y > 1e-3
    ? envSubjectPos.xz - envLightDir.xz * (envSubjectPos.y / max(envLightDir.y, 1e-3))
    : envSubjectPos.xz;
  float d = length(vWorldPos.xz - off) / max(envSubjectRadius, 1e-3);
  float edge = clamp(envDropShadowEdgeSmoothness, 0.02, 4.0);
  float core = 1.0 - smoothstep(1.0 - edge, 1.0, clamp(d, 0.0, 1.0));
  vec3 shadowCol = mix(envDropShadowColor1.rgb, envDropShadowColor2.rgb, clamp(d, 0.0, 1.0));
  float shadowA = mix(envDropShadowColor1.a, envDropShadowColor2.a, clamp(d, 0.0, 1.0)) * core;
  lit = mix(lit, shadowCol, clamp(shadowA, 0.0, 1.0));

  gl_FragColor = vec4(envApplyFogRamp(lit, vFogRamp, vWorldPos.y), 1.0);
}
`;

class Ground {
  constructor() {
    this.material = new THREE.ShaderMaterial({
      name: 'env-ground',
      uniforms: withEnvGlobals({
        envSubjectPos: { value: new THREE.Vector3(0, 0.6, 0) },
        envSubjectRadius: { value: 0.55 },
        envBaseColor: { value: new THREE.Vector3(0.62, 0.66, 0.6) },
      }),
      vertexShader: GROUND_VERT,
      fragmentShader: GROUND_FRAG,
      side: THREE.FrontSide,
    });
    const g = new THREE.PlaneGeometry(120, 120, 1, 1);
    g.rotateX(-Math.PI / 2);
    this.mesh = new THREE.Mesh(g, this.material);
    this.mesh.name = 'env_ground';
    this.mesh.position.y = -0.002;     // 让 viewer 自带的地格网仍画在上面,不与地面 z-fight
    this.mesh.renderOrder = -900;
    this.mesh.frustumCulled = false;
  }
  dispose() {
    this.mesh.geometry.dispose();
    this.material.dispose();
    this.mesh.removeFromParent();
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
// 未建模的发射形状(Donut / Hemisphere / ConeVolume / Mesh / Box 等)在发射器引擎里**整条停发**
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
 * 一份效果文档里 Mesh 绘制模式引用到的 glb 文件名(去重)。
 * 渲染器上的 `meshes` 是个数组:Mesh 模式最多挂 4 个网格,逐粒子随机选一个。
 */
function meshFilesOf(doc) {
  const out = new Set();
  for (const e of Object.values((doc && doc.effects) || {})) {
    for (const p of e.particles || []) {
      for (const m of ((p.renderer || {}).meshes) || []) {
        if (m && m.file) out.add(m.file);
      }
    }
  }
  return [...out];
}

/** 数一份效果文档里用到但发射器引擎没建模的形状。 */export function unmodelledShapes(doc) {
  const counts = {};
  let noShape = 0, total = 0;
  for (const e of Object.values((doc && doc.effects) || {})) {
    for (const p of e.particles || []) {
      total++;
      const t = ((p.system || {}).shape || {}).type;
      if (!t) { noShape++; continue; }
      if (!EMIT_SHAPES.has(t)) counts[t] = (counts[t] || 0) + 1;
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

    this.index = null;
    this.names = [];            // 现象资产名(目录名),升序
    this.loaded = new Map();    // 资产名 → { config, ramp, profile, fx, overrides }
    this.enabled = false;
    this.particlesOn = true;
    this.indoor = false;        // 「室内覆盖」= 用带覆盖的那个站点
    this.site = 'grasslands';   // 站点粒子挂哪一站
    this.overrideSite = null;   // 带覆盖的站点名(从 index 读出,不写死)

    this.sky = new SkyDome();
    this.ground = new Ground();
    this.skyVisible = true;
    this.groundVisible = true;
    this.group = new THREE.Group();
    this.group.name = 'env_root';
    this.fxWorld = new THREE.Group();
    this.fxWorld.name = 'env_fx_world';
    this.fxCamera = new THREE.Group();
    this.fxCamera.name = 'env_fx_camera';
    this.group.add(this.sky.mesh, this.ground.mesh, this.fxWorld);
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
    this.skyMeshNote = 'index.json 未给天空网格,使用半球近似';
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
    this.overrideSites = [...sites].sort();
    if (idx.sky && idx.sky.mesh) {
      this.skyMeshNote = `index.json 给了天空网格 ${idx.sky.mesh}(未装载:本示例的装载分支待接)`;
    }
    return true;
  }

  /** 一个现象的四份产物 + 覆盖。已装载的直接返回(现象资产很轻,全量常驻没有压力)。 */
  async loadPhenomenon(name) {
    if (this.loaded.has(name)) return this.loaded.get(name);
    const entry = this.index && this.index.phenomena && this.index.phenomena[name];
    if (!entry) { this.errors.push(`index.json 里没有 ${name}`); return null; }
    const rel = (p) => `${this.root}/${p}`;
    const declaredEmitters = entry.fx ? num(entry.fx.emitters) : 0;
    const [config, profileDoc, fx] = await Promise.all([
      this._json(rel(entry.config)),
      entry.postprocess ? this._json(rel(entry.postprocess)) : null,
      entry.fx && entry.fx.file ? this._json(rel(entry.fx.file)) : null,
    ]);
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

    // 天空:两张渐变同时在位 + 进度。
    this.sky.setRamps(a.ramp, b.ramp, t);
  }

  /** 挂粒子:三族按语义挂,`site` 只挂当前站点(或对所有站点通用的那些)。 */
  _mountEffects() {
    this._retireEffects();
    if (!this.particlesOn || !this.to || !this.to.rec.fx) return;
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

  setSite(site) {
    this.site = site;
    this.refreshSite();
  }

  setPost(on) { this.postOn = !!on; this.post.setEnabled(!!on); }

  setSkyVisible(on) {
    this.skyVisible = !!on;
    this.sky.mesh.visible = this.skyVisible;
    return this.skyVisible;
  }

  setGroundVisible(on) {
    this.groundVisible = !!on;
    this.ground.mesh.visible = this.groundVisible;
    return this.groundVisible;
  }

  /** 角色材质换了(换角色)就重新接线。 */
  setCharacterMaterials(mats) {
    this.characterMaterials = mats || [];
    this._applyBlend();
  }

  setSubject(v3, radius) {
    this.subject.copy(v3);
    this.ground.material.uniforms.envSubjectPos.value.copy(v3);
    if (radius) this.ground.material.uniforms.envSubjectRadius.value = radius;
  }

  // ---- 帧循环 ----

  attach() {
    if (!this.group.parent) this.scene.add(this.group);
    this.enabled = true;
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
      ground: GROUND_FRAG,
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
        visible: this.skyVisible,
        geometrySource: this.sky.geometrySource,
        note: this.skyMeshNote,
        approximations: [
          '穹顶几何是半球近似(真天空网格在共享包里,提取侧尚未产出)',
          '穹顶 UV 是近似:按世界方向的高度分量采样渐变,真网格 UV 未知',
          '附加色的缩放因子来源未知,默认 1',
        ],
      },
      ground: {
        visible: this.groundVisible,
      },
      post: this.post.status(),
      notRestored: NOT_RESTORED,
      errors: this.errors.slice(0, 8),
    };
  }

  dispose() {
    this._clearEffects();
    this.sky.dispose();
    this.ground.dispose();
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
  '真天空网格:穹顶几何**已在产物里**(models 里有 dome_01 / HalfSphere_01 等候选),但「天空视图用的是哪一张」尚未认定,所以 index.json 里没有 sky.mesh 指向它 —— 当前仍是半球近似 + 近似 UV。认定之后本示例走 _geometryFor 的网格分支,不必改其余管线',
  '闪电时间轴:只有一个现象带时间轴资产,本示例不建模时间轴(它在产物里登记为未支持)',
  '粒子模块:**提取侧已全部建模**(自定义数据/子发射器/碰撞/受力/噪声/拖尾都在产物里),但本示例的发射器引擎只实现了出生与基本运动 —— 曲线型自定义数据、子发射器、碰撞与拖尾在画面上还未生效;未实现项与未建模的发射形状逐项计数见 particles.unmodelledShapes',
  '发射形状 Mesh / Box 等没有发射公式:这些形状的发射器**整条停发**,不退化成点发射(点发射会把本该铺开的粒子全堆在发射节点原点上)。当前站点真的挂着几个、分别是什么形状,见 particles.suppressed / particles.suppressedShapes。renderMode=Mesh 是另一道渲染器能力缺口,单独见 particles.skipped.unsupported / unsupportedModes',
  '`renderer.maxParticleSize` / `minParticleSize` 是**视口比例**(默认 0.5 = 半个视口高),不是世界尺寸:本示例不按它截尺寸(拿视口比例去截米是单位错误,会把声明 100x60 米的云静默截成 0.5 米),而按视口比例截断的那条律本示例没有建模 —— 大件在近处会比运行时更大',
  '站点决定的那批全局着色量不在本包里,本示例按中性常量处理(它们随站点变而不随天气变)',
  '地面与落影是本示例自己的承影面与投影盘,不是站点地形',
];

/** 原始数据里就没有的东西。**不是欠账**,写在这里免得被当成未实现。 */
export const ABSENT_FROM_DATA = [
  'LUT 颜色分级:后处理档案里没有任何查表类组件,资产里也没有对应的三维贴图 —— 原始数据不含,不是未实现。代码留了贴图类参数的接入位,但没有东西在等它',
  '天空底色:它由装载参数给出,不在现象配置里,所以本示例不编造一个值',
];
