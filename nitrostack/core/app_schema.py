"""Schema and tool input normalization for the application runtime."""

import logging
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, create_model

from nitrostack.protocol.schema import (
    normalize_output_schema,
)

logger = logging.getLogger(__name__)


def get_pydantic_model(schema: Any) -> Type[BaseModel]:
    """Helper to resolve or construct a Pydantic model for input validation."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema
    if isinstance(schema, dict):
        # Dynamically build a Pydantic model from JSON schema
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        fields = {}
        for name, prop in properties.items():
            t = Any
            prop_type = prop.get("type")
            if prop_type == "string":
                t = str
            elif prop_type == "integer":
                t = int
            elif prop_type == "number":
                t = float
            elif prop_type == "boolean":
                t = bool
            elif prop_type == "array":
                t = list
            elif prop_type == "object":
                t = dict

            default = ... if name in required else None
            fields[name] = (t, default)
        return create_model("DynamicInputModel", **fields)

    # Return a default empty model if invalid or empty
    return create_model("EmptyInputModel")


def _is_null_schema(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "null"


def _unwrap_nullable_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Turn JSON Schema `T | null` into `T` so MCP Inspector can render widgets.

    Pydantic v2 emits Optional fields as `anyOf: [{type: T}, {type: null}]` (or
    `type: [T, "null"]`). Inspector/RJSF shows the property name but no input
    control for those unions.
    """
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if not isinstance(variants, list) or len(variants) != 2:
            continue
        non_null = [item for item in variants if not _is_null_schema(item)]
        if len(non_null) != 1:
            continue
        merged = dict(non_null[0])
        for extra_key, extra_val in schema.items():
            if extra_key == key:
                continue
            if extra_key not in merged:
                merged[extra_key] = extra_val
            elif extra_key in ("description", "title", "default") and extra_val is not None:
                merged[extra_key] = extra_val
        if merged.get("default") is None:
            merged.pop("default", None)
        return merged

    types = schema.get("type")
    if isinstance(types, list):
        non_null = [item for item in types if item != "null"]
        if len(non_null) == 1:
            schema = {**schema, "type": non_null[0]}
            if schema.get("default") is None:
                schema.pop("default", None)
    return schema


def inspector_friendly_schema(node: Any) -> Any:
    """Normalize a Pydantic JSON Schema so MCP Inspector can render form fields."""
    if isinstance(node, list):
        return [inspector_friendly_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    unwrapped = _unwrap_nullable_schema(node)
    return {key: inspector_friendly_schema(value) for key, value in unwrapped.items()}


def tool_json_schema(schema_spec: Any) -> Optional[Dict[str, Any]]:
    """Convert a Pydantic model class or JSON-schema dict to MCP 2026-07-28 outputSchema.

    Output schemas describe real results, so ``null`` branches of Optional fields are kept.
    ``inspector_friendly_schema`` is only for input forms.
    """
    if schema_spec is None:
        return None
    if isinstance(schema_spec, dict):
        return normalize_output_schema(schema_spec)
    model = get_pydantic_model(schema_spec)
    if model is not None:
        return normalize_output_schema(model.model_json_schema())
    return None


def _is_blank_inspector_value(value: Any) -> bool:
    """Inspector leaves unused form fields as ``""`` (or whitespace)."""
    return value is None or (isinstance(value, str) and not value.strip())


def _omit_blank_optional_fields(
    payload: Dict[str, Any], input_model: Type[BaseModel]
) -> Dict[str, Any]:
    """Drop blank optional/defaulted fields so Pydantic defaults apply.

    MCP Inspector sends ``filter: ""`` when the enum is left empty. Zod in the
    TS SDK marks that field ``.optional()`` and then uses ``args.filter || 'all'``.
    Pydantic ``default='all'`` only runs when the key is missing, not when it is
    an empty string — so we omit blanks for non-required fields.
    """
    fields = getattr(input_model, "model_fields", {}) or {}
    cleaned: Dict[str, Any] = {}
    for key, value in payload.items():
        field = fields.get(key)
        if field is not None and (not field.is_required()) and _is_blank_inspector_value(value):
            continue
        cleaned[key] = value
    return cleaned


def parse_tool_input(
    input_model: Type[BaseModel], arguments: Optional[Dict[str, Any]]
) -> BaseModel:
    """Validate tool arguments from Inspector or the older nested wrap.

    Inspector sends top-level fields (`{openNow: true}`).
    Older Python clients wrap them (`{input: {openNow: true}}`).
    Empty strings on optional fields are treated as omitted (Inspector dropdowns).
    """
    arguments = arguments or {}
    inner = arguments.get("input")
    looks_wrapped = (
        isinstance(inner, dict)
        and set(arguments.keys()) <= {"input"}
        and "input" not in input_model.model_fields
    )
    payload = inner if looks_wrapped else arguments
    if not isinstance(payload, dict):
        payload = {}
    return input_model.model_validate(_omit_blank_optional_fields(payload, input_model))
