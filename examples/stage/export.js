// Stage animation export: browser-only PNG, APNG and transparent GIF output.

const EXPORT_WIDTH = 320;
const EXPORT_HEIGHT = 320;
const DEFAULT_FRAME_COUNT = 24;
const DEFAULT_FPS = 12;
const GIF_ALPHA_THRESHOLD = 128;
// These are fixed capture-space points; the bbox probe below confirms the character point.
const CHARACTER_SAMPLE = Object.freeze({ x: 160, y: 160 });
const BACKGROUND_SAMPLE = Object.freeze({ x: 0, y: 0 });

const state = { objectUrls: [], lastResult: null };

function setBusy(ui, busy) {
  ui.button.disabled = busy;
  ui.button.textContent = busy ? '…' : '⇩';
  ui.button.title = busy ? '正在编码导出' : '导出 GIF、APNG 与 PNG 帧序列';
}

function setSummary(ui, text, status = '') {
  ui.summary.textContent = text;
  ui.summary.className = `muted${status ? ` check-${status}` : ''}`;
}

function addCheck(ui, id, name, status, detail = '', rows = []) {
  const item = document.createElement('div');
  item.className = `check-item check-${status}`;
  const glyph = status === 'pass' ? '✓' : status === 'fail' ? '✗' : '…';
  const title = document.createElement('div');
  const strong = document.createElement('b');
  strong.textContent = `${glyph} ${id} ${name}`;
  const summary = document.createElement('span');
  summary.textContent = detail ? ` ${detail}` : '';
  title.append(strong, summary);
  item.append(title);
  for (const row of rows) {
    const rowGlyph = row.status === 'pass' ? '✓' : row.status === 'fail' ? '✗' : '…';
    const rowNode = document.createElement('div');
    rowNode.className = `check-row check-${row.status}`;
    rowNode.textContent = `${rowGlyph} ${row.label}${row.detail ? ` ${row.detail}` : ''}`;
    item.append(rowNode);
  }
  ui.checks.append(item);
}

function resetChecks(ui) {
  ui.checks.replaceChildren();
  ui.downloads.replaceChildren();
  ui.meta.textContent = `画布 ${EXPORT_WIDTH}×${EXPORT_HEIGHT} · 角色点 (${CHARACTER_SAMPLE.x},${CHARACTER_SAMPLE.y}) · 背景点 (${BACKGROUND_SAMPLE.x},${BACKGROUND_SAMPLE.y}) · GIF alpha < ${GIF_ALPHA_THRESHOLD} 透明`;
}

async function waitForStageHook() {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const hook = window.__stageExport;
    if (hook?.ready?.()) return hook;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('stage 取帧钩子未就绪');
}

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('浏览器没有返回 PNG Blob'));
    }, 'image/png');
  });
}

function u32be(bytes, offset) {
  return (((bytes[offset] * 0x1000000) >>> 0) | (bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3]) >>> 0;
}

function u16le(bytes, offset) {
  return bytes[offset] | (bytes[offset + 1] << 8);
}

function putU16LE(value) {
  return [value & 0xff, (value >>> 8) & 0xff];
}

function putU16BE(value) {
  return [(value >>> 8) & 0xff, value & 0xff];
}

function putU32BE(value) {
  return [(value >>> 24) & 0xff, (value >>> 16) & 0xff, (value >>> 8) & 0xff, value & 0xff];
}

