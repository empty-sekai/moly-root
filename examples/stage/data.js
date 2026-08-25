// Stage 数据读取：只处理相对路径与产物结构，不持有渲染状态。

export const BASE = new URLSearchParams(location.search).get('base') || '../../local-data';

// 演出产物按**宿主**分两族，各自一套 tracks/clips/clip-targets：
//   * `cutscene-timeline/` —— 剧情演出，宿主是 CutSceneView；
//   * `fixture-timeline/`  —— 家具演出，宿主是 NPCFixtureTimelineView / PlayerFixtureTimelineView。
// 两族不能合读：同一份文档名在两族里是两个宿主的两件东西，混成一处就读不出「谁在放」。
export const TIMELINE_FAMILIES = [
  { id: 'cut_scene', dir: 'cutscene-timeline', label: '剧情演出（CutSceneView）' },
  { id: 'fixture_timeline', dir: 'fixture-timeline', label: '家具演出（FixtureTimelineView）' },
];

const FAMILY_DIR = new Map(TIMELINE_FAMILIES.map((family) => [family.id, family.dir]));

/** 族 → 该族某一类文档的目录。族名不认得就抛，不猜、不退回单族布局。 */
export function familyPath(familyId, kind) {
  const dir = FAMILY_DIR.get(familyId);
  if (!dir) throw new Error(`未知的演出族：${familyId}`);
  return `${dir}/${kind}/`;
}

export const ASSET_SPECS = [
  { id: 'manifest', label: 'manifest.json', path: 'manifest.json', kind: 'file' },
  { id: 'motion-glb', label: 'motion-library.glb', path: 'motion-library.glb', kind: 'binary' },
  { id: 'motion-index', label: 'motion-library.index.json', path: 'motion-library.index.json', kind: 'file' },
  { id: 'facial', label: 'facial-tables.json', path: 'facial-tables.json', kind: 'file' },
  { id: 'emoticons', label: 'emoticons/emoticons.json', path: 'emoticons/emoticons.json', kind: 'file' },
  { id: 'cutscene-tracks', label: 'cutscene-timeline/tracks/', path: 'cutscene-timeline/tracks/', kind: 'directory' },
  { id: 'cutscene-clips', label: 'cutscene-timeline/clips/', path: 'cutscene-timeline/clips/', kind: 'directory' },
  { id: 'cutscene-targets', label: 'cutscene-timeline/clip-targets/', path: 'cutscene-timeline/clip-targets/', kind: 'directory' },
  { id: 'fixture-tracks', label: 'fixture-timeline/tracks/', path: 'fixture-timeline/tracks/', kind: 'directory' },
  { id: 'fixture-clips', label: 'fixture-timeline/clips/', path: 'fixture-timeline/clips/', kind: 'directory' },
  { id: 'fixture-targets', label: 'fixture-timeline/clip-targets/', path: 'fixture-timeline/clip-targets/', kind: 'directory' },
  { id: 'attach', label: 'fixture-interface/attach-points.json', path: 'fixture-interface/attach-points.json', kind: 'file' },
  { id: 'areas', label: 'fixture-interface/areas.json', path: 'fixture-interface/areas.json', kind: 'file' },
  { id: 'fixture-models', label: 'fixture-models/', path: 'fixture-models/', kind: 'directory' },
  { id: 'camera', label: 'camera/', path: 'camera/', kind: 'directory' },
  { id: 'perf-animations', label: 'perf-animations/', path: 'perf-animations/', kind: 'directory' },
  { id: 'ui', label: 'ui/talk.json（UI 域）', path: 'ui/talk.json', kind: 'file', expectedNotReady: true },
];

export const ATTACH_POINTS_PATH = 'fixture-interface/attach-points.json';

