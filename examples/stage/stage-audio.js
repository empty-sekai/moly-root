// stage-audio.js — 声音：按 cue 名寻址，循环点按采样数算
//
// 产物 `phenomena/audio/loop.json` 每条流都带两套循环点：`loopStartSeconds` 这类**四舍五入
// 到 6 位**的秒，和 `loopStartSamples` 这类整数采样。本模块一律用采样数除以该流自己的
// 采样率，不用那个秒 —— 44100 Hz 下 6 位秒的量化误差最大约 0.02 个采样，听不出来，但
// 「我们用的是原值」和「我们用的是原值的近似」是两句不同的话，而**只有后者会在别处累积**。
//
// 寻址用游戏自己的方案：`index.json` 的 `siteBgms` 按 (siteId, brightnessType) 给 cue，
// `siteSoundFallbacks` 按 siteId 给环境音。**没有「现象 → cue」这一层**，所以本模块不造一个。
//
// 格式：默认 ogg（全部 31 条共 37 MiB；wav 是 446 MiB）。Vorbis 不保证解码后采样数与原始
// 一致（编码器前导/补齐），所以本模块**测量并报告**解码后的采样数与产物记的差，而不是假设
// 它为零 —— 差多少是个数字，不是一句「应该没问题」。

const AUDIO_ROOT = 'phenomena/';

/** 一条流的循环窗口，单位秒，由采样数与该流采样率算出。 */
export function loopWindow(stream) {
  const rate = Number(stream.sampleRate) || 0;
  if (!rate) return null;
  return {
    start: Number(stream.loopStartSamples) / rate,
    end: Number(stream.loopEndSamples) / rate,
    startSamples: Number(stream.loopStartSamples),
    endSamples: Number(stream.loopEndSamples),
    rate,
  };
}

/** 把 index.json 里的音频账读成一张按「cue + 子曲号」索引的表。
 *
 * **不能只按 cue 名索引**：实测 31 条流里 `se_shooring_star` 一个名字底下有 6 条子曲，
 * 按名字建 Map 会让后 5 条静默盖掉前 5 条，界面上只剩 26 条而没有一个字说少了 5 条。
 * 所以键带上子曲号，并把「一名多曲」的条数单独记出来。
 */
export function audioCatalog(index) {
  const audio = index?.audio || {};
  const cues = new Map();
  const byName = new Map();
  let streams = 0;
  for (const pkg of audio.packages || []) {
    for (const stream of pkg.streams || []) {
      streams += 1;
      const name = String(stream.cue);
      const subsong = Number(stream.subsong) || 0;
      const list = byName.get(name) || [];
      list.push(subsong);
      byName.set(name, list);
      // 同名多曲时键上带子曲号，单曲的键就是 cue 名本身 —— 映射表点名的是 cue 名。
      cues.set(list.length > 1 || subsong > 1 ? `${name}#${subsong}` : name,
               { ...stream, package: pkg.package, cueName: name, subsong });
    }
  }
  // 第一条同名流当初用的是裸名字；若它后来发现有兄弟，补一个带号的别名，
  // 让「按 cue 名找得到」和「六条都在」同时成立。
  for (const [name, subsongs] of byName) {
    if (subsongs.length <= 1) continue;
    const bare = cues.get(name);
    if (bare) cues.set(`${name}#${bare.subsong}`, bare);
  }
  const siteBgms = (index?.siteBgms || []).map((row) => ({ ...row }));
  const fallbacks = (index?.siteSoundFallbacks || []).map((row) => ({ ...row }));
  const resolvable = (cue) => cues.has(String(cue))
    || [...cues.values()].some((s) => s.cueName === String(cue));
  return {
    cues,
    siteBgms,
    fallbacks,
    streams,
    // 一个 cue 名底下不止一条流的，记出来：这是产物的形状，不是错误，
    // 但如果不记，界面上的条数就会比产物少而没人知道少在哪。
    multiSubsong: [...byName.entries()]
      .filter(([, list]) => list.length > 1)
      .map(([name, list]) => ({ cue: name, subsongs: list.length })),
    // 映射里点名了但产物里没有这条流 —— 这是缺口，不能在界面上装作没有。
    namedButAbsent: [...siteBgms, ...fallbacks]
      .map((row) => String(row.cue))
      .filter((cue) => !resolvable(cue)),
    decoderPresent: !!audio.decoderPresent,
    transcoderPresent: !!audio.transcoderPresent,
  };
}

export class StageAudio {
  constructor(base) {
    this.base = base.endsWith('/') ? base : `${base}/`;
    this.context = null;
    this.source = null;
    this.gain = null;
    this.catalog = null;
    this.playing = '';
    this.format = 'ogg';
    this.volume = 0.7;
    /** 每条已解码流的实测账：解码采样数 vs 产物记的采样数。 */
    this.decoded = new Map();
    this.lastError = '';
  }

  attach(catalog) {
    this.catalog = catalog;
    return this;
  }

