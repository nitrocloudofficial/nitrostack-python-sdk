from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import BaseModel

from nitrostack import (
    ExecutionContext,
    ToolExamples,
    ToolInvocation,
    injectable,
    module,
    tool,
)
from nitrostack.core.app import McpApplication
from nitrostack.core.visibility import SessionVisibilityStore, VisibilityTransform
from nitrostack.runtime.request_ctx import RequestContext, request_ctx


class EmptyInput(BaseModel):
    pass


class AdminInput(BaseModel):
    secret_value: str


@injectable()
class VisibilityTools:
    @tool("public_tool", "public", EmptyInput)
    async def public(self, input: EmptyInput, context: ExecutionContext):
        return "public"

    @tool("admin_tool", "admin", AdminInput, visibility="hidden", default_visible=False)
    async def admin(self, input: AdminInput, context: ExecutionContext):
        return input.secret_value

    @tool("unlock", "unlock", EmptyInput)
    async def unlock(self, input: EmptyInput, context: ExecutionContext):
        context.enable_tools(["admin_tool", "not_registered"])
        return "ok"

    @tool("lock", "lock", EmptyInput)
    async def lock(self, input: EmptyInput, context: ExecutionContext):
        context.disable_tools(["admin_tool"])
        return "ok"


@module(name="visibility", providers=[VisibilityTools])
class VisibilityModule:
    pass


def _app():
    app = McpApplication(VisibilityModule)
    app.protocol_era = "legacy"
    return app


def _request(session_id="session-a"):
    return RequestContext(session=SimpleNamespace(id=session_id), request_id="1")


