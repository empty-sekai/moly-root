// cloth.js — contract-driven cloth solver
//
// Inputs follow the public rig contract: Verlet integration, distance constraints,
// collision primitives, and bounded finite-value recovery.
// Capsule length is a half-length; startRadius is at the -direction endpoint.
// Particle radius comes from the depth-evaluated cloth parameter.
// Gravity is evaluated from the contract; when disabled, motion comes from world
// movement/rotation influence, restore rotation, and angular limits.
// The default simulation rate is 90 Hz.
// Per-frame order: update animation, restore cloth bones to rest, update world
// matrices, read animated positions, step the solver, then write bone transforms back.

import * as THREE from './three.module.min.js';

export const SELECTION = { INVALID: 0, MOVE: 1, FIXED: 2, EXTEND: 3 };
export const SIM_HZ = 90;
export const SIM_DT = 1 / SIM_HZ;

// ---------------------------------------------------------------- BezierParam

export function evalBezier(p, depth) {
  if (p == null) return 0;
  if (typeof p === 'number') return p;
  const start = +(p.startValue ?? 0);
  if (!(p.useEndValue | 0)) return start;
  const end = +(p.endValue ?? start);
  const t = Math.min(1, Math.max(0, +depth || 0));
  const cv = +(p.curveValue ?? 0);
  if ((p.useCurveValue | 0) && cv !== 0) {
    const c = end + (start - end) * (cv * 0.5 + 0.5);
    const s = 1 - t;
    return s * s * start + 2 * s * t * c + t * t * end;
  }
  return start + (end - start) * t;
}

export function pFlag(params, name) { return !!((params && params[name]) | 0); }
export function pScalar(params, name, dflt = 0) {
  const v = params ? params[name] : undefined;
  return (typeof v === 'number' && isFinite(v)) ? v : dflt;
}

// ---------------------------------------------------------------- 碰撞体

const AXIS_DIR = [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1)];
const _v0 = new THREE.Vector3(), _v1 = new THREE.Vector3(), _v2 = new THREE.Vector3();
const _v3 = new THREE.Vector3(), _v4 = new THREE.Vector3();
const _q0 = new THREE.Quaternion(), _q1 = new THREE.Quaternion(), _q2 = new THREE.Quaternion();
const Q_IDENT = new THREE.Quaternion();

// col:{kind:'sphere'|'capsule'|'plane', center:[3], radius | axis,length,startRadius,endRadius | normalDirection}
// m4: 骨骼世界矩阵。写入/返回 prim:{kind, c|p0,p1|o,n, R|r0,r1}
export function colliderToWorld(col, m4, prim) {
  prim = prim || {};
  prim.kind = col.kind;
  const sc = _v4.setFromMatrixScale(m4);
  const s = (Math.abs(sc.x) + Math.abs(sc.y) + Math.abs(sc.z)) / 3;
  const c = col.center || [0, 0, 0];
  if (col.kind === 'sphere') {
    prim.c = (prim.c || new THREE.Vector3()).set(c[0], c[1], c[2]).applyMatrix4(m4);
    prim.R = col.radius * s;
    return prim;
  }
  if (col.kind === 'plane') {
    // 无限平面过 boneWorld×center,法线 = 旋转后的局部法线(planeSemantics:transform up 轴)
    prim.o = (prim.o || new THREE.Vector3()).set(c[0], c[1], c[2]).applyMatrix4(m4);
    const nd = col.normalDirection || [0, 1, 0];
    m4.decompose(_v0, _q0, _v1);
    prim.n = (prim.n || new THREE.Vector3()).set(nd[0], nd[1], nd[2]).applyQuaternion(_q0).normalize();
    return prim;
  }
  // Capsule length is a half-length; endpoints use center ± direction * length.
  // startRadius belongs to the negative-direction endpoint.
  const d = AXIS_DIR[col.axis | 0];
  const L = col.length;
  prim.p0 = (prim.p0 || new THREE.Vector3())
    .set(c[0] - d.x * L, c[1] - d.y * L, c[2] - d.z * L).applyMatrix4(m4);
  prim.p1 = (prim.p1 || new THREE.Vector3())
    .set(c[0] + d.x * L, c[1] + d.y * L, c[2] + d.z * L).applyMatrix4(m4);
  prim.r0 = col.startRadius * s;
  prim.r1 = col.endRadius * s;
  return prim;
}

