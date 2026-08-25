// 演出动画上链：按**节点路径**把 glTF 动画通道绑到真正的目标节点。
//
// 为什么不能靠名字绑：
//   * 演出动画包与几何包是两个文件，GLTFLoader 会给重名节点补 `_1` `_2` 后缀去重，
//     两个文件的去重编号互不相干 —— 按 Object3D 名字对表必然错位。
//   * 一个家具包里 `mdl_<件名>` 这样的名字出现三四次（不同 prefab 变体各一份），
//     `getObjectByName` 只会给第一个命中，于是「动了，但动的是别的节点」。
//   * 演出动画的通道有两个归属：本包节点（native）与从角色骨架借来的节点
//     （`nodes[].extras.foreign`，kind = `character-rig`，path 是作者态骨路径）。
//     两者要落到两个不同的目标树上。
//
// 所以这里一律走「作者态路径 → 目标树节点」：
//   1. 源包：从 glTF json 的 `nodes[].name` + `children` 数出每个节点的场景路径；
//      带 `extras.foreign.path` 的节点用它给的作者态路径覆盖。
//   2. 目标树：同样从 json 数路径，再用 `parser.associations` 把路径挂到真正的 Object3D 上。
//   3. 匹配：取**最长且唯一**的路径后缀。后缀多于一个候选 = 歧义，不绑；
//      只剩根节点能匹配（`matched < total` 且深度为 0）= 兜底，**拒绝绑定**并计数。
//   4. 绑上之后把轨改名成 `<目标 uuid>.<属性>` —— uuid 在 PropertyBinding.findNode 里
//      与名字同权且天然唯一，一条轨只会落到一个节点上。
//
// 本模块是纯函数 + 报告，stage.js 与 selfcheck.js 共用同一套计数，判据与运行时不会各说一套。

import * as THREE from 'three';

/** glb 的 JSON 块（只解 JSON，不解二进制）。glTF 2.0 规定第一块必须是 JSON。 */
export function glbJson(buffer) {
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== 0x46546c67) throw new Error('不是 glb（magic 不符）');
  const length = view.getUint32(12, true);
  if (view.getUint32(16, true) !== 0x4e4f534a) throw new Error('glb 第一块不是 JSON');
  return JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 20, length)));
}

/** 节点下标 → { path, scene, kind, pkg }。foreign 节点用作者态路径覆盖场景路径。 */
export function nodePathIndex(json) {
  const nodes = json?.nodes || [];
  const nameOf = (index) => nodes[index]?.name || `node${index}`;
  const pathOf = new Map();
  const walk = (index, prefix, scene) => {
    const path = prefix ? `${prefix}/${nameOf(index)}` : nameOf(index);
    if (!pathOf.has(index)) pathOf.set(index, { path, scene });
    for (const child of nodes[index]?.children || []) walk(child, path, scene);
  };
  (json?.scenes || []).forEach((entry, sceneIndex) => {
    for (const root of entry?.nodes || []) walk(root, '', sceneIndex);
  });
  nodes.forEach((definition, index) => {
    const base = pathOf.get(index) || { path: nameOf(index), scene: -1 };
    const foreign = definition?.extras?.foreign;
    pathOf.set(index, foreign?.path
      ? { ...base, path: foreign.path, kind: foreign.kind || 'foreign', pkg: foreign.package || '' }
      : { ...base, kind: 'native', pkg: '' });
  });
  return pathOf;
}

/** glTF 节点下标 → Object3D（GLTFLoader 自己维护的 associations 是唯一可靠出处）。 */
export function nodeObjectsOf(gltf) {
  const objects = [];
  const associations = gltf?.parser?.associations;
  if (!associations) return objects;
  for (const [object, association] of associations) {
    if (association && association.nodes !== undefined && objects[association.nodes] === undefined) {
      objects[association.nodes] = object;
    }
  }
  return objects;
}

/**
 * 目标树的后缀索引。`sceneIndex` < 0 表示不限场景（角色包只有一个场景）。
 * `depthOf` 记每个节点在场景路径里的深度，根节点是 0 —— 拒绝兜底要用它。
 */
export function suffixIndex(json, nodeObjects, sceneIndex = -1) {
  const pathOf = nodePathIndex(json);
  const bySuffix = new Map();
  const depthOf = new Map();
  let nodes = 0;
  for (const [index, record] of pathOf) {
    if (sceneIndex >= 0 && record.scene !== sceneIndex) continue;
    const object = nodeObjects[index];
    if (!object) continue;
    const segments = record.path.split('/');
    depthOf.set(object, segments.length - 1);
    nodes += 1;
    for (let take = 1; take <= segments.length; take += 1) {
      const suffix = segments.slice(segments.length - take).join('/');
      if (!bySuffix.has(suffix)) bySuffix.set(suffix, []);
      const bucket = bySuffix.get(suffix);
      if (!bucket.includes(object)) bucket.push(object);
    }
  }
  return { bySuffix, depthOf, nodes };
}

