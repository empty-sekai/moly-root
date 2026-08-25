// Stage 演出 demo：装配角色、家具挂点与演出动画，并**接上 viewer 那套渲染与动画**。
//
// 渲染与着色一律复用 `../viewer/` 的现成实现（只读那个目录，一行都不改）：
//   ../viewer/shading.js     角色/家具 toon 着色（由 character.js / fixture-materials.js 调用）
//   ../viewer/facial.js      表情表与眨眼状态机
//   ../viewer/cloth.js       布料链求解
//   ../viewer/segments.js    动作分段 S→L→E（本页由时间轴驱动，衔接口径来自它）
//   ../viewer/environment.js 现象环境（天空/地面/粒子/光照九项）+ 后处理链
//   ../viewer/GLTFLoader.js  ../viewer/OrbitControls.js
//
// 动画上链见 anim-bind.js：通道按**节点路径**绑到真实节点，绑不上的逐条报出，不兜底到根节点。

import * as THREE from 'three';
import { GLTFLoader } from '../viewer/GLTFLoader.js';
import { OrbitControls } from '../viewer/OrbitControls.js';
import * as Shading from '../viewer/shading.js';
import * as Facial from '../viewer/facial.js';
import { Environment, CROSS_FADE_SECONDS } from '../viewer/environment.js';
import { buildCharacter, updateCharacter } from './character.js';
import { applyFixtureMaterials, applyPhenomenaLight } from './fixture-materials.js';
import * as Bind from './anim-bind.js';
import {
  RECOMMENDED_PACKAGES,
  RECOMMENDATION_NOTE,
  ATTACH_POINTS_PATH,
  attachForTarget,
  assetUrl,
  fetchJson,
  fetchOptionalJson,
  findFixtureAnimation,
  findFixtureGeometry,
  loadPerformance,
  loadTrackCatalog,
} from './data.js';
import { runSelfcheck } from './selfcheck.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const loader = new GLTFLoader();

const state = {
  manifest: null,
  catalog: null,
  attachDoc: null,
  tables: null,
  performance: null,
  character: null,          // character.js 的 record
  characterGltf: null,
  fixture: null,            // { root, gltf, json, nodeObjects, package, report }
  lanes: [],
  lane: null,
  clipsByKey: new Map(),    // `${pkg}::${clipName}` → 已改绑的 AnimationClip
  loopByName: new Map(),
  binding: null,            // 合并后的上链报告
  packages: new Map(),      // pkg → { gltf, json, nodeObjects, index }
  packageIndex: new Map(),  // pkg → index.json（可播性来源）
  motion: null,            // 共享动作库 { clips, loop }
  motionState: '未加载',
  mixer: null,
  activeActions: new Map(),
  playing: false,
  timelineTime: 0,
  playbackRate: 1,
  totalDuration: 0,
  lastFrame: 0,
  runToken: 0,
  mode: 'spontaneous',
  unplayable: [],
  env: { environment: null, on: false, error: '', failed: false, name: '' },
};

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14151c);
const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
// stencil：眉眼透发 overlay 用模板等值测试，没有模板缓冲它就画不出来（与 viewer 同一份要求）。
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, stencil: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.LinearSRGBColorSpace;   // gamma 直通，与 viewer 的采样口径一致
renderer.toneMapping = THREE.NoToneMapping;
$('viewport').appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.enablePan = true;
scene.add(camera);        // 相机族环境粒子挂在相机下，相机必须在场景图里（与 viewer 同一要求）
const grid = new THREE.GridHelper(8, 16, 0x3a5561, 0x1c2c34);
scene.add(grid);
// 演员组：家具与角色都挂在这里，改绑后的动画轨用 uuid 定位，绑定不会跨树串味。
const actors = new THREE.Group();
actors.name = 'stage_actors';
const fixtureMount = new THREE.Group();
fixtureMount.name = 'stage_fixture_mount';
actors.add(fixtureMount);
scene.add(actors);

function setStatus(text, tone = '') {
  const element = $('stage-status');
  element.textContent = text;
  element.className = tone;
}

function setGeometryWarning(visible, text = '家具几何未就绪') {
  const warning = $('geometry-warning');
  warning.hidden = !visible;
  warning.textContent = text;
}

function setText(id, value) {
  const element = $(id);
  if (element) element.textContent = value;
}

function updateFacts() {
  const performance = state.performance;
  const entry = performance?.entry;
  setText('package-name', entry?.package || '—');
  setText('timeline-name', entry?.timeline || '—');
  setText('fixture-name', performance?.fixtureTarget?.targetPackage || '—');
  const attach = performance?.attach;
  setText('attach-name', attach?.entry
    ? `${attach.entry.start?.name || '?'}${attach.matchedById ? '（按 clip 号命中）' : '（回退第一条）'}`
    : (performance?.fixtureTarget ? '未命中' : '无家具目标'));
  setText('clip-count', String(performance?.clipCount || 0));
  setText('timeline-time', `${state.timelineTime.toFixed(2)} / ${state.totalDuration.toFixed(2)} s`);
}

