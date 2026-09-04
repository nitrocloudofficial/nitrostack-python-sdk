"""Phase 6 — HTTP tools/call through a pipe (Python-only integration)."""
from __future__ import annotations

import asyncio
import os
import sys

from pydantic import BaseModel
from starlette.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import DIContainer, ExecutionContext, injectable, module, tool, use_guards, use_pipes
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.auth.jwt import JWTModule, JWTService
from nitrostack.core.pipeline import JwtGuard, PipeMetadata
from nitrostack.transports.http import build_http_app

JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class GreetInput(BaseModel):
    name: str = "world"


class AllowAllGuard:
    async def can_activate(self, context: ExecutionContext) -> bool:
        return True


class UpperNamePipe:
    async def transform(self, value, metadata: PipeMetadata):
        if hasattr(value, "name"):
            value.name = str(value.name).upper()
        return value


@injectable()
class GreetController:
    @tool(name="greet", description="Greet after the pipe", input_schema=GreetInput)
    @use_guards(AllowAllGuard)
    @use_pipes(UpperNamePipe)
    async def greet(self, input: GreetInput, context: ExecutionContext) -> dict:
        return {"hello": input.name}

    @tool(
        name="slow_greet",
        description="Task-mode greet after the pipe",
        input_schema=GreetInput,
        task_support="required",
    )
    @use_guards(AllowAllGuard)
    @use_pipes(UpperNamePipe)
    async def slow_greet(self, input: GreetInput, context: ExecutionContext) -> dict:
        if context.task is not None:
            context.task.update_progress("started")
            context.task.update_progress("finishing")
        return {"hello": input.name}


@module(name="lifecycle", controllers=[GreetController], providers=[AllowAllGuard, UpperNamePipe])
class LifecycleModule:
    pass


def test_http_tool_call_runs_pipe_then_handler():
    @mcp_app(module=LifecycleModule, server=ServerConfig(name="lifecycle", stateless=True))
    class App:
        pass

    app = asyncio.run(McpApplicationFactory.create(App))
    http_app = build_http_app(app, enable_cors=True, stateless=True, json_response=True)
    with TestClient(http_app) as client:
        health = client.get("/mcp/health")
        assert health.status_code == 200
        resp = client.post(
            "/mcp",
            headers=JSON_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "greet", "arguments": {"name": "ada"}},
            },
        )
    assert resp.status_code == 200, resp.text
    payload = resp.json()["result"]
    assert payload["isError"] is False
    assert payload["structuredContent"]["hello"] == "ADA"


def test_http_guard_pipe_task_progress_and_completion():
    @mcp_app(module=LifecycleModule, server=ServerConfig(name="lifecycle-task", stateless=True))
    class App:
        pass

    app = asyncio.run(McpApplicationFactory.create(App))
    http_app = build_http_app(app, enable_cors=True, stateless=True, json_response=True)
    with TestClient(http_app) as client:
        created = client.post(
            "/mcp",
            headers=JSON_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "slow_greet",
                    "arguments": {"name": "ada"},
                    "task": {"ttl": 60},
                    "_meta": {"progressToken": "tok-life"},
                },
            },
        )
        assert created.status_code == 200, created.text
        created_body = created.json()["result"]
        task_id = created_body.get("task", {}).get("taskId") or created_body.get("taskId")
        assert task_id, created_body

        status = None
        for _ in range(40):
            polled = client.post(
                "/mcp",
                headers=JSON_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tasks/get",
                    "params": {"taskId": task_id},
                },
            )
            assert polled.status_code == 200, polled.text
            status = polled.json()["result"].get("status")
            if status in {"completed", "failed", "cancelled"}:
                break
            asyncio.run(asyncio.sleep(0.05))

        assert status == "completed"
        payload = polled.json()["result"]
        assert payload.get("result") is not None
        result_payload = payload["result"]
        assert result_payload.get("isError") is False
        hello = (result_payload.get("structuredContent") or {}).get("hello")
        if hello is None:
            hello = result_payload["content"][0]["text"]
            assert "ADA" in hello
        else:
            assert hello == "ADA"



@injectable()
class JwtGreetController:
    @tool(name="jwt_greet", description="Greet after JWT guard", input_schema=GreetInput)
    @use_guards(JwtGuard)
    @use_pipes(UpperNamePipe)
    async def greet(self, input: GreetInput, context: ExecutionContext) -> dict:
        return {"hello": input.name, "sub": getattr(context.auth, "subject", None)}


@module(
    name="jwt-lifecycle",
    controllers=[JwtGreetController],
    providers=[JwtGuard, UpperNamePipe],
)
class JwtLifecycleModule:
    pass


def test_http_jwt_guard_allows_valid_token_and_rejects_missing(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "lifecycle-jwt")
    JWTModule.for_root(secret_env_var="JWT_SECRET", audience="mcp", issuer="nitro")
    token = DIContainer.get_instance().resolve(JWTService).create_token({"sub": "ada"})

    @mcp_app(module=JwtLifecycleModule, server=ServerConfig(name="jwt-lifecycle", stateless=True))
    class App:
        pass

    app = asyncio.run(McpApplicationFactory.create(App))
    http_app = build_http_app(app, enable_cors=True, stateless=True, json_response=True)
    with TestClient(http_app) as client:
        denied = client.post(
            "/mcp",
            headers=JSON_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "jwt_greet", "arguments": {"name": "ada"}},
            },
        )
        assert denied.status_code == 200, denied.text
        denied_body = denied.json()["result"]
        assert denied_body.get("isError") is True

        allowed = client.post(
            "/mcp",
            headers=JSON_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "jwt_greet",
                    "arguments": {"name": "ada"},
                    "_meta": {"authorization": f"Bearer {token}"},
                },
            },
        )
    assert allowed.status_code == 200, allowed.text
    payload = allowed.json()["result"]
    assert payload.get("isError") is False, payload
    content = payload.get("structuredContent") or {}
    assert content.get("hello") == "ADA"
    assert content.get("sub") == "ada"


def test_transport_headers_take_precedence_over_meta():
    """Client-controlled _meta must not override real transport credentials."""
    from nitrostack.core.app import _auth_metadata_from_request_ctx

    class FakeRequest:
        headers = {"authorization": "Bearer real-token", "x-api-key": "real-key"}

    class FakeCtx:
        meta = {
            "authorization": "Bearer meta-token",
            "x-api-key": "meta-key",
            "_oauth": "meta-oauth",
        }
        request = FakeRequest()

    extra = _auth_metadata_from_request_ctx(FakeCtx())
    assert extra["authorization"] == "Bearer real-token"
    assert extra["x-api-key"] == "real-key"
    # _meta-only slots with no transport counterpart still pass through.
    assert extra["_oauth"] == "meta-oauth"


def test_meta_auth_used_when_transport_has_no_headers():
    """STDIO-style contexts (no HTTP request) fall back to _meta auth."""
    from nitrostack.core.app import _auth_metadata_from_request_ctx

    class FakeCtx:
        meta = {"authorization": "Bearer meta-token"}
        request = None

    extra = _auth_metadata_from_request_ctx(FakeCtx())
    assert extra["authorization"] == "Bearer meta-token"
