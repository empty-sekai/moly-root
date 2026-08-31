// facial.js — facial atlas and blink/speech runtime
//
// Atlas cells use a one-based index, four columns, and a lower bound of one.
// Eye cells are 512×256 in a 2048×2048 texture; mouth cells are 512×256 in
// a 2048×1024 texture. glTF offsets use positive V after export.
//
// Blink timing is open [3000,5000) ms, closed [100,150) ms, open [100,150) ms,
// with a 50% chance of a second blink. Speech alternates closed [50,100) ms
// and open [100,300) ms. Default rows provide the initial open eye and closed mouth.

export const EYE_CELL = { x: 0.25, y: 0.125 };
export const MOUTH_CELL = { x: 0.25, y: 0.25 };

export function cellOf(index) {
  let i = index | 0;
  if (i < 2) i = 1; // 0, -1, and 1 all select cell 1
  i -= 1;
  return { col: i % 4, row: i >> 2 };
}

export function cellOffset(index, cell) {
  const c = cellOf(index);
  return { x: c.col * cell.x, y: c.row * cell.y };
}

// Derive a cell offset from atlas metadata; fall back to the documented cell sizes.
export function cellFromAtlas(atlas, fallback) {
  if (!atlas) return fallback;
  const g = atlas.gltfOffsetPerCell; // glTF-space per-cell offset, with positive V
  if (Array.isArray(g) && g.length === 2) return { x: +g[0], y: +g[1] };
  const tex = atlas.textureSize || atlas.texture || atlas.atlasSize;
  const one = atlas.cellSize || atlas.oneTextureSize || atlas.cell;
  if (Array.isArray(tex) && Array.isArray(one) && tex[0] > 0 && tex[1] > 0)
    return { x: one[0] / tex[0], y: one[1] / tex[1] };
  return fallback;
}

// A rig may provide complete default rows; a string remains a table key for compatibility.
export function rowFromDefault(v, kind) {
  if (!v) return null;
  if (typeof v === 'string') return { name: v };
  if (kind === 'eye' && v.PatternName !== undefined) return {
    pattern: v.PatternName, open: v.OpenEyeIndex | 0, close: v.CloseEyeIndex | 0, blink: !!v.BlinkEnabled,
  };
  if (kind === 'mouth' && v.Name !== undefined) return {
    name: v.Name, open: v.OpenLipSyncIndex | 0, middle: (v.MiddleLipSyncIndex ?? -1) | 0, close: v.CloseLipSyncIndex | 0,
  };
  return null;
}

// ---------------------------------------------------------------- 表

function rowsOf(v, listKey) {
  if (Array.isArray(v)) return v;
  if (v && Array.isArray(v[listKey])) return v[listKey];
  return null;
}

// Accept the contract's logical tables and common case variants.
export function normalizeTables(json) {
  if (!json) return null;
  const find = (names, listKey) => {
    for (const k of Object.keys(json)) {
      const lk = k.toLowerCase();
      if (names.some((n) => lk === n || lk.includes(n))) {
        const rows = rowsOf(json[k], listKey);
        if (rows) return rows;
      }
    }
    return null;
  };
  const eyeRows = find(['npcavatareyedata', 'eyedata', 'eye'], 'EyeDataList');
  const lipRows = find(['npcavatarlipsyncdata', 'lipsyncdata', 'lipsync', 'mouth'], 'LipSyncDataList');
  const defRows = find(['npcavatardefaultfacialdata', 'defaultfacial', 'defaults'], 'NPCAvatarDefaultFacialData');
  if (!eyeRows || !lipRows) return null;
  const eye = new Map(), lip = new Map(), defaults = new Map();
  for (const r of eyeRows) eye.set(r.PatternName ?? r.patternName ?? r.name, {
    pattern: r.PatternName ?? r.patternName ?? r.name,
    open: (r.OpenEyeIndex ?? r.open) | 0,
    close: (r.CloseEyeIndex ?? r.close) | 0,
    blink: !!(r.BlinkEnabled ?? r.blink),
  });
  for (const r of lipRows) lip.set(r.Name ?? r.name, {
    name: r.Name ?? r.name,
    open: (r.OpenLipSyncIndex ?? r.open) | 0,
    middle: (r.MiddleLipSyncIndex ?? r.middle ?? -1) | 0,
    close: (r.CloseLipSyncIndex ?? r.close) | 0,
  });
  if (defRows) for (const r of defRows) defaults.set((r.CharacterUnitId ?? r.unitId) | 0, {
    eye: r.EyePatternName ?? r.eye,
    mouth: r.MouthPatternName ?? r.mouth,
  });
  return { eye, lip, defaults, counts: { eye: eye.size, lip: lip.size, defaults: defaults.size } };
}

