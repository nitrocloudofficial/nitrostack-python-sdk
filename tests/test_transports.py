"""
Phase 3 (HTTP/dual transport) tests.

Follows the repo's existing test convention (plain functions runnable both
via `python tests/test_transports.py` and picked up by pytest as `test_*`)
rather than introducing a new async test-runner dependency.
"""
import asyncio
import json as _json
import os
import socket
import sys

# Ensure parent directory is in sys.path so nitrostack can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pydantic import BaseModel
from starlette.testclient import TestClient

import mcp.types as types
from mcp.server.lowlevel.server import request_ctx, RequestContext
from mcp.server.experimental.request_context import Experimental
from nitrostack import injectable, module, tool, ExecutionContext, DIContainer
from nitrostack.core.app import McpApplication, McpApplicationFactory, ServerConfig, mcp_app
from nitrostack.transports.http import build_http_app
from nitrostack.transports.dual import run_dual

INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "phase3-tests", "version": "1.0"},
    },
}
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class EchoInput(BaseModel):
    value: str = ""


@injectable(deps=[])
class CounterService:
    def __init__(self) -> None:
        self.count = 0

    def increment(self) -> int:
        self.count += 1
        return self.count


@injectable(deps=[CounterService])
class TransportsTestController:
    def __init__(self, counter: CounterService) -> None:
        self.counter = counter

    @tool(name="echo", description="Echo the input value", input_schema=EchoInput)
    async def echo(self, input: EchoInput, context: ExecutionContext) -> str:
        return input.value

    @tool(name="bump_counter", description="Increment the shared counter and return its value", input_schema=EchoInput)
    async def bump_counter(self, input: EchoInput, context: ExecutionContext) -> int:
        return self.counter.increment()

    @tool(
        name="progress_task",
        description="Task-mode tool that reports progress a few times before completing",
        input_schema=EchoInput,
        task_support="required",
    )
    async def progress_task(self, input: EchoInput, context: ExecutionContext) -> str:
        for step in (1, 2, 3):
            context.task.update_progress(f"step-{step}")
            await asyncio.sleep(0)  # yield control so the notification task can run
        return "done"


@module(name="transports_test", controllers=[TransportsTestController], providers=[CounterService])
class TransportsTestModule:
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _build_app() -> McpApplication:
    @mcp_app(module=TransportsTestModule, server=ServerConfig(name="transports-test-server"))
    class _TestApp:
        pass

    return await McpApplicationFactory.create(_TestApp)


