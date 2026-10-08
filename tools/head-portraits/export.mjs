#!/usr/bin/env node
// Local, source-locked batch driver for the real chara viewer.
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { spawn } from 'node:child_process';

export const GENERATOR = 'moly-root-chara-head-v1';
const toolRoot = path.dirname(fileURLToPath(import.meta.url));
const viewerRoot = path.resolve(toolRoot, '../../examples/viewer');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const sorted = values => Object.fromEntries([...values].sort(([a], [b]) => a.localeCompare(b, 'en')));

export async function localFile(root, relative) {
  if (typeof relative !== 'string' || !relative || relative.includes('\\') ||
      relative.split('/').some(part => part === '..' || part === '') || path.isAbsolute(relative)) {
    throw new Error(`Expected a relative asset path: ${relative}`);
  }
  const resolvedRoot = await fs.realpath(root);
  const file = await fs.realpath(path.resolve(resolvedRoot, relative));
  if (!file.startsWith(resolvedRoot + path.sep)) throw new Error(`Asset escapes source directory: ${relative}`);
  return file;
}

export async function sourceRoster(source, region, version, units) {
  const manifest = JSON.parse(await fs.readFile(await localFile(source, 'manifest.json'), 'utf8'));
  const metadata = JSON.parse(await fs.readFile(await localFile(source, 'mysekai-fixtures.json'), 'utf8'));
  if (!region || metadata.region !== region) throw new Error(`Source region ${metadata.region} does not match ${region}`);
  if (!metadata.gameVersion || (version && metadata.gameVersion !== version)) throw new Error('Source game version mismatch');
  if (!Array.isArray(manifest.units) || !manifest.units.length) throw new Error('Source manifest has no units');
  const ids = new Set();
  const codes = new Set();
  const rows = [];
  for (const entry of manifest.units) {
    const code = Number(entry.unit);
    if (!Number.isSafeInteger(code) || code <= 100 || codes.has(code)) throw new Error(`Invalid/duplicate unit code: ${entry.unit}`);
    codes.add(code);
    const rig = JSON.parse(await fs.readFile(await localFile(source, entry.rig), 'utf8'));
    const unit = rig.unitId ?? code - 100;
    if (!Number.isSafeInteger(unit) || unit <= 0 || ids.has(unit)) throw new Error(`Invalid/duplicate rig unit: ${unit}`);
    ids.add(unit);
    await localFile(source, entry.glb);
    rows.push({ unit, unitCode: code, model: entry.glb, rig: entry.rig });
  }
  const requested = units ? units.split(',').map(Number) : null;
  if (requested && (!requested.length || new Set(requested).size !== requested.length || requested.some(id => !ids.has(id)))) {
    throw new Error('--units must contain unique unit IDs present in the source manifest');
  }
  return { version: metadata.gameVersion, rosterCount: rows.length,
    rows: rows.filter(row => !requested || requested.includes(row.unit)).sort((a, b) => a.unit - b.unit) };
}

async function snapshot(root, names) {
  const result = new Map();
  for (const name of names) result.set(name, sha(await fs.readFile(await localFile(root, name))));
  return result;
}

async function verifySnapshot(root, entries) {
  for (const [name, hash] of entries) {
    if (sha(await fs.readFile(await localFile(root, name))) !== hash) throw new Error(`Input changed during capture: ${name}`);
  }
}