function updateRenderFacts() {
  const fixture = state.fixture?.report;
  const character = state.character?.counts;
  setText('material-facts', fixture
    ? `家具 ${fixture.toon} toon / ${fixture.decal} 阴影贴片 / 有贴图 ${fixture.textured} · 角色 ${character?.replaced ?? 0} toon（布料链 ${character?.clothChains ?? 0}）`
    : `家具未装载 · 角色 ${character?.replaced ?? 0} toon`);
  const binding = state.binding;
  setText('binding-facts', binding
    ? `通道 ${binding.channels} · 按路径绑上 ${binding.bound} · 绑不上 ${binding.unbound.length}`
      + `（后缀命中 ${binding.viaSuffix} · 歧义 ${binding.ambiguous} · 拒绝根兜底 ${binding.rootFallback}）`
    : '—');
  const lane = state.lane;
  setText('playable-facts', lane
    ? `本轨 clip ${lane.events.length} · 可播 ${lane.playable} · 不可播 ${lane.unplayable}`
      + (lane.nullTargets ? ` · 空动画段 ${lane.nullTargets}` : '')
    : '—');
  const detail = $('binding-detail');
  if (detail) {
    const rows = [];
    if (binding?.unbound.length) {
      rows.push(`绑不上的通道 ${binding.unbound.length} 条（逐条路径）：`);
      for (const item of binding.unbound.slice(0, 40)) {
        rows.push(`  ${item.kind} ${item.path}.${item.property} ← ${item.clip} · ${item.reason}`);
      }
      if (binding.unbound.length > 40) rows.push(`  …还有 ${binding.unbound.length - 40} 条`);
    } else if (binding) {
      rows.push('绑不上的通道：0');
    }
    if (state.unplayable.length) {
      rows.push(`本轨不可播的 clip ${state.unplayable.length} 条：`);
      for (const name of state.unplayable.slice(0, 20)) rows.push(`  ${name}`);
      if (state.unplayable.length > 20) rows.push(`  …还有 ${state.unplayable.length - 20} 条`);
    }
    detail.textContent = rows.join('\n') || '等待选中演出…';
  }
}

function treeLines(node, depth = 0, lines = []) {
  if (!node) return lines;
  const prefix = '  '.repeat(depth);
  lines.push(`${prefix}${node.class || 'Track'} · ${node.name || '(unnamed)'}`);
  for (const child of node.children || []) treeLines(child, depth + 1, lines);
  return lines;
}

function renderTree(timeline) {
  const lines = [`Timeline · ${timeline?.name || '—'}`];
  for (const track of (timeline?.tracks || []).slice(0, 120)) treeLines(track, 1, lines);
  const total = (timeline?.tracks || []).length;
  if (total > 120) lines.push(`  …共 ${total} 条顶层轨，只列前 120 条`);
  $('track-tree').textContent = lines.join('\n');
}

function stopActions() {
  for (const action of state.activeActions.values()) action.stop();
  state.activeActions.clear();
  state.mixer?.stopAllAction();
}

function clearFixture() {
  stopActions();
  if (state.fixture?.root) {
    fixtureMount.remove(state.fixture.root);
    state.fixture.root.traverse((object) => {
      object.geometry?.dispose?.();
      const list = Array.isArray(object.material) ? object.material : [object.material];
      for (const material of list) material?.dispose?.();
    });
  }
  state.fixture = null;
}

// ---------------------------------------------------------------- 装载

async function loadCharacter(unit) {
  const record = (state.manifest?.units || []).find((item) => String(item.unit) === String(unit));
  if (!record) throw new Error(`manifest 没有角色 ${unit}`);
  const gltf = await loader.loadAsync(assetUrl(record.glb || `sd_${unit}.glb`));
  const rigRaw = (await fetchOptionalJson(record.rig || `sd_${unit}.rig.json`)).value;
  const character = await buildCharacter({
    gltf, rigRaw, tables: state.tables, unit: Number(unit), stencilIndex: 0,
  });
  state.characterGltf = gltf;
  state.character = character;
  actors.add(character.root);
  character.root.visible = false;         // 没命中 attach 前不摆出来
  return character;
}

async function loadMotionLibrary() {
  if (state.motion) return state.motion;
  state.motionState = '加载中…';
  setText('motion-status', `动作库：${state.motionState}`);
  try {
    const index = await fetchJson('motion-library.index.json');
    const gltf = await loader.loadAsync(assetUrl('motion-library.glb'));
    const loop = new Map();
    for (const clip of Object.values(index?.clips || {})) {
      for (const segment of Object.values(clip?.segments || {})) {
        if (segment?.name) loop.set(segment.name, !!segment.loop);
      }
    }
    state.motion = { clips: gltf.animations || [], loop, index };
    for (const [name, value] of loop) state.loopByName.set(name, value);
    state.motionState = `${state.motion.clips.length} 条 AnimationClip`;
    setText('motion-status', `动作库：${state.motionState}`);
    return state.motion;
  } catch (error) {
    state.motionState = `加载失败（${String(error).slice(0, 60)}）`;
    setText('motion-status', `动作库：${state.motionState}`);
    console.warn('[stage] motion library load failed', error);
    return null;
  }
}

