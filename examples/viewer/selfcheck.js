// selfcheck.js — 页内自检:程序化断言画到 HUD,并输出 [selfcheck] 控制台行供自动化采集。
// 故障注入:?sabotage=stencil|nan|gamma|shader|anim|fxpoint|fxnode|resize 逐项模拟违规,对应检查必须转红。

import { EMIT_FAULT } from './emoticon.js';

export class CheckPanel {
  constructor(el) {
    this.el = el;
    this.items = new Map(); // id → {name, status, detail}
    this.order = [];
    this.done = false;
  }
  set(id, name, status, detail = '') {
    if (!this.items.has(id)) this.order.push(id);
    this.items.set(id, { name, status, detail });
    this.render();
    if (status !== 'pending') console.log(`[selfcheck] ${status.toUpperCase()} ${id}: ${name}${detail ? ' — ' + detail : ''}`);
  }
  counts() {
    let pass = 0, fail = 0, skip = 0, pending = 0;
    for (const { status } of this.items.values()) {
      if (status === 'pass') pass++;
      else if (status === 'fail') fail++;
      else if (status === 'skip') skip++;
      else pending++;
    }
    return { pass, fail, skip, pending };
  }
  maybeFinish() {
    const c = this.counts();
    if (c.pending === 0 && !this.done) {
      this.done = true;
      console.log(`[selfcheck] DONE pass=${c.pass} fail=${c.fail} skip=${c.skip}`);
      document.body.dataset.selfcheck = 'done';
      document.body.dataset.selfcheckFail = String(c.fail);
      this.render();
    } else if (c.pending === 0 && this.done) {
      // DONE 之后仍可能有项被复测(动效两项要等用户起播),结论变了就再说一声。
      // 只补 UPDATE 行、不重发 DONE:外部探针按第一条 DONE 收口,别把它们的时序打乱。
      const line = `pass=${c.pass} fail=${c.fail} skip=${c.skip}`;
      if (line !== this._lastLine) {
        this._lastLine = line;
        console.log(`[selfcheck] UPDATE ${line}`);
        document.body.dataset.selfcheckFail = String(c.fail);
      }
    }
  }
  render() {
    const glyph = { pass: '✓', fail: '✗', skip: '−', pending: '…' };
    const cls = { pass: 'ok', fail: 'bad', skip: 'warn', pending: 'dim' };
    const c = this.counts();
    const head = `<span class="${c.fail ? 'bad' : 'ok'}">${c.pass}✓ ${c.fail}✗</span>`
      + (c.skip ? ` <span class="warn">${c.skip}−</span>` : '')
      + (c.pending ? ` <span class="dim">${c.pending}…</span>` : ' <span class="dim">完成</span>');
    let html = `<b>自检</b> ${head}`;
    for (const id of this.order) {
      const it = this.items.get(id);
      html += `\n<span class="${cls[it.status]}">${glyph[it.status]} ${it.name}</span>`
        + (it.detail ? ` <span class="dim">${it.detail}</span>` : '');
    }
    this.el.innerHTML = html;
    // 面板默认收起,所以计数要镜到 summary 上 —— 不然收起状态下看不见通过/失败数。
    // summary 用词而不用 ✓/✗:11px 下这两个符号很容易被看成 "/" 和 "X"。
    // 面板正文里的字样一个都没动。
    const sum = document.getElementById('selfcheckSummary');
    if (sum) {
      sum.innerHTML = `<span class="${c.fail ? 'bad' : 'ok'}">${c.pass} 通过</span>`
        + ` · <span class="${c.fail ? 'bad' : 'dim'}">${c.fail} 失败</span>`
        + (c.skip ? ` · <span class="warn">${c.skip} 跳过</span>` : '')
        + (c.pending ? ` · <span class="dim">${c.pending} 进行中</span>` : '');
    }
  }
}

export function parseSabotage() {
  const p = new URLSearchParams(location.search).get('sabotage');
  return p ? p.split(',').map((s) => s.trim()).filter(Boolean) : [];
}

// 在材质构建后、首帧渲染前调用(shader/gamma/stencil 需要抢在编译/首检前)
export function applySabotage(app, names) {
  for (const n of names) {
    if (n === 'stencil' && app.overlayMesh) {
      app.overlayMesh.material.stencilFunc = app.THREE.AlwaysStencilFunc;
      console.warn('[sabotage] overlay stencilFunc → Always');
    } else if (n === 'gamma' && app.materials.length) {
      app.materials[0].uniforms.mainTex.value.colorSpace = app.THREE.SRGBColorSpace;
      console.warn('[sabotage] mainTex colorSpace → srgb');
    } else if (n === 'shader' && app.materials.length) {
      app.materials[0].fragmentShader += '\nthis is not glsl;';
      app.materials[0].needsUpdate = true;
      console.warn('[sabotage] fragment 注入非法代码');
    } else if (n === 'anim' && app.mixer) {
      app.mixer.timeScale = 0;
      console.warn('[sabotage] mixer.timeScale=0');
    } else if (n === 'patch') {
      app._sabotagePatch = true;
      if (app.eyeMesh) app.eyeMesh.visible = false;
      if (app.overlayMesh) app.overlayMesh.visible = false;
      if (app.mouthMesh) app.mouthMesh.visible = false;
      console.warn('[sabotage] facial atlas meshes hidden');
    } else if (n === 'nan') {
      app._sabotageNaN = true; // viewer 帧循环里在 solver 就绪后注入一次
      console.warn('[sabotage] 将注入 NaN 到布料粒子');
    } else if (n === 'fxpoint') {
      // 本单修复前的行为:未建模形状退化成点发射。散布判据必须红。
      EMIT_FAULT.pointEmit = true;
      console.warn('[sabotage] 未建模发射形状 → 退化成点发射');
    } else if (n === 'fxnode') {
      // 出生点不过发射节点的世界变换:高度判据必须红(节点在 15 米高处,粒子从 0 米冒出来)。
      EMIT_FAULT.dropNodeTransform = true;
      console.warn('[sabotage] World 空间出生点 → 不过发射节点世界变换');
    }
  }
}

