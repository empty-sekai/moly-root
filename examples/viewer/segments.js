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

export class SegmentController {
  // mixer: THREE.AnimationMixer;clipByName: Map name→AnimationClip;loopByName: Map name→bool|undefined
  constructor(mixer, clipByName, loopByName) {
    this.mixer = mixer;
    this.clipByName = clipByName;
    this.loopByName = loopByName || new Map();
    this.current = null;       // 当前 action
    this.currentName = null;
    this.phase = 'idle';       // idle|S|L|E|single
    this.family = null;
    this.onchange = null;
    mixer.addEventListener('finished', (e) => this._onFinished(e));
  }

  _emit() { if (this.onchange) this.onchange({ phase: this.phase, clip: this.currentName, family: this.family }); }

  _isLoop(name, phase) {
    const m = this.loopByName.get(name);
    if (m !== undefined && m !== null) return !!m;
    return phase === 'L'; // manifest 缺位时按后缀推断
  }

  _start(name, loop, fade) {
    const clip = this.clipByName.get(name);
    if (!clip) return null;
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
    this.family = family;
    const s = family.segs.S, l = family.segs.L;
    if (s && l) { this.phase = 'S'; this._start(s, false, FADE); }
    else if (l) { this.phase = 'L'; this._start(l, true, FADE); }
    else {
      const only = family.plain || family.segs.O || family.segs.S || family.segs.E;
      if (!only) return;
      this.phase = 'single';
      this._start(only, this._isLoop(only, splitName(only).phase), FADE);
    }
    this._emit();
  }

  // 单段照播(点段名):loop 按 manifest / 后缀
  playSegment(name, familyRef) {
    this.family = familyRef || this.family;
    this.phase = 'single';
    this._start(name, this._isLoop(name, splitName(name).phase), FADE);
    this._emit();
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
