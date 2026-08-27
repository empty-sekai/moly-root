"""A minimal JSON Schema (2020-12 subset) evaluator, used by verify.py only
when the third-party ``jsonschema`` package is not installed.

This is not a general-purpose schema engine -- it implements exactly the
keywords manifest.schema.json actually uses: ``type``, ``const``, ``enum``,
``pattern``, ``minLength``, ``minItems``, ``minimum``, ``required``,
``properties``, ``additionalProperties`` (boolean form only), ``items``,
``allOf``, ``if``/``then``, and ``$ref`` resolved against a document-local
``$defs`` map. Anything outside that set is silently ignored rather than
rejected, so this is deliberately a subset, not a compatible reimplementation
of JSON Schema.
"""
from __future__ import annotations

import re


def _resolve_ref(ref: str, root: dict) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"schema_lite only resolves local refs, got {ref!r}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _type_ok(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def iter_errors(instance, schema: dict, root: dict, path: str = ""):
    """Yield "<path>: <message>" strings for every violation found."""
    if "$ref" in schema:
        schema = _resolve_ref(schema["$ref"], root)

    if "const" in schema:
        if instance != schema["const"]:
            yield f"{path or '<root>'}: expected const {schema['const']!r}, got {instance!r}"
        return  # const fully determines validity for the fixed-value fields this schema uses

    if "enum" in schema and instance not in schema["enum"]:
        yield f"{path or '<root>'}: {instance!r} is not one of {schema['enum']!r}"

    if "type" in schema and not _type_ok(instance, schema["type"]):
        yield f"{path or '<root>'}: expected type {schema['type']!r}, got {type(instance).__name__}"
        return  # further structural checks would be meaningless on the wrong type

    if isinstance(instance, str):
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            yield f"{path or '<root>'}: {instance!r} does not match pattern {schema['pattern']!r}"
        if "minLength" in schema and len(instance) < schema["minLength"]:
            yield f"{path or '<root>'}: length {len(instance)} < minLength {schema['minLength']}"

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            yield f"{path or '<root>'}: {instance} < minimum {schema['minimum']}"

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            yield f"{path or '<root>'}: {len(instance)} items < minItems {schema['minItems']}"
        if "items" in schema:
            item_schema = schema["items"]
            for i, item in enumerate(instance):
                yield from iter_errors(item, item_schema, root, f"{path}[{i}]")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                yield f"{path or '<root>'}: missing required property {key!r}"
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                yield from iter_errors(value, props[key], root, f"{path}/{key}" if path else key)
            elif schema.get("additionalProperties") is False:
                yield f"{path or '<root>'}: unexpected property {key!r} (additionalProperties: false)"

    for sub in schema.get("allOf", []):
        if "if" in sub:
            cond_errors = list(iter_errors(instance, sub["if"], root, path))
            if not cond_errors:  # the `if` branch matched
                yield from iter_errors(instance, sub.get("then", {}), root, path)
        else:
            yield from iter_errors(instance, sub, root, path)


def validate(instance, schema: dict) -> list[str]:
    """All violations of *schema* found in *instance*, as human-readable strings."""
    return list(iter_errors(instance, schema, schema))