export function facialPatchDrawStatus(app) {
  const eye = app.eyeMesh, mouth = app.mouthMesh, overlay = app.overlayMesh;
  const eyeMat = eye && eye.material, mouthMat = mouth && mouth.material;
  const eyeUv = eyeMat && eyeMat.uniforms && eyeMat.uniforms.uvOffset;
  const mouthUv = mouthMat && mouthMat.uniforms && mouthMat.uniforms.uvOffset;
  const eyeWired = !!(eyeUv && Number.isFinite(eyeUv.value.x) && Number.isFinite(eyeUv.value.y));
  const mouthWired = !!(mouthUv && Number.isFinite(mouthUv.value.x) && Number.isFinite(mouthUv.value.y));
  const eyeDrawn = !!(eye && eye.userData._selfcheckDraws > 0);
  const mouthDrawn = !!(mouth && mouth.userData._selfcheckDraws > 0);
  const overlayDrawn = !overlay || overlay.userData._selfcheckDraws > 0;
  return { ok: eyeWired && mouthWired && eyeDrawn && mouthDrawn && overlayDrawn, eyeDrawn, mouthDrawn, overlayDrawn };
}

export function runChecks(app, panel) {
  const T = app.THREE;
  const gl = app.renderer.getContext();

  // --- 同步:上下文与 gamma 管线 ---
  const bits = gl.getParameter(gl.STENCIL_BITS);
  panel.set('ctx.stencil', 'stencil 缓冲', bits >= 8 ? 'pass' : 'fail', `bits=${bits}`);
  const gammaOk = app.renderer.outputColorSpace === T.LinearSRGBColorSpace
    && app.renderer.toneMapping === T.NoToneMapping;
  panel.set('gamma.renderer', '输出直通(LinearSRGB+无TM)', gammaOk ? 'pass' : 'fail',
    `${app.renderer.outputColorSpace}/${app.renderer.toneMapping}`);

  const texes = [];
  for (const m of app.materials.concat(app.overlayMesh ? [app.overlayMesh.material] : []))
    for (const k of ['mainTex', 'bodyMaskTex', 'eyebrowTex'])
      if (m.uniforms && m.uniforms[k] && m.uniforms[k].value) texes.push(m.uniforms[k].value);
  const badTex = texes.filter((t) => t.colorSpace !== T.NoColorSpace);
  panel.set('gamma.textures', '贴图 NoColorSpace', badTex.length ? 'fail' : 'pass', `${texes.length} 张`);

  // --- 同步:stencil 状态 ---
  const ref = app.stencilRef;
  const baseBad = app.materials.filter((m) => !(m.stencilWrite
    && m.stencilFunc === T.AlwaysStencilFunc && m.stencilRef === ref
    && m.stencilZPass === T.ReplaceStencilOp && m.stencilWriteMask === 0xff));
  panel.set('stencil.base', `Base 写模板 Ref=${ref} Always/Replace`, baseBad.length ? 'fail' : 'pass',
    `${app.materials.length - baseBad.length}/${app.materials.length}`);
  if (app.overlayMesh) {
    const om = app.overlayMesh.material;
    const ok = om.stencilWrite && om.stencilFunc === T.EqualStencilFunc && om.stencilRef === ref
      && om.stencilWriteMask === 0 && om.depthFunc === T.AlwaysDepth && om.depthWrite === true
      && om.transparent === true && app.overlayMesh.renderOrder > 0
      && app.overlayMesh.skeleton === app.eyeMesh.skeleton;
    panel.set('stencil.overlay', 'Eye overlay Equal/深度Always/最后画', ok ? 'pass' : 'fail');
  } else panel.set('stencil.overlay', 'Eye overlay', 'skip', '未建(缺 eye 材质)');

  // --- 数据契约 ---
  const di = app.dataInfo;
  panel.set('data.manifest', 'manifest.json', di.manifest ? 'pass' : 'skip', di.manifest ? `${di.unitCount} units` : '缺失→回退固定名单');
  panel.set('data.extras', '材质 extras', di.extrasFound.length ? 'pass' : 'skip',
    di.extrasFound.length ? di.extrasFound.join(',') : '缺失→使用默认参数');
  panel.set('data.tables', 'facial 表', di.tables ? (di.tablesFallback ? 'skip' : 'pass') : 'fail',
    di.tables ? `eye=${di.tables.eye} lip=${di.tables.lip} def=${di.tables.defaults}${di.tablesFallback ? '(内置兜底)' : ''}` : '');
  panel.set('data.rig', 'rig.json', di.rig ? 'pass' : 'skip', di.rig ? `${di.rigChains} 链` : '缺失→布料关闭');

  // --- 首帧后:shader 编译 ---
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const progs = app.renderer.info.programs || [];
    const diag = progs.filter((p) => p.diagnostics);
    const ok = diag.length === 0 && app.shaderErrors.length === 0;
    panel.set('shader.compile', 'shader 编译', ok ? 'pass' : 'fail',
      ok ? `${progs.length} programs` : (diag.map((p) => p.name).join(',') || app.shaderErrors[0] || '').slice(0, 120));
    panel.maybeFinish();
  }));

  // --- 异步:眼/嘴 atlas 贴片实际进入渲染路径 ---
  panel.set('facial.patch-render', '眼/嘴 atlas 贴片实际进入渲染', 'pending');
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const status = facialPatchDrawStatus(app);
    panel.set('facial.patch-render', '眼/嘴 atlas 贴片实际进入渲染', status.ok ? 'pass' : 'fail',
      `eye=${status.eyeDrawn ? 'drawn' : 'not-drawn'} mouth=${status.mouthDrawn ? 'drawn' : 'not-drawn'} overlay=${app.overlayMesh ? (status.overlayDrawn ? 'drawn' : 'not-drawn') : 'absent'}`);
    panel.maybeFinish();
  }));

  // --- 异步:动画推进 / 段衔接(见文件末 armMotionChecks) ---
  armMotionChecks(app, panel);

  // --- 环境(现象):数据装载 / 渐变尺寸 / 淡化时长实测 / 粒子 / 后处理 / 未还原 ---
  armEnvChecks(app, panel);

  // --- 独立布局自检:现象切换、长状态文本、真实窗口 resize ---
  armCanvasLayoutChecks(app, panel);

  // --- 异步:布料有限且受约束 ---
  if (app.clothSystem && app.clothSystem.chainCount) {
    const cs = app.clothSystem;
    const bindOk = cs.missingBones.length === 0 && cs.missingColliders.length === 0;
    panel.set('cloth.bind', `布料绑定 ${cs.chainCount} 链 ${cs.particleCount} 粒子 ${cs.colliderCount} 碰撞体`,
      bindOk ? 'pass' : 'fail',
      bindOk ? '' : `缺骨:${cs.missingBones.slice(0, 3).join(',')} 缺碰撞骨:${cs.missingColliders.slice(0, 3).join(',')}`);
    const rm = cs.stats().restMismatch;
    panel.set('cloth.restmatch', '烘焙约束表长 == 场景 rest 骨距(≤5mm)', rm <= 0.005 ? 'pass' : 'fail',
      `max=${(rm * 1000).toFixed(2)}mm${rm > 0.005 ? '(烘焙表与当前绑定骨距不一致——运行期以烘焙表为准,物理平衡可能偏离动画姿态)' : ''}`);
    panel.set('cloth.finite', '粒子位置有限·位移有界', 'pending');
    setTimeout(() => {
      const st = cs.stats();
      const ok = st.nanResets === 0 && Number.isFinite(st.maxDisp) && st.maxDisp < 1.5;
      panel.set('cloth.finite', '粒子位置有限·位移有界', ok ? 'pass' : 'fail',
        `maxDisp=${(st.maxDisp * 1000).toFixed(1)}mm nanResets=${st.nanResets} tp=${st.teleports}`);
      panel.maybeFinish();
    }, 4000);
  } else {
    panel.set('cloth.bind', '布料绑定', 'skip', di.rig ? 'rig 无链' : '无 rig.json');
  }

  // --- 异步:默认表情 + 眨眼 + 说话探针 ---
  if (app.facial && app.facial.eyeRow) {
    const f = app.facial;
    const defOk = f.currentEyeIndex === f.eyeRow.open && f.currentMouthIndex === f.lipRow.close;
    panel.set('facial.default', `默认表情 eye=Open(${f.eyeRow.open}) mouth=Close(${f.lipRow.close})`,
      defOk ? 'pass' : 'fail', `实际 ${f.currentEyeIndex}/${f.currentMouthIndex}`);
    panel.set('facial.blink', '眨眼循环(≤8s 内闭眼≥1 次)', 'pending');
    const t0 = (f.trace || []).length;
    setTimeout(() => {
      // 这两个探针盯的是**这一个** FacialController 的 trace。换角色会新建一个,旧的
      // update() 从此不再被调用 → trace 不再增长 → closes 必然是 0。那是「测量对象没了」,
      // 不是「眨眼不成立」,所以报 skip 说清楚,别给一个假红。
      if (app.facial !== f) {
        panel.set('facial.blink', '眨眼循环(≤8s 内闭眼≥1 次)', 'skip', '测量期间换了角色,这一轮测不了');
        panel.maybeFinish();
        return;
      }
      const closes = (f.trace || []).slice(t0).filter((e) => e.eye === f.eyeRow.close).length;
      panel.set('facial.blink', '眨眼循环(≤8s 内闭眼≥1 次)', closes >= 1 ? 'pass' : 'fail', `closes=${closes}`);
      panel.maybeFinish();
    }, 8000);
    // 口型循环最短一轮 = 闭[50,100) + 开[100,300),即 2.5s 窗口内引擎侧应有 >=2 次张嘴。
    // 该探针依赖每帧 update() 推进,低帧率下会**测不到**而非**不成立**——
    // 所以帧数不足时报 skip 并附观测帧数(如实说测不了),绝不因此下调张嘴次数门槛。
    const SPEAK_OPENS_MIN = 2;
    const SPEAK_WINDOW_MS = 2500;
    const SPEAK_FRAMES_MIN = 24;   // 2.5s 内至少 24 次 update 才可能观察到 2 轮
    panel.set('facial.speak', `说话探针(${SPEAK_WINDOW_MS / 1000}s 内张嘴≥${SPEAK_OPENS_MIN} 次)`, 'pending');
    setTimeout(() => {
      if (app.userTouchedSpeak) { panel.set('facial.speak', '说话探针', 'skip', '用户已手动操作'); panel.maybeFinish(); return; }
      const m0 = (f.trace || []).length;
      const fr0 = app.frameCount || 0;
      f.setSpeaking(true);
      setTimeout(() => {
        if (!app.userTouchedSpeak) f.setSpeaking(false);
        const label = `说话探针(${SPEAK_WINDOW_MS / 1000}s 内张嘴≥${SPEAK_OPENS_MIN} 次)`;
        if (app.facial !== f) {      // 同 blink:测量期间换角色 → 旧 controller 不再推进
          panel.set('facial.speak', label, 'skip', '测量期间换了角色,这一轮测不了');
          panel.maybeFinish();
          return;
        }
        const opens = (f.trace || []).slice(m0).filter((e) => e.mouth === f.lipRow.open).length;
        const frames = (app.frameCount || 0) - fr0;
        if (opens >= SPEAK_OPENS_MIN) panel.set('facial.speak', label, 'pass', `opens=${opens} frames=${frames}`);
        else if (frames < SPEAK_FRAMES_MIN)
          panel.set('facial.speak', label, 'skip', `帧数不足无法判定:frames=${frames}<${SPEAK_FRAMES_MIN}(opens=${opens})`);
        else panel.set('facial.speak', label, 'fail', `opens=${opens} frames=${frames}`);
        panel.maybeFinish();
      }, SPEAK_WINDOW_MS);
    }, 800);
  } else {
    panel.set('facial.default', '表情运行时', 'skip', '无表数据');
  }

  panel.maybeFinish();
}

