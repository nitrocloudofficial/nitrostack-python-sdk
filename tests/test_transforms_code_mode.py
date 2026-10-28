import asyncio
import os
import pytest
from pydantic import BaseModel

import mcp.types as types
from nitrostack import ExecutionContext, injectable, module, tool, ToolAnnotations
from nitrostack.testing import NitroTestingModule
from nitrostack.transforms.code_mode import (
    CodeModeLimits,
    CodeModeTransform,
    WorkerPool,
    format_legible_error,
)

try:
    import quickjs
    HAS_QUICKJS = True
except ImportError:
    HAS_QUICKJS = False

requires_quickjs = pytest.mark.skipif(
    not HAS_QUICKJS,
    reason="The 'quickjs' package is required for Code Mode. Install it with: pip install 'nitrostack[code-mode]'",
)


def run(coro):
    return asyncio.run(coro)


class CalcInput(BaseModel):
    a: int
    b: int


class WriteInput(BaseModel):
    key: str
    val: str


guard_call_count = 0


class TrackingGuard:
    async def can_activate(self, context):
        global guard_call_count
        guard_call_count += 1
        return True


@injectable()
class SampleController:
    @tool(
        name="add",
        description="Add two numbers together",
        input_schema=CalcInput,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    )
    async def add(self, input: CalcInput, context: ExecutionContext) -> int:
        return input.a + input.b

    @tool(
        name="multiply",
        description="Multiply two numbers together",
        input_schema=CalcInput,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    )
    async def multiply(self, input: CalcInput, context: ExecutionContext) -> int:
        return input.a * input.b

    @tool(
        name="destructive_wipe",
        description="Destructive wipe operation",
        input_schema=WriteInput,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    )
    async def destructive_wipe(self, input: WriteInput, context: ExecutionContext) -> str:
        return f"wiped {input.key}"


@module(name="sample", controllers=[SampleController])
class SampleModule:
    pass


@requires_quickjs
def test_cannot_reach_filesystem():
    """A script cannot reach the filesystem."""
    transform = CodeModeTransform()
    res = run(transform.sandbox.run("typeof fs === 'undefined' ? 'isolated' : fs.readFileSync('/etc/passwd')", None, transform.limits))
    assert res.success is True
    assert res.value == "isolated"

    res2 = run(transform.sandbox.run("fs.readFileSync('/etc/passwd')", None, transform.limits))
    assert res2.success is False
    assert "ReferenceError" in res2.error


@requires_quickjs
def test_cannot_reach_network():
    """A script cannot reach network."""
    transform = CodeModeTransform()
    res = run(transform.sandbox.run("typeof fetch === 'undefined' && typeof XMLHttpRequest === 'undefined' && typeof WebSocket === 'undefined'", None, transform.limits))
    assert res.success is True
    assert res.value is True

    res2 = run(transform.sandbox.run("fetch('https://google.com')", None, transform.limits))
    assert res2.success is False
    assert "ReferenceError" in res2.error


@requires_quickjs
def test_cannot_reach_environment_variables():
    """A script cannot reach environment variables."""
    transform = CodeModeTransform()
    os.environ["SECRET_ENV_TOKEN"] = "supersecret123"
    res = run(transform.sandbox.run("typeof process === 'undefined' ? 'no-process' : process.env.SECRET_ENV_TOKEN", None, transform.limits))
    assert res.success is True
    assert res.value == "no-process"

    res2 = run(transform.sandbox.run("process.env", None, transform.limits))
    assert res2.success is False
    assert "ReferenceError" in res2.error


@requires_quickjs
def test_cannot_reach_host_modules():
    """A script cannot reach host modules."""
    transform = CodeModeTransform()
    res = run(transform.sandbox.run("typeof require === 'undefined' && typeof __import__ === 'undefined'", None, transform.limits))
    assert res.success is True
    assert res.value is True

    res2 = run(transform.sandbox.run("require('os')", None, transform.limits))
    assert res2.success is False
    assert "ReferenceError" in res2.error


@requires_quickjs
def test_infinite_loop_stops_within_timeout():
    """An infinite loop stops within the limits."""
    limits = CodeModeLimits(timeout_ms=200, memory_limit_mb=50, worker_pool_size=4)
    pool = WorkerPool(limits)
    try:
        res = run(pool.run("while(true) {}", None, limits))
        assert res.success is False
        assert res.error is not None
        assert "TimeoutError" in res.error or "timed out" in res.error
    finally:
        run(pool.dispose())


