// 表演编排播放器:按 alone-actions.json 驱动「动作 + 眼型 + 口型 + 时序」。
//
// Contract notes for alone-actions.json:
// - 动作与表情是两条独立通道,配对只存在于编排数据里;
// - 气泡是第三条独立通道:op=emoticon(name + showSeconds)开,op=hideEmoticon 收;
//   name 是 emoticons.json items 的键,showSeconds 是编排给的展示时长(到点自己收);
// - scenarios 互斥,不可串成一条时间线;tail 每轮都跑;
// - t 是标称时间轴(此前 wait 之和),不是逐帧时刻表。
//
// 两种选场景策略:
//   cycle    —— 依次轮播每个场景(便于逐个检查表情行为);
//   faithful —— 按原始编排的 randomBranch 权重与 timeGated 概率、时间门运行。

export class PerformancePlayer {
  constructor({ doc, unitId, tables, facial, playMotion, playEmoticon, hideEmoticon, mode = 'cycle', rng = Math.random }) {
    this.unit = doc && doc.units ? doc.units[String(unitId)] : null;
    this.tables = tables;
    this.facial = facial;
    this.playMotion = playMotion;          // (motionBase, phase|null) => void
    this.playEmoticon = playEmoticon;      // (itemName, showSeconds|undefined) => void
    this.hideEmoticon = hideEmoticon;      // () => void
    this.mode = mode;
    this.rng = rng;
    this.lastFired = new Map();            // motionSlot -> 标称秒(用于 timeGated 去重)
    this.elapsed = 0;                      // 全局标称时钟
    this.cursor = -1;                      // cycle 模式的场景游标
    this.queue = [];
    this.clock = 0;
    this.next = 0;
    this.current = null;
    this.lastApplied = null;
    this.emoticon = null;                  // 编排此刻要求显示的气泡 { name, showSeconds, t }
    this.auto = true;                      // idle 时是否自动选取下一个场景(独处循环)
    this.idle = true;                      // 无场景在播;点播或自动选取都会离开 idle
    this.manual = false;                   // 当前队列来自手动点播(播完进 idle,不看 auto 链)
  }

  get ready() { return !!(this.unit && this.tables && this.facial); }

  scenarios() { return this.unit ? this.unit.scenarios : []; }

  // 选下一段:返回 steps 数组(场景 steps + tail steps 依次拼接,tail 时间轴接在后面)
  _select() {
    const tail = (this.unit.tail && this.unit.tail.steps) || [];
    const list = this.scenarios();
    let picked = null;
    if (!list.length) {
      picked = null;
    } else if (this.mode === 'cycle') {
      this.cursor = (this.cursor + 1) % list.length;
      picked = list[this.cursor];
    } else {
      const branches = list.filter((s) => s.kind === 'randomBranch');
      if (branches.length) {
        const roll = this.rng() * 100;
        picked = branches.find((s) => roll >= s.trigger.low && roll < s.trigger.high) || branches[branches.length - 1];
      } else {
        for (const sc of list) {
          const t = sc.trigger || {};
          const slot = t.motionSlot || sc.id;
          const since = this.elapsed - (this.lastFired.get(slot) ?? -Infinity);
          const gate = (t.timeLimitSeconds ?? 0);
          const memory = (t.slotMemorySeconds ?? 0);
          if (since < Math.max(gate, memory)) continue;
          if (this.rng() > (t.probability ?? 1)) continue;
          picked = sc;
          this.lastFired.set(slot, this.elapsed);
          break;
        }
      }
    }
    const steps = [];
    if (picked) for (const s of picked.steps) steps.push(s);
    const base = steps.length ? this._span(picked.steps) : 0;
    for (const s of tail) steps.push({ ...s, t: (s.t || 0) + base });
    this.current = picked;
    return steps;
  }

  // 一段 steps 的标称长度 = 最后一步的 t + 它之后的 wait 时长
  _span(steps) {
    let end = 0;
    for (const s of steps) end = Math.max(end, (s.t || 0) + (s.op === 'wait' ? (s.seconds || 0) : 0));
    return end;
  }

  reset() {
    this._dropEmoticon();   // scenarios 互斥:换段不能把上一轮挂着的气泡漏进下一轮
    this.queue = this._select();
    this.clock = 0;
    this.next = 0;
    this.manual = false;
    this.idle = !this.queue.length;
    this.total = this._span(this.queue);
    this._applyDue(true);
  }