def _initialize(client: TestClient) -> str:
    resp = client.post("/mcp", headers=JSON_HEADERS, json=INITIALIZE_BODY)
    assert resp.status_code == 200, resp.text
    session_id = resp.headers.get("mcp-session-id")
    assert session_id, "expected mcp-session-id header on initialize response"
    client.post(
        "/mcp",
        headers={**JSON_HEADERS, "mcp-session-id": session_id},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return session_id


def _call_tool(client: TestClient, session_id: str, name: str, arguments: dict, req_id: int = 2):
    resp = client.post(
        "/mcp",
        headers={**JSON_HEADERS, "mcp-session-id": session_id},
        json={"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    return resp


def _parse_sse_json(text: str) -> dict:
    """Extract the (last) JSON-RPC payload out of a possibly multi-event SSE body."""
    payload = None
    for raw_event in text.replace("\r\n", "\n").split("\n\n"):
        for line in raw_event.split("\n"):
            if line.startswith("data:"):
                data = line[len("data:"):].strip()
                if data:
                    payload = _json.loads(data)
    if payload is None:
        raise AssertionError(f"No JSON-RPC payload found in SSE body: {text!r}")
    return payload


def _extract_json_rpc(resp) -> dict:
    """Streamable HTTP responses to POST may come back as a single JSON object
    or as one SSE `message` event depending on `json_response`; nitrostack's
    default wiring uses SSE, matching how a real MCP client is expected to
    consume this endpoint."""
    content_type = resp.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        return _parse_sse_json(resp.text)
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Basic HTTP wiring: health check + CORS
# ---------------------------------------------------------------------------

def test_http_health_and_cors():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True)

    with TestClient(http_app) as client:
        health = client.get("/mcp/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "ok"
        assert body["transport"] == "streamable-http"

        preflight = client.options(
            "/mcp",
            headers={"Origin": "http://example.com", "Access-Control-Request-Method": "POST"},
        )
        assert preflight.status_code == 200
        assert preflight.headers.get("access-control-allow-origin") == "*"
        allow_headers = preflight.headers.get("access-control-allow-headers", "")
        assert "Mcp-Session-Id" in allow_headers
        assert "Mcp-Name" in allow_headers
        assert "Mcp-Method" in allow_headers
        assert "MCP-Protocol-Version" in allow_headers

        root = client.get("/")
        assert root.status_code == 200
        assert "text/html" in root.headers.get("content-type", "")
        assert "MCP" in root.text
        assert "/widgets/preview" in root.text

        version = client.get("/json/version")
        assert version.status_code == 200
        version_body = version.json()
        assert version_body["transport"] == "mcp"
        assert version_body["endpoints"]["mcp"] == "/mcp"
        assert version_body["endpoints"]["sse"] == "/sse"

        json_list = client.get("/json")
        assert json_list.status_code == 200
        assert json_list.json() == []

        favicon = client.get("/favicon.ico")
        assert favicon.status_code == 204

    print("Success! /mcp/health, GET /, and Chrome /json probes behave as expected.")


# ---------------------------------------------------------------------------
# 2. HTTP tool call parity with the in-process testing harness
# ---------------------------------------------------------------------------

def test_http_tool_call_parity():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True)

    with TestClient(http_app) as client:
        session_id = _initialize(client)
        resp = _call_tool(client, session_id, "echo", {"input": {"value": "hello-http"}})
        assert resp.status_code == 200, resp.text
        result = _extract_json_rpc(resp)["result"]
        assert result["content"][0]["text"] == "hello-http"

        unwrapped = _call_tool(client, session_id, "echo", {"value": "top-level"}, req_id=3)
        assert unwrapped.status_code == 200, unwrapped.text
        unwrapped_result = _extract_json_rpc(unwrapped)["result"]
        assert unwrapped_result["content"][0]["text"] == "top-level"

        listed = client.post(
            "/mcp",
            headers={**JSON_HEADERS, "mcp-session-id": session_id},
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200, listed.text
        tools = _extract_json_rpc(listed)["result"]["tools"]
        echo = next(item for item in tools if item["name"] == "echo")
        echo_props = echo["inputSchema"]["properties"]
        assert "value" in echo_props
        assert "input" not in echo_props

    print("Success! HTTP tool call returns the expected JSON-RPC result shape.")


# ---------------------------------------------------------------------------
# 3. Session isolation / lifecycle
# ---------------------------------------------------------------------------

def test_session_isolation_and_termination():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True)

    with TestClient(http_app) as client:
        session_a = _initialize(client)
        session_b = _initialize(client)
        assert session_a != session_b, "each session must get a distinct mcp-session-id"

        # Terminate session A only.
        del_resp = client.delete("/mcp", headers={**JSON_HEADERS, "mcp-session-id": session_a})
        assert del_resp.status_code in (200, 204), del_resp.text

        # Session A is now gone (spec: 404 for unknown/expired session).
        resp_a = _call_tool(client, session_a, "echo", {"input": {"value": "x"}})
        assert resp_a.status_code == 404

        # Session B is unaffected by session A's termination.
        resp_b = _call_tool(client, session_b, "echo", {"input": {"value": "still-alive"}})
        assert resp_b.status_code == 200, resp_b.text
        assert _extract_json_rpc(resp_b)["result"]["content"][0]["text"] == "still-alive"

    print("Success! Sessions are isolated — terminating one does not affect the other.")


# ---------------------------------------------------------------------------
# 4. Max concurrent sessions cap
# ---------------------------------------------------------------------------

def test_max_sessions_cap():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True, max_sessions=1)

    with TestClient(http_app) as client:
        _initialize(client)  # first session: at capacity now

        resp = client.post("/mcp", headers=JSON_HEADERS, json={**INITIALIZE_BODY, "id": 99})
        assert resp.status_code == 429, resp.text
        error = resp.json()["error"]
        assert error["code"] == -32000
        assert "capacity" in error["message"].lower()

    print("Success! A new session beyond max_sessions gets HTTP 429.")


# ---------------------------------------------------------------------------
# 5. Stateless mode (2026-07-28 spec's "no initialize handshake" primitive)
# ---------------------------------------------------------------------------

def test_stateless_mode_skips_handshake():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True, stateless=True)

    with TestClient(http_app) as client:
        # No `initialize` call first, and no session id at all.
        resp = client.post(
            "/mcp",
            headers=JSON_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"input": {"value": "no-handshake"}}},
            },
        )
        assert resp.status_code == 200, resp.text
        assert _extract_json_rpc(resp)["result"]["content"][0]["text"] == "no-handshake"
        assert "mcp-session-id" not in resp.headers

    print("Success! Stateless mode accepts tools/call with no prior initialize and no session id.")