@requires_quickjs
def test_memory_bomb_stops_within_limits():
    """A memory bomb stops within memory limits."""
    limits = CodeModeLimits(timeout_ms=10000, memory_limit_mb=5, worker_pool_size=4)
    pool = WorkerPool(limits)
    try:
        res = run(pool.run("let arr = []; while(true) { arr.push(new Array(50000).fill(1)); }", None, limits))
        assert res.success is False
        assert res.error is not None
        assert "MemoryLimitExceededError" in res.error or "out of memory" in res.error
    finally:
        run(pool.dispose())


@requires_quickjs
def test_pool_keeps_serving_afterwards():
    """The pool keeps serving afterwards following timeout and memory bomb errors."""
    limits = CodeModeLimits(timeout_ms=5000, memory_limit_mb=5, worker_pool_size=4)
    pool = WorkerPool(limits)
    try:
        timeout_limits = CodeModeLimits(timeout_ms=200, memory_limit_mb=5, worker_pool_size=4)
        r1 = run(pool.run("while(true) {}", None, timeout_limits))
        assert r1.success is False

        r2 = run(pool.run("let arr = []; while(true) { arr.push(new Array(50000).fill(1)); }", None, limits))
        assert r2.success is False

        r3 = run(pool.run("const a = 10; const b = 20; a + b;", None, limits))
        assert r3.success is True
        assert r3.value == 30

        r4 = run(pool.run("return 40 + 2;", None, limits))
        assert r4.success is True
        assert r4.value == 42
    finally:
        run(pool.dispose())


@requires_quickjs
def test_script_tool_calls_go_through_pipeline():
    """Script tool calls invoke real tools through the pipeline."""
    async def go():
        transform = CodeModeTransform()
        h = await NitroTestingModule.create(SampleModule, transforms=[transform])

        script = """
        const sum = tools.call('add', { a: 10, b: 5 });
        const product = call_tool('multiply', { a: sum, b: 2 });
        return { sum, product };
        """
        res = await h.call_tool("execute", {"code": script})
        return res

    result = run(go())
    assert isinstance(result, dict)
    assert result["success"] is True
    assert result["value"] == {"sum": 15, "product": 30}
    assert result["toolCallCount"] == 2


@requires_quickjs
def test_destructive_guard():
    """A tool is destructive unless readOnlyHint is true or destructiveHint is explicitly false.
    Destructive tools are rejected unless allowDestructive is set.
    """
    async def test_rejected():
        transform = CodeModeTransform(allow_destructive=False)
        h = await NitroTestingModule.create(SampleModule, transforms=[transform])

        script = "tools.call('destructive_wipe', { key: 'test', val: 'val' });"
        return await h.call_tool("execute", {"code": script})

    res = run(test_rejected())
    assert isinstance(res, dict)
    assert res["success"] is False
    assert "DestructiveToolError" in res["error"]
    assert "destructive" in res["error"]

    async def test_allowed():
        transform = CodeModeTransform(allow_destructive=True)
        h = await NitroTestingModule.create(SampleModule, transforms=[transform])

        script = "tools.call('destructive_wipe', { key: 'test', val: 'val' });"
        return await h.call_tool("execute", {"code": script})

    res2 = run(test_allowed())
    assert isinstance(res2, dict)
    assert res2["success"] is True
    assert res2["value"] == "wiped test"


def test_import_nitrostack_works_without_extra():
    """import nitrostack works without extra installed."""
    import nitrostack
    assert nitrostack.__name__ == "nitrostack"


@requires_quickjs
def test_synthetic_tools_catalog_and_discovery():
    """Search and get_schema synthetic tools work correctly."""
    async def go():
        transform = CodeModeTransform()
        h = await NitroTestingModule.create(SampleModule, transforms=[transform])

        srv = h.app.mcp_server
        assert srv is not None
        tools_list = (await srv.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )).root.tools

        names = [t.name for t in tools_list]
        assert set(names) == {"search", "get_schema", "execute"}

        search_res = await h.call_tool("search", {"query": "add"})
        assert search_res["matches"] == 1
        assert search_res["tools"][0]["name"] == "add"

        schema_res = await h.call_tool("get_schema", {"name": "add"})
        assert "add" in schema_res["schemas"]
        assert "properties" in schema_res["schemas"]["add"]["inputSchema"]

        all_res = await h.call_tool("search", {"query": ""})
        assert all_res["total_tools"] == 3

    run(go())