async function packageIndexOf(pkg) {
  if (state.packageIndex.has(pkg)) return state.packageIndex.get(pkg);
  const result = await fetchOptionalJson(`perf-animations/${pkg}.index.json`);
  const records = new Map();
  for (const record of result.value?.clipRecords || []) {
    if (record?.name) records.set(record.name, record);
  }
  const entry = { doc: result.value, records, error: result.error };
  state.packageIndex.set(pkg, entry);
  return entry;
}

async function loadAnimationPackage(pkg) {
  if (state.packages.has(pkg)) return state.packages.get(pkg);
  const gltf = await loader.loadAsync(assetUrl(`perf-animations/${pkg}.glb`));
  const entry = {
    gltf,
    json: gltf.parser.json,
    nodeObjects: Bind.nodeObjectsOf(gltf),
    url: assetUrl(`perf-animations/${pkg}.glb`),
  };
  state.packages.set(pkg, entry);
  return entry;
}

async function loadFixtureGeometry(fixturePackage) {
  clearFixture();
  setGeometryWarning(false);
  if (!fixturePackage) return null;
  const [geometryInfo, animationInfo] = await Promise.all([
    findFixtureGeometry(fixturePackage),
    findFixtureAnimation(fixturePackage),
  ]);
  if (animationInfo.status !== 'ready') {
    console.warn('[stage] fixture animation package unavailable', animationInfo.error || fixturePackage);
  }
  if (geometryInfo.status !== 'ready') {
    setGeometryWarning(true);
    return null;
  }
  try {
    const gltf = await loader.loadAsync(geometryInfo.url);
    const root = gltf.scene;                                   // 默认场景 = 带挂点的那份 prefab
    const report = applyFixtureMaterials(root, { stencilIndex: 1 });
    fixtureMount.add(root);
    state.fixture = {
      root, gltf, json: gltf.parser.json, nodeObjects: Bind.nodeObjectsOf(gltf),
      package: fixturePackage, report, url: geometryInfo.url,
      sceneIndex: gltf.parser.json.scene ?? 0,
    };
    return state.fixture;
  } catch (error) {
    setGeometryWarning(true, '家具几何未就绪（加载失败）');
    console.warn('[stage] fixture geometry load failed', error);
    return null;
  }
}

// ---------------------------------------------------------------- 轨（演员）分道
//
// 一条时间轴上同一时刻常有上百个动画 clip 同时「在区间内」：它们不是叠加的一场演出，
// 而是**每条轨一个演员/座位的备选**（clip 名里的 cm/cw/uNNN 是角色，尾部三位号是挂点号）。
// 运行时只会选中其中一条轨，所以本页也按轨分道播放，默认选可播 clip 最多的那条。
// 想看全部叠加的效果可以选「全部轨叠加」——那一项在界面上标明它不是运行时行为。

function buildLanes(performance) {
  const lanes = new Map();
  (performance.animationClips || []).forEach((event, index) => {
    const key = event.trackPathId || event.trackName || `#${index}`;
    if (!lanes.has(key)) {
      lanes.set(key, {
        key, trackName: event.trackName, trackClass: event.class,
        events: [], packages: new Set(), names: new Set(), nullTargets: 0,
        playable: 0, unplayable: 0, sample: '',
      });
    }
    const lane = lanes.get(key);
    lane.events.push({ ...event, index });
    const target = event.target;
    if (target?.targetPackage && target?.clipName) {
      lane.packages.add(target.targetPackage);
      lane.names.add(`${target.targetPackage}::${target.clipName}`);
      if (!lane.sample) lane.sample = target.clipName;
    } else {
      lane.nullTargets += 1;
    }
  });
  return [...lanes.values()];
}

async function scoreLanes(lanes) {
  const packages = new Set();
  for (const lane of lanes) for (const pkg of lane.packages) packages.add(pkg);
  const indexes = new Map();
  await Promise.all([...packages].map(async (pkg) => {
    indexes.set(pkg, await packageIndexOf(pkg));
  }));
  for (const lane of lanes) {
    lane.playable = 0;
    lane.unplayable = 0;
    for (const event of lane.events) {
      const target = event.target;
      if (!target?.clipName) continue;
      const record = indexes.get(target.targetPackage)?.records.get(target.clipName);
      // 演出侧作者态与下发态漂移：有的 clip 在包里一条 glTF 通道都没有 —— 那就是不可播。
      if (record && (record.gltfChannels || 0) > 0) lane.playable += 1;
      else lane.unplayable += 1;
    }
  }
  return indexes;
}

/** clip 名尾部的三位号只有在 attach 表里真有这个挂点时才算挂点号（`_001_S` 那种是取次号）。 */
function knownAttachId(clipName) {
  const id = Bind.attachIdFromClipName(clipName);
  if (!id) return '';
  const packageName = state.performance?.fixtureTarget?.targetPackage;
  const entries = packageName ? state.attachDoc?.packages?.[packageName]?.entries || [] : [];
  return entries.some((entry) => String(entry.id) === id) ? id : '';
}

function laneLabel(lane) {
  if (lane.key === '*') return `全部轨叠加（${lane.events.length} clip · 非运行时行为）`;
  const attachId = knownAttachId(lane.sample);
  return `轨 ${lane.trackName || lane.key} · ${lane.events.length} clip · 可播 ${lane.playable}`
    + (attachId ? ` · 挂点 ${attachId}` : '')
    + (lane.sample ? ` · ${lane.sample}` : '');
}

