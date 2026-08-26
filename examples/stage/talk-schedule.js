// 家具旁对话 → 播放时间表。纯函数，没有渲染状态，selfcheck 与 stage 共用同一份。
//
// 为什么需要它：家具演出的角色动作**不在 timeline 上**。87 份 fixture-timeline 文档里
// 只有 5 份引用 `mysekai__character_motion`，其余 82 份动的是家具本体；而下拉框里
// 682 条是家具演出。这与运行时一致——timeline 不含角色分派，NPC 由别处驱动。角色要做
// 什么写在对话脚本里（`change_animation`），所以时间表要从对话的调用流推。
//
// 调用流没有绝对时刻，只有等待：
//   * `wait_time(秒)` / `wait_time_on_auto_mode(秒, ...)` —— 秒数在数据里，照用；
//   * `wait_click()` —— 等玩家点击，**数据里没有时长**。
//
// `wait_click` 的时长是本 demo 自己给的替身，不是游戏值。所以它不藏在总时长里：
// 时间表把「数据给的秒数」与「替身补的秒数」分开计数，界面按这两个数字说话。
// 判据也数它：替身秒数占满整条时间表时，说明这条脚本的节奏一秒也不是数据给的。

/** `wait_click` 的替身时长（秒）。数据里没有这个值，界面必须标明它是替身。 */
export const WAIT_CLICK_STAND_IN = 2.0;

/** 推进时钟的算子；其余算子都是瞬时的。 */
const WAIT_OPS = new Set(['wait_time', 'wait_time_on_auto_mode']);
const CLICK_OPS = new Set(['wait_click']);

/** 落进时间表的算子。其余照原样计数为「未编排」，不丢也不假装编排了。 */
const SCHEDULED_OPS = new Set([
  'change_animation', 'play_animation', 'text', 'emoticon', 'hide_emoticon',
  'change_npc_eye', 'change_npc_mouth', 'voice',
  'show_talk_window', 'hide_talk_window',
  'change_fixture_timeline', 'change_fixture_character_eye',
  'change_fixture_character_mouth', 'fixture_voice', 'look_at_fixture',
  'look_at_to_npc', 'look_at_body', 'show_fixture_emoticon',
  'play_fixture_gimmick', 'stop_fixture_gimmick', 'label',
]);

