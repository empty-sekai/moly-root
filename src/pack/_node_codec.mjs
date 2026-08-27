import { readFileSync } from 'node:fs';
import zlib from 'node:zlib';

// Codec helper invoked as a child process only when no Python binding is
// available in the current environment (see pack/codecs.py). Parameters are
// fixed to match the Python path exactly: quality 9, lgwin 24, size hint =
// input length for brotli; level 9 for gzip.
const op = process.argv[2];
const data = readFileSync(0); // read all of stdin (a pipe, per subprocess.run(input=...))

let out;
switch (op) {
  case 'brotli-compress':
    out = zlib.brotliCompressSync(data, {
      params: {
        [zlib.constants.BROTLI_PARAM_QUALITY]: 9,
        [zlib.constants.BROTLI_PARAM_LGWIN]: 24,
        [zlib.constants.BROTLI_PARAM_SIZE_HINT]: data.length,
      },
    });
    break;
  case 'brotli-decompress':
    out = zlib.brotliDecompressSync(data);
    break;
  case 'gzip-compress':
    // Node's zlib always writes MTIME = 0 in the gzip header (unlike
    // Python's gzip.compress, which defaults to the current time), so no
    // extra option is needed here for determinism.
    out = zlib.gzipSync(data, { level: 9 });
    break;
  case 'gzip-decompress':
    out = zlib.gunzipSync(data);
    break;
  default:
    process.stderr.write(`unknown op: ${op}\n`);
    process.exit(2);
}
process.stdout.write(out);