// ---------------------------------------------------------------- 动效两项
// 「动画推进」与「段衔接 S→L」都要有动作在跑才测得到,而 viewer 默认**不自动播放**
// (载入停在静止姿态,点了才播)。所以这两项挂着等起播:
//   起播 → 按原判据、原阈值测,给 pass/fail(判据一个字没动);
//   宽限期内没人点 → 报 skip 并写明「未播放」,而且**继续挂着** —— 之后点动作族或点面板里
//   的「复测动效」会自动补测,结论用 [selfcheck] UPDATE 行补一句。
// 这条规矩沿用本文件 facial.speak 的做法:测不到就说测不到,绝不为了变绿去下调门槛。
const MOTION_GRACE_MS = 8000;    // 等用户起播的宽限期(超时先如实报 skip,不代表判据放宽)
const ANIM_SETTLE_MS = 1500;     // 与原判据同值:起播 1.5s 后看探针骨动没动

function armMotionChecks(app, panel) {
  const LBL_ANIM = '动画推进';
  const withSL = (f) => !!(f && f.segs && f.segs.S && f.segs.L);
  const segLabel = (f) => (f ? `段衔接 ${f.base} S→L` : '段衔接 S→L');
  // 「在播」看 phase,不看 segctl.current:静止姿态是一个冻住的 action 借住在 current 上
  // (给第一次起播做 crossFade 起点),它不是在播。
  const started = () => !!(app.segctl && app.segctl.phase !== 'idle' && app.mixer);

  let animPhase = 'wait';        // wait → settle → done
  let animSnap = null, animAt = 0;
  panel.set('anim.playing', LBL_ANIM, 'pending');

  let segPhase = 'wait';         // wait → timing → done
  let segFam = null, segAt = 0, segBudget = 0;
  const segTarget = withSL(app.currentFamily) ? app.currentFamily
    : (withSL(app.selfcheckFamily) ? app.selfcheckFamily : null);
  if (segTarget && app.segctl) panel.set('seg.sle', segLabel(segTarget), 'pending');
  else { segPhase = 'done'; panel.set('seg.sle', '段衔接 S→L', 'skip', '当前数据无 S/L 族'); }

  const t0 = performance.now();
  let warned = false;
  const timer = setInterval(() => {
    const now = performance.now();

    if (animPhase === 'wait' && started()) {
      animSnap = app.probeBone ? app.probeBone.quaternion.clone() : null;
      animAt = now;
      animPhase = 'settle';
      panel.set('anim.playing', LBL_ANIM, 'pending', '测量中…');
    } else if (animPhase === 'settle' && now - animAt >= ANIM_SETTLE_MS) {
      const moved = app.mixer && app.mixer.time > 0 && animSnap && app.probeBone
        && animSnap.angleTo(app.probeBone.quaternion) > 1e-4;
      panel.set('anim.playing', LBL_ANIM, moved ? 'pass' : 'fail',
        app.mixer ? `mixer.t=${app.mixer.time.toFixed(2)}s` : '无 mixer');
      animPhase = 'done';
      panel.maybeFinish();
    }

    if (segPhase === 'wait' && app.segctl && app.segctl.phase === 'S' && withSL(app.segctl.family)) {
      segFam = app.segctl.family;
      const sClip = app.clipByName.get(segFam.segs.S);
      segBudget = ((sClip && sClip.duration) || 3) * 1000 / Math.max(app.mixer ? app.mixer.timeScale : 1, 0.01) + 2000;
      segAt = now;
      segPhase = 'timing';
      panel.set('seg.sle', segLabel(segFam), 'pending', '测量中…');
    } else if (segPhase === 'timing') {
      const ctl = app.segctl;
      // 只有「还在测同一个族的 S/L」才允许判定。用户中途点了别的段/别的族 → 这一轮被打断,
      // 那是测量条件没了,不是衔接坏了 —— 退回去等下一次起播,绝不记成 fail(假红最伤信任)。
      if (ctl.family !== segFam || (ctl.phase !== 'S' && ctl.phase !== 'L')) {
        segPhase = 'wait';
        if (warned) panel.set('seg.sle', segLabel(segFam), 'skip', '上一轮测量被打断(中途换了段/族),等下一次起播复测');
      } else if (ctl.phase === 'L') {
        panel.set('seg.sle', segLabel(segFam), 'pass', `${((now - segAt) / 1000).toFixed(1)}s 达到 L`);
        segPhase = 'done';
        panel.maybeFinish();
      } else if (now - segAt > segBudget) {
        panel.set('seg.sle', segLabel(segFam), 'fail', `超时,phase=${ctl.phase}`);
        segPhase = 'done';
        panel.maybeFinish();
      }
    }

    if (!warned && now - t0 > MOTION_GRACE_MS) {
      warned = true;
      if (animPhase === 'wait') {
        panel.set('anim.playing', LBL_ANIM, 'skip', '未播放:默认不自动播,点动作族或「复测动效」后自动补测');
      }
      if (segPhase === 'wait') {
        panel.set('seg.sle', segLabel(segTarget), 'skip', '未播放:点带 S/L 的动作族或「复测动效」后自动补测');
      }
      panel.maybeFinish();
    }

    if (animPhase === 'done' && segPhase === 'done') clearInterval(timer);
  }, 120);
}

