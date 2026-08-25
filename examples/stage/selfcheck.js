// Stage 独立自检：重新读取原始产物，不读取 stage.js 的运行时状态。
//
// c6–c10 是渲染与动画那一组判据。它们只用两类输入：
//   1. **产物**：每次都从服务器重新读（glb 的 JSON 块自己解，不借 stage 已经解好的结果）；
//   2. **已渲染的场景图**：`window.__stageProbe` 只给场景图本体、glTF 节点↔Object3D 的身份
//      对照，以及「推进 N 帧」的入口 —— 判据自己去数，不从 stage 拿数好的结论。

import * as Bind from './anim-bind.js';
import { classifyMaterials } from './fixture-materials.js';

const BASE = new URLSearchParams(location.search).get('base') || '../../local-data';

// 判据自己持一份族表，不从 data.js 借 —— 借了就成了「用被检查的一方检查它自己」。
const FAMILIES = [
  { id: 'cut_scene', dir: 'cutscene-timeline' },
  { id: 'fixture_timeline', dir: 'fixture-timeline' },
];
const ATTACH_POINTS = 'fixture-interface/attach-points.json';

function familyDir(familyId) {
  const hit = FAMILIES.find((family) => family.id === familyId);
  return hit ? hit.dir : '';
}

const CHECK_ASSETS = [
  ['manifest.json', 'file'],
  ['motion-library.glb', 'file'],
  ['motion-library.index.json', 'file'],
  ['facial-tables.json', 'file'],
  ['emoticons/emoticons.json', 'file'],
  ...FAMILIES.flatMap((family) => [
    [`${family.dir}/tracks/`, 'directory'],
    [`${family.dir}/clips/`, 'directory'],
    [`${family.dir}/clip-targets/`, 'directory'],
  ]),
  [ATTACH_POINTS, 'file'],
  ['fixture-interface/areas.json', 'file'],
  ['fixture-models/', 'directory'],
  ['camera/', 'directory'],
  ['perf-animations/', 'directory'],
  // 已知缺口台账。`known-gap` 是**第三种状态**，不是一条通过的检查——
  // 原来这一支把查到的结果只用来改措辞、`status` 写死 `pass`，于是它永远不会红：
  // 产物没生成，判据却报通过。判别式要问的是「**变的是判定还是描述**」。
  // 每条必须带具名理由：光有状态没理由，等于换个地方写恒真。
  ['ui/talk.json', 'known-gap',
   '需要调用方传 APK player data；它不是可下发的包，没有任何包名能路由到它'],
];

function url(relativePath) {
  const base = new URL(BASE, location.href);
  if (!base.pathname.endsWith('/')) base.pathname += '/';
  return new URL(relativePath, base).href;
}

