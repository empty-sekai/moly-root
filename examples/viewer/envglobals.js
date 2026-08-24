// envglobals.js — 现象驱动的全局着色量(一份 uniform 对象,按引用共享)
//
// 游戏侧的现象视觉走**两条互不相通的通道**:一条是全局着色量,一条是一份光照结构,由渲染
// pass 直读。本文件是第一条通道在 three.js 里的等价物:three.js 没有「全局 uniform」,
// 但**同一个 uniform 对象被多份材质按引用共享**时,写一次就等于推给了所有消费方 —— 这正是
// 全局量的语义。于是:
//
//   * 环境层每帧只往 ENV_GLOBALS 写一次;
//   * 每份材质把 ENV_GLOBALS 原样铺进自己的 uniforms(对象同一个,不复制);
//   * 每个着色器**只声明自己真的用得到的那几个**,没声明的照样被推送、只是当前无消费方。
//
// 最后一条是刻意的:风的九项在本示例里没有消费方(没有植被/家具),但它们照样被推出去,
// 将来任何一份声明了它们的材质一进来就立即生效。`ENV_GLOBAL_META` 逐项记着它属于哪一组、
// 来自产物的哪个字段;`environment.js` 用它 + 各着色器源码算出「谁在消费」,不靠手写名单。

import * as THREE from './three.module.min.js';

const V2 = (x = 0, y = 0) => new THREE.Vector2(x, y);
const V3 = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const V4 = (x = 0, y = 0, z = 0, w = 0) => new THREE.Vector4(x, y, z, w);

function tex1x1(rgba) {
  const t = new THREE.DataTexture(new Uint8Array(rgba), 1, 1, THREE.RGBAFormat);
  t.colorSpace = THREE.NoColorSpace;
  t.needsUpdate = true;
  return t;
}
export const ENV_WHITE_TEX = tex1x1([255, 255, 255, 255]);
export const ENV_BLACK_TEX = tex1x1([0, 0, 0, 255]);

/**
 * 全局量本体。**每个值都是一个 `{value}` 对象**,材质之间共享的是这些对象,不是它们的值。
 * 初值一律是「无贡献」的中性值:环境层没起来时,铺了这组 uniform 的材质与没铺完全一样。
 */
export const ENV_GLOBALS = {
  // ---- 光照(九项;游戏侧走另一条通道,本示例用 uniform 表达) ----
  envLightDir: { value: V3(0.5795, 0.5, 0.6435) },
  envCharLightColor: { value: V4(1, 1, 1, 1) },
  envCharSkinShade: { value: V4(0.5, 0.5, 0.5, 1) },
  envCharBodyShade: { value: V4(0.5, 0.5, 0.5, 1) },
  envPhenLightColor: { value: V4(1, 1, 1, 1) },
  envPhenShadeColor: { value: V4(0.5, 0.5, 0.5, 1) },
  envDropShadowColor1: { value: V4(0, 0, 0, 0) },
  envDropShadowColor2: { value: V4(0, 0, 0, 0) },
  envDropShadowEdgeSmoothness: { value: 1 },

  // ---- 云影(四项;采样用尺寸的倒数,滚动 = 速度向量 × 标量) ----
  envCloudShadowTex: { value: ENV_WHITE_TEX },
  envCloudShadowTexB: { value: ENV_WHITE_TEX },
  envCloudFade: { value: 0 },
  envCloudShadowScale: { value: 1 },
  envCloudShadowOpacity: { value: 0 },
  envCloudScrollSpeed: { value: V2(0, 0) },

  // ---- 风(九项;本示例无消费方,照样推送) ----
  envWindSpeed: { value: 0 },
  envWindColor: { value: V4(1, 1, 1, 0) },
  envVertexWaveAmount: { value: 0 },
  envVertexWaveExponent: { value: 1 },
  envVertexRandomAmount: { value: 0 },
  envVertexRandomSpeed: { value: 0 },
  envWindWaveDistortionAmount: { value: 0 },
  envWindWaveDistortionFrequency: { value: 0 },
  envWindNoiseTex: { value: ENV_BLACK_TEX },

  // ---- 角色描边五项(本示例无描边 pass) ----
  envCharOutlineWidth: { value: 0 },
  envCharOutlineDepthOffset: { value: 0 },
  envCharOutlineWidthMaxRate: { value: 1 },
  envCharOutlineWidthMinRate: { value: 1 },
  envCharOutlineColor: { value: V4(0, 0, 0, 1) },

  // ---- 家具描边五项(本示例无家具) ----
  envFixtureOutlineWidth: { value: 0 },
  envFixtureOutlineDepthOffset: { value: 0 },
  envFixtureOutlineWidthMaxRate: { value: 1 },
  envFixtureOutlineWidthMinRate: { value: 1 },
  envFixtureOutlineColor: { value: V4(0, 0, 0, 1) },

  // ---- 其它两项 ----
  envEmissionType: { value: 0 },
  envSkyBottomColor: { value: V4(0, 0, 0, 0) },

  // ---- 雾(后处理档案里的九参) ----
  envFogEnabled: { value: 0 },
  envFogNearColor: { value: V4(0, 0, 0, 0) },
  envFogFarColor: { value: V4(0, 0, 0, 0) },
  envFogParams: { value: V4(0, 0, 0, 0) },

  // ---- 本示例自己的量(不是现象量,标注清楚) ----
  envTime: { value: 0 },
};