// 有符号净空;p:Vector3, r:粒子半径。负 = 穿插。
export function clearance(p, r, prim) {
  if (prim.kind === 'plane') return _v0.subVectors(p, prim.o).dot(prim.n) - r;
  let cp, R;
  if (prim.kind === 'sphere') { cp = prim.c; R = prim.R; }
  else {
    _v0.subVectors(prim.p1, prim.p0);
    const seg2 = _v0.lengthSq();
    const t = seg2 <= 1e-18 ? 0 : Math.min(1, Math.max(0, _v1.subVectors(p, prim.p0).dot(_v0) / seg2));
    cp = _v2.copy(prim.p0).addScaledVector(_v0, t);
    R = prim.r0 + (prim.r1 - prim.r0) * t;
  }
  return p.distanceTo(cp) - (R + r);
}

// 穿插时沿径向法线推出(写回 p),返回是否推动。退化 d≈0 沿 +Y。
export function pushOut(p, r, prim) {
  if (prim.kind === 'plane') {
    const d = _v0.subVectors(p, prim.o).dot(prim.n);
    if (d >= r) return false;
    p.addScaledVector(prim.n, r - d);
    return true;
  }
  let cp, R;
  if (prim.kind === 'sphere') { cp = prim.c; R = prim.R; }
  else {
    _v0.subVectors(prim.p1, prim.p0);
    const seg2 = _v0.lengthSq();
    const t = seg2 <= 1e-18 ? 0 : Math.min(1, Math.max(0, _v1.subVectors(p, prim.p0).dot(_v0) / seg2));
    cp = _v2.copy(prim.p0).addScaledVector(_v0, t);
    R = prim.r0 + (prim.r1 - prim.r0) * t;
  }
  const d = p.distanceTo(cp);
  const total = R + r;
  if (d >= total) return false;
  if (d <= 1e-9) { p.copy(cp); p.y += total; return true; }
  p.sub(cp).multiplyScalar(total / d).add(cp);
  return true;
}

// ---------------------------------------------------------------- rig 归一化

function pick(obj, keys) {
  if (!obj) return undefined;
  for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  return undefined;
}
const asArr = (v) => (Array.isArray(v) ? v : undefined);

function normCollider(c) {
  const kind = c.kind || (c.normalDirection ? 'plane' : (c.radius !== undefined && c.length === undefined ? 'sphere' : 'capsule'));
  const cc = c.center;
  const out = {
    kind,
    bone: c.bone || c.boneName || c.transform || '',
    center: asArr(cc) || [cc && cc.x || 0, cc && cc.y || 0, cc && cc.z || 0],
    id: c.pathId !== undefined ? c.pathId : c.id,
  };
  if (kind === 'sphere') out.radius = +pick(c, ['radius', 'r']) || 0;
  else if (kind === 'plane') out.normalDirection = asArr(c.normalDirection) || [0, 1, 0];
  else {
    out.axis = +pick(c, ['axis']) | 0;
    out.length = +pick(c, ['length', 'halfLength']) || 0;
    out.startRadius = +pick(c, ['startRadius', 'r0']) || 0;
    out.endRadius = +pick(c, ['endRadius', 'r1']) || 0;
  }
  if (typeof c.node === 'number') out.node = c.node; // glTF node index(重名骨免疫)
  return out;
}

function normEdgeList(list) {
  if (!Array.isArray(list) || !list.length) return null;
  const seen = new Set(); const out = [];
  for (const e of list) {
    const a = e.vertexIndex !== undefined ? e.vertexIndex : (e.a !== undefined ? e.a : e[0]);
    const b = e.targetVertexIndex !== undefined ? e.targetVertexIndex : (e.b !== undefined ? e.b : e[1]);
    const len = e.length !== undefined ? e.length : (e.len !== undefined ? e.len : e[2]);
    if (a === undefined || b === undefined || len === undefined) continue;
    const k = a < b ? a + '|' + b : b + '|' + a;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({ a: a | 0, b: b | 0, len: +len });
  }
  return out.length ? out : null;
}