function concatBytes(...parts) {
  const size = parts.reduce((total, part) => total + part.length, 0);
  const result = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

function ascii(value) {
  return Uint8Array.from(value, (char) => char.charCodeAt(0));
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const value of bytes) {
    crc ^= value;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBytes = ascii(type);
  const body = concatBytes(typeBytes, data);
  return concatBytes(Uint8Array.from(putU32BE(data.length)), body, Uint8Array.from(putU32BE(crc32(body))));
}

function parsePng(bytes) {
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (!signature.every((value, index) => bytes[index] === value)) throw new Error('PNG 签名无效');
  const chunks = [];
  let offset = 8;
  while (offset + 12 <= bytes.length) {
    const length = u32be(bytes, offset);
    const end = offset + 12 + length;
    if (end > bytes.length) throw new Error('PNG chunk 越界');
    const type = String.fromCharCode(...bytes.slice(offset + 4, offset + 8));
    const data = bytes.slice(offset + 8, offset + 8 + length);
    chunks.push({ type, data, raw: bytes.slice(offset, end) });
    offset = end;
    if (type === 'IEND') break;
  }
  const ihdr = chunks.find((chunk) => chunk.type === 'IHDR');
  const idat = chunks.filter((chunk) => chunk.type === 'IDAT');
  if (!ihdr || !idat.length) throw new Error('PNG 缺少 IHDR 或 IDAT');
  return { chunks, ihdr: ihdr.data, idat };
}

function apngFrameControl(sequence, width, height, fps) {
  const delayDen = Math.min(65535, Math.max(1, Math.round(fps)));
  return Uint8Array.from([
    ...putU32BE(sequence), ...putU32BE(width), ...putU32BE(height),
    ...putU32BE(0), ...putU32BE(0), ...putU16BE(1), ...putU16BE(delayDen), 0, 0,
  ]);
}

function buildApng(pngBytesList, width, height, fps) {
  if (!pngBytesList.length) throw new Error('没有 PNG 帧');
  const first = parsePng(pngBytesList[0]);
  const output = [Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10])];
  for (const chunk of first.chunks) {
    if (chunk.type === 'IHDR') {
      output.push(chunk.raw);
      output.push(pngChunk('acTL', Uint8Array.from([...putU32BE(pngBytesList.length), ...putU32BE(0)])));
    } else if (!['IDAT', 'IEND', 'acTL', 'fcTL', 'fdAT'].includes(chunk.type)) {
      output.push(chunk.raw);
    }
  }
  let sequence = 0;
  for (let index = 0; index < pngBytesList.length; index += 1) {
    const frame = parsePng(pngBytesList[index]);
    if (u32be(frame.ihdr, 0) !== width || u32be(frame.ihdr, 4) !== height) throw new Error('PNG 帧尺寸不一致');
    output.push(pngChunk('fcTL', apngFrameControl(sequence, width, height, fps)));
    sequence += 1;
    for (const chunk of frame.idat) {
      if (index === 0) output.push(chunk.raw);
      else {
        output.push(pngChunk('fdAT', Uint8Array.from([...putU32BE(sequence), ...chunk.data])));
        sequence += 1;
      }
    }
  }
  output.push(pngChunk('IEND', new Uint8Array()));
  return concatBytes(...output);
}

function palette() {
  const result = new Uint8Array(256 * 3);
  for (let index = 1; index <= 216; index += 1) {
    const cube = index - 1;
    const r = Math.floor(cube / 36);
    const g = Math.floor(cube / 6) % 6;
    const b = cube % 6;
    result[index * 3] = r * 51;
    result[index * 3 + 1] = g * 51;
    result[index * 3 + 2] = b * 51;
  }
  return result;
}

function quantizeFrame(pixels) {
  const indexes = new Uint8Array(pixels.length / 4);
  for (let pixel = 0, out = 0; pixel < pixels.length; pixel += 4, out += 1) {
    if (pixels[pixel + 3] < GIF_ALPHA_THRESHOLD) {
      indexes[out] = 0;
      continue;
    }
    const r = Math.min(5, Math.round(pixels[pixel] / 255 * 5));
    const g = Math.min(5, Math.round(pixels[pixel + 1] / 255 * 5));
    const b = Math.min(5, Math.round(pixels[pixel + 2] / 255 * 5));
    indexes[out] = 1 + r * 36 + g * 6 + b;
  }
  return indexes;
}

function lzwEncode(indexes, minimumCodeSize = 8) {
  // Reset after each literal. This is intentionally conservative: it keeps every
  // emitted code at the initial width, while still using the GIF LZW grammar.
  const clear = 1 << minimumCodeSize;
  const end = clear + 1;
  const codeSize = minimumCodeSize + 1;
  const output = [];
  let bitCount = 0;
  const writeCode = (code) => {
    for (let bit = 0; bit < codeSize; bit += 1) {
      if (bitCount === 0) output.push(0);
      if (code & (1 << bit)) output[output.length - 1] |= 1 << bitCount;
      bitCount = (bitCount + 1) % 8;
    }
  };
  writeCode(clear);
  for (let index = 0; index < indexes.length; index += 1) {
    writeCode(indexes[index]);
    if (index + 1 < indexes.length) writeCode(clear);
  }
  writeCode(end);
  return Uint8Array.from(output);
}

function subBlocks(bytes) {
  const parts = [];
  for (let offset = 0; offset < bytes.length; offset += 255) {
    const part = bytes.slice(offset, offset + 255);
    parts.push(Uint8Array.from([part.length]), part);
  }
  parts.push(Uint8Array.from([0]));
  return concatBytes(...parts);
}

