"""Task descriptions separate content-affecting facts from source locations."""
import re

from core.content import document_hash


def key_parts(task):
    return {"stage": task["stage"], "subject": task["subject"],
            "params": task["params"], "atoms": task["atoms"], "context": task["context"],
            "inputs": [{k: item[k] for k in ("role", "sha256", "size")}
                       for item in sorted(task["inputs"], key=lambda item: item["role"])]}


def validate(task):
    if task.get("schema") != "moly-task/1":
        raise ValueError("unsupported task schema")
    if (not isinstance(task["subject"], str) or not task["subject"]
            or not re.fullmatch(r"[a-z][a-z0-9_.-]*", task["stage"]["name"])
            or type(task["stage"]["version"]) is not int or task["stage"]["version"] < 1):
        raise ValueError("invalid task stage or subject")
    roles = set()
    for item in task["inputs"]:
        role = item["role"]
        if not isinstance(role, str) or not role or role in roles:
            raise ValueError("empty or duplicate task input role")
        roles.add(role)
        if (not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or type(item["size"]) is not int or item["size"] < 0
                or not isinstance(item.get("locators"), list)):
            raise ValueError("invalid input identity or locators")
    for field in ("params", "atoms", "context"):
        if not isinstance(task[field], dict):
            raise ValueError(f"{field} must be an object")
    if task.get("key") != document_hash(key_parts(task)):
        raise ValueError("task key does not match its declared inputs")
    return task


def describe(stage, version, subject, inputs, *, params=None, atoms=None, context=None):
    task = {"schema": "moly-task/1", "stage": {"name": stage, "version": version},
            "subject": subject, "inputs": sorted(inputs, key=lambda item: item["role"]),
            "params": params or {}, "atoms": atoms or {}, "context": context or {}}
    task["key"] = document_hash(key_parts(task))
    return validate(task)
