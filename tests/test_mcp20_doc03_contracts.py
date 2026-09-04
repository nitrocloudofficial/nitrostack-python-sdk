"""Tests for MCP 2.0 tool/resource/prompt contracts (Doc 03)."""

import pytest
from pydantic import BaseModel, Field

from nitrostack.core.errors import ResourceNotFoundError
from nitrostack.protocol.constants import MAX_SCHEMA_DEPTH
from nitrostack.protocol.contracts import (
    build_cache_hint_meta,
    build_prompt_get_result,
    build_prompt_text_message,
    build_resource_blob_content,
    build_resource_text_content,
)
from nitrostack.protocol.jsonrpc import map_exception_to_jsonrpc
from nitrostack.protocol.resources import (
    resolve_resource_uri,
    uri_template_to_pattern,
)
from nitrostack.protocol.schema import (
    JSON_SCHEMA_2020_12_URI,
    bound_schema_depth,
    normalize_input_schema,
    normalize_output_schema,
)


class _DeepSchema:
    @staticmethod
    def nested(depth: int) -> dict:
        node: dict = {"type": "object", "properties": {"value": {"type": "string"}}}
        for _ in range(depth):
            node = {"type": "object", "properties": {"child": node}}
        return node


class TestJsonSchema2020_12:
    def test_input_schema_requires_object_root(self):
        schema = normalize_input_schema({"type": "string"})
        assert schema["type"] == "object"
        assert schema["$schema"] == JSON_SCHEMA_2020_12_URI
        assert "properties" in schema

    def test_output_schema_allows_primitive_root(self):
        schema = normalize_output_schema({"type": "integer"})
        assert schema["type"] == "integer"
        assert schema["$schema"] == JSON_SCHEMA_2020_12_URI

    def test_depth_bounding_collapses_deep_nodes(self):
        deep = _DeepSchema.nested(MAX_SCHEMA_DEPTH + 5)
        bounded = bound_schema_depth(deep)
        # Walk down until we hit the collapsed node
        node = bounded
        for _ in range(MAX_SCHEMA_DEPTH - 1):
            node = node["properties"]["child"]
        assert node["properties"]["child"] == {}


class TestResourceUriResolution:
    def test_static_resource_exact_match(self):
        static = {"mcp://telemetry/system_metrics": "entry-a"}
        result = resolve_resource_uri("mcp://telemetry/system_metrics", static, [])
        assert result is not None
        assert result.entry == "entry-a"
        assert result.path_params == {}

    def test_template_match_extracts_params(self):
        pattern = uri_template_to_pattern("mcp://customers/{customerId}/orders")
        templates = [(pattern, "entry-b")]
        result = resolve_resource_uri("mcp://customers/acme/orders", {}, templates)
        assert result is not None
        assert result.matched_via_template is True
        assert result.path_params == {"customerId": "acme"}

    def test_missing_resource_maps_to_invalid_params(self):
        resp = map_exception_to_jsonrpc(ResourceNotFoundError("mcp://missing"), "1")
        assert resp["error"]["code"] == -32602


class TestWireContracts:
    def test_cache_hint_meta(self):
        meta = build_cache_hint_meta(60000, cache_scope="private")
        hint = meta["io.modelcontextprotocol/cacheHint"]
        assert hint["ttlMs"] == 60000
        assert hint["cacheScope"] == "private"

    def test_resource_text_content(self):
        content = build_resource_text_content(
            "mcp://telemetry/system_metrics",
            '{"cpuUsage": 14.2}',
        )
        assert content["mimeType"] == "application/json"
        assert "cpuUsage" in content["text"]

    def test_resource_blob_content(self):
        content = build_resource_blob_content(
            "mcp://assets/diagram.png",
            "iVBORw0KGgo=",
            mime_type="image/png",
        )
        assert content["blob"] == "iVBORw0KGgo="
        assert content["mimeType"] == "image/png"

    def test_prompt_get_result(self):
        messages = [
            build_prompt_text_message("user", "Review this Python code:\ndef foo(): pass")
        ]
        result = build_prompt_get_result("Senior Code Review Prompt", messages)
        assert result["description"] == "Senior Code Review Prompt"
        assert result["messages"][0]["content"]["type"] == "text"


class TestAppIntegrationSchema:
    def test_pydantic_model_produces_modern_input_schema(self):
        from nitrostack.core.app import inspector_friendly_schema

        class CalcInput(BaseModel):
            a: float = Field(description="First number")
            b: float = Field(description="Second number")

        schema = normalize_input_schema(inspector_friendly_schema(CalcInput.model_json_schema()))
        assert schema["$schema"] == JSON_SCHEMA_2020_12_URI
        assert schema["type"] == "object"
        assert "a" in schema["properties"]