function populateLanes(lanes) {
  const select = $('lane-select');
  if (!select) return;
  select.innerHTML = '';
  for (const lane of lanes) {
    const option = document.createElement('option');
    option.value = lane.key;
    option.textContent = laneLabel(lane);
    select.appendChild(option);
  }
  select.disabled = lanes.length === 0;
}

// ---------------------------------------------------------------- 动画上链

function makeResolvers() {
  const resolvers = {};
  if (state.fixture) {
    resolvers.native = Bind.suffixIndex(
      state.fixture.json, state.fixture.nodeObjects, state.fixture.sceneIndex,
    );
  }
  if (state.characterGltf) {
    const json = state.characterGltf.parser.json;
    resolvers['character-rig'] = Bind.suffixIndex(
      json, Bind.nodeObjectsOf(state.characterGltf), json.scene ?? 0,
    );
  }
  return resolvers;
}

async function bindLane(lane) {
  stopActions();
  state.clipsByKey = new Map();
  state.unplayable = [];
  const resolvers = makeResolvers();
  const reports = [];
  const wanted = new Map();                    // pkg → Set(clipName)
  for (const key of lane.names) {
    const marker = key.indexOf('::');
    const pkg = key.slice(0, marker);
    const name = key.slice(marker + 2);
    if (!wanted.has(pkg)) wanted.set(pkg, new Set());
    wanted.get(pkg).add(name);
  }

  for (const [pkg, names] of wanted) {
    // 角色动作走共享动作库（它按人形骨名烘焙，viewer 播的就是这一份）；
    // 库读不到时退回同名的演出动画包，并在界面上说明退了。
    if (pkg === 'mysekai__character_motion') {
      const motion = await loadMotionLibrary();
      if (motion) {
        const report = Bind.retargetByName({
          clips: motion.clips, root: state.character?.root, want: names,
        });
        for (const [name, clip] of report.clips) state.clipsByKey.set(`${pkg}::${name}`, clip);
        reports.push(report);
        for (const name of names) {
          if (!state.clipsByKey.has(`${pkg}::${name}`)) state.unplayable.push(`${pkg}::${name}`);
        }
        continue;
      }
    }
    try {
      const entry = await loadAnimationPackage(pkg);
      const report = Bind.retargetClips({
        clips: entry.gltf.animations || [], json: entry.json,
        nodeObjects: entry.nodeObjects, resolvers, want: names,
      });
      for (const [name, clip] of report.clips) state.clipsByKey.set(`${pkg}::${name}`, clip);
      reports.push(report);
      for (const name of names) {
        if (!state.clipsByKey.has(`${pkg}::${name}`)) state.unplayable.push(`${pkg}::${name}`);
      }
    } catch (error) {
      console.warn('[stage] animation package load failed', pkg, error);
      for (const name of names) state.unplayable.push(`${pkg}::${name}（包读不到）`);
    }
  }
  state.binding = Bind.mergeReports(reports);
  state.mixer = new THREE.AnimationMixer(actors);
  return state.binding;
}

function loopFor(name) {
  const known = state.loopByName.get(name);
  if (known !== undefined) return known;
  return /_(?:L|Loop)$/i.test(String(name || ''));
}

function clipForEvent(event) {
  const target = event.target;
  if (!target?.targetPackage || !target?.clipName) return null;
  return state.clipsByKey.get(`${target.targetPackage}::${target.clipName}`) || null;
}

function startEvent(event) {
  const clip = clipForEvent(event);
  if (!clip || !state.mixer) return;
  const action = state.mixer.clipAction(clip);
  const loop = loopFor(event.target.clipName);
  const scale = Number.isFinite(event.timeScale) && event.timeScale !== 0 ? event.timeScale : 1;
  action.reset();
  action.enabled = true;
  action.clampWhenFinished = !loop;
  action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, loop ? Infinity : 1);
  action.setEffectiveWeight(1);
  action.setEffectiveTimeScale(scale);
  action.time = Math.max(0, event.clipIn);
  action.play();
  state.activeActions.set(event.index, action);
}

function updateEvents(dt) {
  const events = state.lane?.events || [];
  const now = state.timelineTime;
  for (const event of events) {
    const start = event.start;
    const end = event.start + event.duration;
    const active = state.activeActions.has(event.index);
    if (now >= start && now < end) {
      if (!active) startEvent(event);
    } else if (active && now >= end) {
      state.activeActions.get(event.index).stop();
      state.activeActions.delete(event.index);
    }
  }
  if (state.playing) state.mixer?.update(dt);
}

function activeClipLabel() {
  const events = state.lane?.events || [];
  const now = state.timelineTime;
  const inWindow = events.filter((event) => now >= event.start && now < event.start + event.duration);
  if (!inWindow.length) return '当前无 clip';
  const playing = inWindow.filter((event) => clipForEvent(event));
  const dead = inWindow.length - playing.length;
  const name = playing[0]?.target?.clipName || inWindow[0]?.target?.clipName || '(空动画段)';
  return `${name}${playing.length ? '' : ' · 该 clip 不可播'}${dead && playing.length ? ` · 同窗 ${dead} 条不可播` : ''}`;
}

// ---------------------------------------------------------------- 摆位与相机

