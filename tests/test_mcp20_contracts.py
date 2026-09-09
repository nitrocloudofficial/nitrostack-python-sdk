"""Tests for MCP 2.0 tool/resource/prompt contracts."""

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
    UnsupportedJsonSchemaError,
    assert_json_schema_2020_12,
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

    def test_items_object_is_a_single_schema(self):
        bounded = bound_schema_depth(
            {"type": "array", "items": {"type": "string", "minLength": 1}},
            max_depth=8,
        )
        assert bounded["items"] == {"type": "string", "minLength": 1}

    def test_missing_schema_is_treated_as_2020_12(self):
        assert_json_schema_2020_12({"type": "object", "properties": {}})
        schema = normalize_input_schema({"type": "object", "properties": {"n": {"type": "integer"}}})
        assert schema["$schema"] == JSON_SCHEMA_2020_12_URI

    def test_draft_04_schema_is_rejected(self):
        draft = {
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "object",
            "properties": {"n": {"type": "integer"}},
        }
        with pytest.raises(UnsupportedJsonSchemaError, match="draft-04"):
            assert_json_schema_2020_12(draft, name="tool 'legacy' input")
        with pytest.raises(UnsupportedJsonSchemaError, match="draft-04"):
            normalize_input_schema(draft)

    def test_items_tuple_list_is_rejected(self):
        schema = {
            "type": "object",
            "properties": {
                "pair": {
                    "type": "array",
                    "items": [{"type": "string"}, {"type": "integer"}],
                }
            },
        }
        with pytest.raises(UnsupportedJsonSchemaError, match="tuple"):
            assert_json_schema_2020_12(schema, name="tool 'pair' input")
        with pytest.raises(UnsupportedJsonSchemaError, match="items"):
            bound_schema_depth(schema)

    def test_prefix_items_tuple_is_allowed(self):
        schema = {
            "$schema": JSON_SCHEMA_2020_12_URI,
            "type": "object",
            "properties": {
                "pair": {
                    "type": "array",
                    "prefixItems": [{"type": "string"}, {"type": "integer"}],
                    "items": False,
                }
            },
        }
        assert_json_schema_2020_12(schema)
        bounded = bound_schema_depth(schema, max_depth=8)
        assert bounded["properties"]["pair"]["prefixItems"][0]["type"] == "string"


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

    def test_draft_04_tool_fails_registration(self):
        import asyncio

        from nitrostack import injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.context import ExecutionContext
        from nitrostack.core.di import DIContainer

        draft_schema = {
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "object",
            "properties": {"value": {"type": "string"}},
        }

        @injectable()
        class DraftController:
            @tool(name="legacy_echo", description="echo", input_schema=draft_schema)
            async def legacy_echo(self, input, context: ExecutionContext) -> str:
                return "ok"

        @module(name="DraftSchema", controllers=[DraftController])
        class DraftModule:
            pass

        @mcp_app(module=DraftModule, server=ServerConfig(name="draft-schema"))
        class DraftApp:
            pass

        DIContainer.reset()
        try:
            with pytest.raises(UnsupportedJsonSchemaError, match="draft-04"):
                asyncio.run(McpApplicationFactory.create(DraftApp))
        finally:
            DIContainer.reset()

    def test_valid_2020_12_tool_registers(self):
        import asyncio

        from nitrostack import injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.context import ExecutionContext
        from nitrostack.core.di import DIContainer

        class EchoIn(BaseModel):
            value: str = ""

        @injectable()
        class ModernController:
            @tool(name="modern_echo", description="echo", input_schema=EchoIn)
            async def modern_echo(self, input: EchoIn, context: ExecutionContext) -> str:
                return input.value

        @module(name="ModernSchema", controllers=[ModernController])
        class ModernModule:
            pass

        @mcp_app(module=ModernModule, server=ServerConfig(name="modern-schema"))
        class ModernApp:
            pass

        DIContainer.reset()
        try:
            app = asyncio.run(McpApplicationFactory.create(ModernApp))
            assert "modern_echo" in app._tools
            listed = app._tool_input_schema(app._tools["modern_echo"].input_model)
            assert listed["$schema"] == JSON_SCHEMA_2020_12_URI
        finally:
            DIContainer.reset()

    def test_tuple_items_tool_fails_registration(self):
        import asyncio

        from nitrostack import injectable, module, tool
        from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
        from nitrostack.core.context import ExecutionContext
        from nitrostack.core.di import DIContainer

        tuple_schema = {
            "type": "object",
            "properties": {
                "pair": {"type": "array", "items": [{"type": "string"}, {"type": "number"}]}
            },
        }

        @injectable()
        class TupleController:
            @tool(name="tuple_echo", description="echo", input_schema=tuple_schema)
            async def tuple_echo(self, input, context: ExecutionContext) -> str:
                return "ok"

        @module(name="TupleSchema", controllers=[TupleController])
        class TupleModule:
            pass

        @mcp_app(module=TupleModule, server=ServerConfig(name="tuple-schema"))
        class TupleApp:
            pass

        DIContainer.reset()
        try:
            with pytest.raises(UnsupportedJsonSchemaError, match="items"):
                asyncio.run(McpApplicationFactory.create(TupleApp))
        finally:
            DIContainer.reset()
