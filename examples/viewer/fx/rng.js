// fx/rng.js — 粒子系统的逐粒子随机流
//
// 在这个文件之前,每一处要随机数的地方都直接叫 `Math.random()`。那不是这套粒子系统
// 做的事,而差别不是「看起来一样就行」的那种:引擎的随机是**一条可复现的流**,同一个
// 种子必然给出同一串取值。拿平台随机数顶替,同一个系统每次加载都长得不一样,而原版
// 每次都一模一样 —— 而且**取多少次**也不一样,于是连「同一颗粒子的角度和半径」这种
// 相邻两次取值的配对关系都对不上。
//
// ---- 发生器 ------------------------------------------------------------------
//
// 是 xorshift128,返回完整的 32 位无符号整数:
//
//   t = x ^ (x << 11);  x = y;  y = z;  z = w;
//   w = (w ^ (w >> 19)) ^ (t ^ (t >> 8));   返回 w
//
// 归一化到 [0,1] 的取法是**取低 23 位除以 0x7FFFFF**:
//
//   toUnit(u) = (u & 0x007FFFFF) / 8388607
//
// 除数是 8388607 不是 8388608,所以 **1.0 是取得到的**(闭区间),与常见的 [0,1) 实现
// 有边界差异 —— 照实记。
//
// ---- 播种 --------------------------------------------------------------------
//
// 常量 1812433253 是**种子扩散乘数**,不是取值迭代式:一个 32 位种子经三轮
// `y = x * M + 1` 摊成四个状态字。发生器本身一次算**四道**,四道的种子偏移是
// {0, 367, 734, 1101} —— 一次取值同时产出四个数,正好喂四颗粒子。
//
// ---- 四道车道映射(最容易做错的一条) ------------------------------------------
//
// 一批粒子的生成起点对齐到 4,批内第 j 颗用车道 `j & 3`,**流每 4 颗才推进一次**。
// 所以「一帧发 1 颗、连发 4 帧」与「一帧发 4 颗」消耗的随机数**不同**:前者推进 4 次
// 而每次只用车道 0,后者推进 1 次用满四道。复刻时必须按真实的每帧发射数分批,
// 把总数一次性铺平会让整条流错位。
//
// ---- 抽了可能丢弃 -------------------------------------------------------------
//
// 出生那一段的若干次取值在**分支之外**:曲线不是「两值之间随机」也照抽,取出来的数
// 被求值函数忽略,但流已经推进了。少抽一次,之后每一颗粒子的每一个量全部错位。
// 所以下面的取值表是按**位置**定的,不是按「这一项用不用得上」定的。

const M = 0x6C078965;                 // 种子扩散乘数
const LANE_OFFSET = [0, 367, 734, 1101];
const UNIT = 1 / 8388607;

const u32 = (v) => v >>> 0;
const mul = (a, b) => Math.imul(a, b) >>> 0;

/** 低 23 位除以 0x7FFFFF。**闭区间 [0,1]**,1.0 取得到。 */
export function toUnit(w) {
  return (w & 0x007FFFFF) * UNIT;
}

/** 一个 32 位种子经三轮 `y = x*M + 1` 摊成四个状态字。 */
function spread(seed) {
  const x = u32(seed);
  const y = u32(mul(x, M) + 1);
  const z = u32(mul(y, M) + 1);
  const w = u32(mul(z, M) + 1);
  return [x, y, z, w];
}

/** 单道 xorshift128。全局自动种子源与几处标量用途走它。 */
export class Rand {
  constructor(seed = 0) { this.setSeed(seed); }

  setSeed(seed) {
    const s = spread(seed);
    this.x = s[0]; this.y = s[1]; this.z = s[2]; this.w = s[3];
    this.steps = 0;
    return this;
  }

  /** 下一个完整的 32 位输出。 */
  step() {
    const t = u32(this.x ^ (this.x << 11));
    this.x = this.y; this.y = this.z; this.z = this.w;
    this.w = u32((this.w ^ (this.w >>> 19)) ^ (t ^ (t >>> 8)));
    this.steps += 1;
    return this.w;
  }

  /** 下一个 [0,1] 闭区间取值。 */
  unit() { return toUnit(this.step()); }
}

