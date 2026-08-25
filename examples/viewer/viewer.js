// viewer.js — character viewer orchestration
// Public inputs: manifest.json, sd_<unit>.glb, sd_<unit>.rig.json, and facial-tables.json.
// Optional sidecars are reported and the viewer uses documented local fallbacks where possible.

import * as THREE from './three.module.min.js';
import { GLTFLoader } from './GLTFLoader.js';
import { OrbitControls } from './OrbitControls.js';
import * as Shading from './shading.js';
import * as Facial from './facial.js';
import * as Cloth from './cloth.js';
import * as Seg from './segments.js';
import { CheckPanel, runChecks, applySabotage, parseSabotage } from './selfcheck.js';
import { PerformancePlayer, loadPerformance } from './performance.js';
import { loadEmoticons } from './emoticon.js';
import { Environment, CROSS_FADE_SECONDS } from './environment.js';

// ---- shader 错误捕获(须在 renderer 前安装) ----
const shaderErrors = [];
{
  const orig = console.error.bind(console);
  console.error = (...a) => {
    const s = a.map(String).join(' ');
    if (/THREE|shader|WebGL/i.test(s)) shaderErrors.push(s);
    orig(...a);
  };
}

const params = new URLSearchParams(location.search);
const BASE = params.get('base') || '.';
const SABOTAGE = parseSabotage();
const FALLBACK_UNITS = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115,
  116, 117, 118, 119, 120, 121, 127, 128, 129, 130, 131, 133, 139, 142, 149, 155];

const $ = (id) => document.getElementById(id);
const view = $('view'), hud = $('hud'), checkEl = $('selfcheck');

