"""Independent spillover verification through official HTTP and stdio transports."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import anyio
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp_types import jsonrpc_message_adapter
import pytest

from nitrostack import DIContainer
from nitrostack.auth.jwt import JWTModule, JWTService
from nitrostack.runtime.request_ctx import stdio_connection_id
from nitrostack.transports.stdio import serve_stdio_streams
from tests.spillover_transport_support import MODERN_META, HttpPeer, build_app, http_app, large_value

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_context(monkeypatch):
    DIContainer.reset()
    for key in ("NITRO_MCP_PROTOCOL_VERSION", "MCP_STATELESS"):
        monkeypatch.delenv(key, raising=False)
    yield
    DIContainer.reset()


@pytest.fixture(params=["memory", "file"])
def backend(request):
    return request.param


def envelope(response):
    result = response["result"]
    assert result.get("isError", False) is False
    data = result["structuredContent"]
    assert len(json.dumps(data["preview"], ensure_ascii=False).encode()) <= 2048
    assert result["content"][1]["type"] == "resource_link"
    assert result["content"][1]["uri"] == data["resource_uri"]
    return data


def assert_full(peer, data, value):
    expected = (value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, separators=(",", ":"),
    )).encode()
    assert data["original_size_bytes"] == len(expected)
    contents = peer.read(data["resource_uri"])["result"]["contents"][0]
    assert contents["text"].encode() == expected
    assert contents["mimeType"] == ("text/plain" if isinstance(value, str) else "application/json")


def test_anonymous_http_roundtrip_and_session_isolation(backend, tmp_path):
    with http_app(backend, tmp_path, threshold=16) as client:
        alice, bob = HttpPeer(client), HttpPeer(client)
        value = {"rows": ["é🚀" * 100]}
        data = envelope(alice.call(value))
        assert_full(alice, data, value)
        assert "error" in bob.read(data["resource_uri"])
        original = bob.headers["mcp-session-id"]
        bob.headers["mcp-session-id"] = "not-server-issued"
        response = client.post("/mcp", headers=bob.headers, json={
            "jsonrpc": "2.0", "id": 99, "method": "resources/read",
            "params": {"uri": data["resource_uri"]},
        })
        assert response.status_code == 404
        bob.headers["mcp-session-id"] = original
        # Expired/deleted transport sessions cannot recover anonymous payloads.
        deleted = client.delete("/mcp", headers=alice.headers)
        assert deleted.status_code == 200
        assert "error" in HttpPeer(client).read(data["resource_uri"])


@pytest.mark.parametrize("era", ["legacy", "modern", "auto"])
def test_verified_identity_reconnect_and_auth_isolation(backend, tmp_path, monkeypatch, era):
    monkeypatch.setenv("JWT_SECRET", "spillover-wire-regression-secret")
    JWTModule.for_root(secret_env_var="JWT_SECRET")
    jwt = DIContainer.get_instance().resolve(JWTService)
    claims = {"sub": "alice", "iss": "issuer-a", "client_id": "client-a", "tenant_id": "tenant-a"}
    token = jwt.create_token(claims)
    with http_app(backend, tmp_path, era=era, threshold=16) as client:
        alice = HttpPeer(client, era, token)
        data = envelope(alice.call("private-value" * 100))
        assert_full(HttpPeer(client, era, token), data, "private-value" * 100)
        for field in ("sub", "iss", "client_id", "tenant_id"):
            other = jwt.create_token({**claims, field: "another"})
            assert "error" in HttpPeer(client, era, other).read(data["resource_uri"])
        invalid = HttpPeer(client, era, "invalid-signature")
        assert "error" in invalid.read(data["resource_uri"])
        assert invalid.call("private-value" * 100)["result"]["isError"] is True
        assert "error" in HttpPeer(client, era).read(data["resource_uri"])


@pytest.mark.parametrize("era", ["modern", "auto"])
def test_anonymous_sessionless_cannot_create_owner(backend, tmp_path, era):
    with http_app(backend, tmp_path, era=era, threshold=16) as client:
        peer = HttpPeer(client, era)
        denied = peer.call("private-value" * 100)["result"]
        assert denied["isError"] is True
        assert "structuredContent" not in denied
        # Unsigned metadata is not a substitute for a trusted transport identity.
        denied = peer.rpc("tools/call", {
            "name": "payload", "arguments": {"value": "private-value" * 100},
            "_meta": {"sessionId": "forged", "userId": "alice"},
        })
        assert denied["result"]["isError"] is True


def test_expired_missing_and_malformed_uris(backend, tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("nitrostack.core.interceptors.spillover.time", SimpleNamespace(time=lambda: clock[0]))
    with http_app(backend, tmp_path, threshold=16) as client:
        peer = HttpPeer(client)
        data = envelope(peer.call("private-value" * 100))
        clock[0] += 301
        for uri in (data["resource_uri"], "resource://data-spillover/" + "0" * 32,
                    "resource://data-spillover/../escape", "resource://data-spillover/%2e%2e%2fescape"):
            response = peer.read(uri)
            assert "error" in response
            assert "private-value" not in json.dumps(response)


def test_custom_prefix_and_background_task(backend, tmp_path):
    with http_app(backend, tmp_path, threshold=16, prefix="resource://reports/") as client:
        peer = HttpPeer(client)
        value = "task-result" * 100
        response = peer.call(value, task={"ttl": 60000})["result"]
        task_id = response["structuredContent"]["task"]["taskId"]
        for _ in range(100):
            result = peer.rpc("tasks/get", {"taskId": task_id})
            assert "result" in result, result
            if result["result"]["status"] == "completed":
                break
            time.sleep(0.01)
        else:
            pytest.fail("Spillover task did not complete")
        completed = result["result"]["result"]
        data = envelope({"result": completed})
        assert data["resource_uri"].startswith("resource://reports/")
        assert_full(peer, data, value)


CASES = ["threshold", "threshold-plus-one", "1MiB", "5MiB", "10MiB",
         "unicode", "escaped", "rows", "dictionary", "nested", "too-large", "20MiB"]


@pytest.mark.parametrize("case", CASES)
def test_large_payloads_over_http(backend, tmp_path, case):
    value = large_value(case)
    with http_app(backend, tmp_path) as client:
        peer = HttpPeer(client)
        response = peer.rpc("tools/call", {"name": "payload", "arguments": {"case": case}})
        if case == "threshold":
            result = response["result"]
            assert result["content"][0]["text"] == value
            assert "resource_uri" not in result.get("structuredContent", {})
        elif case in ("too-large", "20MiB"):
            result = response["result"]
            assert result["isError"] is True
            assert "max_bytes" in result["content"][0]["text"]
            assert len(json.dumps(result)) < 2048
        else:
            data = envelope(response)
            assert_full(peer, data, value)
            assert "error" in HttpPeer(client).read(data["resource_uri"])


class StdioPeer:
    def __init__(self, backend, root):
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "spillover_transport_support.py"), backend, str(root)],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        self.reader = ThreadPoolExecutor(max_workers=1)
        self.next_id = 1

    def rpc(self, method, params):
        self.process.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": self.next_id, "method": method,
            "params": {**params, "_meta": MODERN_META},
        }) + "\n")
        self.process.stdin.flush()
        line = self.reader.submit(self.process.stdout.readline).result(timeout=10)
        response = json.loads(line)
        assert response["id"] == self.next_id
        self.next_id += 1
        return response

    def close(self, *, clean=False):
        try:
            if clean:
                self.process.stdin.close()
                self.process.wait(timeout=10)
                assert self.process.returncode == 0, self.process.stderr.read()
                assert self.process.stdout.read() == ""
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.reader.shutdown()
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()


def test_stdio_roundtrip_connection_isolation_and_shutdown(backend, tmp_path):
    # Both processes use one disk root sequentially to prove persistence does not
    # let a later stdio connection inherit anonymous ownership of an earlier one.
    first = StdioPeer(backend, tmp_path)
    try:
        value = "stdio-result" * 100
        data = envelope(first.rpc("tools/call", {"name": "payload", "arguments": {"value": value}}))
        result = first.rpc("resources/read", {"uri": data["resource_uri"]})
        assert result["result"]["contents"][0]["text"] == value
        first.close(clean=True)
    finally:
        first.close()
    second = StdioPeer(backend, tmp_path)
    try:
        assert "error" in second.rpc("resources/read", {"uri": data["resource_uri"]})
        second.close(clean=True)
    finally:
        second.close()


def test_stdio_identity_is_reset_after_shutdown_and_not_inherited_by_other_task(tmp_path):
    async def scenario():
        app = build_app("memory", tmp_path, era="auto", threshold=16)
        async with create_client_server_memory_streams() as (client, server):
            client_read, client_write = client
            seen = []

            async def serve():
                await serve_stdio_streams(app.mcp_server, *server, "auto")
                seen.append(stdio_connection_id.get())

            async with anyio.create_task_group() as group:
                group.start_soon(serve)
                await client_write.send(SessionMessage(jsonrpc_message_adapter.validate_python({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                        "name": "payload", "arguments": {"value": "x" * 100}, "_meta": MODERN_META,
                    },
                })))
                response = await asyncio.wait_for(client_read.receive(), 5)
                assert "resource_uri" in response.message.model_dump_json()
                assert stdio_connection_id.get() is None
                # The same app can serve HTTP concurrently without borrowing the
                # anonymous identity belonging to this stdio connection.
                from starlette.testclient import TestClient
                with TestClient(app.get_combined_app(json_response=True)) as http:
                    assert HttpPeer(http, "auto").call("x" * 100)["result"]["isError"] is True
                await client_write.aclose()
        assert seen == [None]
        assert stdio_connection_id.get() is None
    asyncio.run(scenario())
