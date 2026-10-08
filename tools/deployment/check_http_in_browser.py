"""Loopback CORS check: hidden Content-Encoding still transparently decodes.

No source writes, cache clearing, cloud requests or app resources are involved.
Open the printed URL and close this process after reading the result.
"""
import argparse
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from pack import http_delivery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8124)
    args = parser.parse_args()
    raw = args.sample.read_bytes()
    if http_delivery.brotli is None:
        raise RuntimeError("Brotli is required for this explicit two-encoding check")
    bodies = {"gzip": gzip.compress(raw, compresslevel=http_delivery.GZIP_LEVEL, mtime=0),
              "br": http_delivery.brotli.compress(raw, quality=http_delivery.BROTLI_QUALITY,
                                                  lgwin=http_delivery.BROTLI_LGWIN)}
    expected = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "encodedBytes": {key: len(value) for key, value in bodies.items()}}
    page = ("<!doctype html><meta charset=utf-8><title>Moly HTTP decoding check</title>"
            "<h1>Lossless HTTP decoding across origins</h1><pre id=result>Checking…</pre><script>"
            "const expected=" + json.dumps(expected) + ";"
            "(async()=>{const results=[];for(const encoding of ['br','gzip']){"
            "const response=await fetch('http://127.0.0.1:'+location.port+'/payload.glb?encoding='+encoding,"
            "{cache:'no-store',mode:'cors'});"
            "const body=await response.arrayBuffer();const hash=Array.from(new Uint8Array("
            "await crypto.subtle.digest('SHA-256',body))).map(x=>x.toString(16).padStart(2,'0')).join('');"
            "results.push({encoding,responseType:response.type,responseEncoding:response.headers.get('Content-Encoding'),"
            "encodedContentLength:Number(response.headers.get('Content-Length')),"
            "decodedBytes:body.byteLength,decodedSha256:hash,"
            "passed:response.ok&&response.type==='cors'&&response.headers.get('Content-Encoding')===null"
            "&&Number(response.headers.get('Content-Length'))===expected.encodedBytes[encoding]"
            "&&body.byteLength===expected.bytes&&hash===expected.sha256});}"
            "const report={pageOrigin:location.origin,apiOrigin:'http://127.0.0.1:'+location.port,"
            "expected,results,passed:results.every(r=>r.passed)};"
            "document.getElementById('result').textContent=JSON.stringify(report,null,2);"
            "await fetch('/result',{method:'POST',body:JSON.stringify(report)});"
            "})().catch(e=>document.getElementById('result').textContent=String(e));</script>").encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                body, kind, encoding = page, "text/html; charset=utf-8", None
            elif parsed.path == "/payload.glb":
                encoding = parse_qs(parsed.query).get("encoding", [None])[0]
                if encoding not in bodies:
                    self.send_error(400)
                    return
                body, kind = bodies[encoding], "model/gltf-binary"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-transform")
            self.send_header("Access-Control-Allow-Origin", "*")
            # Deliberately no Access-Control-Expose-Headers. Fetch cannot read
            # Content-Encoding, but can read the encoded Content-Length.
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/result" or not 0 < size < 8192:
                self.send_error(400)
                return
            report = json.loads(self.rfile.read(size))
            print(json.dumps({"browserResult": report}), flush=True)
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(json.dumps({"url": f"http://localhost:{server.server_port}/", "expected": expected,
                      "encodedBytes": {key: len(value) for key, value in bodies.items()}}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