function gifGraphicControl(delay) {
  return Uint8Array.from([0x21, 0xf9, 0x04, 0x09, delay & 0xff, (delay >>> 8) & 0xff, 0x00, 0x00]);
}

function buildGif(frames, width, height, fps) {
  const output = [ascii('GIF89a')];
  output.push(Uint8Array.from([...putU16LE(width), ...putU16LE(height), 0xf7, 0x00, 0x00]));
  output.push(palette());
  output.push(Uint8Array.from([0x21, 0xff, 0x0b]), ascii('NETSCAPE2.0'), Uint8Array.from([0x03, 0x01, 0x00, 0x00, 0x00]));
  const delay = Math.max(1, Math.round(100 / fps));
  for (const pixels of frames) {
    const indexed = quantizeFrame(pixels);
    const compressed = lzwEncode(indexed, 8);
    output.push(gifGraphicControl(delay));
    output.push(Uint8Array.from([0x2c, ...putU16LE(0), ...putU16LE(0), ...putU16LE(width), ...putU16LE(height), 0x00]));
    output.push(Uint8Array.from([0x08]), subBlocks(compressed));
  }
  output.push(Uint8Array.from([0x3b]));
  return concatBytes(...output);
}

function parseGif(bytes) {
  const signature = String.fromCharCode(...bytes.slice(0, 6));
  let offset = 6;
  const width = u16le(bytes, offset);
  const height = u16le(bytes, offset + 2);
  const packed = bytes[offset + 4];
  offset += 7;
  if (packed & 0x80) offset += 3 * (1 << ((packed & 7) + 1));
  let frames = 0;
  let transparentFrames = 0;
  const transparentIndexes = new Set();
  let pendingTransparency = false;
  while (offset < bytes.length) {
    const marker = bytes[offset++];
    if (marker === 0x3b) break;
    if (marker === 0x21) {
      const label = bytes[offset++];
      if (label === 0xf9) {
        const size = bytes[offset++];
        const gcePacked = bytes[offset];
        const transparentIndex = bytes[offset + 3];
        pendingTransparency = size >= 4 && !!(gcePacked & 1);
        if (pendingTransparency) transparentIndexes.add(transparentIndex);
        offset += size + 1;
      } else {
        while (offset < bytes.length) {
          const size = bytes[offset++];
          if (!size) break;
          offset += size;
        }
      }
      continue;
    }
    if (marker !== 0x2c) break;
    offset += 8;
    const imagePacked = bytes[offset++];
    if (imagePacked & 0x80) offset += 3 * (1 << ((imagePacked & 7) + 1));
    offset += 1;
    while (offset < bytes.length) {
      const size = bytes[offset++];
      if (!size) break;
      offset += size;
    }
    frames += 1;
    if (pendingTransparency) transparentFrames += 1;
    pendingTransparency = false;
  }
  return { signature, width, height, frames, transparentFrames, transparentIndexes };
}

function parseApng(bytes) {
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (!signature.every((value, index) => bytes[index] === value)) throw new Error('APNG/PNG 签名无效');
  let offset = 8;
  let width = 0;
  let height = 0;
  let declaredFrames = 0;
  let frameControls = 0;
  while (offset + 12 <= bytes.length) {
    const length = u32be(bytes, offset);
    const type = String.fromCharCode(...bytes.slice(offset + 4, offset + 8));
    const dataOffset = offset + 8;
    if (type === 'IHDR') {
      width = u32be(bytes, dataOffset);
      height = u32be(bytes, dataOffset + 4);
    } else if (type === 'acTL') {
      declaredFrames = u32be(bytes, dataOffset);
    } else if (type === 'fcTL') {
      frameControls += 1;
    }
    offset += 12 + length;
    if (type === 'IEND') break;
  }
  return { width, height, declaredFrames, frameControls };
}

async function decodePixels(blob, width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('浏览器没有 2D canvas');
  let bitmap;
  if (typeof createImageBitmap === 'function') bitmap = await createImageBitmap(blob);
  else bitmap = await new Promise((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(blob);
    image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('图像解码失败')); };
    image.src = url;
  });
  context.clearRect(0, 0, width, height);
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close?.();
  return context.getImageData(0, 0, width, height).data;
}

function sample(data, point, width) {
  const index = (point.y * width + point.x) * 4;
  return { r: data[index], g: data[index + 1], b: data[index + 2], a: data[index + 3] };
}