  /** AudioContext 要等用户手势才能出声；解码本身不需要，所以两件事分开。 */
  ensureContext() {
    if (!this.context) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) { this.lastError = '本浏览器没有 Web Audio'; return null; }
      this.context = new Ctor();
      this.gain = this.context.createGain();
      this.gain.gain.value = this.volume;
      this.gain.connect(this.context.destination);
    }
    return this.context;
  }

  async decode(cue) {
    const stream = this.catalog?.cues.get(String(cue));
    if (!stream) { this.lastError = `产物里没有 cue ${cue}`; return null; }
    if (this.decoded.has(cue)) return this.decoded.get(cue).buffer;
    const context = this.ensureContext();
    if (!context) return null;
    const path = this.format === 'wav' ? stream.wav : stream.ogg;
    const response = await fetch(`${this.base}${AUDIO_ROOT}${path}`);
    if (!response.ok) {
      this.lastError = `${path}: ${response.status}`;
      return null;
    }
    const bytes = await response.arrayBuffer();
    const buffer = await context.decodeAudioData(bytes);
    // 实测账：解码出来的采样数与产物记的差。ogg 有前导/补齐，wav 没有；这个差是
    // **测出来的**，不是按格式假定的。窗口用产物的采样数算，所以差值直接说明循环点
    // 在解码后的缓冲里偏了几个采样。
    const claimed = Number(stream.samples) || 0;
    const got = buffer.length;
    const fileRate = Number(stream.sampleRate) || 0;
    // 解码后的采样数与产物记的差要**分解**，不能当成一个数报。
    // AudioContext 有自己的采样率（实测 44100 的文件进 48000 的上下文），重采样本身
    // 就会改变采样数 —— 那部分是预期的，不是缺陷。减掉它剩下的才是编解码器的前导/补齐。
    // 混在一起报会让一个正常的重采样看着像 286208 个采样的错。
    // 循环点用**秒**配给播放节点，而秒对重采样不变，所以这一项不影响循环正确性。
    const expected = fileRate
      ? Math.round(claimed * (context.sampleRate / fileRate)) : claimed;
    this.decoded.set(cue, {
      buffer,
      claimedSamples: claimed,
      decodedSamples: got,
      expectedSamples: expected,
      resampleDelta: expected - claimed,
      residualSamples: got - expected,
      claimedSeconds: Number(stream.durationSeconds) || 0,
      decodedSeconds: buffer.duration,
      contextRate: context.sampleRate,
      fileRate,
      format: this.format,
    });
    return buffer;
  }

  async play(cue) {
    const buffer = await this.decode(cue);
    if (!buffer) return false;
    const context = this.ensureContext();
    if (context.state === 'suspended') {
      try { await context.resume(); } catch { /* 需要用户手势，界面上如实说 */ }
    }
    this.stop();
    const stream = this.catalog.cues.get(String(cue));
    const source = context.createBufferSource();
    source.buffer = buffer;
    const window_ = loopWindow(stream);
    if (stream.loop && window_) {
      source.loop = true;
      source.loopStart = window_.start;
      source.loopEnd = window_.end;
    }
    source.connect(this.gain);
    source.start(0, stream.loop && window_ ? 0 : 0);
    this.source = source;
    this.playing = String(cue);
    return true;
  }

  stop() {
    if (this.source) {
      try { this.source.stop(); } catch { /* 已经停了 */ }
      try { this.source.disconnect(); } catch { /* 同上 */ }
    }
    this.source = null;
    this.playing = '';
  }

  setVolume(value) {
    this.volume = Math.max(0, Math.min(1, Number(value) || 0));
    if (this.gain) this.gain.gain.value = this.volume;
  }

  /** 取证用：每条已解码流的循环窗口与实测漂移。 */
  report() {
    const rows = [];
    for (const [cue, info] of this.decoded) {
      const stream = this.catalog?.cues.get(cue);
      const window_ = stream ? loopWindow(stream) : null;
      rows.push({
        cue,
        format: info.format,
        loop: !!stream?.loop,
        loopStartSamples: window_?.startSamples ?? null,
        loopEndSamples: window_?.endSamples ?? null,
        loopStartSeconds: window_ ? +window_.start.toFixed(9) : null,
        loopEndSeconds: window_ ? +window_.end.toFixed(9) : null,
        claimedSamples: info.claimedSamples,
        decodedSamples: info.decodedSamples,
        expectedSamples: info.expectedSamples,
        resampleDelta: info.resampleDelta,
        residualSamples: info.residualSamples,
        secondsError: +(info.decodedSeconds - info.claimedSeconds).toFixed(6),
        fileRate: info.fileRate,
        contextRate: info.contextRate,
      });
    }
    return {
      cues: this.catalog ? this.catalog.cues.size : 0,
      streams: this.catalog ? this.catalog.streams : 0,
      multiSubsong: this.catalog ? this.catalog.multiSubsong : [],
      looping: this.catalog
        ? [...this.catalog.cues.values()].filter((s) => s.loop).length : 0,
      siteBgms: this.catalog ? this.catalog.siteBgms.length : 0,
      namedButAbsent: this.catalog ? this.catalog.namedButAbsent : [],
      decodedCount: rows.length,
      contextState: this.context ? this.context.state : 'none',
      playing: this.playing,
      lastError: this.lastError,
      rows,
    };
  }
}