// The uniq-pattern suffixes are the characters' English given names in lower
// case, matched against the pages' character ids (game character id = unit id
// minus 100). Only characters that actually have uniq rows appear here. A uniq
// row is only ever valid on its own character, because each atlas paints its
// own face in the shared uniq cell; offering a row named after someone else
// would draw the current character's face under another character's name.
export const UNIQ_NAMES = new Map([
  [2, 'saki'], [4, 'shiho'], [7, 'airi'], [10, 'an'], [11, 'akito'],
  [13, 'tsukasa'], [14, 'emu'], [15, 'nene'], [16, 'rui'], [19, 'ena'],
  [20, 'mizuki'], [30, 'wnsmiku'],
]);

// Given name of the character at this unit id, or '' when the character has
// no uniq rows (and therefore never needs the name).
export function uniqNameOf(unitId) {
  return UNIQ_NAMES.get(unitId | 0) || '';
}

// Fallback rows for a missing facial-tables.json; the viewer reports this as a fallback.
// Built-in fallback rows for the standard eye and mouth atlas.
export function fallbackTables() {
  const eye = new Map([['normal', { pattern: 'normal', open: 1, close: 5, blink: true }]]);
  const lip = new Map([
    ['smile01', { name: 'smile01', open: 7, middle: -1, close: 5 }],
    ['normal01', { name: 'normal01', open: 3, middle: -1, close: 1 }],
  ]);
  const normal01Units = new Set([4, 11, 12, 15, 17, 18, 31]);
  const defaults = new Map();
  for (let u = 1; u <= 55; u++) defaults.set(u, { eye: 'normal', mouth: normal01Units.has(u) ? 'normal01' : 'smile01' });
  return { eye, lip, defaults, counts: { eye: 1, lip: 2, defaults: 55 }, fallback: true };
}

export function patternsFor(unitId, tables, override) {
  const d = tables.defaults.get(unitId | 0) || { eye: 'normal', mouth: 'smile01' };
  // An override may be a complete rig row or a table key.
  const oe = rowFromDefault(override && override.eye, 'eye');
  const om = rowFromDefault(override && override.mouth, 'mouth');
  if (oe && oe.open !== undefined && om && om.open !== undefined) return { eyeRow: oe, lipRow: om };
  const eyeName = (oe && oe.name) || (oe && oe.pattern) || d.eye || 'normal';
  const mouthName = (om && om.name) || d.mouth || 'smile01';
  const eyeRow = (oe && oe.open !== undefined ? oe : null)
    || tables.eye.get(eyeName) || tables.eye.get('normal') || { pattern: 'normal', open: 1, close: 5, blink: true };
  const lipRow = (om && om.open !== undefined ? om : null)
    || tables.lip.get(mouthName) || tables.lip.get('smile01') || { name: 'smile01', open: 7, middle: -1, close: 5 };
  return { eyeRow, lipRow };
}

// ---------------------------------------------------------------- 状态机

// Random ranges use an exclusive upper bound: [a,b).
function randRange(rng, a, b) { return a + rng() * (b - a); }

export class FacialController {
  // opts: {applyEye(idx), applyMouth(idx), rng?, now?}
  constructor(opts) {
    this.applyEye = opts.applyEye;
    this.applyMouth = opts.applyMouth;
    this.rng = opts.rng || Math.random;
    this.now = opts.now || (() => performance.now());
    this.blinkEnabled = true;   // UI 开关(对应 _isBlinkEnabled,ctor 默认 true)
    this.speaking = false;      // Speaking mode is controlled by the UI.
    this.eyeRow = null;
    this.lipRow = null;
    this._eyePhase = 'idle';
    this._mouthPhase = 'idle';
    this.mouthHeldOpen = false;
    this._eyeDeadline = 0;
    this._mouthDeadline = 0;
    this.currentEyeIndex = -999;
    this.currentMouthIndex = -999;
    this.trace = opts.trace ? [] : null;
  }

  _setEye(idx) {
    if (idx === this.currentEyeIndex) return;
    this.currentEyeIndex = idx;
    if (this.trace) this.trace.push({ t: this.now(), eye: idx });
    this.applyEye(idx);
  }

  _setMouth(idx) {
    if (idx === this.currentMouthIndex) return;
    this.currentMouthIndex = idx;
    if (this.trace) this.trace.push({ t: this.now(), mouth: idx });
    this.applyMouth(idx);
  }