  // 手动点播一个场景:完整播放它(含 tail 收尾),播完进 idle。
  // 点播优先于自动选取 —— 播放期间 update 只推进这条队列,不会切换场景。
  playScenario(scenario) {
    if (!scenario || !scenario.steps) return;
    this._dropEmoticon();
    const tail = (this.unit && this.unit.tail && this.unit.tail.steps) || [];
    const steps = [...scenario.steps];
    const base = this._span(scenario.steps);
    for (const s of tail) steps.push({ ...s, t: (s.t || 0) + base });
    this.current = scenario;
    this.queue = steps;
    this.clock = 0;
    this.next = 0;
    this.manual = true;
    this.idle = false;
    this.total = this._span(this.queue);
    this._applyDue(true);
  }

  // 收掉当前队列与气泡,回 idle(auto 为真时下一帧会自动选取新场景)。
  stopToIdle() {
    this._dropEmoticon();
    this.queue = [];
    this.manual = false;
    this.idle = true;
    this.current = null;
  }

  // 收掉「编排认为正在显示」的气泡(换场景/关表演都走这里)
  _dropEmoticon() {
    if (!this.emoticon) return;
    this.emoticon = null;
    if (this.hideEmoticon) this.hideEmoticon();
  }

  _row(kind, name) {
    const map = kind === 'eye' ? this.tables.eye : this.tables.lip;
    return map ? map.get(name) : null;
  }

  _applyDue(initial) {
    while (this.next < this.queue.length && (this.queue[this.next].t || 0) <= this.clock + 1e-6) {
      const step = this.queue[this.next++];
      if (step.op === 'eye' || step.op === 'mouth') {
        const row = this._row(step.op === 'eye' ? 'eye' : 'mouth', step.pattern);
        if (row) {
          const eyeRow = step.op === 'eye' ? row : this.facial.eyeRow;
          const lipRow = step.op === 'mouth' ? row : this.facial.lipRow;
          this.facial.setPatterns(eyeRow, lipRow);
          this.lastApplied = { op: step.op, pattern: step.pattern, t: step.t };
        }
      } else if (step.op === 'emoticon' && this.playEmoticon) {
        const name = step.name || step.emoticon;
        const seconds = +step.showSeconds > 0 ? +step.showSeconds : null;
        this.emoticon = { name, showSeconds: seconds, t: step.t || 0 };
        this.playEmoticon(name, seconds);
        this.lastApplied = { op: 'emoticon', name, showSeconds: seconds, t: step.t };
      } else if (step.op === 'hideEmoticon' && this.hideEmoticon) {
        this.emoticon = null;
        this.hideEmoticon();
        this.lastApplied = { op: 'hideEmoticon', t: step.t };
      } else if (step.op === 'animation' && this.playMotion) {
        this.playMotion(step.motion, step.phase || null);
        this.lastApplied = { op: 'animation', motion: step.motion, phase: step.phase || null, t: step.t };
      }
      if (initial && step.op === 'wait') break;
    }
  }

  update(dt) {
    if (!this.ready) return;
    if (this.idle) {
      if (this.auto) this.reset();         // 独处循环:idle 即选取下一个场景
      if (this.idle) return;               // auto 关着,或没选出场景:保持静止
    }
    this.clock += dt;
    this.elapsed += dt;
    this._applyDue(false);
    // showSeconds 到点时 view 自己收场,编排侧的「正在显示」也跟着落(否则 status 与收场逻辑失真)
    const em = this.emoticon;
    if (em && em.showSeconds && this.clock >= em.t + em.showSeconds) this.emoticon = null;
    if (this.clock >= this.total && this.next >= this.queue.length) {
      // 一段播完:统一停到 idle。手动点播到此为止;自动模式由下一帧的 idle 分支接续。
      this._dropEmoticon();
      this.queue = [];
      this.manual = false;
      this.idle = true;
    }
  }

  status() {
    const sc = this.current;
    return {
      scenario: sc ? sc.id : null,
      kind: sc ? sc.kind : null,
      clock: +this.clock.toFixed(2),
      total: +(this.total || 0).toFixed(2),
      applied: this.lastApplied,
      emoticon: this.emoticon ? this.emoticon.name : null,
      scenarios: this.scenarios().length,
      mode: this.mode,
      auto: this.auto,
      idle: this.idle,
      manual: this.manual,
    };
  }
}

export function loadPerformance(url) {
  return fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null);
}