// ---------------------------------------------------------------- 环境六项
// 环境层默认**不开**(没选现象时它就该是关的),所以这几项与动效两项同规矩:
//   开了 → 按判据测,给 pass/fail;
//   宽限期内没开 → 报 skip 并写明「未开启」,而且**继续挂着**,之后开了会自动补测。
// 淡化时长这一项要等一次真的切换发生:`environment.status().fadeRuns` 里有实测秒数才判。
// 「未还原」这一项**恒为 warn(skip)**:它不是失败,但也绝不能因为「看起来正常」就消失。
const ENV_GRACE_MS = 9000;      // 等用户开环境层的宽限期
const FADE_TOLERANCE = 0.12;    // 实测淡化时长与声明值的允许偏差(秒);帧粒度决定了它不可能为 0

// ---- 粒子位置判据(散布 + 高度) ----
//
// 两条判据只看**出生点**:出生后重力与 velocityOverLifetime 会把粒子带走几十米,
// 拿在场粒子的散布去判形状会把「形状对不对」和「运动对不对」混成一件事。
// 读数由 `environment.status().particles.placement` 逐发射器给出。
//
//   散布 —— 带半径的已建模形状,出生点到**形状原点**的最远距离应与「声明半径 × 形状缩放」
//           同阶,容差 [0.4, 1.6] 倍。参照点是形状原点(节点世界变换作用在 `shape.position`
//           上),不是节点原点 —— `shape.position` 是数据明写的偏移,拿节点原点当参照会把
//           合法偏移判成错(实测有一个边发射器整体下移 2 米)。
//           红条件:`?sabotage=fxpoint`(未建模形状退化成点发射)——Hemisphere 半径 25 的
//           发射器出生点散布会落到 1e-16 米量级,判据必须红。
//   高度 —— 出生点的平均高度应落在**形状原点的世界高度**附近:平面形状容差 0.5 米,
//           立体形状(Sphere/BoxEdge)再放一个半径。
//           红条件:`?sabotage=fxnode`(出生点不过发射节点的世界变换)——形状原点在 15 米
//           高处、粒子却从 0 米冒出来,判据必须红。
const SPREAD_LO = 0.4, SPREAD_HI = 1.6;
const SPREAD_MIN_RADIUS = 1;     // 声明半径 < 1 米的形状本来就是点状(子发射器 r=1e-4),不判散布
const SPREAD_MIN_BIRTHS = 30;    // 出生点太少时「最远距离」这个统计量不稳,不判
const HEIGHT_TOL = 0.5;          // 高度容差(米);平面形状的出生点本应恰在节点高度上
const PLACE_WATCH_MS = 1000;     // 首判之后的复判间隔(换现象/换站点会换一批发射器)
// 每种形状的尺寸用到 `shape.scale` 的哪几轴 —— 散布的期望值按这几轴里最大的缩放算。
// **不在表里的形状不会被跳过**:按三轴里最大的缩放算(否则一个没有发射公式的形状万一
// 真发射了,判据会因为查不到表而放它过去 —— 实测 `?sabotage=fxpoint` 就是这么漏的)。
// BoxEdge 是唯一的例外:它的尺寸整个来自 shape.scale,radius 对它没有语义,所以不判散布。
const SPREAD_AXES = { Sphere: [0, 1, 2], Circle: [0, 1], Cone: [0, 1], SingleSidedEdge: [0],
  Hemisphere: [0, 1, 2], ConeVolume: [0, 1], Donut: [0, 1] };