# ---------------------------------------------------------------------------
# 6. DI singletons shared between the HTTP transport and a direct
#    (stdio-equivalent) dispatch on the same low-level server.
# ---------------------------------------------------------------------------

def test_di_singletons_shared_across_transports():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True)

    with TestClient(http_app) as client:
        session_id = _initialize(client)
        resp = _call_tool(client, session_id, "bump_counter", {"input": {}})
        assert resp.status_code == 200, resp.text
        http_count = int(_extract_json_rpc(resp)["result"]["content"][0]["text"])

    async def _dispatch_direct():
        handler = app.mcp_server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name="bump_counter", arguments={"input": {}}),
        )
        response = await handler(request)
        return int(response.root.content[0].text)

    direct_count = asyncio.run(_dispatch_direct())

    assert http_count == 1
    assert direct_count == 2, "direct dispatch must see the HTTP call's increment (shared DI singleton)"

    print("Success! The same CounterService singleton is shared between the HTTP transport and direct dispatch.")


# ---------------------------------------------------------------------------
# 7. Live progress push: `context.task.update_progress()` sends a real MCP
#    `notifications/progress` over whichever transport's session initiated
#    the task, when the client asked for it via `_meta.progressToken`.
# ---------------------------------------------------------------------------

class _StubSession:
    def __init__(self) -> None:
        self.calls = []

    async def send_progress_notification(self, progress_token, progress, total=None, message=None, related_request_id=None):
        self.calls.append({"progress_token": progress_token, "progress": progress, "message": message})


async def _test_progress_notifications_pushed():
    app = await _build_app()
    handler = app.mcp_server.request_handlers[types.CallToolRequest]
    stub_session = _StubSession()

    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(
            name="progress_task",
            arguments={"input": {"value": ""}},
            task=types.TaskMetadata(ttl=60_000),
            _meta={"progressToken": "tok-abc"},
        ),
    )
    token = request_ctx.set(
        RequestContext(
            request_id="progress-test-req",
            meta=req.params.meta,
            session=stub_session,
            lifespan_context=None,
            experimental=Experimental(task_metadata=req.params.task),
            request=req,
        )
    )
    try:
        response = await handler(req)
    finally:
        request_ctx.reset(token)

    assert isinstance(response.root, types.CreateTaskResult)
    task_id = response.root.task.taskId

    # Wait for the background task to finish (it does 3 quick progress updates).
    get_handler = app.mcp_server.request_handlers[types.GetTaskRequest]
    get_req = types.GetTaskRequest(
        method="tasks/get", params=types.GetTaskRequestParams(taskId=task_id)
    )
    for _ in range(40):
        get_res = await get_handler(get_req)
        if get_res.status in ("completed", "failed", "cancelled"):
            break
        await asyncio.sleep(0.05)

    # Give the fire-and-forget notification tasks a moment to actually run.
    for _ in range(20):
        if len(stub_session.calls) >= 3:
            break
        await asyncio.sleep(0.05)

    assert [c["message"] for c in stub_session.calls] == ["step-1", "step-2", "step-3"]
    assert all(c["progress_token"] == "tok-abc" for c in stub_session.calls)
    assert [c["progress"] for c in stub_session.calls] == [1, 2, 3]

    print("Success! update_progress() pushed live notifications/progress with the client's token.")


