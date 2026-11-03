"""Issue #41: output schemas keep the null branch of Optional fields."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema
import pytest
from pydantic import BaseModel

from nitrostack import DIContainer, NitroTestingModule, module, tool
from nitrostack.core.app import inspector_friendly_schema, tool_json_schema

ROOT = Path(__file__).resolve().parents[1]
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "schema-client", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


class Inner(BaseModel):
    note: Optional[str] = None


class Out(BaseModel):
    maybe: Optional[str] = None
    n: Optional[int] = None
    required_str: str = "x"
    inner: Optional[Inner] = None
    items: List[Optional[int]] = []


class Empty(BaseModel):
    pass


def _accepts_null(prop: Dict[str, Any]) -> bool:
    for key in ("anyOf", "oneOf"):
        if any(v.get("type") == "null" for v in prop.get(key, [])):
            return True
    types = prop.get("type")
    return isinstance(types, list) and "null" in types


def test_output_schema_keeps_null_for_model():
    props = tool_json_schema(Out)["properties"]
    assert _accepts_null(props["maybe"])
    assert _accepts_null(props["n"])
    assert _accepts_null(props["inner"])


def test_output_schema_keeps_null_for_dict():
    props = tool_json_schema(Out.model_json_schema())["properties"]
    assert _accepts_null(props["maybe"])
    assert _accepts_null(props["n"])


def test_output_schema_keeps_null_in_nested_models_and_arrays():
    schema = tool_json_schema(Out)
    assert _accepts_null(schema["$defs"]["Inner"]["properties"]["note"])
    assert _accepts_null(schema["properties"]["items"]["items"])


def test_output_schema_does_not_loosen_required_fields():
    props = tool_json_schema(Out)["properties"]
    assert not _accepts_null(props["required_str"])
    assert props["required_str"]["type"] == "string"


def test_output_schema_validates_null_payload():
    payload = {"maybe": None, "n": None, "required_str": "x", "inner": None, "items": [None, 1]}
    jsonschema.validate(payload, tool_json_schema(Out))


def test_input_schema_still_unwraps_optional_for_inspector():
    props = inspector_friendly_schema(Out.model_json_schema())["properties"]
    assert not _accepts_null(props["maybe"])
    assert props["maybe"]["type"] == "string"
    assert props["n"]["type"] == "integer"


class Tools:
    @tool("profile", "Return empty optionals", Empty, output_schema=Out)
    async def profile(self, input, context):
        return Out()


@module(name="schema-app", providers=[Tools])
class Root:
    pass


@pytest.mark.asyncio
async def test_listed_schema_accepts_the_real_result_in_process():
    DIContainer.reset()
    try:
        harness = await NitroTestingModule.create(Root)
        entry = next(iter(harness.app._tools.values()))
        definition = harness.app._build_tool_definition(entry)
        result = await harness.call_tool("profile", {"input": {}})
        assert result["maybe"] is None
        jsonschema.validate(result, definition.output_schema)
    finally:
        DIContainer.reset()


class StdioServer:
    """Runs the test server as a real subprocess and talks JSON-RPC over its stdio."""

    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "output_schema_stdio_server.py")],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        self.next_id = 1

    def rpc(self, method, params):
        self.process.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": self.next_id, "method": method,
            "params": {**params, "_meta": MODERN_META},
        }) + "\n")
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        assert response["id"] == self.next_id
        self.next_id += 1
        return response

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            for stream in (self.process.stdout, self.process.stderr):
                stream.close()


@pytest.mark.timeout(60)
def test_stdio_client_can_validate_null_results_against_the_advertised_schema():
    server = StdioServer()
    try:
        tools = server.rpc("tools/list", {})["result"]["tools"]
        schema = next(t for t in tools if t["name"] == "profile")["outputSchema"]
        assert _accepts_null(schema["properties"]["nickname"])
        assert _accepts_null(schema["properties"]["age"])

        result = server.rpc("tools/call", {"name": "profile", "arguments": {}})["result"]
        assert result.get("isError", False) is False
        data = result["structuredContent"]
        assert data["nickname"] is None and data["age"] is None
        # This is what an MCP client does; it used to fail with "must be string".
        jsonschema.validate(data, schema)
    finally:
        server.close()
