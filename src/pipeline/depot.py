"""Run versioned stages and retain their outputs in the existing pack CAS."""
from pathlib import Path
import tempfile

from core.atomic import exclusive_lock, write_bytes
from core.content import canonical_bytes, content_hash
from pack.paths import output_path
from pack.task_results import read_result, retained_results
from source.snapshot import read_inputs
from .contract import key_parts, validate


class Depot:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def plan(self, task, *, previous=None):
        from .stages import validate_installed
        validate(task)
        validate_installed(task)
        result = read_result(self.root, task["key"])
        reasons = []
        if previous is not None:
            validate(previous)
            old, new = key_parts(previous), key_parts(task)
            reasons = [field + "-changed" for field in new if old[field] != new[field]]
        if not result and not reasons:
            reasons = ["result-missing"]
        return {"schema": "moly-plan/1", "task": task,
                "status": "hit" if result else "run", "reasons": reasons or ["cached"]}

    def run(self, task, *, force=False):
        from .stages import execute, validate_installed
        validate(task)
        validate_installed(task)
        with exclusive_lock(self.root / ".publish.lock"):
            previous = read_result(self.root, task["key"])
            if previous is not None and not force:
                return {"status": "hit", "result": previous}
            # Freeze and verify all bytes before calling a reader. A caller
            # changing files during extraction cannot contaminate this task.
            inputs = read_inputs(task["inputs"])
            with tempfile.TemporaryDirectory(prefix="moly-task-") as temp:
                artifacts = execute(task, inputs, Path(temp))
            validate_installed(task)
            rows = []
            for logical, data in sorted(artifacts.items()):
                output_path(self.root, logical)
                digest = content_hash(data)
                blob = f"{digest[:2]}/{digest}.bin"
                path = output_path(self.root, "blobs/" + blob)
                if path.exists():
                    if path.read_bytes() != data:
                        raise ValueError("corrupt artifact at content address")
                else:
                    write_bytes(path, data)
                rows.append({"path": logical, "sha256": digest, "size": len(data), "blob": blob})
            validate_installed(task)
            result = {"schema": "moly-result/1", "key": task["key"],
                      "keyParts": key_parts(task), "status": "ok", "artifacts": rows}
            if previous is not None and result != previous:
                raise ValueError("stage changed output for the same task key; bump its version")
            write_bytes(output_path(self.root, f"tasks/{task['key']}.json"), canonical_bytes(result))
            return {"status": "ran", "result": result}

    def materialize(self, key, destination):
        destination = Path(destination).resolve()
        if destination.is_relative_to(self.root) or self.root.is_relative_to(destination):
            raise ValueError("layout and depot must be separate directories")
        with exclusive_lock(self.root / ".publish.lock"):
            result = read_result(self.root, key)
            if result is None:
                raise ValueError("task has no committed result")
            # Discovery is installed last. New hashed documents are complete
            # before an existing reader can see the new root index.
            for artifact in sorted(result["artifacts"], key=lambda row: (
                    row["path"] == "text/index.json" or row["path"].endswith("/latest.json"))):
                write_bytes(output_path(destination, artifact["path"]),
                            output_path(self.root, "blobs/" + artifact["blob"]).read_bytes())
            # Do not hard-link editable layouts to immutable shared bytes.
            return {"key": key, "files": len(result["artifacts"])}

    def verify(self):
        with exclusive_lock(self.root / ".publish.lock"):
            roots, blobs = retained_results(self.root)
            return {"results": len(roots), "blobs": len(blobs), "bytes": sum(blobs.values())}
