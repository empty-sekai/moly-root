// 现象音频:目录(catalog) + 播放控制器(controller)。
// 目录:按 cue 名建索引;重复 cue 额外给 cue#subsong 地址,31 条流一条不塌。
// 控制器:AudioContext + GainNode + fetch → arrayBuffer → decodeAudioData →
// AudioBufferSourceNode;循环区间直接读 loopStartSeconds/loopEndSeconds(不反算);
// 取不到的流计入失败,不在其他流上静默。面板状态经 status() 暴露。

function asText(value) {
  return value == null ? '' : String(value);
}

function asNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function duplicateKey(cue, subsong) {
  return `${cue}#${subsong}`;
}

/**
 * 读索引,不用「第 N 条流」当下标。
 * 裸 cue 名寻址唯一流与重复 cue 的第一条;每条重复流另有 cue#subsong 显式地址。
 * opts.cueOnly:跳过 cue#subsong 地址(重复 cue 塌成一条)——寻址对照用,
 * 让「31 条」塌成「26 条」这一事实可被量出来。
 */
export function audioCatalog(index, opts = {}) {
  const cueOnly = !!opts.cueOnly;
  const audio = index?.audio || {};
  const cues = new Map();
  const byName = new Map();
  const streams = [];

  for (const pkg of Array.isArray(audio.packages) ? audio.packages : []) {
    for (const raw of Array.isArray(pkg.streams) ? pkg.streams : []) {
      const cue = asText(raw.cue);
      if (!cue) continue;
      const subsong = asNumber(raw.subsong, 0);
      const stream = { ...raw, cueName: cue, subsong, package: pkg.package };
      const siblings = byName.get(cue) || [];
      siblings.push(stream);
      byName.set(cue, siblings);
      streams.push(stream);
    }
  }

  for (const [cue, list] of byName) {
    if (list.length === 1) {
      cues.set(cue, list[0]);
      continue;
    }
    if (!cueOnly) {
      for (const stream of list) cues.set(duplicateKey(cue, stream.subsong), stream);
    }
    // 裸 cue 名仍可达第一条(播放按 cue 路由时它当作首条)。
    cues.set(cue, list[0]);
  }

  const siteBgms = Array.isArray(index?.siteBgms)
    ? index.siteBgms.map((row) => ({ ...row })) : [];
  const siteSoundFallbacks = Array.isArray(index?.siteSoundFallbacks)
    ? index.siteSoundFallbacks.map((row) => ({ ...row })) : [];
  const resolve = (cue) => {
    const name = asText(cue);
    return cues.get(name) || streams.find((stream) => stream.cueName === name) || null;
  };
  const namedRoutes = [...siteBgms, ...siteSoundFallbacks];

  return {
    cues,
    streams,
    streamsCount: streams.length,
    cueNamesCount: byName.size,
    multiSubsong: [...byName.entries()]
      .filter(([, list]) => list.length > 1)
      .map(([cue, list]) => ({ cue, subsongs: list.map((stream) => stream.subsong) })),
    siteBgms,
    siteSoundFallbacks,
    namedButAbsent: namedRoutes.filter((row) => !resolve(row.cue)).map((row) => ({ ...row })),
    decoderPresent: !!audio.decoderPresent,
    transcoderPresent: !!audio.transcoderPresent,
    resolve,
  };
}

/** 读循环区间的秒值,不从采样数反算。 */
export function loopWindow(stream) {
  if (!stream || !stream.loop) return null;
  const start = Number(stream.loopStartSeconds);
  const end = Number(stream.loopEndSeconds);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return { start, end };
}

// ---- 寻址与路由 ------------------------------------------------------------

/** 流的地址:裸 cue 名,重复 cue 的下一条起用 cue#subsong。
 *  裸名在本目录里恒指向多流 cue 的第一条,所以第一条的地址就是裸名;
 *  与流在 packages 数组里的位置无关。 */
export function addressOf(cat, stream) {
  const list = cat.streams.filter((s) => s.cueName === stream.cueName);
  if (list.length <= 1) return stream.cueName;
  return list[0] === stream
    ? stream.cueName
    : duplicateKey(stream.cueName, stream.subsong);
}

