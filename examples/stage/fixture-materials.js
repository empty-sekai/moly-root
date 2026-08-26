// 家具材质：走 `../viewer/environment.js` 的 `makeSurfaceMaterial` —— 游戏为世界表面发货的
// 那八套程序，站点侧与家具侧**同一份实现**。
//
// 为什么必须是它，而不是角色卡通链：家具主着色器 `Mysekai/Fixture/Basic` 的编译产物里
// 声明着 `_GlobalPhenomenaShadeColor` 与 `_GlobalPhenomenaDirectionalLightColor`（实测 13 个
// 全局量，读自游戏自己的 blob），并由材质属性 `_UsePhenomenaLighting` 门控 —— 家具族 1805 个
// 材质里 1239 个把这个开关开着。**角色卡通链里根本没有这个概念**，所以那 1239 个开关对它
// 完全不起作用，家具于是永远是平的。改道之后这条现象光照通道才第一次通电。
//
// 那张全局量表里**没有** `_MysekaiFixtureShadeColor`，与「它在代码里被硬编码成 1.0f 白」是
// 两条独立路径得出的同一结论：这条路径上家具本来就没有专属暗部色。
//
// 三种材质角色：
//   * 着色器名命中八套之一 → 该程序（家具本体多为 `Fixture/Basic`，阴影贴片为 `Fixture/ShadowMesh`）。
//   * 命中不了 → 工厂的回落程序，**并计数**（`isFallback`）。回落不是错误，但必须是个数字。
//   * 阴影贴片额外压一层 polygonOffset 与 renderOrder —— 那是深度排序,不是着色律,
//     所以它留在消费侧而不是塞进程序里。

import * as THREE from 'three';
import { makeSurfaceMaterial, surfaceProgramForShader } from '../viewer/environment.js';

const SHADOW_MESH_SHADER = 'Mysekai/Fixture/ShadowMesh';

/** 材质记下来的着色器名,没有就返回空串（旧产物没有这个字段）。 */
function shaderNameOf(material) {
  const value = (material?.userData || {}).shader;
  if (typeof value === 'string') return value;
  // 未解析的指针记成对象（带 status/reason）,那不是名字,按「没有名字」处理。
  return '';
}

/**
 * 这个材质是不是阴影贴片。
 * 有着色器名 ⇒ 按名字判（权威）；没有 ⇒ 退回后缀猜法,**并告诉调用方这是猜的**,让它计数。
 */
function shadowDecalDecision(material, mesh) {
  const shader = shaderNameOf(material);
  if (shader) return { decal: shader === SHADOW_MESH_SHADER, guessed: false, shader };
  const materialName = String(material?.name || '');
  const meshName = String(mesh?.name || '');
  const guess = /_shadow$/i.test(materialName)
    || /(^|_)shadow$/i.test(meshName) || meshName === 'mdl_shadow';
  return { decal: guess, guessed: true, shader: '' };
}

/**
 * glb 材质的 extras → 工厂要的记录形状。
 * 站点记录用 `shader.name`，家具产物里 `shader` 直接是字符串，所以这里补一层，
 * **不改产物**：产物的形状由提取侧的判据管着，消费侧迁就它。
 */
function recordFrom(extras, shaderName) {
  return {
    shader: { name: shaderName },
    floats: extras.floats || {},
    colors: extras.colors || {},
    textures: extras.textures || {},
    textureScaleOffset: extras.textureScaleOffset || {},
    keywords: extras.keywords || [],
  };
}

/**
 * 把一棵家具 prefab 的材质全部换成八套程序之一。
 * `root` 应当是 `gltf.scene`（默认场景 = 带挂点的那份 prefab）。
 */