const SPREAD_SKIP = new Set(['BoxEdge']);
// 出生点在体内散开,高度容差要加一个「半径」。Hemisphere 是半球体,ConeVolume 是柱/锥体,
// Donut 的管子在局部 z 上有 ±donutRadius 的厚度 —— 三者的出生点都不落在一个平面上。
const VOLUME_SHAPES = new Set(['Sphere', 'BoxEdge', 'Hemisphere', 'ConeVolume', 'Donut']);

/**
 * 一个形状的出生点**最远能到多远**(形状局部尺度,还没乘 shape.scale)。
 * 只看该形状真的读的字段:Donut 的外缘是 `radius + donutRadius`;ConeVolume 的粒子从
 * 半径 `radius` 的底盘出发再沿轴走最多 `length`。拿裸 `radius` 当期望会把对的实现判成错。
 */
function expectedReach(r) {
  const radius = r.radius || 0;
  if (r.shape === 'Donut') return radius + Math.abs(r.donutRadius || 0);
  if (r.shape === 'ConeVolume') return Math.hypot(radius, Math.abs(r.length || 0));
  return radius;
}

/** 逐发射器判散布与高度。返回 `{spread, height, bad, badTags}`,`bad` 非空即红。 */
export function placementVerdict(rows) {
  const spread = [], height = [], bad = [], badTags = [];
  for (const r of rows || []) {
    if (r.suppressed || !r.births) continue;   // 停发的另有计数;没发过的等下一轮
    const sc = r.shapeScale || [1, 1, 1];
    const axes = SPREAD_AXES[r.shape] || [0, 1, 2];
    const scale = Math.max(...axes.map((i) => Math.abs(Number.isFinite(sc[i]) ? sc[i] : 1)));
    const R = expectedReach(r) * scale;
    const tag = `${(r.effect || '').replace(/^fx_env_/, '')}/${r.node || '(root)'}`;
    if (r.shape && !SPREAD_SKIP.has(r.shape) && R >= SPREAD_MIN_RADIUS && r.births >= SPREAD_MIN_BIRTHS) {
      const ok = r.radialMax >= SPREAD_LO * R && r.radialMax <= SPREAD_HI * R;
      spread.push(`${tag} ${r.shape} r=${R.toFixed(1)}m→散布 ${r.radialMax.toFixed(1)}m${ok ? '' : ' ✗'}`);
      if (!ok) {
        bad.push(`${tag}: ${r.shape} 半径 ${R.toFixed(1)}m,${r.births} 个出生点最远只有`
          + ` ${r.radialMax.toFixed(3)}m(容差 ${SPREAD_LO}~${SPREAD_HI} 倍)`);
        badTags.push(`spread:${tag}`);
      }
    }
    if (r.centerY != null && r.birthY) {
      const tol = HEIGHT_TOL + (VOLUME_SHAPES.has(r.shape) ? R : 0);
      const d = Math.abs(r.birthY.mean - r.centerY);
      const ok = d <= tol;
      height.push(`${tag} 形状原点 y=${r.centerY.toFixed(1)}→出生 y=${r.birthY.mean.toFixed(1)}${ok ? '' : ' ✗'}`);
      if (!ok) {
        bad.push(`${tag}: 形状原点在 y=${r.centerY.toFixed(2)}(节点 y=${(r.nodeY ?? 0).toFixed(2)}),`
          + `出生点平均高度 ${r.birthY.mean.toFixed(2)}(差 ${d.toFixed(2)}m > 容差 ${tol.toFixed(2)}m)`);
        badTags.push(`height:${tag}`);
      }
    }
  }
  return { spread, height, bad, badTags };
}