// ---- renderer / scene ----
const renderer = new THREE.WebGLRenderer({ antialias: true, stencil: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.LinearSRGBColorSpace; // gamma 直通
renderer.toneMapping = THREE.NoToneMapping;
// draw call / 三角形数按**整帧**统计。three.js 默认每次 render() 开头清零计数器,
// 于是多趟渲染的帧只剩最后一趟的数 —— 后处理链把场景渲进离屏靶再合成一个全屏四边形时,
// 读出来永远是「1 calls / 2 tris」,看着像场景没画,其实只是量错了。关掉自动清零,
// 由帧循环开头清一次,读数就是这一帧真正提交的总量。
renderer.info.autoReset = false;
// 后处理链一帧里要 render 多次,而 three.js 每次 render 开头都会清计数器 ——
// 关掉自动清零、帧首手动清一次,统计才是「整帧」而不是「最后一次绘制」。
let lastFrameInfo = { calls: 0, triangles: 0 };
view.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14151c);
// 远平面要装得下天空网格:它是产物里那一个,离自己的原点最远约 660,而它每帧钉在相机脚下。
const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
const grid = new THREE.GridHelper(4, 16, 0x2c2f3d, 0x21232e);
scene.add(grid);
scene.add(camera);   // 相机族环境粒子挂在相机下,相机必须在场景图里

const loader = new GLTFLoader();
const clock = new THREE.Clock();

// ---- app 状态(selfcheck 消费) ----
const app = {
  THREE, renderer, scene, camera,
  materials: [], overlayMesh: null, eyeMesh: null, mouthMesh: null, bodyMeshes: [],
  mixer: null, clothSystem: null, facial: null, segctl: null, clipByName: new Map(),
  currentFamily: null, probeBone: null, stencilRef: 4, shaderErrors,
  dataInfo: { manifest: false, unitCount: 0, rig: false, rigChains: 0, tables: null, tablesFallback: false, extrasFound: [], emoticons: 0 },
  userTouchedSpeak: false,
  _sabotageNaN: false,
  _sabotagePatch: false,
  _sabotageResize: false,
  _resize: null,
  emoticon: null,
  emoticonItems: [],
  environment: null,
};

let tables = null;
let manifest = null;
let unitList = [];
let current = null;            // { root, mats, overlay, mixer, cloth, facial, segctl, families, headAnchor, headOffset }
let clothOn = true, playing = true, night = false, wire = false;
let perfDoc = null;                       // alone-actions.json(表演编排)
let perfMode = 'faithful';                // faithful=按编排的时间门+概率;cycle=依次轮播(检查用)
// 表演场景是主入口:点一个场景=完整播放一遍(动作×表情×气泡全配对),播完即停。
// 「自动演出」(默认开)只管空闲时段:idle 时编排按策略自动选取下一个场景;
// 手动点播的场景播放期间它不插手,播完后照常接续。
let perfAuto = true;

function syncPerfUI() {
  const b = $('bAutoPerf');
  if (b) { b.classList.toggle('on', perfAuto); b.disabled = !perfDoc; }
  const hint = $('perfHint');
  if (hint) {
    hint.textContent = !perfDoc
      ? '缺 alone-actions.json,表演不可用'
      : `点场景=完整播一遍,播完即停${perfAuto ? ';空闲时自动演出' : ''}`;
  }
  if (current && current.perf) current.perf.auto = perfAuto && !!perfDoc;
}

// 表演场景列表:标签取首个动作步的动作名;标注额外动作数、触发方式、是否带气泡。
function scenarioLabel(sc) {
  const anims = (sc.steps || []).filter((st) => st.op === 'animation');
  const first = anims.length ? String(anims[0].motion || '').replace(/^mov_c[wm]_/, '') : '(无动作)';
  return {
    first,
    extra: anims.length > 1 ? `+${anims.length - 1}` : '',
    emo: (sc.steps || []).some((st) => st.op === 'emoticon'),
    kind: sc.kind === 'randomBranch' ? '分支' : '时间门',
  };
}

function buildScenarioList(perf) {
  const box = $('scenarios');
  if (!box) return;
  box.innerHTML = '';
  const list = perf ? perf.scenarios() : [];
  for (const sc of list) {
    const L = scenarioLabel(sc);
    const d = document.createElement('button');
    d.type = 'button';
    d.className = 'row';
    d.dataset.key = `${sc.id ?? ''} ${L.first}`.toLowerCase();
    d.innerHTML = `<span>${L.first}${L.extra ? ` <small>${L.extra}</small>` : ''}</span>`
      + `<small>${L.kind}${L.emo ? ' · 气泡' : ''}</small>`;
    d.title = '完整播放一遍该场景,播完即停';
    d.onclick = () => { if (current && current.perf) current.perf.playScenario(sc); };
    box.appendChild(d);
  }
  if (!list.length) box.innerHTML = '<div class="empty">无编排数据</div>';
  applyFilter('scenarios', 'scnCount', $('scnFilter') ? $('scnFilter').value : '');
}
// ---- 眼口标签(表情表的行)手动选择:检查工具,选择即应用 ----
// 眨眼机取所选眼行的开/闭格,说话机取所选口行的开合格;与动作库同规则,
// 选择时停用自动演出(否则编排的 eye/mouth 步下一拍就把脸盖回去)。
function fillFacialSelects() {
  const se = $('selEye'), sm = $('selMouth');
  if (!se || !sm || !tables) return;
  se.innerHTML = ''; sm.innerHTML = '';
  se.append(new Option('眼型:默认', ''));
  for (const name of tables.eye.keys()) se.append(new Option(`眼型:${name}`, name));
  sm.append(new Option('口型:默认', ''));
  for (const name of tables.lip.keys()) sm.append(new Option(`口型:${name}`, name));
}

function resetFacialSelects() {
  const se = $('selEye'), sm = $('selMouth');
  if (se) se.value = '';
  if (sm) sm.value = '';
}

function applyFacialSelection() {
  if (!current || !current.facial) return;
  if (current.perf) current.perf.stopToIdle();
  if (perfAuto) { perfAuto = false; syncPerfUI(); }
  const d = Facial.patternsFor(current.unitId ?? 0, tables, null);
  const eyeName = $('selEye').value, lipName = $('selMouth').value;
  current.facial.setPatterns(
    (eyeName && tables.eye.get(eyeName)) || d.eyeRow,
    (lipName && tables.lip.get(lipName)) || d.lipRow);
}

// ---- 气泡(emoticon)----
// 数据:emoticons/emoticons.json(items = fx_emote_*;sprite=图集动画 view,particle=粒子 view)。
// loadEmoticons 给的是注册表 { doc, items, names, kindOf, create(name, opts) };走 create 而不是自己
// new EmoticonView,是为了共用它那份按文件名缓存的贴图加载器(同一条目重播不再重下 PNG)。
// Contract semantics: showSeconds starts automatic hiding; hide plays end, then waits disposeDelaySeconds before disposal.
let emoticons = null;                     // 注册表(缺 emoticons.json 时为 null,功能整块降级)
let emoticonItems = [];                   // [{ name, viewKind }](UI 下拉与 probe 用)
let emoticonAuto = true;                  // 编排里的 emoticon/hideEmoticon 步是否驱动气泡
let selectedEmoticon = '';
let emoticonErr = null;                   // 上次查表/构建失败原因(HUD 如实显示,不静默吞掉)
let emoticonDelay = 1;                    // disposeDelaySeconds(json semantics,缺省 1s)
let emoMountWarned = false;               // 缺 HeadRoot 挂点时只吼一次(换角色不要刷屏)
const emoticonReady = loadEmoticons(`${BASE}/emoticons/emoticons.json`).then((reg) => {
  emoticons = reg;
  emoticonItems = reg ? reg.names.map((name) => ({ name, viewKind: reg.kindOf(name) })) : [];
  emoticonDelay = +(reg && reg.doc && reg.doc.semantics && reg.doc.semantics.disposeDelaySeconds) || 1;
  app.emoticons = reg;
  app.emoticonItems = emoticonItems;
  app.dataInfo.emoticons = emoticonItems.length;
  queueMicrotask(fillEmoticonSelect);
  return reg;
}).catch((e) => {                         // Without emoticons/: disable that feature without blocking the viewer
  emoticonErr = `emoticons.json: ${String(e).slice(0, 100)}`;
  queueMicrotask(fillEmoticonSelect);
  return null;
});
// ---- 环境(现象:天气)----
// 数据:phenomena/index.json + 逐现象 config/ramp/postprocess/fx。两条通道见 environment.js:
// 一条是现象驱动的全局着色量(逐帧写一组共享 uniform),一条是光照九项(角色组直接喂 toon 着色)。
// 切换现象 = 0.25 秒交叉淡化;站点覆盖按两级查找(室内开关选的是那个真的带覆盖的站点)。
// 环境层默认**关**:没有 phenomena/ 数据时页面照旧可用,开关与列表如实显示缺失原因。
let environment = null;
let envOn = false;
let envErr = null;
let envWant = params.get('env');            // ?env=<NNN_name> 直达;?env=0 关环境层
const envReady = (async () => {
  const env = new Environment({ scene, camera, renderer, base: BASE });
  const ok = await env.load();
  if (!ok) { envErr = env.errors[0] || 'phenomena/ 读不到'; return null; }
  environment = env;
  app.environment = env;
  return env;
})().catch((e) => { envErr = `phenomena: ${String(e).slice(0, 100)}`; return null; });

function envLabel(name) {
  const entry = environment && environment.index.phenomena[name];
  const m = entry && entry.master;
  // 名字优先用 master 的英文名(master 由使用者自备,没有它就只有资产名)。
  const short = m && m.englishName ? m.englishName : name.replace(/^\d+_/, '');
  return { short, bright: m ? m.brightnessType : null, time: m ? m.timePeriodType : null, icon: entry && entry.icon };
}

function buildEnvList() {
  const box = $('envList');
  if (!box) return;
  box.innerHTML = '';
  if (!environment) {
    box.innerHTML = `<div class="empty">${envErr ? `缺 phenomena 数据(${envErr})` : '无现象数据'}</div>`;
    for (const id of ['bEnv', 'bEnvIndoor', 'bEnvParticles', 'bEnvPost', 'bEnvSky', 'bEnvGround', 'selEnvSite']) {
      const el = $(id); if (el) el.disabled = true;
    }
    return;
  }
  for (const name of environment.names) {
    const L = envLabel(name);
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'echip';
    b.dataset.env = name;
    const img = L.icon
      ? `<img src="${environment.root}/${L.icon}" alt="" loading="lazy">`
      : '<span class="ph"></span>';         // 无图标(投递站现象):同尺寸占位
    b.innerHTML = `${img}<span class="en">${L.short}</span>`;
    b.title = `${name}${L.time ? ` · ${L.time}` : ''}${L.bright ? ` · ${L.bright}` : ''}`;
    b.onclick = () => setEnvPhenomenon(name);
    box.appendChild(b);
  }
  const badge = $('envCount');
  if (badge) badge.textContent = environment.names.length || '';
  const sel = $('selEnvSite');
  if (sel) {
    // 站点名单从数据里数出来(粒子的 site 字段),不写死。
    const sites = new Set();
    for (const p of Object.values(environment.index.phenomena || {})) {
      for (const v of p.variants || []) {
        const m = /^unique__(.+)$/.exec(v);
        if (m) sites.add(m[1]);
      }
    }
    const list = [...sites].sort();
    sel.innerHTML = list.map((s) => `<option value="${s}">站点:${s}</option>`).join('');
    if (list.includes(environment.site)) sel.value = environment.site;
    else if (list.length) { environment.site = list[0]; sel.value = list[0]; }
  }
}

function syncEnvUI() {
  const b = $('bEnv');
  if (b) b.classList.toggle('on', envOn);
  document.querySelectorAll('#envList .echip').forEach((r) => r.classList.toggle('on',
    envOn && environment && environment.to && r.dataset.env === environment.to.name));
  const hint = $('envHint');
  if (hint) {
    if (!environment) hint.innerHTML = `<span class="warn">${envErr || '无现象数据'}</span>`;
    else if (!envOn) hint.textContent = `${environment.names.length} 个现象 · 点一个开启环境层`;
    else {
      const st = environment.status();
      const L = envLabel(st.phenomenon || '');
      hint.innerHTML = `<span class="ok">${st.phenomenon || '—'}</span> <span class="dim">`
        + `${L.time || '时段未知'} · ${L.bright ? `亮度 ${L.bright}` : '亮度未知'}`
        + `${st.usedOverride ? ' · 覆盖' : ''}${st.homeAngleUsed ? ' · 家园角' : ''}</span>`;
    }
  }
  const lh = $('lightHint');
  if (lh) {
    lh.innerHTML = envOn
      ? '<span class="warn">环境层接管光照:滑块此刻不生效</span>'
      : '&nbsp;';
  }
  const ib = $('bEnvIndoor');
  if (ib) ib.disabled = !environment || !envOn;
  for (const id of ['bEnvParticles', 'bEnvPost', 'bEnvSky', 'bEnvGround', 'selEnvSite']) {
    const el = $(id); if (el) el.disabled = !environment || !envOn;
  }
}

/** 点一个现象:第一次点顺手把环境层打开;之后每次切换走 0.25 秒交叉淡化。 */
async function setEnvPhenomenon(name, seconds = CROSS_FADE_SECONDS) {
  if (!environment) return false;
  const first = !environment.to;
  const ok = await environment.setPhenomenon(name, first ? 0 : seconds);
  if (!ok) { syncEnvUI(); return false; }
  if (!envOn) enableEnv(true);
  syncEnvUI();
  return true;
}

function enableEnv(on) {
  if (!environment) return;
  envOn = !!on;
  if (envOn) {
    environment.attach();
    environment.setCharacterMaterials(current ? current.mats : []);
    grid.visible = false;                  // 有地面了,诊断用的地格网让位
    scene.background = null;               // 天空网格接管背景
  } else {
    environment.detach();
    grid.visible = true;
    scene.background = new THREE.Color(0x14151c);
    // 交还光照:把面板上的角度与日/夜色重新推给角色材质。
    if (current) {
      Shading.setNight(current.mats, night);
      Shading.setLightDir(current.mats, Shading.lightDirFromAngles(+$('rLightXZ').value, +$('rLightY').value));
      Shading.setShadeColors(current.mats, Shading.SHADE_NEUTRAL, Shading.SHADE_NEUTRAL);
    }
  }
  syncEnvUI();
}

// The self-check uses the same public switch path as the environment buttons.
app.setEnvPhenomenon = setEnvPhenomenon;
app.setEnvEnabled = enableEnv;

const perfReady = loadPerformance(`${BASE}/alone-actions.json`).then((d) => { perfDoc = d; return d; });
let checksRan = false;
const panel = new CheckPanel(checkEl);

async function fetchJson(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

// ---- 共享动作库(批量提取布局) ----
// 批量提取的角色包不内嵌剪辑:动画全部在 motion-library.glb 里,轨道按人形骨名
// 绑定,同一份库驱动所有角色。只加载一次,跨角色复用;失败记 null 不重试。
let motionLibraryPromise;
function sharedMotionLibrary() {
  if (motionLibraryPromise === undefined) {
    motionLibraryPromise = (async () => {
      const index = await fetchJson(`${BASE}/motion-library.index.json`);
      if (!index) return null;
      const gltf = await loader.loadAsync(`${BASE}/motion-library.glb`);
      const loop = new Map();
      for (const clip of Object.values(index.clips || {})) {
        for (const seg of Object.values(clip.segments || {})) {
          if (seg && seg.name) loop.set(seg.name, !!seg.loop);
        }
      }
      app.dataInfo.motionLibrary = (gltf.animations || []).length;
      return { clips: gltf.animations || [], loop };
    })().catch((err) => {
      console.warn('[viewer] 共享动作库加载失败:', err);
      return null;
    });
  }
  return motionLibraryPromise;
}

// ---- Material extras (accept documented aliases and use neutral defaults when absent) ----
function extraOf(ud, aliases) {
  if (!ud) return undefined;
  const low = {};
  for (const k of Object.keys(ud)) low[k.toLowerCase()] = k;
  for (const a of aliases) {
    if (ud[a] !== undefined) return ud[a];
    const hit = low[a.toLowerCase()];
    if (hit !== undefined) return ud[hit];
  }
  return undefined;
}
const texIndex = (v) => (typeof v === 'number' ? v : (v && typeof v.index === 'number' ? v.index : null));

const ROLE_DEFAULTS = {
  body: { usage: 1, override: 1, intensity: 1, threshold: 0.01, smoothness: 0.05 },
  eye: { usage: 0, override: 0, intensity: 1, threshold: 0, smoothness: 0, eyebrowAlpha: 0.4, eyebrowClip: 0.5 },
  mouth: { usage: 0, override: 0, intensity: 1, threshold: 0, smoothness: 0, eyebrowAlpha: 1, eyebrowClip: 0.5 },
};

function classifyRole(matName, meshName, primIndex) {
  const n = (matName || '').toLowerCase();
  if (n.includes('eye')) return 'eye';
  if (n.includes('mouth')) return 'mouth';
  if (n.includes('body')) return 'body';
  if (n.includes('face_sub0')) return 'eye';
  if (n.includes('face_sub1')) return 'mouth';
  const mn = (meshName || '').toLowerCase();
  if (mn.includes('face')) return primIndex === 0 ? 'eye' : 'mouth'; // Face primitive order is [eye, mouth].
  return 'body';
}

function disposeCurrent() {
  if (!current) return;
  clearEmoticon();                       // 气泡:先回收 view(几何/贴图)
  // 挂点在骨架里,不一定是 scene 的直属子;四个挂点逐个摘掉
  for (const a of Object.values(current.emoAnchors || {})) a.removeFromParent();
  scene.remove(current.root);
  if (current.mixer) { current.mixer.stopAllAction(); current.mixer.uncacheRoot(current.root); }
  current.root.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
  for (const m of current.mats) {
    for (const k of Object.keys(m.uniforms || {})) {
      const v = m.uniforms[k].value;
      if (v && v.isTexture) v.dispose();
    }
    m.dispose();
  }
  current = null;
  app.materials = []; app.overlayMesh = null; app.mixer = null; app.clothSystem = null;
  app.facial = null; app.segctl = null; app.currentFamily = null; app.emoticon = null;
}

async function loadUnit(unit) {
  const entry = unitList.find((u) => u.unit === unit) || { unit, glb: `sd_${unit}.glb`, rig: `sd_${unit}.rig.json`, clips: null };
  hudStatus(`sd_${unit} 载入中…`);
  document.querySelectorAll('#list .uchip').forEach((r) => r.classList.toggle('on', +r.dataset.u === unit));

  let gltf;
  try {
    gltf = await loader.loadAsync(`${BASE}/${entry.glb}`);
  } catch (e) {
    hudStatus(`<span class="bad">sd_${unit} 载入失败</span> ${String(e).slice(0, 160)}`);
    return;
  }
  const rigRaw = await fetchJson(`${BASE}/${entry.rig}`);
  const rig = rigRaw ? Cloth.normalizeRig(rigRaw) : null;

  disposeCurrent();

  const root = gltf.scene;
  scene.add(root);
  root.updateMatrixWorld(true); // 绑定姿态

  // ---- 收集蒙皮网格并按角色分派材质 ----
  const stencilRef = Shading.stencilRefFor(0); // 单角色场景:序号 0 → ref 4
  const parser = gltf.parser;
  const meshRecs = [];
  root.traverse((o) => {
    if (o.isSkinnedMesh || o.isMesh) {
      o.frustumCulled = false;
      meshRecs.push(o);
    }
  });

  const mats = [];
  const extrasFound = new Set();
  let eyeMesh = null, mouthMesh = null, eyeParams = null;
  const bodyMeshes = [];
  const primIndexByMesh = new Map();
  {
    const counters = new Map();
    for (const m of meshRecs) {
      const g = m.parent && m.parent.isGroup ? m.parent.name : m.name;
      const i = counters.get(g) || 0;
      primIndexByMesh.set(m, i);
      counters.set(g, i + 1);
    }
  }

  for (const mesh of meshRecs) {
    const old = mesh.material;
    const role = classifyRole(old.name, mesh.parent && mesh.parent.isGroup ? mesh.parent.name : mesh.name, primIndexByMesh.get(mesh));
    const dflt = ROLE_DEFAULTS[role];
    const ud = old.userData || {};
    for (const k of Object.keys(ud)) extrasFound.add(k);

    // Material extras may provide either a numeric usage or a body/face role string.
    let usage = extraOf(ud, ['characterShaderUsage', 'shaderUsage', '_CharacterShaderUsage']);
    if (usage === undefined) {
      const us = extraOf(ud, ['usage']);
      usage = typeof us === 'string' ? (us.toLowerCase() === 'body' ? 1 : 0) : us;
    }
    usage = usage ?? dflt.usage;
    const sh = extraOf(ud, ['shading']) || {};
    const override = sh.override ?? extraOf(ud, ['overrideShadingParameter', '_OverrideShadingParameter', 'override']) ?? dflt.override;
    const intensity = sh.intensity ?? extraOf(ud, ['localBodyShadingIntensity', '_LocalBodyShadingIntensity', 'shadingIntensity', 'intensity']) ?? dflt.intensity;
    const threshold = sh.edgeThreshold ?? extraOf(ud, ['localBodyShadingEdgeThreshold', '_LocalBodyShadingEdgeThreshold', 'shadingEdgeThreshold', 'threshold']) ?? dflt.threshold;
    const smoothness = sh.edgeSmoothness ?? extraOf(ud, ['localBodyShadingEdgeSmoothness', '_LocalBodyShadingEdgeSmoothness', 'shadingEdgeSmoothness', 'smoothness']) ?? dflt.smoothness;
    const brightness = extraOf(ud, ['brightness', '_Brightness']) ?? 1;

    let bodyMaskTex = null;
    const bmIdx = texIndex(extraOf(ud, ['bodyMaskTexture', 'bodyMaskTex', 'bodyMask', 'bodyMaskTexIndex', '_BodyMaskTex', 'maskTex', 'mask']));
    if (bmIdx !== null && parser) { try { bodyMaskTex = await parser.getDependency('texture', bmIdx); } catch { } }

    const mat = Shading.makeCharacterMaterial({
      name: `${old.name || role}·toon`,
      mainTex: old.map || null,
      bodyMaskTex,
      usage: +usage, override: +override, intensity: +intensity, threshold: +threshold, smoothness: +smoothness,
      brightness: +brightness, stencilRef,
    });
    mat.wireframe = wire;
    mesh.material = mat;
    mats.push(mat);

    if (role === 'eye') {
      eyeMesh = mesh;
      let ebTex = null;
      const ebIdx = texIndex(extraOf(ud, ['eyebrowTexture', 'eyebrowTex', 'eyeMaskTex', 'eyebrowTexIndex', '_EyebrowTex', 'eyeMask']));
      if (ebIdx !== null && parser) { try { ebTex = await parser.getDependency('texture', ebIdx); } catch { } }
      eyeParams = {
        mainTex: old.map || null,
        tex: ebTex,
        clip: +(extraOf(ud, ['eyebrowClip', '_EyebrowClip']) ?? dflt.eyebrowClip),
        alpha: +(extraOf(ud, ['eyebrowAlpha', '_EyebrowAlpha']) ?? dflt.eyebrowAlpha),
        usage: +usage, brightness: +brightness,
      };
    }
    if (role === 'mouth') mouthMesh = mesh;
    if (role === 'body') bodyMeshes.push(mesh);
  }

  // Query-controlled A/B switch for coplanar face/body draw order.
  if (params.get('mouthOrder') === 'after-body') {
    for (const mesh of bodyMeshes) mesh.renderOrder = 0;
    if (eyeMesh) eyeMesh.renderOrder = 10;
    if (mouthMesh) mouthMesh.renderOrder = 10;
  }

  // ---- 眉眼透发 overlay(仅 eye;mouth/body 默认黑 mask 恒 discard,不建) ----
  let overlay = null;
  if (eyeMesh) {
    const om = Shading.makeCharacterMaterial({
      name: 'eye·eyebrow-overlay',
      mainTex: eyeParams.mainTex,
      bodyMaskTex: null,
      usage: eyeParams.usage, override: 0, intensity: 1, threshold: 0, smoothness: 0,
      brightness: eyeParams.brightness, stencilRef,
      eyebrow: { tex: eyeParams.tex, clip: eyeParams.clip, alpha: eyeParams.alpha },
    });
    overlay = eyeMesh.clone();
    overlay.material = om;
    overlay.name = 'eye_eyebrow_overlay';
    overlay.renderOrder = 1000;       // 最后渲染(不透明+其余透明之后)
    overlay.frustumCulled = false;
    if (overlay.isSkinnedMesh) overlay.bind(eyeMesh.skeleton, eyeMesh.bindMatrix);
    eyeMesh.parent.add(overlay);
    mats.push(om);
  }

  // ---- Head reference (the documented local offset is measured in avatar space) ----
  const headWorld = Shading.HEAD_LOCAL.clone().applyMatrix4(root.matrixWorld);
  const headAnchor = root.getObjectByName('EX_IK_joint') || root.getObjectByName('Head') || root;
  const headOffset = headAnchor.worldToLocal(headWorld.clone());
  // ---- Overhead-item anchors (sprite and particle paths use different nodes) ----
  // The rig contract names Head, HeadRoot, Spine, and Hips as attachment points.
  // Sprite items use HeadRoot with zero local position. Particle items select Face,
  // Spine, or Hips from their view metadata; missing anchors use the head reference.
  const EMO_MOUNTS = { Face: 'Head', Spine: 'Spine', Hips: 'Hips', __sprite: 'HeadRoot' };
  const emoAnchors = {};
  for (const [key, nodeName] of Object.entries(EMO_MOUNTS)) {
    const anchor = new THREE.Object3D();
    anchor.name = `emoticon_anchor_${nodeName}`;
    const mount = root.getObjectByName(nodeName);
    if (mount) {
      anchor.position.set(0, 0, 0);
      mount.add(anchor);
    } else {
      if (!emoMountWarned) {
        emoMountWarned = true;
        console.warn(`[emoticon] missing ${nodeName} anchor; using the head reference`);
      }
      anchor.position.copy(headWorld);
      scene.add(anchor);
    }
    emoAnchors[key] = anchor;
  }
  const emoAnchor = emoAnchors.__sprite;   // Default anchor for sprite items and diagnostics
  Shading.setHeadPos(mats, headWorld);
  Shading.setNight(mats, night);
  Shading.setLightDir(mats, Shading.lightDirFromAngles(
    +$('rLightXZ').value, +$('rLightY').value)); // 换角色保留当前光向
  Shading.setDebugMode(mats, +$('selDebug').value);
  // 环境层开着:光照九项里的角色三项由现象决定,换角色要立刻重新接线(不然新角色吃默认光)。
  if (envOn && environment) environment.setCharacterMaterials(mats);

  // ---- 布料 ----
  let cloth = null;
  if (rig && rig.chains.length) {
    // glTF node index → Object3D(重名骨免疫;parser.associations 由 GLTFLoader 维护)
    const nodeByIndex = [];
    if (parser && parser.associations) {
      for (const [obj, assoc] of parser.associations) {
        if (assoc && assoc.nodes !== undefined) nodeByIndex[assoc.nodes] = obj;
      }
    }
    cloth = new Cloth.ClothSystem(root, rig, nodeByIndex);
    if (params.get('nocollide')) for (const rec of cloth.chains) rec.cols = []; // 诊断:关碰撞
  }

  // ---- 动画/段衔接 ----
  let clips = gltf.animations || [];
  const loopByName = new Map();
  if (entry.clips) for (const c of entry.clips) loopByName.set(c.name, c.loop);
  if (!clips.length) {
    // 角色 glb 无内嵌剪辑 → 回退到共享动作库(loop 标记来自库的索引)。
    const lib = await sharedMotionLibrary();
    if (lib) {
      clips = lib.clips;
      for (const [name, loop] of lib.loop) if (!loopByName.has(name)) loopByName.set(name, loop);
    }
  }
  const mixer = clips.length ? new THREE.AnimationMixer(root) : null;
  const clipByName = new Map(clips.map((c) => [c.name, c]));
  const families = Seg.groupClips(clips.map((c) => ({ name: c.name })));
  const segctl = mixer ? new Seg.SegmentController(mixer, clipByName, loopByName) : null;
  if (segctl) segctl.onchange = (s) => { updateClipUI(s); };

  // ---- 静止姿态 ----
  // 「不自动播放」不等于把绑定姿态(两臂平举的大字形)摊给用户看 —— 那既不是 rest 也不是第一帧。
  // 做法:取一个 idle 族的第 0 帧写进骨架,再把这个 action 冻住(paused)。
  // 不能 stop() 它:three.js 停用 action 时会 restoreOriginalState,骨架立刻弹回大字形。
  // 冻住的 action 交给 segctl 当 current —— phase 仍是 'idle'(所以不算「在播」,mixer 也不推),
  // 但用户点第一个动作族时能从这副静止姿态 crossFade 过去,而不是从大字形淡入。
  if (mixer && segctl) {
    const restFam = families.find((f) => /idle/i.test(f.base) && f.segs.L)
      || families.find((f) => f.segs.L) || families[0];
    const restName = restFam
      && (restFam.segs.L || restFam.plain || restFam.segs.S || restFam.segs.O || restFam.segs.E);
    const restClip = restName && clipByName.get(restName);
    if (restClip) {
      const ra = mixer.clipAction(restClip);
      ra.reset();
      ra.play();
      ra.paused = true;
      mixer.update(0);            // dt=0 也会把第 0 帧算出来写进骨架
      segctl.current = ra;        // 只借它做 crossFade 起点;phase 不动
    }
  }

  // ---- 表情 ----
  const unitId = (rig && rig.unitId) || (unit >= 100 ? unit - 100 : unit);
  const { eyeRow, lipRow } = Facial.patternsFor(unitId, tables,
    rig ? { eye: rig.defaultEye, mouth: rig.defaultMouth } : null);
  const eyeCell = Facial.cellFromAtlas(rig && rig.eyeAtlas, Facial.EYE_CELL);
  const mouthCell = Facial.cellFromAtlas(rig && rig.mouthAtlas, Facial.MOUTH_CELL);
  const facial = new Facial.FacialController({
    trace: true,
    applyEye: (idx) => {
      const o = Facial.cellOffset(idx, eyeCell);
      if (eyeMesh) eyeMesh.material.uniforms.uvOffset.value.set(o.x, o.y);
      if (overlay) overlay.material.uniforms.uvOffset.value.set(o.x, o.y);
    },
    applyMouth: (idx) => {
      const o = Facial.cellOffset(idx, mouthCell);
      if (mouthMesh) mouthMesh.material.uniforms.uvOffset.value.set(o.x, o.y);
    },
  });
  facial.setPatterns(eyeRow, lipRow);
  facial.setBlinkEnabled($('bBlink').classList.contains('on'));

  current = { unit, unitId, root, mats, overlay, mixer, cloth, facial, segctl, families, headAnchor, headOffset, clipByName, emoAnchor, emoAnchors, emoticon: null, emote: null, started: false };
  await emoticonReady;                   // 首个 emoticon 步要能查到条目(编排在下面才起跑)

  // ---- app / HUD / UI ----
  app.materials = mats.filter((m) => !m.defines || !('EYEBROW' in m.defines));
  app.overlayMesh = overlay; app.eyeMesh = eyeMesh; app.mouthMesh = mouthMesh;
  // onBeforeRender is reached only for objects that enter the renderer draw path.
  for (const mesh of [eyeMesh, mouthMesh, overlay]) {
    if (!mesh) continue;
    mesh.userData._selfcheckDraws = 0;
    mesh.onBeforeRender = () => { mesh.userData._selfcheckDraws++; };
  }
  app.facialPatch = { eyeCell, mouthCell };
  if (app._sabotagePatch) {
    if (eyeMesh) eyeMesh.visible = false;
    if (overlay) overlay.visible = false;
    if (mouthMesh) mouthMesh.visible = false;
  }
  app.mixer = mixer; app.clothSystem = cloth; app.facial = facial; app.segctl = segctl;
  window.app = app;                       // 供外部探针读取运行时状态
  app.clipByName = clipByName; app.stencilRef = stencilRef;
  app.probeBone = root.getObjectByName('Hips') || root.getObjectByName('Head');
  app.dataInfo.rig = !!rig; app.dataInfo.rigChains = rig ? rig.chains.length : 0;
  app.dataInfo.extrasFound = [...extrasFound].slice(0, 8);

  fitCamera(root);
  buildFamilyList(families);

  // 不自动播放:载入完停在绑定姿态,等用户点动作族(点行=整族 S→L,点小方块=单段)。
  // 这里只把「带 S+L 的探针族」记下来交给自检 —— 记下来 ≠ 播起来:currentFamily 保持 null,
  // 它的语义是「此刻有哪个族在跑」,静止时就该是空的。
  app.selfcheckFamily = families.find((f) => f.segs.S && f.segs.L) || null;
  if (AUTO_PLAY && segctl) {
    const pick = (PLAY_WANT && PLAY_WANT !== '1' && families.find((f) => f.base.includes(PLAY_WANT)))
      || app.selfcheckFamily || families.find((f) => f.segs.L) || families[0];
    if (pick) { perfAuto = false; startFamily(pick); }   // ?play= 播裸动作:自动演出不接管
  }

  // 表演编排:按 alone-actions.json 驱动动作 + 眼型 + 口型(动作与表情是独立通道,
  // 配对只存在于编排数据里 —— 没有它就只能挂一张静止默认脸)
  await perfReady;
  if (perfDoc && facial && segctl) {
    const byBase = new Map(families.map((f) => [f.base, f]));
    current.perf = new PerformancePlayer({
      doc: perfDoc, unitId, tables, facial, mode: perfMode,
      playMotion: (motion, phase) => {
        const family = byBase.get(motion);
        if (!family) return;
        startFamily(family, phase && family.segs[phase] ? phase : null);
      },
      playEmoticon: (name, showSeconds) => { if (emoticonAuto) playEmoticon(name, showSeconds); },
      hideEmoticon: () => hideEmoticon(),
    });
    app.perf = current.perf;
    buildScenarioList(current.perf);
    syncPerfUI();
    resetFacialSelects();                 // 换角色回默认脸,选择器同步归位
  }
  if (!(perfDoc && facial && segctl)) { buildScenarioList(null); syncPerfUI(); }

  if (!checksRan) {
    checksRan = true;
    applySabotage(app, SABOTAGE);
    runChecks(app, panel);
  }
  const emoWant = params.get('emoteitem');   // 直达单条气泡:检查或截图不用等编排掷到它
  if (emoWant) playEmoticon(emoWant);
  refreshHud(0);
}

// ---- 相机 ----
function fitCamera(obj) {
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const c = box.getCenter(new THREE.Vector3());
  const h = Math.max(size.y, 0.1);
  controls.target.set(c.x, c.y, c.z);
  camera.position.set(c.x + h * 0.4, c.y + h * 0.25, c.z + h * 2.2);
  controls.update();
}

// ---- UI ----
function hudStatus(html) { $('status').innerHTML = html; }

// 起播的唯一出入口:currentFamily 只在真的起播时写,自检据此判断「有没有动作在跑」;
// started 一旦为真就恒定推 mixer(和改动前一样),在此之前一帧都不推。
function startFamily(f, seg) {
  if (!current || !current.segctl) return;
  current.started = true;
  app.currentFamily = f;
  if (seg && f.segs[seg]) current.segctl.playSegment(f.segs[seg], f);
  else current.segctl.playFamily(f);
}

// 长列表统一的筛选:按 data-key 里的小写串匹配,顺手更新计数徽标。
// 用 hidden 属性而不是重建 DOM —— 行上挂着事件与选中态,重建等于把它们抖掉。
function applyFilter(boxId, countId, qRaw) {
  const box = $(boxId); if (!box) return;
  const q = (qRaw || '').trim().toLowerCase();
  const rows = box.querySelectorAll('[data-key]');
  let shown = 0;
  for (const r of rows) {
    const hit = !q || r.dataset.key.includes(q);
    r.hidden = !hit;
    if (hit) shown++;
  }
  const badge = $(countId);
  if (badge) badge.textContent = rows.length ? (shown === rows.length ? `${rows.length}` : `${shown}/${rows.length}`) : '';
}

function buildUnitList() {
  const box = $('list');
  box.innerHTML = '';
  for (const u of unitList) {
    const d = document.createElement('button');   // 用 button:Tab 能走到,回车/空格即触发
    d.type = 'button';
    d.className = 'uchip'; d.dataset.u = u.unit;
    d.textContent = u.unit;
    d.title = `sd_${u.unit} · unit ${u.unit - 100}`;
    d.onclick = () => loadUnit(u.unit);
    box.appendChild(d);
  }
  const badge = $('unitCount');
  if (badge) badge.textContent = unitList.length || '';
}

function buildFamilyList(families) {
  const box = $('clips');
  box.innerHTML = '';
  for (const f of families) {
    const d = document.createElement('div');
    d.className = 'fam'; d.dataset.base = f.base;
    const short = f.base.replace(/^mov_c[wm]_/, '');   // mov_cm_/mov_cw_ 是共有前缀,留着只会把
                                                       // 差异挤到省略号后面(窄栏里几行长得一模一样)
    d.dataset.key = f.base.toLowerCase();
    const segs = ['S', 'L', 'E', 'O'].filter((k) => f.segs[k]);
    // 族名与每个段各自是一个按钮:嵌套可点区域用 button 才能逐个 Tab 到,
    // 判段仍看 ev.target.dataset.seg,所以委托到行上的这一个 onclick 就够。
    const chips = segs.map((k) => `<button type="button" class="chip" data-seg="${k}" title="只播 ${f.base}_${k} 段">${k}</button>`).join('');
    d.innerHTML = `<button type="button" class="fname" title="${f.base}">${short}</button>`
      + `<span class="chips">${chips || '<span class="none">·</span>'}</span>`;
    d.onclick = (ev) => {
      const seg = ev.target.dataset && ev.target.dataset.seg;
      if (current && current.perf) current.perf.stopToIdle();
      if (perfAuto) { perfAuto = false; syncPerfUI(); }   // 试播裸动作期间自动演出不接管
      startFamily(f, seg);
    };
    box.appendChild(d);
  }
  if (!families.length) box.innerHTML = '<div class="empty">无动画</div>';
  applyFilter('clips', 'famCount', $('famFilter').value);
}

function updateClipUI(s) {
  document.querySelectorAll('#clips .fam').forEach((r) => r.classList.toggle('on', current && current.segctl && r.dataset.base === (s.family && s.family.base)));
  $('phase').textContent = s.clip ? `${s.clip}(${s.phase})` : '—';
}

$('famFilter').oninput = (e) => applyFilter('clips', 'famCount', e.target.value);

// ---- 控制条 ----
$('bPlay').onclick = (e) => {
  playing = !playing;
  e.target.classList.toggle('on', playing);
  e.target.textContent = playing ? '暂停' : '播放';
};
$('bStop').onclick = () => { if (current && current.segctl) current.segctl.stopToEnd(); };
$('rSpeed').oninput = (e) => {
  const k = e.target.value / 100;
  $('oSpeed').value = `${k.toFixed(2)}×`;
  if (current && current.mixer) current.mixer.timeScale = k;
};
$('bCloth').onclick = (e) => {
  clothOn = !clothOn;
  e.target.classList.toggle('on', clothOn);
  if (!clothOn && current && current.cloth) { current.cloth.restoreRest(); current.cloth.reset(); }
};
// ---- 气泡(emoticon)运行时 ----
// 同一时刻只挂一个 view(真运行时也只有一个 view 位)。收场两段式:hide() 进 end 段 → view 在
// end 段放完 + disposeDelaySeconds 之后自己 stop()(phase 回 idle、root 隐藏,对象留着可重播)。
// 所有权约定:view 归 viewer —— 见到 stop() 就立刻 dispose() 真回收(不做池化);另外留一道
// grace 兜底,renderer 侧哪天某个分支不再自己 stop(),粒子发射器也不会一直喷到 maxParticles。
function playEmoticon(name, showSeconds = null) {
  if (!current || !current.emoAnchors) return null;
  const item = emoticons && emoticons.items[name];
  if (!item) {                           // 名字对不上就如实报错,别拿别的条目冒充
    emoticonErr = emoticons ? `无条目 ${name}` : '无 emoticons 数据';
    console.warn('[emoticon]', emoticonErr);
    return null;
  }
  clearEmoticon();
  // Select the attachment from the view contract: sprite items use HeadRoot;
  // particle items use view.anchor (Face/Spine/Hips), with Hips as the neutral fallback.
  const wantKind = item.viewKind === 'sprite' ? '__sprite'
    : ((item.view && item.view.anchor) || 'Hips');
  const anchor = current.emoAnchors[wantKind] || current.emoAnchors.Hips;
  if (!current.emoAnchors[wantKind]) {
    console.warn(`[emoticon] ${name} 的 view.anchor=${JSON.stringify((item.view || {}).anchor)} `
      + '不在 Face/Spine/Hips 里,落到 Hips');
  }
  let view;
  try {
    // worldParent=场景(World 空间的发射器要挂世界,不跟着挂点走)
    view = emoticons.create(name, { anchor, worldParent: scene });
  } catch (e) {
    emoticonErr = `${name}: ${String(e).slice(0, 120)}`;
    console.error('[emoticon]', e);
    return null;
  }
  if (!view) { emoticonErr = `无条目 ${name}`; return null; }
  const sec = +showSeconds > 0 ? +showSeconds : null;   // null=不自动收(手动播放/等 hideEmoticon 步)
  view.play(sec);
  current.emoticon = view;
  // keepPosition gates rotation, not position, so it is passed to updateEmoticon.
  // refNode is the billboard reference node; Hips is used for the default presentation path.
  current.emote = {
    name, kind: item.viewKind, seconds: sec, anchor,   // name 用参数:items[name] 里不带 name 字段(注册表 create 才补)
    keepPosition: !!(item.view && item.view.keepPosition),
    refNode: current.emoAnchors && current.emoAnchors.Hips,
    endClock: 0,
    grace: (+(item.clips && item.clips.end && item.clips.end.duration) || 0) + emoticonDelay,
  };
  emoticonErr = null;
  app.emoticon = view;
  selectedEmoticon = name;
  const sel = $('selEmoticon'); if (sel) sel.value = name;
  return view;
}
function hideEmoticon() {                // 收场:进 end 段,回收由 updateEmoticon 兜
  if (!current || !current.emoticon) return;
  current.emoticon.hide();
  if (current.emote) current.emote.endClock = 0;
}
function clearEmoticon() {               // 立即回收(换条目/换角色/放完)
  if (!current) return;
  const view = current.emoticon;
  current.emoticon = null; current.emote = null; app.emoticon = null;
  if (!view) return;
  try { view.dispose(); } catch (e) { console.error('[emoticon] dispose', e); }
}
const _paq = new THREE.Quaternion();
const _emoA = new THREE.Vector3(), _emoB = new THREE.Vector3(), _emoC = new THREE.Vector3();
const _emoE = new THREE.Euler();
const _emoQ = new THREE.Quaternion();
const _emoM4 = new THREE.Matrix4();
const _emoUp = new THREE.Vector3(0, 1, 0), _emoZero = new THREE.Vector3();
// glTF uses the reflected right-handed coordinate contract. Skeleton anchors already
// use that frame; particle-local transforms are reflected when the item is created.
// Sprite roots are camera-aligned at runtime and therefore keep their local item data.

/** Construct a rotation with local +Z on forward and +Y as the implicit up axis. */
function lookRotation(forward, out) {
  // Matrix4.lookAt places +Z on normalize(eye - target); using forward as eye
  // and the origin as target gives the desired single-vector construction.
  _emoM4.lookAt(forward, _emoZero, _emoUp);
  return out.setFromRotationMatrix(_emoM4);
}

/**
 * Particle billboard rule from the public view contract.
 * With keepPosition enabled, compare camera and reference-node X/Z vectors,
 * compute the signed angle, and apply it around the item's local X axis.
 * The Y component is intentionally ignored and degenerate vectors produce zero.
 */
function particleYawRad(refNode, cam) {
  refNode.updateWorldMatrix(true, false);
  refNode.getWorldPosition(_emoA);
  _emoB.set(0, 0, 1).applyQuaternion(refNode.getWorldQuaternion(_paq));
  cam.getWorldPosition(_emoC);
  const dx = _emoC.x - _emoA.x, dz = _emoC.z - _emoA.z;
  const fx = _emoB.x, fz = _emoB.z;
  const len = Math.sqrt((dx * dx + dz * dz) * (fx * fx + fz * fz));
  let deg = 0;
  if (len >= 1e-15) {
    deg = Math.acos(Math.min(1, Math.max(-1, (dx * fx + dz * fz) / len))) * 57.29578;
  }
  const signed = (dx * fz - dz * fx) < 0 ? -deg : deg;
  return signed * -0.017453292;
}
function updateEmoticon(dt) {
  const view = current && current.emoticon, st = current && current.emote;
  if (!view || !st) return;
  // Attached items follow their selected anchor. Sprite items use a world-facing
  // rotation, particles with keepPosition use the single-axis rule below, and
  // particles without it retain their authored local rotation.
  if (EMO_FACE_CAMERA) {
    const top = current.emoticon && current.emoticon.nodeMap.get('');
    if (top) {
      if (st.kind === 'sprite') {
        top.getWorldPosition(_emoA);
        camera.getWorldPosition(_emoC);
        // The camera-facing vector points from the item toward the camera. This
        // keeps local +Z toward the viewer in the three.js camera convention.
        _emoB.subVectors(_emoC, _emoA);
        if (_emoB.lengthSq() > 1e-12) {
          lookRotation(_emoB.normalize(), _emoQ);
          const p = top.parent;
          if (p) { p.getWorldQuaternion(_paq); top.quaternion.copy(_paq).invert().multiply(_emoQ); }
          else top.quaternion.copy(_emoQ);
        }
      } else if (st.keepPosition) {
        // The reflected coordinate frame reverses the signed angle; the local axis remains X.
        _emoE.set(-particleYawRad(st.refNode || current.root, camera), 0, 0);
        top.quaternion.setFromEuler(_emoE);
      }
    }
  }
  view.update(dt);
  if (view.hidden) { clearEmoticon(); return; }         // view 自己 stop() 了(phase=idle)
  if (view.endRequested) {                              // 已进 end 段(hide 或 showSeconds 到点)
    st.endClock += dt;
    if (st.endClock >= st.grace) clearEmoticon();
  }
}
$('bEmoticonPlay').onclick = () => { playEmoticon($('selEmoticon').value || selectedEmoticon); };
$('bEmoticonHide').onclick = () => hideEmoticon();
$('bEmoticonAuto').onclick = (e) => {
  emoticonAuto = !emoticonAuto;
  e.target.classList.toggle('on', emoticonAuto);
  if (!emoticonAuto) hideEmoticon();     // 关自动:把编排挂上的气泡收掉,别让它一直挂在头上
};
$('selEmoticon').onchange = (e) => { selectedEmoticon = e.target.value; };
function fillEmoticonSelect() {
  const sel = $('selEmoticon'); if (!sel) return;
  if (!emoticonItems.length) { sel.innerHTML = '<option value="">头顶件:无数据</option>'; sel.disabled = true; return; }
  sel.disabled = false;
  sel.innerHTML = emoticonItems.map((x) => `<option value="${x.name}">${x.name.replace(/^fx_emote_/, '')} · ${x.viewKind}</option>`).join('');
  selectedEmoticon = emoticonItems[0].name; sel.value = selectedEmoticon;
  emoFind('');
}
// 53 个头顶件的「搜索」做成输入即定位,而不是过滤掉 option:
// selEmoticon.value 是外部探针直接按名字写的接口,option 一旦被过滤掉,写 value 会静默失败、
// 可能仍播放旧值 —— 因此让下拉始终是全量,输入框负责跳到第几个匹配(回车翻下一个)。
let emoHits = [], emoCursor = 0;
function emoFind(qRaw, advance = false) {
  const q = (qRaw || '').trim().toLowerCase();
  const note = $('emoMatch');
  emoHits = q ? emoticonItems.filter((x) => x.name.toLowerCase().includes(q)) : [];
  if (!q) { if (note) note.innerHTML = '&nbsp;'; emoCursor = 0; return; }
  if (!emoHits.length) { if (note) note.innerHTML = '<span class="warn">无匹配</span>'; return; }
  emoCursor = advance ? (emoCursor + 1) % emoHits.length : 0;
  const hit = emoHits[emoCursor];
  const sel = $('selEmoticon');
  if (sel) { sel.value = hit.name; selectedEmoticon = hit.name; }
  if (note) note.textContent = `${emoCursor + 1}/${emoHits.length} · ${hit.name.replace(/^fx_emote_/, '')}`;
}
$('emoFilter').oninput = (e) => emoFind(e.target.value);
$('emoFilter').onkeydown = (e) => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  emoFind(e.target.value, true);
};
$('bClothReset').onclick = () => { if (current && current.cloth) current.cloth.reset(); };
$('bBlink').onclick = (e) => {
  e.target.classList.toggle('on');
  if (current) current.facial.setBlinkEnabled(e.target.classList.contains('on'));
};
$('bSpeak').onclick = (e) => {
  app.userTouchedSpeak = true;
  e.target.classList.toggle('on');
  if (current) current.facial.setSpeaking(e.target.classList.contains('on'));
};
$('bNight').onclick = (e) => {
  night = !night;
  e.target.classList.toggle('on', night);
  if (current && !envOn) Shading.setNight(current.mats, night);   // 环境层接管光色时不插手
};
// Light direction controls default to angleXZ=48° / angleY=30°.
// 滑块旁的读数用等宽字体、固定宽度:拖动时数字不该把旁边的控件挤来挤去。
const applyLight = () => {
  const xz = +$('rLightXZ').value, y = +$('rLightY').value;
  $('oLightXZ').value = `${xz}°`;
  $('oLightY').value = `${y}°`;
  const v = Shading.lightDirFromAngles(xz, y);
  // 环境层开着时光照由现象决定:滑块此刻不生效(提示行照实说,不静默失效)。
  if (current && !envOn) Shading.setLightDir(current.mats, v);
};
$('rLightXZ').oninput = applyLight;
$('rLightY').oninput = applyLight;
$('bLightReset').onclick = () => { $('rLightXZ').value = 48; $('rLightY').value = 30; applyLight(); };
$('selDebug').onchange = (e) => { if (current) Shading.setDebugMode(current.mats, +e.target.value); };
$('bAutoPerf').onclick = () => { perfAuto = !perfAuto; syncPerfUI(); };
// ---- 环境面板 ----
$('bEnv').onclick = async () => {
  if (!environment) return;
  if (!envOn && !environment.to) {            // 还没选过现象:开就落到第一个
    await setEnvPhenomenon(environment.names[0], 0);
    return;
  }
  enableEnv(!envOn);
};
$('bEnvIndoor').onclick = (e) => {
  if (!environment) return;
  const on = !e.target.classList.contains('on');
  e.target.classList.toggle('on', on);
  environment.setIndoor(on);               // 两级查找的第一级(带覆盖的那个站点)
  syncEnvUI();
};
$('bEnvParticles').onclick = (e) => {
  if (!environment) return;
  const on = !e.target.classList.contains('on');
  e.target.classList.toggle('on', on);
  environment.setParticles(on);
};
$('bEnvPost').onclick = (e) => {
  if (!environment) return;
  const on = !e.target.classList.contains('on');
  e.target.classList.toggle('on', on);
  environment.setPost(on);
};
$('bEnvSky').onclick = (e) => {
  if (!environment) return;
  const on = !e.target.classList.contains('on');
  e.target.classList.toggle('on', on);
  environment.setSkyVisible(on);
};
$('bEnvGround').onclick = (e) => {
  if (!environment) return;
  const on = !e.target.classList.contains('on');
  e.target.classList.toggle('on', on);
  environment.setGroundVisible(on);
};
$('selEnvSite').onchange = (e) => { if (environment) environment.setSite(e.target.value); };
$('scnFilter').oninput = (e) => applyFilter('scenarios', 'scnCount', e.target.value);
$('selPerfMode').onchange = (e) => {
  perfMode = e.target.value;
  // 换策略只影响自动选取:正在自动播的段停到 idle 按新策略重选;手动点播不打断。
  if (current && current.perf) {
    current.perf.mode = perfMode;
    if (!current.perf.manual) current.perf.stopToIdle();
  }
};
// 自动演出默认开;?perf=0 关(手动点播仍可用)。
if (params.get('perf') === '0') perfAuto = false;
if (params.get('perfmode')) { perfMode = params.get('perfmode'); $('selPerfMode').value = perfMode; }
// ?emote=0:编排的 emoticon 步不驱动气泡(只留手动);?emoteitem=<名>:载入后直接挂一个(检查或截图)
if (params.get('emote') === '0') { emoticonAuto = false; $('bEmoticonAuto').classList.remove('on'); }
// Camera-facing behavior is enabled by default; =0 leaves world orientation for comparison.
const EMO_FACE_CAMERA = params.get('emoteface') !== '0';
if (params.get('debug')) $('selDebug').value = params.get('debug'); // URL 直达诊断模式
// ?freeze=<秒>:载入后把动画推进到固定时刻并暂停 —— 便于比较固定画面
const FREEZE = params.get('freeze') !== null && params.get('freeze') !== undefined
  ? parseFloat(params.get('freeze') || '0.5') : null;
// 载入即起播的**唯一**入口,全部要 URL 明写(默认路径一律停在静止姿态等用户点):
//   ?play=1 / ?play=<族名片段>  —— 便于获得确定的动作画面
//   ?freeze=<秒>                —— 要冻一个可比帧,前提是有动作在跑
//   ?sabotage=anim              —— 故障注入会让「动画推进」检查失败;没有动作时该检查没有测量对象
const PLAY_WANT = params.get('play');
const AUTO_PLAY = PLAY_WANT !== null || FREEZE !== null || SABOTAGE.includes('anim');
$('bWire').onclick = (e) => {
  wire = !wire;
  e.target.classList.toggle('on', wire);
  if (current) for (const m of current.mats) m.wireframe = wire;
};

// ---- 状态条 ----
// 从「一大块 pre 文本」改成一行紧凑的键值格:每格 键(小号大写)+ 值(等宽),
// 靠 flex 的行距自动折行。信息一项没少,但不再占掉画面。
let fpsAcc = 0, fpsN = 0, fps = 0, hudT = 0;
const cell = (k, v) => `<span class="cell"><span class="k">${k}</span><span class="v">${v}</span></span>`;
function refreshHud(dt) {
  fpsAcc += dt; fpsN++;
  hudT += dt;
  if (hudT < 0.25) return;
  fps = fpsN / Math.max(fpsAcc, 1e-6); fpsAcc = 0; fpsN = 0; hudT = 0;
  if (!current) return;
  const info = lastFrameInfo;   // 上一帧的整帧统计(见 lastFrameInfo 的注释)
  const cs = current.cloth ? current.cloth.stats() : null;
  const f = current.facial;
  const em = current.emote && current.emoticon ? current.emoticon.stats() : null;
  hudStatus(
    `<span class="cell"><span class="v unit">sd_${current.unit}</span></span>`
    + cell('perf', `<span class="dim">${fps.toFixed(0)} fps · ${info.calls} calls · ${info.triangles} tris</span>`)
    + cell('动作', `<span class="${current.segctl && current.segctl.phase !== 'idle' ? 'ok' : 'dim'}">${$('phase').textContent}</span>`)
    + cell('布料', cs
      ? `<span class="ok">${cs.chains} 链 ${cs.particles} 粒</span> <span class="dim">sub=${cs.substeps} disp=${(cs.maxDisp * 1000).toFixed(0)}mm nan=${cs.nanResets}</span>`
      : '<span class="dim">无 rig 数据</span>')
    + cell('表情', `<span class="ok">eye#${f.currentEyeIndex} mouth#${f.currentMouthIndex}</span> <span class="dim">${f.eyeRow ? f.eyeRow.pattern : ''}/${f.lipRow ? f.lipRow.name : ''}</span>`)
    + cell('头顶件', (em
      ? `<span class="ok">${current.emote.name.replace(/^fx_emote_/, '')}</span> <span class="dim">${em.kind} ${em.phase || '—'}`
        + `${em.kind === 'particle' ? ` ${em.live} 粒(峰 ${em.peak})` : ` ${em.sprites} 片`}${current.emote.seconds ? ` ${current.emote.seconds}s` : ''}</span>`
        + `${em.suppressed ? ` <span class="warn">停发 ${em.suppressed} 发(${Object.entries(em.suppressedShapes)
          .map(([k, v]) => `${k}x${v}`).join(' ')} 无发射公式)</span>` : ''}`
      : (emoticonErr ? `<span class="bad">${emoticonErr}</span>` : '<span class="dim">无</span>'))
      + `${emoticonAuto ? '' : ' <span class="dim">auto 关</span>'}`)
    + cell('表演', current && current.perf
      ? (current.perf.idle
        ? `<span class="dim">待机${perfAuto ? '(自动)' : ''}</span>`
        : `<span class="ok">${current.perf.manual ? '点播' : perfMode}</span>`
          + `<span class="dim"> ${(current.perf.current && current.perf.current.id) ?? ''}</span>`)
      : '<span class="dim">无编排数据</span>')
    + cell('环境', (() => {
      if (!environment) return `<span class="${envErr ? 'bad' : 'dim'}">${envErr || '无现象数据'}</span>`;
      if (!envOn) return '<span class="dim">关</span>';
      const st = environment.status();
      const sup = st.particles.suppressed
        ? ` <span class="warn">停发 ${st.particles.suppressed} 发(${Object.entries(st.particles.suppressedShapes)
          .map(([k, v]) => `${k}x${v}`).join(' ')} 无发射公式)</span>` : '';
      // 摘掉的渲染器与停发是两件事:摘掉 = 原版本来就不画(渲染器关着/节点关着),
      // 停发 = 我们没有它的发射公式。混成一个数会把「照原版不画」读成缺失。
      const sk = st.particles.skipped;
      const skipped = sk && sk.total
        ? ` · <span class="dim">不画 ${sk.total}(渲染器关 ${sk.disabled} 节点关 ${sk.inactive})</span>` : '';
      return `<span class="ok">${st.phenomenon}</span> <span class="dim">`
        + `${st.usedOverride ? '覆盖' : st.site}${st.fadingFrom ? ` 淡化 ${(st.fade * 100).toFixed(0)}%` : ''}`
        + ` · 雾 ${st.fog.enabled ? 'on' : 'off'} · 粒子 ${st.particles.live}/${st.particles.emitters} 发`
        + `${st.post.enabled ? ` · 后处理 ${st.post.passes.length} 趟` : ' · 后处理关'}</span>${skipped}${sup}`;
    })())
  );
}

// ---- 帧循环(顺序:mixer → 布料骨复位 rest → 矩阵 → 读动画位 → solver → 写回 → 渲染) ----
const _hp = new THREE.Vector3();
let nanInjected = false;
let dbgFrame = 0;
renderer.setAnimationLoop(() => {
  const dt = clock.getDelta();
  renderer.info.reset();   // 整帧统计:autoReset 关着,这里清一次(见 renderer 初始化处)
  if (FREEZE !== null && current && current.mixer && !current._froze) {
    current.mixer.update(FREEZE);   // 推进到固定时刻
    playing = false;                // 之后冻结
    current._froze = true;
  }
  if (current) {
    app.frameCount = (app.frameCount || 0) + 1;   // facial.speak 那项自检要用观测帧数判断
                                                  // 「测不到」还是「不成立」,之前一直没人给它计数
    if (playing && current.perf) current.perf.update(dt);
    // 没起播过就不推 mixer:AnimationMixer.update 连时钟一起推,没有 action 也会让 mixer.time
    // 往上走 —— 那会让「静止」在外部看起来像在跑。有 action 之后就恒定推进,和以前一样。
    if (playing && current.mixer && current.started) current.mixer.update(dt);
    if (clothOn && current.cloth) current.cloth.restoreRest();
    scene.updateMatrixWorld(true);
    if (current.headAnchor) {
      _hp.copy(current.headOffset);
      current.headAnchor.localToWorld(_hp);
      Shading.setHeadPos(current.mats, _hp);
      if (envOn && environment) environment.setSubject(_hp);   // 落影投射体跟着头部参考点
    }
    if (playing) updateEmoticon(dt);   // 朝相机 + 段推进 + 回收(位置由 HeadRoot 挂点带,不用同步)
    if (clothOn && current.cloth) {
      if (app._sabotageNaN && !nanInjected && current.cloth.chains.length && current.cloth.chains[0].solver.inited) {
        const s0 = current.cloth.chains[0].solver;
        s0.pos[s0.pos.length - 1] = NaN; // 链尾 moving 顶点(fixed 槽会被动画覆写自愈,注了白注)
        nanInjected = true;
      }
      current.cloth.step(dt);
      if (params.get('clothdebug') && (++dbgFrame % 60) === 0) {
        const worst = [...current.cloth.chains].sort((a, b) => b.solver.lastMaxDisp - a.solver.lastMaxDisp)[0];
        const s = worst.solver;
        const per = [];
        for (let i = 0; i < s.n; i++) {
          const d = Math.hypot(s.pos[3 * i] - worst.anim[3 * i], s.pos[3 * i + 1] - worst.anim[3 * i + 1], s.pos[3 * i + 2] - worst.anim[3 * i + 2]);
          per.push(`${worst.chain.bones[i]}:${(d * 1000).toFixed(1)}`);
        }
        console.log('[clothdbg]', current.cloth.chains.map((r) => `${r.chain.name}=${(r.solver.lastMaxDisp * 1000).toFixed(1)}`).join(' '));
        console.log('[clothdbg-worst]', worst.chain.name, per.join(' '));
      }
    }
    current.facial.update();
  }
  if (envOn && environment) environment.update(dt);   // 淡化推进 + 天空跟随 + 环境粒子
  refreshHud(dt);
  controls.update();
  // 环境层的后处理链接管最终绘制;它没接管(关掉/无档案)时照常直画到画布。
  if (!(envOn && environment && environment.render())) renderer.render(scene, camera);
  // Negative self-check path: emulate a post-process buffer writing the wrong height
  // back into the renderer. Normal rendering never takes this path.
  if (app._sabotageResize) renderer.setSize(view.clientWidth, view.clientHeight + 24, false);
  // 绘制之后拍一份整帧统计给 HUD 用。HUD 在绘制**之前**刷新(它要用同一帧的 dt),
  // 而 autoReset 关着、计数器在帧首清零 —— 直接读就永远是 0。读上一帧的快照才是整帧真值。
  lastFrameInfo = { calls: renderer.info.render.calls, triangles: renderer.info.render.triangles };
});

function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, app._sabotageResize ? h + 24 : h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
// 画面是网格里的一格,宽高不只随窗口变(右栏展开自检、栏宽随断点变都会动),
// 所以直接盯这一格的尺寸,别只听 window.resize。
if (typeof ResizeObserver === 'function') new ResizeObserver(resize).observe(view);
resize();
app._resize = resize;

// 自检里「动画推进 / 段衔接」两项需要有动作在跑才测得到。播一遍带 S+L 的探针族,
// 那两项自己会复测(逻辑在 selfcheck 侧盯着,这里只负责起播);走预览语义,编排挂起。
$('selEye').onchange = applyFacialSelection;
$('selMouth').onchange = applyFacialSelection;
$('bCheckMotion').onclick = () => {
  const fam = app.currentFamily || app.selfcheckFamily;
  if (!fam) return;
  if (current && current.perf) current.perf.stopToIdle();
  if (perfAuto) { perfAuto = false; syncPerfUI(); }
  startFamily(fam);
};

// ---- 启动 ----
(async () => {
  const tRaw = await fetchJson(`${BASE}/facial-tables.json`);
  tables = (tRaw && Facial.normalizeTables(tRaw)) || Facial.fallbackTables();
  fillFacialSelects();
  app.dataInfo.tables = tables.counts;
  app.dataInfo.tablesFallback = !!tables.fallback;

  manifest = await fetchJson(`${BASE}/manifest.json`);
  if (manifest && Array.isArray(manifest.units) && manifest.units.length) {
    unitList = manifest.units.map((u) => ({
      unit: +u.unit, glb: u.glb || `sd_${u.unit}.glb`, rig: u.rig || `sd_${u.unit}.rig.json`, clips: u.clips || null,
    }));
    app.dataInfo.manifest = true;
  } else {
    unitList = FALLBACK_UNITS.map((u) => ({ unit: u, glb: `sd_${u}.glb`, rig: `sd_${u}.rig.json`, clips: null }));
  }
  app.dataInfo.unitCount = unitList.length;
  buildUnitList();
  const want = +params.get('unit') || unitList[0].unit;
  await loadUnit(unitList.some((u) => u.unit === want) ? want : unitList[0].unit);

  // 环境层:列表总是建(缺数据就显示缺失原因)。`?env=0` 明确关掉,`?env=<资产名>` 直达一个现象。
  await envReady;
  app.dataInfo.phenomena = environment ? environment.names.length : 0;
  buildEnvList();
  if (environment && envWant && envWant !== '0') {
    const hit = environment.names.includes(envWant)
      ? envWant
      : environment.names.find((n) => n.includes(envWant));
    if (hit) await setEnvPhenomenon(hit, 0);
    else envErr = `?env=${envWant} 不在现象名单里`;
  }
  syncEnvUI();
})();
