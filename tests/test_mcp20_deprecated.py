"""Retired methods are rejected by the official handler and HTTP transport."""

import mcp.types as types
import pytest

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


@pytest.mark.parametrize("era", ["auto", "modern"])
def test_sessionless_official_handler_hides_tasks_list(era):
    app = make_app(era)
    try:
        assert types.ListTasksRequest not in app.mcp_server.request_handlers
    finally:
        DIContainer.reset()


@pytest.mark.parametrize("era", ["auto", "modern"])
@pytest.mark.parametrize("method", ["tasks/list", "tasks/get", "tasks/result", "tasks/cancel", "tasks/update"])
def test_sessionless_http_hides_task_methods(era, method):
    with http_client(era) as (_, _, client):
        response = post(client, method, {"taskId": "missing"} if method != "tasks/list" else {})
        assert response.status_code == 404, response.text
        assert decode(response)["error"]["code"] == types.METHOD_NOT_FOUND
