"""JSON Schema 2020-12 normalization and depth bounding (SEP-2106)."""

from __future__ import annotations

from typing import Any

from nitrostack.protocol.constants import MAX_SCHEMA_DEPTH

JSON_SCHEMA_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"

_COMPOSITION_KEYS = frozenset({"allOf", "anyOf", "oneOf"})
_MAP_SCHEMA_KEYS = frozenset({"properties", "patternProperties"})
_SINGLE_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "items",
        "contains",
        "propertyNames",
        "if",
        "then",
        "else",
        "not",
    }
)


def bound_schema_depth(
    node: Any,
    *,
    max_depth: int = MAX_SCHEMA_DEPTH,
    depth: int = 0,
) -> Any:
    """Collapse schema nodes beyond ``max_depth`` to permissive ``{}`` (DoS defense)."""
    if depth >= max_depth:
        return {}

    if isinstance(node, list):
        return [bound_schema_depth(item, max_depth=max_depth, depth=depth + 1) for item in node]

    if not isinstance(node, dict):
        return node

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in _COMPOSITION_KEYS and isinstance(value, list):
            result[key] = [
                bound_schema_depth(item, max_depth=max_depth, depth=depth + 1) for item in value
            ]
        elif key == "$defs" and isinstance(value, dict):
            result[key] = {
                def_key: bound_schema_depth(def_val, max_depth=max_depth, depth=depth + 1)
                for def_key, def_val in value.items()
            }
        elif key in _MAP_SCHEMA_KEYS and isinstance(value, dict):
            result[key] = {
                nested_key: bound_schema_depth(nested_val, max_depth=max_depth, depth=depth + 1)
                for nested_key, nested_val in value.items()
            }
        elif key in _SINGLE_SCHEMA_KEYS or key == "prefixItems":
            if isinstance(value, dict):
                result[key] = bound_schema_depth(value, max_depth=max_depth, depth=depth + 1)
            elif isinstance(value, list):
                result[key] = [
                    bound_schema_depth(item, max_depth=max_depth, depth=depth + 1) for item in value
                ]
            else:
                result[key] = bound_schema_depth(value, max_depth=max_depth, depth=depth + 1)
        elif key == "$ref":
            result[key] = value
        elif isinstance(value, (dict, list)):
            result[key] = bound_schema_depth(value, max_depth=max_depth, depth=depth + 1)
        else:
            result[key] = value
    return result


def normalize_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize tool inputSchema for MCP 2026-07-28.

    Root MUST be ``type: object``; ``$schema`` is set to JSON Schema 2020-12.
    """
    bounded = bound_schema_depth(schema)
    if not isinstance(bounded, dict):
        bounded = {}
    bounded = dict(bounded)
    bounded["type"] = "object"
    bounded.setdefault("properties", {})
    bounded["$schema"] = JSON_SCHEMA_2020_12_URI
    return bounded


def normalize_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool outputSchema — unrestricted root type, bounded depth."""
    bounded = bound_schema_depth(schema)
    if not isinstance(bounded, dict):
        bounded = {"type": "object"}
    bounded = dict(bounded)
    bounded["$schema"] = JSON_SCHEMA_2020_12_URI
    return bounded


def normalize_json_schema(
    schema: dict[str, Any],
    *,
    require_object_root: bool = False,
) -> dict[str, Any]:
    """Generic schema normalizer used by tool input/output builders."""
    if require_object_root:
        return normalize_input_schema(schema)
    return normalize_output_schema(schema)