  // Inspection hold: park the mouth on the cell its pattern is named for.
  // A lip row is a pair of cells and the resting law shows the closed one, so at
  // rest every pattern looks like one of a few near-identical closed lines and the
  // name says nothing. This is entered only by a manual pick; speech and any
  // performance step clear it, so playback keeps the law unchanged.
  holdMouthOpen(on) {
    this.mouthHeldOpen = !!on;
    if (!this.lipRow) return;
    this._setMouth(this.mouthHeldOpen && !this.speaking ? this.lipRow.open : this.lipRow.close);
  }

  setPatterns(eyeRow, lipRow) {
    this.eyeRow = eyeRow;
    this.lipRow = lipRow;
    this.mouthHeldOpen = false;
    this._setEye(eyeRow.open);
    this._setMouth(lipRow.close);
    // Changing a pattern updates the current cell without restarting the blink clock.
    // This keeps frequent performance updates from starving the closed-eye interval.
    if (this._eyePhase === 'idle') {
      this._eyePhase = 'hold';
      this._eyeDeadline = this.now() + randRange(this.rng, 3000, 5000);
    }
    this._mouthPhase = this.speaking ? 'close' : 'still';
    this._mouthDeadline = this.now() + randRange(this.rng, 50, 100);
  }

  setSpeaking(on) {
    this.speaking = !!on;
    if (on) this.mouthHeldOpen = false;
    if (!this.lipRow) return;
    if (on) {
    // Enter speech with a closed cell, then open after [50,100) ms.
      this._setMouth(this.lipRow.close);
      this._mouthPhase = 'close';
      this._mouthDeadline = this.now() + randRange(this.rng, 50, 100);
    } else {
      this._setMouth(this.lipRow.close); // Resting speech state is Close
      this._mouthPhase = 'still';
    }
  }

  setBlinkEnabled(on) {
    this.blinkEnabled = !!on;
    if (!this.eyeRow) return;
    if (!on) { this._setEye(this.eyeRow.open); this._eyePhase = 'disabled'; }
    else { this._eyePhase = 'hold'; this._eyeDeadline = this.now() + randRange(this.rng, 3000, 5000); }
  }

  update() {
    const t = this.now();
    if (this.eyeRow) this._updateEye(t);
    if (this.lipRow) this._updateMouth(t);
  }

  _updateEye(t) {
    const row = this.eyeRow;
    if (!this.blinkEnabled) { this._setEye(row.open); return; } // Keep the eye open when disabled
    if (!row.blink) { // Rows without blink support stay open
      this._setEye(row.open);
      if (t >= this._eyeDeadline) this._eyeDeadline = t + randRange(this.rng, 3000, 5000);
      return;
    }
    if (t < this._eyeDeadline) return;
    switch (this._eyePhase) {
      case 'hold':
        this._setEye(row.close);
        this._eyePhase = 'close1';
        this._eyeDeadline = t + randRange(this.rng, 100, 150);
        break;
      case 'close1':
        this._setEye(row.open);
        this._eyePhase = 'open1';
        this._eyeDeadline = t + randRange(this.rng, 100, 150);
        break;
      case 'open1':
        if (this.rng() < 0.5) { // 50% chance of a second blink
          this._setEye(row.close);
          this._eyePhase = 'close2';
          this._eyeDeadline = t + randRange(this.rng, 100, 150);
        } else {
          this._eyePhase = 'hold';
          this._eyeDeadline = t + randRange(this.rng, 3000, 5000);
        }
        break;
      case 'close2':
        this._setEye(row.open);
        this._eyePhase = 'open2';
        this._eyeDeadline = t + randRange(this.rng, 100, 150);
        break;
      case 'open2':
      default:
        this._eyePhase = 'hold';
        this._eyeDeadline = t + randRange(this.rng, 3000, 5000);
        break;
    }
  }

  _updateMouth(t) {
    const row = this.lipRow;
    // Resting mode keeps Close; the inspection hold is the one exception.
    if (!this.speaking) { this._setMouth(this.mouthHeldOpen ? row.open : row.close); return; }
    if (t < this._mouthDeadline) return;
    switch (this._mouthPhase) {
      case 'close':
        this._setMouth(row.open);
        this._mouthPhase = 'open';
        this._mouthDeadline = t + randRange(this.rng, 100, 300);
        break;
      case 'open':
      default:
        this._setMouth(row.close);
        this._mouthPhase = 'close';
        this._mouthDeadline = t + randRange(this.rng, 50, 100);
        break;
    }
  }
}
