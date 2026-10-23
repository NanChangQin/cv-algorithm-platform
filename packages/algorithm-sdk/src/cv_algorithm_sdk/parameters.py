"""JSON-safe parameter validation shared by manifests and runtime contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator


def json_value(value: Any) -> Any:
    """Reject NaN/Infinity, non-JSON keys and arbitrary Python objects."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            json_value(item)
    elif isinstance(value, list):
        for item in value:
            json_value(item)
    elif value is not None and type(value) not in (str, bool, int, float):
        raise ValueError("parameters must contain JSON values")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("parameters must contain finite JSON values") from exc
    return value


def parameter_schema(spec: dict[str, Any]) -> dict[str, Any]:
    if spec["type"] == "json":
        return deepcopy(spec["json_schema"])
    schema = {"type": spec["type"]}
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if spec.get(key) is not None:
            schema[key] = spec[key]
    if spec.get("options") is not None:
        schema["enum"] = spec["options"]
    return schema


def validate_value(name: str, value: Any, schema: dict[str, Any]) -> None:
    json_value(value)
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path)
        raise ValueError(f"{name}{'.' + path if path else ''}: {error.message}")


def canonical_value(value: Any, schema: dict[str, Any]) -> Any:
    """JSON Schema treats 3.0 as integer; Python runtimes often require int(3)."""
    for branch in schema.get("anyOf", []):
        if Draft202012Validator(branch).is_valid(value):
            return canonical_value(value, branch)
    if schema.get("type") == "integer" and type(value) is float:
        return int(value)
    if isinstance(value, list):
        prefix = schema.get("prefixItems", [])
        item_schema = schema.get("items", {})
        return [canonical_value(item, prefix[index] if index < len(prefix) else (
            item_schema if isinstance(item_schema, dict) else {}
        )) for index, item in enumerate(value)]
    if isinstance(value, dict):
        return {key: canonical_value(item, schema.get("properties", {}).get(key, {}))
                for key, item in value.items()}
    return value


def resolve_parameters(
    specs: dict[str, dict[str, Any]], supplied: dict[str, Any]
) -> dict[str, Any]:
    json_value(supplied)
    unknown = supplied.keys() - specs.keys()
    if unknown:
        raise ValueError(f"unknown parameters: {', '.join(sorted(unknown))}")
    resolved = {}
    for name, spec in specs.items():
        value = deepcopy(supplied[name] if name in supplied else spec["default"])
        validate_value(name, value, parameter_schema(spec))
        resolved[name] = canonical_value(value, parameter_schema(spec))
    return resolved