function applyTransform(object, transform) {
  if (!object || !transform) return;
  object.position.fromArray(transform.position || [0, 0, 0]);
  object.quaternion.fromArray(transform.rotation || [0, 0, 0, 1]);
  object.scale.fromArray(transform.scale || [1, 1, 1]);
  object.updateMatrixWorld(true);
}

/** 角色摆到挂点：家具里有那个 loc 节点就直接挂上去（挂点会跟着家具动），否则用产物里的变换。 */
function placeCharacter(attach) {
  const character = state.character;
  if (!character) return '';
  const root = character.root;
  if (!attach?.entry) {
    root.visible = false;
    root.removeFromParent();
    actors.add(root);
    return '';
  }
  root.visible = true;
  const name = attach.entry.start?.name || '';
  const node = name && state.fixture ? state.fixture.root.getObjectByName(name) : null;
  if (node) {
    root.removeFromParent();
    node.add(root);
    root.position.set(0, 0, 0);
    root.quaternion.identity();
    root.scale.set(1, 1, 1);
    root.updateMatrixWorld(true);
    return `挂到节点 ${name}`;
  }
  root.removeFromParent();
  actors.add(root);
  applyTransform(root, attach.entry.start?.transform);
  return `按 attach-points 变换摆位（${name || '无节点名'}）`;
}

function frameObject(...objects) {
  const box = new THREE.Box3();
  for (const object of objects) {
    if (object) box.expandByObject(object);
  }
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() * 0.65, 1.2);
  camera.position.set(center.x + radius * 0.9, center.y + radius * 0.55, center.z + radius * 1.2);
  camera.near = Math.max(0.01, radius / 100);
  camera.far = Math.max(100, radius * 20);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

// ---------------------------------------------------------------- 现象环境（天空/地面/粒子/后处理）

async function bootEnvironment() {
  const environment = new Environment({
    scene, camera, renderer, base: params.get('base') || '../../local-data',
  });
  try {
    const ok = await environment.load();
    if (!ok) {
      state.env.error = environment.errors[0] || 'phenomena/ 读不到';
      return null;
    }
  } catch (error) {
    state.env.error = String(error).slice(0, 120);
    return null;
  }
  state.env.environment = environment;
  const select = $('env-select');
  if (select) {
    select.innerHTML = '';
    for (const name of environment.names) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    }
    select.disabled = false;
  }
  const want = params.get('env');
  if (want === '0') {
    setEnvEnabled(false);
    return environment;
  }
  const name = environment.names.includes(want) ? want : environment.names[0];
  await setPhenomenon(name);
  return environment;
}

async function setPhenomenon(name) {
  const environment = state.env.environment;
  if (!environment || !name) return false;
  const first = !environment.to;
  const ok = await environment.setPhenomenon(name, first ? 0 : CROSS_FADE_SECONDS);
  if (!ok) return false;
  state.env.name = name;
  const select = $('env-select');
  if (select) select.value = name;
  if (!state.env.on) setEnvEnabled(true);
  return true;
}

function setEnvEnabled(on) {
  const environment = state.env.environment;
  const toggle = $('env-toggle');
  if (!environment) {
    if (toggle) {
      toggle.checked = false;
      toggle.disabled = true;
    }
    setText('env-note', `环境层不可用：${state.env.error || '无现象数据'}`);
    return;
  }
  state.env.on = !!on && !state.env.failed;
  if (toggle) toggle.checked = state.env.on;
  if (state.env.on) {
    environment.attach();
    environment.setCharacterMaterials(state.character ? state.character.mats : []);
    grid.visible = false;
    scene.background = null;
  } else {
    environment.detach();
    grid.visible = true;
    scene.background = new THREE.Color(0x14151c);
    if (state.character) {
      Shading.setNight(state.character.mats, false);
      Shading.setLightDir(state.character.mats, Shading.LIGHT_DIR);
      Shading.setShadeColors(state.character.mats, Shading.SHADE_NEUTRAL, Shading.SHADE_NEUTRAL);
    }
    if (state.fixture) {
      Shading.setLightDir(state.fixture.report.materials, Shading.LIGHT_DIR);
      Shading.setLightColor(state.fixture.report.materials, Shading.LIGHT_DAY);
      Shading.setShadeColors(state.fixture.report.materials, Shading.SHADE_NEUTRAL, Shading.SHADE_NEUTRAL);
    }
  }
  updateEnvNote();
}

function updateEnvNote() {
  const environment = state.env.environment;
  if (!environment) return;
  const status = environment.status ? environment.status() : {};
  setText('env-note', state.env.on
    ? `现象 ${status.phenomenon || state.env.name || '—'} · 后处理 ${environment.postOn ? '开' : '关'}`
      + ` · 粒子 ${environment.liveParticles ? environment.liveParticles() : 0}`
      + (state.env.failed ? ' · 后处理链报错，已退回直画' : '')
    : `环境层已关（${environment.names.length} 个现象可选）`);
}

// ---------------------------------------------------------------- 选片

function markLabel(packageName) {
  const hit = RECOMMENDED_PACKAGES.find((item) => packageName.includes(item.match));
  return hit ? `${hit.mark} ` : '';
}