export function applyFixtureMaterials(root, { stencilIndex = 1 } = {}) {
  const report = {
    meshes: 0, toon: 0, decal: 0, textured: 0, untextured: 0,
    alphaClip: 0, leftDefault: 0, materials: [],
    byShader: {}, routedByName: 0, guessedByName: 0,
    unmatchedShaders: {},
    // 改道后的新账：真正用上的程序族、回落条数、以及程序问了但产物没给的贴图槽。
    byFamily: {}, fallback: 0, emissive: 0,
    slotsRequested: 0, slotsMissing: {},
  };
  if (!root) return report;
  root.traverse((mesh) => {
    if (!mesh.isMesh && !mesh.isSkinnedMesh) return;
    report.meshes += 1;
    mesh.frustumCulled = false;
    const old = mesh.material;
    const extras = old?.userData || {};

    const decision = shadowDecalDecision(old, mesh);
    if (decision.shader) {
      report.routedByName += 1;
      report.byShader[decision.shader] = (report.byShader[decision.shader] || 0) + 1;
      if (!surfaceProgramForShader(decision.shader)) {
        report.unmatchedShaders[decision.shader] =
          (report.unmatchedShaders[decision.shader] || 0) + 1;
      }
    } else {
      report.guessedByName += 1;
    }

    // 程序要哪一槽的贴图就问哪一槽。家具 glb 只把 `_MainTex` 绑成 baseColorTexture,
    // 其余槽产物里没有对应的 three 纹理对象 —— 返回 null 让工厂用产物给的默认语义,
    // **并把「问了没有」逐槽记下来**,免得「没贴图」在账上看不出是产物缺还是没接。
    const ctx = {
      texture: (rec, slot) => {
        report.slotsRequested += 1;
        if (slot === '_MainTex' && old?.map) return old.map;
        if ((rec?.textures || {})[slot] !== undefined) {
          report.slotsMissing[slot] = (report.slotsMissing[slot] || 0) + 1;
        }
        return null;
      },
      gltfMap: old?.map || null,
      gltfBaseColor: [old?.color?.r ?? 1, old?.color?.g ?? 1, old?.color?.b ?? 1,
                      old?.opacity ?? 1],
      gltfAlphaTest: Number(old?.alphaTest) || 0,
      blackCube: null,
    };

    const shaderName = decision.shader || '';
    const built = makeSurfaceMaterial({
      shaderName,
      record: recordFrom(extras, shaderName),
      ctx,
      geometry: mesh.geometry,
      source: old,
    });
    const material = built.material;
    material.userData.stageShading = built.isFallback ? 'surface-fallback' : 'surface-program';
    material.userData.stageRole = 'fixture';
    material.userData.stageFamily = built.family;
    material.userData.stageSourceMaterial = extras.sourceMaterial || old?.name || '';
    report.byFamily[built.family] = (report.byFamily[built.family] || 0) + 1;
    if (built.isFallback) report.fallback += 1;
    if (built.emissive) report.emissive += 1;

    if (decision.decal || built.family === 'shadowMesh') {
      // 深度排序,不是着色律:贴片与地面同面,不压一下会闪。
      material.polygonOffset = true;
      material.polygonOffsetFactor = -2;
      material.polygonOffsetUnits = -2;
      material.depthWrite = false;
      mesh.renderOrder = -1;
      report.decal += 1;
    } else {
      report.toon += 1;
    }
    if (Number(extras.alphaClip) > 0) report.alphaClip += 1;
    // 「有贴图」按程序真的会采到的那张算:`_MainTex` 槽拿到了东西才算。
    if (old?.map) report.textured += 1; else report.untextured += 1;
    mesh.material = material;
    report.materials.push(material);
  });
  return report;
}

/** 场景图取证用：某棵树上「走程序 / 回落 / 仍是默认材质」各多少。 */
export function classifyMaterials(root) {
  const counts = { viewerToon: 0, decal: 0, leftDefault: 0, withMap: 0, meshes: 0, defaults: [] };
  if (!root) return counts;
  root.traverse((mesh) => {
    if (!mesh.isMesh && !mesh.isSkinnedMesh) return;
    counts.meshes += 1;
    const list = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const material of list) {
      if (!material) continue;
      const tag = material.userData?.stageShading;
      if (tag === 'viewer-toon' || tag === 'surface-program') counts.viewerToon += 1;
      else if (tag === 'shadow-decal' || tag === 'surface-fallback') counts.decal += 1;
      else {
        counts.leftDefault += 1;
        if (counts.defaults.length < 8) counts.defaults.push(`${mesh.name}/${material.name || material.type}`);
      }
      if (material.map || material.uniforms?.mMainTex?.value) counts.withMap += 1;
    }
  });
  return counts;
}

/**
 * 现象环境接管时家具吃哪一路光。
 * 改道之后**这里不需要做任何事**：八套程序的 uniform 由 `withEnvGlobals` 铺进去,
 * 环境层每帧往同一组 uniform 对象写一次,家具材质按引用共享,写一次就等于推给了它们。
 * 保留这个函数只为调用方不必改；返回 false 表示「本层无需接管」。
 */
export function applyPhenomenaLight() {
  return false;
}

