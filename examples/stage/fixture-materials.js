// 家具材质：同样用 `../viewer/shading.js` 那份 toon 着色，不另做一套。
//
// 为什么家具在没有这一层时会「连贴图都没有」：家具 glb 的 `COLOR_0` 不是颜色，是遮罩通道
// （实测某沙发件 R/G 有值、B 恒 0、A 恒 255）。GLTFLoader 见到 COLOR_0 就把 MeshStandardMaterial
// 的 `vertexColors` 打开，于是 baseColorTexture 被这组遮罩逐分量乘掉 —— 画面上就是「贴图没了」。
// viewer 的 toon 着色器根本不读 COLOR_0，换上它这一项自然消失。
//
// 三种材质角色：
//   * 有 `_MainTex` 的本体件 → viewer toon（usage=body）。`_Cull` 决定 side，`_AlphaClip` 用
//     onBeforeCompile 在 albedo 采样后补一句 discard（着色器源码不动，viewer 目录只读）。
//   * `mdl_shadow` 这类阴影贴片 → **不是本体**：它是 y=0 的一小片四边形，没有 _MainTex，
//     颜色全在 COLOR_0 里（实测 rgb=0、a=64/255）。所以按半透明贴片画：顶点色 + 透明混合 +
//     不写深度 + polygonOffset 压住与地面的同面冲突，绝不当实体，也不会盖住本体。
//   * 既没贴图也不是贴片的件 → 仍按 toon 画（白底），并单独计数，不假装它有贴图。

import * as THREE from 'three';
import * as Shading from '../viewer/shading.js';
import { surfaceProgramForShader } from '../viewer/environment.js';

// 家具本体按「身体」角色着色：与 viewer 的 body 默认档一致。
export const FIXTURE_TOON = { usage: 1, override: 1, intensity: 1, threshold: 0.01, smoothness: 0.05 };

const CULL_TO_SIDE = { 0: THREE.DoubleSide, 1: THREE.BackSide, 2: THREE.FrontSide };

const ALBEDO_ANCHOR = 'vec4 albedo = texture2D(mainTex, vUv);';

// 材质该走哪套程序,**按产物记下来的着色器名判**,不按材质名后缀猜。
//
// 后缀猜法（`/_shadow$/`）曾是这里唯一的依据,而那是三档判据里的红档:材质名的词缀
// 不是「谁来画这个面」的陈述。产物现在带着从资产里解出来的着色器名（实测六个包 17/17:
// `Mysekai/Fixture/Basic` 12 · `ShadowMesh` 2 · `Effect/UberUnlit` 2 · `Object` 1）,
// 所以判据升到第二档:**名 → 程序**的映射,且**未命中必须计数**。
//
// 未命中不给回落程序。`surfaceProgramForShader` 故意在未命中时返回 null 而不是近似程序——
// 回落会让「用了近似」在消费侧不留痕迹,于是覆盖账上出现假的「用上了」。要近似可以,
// 但必须由这里自己声明并计数。
const SHADOW_MESH_SHADER = 'Mysekai/Fixture/ShadowMesh';

/** 材质记下来的着色器名,没有就返回空串（旧产物没有这个字段）。 */
function shaderNameOf(material) {
  const extras = material?.userData || {};
  const value = extras.shader;
  if (typeof value === 'string') return value;
  // 未解析的指针记成对象（带 status/reason）,那不是名字,按「没有名字」处理。
  return '';
}

/**
 * 这个材质是不是阴影贴片。
 * 有着色器名 ⇒ 按名字判（权威）；没有 ⇒ 退回后缀猜法,**并告诉调用方这是猜的**,
 * 让它计数。旧产物仍能画出来,但「有多少是猜的」在界面上是个数字。
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

function attachAlphaClip(material, clip) {
  material.userData.stageAlphaClip = clip;
  material.userData.stageAlphaClipApplied = false;
  material.onBeforeCompile = (shader) => {
    if (!shader.fragmentShader.includes(ALBEDO_ANCHOR)) return;   // 锚点不在就不动，交由界面如实报
    shader.fragmentShader = shader.fragmentShader.replace(
      ALBEDO_ANCHOR,
      `${ALBEDO_ANCHOR}\n  if (albedo.a < ${clip.toFixed(6)}) discard;   // _AlphaClip`,
    );
    material.userData.stageAlphaClipApplied = true;
  };
  material.customProgramCacheKey = () => `fixture-alphaclip-${clip.toFixed(6)}`;
}

/**
 * 把一棵家具 prefab 的材质全部换成本层的三种角色之一。
 * `root` 应当是 `gltf.scene`（默认场景 = 带挂点的那份 prefab）。
 */
