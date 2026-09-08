"""JSON Schema 2020-12 normalization, dialect gate, and depth bounding."""

from __future__ import annotations

from typing import Any, Optional

from nitrostack.protocol.constants import MAX_SCHEMA_DEPTH

JSON_SCHEMA_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"
_ACCEPTED_DIALECT_URIS = frozenset(
    {
        JSON_SCHEMA_2020_12_URI,
        "http://json-schema.org/draft/2020-12/schema",
        f"{JSON_SCHEMA_2020_12_URI}#",
        "http://json-schema.org/draft/2020-12/schema#",
    }
)

_COMPOSITION_KEYS = frozenset({"allOf", "anyOf", "oneOf"})
_MAP_SCHEMA_KEYS = frozenset({"properties", "patternProperties"})
_SINGLE_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "contains",
        "propertyNames",
        "if",
        "then",
        "else",
        "not",
    }
)


class UnsupportedJsonSchemaError(ValueError):
    """Raised when a registered schema is not JSON Schema 2020-12."""


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
        elif key == "items":
            if isinstance(value, list):
                raise UnsupportedJsonSchemaError(
                    "JSON Schema 2020-12 'items' must be a single schema; "
                    "use 'prefixItems' for tuple validation"
                )
            result[key] = bound_schema_depth(value, max_depth=max_depth, depth=depth + 1)
        elif key == "prefixItems":
            if isinstance(value, list):
                result[key] = [
                    bound_schema_depth(item, max_depth=max_depth, depth=depth + 1) for item in value
                ]
            elif isinstance(value, dict):
                result[key] = bound_schema_depth(value, max_depth=max_depth, depth=depth + 1)
            else:
                result[key] = value
        elif key in _SINGLE_SCHEMA_KEYS:
            if isinstance(value, dict):
                result[key] = bound_schema_depth(value, max_depth=max_depth, depth=depth + 1)
            else:
                result[key] = bound_schema_depth(value, max_depth=max_depth, depth=depth + 1)
        elif key == "$ref":
            result[key] = value
        elif isinstance(value, (dict, list)):
            result[key] = bound_schema_depth(value, max_depth=max_depth, depth=depth + 1)
        else:
            result[key] = value
    return result


def _dialect_uri(value: Any) -> str:
    return str(value).strip()


def is_json_schema_2020_12_dialect(schema_uri: Optional[str]) -> bool:
    """True when ``$schema`` is absent (treated as 2020-12) or a 2020-12 URI."""
    if schema_uri is None:
        return True
    uri = _dialect_uri(schema_uri)
    if not uri:
        return True
    return uri in _ACCEPTED_DIALECT_URIS


def _assert_items_not_tuple(node: Any, *, name: str, path: str = "$") -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _assert_items_not_tuple(item, name=name, path=f"{path}[{index}]")
        return
    if not isinstance(node, dict):
        return
    items = node.get("items")
    if isinstance(items, list):
        raise UnsupportedJsonSchemaError(
            f"{name} at {path}.items is a tuple list; JSON Schema 2020-12 "
            "requires a single schema for items (use prefixItems for tuples)"
        )
    for key, value in node.items():
        if key in _COMPOSITION_KEYS and isinstance(value, list):
            for index, item in enumerate(value):
                _assert_items_not_tuple(item, name=name, path=f"{path}.{key}[{index}]")
        elif key in {"$defs", "definitions"} and isinstance(value, dict):
            for def_key, def_val in value.items():
                _assert_items_not_tuple(def_val, name=name, path=f"{path}.{key}.{def_key}")
        elif key in _MAP_SCHEMA_KEYS and isinstance(value, dict):
            for nested_key, nested_val in value.items():
                _assert_items_not_tuple(
                    nested_val, name=name, path=f"{path}.{key}.{nested_key}"
                )
        elif key == "prefixItems" and isinstance(value, list):
            for index, item in enumerate(value):
                _assert_items_not_tuple(item, name=name, path=f"{path}.prefixItems[{index}]")
        elif key in _SINGLE_SCHEMA_KEYS or key == "items":
            _assert_items_not_tuple(value, name=name, path=f"{path}.{key}")
        elif key != "enum" and isinstance(value, (dict, list)):
            _assert_items_not_tuple(value, name=name, path=f"{path}.{key}")


def _assert_nested_dialects(node: Any, *, name: str, path: str = "$") -> None:
    if isinstance(node, list):
        for index, item in enumerate(node):
            _assert_nested_dialects(item, name=name, path=f"{path}[{index}]")
        return
    if not isinstance(node, dict):
        return
    dialect = node.get("$schema")
    if dialect is not None and not is_json_schema_2020_12_dialect(_dialect_uri(dialect)):
        raise UnsupportedJsonSchemaError(
            f"{name} at {path} uses unsupported JSON Schema dialect {dialect!r}; "
            f"required dialect is {JSON_SCHEMA_2020_12_URI}"
        )
    for key, value in node.items():
        if key == "enum":
            continue
        if isinstance(value, (dict, list)):
            _assert_nested_dialects(value, name=name, path=f"{path}.{key}")


def assert_json_schema_2020_12(schema: Any, *, name: str = "schema") -> None:
    """Reject unsupported drafts and tuple-style ``items`` lists.

    A missing ``$schema`` is treated as JSON Schema 2020-12.
    """
    if schema is None:
        return
    if not isinstance(schema, dict):
        return
    _assert_nested_dialects(schema, name=name)
    _assert_items_not_tuple(schema, name=name)


def gate_registered_schema(schema: Any, *, name: str) -> None:
    """Registration-time dialect gate for a dict or Pydantic model."""
    if schema is None:
        return
    if isinstance(schema, dict):
        assert_json_schema_2020_12(schema, name=name)
        return
    model_schema = getattr(schema, "model_json_schema", None)
    if callable(model_schema):
        assert_json_schema_2020_12(model_schema(), name=name)


def normalize_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize tool inputSchema for MCP 2026-07-28.

    Root MUST be ``type: object``; ``$schema`` is set to JSON Schema 2020-12.
    Unsupported drafts and tuple-style ``items`` fail instead of being rewritten.
    """
    assert_json_schema_2020_12(schema, name="inputSchema")
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
    assert_json_schema_2020_12(schema, name="outputSchema")
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