/**
 * 按律求该现象在某个站点播什么(music 层 + ambience 层):
 * - music:现象的 bgms 行取代整层;没有则走站点底层 siteBgms[(siteId, 亮度)]。
 * - 亮度取现象 master.brightnessType;master 缺(没有 master 行的现象)取 'none'。
 * - ambience:现象的 siteSounds 行里这一站的行优先;没有则取这一站的
 *   siteSoundFallbacks 行(站点的 other 行);再没有则这一站安静。
 */
export function routeFor(index, phenomenonName, siteId) {
  const phen = (index?.phenomena || {})[phenomenonName] || null;
  if (!phen) {
    return { music: null, se: [], from: { music: 'phenomenon-missing', se: 'phenomenon-missing' }, brightness: null, siteId };
  }
  const master = phen.master || {};
  // 没有 master 行的现象取 'none'(站点底层表里有一行这种亮度,如节庆花园)。
  const brightness = master.brightnessType != null ? master.brightnessType : 'none';

  const bgmRows = Array.isArray(phen.bgms) ? phen.bgms : [];
  let music = null;
  let musicFrom = null;
  if (bgmRows.length) {
    music = bgmRows[0];
    musicFrom = `phenomenon.bgms:${music.id}`;
  } else if (siteId != null) {
    const rows = (index?.siteBgms || []).filter((r) => r.siteId === siteId);
    const row = rows.find((r) => r.brightnessType === brightness)
      || rows.find((r) => r.brightnessType == null);
    if (row) { music = row; musicFrom = `siteBgms:${row.id}`; }
    else musicFrom = `siteBgms:no-row(siteId=${siteId},bright=${brightness})`;
  } else {
    musicFrom = 'siteBgms:no-siteId';
  }

  const siteRows = (Array.isArray(phen.siteSounds) ? phen.siteSounds : [])
    .filter((r) => r.siteId === siteId);
  let se = siteRows;
  let seFrom = siteRows.length ? 'phenomenon.siteSounds' : null;
  if (!siteRows.length) {
    const fb = (index?.siteSoundFallbacks || []).filter((r) => r.siteId === siteId);
    if (fb.length) { se = fb; seFrom = 'siteSoundFallbacks'; }
    else if (siteId != null) seFrom = 'none';
  }

  return { music, se, from: { music: musicFrom, se: seFrom || 'none' }, brightness, siteId };
}

// ---- 播放控制器 -------------------------------------------------------------

/**
 * 播放控制器。opts:
 * - base: 数据的父目录(与 viewer 的 ?base= 一致)
 * - cueOnly: 只测试用,目录不挂 cue#subsong 地址(重复 cue 塌成一条)
 */