function armEnvChecks(app, panel) {
  const LBL_DATA = '环境数据装载';
  const LBL_RAMP = '天空渐变尺寸';
  const LBL_FADE = '交叉淡化时长实测';
  const LBL_FX = '环境粒子';
  const LBL_PLACE = '粒子位置(散布/高度)';
  const LBL_POST = '后处理链';
  const LBL_TODO = '未还原/近似清单';
  const ENV_ITEMS = [['env.data', LBL_DATA], ['env.ramp', LBL_RAMP], ['env.fade', LBL_FADE],
    ['env.fx', LBL_FX], ['env.place', LBL_PLACE], ['env.post', LBL_POST], ['env.todo', LBL_TODO]];
  for (const [id, label] of ENV_ITEMS) {
    panel.set(id, label, 'pending');
  }

  const st = () => (app.environment ? app.environment.status() : null);
  const done = new Set();
  const finish = (id, label, status, detail) => {
    if (done.has(id)) return;
    done.add(id);
    panel.set(id, label, status, detail);
    panel.maybeFinish();
  };

  const t0 = performance.now();
  let warned = false;

  /**
   * 位置判据的一次结论。返回 null = **还测不到**(出生点还没攒够),不是通过。
   * 「没有可判散布的发射器」与「散布不对」是两件事,分开报。
   */
  const judgePlace = (s) => {
    const p = s.particles || {};
    if (!p.on) return { status: 'skip', detail: '环境粒子开关关着', key: 'off' };
    if (!p.emitters) return { status: 'skip', detail: '该现象在当前站点没有粒子效果', key: 'none' };
    const v = placementVerdict(p.placement);
    const supTail = p.suppressed
      ? ` · 停发 ${p.suppressed} 发(${Object.entries(p.suppressedShapes || {})
        .map(([k, x]) => `${k}x${x}`).join(',')} 无发射公式)` : '';
    // `key` 只带**结论的身份**(哪几个发射器红了/几项在判),不带出生点计数这类每秒都在变的数 ——
    // 否则复判会因为数字变了而每秒重发一行。
    if (v.bad.length) {
      return {
        status: 'fail',
        detail: v.bad.slice(0, 3).join(' | ') + (v.bad.length > 3 ? ` 等 ${v.bad.length} 条` : '') + supTail,
        key: `fail|${v.badTags.join(',')}`,
      };
    }
    if (v.spread.length) {
      return {
        status: 'pass',
        detail: `散布 ${v.spread.length} 项:${v.spread.slice(0, 2).join(' · ')}`
          + ` · 高度 ${v.height.length} 项全在形状原点高度 ±${HEIGHT_TOL}m 内${supTail}`,
        key: `pass|s=${v.spread.length}|h=${v.height.length}|sup=${p.suppressed || 0}`,
      };
    }
    if (v.height.length) {
      return {
        status: 'pass',
        detail: '无带半径的已建模形状可判散布(点发射与停发的不参与)'
          + ` · 高度 ${v.height.length} 项全在形状原点高度 ±${HEIGHT_TOL}m 内${supTail}`,
        key: `pass|s=0|h=${v.height.length}|sup=${p.suppressed || 0}`,
      };
    }
    return null;
  };

  // 位置判据是**活的**:换现象、换站点都会换一批发射器,一锤定音的结论会把后面挂上的错法
  // 整个漏掉(实测:先挂的现象没有未建模形状,判据先判了通过,之后切到带 Hemisphere 的现象
  // 就再也不复判了)。所以首判之后挂一个守望,结论变了就补一行(UPDATE 行就是为这个留的)。
  let placeWatch = null, placeKey = '';
  const startPlaceWatch = (first) => {
    placeKey = first.key;
    if (placeWatch) return;
    placeWatch = setInterval(() => {
      const s2 = st();
      if (!s2 || !s2.enabled) return;
      const v = judgePlace(s2);
      if (!v || v.key === placeKey) return;
      placeKey = v.key;
      panel.set('env.place', LBL_PLACE, v.status, v.detail);
      panel.maybeFinish();
    }, PLACE_WATCH_MS);
  };

  const timer = setInterval(() => {
    const s = st();
    const now = performance.now();

    // 装载与渐变尺寸只要环境层的清单在位就能判,不必等开启。
    if (s && s.indexLoaded && !done.has('env.data')) {
      const ok = s.phenomenaCount > 0 && !s.errors.length;
      finish('env.data', LBL_DATA, ok ? 'pass' : 'fail',
        `${s.phenomenaCount} 个现象 · 已装载 ${s.loadedCount}`
        + `${s.errors.length ? ` · 错误 ${s.errors[0]}` : ''}`);
    }
    if (s && s.phenomenon && !done.has('env.ramp')) {
      // 契约:渐变是 32x1。尺寸不符就是数据不对,不是「近似」。
      const r = s.ramp || {};
      const ok = r.width === 32 && r.height === 1;
      finish('env.ramp', LBL_RAMP, ok ? 'pass' : 'fail',
        `${r.width ?? '?'}x${r.height ?? '?'}${s.rampReady ? ' · 已解码' : ' · 未解码'}`);
    }
    if (s && s.enabled && !done.has('env.fx')) {
      const p = s.particles;
      const shapes = Object.entries(p.unmodelledShapes || {});
      const sup = Object.entries(p.suppressedShapes || {});
      if (!p.on) {
        finish('env.fx', LBL_FX, 'skip', '环境粒子开关关着');
      } else if (p.emitters > 0) {
        // 未建模形状与无形状模块逐项报出来;停发的那些**不算失败也不算通过**,但必须
        // 出现在这行里:静默才是失败。
        //
        // 「有发射器就该有活粒子」只在**真的有发射器会发**时成立:一个现象里会发射的发射器
        // 可能整批都是未建模形状(实测:15 个现象里有 4 个是这样),那时零活粒子是**如实的
        // 欠账**,不是实现错了 —— 报 warn 并写清楚,不许当成通过,也不许算成失败。
        // 刚挂上、一帧都还没走过时活粒子当然是 0 —— 那是**测不到**,等出生点或宽限期。
        const rows = p.placement || [];
        const births = rows.reduce((n, r) => n + r.births, 0);
        const shouldEmit = rows.filter((r) => r.emits && !r.suppressed).length;
        const tail = `${p.suppressed ? ` · 停发 ${p.suppressed} 发(${sup.map(([k, v]) => `${k}x${v}`).join(',')} 无发射公式)` : ''}`
          + `${shapes.length ? ` · 文档内未建模形状 ${shapes.map(([k, v]) => `${k}x${v}`).join(',')}` : ''}`
          + `${p.emittersWithoutShape ? ` · 无形状模块 ${p.emittersWithoutShape}(点发射即其语义)` : ''}`;
        if (!shouldEmit && p.suppressed) {
          finish('env.fx', LBL_FX, 'skip',
            `${p.effects} 效果 ${p.emitters} 发射器:会发射的 ${p.suppressed} 发全因形状无发射公式停发,`
            + `画面上这个现象没有粒子${tail}`);
        } else if (p.live || births) {
          finish('env.fx', LBL_FX, p.live > 0 ? 'pass' : 'fail',
            `${p.effects} 效果 ${p.emitters} 发射器 ${p.live} 活粒子(应发 ${shouldEmit} 发)${tail}`);
        }
      } else {
        finish('env.fx', LBL_FX, 'skip', '该现象在当前站点没有粒子效果');
      }
    }
    // 位置判据:等出生点攒够。判据与红条件见文件上方 placementVerdict 的注释。
    if (s && s.enabled && !done.has('env.place')) {
      const v = judgePlace(s);
      if (v) { finish('env.place', LBL_PLACE, v.status, v.detail); startPlaceWatch(v); }
    }
    if (s && s.enabled && !done.has('env.post')) {
      const p = s.post;
      if (!p.enabled) finish('env.post', LBL_POST, 'skip', '后处理开关关着');
      else {
        // 一趟都没跑不等于坏了:若档案里**能生效的组件全被摘除**(颜色方程未取证),
        // 那「零趟」正是当前的正确行为。把这两种情形分开报,免得被读成故障。
        const sup = (p.suppressed || []).length;
        const live = p.components.filter((c) => !c.suppressed).length;
        const ok = p.passes.length > 0 ? p.components.length > 0 : (sup > 0 && live <= 1);
        const inherited = p.components.reduce((n, c) => n + c.inherited.length, 0);
        finish('env.post', LBL_POST, ok ? 'pass' : 'fail',
          `${p.components.length} 组件${sup ? `(摘除 ${sup})` : ''} · ${p.passes.length} 趟`
          + `${p.passes.length ? `(${p.passes.join('→')})` : '——能生效的组件都被摘除,按纪律不跑'}`
          + `${inherited ? ` · 档案未设 ${inherited} 参(按中性值)` : ''}`
          + `${p.lut.present ? ' · LUT 已接' : ' · 无 LUT'}`);
      }
    }
    if (s && !done.has('env.todo')) {
      // 未还原清单恒为 warn:它是如实报告,不是通过,也不是失败。
      const n = (s.notRestored || []).length + (s.sky.approximations || []).length
        + (s.post.unresolved || []).length;
      finish('env.todo', LBL_TODO, 'skip',
        `${n} 条(${(s.notRestored || []).length} 未还原 + ${(s.sky.approximations || []).length} 天空近似`
        + ` + ${(s.post.unresolved || []).length} 语义未定)`);
    }
    // 淡化:等一次真实切换留下实测秒数。
    if (s && !done.has('env.fade') && (s.fadeRuns || []).length) {
      const last = s.fadeRuns[s.fadeRuns.length - 1];
      // 判据看**模拟时间**(累加的帧增量),不看墙钟:浏览器会节流动画帧,
      // 墙钟随刷新率漂移,而声明的 0.25 秒是模拟时长。墙钟仍打印出来作参考。
      const sim = typeof last.simulated === 'number' ? last.simulated : last.seconds;
      const ok = Math.abs(sim - last.declared) <= FADE_TOLERANCE;
      finish('env.fade', LBL_FADE, ok ? 'pass' : 'fail',
        `${last.from}→${last.to} 模拟 ${sim.toFixed(3)}s / 声明 ${last.declared}s`
        + `(容差 ${FADE_TOLERANCE}s;墙钟 ${last.seconds.toFixed(3)}s 仅参考)`);
    }

    if (!warned && now - t0 > ENV_GRACE_MS) {
      warned = true;
      if (!s) {
        for (const [id, label] of ENV_ITEMS) {
          finish(id, label, 'skip', '无 phenomena 数据');
        }
      } else {
        if (!done.has('env.ramp')) panel.set('env.ramp', LBL_RAMP, 'skip', '未选现象:点一个现象后自动补测');
        if (!done.has('env.fx')) {
          // 层开着、有该发的发射器,宽限期内一个粒子都没出生 → 这才是红。
          // 「会发的全被停发」不算红:那一路在上面按 warn 报过(如实的欠账)。
          const p = s.particles || {};
          const shouldEmit = (p.placement || []).filter((r) => r.emits && !r.suppressed).length;
          const stuck = s.enabled && p.on && shouldEmit > 0;
          panel.set('env.fx', LBL_FX, stuck ? 'fail' : 'skip', stuck
            ? `${shouldEmit} 个应发的发射器在 ${(ENV_GRACE_MS / 1000).toFixed(0)} 秒内零出生点`
            : '未开环境层:开启后自动补测');
        }
        if (!done.has('env.place')) {
          // 「测不到」与「不成立」分开写:没开层、没粒子、或者出生点还没攒够都是测不到。
          const p = s.particles || {};
          panel.set('env.place', LBL_PLACE, 'skip', s.enabled
            ? `未攒够出生点:${p.emitters || 0} 发射器,可判散布的 0 项`
              + `${p.suppressed ? ` · 停发 ${p.suppressed} 发` : ''}`
            : '未开环境层:开启后自动补测');
        }
        if (!done.has('env.post')) panel.set('env.post', LBL_POST, 'skip', '未开环境层:开启后自动补测');
        if (!done.has('env.fade')) panel.set('env.fade', LBL_FADE, 'skip', '未发生切换:切一次现象后自动补测');
        panel.maybeFinish();
      }
    }
    if (done.size >= ENV_ITEMS.length) clearInterval(timer);
  }, 150);
}


