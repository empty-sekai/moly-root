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
import { createAudioController } from './audio.js';

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
// ---- 音频(现象层)----
// 独立控制器:数据目录 + 播放(循环区间按产物秒值)。「播什么」全部按 index 的
// bgms / siteSounds / siteBgms / siteSoundFallbacks 行路由,不从这里猜映射。
const audio = createAudioController({
  base: BASE,
  cueOnly: params.get('audiocueonly') === '1',   // 寻址对照(调试用):重复 cue 只留裸名地址
});
audio.configure({ siteIdFor: siteIdOfAudio });
audio.init();
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
let cameraMode = 'orbit';
const freecam = {
  active: false,
  yaw: 0,
  pitch: 0,
  dragging: false,
  pointerId: null,
  lastX: 0,
  lastY: 0,
  keys: new Set(),
  shift: false,
  slow: false,
};
const FREECAM_SPEED = 3;
// 两个修饰键各自是一个倍率,**相乘**而不是互相覆盖:同时按住就回到 1 倍。
// 这样不必规定「谁优先」,行为也不会随按下顺序变。
const FREECAM_FAST = 4;
const FREECAM_SLOW = 0.25;
// 减速键用 Z,不用 Ctrl:`Ctrl+W` 是浏览器的关标签页,`preventDefault()` 拦不住它,
// 而 W 恰好是最常按的移动键。Z 在左手小指位、贴着 WASD,且不构成任何浏览器组合键。
// 注意它与 Shift 有一处本质不同:**Z 是可打印字符**,所以它必须先过「焦点在输入框」
// 那道闸再判——否则在筛选框里打一个 z 会既触发减速又被吞掉。
const FREECAM_SLOW_KEYS = Object.freeze({ KeyZ: true, z: true, Z: true });
const FREECAM_PITCH_LIMIT = 89 * Math.PI / 180;
const FREE_MOVE_KEYS = Object.freeze({
  KeyW: 'forward', KeyS: 'back', KeyA: 'left', KeyD: 'right',
  KeyQ: 'down', KeyE: 'up', KeyR: 'down', KeyF: 'up',
  w: 'forward', s: 'back', a: 'left', d: 'right',
  q: 'down', e: 'up', r: 'down', f: 'up',
});
const _freeEuler = new THREE.Euler(0, 0, 0, 'YXZ');
const _freeForward = new THREE.Vector3();
const _freeRight = new THREE.Vector3();
const _freeMove = new THREE.Vector3();
const grid = new THREE.GridHelper(4, 16, 0x2c2f3d, 0x21232e);
scene.add(grid);
scene.add(camera);   // 相机族环境粒子挂在相机下,相机必须在场景图里

const loader = new GLTFLoader();
const clock = new THREE.Clock();

