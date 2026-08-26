// Stage 的角色装配：**着色本身一行都不自己写**，全部来自 `../viewer/` 的现成模块。
//
//   ../viewer/shading.js   —— toon 材质（体/眼/口三种角色 + 眉眼透发 overlay + 光照接口）
//   ../viewer/facial.js    —— 表情表与眨眼/口型状态机（驱动材质的 uvOffset）
//   ../viewer/cloth.js     —— rig 的布料链求解
//   ../viewer/segments.js  —— 动作分段 S→L→E 的衔接（本页由演出时间轴驱动时不接管起播）
//
// 为什么这层要另写而不是直接 import viewer 的装配函数：viewer.js 的 `loadUnit()` 没有导出，
// 而且它整段与 viewer 自己的 DOM（HUD、列表、开关、自检面板）绑在一起，import 进来就把
// 另一个域的界面一起拖进本页。所以这里只**照抄它的材质分派口径**（`ROLE_DEFAULTS` /
// `classifyRole` / `extraOf` / `texIndex` 四小段，见 viewer.js 的 loadUnit 材质分派段），
// 着色实现一律调用 shading.js，不复制着色代码。

import * as THREE from 'three';
import * as Shading from '../viewer/shading.js';
import * as Facial from '../viewer/facial.js';
import * as Cloth from '../viewer/cloth.js';
import * as Seg from '../viewer/segments.js';

export { Seg };

// —— 以下四小段与 viewer.js loadUnit 的材质分派口径一致（同一份契约，别各写一份）——
const ROLE_DEFAULTS = {
  body: { usage: 1, override: 1, intensity: 1, threshold: 0.01, smoothness: 0.05 },
  eye: { usage: 0, override: 0, intensity: 1, threshold: 0, smoothness: 0, eyebrowAlpha: 0.4, eyebrowClip: 0.5 },
  mouth: { usage: 0, override: 0, intensity: 1, threshold: 0, smoothness: 0, eyebrowAlpha: 1, eyebrowClip: 0.5 },
};

function extraOf(userData, aliases) {
  if (!userData) return undefined;
  const lower = {};
  for (const key of Object.keys(userData)) lower[key.toLowerCase()] = key;
  for (const alias of aliases) {
    if (userData[alias] !== undefined) return userData[alias];
    const hit = lower[alias.toLowerCase()];
    if (hit !== undefined) return userData[hit];
  }
  return undefined;
}

const texIndex = (value) => (typeof value === 'number'
  ? value
  : (value && typeof value.index === 'number' ? value.index : null));

function classifyRole(materialName, meshName, primIndex) {
  const name = (materialName || '').toLowerCase();
  if (name.includes('eye')) return 'eye';
  if (name.includes('mouth')) return 'mouth';
  if (name.includes('body')) return 'body';
  if (name.includes('face_sub0')) return 'eye';
  if (name.includes('face_sub1')) return 'mouth';
  const mesh = (meshName || '').toLowerCase();
  if (mesh.includes('face')) return primIndex === 0 ? 'eye' : 'mouth';
  return 'body';
}
// —— 照抄段结束 ——

/**
 * 装配一个角色：材质换成 viewer 的 toon 着色，接上表情、布料、眉眼 overlay。
 * 返回的 `record` 交给 `updateCharacter()` 逐帧推进。
 */