// ---------------------------------------------------------------- Layout self-check
// This probe only consumes DOM and renderer dimensions; it does not alter render state.
const LAYOUT_WAIT_MS = 12000;
const LAYOUT_SETTLE_MS = 360;
const LAYOUT_SWITCH_SECONDS = 0;
const LAYOUT_FRAME_WAIT = 2;
const LAYOUT_KEYS = ['canvasWidth', 'canvasHeight', 'clientWidth', 'clientHeight',
  'viewWidth', 'viewHeight', 'rendererWidth', 'rendererHeight', 'drawingWidth',
  'drawingHeight', 'railLeft'];

const waitMs = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const waitFrames = (n = 1) => new Promise((resolve) => {
  const step = () => (n-- > 0 ? requestAnimationFrame(step) : resolve());
  requestAnimationFrame(step);
});

function layoutSnapshot(app) {
  const view = document.getElementById('view');
  const rail = document.getElementById('railR');
  const canvas = app.renderer && app.renderer.domElement;
  const css = app.renderer && app.renderer.getSize
    ? app.renderer.getSize(new app.THREE.Vector2()) : { x: NaN, y: NaN };
  const db = app.renderer && app.renderer.getDrawingBufferSize
    ? app.renderer.getDrawingBufferSize(new app.THREE.Vector2()) : { x: NaN, y: NaN };
  return {
    canvasWidth: canvas ? canvas.width : NaN,
    canvasHeight: canvas ? canvas.height : NaN,
    clientWidth: canvas ? canvas.clientWidth : NaN,
    clientHeight: canvas ? canvas.clientHeight : NaN,
    viewWidth: view ? view.clientWidth : NaN,
    viewHeight: view ? view.clientHeight : NaN,
    rendererWidth: css.x,
    rendererHeight: css.y,
    drawingWidth: db.x,
    drawingHeight: db.y,
    railLeft: rail ? rail.getBoundingClientRect().left : NaN,
  };
}

function layoutSame(a, b) {
  return LAYOUT_KEYS.every((key) => Number.isFinite(a[key]) && Number.isFinite(b[key])
    && Math.abs(a[key] - b[key]) <= (key === 'railLeft' ? 0.01 : 0));
}

function layoutFitsView(s) {
  return s.clientWidth === s.viewWidth && s.clientHeight === s.viewHeight
    && s.rendererWidth === s.viewWidth && s.rendererHeight === s.viewHeight
    && s.drawingWidth === s.canvasWidth && s.drawingHeight === s.canvasHeight;
}

function layoutShort(s) {
  return `view=${s.viewWidth}x${s.viewHeight} renderer=${s.rendererWidth}x${s.rendererHeight}`
    + ` canvas=${s.canvasWidth}x${s.canvasHeight} rail=${s.railLeft.toFixed(2)}`;
}

