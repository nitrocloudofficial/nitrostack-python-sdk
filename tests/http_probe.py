"""Shared probes of the actual official MCP HTTP mount (no sidecar doubles)."""

import asyncio
import json
import os
from contextlib import contextmanager
from unittest.mock import patch

from mcp_types import PROTOCOL_VERSION_META_KEY
from pydantic import BaseModel
from starlette.testclient import TestClient

from nitrostack import DIContainer, module, resource, tool
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.protocol.version import LEGACY_PROTOCOL_VERSION, MODERN_PROTOCOL_VERSION


class EchoInput(BaseModel):
    value: str = "ok"


def make_app(era="auto"):
    """Resolve era deterministically without leaking process environment or DI."""
    DIContainer.reset()

    class ProbeTools:
        @tool(name="echo", description="Echo and expose verified context", input_schema=EchoInput,
              task_support="optional")
        async def echo(self, input, context):
            return {"value": input.value, "user": context.user,
                    "protocol_version": context.protocol_version,
                    "mirrored": context.mcp_param_headers}

        @resource(uri="probe://info", name="Probe", description="Readable probe")
        async def info(self, context):
            return "probe resource"

    @module(name="http-probe", providers=[ProbeTools])
    class ProbeModule:
        pass

    @mcp_app(module=ProbeModule, server=ServerConfig(name="http-probe", protocol_era=era))
    class ProbeApp:
        pass

    with patch.dict(os.environ, {"NITRO_MCP_PROTOCOL_VERSION": era, "MCP_STATELESS": ""}):
        return asyncio.run(McpApplicationFactory.create(ProbeApp))


def envelope(method="tools/list", params=None, *, version=MODERN_PROTOCOL_VERSION, modern=True):
    params = dict(params or {})
    if modern:
        params["_meta"] = {
            PROTOCOL_VERSION_META_KEY: version,
            "io.modelcontextprotocol/clientInfo": {"name": "http-probe", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
            **params.get("_meta", {}),
        }
    return {"jsonrpc": "2.0", "id": "probe", "method": method, "params": params}


def headers(method="tools/list", *, name=None, version=MODERN_PROTOCOL_VERSION):
    result = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
              "MCP-Protocol-Version": version, "Mcp-Method": method}
    if name is not None:
        result["Mcp-Name"] = name
    return result


def decode(response):
    """Handle JSON or a completed request's SSE response explicitly."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        messages = [json.loads(line[5:].strip()) for line in response.text.splitlines()
                    if line.startswith("data:")]
        return next(message for message in messages if "id" in message)
    return response.json()


def post(client, method="tools/list", params=None, *, modern=True, extra_headers=None,
         version=MODERN_PROTOCOL_VERSION):
    return client.post("/mcp", json=envelope(method, params, modern=modern, version=version),
                       headers={**headers(method, name=(params or {}).get("name") or (params or {}).get("uri"),
                                          version=version), **(extra_headers or {})})


@contextmanager
def http_client(era="auto", *, app=None, json_response=True, **options):
    app = app or make_app(era)
    http = app.get_combined_app(json_response=json_response, **options)
    try:
        with TestClient(http) as client:
            if era == "legacy":
                response = post(client, "initialize", {
                    "protocolVersion": LEGACY_PROTOCOL_VERSION, "capabilities": {},
                    "clientInfo": {"name": "legacy-probe", "version": "1"},
                }, modern=False, version=LEGACY_PROTOCOL_VERSION)
                assert response.status_code == 200, response.text
                assert "result" in decode(response), response.text
                session = response.headers.get("mcp-session-id")
                assert session
                client.headers["mcp-session-id"] = session
                client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                            headers=headers("notifications/initialized", version=LEGACY_PROTOCOL_VERSION))
            yield app, http, client
    finally:
        DIContainer.reset()