const mime = { '.js': 'text/javascript', '.html': 'text/html', '.css': 'text/css', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.glb': 'model/gltf-binary', '.wav': 'audio/wav' };

async function startServer(source, sourceHashes, rendererHashes) {
  const faults = [];
  const missing = new Set();
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost');
      const isSource = url.pathname.startsWith('/source/');
      if (!isSource && !url.pathname.startsWith('/viewer/')) { response.writeHead(404).end(); return; }
      const name = decodeURIComponent(url.pathname.slice(8)) || 'index.html';
      const root = isSource ? source : viewerRoot;
      const hashes = isSource ? sourceHashes : rendererHashes;
      const bytes = await fs.readFile(await localFile(root, name));
      const hash = sha(bytes);
      if (hashes.has(name) && hashes.get(name) !== hash) throw new Error(`Input changed during capture: ${name}`);
      hashes.set(name, hash);
      response.writeHead(200, { 'Content-Type': mime[path.extname(name)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
      response.end(bytes);
    } catch (error) {
      if (error.code === 'ENOENT') missing.add(new URL(request.url, 'http://localhost').pathname);
      else faults.push(String(error));
      response.writeHead(error.code === 'ENOENT' ? 404 : 500).end();
    }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  return { server, faults, missing, base: `http://127.0.0.1:${server.address().port}` };
}

async function finalize(python, source, out) {
  await new Promise((resolve, reject) => {
    const child = spawn(python, [path.join(toolRoot, 'finalize.py'), '--source', source, '--out', out], { stdio: 'inherit' });
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolve() : reject(new Error(`Portrait validation failed (${code})`)));
  });
}

export async function exportHeads(options) {
  const source = await fs.realpath(options.source);
  const out = path.resolve(options.out);
  const roster = await sourceRoster(source, options.region, options.version, options.units);
  const sourceHashes = await snapshot(source, ['manifest.json', 'mysekai-fixtures.json', 'facial-tables.json',
    ...roster.rows.flatMap(row => [row.model, row.rig])]);
  const rendererHashes = await snapshot(viewerRoot,
    (await fs.readdir(viewerRoot)).filter(name => /\.(js|html|css)$/.test(name)).sort());
  const toolingHashes = await snapshot(toolRoot, ['export.mjs', 'finalize.py', 'package.json', 'package-lock.json', 'requirements.txt']);
  await fs.mkdir(out, { recursive: true });
  if ((await fs.readdir(out)).length) throw new Error(`Output must be an empty directory: ${out}`);
  const { chromium } = await import('playwright');
  const service = await startServer(source, sourceHashes, rendererHashes);
  let browser;
  let page;
  const errors = [];
  try {
    browser = await chromium.launch({ headless: true, ...(options.channel ? { channel: options.channel } : {}) });
    const browserVersion = browser.version();
    page = await browser.newPage({ viewport: { width: 1024, height: 900 }, deviceScaleFactor: 1 });
    page.setDefaultTimeout(40000);
    await page.route('**/*', route => {
      const url = route.request().url();
      if (url.startsWith(service.base + '/') || url.startsWith('data:') || url.startsWith('blob:')) return route.continue();
      errors.push(`External resource is not a local source input: ${url}`);
      return route.abort();
    });
    page.on('pageerror', error => errors.push(String(error)));
    const portraits = [];
    let rendering;
    for (const row of roster.rows) {
      await page.goto(`${service.base}/viewer/index.html?base=/source&unit=${row.unitCode}&portrait=1&perf=0&emote=0&env=0`, { waitUntil: 'load' });
      await page.waitForFunction(unit => window.app?.portrait?.unit === unit, row.unit);
      const result = await page.evaluate(() => new Promise((resolve, reject) => {
        const app = window.app;
        const timer = setTimeout(() => { app.onShot = null; reject(new Error('Viewer did not render a capture frame')); }, 10000);
        app.onShot = url => {
          clearTimeout(timer);
          app.onShot = null;
          const gl = app.renderer.getContext();
          const debug = gl.getExtension('WEBGL_debug_renderer_info');
          resolve({ url, diagnostics: app.portrait, shaderErrors: app.shaderErrors,
            data: { rig: app.dataInfo.rig, tablesFallback: app.dataInfo.tablesFallback, clipCount: app.clipByName.size },
            facial: { eye: app.facial.currentEyeIndex, mouth: app.facial.currentMouthIndex },
            rendering: { webgl: gl.getParameter(gl.VERSION), renderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER) } });
        };
        app.requestScreenshot();
      }));
      if (errors.length || service.faults.length || result.shaderErrors.length) throw new Error([...errors, ...service.faults, ...result.shaderErrors].join('\n'));
      if (!result.data.rig || result.data.tablesFallback || !result.data.clipCount) throw new Error(`Missing faithful pose/facial input for unit ${row.unit}`);
      if (rendering && JSON.stringify(rendering) !== JSON.stringify(result.rendering)) throw new Error('WebGL rendering environment changed during batch');
      rendering = result.rendering;
      if (!result.url.startsWith('data:image/png;base64,')) throw new Error('Viewer did not return a PNG');
      const bytes = Buffer.from(result.url.slice('data:image/png;base64,'.length), 'base64');
      const sourceFile = `unit-${row.unit}.source.png`;
      await fs.writeFile(path.join(out, sourceFile), bytes);
      portraits.push({ ...row, sourceFile, sourceSha256: sha(bytes), modelSha256: sourceHashes.get(row.model),
        rigSha256: sourceHashes.get(row.rig), diagnostics: result.diagnostics, facial: result.facial });
      console.log(`captured ${options.region} unit ${row.unit} (${portraits.length}/${roster.rows.length})`);
    }
    await browser.close();
    browser = null;
    await verifySnapshot(source, sourceHashes);
    await verifySnapshot(viewerRoot, rendererHashes);
    await verifySnapshot(toolRoot, toolingHashes);
    const capture = { generator: GENERATOR, region: options.region, version: roster.version, expected: roster.rows.length,
      rosterCount: roster.rosterCount, portraits };
    // No timestamp, absolute machine path, port or per-run identifier enters the reproducible manifest.
    capture.rendering = rendering;
    capture.browserVersion = browserVersion;
    capture.sourceSha256 = sorted(sourceHashes);
    capture.rendererSha256 = sorted(rendererHashes);
    capture.toolingSha256 = sorted(toolingHashes);
    capture.optionalMissingResources = [...service.missing].sort();
    await fs.writeFile(path.join(out, 'capture.json'), JSON.stringify(capture, null, 2) + '\n');
    await finalize(options.python || 'python', source, out);
  } catch (error) {
    if (page && browser) await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
    throw error;
  } finally {
    await browser?.close();
    await new Promise(resolve => service.server.close(resolve));
  }
}

async function main() {
  const { values } = parseArgs({ options: {
    source: { type: 'string' }, out: { type: 'string' }, region: { type: 'string' }, version: { type: 'string' },
    units: { type: 'string' }, channel: { type: 'string' }, python: { type: 'string' }, help: { type: 'boolean' },
  } });
  if (values.help) {
    console.log('node tools/head-portraits/export.mjs --source <runtime-dir> --out <empty-dir> --region <cn|jp> [--version <expected-version>] [--units 1,17] [--channel chrome] [--python python]');
    return;
  }
  for (const key of ['source', 'out', 'region']) if (!values[key]) throw new Error(`Missing --${key}`);
  await exportHeads(values);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(error => { console.error(error); process.exitCode = 1; });
}