// Accept the documented rig shapes and normalize them to chains/colliders.
export function normalizeRig(rig) {
  if (!rig) return null;
  const cloth = rig.cloth || rig;
  const chainsRaw = asArr(cloth) || asArr(cloth.chains) || asArr(cloth.components)
    || asArr(rig.chains) || asArr(rig.components) || [];
  const collidersRaw = asArr(cloth.colliders) || asArr(rig.colliders) || [];
  const colliders = collidersRaw.map(normCollider);
  const byId = new Map(), byBone = new Map();
  colliders.forEach((c) => {
    if (c.id !== undefined) byId.set(c.id, c);
    if (!byBone.has(c.bone)) byBone.set(c.bone, []);
    byBone.get(c.bone).push(c);
  });

  const chains = [];
  for (const c of chainsRaw) {
    const vtx = c.vertices || c;
    const bones = pick(vtx, ['bones', 'boneNames']) || pick(c, ['bones', 'boneNames']);
    if (!bones || !bones.length) continue;
    const n = bones.length;
    const parent = pick(vtx, ['parent', 'parentList']) || [];
    const root = pick(vtx, ['root', 'rootList']) || [];
    const selection = pick(vtx, ['selection', 'selectionData']) || [];
    const depth = pick(vtx, ['depth', 'vertexDepthList']) || [];
    const params = pick(c, ['params', 'clothParams']) || {};
    const cons = c.constraints || {};
    // 每链碰撞体:优先内联对象,其次 pathId/骨名引用,缺省 = 全部
    let chainCols = null;
    const inline = asArr(c.colliders);
    if (inline && inline.length && typeof inline[0] === 'object') chainCols = inline.map(normCollider);
    else {
      const idRefs = pick(c, ['colliderPathIds']) || pick(c.team || {}, ['colliderPathIds']);
      const refs = asArr(c.colliders) || pick(c, ['colliderBones', 'colliderRefs'])
        || pick(c.team || {}, ['colliderBones']);
      if (idRefs && byId.size) chainCols = idRefs.map((i) => byId.get(i)).filter(Boolean);
      else if (refs) chainCols = refs.flatMap((r) => byBone.get(r) || []);
    }
    const P = new Int32Array(n), R = new Int32Array(n), S = new Int32Array(n);
    const D = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      P[i] = parent[i] !== undefined ? parent[i] : -1;
      S[i] = selection[i] !== undefined ? selection[i] : (P[i] < 0 ? SELECTION.FIXED : SELECTION.MOVE);
      D[i] = depth[i] !== undefined ? depth[i] : 0;
    }
    for (let i = 0; i < n; i++) {
      if (root[i] !== undefined) { R[i] = root[i]; continue; }
      let v = i, guard = 0;
      while (P[v] >= 0 && guard++ < n) v = P[v];
      R[i] = v === i ? -1 : v;
    }
    chains.push({
      name: c.name || c.component || ('chain' + chains.length),
      bones, parent: P, root: R, selection: S, depth: D, params,
      nodeIndices: (Array.isArray(vtx.nodeIndices) && vtx.nodeIndices.length === n) ? vtx.nodeIndices : null,
      colliders: chainCols && chainCols.length ? chainCols : colliders,
      structEdges: normEdgeList(pick(cons, ['structDistanceDataList', 'struct', 'structDistance']) || c.structDistance),
      rootDist: normEdgeList(pick(cons, ['rootDistanceDataList', 'rootDistance']) || c.rootDistance),
    });
  }
  return {
    unitId: rig.unitId, defaultEye: rig.defaultEye, defaultMouth: rig.defaultMouth,
    eyeAtlas: rig.eyeAtlas, mouthAtlas: rig.mouthAtlas, materials: rig.materials,
    chains, colliders,
  };
}

// ---------------------------------------------------------------- 单链求解器

