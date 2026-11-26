"""Subprocess entry points for generated projects; never contacts external services."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import multiprocessing
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch


def cli(args):
    from nitrostack.cli import skills, upgrade
    from nitrostack.cli.main import main

    def offline():
        raise skills.SkillsCloneError("offline template test")

    skills.clone_skills_repo = offline
    upgrade.verify_nitrostack_version = lambda *args, **kwargs: None
    upgrade.fetch_latest_nitrostack_version = lambda *args, **kwargs: "0.3.2"
    sys.argv = ["nitrostack-py", *args]
    main()


async def behavior(kind):
    sys.path.insert(0, str(Path.cwd()))
    # Import/bootstrap must not start transports or sandbox workers.
    from nitrostack.transforms.code_mode.worker_pool import WorkerPool
    with patch.object(WorkerPool, "__init__", side_effect=AssertionError("eager worker")):
        import main as project
        from nitrostack import McpApplicationFactory
        app = await McpApplicationFactory.create(project.Application)
    assert multiprocessing.active_children() == []
    import mcp.types as types
    from nitrostack.testing import NitroTestingModule
    harness = NitroTestingModule(app)
    listed = await app.mcp_server.request_handlers[types.ListToolsRequest](types.ListToolsRequest(method="tools/list"))
    names = {t.name for t in listed.root.tools}
    if kind == "enterprise-search":
        assert names == {"search_tools", "call_tool", "finance_get_financial_summary", "inventory_report", "support_get_system_status"}
        browse = await harness.call_tool("search_tools", {"query": ""})
        assert browse["total_tools"] == 6
        ranked = await harness.call_tool("search_tools", {"query": "overdue invoices"})
        assert ranked["results"][0]["name"] == "finance_list_invoices"
        assert ranked == await harness.call_tool("search_tools", {"query": "overdue invoices"})
        assert (await harness.call_tool("search_tools", {"query": "zzzznothingmatches"}))["results"] == []
        result = await harness.call_tool("call_tool", {"name": "finance_list_invoices", "arguments": {"overdue_only": True}})
        assert [row["id"] for row in result["invoices"]] == ["INV-001"]
        result["invoices"][0]["amount"] = 0
        result = await harness.call_tool("call_tool", {"name": "finance_list_invoices", "arguments": {"overdue_only": True}})
        assert result["invoices"][0]["amount"] == 1200
    else:
        assert names == {"search", "get_schema", "execute"}
        search = await harness.call_tool("search", {"query": "inventory"})
        assert search["total_tools"] == 7
        assert {tool["name"] for tool in search["tools"]} == {"inventory_check_stock", "inventory_report"}
        schema = await harness.call_tool("get_schema", {"name": "inventory_check_stock"})
        stock_schema = schema["schemas"]["inventory_check_stock"]["inputSchema"]
        assert stock_schema["properties"]["sku"]["type"] == "string"
        assert "sku" in stock_schema["required"]
        assert project.CODE_MODE._sandbox is None
        try:
            if sys.platform == "win32" and importlib.util.find_spec("quickjs") is None:
                # Exercise the documented unavailable-binding error, never pretend a fake is QuickJS.
                result = await harness.call_tool("execute", {"code": "return 1;"})
                assert "required for Code Mode" in json.dumps(result)
                print("NATIVE_QUICKJS_UNAVAILABLE: verified diagnostic; real execution runs on Linux")
            else:
                result = await harness.call_tool("execute", {"code": Path("scripts/sample-batch.js").read_text()})
                assert result["success"] is True, result
                assert result["value"]["total_units"] == 18
                assert result["toolCallCount"] == 3
                pool = project.CODE_MODE.sandbox
                workers = [slot.process for slot in pool._all_slots]
                project.CODE_MODE.limits.timeout_ms = 200
                failure = await harness.call_tool("execute", {"code": "while (true) {}"})
                assert failure["success"] is False, failure
                await project.CODE_MODE.dispose()
                assert all(not worker.is_alive() for worker in workers)
                assert multiprocessing.active_children() == []
        finally:
            await project.CODE_MODE.dispose()
    print("BEHAVIOR_OK", kind)


async def cleanup():
    sys.path.insert(0, str(Path.cwd()))
    import main as project
    from types import SimpleNamespace
    for fail_creation in (True, False):
        create = AsyncMock(side_effect=RuntimeError("startup failed")) if fail_creation else AsyncMock(return_value=SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("startup failed"))))
        with patch.object(project.McpApplicationFactory, "create", create), patch.object(project.CODE_MODE, "dispose", new_callable=AsyncMock) as dispose:
            try:
                await project.main()
            except RuntimeError as exc:
                assert str(exc) == "startup failed"
            else:
                raise AssertionError("expected startup failure")
            dispose.assert_awaited_once()
    print("CLEANUP_OK")


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "cli":
        cli(sys.argv[2:])
    elif action == "cleanup":
        asyncio.run(cleanup())
    else:
        asyncio.run(behavior(action))
