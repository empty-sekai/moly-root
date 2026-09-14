"""Build and install the full distribution outside its source tree.

Run with a Python environment containing the PyPA `build` package:
  python tests/check_distribution.py --work-dir <new-directory-outside-source>
Dependency installation uses the configured Python package index. All asset
inputs are synthetic; the helper never opens or downloads game resources.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import venv
import zipfile

RESOURCES = {"categories.toml", "groups.toml", "transforms.toml",
             "_node_codec.mjs", "transform_glb.mjs", "manifest.schema.json"}


def check(source, work):
    source, work = Path(source).resolve(), Path(work).resolve()
    if work.is_relative_to(source) or source.is_relative_to(work):
        raise ValueError("distribution check directory must be outside the source")
    work.mkdir(parents=True, exist_ok=False)
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

    def run(name, command):
        result = subprocess.run(command, cwd=work, env=environment, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        (work / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {work / (name + '.log')}")
        return result.stdout

    run("build", [sys.executable, "-m", "build", str(source), "--outdir", str(work / "dist")])
    wheel, = (work / "dist").glob("*.whl")
    sdist, = (work / "dist").glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert {"pack/" + name for name in RESOURCES} <= names
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        for resource in RESOURCES:
            assert any(name.endswith("/src/pack/" + resource) for name in names), resource

    virtualenv = work / "venv"
    venv.EnvBuilder(with_pip=True).create(virtualenv)
    scripts = virtualenv / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    moly = scripts / ("moly.exe" if os.name == "nt" else "moly")
    run("install", [str(python), "-m", "pip", "install", str(wheel)])
    run("dependencies", [str(python), "-m", "pip", "check"])
    for command in ([], ["pull"], ["extract"], ["fetch-apk"]):
        run("help-" + (command[0] if command else "moly"), [str(moly), *command, "--help"])

    runtime = r'''
import importlib.resources as resources
import json
from pathlib import Path
import shutil
import sys
from pack import build, codecs, groups
from core import cli
for module in (build, groups, cli):
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), module.__file__
for name in sys.argv[1:]:
    assert resources.files("pack").joinpath(name).read_bytes(), name
node_fallback = False
if shutil.which("node"):
    codecs._pybrotli = None
    data = b"installed helper round trip" * 32
    assert codecs.brotli_decompress(codecs.brotli_compress(data)) == data
    node_fallback = True
print(json.dumps({"python": sys.version, "node_fallback_checked": node_fallback}))
'''
    runtime_info = json.loads(run("runtime", [str(python), "-c", runtime, *sorted(RESOURCES)]))
    assets = work / "synthetic-assets"
    assets.mkdir()
    (assets / "manifest.json").write_text('{"units": []}', encoding="utf-8")
    (assets / "characters.json").write_text('{"version": "v1"}', encoding="utf-8")
    (assets / "a.bin").write_bytes(b"synthetic binary")
    weather = assets / "phenomena" / "001_sunny"
    weather.mkdir(parents=True)
    (weather / "config.json").write_text('{"synthetic": true}', encoding="utf-8")
    # A release root may itself be called "catalogs"; only its content-addressed
    # historical children live one level below that root.
    standalone, grouped = work / "standalone", work / "catalogs"
    run("standalone-build", [str(python), "-m", "pack.build", "--src", str(assets),
                              "--out", str(standalone), "--version", "smoke"])
    run("standalone-verify", [str(python), "-m", "pack.verify", "--out", str(standalone)])
    for version in ("v1", "v2"):
        (assets / "characters.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        run("groups-" + version, [str(python), "-m", "pack.groups", "--src", str(assets),
                                  "--out", str(grouped), "--version", version])
    run("groups-verify", [str(python), "-m", "pack.verify", "--out", str(grouped)])
    historical = next((grouped / "catalogs").glob("*.json"))
    run("history-verify", [str(python), "-m", "pack.verify", "--catalog", str(historical)])
    garbage = json.loads(run("groups-gc", [str(python), "-m", "pack.gc", "--out", str(grouped), "--json"]))
    assert garbage["delete"] == [] and garbage["retained_catalogs"] == 2
    result = {"wheel": wheel.name, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
              "sdist": sdist.name, "resources": sorted(RESOURCES), "runtime": runtime_info,
              "cli_help": "passed", "standalone_pack": "passed", "grouped_pack": "passed",
              "retained_catalogs": garbage["retained_catalogs"], "catalog_root_name": grouped.name,
              "historical_catalog": "passed"}
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    check(args.source, args.work_dir)