/** 最长且唯一的路径后缀匹配。绑不上就说清为什么，不给兜底目标。 */
export function resolvePath(index, path) {
  const segments = String(path || '').split('/').filter(Boolean);
  if (!index || !segments.length) return { object: null, reason: 'empty-path', matched: 0, total: segments.length };
  for (let take = segments.length; take >= 1; take -= 1) {
    const suffix = segments.slice(segments.length - take).join('/');
    const hits = index.bySuffix.get(suffix);
    if (!hits || !hits.length) continue;
    if (hits.length > 1) {
      return { object: null, reason: `ambiguous:${suffix}`, matched: take, total: segments.length };
    }
    const object = hits[0];
    if (take < segments.length && (index.depthOf.get(object) || 0) === 0) {
      return { object: null, reason: `root-fallback-refused:${suffix}`, matched: take, total: segments.length };
    }
    return { object, matched: take, total: segments.length, exact: take === segments.length };
  }
  return { object: null, reason: 'no-path-match', matched: 0, total: segments.length };
}

function emptyReport() {
  return {
    channels: 0, bound: 0, viaSuffix: 0, rootFallback: 0, ambiguous: 0,
    unbound: [], clips: new Map(), boundObjects: new Set(), emptyClips: [],
  };
}

/**
 * 把演出动画包的剪辑改绑到目标树。
 * `resolvers` 是 `{ native: suffixIndex, 'character-rig': suffixIndex, ... }`。
 */
export function retargetClips({ clips, json, nodeObjects, resolvers, want = null }) {
  const report = emptyReport();
  const pathOf = nodePathIndex(json);
  const indexByName = new Map();
  nodeObjects.forEach((object, index) => {
    if (object && object.name && !indexByName.has(object.name)) indexByName.set(object.name, index);
  });
  for (const clip of clips || []) {
    if (want && !want.has(clip.name)) continue;
    const tracks = [];
    for (const track of clip.tracks || []) {
      report.channels += 1;
      const dot = track.name.lastIndexOf('.');
      const nodeName = dot < 0 ? track.name : track.name.slice(0, dot);
      const property = dot < 0 ? '' : track.name.slice(dot + 1);
      const nodeIndex = indexByName.get(nodeName);
      const record = nodeIndex === undefined ? null : pathOf.get(nodeIndex);
      const resolver = record ? (resolvers[record.kind] || null) : null;
      const hit = record && resolver
        ? resolvePath(resolver, record.path)
        : { object: null, reason: record ? `no-resolver:${record.kind}` : 'unknown-track-node' };
      if (!hit.object) {
        report.unbound.push({
          clip: clip.name, path: record?.path || nodeName, kind: record?.kind || '?',
          property, reason: hit.reason,
        });
        if (String(hit.reason).startsWith('root-fallback')) report.rootFallback += 1;
        if (String(hit.reason).startsWith('ambiguous')) report.ambiguous += 1;
        continue;
      }
      if (!hit.exact) report.viaSuffix += 1;
      const retargeted = track.clone();
      retargeted.name = `${hit.object.uuid}.${property}`;
      tracks.push(retargeted);
      report.bound += 1;
      report.boundObjects.add(hit.object);
    }
    if (tracks.length) {
      report.clips.set(clip.name, new THREE.AnimationClip(clip.name, clip.duration, tracks, clip.blendMode));
    } else {
      report.emptyClips.push(clip.name);
    }
  }
  return report;
}

/**
 * 共享动作库的剪辑：库本身就是按人形骨**名**烘焙的（viewer 用的就是这条路），
 * 所以按名字取节点；取到之后同样改成 uuid 绑定，免得家具树里也有 `Hips` 这种同名骨来抢。
 */
export function retargetByName({ clips, root, want = null }) {
  const report = emptyReport();
  for (const clip of clips || []) {
    if (want && !want.has(clip.name)) continue;
    const tracks = [];
    for (const track of clip.tracks || []) {
      report.channels += 1;
      const dot = track.name.lastIndexOf('.');
      const nodeName = dot < 0 ? track.name : track.name.slice(0, dot);
      const property = dot < 0 ? '' : track.name.slice(dot + 1);
      const object = root ? root.getObjectByName(nodeName) : null;
      if (!object) {
        report.unbound.push({ clip: clip.name, path: nodeName, kind: 'motion-library', property, reason: 'no-bone' });
        continue;
      }
      const retargeted = track.clone();
      retargeted.name = `${object.uuid}.${property}`;
      tracks.push(retargeted);
      report.bound += 1;
      report.boundObjects.add(object);
    }
    if (tracks.length) {
      report.clips.set(clip.name, new THREE.AnimationClip(clip.name, clip.duration, tracks, clip.blendMode));
    } else {
      report.emptyClips.push(clip.name);
    }
  }
  return report;
}

export function mergeReports(reports) {
  const total = emptyReport();
  for (const report of reports) {
    if (!report) continue;
    total.channels += report.channels;
    total.bound += report.bound;
    total.viaSuffix += report.viaSuffix;
    total.rootFallback += report.rootFallback;
    total.ambiguous += report.ambiguous;
    total.unbound.push(...report.unbound);
    total.emptyClips.push(...report.emptyClips);
    for (const object of report.boundObjects) total.boundObjects.add(object);
  }
  return total;
}

/** clip 名里的挂点号：`act_cw_ex01_ext0001_fixture_sofa1_033_S` → `033`。 */
export function attachIdFromClipName(name) {
  const match = /_(\d{3})(?:_\d+)?_(?:S|L|E|O)$/.exec(String(name || ''))
    || /_(\d{3})(?:_\d+)?$/.exec(String(name || ''));
  return match ? match[1] : '';
}