// 选片建议：两族 tracks 合起来近两百份文档、fixture-models/ 有一千件几何，逐条翻是没法看的。
// 这几件是从产物里数出来的「先看这些」：
//   * clb1102_fixture_egg1..4 —— 家具本体自己带整套骨骼动画（动画包里全是本包节点的通道，
//     一条借来的角色骨都没有），是唯一能一眼看出「家具在动」的一组；
//   * ext0001_fixture_sofa1 —— 被动画引用最多的一件（605 次），五个座位挂点齐全，
//     是看「角色坐上去 + 挂点选取」最典型的一件。
// 另有一件带操作型脚本的机器人件（fixtureId 423）：产物里没有 fixtureId → 包名的对照表，
// 所以这里不猜它的包名，界面上如实说明。
export const RECOMMENDED_PACKAGES = [
  { match: 'mdl_ext0001_fixture_sofa1', mark: '★ 挂点/坐姿' },
  { match: 'mdl_clb1102_fixture_egg1', mark: '★ 本体动画' },
  { match: 'mdl_clb1102_fixture_egg2', mark: '★ 本体动画' },
  { match: 'mdl_clb1102_fixture_egg3', mark: '★ 本体动画' },
  { match: 'mdl_clb1102_fixture_egg4', mark: '★ 本体动画' },
];

export const RECOMMENDATION_NOTE = '★ 本体动画：家具自己带骨骼动画（egg1..4）。'
  + '★ 挂点/坐姿：被动画引用最多的沙发件（605 次，五个座位挂点）。'
  + '第五件操作型家具（机器人件 fixtureId 423）在产物里没有 fixtureId→包名的对照，未标注。';

function baseUrl() {
  const value = new URL(BASE, location.href);
  if (!value.pathname.endsWith('/')) value.pathname += '/';
  return value;
}

export function assetUrl(relativePath) {
  return new URL(relativePath, baseUrl()).href;
}

export async function fetchJson(relativePath) {
  const response = await fetch(assetUrl(relativePath), { cache: 'no-store' });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

export async function fetchOptionalJson(relativePath) {
  try {
    return { value: await fetchJson(relativePath), error: '' };
  } catch (error) {
    return { value: null, error: String(error) };
  }
}

function directoryNames(html, directoryUrl) {
  const names = [];
  const seen = new Set();
  const re = /<a\s+[^>]*href=["']([^"']+)["'][^>]*>/gi;
  let match;
  while ((match = re.exec(html))) {
    const href = match[1];
    if (!href || href.startsWith('?') || href.startsWith('#') || href.endsWith('/')) continue;
    const url = new URL(href, directoryUrl);
    const name = decodeURIComponent(url.pathname.split('/').pop() || '');
    if (name && !seen.has(name)) {
      seen.add(name);
      names.push(name);
    }
  }
  return names;
}

export async function listDirectory(relativePath) {
  const url = assetUrl(relativePath);
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) return { ok: false, files: [], error: `${response.status} ${response.statusText}`, url };
    const html = await response.text();
    return { ok: true, files: directoryNames(html, url), error: '', url };
  } catch (error) {
    return { ok: false, files: [], error: String(error), url };
  }
}

export async function probeAsset(spec) {
  if (spec.kind === 'directory') return listDirectory(spec.path);
  try {
    const response = await fetch(assetUrl(spec.path), { method: 'HEAD', cache: 'no-store' });
    return { ok: response.ok, files: [], error: response.ok ? '' : `${response.status} ${response.statusText}` };
  } catch (error) {
    return { ok: false, files: [], error: String(error) };
  }
}

/** 一族的 tracks 目录：条目带上自己的族，取 clips/clip-targets 时按族回到同一处。 */
async function loadFamilyCatalog(family) {
  const tracksPath = familyPath(family.id, 'tracks');
  const listing = await listDirectory(tracksPath);
  if (!listing.ok) return { family, listing, files: [], docs: [], entries: [], targetFiles: [], targetListing: null };
  const files = listing.files.filter((name) => name.endsWith('.json'));
  const docs = await Promise.all(files.map(async (file) => {
    try {
      return { file, doc: await fetchJson(`${tracksPath}${file}`) };
    } catch (error) {
      return { file, doc: null, error: String(error) };
    }
  }));
  const entries = [];
  for (const { file, doc } of docs) {
    for (const timeline of doc?.timelines || []) {
      if (!timeline || !timeline.name) continue;
      entries.push({
        key: `${family.id}::${file}::${timeline.name}`,
        family: family.id,
        familyLabel: family.label,
        file,
        package: doc.package || file.replace(/\.json$/, ''),
        timeline: timeline.name,
        track: timeline,
      });
    }
  }
  const targetListing = await listDirectory(familyPath(family.id, 'clip-targets'));
  const targetFiles = targetListing.ok ? targetListing.files.filter((name) => name.endsWith('.json')) : [];
  return { family, listing, files, docs, entries, targetFiles, targetListing };
}