export function applyFixtureMaterials(root, { stencilIndex = 1 } = {}) {
  const stencilRef = Shading.stencilRefFor(stencilIndex);
  const report = {
    meshes: 0, toon: 0, decal: 0, textured: 0, untextured: 0,
    alphaClip: 0, leftDefault: 0, materials: [],
    // 路由账:按着色器名判的 / 名字缺失只能猜的 / 名字在而八套程序里没有的。
    // 第三项是「未命中」,它不是错误,但必须是个数字而不是沉默。
    byShader: {}, routedByName: 0, guessedByName: 0,
    unmatchedShaders: {},
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

    if (decision.decal) {
      const decal = new THREE.MeshBasicMaterial({
        name: `${old?.name || mesh.name}·shadow-decal`,
        color: 0xffffff,
        vertexColors: true,
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
        toneMapped: false,
      });
      decal.polygonOffset = true;
      decal.polygonOffsetFactor = -2;
      decal.polygonOffsetUnits = -2;
      decal.userData.stageShading = 'shadow-decal';
      mesh.material = decal;
      mesh.renderOrder = -1;
      report.decal += 1;
      report.materials.push(decal);
      return;
    }

    const material = Shading.makeCharacterMaterial({
      name: `${old?.name || mesh.name}·toon`,
      mainTex: old?.map || null,
      bodyMaskTex: null,                     // 家具没有身体遮罩：黑图 ⇒ mask=(0,0) ⇒ 走阶调支
      usage: FIXTURE_TOON.usage,
      override: FIXTURE_TOON.override,
      intensity: FIXTURE_TOON.intensity,
      threshold: FIXTURE_TOON.threshold,
      smoothness: FIXTURE_TOON.smoothness,
      brightness: 1,
      stencilRef,
    });
    // 着色器采样 uniforms.mainTex；把同一个纹理对象挂到 material.map，
    // 让「材质有 map」与「着色器在采样这张贴图」是同一件事（c6 判据要数它）。
    material.map = old?.map || null;
    if (material.map) material.uniforms.mainTex.value = material.map;
    material.userData.stageShading = 'viewer-toon';
    material.userData.stageRole = 'fixture';
    material.userData.stageSourceMaterial = extras.sourceMaterial || old?.name || '';
    const cull = extras.cullMode;
    if (cull !== null && cull !== undefined && CULL_TO_SIDE[+cull] !== undefined) {
      material.side = CULL_TO_SIDE[+cull];
    }
    const clip = extras.alphaClip;
    if (clip !== null && clip !== undefined && +clip > 0) {
      attachAlphaClip(material, +clip);
      report.alphaClip += 1;
    }
    mesh.material = material;
    report.toon += 1;
    if (material.map) report.textured += 1; else report.untextured += 1;
    report.materials.push(material);
  });
  return report;
}

/** 场景图取证用：某棵树上「viewer 着色 / 阴影贴片 / 仍是默认材质」各多少。 */
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
      if (tag === 'viewer-toon') counts.viewerToon += 1;
      else if (tag === 'shadow-decal') counts.decal += 1;
      else {
        counts.leftDefault += 1;
        if (counts.defaults.length < 8) counts.defaults.push(`${mesh.name}/${material.name || material.type}`);
      }
      if (material.map) counts.withMap += 1;
    }
  });
  return counts;
}

/** 现象环境接管时家具吃哪一路光：按 environment 的接线表，世界件走「现象方向光色/现象影色」。 */
export function applyPhenomenaLight(materials, light) {
  if (!light || !materials?.length) return false;
  const dir = light.dir;
  Shading.setLightDir(materials, [dir.x, dir.y, dir.z]);
  Shading.setLightColor(materials, light.phenLightColor);
  Shading.setShadeColors(materials, light.phenShadeColor, light.phenShadeColor);
  return true;
}
