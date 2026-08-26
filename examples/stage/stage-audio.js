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

/** 把 index.json 里的音频账读成一张按 cue 名索引的表。 */
export function audioCatalog(index) {
  const audio = index?.audio || {};
  const cues = new Map();
  for (const pkg of audio.packages || []) {
    for (const stream of pkg.streams || []) {
      cues.set(String(stream.cue), { ...stream, package: pkg.package });
    }
  }
  const siteBgms = (index?.siteBgms || []).map((row) => ({ ...row }));
  const fallbacks = (index?.siteSoundFallbacks || []).map((row) => ({ ...row }));
  return {
    cues,
    siteBgms,
    fallbacks,
    // 映射里点名了但产物里没有这条流 —— 这是缺口，不能在界面上装作没有。
    namedButAbsent: [...siteBgms, ...fallbacks]
      .map((row) => String(row.cue))
      .filter((cue) => !cues.has(cue)),
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
    this.decoded.set(cue, {
      buffer,
      claimedSamples: claimed,
      decodedSamples: got,
      driftSamples: got - claimed,
      claimedSeconds: Number(stream.durationSeconds) || 0,
      decodedSeconds: buffer.duration,
      contextRate: context.sampleRate,
      fileRate: Number(stream.sampleRate) || 0,
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
        driftSamples: info.driftSamples,
        fileRate: info.fileRate,
        contextRate: info.contextRate,
      });
    }
    return {
      cues: this.catalog ? this.catalog.cues.size : 0,
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
