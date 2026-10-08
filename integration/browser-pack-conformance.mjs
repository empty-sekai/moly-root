// Execute the actual Rust/WASM PackReader and browser infrastructure against
// Python-generated stores. No renderer mock is used for asset IO.
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";

const options = {};
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i];
  if (!key?.startsWith("--") || !process.argv[i + 1]) throw new Error("Arguments are --name value pairs");
  options[key.slice(2)] = path.resolve(process.argv[i + 1]);
}
for (const key of ["consumer", "wasm", "fixtures", "output"])
  if (!options[key]) throw new Error(`Missing --${key}`);
const require = createRequire(path.join(options.consumer, "web/package.json"));
const { chromium } = require("playwright");
const sha = value => createHash("sha256").update(value).digest("hex");
const publicIndex = JSON.parse(fs.readFileSync(path.join(options.fixtures, "fixtures.json")));
const logical = ["fixture-models/mysekai__public/model.glb", "sd_104.glb", "site/scenes/site01/site01.glb",
  "ui/action-icon/Talk.png", "phenomena/audio/public/voice.wav", "phenomena/001_sunny/config.json",
  "fixture-thumbnails/mysekai__public.png", "manifest.json", "ui/action-icon/\ue000.png", "ui/action-icon/😀.png"];
for (const boundary of ["ui/action-icon/large.png", "fixture-models/mysekai__public/zeros.json"])
  if (publicIndex.expected.cn[boundary]) logical.push(boundary);
const selections = Object.entries(publicIndex.catalogs).map(([region, row]) => ({ region, id: row.id,
  probes: logical.map(name => ({ path: name, ...publicIndex.expected[region][name] })) }));
const scenarios = [{ name: "public", store: path.join(options.fixtures, "store"), selections,
  legacy: path.join(options.fixtures, "legacy") }];
if (options["real-report"] || options.store) {
  if (!options["real-report"] || !options.store) throw new Error("--real-report and --store are required together");
  const data = JSON.parse(fs.readFileSync(options["real-report"]));
  scenarios.push({ name: "real-cn-jp", store: options.store, selections: Object.entries(data.regions).sort(([a], [b]) => a.localeCompare(b))
    .map(([region, row]) => ({ region, id: row.catalog_sha256, probes: row.probes })) });
}