/** 逐项元数据:组、产物里的来源字段、以及本示例侧的说明。 */
export const ENV_GLOBAL_META = {
  envLightDir: { group: 'light', from: 'config.light.angleXZ/angleY(度→单位向量)' },
  envCharLightColor: { group: 'light', from: 'config.light.characterDirectionalLightColor' },
  envCharSkinShade: { group: 'light', from: 'config.light.characterShadeSkinColor' },
  envCharBodyShade: { group: 'light', from: 'config.light.characterBodyShadeColor' },
  envPhenLightColor: { group: 'light', from: 'config.light.phenomenaDirectionalLightColor' },
  envPhenShadeColor: { group: 'light', from: 'config.light.phenomenaShadeColor' },
  envDropShadowColor1: { group: 'light', from: 'config.light.dropShadowColor1' },
  envDropShadowColor2: { group: 'light', from: 'config.light.dropShadowColor2' },
  envDropShadowEdgeSmoothness: { group: 'light', from: 'config.light.dropShadowEdgeSmoothness' },
  envCloudShadowTex: { group: 'cloud', from: 'config.cloud.cloudShadowTexture' },
  envCloudShadowTexB: { group: 'cloud', from: '(本示例)交叉淡化期间的第二张云影贴图' },
  envCloudFade: { group: 'cloud', from: '(本示例)云影两张贴图之间的进度' },
  envCloudShadowScale: { group: 'cloud', from: '1 / config.cloud.cloudShadowTextureSize' },
  envCloudShadowOpacity: { group: 'cloud', from: 'config.cloud.cloudShadowOpacity' },
  envCloudScrollSpeed: { group: 'cloud', from: 'config.cloud.cloudScrollVelocity × cloudScrollSpeed' },
  envWindSpeed: { group: 'wind', from: 'config.wind.windSpeed' },
  envWindColor: { group: 'wind', from: 'config.wind.windColor' },
  envVertexWaveAmount: { group: 'wind', from: 'config.wind.vertexWaveAnimationAmount' },
  envVertexWaveExponent: { group: 'wind', from: 'config.wind.vertexWaveExponent' },
  envVertexRandomAmount: { group: 'wind', from: 'config.wind.vertexRandomAnimationAmount' },
  envVertexRandomSpeed: { group: 'wind', from: 'config.wind.vertexRandomAnimationSpeed' },
  envWindWaveDistortionAmount: { group: 'wind', from: 'config.wind.windWaveDistortionAmount' },
  envWindWaveDistortionFrequency: { group: 'wind', from: 'config.wind.windWaveDistortionFrequency' },
  envWindNoiseTex: { group: 'wind', from: 'config.wind.windNoiseTexture' },
  envCharOutlineWidth: { group: 'character', from: 'config.character.outlineWidth' },
  envCharOutlineDepthOffset: { group: 'character', from: 'config.character.outlineDepthOffset' },
  envCharOutlineWidthMaxRate: { group: 'character', from: 'config.character.outlineWidthMaxRate' },
  envCharOutlineWidthMinRate: { group: 'character', from: 'config.character.outlineWidthMinRate' },
  envCharOutlineColor: { group: 'character', from: 'config.character.outlineColor' },
  envFixtureOutlineWidth: { group: 'fixture', from: 'config.fixture.outlineWidth' },
  envFixtureOutlineDepthOffset: { group: 'fixture', from: 'config.fixture.outlineDepthOffset' },
  envFixtureOutlineWidthMaxRate: { group: 'fixture', from: 'config.fixture.outlineWidthMaxRate' },
  envFixtureOutlineWidthMinRate: { group: 'fixture', from: 'config.fixture.outlineWidthMinRate' },
  envFixtureOutlineColor: { group: 'fixture', from: 'config.fixture.outlineColor' },
  envEmissionType: { group: 'misc', from: 'config.emissionType' },
  envSkyBottomColor: {
    group: 'misc',
    from: '(产物里没有)天空底色由装载参数给出,不在 config 里;地平线色改取渐变的第 0 个纹素',
  },
  envFogEnabled: { group: 'fog', from: 'MysekaiFogVolume.enabled × 组件 active(原版是着色器关键字)' },
  envFogNearColor: { group: 'fog', from: 'MysekaiFogVolume.nearColor.rgb + a=nearDensity×density' },
  envFogFarColor: { group: 'fog', from: 'MysekaiFogVolume.farColor.rgb + a=farDensity×density' },
  envFogParams: { group: 'fog', from: 'MysekaiFogVolume:(-1/(end-start), end/(end-start), 1/height, 0)' },
  envTime: { group: 'viewer', from: '(本示例)秒计时,给云影滚动用' },
};