export async function buildCharacter({ gltf, rigRaw, tables, unit, stencilIndex = 0 }) {
  const root = gltf.scene;
  root.updateMatrixWorld(true);                     // 绑定姿态：布料取 rest 位置要用
  const parser = gltf.parser;
  const rig = rigRaw ? Cloth.normalizeRig(rigRaw) : null;
  const stencilRef = Shading.stencilRefFor(stencilIndex);

  const meshes = [];
  root.traverse((object) => {
    if (object.isSkinnedMesh || object.isMesh) {
      object.frustumCulled = false;
      meshes.push(object);
    }
  });

  const primIndexByMesh = new Map();
  {
    const counters = new Map();
    for (const mesh of meshes) {
      const group = mesh.parent && mesh.parent.isGroup ? mesh.parent.name : mesh.name;
      const index = counters.get(group) || 0;
      primIndexByMesh.set(mesh, index);
      counters.set(group, index + 1);
    }
  }

  const mats = [];
  const extrasFound = new Set();
  let eyeMesh = null;
  let mouthMesh = null;
  let eyeParams = null;
  let replaced = 0;

  for (const mesh of meshes) {
    const old = mesh.material;
    const role = classifyRole(
      old?.name,
      mesh.parent && mesh.parent.isGroup ? mesh.parent.name : mesh.name,
      primIndexByMesh.get(mesh),
    );
    const dflt = ROLE_DEFAULTS[role];
    const userData = old?.userData || {};
    for (const key of Object.keys(userData)) extrasFound.add(key);

    let usage = extraOf(userData, ['characterShaderUsage', 'shaderUsage', '_CharacterShaderUsage']);
    if (usage === undefined) {
      const raw = extraOf(userData, ['usage']);
      usage = typeof raw === 'string' ? (raw.toLowerCase() === 'body' ? 1 : 0) : raw;
    }
    usage = usage ?? dflt.usage;
    const shading = extraOf(userData, ['shading']) || {};
    const override = shading.override
      ?? extraOf(userData, ['overrideShadingParameter', '_OverrideShadingParameter', 'override']) ?? dflt.override;
    const intensity = shading.intensity
      ?? extraOf(userData, ['localBodyShadingIntensity', '_LocalBodyShadingIntensity', 'shadingIntensity', 'intensity']) ?? dflt.intensity;
    const threshold = shading.edgeThreshold
      ?? extraOf(userData, ['localBodyShadingEdgeThreshold', '_LocalBodyShadingEdgeThreshold', 'shadingEdgeThreshold', 'threshold']) ?? dflt.threshold;
    const smoothness = shading.edgeSmoothness
      ?? extraOf(userData, ['localBodyShadingEdgeSmoothness', '_LocalBodyShadingEdgeSmoothness', 'shadingEdgeSmoothness', 'smoothness']) ?? dflt.smoothness;
    const brightness = extraOf(userData, ['brightness', '_Brightness']) ?? 1;

    let bodyMaskTex = null;
    const maskIndex = texIndex(extraOf(userData, [
      'bodyMaskTexture', 'bodyMaskTex', 'bodyMask', 'bodyMaskTexIndex', '_BodyMaskTex', 'maskTex', 'mask',
    ]));
    if (maskIndex !== null && parser) {
      try { bodyMaskTex = await parser.getDependency('texture', maskIndex); } catch { /* 缺就用黑图 */ }
    }

    const material = Shading.makeCharacterMaterial({
      name: `${old?.name || role}·toon`,
      mainTex: old?.map || null,
      bodyMaskTex,
      usage: +usage,
      override: +override,
      intensity: +intensity,
      threshold: +threshold,
      smoothness: +smoothness,
      brightness: +brightness,
      stencilRef,
    });
    // 判据要数「贴图有没有真的上链」：着色器采样的是 uniforms.mainTex，这里把同一个
    // 纹理对象也挂到 material.map 上，让「材质有 map」与「着色器在采样它」是同一件事。
    material.map = material.uniforms.mainTex.value || null;
    material.userData.stageShading = 'viewer-toon';
    material.userData.stageRole = role;
    mesh.material = material;
    mats.push(material);
    replaced += 1;

    if (role === 'eye') {
      eyeMesh = mesh;
      let eyebrowTex = null;
      const eyebrowIndex = texIndex(extraOf(userData, [
        'eyebrowTexture', 'eyebrowTex', 'eyeMaskTex', 'eyebrowTexIndex', '_EyebrowTex', 'eyeMask',
      ]));
      if (eyebrowIndex !== null && parser) {
        try { eyebrowTex = await parser.getDependency('texture', eyebrowIndex); } catch { /* 缺就恒 discard */ }
      }
      eyeParams = {
        mainTex: old?.map || null,
        tex: eyebrowTex,
        clip: +(extraOf(userData, ['eyebrowClip', '_EyebrowClip']) ?? dflt.eyebrowClip),
        alpha: +(extraOf(userData, ['eyebrowAlpha', '_EyebrowAlpha']) ?? dflt.eyebrowAlpha),
        usage: +usage,
        brightness: +brightness,
      };
    }
    if (role === 'mouth') mouthMesh = mesh;
  }

  // 眉眼透发 overlay：只有 eye 建（mouth/body 的 mask 默认黑，恒 discard，建了也是空跑）
  let overlay = null;
  if (eyeMesh && eyeParams) {
    const overlayMaterial = Shading.makeCharacterMaterial({
      name: 'eye·eyebrow-overlay',
      mainTex: eyeParams.mainTex,
      bodyMaskTex: null,
      usage: eyeParams.usage,
      override: 0,
      intensity: 1,
      threshold: 0,
      smoothness: 0,
      brightness: eyeParams.brightness,
      stencilRef,
      eyebrow: { tex: eyeParams.tex, clip: eyeParams.clip, alpha: eyeParams.alpha },
    });
    overlayMaterial.map = overlayMaterial.uniforms.mainTex.value || null;
    overlayMaterial.userData.stageShading = 'viewer-toon';
    overlayMaterial.userData.stageRole = 'eyebrow';
    overlay = eyeMesh.clone();
    overlay.material = overlayMaterial;
    overlay.name = 'eye_eyebrow_overlay';
    overlay.renderOrder = 1000;
    overlay.frustumCulled = false;
    if (overlay.isSkinnedMesh) overlay.bind(eyeMesh.skeleton, eyeMesh.bindMatrix);
    eyeMesh.parent.add(overlay);
    mats.push(overlayMaterial);
  }

  // 头部参考点（球面项的圆心）——契约里的局部偏移量在 avatar 空间量出
  const headWorld = Shading.HEAD_LOCAL.clone().applyMatrix4(root.matrixWorld);
  const headAnchor = root.getObjectByName('EX_IK_joint') || root.getObjectByName('Head') || root;
  const headOffset = headAnchor.worldToLocal(headWorld.clone());
  Shading.setHeadPos(mats, headWorld);
  Shading.setNight(mats, false);
  Shading.setLightDir(mats, Shading.LIGHT_DIR);

  // 布料
  let cloth = null;
  if (rig && rig.chains.length) {
    const nodeByIndex = [];
    if (parser && parser.associations) {
      for (const [object, association] of parser.associations) {
        if (association && association.nodes !== undefined) nodeByIndex[association.nodes] = object;
      }
    }
    cloth = new Cloth.ClothSystem(root, rig, nodeByIndex);
  }

  // 表情：默认脸 + 眨眼；对话步骤驱动的口型/眼型见 stage.js 的 `updateTalkFacial`。
  //
  // 演出**时间轴**上的 `ChangeEyePreset` / `ChangeLipSyncPreset` clip 至今不接,原因要说准:
  // 不是资产里没有预设名——`ChangeLipSyncPresetClip.LipSyncDataList` 里整张表都在
  // (实测逐行 `{Name:"smile01", Open:7, Middle:-1, Close:5}`),`ChangeEyePresetClip.EyeDataList`
  // 同样。**是我们的产物没有提取 clip asset 的字段**:逐类比对 32879 个 MonoBehaviour 后,
  // 这两类各 3060 / 8067 个对象、值字段 3 个、产物携带 **0**。
  //
  // ⇒ 这是**提取缺口**,不是游戏侧缺口。两者排产方向相反,所以措辞不能含糊:
  // 「产物里没有」写成「资产里没有」会让人去找一个不存在的游戏侧缺陷。
  const unitId = (rig && rig.unitId) || (unit >= 100 ? unit - 100 : unit);
  const { eyeRow, lipRow } = Facial.patternsFor(
    unitId, tables, rig ? { eye: rig.defaultEye, mouth: rig.defaultMouth } : null,
  );
  const eyeCell = Facial.cellFromAtlas(rig && rig.eyeAtlas, Facial.EYE_CELL);
  const mouthCell = Facial.cellFromAtlas(rig && rig.mouthAtlas, Facial.MOUTH_CELL);
  const facial = new Facial.FacialController({
    applyEye: (index) => {
      const offset = Facial.cellOffset(index, eyeCell);
      if (eyeMesh) eyeMesh.material.uniforms.uvOffset.value.set(offset.x, offset.y);
      if (overlay) overlay.material.uniforms.uvOffset.value.set(offset.x, offset.y);
    },
    applyMouth: (index) => {
      const offset = Facial.cellOffset(index, mouthCell);
      if (mouthMesh) mouthMesh.material.uniforms.uvOffset.value.set(offset.x, offset.y);
    },
  });
  facial.setPatterns(eyeRow, lipRow);
  facial.setBlinkEnabled(true);

  return {
    unit, unitId, root, mats, overlay, eyeMesh, mouthMesh, facial, cloth, rig,
    headAnchor, headOffset, headWorld, stencilRef,
    counts: {
      meshes: meshes.length, replaced, materials: mats.length,
      clothChains: cloth ? cloth.chains.length : 0,
      extras: [...extrasFound].slice(0, 8),
    },
  };
}

const _head = new THREE.Vector3();

/** 逐帧推进（顺序照 viewer 的帧循环：mixer 之后复位布料 rest → 更新矩阵 → 头部 → solver → 表情）。 */
export function updateCharacter(record, dt, { cloth = true, onHead = null } = {}) {
  if (!record) return null;
  if (cloth && record.cloth) record.cloth.restoreRest();
  record.root.updateMatrixWorld(true);
  if (record.headAnchor) {
    _head.copy(record.headOffset);
    record.headAnchor.localToWorld(_head);
    Shading.setHeadPos(record.mats, _head);
    record.headWorld.copy(_head);
    if (onHead) onHead(_head);
  }
  if (cloth && record.cloth) record.cloth.step(dt);
  record.facial.update();
  return record.headWorld;
}

export function disposeCharacter(record) {
  if (!record) return;
  record.overlay?.removeFromParent();
  record.root.traverse((object) => { object.geometry?.dispose?.(); });
  for (const material of record.mats) {
    for (const key of Object.keys(material.uniforms || {})) {
      const value = material.uniforms[key].value;
      if (value && value.isTexture) value.dispose();
    }
    material.dispose();
  }
}