function sampleRows(product, data) {
  const character = sample(data, CHARACTER_SAMPLE, EXPORT_WIDTH);
  const background = sample(data, BACKGROUND_SAMPLE, EXPORT_WIDTH);
  return [
    { status: character.a === 255 ? 'pass' : 'fail', label: `${product} 角色点 (${CHARACTER_SAMPLE.x},${CHARACTER_SAMPLE.y})`, detail: `alpha=${character.a}，要求 255` },
    { status: background.a === 0 ? 'pass' : 'fail', label: `${product} 背景点 (${BACKGROUND_SAMPLE.x},${BACKGROUND_SAMPLE.y})`, detail: `alpha=${background.a}，要求 0` },
  ];
}

function resourceCounts() {
  let sameOrigin = 0;
  let crossOrigin = 0;
  for (const entry of performance.getEntriesByType('resource')) {
    try {
      if (new URL(entry.name, location.href).origin === location.origin) sameOrigin += 1;
      else crossOrigin += 1;
    } catch {
      crossOrigin += 1;
    }
  }
  return { sameOrigin, crossOrigin };
}

function addDownloadLinks(ui, records) {
  for (const record of records) {
    const url = URL.createObjectURL(record.blob);
    state.objectUrls.push(url);
    const link = document.createElement('a');
    link.href = url;
    link.download = record.name;
    link.className = 'download-link';
    link.textContent = record.name;
    ui.downloads.append(link);
  }
  for (const link of ui.downloads.querySelectorAll('a')) link.click();
}

async function captureFrames(hook, frameCount, fps) {
  const capture = hook.createCaptureRenderer(EXPORT_WIDTH, EXPORT_HEIGHT);
  const duration = Number(hook.duration?.()) || 0;
  const pngBlobs = [];
  const pixels = [];
  try {
    for (let index = 0; index < frameCount; index += 1) {
      const time = duration > 0 ? duration * index / frameCount : index / fps;
      const rendered = hook.renderFrame(capture, time);
      pixels.push(rendered.pixels);
      pngBlobs.push(await canvasToBlob(capture.domElement));
    }
  } finally {
    capture.dispose?.();
  }
  return { pngBlobs, pixels };
}