@requires_quickjs
def test_configurable_synthetic_tool_names():
    """Synthetic tool names can be configured."""
    async def go():
        transform = CodeModeTransform(
            search_tool_name="find_tools",
            get_schema_tool_name="tool_schema",
            execute_tool_name="run_code",
        )
        h = await NitroTestingModule.create(SampleModule, transforms=[transform])
        srv = h.app.mcp_server
        assert srv is not None
        tools_list = (await srv.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )).root.tools
        names = [t.name for t in tools_list]
        assert set(names) == {"find_tools", "tool_schema", "run_code"}

    run(go())


@requires_quickjs
def test_max_tool_calls_limit():
    """Tool calls exceeding max_tool_calls limit are rejected."""
    async def go():
        transform = CodeModeTransform(max_tool_calls=2)
        h = await NitroTestingModule.create(SampleModule, transforms=[transform])

        script = """
        tools.call('add', { a: 1, b: 2 });
        tools.call('add', { a: 3, b: 4 });
        tools.call('add', { a: 5, b: 6 }); // 3rd call exceeds limit
        """
        res = await h.call_tool("execute", {"code": script})
        return res

    res = run(go())
    assert isinstance(res, dict)
    assert res["success"] is False
    assert "Tool call limit exceeded" in res["error"]


def test_legible_error_scrubbing():
    """Errors returned to the model are readable and do not leak host paths or stack frames."""
    raw_error = f"Error in /home/aadarshm07/nitrostack/secret/path.py at line 42\n  File \"/home/user/app.py\", line 10, in execute\nReferenceError: 'x' is not defined"
    cleaned = format_legible_error(raw_error)
    assert "/home/aadarshm07" not in cleaned
    assert "File \"/home" not in cleaned
    assert "ReferenceError: 'x' is not defined" in cleaned


@requires_quickjs
def test_console_logs_captured():
    """console.log, console.warn, console.error are captured into logs array."""
    async def go():
        transform = CodeModeTransform()
        h = await NitroTestingModule.create(SampleModule, transforms=[transform])

        script = """
        console.log("Starting calculation");
        console.warn("Watch out for precision");
        console.error("Test error message");
        return 100;
        """
        res = await h.call_tool("execute", {"code": script})
        return res

    res = run(go())
    assert isinstance(res, dict)
    assert res["success"] is True
    assert res["value"] == 100
    logs = res["logs"]
    assert any("[info] Starting calculation" in l for l in logs)
    assert any("[warn] Watch out for precision" in l for l in logs)
    assert any("[error] Test error message" in l for l in logs)


@requires_quickjs
def test_worker_slot_recovers_if_connection_none():
    """If a slot connection or process is killed/None, it recovers automatically."""
    limits = CodeModeLimits(worker_pool_size=1)
    pool = WorkerPool(limits)
    try:
        slot = pool._all_slots[0]
        slot.terminate()
        assert slot.conn is None

        res = run(pool.run("return 123 * 2;", None, limits))
        assert res.success is True
        assert res.value == 246
    finally:
        run(pool.dispose())


@requires_quickjs
def test_worker_pool_disposed_raises_runtime_error():
    """Running against a disposed pool raises RuntimeError, not AttributeError on NoneType."""
    limits = CodeModeLimits(worker_pool_size=1)
    pool = WorkerPool(limits)
    run(pool.dispose())

    with pytest.raises(RuntimeError, match="disposed"):
        run(pool.run("1 + 1", None, limits))


@requires_quickjs
def test_timeout_applied_while_tool_call_in_progress():
    """Script timeout is enforced while a slow tool call is in progress."""
    async def slow_tool(name, args):
        await asyncio.sleep(0.6)
        return "done"

    limits = CodeModeLimits(timeout_ms=50, worker_pool_size=1)
    pool = WorkerPool(limits)
    try:
        res = run(pool.run("tools.call('slow', {})", slow_tool, limits))
        assert res.success is False
        assert res.error is not None
        assert "TimeoutError" in res.error or "timed out" in res.error
        # The script duration should be around 50ms, not 600ms+
        assert res.duration_ms < 250
    finally:
        run(pool.dispose())