export class ChainSolver {
  // chain: normalizeRig 输出的一条;restPositions: Float64Array(3n) 绑定姿态世界位置
  constructor(chain, restPositions) {
    const n = this.n = chain.bones.length;
    this.chain = chain;
    this.fixed = new Uint8Array(n);
    for (let i = 0; i < n; i++) this.fixed[i] = chain.selection[i] === SELECTION.FIXED ? 1 : 0;

    // 拓扑序(父先于子)与子表
    this.order = [];
    {
      const placed = new Uint8Array(n); let guard = 0;
      while (this.order.length < n && guard++ <= n) {
        for (let i = 0; i < n; i++) {
          if (placed[i]) continue;
          const p = chain.parent[i];
          if (p < 0 || placed[p]) { placed[i] = 1; this.order.push(i); }
        }
      }
    }
    this.children = [];
    for (let i = 0; i < n; i++) this.children.push([]);
    for (let i = 0; i < n; i++) if (chain.parent[i] >= 0) this.children[chain.parent[i]].push(i);

    const P = chain.params, D = chain.depth;
    const curve = (name) => {
      const a = new Float64Array(n);
      for (let i = 0; i < n; i++) a[i] = evalBezier(P[name], D[i]);
      return a;
    };
    this.radius = P.radius ? curve('radius') : new Float64Array(n);
    this.drag = pFlag(P, 'useDrag') ? curve('drag') : new Float64Array(n);
    this.maxVel = pFlag(P, 'useMaxVelocity') ? curve('maxVelocity') : new Float64Array(n).fill(Infinity);
    if (pFlag(P, 'useClampRotation')) {
      this.clampAng = curve('clampRotationAngle');
      for (let i = 0; i < n; i++) this.clampAng[i] *= Math.PI / 180;
    } else this.clampAng = new Float64Array(n).fill(Math.PI);
    this.restorePow = pFlag(P, 'useRestoreRotation') ? curve('restoreRotation') : new Float64Array(n);
    this.useCollision = pFlag(P, 'useCollision');
    this.moveInfl = P.worldMoveInfluence ? curve('worldMoveInfluence') : new Float64Array(n).fill(1);
    this.rotInfl = P.worldRotationInfluence ? curve('worldRotationInfluence') : new Float64Array(n).fill(1);
    {
      const g = P.gravityDirection || { x: 0, y: -1, z: 0 };
      const mag = pFlag(P, 'useGravity') ? curve('gravity') : new Float64Array(n);
      this.grav = new Float64Array(3 * n);
      for (let i = 0; i < n; i++) {
        this.grav[3 * i] = (g.x || 0) * mag[i];
        this.grav[3 * i + 1] = (g.y || 0) * mag[i];
        this.grav[3 * i + 2] = (g.z || 0) * mag[i];
      }
    }
    this.useClampDist = pFlag(P, 'useClampDistanceRatio');
    this.minRatio = pScalar(P, 'clampDistanceMinRatio', 1);
    this.maxRatio = pScalar(P, 'clampDistanceMaxRatio', 1);
    this.maxMoveSpeed = pScalar(P, 'maxMoveSpeed', Infinity) || Infinity;
    this.maxRotSpeed = (pScalar(P, 'maxRotationSpeed', Infinity) || Infinity) * Math.PI / 180;
    this.useTeleportReset = pFlag(P, 'useResetTeleport');
    this.teleportDist = pScalar(P, 'teleportDistance', 0.2);
    this.teleportRot = pScalar(P, 'teleportRotation', 45) * Math.PI / 180;

    // struct 距离边:有表用表(裙含横向环边),否则由 parent 边 + 绑定长度重建
    const rest = restPositions;
    const restLen = (a, b) => Math.hypot(
      rest[3 * a] - rest[3 * b], rest[3 * a + 1] - rest[3 * b + 1], rest[3 * a + 2] - rest[3 * b + 2]);
    if (chain.structEdges) this.edges = chain.structEdges.filter((e) => e.a < n && e.b < n);
    else {
      this.edges = [];
      for (let i = 0; i < n; i++)
        if (chain.parent[i] >= 0) this.edges.push({ a: chain.parent[i], b: i, len: restLen(chain.parent[i], i) });
    }
    // Rest lengths in the constraint table should match the scene's bound rest-bone distances.
    // A mismatch is reported by self-check; the solver continues using the constraint table.
    this.restMismatch = 0;
    for (const e of this.edges) {
      const d = Math.abs(e.len - restLen(e.a, e.b));
      if (d > this.restMismatch) this.restMismatch = d;
    }
    this.edgeStiff = this.edges.map((e) =>
      (P.structDistanceStiffness ? evalBezier(P.structDistanceStiffness, (D[e.a] + D[e.b]) / 2) : 1));
    // 根距约束(clampDistanceMin/MaxRatio 的作用面)
    this.rootPairs = [];
    if (this.useClampDist) {
      if (chain.rootDist) {
        for (const e of chain.rootDist) if (e.a < n && e.b < n) this.rootPairs.push({ v: e.a, r: e.b, len: e.len });
      } else {
        for (let i = 0; i < n; i++) {
          const r = chain.root[i];
          if (r >= 0 && !this.fixed[i]) this.rootPairs.push({ v: i, r, len: restLen(i, r) });
        }
      }
    }

    let anchor = 0;
    for (const i of this.order) if (chain.parent[i] < 0) { anchor = i; break; }
    this.anchorIdx = anchor;
    this.pos = new Float64Array(3 * n);
    this.prev = new Float64Array(3 * n);
    this.inited = false;
    this.anchorPrevP = new THREE.Vector3();
    this.anchorPrevQ = new THREE.Quaternion();
    this.iterations = 4;
    this.nanResets = 0;
    this.teleportResets = 0;
    this.lastMaxDisp = 0;
  }

  reset(anim, anchorQ) {
    this.pos.set(anim); this.prev.set(anim); this.inited = true;
    this.anchorPrevP.fromArray(anim, 3 * this.anchorIdx);
    this.anchorPrevQ.copy(anchorQ || Q_IDENT);
  }