def test_progress_notifications_pushed():
    asyncio.run(_test_progress_notifications_pushed())


# ---------------------------------------------------------------------------
# 8. Dual mode: coordinated shutdown (stdio stopping tears down HTTP too)
# ---------------------------------------------------------------------------

async def _test_dual_mode_coordinated_shutdown():
    app = await _build_app()
    http_app = build_http_app(app, enable_cors=True)
    port = _free_port()

    stdio_started = asyncio.Event()
    stdio_stop = asyncio.Event()

    class _StubStdioApp:
        """Duck-typed stand-in so this test doesn't depend on the real
        process's stdin (which may already be at EOF or non-interactive
        under a test runner)."""

        async def _run_stdio(self) -> None:
            stdio_started.set()
            await stdio_stop.wait()

    stub = _StubStdioApp()

    dual_task = asyncio.create_task(run_dual(stub, http_app, host="127.0.0.1", port=port, graceful_timeout=2))

    await asyncio.wait_for(stdio_started.wait(), timeout=5)

    # Give uvicorn a moment to finish binding, then confirm HTTP is live.
    import httpx

    for _ in range(20):
        try:
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.get(f"http://127.0.0.1:{port}/mcp/health", timeout=1)
            if resp.status_code == 200:
                break
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("HTTP transport never became reachable in dual mode")

    assert resp.status_code == 200

    # Stopping the STDIO side should tear down the whole dual-mode run.
    stdio_stop.set()
    await asyncio.wait_for(dual_task, timeout=5)

    # The HTTP port should no longer accept connections.
    try:
        async with httpx.AsyncClient() as http_client:
            await http_client.get(f"http://127.0.0.1:{port}/mcp/health", timeout=1)
        raise AssertionError("HTTP transport should have stopped after STDIO shutdown")
    except httpx.TransportError:
        pass

    print("Success! Stopping STDIO in dual mode also shuts down the HTTP transport (coordinated shutdown).")


def test_dual_mode_coordinated_shutdown():
    asyncio.run(_test_dual_mode_coordinated_shutdown())


# ---------------------------------------------------------------------------
# 9. `/mcp` (no trailing slash) must NOT 307 to `/mcp/`.
#    MCP Inspector's Streamable HTTP client GETs `/mcp` for the SSE stream;
#    a 307 on that GET drops the connection and blanks List Tools.
# ---------------------------------------------------------------------------

