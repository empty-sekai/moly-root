// shading.js — contract-driven character toon materials
//
// The material contract supplies body masks, face roles, shade colours, light
// direction, and a stencil id. Body pixels use the mask's two channels; face
// pixels use the face branch. The shader combines a thresholded toon term with
// an X/Z sphere term, then applies light colour and brightness.
//
// UV offsets are added in the vertex stage and are shared by the main, mask, and
// eyebrow textures. The eyebrow overlay discards below its mask threshold and
// is rendered with stencil equality, depth Always, and a final render order.
// Texture sampling stays linear and the renderer performs no tone mapping.
// The default light angles are 48 degrees azimuth and 30 degrees elevation;
// the head reference is (0, 0.6193, 0) in avatar space.
//
// 现象环境的全局量(envglobals.js)按引用铺进每份材质:本着色器只声明并消费其中的**雾**九参,
// 其余全局量照样在 uniforms 里被推送,只是这一支着色器不读它们。环境层没起来时雾开关为 0,
// 输出与不铺这组 uniform 完全一致。

import * as THREE from './three.module.min.js';
import { withEnvGlobals, ENV_FOG_CHUNK } from './envglobals.js';

export const LIGHT_DIR = [0.5795, 0.5, 0.6435];      // cos48·cos30, sin30, sin48·cos30
export const LIGHT_DAY = [1, 1, 1, 1];
export const LIGHT_NIGHT = [0.703, 0.71863, 1, 1];
export const SHADE_NEUTRAL = [0.5, 0.5, 0.5, 1];
export const SPHERE_EDGE = 0.0;
export const SPHERE_SMOOTH = 1.0;
export const HEAD_LOCAL = new THREE.Vector3(0, 0.6193, 0);
export const stencilRefFor = (i) => i * 4 + 4;             // Stable per-character stencil slot

const VERT = /* glsl */`
varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vWorldPos;
varying float vFogRamp;
uniform vec2 uvOffset;
${ENV_FOG_CHUNK}
#include <skinning_pars_vertex>
void main() {
  vUv = uv + uvOffset;   // Apply the atlas offset before all texture samples
  #include <beginnormal_vertex>
  #include <skinbase_vertex>
  #include <skinnormal_vertex>
  #include <begin_vertex>
  #include <skinning_vertex>
  vec4 wp = modelMatrix * vec4(transformed, 1.0);
  vWorldPos = wp.xyz;
  // 雾的能见度斜坡逐顶点算(与原版同一位置),片元只吃插值结果。
  // 距离用线性眼空间前向深度:眼空间 z 是负的,取负即前向距离。
  vFogRamp = envFogRamp(-(viewMatrix * wp).z);
#ifdef EYEBROW
  vNormal = vec3(0.0);   // Eyebrow geometry intentionally has no normal input
#else
  vNormal = mat3(modelMatrix) * objectNormal;
#endif
  gl_Position = projectionMatrix * viewMatrix * wp;
}
`;