async function run(scenario, browser) {
  const requests = new Map(); let badShared = null;
  const first = scenario.selections[0];
  const catalog = JSON.parse(fs.readFileSync(path.join(scenario.store, "catalogs", first.id + ".json")));
  const probe = first.probes.find(row => row.path.endsWith(".glb"));
  const pack = catalog.packages.find(row => row.paths.includes(probe.path));
  const manifest = JSON.parse(fs.readFileSync(path.join(scenario.store, pack.manifest)));
  const entry = manifest.entries.find(row => row.path === probe.path);
  const faultTargets = { blob: "blobs/" + entry.blob, package: pack.manifest, catalog: `catalogs/${first.id}.json` };
  const faults = ["missing-blob", "corrupt-blob", "missing-package", "corrupt-package", "missing-catalog", "corrupt-catalog"];
  const server = http.createServer((request, response) => {
    try {
      const url = new URL(request.url, "http://localhost");
      requests.set(url.pathname, (requests.get(url.pathname) ?? 0) + 1);
      if (url.pathname === "/favicon.ico") { response.writeHead(204); response.end(); return; }
      if (url.pathname === "/moly/") {
        response.writeHead(200, { "Content-Type": "text/html", "Cache-Control": "no-store" });
        response.end("<!doctype html><html><head><meta charset=utf-8><title>Moly v2 asset conformance</title></head><body><h1>Moly v2 asset conformance</h1><div id=images></div><pre id=report></pre></body></html>"); return;
      }
      let base, relative, fault = null, immutable = false;
      if (url.pathname.startsWith("/moly/asset-store/")) {
        base = scenario.store; relative = url.pathname.slice("/moly/asset-store/".length);
        immutable = /^(?:blobs\/[a-f0-9]{2}\/[a-f0-9]{64}\.(?:bin|gzz|brz|br)|(?:packages|catalogs)\/[a-f0-9]{64}\.json)$/.test(relative);
      } else if (url.pathname.startsWith("/moly/fault/")) {
        const parts = url.pathname.slice("/moly/fault/".length).split("/");
        fault = parts.shift(); relative = parts.join("/"); base = scenario.store;
      } else if (scenario.legacy && url.pathname.startsWith("/moly/legacy/")) {
        base = scenario.legacy; relative = url.pathname.slice("/moly/legacy/".length);
      } else if (url.pathname.startsWith("/moly/probe/")) {
        base = options.wasm; relative = url.pathname.slice("/moly/probe/".length);
      } else {
        base = path.join(options.consumer, "web"); relative = url.pathname.slice("/moly/".length);
        if (!["cache-worker.mjs", "asset-pack-client.mjs", "asset-pack-images.mjs"].includes(relative)) throw new Error("Unavailable test route");
      }
      relative = relative.split("/").map(decodeURIComponent).join("/");
      if (!relative || relative.split("/").some(part => !part || part === "." || part === ".." || part.includes("\\"))) throw new Error("Invalid route");
      const target = path.resolve(base, relative);
      if (!target.startsWith(path.resolve(base) + path.sep)) throw new Error("Route escaped test source");
      if (fault?.startsWith("missing-") && relative === faultTargets[fault.slice(8)]) {
        response.writeHead(404, { "Cache-Control": "no-store" }); response.end(); return;
      }
      let bytes = fs.readFileSync(target);
      if ((fault?.startsWith("corrupt-") && relative === faultTargets[fault.slice(8)]) || url.pathname === badShared)
        bytes = Buffer.concat([bytes, Buffer.from(" ")]);
      const type = relative.endsWith(".wasm") ? "application/wasm" : /\.(?:mjs|js)$/.test(relative)
        ? "text/javascript" : relative.endsWith(".json") ? "application/json" : "application/octet-stream";
      response.writeHead(200, { "Content-Type": type, "Content-Length": String(bytes.length),
        "X-Moly-Decoded-Bytes": String(bytes.length), "Cache-Control": immutable ? "public, max-age=31536000, immutable, no-transform" : "no-store" });
      response.end(bytes);
    } catch (error) { response.writeHead(404, { "Cache-Control": "no-store" }); response.end(String(error.message)); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const context = await browser.newContext(); const page = await context.newPage();
  const deadline = setTimeout(() => { console.error("Browser conformance exceeded 120 s", JSON.stringify([...requests])); context.close().catch(() => {}); }, 120000);
  const pageErrors = []; page.on("pageerror", error => { pageErrors.push(error.stack || error.message); console.error("PAGEERROR", error.stack || error.message); context.close().catch(() => {}); });
  page.on("console", message => { if (message.type() === "error" || message.text().startsWith("TRACE")) console.log("BROWSER", message.text()); });
  try {
    await page.goto(origin + "/moly/");
    console.log("PHASE", 'actual reader IO');
    const reads = await page.evaluate(async ({ selections }) => {
      const { PackClient, packDigest } = await import("/moly/asset-pack-client.mjs");
      console.log("TRACE wasm init"); const wasm = await import("/moly/probe/moly_pack_probe.js"); await wasm.default(); console.log("TRACE wasm initialized");
      window.probe = wasm;
      const result = [];
      for (const selection of selections) {
        const root = location.origin + "/moly/asset-store/", client = new PackClient(root, selection.id);
        for (const row of selection.probes) {
          console.log("TRACE wasm read", selection.region, row.path);
          const fromRust = await wasm.read_packed(root, selection.id, row.path);
          console.log("TRACE wasm received", fromRust.length);
          const fromJS = await client.read(row.path);
          const hash = await packDigest(fromRust), jsHash = await packDigest(fromJS);
          if (fromRust.length !== row.bytes || hash !== row.sha256 || jsHash !== row.sha256)
            throw new Error(`Cross-language source mismatch: ${selection.region}/${row.path}`);
          if (row.path.endsWith(".glb") && new DataView(fromRust.buffer, fromRust.byteOffset).getUint32(0, true) !== 0x46546c67)
            throw new Error("GLB did not decode to valid magic");
          if (row.path.endsWith(".json")) JSON.parse(new TextDecoder().decode(fromRust));
          result.push({ region: selection.region, path: row.path, bytes: fromRust.length, sha256: hash });
        }
      }
      return result;
    }, { selections: scenario.selections });
    console.log("PHASE", 'corruption failures');
    const corruptions = await page.evaluate(async ({ faults, first, logical }) => {
      const { PackClient } = await import("/moly/asset-pack-client.mjs"); const results = [];
      for (const mode of faults) {
        const root = `${location.origin}/moly/fault/${mode}/`;
        for (const reader of ["wasm", "javascript"]) {
          let message = null;
          try { if (reader === "wasm") await window.probe.read_packed(root, first.id, logical);
            else await new PackClient(root, first.id).read(logical); }
          catch (error) { message = String(error); }
          if (!message) throw new Error(`${reader} silently accepted ${mode}`);
          results.push({ reader, mode, error: message });
        }
      }
      return results;
    }, { faults, first, logical: probe.path });
    console.log("PHASE", 'legacy migration boundary');
    let legacyRefusal = null;
    if (scenario.legacy) legacyRefusal = await page.evaluate(async () => {
      const { PackClient } = await import("/moly/asset-pack-client.mjs");
      const root = location.origin + "/moly/legacy/", result = [];
      for (const reader of ["wasm", "javascript"]) {
        let message = null;
        try { if (reader === "wasm") await window.probe.read_packed(root, "", "sd_104.glb");
          else await new PackClient(root).read("sd_104.glb"); }
        catch (error) { message = String(error); }
        if (!message) throw new Error(`${reader} silently accepted a legacy v1 pack`);
        result.push({ reader, error: message });
      }
      return result;
    });
    console.log("PHASE", 'DOM image');
    const image = first.probes.find(row => row.path.endsWith(".png"));
    const visual = await page.evaluate(async ({ first, image }) => {
      const { PackClient } = await import("/moly/asset-pack-client.mjs");
      const { PackedImages } = await import("/moly/asset-pack-images.mjs");
      const images = new PackedImages(new PackClient(location.origin + "/moly/asset-store/", first.id));
      const element = document.createElement("img"); document.getElementById("images").append(element);
      const loaded = new Promise((resolve, reject) => { element.onload = resolve; element.onerror = () => reject(new Error("Packed DOM image failed")); });
      images.bind(element, image.path); await loaded;
      const result = { path: image.path, width: element.naturalWidth, height: element.naturalHeight };
      if (!result.width || !result.height) throw new Error("Packed image has no decoded pixels");
      images.dispose(); return result;
    }, { first, image });
    console.log("PHASE", 'browser audio decoding');
    const audio = await page.evaluate(async selections => {
      const rows = [];
      for (const selection of selections) {
        const source = selection.probes.find(row => /\.(?:wav|ogg)$/.test(row.path));
        if (!source) continue;
        const bytes = await window.probe.read_packed(location.origin + "/moly/asset-store/", selection.id, source.path);
        const decoded = await new OfflineAudioContext(2, 1, 48000).decodeAudioData(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
        if (!decoded.length || !decoded.numberOfChannels || !Number.isFinite(decoded.duration)) throw new Error("Packed audio failed browser decoding");
        rows.push({ region: selection.region, path: source.path, frames: decoded.length,
          sampleRate: decoded.sampleRate, channels: decoded.numberOfChannels, durationSeconds: decoded.duration });
      }
      return rows;
    }, scenario.selections);
    console.log("PHASE", 'real service worker registration');
    // Real service worker, not a CacheStorage-only mock.
    await page.evaluate(async () => {
      await navigator.serviceWorker.register("/moly/cache-worker.mjs", { type: "module", scope: "/moly/" });
      await navigator.serviceWorker.ready;
      if (!navigator.serviceWorker.controller) await new Promise(resolve => navigator.serviceWorker.addEventListener("controllerchange", resolve, { once: true }));
      window.cacheCommand = (type, enabled) => new Promise((resolve, reject) => {
        const channel = new MessageChannel(), timeout = setTimeout(() => reject(new Error("Cache command timed out")), 15000);
        channel.port1.onmessage = ({ data }) => { clearTimeout(timeout); channel.port1.close(); data.ok ? resolve(data) : reject(new Error("Cache command rejected")); };
        navigator.serviceWorker.controller.postMessage({ source: "moly-cache-host", schemaVersion: 1, type, enabled }, [channel.port2]);
      });
      await window.cacheCommand("retain", true); await window.cacheCommand("clear");
    });
    console.log("PHASE", 'shared object selection');
    const shared = await page.evaluate(async selections => {
      const { PackClient } = await import("/moly/asset-pack-client.mjs");
      const clients = selections.slice(0,2).map(row => new PackClient(location.origin + "/moly/asset-store/", row.id));
      const a = await clients[0].selection(), b = await clients[1].selection();
      for (const name of a.owners.keys()) {
        if (!b.owners.has(name)) continue;
        const one = await clients[0].resolve(name), two = await clients[1].resolve(name);
        if (one.url === two.url && one.blob_bytes > 0 && one.blob_bytes < 1048576) return { path: name, url: one.url, bytes: one.blob_bytes,
          contentSha256: one.content_sha256, contentBytes: one.bytes };
      }
      throw new Error("No measured shared object for cache test");
    }, scenario.selections);
    await page.evaluate(() => window.cacheCommand("clear"));
    console.log("PHASE", 'cache retention');
    const cache = await page.evaluate(async shared => {
      const one = await fetch(shared.url, { cache: "no-store", headers: { "X-Moly-Required": "1" } }); await one.arrayBuffer();
      const first = await window.cacheCommand("query");
      const two = await fetch(shared.url, { cache: "no-store" }); await two.arrayBuffer();
      const second = await window.cacheCommand("query");
      if (first.entries !== 1 || second.entries !== 1 || first.bytes !== shared.bytes || two.headers.get("X-Moly-Cache") !== "retained")
        throw new Error("Shared immutable response was not retained exactly once");
      for (let i = 0; i < 2; i++) await (await fetch("/moly/asset-store/asset-packs.json", { cache: "no-store" })).arrayBuffer();
      const afterSelectors = await window.cacheCommand("query");
      if (afterSelectors.entries !== 1) throw new Error("Mutable selector entered immutable cache");
      await window.cacheCommand("clear");
      return { sharedPath: shared.path, first, second, mutableSelectorsExcluded: true };
    }, shared);
    console.log("PHASE", 'damaged cache response');
    badShared = new URL(shared.url).pathname;
    const damagedRetention = await page.evaluate(async shared => {
      await (await fetch(shared.url, { cache: "no-store" })).arrayBuffer();
      const state = await window.cacheCommand("query");
      if (state.entries !== 0) throw new Error("Damaged immutable response was cached");
      return { badChecksumNotRetained: true, state };
    }, shared);
    badShared = null;
    console.log("PHASE", 'offline multi-region logical resolution');
    const retained = await page.evaluate(async ({ shared, selections }) => {
      const { PackClient, packDigest } = await import("/moly/asset-pack-client.mjs");
      await window.cacheCommand("clear");
      const counts = [];
      for (const selection of selections.slice(0, 2)) {
        const client = new PackClient(location.origin + "/moly/asset-store/", selection.id, { required: true });
        if (await packDigest(await client.read(shared.path)) !== shared.contentSha256) throw new Error("Warm shared content changed");
        counts.push(await window.cacheCommand("query"));
      }
      return counts;
    }, { shared, selections: scenario.selections });
    await context.setOffline(true);
    let offline;
    try {
      offline = await page.evaluate(async ({ shared, selections }) => {
        const { PackClient, packDigest } = await import("/moly/asset-pack-client.mjs"), rows = [];
        const root = location.origin + "/moly/asset-store/";
        for (const selection of selections.slice(0, 2)) {
          const rust = await window.probe.read_packed(root, selection.id, shared.path);
          const js = await new PackClient(root, selection.id).read(shared.path);
          if (await packDigest(rust) !== shared.contentSha256 || await packDigest(js) !== shared.contentSha256)
            throw new Error("Offline release selected incorrect shared content");
          rows.push({ region: selection.region, path: shared.path, decodedBytes: rust.length, sha256: shared.contentSha256 });
        }
        return rows;
      }, { shared, selections: scenario.selections });
    } finally { await context.setOffline(false); }
    assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
    const report = { name: scenario.name, reads, corruptionFailures: corruptions, legacyRefusal, visual, audio, cache, damagedRetention,
      selectedCatalogs: scenario.selections.map(({ region, id }) => ({ region, sha256: id })),
      offlineRegionSwitch: { retained, reads: offline, actualNetworkDisabled: true },
      sourcePathsRead: reads.length, actualWasmReader: true, actualServiceWorker: true, browserConsoleErrors: pageErrors };
    await page.evaluate(value => { document.getElementById("report").textContent = JSON.stringify(value, null, 2); },
      { scenario: scenario.name, sourcePathsRead: reads.length, corruptionsRejected: corruptions.length, sharedCache: true, image: visual });
    await page.screenshot({ path: path.join(path.dirname(options.output), `${scenario.name}-asset-conformance.png`), fullPage: true });
    return report;
  } finally { clearTimeout(deadline); await context.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}

fs.mkdirSync(path.dirname(options.output), { recursive: true });
const browser = await chromium.launch({ headless: true, ...(options.browser ? { executablePath: options.browser } : {}) });
try {
  const results = [];
  for (const scenario of scenarios) { console.log("RUN", scenario.name); results.push(await run(scenario, browser)); console.log("PASS", scenario.name); }
  const report = { schema: "moly-browser-asset-conformance/1", browser: browser.version(),
    wasmSha256: sha(fs.readFileSync(path.join(options.wasm, "moly_pack_probe_bg.wasm"))),
    qualification: "Actual PackReader asset IO, pinned publication objects, DOM image decoding and real Chromium service worker. Not a full game renderer/playback or visual QA claim.", results };
  fs.writeFileSync(options.output, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ scenarios: results.length, reads: results.reduce((n,row)=>n+row.reads.length,0), output: options.output }));
} finally { await browser.close(); }