async function json(relativePath) {
  const response = await fetch(url(relativePath), { cache: 'no-store' });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function directoryFiles(html, directoryUrl) {
  const files = [];
  const seen = new Set();
  const re = /<a\s+[^>]*href=["']([^"']+)["'][^>]*>/gi;
  let match;
  while ((match = re.exec(html))) {
    const href = match[1];
    if (!href || href.endsWith('/') || href.startsWith('?') || href.startsWith('#')) continue;
    const name = decodeURIComponent(new URL(href, directoryUrl).pathname.split('/').pop() || '');
    if (name && !seen.has(name)) {
      seen.add(name);
      files.push(name);
    }
  }
  return files;
}

async function directory(relativePath) {
  const directoryUrl = url(relativePath);
  try {
    const response = await fetch(directoryUrl, { cache: 'no-store' });
    if (!response.ok) return { ok: false, files: [], error: `${response.status} ${response.statusText}` };
    return { ok: true, files: directoryFiles(await response.text(), directoryUrl), error: '' };
  } catch (error) {
    return { ok: false, files: [], error: String(error) };
  }
}

async function exists(relativePath) {
  try {
    const response = await fetch(url(relativePath), { method: 'HEAD', cache: 'no-store' });
    return { ok: response.ok, error: response.ok ? '' : `${response.status} ${response.statusText}` };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

/** 下拉框的 value 是 `族::文档::timeline`。族认不出来就不猜，返回 null 让判据红。 */
function selectedEntry() {
  const value = document.getElementById('performance-select')?.value || '';
  const first = value.indexOf('::');
  if (first < 0) return null;
  const second = value.indexOf('::', first + 2);
  if (second < 0) return null;
  const family = value.slice(0, first);
  const dir = familyDir(family);
  if (!dir) return null;
  return { family, dir, file: value.slice(first + 2, second), timeline: value.slice(second + 2) };
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}

export class CheckPanel {
  constructor(root) {
    this.root = root;
    this.items = new Map();
    this.order = [];
  }
  set(id, name, status, detail = '', rows = []) {
    if (!this.items.has(id)) this.order.push(id);
    this.items.set(id, { id, name, status, detail, rows });
    this.render();
  }
  render() {
    const glyph = { pass: '✓', fail: '✗', gap: '△', pending: '…' };
    const cls = { pass: 'check-pass', fail: 'check-fail', gap: 'check-gap',
                  pending: 'check-pending' };
    let pass = 0;
    let fail = 0;
    let gap = 0;
    for (const item of this.items.values()) {
      if (item.status === 'pass') pass += 1;
      if (item.status === 'fail') fail += 1;
      if (item.status === 'gap') gap += 1;
    }
    const summary = document.getElementById('selfcheck-summary');
    // 已知缺口单独计数，不并进通过——并进去就是把「我们知道它没就绪」说成「它好了」。
    if (summary) {
      summary.textContent = `${pass} 通过 · ${fail} 失败`
        + (gap ? ` · ${gap} 已知缺口` : '');
    }
    this.root.innerHTML = this.order.map((id) => {
      const item = this.items.get(id);
      const rows = item.rows.map((row) => `<div class="check-row ${cls[row.status] || 'check-pending'}">${glyph[row.status] || '…'} ${esc(row.label)}${row.detail ? ` <span>${esc(row.detail)}</span>` : ''}</div>`).join('');
      return `<div class="check-item ${cls[item.status] || 'check-pending'}"><div><b>${glyph[item.status] || '…'} ${esc(item.id)} ${esc(item.name)}</b> <span>${esc(item.detail)}</span></div>${rows}</div>`;
    }).join('');
  }
}

async function checkAssets(panel) {
  const rows = [];
  for (const [path, kind, reason] of CHECK_ASSETS) {
    if (kind === 'known-gap') {
      const result = path.endsWith('/') ? await directory(path) : await exists(path);
      if (result.ok) {
        // 台账过期：它已经存在了，却还挂在已知缺口上。这一条正是「曾经准、
        // 后来不准」——不改名就会让人以为这一项仍然没就绪。
        rows.push({ status: 'fail', label: path,
                    detail: '台账过期：它已经存在了，却仍标着已知缺口' });
      } else if (!reason) {
        rows.push({ status: 'fail', label: path,
                    detail: '标了已知缺口却没有具名理由' });
      } else {
        rows.push({ status: 'gap', label: path, detail: `已知缺口：${reason}` });
      }
      continue;
    }
    const result = kind === 'directory' ? await directory(path) : await exists(path);
    rows.push({ status: result.ok ? 'pass' : 'fail', label: path, detail: result.ok ? '成功' : `缺失${result.error ? ` · ${result.error}` : ''}` });
  }
  const failed = rows.filter((row) => row.status === 'fail').length;
  const gaps = rows.filter((row) => row.status === 'gap').length;
  panel.set('c1', '产物齐全', failed ? 'fail' : (gaps ? 'gap' : 'pass'),
    `${rows.length} 项逐项检查`
    + (gaps ? ` · 已知缺口 ${gaps} 项（不计为通过）` : ''), rows);
  return rows;
}

/** 两族各自数一遍：一族读不到就只是那一族为空，不会让另一族的条目消失。 */
async function loadCatalogForCheck() {
  const entries = [];
  const listings = [];
  for (const family of FAMILIES) {
    const listing = await directory(`${family.dir}/tracks/`);
    listings.push({ family, listing, entries: 0 });
    if (!listing.ok) continue;
    const names = listing.files.filter((name) => name.endsWith('.json'));
    await Promise.all(names.map(async (file) => {
      try {
        const doc = await json(`${family.dir}/tracks/${file}`);
        for (const timeline of doc.timelines || []) {
          if (timeline?.name) {
            entries.push({
              family: family.id, dir: family.dir, file,
              timeline: timeline.name, package: doc.package || file,
            });
          }
        }
      } catch {
        // c1 already reports a broken directory member; c2 counts only readable entries.
      }
    }));
    listings[listings.length - 1].entries = entries.filter((item) => item.family === family.id).length;
  }
  entries.sort((a, b) => `${a.family}${a.package}${a.timeline}`.localeCompare(`${b.family}${b.package}${b.timeline}`));
  return { entries, listings, listing: listings[0]?.listing || { ok: false, files: [], error: '' } };
}

async function checkSelection(panel, catalog) {
  const selected = selectedEntry();
  if (!selected) {
    panel.set('c3', 'attach 命中', 'fail', '没有选中的演出');
    panel.set('c4', '时间轴非空', 'fail', '没有选中的演出');
    panel.set('c5', '降级可见', 'pass', '没有家具目标，降级条件不适用');
    return;
  }
  const known = catalog.entries.find((entry) => entry.family === selected.family
    && entry.file === selected.file && entry.timeline === selected.timeline);
  if (!known) {
    const where = `${selected.dir}/tracks/`;
    panel.set('c3', 'attach 命中', 'fail', `选中条目不在 ${where} 产物中`);
    panel.set('c4', '时间轴非空', 'fail', `选中条目不在 ${where} 产物中`);
    panel.set('c5', '降级可见', 'fail', '无法核对选中演出的家具目标');
    return;
  }
  const [trackDoc, clipDoc, targetDoc, attachDoc] = await Promise.all([
    json(`${selected.dir}/tracks/${selected.file}`),
    json(`${selected.dir}/clips/${selected.file}`),
    json(`${selected.dir}/clip-targets/${selected.file}`),
    json(ATTACH_POINTS),
  ]);
  const fixture = (targetDoc.clips || []).find((target) => target && String(target.targetPackage || '').includes('__fixture__')) || null;
  const attachPackage = fixture && attachDoc.packages && attachDoc.packages[fixture.targetPackage];
  const attach = attachPackage && attachPackage.entries && attachPackage.entries[0];
  if (attach) {
    const transform = attach.start?.transform || {};
    panel.set('c3', 'attach 命中', 'pass', `${fixture.targetPackage} · ${attach.start?.name || 'loc_start###'} · ${JSON.stringify(transform)}`);
  } else {
    panel.set('c3', 'attach 命中', 'fail', fixture ? `${fixture.targetPackage} 未命中 attach-points` : '选中演出没有家具目标');
  }
  const clips = (clipDoc.tracks || []).flatMap((track) => (track.clips || []).map((clip) => ({ clip, class: track.class })));
  const total = clips.reduce((max, item) => Math.max(max, (Number(item.clip.m_Start) || 0) + (Number(item.clip.m_Duration) || 0)), 0);
  panel.set('c4', '时间轴非空', clips.length > 0 ? 'pass' : 'fail', `${clips.length} clips · 总时长 ${total.toFixed(3)} s`);

  const geometry = await directory('fixture-models/');
  const hasGeometry = geometry.ok && geometry.files.some((name) => name.endsWith('.glb') && (!fixture || name.includes(fixture.targetPackage)));
  const warning = document.getElementById('geometry-warning');
  const warningVisible = !!warning && !warning.hidden && warning.getClientRects().length > 0;
  const placeholderCount = document.querySelectorAll('[data-fixture-placeholder]').length;
  if (!fixture) {
    panel.set('c5', '降级可见', 'pass', '选中演出没有家具目标，降级条件不适用');
  } else if (hasGeometry) {
    panel.set('c5', '降级可见', 'pass', '家具几何已就绪，没有触发降级');
  } else {
    const ok = warningVisible && placeholderCount === 0;
    panel.set('c5', '降级可见', ok ? 'pass' : 'fail', ok ? '家具几何未就绪 · 无占位方块' : '家具缺失提示或占位检查失败');
  }
  const timelineExists = (trackDoc.timelines || []).some((timeline) => timeline.name === selected.timeline);
  if (!timelineExists) panel.set('c4', '时间轴非空', 'fail', '所选 timeline 不在 tracks 文档中');
}

// ---------------------------------------------------------------- c6–c10：渲染与动画

/** glb 只取 JSON 块。用默认缓存语义：同一份文件浏览器刚下过，条件请求就够了。 */
async function glbJsonOf(fileUrl) {
  const response = await fetch(fileUrl);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return Bind.glbJson(await response.arrayBuffer());
}

/** 一个场景里「带 baseColorTexture 的 primitive」有多少个（按节点实例数，与画出来的 mesh 一一对应）。 */
function texturedPrimitives(json, sceneIndex) {
  const nodes = json.nodes || [];
  const meshes = json.meshes || [];
  const materials = json.materials || [];
  let textured = 0;
  let total = 0;
  const walk = (index) => {
    const node = nodes[index];
    if (!node) return;
    if (node.mesh !== undefined) {
      for (const primitive of meshes[node.mesh]?.primitives || []) {
        total += 1;
        const material = primitive.material === undefined ? null : materials[primitive.material];
        if (material?.pbrMetallicRoughness?.baseColorTexture) textured += 1;
      }
    }
    for (const child of node.children || []) walk(child);
  };
  for (const root of json.scenes?.[sceneIndex]?.nodes || []) walk(root);
  return { textured, total };
}

function renderedMeshes(root) {
  let meshes = 0;
  let withMap = 0;
  root?.traverse((object) => {
    if (!object.isMesh && !object.isSkinnedMesh) return;
    meshes += 1;
    const list = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of list) if (material?.map) withMap += 1;
  });
  return { meshes, withMap };
}

async function checkTextureUpload(panel, probe) {
  const fixture = probe?.fixture?.();
  if (!fixture) {
    panel.set('c6', '贴图上链', 'pass', '当前演出没有装载家具几何，判据不适用');
    return;
  }
  try {
    const json = await glbJsonOf(fixture.url);
    const sceneIndex = json.scene ?? 0;
    const expected = texturedPrimitives(json, sceneIndex);
    const actual = renderedMeshes(fixture.root);
    const residual = actual.withMap - expected.textured;
    panel.set('c6', '贴图上链', residual === 0 ? 'pass' : 'fail',
      `glb 默认场景 ${sceneIndex}：带 baseColorTexture 的 primitive ${expected.textured} / 共 ${expected.total}`
      + ` · 场景图里材质有 map 的 mesh ${actual.withMap} / 共 ${actual.meshes} · 残差 ${residual}`, [
        { status: 'pass', label: '产物一侧（重新读 glb 的 JSON 块）', detail: `${expected.textured} 个 primitive 带贴图` },
        { status: 'pass', label: '场景图一侧（已渲染的材质）', detail: `${actual.withMap} 个 mesh 的材质有 map` },
        { status: residual === 0 ? 'pass' : 'fail', label: '残差', detail: String(residual) },
      ]);
  } catch (error) {
    panel.set('c6', '贴图上链', 'fail', `读不到家具 glb：${String(error).slice(0, 80)}`);
  }
}

function checkMaterials(panel, probe) {
  const fixture = probe?.fixture?.();
  const character = probe?.character?.();
  if (!fixture && !character) {
    panel.set('c7', '材质非默认', 'fail', '场景里既没有家具也没有角色');
    return;
  }
  const rows = [];
  let leftDefault = 0;
  let viewerToon = 0;
  let decal = 0;
  for (const [label, root] of [['家具', fixture?.root], ['角色', character?.root]]) {
    if (!root) continue;
    const counts = classifyMaterials(root);
    viewerToon += counts.viewerToon;
    decal += counts.decal;
    leftDefault += counts.leftDefault;
    rows.push({
      status: counts.leftDefault === 0 ? 'pass' : 'fail',
      label: `${label}：viewer 着色 ${counts.viewerToon} · 阴影贴片 ${counts.decal} · 仍是默认材质 ${counts.leftDefault}`,
      detail: counts.defaults.join(' / '),
    });
  }
  panel.set('c7', '材质非默认', leftDefault === 0 ? 'pass' : 'fail',
    `使用 viewer 着色的材质 ${viewerToon} · 阴影贴片 ${decal} · 仍是 three.js 默认材质 ${leftDefault}`, rows);
}

async function checkChannelBinding(panel, probe) {
  const lane = probe?.lane?.();
  const fixture = probe?.fixture?.();
  const character = probe?.character?.();
  if (!lane) {
    panel.set('c8', '通道绑定残差', 'fail', '没有选中的动画轨');
    return null;
  }
  const resolvers = {};
  if (fixture) resolvers.native = Bind.suffixIndex(fixture.json, fixture.nodeObjects, fixture.sceneIndex);
  if (character) {
    resolvers['character-rig'] = Bind.suffixIndex(
      character.json, character.nodeObjects, character.json.scene ?? 0,
    );
  }
  const wanted = new Map();
  for (const key of lane.names) {
    const marker = key.indexOf('::');
    const pkg = key.slice(0, marker);
    if (!wanted.has(pkg)) wanted.set(pkg, new Set());
    wanted.get(pkg).add(key.slice(marker + 2));
  }
  const totals = { channels: 0, bound: 0, unbound: [], viaSuffix: 0, ambiguous: 0, rootFallback: 0 };
  const boundObjects = new Set();
  const errors = [];
  for (const [pkg, names] of wanted) {
    let json;
    try {
      json = await glbJsonOf(url(`perf-animations/${pkg}.glb`));
    } catch (error) {
      errors.push(`${pkg}: ${String(error).slice(0, 60)}`);
      continue;
    }
    const pathOf = Bind.nodePathIndex(json);
    for (const animation of json.animations || []) {
      if (!names.has(animation.name)) continue;
      for (const channel of animation.channels || []) {
        totals.channels += 1;
        const record = pathOf.get(channel.target?.node);
        const resolver = record ? resolvers[record.kind] : null;
        const hit = record && resolver
          ? Bind.resolvePath(resolver, record.path)
          : { object: null, reason: record ? `no-resolver:${record.kind}` : 'unknown-node' };
        if (hit.object) {
          totals.bound += 1;
          if (!hit.exact) totals.viaSuffix += 1;
          boundObjects.add(hit.object);
          continue;
        }
        if (String(hit.reason).startsWith('root-fallback')) totals.rootFallback += 1;
        if (String(hit.reason).startsWith('ambiguous')) totals.ambiguous += 1;
        totals.unbound.push(`${record?.kind || '?'} ${record?.path || '?'}.${channel.target?.path} ← ${animation.name} · ${hit.reason}`);
      }
    }
  }
  const rows = [
    { status: 'pass', label: '按路径绑上的通道', detail: `${totals.bound}（其中按最长唯一后缀命中 ${totals.viaSuffix}）` },
    { status: totals.unbound.length ? 'fail' : 'pass', label: '绑不上的通道', detail: String(totals.unbound.length) },
    { status: totals.rootFallback === 0 ? 'pass' : 'fail', label: '被兜底绑到根节点的通道', detail: String(totals.rootFallback) },
    { status: 'pass', label: '路径歧义（多个同后缀候选）', detail: String(totals.ambiguous) },
  ];
  for (const line of totals.unbound.slice(0, 25)) rows.push({ status: 'fail', label: line });
  if (totals.unbound.length > 25) {
    rows.push({ status: 'fail', label: `…还有 ${totals.unbound.length - 25} 条绑不上的通道（面板「上链残差」里全列）` });
  }
  for (const line of errors) rows.push({ status: 'fail', label: `动画包读不到：${line}` });
  // 差分：判据独立重数的账，必须与运行时自己报的账逐个数字相同。
  // 这两边曾各说一套（运行时绑上 0、判据重数 18/18），而「一致」不是靠把两边改到
  // 一样得来的——是先回产物判定「通道的目标在 glTF 里用节点下标表示，名字只是派生物」，
  // 判定判据那一侧对、运行时那一侧错，然后只改错的那一侧。
  const runtime = probe.bindingReport ? probe.bindingReport() : null;
  const diffs = [];
  if (!runtime) {
    diffs.push('运行时没有上链报告');
  } else {
    const pairs = [
      ['通道数', totals.channels, runtime.channels],
      ['绑上', totals.bound, runtime.bound],
      ['绑不上', totals.unbound.length, runtime.unbound],
      ['根兜底', totals.rootFallback, runtime.rootFallback],
      ['歧义', totals.ambiguous, runtime.ambiguous],
    ];
    for (const [label, mine, theirs] of pairs) {
      if (mine !== theirs) diffs.push(`${label}：判据 ${mine} vs 运行时 ${theirs}`);
    }
    rows.push({
      status: diffs.length ? 'fail' : 'pass',
      label: '与运行时的差分（同一输入，两个实现）',
      detail: diffs.length ? diffs.join(' · ') : '五个计数逐个相同',
    });
  }
  const ok = totals.rootFallback === 0 && totals.channels > 0
    && !errors.length && !diffs.length;
  panel.set('c8', '通道绑定残差', ok ? 'pass' : 'fail',
    `轨 ${lane.key} · 通道 ${totals.channels} · 绑上 ${totals.bound} · 绑不上 ${totals.unbound.length} · 根兜底 ${totals.rootFallback}`
    + (diffs.length ? ` · 与运行时不一致 ${diffs.length} 处` : ' · 与运行时一致'),
    rows);
  return boundObjects;
}

function transformSnapshot(root) {
  const rows = [];
  root?.traverse((object) => {
    rows.push({
      object,
      position: object.position.clone(),
      quaternion: object.quaternion.clone(),
    });
  });
  return rows;
}

function checkMotion(panel, probe, boundObjects) {
  if (!probe?.advance) {
    panel.set('c9', '实际在动', 'fail', '没有推进入口');
    return;
  }
  // 一条时间轴上 clip 可能只落在后半段：只推 1 秒就断言「没动」是假阴性。
  // 所以按 0.5 秒一段往前推，走完整条时间轴（探针到头会自己回绕）或提前测到在动为止。
  const chunkFrames = 30;
  const duration = probe.duration ? probe.duration() : 0;
  const chunks = Math.max(2, Math.min(30, Math.ceil((duration + 0.5) / 0.5)));
  let moved = 0;
  let movedBound = 0;
  let frames = 0;
  const names = new Set();
  for (let chunk = 0; chunk < chunks && movedBound === 0; chunk += 1) {
    const before = transformSnapshot(probe.actors);
    probe.advance(chunkFrames, 1 / 60);
    frames += chunkFrames;
    let chunkMoved = 0;
    for (const row of before) {
      const changed = row.position.distanceToSquared(row.object.position) > 1e-12
        || Math.abs(row.quaternion.dot(row.object.quaternion)) < 1 - 1e-9;
      if (!changed) continue;
      chunkMoved += 1;
      if (boundObjects && boundObjects.has(row.object)) {
        movedBound += 1;
        if (names.size < 8) names.add(row.object.name || '(unnamed)');
      }
    }
    moved = Math.max(moved, chunkMoved);
  }
  // 「有节点在动」远不足以说明角色在做动作：这条判据曾经只要求 movedBound > 0，
  // 而两根 twist 骨在动就满足它 —— 画面上角色仍然整场 T-pose。所以判据的主体换成
  // **指向身体骨的通道数**：Hips / Spine / Head 各自都要有通道绑上，缺任何一根都红。
  // 「变过的节点数」留着看，但不再单独决定颜色。
  const bones = probe.bodyBones ? probe.bodyBones() : ['Hips', 'Spine', 'Head'];
  const body = probe.bodyChannels ? probe.bodyChannels() : {};
  const missingBones = bones.filter((bone) => !(Number(body[bone]) > 0));
  const ok = missingBones.length === 0;
  const rows = [
    { status: 'pass', label: '场景图里变过的节点（含布料与表情等非动画驱动）', detail: String(moved) },
    { status: movedBound > 0 ? 'pass' : 'fail',
      label: '其中被动画通道绑到的节点',
      detail: `${movedBound}${names.size ? ` · ${[...names].join(' ')}` : ''}` },
  ];
  for (const bone of bones) {
    const count = Number(body[bone]) || 0;
    rows.push({
      status: count > 0 ? 'pass' : 'fail',
      label: `指向 ${bone} 的通道`,
      detail: count > 0 ? String(count) : '0（这根骨没有任何动画通道）',
    });
  }
  if (missingBones.length) {
    rows.push({
      status: 'fail',
      label: '判据说明',
      detail: `${missingBones.join(' / ')} 上没有通道：动的只可能是别的骨（twist / collider 一类），`
        + '身体没有被驱动，画面就是 T-pose。',
    });
  }
  panel.set('c9', '身体通道与实际在动', ok ? 'pass' : 'fail',
    `推进 ${frames} 帧（${(frames / 60).toFixed(1)} s）：`
    + bones.map((bone) => `${bone} ${Number(body[bone]) || 0}`).join(' · ')
    + ` · 变过的节点 ${moved}（其中被通道绑到 ${movedBound}）`,
    rows);
}

// c11：角色动作的来源。家具演出的 timeline 上没有角色动画，所以这一族的角色动作
// 必须来自家具旁对话；这条判据数的就是「对话给了几条动作、绑上了几条、缺了哪些」。
// 时长里由 demo 替身补的秒数单列，不并进总时长——那不是游戏值。
function checkTalkSource(panel, probe) {
  const talk = probe?.talk?.();
  if (!talk || talk.talkId === undefined || talk.talkId === null) {
    panel.set('c11', '角色动作来源', 'fail',
      `没有选中的家具旁对话${talk?.status ? ` · ${talk.status}` : ''}`
      + (talk?.choices ? ` · 可选 ${talk.choices} 条` : ''));
    return;
  }
  // 对话点的是**族名**，库里放的是该族的 S/L/E 分段。所以「绑上了没有」要按族比，
  // 按分段名比族名会把每一族都算成缺（实测过：3 个族、9 条分段，比出来「缺 3」）。
  const wanted = talk.motionsWanted || [];
  const families = new Set(talk.familiesBound || []);
  const segments = talk.segmentsBound || [];
  const missing = wanted.filter((name) => !families.has(name));
  const unresolved = talk.unresolvedTokens || [];
  const rows = [
    { status: wanted.length ? 'pass' : 'fail', label: '对话要的角色动作',
      detail: wanted.length ? `${wanted.length} 条 · ${wanted.slice(0, 6).join(' ')}` : '0（这条对话没有 change_animation）' },
    { status: missing.length ? 'fail' : 'pass', label: '绑上动作库的族',
      detail: `${families.size}/${wanted.length} 族 · ${segments.length} 条分段`
        + (missing.length ? ` · 缺 ${missing.join(' ')}` : '') },
    { status: unresolved.length ? 'fail' : 'pass', label: '提取侧未解开的常量',
      detail: unresolved.length ? unresolved.join(' ') : '0' },
    { status: 'pass', label: '时长：数据给的 / 点击替身补的',
      detail: `${talk.dataSeconds.toFixed(1)} s / ${talk.standInSeconds.toFixed(1)} s（点击 ${talk.clickWaits} 次）` },
  ];
  const unscheduled = Object.keys(talk.unscheduled || {});
  if (unscheduled.length) {
    rows.push({ status: 'pass', label: '时间表未编排的算子',
      detail: JSON.stringify(talk.unscheduled) });
  }
  const ok = wanted.length > 0 && !missing.length && !unresolved.length;
  panel.set('c11', '角色动作来源', ok ? 'pass' : 'fail',
    `对话 #${talk.talkId} · 形态 ${talk.form} · 家具 ${(talk.fixtureIds || []).join('/')}`
    + ` · 动作族 ${families.size}/${wanted.length}（分段 ${segments.length}）`,
    rows);
}

async function checkPlayability(panel, probe) {
  const selected = selectedEntry();
  const laneKey = document.getElementById('lane-select')?.value || '';
  if (!selected || !laneKey) {
    panel.set('c10', '不可播如实', 'fail', '没有选中的演出或动画轨');
    return;
  }
  const [clipDoc, targetDoc] = await Promise.all([
    json(`${selected.dir}/clips/${selected.file}`),
    json(`${selected.dir}/clip-targets/${selected.file}`),
  ]);
  const targets = new Map();
  for (const item of targetDoc.keyedClips || []) {
    targets.set(`${item.trackPathId}:${item.clipIndex}`, item.target || null);
  }
  const wanted = [];
  for (const track of clipDoc.tracks || []) {
    const clips = track.clips || [];
    for (let index = 0; index < clips.length; index += 1) {
      const key = `${track.pathId}:${index}`;
      if (!targets.has(key)) continue;
      if (laneKey !== '*' && String(track.pathId) !== laneKey) continue;
      wanted.push(targets.get(key));
    }
  }
  const packages = new Set(wanted.filter(Boolean).map((target) => target.targetPackage));
  const records = new Map();
  await Promise.all([...packages].map(async (pkg) => {
    try {
      const doc = await json(`perf-animations/${pkg}.index.json`);
      const map = new Map();
      for (const record of doc.clipRecords || []) if (record?.name) map.set(record.name, record);
      records.set(pkg, map);
    } catch {
      records.set(pkg, new Map());
    }
  }));
  let playable = 0;
  let unplayable = 0;
  let empty = 0;
  const names = [];
  for (const target of wanted) {
    if (!target?.clipName) { empty += 1; continue; }
    const record = records.get(target.targetPackage)?.get(target.clipName);
    if (record && (record.gltfChannels || 0) > 0) playable += 1;
    else {
      unplayable += 1;
      if (names.length < 10) names.push(target.clipName);
    }
  }
  const shown = document.getElementById('playable-facts')?.textContent || '';
  const declared = shown.includes(`不可播 ${unplayable}`);
  panel.set('c10', '不可播如实', declared ? 'pass' : 'fail',
    `轨 ${laneKey}：clip ${wanted.length} · 可播 ${playable} · 不可播 ${unplayable} · 空动画段 ${empty}`, [
      { status: 'pass', label: '不可播的 clip（产物里一条 glTF 通道都没有）', detail: names.join(' ') || '无' },
      { status: declared ? 'pass' : 'fail', label: '界面上已标明不可播条数', detail: declared ? shown : `界面文本：${shown || '(空)'}` },
    ]);
}

async function checkRenderAndMotion(panel) {
  const probe = window.__stageProbe;
  if (!probe) {
    for (const [id, name] of [['c6', '贴图上链'], ['c7', '材质非默认'], ['c8', '通道绑定残差'], ['c9', '身体通道与实际在动'], ['c10', '不可播如实'], ['c11', '角色动作来源']]) {
      panel.set(id, name, 'fail', '没有场景图探针（stage.js 未起来）');
    }
    return;
  }
  await checkTextureUpload(panel, probe);
  checkMaterials(panel, probe);
  const boundObjects = await checkChannelBinding(panel, probe);
  checkMotion(panel, probe, boundObjects);
  checkTalkSource(panel, probe);
  await checkPlayability(panel, probe);
}

let generation = 0;
export async function runSelfcheck() {
  const current = ++generation;
  const root = document.getElementById('selfcheck');
  if (!root) return;
  const panel = new CheckPanel(root);
  panel.set('c1', '产物齐全', 'pending', '读取中…');
  panel.set('c2', '演出可选', 'pending', '读取中…');
  panel.set('c3', 'attach 命中', 'pending', '等待选中演出');
  panel.set('c4', '时间轴非空', 'pending', '等待选中演出');
  panel.set('c5', '降级可见', 'pending', '等待选中演出');
  panel.set('c6', '贴图上链', 'pending', '等待场景图');
  panel.set('c7', '材质非默认', 'pending', '等待场景图');
  panel.set('c8', '通道绑定残差', 'pending', '等待动画上链');
  panel.set('c9', '身体通道与实际在动', 'pending', '等待动画上链');
  panel.set('c10', '不可播如实', 'pending', '等待选中演出');
  panel.set('c11', '角色动作来源', 'pending', '等待家具旁对话');
  const assetsPromise = checkAssets(panel);
  const catalogPromise = loadCatalogForCheck();
  const catalog = await catalogPromise;
  if (current !== generation) return;
  panel.set('c2', '演出可选', catalog.entries.length > 0 ? 'pass' : 'fail',
    `演出条目 ${catalog.entries.length}（两族分开读）`,
    (catalog.listings || []).map((row) => ({
      status: row.listing.ok ? 'pass' : 'fail',
      label: `${row.family.dir}/tracks/`,
      detail: row.listing.ok
        ? `${row.listing.files.filter((name) => name.endsWith('.json')).length} 份文档 · ${row.entries} 条 timeline`
        : `读不到${row.listing.error ? ` · ${row.listing.error}` : ''}`,
    })));
  await assetsPromise;
  if (current !== generation) return;
  await checkSelection(panel, catalog);
  if (current !== generation) return;
  await checkRenderAndMotion(panel);
}