const FRAG = /* glsl */`
uniform sampler2D mainTex;
uniform sampler2D bodyMaskTex;
uniform float shaderUsage;        // 0=Face 1=Body 其它=default(用 float+step 避开 D3D int 三元雷区)
uniform vec4 skinShade;
uniform vec4 bodyShade;
uniform vec4 lightColor;
uniform vec3 lightDir;            // Unit vector toward the light; keep its X/Z magnitudes
uniform vec3 headPos;
uniform float overrideShading;
uniform float shadeIntensity;
uniform float shadeThreshold;
uniform float shadeSmoothness;
uniform float sphereEdge;
uniform float sphereSmooth;
uniform int debugMode;            // 0 正常 1 无阴影 2 无球面 3 分支可视化(诊断用)
uniform float brightness;
#ifdef EYEBROW
uniform sampler2D eyebrowTex;
uniform float eyebrowClip;
uniform float eyebrowAlpha;
#endif
varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vWorldPos;
varying float vFogRamp;
${ENV_FOG_CHUNK}
void main() {
  vec4 albedo = texture2D(mainTex, vUv);
#ifdef EYEBROW
  float ebm = texture2D(eyebrowTex, vUv).r;                    // Red-channel eyebrow mask
  if (ebm - eyebrowClip < 0.0) discard;                        // Clip transparent eyebrow pixels
#endif
  vec2 bodyMask = texture2D(bodyMaskTex, vUv).xy;              // 无条件采样保持 uniform control flow
  float isBody = step(0.5, shaderUsage) * step(shaderUsage, 1.5);
  float isFace = step(shaderUsage, 0.5) * step(-0.5, shaderUsage);
  vec2 mask = bodyMask * isBody + vec2(1.0, 0.0) * isFace;     // Role-selected mask
  vec4 shadeCol = mix(skinShade, bodyShade, 1.0 - mask.x);
  vec3 N = dot(vNormal, vNormal) > 1e-12 ? normalize(vNormal) : vec3(0.0);
  float lambert = clamp(dot(N, lightDir) - mask.y, 0.0, 1.0);
  float useLocal = step(0.5, overrideShading);                 // Select per-material shading controls
  float intensity = mix(1.0, shadeIntensity, useLocal);
  float thr = mix(0.0, shadeThreshold, useLocal);
  float sm = mix(0.0, shadeSmoothness, useLocal);
  float toon;
  if (sm < 0.004) {                                            // Hard toon threshold
    toon = intensity * (thr >= lambert ? 1.0 : 0.0);
  } else {                                                     // Reversed smooth transition
    float x = clamp((lambert - (thr + sm)) / (-2.0 * sm), 0.0, 1.0);
    toon = intensity * x * x * (3.0 - 2.0 * x);
  }
  vec2 d = normalize(vWorldPos.xz - headPos.xz);               // Sphere term uses X/Z only
  float sdot = dot(d, lightDir.xz);                            // Preserve light X/Z magnitudes
  float xs = clamp((sdot - (sphereEdge + sphereSmooth)) / (-2.0 * sphereSmooth), 0.0, 1.0);
  float sphere = xs * xs * (3.0 - 2.0 * xs);                   // Smooth sphere term
  float pick = step(0.5, mask.x);                              // Face mask selects sphere shading
  float shading = mix(toon, sphere, pick) * shadeCol.a;
  if (debugMode == 1) shading = 0.0;              // 诊断:关掉全部阴影(球面+阶调)
  if (debugMode == 2) shading = mix(toon, 0.0, pick);        // 诊断:只关球面
  vec3 lit = mix(albedo.rgb, albedo.rgb * lightColor.rgb, lightColor.a);
  vec3 color = mix(lit, lit * shadeCol.rgb, shading);
  if (debugMode == 3)                              // 诊断:分支可视化(红=球面支,蓝=阶调支)
    color = mix(color, mix(vec3(0.2,0.2,1.0), vec3(1.0,0.2,0.2), pick), 0.35);
  color = envApplyFogRamp(color, vFogRamp, vWorldPos.y);   // 现象的雾(开关为 0 时原样返回)
#ifdef EYEBROW
  gl_FragColor = vec4(color, albedo.a * ebm * eyebrowAlpha) * brightness;
#else
  gl_FragColor = vec4(color * brightness, albedo.a * brightness);
#endif
}
`;

// 环境层要按名字数出「谁在消费哪个全局量」,所以把片元源码导出(只读用途)。
export const CHARACTER_FRAG = FRAG;

let _black = null, _white = null;
function tex1x1(rgba) {
  const t = new THREE.DataTexture(new Uint8Array(rgba), 1, 1, THREE.RGBAFormat);
  t.colorSpace = THREE.NoColorSpace;
  t.needsUpdate = true;
  return t;
}
export function blackTex() { return _black || (_black = tex1x1([0, 0, 0, 255])); }
export function whiteTex() { return _white || (_white = tex1x1([255, 255, 255, 255])); }

export function prepTexture(t) {
  if (!t) return t;
  t.colorSpace = THREE.NoColorSpace; // Gamma 直通:采样不做 sRGB 解码
  t.anisotropy = 1;
  t.minFilter = THREE.LinearMipmapNearestFilter; // Bilinear magnification with nearest mip level
  t.magFilter = THREE.LinearFilter;
  t.needsUpdate = true;
  return t;
}

