// Applies the meshopt recipe recorded in transforms.toml (this directory) to
// every self-contained .glb under --src, writing the transformed bytes to
// --out at the same relative path. pack/build.py's --xf-overlay mechanism
// then substitutes these bytes for the --src originals when packing.
//
// Nothing here hardcodes a bit width or a topology switch -- every number
// comes from transforms.toml, read at startup, so "which settings produced
// this pack" is always answerable from the one file both this script and
// build.py read.
//
// Usage:
//   node transform_glb.mjs --src <dir> --out <dir>
//
// Self-contained vs external-texture, and why the latter is excluded:
//   A glb is treated as self-contained iff every entry in its JSON chunk's
//   `images[]` either has no `uri`, or a `uri` that starts with `data:`
//   (i.e. the texture bytes are inlined in the glb itself, not a separate
//   sibling file the deploy tree also serves). Re-encoding an
//   external-texture glb with gltf-transform's NodeIO would inline that
//   texture into the glb's own binary chunk, duplicating a PNG that today is
//   shared (one blob, many referencing entries) into a private copy per glb
//   -- destroying the corpus's content-addressed texture dedup. So those
//   files are simply left out of --out; build.py's overlay only replaces
//   paths present in the overlay directory, and every path missing from it
//   keeps flowing through unmodified with xf=null.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TRANSFORMS_TOML = path.join(HERE, 'transforms.toml');

// This repo (moly-root) has no node_modules of its own for @gltf-transform/*
// or meshoptimizer, and no network access is assumed here. Those npm
// packages are already installed (and already proven to work for this exact
// quantize + EXT_meshopt_compression path) at F:/mysekai/moly/_work/quant,
// alongside run_quant.js. ESM `import` does not consult NODE_PATH, so a
// plain `import '@gltf-transform/core'` from this file's own directory would
// not find them; `createRequire` with a synthetic base path inside that
// directory runs Node's classic (NODE_PATH-agnostic) CJS resolution instead,
// which walks up from the given path looking for node_modules -- finding it
// immediately, since the base path IS that directory. This reads the
// packages installed there; it does not import run_quant.js itself.
const QUANT_DIR = 'F:/mysekai/moly/_work/quant';
const requireFromQuant = createRequire(path.join(QUANT_DIR, 'noop.cjs'));

const { NodeIO } = requireFromQuant('@gltf-transform/core');
const { EXTMeshoptCompression, KHRMeshQuantization } = requireFromQuant('@gltf-transform/extensions');
const { quantize } = requireFromQuant('@gltf-transform/functions');
const { MeshoptDecoder, MeshoptEncoder } = requireFromQuant('meshoptimizer');

const REQUIRED_PARAM_KEYS = [
  'quantize_position', 'quantize_normal', 'quantize_texcoord',
  'weld', 'simplify', 'reorder',
];

// Reads transforms.toml via Python's tomllib (child process) rather than
// hand-rolling a TOML parser: pack/build.py (Python) reads the very same
// file with tomllib to build the manifest's `transforms` field, and using
// the identical parser for both readers rules out the two ever disagreeing
// about what transforms.toml says.
function readTransformsToml(tomlPath) {
  if (!fs.existsSync(tomlPath)) {
    throw new Error(`transforms.toml not found: ${tomlPath}`);
  }
  const pyScript =
    "import tomllib, json, sys\n" +
    "with open(sys.argv[1], 'rb') as fh:\n" +
    "    print(json.dumps(tomllib.load(fh)))\n";
  let out;
  try {
    out = execFileSync('python', ['-c', pyScript, tomlPath], { encoding: 'utf8' });
  } catch (e) {
    throw new Error(`failed to parse ${tomlPath} via python -c tomllib: ${e.message}`);
  }
  return JSON.parse(out);
}

function loadMeshoptRecipe() {
  const toml = readTransformsToml(TRANSFORMS_TOML);
  const recipe = toml.meshopt;
  if (!recipe) throw new Error(`transforms.toml has no [meshopt] table: ${TRANSFORMS_TOML}`);
  for (const key of ['tool', 'tool_version', 'params']) {
    if (!(key in recipe)) throw new Error(`transforms.toml [meshopt] is missing required key: ${key}`);
  }
  const params = recipe.params;
  const missing = REQUIRED_PARAM_KEYS.filter((k) => !(k in params));
  if (missing.length) {
    throw new Error(`transforms.toml [meshopt.params] is missing required key(s): ${missing.join(', ')}`);
  }
  // Explicit, named refusal -- not a silent no-op -- if the recipe ever asks
  // for a topology change this script does not implement. This round
  // supports only weld=false simplify=false reorder=false.
  if (params.weld || params.simplify || params.reorder) {
    throw new Error(
      `transforms.toml [meshopt.params] has weld=${params.weld} simplify=${params.simplify} ` +
      `reorder=${params.reorder}; transform_glb.mjs only supports weld=false simplify=false ` +
      `reorder=false this round -- refusing to run rather than silently ignore a requested ` +
      `topology change`);
  }
  return recipe;
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--src') args.src = argv[++i];
    else if (argv[i] === '--out') args.out = argv[++i];
    else throw new Error(`unknown arg: ${argv[i]} (usage: --src <dir> --out <dir>)`);
  }
  if (!args.src || !args.out) throw new Error('usage: node transform_glb.mjs --src <dir> --out <dir>');
  return args;
}

const GLB_MAGIC = 0x46546c67;
const CHUNK_JSON = 0x4e4f534a;