export async function loadTrackCatalog() {
  const families = await Promise.all(TIMELINE_FAMILIES.map(loadFamilyCatalog));
  const entries = families.flatMap((result) => result.entries);
  entries.sort((a, b) => a.family.localeCompare(b.family)
    || a.package.localeCompare(b.package)
    || a.timeline.localeCompare(b.timeline));
  return {
    entries,
    families,
    // 两族各自的读取结果分开留着，界面上要能说出「哪一族没读到」。
    byFamily: new Map(families.map((result) => [result.family.id, result])),
    files: families.flatMap((result) => result.files),
    docs: families.flatMap((result) => result.docs),
    targetFiles: families.flatMap((result) => result.targetFiles),
  };
}

/** 下拉框的 value 就是 `族::文档::timeline`（timeline 名里再有 `::` 也不会切错）。 */
export function splitSelection(key) {
  const text = String(key || '');
  const first = text.indexOf('::');
  if (first < 0) return { family: '', file: '', timeline: '' };
  const second = text.indexOf('::', first + 2);
  if (second < 0) return { family: '', file: '', timeline: '' };
  return {
    family: text.slice(0, first),
    file: text.slice(first + 2, second),
    timeline: text.slice(second + 2),
  };
}

function clipFields(clip, trackClass, trackName, target, trackPathId, clipIndex) {
  return {
    class: trackClass,
    trackName,
    trackPathId: String(trackPathId ?? ''),
    clipIndex: Number(clipIndex ?? 0),
    displayName: clip?.m_DisplayName || '',
    start: Number(clip?.m_Start) || 0,
    duration: Math.max(0, Number(clip?.m_Duration) || 0),
    clipIn: Number(clip?.m_ClipIn) || 0,
    timeScale: Number(clip?.m_TimeScale) || 1,
    easeIn: Number(clip?.m_EaseInDuration) || 0,
    easeOut: Number(clip?.m_EaseOutDuration) || 0,
    blendIn: Number(clip?.m_BlendInDuration) || 0,
    blendOut: Number(clip?.m_BlendOutDuration) || 0,
    postExtrapolation: clip?.m_PostExtrapolationMode?.name || '',
    preExtrapolation: clip?.m_PreExtrapolationMode?.name || '',
    target: target || null,
  };
}