/** 四道并行的 xorshift128。一次取值产出四个数,给同一组的四颗粒子。 */
export class Rand4 {
  constructor(seed = 0) {
    this.x = new Uint32Array(4); this.y = new Uint32Array(4);
    this.z = new Uint32Array(4); this.w = new Uint32Array(4);
    this.out = new Uint32Array(4);
    this.setSeed(seed);
  }

  setSeed(seed) {
    for (let i = 0; i < 4; i++) {
      const s = spread(u32(u32(seed) + LANE_OFFSET[i]));
      this.x[i] = s[0]; this.y[i] = s[1]; this.z[i] = s[2]; this.w[i] = s[3];
    }
    this.steps = 0;
    return this;
  }

  /**
   * 推进一次,返回四道的输出。返回的是**内部缓冲**,调用方要留住就自己拷 ——
   * 每颗粒子一次取值在这条路径上每帧要跑上千次,不能每次新建数组。
   */
  step() {
    const { x, y, z, w, out } = this;
    for (let i = 0; i < 4; i++) {
      const t = u32(x[i] ^ (x[i] << 11));
      x[i] = y[i]; y[i] = z[i]; z[i] = w[i];
      w[i] = u32((w[i] ^ (w[i] >>> 19)) ^ (t ^ (t >>> 8)));
      out[i] = w[i];
    }
    this.steps += 1;
    return out;
  }
}

/**
 * 一条按「四颗一组」消费的模块流。
 *
 * `names` 是这个模块每组要按顺序取的那几次值 —— 顺序就是真源里取值点的先后,
 * **不是**「这一项用不用得上」。一批的第 0 颗推进整组取值并缓存,第 1..3 颗直接读
 * 缓存的另外三道;第 4 颗又是新的一组。
 *
 * `beginBatch()` 把车道游标归零。一批 = 真源里的一次生成调用,生成起点对齐到 4,
 * 所以每一批的第一颗恒落在车道 0。
 */
export class LaneStream {
  constructor(seed, names) {
    this.rand = new Rand4(seed);
    this.names = names.slice();
    this.group = this.names.map(() => new Uint32Array(4));
    this.lane = 0;
    this.groups = 0;
    this.taken = 0;
    this.batches = 0;
  }

  setSeed(seed) {
    this.rand.setSeed(seed);
    this.lane = 0;
    return this;
  }

  /** 新的一批:车道游标归零(下一颗必然推进新的一组)。 */
  beginBatch() { this.lane = 0; this.batches += 1; }

  /** 这条流一共推进了多少次(判据读它对账「每 4 颗推进一次」)。 */
  get steps() { return this.rand.steps; }

  /**
   * 取下一颗粒子的一整组值。返回 `{lane, u: {...}, w: {...}}`:
   * `u` 是归一化到 [0,1] 的取值,`w` 是原始的 32 位输出(粒子种子要的是原值,不做变换)。
   */
  next() {
    if (this.lane === 0) {
      for (let i = 0; i < this.names.length; i++) this.group[i].set(this.rand.step());
      this.groups += 1;
    }
    const lane = this.lane;
    this.lane = (this.lane + 1) & 3;
    this.taken += 1;
    const u = {}; const w = {};
    for (let i = 0; i < this.names.length; i++) {
      const raw = this.group[i][lane];
      w[this.names[i]] = raw;
      u[this.names[i]] = toUnit(raw);
    }
    return { lane, u, w };
  }
}

// 自动种子的全局源。
//
// 真源里它是**进程级**的一个标量发生器,初值恰好等于 `SetSeed(0)`,每有一个
// `autoRandomSeed` 为真的系统重新播种就从它取一个数。**所以它不是时间熵,是一条
// 确定性序列** —— 同一个进程里启动次序相同,拿到的自动种子就完全相同;而反复重播
// 会一直往下走,所以自动种子的系统每次重播确实换一个种子,这与原版一致,不是缺陷。
const AUTO = new Rand(0);

/** 取下一个自动种子(等价于真源里全局管理器上的那一次取值)。 */
export function nextAutoSeed() { return AUTO.step(); }

/** 把全局自动种子源退回初值。只给判据用:让「同一个进程内第 N 次」可复现。 */
export function resetAutoSeed() { AUTO.setSeed(0); }

/** 全局自动种子源到目前为止取了多少次(判据读它对账)。 */
export function autoSeedDraws() { return AUTO.steps; }