// ---- app 状态(selfcheck 消费) ----
const app = {
  THREE, renderer, scene, camera, controls,
  cameraMode,
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
  audio,
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
// 表里三族行,只有一族对着 SD 角色自己的图集:
//   base   —— 本图集的行,闭合格全落在真闭眼那几格上;
//   egg*   —— 家具头像(蛋)的行,寻的是另一张图集(每个蛋自己的纹理),
//             闭合格会指到本图集的睁眼格,选了也画不对名字;
//   uniq*  —— 本角色专属槽:每个角色的图集在这一格画着自己专属的脸,
//             后缀就是角色的英文名,只有当前角色是那个角色时才成立,
//             所以按角色过滤(整族丢弃会让所有角色都用不上自己的专属脸)。
// 眼表写作 uniq1_/uniq2_(两个槽),口表只有一个槽,写作 uniq_(不带序号)。
// egg 族整族扣下:逐行以禁用项陈列(可看得见、数得出、选不了),数目与理由写进标题。
const FACIAL_FAMILY = /^(egg\d|uniq\d?)_/;

// 故障注入(自检探针用):?sabotageFacial=cellShift 把眼睛整体画偏一格,
// 判据必须因此转红;=labelShift 把标签里的格号印错一格,标签检查必须转红。
const FACIAL_SABOTAGE = params.get('sabotageFacial');

function facialFamily(name) {
  const m = FACIAL_FAMILY.exec(name);
  return m ? m[1] : 'base';
}
function uniqSuffix(name) {
  const fam = facialFamily(name);
  if (!fam.startsWith('uniq')) return '';
  return fam === 'uniq' ? name.slice(5) : name.slice(fam.length + 1);
}

// 一行表情是「开格/闭格」一对;把格号写进标签,名字指向哪一格当场看得见。
function cells(row) {
  const shift = FACIAL_SABOTAGE === 'labelShift' ? 1 : 0;
  if (!row) return '?';
  return `开${row.open + shift}/闭${row.close > 0 ? row.close + shift : '-'}`;
}
function blinkMark(row) {
  return row.blink ? '·眨眼' : '·不眨眼';
}

function fillFacialSelects(unitId) {
  const se = $('selEye'), sm = $('selMouth');
  if (!se || !sm || !tables) return;
  se.innerHTML = ''; sm.innerHTML = '';
  const name = Facial.uniqNameOf(unitId || 0);
  const group = (sel, tag) => {
    const g = document.createElement('optgroup');
    g.label = tag;
    sel.appendChild(g);
    return g;
  };
  const option = (sel, text, value, extra) => {
    const o = new Option(text, value, false, false);
    if (extra) for (const k in extra) o[k] = extra[k];
    sel.appendChild(o);
    return o;
  };
  let heldEye = 0, heldLip = 0;

  const gEyeBase = group(se, '基础眼型');
  option(se, '眼型:默认', '');
  for (const [nm] of tables.eye) {
    const fam = facialFamily(nm);
    if (fam === 'base') option(gEyeBase, `眼型:${nm} ·${cells(tables.eye.get(nm))}${blinkMark(tables.eye.get(nm))}`, nm);
    else if (fam.startsWith('egg')) heldEye++;
  }
  // 只发当前角色自己的专属行;名字后缀 != 当前角色英文名的行不发。
  let gEyeUniq = null;
  for (const [nm, row] of tables.eye) {
    const fam = facialFamily(nm);
    if (!fam.startsWith('uniq')) continue;
    if (name && uniqSuffix(nm) === name) {
      if (!gEyeUniq) gEyeUniq = group(se, `专属眼型(${name})`);
      option(gEyeUniq, `眼型:专属(${name}) ·${cells(row)}${blinkMark(row)}`, nm);
    }
  }
  const gEyeEgg = group(se, `家具头像(蛋) — 指向另一张图集,与角色图集不对应,不可选(眼 ${heldEye} 行)`);
  for (const [nm, row] of tables.eye) {
    if (facialFamily(nm).startsWith('egg')) option(gEyeEgg, `眼型:${nm} ·${cells(row)}`, nm, { disabled: true });
  }

  const gMouthBase = group(sm, '基础口型');
  option(sm, '口型:默认', '');
  for (const [nm] of tables.lip) {
    const fam = facialFamily(nm);
    if (fam === 'base') option(gMouthBase, `口型:${nm} ·${cells(tables.lip.get(nm))}`, nm);
    else if (fam.startsWith('egg')) heldLip++;
  }
  let gMouthUniq = null;
  for (const [nm, row] of tables.lip) {
    if (!facialFamily(nm).startsWith('uniq')) continue;
    if (name && uniqSuffix(nm) === name) {
      if (!gMouthUniq) gMouthUniq = group(sm, `专属口型(${name})`);
      option(gMouthUniq, `口型:专属(${name}) ·${cells(row)}`, nm);
    }
  }
  const gMouthEgg = group(sm, `家具头像(蛋) — 指向另一张图集,与角色图集不对应,不可选(口 ${heldLip} 行)`);
  for (const [nm, row] of tables.lip) {
    if (facialFamily(nm).startsWith('egg')) option(gMouthEgg, `口型:${nm} ·${cells(row)}`, nm, { disabled: true });
  }

  // egg 行逐行陈列,数量写进标题:不是静默扣下,而是查得见、选不了。
  const held = (n, what) => n
    ? `${what} ${n} 行属家具头像(蛋)的图集,名字指向的格子在另一张纹理上,选了会对不上;`
    : '';
  se.title = `眼型标签(表情表行);选择即应用并停用自动演出,眨眼取所选行的开/闭格。${held(heldEye, '眼表另有')}`
    + (name ? `当前角色专属:${name}` : (unitId > 0 ? '该角色没有专属表情行。' : '未加载角色,专属行不显示;换角色后重排。'));
  sm.title = `口型标签(表情表行);选择即应用并停用自动演出,说话取所选行的开/闭格。${held(heldLip, '口表另有')}`
    + (name ? `当前角色专属:${name}` : (unitId > 0 ? '该角色没有专属表情行。' : '未加载角色,专属行不显示;换角色后重排。'));
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
  // 手选口型时停在它的开格:一行口型是「开/闭」一对格子,静止律显示的是闭格,
  // 而闭格只有几种几乎一样的线条,名字在上面看不出来。演出与说话不受影响。
  current.facial.holdMouthOpen(!!lipName);
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

// ---- 音频(现象层) ----
// 站点键 → siteId:站点表行里显式写着 id,按「场景键 + siteType 名」接。
// 音频只认 siteId(站点行的号),不认站点键的拼法。
function siteIdOfAudio(siteKey) {
  if (!environment) return null;
  const sv = environment.siteView;
  const row = sv.rowFor(siteKey);
  if (!row || !row.placement) return null;
  const rows = ((sv.placement || {}).sites) || [];
  const hit = rows.find((r) => r.scene === row.scene && r.siteType === row.siteType);
  return hit && typeof hit.id === 'number' ? hit.id : null;
}

function syncAudioUI() {
  const b = $('bAudio');
  const h = $('audioHint');
  const st = audio.status();
  if (b) b.classList.toggle('on', st.armed);
  const r = $('rAudio');
  if (r && document.activeElement !== r) r.value = String(Math.round((st.volume || 0) * 100));
  const o = $('oAudio');
  if (o) o.value = `${Math.round((st.volume || 0) * 100)}%`;
  const c = $('audioCount');
  if (c) c.textContent = st.ready ? String(st.streamsCount) : '';
  if (!h) return;
  if (!st.ready) {
    h.innerHTML = `<span class="${st.loadError ? 'bad' : 'dim'}">${st.loadError || '音频索引载入中…'}</span>`;
    return;
  }
  const loopTxt = (l) => (l ? `循环 ${l.start}/${l.end}` : '整段(无循环点)');
  const m = st.music;
  let mTxt;
  if (!st.armed || !st.active) {
    mTxt = `<span class="dim">${st.armed ? '环境层关,声音停' : '音频关'}</span>`;
  } else if (!m) {
    mTxt = '<span class="dim">还没有选中现象</span>';
  } else if (m.streamState === 'none') {
    mTxt = `<span class="dim">按律无 BGM(${m.from || '——'})</span>`;
  } else if (m.streamState === 'missing') {
    mTxt = `<span class="warn">音乐 ${m.cue} 目录里没这条流</span>`;
  } else {
    mTxt = `<span class="ok">音乐 ${m.cue}</span> <span class="dim">${m.address ? `${loopTxt(m.loopInterval)} · ` : ''}${m.from || ''}</span>`;
  }
  const seTxt = st.se && st.se.length
    ? `<span class="ok">环境音:${st.se.map((s) => (s.cue || '?')).join('、')}</span>`
       + `<span class="dim">${st.se.some((s) => s.streamState === 'missing') ? ' 缺流' : ''}</span>`
    : '';
  const failTxt = st.failures.length
    ? ` <span class="bad">取不到 ${st.failures.length} 条(第一处:${st.failures[0].address})</span>`
    : (st.missingFromRoutes.length
      ? ` <span class="warn">缺流 ${st.missingFromRoutes.join('、')}</span>` : '');
  const ctxTxt = (st.armed && st.context !== 'running')
    ? ` <span class="warn">上下文 ${st.context}(浏览器等一次播放手势)</span>` : '';
  h.innerHTML = `${mTxt}${seTxt ? `<br>${seTxt}` : ''}${failTxt}${ctxTxt}`
    + (st.playing ? ` <span class="dim">${st.playing} 个源在响</span>` : '');
}

function buildEnvList() {
  const box = $('envList');
  if (!box) return;
  box.innerHTML = '';
  if (!environment) {
    box.innerHTML = `<div class="empty">${envErr ? `缺 phenomena 数据(${envErr})` : '无现象数据'}</div>`;
    for (const id of ['bEnv', 'bEnvIndoor', 'bEnvParticles', 'bEnvPost', 'bEnvSky', 'bEnvGround',
      'selEnvSite', 'selEnvLevel', 'selEnvEmission', 'bEnvTimeline', 'bEnvTimelineReset',
      'rEnvTimeline']) {
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
  buildSiteLists();
}

/**
 * 站点与室内等级两个下拉框。**一行一个站点,不是一行一个场景包** ——
 * placement 表里 `first_floor` 这一个场景包**承载三个站点**(1/2/3 楼),三行只差
 * `sitePosition.y`(0 / 500 / 1000)。等级名单跟着站点走:2 楼与 3 楼在 placement 表里
 * **只开放 5 级**,所以换站点后要重建这一框。
 */
function buildSiteLists() {
  if (!environment) return;
  const sel = $('selEnvSite');
  if (sel) {
    const list = environment.siteScenes();
    sel.innerHTML = list.map((s) => {
      const floor = (s.siteType && s.siteType !== s.scene) ? `·${s.siteType}` : '';
      const y = s.position && s.position[1] ? ` y=${s.position[1]}` : '';
      return `<option value="${s.key}">站点:${s.key}${floor}${s.indoor ? '(室内)' : ''}${y}</option>`;
    }).join('');
    if (list.some((s) => s.key === environment.site)) sel.value = environment.site;
    else if (list.length) sel.value = list[0].key;
    if (!list.length) sel.innerHTML = '<option value="">站点产物读不到</option>';
  }
  const lvl = $('selEnvLevel');
  if (lvl) {
    // 等级名单 = 产物里的扩张模块等级 ∩ placement 表里这一站开放的等级。
    // 当前是第几级由服务端决定,本仓拿不到,所以做成面板下发。
    const levels = environment.siteLevels();
    const top = levels.length ? levels[levels.length - 1].key : null;
    lvl.innerHTML = levels.length
      ? levels.map((x) => `<option value="${x.key}">室内等级:lv_${x.key}`
        + `${x.key === top ? '(默认:本站最高级)' : ''}</option>`).join('')
      : '<option value="">室内等级读不到</option>';
    if (levels.length) lvl.value = environment.siteLevel || top;
  }
  const em = $('selEnvEmission');
  if (em && environment.siteView) em.value = environment.siteView.emission;
}

/** 时间轴那一行:当前时刻 / 第几帧 / 这一拍四条轨写出了什么。面板与判据看同一份读数。 */
function syncTimelineUI() {
  const slider = $('rEnvTimeline');
  const out = $('oEnvTimeline');
  const hint = $('envTlHint');
  const btn = $('bEnvTimeline');
  if (!slider || !out || !hint) return;
  const st = (environment && envOn) ? environment.status() : null;
  const T = st ? st.timeline : null;
  const on = !!(T && T.has);
  slider.disabled = !on;
  if (btn) { btn.disabled = !on; btn.classList.toggle('on', !!(T && T.playing)); }
  const rb = $('bEnvTimelineReset');
  if (rb) rb.disabled = !on;
  if (!on) {
    out.textContent = '—';
    hint.innerHTML = st
      ? (T && T.dataError
        ? `<span class="bad">${T.dataError}</span>`
        : `<span class="dim">本现象无时间轴(带时间轴的:${(T && T.phenomenaWithTimeline || []).join('、') || '无'})</span>`)
      : '&nbsp;';
    return;
  }
  // 拖动中不回写滑块位置,否则手指会被时钟推着走。
  if (document.activeElement !== slider) {
    slider.value = String(Math.round((T.time / Math.max(T.duration, 1e-6)) * 1000));
  }
  out.textContent = `${T.time.toFixed(2)}s`;
  const A = T.applied || {};
  const f4 = (v) => (Array.isArray(v) ? v.map((x) => (+x).toFixed(2)).join(' ') : '—');
  const flash = (+A.skyAdditiveIntensity > 1e-6) || (+A.lightAdditiveIntensity > 1e-6);
  hint.innerHTML = `<span class="${flash ? 'ok' : 'dim'}">时间轴 ${T.time.toFixed(2)}/${T.duration.toFixed(2)}s`
    + ` · 帧 ${T.frame}/${T.frames} · ${T.playing ? '播' : '停'}</span>`
    + ` <span class="dim">${T.trackCount} 轨 ${T.clipCount} clip`
    + `${T.notModelledClips || T.notModelled.length ? ` · 未做 ${T.notModelled.length} 轨/${T.notModelledClips} clip` : ''}</span>`
    + `<br><span class="${flash ? 'ok' : 'dim'}">天空附加 ${(+A.skyAdditiveIntensity || 0).toFixed(3)}`
    + ` [${f4(A.skyAdditiveColor)}] · 光附加 ${(+A.lightAdditiveIntensity || 0).toFixed(3)}`
    + ` [${f4(A.lightAdditiveColor)}]</span>`;
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
      // 站点一行如实说三件事:挂着谁、是不是室内、几何真的挂上了多少。
      const S = st.site;
      const m = S.mounted;
      const siteLine = S.mounting
        ? `<span class="dim">站点 ${S.key} 装载中…</span>`
        : (m
          ? `<span class="${S.indoor ? 'warn' : 'ok'}">${S.key}${S.indoor ? '(室内:不挂天气)' : ''}</span>`
            + `<span class="dim"> · ${m.meshes} 网格 / ${m.triangles} 三角`
            + `${m.hiddenMeshes ? ` · 原版不画 ${m.hiddenMeshes}` : ''}</span>`
          : `<span class="bad">站点 ${S.key} 没挂上</span>`);
      hint.innerHTML = `<span class="ok">${st.phenomenon || '—'}</span> <span class="dim">`
        + `${L.time || '时段未知'} · ${L.bright ? `亮度 ${L.bright}` : '亮度未知'}`
        + `${st.usedOverride ? ' · 覆盖' : ''}${st.homeAngleUsed ? ' · 家园角' : ''}</span><br>${siteLine}`;
    }
  }
  const sh = $('envSiteHint');
  if (sh) {
    // 三件一起说:这一站的材质各走哪一族程序、站点的世界位置、室内拼装情况。
    const S = environment && envOn ? environment.status().site : null;
    const m = S ? S.mounted : null;
    if (!m) sh.innerHTML = '&nbsp;';
    else {
      const fam = Object.entries(m.families || {})
        .sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join('、');
      const org = (m.worldOrigin || [0, 0, 0]).map((x) => +x).join(',');
      const em = S.emission || {};
      const lines = [];
      lines.push(`<span class="ok">站点族</span> <span class="dim">${fam || '无'}`
        + `${m.vertexColorRescaled ? ` · 顶点色还原 ${m.vertexColorRescaled}` : ''}`
        + ` · 世界位置 (${org}) 施加在${m.worldOriginApplied === 'shading' ? '着色的世界坐标' : '场景变换'}`
        + ` · 发光缓冲 ${em.mode}(${em.emissiveMaterials || 0} 份材质写非零,不合成)</span>`);
      if (m.indoor) {
        const parts = (m.assembly || []).map((f) => `${f.part}${f.skipped ? '(无几何)' : ` ${f.triangles}`}`);
        lines.push(`<span class="ok">室内 lv_${m.level}</span> <span class="dim">${m.levelSource}`
          + ` · 场景 ${m.files.length} 件共 ${m.triangles} 三角(画 ${m.drawnTriangles})`
          + `${parts.length ? ` · 拼装:${parts.join('、')}` : ''}</span>`);
      }
      sh.innerHTML = lines.join('<br>');
    }
  }
  syncTimelineUI();
  const lh = $('lightHint');
  if (lh) {
    lh.innerHTML = envOn
      ? '<span class="warn">环境层接管光照:滑块此刻不生效</span>'
      : '&nbsp;';
  }
  const ib = $('bEnvIndoor');
  if (ib) ib.disabled = !environment || !envOn;
  for (const id of ['bEnvParticles', 'bEnvPost', 'bEnvSky', 'bEnvGround', 'selEnvSite',
    'selEnvLevel', 'selEnvEmission']) {
    const el = $(id); if (el) el.disabled = !environment || !envOn;
  }
  syncAudioUI();
}

/** 点一个现象:第一次点顺手把环境层打开;之后每次切换走 0.25 秒交叉淡化。 */
async function setEnvPhenomenon(name, seconds = CROSS_FADE_SECONDS) {
  if (!environment) return false;
  const first = !environment.to;
  const ok = await environment.setPhenomenon(name, first ? 0 : seconds);
  if (!ok) { syncEnvUI(); return false; }
  // 音频跟着视觉切换走:视觉这次用了多长时间的交叉淡化,音频就用多长(不加自创时长)。
  audio.setScene(name, environment.site, first ? 0 : seconds);
  if (!envOn) enableEnv(true);
  syncEnvUI();
  return true;
}

function enableEnv(on) {
  if (!environment) return;
  envOn = !!on;
  audio.setActive(envOn);
  if (envOn) {
    environment.attach();
    environment.setCharacterMaterials(current ? current.mats : []);
    grid.visible = false;                  // 真站点接管承影面,诊断用的地格网让位
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
      // 故障注入(?sabotageFacial=cellShift):把要画的格子整体偏一格。
      // 判据按 canon 公式独立核验画出的格,偏移一格的画法必须被判红;
      // 不带该参数时本行恒为假,零影响。
      if (FACIAL_SABOTAGE === 'cellShift') idx += 1;
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
  fillFacialSelects(unitId);      // 专属行只发当前角色;换角色时重排(加载完成前只发基础行)
  resetFacialSelects();
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
function isEditableTarget(target) {
  const el = target && target.nodeType === 1 ? target : document.activeElement;
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
}

function freeMoveKey(event) {
  return FREE_MOVE_KEYS[event.code] || FREE_MOVE_KEYS[(event.key || '').toLowerCase()] || null;
}

function applyFreecamRotation() {
  _freeEuler.set(freecam.pitch, freecam.yaw, 0, 'YXZ');
  camera.quaternion.setFromEuler(_freeEuler);
}

function isFreecamSlowKey(event) {
  return !!(FREECAM_SLOW_KEYS[event.code] || FREECAM_SLOW_KEYS[event.key]);
}

function onFreecamKeyDown(event) {
  if (!freecam.active) return;
  if (event.key === 'Shift') { freecam.shift = true; return; }
  const editable = isEditableTarget(event.target) || isEditableTarget(document.activeElement);
  // 减速键是可打印字符,**先让输入框拿走**再判,否则在筛选框里打一个 z 会既减速又被吞掉。
  if (isFreecamSlowKey(event)) {
    if (editable) return;
    freecam.slow = true;
    event.preventDefault();
    return;
  }
  const move = freeMoveKey(event);
  if (!move) return;
  if (editable) return;
  freecam.keys.add(move);
  // 修饰键可能在移动键之前就按下了(那一下的 keydown 已经过去),所以每次移动键事件都对一次真值。
  if (event.shiftKey) freecam.shift = true;
  event.preventDefault();
}

function onFreecamKeyUp(event) {
  if (!freecam.active) return;
  if (event.key === 'Shift') { freecam.shift = false; return; }
  // 松开一律复位,不看焦点在哪:按下时焦点在画布、松开时焦点已经跑进输入框的情况真会发生,
  // 那时若因为「在输入框里」而跳过复位,减速就会一直粘着。
  if (isFreecamSlowKey(event)) { freecam.slow = false; return; }
  const move = freeMoveKey(event);
  if (!move) return;
  freecam.keys.delete(move);
  if (!isEditableTarget(event.target) && !isEditableTarget(document.activeElement)) event.preventDefault();
}

function onFreecamPointerDown(event) {
  if (!freecam.active || event.button !== 0) return;
  freecam.dragging = true;
  freecam.pointerId = event.pointerId;
  freecam.lastX = event.clientX;
  freecam.lastY = event.clientY;
  renderer.domElement.setPointerCapture?.(event.pointerId);
  event.preventDefault();
}

function onFreecamPointerMove(event) {
  if (!freecam.active || !freecam.dragging || event.pointerId !== freecam.pointerId) return;
  const dx = event.clientX - freecam.lastX;
  const dy = event.clientY - freecam.lastY;
  freecam.lastX = event.clientX;
  freecam.lastY = event.clientY;
  freecam.yaw -= dx * 0.004;
  freecam.pitch = Math.max(-FREECAM_PITCH_LIMIT,
    Math.min(FREECAM_PITCH_LIMIT, freecam.pitch - dy * 0.004));
  applyFreecamRotation();
  event.preventDefault();
}

function onFreecamPointerUp(event) {
  if (!freecam.dragging || event.pointerId !== freecam.pointerId) return;
  freecam.dragging = false;
  renderer.domElement.releasePointerCapture?.(event.pointerId);
  freecam.pointerId = null;
}

function startFreecamInput() {
  window.addEventListener('keydown', onFreecamKeyDown);
  window.addEventListener('keyup', onFreecamKeyUp);
  renderer.domElement.addEventListener('pointerdown', onFreecamPointerDown);
  renderer.domElement.addEventListener('pointermove', onFreecamPointerMove);
  renderer.domElement.addEventListener('pointerup', onFreecamPointerUp);
  renderer.domElement.addEventListener('pointercancel', onFreecamPointerUp);
}

function stopFreecamInput() {
  window.removeEventListener('keydown', onFreecamKeyDown);
  window.removeEventListener('keyup', onFreecamKeyUp);
  renderer.domElement.removeEventListener('pointerdown', onFreecamPointerDown);
  renderer.domElement.removeEventListener('pointermove', onFreecamPointerMove);
  renderer.domElement.removeEventListener('pointerup', onFreecamPointerUp);
  renderer.domElement.removeEventListener('pointercancel', onFreecamPointerUp);
  freecam.dragging = false;
  freecam.pointerId = null;
  freecam.keys.clear();
  freecam.shift = false;
  freecam.slow = false;
}

function updateFreecam(dt) {
  if (!freecam.active) return;
  if (isEditableTarget(document.activeElement)) {
    freecam.keys.clear();
    freecam.shift = false;
    freecam.slow = false;
    return;
  }
  _freeForward.set(0, 0, -1).applyQuaternion(camera.quaternion).normalize();
  _freeRight.set(1, 0, 0).applyQuaternion(camera.quaternion).normalize();
  _freeMove.set(0, 0, 0);
  if (freecam.keys.has('forward')) _freeMove.add(_freeForward);
  if (freecam.keys.has('back')) _freeMove.sub(_freeForward);
  if (freecam.keys.has('right')) _freeMove.add(_freeRight);
  if (freecam.keys.has('left')) _freeMove.sub(_freeRight);
  if (freecam.keys.has('up')) _freeMove.y += 1;
  if (freecam.keys.has('down')) _freeMove.y -= 1;
  if (_freeMove.lengthSq() === 0) return;
  _freeMove.normalize().multiplyScalar(FREECAM_SPEED * dt
    * (freecam.shift ? FREECAM_FAST : 1) * (freecam.slow ? FREECAM_SLOW : 1));
  camera.position.add(_freeMove);
}

function syncCameraModeUI() {
  const orbit = $('bCameraOrbit'), free = $('bCameraFree');
  if (orbit) orbit.classList.toggle('on', cameraMode === 'orbit');
  if (free) free.classList.toggle('on', cameraMode === 'free');
  const label = $('cameraModeLabel');
  if (label) label.textContent = cameraMode === 'free' ? '自由' : '轨道';
  const hint = $('cameraHint');
  if (hint) hint.textContent = cameraMode === 'free'
    ? 'WASD 移动 · Q/E 或 R/F 升降 · Shift 加速 · Z 减速 · 拖拽转向'
    : '拖拽旋转 · 滚轮缩放';
  const hudHint = document.querySelector('#hud .hint');
  if (hudHint) hudHint.textContent = cameraMode === 'free'
    ? '自由: WASD 移动 · Q/E 升降 · Shift 加速 · Z 减速 · 拖拽转向'
    : '拖拽旋转 · 滚轮缩放';
  app.cameraMode = cameraMode;
}

function setCameraMode(mode) {
  if (mode === cameraMode) { syncCameraModeUI(); return; }
  if (mode === 'free') {
    camera.getWorldDirection(_freeForward);
    freecam.yaw = Math.atan2(-_freeForward.x, -_freeForward.z);
    freecam.pitch = Math.max(-FREECAM_PITCH_LIMIT,
      Math.min(FREECAM_PITCH_LIMIT, Math.asin(Math.max(-1, Math.min(1, _freeForward.y)))));
    freecam.active = true;
    controls.enabled = false;
    startFreecamInput();
  } else {
    camera.getWorldDirection(_freeForward);
    freecam.active = false;
    stopFreecamInput();
    controls.target.copy(camera.position).addScaledVector(_freeForward, 4);
    controls.enabled = true;
    controls.update();
  }
  cameraMode = mode;
  syncCameraModeUI();
}

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
$('bCameraOrbit').onclick = () => setCameraMode('orbit');
$('bCameraFree').onclick = () => setCameraMode('free');
syncCameraModeUI();
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
  environment.setSiteVisible(on);          // 真站点几何的可见性(原先管的是自制承影面)
};
$('selEnvSite').onchange = async (e) => {
  if (!environment) return;
  await environment.setSite(e.target.value);   // 换站点 = 重挂几何 + 重判室内 + 重取两级查找
  buildSiteLists();                            // 2 楼与 3 楼只开放 5 级:等级名单跟着站点重建
  syncEnvUI();
  audio.setSite(e.target.value, CROSS_FADE_SECONDS);   // 环境音跟着站点走同一个淡化时长
};
$('selEnvEmission').onchange = (e) => {
  if (!environment) return;
  // 站点的**第二个渲染目标**:不产 / 只产缓冲 / 把缓冲直显到屏幕。
  // 「产出」态下这块缓冲会与粒子特效缓冲相加、一起进泛光金字塔(真源里两者写同一块
  // effectRT),所以发光**经泛光那一路**进主目标;泛光那一支关着时它就不进。
  // 「直显」是查看手段,不是原版的一条通路。
  environment.setSiteEmission(e.target.value);
  syncEnvUI();
};
$('selEnvLevel').onchange = async (e) => {
  if (!environment) return;
  await environment.setSiteLevel(e.target.value);   // 室内等级下发 = 换扩张模块 + 换可走面,重拼
  syncEnvUI();
};
$('bEnvTimeline').onclick = (e) => {
  if (!environment) return;
  const on = !e.target.classList.contains('on');
  e.target.classList.toggle('on', on);
  environment.setTimelinePlaying(on);           // 停下来才看得清同一拍(判据也靠它)
  syncEnvUI();
};
$('bEnvTimelineReset').onclick = () => {
  if (!environment) return;
  environment.resetTimeline();
  syncEnvUI();
};
// ---- 音频面板 ----
// 开关 = 真实手势(浏览器自动播放策略要求的第一次手势在用户按这个按钮时给出;
// 判据跑无头 Chrome 时用 --autoplay-policy=no-user-gesture-required 绕过策略)。
$('bAudio').onclick = () => {
  const armed = audio.status().armed;
  audio.setArmed(!armed);
  syncAudioUI();
};
$('rAudio').oninput = (e) => {
  audio.setVolume(+e.target.value / 100);
  syncAudioUI();
};
$('rEnvTimeline').oninput = (e) => {
  if (!environment) return;
  const T = environment.status().timeline;
  if (!T.has) return;
  // 滑块是 0..1000 的整数格,换算成秒;拖动即定位(播放中拖也生效,松手后时钟从这里继续)。
  environment.setTimelineTime((+e.target.value / 1000) * T.duration);
  syncTimelineUI();
};
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
  // 时间轴那一行每 0.25 秒刷一次:它是**在跑的**,只在点面板时刷就永远看着像停着。
  syncTimelineUI();
  syncAudioUI();
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
        + `${st.site.key}${st.site.indoor ? '(室内)' : ''}${st.usedOverride ? ' 覆盖' : ''}`
        + `${st.fadingFrom ? ` 淡化 ${(st.fade * 100).toFixed(0)}%` : ''}`
        + ` · 雾 ${st.fog.enabled ? 'on' : 'off'} · 粒子 ${st.particles.live}/${st.particles.emitters} 发`
        + `${st.post.enabled ? ` · 后处理 ${st.post.passes.length} 趟` : ' · 后处理关'}`
        + `${st.timeline.has
          ? ` · 时间轴 ${st.timeline.time.toFixed(2)}s(帧 ${st.timeline.frame})`
            + `${(st.timeline.applied && +st.timeline.applied.skyAdditiveIntensity > 1e-6) ? ' 闪' : ''}`
          : ''}</span>${skipped}${sup}`;
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
  updateFreecam(dt);
  refreshHud(dt);
  if (cameraMode === 'orbit') controls.update();
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
  fillFacialSelects(0);
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