function armCanvasLayoutChecks(app, panel) {
  if (parseSabotage().includes('resize')) {
    // Negative path: emulate post-processing writing its independent buffer size back.
    app._sabotageResize = true;
    console.warn('[sabotage] renderer 尺寸错误回写');
  }
  panel.set('layout.phenomena', '15 个现象切换保持画面尺寸与右栏', 'pending');
  panel.set('layout.status', '长状态文本不改变画面尺寸与右栏', 'pending');
  panel.set('layout.resize', '真实窗口 resize 改变画面尺寸', 'pending');

  const initial = layoutSnapshot(app);
  let resizeSettled = false;
  const onResize = () => {
    if (resizeSettled) return;
    waitFrames(LAYOUT_FRAME_WAIT).then(() => {
      const after = layoutSnapshot(app);
      const changed = after.canvasWidth !== initial.canvasWidth || after.canvasHeight !== initial.canvasHeight;
      panel.set('layout.resize', '真实窗口 resize 改变画面尺寸', changed ? 'pass' : 'fail',
        `before=${initial.canvasWidth}x${initial.canvasHeight} after=${after.canvasWidth}x${after.canvasHeight}`);
      resizeSettled = true;
      removeEventListener('resize', onResize);
      panel.maybeFinish();
    });
  };
  addEventListener('resize', onResize);
  setTimeout(() => {
    if (resizeSettled) return;
    resizeSettled = true;
    removeEventListener('resize', onResize);
    panel.set('layout.resize', '真实窗口 resize 改变画面尺寸', 'skip', '未收到真实窗口 resize');
    panel.maybeFinish();
  }, LAYOUT_WAIT_MS);

  // **这条判据会替用户把每个现象点一遍,所以默认不跑。**
  // 自检的本分是观察,不是驱动界面 —— 页面一打开就自动切 15 个现象,
  // 用户看到的是「自己在被自动点击」,而且循环结束时若初始没选过现象,
  // 恢复分支无从恢复,选择状态就留在半开态(实测:点完之后选不了)。
  // 要跑它就显式加 `?check=layout`(探针与 CI 用),普通打开一律跳过。
  const LAYOUT_DRIVE = new URLSearchParams(location.search).get('check') === 'layout';

  (async () => {
    if (!LAYOUT_DRIVE) {
      panel.set('layout.phenomena', '15 个现象切换保持画面尺寸与右栏', 'skip',
        '默认不跑:它要替用户切换全部现象;加 ?check=layout 才执行');
      panel.maybeFinish();
      return;
    }
    const deadline = performance.now() + LAYOUT_WAIT_MS;
    while ((!app.environment || !app.environment.names || !app.environment.names.length)
      && performance.now() < deadline) await waitMs(100);
    const env = app.environment;
    if (!env || !env.names || !env.names.length || typeof app.setEnvPhenomenon !== 'function') {
      panel.set('layout.phenomena', '15 个现象切换保持画面尺寸与右栏', 'skip', '无 phenomena 数据');
      panel.maybeFinish();
      return;
    }

    const names = env.names.slice();
    const initialName = env.to && env.to.name;
    const initiallyEnabled = !!env.enabled;
    let baseline = null;
    const rows = [];
    const bad = [];
    try {
      for (const name of names) {
        const ok = await app.setEnvPhenomenon(name, LAYOUT_SWITCH_SECONDS);
        if (!ok) {
          bad.push(`${name}:切换失败`);
          continue;
        }
        await waitMs(LAYOUT_SETTLE_MS);
        await waitFrames(LAYOUT_FRAME_WAIT);
        const snap = layoutSnapshot(app);
        rows.push(`${name} ${layoutShort(snap)}`);
        if (!baseline) baseline = snap;
        else if (!layoutSame(baseline, snap)) bad.push(`${name}:与首项尺寸不同`);
        if (!layoutFitsView(snap)) bad.push(`${name}:renderer 未跟随 view`);
      }
    } catch (e) {
      bad.push(`异常:${String(e).slice(0, 120)}`);
    } finally {
      try {
        if (initialName && names.includes(initialName)) {
          await app.setEnvPhenomenon(initialName, LAYOUT_SWITCH_SECONDS);
          await waitMs(LAYOUT_SETTLE_MS);
        }
        // 初始**没选过**现象时(initialName 为空),上面那支恢复不了任何东西 ——
        // 于是界面停在「层被这条判据打开、但用户从没点过」的半开态,选择就失灵了。
        // 所以这里必须无条件把层关回初始态,而不是只在有初始现象时才收拾。
        if (!initiallyEnabled && typeof app.setEnvEnabled === 'function') {
          app.setEnvEnabled(false);
          await waitMs(LAYOUT_SETTLE_MS);
        }
      } catch (e) {
        bad.push(`恢复失败:${String(e).slice(0, 120)}`);
      }
    }

    const countOk = names.length === 15 && rows.length === names.length;
    const status = !bad.length && countOk ? 'pass' : 'fail';
    panel.set('layout.phenomena', '15 个现象切换保持画面尺寸与右栏', status,
      `${rows.length}/${names.length} 项${bad.length ? ` · ${bad.slice(0, 3).join(' | ')}` : ''}`);
    panel.maybeFinish();
  })().catch((e) => {
    panel.set('layout.phenomena', '15 个现象切换保持画面尺寸与右栏', 'fail', String(e).slice(0, 160));
    panel.maybeFinish();
  });

  (async () => {
    const status = document.getElementById('status');
    if (!status) {
      panel.set('layout.status', '长状态文本不改变画面尺寸与右栏', 'skip', '无 status 节点');
      panel.maybeFinish();
      return;
    }
    await waitFrames(LAYOUT_FRAME_WAIT);
    const before = layoutSnapshot(app);
    const old = status.innerHTML;
    try {
      status.innerHTML = `<span class="cell"><span class="k">fake</span><span class="v">${'X'.repeat(300)}</span></span>`;
      await waitFrames(LAYOUT_FRAME_WAIT);
      const after = layoutSnapshot(app);
      const ok = layoutSame(before, after) && layoutFitsView(before) && layoutFitsView(after);
      panel.set('layout.status', '长状态文本不改变画面尺寸与右栏', ok ? 'pass' : 'fail',
        `before=${layoutShort(before)} after=${layoutShort(after)}`);
    } catch (e) {
      panel.set('layout.status', '长状态文本不改变画面尺寸与右栏', 'fail', String(e).slice(0, 160));
    } finally {
      status.innerHTML = old;
    }
    panel.maybeFinish();
  })();
}
