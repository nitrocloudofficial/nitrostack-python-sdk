"""Retired methods are rejected by the official handler and HTTP transport."""

import asyncio

import mcp.types as types
import pytest
from mcp import MCPError

from tests.http_probe import decode, http_client, make_app, post
from nitrostack.core.di import DIContainer
from nitrostack.protocol.deprecated import DEPRECATED_MODERN_METHODS, deprecated_method_message, rejects_deprecated_method


def test_initialize_is_not_in_retired_method_table():
    assert "initialize" not in DEPRECATED_MODERN_METHODS
    assert "notifications/initialized" not in DEPRECATED_MODERN_METHODS
    assert deprecated_method_message("initialize") is None


@pytest.mark.parametrize("method", sorted(DEPRECATED_MODERN_METHODS))
def test_retired_method_policy(method):
    assert rejects_deprecated_method(method, "modern")
    assert not rejects_deprecated_method(method, "auto")
    assert not rejects_deprecated_method(method, "legacy")


@pytest.mark.parametrize("era", ["auto", "modern"])
@pytest.mark.parametrize("method", ["resources/subscribe", "resources/unsubscribe"])
@pytest.mark.parametrize("modern", [True, False])
def test_sessionless_resource_subscriptions_are_rejected(era, method, modern):
    with http_client(era) as (_, _, client):
        response = post(client, method, {"uri": "probe://info"}, modern=modern,
                        version="2026-07-28" if modern else "2025-06-18")
        assert response.status_code == (404 if modern else 200), response.text
        error = decode(response)["error"]
        assert error["code"] == (types.METHOD_NOT_FOUND if modern or era == "auto" else -32022)


@pytest.mark.parametrize("method", ["resources/subscribe", "resources/unsubscribe"])
def test_legacy_resource_subscription_compatibility(method):
    with http_client("legacy") as (_, _, client):
        response = post(client, method, {"uri": "probe://info"}, modern=False, version="2025-06-18")
        assert response.status_code == 200, response.text
        assert decode(response)["result"] == {}


@pytest.mark.parametrize("method", ["tasks/list", "tasks/result", "logging/setLevel"])
def test_modern_retired_methods_reach_official_dispatch(method):
    params = {"tasks/list": {}, "tasks/result": {"taskId": "missing"}, "logging/setLevel": {"level": "info"}}[method]
    with http_client("modern") as (_, _, client):
        response = post(client, method, params)
        assert response.status_code == 404, response.text
        assert decode(response)["error"]["code"] == types.METHOD_NOT_FOUND


def test_auto_official_handler_still_lists_tasks():
    app = make_app("auto")
    try:
        handler = app.mcp_server.request_handlers[types.ListTasksRequest]
        response = asyncio.run(handler(types.ListTasksRequest(method="tasks/list", params={})))
        assert response.tasks == []
    finally:
        DIContainer.reset()


def test_modern_official_handler_rejects_tasks_list():
    app = make_app("modern")
    try:
        handler = app.mcp_server.request_handlers[types.ListTasksRequest]
        with pytest.raises(MCPError) as exc:
            asyncio.run(handler(types.ListTasksRequest(method="tasks/list", params={})))
        assert exc.value.error.code == types.METHOD_NOT_FOUND
        assert exc.value.error.message == deprecated_method_message("tasks/list")
    finally:
        DIContainer.reset()
