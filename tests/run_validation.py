"""Record source identity, commands and complete logs for release validation.

Requires the project's dependencies, pytest, build and Node.js. Artifacts are
written outside the checkout so synthetic outputs cannot pollute source tests.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile


def validate(work, *, service_user_check=False):
    source = Path(__file__).resolve().parents[1]
    work = Path(work).resolve()
    if work.is_relative_to(source) or source.is_relative_to(work):
        raise ValueError("validation artifacts must be outside the source tree")
    work.mkdir(parents=True, exist_ok=False)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for viewer contract validation")

    def output(command):
        return subprocess.check_output(command, cwd=source, text=True, encoding="utf-8").strip()

    metadata = {
        "head_sha": output(["git", "rev-parse", "HEAD"]),
        "tree": output(["git", "rev-parse", "HEAD^{tree}"]),
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python": sys.version,
        "node": output([node, "--version"]),
        "dependencies": {name: importlib.metadata.version(name)
                         for name in ("pytest", "build", "UnityPy", "Pillow")},
        "commands": [],
    }
    if output(["git", "status", "--porcelain", "--untracked-files=no"]):
        raise RuntimeError("validation requires an unchanged tracked source tree")
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    def run(name, command):
        print(f"Running {name}", flush=True)
        entry = {"name": name, "argv": command, "log": f"{name}.log"}
        metadata["commands"].append(entry)
        with (work / entry["log"]).open("w", encoding="utf-8") as log:
            with subprocess.Popen(command, cwd=source, env=environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as process:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    print(line, end="", flush=True)
                entry["exit_code"] = process.wait()
        if entry["exit_code"]:
            raise RuntimeError(f"{name} failed with exit code {entry['exit_code']}")

    try:
        run("pytest", [sys.executable, "-m", "pytest", "-q", "-rs", "--tb=short",
                       "--junitxml=" + str(work / "pytest.xml")])
        if service_user_check and os.name == "posix" and os.geteuid() != 0:
            run("service-user", ["sudo", "-n", "--preserve-env=PATH,LD_LIBRARY_PATH", sys.executable,
                                 "-m", "pytest", "-q", "--tb=short",
                                 "--junitxml=" + str(work / "service-user.xml"),
                                 "tests/test_atomic_permissions.py::test_another_uid_can_read_new_and_updated_release_objects"])
        archive = work / "source.zip"
        run("source-snapshot", ["git", "archive", "--format=zip", "--output=" + str(archive), metadata["head_sha"]])
        snapshot = work / "source"
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(snapshot)
        run("distribution", [sys.executable, str(snapshot / "tests" / "check_distribution.py"),
                             "--source", str(snapshot), "--work-dir", str(work / "install-check")])
        if (output(["git", "rev-parse", "HEAD"]) != metadata["head_sha"]
                or output(["git", "status", "--porcelain", "--untracked-files=no"])):
            raise RuntimeError("tracked source changed during validation")
        metadata["status"] = "passed"
    finally:
        metadata.setdefault("status", "failed")
        metadata["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (work / "validation.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--service-user-check", action="store_true",
                        help="run the separate UID publication check with sudo on non-root POSIX runners")
    args = parser.parse_args()
    validate(args.work_dir, service_user_check=args.service_user_check)