export async function loadPerformance(entry) {
  if (!entry?.file) throw new Error('没有选中的演出条目');
  if (!entry.family) throw new Error(`演出条目没有族：${entry.file}`);
  const tracksPath = `${familyPath(entry.family, 'tracks')}${entry.file}`;
  const clipsPath = `${familyPath(entry.family, 'clips')}${entry.file}`;
  const targetsPath = `${familyPath(entry.family, 'clip-targets')}${entry.file}`;
  const [trackResult, clipResult, targetResult] = await Promise.all([
    fetchOptionalJson(tracksPath),
    fetchOptionalJson(clipsPath),
    fetchOptionalJson(targetsPath),
  ]);
  if (!trackResult.value) throw new Error(`${tracksPath}: ${trackResult.error}`);
  if (!clipResult.value) throw new Error(`${clipsPath}: ${clipResult.error}`);
  const trackDoc = trackResult.value;
  const clipDoc = clipResult.value;
  const targetDoc = targetResult.value || { package: entry.package, clips: [], keyedClips: [] };
  // 目标表按包内对象存储顺序产出，与轨序无关；只能按 (轨 pathId, 轨内 clip 下标) 配对。
  // 轨名不是标识（同一个包里重名轨很常见），轨类名也不是判据——动画既挂在
  // AnimationTrack 上，也挂在 FixtureIdleAnimationTrack 这类轨上。
  const targetsByKey = new Map();
  for (const item of targetDoc?.keyedClips || []) {
    if (!item) continue;
    targetsByKey.set(`${item.trackPathId}:${item.clipIndex}`, item.target || null);
  }
  const allClips = [];
  const animationClips = [];
  for (const track of clipDoc?.tracks || []) {
    const clips = track.clips || [];
    for (let index = 0; index < clips.length; index += 1) {
      const key = `${track.pathId}:${index}`;
      // 配上目标 = 这个 clip 的 asset 是 AnimationPlayableAsset；目标本身可以为 null
      //（m_Clip 空指针是合法的空动画段）。
      const paired = targetsByKey.has(key);
      const target = paired ? targetsByKey.get(key) : null;
      const value = clipFields(clips[index], track.class, track.name, target, track.pathId, index);
      allClips.push(value);
      if (paired) animationClips.push(value);
    }
  }
  const totalDuration = allClips.reduce(
    (max, clip) => Math.max(max, clip.start + clip.duration), 0,
  );
  const fixtureTarget = animationClips.find(
    (clip) => clip.target && String(clip.target.targetPackage || '').includes('__fixture__'),
  )?.target || (targetDoc?.clips || []).find(
    (target) => target && String(target.targetPackage || '').includes('__fixture__'),
  ) || null;
  const timeline = (trackDoc?.timelines || []).find((item) => item.name === entry.timeline) || entry.track;
  return {
    entry,
    trackDoc,
    clipDoc,
    targetDoc,
    targetError: targetResult.error,
    timeline,
    allClips,
    animationClips,
    totalDuration,
    fixtureTarget,
    clipCount: allClips.length,
    emoticonClips: allClips.filter((clip) => clip.class === 'EmoticonTrack'),
  };
}

// attach 的选取：clip 名尾部的三位号就是挂点号（`..._033_S` ⇒ 挂点 `033` ⇒ loc_start033）。
// 给了号就按号取，取不到才退回第一条 —— 一件家具有五个座位，按下标取等于永远坐同一个位置。
export function attachForTarget(attachDoc, target, attachId = '') {
  const packageName = target?.targetPackage;
  const packageDoc = packageName ? attachDoc?.packages?.[packageName] : null;
  const entries = packageDoc?.entries || [];
  const byId = attachId ? entries.find((item) => String(item.id) === String(attachId)) : null;
  const entry = byId || entries[0] || null;
  return {
    packageName: packageName || '',
    packageDoc,
    entry,
    entries,
    attachId: entry ? String(entry.id ?? '') : '',
    matchedById: !!byId,
  };
}

export async function findFixtureAnimation(fixturePackage) {
  if (!fixturePackage) return { status: 'none', url: '', files: [] };
  const listing = await listDirectory('perf-animations/');
  if (!listing.ok) return { status: 'missing', url: '', files: [], error: listing.error };
  const exact = `${fixturePackage}.glb`;
  const match = listing.files.find((name) => name === exact);
  return match
    ? { status: 'ready', url: assetUrl(`perf-animations/${match}`), files: [match] }
    : { status: 'missing', url: '', files: listing.files, error: `没有匹配 ${exact}` };
}

export async function findFixtureGeometry(fixturePackage) {
  if (!fixturePackage) return { status: 'none', url: '', files: [] };
  const listing = await listDirectory('fixture-models/');
  if (!listing.ok) return { status: 'missing', url: '', files: [], error: listing.error };
  const exact = `${fixturePackage}.glb`;
  const files = listing.files.filter((name) => name.toLowerCase().endsWith('.glb'));
  const match = files.find((name) => name === exact)
    || files.find((name) => name.includes(fixturePackage));
  return match
    ? { status: 'ready', url: assetUrl(`fixture-models/${match}`), files: [match] }
    : { status: 'missing', url: '', files, error: `没有匹配 ${fixturePackage}.glb` };
}