/** 把全局量铺进一份材质的 uniforms(对象按引用共享,**不要**在这里复制值)。 */
export function withEnvGlobals(uniforms) {
  return { ...ENV_GLOBALS, ...uniforms };
}

/** 写一个全局量:数值直接赋,向量/颜色按分量写,贴图换引用。 */
export function envSet(name, value) {
  const u = ENV_GLOBALS[name];
  if (!u) return false;
  const cur = u.value;
  if (Array.isArray(value)) {
    if (cur && typeof cur.fromArray === 'function') cur.fromArray(value);
    else u.value = value.slice();
  } else if (value && value.isTexture) {
    u.value = value;
  } else if (cur && cur.isVector3 && value && value.isVector3) {
    cur.copy(value);
  } else {
    u.value = Number.isFinite(+value) ? +value : 0;
  }
  return true;
}

// ---- 雾的 GLSL(消费方直接拼进自己的片元着色器) --------------------------
//
// 雾的九参是**取值**,不是公式:档案只给近/远色、近/远密度、起止距离、雾高与总密度,
// 组合方式在本示例里是**近似**,写死在下面这一段里,好处是三个消费方共用同一段代码:
//
//   t     = saturate((距离 - 起) / (止 - 起))
//   密度  = mix(近密度, 远密度, t) × 总密度
//   系数  = (1 - exp(-密度 × t)) × 高度衰减,高度衰减 = saturate(1 - y / 雾高)
//   颜色  = mix(近色, 远色, t)
//
// 雾:**原版只有三个全局量**,九个字段在运行时就折进它们了 ——
//   nearColor.rgb 原样;nearColor.a = nearDensity × density
//   farColor.rgb  原样;farColor.a  = farDensity  × density
//   P = (−1/(end−start), end/(end−start), 1/height, 0);P.w 上传为 0 且无人读
// 资产里雾色自带的 alpha **被丢弃**(它不是权重),alpha 槽装的是密度。
//
// 片元律照抄真源,三处最容易写错的地方各标一句:
//   * `t` 是**能见度**不是雾度:start 处 1、end 处 0;
//   * 高度衰减是 **exp2** 且用**绝对世界 y**(不是相机相对、不是线性 1−y/h);
//   * 雾色先 mix 再 clamp(反过来在 alpha>1 的档案上会少 14%)。
// 距离用**线性眼空间前向深度**,不是到相机的径向距离;原版逐顶点算这条斜坡再插值,
// 所以下面把它拆成 vertex/fragment 两段。
export const ENV_FOG_CHUNK = /* glsl */`
uniform float envFogEnabled;
uniform vec4 envFogNearColor;   // rgb=近色, a=nearDensity*density
uniform vec4 envFogFarColor;    // rgb=远色, a=farDensity*density
uniform vec4 envFogParams;      // x=-1/(end-start), y=end/(end-start), z=1/height, w=0

// 顶点侧:线性眼空间前向深度 → 能见度斜坡。与原版同一个位置算。
float envFogRamp(float eyeDepth) {
  return clamp(eyeDepth * envFogParams.x + envFogParams.y, 0.0, 1.0);
}

// 片元侧:ramp 由顶点插值送来;worldY 是绝对世界高度。
vec3 envApplyFogRamp(vec3 color, float ramp, float worldY) {
  if (envFogEnabled < 0.5) return color;
  vec4 fogCol = clamp(mix(envFogFarColor, envFogNearColor, ramp), 0.0, 1.0);
  float h = min(exp2(-worldY * envFogParams.z), 1.0);
  float f = (1.0 - ramp) * fogCol.a * h;
  return clamp(mix(color, fogCol.rgb, clamp(f, 0.0, 1.0)), 0.0, 1.0);
}
`;

/** 从若干份着色器源码里数出每个全局量的消费方(名字出现即算声明)。 */
export function envConsumers(sources) {
  const out = {};
  for (const name of Object.keys(ENV_GLOBALS)) {
    const re = new RegExp(`\\b${name}\\b`);
    out[name] = Object.keys(sources).filter((k) => re.test(sources[k] || ''));
  }
  return out;
}