function populateCatalog(catalog) {
  const select = $('performance-select');
  select.innerHTML = '';
  // 建议组置顶，其余按**族**分组 —— 两族是两个宿主，界面上不混成一张平表。
  const recommended = document.createElement('optgroup');
  recommended.label = '选片建议（先看这些）';
  const groups = new Map();
  for (const result of catalog.families || []) {
    const group = document.createElement('optgroup');
    group.label = `${result.family.label} · ${result.family.dir}/`;
    groups.set(result.family.id, group);
  }
  for (const entry of catalog.entries) {
    const option = document.createElement('option');
    option.value = entry.key;
    const mark = markLabel(entry.package);
    option.textContent = `${mark}${entry.package} / ${entry.timeline}`;
    (mark ? recommended : groups.get(entry.family) || recommended).appendChild(option);
  }
  if (recommended.children.length) select.appendChild(recommended);
  for (const group of groups.values()) if (group.children.length) select.appendChild(group);
  select.disabled = catalog.entries.length === 0;
  const missing = (catalog.families || [])
    .filter((result) => !result.listing?.ok)
    .map((result) => `${result.family.dir}/tracks/ 读不到`);
  const counts = (catalog.families || [])
    .map((result) => `${result.family.dir}/ ${result.entries.length} 条`)
    .join(' · ');
  setText('recommendation-note', `${counts ? `${counts}。` : ''}${RECOMMENDATION_NOTE}`
    + (missing.length ? ` 未就绪：${missing.join('、')}。` : ''));
}

function defaultEntry(catalog) {
  const wanted = params.get('performance');
  if (wanted) {
    const hit = catalog.entries.find((entry) => entry.key === wanted || entry.package.includes(wanted));
    if (hit) return hit;
  }
  for (const item of RECOMMENDED_PACKAGES) {
    const hit = catalog.entries.find((entry) => entry.package.includes(item.match));
    if (hit) return hit;
  }
  return catalog.entries.find((entry) => entry.package.includes('__fixture_timeline__')) || catalog.entries[0];
}

function resetPlayback(playing = true) {
  stopActions();
  state.timelineTime = 0;
  state.playing = playing;
  state.totalDuration = state.performance?.totalDuration || 0;
  updateFacts();
}

async function applyLane(lane) {
  const token = state.runToken;
  state.lane = lane;
  await bindLane(lane);
  if (token !== state.runToken) return;
  const attach = attachForTarget(
    state.attachDoc, state.performance.fixtureTarget, knownAttachId(lane.sample),
  );
  state.performance.attach = attach;
  const placement = placeCharacter(attach);
  frameObject(state.fixture?.root, state.character?.root);
  resetPlayback(true);
  updateFacts();
  updateRenderFacts();
  if (attach.entry) {
    setStatus(`attach ${attach.attachId || '?'} · ${placement} · 通道绑上 ${state.binding.bound}/${state.binding.channels}`,
      state.binding.unbound.length ? 'warn' : 'ok');
  } else if (state.performance.fixtureTarget) {
    setStatus('attach 未命中，角色未放置', 'bad');
  } else {
    setStatus('当前演出没有家具目标', 'warn');
  }
}

async function applyPerformance(performance) {
  const token = ++state.runToken;
  stopActions();
  state.performance = performance;
  state.totalDuration = performance.totalDuration;
  renderTree(performance.timeline);
  await loadFixtureGeometry(performance.fixtureTarget?.targetPackage || '');
  if (token !== state.runToken) return;
  const lanes = buildLanes(performance);
  await scoreLanes(lanes);
  if (token !== state.runToken) return;
  lanes.sort((a, b) => b.playable - a.playable || b.events.length - a.events.length);
  if (lanes.length > 1) {
    lanes.push({
      key: '*', trackName: '*', trackClass: '', sample: lanes[0]?.sample || '',
      events: lanes.flatMap((lane) => lane.events),
      packages: new Set(lanes.flatMap((lane) => [...lane.packages])),
      names: new Set(lanes.flatMap((lane) => [...lane.names])),
      nullTargets: lanes.reduce((sum, lane) => sum + lane.nullTargets, 0),
      playable: lanes.reduce((sum, lane) => sum + lane.playable, 0),
      unplayable: lanes.reduce((sum, lane) => sum + lane.unplayable, 0),
    });
  }
  state.lanes = lanes;
  populateLanes(lanes);
  if (!lanes.length) {
    state.lane = null;
    state.binding = null;
    resetPlayback(false);
    setStatus('该 timeline 没有动画 clip', 'warn');
    updateRenderFacts();
    return;
  }
  const select = $('lane-select');
  if (select) select.value = lanes[0].key;
  await applyLane(lanes[0]);
}

async function selectPerformance(key) {
  const entry = state.catalog?.entries.find((item) => item.key === key);
  if (!entry) return;
  $('performance-select').disabled = true;
  setStatus(`读取 ${entry.package}…`);
  try {
    await applyPerformance(await loadPerformance(entry));
  } catch (error) {
    setStatus(`演出数据读取失败：${String(error)}`, 'bad');
    console.warn('[stage] performance failed', error);
  } finally {
    $('performance-select').disabled = false;
  }
  runSelfcheck();
}