async function exportAll(ui) {
  const frameCount = Math.max(1, Math.min(120, Number.parseInt(ui.frameCount.value, 10) || DEFAULT_FRAME_COUNT));
  const fps = Math.max(1, Math.min(60, Number.parseInt(ui.fps.value, 10) || DEFAULT_FPS));
  ui.frameCount.value = String(frameCount);
  ui.fps.value = String(fps);
  ui.checks.replaceChildren();
  ui.downloads.replaceChildren();
  ui.meta.textContent = `画布 ${EXPORT_WIDTH}×${EXPORT_HEIGHT} · ${frameCount} 帧 · ${fps} FPS · 角色点 (${CHARACTER_SAMPLE.x},${CHARACTER_SAMPLE.y}) · 背景点 (${BACKGROUND_SAMPLE.x},${BACKGROUND_SAMPLE.y}) · GIF alpha < ${GIF_ALPHA_THRESHOLD} 透明`;
  setBusy(ui, true);
  setSummary(ui, '编码中…');
  try {
    const hook = await waitForStageHook();
    const captured = await captureFrames(hook, frameCount, fps);
    const pngBytes = await Promise.all(captured.pngBlobs.map((blob) => blob.arrayBuffer().then((value) => new Uint8Array(value))));
    const apngBytes = buildApng(pngBytes, EXPORT_WIDTH, EXPORT_HEIGHT, fps);
    const gifBytes = buildGif(captured.pixels, EXPORT_WIDTH, EXPORT_HEIGHT, fps);
    const gifBlob = new Blob([gifBytes], { type: 'image/gif' });
    const apngBlob = new Blob([apngBytes], { type: 'image/apng' });
    const pngRecords = captured.pngBlobs.map((blob, index) => ({ name: `stage-frame-${String(index).padStart(3, '0')}.png`, blob }));
    const records = [{ name: 'stage-animation.gif', blob: gifBlob }, { name: 'stage-animation.apng', blob: apngBlob }, ...pngRecords];
    const gifInfo = parseGif(gifBytes);
    const apngInfo = parseApng(apngBytes);
    const gifPixels = await decodePixels(gifBlob, EXPORT_WIDTH, EXPORT_HEIGHT);
    const apngPixels = await decodePixels(apngBlob, EXPORT_WIDTH, EXPORT_HEIGHT);
    const pngPixels = await decodePixels(captured.pngBlobs[0], EXPORT_WIDTH, EXPORT_HEIGHT);
    const c2Rows = [...sampleRows('GIF', gifPixels), ...sampleRows('APNG', apngPixels), ...sampleRows('PNG 帧 000', pngPixels)];
    const resource = resourceCounts();
    const c1Pass = gifInfo.frames === frameCount && apngInfo.frameControls === frameCount && pngRecords.length === frameCount;
    const c2Pass = c2Rows.every((row) => row.status === 'pass');
    const c3Rows = [
      { status: gifInfo.signature === 'GIF89a' ? 'pass' : 'fail', label: 'GIF 签名', detail: gifInfo.signature },
      { status: gifInfo.width === EXPORT_WIDTH && gifInfo.height === EXPORT_HEIGHT ? 'pass' : 'fail', label: 'GIF 逻辑屏尺寸', detail: `${gifInfo.width}×${gifInfo.height}` },
      { status: gifInfo.frames === frameCount ? 'pass' : 'fail', label: 'GIF 帧数', detail: String(gifInfo.frames) },
      { status: gifInfo.transparentFrames === frameCount && gifInfo.transparentIndexes.has(0) ? 'pass' : 'fail', label: 'GIF 透明索引', detail: `${gifInfo.transparentFrames}/${frameCount} 帧 · index 0` },
    ];
    const c3Pass = c3Rows.every((row) => row.status === 'pass');
    const c4Pass = apngInfo.width === EXPORT_WIDTH && apngInfo.height === EXPORT_HEIGHT && apngInfo.declaredFrames === frameCount && apngInfo.frameControls === frameCount;
    const c5Pass = resource.crossOrigin === 0;
    addCheck(ui, 'c1', '帧数一致', c1Pass ? 'pass' : 'fail', `请求 ${frameCount} · GIF ${gifInfo.frames} · APNG ${apngInfo.frameControls} · PNG ${pngRecords.length}`);
    addCheck(ui, 'c2', '透明阳性/阴性对照', c2Pass ? 'pass' : 'fail', `角色点 (${CHARACTER_SAMPLE.x},${CHARACTER_SAMPLE.y}) · 背景点 (${BACKGROUND_SAMPLE.x},${BACKGROUND_SAMPLE.y}) · GIF alpha < ${GIF_ALPHA_THRESHOLD}`, c2Rows);
    addCheck(ui, 'c3', 'GIF 合法性', c3Pass ? 'pass' : 'fail', '', c3Rows);
    addCheck(ui, 'c4', 'APNG 合法性', c4Pass ? 'pass' : 'fail', `acTL=${apngInfo.declaredFrames} · fcTL=${apngInfo.frameControls}`);
    addCheck(ui, 'c5', '无外部请求', c5Pass ? 'pass' : 'fail', `同源 ${resource.sameOrigin} · 跨源 ${resource.crossOrigin}`);
    addCheck(ui, 'c6', '体积', 'pass', `GIF ${gifBlob.size} B · APNG ${apngBlob.size} B · PNG 序列 ${pngRecords.reduce((sum, record) => sum + record.blob.size, 0)} B`);
    addDownloadLinks(ui, records);
    const allPass = c1Pass && c2Pass && c3Pass && c4Pass && c5Pass;
    setSummary(ui, allPass ? '机械判据全绿' : '有判据失败', allPass ? 'pass' : 'fail');
  } catch (error) {
    addCheck(ui, 'export', '导出失败', 'fail', String(error));
    setSummary(ui, '导出失败', 'fail');
    console.warn('[stage/export]', error);
  } finally {
    setBusy(ui, false);
  }
}

function boot() {
  const ui = {
    button: document.getElementById('export-button'),
    frameCount: document.getElementById('export-frame-count'),
    fps: document.getElementById('export-fps'),
    summary: document.getElementById('export-summary'),
    meta: document.getElementById('export-meta'),
    checks: document.getElementById('export-check'),
    downloads: document.getElementById('export-downloads'),
  };
  if (Object.values(ui).some((node) => !node)) return;
  ui.button.addEventListener('click', () => exportAll(ui));
  ui.meta.textContent = `画布 ${EXPORT_WIDTH}×${EXPORT_HEIGHT} · 角色点 (${CHARACTER_SAMPLE.x},${CHARACTER_SAMPLE.y}) · 背景点 (${BACKGROUND_SAMPLE.x},${BACKGROUND_SAMPLE.y}) · GIF alpha < ${GIF_ALPHA_THRESHOLD} 透明`;
}

boot();