def test_mcp_path_does_not_redirect():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True)

    with TestClient(http_app, follow_redirects=False) as client:
        init = client.post("/mcp", headers=JSON_HEADERS, json=INITIALIZE_BODY)
        assert init.status_code == 200, (
            f"POST /mcp should be handled directly, not redirected, got {init.status_code}"
        )
        session_id = init.headers.get("mcp-session-id")
        assert session_id

        client.post(
            "/mcp",
            headers={**JSON_HEADERS, "mcp-session-id": session_id},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        listed = client.post(
            "/mcp",
            headers={**JSON_HEADERS, "mcp-session-id": session_id},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200, (
            f"tools/list on /mcp should not redirect, got {listed.status_code}: {listed.text}"
        )
        payload = _extract_json_rpc(listed)
        names = [t["name"] for t in payload.get("result", {}).get("tools", [])]
        assert "echo" in names

        slashed = client.post(
            "/mcp/",
            headers={**JSON_HEADERS, "mcp-session-id": session_id},
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
        )
        assert slashed.status_code == 200

    print("Success! POST /mcp is handled without a 307 redirect; tools/list works.")


# ---------------------------------------------------------------------------
# 10. Legacy SSE: messages path must use a trailing slash so POSTs hit
#    SseServerTransport instead of being swallowed by Mount("/mcp") /
#    Streamable HTTP (which 406s clients that don't send Streamable Accept).
# ---------------------------------------------------------------------------

def test_legacy_sse_messages_not_swallowed_by_streamable_http():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True)

    with TestClient(http_app) as client:
        # Trailing-slash path reaches SseServerTransport. Unknown session → 404
        # (or 400 for bad id), never Streamable HTTP's Accept-header 406.
        sse_post = client.post(
            "/mcp/messages/?session_id=00000000000000000000000000000000",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=INITIALIZE_BODY,
        )
        assert sse_post.status_code in (400, 404), (
            f"/mcp/messages/ should hit legacy SSE handler, got {sse_post.status_code}: {sse_post.text}"
        )
        assert "Not Acceptable" not in sse_post.text

        # Bare `/mcp/messages` (no trailing slash) is still swallowed by
        # Mount("/mcp") / Streamable HTTP — documents why the slash matters.
        swallowed = client.post(
            "/mcp/messages?session_id=00000000000000000000000000000000",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=INITIALIZE_BODY,
        )
        assert swallowed.status_code == 406, (
            f"bare /mcp/messages should still hit Streamable HTTP (406), got {swallowed.status_code}"
        )

    print("Success! Legacy SSE /mcp/messages/ routes correctly (not swallowed by Streamable HTTP).")


# ---------------------------------------------------------------------------
# 11. Client-header tolerance: `StreamableHTTPServerTransport` matches Accept
#    media types with `startswith` (so `*/*` is rejected with 406). Protocol
#    version is left on the request so later checks see the client value.
# ---------------------------------------------------------------------------

def test_wildcard_and_missing_accept_are_honoured():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True)

    with TestClient(http_app) as client:
        init = client.post(
            "/mcp",
            headers={"Content-Type": "application/json", "Accept": "*/*"},
            json=INITIALIZE_BODY,
        )
        assert init.status_code == 200, (
            f"POST /mcp with Accept: */* should be served, got {init.status_code}: {init.text}"
        )
        session_id = init.headers.get("mcp-session-id")
        assert session_id
        assert _extract_json_rpc(init)["result"]["protocolVersion"]

        client.post(
            "/mcp",
            headers={"Content-Type": "application/json", "Accept": "*/*", "mcp-session-id": session_id},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        listed = client.post(
            "/mcp",
            headers={"Content-Type": "application/json", "Accept": "text/html,*/*;q=0.8", "mcp-session-id": session_id},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200, listed.text
        assert "echo" in [t["name"] for t in _extract_json_rpc(listed)["result"]["tools"]]

        # No Accept header at all means "anything" per RFC 9110. Reaching the
        # session check (400) instead of content negotiation (406) is the proof.
        no_accept = client.get("/mcp", headers={"Accept": ""})
        assert no_accept.status_code == 400, no_accept.text
        assert "Not Acceptable" not in no_accept.text

    print("Success! Wildcard and absent Accept headers no longer 406 on /mcp.")


def test_header_compat_preserves_protocol_version_for_inner_app():
    from nitrostack.transports.http import HeaderCompatMiddleware

    captured: dict[str, list] = {}

    async def inner(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        captured["headers"] = list(scope.get("headers") or [])
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    wrapped = HeaderCompatMiddleware(inner)
    with TestClient(wrapped) as client:
        response = client.post(
            "/mcp",
            headers={**JSON_HEADERS, "MCP-Protocol-Version": "2026-07-28"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
    assert response.status_code == 200, response.text
    headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in captured["headers"]}
    assert headers["mcp-protocol-version"] == "2026-07-28"

    print("Success! HeaderCompatMiddleware keeps MCP-Protocol-Version for the inner app.")


def test_delete_terminates_live_session_and_404s_unknown_one():
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True)

    with TestClient(http_app) as client:
        session_id = _initialize(client)

        terminated = client.delete("/mcp", headers={**JSON_HEADERS, "mcp-session-id": session_id})
        assert terminated.status_code in (200, 204), terminated.text

        # A 404 on DELETE means the session id is unknown (already terminated,
        # or minted by a previous run of the server), not that routing is broken.
        unknown = client.delete("/mcp", headers={**JSON_HEADERS, "mcp-session-id": "0" * 32})
        assert unknown.status_code == 404, unknown.text

    print("Success! DELETE /mcp terminates a live session and 404s an unknown one.")


def test_oauth_register_returns_json_not_html():
    """Inspector Auth-on DCR hits POST /register; HTML 404s parse as invalid OAuth JSON."""
    DIContainer.reset()
    app = asyncio.run(_build_app())
    http_app = build_http_app(app, enable_cors=True, stateless=True)
    with TestClient(http_app) as client:
        resp = client.post("/register", json={"client_name": "inspector"})
        assert resp.status_code == 404
        assert "application/json" in resp.headers.get("content-type", "")
        body = resp.json()
        assert body["error"] == "invalid_request"
        assert "OAuth" in body["error_description"]
        well_known = client.get("/.well-known/oauth-authorization-server")
        assert well_known.status_code == 404
        assert well_known.json()["error"] == "invalid_request"


def test_oauth_configured_skips_not_supported_stubs():
    """Real OAuthModule must not be shadowed by Inspector 'this server does not use OAuth' JSON."""
    from nitrostack.auth.oauth import OAuthModule

    DIContainer.reset()
    OAuthModule.for_root(
        resource_uri="http://localhost:3000/mcp",
        authorization_servers=["http://localhost:3000/oauth"],
        scopes_supported=["read"],
    )
    try:
        app = asyncio.run(_build_app())
        http_app = build_http_app(app, enable_cors=True, stateless=True)
        with TestClient(http_app) as client:
            well_known = client.get("/.well-known/oauth-protected-resource")
            assert well_known.status_code == 404
            ctype = well_known.headers.get("content-type", "")
            if "json" in ctype:
                assert "does not use OAuth" not in well_known.json().get("error_description", "")
            else:
                assert "does not use OAuth" not in well_known.text
            register = client.post("/register", json={"client_name": "inspector"})
            assert register.status_code == 404
            if "json" in register.headers.get("content-type", ""):
                body = register.json()
                assert body.get("error_description") is None or "does not use OAuth" not in body["error_description"]
    finally:
        DIContainer.reset()


if __name__ == "__main__":
    DIContainer.reset()
    test_http_health_and_cors()
    test_http_tool_call_parity()
    test_session_isolation_and_termination()
    test_max_sessions_cap()
    test_stateless_mode_skips_handshake()
    test_di_singletons_shared_across_transports()
    test_progress_notifications_pushed()
    test_dual_mode_coordinated_shutdown()
    test_mcp_path_does_not_redirect()
    test_legacy_sse_messages_not_swallowed_by_streamable_http()
    test_wildcard_and_missing_accept_are_honoured()
    test_header_compat_preserves_protocol_version_for_inner_app()
    test_delete_terminates_live_session_and_404s_unknown_one()
    test_oauth_register_returns_json_not_html()
    test_oauth_configured_skips_not_supported_stubs()
    print("\nAll Phase 3 transport tests passed successfully!")