  // anim: Float64Array(3n) 本帧动画位置;anchorQ: 链根骨世界旋转;prims: 碰撞 prim;nSub: 子步数
  step(anim, anchorQ, dtFrame, prims, nSub) {
    const n = this.n;
    if (!this.inited) { this.reset(anim, anchorQ); return; }
    const pos = this.pos, prev = this.prev;

    // ---- 世界运动惯性(worldMove/RotationInfluence;maxMoveSpeed/maxRotationSpeed 截流) ----
    const a1 = _v3.fromArray(anim, 3 * this.anchorIdx);
    const a0 = this.anchorPrevP;
    const dq = _q0.copy(anchorQ).multiply(_q1.copy(this.anchorPrevQ).invert());
    const trans = _v0.subVectors(a1, a0);
    const tLen = trans.length();
    const rotAngle = 2 * Math.acos(Math.min(1, Math.abs(dq.w)));
    // teleport 复位:useResetTeleport 链用数据阈值(0.2m/45°);其余仅超大跳变兜底
    const distThresh = this.useTeleportReset ? this.teleportDist : 1.0;
    if (dtFrame > 0 && (tLen > distThresh || (this.useTeleportReset && rotAngle > this.teleportRot))) {
      this.teleportResets++;
      this.reset(anim, anchorQ);
      return;
    }
    const capT = this.maxMoveSpeed * dtFrame;
    const feltT = _v1.copy(trans);
    if (tLen > capT && tLen > 0) feltT.multiplyScalar(capT / tLen);
    const capR = this.maxRotSpeed * dtFrame;
    const feltQ = _q1.copy(dq);
    if (rotAngle > capR && rotAngle > 1e-9) feltQ.copy(Q_IDENT).slerp(dq, capR / rotAngle);
    for (let i = 0; i < n; i++) {
      if (this.fixed[i]) continue;
      const mi = Math.min(1, Math.max(0, this.moveInfl[i]));
      const ri = Math.min(1, Math.max(0, this.rotInfl[i]));
      // 位移拆解:fullRigid = ΔR·(p−a0)−(p−a0)+trans;felt 惯性 = feltT·mi + feltRotDisp·ri
      // pos/prev 同加 shift = fullRigid − felt ⇒ 粒子仅「感到」felt 部分
      _v2.set(pos[3 * i] - a0.x, pos[3 * i + 1] - a0.y, pos[3 * i + 2] - a0.z);
      _v4.copy(_v2).applyQuaternion(dq).sub(_v2).add(trans); // fullRigid
      const frx = _v2.x, fry = _v2.y, frz = _v2.z;
      _v2.applyQuaternion(feltQ);
      const feltRx = _v2.x - frx, feltRy = _v2.y - fry, feltRz = _v2.z - frz;
      const sx = _v4.x - feltT.x * mi - feltRx * ri;
      const sy = _v4.y - feltT.y * mi - feltRy * ri;
      const sz = _v4.z - feltT.z * mi - feltRz * ri;
      pos[3 * i] += sx; pos[3 * i + 1] += sy; pos[3 * i + 2] += sz;
      prev[3 * i] += sx; prev[3 * i + 1] += sy; prev[3 * i + 2] += sz;
    }
    this.anchorPrevP.copy(a1);
    this.anchorPrevQ.copy(anchorQ);

    for (let s = 0; s < nSub; s++) this._substep(anim, prims);

    // NaN 守卫 + 位移统计
    let bad = false;
    for (let i = 0; i < 3 * n; i++) if (!Number.isFinite(pos[i])) { bad = true; break; }
    if (bad) { this.nanResets++; this.reset(anim, anchorQ); return; }
    let maxDisp = 0;
    for (let i = 0; i < n; i++) {
      const dx = pos[3 * i] - anim[3 * i], dy = pos[3 * i + 1] - anim[3 * i + 1], dz = pos[3 * i + 2] - anim[3 * i + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (d > maxDisp) maxDisp = d;
    }
    this.lastMaxDisp = maxDisp;
  }

  _substep(anim, prims) {
    const n = this.n, pos = this.pos, prev = this.prev, fixed = this.fixed;
    const dt = SIM_DT;
    // Verlet:vel=(pos−prev)·(1−drag),|vel|≤maxVel·dt;gravity 数据面全 0
    for (let i = 0; i < n; i++) {
      const i3 = 3 * i;
      if (fixed[i]) {
        prev[i3] = pos[i3]; prev[i3 + 1] = pos[i3 + 1]; prev[i3 + 2] = pos[i3 + 2];
        pos[i3] = anim[i3]; pos[i3 + 1] = anim[i3 + 1]; pos[i3 + 2] = anim[i3 + 2];
        continue;
      }
      const damp = 1 - this.drag[i];
      let vx = (pos[i3] - prev[i3]) * damp;
      let vy = (pos[i3 + 1] - prev[i3 + 1]) * damp;
      let vz = (pos[i3 + 2] - prev[i3 + 2]) * damp;
      const sp = Math.sqrt(vx * vx + vy * vy + vz * vz), lim = this.maxVel[i] * dt;
      if (sp > lim && sp > 0) { const k = lim / sp; vx *= k; vy *= k; vz *= k; }
      prev[i3] = pos[i3]; prev[i3 + 1] = pos[i3 + 1]; prev[i3 + 2] = pos[i3 + 2];
      pos[i3] += vx + this.grav[i3] * dt * dt;
      pos[i3 + 1] += vy + this.grav[i3 + 1] * dt * dt;
      pos[i3 + 2] += vz + this.grav[i3 + 2] * dt * dt;
    }
    // restoreRotation:向动画方向回拉(每子步一次,幂 = 数据值 0.01–0.11)
    for (const v of this.order) {
      const p = this.chain.parent[v];
      if (p < 0 || fixed[v]) continue;
      const pow = this.restorePow[v];
      if (pow <= 0) continue;
      _v0.set(pos[3 * v] - pos[3 * p], pos[3 * v + 1] - pos[3 * p + 1], pos[3 * v + 2] - pos[3 * p + 2]);
      _v1.set(anim[3 * v] - anim[3 * p], anim[3 * v + 1] - anim[3 * p + 1], anim[3 * v + 2] - anim[3 * p + 2]);
      const lc = _v0.length(), la = _v1.length();
      if (lc < 1e-9 || la < 1e-9) continue;
      _v0.multiplyScalar(1 / lc); _v1.multiplyScalar(1 / la);
      _q0.setFromUnitVectors(_v0, _v1);
      _q1.copy(Q_IDENT).slerp(_q0, Math.min(1, pow));
      _v0.applyQuaternion(_q1).multiplyScalar(lc);
      pos[3 * v] = pos[3 * p] + _v0.x;
      pos[3 * v + 1] = pos[3 * p + 1] + _v0.y;
      pos[3 * v + 2] = pos[3 * p + 2] + _v0.z;
    }
    // 约束迭代:距离 → 根距钳制 → 限角 → 碰撞 → 固定点回钉
    for (let it = 0; it < this.iterations; it++) {
      this._projectDistance();
      this._projectRootClamp();
      this._projectClampRotation(anim);
      if (this.useCollision) this._projectCollision(prims);
      for (let i = 0; i < n; i++) if (fixed[i]) {
        pos[3 * i] = anim[3 * i]; pos[3 * i + 1] = anim[3 * i + 1]; pos[3 * i + 2] = anim[3 * i + 2];
      }
    }
  }

  _projectDistance() {
    const pos = this.pos, fixed = this.fixed;
    for (let e = 0; e < this.edges.length; e++) {
      const edge = this.edges[e];
      const a = edge.a, b = edge.b, len = edge.len;
      const st = this.edgeStiff[e];
      const fa = fixed[a], fb = fixed[b];
      if (fa && fb) continue;
      const dx = pos[3 * b] - pos[3 * a], dy = pos[3 * b + 1] - pos[3 * a + 1], dz = pos[3 * b + 2] - pos[3 * a + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (d < 1e-9) continue;
      const diff = (d - len) / d * st;
      const wa = fa ? 0 : (fb ? 1 : 0.5), wb = fb ? 0 : (fa ? 1 : 0.5);
      pos[3 * a] += dx * diff * wa; pos[3 * a + 1] += dy * diff * wa; pos[3 * a + 2] += dz * diff * wa;
      pos[3 * b] -= dx * diff * wb; pos[3 * b + 1] -= dy * diff * wb; pos[3 * b + 2] -= dz * diff * wb;
    }
  }

  _projectRootClamp() {
    const pos = this.pos;
    for (const rp of this.rootPairs) {
      const v = rp.v, r = rp.r, len = rp.len;
      if (this.fixed[v]) continue;
      const dx = pos[3 * v] - pos[3 * r], dy = pos[3 * v + 1] - pos[3 * r + 1], dz = pos[3 * v + 2] - pos[3 * r + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (d < 1e-9) continue;
      const t = Math.min(Math.max(d, len * this.minRatio), len * this.maxRatio);
      if (t === d) continue;
      const k = t / d;
      pos[3 * v] = pos[3 * r] + dx * k;
      pos[3 * v + 1] = pos[3 * r + 1] + dy * k;
      pos[3 * v + 2] = pos[3 * r + 2] + dz * k;
    }
  }

  _projectClampRotation(anim) {
    const pos = this.pos;
    for (const v of this.order) {
      const p = this.chain.parent[v];
      if (p < 0 || this.fixed[v]) continue;
      const limit = this.clampAng[v];
      if (limit >= Math.PI - 1e-6) continue;
      _v0.set(pos[3 * v] - pos[3 * p], pos[3 * v + 1] - pos[3 * p + 1], pos[3 * v + 2] - pos[3 * p + 2]);
      _v1.set(anim[3 * v] - anim[3 * p], anim[3 * v + 1] - anim[3 * p + 1], anim[3 * v + 2] - anim[3 * p + 2]);
      const lc = _v0.length();
      if (lc < 1e-9 || _v1.lengthSq() < 1e-18) continue;
      _v0.multiplyScalar(1 / lc); _v1.normalize();
      const ang = _v1.angleTo(_v0);
      if (ang <= limit) continue;
      _q0.setFromUnitVectors(_v1, _v0);
      _q1.copy(Q_IDENT).slerp(_q0, limit / ang);
      _v1.applyQuaternion(_q1).multiplyScalar(lc);
      pos[3 * v] = pos[3 * p] + _v1.x;
      pos[3 * v + 1] = pos[3 * p + 1] + _v1.y;
      pos[3 * v + 2] = pos[3 * p + 2] + _v1.z;
    }
  }

  _projectCollision(prims) {
    if (!prims || !prims.length) return;
    const pos = this.pos;
    for (let i = 0; i < this.n; i++) {
      if (this.fixed[i]) continue;
      const r = this.radius[i];
      _v3.set(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]);
      let moved = false;
      for (const prim of prims) moved = pushOut(_v3, r, prim) || moved;
      if (moved) { pos[3 * i] = _v3.x; pos[3 * i + 1] = _v3.y; pos[3 * i + 2] = _v3.z; }
    }
  }
}

// ---------------------------------------------------------------- 场景绑定

export class ClothSystem {
  // nodeByIndex: glTF node index → Object3D (optional). Node names may repeat,
  // so nodeIndices/node references take precedence over name-based lookup.
  constructor(root, rigNorm, nodeByIndex) {
    this.root = root;
    this.rig = rigNorm;
    this.chains = [];
    this.missingBones = [];
    this.missingColliders = [];
    this.accumulator = 0;
    this.lastSubsteps = 0;

    root.updateMatrixWorld(true); // 绑定姿态(调用方保证 mixer 尚未播放)
    const byIdx = (i) => (nodeByIndex ? nodeByIndex[i] : undefined) || null;

    for (const chain of rigNorm.chains) {
      const bones = chain.bones.map((name, i) =>
        (chain.nodeIndices ? byIdx(chain.nodeIndices[i]) : null) || root.getObjectByName(name) || null);
      const missing = chain.bones.filter((_, i) => !bones[i]);
      if (missing.length) { this.missingBones.push(...missing); continue; }
      const n = bones.length;
      const rest = new Float64Array(3 * n);
      for (let i = 0; i < n; i++) {
        const e = bones[i].matrixWorld.elements;
        rest[3 * i] = e[12]; rest[3 * i + 1] = e[13]; rest[3 * i + 2] = e[14];
      }
      const restLocal = bones.map((b) => ({ p: b.position.clone(), q: b.quaternion.clone(), s: b.scale.clone() }));
      const colRecs = [];
      for (const def of chain.colliders) {
        const bone = (def.node !== undefined ? byIdx(def.node) : null) || root.getObjectByName(def.bone) || null;
        if (bone) colRecs.push({ def, bone });
        else this.missingColliders.push(def.bone);
      }
      const solver = new ChainSolver(chain, rest);
      this.chains.push({
        chain, bones, restLocal, solver,
        cols: colRecs,
        prims: [],
        anim: new Float64Array(3 * n),
        animQ: bones.map(() => new THREE.Quaternion()),
        newP: bones.map(() => new THREE.Vector3()),
        newQ: bones.map(() => new THREE.Quaternion()),
      });
    }
    this.missingColliders = [...new Set(this.missingColliders)];
  }

  get chainCount() { return this.chains.length; }
  get particleCount() { return this.chains.reduce((s, c) => s + c.bones.length, 0); }
  get colliderCount() { return this.chains.reduce((s, c) => s + c.cols.length, 0); }

  restoreRest() {
    for (const rec of this.chains) {
      for (let i = 0; i < rec.bones.length; i++) {
        const r = rec.restLocal[i];
        rec.bones[i].position.copy(r.p);
        rec.bones[i].quaternion.copy(r.q);
        rec.bones[i].scale.copy(r.s);
      }
    }
  }

  reset() { for (const rec of this.chains) rec.solver.inited = false; this.accumulator = 0; }

  // 前置条件:场景矩阵已按「动画姿态」更新(布料骨 rest 本地 + 动画父链)
  step(dt) {
    dt = Math.min(Math.max(dt, 0), 0.1);
    this.accumulator += dt;
    let nSub = Math.floor(this.accumulator / SIM_DT);
    if (nSub > 0) this.accumulator -= nSub * SIM_DT;
    if (nSub > 6) nSub = 6;
    this.lastSubsteps = nSub;

    for (const rec of this.chains) {
      const bones = rec.bones, solver = rec.solver, anim = rec.anim;
      for (let i = 0; i < bones.length; i++) {
        const e = bones[i].matrixWorld.elements;
        anim[3 * i] = e[12]; anim[3 * i + 1] = e[13]; anim[3 * i + 2] = e[14];
        bones[i].matrixWorld.decompose(_v0, rec.animQ[i], _v1);
      }
      rec.prims.length = 0;
      for (const cr of rec.cols) rec.prims.push(colliderToWorld(cr.def, cr.bone.matrixWorld, {}));
      solver.step(anim, rec.animQ[solver.anchorIdx], dt, rec.prims, nSub);
      this._writeBack(rec);
    }
  }

  // 位置直写 + 由父→子方向反解旋转(叶子用自身父边);fixed 顶点保持动画位置但旋转随子线。
  _writeBack(rec) {
    const chain = rec.chain, bones = rec.bones, solver = rec.solver, anim = rec.anim;
    const pos = solver.pos;
    const kids = solver.children;
    for (const v of solver.order) {
      const bone = bones[v];
      const p = chain.parent[v];
      let dirOk = false;
      if (kids[v].length) {
        _v0.set(0, 0, 0); _v1.set(0, 0, 0);
        for (const k of kids[v]) {
          _v0.x += anim[3 * k] - anim[3 * v];
          _v0.y += anim[3 * k + 1] - anim[3 * v + 1];
          _v0.z += anim[3 * k + 2] - anim[3 * v + 2];
          _v1.x += pos[3 * k] - pos[3 * v];
          _v1.y += pos[3 * k + 1] - pos[3 * v + 1];
          _v1.z += pos[3 * k + 2] - pos[3 * v + 2];
        }
        dirOk = true;
      } else if (p >= 0) {
        _v0.set(anim[3 * v] - anim[3 * p], anim[3 * v + 1] - anim[3 * p + 1], anim[3 * v + 2] - anim[3 * p + 2]);
        _v1.set(pos[3 * v] - pos[3 * p], pos[3 * v + 1] - pos[3 * p + 1], pos[3 * v + 2] - pos[3 * p + 2]);
        dirOk = true;
      }
      const wq = rec.newQ[v];
      if (dirOk && _v0.lengthSq() > 1e-18 && _v1.lengthSq() > 1e-18) {
        _q0.setFromUnitVectors(_v0.normalize(), _v1.normalize());
        wq.copy(_q0).multiply(rec.animQ[v]);
      } else wq.copy(rec.animQ[v]);
      const wp = rec.newP[v];
      if (solver.fixed[v]) wp.set(anim[3 * v], anim[3 * v + 1], anim[3 * v + 2]);
      else wp.set(pos[3 * v], pos[3 * v + 1], pos[3 * v + 2]);
      // 本地化:父 = 链内顶点(其新世界)或链外骨(动画世界矩阵)
      if (p >= 0) { _v2.copy(rec.newP[p]); _q1.copy(rec.newQ[p]); }
      else if (bone.parent) bone.parent.matrixWorld.decompose(_v2, _q1, _v4);
      else { _v2.set(0, 0, 0); _q1.identity(); }
      _q2.copy(_q1).invert();
      bone.quaternion.copy(_q2).multiply(wq);
      bone.position.copy(wp).sub(_v2).applyQuaternion(_q2);
    }
  }

  stats() {
    let maxDisp = 0, nanResets = 0, teleports = 0, restMismatch = 0;
    for (const rec of this.chains) {
      if (rec.solver.lastMaxDisp > maxDisp) maxDisp = rec.solver.lastMaxDisp;
      if (rec.solver.restMismatch > restMismatch) restMismatch = rec.solver.restMismatch;
      nanResets += rec.solver.nanResets;
      teleports += rec.solver.teleportResets;
    }
    return {
      chains: this.chainCount, particles: this.particleCount, colliders: this.colliderCount,
      maxDisp, nanResets, teleports, restMismatch, substeps: this.lastSubsteps,
    };
  }
}