// role: 'body'|'eye'|'mouth';overlay 由 eyebrow 参数决定(仅 eye 建)
export function makeCharacterMaterial(opts) {
  const {
    name, mainTex, bodyMaskTex, usage, override, intensity, threshold, smoothness,
    brightness = 1, stencilRef = 4, eyebrow = null,
  } = opts;
  const defines = {};
  if (eyebrow) defines.EYEBROW = '';
  const mat = new THREE.ShaderMaterial({
    name,
    defines,
    vertexShader: VERT,
    fragmentShader: FRAG,
    uniforms: withEnvGlobals({
      mainTex: { value: prepTexture(mainTex) || whiteTex() },
      bodyMaskTex: { value: prepTexture(bodyMaskTex) || blackTex() },
      shaderUsage: { value: +usage || 0 },
      skinShade: { value: new THREE.Vector4(...SHADE_NEUTRAL) },
      bodyShade: { value: new THREE.Vector4(...SHADE_NEUTRAL) },
      lightColor: { value: new THREE.Vector4(...LIGHT_DAY) },
      lightDir: { value: new THREE.Vector3(...LIGHT_DIR) },
      headPos: { value: new THREE.Vector3(0, HEAD_LOCAL.y, 0) },
      overrideShading: { value: override ? 1 : 0 },
      shadeIntensity: { value: intensity ?? 1 },
      shadeThreshold: { value: threshold ?? 0 },
      shadeSmoothness: { value: smoothness ?? 0 },
      sphereEdge: { value: SPHERE_EDGE },
      sphereSmooth: { value: SPHERE_SMOOTH },
      debugMode: { value: 0 },
      brightness: { value: brightness },
      uvOffset: { value: new THREE.Vector2(0, 0) },
      ...(eyebrow ? {
        eyebrowTex: { value: prepTexture(eyebrow.tex) || blackTex() }, // shader 默认 "black" ⇒ 恒 discard
        eyebrowClip: { value: eyebrow.clip ?? 0.5 },
        eyebrowAlpha: { value: eyebrow.alpha ?? 1 },
      } : {}),
    }),
    side: THREE.FrontSide, // Cull Back
  });
  if (eyebrow) {
    // Pass 2 Eyebrow:SrcAlpha/OneMinusSrcAlpha,ZTest Always,ZWrite On,Stencil Equal(不写)
    mat.transparent = true;
    mat.blending = THREE.NormalBlending;
    mat.depthTest = true;
    mat.depthFunc = THREE.AlwaysDepth;
    mat.depthWrite = true;
    mat.stencilWrite = true;
    mat.stencilFunc = THREE.EqualStencilFunc;
    mat.stencilRef = stencilRef;
    mat.stencilFuncMask = 0xff;
    mat.stencilWriteMask = 0x00;
    mat.stencilFail = THREE.KeepStencilOp;
    mat.stencilZFail = THREE.KeepStencilOp;
    mat.stencilZPass = THREE.KeepStencilOp;
  } else {
    // Pass 0 Base:One/Zero,LEqual,ZWrite On,Stencil Always/Replace(写整个剪影)
    mat.transparent = false;
    mat.depthTest = true;
    mat.depthWrite = true;
    mat.stencilWrite = true;
    mat.stencilFunc = THREE.AlwaysStencilFunc;
    mat.stencilRef = stencilRef;
    mat.stencilFuncMask = 0xff;
    mat.stencilWriteMask = 0xff;
    mat.stencilFail = THREE.KeepStencilOp;
    mat.stencilZFail = THREE.KeepStencilOp;
    mat.stencilZPass = THREE.ReplaceStencilOp;
  }
  return mat;
}

// 全体材质共享的全局量更新
export function setGlobal(materials, key, setter) {
  for (const m of materials) {
    const u = m.uniforms && m.uniforms[key];
    if (u) setter(u);
  }
}
export function setNight(materials, night) {
  const c = night ? LIGHT_NIGHT : LIGHT_DAY;
  setGlobal(materials, 'lightColor', (u) => u.value.set(c[0], c[1], c[2], c[3]));
}
// 现象环境接管时用的三个入口:光色取现象的**角色**方向光色,两个暗部色取皮肤/身体暗部色。
// 三者都是 `[r,g,b,a]`,alpha 照原样用 —— 着色器里它是「这一项施加多少」的权重,不是透明度。
export function setLightColor(materials, c) {
  setGlobal(materials, 'lightColor', (u) => u.value.set(c[0], c[1], c[2], c[3] ?? 1));
}
export function setShadeColors(materials, skin, body) {
  setGlobal(materials, 'skinShade', (u) => u.value.set(skin[0], skin[1], skin[2], skin[3] ?? 1));
  setGlobal(materials, 'bodyShade', (u) => u.value.set(body[0], body[1], body[2], body[3] ?? 1));
}
// Light direction from azimuth and elevation:
// L = (cos(xz)·cos(y), sin(y), sin(xz)·cos(y)).
export function lightDirFromAngles(angleXZDeg, angleYDeg) {
  const a = angleXZDeg * Math.PI / 180, b = angleYDeg * Math.PI / 180;
  return [Math.cos(a) * Math.cos(b), Math.sin(b), Math.sin(a) * Math.cos(b)];
}
export function setLightDir(materials, v) {
  setGlobal(materials, 'lightDir', (u) => u.value.set(v[0], v[1], v[2]));
}
export function setHeadPos(materials, v3) {
  setGlobal(materials, 'headPos', (u) => u.value.copy(v3));
}

export function setDebugMode(materials, mode) {
  setGlobal(materials, 'debugMode', (u) => { u.value = mode | 0; });
}