// Reads only the JSON chunk of a GLB (cheap self-contained/external-texture
// classification) without pulling gltf-transform in for this check.
function readGlbJsonChunk(filePath) {
  const buf = fs.readFileSync(filePath);
  if (buf.readUInt32LE(0) !== GLB_MAGIC) throw new Error(`not a GLB (bad magic): ${filePath}`);
  const totalLength = buf.readUInt32LE(8);
  let offset = 12;
  while (offset < totalLength) {
    const chunkLength = buf.readUInt32LE(offset);
    const chunkType = buf.readUInt32LE(offset + 4);
    if (chunkType === CHUNK_JSON) {
      return JSON.parse(buf.subarray(offset + 8, offset + 8 + chunkLength).toString('utf8'));
    }
    offset += 8 + chunkLength;
  }
  throw new Error(`no JSON chunk found: ${filePath}`);
}

function isSelfContained(json) {
  const images = json.images || [];
  for (const img of images) {
    if (typeof img.uri === 'string' && !img.uri.startsWith('data:')) return false;
  }
  return true;
}

function findGlbFiles(root) {
  const out = [];
  (function walk(dir) {
    for (const dirent of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, dirent.name);
      if (dirent.isDirectory()) walk(p);
      else if (dirent.isFile() && dirent.name.toLowerCase().endsWith('.glb')) out.push(p);
    }
  })(root);
  return out.sort();
}

async function main() {
  const { src, out } = parseArgs(process.argv.slice(2));
  const srcRoot = path.resolve(src);
  const outRoot = path.resolve(out);

  // Guards against the MSYS-path-silently-resolves-to-nothing failure mode:
  // a path like /f/... looks valid to fs.existsSync's caller but is not a
  // directory that ever holds real files on Windows.
  if (!fs.existsSync(srcRoot) || !fs.statSync(srcRoot).isDirectory()) {
    throw new Error(
      `--src is not a directory: ${srcRoot} (pass a drive-qualified path such as F:/... or F:\\..., ` +
      `not an MSYS-style /f/... path -- the latter resolves to nothing on Windows without raising)`);
  }

  const recipe = loadMeshoptRecipe();
  const params = recipe.params;
  console.log(`recipe: ${recipe.tool} (${recipe.tool_version})`);
  console.log(`params: ${JSON.stringify(params)}`);

  const allGlbs = findGlbFiles(srcRoot);
  const included = [];
  const excluded = [];
  for (const abs of allGlbs) {
    const rel = path.relative(srcRoot, abs).split(path.sep).join('/');
    let json;
    try {
      json = readGlbJsonChunk(abs);
    } catch (e) {
      throw new Error(`failed to read GLB JSON chunk for ${rel}: ${e.message}`);
    }
    if (isSelfContained(json)) included.push(rel);
    else excluded.push(rel);
  }

  console.log(`glb files found under --src: ${allGlbs.length}`);
  console.log(`self-contained (included):   ${included.length}`);
  console.log(`external-texture (excluded): ${excluded.length}`);

  await MeshoptDecoder.ready;
  await MeshoptEncoder.ready;
  const io = new NodeIO()
    .registerExtensions([EXTMeshoptCompression, KHRMeshQuantization])
    .registerDependencies({
      'meshopt.decoder': MeshoptDecoder,
      'meshopt.encoder': MeshoptEncoder,
    });

  fs.mkdirSync(outRoot, { recursive: true });

  let done = 0;
  const t0 = Date.now();
  for (const rel of included) {
    const inAbs = path.join(srcRoot, ...rel.split('/'));
    const outAbs = path.join(outRoot, ...rel.split('/'));
    fs.mkdirSync(path.dirname(outAbs), { recursive: true });

    const origBuf = fs.readFileSync(inAbs);
    const doc = await io.readBinary(new Uint8Array(origBuf));

    // quantize() only -- no weld(), simplify(), or reorder() call anywhere
    // in this script, per transforms.toml's (enforced, checked above)
    // weld=false simplify=false reorder=false.
    await doc.transform(
      quantize({
        quantizePosition: params.quantize_position,
        quantizeNormal: params.quantize_normal,
        quantizeTexcoord: params.quantize_texcoord,
        cleanup: true,
      }),
    );

    // EXT_meshopt_compression's default encoder options are already
    // { method: 'quantize' } even without an explicit setEncoderOptions()
    // call (see @gltf-transform/extensions DEFAULT_ENCODER_OPTIONS). That
    // name does not mean a lossy filter is applied on top of quantize() --
    // it names which meshopt codec path re-encodes the already-quantized
    // integers, not an additional transform.
    doc.createExtension(EXTMeshoptCompression).setRequired(true);

    const xfBytes = await io.writeBinary(doc);
    fs.writeFileSync(outAbs, Buffer.from(xfBytes));
    done++;
    if (done % 200 === 0) {
      console.log(`  ... ${done}/${included.length} (${Date.now() - t0}ms elapsed)`);
    }
  }

  const elapsedMs = Date.now() - t0;
  console.log(`done: ${done}/${included.length} files transformed -> ${outRoot} (${elapsedMs}ms)`);
  console.log(`excluded (left out of --out, untouched by build.py's overlay): ${excluded.length}`);

  if (done !== included.length) {
    console.error(`FATAL: transformed ${done} but expected ${included.length}`);
    process.exit(1);
  }
}

main().catch((e) => {
  console.error('FAILED', e && e.stack || e);
  process.exit(1);
});