export function createAudioController(opts = {}) {
  const base = String(opts.base || '.').replace(/\/+$/, '');
  const cueOnly = !!opts.cueOnly;
  const srcRoot = `${base}/phenomena`;

  const state = {
    ready: false,
    loadError: null,
    armed: false,          // 音频面板开
    active: false,         // 环境层开(现象在放才该有声音)
    volume: 1,
    context: 'uncreated',  // uncreated | suspended | running | closed
    currentTime: 0,
    sampleRate: 0,
    phenomenon: null,
    siteKey: null,
    siteId: null,
    brightness: null,
    music: null,           // { cue, address, ogg, loopInterval, from, streamState }
    se: [],                // 同 music 形状的数组
    missingCues: [],       // 路由里有行但目录里查不到流
    failures: [],          // { address, ogg, reason }(按地址去重)
    playing: 0,            // 活着(未 ended)的 source 数
    energy: -1,            // 目的地前 analyser 的 RMS;非运行态 -1
  };

  let index = null;
  let cat = null;
  let ctx = null;
  let masterGain = null;
  let analyser = null;
  let siteIdFor = null;         // (siteKey) => siteId|null,由接入方(站点表行)供给
  const buffers = new Map();    // address -> Promise<AudioBuffer>
  const bufferFailures = new Set();
  const activeSources = [];     // { gain, node, target, state }
  let seqCounter = 0;

  async function fetchJson(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function load() {
    try {
      index = await fetchJson(`${srcRoot}/index.json`);
    } catch (e) {
      state.loadError = `phenomena/index.json: ${String(e.message || e)}`.slice(0, 120);
      return false;
    }
    cat = audioCatalog(index, { cueOnly });
    state.ready = true;
    return true;
  }

  function addressOfStream(stream) {
    return addressOf(cat, stream);
  }

  // ---- 音频上下文 ----

  function ensureContext() {
    if (ctx) return ctx;
    state.context = 'uncreated';
    try {
      ctx = new AudioContext();
    } catch (e) {
      state.context = 'failed';
      state.loadError = `AudioContext: ${String(e.message || e)}`.slice(0, 120);
      ctx = null;
      return null;
    }
    masterGain = ctx.createGain();
    // 主增益初始就是面板音量:控制器自己的输出,不等「音量滑块动过」才出声。
    masterGain.gain.value = state.volume;
    analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    masterGain.connect(analyser);
    analyser.connect(ctx.destination);
    state.sampleRate = ctx.sampleRate;
    ctx.onstatechange = () => { state.context = ctx.state; };
    state.context = ctx.state;
    return ctx;
  }

  function resumeContext() {
    const c = ensureContext();
    if (!c) return false;
    if (c.state === 'suspended') {
      c.resume().catch(() => { /* 状态由 onstatechange 反映 */ });
    }
    return c.state !== 'closed';
  }

  function applyVolume() {
    if (masterGain) masterGain.gain.value = state.volume;
  }

  // ---- 解码 ----

  function bufferFor(address, stream) {
    if (buffers.has(address)) return buffers.get(address);
    if (bufferFailures.has(address)) return Promise.reject(new Error('previous failure'));
    const done = (async () => {
      const url = `${srcRoot}/${stream.ogg}`;
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status} ${stream.ogg}`);
      const bytes = await r.arrayBuffer();
      return ctx.decodeAudioData(bytes);
    })();
    buffers.set(address, done);
    done.catch(() => { bufferFailures.add(address); });
    return done;
  }

  // ---- 换音 ----

  function stopAll() {
    for (const s of activeSources.splice(0)) {
      s.stoppedByUs = true;
      try { s.gain.gain.value = 0; s.node.stop(); } catch { /* 已停 */ }
    }
    state.playing = 0;
  }

  function fadeOutThen(seconds, action) {
    const now = ctx ? ctx.currentTime : 0;
    if (ctx && seconds > 0) {
      for (const s of activeSources) {
        const g = s.gain.gain;
        g.cancelScheduledValues(now);
        g.setValueAtTime(g.value, now);
        g.linearRampToValueAtTime(0, now + seconds);
      }
      setTimeout(() => {
        stopAll();
        action();
      }, Math.ceil(seconds * 1000) + 40);
    } else {
      stopAll();
      action();
    }
  }

  /**
   * 起一条流。queue:同 cue 的多条流(一次性 cue,如流星音)按序播一遍;
   * 播完最后一条就停下(发射节奏不在数据里,不发明)。
   */
  function spawn(stream, loop, gainStart, seconds, seqIndex, queue, queueIndex) {
    const address = addressOfStream(stream);
    const g = ctx.createGain();
    g.gain.value = gainStart;
    const node = ctx.createBufferSource();
    node.connect(g);
    g.connect(masterGain);
    const target = {
      id: ++seqCounter, address, cue: stream.cueName, ogg: stream.ogg,
      loopInterval: loop, stream, gain: g, node, sequence: seqIndex,
      state: 'loading', failedReason: null, started: false,
      stoppedByUs: false, queue: queue || null, queueIndex: queueIndex || 0,
    };
    activeSources.push(target);
    const startNode = (buffer) => {
      node.buffer = buffer;
      if (loop) { node.loop = true; node.loopStart = loop.start; node.loopEnd = loop.end; }
      node.start(0);
      target.started = true;
      target.state = 'playing';
      if (seconds > 0) {
        g.gain.cancelScheduledValues(0);
        g.gain.setValueAtTime(gainStart, ctx.currentTime);
        g.gain.linearRampToValueAtTime(1, ctx.currentTime + seconds);
      } else {
        g.gain.value = 1;
      }
      g.gain.value = Math.min(g.gain.value, state.volume);
      state.playing += 1;
      node.addEventListener('ended', () => {
        const i = activeSources.indexOf(target);
        if (i >= 0) activeSources.splice(i, 1);
        state.playing = Math.max(0, state.playing - 1);
        if (!target.stoppedByUs && target.queue && target.queueIndex + 1 < target.queue.length) {
          const next = target.queue[target.queueIndex + 1];
          spawn(next, loopWindow(next), gainStart, seconds, seqIndex, target.queue, target.queueIndex + 1);
        }
      });
    };
    bufferFor(address, stream).then(startNode).catch((e) => {
      target.state = 'failed';
      target.failedReason = String(e.message || e).slice(0, 140);
      pushFailure(address, stream.ogg, target.failedReason);
      const i = activeSources.indexOf(target);
      if (i >= 0) activeSources.splice(i, 1);
      // 失败的那条跳过,继续序列里的下一条(其余照放)。
      if (target.queue && target.queueIndex + 1 < target.queue.length) {
        const next = target.queue[target.queueIndex + 1];
        spawn(next, loopWindow(next), gainStart, seconds, seqIndex, target.queue, target.queueIndex + 1);
      }
    });
    return target;
  }

  function pushFailure(address, ogg, reason) {
    for (const f of state.failures) if (f.address === address) return;
    state.failures.push({ address, ogg, reason });
  }

  function currentRoute() {
    if (!index || !state.phenomenon) return null;
    return routeFor(index, state.phenomenon, state.siteId);
  }

  function recalcSite() {
    if (siteIdFor && state.siteKey != null) {
      try { state.siteId = siteIdFor(state.siteKey); } catch { state.siteId = null; }
    } else {
      state.siteId = null;
    }
  }

  function streamsOf(cue) {
    return cat.streams.filter((s) => s.cueName === cue);
  }

  /** 全部重算:当前现象与站点下的目标集,按给定淡化秒数换音。 */
  function apply(seconds) {
    if (!ctx || !state.ready) return;
    const route = currentRoute();
    const want = !!(state.armed && state.active && route);
    if (!want) { fadeOutThen(0, () => { state.music = null; state.se = []; }); return; }
    const musicFrom = route.from.music;
    const musicRow = route.music;
    state.brightness = route.brightness;
    fadeOutThen(seconds, () => {
      state.music = {
        cue: musicRow ? musicRow.cue : null,
        address: null, ogg: null, loopInterval: null,
        from: musicFrom,
        streamState: musicRow ? 'queued' : 'none',
      };
      if (musicRow) {
        const stream = cat.resolve(musicRow.cue);
        if (!stream) {
          state.music.streamState = 'missing';
          if (!state.missingCues.includes(musicRow.cue)) state.missingCues.push(musicRow.cue);
        } else {
          const loop = loopWindow(stream);
          const addr = addressOfStream(stream);
          const all = streamsOf(musicRow.cue);
          const queue = all.length > 1 ? all : null;
          const t = spawn(stream, loop, seconds > 0 ? 0 : state.volume, seconds, 0, queue, 0);
          state.music = {
            cue: musicRow.cue, address: addr, ogg: stream.ogg,
            loopInterval: loop, from: musicFrom,
            streamState: 'started', targetId: t.id,
          };
        }
      }
      const se = [];
      const seen = new Set();
      for (const row of route.se) {
        if (seen.has(row.cue)) continue;
        seen.add(row.cue);
        const stream = cat.resolve(row.cue);
        if (!stream) {
          se.push({ cue: row.cue, address: null, ogg: null, loopInterval: null, streamState: 'missing' });
          if (!state.missingCues.includes(row.cue)) state.missingCues.push(row.cue);
          continue;
        }
        const loop = loopWindow(stream);
        const addr = addressOfStream(stream);
        const all = streamsOf(row.cue);
        const queue = all.length > 1 ? all : null;
        const t = spawn(stream, loop, seconds > 0 ? 0 : state.volume, seconds, 0, queue, 0);
        se.push({ cue: row.cue, address: addr, ogg: stream.ogg, loopInterval: loop, streamState: 'started', targetId: t.id });
      }
      state.se = se;
    });
  }

  // ---- 对外 ----

  async function init(onReady) {
    const ok = await load();
    if (ok && onReady) onReady();
    return ok;
  }

  function setArmed(on) {
    state.armed = !!on;
    if (state.armed) {
      resumeContext();
      applyVolume();
      apply(0);
    } else {
      fadeOutThen(0, () => { state.music = null; state.se = []; });
    }
  }

  function setActive(on) {
    state.active = !!on;
    apply(0);
  }

  function setVolume(v) {
    state.volume = Math.max(0, Math.min(1, +v || 0));
    applyVolume();
  }

  function setScene(phenomenon, siteKey, seconds) {
    state.phenomenon = phenomenon || null;
    state.siteKey = siteKey != null ? siteKey : state.siteKey;
    recalcSite();
    apply(Number.isFinite(seconds) ? seconds : 0);
  }

  function setSite(siteKey, seconds) {
    state.siteKey = siteKey;
    recalcSite();
    apply(Number.isFinite(seconds) ? seconds : 0);
  }

  function configure(opts2) {
    if (opts2.siteIdFor) siteIdFor = opts2.siteIdFor;
  }

  /** 测试钩子:换掉索引文档并重建目录(在页面里改数据后重算用)。 */
  function reindex(doc) {
    index = doc;
    cat = audioCatalog(doc);
    buffers.clear();
    bufferFailures.clear();
    apply(0);
  }

  /** 全部流的地址清单(判据「31 条全可达」按它逐条取)。
   *  只列「经目录能解析回自身」的流:正常 31 条;cueOnly 时重复 cue
   *  塌成一条,只剩 26 条可解析 —— 这个数本身就是寻址半径的证据。 */
  function addresses() {
    const out = [];
    if (!cat) return out;
    for (const stream of cat.streams) {
      const address = addressOfStream(stream);
      const resolved = cat.resolve(address);
      if (resolved !== stream) continue;
      out.push({
        address,
        cue: stream.cueName,
        subsong: stream.subsong,
        ogg: stream.ogg,
        what: resolved === stream ? 'ok' : 'miss',
      });
    }
    return out;
  }

  /** 状态快照;withEnergy=true 时顺带算目的地前 analyser 的 RMS(真在出声的证据)。 */
  function status(withEnergy) {
    if (ctx) {
      state.currentTime = ctx.currentTime;
      state.context = ctx.state;
    }
    if (withEnergy && analyser && ctx && ctx.state === 'running') {
      const d = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(d);
      let sum = 0;
      for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
      state.energy = Math.sqrt(sum / d.length);
    } else if (withEnergy) {
      state.energy = -1;
    }
    return {
      ...state,
      streamsCount: cat ? cat.streamsCount : 0,
      cueNamesCount: cat ? cat.cueNamesCount : 0,
      multiSubsong: cat ? cat.multiSubsong : [],
      missingFromRoutes: state.missingCues.slice(),
      failures: state.failures.slice(),
    };
  }

  return {
    init, status, setArmed, setActive, setVolume, setScene, setSite, configure,
    reindex, addresses, loopWindow,
    resolveCue: (cue) => (cat ? cat.resolve(cue) : null),
    routeFor: (phen, siteId) => (index ? routeFor(index, phen, siteId) : null),
    _debug: {
      get ctx() { return ctx; },
      get cat() { return cat; },
      get active() { return activeSources.slice(); },
      masterGain: () => masterGain,
      analyser: () => analyser,
    },
  };
}
