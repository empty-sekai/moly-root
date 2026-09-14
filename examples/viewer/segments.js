// segments.js — motion phase transitions (S→L→E)
//
// The public clip convention uses _S, _L, _E, and _O suffixes for Start,
// Loop, End, and OneShot. S transitions into L with a 0.5 s cross-fade;
// stopping transitions L into E with the same fade. Single clips play directly.

import * as THREE from './three.module.min.js';

export const FADE = 0.5;

export function splitName(name) {
  const m = /^(.*)_(S|L|E|O)$/.exec(name);
  return m ? { base: m[1], phase: m[2] } : { base: name, phase: null };
}

// clips: [{name, loop?}]  → families: [{base, segs:{S?,L?,E?,O?}, plain?}]
export function groupClips(clips) {
  const fams = new Map();
  for (const c of clips) {
    const { base, phase } = splitName(c.name);
    if (!fams.has(base)) fams.set(base, { base, segs: {}, plain: null });
    const f = fams.get(base);
    if (phase) f.segs[phase] = c.name;
    else f.plain = c.name;
  }
  return [...fams.values()];
}

// Index v2 owns the source loop flag. A missing value is deliberately null:
// it must not become either an explicit false or a suffix-based source guess.
export function loopByNameFromIndex(index, clips = []) {
  const version = index.version === undefined ? 1 : index.version;
  if (version !== 1 && version !== 2) throw new Error(`Unsupported motion index version: ${version}`);
  const field = version === 2 ? 'sourceLoopTime' : 'loop';
  const loop = new Map();
  for (const family of Object.values(index.clips || {})) {
    for (const segment of Object.values(family.segments || {})) {
      if (segment && segment.name) {
        const value = segment[field];
        loop.set(segment.name, typeof value === 'boolean' ? value : null);
      }
    }
  }
  for (const clip of clips) if (!loop.has(clip.name)) loop.set(clip.name, null);
  return loop;
}

export class SegmentController {
  // loopByName: bool = known, null = unresolved, absent = unindexed legacy clip.
  constructor(mixer, clipByName, loopByName) {
    this.mixer = mixer;
    this.clipByName = clipByName;
    this.loopByName = loopByName || new Map();
    this.current = null;       // 当前 action
    this.currentName = null;
    this.phase = 'idle';       // idle|S|L|E|single
    this.family = null;
    this.unresolved = null;
    this.onchange = null;
    mixer.addEventListener('finished', (e) => this._onFinished(e));
  }

  _emit() { if (this.onchange) this.onchange({ phase: this.phase, clip: this.currentName, family: this.family, unresolved: this.unresolved }); }

  _isLoop(name, phase) {
    const m = this.loopByName.get(name);
    if (this.loopByName.has(name)) return typeof m === 'boolean' ? m : null;
    return phase === 'L'; // 仅兼容没有索引记录的旧式内嵌剪辑
  }

  _start(name, loop, fade) {
    const clip = this.clipByName.get(name);
    if (!clip) return null;
    if (typeof loop !== 'boolean') {
      this.unresolved = { clip: name, reason: 'missing loop metadata' };
      return null;
    }
    this.unresolved = null;
    const action = this.mixer.clipAction(clip);
    action.reset();
    action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
    action.clampWhenFinished = true;
    action.enabled = true;
    if (this.current && this.current !== action) {
      action.play();
      this.current.crossFadeTo(action, fade, false);
    } else {
      action.fadeIn(fade * 0.5).play();
    }
    this.current = action;
    this.currentName = name;
    return action;
  }

  // 族播放:有 S 则 S→L,无 S 有 L 则直接 L,再不然播 plain/E/O 单段
  playFamily(family) {
    const s = family.segs.S, l = family.segs.L;
    let action, phase;
    if (s && l) { phase = 'S'; action = this._start(s, false, FADE); }
    else if (l) { phase = 'L'; action = this._start(l, true, FADE); }
    else {
      const only = family.plain || family.segs.O || family.segs.S || family.segs.E;
      if (!only) return false;
      phase = 'single';
      action = this._start(only, this._isLoop(only, splitName(only).phase), FADE);
    }
    if (!action) { this._emit(); return false; }
    this.family = family;
    this.phase = phase;
    this._emit();
    return true;
  }

  // 单段点播使用索引循环语义; 未解析时保持当前播放并报告缺口。
  playSegment(name, familyRef) {
    const action = this._start(name, this._isLoop(name, splitName(name).phase), FADE);
    if (!action) { this._emit(); return false; }
    this.family = familyRef || this.family;
    this.phase = 'single';
    this._emit();
    return true;
  }

  // 停止:L(或 S)crossFade 0.5s 进 E;无 E 则原地停在当前姿态
  stopToEnd() {
    const fam = this.family;
    if (!fam) return false;
    const e = fam.segs.E;
    if (!e || (this.phase !== 'L' && this.phase !== 'S')) return false;
    this.phase = 'E';
    this._start(e, false, FADE);
    this._emit();
    return true;
  }

  _onFinished(ev) {
    if (ev.action !== this.current) return;
    if (this.phase === 'S' && this.family && this.family.segs.L) {
      this.phase = 'L';
      this._start(this.family.segs.L, true, FADE);
      this._emit();
    } else if (this.phase === 'E') {
      this.phase = 'idle'; // 停在 E 末帧(clamp)
      this._emit();
    } else if (this.phase === 'single') {
      this.phase = 'idle';
      this._emit();
    }
  }
}