async function selectLane(key) {
  const lane = state.lanes.find((item) => item.key === key);
  if (!lane) return;
  $('lane-select').disabled = true;
  try {
    await applyLane(lane);
  } catch (error) {
    setStatus(`动画上链失败：${String(error)}`, 'bad');
    console.warn('[stage] lane failed', error);
  } finally {
    $('lane-select').disabled = false;
  }
  runSelfcheck();
}

// ---------------------------------------------------------------- 界面壳

function updateOverlay() {
  const performance = state.performance;
  if (!performance) return;
  const active = performance.emoticonClips.find(
    (clip) => state.timelineTime >= clip.start && state.timelineTime < clip.start + clip.duration,
  );
  $('emoticon-bubble').textContent = active ? '气泡轨道 · 在区间内（产物里没有气泡条目名）' : '气泡轨道 · —';
  $('tweet-hud').textContent = `${performance.entry.timeline} · ${state.timelineTime.toFixed(2)} s`;
  setText('active-clip', activeClipLabel());
  const talk = performance.allClips.find(
    (clip) => clip.class === 'TextTrack' && state.timelineTime >= clip.start && state.timelineTime < clip.start + clip.duration,
  );
  $('dialogue-text').textContent = talk?.displayName || 'UI 字段待接入';
}

function setForm(form) {
  state.mode = form;
  document.body.dataset.form = form;
  for (const button of document.querySelectorAll('.mode-button')) {
    button.classList.toggle('is-active', button.dataset.form === form);
  }
  setText('form-indicator', form === 'spontaneous' ? '自发型' : '玩家参与型');
  $('tweet-hud').hidden = form !== 'spontaneous';
  $('dialogue-window').hidden = form !== 'participatory';
  $('emoticon-bubble').hidden = form !== 'spontaneous';
}

function resize() {
  const viewport = $('viewport');
  const width = viewport.clientWidth;
  const height = viewport.clientHeight;
  if (!width || !height) return;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  if (state.env.environment) state.env.environment.post.setSize(width, height);
}

function drawScene() {
  const environment = state.env.environment;
  if (state.env.on && environment && !state.env.failed) {
    try {
      if (environment.render()) return;
    } catch (error) {
      state.env.failed = true;                      // 后处理链出问题就退回直画，不让画面变黑
      console.warn('[stage] environment render failed; falling back to direct draw', error);
      updateEnvNote();
    }
  }
  renderer.render(scene, camera);
}

function stepFrame(dt) {
  updateEvents(dt);
  const character = state.character;
  if (character) {
    updateCharacter(character, dt, {
      onHead: (head) => {
        if (state.env.on && state.env.environment) state.env.environment.setSubject(head);
      },
    });
  }
  const environment = state.env.environment;
  if (state.env.on && environment) {
    environment.update(dt);
    if (state.fixture) applyPhenomenaLight(state.fixture.report.materials, environment.light);
  }
}

function animate(now) {
  requestAnimationFrame(animate);
  if (!state.lastFrame) state.lastFrame = now;
  const dt = Math.min((now - state.lastFrame) / 1000, 0.1);
  state.lastFrame = now;
  if (state.playing && state.performance) {
    state.timelineTime += dt * state.playbackRate;
    if (state.timelineTime >= state.totalDuration + 0.05) {
      state.timelineTime = 0;                       // 循环播放：演出短，反复看才看得清
    }
  }
  stepFrame(dt);
  updateOverlay();
  updateFacts();
  controls.update();
  drawScene();
}

async function boot() {
  setText('base-path', params.get('base') || '../../local-data');
  setForm('spontaneous');
  window.addEventListener('resize', resize);
  if (typeof ResizeObserver === 'function') new ResizeObserver(resize).observe($('viewport'));
  resize();

  $('performance-select').addEventListener('change', (event) => selectPerformance(event.target.value));
  $('lane-select')?.addEventListener('change', (event) => selectLane(event.target.value));
  $('play-button').addEventListener('click', () => { if (state.performance) state.playing = true; });
  $('stop-button').addEventListener('click', () => resetPlayback(false));
  $('speed-select').addEventListener('change', (event) => { state.playbackRate = Number(event.target.value) || 1; });
  $('env-select')?.addEventListener('change', (event) => setPhenomenon(event.target.value));
  $('env-toggle')?.addEventListener('change', (event) => setEnvEnabled(event.target.checked));
  for (const button of document.querySelectorAll('.mode-button')) {
    button.addEventListener('click', () => setForm(button.dataset.form));
  }

  try {
    state.manifest = await fetchJson('manifest.json');
    state.attachDoc = await fetchJson(ATTACH_POINTS_PATH);
    const facial = await fetchOptionalJson('facial-tables.json');
    state.tables = Facial.normalizeTables(facial.value) || Facial.fallbackTables();
    const wanted = params.get('unit');
    const units = state.manifest?.units || [];
    const unit = units.find((item) => String(item.unit) === String(wanted)) || units[0];
    if (!unit) throw new Error('manifest 没有角色条目');
    await loadCharacter(unit.unit);
    await bootEnvironment();
    state.catalog = await loadTrackCatalog();
    populateCatalog(state.catalog);
    if (!state.catalog.entries.length) throw new Error('cutscene-timeline/tracks/ 与 fixture-timeline/tracks/ 都没有可选 timeline');
    const entry = defaultEntry(state.catalog);
    $('performance-select').value = entry.key;
    await selectPerformance(entry.key);
  } catch (error) {
    setStatus(`启动失败：${String(error)}`, 'bad');
    console.warn('[stage] boot failed', error);
  }
  runSelfcheck();
}