def test_session_store_lru_eviction():
    now = [0.0]
    store = SessionVisibilityStore(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    store.update(("a", "u"), ["tool"], True)
    store.update(("b", "u"), ["tool"], True)
    assert store.is_visible(("a", "u"), "tool", False)
    store.update(("c", "u"), ["tool"], True)
    assert not store.is_visible(("b", "u"), "tool", False)


def test_session_store_ttl_expiry_uses_fake_clock():
    now = [0.0]
    store = SessionVisibilityStore(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    store.update(("session", "subject"), ["tool"], True)
    now[0] = 11
    assert not store.is_visible(("session", "subject"), "tool", False)


def test_session_key_requires_session_and_includes_verified_subject():
    assert VisibilityTransform.session_key(None, "user-a") is None
    assert VisibilityTransform.session_key("client-session", "user-a") != (
        VisibilityTransform.session_key("client-session", "user-b")
    )


def test_tool_default_visible_preserves_existing_positional_arguments():
    examples = ToolExamples(input={"x": 1}, output={"y": 2})
    invocation = ToolInvocation(invoking="Working", invoked="Worked")
    metadata = {"custom": "value"}

    @tool(
        "positional_compat",
        "compatibility",
        EmptyInput,
        "Compatibility",
        None,
        None,
        "forbidden",
        "hidden",
        examples,
        invocation,
        metadata,
    )
    async def positional_compat(self, input: EmptyInput, context: ExecutionContext):
        return "ok"

    config = positional_compat._mcp_tool_config
    assert config.examples is examples
    assert config.invocation is invocation
    assert config.metadata is metadata
    assert config.visibility == "hidden"
    assert config.default_visible is False


def test_tools_list_and_call_enforce_visibility_and_isolate_sessions():
    async def run():
        app = _app()
        list_handler = app.mcp_server.request_handlers[
            __import__("mcp.types", fromlist=["ListToolsRequest"]).ListToolsRequest
        ]
        request = _request()
        token = request_ctx.set(request)
        try:
            listed = await list_handler(SimpleNamespace(params=None))
            assert "admin_tool" not in [item.name for item in listed.root.tools]
            denied = await app._call_tool("admin_tool", {})
            assert denied.is_error

            enabled = await app._call_tool("unlock", {})
            assert not enabled.is_error
            listed = await list_handler(SimpleNamespace(params=None))
            assert "admin_tool" in [item.name for item in listed.root.tools]

            entry = app._tools["admin_tool"]
            other_session = app._visibility_session_key(_request("session-b"))
            assert not app.visibility_transform.is_visible(entry, other_session)
            other_subject = ("session-a", "verified-user-b")
            assert not app.visibility_transform.is_visible(entry, other_subject)

            disabled = await app._call_tool("lock", {})
            assert not disabled.is_error
            listed = await list_handler(SimpleNamespace(params=None))
            assert "admin_tool" not in [item.name for item in listed.root.tools]
        finally:
            request_ctx.reset(token)

    asyncio.run(run())


def test_sessionless_mutation_is_noop_but_static_default_applies():
    app = _app()
    context = ExecutionContext(request_id="r")
    app._configure_visibility_context(context, None)
    context.enable_tools(["admin_tool"])
    assert not app.visibility_transform.is_visible(app._tools["admin_tool"], None)
    assert app.visibility_transform.is_visible(app._tools["public_tool"], None)


def test_http_session_id_is_transport_owned_and_keyed_with_verified_subject(monkeypatch):
    """Live HTTP requests use validated session ids and verified subjects for visibility."""
    from starlette.testclient import TestClient

    from nitrostack import DIContainer
    from nitrostack.auth.jwt import JWTModule, JWTService
    from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
    from nitrostack.transports.http import build_http_app

    async def build():
        @mcp_app(module=VisibilityModule, server=ServerConfig(name="visibility-http", protocol_era="legacy"))
        class HttpVisibilityApp:
            pass

        return await McpApplicationFactory.create(HttpVisibilityApp)

    monkeypatch.setenv("JWT_SECRET", "visibility-test-secret")
    DIContainer.reset()
    JWTModule.for_root(secret_env_var="JWT_SECRET")
    app = asyncio.run(build())
    http_app = build_http_app(
        app, protocol_era="legacy", wire_mode="sessionful", enable_cors=True
    )
    token = DIContainer.get_instance().resolve(JWTService).create_token({"sub": "owner"})
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "visibility-test", "version": "1"},
        },
    }

    def decode_response(response):
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            for line in response.text.replace("\r\n", "\n").splitlines():
                if line.startswith("data:"):
                    return __import__("json").loads(line[len("data:"):].strip())
            raise AssertionError(f"No JSON-RPC response in {response.text!r}")
        return response.json()

    with TestClient(http_app) as client:
        session_a = client.post("/mcp", headers=auth_headers, json=init_body)
        session_b = client.post("/mcp", headers=auth_headers, json=init_body)
        assert session_a.status_code == session_b.status_code == 200
        id_a = session_a.headers["mcp-session-id"]
        id_b = session_b.headers["mcp-session-id"]
        assert id_a != id_b

        for session_id in (id_a, id_b):
            client.post(
                "/mcp",
                headers={**auth_headers, "mcp-session-id": session_id},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )

        def rpc(session_id, method, params, request_id=2, headers=auth_headers):
            response = client.post(
                "/mcp",
                headers={**headers, "mcp-session-id": session_id},
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            )
            assert response.status_code == 200, response.text
            return decode_response(response)["result"]

        def call(session_id, name, request_id=2, headers=auth_headers, arguments=None):
            return rpc(
                session_id,
                "tools/call",
                {"name": name, "arguments": arguments or {}},
                request_id,
                headers,
            )

        denied_before_unlock = call(id_a, "admin_tool")
        assert denied_before_unlock["isError"] is True
        unlocked = call(id_a, "unlock", request_id=3)
        assert unlocked["isError"] is False

        listed_a = rpc(id_a, "tools/list", {}, request_id=4)
        listed_b = rpc(id_b, "tools/list", {}, request_id=5)
        names_a = {entry["name"] for entry in listed_a["tools"]}
        names_b = {entry["name"] for entry in listed_b["tools"]}
        assert "admin_tool" in names_a
        assert "admin_tool" not in names_b

        allowed_call = call(
            id_a, "admin_tool", request_id=6, arguments={"secret_value": "private"}
        )
        denied_other_session = call(id_b, "admin_tool", request_id=7)
        assert allowed_call["isError"] is False
        assert denied_other_session["isError"] is True

        # A different verified subject using the same validated session id gets
        # an independent visibility key.
        other_token = DIContainer.get_instance().resolve(JWTService).create_token({"sub": "intruder"})
        other_headers = {**auth_headers, "Authorization": f"Bearer {other_token}"}
        listed_other_subject = rpc(id_a, "tools/list", {}, request_id=8, headers=other_headers)
        assert "admin_tool" not in {entry["name"] for entry in listed_other_subject["tools"]}

        # A client-supplied random ID is rejected; it cannot select stored state.
        arbitrary = client.post(
            "/mcp",
            headers={**auth_headers, "mcp-session-id": "client-chosen"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert arbitrary.status_code == 404
    DIContainer.reset()


def test_search_tools_uses_current_request_visibility_for_names_and_schemas(monkeypatch):
    from starlette.testclient import TestClient

    from nitrostack import DIContainer
    from nitrostack.auth.jwt import JWTModule, JWTService
    from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app
    from nitrostack.transports.http import build_http_app
    from nitrostack.transforms.search import RegexSearchTransform

    async def build():
        @mcp_app(
            module=VisibilityModule,
            server=ServerConfig(name="visibility-search-http", protocol_era="legacy"),
            transforms=[RegexSearchTransform(always_visible=["unlock", "lock"])],
        )
        class SearchVisibilityApp:
            pass

        return await McpApplicationFactory.create(SearchVisibilityApp)

    monkeypatch.setenv("JWT_SECRET", "visibility-search-test-secret")
    DIContainer.reset()
    try:
        JWTModule.for_root(secret_env_var="JWT_SECRET")
        app = asyncio.run(build())
        http_app = build_http_app(
            app, protocol_era="legacy", wire_mode="sessionful", enable_cors=True
        )
        jwt = DIContainer.get_instance().resolve(JWTService)
        headers = {
            "Authorization": f"Bearer {jwt.create_token({'sub': 'search-owner'})}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "visibility-search-test", "version": "1"},
            },
        }

        def decode(response):
            if response.headers.get("content-type", "").startswith("text/event-stream"):
                import json

                data = next(line[5:].strip() for line in response.text.splitlines() if line.startswith("data:"))
                return json.loads(data)
            return response.json()

        with TestClient(http_app) as client:
            initialized = [
                client.post("/mcp", headers=headers, json=initialize),
                client.post("/mcp", headers=headers, json=initialize),
            ]
            assert all(response.status_code == 200 for response in initialized)
            session_a, session_b = [response.headers["mcp-session-id"] for response in initialized]

            def call(session_id, name, request_id, arguments):
                response = client.post(
                    "/mcp",
                    headers={**headers, "mcp-session-id": session_id},
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    },
                )
                assert response.status_code == 200, response.text
                return decode(response)["result"]

            enabled = call(session_a, "unlock", 2, {})
            assert enabled["isError"] is False

            visible = call(
                session_a,
                "search_tools",
                3,
                {"query": "admin_tool", "detail": "full"},
            )
            hidden = call(
                session_b,
                "search_tools",
                4,
                {"query": "admin_tool", "detail": "full"},
            )
            visible_results = visible["structuredContent"]["results"]
            hidden_results = hidden["structuredContent"]["results"]
            assert any(result["name"] == "admin_tool" for result in visible_results)
            admin_result = next(result for result in visible_results if result["name"] == "admin_tool")
            assert "secret_value" in str(admin_result)
            assert all(result["name"] != "admin_tool" for result in hidden_results)
            assert "secret_value" not in str(hidden_results)
    finally:
        DIContainer.reset()
