// selfcheck.js — 页内自检:程序化断言画到 HUD,并输出 [selfcheck] 控制台行供自动化采集。
// 故障注入:?sabotage=stencil|nan|gamma|shader|anim 逐项模拟违规,对应检查必须转红。

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