requestAnimationFrame(animate);
boot();

// ---------------------------------------------------------------- 取证探针
//
// 自检（selfcheck.js）只准读**产物**与**已渲染的场景图**，不读本文件算出的中间结论。
// 所以这里只暴露场景图本体、两个 glb 的来源与 glTF 节点↔Object3D 的身份对照，
// 外加一个「推进 N 帧」的入口 —— 判据要自己去数，不从这里拿数好的结论。

window.__stageProbe = {
  scene,
  actors,
  character: () => (state.character ? {
    root: state.character.root,
    nodeObjects: Bind.nodeObjectsOf(state.characterGltf),
    json: state.characterGltf.parser.json,
  } : null),
  fixture: () => (state.fixture ? {
    root: state.fixture.root,
    url: state.fixture.url,
    package: state.fixture.package,
    nodeObjects: state.fixture.nodeObjects,
    json: state.fixture.json,
    sceneIndex: state.fixture.sceneIndex,
  } : null),
  lane: () => (state.lane ? {
    key: state.lane.key,
    packages: [...state.lane.packages],
    names: [...state.lane.names],
    events: state.lane.events.length,
  } : null),
  duration: () => state.totalDuration,
  advance(frames = 30, dt = 1 / 60) {
    const wasPlaying = state.playing;
    state.playing = true;
    for (let i = 0; i < frames; i += 1) {
      state.timelineTime += dt * state.playbackRate;
      if (state.timelineTime >= state.totalDuration + 0.05) state.timelineTime = 0;
      stepFrame(dt);
    }
    state.playing = wasPlaying;
    return { time: state.timelineTime, active: state.activeActions.size };
  },
  seek(time) {
    state.timelineTime = Math.max(0, Math.min(Number(time) || 0, state.totalDuration));
    stepFrame(0);
    return state.timelineTime;
  },
};

// ---------------------------------------------------------------- 导出钩子（导出车道用）

const EXPORT_WIDTH = 320;
const EXPORT_HEIGHT = 320;

function createStageCaptureRenderer(width, height) {
  const capture = new THREE.WebGLRenderer({
    antialias: true, alpha: true, preserveDrawingBuffer: true, stencil: true,
  });
  capture.setPixelRatio(1);
  capture.outputColorSpace = THREE.LinearSRGBColorSpace;
  capture.toneMapping = THREE.NoToneMapping;
  capture.setClearColor(0x000000, 0);
  capture.setClearAlpha(0);
  capture.setSize(width, height, false);
  return capture;
}

function renderStageExportFrame(capture, time) {
  const previousBackground = scene.background;
  const previousGrid = grid.visible;
  const previousPlaying = state.playing;
  const previousTime = state.timelineTime;
  const previousCamera = camera.clone();
  const previousTarget = controls.target.clone();
  const previousActions = [...state.activeActions.values()];
  scene.background = null;
  grid.visible = false;
  stopActions();
  state.timelineTime = Math.max(0, Math.min(Number(time) || 0, state.totalDuration));
  state.playing = true;
  updateEvents(0);
  for (const [index, action] of state.activeActions) {
    const event = (state.lane?.events || []).find((item) => item.index === index);
    if (!event) continue;
    const elapsed = Math.max(0, state.timelineTime - event.start);
    const duration = action.getClip()?.duration || 0;
    const local = Math.max(0, Number(event.clipIn) || 0) + elapsed * (Number(event.timeScale) || 1);
    action.time = action.loop === THREE.LoopRepeat && duration > 0
      ? local % duration
      : Math.min(local, duration);
  }
  state.mixer?.update(0);
  if (state.character) updateCharacter(state.character, 0, { cloth: false });
  capture.render(scene, camera);
  const gl = capture.getContext();
  const raw = new Uint8Array(EXPORT_WIDTH * EXPORT_HEIGHT * 4);
  gl.readPixels(0, 0, EXPORT_WIDTH, EXPORT_HEIGHT, gl.RGBA, gl.UNSIGNED_BYTE, raw);
  const pixels = new Uint8ClampedArray(raw.length);
  for (let y = 0; y < EXPORT_HEIGHT; y += 1) {
    const source = (EXPORT_HEIGHT - y - 1) * EXPORT_WIDTH * 4;
    const target = y * EXPORT_WIDTH * 4;
    pixels.set(raw.subarray(source, source + EXPORT_WIDTH * 4), target);
  }
  state.timelineTime = previousTime;
  state.playing = previousPlaying;
  camera.copy(previousCamera);
  controls.target.copy(previousTarget);
  controls.update();
  scene.background = previousBackground;
  grid.visible = previousGrid;
  stopActions();
  for (const action of previousActions) action.play();
  return { pixels };
}

window.__stageExport = {
  ready: () => !!state.performance && !!state.character,
  duration: () => state.totalDuration,
  createCaptureRenderer: createStageCaptureRenderer,
  renderFrame: renderStageExportFrame,
};