function seconds(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

/**
 * 把一条对话的 steps 排成时间表。
 *
 * 返回 `{ events, duration, dataSeconds, standInSeconds, clickWaits, dataWaits,
 * unscheduled }`：
 *   * `events` —— `{ at, index, op, ... }`，按源序，`at` 是累计时钟；
 *   * `dataSeconds` / `standInSeconds` —— 时长里数据给的与替身补的，分开算；
 *   * `unscheduled` —— 词表内但本时间表没编排的算子计数（按名字，不进 other 桶）。
 */
export function buildSchedule(steps, { waitClickSeconds = WAIT_CLICK_STAND_IN } = {}) {
  const events = [];
  const unscheduled = new Map();
  let clock = 0;
  let dataSeconds = 0;
  let standInSeconds = 0;
  let clickWaits = 0;
  let dataWaits = 0;

  (steps || []).forEach((step, index) => {
    const op = step?.op;
    if (WAIT_OPS.has(op)) {
      const span = seconds(step.seconds);
      clock += span;
      dataSeconds += span;
      dataWaits += 1;
      return;
    }
    if (CLICK_OPS.has(op)) {
      const span = seconds(waitClickSeconds);
      clock += span;
      standInSeconds += span;
      clickWaits += 1;
      return;
    }
    if (!SCHEDULED_OPS.has(op)) {
      unscheduled.set(op, (unscheduled.get(op) || 0) + 1);
      return;
    }
    events.push({ ...step, at: clock, index });
  });

  return {
    events,
    duration: clock,
    dataSeconds,
    standInSeconds,
    clickWaits,
    dataWaits,
    unscheduled: Object.fromEntries([...unscheduled].sort()),
  };
}

/**
 * 时间表要的角色动作名。`change_animation` 的 `motion` 在提取侧已经把常量解开成
 * 动作库里的名字（`mov_*`）；仍是 `表.键` 形状的说明那条常量提取侧也没解开，
 * 单列出来，不混进「库里没有」。
 */
export function motionsWanted(schedule) {
  const names = new Set();
  const unresolvedTokens = new Set();
  for (const event of schedule?.events || []) {
    if (event.op !== 'change_animation' && event.op !== 'play_animation') continue;
    const motion = event.motion;
    if (typeof motion !== 'string' || !motion) continue;
    if (/^[A-Za-z_]\w*\.[A-Za-z_]\w*$/.test(motion)) unresolvedTokens.add(motion);
    else names.add(motion);
  }
  return { names, unresolvedTokens };
}

/** 时钟 `time` 处生效的最后一条 `change_animation`（换动作是覆盖式的，不是叠加）。 */
export function animationAt(schedule, time) {
  let current = null;
  for (const event of schedule?.events || []) {
    if (event.at > time) break;
    if (event.op === 'change_animation' || event.op === 'play_animation') current = event;
  }
  return current;
}

/** 时钟 `time` 处该显示的对话行（下一行出现之前一直显示）。 */
export function textAt(schedule, time) {
  let current = null;
  for (const event of schedule?.events || []) {
    if (event.at > time) break;
    if (event.op === 'text') current = event;
  }
  return current;
}

/**
 * 时钟 `time` 处生效的口型 / 眼型图案名。
 *
 * 数据里 `change_npc_mouth` 带 `pattern: "smile01"` 与 `alias:
 * "LipSyncPresets.smile01"` —— **图案名是有的**。这与演出时间轴上那条路不同：
 * 那边的 `ChangeLipSyncPreset` clip 只有 `m_Asset` 指针、产物里没有名字，所以逐 clip
 * 的表情至今不接。**对话这条路有名字，所以能接。** 两条路不要混着说。
 */
export function facialAt(schedule, time) {
  let mouth = null;
  let eye = null;
  for (const event of schedule?.events || []) {
    if (event.at > time) break;
    if (event.op === 'change_npc_mouth'
        || event.op === 'change_fixture_character_mouth') mouth = event;
    if (event.op === 'change_npc_eye'
        || event.op === 'change_fixture_character_eye') eye = event;
  }
  return { mouth, eye };
}

/**
 * 时钟 `time` 处有没有一句台词正在说 —— 口型动不动就看这个。
 *
 * `voice` 算子带 `cue` 与 `who`，**不带时长**，而语音 cue 也不在产物里
 * （音频产物是 12 个 bgm 包 + 1 个 se 包，没有 voice 包）。所以说话窗口的**结束**
 * 只能从时间表的结构推：一句台词说到该段的 `wait_click`（玩家点掉这一行）为止，
 * 没有 `wait_click` 就说到下一个 `voice` 或表尾。
 *
 * **这是结构推导，不是编出来的时长**，而且 `wait_click` 自己的秒数是替身
 * （`WAIT_CLICK_STAND_IN`），所以窗口长度里含替身成分 —— 界面必须照实说，
 * 不能让「口型动了」看着像时长也对了。
 */
export function speakingAt(schedule, time) {
  const events = schedule?.events || [];
  let active = null;
  for (let i = 0; i < events.length; i += 1) {
    const event = events[i];
    if (event.op !== 'voice' && event.op !== 'fixture_voice') continue;
    if (event.at > time) break;
    let end = schedule.duration;
    for (let j = i + 1; j < events.length; j += 1) {
      const later = events[j];
      if (later.op === 'wait_click' || later.op === 'voice'
          || later.op === 'fixture_voice') { end = later.at; break; }
    }
    active = time < end ? { event, from: event.at, to: end } : null;
  }
  return active;
}

/**
 * 时间表里每个算子有没有消费方 —— **这一张表是为了不让 `unscheduled` 变成假绿。**
 *
 * `unscheduled` 数的是「不在 `SCHEDULED_OPS` 词表里的算子」，而进了词表只意味着
 * 它带时间戳落进了 `events`，**不意味着有人读它**。于是 talk #530 报
 * `unscheduled: {}`，读起来像「全都编排了」，实际上九族算子里只有两族有消费方 ——
 * 名字承诺过头，正是判据一。
 *
 * 消费方名单**逐条写死**，不用通配：写死了才能在加了新读者时必须来改这里，
 * 也才能让「有几族没人读」是个数而不是沉默。
 */
export const CONSUMED_OPS = new Set([
  'change_animation', 'play_animation',   // animationAt
  'text',                                 // textAt
  'change_npc_mouth', 'change_fixture_character_mouth',   // facialAt
  'change_npc_eye', 'change_fixture_character_eye',       // facialAt
  'voice', 'fixture_voice',               // speakingAt
  'wait_time', 'wait_time_on_auto_mode', 'wait_click',    // 推进时钟
]);

export function consumerCoverage(schedule) {
  const consumed = new Map();
  const scheduledNoReader = new Map();
  for (const event of schedule?.events || []) {
    const bucket = CONSUMED_OPS.has(event.op) ? consumed : scheduledNoReader;
    bucket.set(event.op, (bucket.get(event.op) || 0) + 1);
  }
  const total = (schedule?.events || []).length;
  const read = [...consumed.values()].reduce((sum, n) => sum + n, 0);
  return {
    events: total,
    consumed: read,
    scheduledNoReader: total - read,
    byOpConsumed: Object.fromEntries([...consumed].sort()),
    byOpNoReader: Object.fromEntries([...scheduledNoReader].sort()),
    unscheduled: schedule?.unscheduled || {},
  };
}
